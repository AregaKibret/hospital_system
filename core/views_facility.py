"""
Facility & Hospital Location Management — Buildings, Floors, Wards, Rooms,
Beds, and the patient Admission / Transfer / Discharge workflow.
"""
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    Admission, AuditLog, Bed, Building, Department, Floor, InvoiceItem, ORRoom,
    Room, Visit, Ward,
)


# ── Buildings ───────────────────────────────────────────────────────────────

@hms_permission_required('core.view_facility')
def building_list(request):
    buildings = Building.objects.all().order_by('name')
    return render(request, 'facility/building_list.html', {'buildings': buildings})


@hms_permission_required('core.manage_facilities')
def building_create(request):
    if request.method == 'POST':
        p = request.POST
        try:
            b = Building.objects.create(
                name=p['name'].strip(), code=p.get('code', '').strip(),
                notes=p.get('notes', '').strip(),
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.FACILITY,
                object_type='Building', object_id=b.pk, object_repr=b.name,
                description=f'Building "{b.name}" created', request=request,
            )
            messages.success(request, f'Building "{b.name}" created.')
            return redirect('building_list')
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
    return render(request, 'facility/building_form.html', {'action': 'Create'})


@hms_permission_required('core.manage_facilities')
def building_edit(request, building_id):
    building = get_object_or_404(Building, pk=building_id)
    if request.method == 'POST':
        p = request.POST
        try:
            building.name = p['name'].strip()
            building.code = p.get('code', '').strip()
            building.notes = p.get('notes', '').strip()
            building.is_active = bool(p.get('is_active'))
            building.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
                object_type='Building', object_id=building.pk, object_repr=building.name,
                description=f'Building "{building.name}" updated', request=request,
            )
            messages.success(request, f'Building "{building.name}" updated.')
            return redirect('building_list')
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
    return render(request, 'facility/building_form.html', {'building': building, 'action': 'Edit'})


@require_POST
@hms_permission_required('core.manage_facilities')
def building_delete(request, building_id):
    building = get_object_or_404(Building, pk=building_id)
    blockers = []
    if building.floors.exists():
        blockers.append('floors')
    if building.wards.exists():
        blockers.append('wards')
    if building.or_rooms.exists():
        blockers.append('operating rooms')
    if building.departments.exists():
        blockers.append('departments')
    if blockers:
        messages.error(request, f'Cannot delete "{building.name}" — it still has {", ".join(blockers)} assigned to it.')
        return redirect('building_list')
    name = building.name
    building.delete()
    log_action(
        request.user, AuditLog.Action.DELETE, AuditLog.Module.FACILITY,
        object_type='Building', object_id=building_id, object_repr=name,
        description=f'Building "{name}" deleted', request=request,
    )
    messages.success(request, f'Building "{name}" deleted.')
    return redirect('building_list')


# ── Floors ────────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_facility')
def floor_list(request, building_id):
    building = get_object_or_404(Building, pk=building_id)
    floors = building.floors.order_by('level_order')
    return render(request, 'facility/floor_list.html', {'building': building, 'floors': floors})


@hms_permission_required('core.manage_facilities')
def floor_create(request, building_id):
    building = get_object_or_404(Building, pk=building_id)
    if request.method == 'POST':
        p = request.POST
        try:
            f = Floor.objects.create(
                building=building, name=p['name'].strip(),
                level_order=int(p.get('level_order', 0) or 0),
                notes=p.get('notes', '').strip(),
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.FACILITY,
                object_type='Floor', object_id=f.pk, object_repr=str(f),
                description=f'Floor "{f.name}" created in {building.name}', request=request,
            )
            messages.success(request, f'Floor "{f.name}" created.')
            return redirect('floor_list', building_id=building.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
    return render(request, 'facility/floor_form.html', {'building': building, 'action': 'Create'})


@hms_permission_required('core.manage_facilities')
def floor_edit(request, building_id, floor_id):
    building = get_object_or_404(Building, pk=building_id)
    floor = get_object_or_404(Floor, pk=floor_id, building=building)
    if request.method == 'POST':
        p = request.POST
        try:
            floor.name = p['name'].strip()
            floor.level_order = int(p.get('level_order', floor.level_order) or 0)
            floor.notes = p.get('notes', '').strip()
            floor.is_active = bool(p.get('is_active'))
            floor.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
                object_type='Floor', object_id=floor.pk, object_repr=str(floor),
                description=f'Floor "{floor.name}" updated', request=request,
            )
            messages.success(request, f'Floor "{floor.name}" updated.')
            return redirect('floor_list', building_id=building.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
    return render(request, 'facility/floor_form.html', {'building': building, 'floor': floor, 'action': 'Edit'})


@require_POST
@hms_permission_required('core.manage_facilities')
def floor_delete(request, building_id, floor_id):
    building = get_object_or_404(Building, pk=building_id)
    floor = get_object_or_404(Floor, pk=floor_id, building=building)
    blockers = []
    if floor.wards.exists():
        blockers.append('wards')
    if floor.or_rooms.exists():
        blockers.append('operating rooms')
    if floor.departments.exists():
        blockers.append('departments')
    if blockers:
        messages.error(request, f'Cannot delete "{floor.name}" — it still has {", ".join(blockers)} assigned to it.')
        return redirect('floor_list', building_id=building.pk)
    name = floor.name
    floor.delete()
    log_action(
        request.user, AuditLog.Action.DELETE, AuditLog.Module.FACILITY,
        object_type='Floor', object_id=floor_id, object_repr=name,
        description=f'Floor "{name}" deleted from {building.name}', request=request,
    )
    messages.success(request, f'Floor "{name}" deleted.')
    return redirect('floor_list', building_id=building.pk)


# ── Wards ─────────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_facility')
def ward_list(request):
    wards = Ward.objects.select_related('department', 'building', 'floor').annotate(
        room_count=Count('rooms', distinct=True),
    ).order_by('name')
    return render(request, 'facility/ward_list.html', {'wards': wards})


@hms_permission_required('core.manage_facilities')
def ward_create(request):
    if request.method == 'POST':
        p = request.POST
        try:
            ward = Ward.objects.create(
                name=p['name'].strip(), code=p['code'].strip(),
                ward_type=p.get('ward_type', Ward.WardType.MEDICAL),
                department_id=p.get('department') or None,
                building_id=p.get('building') or None,
                floor_id=p.get('floor') or None,
                gender_restriction=p.get('gender_restriction', Ward.Gender.MIXED),
                is_isolation=bool(p.get('is_isolation')),
                is_icu_hdu=bool(p.get('is_icu_hdu')),
                notes=p.get('notes', '').strip(),
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.FACILITY,
                object_type='Ward', object_id=ward.pk, object_repr=ward.name,
                description=f'Ward "{ward.name}" created', request=request,
            )
            messages.success(request, f'Ward "{ward.name}" created.')
            return redirect('ward_detail', ward_id=ward.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
    return render(request, 'facility/ward_form.html', {
        'action': 'Create', 'ward_types': Ward.WardType.choices, 'gender_choices': Ward.Gender.choices,
        'departments': Department.objects.filter(is_active=True), 'buildings': Building.objects.filter(is_active=True),
        'floors': Floor.objects.filter(is_active=True).select_related('building'),
    })


@hms_permission_required('core.manage_facilities')
def ward_edit(request, ward_id):
    ward = get_object_or_404(Ward, pk=ward_id)
    if request.method == 'POST':
        p = request.POST
        try:
            ward.name = p['name'].strip()
            ward.code = p['code'].strip()
            ward.ward_type = p.get('ward_type', ward.ward_type)
            ward.department_id = p.get('department') or None
            ward.building_id = p.get('building') or None
            ward.floor_id = p.get('floor') or None
            ward.gender_restriction = p.get('gender_restriction', ward.gender_restriction)
            ward.is_isolation = bool(p.get('is_isolation'))
            ward.is_icu_hdu = bool(p.get('is_icu_hdu'))
            ward.notes = p.get('notes', '').strip()
            ward.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
                object_type='Ward', object_id=ward.pk, object_repr=ward.name,
                description=f'Ward "{ward.name}" updated', request=request,
            )
            messages.success(request, f'Ward "{ward.name}" updated.')
            return redirect('ward_detail', ward_id=ward.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
    return render(request, 'facility/ward_form.html', {
        'ward': ward, 'action': 'Edit', 'ward_types': Ward.WardType.choices, 'gender_choices': Ward.Gender.choices,
        'departments': Department.objects.filter(is_active=True), 'buildings': Building.objects.filter(is_active=True),
        'floors': Floor.objects.filter(is_active=True).select_related('building'),
    })


@hms_permission_required('core.view_facility')
def ward_detail(request, ward_id):
    ward = get_object_or_404(Ward, pk=ward_id)
    rooms = ward.rooms.annotate(n_beds=Count('beds')).order_by('room_number')
    return render(request, 'facility/ward_detail.html', {
        'ward': ward, 'rooms': rooms, 'status_choices': Ward.Status.choices,
    })


@require_POST
@hms_permission_required('core.manage_facilities')
def ward_update_status(request, ward_id):
    ward = get_object_or_404(Ward, pk=ward_id)
    new_status = request.POST.get('status', '').strip()
    if new_status not in {c[0] for c in Ward.Status.choices}:
        messages.error(request, 'Invalid status.')
        return redirect('ward_detail', ward_id=ward.pk)
    old_status = ward.status
    ward.status = new_status
    ward.save(update_fields=['status'])
    log_action(
        request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
        object_type='Ward', object_id=ward.pk, object_repr=ward.name,
        description=f'Ward "{ward.name}" status changed: {old_status} → {new_status}', request=request,
    )
    messages.success(request, f'Ward status updated to "{ward.get_status_display()}".')
    return redirect('ward_detail', ward_id=ward.pk)


# ── Rooms ─────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_facilities')
def room_create(request, ward_id):
    ward = get_object_or_404(Ward, pk=ward_id)
    if request.method == 'POST':
        p = request.POST
        try:
            room = Room.objects.create(
                ward=ward, room_number=p['room_number'].strip(), room_name=p.get('room_name', '').strip(),
                room_type=p.get('room_type', Room.RoomType.STANDARD),
                capacity=int(p.get('capacity', 1) or 1),
                privacy_type=p.get('privacy_type', Room.Privacy.SEMI_PRIVATE),
                is_ac=bool(p.get('is_ac')), is_isolation=bool(p.get('is_isolation')),
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.FACILITY,
                object_type='Room', object_id=room.pk, object_repr=str(room),
                description=f'Room {room.room_number} created in ward {ward.name}', request=request,
            )
            messages.success(request, f'Room {room.room_number} created.')
            return redirect('room_detail', room_id=room.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
    return render(request, 'facility/room_form.html', {
        'ward': ward, 'action': 'Create', 'room_types': Room.RoomType.choices, 'privacy_choices': Room.Privacy.choices,
    })


@hms_permission_required('core.manage_facilities')
def room_edit(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    if request.method == 'POST':
        p = request.POST
        try:
            room.room_number = p['room_number'].strip()
            room.room_name = p.get('room_name', '').strip()
            room.room_type = p.get('room_type', room.room_type)
            room.capacity = int(p.get('capacity', room.capacity) or room.capacity)
            room.privacy_type = p.get('privacy_type', room.privacy_type)
            room.is_ac = bool(p.get('is_ac'))
            room.is_isolation = bool(p.get('is_isolation'))
            room.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
                object_type='Room', object_id=room.pk, object_repr=str(room),
                description=f'Room {room.room_number} updated', request=request,
            )
            messages.success(request, f'Room {room.room_number} updated.')
            return redirect('room_detail', room_id=room.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
    return render(request, 'facility/room_form.html', {
        'room': room, 'ward': room.ward, 'action': 'Edit',
        'room_types': Room.RoomType.choices, 'privacy_choices': Room.Privacy.choices,
    })


@hms_permission_required('core.view_facility')
def room_detail(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    beds = room.beds.select_related('current_patient').order_by('bed_number')
    return render(request, 'facility/room_detail.html', {
        'room': room, 'beds': beds, 'status_choices': Room.Status.choices,
    })


@require_POST
@hms_permission_required('core.manage_facilities')
def room_update_status(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    new_status = request.POST.get('status', '').strip()
    if new_status not in {c[0] for c in Room.Status.choices}:
        messages.error(request, 'Invalid status.')
        return redirect('room_detail', room_id=room.pk)
    old_status = room.status
    room.status = new_status
    room.save(update_fields=['status'])
    log_action(
        request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
        object_type='Room', object_id=room.pk, object_repr=str(room),
        description=f'Room {room.room_number} status changed: {old_status} → {new_status}', request=request,
    )
    messages.success(request, f'Room status updated to "{room.get_status_display()}".')
    return redirect('room_detail', room_id=room.pk)


# ── Beds ──────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_facilities')
def bed_create(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    if request.method == 'POST':
        p = request.POST
        try:
            bed = Bed(
                room=room, bed_number=p['bed_number'].strip(), bed_code=p['bed_code'].strip(),
            )
            bed.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.FACILITY,
                object_type='Bed', object_id=bed.pk, object_repr=bed.bed_code,
                description=f'Bed {bed.bed_code} created in room {room.room_number}', request=request,
            )
            messages.success(request, f'Bed {bed.bed_code} created.')
            return redirect('room_detail', room_id=room.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
    return render(request, 'facility/bed_form.html', {'room': room, 'action': 'Create'})


@hms_permission_required('core.manage_facilities')
def bed_edit(request, bed_id):
    bed = get_object_or_404(Bed, pk=bed_id)
    if request.method == 'POST':
        p = request.POST
        try:
            bed.bed_number = p['bed_number'].strip()
            bed.bed_code = p['bed_code'].strip()
            bed.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
                object_type='Bed', object_id=bed.pk, object_repr=bed.bed_code,
                description=f'Bed {bed.bed_code} updated', request=request,
            )
            messages.success(request, f'Bed {bed.bed_code} updated.')
            return redirect('room_detail', room_id=bed.room_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')
    return render(request, 'facility/bed_form.html', {'bed': bed, 'room': bed.room, 'action': 'Edit'})


@require_POST
@hms_permission_required('core.manage_facilities')
def bed_update_status(request, bed_id):
    bed = get_object_or_404(Bed, pk=bed_id)
    new_status = request.POST.get('status', '').strip()
    if new_status not in {c[0] for c in Bed.Status.choices}:
        messages.error(request, 'Invalid status.')
        return redirect('room_detail', room_id=bed.room_id)
    if bed.status == Bed.Status.OCCUPIED and new_status != Bed.Status.OCCUPIED and bed.admissions.filter(status=Admission.Status.ADMITTED).exists():
        messages.error(request, f'Bed {bed.bed_code} has an active admission — discharge or transfer the patient first.')
        return redirect('room_detail', room_id=bed.room_id)
    old_status = bed.status
    bed.status = new_status
    if new_status != Bed.Status.OCCUPIED:
        bed.current_patient = None
    bed.save(update_fields=['status', 'current_patient'])
    log_action(
        request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
        object_type='Bed', object_id=bed.pk, object_repr=bed.bed_code,
        description=f'Bed {bed.bed_code} status changed: {old_status} → {new_status}', request=request,
    )
    messages.success(request, f'Bed status updated to "{bed.get_status_display()}".')
    return redirect('room_detail', room_id=bed.room_id)


# ── Admission / Transfer / Discharge ──────────────────────────────────────────

def _redirect_after_bed_action(request, visit_id):
    """Send the user back to the patient's chart if they're allowed to see
    it; otherwise fall back to the facility dashboard. Not every role that
    can admit/transfer/discharge (assign_bed) also has view_patient_flow —
    redirecting them into that page anyway would bounce a successful action
    straight into an access-denied wall."""
    if visit_id and request.user.has_perm('core.view_patient_flow'):
        return redirect('patient_journey_detail', visit_id=visit_id)
    return redirect('facility_dashboard')


def _available_beds_qs(department=None, ward_type=None, room_type=None, gender=None):
    qs = Bed.objects.filter(
        status=Bed.Status.AVAILABLE, room__status=Room.Status.ACTIVE, ward__status=Ward.Status.ACTIVE,
    ).select_related('room', 'ward')
    if department:
        qs = qs.filter(ward__department_id=department)
    if ward_type:
        qs = qs.filter(ward__ward_type=ward_type)
    if room_type:
        qs = qs.filter(room__room_type=room_type)
    if gender:
        qs = qs.filter(Q(ward__gender_restriction=gender) | Q(ward__gender_restriction=Ward.Gender.MIXED))
    return qs.order_by('ward__name', 'room__room_number', 'bed_number')


def _perform_admission(patient, visit, bed, admitted_by, admission_request=None, priority=None):
    """Create the Admission row and flip the bed to Occupied. Shared by the
    one-step "Quick Admit" fast path (admission_create below) and the formal
    AdmissionRequest → deposit → bed-assignment pipeline
    (views_admissions.admission_request_assign_bed) — both must converge on
    the exact same admission side-effects."""
    kwargs = dict(patient=patient, visit=visit, bed=bed, admitted_by=admitted_by)
    if admission_request is not None:
        kwargs['admission_request'] = admission_request
    if priority:
        kwargs['priority'] = priority
    admission = Admission.objects.create(**kwargs)
    bed.status = Bed.Status.OCCUPIED
    bed.current_patient = patient
    bed.save(update_fields=['status', 'current_patient'])
    return admission


def _visit_balance_outstanding(visit):
    """Sum of unpaid balances across all non-terminal invoice items tied to
    this visit — the settlement check gating bed release in patient_discharge."""
    if visit is None:
        return 0
    items = InvoiceItem.objects.filter(invoice__visit=visit).exclude(
        payment_status__in=[InvoiceItem.PaymentStatus.CANCELLED, InvoiceItem.PaymentStatus.REFUNDED],
    )
    total = sum((item.balance for item in items if not item.payment_cleared), 0)
    return total


@hms_permission_required('core.assign_bed')
def admission_create(request, visit_id):
    visit = get_object_or_404(Visit.objects.select_related('patient'), pk=visit_id)
    patient = visit.patient

    if Admission.objects.filter(patient=patient, status=Admission.Status.ADMITTED).exists():
        messages.warning(request, f'{patient.full_name} already has an active admission.')
        return _redirect_after_bed_action(request, visit.pk)

    department = request.GET.get('department', '')
    ward_type = request.GET.get('ward_type', '')
    room_type = request.GET.get('room_type', '')
    gender = patient.sex if patient.sex in ('Male', 'Female') else ''
    available_beds = _available_beds_qs(department, ward_type, room_type, gender)

    if request.method == 'POST':
        bed_id = request.POST.get('bed_id')
        bed = available_beds.filter(pk=bed_id).first()
        if not bed:
            messages.error(request, 'That bed is no longer available. Please select another.')
            return redirect('admission_create', visit_id=visit_id)
        try:
            with transaction.atomic():
                admission = _perform_admission(patient, visit, bed, request.user)
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.FACILITY,
                object_type='Admission', object_id=admission.pk, object_repr=f'{patient.full_name} → {bed.bed_code}',
                description=f'{patient.full_name} admitted to bed {bed.bed_code} ({bed.room.ward.name})',
                request=request,
            )
            messages.success(request, f'{patient.full_name} admitted to bed {bed.bed_code}.')
            return _redirect_after_bed_action(request, visit.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'facility/admission_form.html', {
        'visit': visit, 'patient': patient, 'available_beds': available_beds,
        'departments': Department.objects.filter(is_active=True), 'ward_types': Ward.WardType.choices,
        'room_types': Room.RoomType.choices,
        'department_f': department, 'ward_type_f': ward_type, 'room_type_f': room_type,
        'mode': 'admit',
    })


@hms_permission_required('core.assign_bed')
def patient_transfer(request, admission_id):
    current = get_object_or_404(Admission.objects.select_related('patient', 'bed__room__ward', 'visit'), pk=admission_id)
    if current.status != Admission.Status.ADMITTED:
        messages.error(request, 'This admission is not currently active.')
        return redirect('facility_dashboard')

    department = request.GET.get('department', '')
    ward_type = request.GET.get('ward_type', '')
    room_type = request.GET.get('room_type', '')
    available_beds = _available_beds_qs(department, ward_type, room_type).exclude(pk=current.bed_id)

    if request.method == 'POST':
        bed_id = request.POST.get('bed_id')
        new_bed = available_beds.filter(pk=bed_id).first()
        reason = request.POST.get('transfer_reason', '').strip()
        if not new_bed:
            messages.error(request, 'That bed is no longer available. Please select another.')
            return redirect('patient_transfer', admission_id=admission_id)
        try:
            with transaction.atomic():
                old_bed = current.bed
                now = timezone.now()
                current.status = Admission.Status.TRANSFERRED
                current.discharged_at = now
                current.discharged_by = request.user
                current.transfer_reason = reason
                current.save()

                old_bed.status = Bed.Status.AVAILABLE
                old_bed.current_patient = None
                old_bed.save(update_fields=['status', 'current_patient'])

                new_admission = Admission.objects.create(
                    patient=current.patient, visit=current.visit, bed=new_bed,
                    admitted_by=request.user, admitted_at=now,
                )
                new_bed.status = Bed.Status.OCCUPIED
                new_bed.current_patient = current.patient
                new_bed.save(update_fields=['status', 'current_patient'])
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
                object_type='Admission', object_id=new_admission.pk,
                object_repr=f'{current.patient.full_name} {old_bed.bed_code} → {new_bed.bed_code}',
                description=f'{current.patient.full_name} transferred from {old_bed.bed_code} to {new_bed.bed_code}. Reason: {reason or "—"}',
                request=request,
            )
            messages.success(request, f'{current.patient.full_name} transferred to bed {new_bed.bed_code}.')
            return _redirect_after_bed_action(request, current.visit_id)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'facility/admission_form.html', {
        'admission': current, 'patient': current.patient, 'available_beds': available_beds,
        'departments': Department.objects.filter(is_active=True), 'ward_types': Ward.WardType.choices,
        'room_types': Room.RoomType.choices,
        'department_f': department, 'ward_type_f': ward_type, 'room_type_f': room_type,
        'mode': 'transfer',
    })


@require_POST
@hms_permission_required('core.assign_bed')
def patient_discharge(request, admission_id):
    admission = get_object_or_404(Admission.objects.select_related('patient', 'bed', 'visit'), pk=admission_id)
    if admission.status != Admission.Status.ADMITTED:
        messages.error(request, 'This admission is not currently active.')
        return redirect('facility_dashboard')

    outstanding = _visit_balance_outstanding(admission.visit)
    if outstanding > 0:
        messages.error(
            request,
            f'Cannot discharge {admission.patient.full_name} — outstanding inpatient balance of '
            f'ETB {outstanding} must be settled or waived first.',
        )
        return _redirect_after_bed_action(request, admission.visit_id)
    if admission.discharge_approved_by_id is None:
        messages.error(
            request,
            f'Cannot discharge {admission.patient.full_name} — a doctor must approve discharge first.',
        )
        return _redirect_after_bed_action(request, admission.visit_id)

    with transaction.atomic():
        admission.status = Admission.Status.DISCHARGED
        admission.discharged_at = timezone.now()
        admission.discharged_by = request.user
        admission.save()
        bed = admission.bed
        bed.status = Bed.Status.AVAILABLE
        bed.current_patient = None
        bed.save(update_fields=['status', 'current_patient'])
    log_action(
        request.user, AuditLog.Action.UPDATE, AuditLog.Module.FACILITY,
        object_type='Admission', object_id=admission.pk, object_repr=f'{admission.patient.full_name} ({bed.bed_code})',
        description=f'{admission.patient.full_name} discharged from bed {bed.bed_code}', request=request,
    )
    messages.success(request, f'{admission.patient.full_name} discharged.')
    return _redirect_after_bed_action(request, admission.visit_id)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_facility')
def facility_dashboard(request):
    today = timezone.localdate()

    total_or = ORRoom.objects.count()
    available_or = ORRoom.objects.filter(availability_status=ORRoom.Availability.AVAILABLE).count()

    total_wards = Ward.objects.count()
    total_rooms = Room.objects.count()
    total_beds = Bed.objects.count()
    occupied_beds = Bed.objects.filter(status=Bed.Status.OCCUPIED).count()
    available_beds = Bed.objects.filter(status=Bed.Status.AVAILABLE).count()
    maintenance_beds = Bed.objects.filter(status__in=[Bed.Status.MAINTENANCE, Bed.Status.OUT_OF_SERVICE]).count()

    non_maintenance = total_beds - maintenance_beds
    bed_occupancy_rate = round((occupied_beds / non_maintenance) * 100, 1) if non_maintenance else 0

    surgeries_today = 0
    try:
        from .models import SurgeryOrder
        surgeries_today = SurgeryOrder.objects.filter(schedule__scheduled_date=today).count()
    except Exception:
        pass
    max_capacity = ORRoom.objects.filter(
        availability_status=ORRoom.Availability.AVAILABLE, max_surgeries_per_day__isnull=False,
    ).aggregate(t=Count('max_surgeries_per_day'))['t'] or 0
    or_utilization_rate = round((surgeries_today / max_capacity) * 100, 1) if max_capacity else 0

    return render(request, 'facility/dashboard.html', {
        'total_or': total_or, 'available_or': available_or,
        'total_wards': total_wards, 'total_rooms': total_rooms, 'total_beds': total_beds,
        'occupied_beds': occupied_beds, 'available_beds': available_beds, 'maintenance_beds': maintenance_beds,
        'bed_occupancy_rate': bed_occupancy_rate, 'or_utilization_rate': or_utilization_rate,
        'surgeries_today': surgeries_today,
    })
