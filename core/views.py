from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db.models import Q
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
from .models import AuditLog, Invoice, InvoiceItem, Patient, Queue, UserProfile, Visit
from .permissions import DASHBOARD_MODULES

User = get_user_model()


# ── Dashboard ────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    from django.utils import timezone
    today = timezone.localdate()
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
            return redirect('dashboard')
    else:
        form = PatientForm()
    return render(request, 'register_patient.html', {'form': form})


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


CONSULTATION_FEE = 300  # ETB — standard OPD consultation charge


@hms_permission_required('core.add_visit')
def create_visit(request, patient_id):
    from decimal import Decimal
    from django.utils import timezone

    patient = get_object_or_404(Patient, id=patient_id)

    if request.method == 'POST':
        form = VisitForm(request.POST, patient=patient)
        if form.is_valid():
            visit = form.save(commit=False)
            visit.patient = patient
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

            # Assign queue number
            last_queue = Queue.objects.order_by('-queue_number').first()
            Queue.objects.create(
                visit=visit,
                queue_number=(last_queue.queue_number + 1) if last_queue else 1,
            )

            visit_type = visit.visit_type

            if visit_type == Visit.VisitType.NEW_VISIT:
                # Auto-create a Draft invoice with the standard consultation fee
                fee = Decimal(str(CONSULTATION_FEE))
                invoice = Invoice.objects.create(
                    patient=patient,
                    visit=visit,
                    created_by=request.user,
                    status='Draft',
                    total_amount=fee,
                    due_date=timezone.localdate(),
                )
                InvoiceItem.objects.create(
                    invoice=invoice,
                    description='Consultation Fee',
                    service_type=InvoiceItem.ServiceType.CONSULTATION,
                    quantity=Decimal('1'),
                    unit_price=fee,
                    total=fee,
                )
                from .patient_flow import advance_to_waiting_payment
                advance_to_waiting_payment(visit, performed_by=request.user)
                messages.success(
                    request,
                    f'Visit created. Consultation invoice (ETB {CONSULTATION_FEE:,}) '
                    f'generated automatically — pending cashier approval.',
                )
                return redirect('dashboard')

            elif visit_type == Visit.VisitType.REVISIT:
                from .patient_flow import advance_to_waiting_payment
                advance_to_waiting_payment(visit, performed_by=request.user)
                messages.success(
                    request,
                    'Revisit recorded. No consultation fee charged. '
                    'Queue number assigned.',
                )
                return redirect('dashboard')

            else:  # Repayment
                from .patient_flow import advance_to_waiting_payment
                advance_to_waiting_payment(visit, performed_by=request.user)
                messages.success(
                    request,
                    'Repayment visit created. Please process the outstanding '
                    'invoice through Billing.',
                )
                return redirect('billing_dashboard')
    else:
        form = VisitForm(patient=patient)

    previous_visits = Visit.objects.filter(patient=patient).select_related(
        'doctor', 'department'
    ).order_by('-created_at')

    return render(request, 'create_visit.html', {
        'patient': patient,
        'form': form,
        'previous_visits': previous_visits,
        'consultation_fee': CONSULTATION_FEE,
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
