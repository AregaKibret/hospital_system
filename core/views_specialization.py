"""Doctor Specialization Management module.

Admin CRUD for Specialization, plus the (previously missing) web UI for
creating/editing clinical Doctor records — the only place a Specialization
is actually assigned to a doctor.
"""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render

from .audit import log_action
from .decorators import hms_permission_required
from .models import AuditLog, Department, Doctor, Specialization

User = get_user_model()


# ── Specialization CRUD ───────────────────────────────────────────────────────

@hms_permission_required('core.manage_specializations')
def specialization_list(request):
    specializations = Specialization.objects.prefetch_related('departments').order_by('display_order', 'name')
    return render(request, 'specialization/specialization_list.html', {'specializations': specializations})


@hms_permission_required('core.manage_specializations')
def specialization_create(request):
    return _specialization_form(request, specialization=None)


@hms_permission_required('core.manage_specializations')
def specialization_edit(request, specialization_id):
    specialization = get_object_or_404(Specialization, pk=specialization_id)
    return _specialization_form(request, specialization=specialization)


def _specialization_form(request, specialization):
    departments = Department.objects.filter(is_active=True).order_by('name')
    is_edit = specialization is not None

    if request.method == 'POST':
        p = request.POST
        name = p.get('name', '').strip()
        dept_ids = p.getlist('departments')
        errors = []
        if not name:
            errors.append('Specialization name is required.')
        qs = Specialization.objects.filter(name__iexact=name)
        if is_edit:
            qs = qs.exclude(pk=specialization.pk)
        if name and qs.exists():
            errors.append(f'A specialization named "{name}" already exists.')
        if not dept_ids:
            errors.append('Select at least one department for this specialization.')

        try:
            display_order = int(p.get('display_order') or 0)
        except ValueError:
            errors.append('Display order must be a number.')
            display_order = 0

        if not errors:
            obj = specialization or Specialization(created_by=request.user)
            obj.name = name
            obj.display_order = display_order
            obj.is_active = bool(p.get('is_active', 'on' if not is_edit else ''))
            obj.module_url_name = p.get('module_url_name', '').strip()
            obj.color = p.get('color', 'indigo').strip() or 'indigo'
            obj.notes = p.get('notes', '').strip()
            obj.save()
            obj.departments.set(dept_ids)

            log_action(
                request.user, AuditLog.Action.UPDATE if is_edit else AuditLog.Action.CREATE,
                AuditLog.Module.CARD_MANAGEMENT,
                object_type='Specialization', object_id=obj.pk, object_repr=str(obj),
                description=f'Specialization "{obj.name}" {"updated" if is_edit else "created"} '
                            f'(departments: {", ".join(d.name for d in obj.departments.all())})',
                request=request,
            )
            messages.success(request, f'Specialization "{obj.name}" {"updated" if is_edit else "created"} successfully.')
            return redirect('specialization_list')

        for err in errors:
            messages.error(request, err)

    return render(request, 'specialization/specialization_form.html', {
        'specialization': specialization,
        'is_edit': is_edit,
        'departments': departments,
        'colors': ['indigo', 'blue', 'emerald', 'rose', 'amber', 'violet', 'sky', 'teal', 'orange', 'pink'],
        'post': request.POST if request.method == 'POST' else None,
    })


@hms_permission_required('core.manage_specializations')
def specialization_toggle_active(request, specialization_id):
    spec = get_object_or_404(Specialization, pk=specialization_id)
    if request.method == 'POST':
        spec.is_active = not spec.is_active
        spec.save(update_fields=['is_active'])
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.CARD_MANAGEMENT,
            object_type='Specialization', object_id=spec.pk, object_repr=str(spec),
            description=f'Specialization "{spec.name}" {"activated" if spec.is_active else "deactivated"}',
            request=request,
        )
        messages.success(request, f'Specialization "{spec.name}" {"activated" if spec.is_active else "deactivated"}.')
    return redirect('specialization_list')


# ── AJAX: specializations available for a given department ──────────────────

def specializations_for_department(request):
    from django.http import JsonResponse
    dept_id = request.GET.get('department')
    qs = Specialization.objects.filter(is_active=True)
    if dept_id:
        qs = qs.filter(departments__id=dept_id)
    qs = qs.order_by('display_order', 'name')
    return JsonResponse({'results': [{'id': s.pk, 'name': s.name} for s in qs]})


# ── Doctor create/edit (previously only possible via Django admin) ──────────

@hms_permission_required('core.manage_doctors')
def doctor_list(request):
    doctors = Doctor.objects.select_related('department', 'specialization', 'user').order_by('last_name', 'first_name')
    dept_filter = request.GET.get('department', '')
    if dept_filter:
        doctors = doctors.filter(department_id=dept_filter)
    return render(request, 'specialization/doctor_list.html', {
        'doctors': doctors,
        'departments': Department.objects.filter(is_active=True).order_by('name'),
        'dept_filter': dept_filter,
    })


@hms_permission_required('core.manage_doctors')
def doctor_create(request):
    return _doctor_form(request, doctor=None)


@hms_permission_required('core.manage_doctors')
def doctor_edit(request, doctor_id):
    doctor = get_object_or_404(Doctor, pk=doctor_id)
    return _doctor_form(request, doctor=doctor)


def _doctor_form(request, doctor):
    departments = Department.objects.filter(is_active=True).order_by('name')
    is_edit = doctor is not None
    # Users not already linked to a Doctor profile (plus the current one, on edit)
    users_qs = User.objects.filter(doctor_profile__isnull=True).order_by('username')
    if is_edit and doctor.user_id:
        users_qs = User.objects.filter(pk=doctor.user_id) | users_qs

    if request.method == 'POST':
        p = request.POST
        first_name = p.get('first_name', '').strip()
        last_name = p.get('last_name', '').strip()
        dept_id = p.get('department', '').strip()
        specialization_id = p.get('specialization', '').strip()
        errors = []
        if not first_name or not last_name:
            errors.append('First and last name are required.')

        department = None
        if dept_id:
            try:
                department = Department.objects.get(pk=dept_id)
            except Department.DoesNotExist:
                errors.append('Selected department not found.')
        else:
            errors.append('Department is required.')

        specialization = None
        if specialization_id:
            try:
                specialization = Specialization.objects.get(pk=specialization_id, is_active=True)
            except Specialization.DoesNotExist:
                errors.append('Selected specialization not found.')
        else:
            errors.append('Specialization is required for a doctor.')

        if specialization and department and department not in specialization.departments.all():
            errors.append(f'"{specialization.name}" is not configured for {department.name}.')

        user_id = p.get('user', '').strip()
        user_obj = None
        if user_id:
            try:
                user_obj = User.objects.get(pk=user_id)
            except User.DoesNotExist:
                errors.append('Selected user account not found.')

        sec_spec_ids = p.getlist('secondary_specializations')

        if not errors:
            old_repr = str(doctor) if is_edit else None
            obj = doctor or Doctor()
            obj.first_name = first_name
            obj.last_name = last_name
            obj.department = department
            obj.specialization = specialization
            obj.subspecialty = p.get('subspecialty', '').strip()
            obj.mobile = p.get('mobile', '').strip()
            obj.active = bool(p.get('active', 'on' if not is_edit else ''))
            if not is_edit:
                obj.user = user_obj
                obj.employee_id = p.get('employee_id', '').strip() or None
            obj.save()
            obj.secondary_specializations.set(sec_spec_ids)

            log_action(
                request.user, AuditLog.Action.UPDATE if is_edit else AuditLog.Action.CREATE,
                AuditLog.Module.CARD_MANAGEMENT,
                object_type='Doctor', object_id=obj.pk, object_repr=str(obj),
                description=f'Doctor "{obj.full_name}" {"updated" if is_edit else "created"} — '
                            f'{department.name} / {specialization.name}',
                request=request,
            )
            messages.success(request, f'Dr. {obj.full_name} {"updated" if is_edit else "created"} successfully.')
            return redirect('doctor_list')

        for err in errors:
            messages.error(request, err)

    if request.method == 'POST':
        selected_secondary_ids = request.POST.getlist('secondary_specializations')
    elif is_edit:
        selected_secondary_ids = [str(pk) for pk in doctor.secondary_specializations.values_list('id', flat=True)]
    else:
        selected_secondary_ids = []

    all_specializations = Specialization.objects.filter(is_active=True).order_by('name')
    return render(request, 'specialization/doctor_form.html', {
        'doctor': doctor,
        'is_edit': is_edit,
        'departments': departments,
        'users': users_qs,
        'all_specializations': all_specializations,
        'selected_secondary_ids': selected_secondary_ids,
        'post': request.POST if request.method == 'POST' else None,
    })
