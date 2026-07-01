"""
Operating Room Scheduling Module — views_or.py
"""
import json
from datetime import datetime, date as date_type, timedelta, time as time_type

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog, Department, Doctor, OperatingRoom, ORSchedule,
    ORScheduleStatusLog, Patient, SurgeryRequest,
)

User = get_user_model()


# ── Helper ────────────────────────────────────────────────────────────────────

def _log(user, action, module, desc):
    try:
        log_action(user, action, module, description=desc)
    except Exception:
        pass


def _priority_sort_key(sr):
    return {'Emergency': 0, 'Urgent': 1, 'Elective': 2}.get(sr.priority, 3)


def _compute_end_time(start: time_type, duration_minutes: int) -> time_type:
    start_dt = datetime.combine(date_type.today(), start)
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    return end_dt.time()


def _check_or_conflicts(or_room, date, start_time, end_time, exclude_pk=None):
    """
    Return list of conflicting ORSchedule objects.
    Excludes Cancelled and Postponed from conflict check.
    """
    qs = ORSchedule.objects.filter(
        operating_room=or_room,
        date=date,
    ).exclude(
        status__in=[ORSchedule.Status.CANCELLED, ORSchedule.Status.POSTPONED]
    ).select_related('surgery_request', 'primary_surgeon')

    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    conflicts = []
    for sched in qs:
        # overlap: new_start < existing_end AND new_end > existing_start
        if start_time < sched.end_time and end_time > sched.start_time:
            conflicts.append(sched)
    return conflicts


def _check_surgeon_conflicts(surgeon, date, start_time, end_time, exclude_pk=None):
    """Return conflicting schedules for a given surgeon (primary or assistant)."""
    qs = ORSchedule.objects.filter(
        date=date,
    ).filter(
        Q(primary_surgeon=surgeon) | Q(assistant_surgeon=surgeon)
    ).exclude(
        status__in=[ORSchedule.Status.CANCELLED, ORSchedule.Status.POSTPONED]
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    conflicts = []
    for sched in qs:
        if start_time < sched.end_time and end_time > sched.start_time:
            conflicts.append(sched)
    return conflicts


# ── 1. Dashboard ──────────────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def or_dashboard(request):
    today = timezone.localdate()

    today_schedules = ORSchedule.objects.filter(date=today).select_related(
        'surgery_request', 'operating_room', 'primary_surgeon',
        'surgery_request__patient',
    )

    status_counts = {
        'scheduled': today_schedules.filter(status=ORSchedule.Status.SCHEDULED).count(),
        'in_or':     today_schedules.filter(status=ORSchedule.Status.IN_OR).count(),
        'started':   today_schedules.filter(status=ORSchedule.Status.SURGERY_STARTED).count(),
        'completed': today_schedules.filter(status=ORSchedule.Status.SURGERY_COMPLETED).count(),
        'cancelled': today_schedules.filter(status=ORSchedule.Status.CANCELLED).count(),
    }
    total_today = today_schedules.exclude(
        status=ORSchedule.Status.CANCELLED
    ).count()

    active_ops = today_schedules.filter(
        status__in=[ORSchedule.Status.IN_OR, ORSchedule.Status.SURGERY_STARTED]
    )

    total_active_ors = OperatingRoom.objects.filter(is_active=True).count()
    occupied_or_ids = active_ops.values_list('operating_room_id', flat=True).distinct()
    available_ors = total_active_ors - len(set(occupied_or_ids))

    upcoming = ORSchedule.objects.filter(
        date__gt=today,
        date__lte=today + timedelta(days=7),
        status=ORSchedule.Status.SCHEDULED,
    ).select_related(
        'surgery_request__patient', 'operating_room', 'primary_surgeon',
    ).order_by('date', 'start_time')[:15]

    pending_requests = list(SurgeryRequest.objects.filter(
        status=SurgeryRequest.Status.REQUESTED,
    ).select_related('patient', 'requested_by', 'department'))
    pending_requests.sort(key=_priority_sort_key)

    return render(request, 'or_schedule/dashboard.html', {
        'today': today,
        'total_today': total_today,
        'status_counts': status_counts,
        'active_ops': active_ops,
        'available_ors': available_ors,
        'total_active_ors': total_active_ors,
        'upcoming': upcoming,
        'pending_requests': pending_requests,
    })


# ── 2. Surgery Request List ───────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def surgery_request_list(request):
    qs = SurgeryRequest.objects.select_related(
        'patient', 'requested_by', 'department',
    ).order_by('-created_at')

    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    dept_filter = request.GET.get('department_id', '')
    q = request.GET.get('q', '').strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if priority_filter:
        qs = qs.filter(priority=priority_filter)
    if dept_filter:
        qs = qs.filter(department_id=dept_filter)
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q)
            | Q(patient__last_name__icontains=q)
            | Q(patient__card_number__icontains=q)
            | Q(procedure_name__icontains=q)
            | Q(request_number__icontains=q)
        )

    # KPIs
    all_reqs = SurgeryRequest.objects.all()
    kpis = {
        'total_requested': all_reqs.filter(status=SurgeryRequest.Status.REQUESTED).count(),
        'total_approved':  all_reqs.filter(status=SurgeryRequest.Status.APPROVED).count(),
        'total_scheduled': all_reqs.filter(status=SurgeryRequest.Status.SCHEDULED).count(),
        'pending_emergency': all_reqs.filter(
            status=SurgeryRequest.Status.REQUESTED,
            priority=SurgeryRequest.Priority.EMERGENCY,
        ).count(),
    }

    departments = Department.objects.filter(is_active=True)
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'or_schedule/surgery_request_list.html', {
        'page_obj': page_obj,
        'kpis': kpis,
        'departments': departments,
        'status_choices': SurgeryRequest.Status.choices,
        'priority_choices': SurgeryRequest.Priority.choices,
        'status_filter': status_filter,
        'priority_filter': priority_filter,
        'dept_filter': dept_filter,
        'q': q,
    })


# ── 3. Surgery Request Create ─────────────────────────────────────────────────

@hms_permission_required('core.order_surgery')
def surgery_request_create(request):
    doctors = Doctor.objects.filter(active=True).select_related('department')
    departments = Department.objects.filter(is_active=True)

    # Resolve requesting doctor
    try:
        requesting_doctor = request.user.doctor_profile
    except Exception:
        requesting_doctor = Doctor.objects.first()

    if request.method == 'POST':
        if not requesting_doctor:
            messages.error(request, 'No doctor profile found. Please contact administrator.')
            return render(request, 'or_schedule/surgery_request_form.html', {
                'doctors': doctors,
                'departments': departments,
                'anesthesia_choices': SurgeryRequest.AnesthesiaType.choices,
                'priority_choices': SurgeryRequest.Priority.choices,
                'admission_choices': SurgeryRequest.AdmissionStatus.choices,
                'post': request.POST,
            })

        patient_id = request.POST.get('patient_id')
        procedure_name = request.POST.get('procedure_name', '').strip()
        diagnosis = request.POST.get('diagnosis', '').strip()

        errors = []
        if not patient_id:
            errors.append('Patient is required.')
        if not procedure_name:
            errors.append('Procedure name is required.')
        if not diagnosis:
            errors.append('Diagnosis is required.')

        patient = None
        if patient_id:
            try:
                patient = Patient.objects.get(pk=patient_id)
            except Patient.DoesNotExist:
                errors.append('Invalid patient selected.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'or_schedule/surgery_request_form.html', {
                'doctors': doctors,
                'departments': departments,
                'anesthesia_choices': SurgeryRequest.AnesthesiaType.choices,
                'priority_choices': SurgeryRequest.Priority.choices,
                'admission_choices': SurgeryRequest.AdmissionStatus.choices,
                'post': request.POST,
                'selected_patient': patient,
            })

        dept_id = request.POST.get('department_id') or None
        dept = None
        if dept_id:
            try:
                dept = Department.objects.get(pk=dept_id)
            except Department.DoesNotExist:
                pass

        sr = SurgeryRequest.objects.create(
            patient=patient,
            requested_by=requesting_doctor,
            department=dept,
            diagnosis=diagnosis,
            procedure_name=procedure_name,
            procedure_code=request.POST.get('procedure_code', '').strip(),
            anesthesia_type=request.POST.get('anesthesia_type', SurgeryRequest.AnesthesiaType.GENERAL),
            priority=request.POST.get('priority', SurgeryRequest.Priority.ELECTIVE),
            admission_status=request.POST.get('admission_status', SurgeryRequest.AdmissionStatus.INPATIENT),
            expected_duration=int(request.POST.get('expected_duration', 60) or 60),
            required_equipment=request.POST.get('required_equipment', '').strip(),
            required_implants=request.POST.get('required_implants', '').strip(),
            preop_notes=request.POST.get('preop_notes', '').strip(),
            anesthesia_notes=request.POST.get('anesthesia_notes', '').strip(),
            notes=request.POST.get('notes', '').strip(),
            status=SurgeryRequest.Status.REQUESTED,
            created_by=request.user,
        )

        _log(request.user, AuditLog.Action.CREATE, AuditLog.Module.SURGERY,
             f'Surgery request {sr.request_number} created for {patient.full_name}')
        messages.success(request, f'Surgery request {sr.request_number} created successfully.')
        return redirect('surgery_request_detail', pk=sr.pk)

    return render(request, 'or_schedule/surgery_request_form.html', {
        'doctors': doctors,
        'departments': departments,
        'anesthesia_choices': SurgeryRequest.AnesthesiaType.choices,
        'priority_choices': SurgeryRequest.Priority.choices,
        'admission_choices': SurgeryRequest.AdmissionStatus.choices,
        'post': {},
    })


# ── 4. Surgery Request Detail ─────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def surgery_request_detail(request, pk):
    sr = get_object_or_404(
        SurgeryRequest.objects.select_related(
            'patient', 'requested_by', 'department',
            'approved_by', 'created_by',
        ),
        pk=pk,
    )

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'approve':
            if not request.user.has_perm('core.approve_surgery_order'):
                messages.error(request, 'You do not have permission to approve surgery requests.')
            elif sr.status != SurgeryRequest.Status.REQUESTED:
                messages.error(request, 'Only Requested surgeries can be approved.')
            else:
                sr.status = SurgeryRequest.Status.APPROVED
                sr.approved_by = request.user
                sr.approved_at = timezone.now()
                sr.save()
                _log(request.user, AuditLog.Action.APPROVE, AuditLog.Module.SURGERY,
                     f'Surgery request {sr.request_number} approved')
                messages.success(request, 'Surgery request approved.')

        elif action == 'reject':
            if not request.user.has_perm('core.approve_surgery_order'):
                messages.error(request, 'You do not have permission to reject surgery requests.')
            elif sr.status not in (SurgeryRequest.Status.REQUESTED, SurgeryRequest.Status.APPROVED):
                messages.error(request, 'Cannot reject a scheduled or cancelled request.')
            else:
                reason = request.POST.get('rejection_reason', '').strip()
                if not reason:
                    messages.error(request, 'Please provide a rejection reason.')
                else:
                    sr.status = SurgeryRequest.Status.CANCELLED
                    sr.rejection_reason = reason
                    sr.save()
                    _log(request.user, AuditLog.Action.CANCEL, AuditLog.Module.SURGERY,
                         f'Surgery request {sr.request_number} rejected: {reason[:100]}')
                    messages.success(request, 'Surgery request rejected.')

        elif action == 'schedule':
            if not request.user.has_perm('core.manage_or_schedule'):
                messages.error(request, 'You do not have permission to schedule surgeries.')
            else:
                return redirect(f'/or/schedule/new/?request_id={sr.pk}')

        return redirect('surgery_request_detail', pk=sr.pk)

    # Try to fetch linked schedule
    linked_schedule = None
    try:
        linked_schedule = sr.or_schedule
    except ORSchedule.DoesNotExist:
        pass

    return render(request, 'or_schedule/surgery_request_detail.html', {
        'sr': sr,
        'linked_schedule': linked_schedule,
    })


# ── 5. OR Schedule List ───────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def or_schedule_list(request):
    qs = ORSchedule.objects.select_related(
        'surgery_request__patient', 'operating_room',
        'primary_surgeon', 'scheduled_by',
    ).order_by('-date', 'start_time')

    status_filter = request.GET.get('status', '')
    date_filter = request.GET.get('date', '')
    or_room_filter = request.GET.get('or_room_id', '')
    surgeon_filter = request.GET.get('surgeon_id', '')
    q = request.GET.get('q', '').strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if date_filter:
        try:
            qs = qs.filter(date=date_filter)
        except ValueError:
            pass
    if or_room_filter:
        qs = qs.filter(operating_room_id=or_room_filter)
    if surgeon_filter:
        qs = qs.filter(primary_surgeon_id=surgeon_filter)
    if q:
        qs = qs.filter(
            Q(schedule_number__icontains=q)
            | Q(surgery_request__patient__first_name__icontains=q)
            | Q(surgery_request__patient__last_name__icontains=q)
            | Q(surgery_request__patient__card_number__icontains=q)
            | Q(surgery_request__procedure_name__icontains=q)
        )

    today = timezone.localdate()
    today_qs = ORSchedule.objects.filter(date=today)
    kpis = {
        'today_scheduled': today_qs.filter(status=ORSchedule.Status.SCHEDULED).count(),
        'today_completed': today_qs.filter(status=ORSchedule.Status.SURGERY_COMPLETED).count(),
        'active_now': today_qs.filter(
            status__in=[ORSchedule.Status.IN_OR, ORSchedule.Status.SURGERY_STARTED]
        ).count(),
        'pending_approval': ORSchedule.objects.filter(status=ORSchedule.Status.APPROVED).count(),
    }

    or_rooms = OperatingRoom.objects.filter(is_active=True)
    surgeons = Doctor.objects.filter(active=True)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'or_schedule/or_schedule_list.html', {
        'page_obj': page_obj,
        'kpis': kpis,
        'or_rooms': or_rooms,
        'surgeons': surgeons,
        'status_choices': ORSchedule.Status.choices,
        'status_filter': status_filter,
        'date_filter': date_filter,
        'or_room_filter': or_room_filter,
        'surgeon_filter': surgeon_filter,
        'q': q,
    })


# ── 6. OR Schedule Create ─────────────────────────────────────────────────────

@hms_permission_required('core.manage_or_schedule')
def or_schedule_create(request):
    request_id = request.GET.get('request_id') or request.POST.get('request_id')
    if not request_id:
        messages.error(request, 'No surgery request specified.')
        return redirect('surgery_request_list')

    sr = get_object_or_404(
        SurgeryRequest.objects.select_related('patient', 'requested_by', 'department'),
        pk=request_id,
    )

    if sr.status != SurgeryRequest.Status.APPROVED:
        messages.error(request, 'Only Approved surgery requests can be scheduled.')
        return redirect('surgery_request_detail', pk=sr.pk)

    # Check if already scheduled
    try:
        existing = sr.or_schedule
        messages.warning(request, f'This request already has schedule {existing.schedule_number}.')
        return redirect('or_schedule_detail', pk=existing.pk)
    except ORSchedule.DoesNotExist:
        pass

    or_rooms = OperatingRoom.objects.filter(is_active=True)
    surgeons = Doctor.objects.filter(active=True).select_related('department')
    users = User.objects.filter(is_active=True).order_by('last_name', 'first_name')
    conflict_errors = []

    if request.method == 'POST':
        or_room_id = request.POST.get('operating_room_id')
        date_str = request.POST.get('date', '')
        start_time_str = request.POST.get('start_time', '')
        primary_surgeon_id = request.POST.get('primary_surgeon_id')
        assistant_surgeon_id = request.POST.get('assistant_surgeon_id') or None
        anesthetist_id = request.POST.get('anesthetist_id') or None
        scrub_nurse_id = request.POST.get('scrub_nurse_id') or None
        circulating_nurse_id = request.POST.get('circulating_nurse_id') or None
        notes = request.POST.get('notes', '').strip()

        errors = []
        if not or_room_id:
            errors.append('Operating room is required.')
        if not date_str:
            errors.append('Date is required.')
        if not start_time_str:
            errors.append('Start time is required.')
        if not primary_surgeon_id:
            errors.append('Primary surgeon is required.')

        parsed_date = None
        parsed_start = None
        parsed_end = None

        if date_str:
            try:
                parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                errors.append('Invalid date format.')

        if start_time_str:
            try:
                parsed_start = datetime.strptime(start_time_str, '%H:%M').time()
                parsed_end = _compute_end_time(parsed_start, sr.expected_duration)
            except ValueError:
                errors.append('Invalid start time format.')

        or_room = None
        if or_room_id:
            try:
                or_room = OperatingRoom.objects.get(pk=or_room_id)
            except OperatingRoom.DoesNotExist:
                errors.append('Invalid operating room.')

        primary_surgeon = None
        if primary_surgeon_id:
            try:
                primary_surgeon = Doctor.objects.get(pk=primary_surgeon_id)
            except Doctor.DoesNotExist:
                errors.append('Invalid primary surgeon.')

        assistant_surgeon = None
        if assistant_surgeon_id:
            try:
                assistant_surgeon = Doctor.objects.get(pk=assistant_surgeon_id)
            except Doctor.DoesNotExist:
                pass

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'or_schedule/or_schedule_form.html', {
                'sr': sr,
                'or_rooms': or_rooms,
                'surgeons': surgeons,
                'users': users,
                'post': request.POST,
            })

        # Conflict checks
        with transaction.atomic():
            or_conflicts = _check_or_conflicts(or_room, parsed_date, parsed_start, parsed_end)
            if or_conflicts:
                for c in or_conflicts:
                    conflict_errors.append(
                        f'OR conflict with {c.schedule_number} ({c.surgery_request.procedure_name}) '
                        f'{c.start_time:%H:%M}–{c.end_time:%H:%M}'
                    )

            surgeon_conflicts = _check_surgeon_conflicts(primary_surgeon, parsed_date, parsed_start, parsed_end)
            if surgeon_conflicts:
                for c in surgeon_conflicts:
                    conflict_errors.append(
                        f'Surgeon conflict: Dr. {primary_surgeon.full_name} already has '
                        f'{c.schedule_number} ({c.start_time:%H:%M}–{c.end_time:%H:%M})'
                    )

            if conflict_errors:
                for e in conflict_errors:
                    messages.error(request, e)
                return render(request, 'or_schedule/or_schedule_form.html', {
                    'sr': sr,
                    'or_rooms': or_rooms,
                    'surgeons': surgeons,
                    'users': users,
                    'post': request.POST,
                    'conflict_errors': conflict_errors,
                })

            anesthetist = User.objects.filter(pk=anesthetist_id).first() if anesthetist_id else None
            scrub_nurse = User.objects.filter(pk=scrub_nurse_id).first() if scrub_nurse_id else None
            circulating_nurse = User.objects.filter(pk=circulating_nurse_id).first() if circulating_nurse_id else None

            schedule = ORSchedule.objects.create(
                surgery_request=sr,
                operating_room=or_room,
                date=parsed_date,
                start_time=parsed_start,
                end_time=parsed_end,
                primary_surgeon=primary_surgeon,
                assistant_surgeon=assistant_surgeon,
                anesthetist=anesthetist,
                scrub_nurse=scrub_nurse,
                circulating_nurse=circulating_nurse,
                status=ORSchedule.Status.SCHEDULED,
                notes=notes,
                scheduled_by=request.user,
            )

            # Update surgery request status
            sr.status = SurgeryRequest.Status.SCHEDULED
            sr.save()

            # Create status log
            ORScheduleStatusLog.objects.create(
                schedule=schedule,
                from_status='',
                to_status=ORSchedule.Status.SCHEDULED,
                changed_by=request.user,
                notes=f'Schedule created by {request.user.get_full_name() or request.user.username}',
            )

            _log(request.user, AuditLog.Action.CREATE, AuditLog.Module.SURGERY,
                 f'OR Schedule {schedule.schedule_number} created for {sr.procedure_name}')
            messages.success(request, f'Schedule {schedule.schedule_number} created successfully.')
            return redirect('or_schedule_detail', pk=schedule.pk)

    return render(request, 'or_schedule/or_schedule_form.html', {
        'sr': sr,
        'or_rooms': or_rooms,
        'surgeons': surgeons,
        'users': users,
        'post': {},
    })


# ── 7. OR Schedule Detail ─────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def or_schedule_detail(request, pk):
    schedule = get_object_or_404(
        ORSchedule.objects.select_related(
            'surgery_request__patient',
            'surgery_request__requested_by',
            'surgery_request__department',
            'operating_room',
            'primary_surgeon',
            'assistant_surgeon',
            'anesthetist',
            'scrub_nurse',
            'circulating_nurse',
            'scheduled_by',
        ),
        pk=pk,
    )
    status_logs = schedule.status_logs.select_related('changed_by').order_by('changed_at')

    if request.method == 'POST':
        action = request.POST.get('action')
        now = timezone.now()
        old_status = schedule.status

        def _update_status(new_status, note=''):
            schedule.status = new_status
            schedule.save()
            ORScheduleStatusLog.objects.create(
                schedule=schedule,
                from_status=old_status,
                to_status=new_status,
                changed_by=request.user,
                notes=note,
            )
            _log(request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
                 f'OR Schedule {schedule.schedule_number} status → {new_status}')

        if action == 'patient_prepared':
            schedule.patient_prepared_at = now
            _update_status(ORSchedule.Status.PATIENT_PREPARED, 'Patient prepared for OR')
            messages.success(request, 'Status updated: Patient Prepared.')

        elif action == 'in_or':
            schedule.in_or_at = now
            _update_status(ORSchedule.Status.IN_OR, 'Patient transferred to OR')
            messages.success(request, 'Status updated: In OR.')

        elif action == 'start_surgery':
            schedule.surgery_started_at = now
            _update_status(ORSchedule.Status.SURGERY_STARTED, 'Surgery started')
            messages.success(request, 'Status updated: Surgery Started.')

        elif action == 'complete_surgery':
            schedule.surgery_completed_at = now
            _update_status(ORSchedule.Status.SURGERY_COMPLETED, 'Surgery completed')
            messages.success(request, 'Status updated: Surgery Completed.')

        elif action == 'cancel':
            if not request.user.has_perm('core.manage_or_schedule'):
                messages.error(request, 'You do not have permission to cancel schedules.')
            else:
                reason = request.POST.get('cancellation_reason', '').strip()
                if not reason:
                    messages.error(request, 'Cancellation reason is required.')
                else:
                    schedule.cancellation_reason = reason
                    _update_status(ORSchedule.Status.CANCELLED, f'Cancelled: {reason[:200]}')
                    messages.success(request, 'Schedule cancelled.')

        elif action == 'postpone':
            if not request.user.has_perm('core.manage_or_schedule'):
                messages.error(request, 'You do not have permission to postpone schedules.')
            else:
                reason = request.POST.get('postpone_reason', '').strip()
                if not reason:
                    messages.error(request, 'Postpone reason is required.')
                else:
                    schedule.postpone_reason = reason
                    _update_status(ORSchedule.Status.POSTPONED, f'Postponed: {reason[:200]}')
                    messages.success(request, 'Schedule postponed.')

        return redirect('or_schedule_detail', pk=pk)

    return render(request, 'or_schedule/or_schedule_detail.html', {
        'schedule': schedule,
        'sr': schedule.surgery_request,
        'status_logs': status_logs,
    })


# ── 8. OR Schedule Edit ───────────────────────────────────────────────────────

@hms_permission_required('core.manage_or_schedule')
def or_schedule_edit(request, pk):
    schedule = get_object_or_404(ORSchedule, pk=pk)

    if schedule.status not in (ORSchedule.Status.SCHEDULED, ORSchedule.Status.APPROVED):
        messages.error(request, 'Only Scheduled or Approved schedules can be edited.')
        return redirect('or_schedule_detail', pk=pk)

    sr = schedule.surgery_request
    or_rooms = OperatingRoom.objects.filter(is_active=True)
    surgeons = Doctor.objects.filter(active=True).select_related('department')
    users = User.objects.filter(is_active=True).order_by('last_name', 'first_name')

    if request.method == 'POST':
        or_room_id = request.POST.get('operating_room_id')
        date_str = request.POST.get('date', '')
        start_time_str = request.POST.get('start_time', '')
        primary_surgeon_id = request.POST.get('primary_surgeon_id')
        notes = request.POST.get('notes', '').strip()

        errors = []
        parsed_date = None
        parsed_start = None
        parsed_end = None

        if date_str:
            try:
                parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                errors.append('Invalid date format.')
        else:
            errors.append('Date is required.')

        if start_time_str:
            try:
                parsed_start = datetime.strptime(start_time_str, '%H:%M').time()
                parsed_end = _compute_end_time(parsed_start, sr.expected_duration)
            except ValueError:
                errors.append('Invalid start time.')
        else:
            errors.append('Start time is required.')

        or_room = None
        if or_room_id:
            try:
                or_room = OperatingRoom.objects.get(pk=or_room_id)
            except OperatingRoom.DoesNotExist:
                errors.append('Invalid OR room.')
        else:
            errors.append('Operating room is required.')

        primary_surgeon = None
        if primary_surgeon_id:
            try:
                primary_surgeon = Doctor.objects.get(pk=primary_surgeon_id)
            except Doctor.DoesNotExist:
                errors.append('Invalid primary surgeon.')
        else:
            errors.append('Primary surgeon is required.')

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            # Conflict check (exclude self)
            or_conflicts = _check_or_conflicts(or_room, parsed_date, parsed_start, parsed_end, exclude_pk=pk)
            surgeon_conflicts = _check_surgeon_conflicts(primary_surgeon, parsed_date, parsed_start, parsed_end, exclude_pk=pk)

            if or_conflicts or surgeon_conflicts:
                for c in or_conflicts:
                    messages.error(request, f'OR conflict with {c.schedule_number} ({c.start_time:%H:%M}–{c.end_time:%H:%M})')
                for c in surgeon_conflicts:
                    messages.error(request, f'Surgeon conflict: Dr. {primary_surgeon.full_name} has {c.schedule_number}')
            else:
                schedule.operating_room = or_room
                schedule.date = parsed_date
                schedule.start_time = parsed_start
                schedule.end_time = parsed_end
                schedule.primary_surgeon = primary_surgeon

                asst_id = request.POST.get('assistant_surgeon_id') or None
                schedule.assistant_surgeon = Doctor.objects.filter(pk=asst_id).first() if asst_id else None
                schedule.anesthetist = User.objects.filter(pk=request.POST.get('anesthetist_id') or None).first()
                schedule.scrub_nurse = User.objects.filter(pk=request.POST.get('scrub_nurse_id') or None).first()
                schedule.circulating_nurse = User.objects.filter(pk=request.POST.get('circulating_nurse_id') or None).first()
                schedule.notes = notes
                schedule.save()

                ORScheduleStatusLog.objects.create(
                    schedule=schedule,
                    from_status=schedule.status,
                    to_status=schedule.status,
                    changed_by=request.user,
                    notes='Schedule details updated',
                )
                _log(request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
                     f'OR Schedule {schedule.schedule_number} updated')
                messages.success(request, 'Schedule updated successfully.')
                return redirect('or_schedule_detail', pk=pk)

    return render(request, 'or_schedule/or_schedule_edit.html', {
        'schedule': schedule,
        'sr': sr,
        'or_rooms': or_rooms,
        'surgeons': surgeons,
        'users': users,
    })


# ── 9. OR Calendar ────────────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def or_calendar(request):
    date_str = request.GET.get('date', '')
    view_mode = request.GET.get('view', 'day')

    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()

    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    # Time slots: 6:00 to 20:00 in 30-min increments
    time_slots = []
    slot_time = time_type(6, 0)
    while slot_time <= time_type(20, 0):
        time_slots.append(slot_time)
        dt = datetime.combine(date_type.today(), slot_time) + timedelta(minutes=30)
        slot_time = dt.time()

    or_rooms = OperatingRoom.objects.filter(is_active=True)

    if view_mode == 'week':
        # Week view: Mon–Sun of the selected date's week
        day_of_week = selected_date.weekday()
        week_start = selected_date - timedelta(days=day_of_week)
        week_days = [week_start + timedelta(days=i) for i in range(7)]
        schedules = ORSchedule.objects.filter(
            date__gte=week_start,
            date__lte=week_start + timedelta(days=6),
        ).exclude(
            status__in=[ORSchedule.Status.CANCELLED, ORSchedule.Status.POSTPONED]
        ).select_related(
            'surgery_request__patient', 'operating_room', 'primary_surgeon',
        )
        # Build week_grid: {day_str: {or_id: [schedules]}}
        week_grid = {}
        for day in week_days:
            week_grid[day] = {r.pk: [] for r in or_rooms}
        for s in schedules:
            if s.date in week_grid and s.operating_room_id in week_grid[s.date]:
                week_grid[s.date][s.operating_room_id].append(s)

        return render(request, 'or_schedule/or_calendar.html', {
            'selected_date': selected_date,
            'prev_date': prev_date,
            'next_date': next_date,
            'view_mode': view_mode,
            'or_rooms': or_rooms,
            'week_days': week_days,
            'week_grid': week_grid,
        })

    # Day view
    schedules = ORSchedule.objects.filter(
        date=selected_date,
    ).exclude(
        status__in=[ORSchedule.Status.CANCELLED, ORSchedule.Status.POSTPONED]
    ).select_related(
        'surgery_request__patient', 'operating_room', 'primary_surgeon',
    )

    # Build grid: {slot: {or_id: schedule or None}}
    # For display we also track "span" (how many 30-min slots a surgery occupies)
    schedule_by_or = {r.pk: [] for r in or_rooms}
    for s in schedules:
        if s.operating_room_id in schedule_by_or:
            schedule_by_or[s.operating_room_id].append(s)

    # Build cell grid for template
    # grid[slot_index][or_id] = {'schedule': s, 'is_start': bool, 'span': int} or None
    grid = []
    for slot in time_slots:
        slot_end = (datetime.combine(date_type.today(), slot) + timedelta(minutes=30)).time()
        row = {}
        for rm in or_rooms:
            cell = None
            for s in schedule_by_or.get(rm.pk, []):
                if s.start_time <= slot < s.end_time:
                    is_start = (s.start_time == slot) or (slot == time_slots[0] and s.start_time < time_slots[0])
                    # Compute span
                    start_dt = datetime.combine(date_type.today(), s.start_time)
                    end_dt = datetime.combine(date_type.today(), s.end_time)
                    span = max(1, int((end_dt - start_dt).total_seconds() / 1800))
                    cell = {'schedule': s, 'is_start': s.start_time == slot, 'span': span}
                    break
            row[rm.pk] = cell
        grid.append({'slot': slot, 'row': row})

    return render(request, 'or_schedule/or_calendar.html', {
        'selected_date': selected_date,
        'prev_date': prev_date,
        'next_date': next_date,
        'view_mode': view_mode,
        'or_rooms': or_rooms,
        'time_slots': time_slots,
        'grid': grid,
        'schedules': schedules,
    })


# ── 10. OR Room List ──────────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def or_room_list(request):
    rooms = OperatingRoom.objects.all()
    today = timezone.localdate()

    # Annotate each room with today's schedule count
    room_data = []
    for room in rooms:
        today_count = ORSchedule.objects.filter(
            operating_room=room, date=today
        ).exclude(
            status__in=[ORSchedule.Status.CANCELLED, ORSchedule.Status.POSTPONED]
        ).count()
        active_now = ORSchedule.objects.filter(
            operating_room=room, date=today,
            status__in=[ORSchedule.Status.IN_OR, ORSchedule.Status.SURGERY_STARTED],
        ).first()
        room_data.append({
            'room': room,
            'today_count': today_count,
            'active_now': active_now,
        })

    return render(request, 'or_schedule/or_room_list.html', {
        'room_data': room_data,
    })


# ── 11a. OR Room Create ───────────────────────────────────────────────────────

@hms_permission_required('core.manage_or_schedule')
def or_room_create(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Room name is required.')
        elif OperatingRoom.objects.filter(name=name).exists():
            messages.error(request, 'A room with this name already exists.')
        else:
            room = OperatingRoom.objects.create(
                name=name,
                room_number=request.POST.get('room_number', '').strip(),
                room_type=request.POST.get('room_type', OperatingRoom.RoomType.GENERAL),
                floor=request.POST.get('floor', '').strip(),
                location_detail=request.POST.get('location_detail', '').strip(),
                capacity_hours=int(request.POST.get('capacity_hours', 8) or 8),
                equipment_notes=request.POST.get('equipment_notes', '').strip(),
                is_active='is_active' in request.POST,
            )
            _log(request.user, AuditLog.Action.CREATE, AuditLog.Module.SURGERY,
                 f'Operating Room "{room.name}" created')
            messages.success(request, f'Operating Room "{room.name}" created.')
            return redirect('or_room_list')

    return render(request, 'or_schedule/or_room_form.html', {
        'room': None,
        'room_type_choices': OperatingRoom.RoomType.choices,
        'post': request.POST if request.method == 'POST' else {},
    })


# ── 11b. OR Room Edit ─────────────────────────────────────────────────────────

@hms_permission_required('core.manage_or_schedule')
def or_room_edit(request, pk):
    room = get_object_or_404(OperatingRoom, pk=pk)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Room name is required.')
        elif OperatingRoom.objects.filter(name=name).exclude(pk=pk).exists():
            messages.error(request, 'Another room with this name already exists.')
        else:
            room.name = name
            room.room_number = request.POST.get('room_number', '').strip()
            room.room_type = request.POST.get('room_type', OperatingRoom.RoomType.GENERAL)
            room.floor = request.POST.get('floor', '').strip()
            room.location_detail = request.POST.get('location_detail', '').strip()
            room.capacity_hours = int(request.POST.get('capacity_hours', 8) or 8)
            room.equipment_notes = request.POST.get('equipment_notes', '').strip()
            room.is_active = 'is_active' in request.POST
            room.save()
            _log(request.user, AuditLog.Action.UPDATE, AuditLog.Module.SURGERY,
                 f'Operating Room "{room.name}" updated')
            messages.success(request, f'Operating Room "{room.name}" updated.')
            return redirect('or_room_list')

    return render(request, 'or_schedule/or_room_form.html', {
        'room': room,
        'room_type_choices': OperatingRoom.RoomType.choices,
        'post': {},
    })


# ── 12. Check OR Conflicts (AJAX) ─────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def patient_search_ajax(request):
    """JSON patient search for OR forms."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'patients': []})

    patients = Patient.objects.filter(
        Q(first_name__icontains=q)
        | Q(last_name__icontains=q)
        | Q(middle_name__icontains=q)
        | Q(card_number__icontains=q)
        | Q(mobile__icontains=q)
    ).filter(is_active=True)[:20]

    return JsonResponse({
        'patients': [
            {
                'id': p.pk,
                'full_name': p.full_name,
                'card_number': p.card_number,
                'sex': p.sex,
                'age': p.age_display or '',
            }
            for p in patients
        ]
    })


@hms_permission_required('core.read_surgery')
def check_or_conflicts(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'GET required'}, status=405)

    or_room_id = request.GET.get('or_room_id')
    date_str = request.GET.get('date')
    start_time_str = request.GET.get('start_time')
    duration_str = request.GET.get('duration_minutes')
    exclude_id = request.GET.get('exclude_schedule_id') or None

    if not all([or_room_id, date_str, start_time_str, duration_str]):
        return JsonResponse({'has_conflict': False, 'conflicts': []})

    try:
        or_room = OperatingRoom.objects.get(pk=or_room_id)
        parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        parsed_start = datetime.strptime(start_time_str, '%H:%M').time()
        duration = int(duration_str)
        parsed_end = _compute_end_time(parsed_start, duration)
    except (OperatingRoom.DoesNotExist, ValueError):
        return JsonResponse({'has_conflict': False, 'conflicts': [], 'error': 'Invalid parameters'})

    conflicts = _check_or_conflicts(or_room, parsed_date, parsed_start, parsed_end, exclude_pk=exclude_id)

    return JsonResponse({
        'has_conflict': bool(conflicts),
        'conflicts': [
            {
                'schedule_number': c.schedule_number,
                'procedure': c.surgery_request.procedure_name,
                'start': c.start_time.strftime('%H:%M'),
                'end': c.end_time.strftime('%H:%M'),
            }
            for c in conflicts
        ],
    })


# ── Reports ───────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_surgery')
def report_or_daily(request):
    date_str = request.GET.get('date', '')
    export = request.GET.get('export', '')

    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()

    schedules = ORSchedule.objects.filter(
        date=selected_date,
    ).select_related(
        'surgery_request__patient', 'surgery_request__requested_by',
        'operating_room', 'primary_surgeon', 'assistant_surgeon',
        'anesthetist',
    ).order_by('operating_room__name', 'start_time')

    if export == 'csv':
        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="or_daily_{selected_date}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Schedule #', 'Patient', 'Procedure', 'OR Room', 'Date',
            'Start Time', 'End Time', 'Primary Surgeon', 'Status', 'Priority',
        ])
        for s in schedules:
            writer.writerow([
                s.schedule_number,
                s.surgery_request.patient.full_name,
                s.surgery_request.procedure_name,
                s.operating_room.name,
                s.date,
                s.start_time.strftime('%H:%M'),
                s.end_time.strftime('%H:%M'),
                f'Dr. {s.primary_surgeon.full_name}',
                s.status,
                s.surgery_request.priority,
            ])
        return response

    or_rooms = OperatingRoom.objects.filter(is_active=True)
    # Group schedules by OR
    by_room = {r.pk: [] for r in or_rooms}
    for s in schedules:
        if s.operating_room_id in by_room:
            by_room[s.operating_room_id].append(s)

    return render(request, 'or_schedule/reports/daily.html', {
        'selected_date': selected_date,
        'schedules': schedules,
        'or_rooms': or_rooms,
        'by_room': by_room,
    })


@hms_permission_required('core.read_surgery')
def report_or_utilization(request):
    date_str_from = request.GET.get('date_from', '')
    date_str_to = request.GET.get('date_to', '')

    today = timezone.localdate()
    if date_str_from:
        try:
            date_from = datetime.strptime(date_str_from, '%Y-%m-%d').date()
        except ValueError:
            date_from = today - timedelta(days=30)
    else:
        date_from = today - timedelta(days=30)

    if date_str_to:
        try:
            date_to = datetime.strptime(date_str_to, '%Y-%m-%d').date()
        except ValueError:
            date_to = today
    else:
        date_to = today

    total_days = max(1, (date_to - date_from).days + 1)
    or_rooms = OperatingRoom.objects.filter(is_active=True)

    utilization_data = []
    for room in or_rooms:
        room_schedules = ORSchedule.objects.filter(
            operating_room=room,
            date__gte=date_from,
            date__lte=date_to,
        )
        completed = room_schedules.filter(status=ORSchedule.Status.SURGERY_COMPLETED)
        cancelled = room_schedules.filter(status=ORSchedule.Status.CANCELLED).count()

        total_minutes = 0
        for s in completed:
            total_minutes += s.duration_minutes

        capacity_minutes = room.capacity_hours * 60 * total_days
        used_hours = round(total_minutes / 60, 1)
        total_hours = round(capacity_minutes / 60, 1)
        util_pct = round((total_minutes / capacity_minutes * 100) if capacity_minutes > 0 else 0, 1)

        utilization_data.append({
            'room': room,
            'used_hours': used_hours,
            'total_hours': total_hours,
            'util_pct': util_pct,
            'cancelled': cancelled,
            'completed_count': completed.count(),
        })

    # Totals
    total_used = sum(d['used_hours'] for d in utilization_data)
    total_cap = sum(d['total_hours'] for d in utilization_data)
    avg_util = round((total_used / total_cap * 100) if total_cap > 0 else 0, 1)

    return render(request, 'or_schedule/reports/utilization.html', {
        'date_from': date_from,
        'date_to': date_to,
        'utilization_data': utilization_data,
        'total_used': total_used,
        'total_cap': total_cap,
        'avg_util': avg_util,
    })


@hms_permission_required('core.read_surgery')
def report_surgeon_schedule(request):
    surgeon_id = request.GET.get('surgeon_id', '')
    date_str_from = request.GET.get('date_from', '')
    date_str_to = request.GET.get('date_to', '')

    today = timezone.localdate()
    date_from = today
    date_to = today + timedelta(days=30)

    if date_str_from:
        try:
            date_from = datetime.strptime(date_str_from, '%Y-%m-%d').date()
        except ValueError:
            pass
    if date_str_to:
        try:
            date_to = datetime.strptime(date_str_to, '%Y-%m-%d').date()
        except ValueError:
            pass

    surgeons = Doctor.objects.filter(active=True)
    selected_surgeon = None
    schedules = []

    if surgeon_id:
        try:
            selected_surgeon = Doctor.objects.get(pk=surgeon_id)
            schedules = ORSchedule.objects.filter(
                date__gte=date_from,
                date__lte=date_to,
            ).filter(
                Q(primary_surgeon=selected_surgeon) | Q(assistant_surgeon=selected_surgeon)
            ).select_related(
                'surgery_request__patient', 'operating_room',
            ).order_by('date', 'start_time')
        except Doctor.DoesNotExist:
            pass

    return render(request, 'or_schedule/reports/surgeon_schedule.html', {
        'surgeons': surgeons,
        'selected_surgeon': selected_surgeon,
        'schedules': schedules,
        'date_from': date_from,
        'date_to': date_to,
        'surgeon_id': surgeon_id,
    })
