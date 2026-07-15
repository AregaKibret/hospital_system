"""Card Type & Consultation Type Management module.

Admin CRUD for CardType/ConsultationType/CardSettings, plus a per-patient
card history view with a manual "Renew Now" action (independent of visit
creation, for renewing ahead of time).
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .audit import log_action
from .card_utils import attach_card_fee_to_invoice, card_status_for, issue_or_renew_card
from .decorators import hms_permission_required
from .models import (
    AuditLog, CardSettings, CardType, ConsultationType, Department,
    Invoice, Patient, PatientCard,
)


# ── Card Types ───────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_card_types')
def card_type_list(request):
    card_types = CardType.objects.prefetch_related('departments').order_by('name')
    return render(request, 'cards/card_type_list.html', {'card_types': card_types})


@hms_permission_required('core.manage_card_types')
def card_type_create(request):
    return _card_type_form(request, card_type=None)


@hms_permission_required('core.manage_card_types')
def card_type_edit(request, card_type_id):
    card_type = get_object_or_404(CardType, pk=card_type_id)
    return _card_type_form(request, card_type=card_type)


def _card_type_form(request, card_type):
    departments = Department.objects.filter(is_active=True).order_by('name')
    is_edit = card_type is not None

    if request.method == 'POST':
        p = request.POST
        name = p.get('name', '').strip()
        code = p.get('code', '').strip().upper()
        errors = []
        if not name:
            errors.append('Card type name is required.')
        if not code:
            errors.append('Card code is required.')
        qs = CardType.objects.filter(name__iexact=name)
        if is_edit:
            qs = qs.exclude(pk=card_type.pk)
        if name and qs.exists():
            errors.append(f'A card type named "{name}" already exists.')
        qs_code = CardType.objects.filter(code__iexact=code)
        if is_edit:
            qs_code = qs_code.exclude(pk=card_type.pk)
        if code and qs_code.exists():
            errors.append(f'Card code "{code}" is already in use.')

        try:
            fee = Decimal(p.get('fee', '0') or '0')
            renewal_fee_raw = p.get('renewal_fee', '').strip()
            renewal_fee = Decimal(renewal_fee_raw) if renewal_fee_raw else None
            validity_value = int(p.get('validity_value') or 1)
        except (InvalidOperation, ValueError):
            errors.append('Fee, renewal fee, and validity must be valid numbers.')
            fee = renewal_fee = validity_value = None

        if not errors:
            old_repr = str(card_type) if is_edit else None
            obj = card_type or CardType(created_by=request.user)
            obj.name = name
            obj.code = code
            obj.description = p.get('description', '').strip()
            obj.fee = fee
            obj.renewal_fee = renewal_fee
            obj.validity_value = validity_value
            obj.validity_unit = p.get('validity_unit', CardType.ValidityUnit.YEARS)
            obj.is_hospital_wide = bool(p.get('is_hospital_wide'))
            obj.is_active = bool(p.get('is_active', 'on' if not is_edit else ''))
            obj.is_default = bool(p.get('is_default'))
            obj.notes = p.get('notes', '').strip()
            obj.save()
            if not obj.is_hospital_wide:
                dept_ids = p.getlist('departments')
                obj.departments.set(dept_ids)
            else:
                obj.departments.clear()

            log_action(
                request.user, AuditLog.Action.UPDATE if is_edit else AuditLog.Action.CREATE,
                AuditLog.Module.CARD_MANAGEMENT,
                object_type='CardType', object_id=obj.pk, object_repr=str(obj),
                description=f'Card type "{obj.name}" {"updated" if is_edit else "created"} '
                            f'(fee ETB {obj.fee}, {obj.validity_value} {obj.validity_unit})',
                request=request,
            )
            messages.success(request, f'Card type "{obj.name}" {"updated" if is_edit else "created"} successfully.')
            return redirect('card_type_list')

        for err in errors:
            messages.error(request, err)

    return render(request, 'cards/card_type_form.html', {
        'card_type': card_type,
        'is_edit': is_edit,
        'departments': departments,
        'validity_units': CardType.ValidityUnit.choices,
        'post': request.POST if request.method == 'POST' else None,
    })


@hms_permission_required('core.manage_card_types')
def card_type_toggle_active(request, card_type_id):
    card_type = get_object_or_404(CardType, pk=card_type_id)
    if request.method == 'POST':
        card_type.is_active = not card_type.is_active
        card_type.save(update_fields=['is_active'])
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.CARD_MANAGEMENT,
            object_type='CardType', object_id=card_type.pk, object_repr=str(card_type),
            description=f'Card type "{card_type.name}" {"activated" if card_type.is_active else "deactivated"}',
            request=request,
        )
        messages.success(request, f'Card type "{card_type.name}" {"activated" if card_type.is_active else "deactivated"}.')
    return redirect('card_type_list')


# ── Consultation Types ────────────────────────────────────────────────────────

@hms_permission_required('core.manage_card_types')
def consultation_type_list(request):
    consultation_types = ConsultationType.objects.select_related('department').order_by('department__name', 'name')
    return render(request, 'cards/consultation_type_list.html', {'consultation_types': consultation_types})


@hms_permission_required('core.manage_card_types')
def consultation_type_create(request):
    return _consultation_type_form(request, consultation_type=None)


@hms_permission_required('core.manage_card_types')
def consultation_type_edit(request, consultation_type_id):
    consultation_type = get_object_or_404(ConsultationType, pk=consultation_type_id)
    return _consultation_type_form(request, consultation_type=consultation_type)


def _consultation_type_form(request, consultation_type):
    departments = Department.objects.filter(is_active=True).order_by('name')
    is_edit = consultation_type is not None

    if request.method == 'POST':
        p = request.POST
        name = p.get('name', '').strip()
        dept_id = p.get('department', '').strip()
        errors = []
        if not name:
            errors.append('Consultation type name is required.')

        department = None
        if dept_id:
            try:
                department = Department.objects.get(pk=dept_id)
            except Department.DoesNotExist:
                errors.append('Selected department not found.')

        qs = ConsultationType.objects.filter(name__iexact=name, department=department)
        if is_edit:
            qs = qs.exclude(pk=consultation_type.pk)
        if name and qs.exists():
            errors.append(f'"{name}" already exists for {department.name if department else "hospital-wide"}.')

        try:
            fee = Decimal(p.get('fee', '0') or '0')
            duration_raw = p.get('duration_minutes', '').strip()
            duration_minutes = int(duration_raw) if duration_raw else None
        except (InvalidOperation, ValueError):
            errors.append('Fee and duration must be valid numbers.')
            fee = duration_minutes = None

        if not errors:
            obj = consultation_type or ConsultationType(created_by=request.user)
            obj.name = name
            obj.department = department
            obj.fee = fee
            obj.duration_minutes = duration_minutes
            obj.is_active = bool(p.get('is_active', 'on' if not is_edit else ''))
            obj.is_default = bool(p.get('is_default'))
            obj.notes = p.get('notes', '').strip()
            obj.save()

            log_action(
                request.user, AuditLog.Action.UPDATE if is_edit else AuditLog.Action.CREATE,
                AuditLog.Module.CARD_MANAGEMENT,
                object_type='ConsultationType', object_id=obj.pk, object_repr=str(obj),
                description=f'Consultation type "{obj.name}" {"updated" if is_edit else "created"} '
                            f'(fee ETB {obj.fee}, {department.name if department else "hospital-wide"})',
                request=request,
            )
            messages.success(request, f'Consultation type "{obj.name}" {"updated" if is_edit else "created"} successfully.')
            return redirect('consultation_type_list')

        for err in errors:
            messages.error(request, err)

    return render(request, 'cards/consultation_type_form.html', {
        'consultation_type': consultation_type,
        'is_edit': is_edit,
        'departments': departments,
        'post': request.POST if request.method == 'POST' else None,
    })


@hms_permission_required('core.manage_card_types')
def consultation_type_toggle_active(request, consultation_type_id):
    ct = get_object_or_404(ConsultationType, pk=consultation_type_id)
    if request.method == 'POST':
        ct.is_active = not ct.is_active
        ct.save(update_fields=['is_active'])
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.CARD_MANAGEMENT,
            object_type='ConsultationType', object_id=ct.pk, object_repr=str(ct),
            description=f'Consultation type "{ct.name}" {"activated" if ct.is_active else "deactivated"}',
            request=request,
        )
        messages.success(request, f'Consultation type "{ct.name}" {"activated" if ct.is_active else "deactivated"}.')
    return redirect('consultation_type_list')


# ── Card Settings (global validity rules) ────────────────────────────────────

@hms_permission_required('core.system_configuration')
def card_settings_edit(request):
    settings_obj = CardSettings.get_solo()
    if request.method == 'POST':
        try:
            warning_days = int(request.POST.get('expiring_soon_warning_days') or 30)
            if warning_days < 0:
                raise ValueError
        except ValueError:
            messages.error(request, 'Warning period must be a non-negative number of days.')
        else:
            settings_obj.expiring_soon_warning_days = warning_days
            settings_obj.allow_admin_override_expired = bool(request.POST.get('allow_admin_override_expired'))
            settings_obj.updated_by = request.user
            settings_obj.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.CARD_MANAGEMENT,
                object_type='CardSettings', object_id=settings_obj.pk,
                object_repr='Card Settings',
                description='Card Settings updated',
                changes={
                    'expiring_soon_warning_days': {'new': warning_days},
                    'allow_admin_override_expired': {'new': settings_obj.allow_admin_override_expired},
                },
                request=request,
            )
            messages.success(request, 'Card Settings updated successfully.')
            return redirect('card_settings_edit')

    return render(request, 'cards/card_settings.html', {'settings_obj': settings_obj})


# ── Patient card history + manual renewal ────────────────────────────────────

@hms_permission_required('core.view_patient')
def patient_card_history(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    cards = patient.cards.select_related('card_type', 'issued_by').order_by('-created_at')
    active_card_types = CardType.objects.filter(is_active=True).order_by('name')
    return render(request, 'cards/patient_card_history.html', {
        'patient': patient,
        'cards': cards,
        'active_card_types': active_card_types,
        'can_override': request.user.has_perm('core.override_card_expiry'),
    })


@hms_permission_required('core.add_visit')
def patient_card_renew(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method != 'POST':
        return redirect('patient_card_history', patient_id=patient.pk)

    card_type_id = request.POST.get('card_type')
    waive_fee = bool(request.POST.get('waive_fee')) and request.user.has_perm('core.override_card_expiry')
    card_type = get_object_or_404(CardType, pk=card_type_id, is_active=True)

    new_card, issued, fee = issue_or_renew_card(patient, card_type, request.user, waive_fee=waive_fee, request=request)
    if not issued:
        messages.info(request, f'{patient.full_name} already has a valid {card_type.name}.')
        return redirect('patient_card_history', patient_id=patient.pk)

    if fee > 0:
        invoice = Invoice.objects.create(
            patient=patient, created_by=request.user, status=Invoice.Status.DRAFT,
            total_amount=fee,
        )
        attach_card_fee_to_invoice(invoice, new_card, fee, request.user, request=request)
        messages.success(
            request,
            f'{card_type.name} renewed for {patient.full_name}. '
            f'ETB {fee:,.2f} card fee sent to Billing (invoice {invoice.invoice_number}).',
        )
    else:
        messages.success(request, f'{card_type.name} renewed for {patient.full_name} (fee waived).')

    return redirect('patient_card_history', patient_id=patient.pk)


# ── AJAX: live card status check (used on the Create Visit screen) ──────────

@hms_permission_required('core.add_visit')
def card_status_check(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    card_type_id = request.GET.get('card_type')
    if not card_type_id:
        return JsonResponse({'error': 'card_type is required'}, status=400)
    card_type = get_object_or_404(CardType, pk=card_type_id)

    status, card = card_status_for(patient, card_type)
    fee_if_renewed = card_type.effective_renewal_fee if card else card_type.fee
    return JsonResponse({
        'status': status,
        'expiry_date': card.expiry_date.isoformat() if card and card.expiry_date else None,
        'fee_if_renewal_needed': str(fee_if_renewed) if status in ('none', 'expired') else '0.00',
    })
