from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .audit import build_changes, log_action
from .decorators import hms_permission_required
from .forms import (
    PasswordResetForm,
    PatientForm,
    UserCreateForm,
    UserUpdateForm,
    VisitForm,
)
from .models import Appointment, AuditLog, Doctor, HospitalProfile, Invoice, InvoiceItem, Patient, Queue, Specialization, UserProfile, Visit
from .permissions import DASHBOARD_MODULES

User = get_user_model()


# ── Dashboard ────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    from django.utils import timezone
    today = timezone.localdate()
    hospital = HospitalProfile.objects.first()
    visible_modules = [
        m for m in DASHBOARD_MODULES
        if request.user.has_perm(m['permission'])
    ]
    stats = {
        'total_patients': Patient.objects.count(),
        'today_visits': Visit.objects.filter(created_at__date=today).count(),
        'queue_waiting': Queue.objects.filter(status='Waiting').count(),
        'queue_count': Queue.objects.count(),
    }
    return render(request, 'dashboard.html', {
        'hospital': hospital,
        'modules': visible_modules,
        'stats': stats,
        'today': today,
    })


# ── Patient management ────────────────────────────────────────────────────────

@hms_permission_required('core.add_patient')
def register_patient(request):
    if request.method == 'POST':
        form = PatientForm(request.POST)
        if form.is_valid():
            patient = form.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.PATIENT,
                object_type='Patient', object_id=patient.pk,
                object_repr=patient.full_name,
                description=f'New patient registered: {patient.full_name} (card {patient.card_number})',
                extra_data={'card_number': patient.card_number},
                request=request,
            )
            messages.success(request, 'Patient registered successfully.')
            return redirect('patient_next_action', patient_id=patient.pk)
    else:
        form = PatientForm()
    return render(request, 'register_patient.html', {'form': form})


@hms_permission_required('core.add_patient')
def patient_next_action(request, patient_id):
    """Shown right after a patient is registered — lets the receptionist
    jump straight into Create Visit / Create Appointment without re-entering
    any patient details. If the registration came from a walk-in
    appointment (?appointment_id=...), that appointment is already linked
    to the patient (see register_patient_for_appointment) — offer to view
    it instead of creating a second, duplicate appointment."""
    patient = get_object_or_404(Patient, pk=patient_id)

    linked_appointment = None
    appointment_id = request.GET.get('appointment_id')
    if appointment_id:
        linked_appointment = Appointment.objects.filter(
            pk=appointment_id, patient=patient,
        ).select_related('doctor', 'department').first()

    return render(request, 'patient_next_action.html', {
        'patient': patient,
        'linked_appointment': linked_appointment,
    })


@hms_permission_required('core.view_patient')
def patient_search(request):
    query = request.GET.get('q', '').strip()
    patients = Patient.objects.all()

    if query:
        patients = patients.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(card_number__icontains=query)
            | Q(mobile__icontains=query)
        )

    paginator = Paginator(patients, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'patient_search.html', {
        'patients': page_obj,
        'query': query,
        'total': patients.count(),
    })


# ── Live Patient Search API ────────────────────────────────────────────────────

@login_required
def api_patient_search(request):
    """JSON endpoint for the PatientPicker widget and global search."""
    if not request.user.has_perm('core.view_patient'):
        return JsonResponse({'patients': [], 'query': ''}, status=403)

    q = request.GET.get('q', '').strip()
    try:
        limit = min(int(request.GET.get('limit', 12)), 30)
    except (ValueError, TypeError):
        limit = 12

    if len(q) < 2:
        return JsonResponse({'patients': [], 'query': q})

    qs = (
        Patient.objects.filter(
            Q(card_number__icontains=q)
            | Q(first_name__icontains=q)
            | Q(middle_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(mobile__icontains=q),
            is_active=True,
        )
        .prefetch_related('visits__doctor', 'visits__department')
        [:limit]
    )

    results = []
    for p in qs:
        last_visit = p.visits.order_by('-created_at').first()
        doctor_name = None
        dept_name = None
        if last_visit:
            if last_visit.doctor:
                doctor_name = last_visit.doctor.full_name
            if last_visit.department:
                dept_name = str(last_visit.department)
        results.append({
            'id': p.id,
            'card_number': p.card_number,
            'full_name': p.full_name,
            'age': p.age_display or '—',
            'sex': p.sex,
            'mobile': p.mobile or '—',
            'last_visit': last_visit.created_at.strftime('%b %d, %Y') if last_visit else None,
            'doctor': doctor_name,
            'department': dept_name,
        })

    return JsonResponse({'patients': results, 'query': q})


# ── Global Search API ─────────────────────────────────────────────────────────

@login_required
def api_global_search(request):
    """Multi-entity search for the navbar global search bar."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'patients': [], 'doctors': [], 'invoices': [], 'query': q})

    patients_out, doctors_out, invoices_out = [], [], []

    if request.user.has_perm('core.view_patient'):
        for p in Patient.objects.filter(
            Q(card_number__icontains=q) | Q(first_name__icontains=q)
            | Q(last_name__icontains=q) | Q(mobile__icontains=q),
            is_active=True,
        )[:6]:
            patients_out.append({
                'id': p.id,
                'card_number': p.card_number,
                'full_name': p.full_name,
                'age': p.age_display or '',
                'sex': p.sex or '',
                'mobile': p.mobile or '',
            })

        from .models import Doctor as DoctorModel
        for d in DoctorModel.objects.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q),
            active=True,
        ).select_related('specialization')[:4]:
            doctors_out.append({
                'id': d.id,
                'full_name': d.full_name,
                'specialization': str(d.specialization) if d.specialization else '',
            })

    if request.user.has_perm('core.read_billing'):
        from .models import Invoice
        for inv in Invoice.objects.filter(
            Q(invoice_number__icontains=q)
            | Q(patient__first_name__icontains=q)
            | Q(patient__last_name__icontains=q),
        ).select_related('patient')[:4]:
            invoices_out.append({
                'id': inv.pk,
                'invoice_number': inv.invoice_number,
                'patient_name': inv.patient.full_name if inv.patient else '',
            })

    return JsonResponse({'patients': patients_out, 'doctors': doctors_out, 'invoices': invoices_out, 'query': q})


CONSULTATION_FEE = 300  # ETB — standard OPD consultation charge


@hms_permission_required('core.add_visit')
def create_visit(request, patient_id):
    from decimal import Decimal
    from django.db import transaction
    from django.utils import timezone

    from .card_utils import attach_card_fee_to_invoice, card_status_for, issue_or_renew_card

    patient = get_object_or_404(Patient, id=patient_id)
    # Carried over from the "what next?" screen when this patient was just
    # registered off a walk-in appointment — link that same appointment to
    # the visit instead of leaving it orphaned.
    appointment_id = request.GET.get('appointment_id') or request.POST.get('appointment_id')

    if request.method == 'POST':
        form = VisitForm(request.POST, patient=patient)
        if form.is_valid():
            card_type = form.cleaned_data['card_type']
            consultation_type = form.cleaned_data['consultation_type']
            waive_card_fee = (
                bool(request.POST.get('waive_card_fee'))
                and request.user.has_perm('core.override_card_expiry')
            )

            specialty_id = request.POST.get('specialty') or None
            with transaction.atomic():
                visit = form.save(commit=False)
                visit.patient = patient
                if specialty_id:
                    try:
                        visit.specialty_id = int(specialty_id)
                    except (ValueError, TypeError):
                        pass
                visit.save()

                log_action(
                    request.user, AuditLog.Action.CREATE, AuditLog.Module.VISIT,
                    object_type='Visit', object_id=visit.pk,
                    object_repr=f'{patient.full_name} — {visit.get_visit_type_display()}',
                    description=f'Visit created for {patient.full_name} ({visit.get_visit_type_display()})',
                    extra_data={
                        'patient': patient.full_name,
                        'visit_type': visit.visit_type,
                        'department': str(visit.department) if visit.department else None,
                    },
                    request=request,
                )

                if appointment_id:
                    appt = Appointment.objects.filter(
                        pk=appointment_id, patient=patient, visit__isnull=True,
                    ).first()
                    if appt:
                        appt.visit = visit
                        appt.save(update_fields=['visit', 'updated_at'])
                        log_action(
                            request.user, AuditLog.Action.UPDATE, AuditLog.Module.APPOINTMENT,
                            object_type='Appointment', object_id=appt.pk,
                            object_repr=appt.appointment_number,
                            description=f'Appointment {appt.appointment_number} linked to new visit for {patient.full_name}',
                            request=request,
                        )

                # Card validity — issue/renew automatically if the patient
                # has no currently-valid card of the selected type. This is
                # how "no visit without a valid card" is enforced: the two
                # happen together in the same transaction, and the renewal
                # fee (if any) rides along on the same invoice.
                patient_card, card_issued, card_fee = issue_or_renew_card(
                    patient, card_type, request.user, waive_fee=waive_card_fee, request=request,
                )
                visit.card_type = card_type
                visit.consultation_type = consultation_type
                visit.patient_card = patient_card
                visit.save(update_fields=['card_type', 'consultation_type', 'patient_card'])

                # Assign queue number
                last_queue = Queue.objects.order_by('-queue_number').first()
                Queue.objects.create(
                    visit=visit,
                    queue_number=(last_queue.queue_number + 1) if last_queue else 1,
                )

                visit_type = visit.visit_type
                invoice = None
                consultation_fee = None

                if visit_type == Visit.VisitType.NEW_VISIT:
                    # Auto-create a Draft invoice with the consultation fee
                    consultation_fee = (
                        consultation_type.fee if consultation_type
                        else Decimal(str(CONSULTATION_FEE))
                    )
                    invoice = Invoice.objects.create(
                        patient=patient,
                        visit=visit,
                        created_by=request.user,
                        status='Draft',
                        total_amount=consultation_fee,
                        due_date=timezone.localdate(),
                    )
                    InvoiceItem.objects.create(
                        invoice=invoice,
                        description=consultation_type.name if consultation_type else 'Consultation Fee',
                        service_type=InvoiceItem.ServiceType.CONSULTATION,
                        quantity=Decimal('1'),
                        unit_price=consultation_fee,
                    )

                if card_issued and card_fee > 0:
                    if invoice is None:
                        invoice = Invoice.objects.create(
                            patient=patient, visit=visit, created_by=request.user,
                            status='Draft', total_amount=Decimal('0.00'),
                            due_date=timezone.localdate(),
                        )
                    attach_card_fee_to_invoice(invoice, patient_card, card_fee, request.user, request=request)
                    invoice.total_amount = invoice.total_amount + card_fee
                    invoice.save(update_fields=['total_amount'])

            if visit_type == Visit.VisitType.NEW_VISIT:
                from .patient_flow import advance_to_waiting_payment
                advance_to_waiting_payment(visit, performed_by=request.user)
                fee_msg = f'Consultation invoice (ETB {consultation_fee:,.2f})'
                if card_issued and card_fee > 0:
                    fee_msg += f' + card fee (ETB {card_fee:,.2f})'
                messages.success(
                    request,
                    f'Visit created. {fee_msg} generated automatically — pending cashier approval.',
                )
                return redirect('dashboard')

            elif visit_type == Visit.VisitType.REVISIT:
                from .patient_flow import advance_to_waiting_payment
                advance_to_waiting_payment(visit, performed_by=request.user)
                msg = 'Revisit recorded. No consultation fee charged.'
                if card_issued and card_fee > 0:
                    msg += f' Card renewal fee (ETB {card_fee:,.2f}) sent to Billing.'
                messages.success(request, msg)
                return redirect('dashboard')

            else:  # Repayment
                from .patient_flow import advance_to_waiting_payment
                advance_to_waiting_payment(visit, performed_by=request.user)
                repay_msg = (
                    'Repayment visit created. Please process the outstanding '
                    'invoice through Billing.'
                )
                if card_issued and card_fee > 0:
                    repay_msg += f' Card renewal fee (ETB {card_fee:,.2f}) also sent to Billing.'
                messages.success(
                    request,
                    repay_msg,
                )
                return redirect('billing_dashboard')
    else:
        form = VisitForm(patient=patient)

    previous_visits = Visit.objects.filter(patient=patient).select_related(
        'doctor', 'department'
    ).order_by('-created_at')

    specializations = Specialization.objects.filter(is_active=True).order_by('display_order', 'name')
    doctors = Doctor.objects.filter(active=True).select_related('specialization')
    doctor_specialty_map = {
        str(d.pk): d.specialization_id for d in doctors if d.specialization_id
    }

    import json
    return render(request, 'create_visit.html', {
        'patient': patient,
        'form': form,
        'previous_visits': previous_visits,
        'consultation_fee': CONSULTATION_FEE,
        'appointment_id': appointment_id,
        'specializations': specializations,
        'doctor_specialty_map': json.dumps(doctor_specialty_map),
    })


# ── Access denied ─────────────────────────────────────────────────────────────

def access_denied(request):
    return render(request, 'accounts/access_denied.html', status=403)


# ── User management ───────────────────────────────────────────────────────────

@hms_permission_required('core.manage_users')
def user_list(request):
    users = User.objects.prefetch_related('groups', 'profile').order_by(
        'first_name', 'last_name', 'username'
    )

    role_filter = request.GET.get('role', '')
    if role_filter:
        users = users.filter(groups__name=role_filter)

    search = request.GET.get('q', '').strip()
    if search:
        users = users.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(username__icontains=search)
        )

    roles = Group.objects.order_by('name')
    paginator = Paginator(users.distinct(), 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'users/user_list.html', {
        'page_obj': page_obj,
        'roles': roles,
        'role_filter': role_filter,
        'search': search,
    })


@hms_permission_required('core.manage_users')
def user_create(request):
    if request.method == 'POST':
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            groups = list(user.groups.values_list('name', flat=True))
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.USER,
                object_type='User', object_id=user.pk, object_repr=user.username,
                description=f'User account created: {user.get_full_name() or user.username}',
                extra_data={'username': user.username, 'groups': groups},
                request=request,
            )
            messages.success(request, f'User "{user.username}" created successfully.')
            return redirect('user_list')
    else:
        form = UserCreateForm()
    return render(request, 'users/user_form.html', {'form': form, 'action': 'Create'})


@hms_permission_required('core.manage_users')
def user_edit(request, user_id):
    target = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        form = UserUpdateForm(request.POST, instance=target)
        if form.is_valid():
            form.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.USER,
                object_type='User', object_id=target.pk, object_repr=target.username,
                description=f'User account updated: {target.get_full_name() or target.username}',
                request=request,
            )
            messages.success(request, f'User "{target.username}" updated.')
            return redirect('user_list')
    else:
        form = UserUpdateForm(instance=target)
    return render(request, 'users/user_form.html', {
        'form': form,
        'action': 'Edit',
        'target_user': target,
    })


@hms_permission_required('core.manage_users')
def user_reset_password(request, user_id):
    target = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        form = PasswordResetForm(request.POST)
        if form.is_valid():
            target.set_password(form.cleaned_data['new_password'])
            target.save()
            if target == request.user:
                update_session_auth_hash(request, target)
            log_action(
                request.user, AuditLog.Action.PASSWORD_CHANGE, AuditLog.Module.USER,
                object_type='User', object_id=target.pk, object_repr=target.username,
                description=f'Password reset for user: {target.username}',
                severity=AuditLog.Severity.WARNING,
                request=request,
            )
            messages.success(request, f'Password for "{target.username}" reset successfully.')
            return redirect('user_list')
    else:
        form = PasswordResetForm()
    return render(request, 'users/reset_password.html', {
        'form': form,
        'target_user': target,
    })


@hms_permission_required('core.manage_users')
def user_toggle_active(request, user_id):
    target = get_object_or_404(User, id=user_id)
    if target == request.user:
        messages.error(request, 'You cannot deactivate your own account.')
    else:
        target.is_active = not target.is_active
        target.save()
        verb = 'activated' if target.is_active else 'deactivated'
        action = AuditLog.Action.ACTIVATE if target.is_active else AuditLog.Action.DEACTIVATE
        log_action(
            request.user, action, AuditLog.Module.USER,
            object_type='User', object_id=target.pk, object_repr=target.username,
            description=f'User account {verb}: {target.username}',
            severity=AuditLog.Severity.WARNING if not target.is_active else AuditLog.Severity.INFO,
            request=request,
        )
        messages.success(request, f'User "{target.username}" {verb}.')
    return redirect('user_list')
