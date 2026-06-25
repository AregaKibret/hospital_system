from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import hms_permission_required
from .forms import (
    PasswordResetForm,
    PatientForm,
    UserCreateForm,
    UserUpdateForm,
    VisitForm,
)
from .models import Patient, Queue, Visit
from .permissions import DASHBOARD_MODULES

User = get_user_model()


# ── Dashboard ────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    visible_modules = [
        m for m in DASHBOARD_MODULES
        if request.user.has_perm(m['permission'])
    ]
    stats = {
        'total_patients': Patient.objects.count(),
        'today_visits': Visit.objects.count(),
        'queue_count': Queue.objects.count(),
    }
    return render(request, 'dashboard.html', {'modules': visible_modules, 'stats': stats})


# ── Patient management ────────────────────────────────────────────────────────

@hms_permission_required('core.add_patient')
def register_patient(request):
    if request.method == 'POST':
        form = PatientForm(request.POST)
        if form.is_valid():
            form.save()
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


@hms_permission_required('core.add_visit')
def create_visit(request, patient_id):
    patient = get_object_or_404(Patient, id=patient_id)

    if request.method == 'POST':
        form = VisitForm(request.POST)
        if form.is_valid():
            visit = form.save(commit=False)
            visit.patient = patient
            visit.save()

            last_queue = Queue.objects.order_by('-queue_number').first()
            Queue.objects.create(
                visit=visit,
                queue_number=(last_queue.queue_number + 1) if last_queue else 1,
            )
            messages.success(request, 'Visit created. Queue number assigned.')
            return redirect('dashboard')
    else:
        form = VisitForm()

    return render(request, 'create_visit.html', {'patient': patient, 'form': form})


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
        messages.success(request, f'User "{target.username}" {verb}.')
    return redirect('user_list')
