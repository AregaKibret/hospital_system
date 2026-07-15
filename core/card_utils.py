"""Shared card issuance/renewal logic — used by both the standalone card
renewal view and the Create Visit workflow, so a card is always issued the
same way regardless of where the request came from."""
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .audit import log_action
from .models import (
    AuditLog, CardSettings, CardType, ConsultationType, Invoice, InvoiceItem,
    PatientCard, Queue, Visit,
)


def get_active_card(patient, card_type):
    """Most recent non-cancelled card of this type for this patient, or None."""
    return (
        PatientCard.objects
        .filter(patient=patient, card_type=card_type)
        .exclude(status=PatientCard.Status.CANCELLED)
        .order_by('-created_at')
        .first()
    )


def issue_or_renew_card(patient, card_type, user, *, waive_fee=False, request=None):
    """Ensure the patient has a currently-valid card of this type.

    Returns (patient_card, newly_issued, fee_charged). If an active,
    non-expired card already exists, it's reused and nothing is charged.
    Otherwise a new card is issued (renewing the old one, if any) and the
    registration/renewal fee is returned so the caller can bill it.
    """
    existing = get_active_card(patient, card_type)
    if existing and existing.effective_status == PatientCard.Status.ACTIVE:
        return existing, False, Decimal('0.00')

    issued_date = timezone.localdate()
    expiry_date = card_type.compute_expiry(issued_date)
    fee = Decimal('0.00') if waive_fee else (
        card_type.effective_renewal_fee if existing else card_type.fee
    )

    new_card = PatientCard.objects.create(
        patient=patient,
        card_type=card_type,
        issued_date=issued_date,
        expiry_date=expiry_date,
        status=PatientCard.Status.ACTIVE,
        fee_paid=fee,
        fee_waived=waive_fee,
        issued_by=user,
        renewed_from=existing,
    )
    if existing:
        existing.status = PatientCard.Status.RENEWED
        existing.save(update_fields=['status'])

    action = 'renewed' if existing else 'issued'
    log_action(
        user, AuditLog.Action.CREATE, AuditLog.Module.CARD_MANAGEMENT,
        object_type='PatientCard', object_id=new_card.pk,
        object_repr=f'{patient.full_name} — {card_type.name}',
        description=(
            f'{card_type.name} card {action} for {patient.full_name} '
            f'({"fee waived" if waive_fee else f"fee ETB {fee:,.2f}"}), '
            f'valid until {expiry_date or "lifetime"}'
        ),
        extra_data={
            'patient': patient.full_name, 'card_type': card_type.name,
            'fee': str(fee), 'waived': waive_fee,
            'expiry_date': str(expiry_date) if expiry_date else None,
        },
        request=request,
    )
    return new_card, True, fee


def attach_card_fee_to_invoice(invoice, patient_card, fee, user, request=None):
    """Create the Card Fee invoice item and link it back to the PatientCard."""
    item = InvoiceItem.objects.create(
        invoice=invoice,
        description=f'{patient_card.card_type.name} — Card Fee'
                     + (' (waived)' if patient_card.fee_waived else ''),
        service_type=InvoiceItem.ServiceType.CARD_FEE,
        quantity=1,
        unit_price=fee,
    )
    patient_card.invoice_item = item
    patient_card.save(update_fields=['invoice_item'])
    return item


def card_status_for(patient, card_type):
    """('none'|'active'|'expiring_soon'|'expired', PatientCard|None)"""
    card = get_active_card(patient, card_type)
    if not card or card.effective_status in (PatientCard.Status.RENEWED, PatientCard.Status.CANCELLED):
        return 'none', None
    if card.is_expired:
        return 'expired', card
    if card.is_expiring_soon:
        return 'expiring_soon', card
    return 'active', card


# ── Appointment check-in: fully automatic billing determination ─────────────
# Maps Appointment.VisitType (New Visit/Revisit/Follow-up) onto the closest
# Visit.VisitType — Visit has no separate "Follow-up" state, so a follow-up
# appointment becomes a Revisit visit (same clinical meaning: an existing
# patient returning, as opposed to a first-time registration).
_APPOINTMENT_TO_VISIT_TYPE = {
    'New Visit': Visit.VisitType.NEW_VISIT,
    'Revisit': Visit.VisitType.REVISIT,
    'Follow-up': Visit.VisitType.REVISIT,
}


def get_default_card_type():
    """The card type used automatically at check-in when no one picks one by
    hand — the admin-flagged default, or the first active hospital-wide type."""
    return (
        CardType.objects.filter(is_active=True, is_default=True).first()
        or CardType.objects.filter(is_active=True, is_hospital_wide=True).order_by('name').first()
    )


def get_default_consultation_type(department):
    """The consultation type used automatically at check-in — the type
    flagged default for this department, else the department's first active
    type, else the hospital-wide default, else None."""
    if department:
        ct = ConsultationType.objects.filter(department=department, is_active=True, is_default=True).first()
        if ct:
            return ct
        ct = ConsultationType.objects.filter(department=department, is_active=True).order_by('name').first()
        if ct:
            return ct
    return ConsultationType.objects.filter(department__isnull=True, is_active=True, is_default=True).first()


@dataclass
class CheckinBillingResult:
    visit: object
    invoice: object
    card_type: object
    consultation_type: object
    patient_card: object
    card_status_before: str
    card_fee_charged: Decimal
    consultation_fee_charged: Decimal
    reasons: list = field(default_factory=list)
    already_checked_in: bool = False

    @property
    def total_due(self):
        return self.card_fee_charged + self.consultation_fee_charged


def perform_checkin_billing(appointment, user, *, waive_card_fee=False, request=None):
    """The automatic "check-in → determine charges → bill" pipeline described
    in the Appointment Check-In workflow. Idempotent: if this appointment was
    already checked in (has a linked visit), returns the existing result
    instead of creating a second visit/invoice.
    """
    if appointment.visit_id:
        visit = appointment.visit
        invoice = Invoice.objects.filter(visit=visit).first()
        return CheckinBillingResult(
            visit=visit, invoice=invoice,
            card_type=visit.card_type, consultation_type=visit.consultation_type,
            patient_card=visit.patient_card,
            card_status_before='active', card_fee_charged=Decimal('0.00'),
            consultation_fee_charged=Decimal('0.00'),
            reasons=['This appointment was already checked in — showing the existing visit/invoice, no new charges created.'],
            already_checked_in=True,
        )

    patient = appointment.patient
    department = appointment.department
    doctor = appointment.doctor
    if not patient:
        raise ValueError('Patient is not registered yet — complete registration before checking in.')
    if not doctor or not department:
        raise ValueError('This appointment has no doctor/department assigned — set them before checking in.')

    card_type = get_default_card_type()
    if not card_type:
        raise ValueError('No card type is configured yet — ask an administrator to set one up under Card Types.')
    consultation_type = get_default_consultation_type(department)

    visit_type = _APPOINTMENT_TO_VISIT_TYPE.get(appointment.visit_type, Visit.VisitType.NEW_VISIT)
    card_status_before, existing_card = card_status_for(patient, card_type)
    reasons = []

    with transaction.atomic():
        visit = Visit.objects.create(
            patient=patient, department=department, doctor=doctor, visit_type=visit_type,
            chief_complaint=appointment.chief_complaint or appointment.reason_for_visit or '',
        )
        log_action(
            user, AuditLog.Action.CREATE, AuditLog.Module.VISIT,
            object_type='Visit', object_id=visit.pk,
            object_repr=f'{patient.full_name} — {visit.get_visit_type_display()}',
            description=f'Visit created via appointment check-in ({appointment.appointment_number}) '
                        f'for {patient.full_name} ({visit.get_visit_type_display()})',
            request=request,
        )

        appointment.visit = visit
        appointment.save(update_fields=['visit', 'updated_at'])

        patient_card, card_issued, card_fee = issue_or_renew_card(
            patient, card_type, user, waive_fee=waive_card_fee, request=request,
        )
        visit.card_type = card_type
        visit.consultation_type = consultation_type
        visit.patient_card = patient_card
        visit.save(update_fields=['card_type', 'consultation_type', 'patient_card'])

        if card_status_before == 'expired':
            reasons.append(
                f'Card expired on {existing_card.expiry_date:%d %b %Y} — '
                + ('renewal fee waived by administrator override.' if waive_card_fee
                   else f'renewal fee of ETB {card_fee:,.2f} applied.')
            )
        elif card_status_before == 'none':
            reasons.append(
                'No card on file for this patient — '
                + ('registration fee waived by administrator override.' if waive_card_fee
                   else f'registration fee of ETB {card_fee:,.2f} applied.')
            )
        else:
            reasons.append(
                f'Card is valid until {patient_card.expiry_date:%d %b %Y}' if patient_card.expiry_date
                else 'Card is valid (lifetime)'
            )
            reasons[-1] += ' — no registration/card fee required.'

        consultation_fee = consultation_type.fee if consultation_type else Decimal('0.00')
        if consultation_type:
            reasons.append(
                f'{visit.get_visit_type_display()} — {consultation_type.name} fee of '
                f'ETB {consultation_fee:,.2f} applied per hospital policy.'
                if consultation_fee > 0 else
                f'{visit.get_visit_type_display()} — {consultation_type.name} carries no fee per hospital policy.'
            )
        else:
            reasons.append('No consultation type configured for this department — no consultation fee applied.')

        invoice = None
        if card_fee > 0 or consultation_fee > 0:
            invoice = Invoice.objects.create(
                patient=patient, visit=visit, created_by=user, status=Invoice.Status.DRAFT,
                total_amount=Decimal('0.00'), due_date=timezone.localdate(),
            )
            total = Decimal('0.00')
            if consultation_fee > 0:
                InvoiceItem.objects.create(
                    invoice=invoice, description=consultation_type.name,
                    service_type=InvoiceItem.ServiceType.CONSULTATION,
                    quantity=1, unit_price=consultation_fee,
                )
                total += consultation_fee
            if card_fee > 0:
                attach_card_fee_to_invoice(invoice, patient_card, card_fee, user, request=request)
                total += card_fee
            invoice.total_amount = total
            invoice.save(update_fields=['total_amount'])

        last_queue = Queue.objects.order_by('-queue_number').first()
        Queue.objects.create(visit=visit, queue_number=(last_queue.queue_number + 1) if last_queue else 1)

        from .patient_flow import advance_to_waiting_payment
        advance_to_waiting_payment(visit, performed_by=user)

        log_action(
            user, AuditLog.Action.CREATE, AuditLog.Module.BILLING,
            object_type='Visit', object_id=visit.pk,
            object_repr=f'Check-in billing — {patient.full_name}',
            description=(
                f'Automatic check-in billing for {patient.full_name} (appointment '
                f'{appointment.appointment_number}): ' + ' '.join(reasons)
                + (f' Total due: ETB {(card_fee + consultation_fee):,.2f}.' if invoice else ' No charge generated.')
            ),
            extra_data={
                'appointment': appointment.appointment_number,
                'patient': patient.full_name,
                'card_status_before': card_status_before,
                'card_type': card_type.name,
                'card_fee_charged': str(card_fee),
                'card_fee_waived': waive_card_fee,
                'consultation_type': consultation_type.name if consultation_type else None,
                'consultation_fee_charged': str(consultation_fee),
                'total_due': str(card_fee + consultation_fee),
                'invoice': invoice.invoice_number if invoice else None,
                'reasons': reasons,
            },
            request=request,
        )

    return CheckinBillingResult(
        visit=visit, invoice=invoice, card_type=card_type, consultation_type=consultation_type,
        patient_card=patient_card, card_status_before=card_status_before,
        card_fee_charged=card_fee, consultation_fee_charged=consultation_fee, reasons=reasons,
    )
