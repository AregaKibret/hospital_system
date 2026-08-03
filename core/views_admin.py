"""
RBAC Administration views — roles, departments, user detail.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .decorators import hms_permission_required
from .models import Department, UserProfile
from .permissions import DEPARTMENT_DEFAULTS, ROLE_CATEGORIES, ROLE_META, ROLE_NAMES

User = get_user_model()


@hms_permission_required('core.manage_roles')
def settings_hub(request):
    return render(request, 'admin/settings_hub.html', {
        'user_count':  User.objects.filter(is_active=True).count(),
        'dept_count':  Department.objects.filter(is_active=True).count(),
        'role_count':  Group.objects.count(),
    })


# ── Permission category labels (for grouped display) ─────────────────────────
_PERM_GROUPS = [
    ('Queue / Triage',        ['manage_queue', 'perform_triage']),
    ('Clinical Notes',        ['write_clinical_note', 'read_clinical_note', 'write_nursing_note', 'read_nursing_note']),
    ('Vitals & Care Plans',   ['record_vital_signs', 'read_vital_signs', 'write_care_plan', 'read_care_plan']),
    ('Diagnosis & Rx',        ['write_diagnosis', 'read_diagnosis', 'write_prescription', 'read_prescription']),
    ('Laboratory',            ['request_lab_test', 'read_lab_request', 'process_lab_test', 'write_lab_result', 'read_lab_result']),
    ('Radiology',             ['request_imaging', 'read_imaging_request', 'process_imaging', 'write_imaging_report', 'read_imaging_report']),
    ('Anesthesia',            ['write_anesthesia_record', 'read_anesthesia_record']),
    ('Billing & Finance',     ['read_billing', 'create_invoice', 'manage_billing', 'process_payment', 'read_financial_report']),
    ('Pharmacy',              ['read_medication_inventory', 'manage_medication', 'dispense_medication',
                               'read_prescription_for_dispensing', 'process_pharmacy_sale',
                               'manage_med_catalog', 'receive_stock', 'manage_suppliers',
                               'view_inv_reports', 'export_inv_reports']),
    ('Dept Pharmacy Stores',  ['view_dept_inventory', 'manage_dept_stores', 'request_medication_transfer',
                               'approve_medication_transfer', 'record_dept_usage', 'view_dept_reports']),
    ('Store / Inventory',     ['read_inventory', 'manage_inventory', 'create_purchase_order', 'approve_purchase_order']),
    ('HR',                    ['read_employee', 'manage_employees', 'manage_attendance', 'manage_payroll']),
    ('Appointments',          ['read_appointment', 'manage_appointments']),
    ('OB/GYN',                ['view_obgyn_dashboard', 'manage_pregnancy', 'view_pregnancy',
                               'manage_anc_visit', 'view_anc_visit',
                               'manage_delivery', 'view_delivery',
                               'manage_newborn', 'view_newborn',
                               'manage_pnc_visit', 'view_pnc_visit',
                               'manage_gyn_consultation', 'view_gyn_consultation',
                               'manage_family_planning', 'view_family_planning',
                               'manage_infertility_case', 'view_infertility_case',
                               'view_obgyn_reports', 'export_obgyn_reports']),
    ('Internal Medicine',     ['view_im_dashboard', 'manage_im_consultation', 'view_im_consultation',
                               'manage_chronic_plan', 'view_chronic_plan',
                               'manage_risk_assessment', 'view_im_reports', 'export_im_reports']),
    ('Pediatrics',            ['view_peds_dashboard', 'manage_peds_consultation', 'view_peds_consultation',
                               'manage_growth_record', 'view_growth_record',
                               'manage_immunization', 'view_immunization',
                               'manage_dev_assessment', 'view_peds_reports', 'export_peds_reports']),
    ('General Surgery',       ['view_surgery_consult_dashboard', 'manage_surgical_consultation', 'view_surgical_consultation',
                               'manage_preop_assessment', 'manage_operative_note', 'view_operative_note',
                               'manage_postop_note', 'view_postop_note',
                               'manage_wound_followup', 'view_surgery_reports', 'export_surgery_reports']),
    ('Cardiology',            ['view_cardiology_dashboard', 'manage_cardiology_consultation', 'view_cardiology_consultation',
                               'manage_ecg_record', 'view_ecg_record',
                               'manage_echo_report', 'view_echo_report',
                               'manage_cardiac_procedure', 'view_cardiology_reports', 'export_cardiology_reports']),
    ('Specialty Assignment',  ['manage_specializations', 'manage_specialty_referral', 'view_specialty_referral',
                               'accept_specialty_referral', 'view_specialty_reports', 'export_specialty_reports']),
    ('Reports',               ['read_clinical_reports', 'read_department_reports']),
    ('System Admin',          ['manage_users', 'manage_roles', 'manage_departments',
                               'system_configuration', 'read_audit_log']),
    ('Patient Records',       ['add_patient', 'view_patient', 'change_patient', 'delete_patient']),
    ('Visits',                ['add_visit', 'view_visit', 'change_visit', 'delete_visit']),
    ('Queue',                 ['view_queue', 'change_queue']),
]


def _get_permission_map():
    """Return {codename: Permission} for all core app permissions."""
    return {p.codename: p for p in Permission.objects.filter(content_type__app_label='core')}


def _grouped_permissions(role_perm_codenames):
    """Return list of (group_label, [(codename, label, has_it)]) for display."""
    perm_map = _get_permission_map()
    shown = set()
    groups = []
    for grp_label, codenames in _PERM_GROUPS:
        items = []
        for cn in codenames:
            if cn in perm_map:
                items.append({
                    'codename': cn,
                    'label':    perm_map[cn].name,
                    'has_it':   cn in role_perm_codenames,
                })
                shown.add(cn)
        if items:
            groups.append({'label': grp_label, 'perms': items})

    # Catch any remaining perms not in our defined groups
    remaining = [
        {'codename': cn, 'label': p.name, 'has_it': cn in role_perm_codenames}
        for cn, p in perm_map.items() if cn not in shown
    ]
    if remaining:
        groups.append({'label': 'Other', 'perms': remaining})
    return groups


# ── Role management ───────────────────────────────────────────────────────────

@hms_permission_required('core.manage_roles')
def role_list(request):
    groups = Group.objects.prefetch_related('permissions', 'user_set').annotate(
        user_count=Count('user', distinct=True),
        perm_count=Count('permissions', distinct=True),
    ).order_by('name')

    # Attach meta info
    role_data = []
    for g in groups:
        meta = ROLE_META.get(g.name, {})
        role_data.append({
            'group':      g,
            'badge':      meta.get('badge', 'bg-slate-100 text-slate-700'),
            'desc':       meta.get('desc', ''),
            'user_count': g.user_count,
            'perm_count': g.perm_count,
        })

    return render(request, 'admin_panel/role_list.html', {
        'role_data':        role_data,
        'role_categories':  ROLE_CATEGORIES,
        'total_users':      User.objects.filter(is_active=True).count(),
        'total_roles':      groups.count(),
    })


@hms_permission_required('core.manage_roles')
def role_detail(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    meta  = ROLE_META.get(group.name, {})
    current_codenames = set(group.permissions.values_list('codename', flat=True))
    grouped_perms = _grouped_permissions(current_codenames)

    users = User.objects.filter(groups=group).prefetch_related('profile__department').order_by('first_name', 'last_name')

    return render(request, 'admin_panel/role_detail.html', {
        'group':          group,
        'meta':           meta,
        'grouped_perms':  grouped_perms,
        'users':          users,
        'perm_count':     len(current_codenames),
        'user_count':     users.count(),
    })


@hms_permission_required('core.manage_roles')
def role_permissions_edit(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    perm_map = _get_permission_map()
    current_codenames = set(group.permissions.values_list('codename', flat=True))

    if request.method == 'POST':
        selected = set(request.POST.getlist('permissions'))
        # Only touch core perms managed here
        managed_codenames = set(perm_map.keys())
        keep_other = group.permissions.exclude(content_type__app_label='core')

        new_perms = [perm_map[cn] for cn in selected if cn in perm_map]
        group.permissions.set(list(keep_other) + new_perms)

        added   = selected - current_codenames
        removed = (current_codenames & managed_codenames) - selected
        messages.success(request, f'Permissions updated for "{group.name}": +{len(added)} added, -{len(removed)} removed.')
        return redirect('role_detail', group_id=group.id)

    grouped_perms = _grouped_permissions(current_codenames)
    return render(request, 'admin_panel/role_permissions_edit.html', {
        'group':         group,
        'grouped_perms': grouped_perms,
    })


@hms_permission_required('core.manage_roles')
def role_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Role name is required.')
        elif Group.objects.filter(name=name).exists():
            messages.error(request, f'A role named "{name}" already exists.')
        else:
            group = Group.objects.create(name=name)
            messages.success(request, f'Role "{name}" created.')
            return redirect('role_permissions_edit', group_id=group.id)
    return render(request, 'admin_panel/role_create.html', {'post': request.POST})


@hms_permission_required('core.manage_roles')
def role_delete(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if request.method == 'POST':
        if group.user_set.exists():
            messages.error(request, f'Cannot delete "{group.name}" — {group.user_set.count()} user(s) are assigned to it. Reassign them first.')
        elif group.name in ROLE_NAMES:
            messages.error(request, f'"{group.name}" is a system role and cannot be deleted.')
        else:
            group.delete()
            messages.success(request, f'Role "{group.name}" deleted.')
            return redirect('role_list')
    return redirect('role_detail', group_id=group.id)


# ── Department management ─────────────────────────────────────────────────────

@hms_permission_required('core.manage_departments')
def dept_list(request):
    depts = Department.objects.annotate(staff_count=Count('staff', distinct=True)).order_by('dept_type', 'name')
    return render(request, 'admin_panel/dept_list.html', {
        'depts':      depts,
        'dept_types': Department.DeptType.choices,
    })


@hms_permission_required('core.manage_departments')
def dept_create(request):
    if request.method == 'POST':
        p = request.POST
        name = p.get('name', '').strip()
        if not name:
            messages.error(request, 'Department name is required.')
        elif Department.objects.filter(name__iexact=name).exists():
            messages.error(request, f'A department named "{name}" already exists.')
        else:
            d = Department.objects.create(
                name=name,
                dept_type=p.get('dept_type', Department.DeptType.OTHER),
                description=p.get('description', '').strip(),
                head=p.get('head', '').strip(),
                phone=p.get('phone', '').strip(),
                location=p.get('location', '').strip(),
            )
            messages.success(request, f'Department "{d.name}" created.')
            return redirect('dept_list')
    return render(request, 'admin_panel/dept_form.html', {
        'action': 'Create', 'dept_types': Department.DeptType.choices, 'post': request.POST,
    })


@hms_permission_required('core.manage_departments')
def dept_edit(request, dept_id):
    dept = get_object_or_404(Department, id=dept_id)
    if request.method == 'POST':
        p = request.POST
        name = p.get('name', '').strip()
        if not name:
            messages.error(request, 'Department name is required.')
        elif Department.objects.filter(name__iexact=name).exclude(id=dept.id).exists():
            messages.error(request, f'A department named "{name}" already exists.')
        else:
            dept.name        = name
            dept.dept_type   = p.get('dept_type', dept.dept_type)
            dept.description = p.get('description', '').strip()
            dept.head        = p.get('head', '').strip()
            dept.phone       = p.get('phone', '').strip()
            dept.location    = p.get('location', '').strip()
            dept.is_active   = p.get('is_active') == 'on'
            dept.save()
            messages.success(request, f'Department "{dept.name}" updated.')
            return redirect('dept_list')
    return render(request, 'admin_panel/dept_form.html', {
        'action': 'Edit', 'dept': dept, 'dept_types': Department.DeptType.choices,
    })


@hms_permission_required('core.manage_departments')
def dept_delete(request, dept_id):
    dept = get_object_or_404(Department, id=dept_id)
    if request.method == 'POST':
        if dept.staff.exists():
            messages.error(request, f'Cannot delete "{dept.name}" — {dept.staff.count()} staff member(s) assigned. Reassign them first.')
        else:
            dept.delete()
            messages.success(request, f'Department "{dept.name}" deleted.')
    return redirect('dept_list')


# ── User detail ───────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_users')
def user_detail(request, user_id):
    target = get_object_or_404(User.objects.prefetch_related('groups__permissions', 'profile__department'), id=user_id)
    profile, _ = UserProfile.objects.get_or_create(user=target)
    group = target.groups.first()
    role_perms = set(group.permissions.values_list('codename', flat=True)) if group else set()

    # All permissions directly on the user (overrides)
    direct_perms = set(target.user_permissions.values_list('codename', flat=True))

    grouped_perms = _grouped_permissions(role_perms | direct_perms)

    return render(request, 'admin_panel/user_detail.html', {
        'target':        target,
        'profile':       profile,
        'group':         group,
        'meta':          ROLE_META.get(group.name, {}) if group else {},
        'grouped_perms': grouped_perms,
        'role_perms':    role_perms,
        'direct_perms':  direct_perms,
    })
