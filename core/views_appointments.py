"""
Consultation Appointment module — views_appointments.py
Covers:
  - Appointment CRUD (create, list, detail, edit, cancel, reschedule, no-show)
  - Consultation dashboard with today's KPIs
  - Doctor availability (weekly schedules + exceptions)
  - AJAX slot generator
  - 6 report views (daily schedule, dept schedule, full list, cancelled, no-show, workload)
"""
import csv
import datetime

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog,
    Appointment,
    AppointmentStatusLog,
    Department,
    Doctor,
    DoctorSchedule,
    DoctorScheduleException,
    Patient,
    Visit,
)


# ── Audit helper ──────────────────────────────────────────────────────────────

def _log(user, action, desc):
    try:
        log_action(
            user, action, AuditLog.Module.APPOINTMENT,
            description=desc,
        )
    except Exception:
        pass


# ── Status transition helper ───────────────────────────────────────────────────

def _transition(appt, to_status, user, notes=''):
    from_status = appt.status
    appt.status = to_status
    appt.save(update_fields=['status', 'updated_at'])
    AppointmentStatusLog.objects.create(
        appointment=appt,
        from_status=from_status,
        to_status=to_status,
        changed_by=user,
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. CONSULTATION DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_appointment')
def consultation_dashboard(request):
    today = timezone.localdate()

    today_qs = Appointment.objects.filter(appointment_date=today)

    status_counts = {
        s: today_qs.filter(status=s).count()
        for s in [
            Appointment.Status.SCHEDULED,
            Appointment.Status.CONFIRMED,
            Appointment.Status.CHECKED_IN,
            Appointment.Status.WAITING,
            Appointment.Status.IN_CONSULTATION,
            Appointment.Status.COMPLETED,
            Appointment.Status.NO_SHOW,
            Appointment.Status.CANCELLED,
        ]
    }

    total_today = today_qs.count()

    # Doctors with schedule today (day_of_week)
    today_dow = today.weekday()  # 0=Mon … 6=Sun
    doctor_schedules = (
        DoctorSchedule.objects
        .filter(day_of_week=today_dow, is_active=True)
        .select_related('doctor', 'doctor__department')
    )

    # Build list of doctor + appt count for today
    doctor_availability = []
    for sched in doctor_schedules:
        # Check for exception on today's date
        exception = DoctorScheduleException.objects.filter(
            doctor=sched.doctor, date=today
        ).first()
        if exception and not exception.is_available:
            available = False
            reason = exception.get_reason_display()
        else:
            available = True
            reason = ''
        count = today_qs.filter(doctor=sched.doctor).count()
        doctor_availability.append({
            'doctor': sched.doctor,
            'schedule': sched,
            'available': available,
            'unavail_reason': reason,
            'appt_count': count,
        })

    upcoming = (
        Appointment.objects
        .filter(
            appointment_date__gte=today,
            status__in=[Appointment.Status.SCHEDULED, Appointment.Status.CONFIRMED],
        )
        .select_related('patient', 'doctor')
        .order_by('appointment_date', 'appointment_time')[:5]
    )

    recent_activity = (
        AppointmentStatusLog.objects
        .select_related('appointment', 'appointment__patient', 'changed_by')
        .order_by('-changed_at')[:5]
    )

    return render(request, 'appointments/dashboard.html', {
        'today': today,
        'total_today': total_today,
        'status_counts': status_counts,
        'doctor_availability': doctor_availability,
        'upcoming': upcoming,
        'recent_activity': recent_activity,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 2. APPOINTMENT LIST
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_appointment')
def appt_list(request):
    today = timezone.localdate()

    qs = Appointment.objects.select_related('patient', 'doctor', 'department').order_by(
        'appointment_date', 'appointment_time'
    )

    # Filters
    status_filter = request.GET.get('status', '')
    date_str = request.GET.get('date', str(today))
    doctor_id = request.GET.get('doctor_id', '')
    visit_type = request.GET.get('visit_type', '')
    priority = request.GET.get('priority', '')
    q = request.GET.get('q', '').strip()

    # Date filter (default today)
    try:
        filter_date = datetime.date.fromisoformat(date_str)
    except (ValueError, TypeError):
        filter_date = today
        date_str = str(today)

    qs = qs.filter(appointment_date=filter_date)

    if status_filter:
        qs = qs.filter(status=status_filter)
    if doctor_id:
        qs = qs.filter(doctor_id=doctor_id)
    if visit_type:
        qs = qs.filter(visit_type=visit_type)
    if priority:
        qs = qs.filter(priority=priority)
    if q:
        qs = qs.filter(
            Q(patient__first_name__icontains=q)
            | Q(patient__last_name__icontains=q)
            | Q(patient__card_number__icontains=q)
            | Q(appointment_number__icontains=q)
        )

    # Tab counts for filter_date
    base_qs = Appointment.objects.filter(appointment_date=filter_date)
    tab_counts = {s: base_qs.filter(status=s).count() for s in Appointment.Status.values}
    tab_counts['all'] = base_qs.count()

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get('page', 1))

    doctors = Doctor.objects.filter(active=True).select_related('department')

    return render(request, 'appointments/appointment_list.html', {
        'page': page,
        'filter_date': filter_date,
        'date_str': date_str,
        'status_filter': status_filter,
        'doctor_id': doctor_id,
        'visit_type': visit_type,
        'priority': priority,
        'q': q,
        'tab_counts': tab_counts,
        'doctors': doctors,
        'statuses': Appointment.Status.choices,
        'visit_types': Appointment.VisitType.choices,
        'priorities': Appointment.Priority.choices,
        'today': today,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 3. CREATE APPOINTMENT
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_appointments')
def appt_create(request):
    today = timezone.localdate()

    if request.method == 'POST':
        patient_id = request.POST.get('patient_id')
        doctor_id = request.POST.get('doctor_id')
        appt_date_str = request.POST.get('appointment_date')
        appt_time_str = request.POST.get('appointment_time')
        visit_type = request.POST.get('visit_type', Appointment.VisitType.NEW_VISIT)
        priority = request.POST.get('priority', Appointment.Priority.NORMAL)
        referral_source = request.POST.get('referral_source', Appointment.ReferralSource.RECEPTION)
        chief_complaint = request.POST.get('chief_complaint', '')
        reason_for_visit = request.POST.get('reason_for_visit', '')
        notes = request.POST.get('notes', '')
        phone_number = request.POST.get('phone_number', '')
        appointment_type = request.POST.get('appointment_type', Appointment.AppointmentType.NEW)

        errors = []
        patient = None
        doctor = None

        if not patient_id:
            errors.append('Patient is required.')
        else:
            try:
                patient = Patient.objects.get(pk=patient_id)
            except Patient.DoesNotExist:
                errors.append('Selected patient not found.')

        if not doctor_id:
            errors.append('Doctor is required.')
        else:
            try:
                doctor = Doctor.objects.select_related('department').get(pk=doctor_id)
            except Doctor.DoesNotExist:
                errors.append('Selected doctor not found.')

        if not appt_date_str:
            errors.append('Appointment date is required.')
        if not appt_time_str:
            errors.append('Appointment time is required.')

        if not errors:
            try:
                appt_date = datetime.date.fromisoformat(appt_date_str)
                appt_time = datetime.time.fromisoformat(appt_time_str)
            except ValueError:
                errors.append('Invalid date or time format.')

        if not errors:
            appt = Appointment.objects.create(
                patient=patient,
                doctor=doctor,
                department=doctor.department,
                appointment_date=appt_date,
                appointment_time=appt_time,
                appointment_type=appointment_type,
                visit_type=visit_type,
                priority=priority,
                referral_source=referral_source,
                chief_complaint=chief_complaint,
                reason_for_visit=reason_for_visit,
                notes=notes,
                phone_number=phone_number,
                status=Appointment.Status.SCHEDULED,
                created_by=request.user,
            )
            AppointmentStatusLog.objects.create(
                appointment=appt,
                from_status='',
                to_status=Appointment.Status.SCHEDULED,
                changed_by=request.user,
                notes='Appointment created',
            )
            _log(request.user, AuditLog.Action.CREATE,
                 f'Appointment {appt.appointment_number} created for {patient.full_name}')
            messages.success(request, f'Appointment {appt.appointment_number} created successfully.')
            return redirect('appt_detail', pk=appt.pk)

        # Re-render form with errors
        patients = Patient.objects.filter(is_active=True).order_by('first_name')
        doctors = Doctor.objects.filter(active=True).select_related('department')
        return render(request, 'appointments/appointment_form.html', {
            'errors': errors,
            'post': request.POST,
            'patients': patients,
            'doctors': doctors,
            'visit_types': Appointment.VisitType.choices,
            'priorities': Appointment.Priority.choices,
            'referral_sources': Appointment.ReferralSource.choices,
            'appointment_types': Appointment.AppointmentType.choices,
            'today': today,
        })

    # GET
    patients = Patient.objects.filter(is_active=True).order_by('first_name')
    doctors = Doctor.objects.filter(active=True).select_related('department')
    # Pre-select patient from query param if provided
    pre_patient_id = request.GET.get('patient_id')
    pre_patient = None
    if pre_patient_id:
        try:
            pre_patient = Patient.objects.get(pk=pre_patient_id)
        except Patient.DoesNotExist:
            pass

    return render(request, 'appointments/appointment_form.html', {
        'patients': patients,
        'doctors': doctors,
        'visit_types': Appointment.VisitType.choices,
        'priorities': Appointment.Priority.choices,
        'referral_sources': Appointment.ReferralSource.choices,
        'appointment_types': Appointment.AppointmentType.choices,
        'pre_patient': pre_patient,
        'today': today,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 4. APPOINTMENT DETAIL + STATUS ACTIONS
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_appointment')
def appt_detail(request, pk):
    appt = get_object_or_404(
        Appointment.objects.select_related('patient', 'doctor', 'department', 'created_by'),
        pk=pk,
    )
    status_logs = appt.status_logs.select_related('changed_by').order_by('changed_at')

    if request.method == 'POST':
        if not request.user.has_perm('core.manage_appointments'):
            messages.error(request, 'You do not have permission to update appointment status.')
            return redirect('appt_detail', pk=pk)

        action = request.POST.get('action')
        now = timezone.now()

        if action == 'confirm' and appt.status == Appointment.Status.SCHEDULED:
            _transition(appt, Appointment.Status.CONFIRMED, request.user)
            messages.success(request, 'Appointment confirmed.')

        elif action == 'checkin' and appt.status in [
            Appointment.Status.SCHEDULED, Appointment.Status.CONFIRMED
        ]:
            appt.checked_in_at = now
            appt.status = Appointment.Status.CHECKED_IN
            appt.save(update_fields=['status', 'checked_in_at', 'updated_at'])
            AppointmentStatusLog.objects.create(
                appointment=appt,
                from_status=Appointment.Status.CONFIRMED,
                to_status=Appointment.Status.CHECKED_IN,
                changed_by=request.user,
            )
            messages.success(request, 'Patient checked in.')

        elif action == 'waiting' and appt.status == Appointment.Status.CHECKED_IN:
            _transition(appt, Appointment.Status.WAITING, request.user)
            messages.success(request, 'Status set to Waiting.')

        elif action == 'start_consultation' and appt.status in [
            Appointment.Status.WAITING, Appointment.Status.CHECKED_IN
        ]:
            prev = appt.status
            appt.consultation_started_at = now
            appt.status = Appointment.Status.IN_CONSULTATION
            appt.save(update_fields=['status', 'consultation_started_at', 'updated_at'])
            AppointmentStatusLog.objects.create(
                appointment=appt,
                from_status=prev,
                to_status=Appointment.Status.IN_CONSULTATION,
                changed_by=request.user,
            )
            messages.success(request, 'Consultation started.')

        elif action == 'complete' and appt.status == Appointment.Status.IN_CONSULTATION:
            appt.consultation_ended_at = now
            appt.status = Appointment.Status.COMPLETED
            appt.save(update_fields=['status', 'consultation_ended_at', 'updated_at'])
            AppointmentStatusLog.objects.create(
                appointment=appt,
                from_status=Appointment.Status.IN_CONSULTATION,
                to_status=Appointment.Status.COMPLETED,
                changed_by=request.user,
            )
            _log(request.user, AuditLog.Action.UPDATE,
                 f'Appointment {appt.appointment_number} completed')
            messages.success(request, 'Appointment marked completed.')

        elif action == 'no_show':
            prev = appt.status
            appt.status = Appointment.Status.NO_SHOW
            appt.save(update_fields=['status', 'updated_at'])
            AppointmentStatusLog.objects.create(
                appointment=appt,
                from_status=prev,
                to_status=Appointment.Status.NO_SHOW,
                changed_by=request.user,
            )
            messages.success(request, 'Appointment marked as No Show.')

        else:
            messages.warning(request, 'Action not applicable for current status.')

        return redirect('appt_detail', pk=pk)

    return render(request, 'appointments/appointment_detail.html', {
        'appt': appt,
        'status_logs': status_logs,
        'Status': Appointment.Status,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 5. EDIT APPOINTMENT
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_appointments')
def appt_edit(request, pk):
    appt = get_object_or_404(Appointment, pk=pk)
    locked_statuses = [
        Appointment.Status.COMPLETED,
        Appointment.Status.CANCELLED,
        Appointment.Status.NO_SHOW,
    ]
    if appt.status in locked_statuses:
        messages.error(request, 'Cannot edit a completed, cancelled, or no-show appointment.')
        return redirect('appt_detail', pk=pk)

    doctors = Doctor.objects.filter(active=True).select_related('department')

    if request.method == 'POST':
        doctor_id = request.POST.get('doctor_id')
        appt_date_str = request.POST.get('appointment_date')
        appt_time_str = request.POST.get('appointment_time')
        visit_type = request.POST.get('visit_type', appt.visit_type)
        priority = request.POST.get('priority', appt.priority)
        referral_source = request.POST.get('referral_source', appt.referral_source)
        chief_complaint = request.POST.get('chief_complaint', appt.chief_complaint)
        reason_for_visit = request.POST.get('reason_for_visit', appt.reason_for_visit)
        notes = request.POST.get('notes', appt.notes)
        phone_number = request.POST.get('phone_number', appt.phone_number)
        appointment_type = request.POST.get('appointment_type', appt.appointment_type)

        errors = []
        try:
            doctor = Doctor.objects.get(pk=doctor_id)
        except Doctor.DoesNotExist:
            errors.append('Doctor not found.')
            doctor = None

        try:
            appt_date = datetime.date.fromisoformat(appt_date_str)
            appt_time = datetime.time.fromisoformat(appt_time_str)
        except (ValueError, TypeError):
            errors.append('Invalid date or time.')
            appt_date = appt_time = None

        if not errors:
            appt.doctor = doctor
            appt.department = doctor.department
            appt.appointment_date = appt_date
            appt.appointment_time = appt_time
            appt.appointment_type = appointment_type
            appt.visit_type = visit_type
            appt.priority = priority
            appt.referral_source = referral_source
            appt.chief_complaint = chief_complaint
            appt.reason_for_visit = reason_for_visit
            appt.notes = notes
            appt.phone_number = phone_number
            appt.save()
            _log(request.user, AuditLog.Action.UPDATE,
                 f'Appointment {appt.appointment_number} updated')
            messages.success(request, 'Appointment updated.')
            return redirect('appt_detail', pk=pk)

        return render(request, 'appointments/appointment_edit.html', {
            'appt': appt,
            'doctors': doctors,
            'errors': errors,
            'visit_types': Appointment.VisitType.choices,
            'priorities': Appointment.Priority.choices,
            'referral_sources': Appointment.ReferralSource.choices,
            'appointment_types': Appointment.AppointmentType.choices,
        })

    return render(request, 'appointments/appointment_edit.html', {
        'appt': appt,
        'doctors': doctors,
        'visit_types': Appointment.VisitType.choices,
        'priorities': Appointment.Priority.choices,
        'referral_sources': Appointment.ReferralSource.choices,
        'appointment_types': Appointment.AppointmentType.choices,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 6. CANCEL APPOINTMENT
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_appointments')
def appt_cancel(request, pk):
    appt = get_object_or_404(Appointment, pk=pk)

    if appt.status in [Appointment.Status.COMPLETED, Appointment.Status.CANCELLED]:
        messages.error(request, 'Appointment is already completed or cancelled.')
        return redirect('appt_detail', pk=pk)

    if request.method == 'POST':
        reason = request.POST.get('cancellation_reason', '').strip()
        if not reason:
            messages.error(request, 'Please provide a cancellation reason.')
            return render(request, 'appointments/appointment_cancel.html', {'appt': appt})

        prev_status = appt.status
        appt.status = Appointment.Status.CANCELLED
        appt.cancellation_reason = reason
        appt.cancelled_by = request.user
        appt.cancelled_at = timezone.now()
        appt.save(update_fields=[
            'status', 'cancellation_reason', 'cancelled_by', 'cancelled_at', 'updated_at'
        ])
        AppointmentStatusLog.objects.create(
            appointment=appt,
            from_status=prev_status,
            to_status=Appointment.Status.CANCELLED,
            changed_by=request.user,
            notes=reason,
        )
        _log(request.user, AuditLog.Action.CANCEL,
             f'Appointment {appt.appointment_number} cancelled: {reason}')
        messages.success(request, 'Appointment cancelled.')
        return redirect('appt_detail', pk=pk)

    return render(request, 'appointments/appointment_cancel.html', {'appt': appt})


# ─────────────────────────────────────────────────────────────────────────────
# 7. RESCHEDULE APPOINTMENT
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_appointments')
def appt_reschedule(request, pk):
    original = get_object_or_404(Appointment, pk=pk)

    if original.status in [Appointment.Status.COMPLETED, Appointment.Status.CANCELLED]:
        messages.error(request, 'Cannot reschedule a completed or cancelled appointment.')
        return redirect('appt_detail', pk=pk)

    doctors = Doctor.objects.filter(active=True).select_related('department')

    if request.method == 'POST':
        new_date_str = request.POST.get('appointment_date')
        new_time_str = request.POST.get('appointment_time')
        doctor_id = request.POST.get('doctor_id', original.doctor_id)
        notes = request.POST.get('notes', '')

        errors = []
        try:
            new_date = datetime.date.fromisoformat(new_date_str)
            new_time = datetime.time.fromisoformat(new_time_str)
        except (ValueError, TypeError):
            errors.append('Invalid date or time.')
            new_date = new_time = None

        try:
            doctor = Doctor.objects.select_related('department').get(pk=doctor_id)
        except Doctor.DoesNotExist:
            errors.append('Doctor not found.')
            doctor = None

        if not errors:
            # Mark original as Rescheduled
            prev_status = original.status
            original.status = Appointment.Status.RESCHEDULED
            original.save(update_fields=['status', 'updated_at'])
            AppointmentStatusLog.objects.create(
                appointment=original,
                from_status=prev_status,
                to_status=Appointment.Status.RESCHEDULED,
                changed_by=request.user,
                notes=f'Rescheduled to {new_date} {new_time}',
            )

            # Create new appointment
            new_appt = Appointment.objects.create(
                patient=original.patient,
                doctor=doctor,
                department=doctor.department,
                appointment_date=new_date,
                appointment_time=new_time,
                appointment_type=original.appointment_type,
                visit_type=original.visit_type,
                priority=original.priority,
                referral_source=original.referral_source,
                chief_complaint=original.chief_complaint,
                reason_for_visit=original.reason_for_visit,
                notes=notes or original.notes,
                phone_number=original.phone_number,
                status=Appointment.Status.SCHEDULED,
                rescheduled_from=original,
                created_by=request.user,
            )
            AppointmentStatusLog.objects.create(
                appointment=new_appt,
                from_status='',
                to_status=Appointment.Status.SCHEDULED,
                changed_by=request.user,
                notes=f'Rescheduled from {original.appointment_number}',
            )
            _log(request.user, AuditLog.Action.UPDATE,
                 f'Appointment {original.appointment_number} rescheduled to {new_appt.appointment_number}')
            messages.success(request, f'Appointment rescheduled. New appointment: {new_appt.appointment_number}')
            return redirect('appt_detail', pk=new_appt.pk)

        return render(request, 'appointments/appointment_reschedule.html', {
            'original': original,
            'doctors': doctors,
            'errors': errors,
        })

    return render(request, 'appointments/appointment_reschedule.html', {
        'original': original,
        'doctors': doctors,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 8. DOCTOR AVAILABILITY LIST
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_appointment')
def doctor_availability_list(request):
    doctors = Doctor.objects.filter(active=True).select_related('department').prefetch_related('schedules')
    days = dict(DoctorSchedule.DAY_CHOICES)

    doctor_data = []
    for doc in doctors:
        schedules_by_day = {s.day_of_week: s for s in doc.schedules.all()}
        weekly = [schedules_by_day.get(d) for d in range(7)]
        doctor_data.append({'doctor': doc, 'weekly': weekly})

    return render(request, 'appointments/doctor_availability.html', {
        'doctor_data': doctor_data,
        'days': [days[i] for i in range(7)],
    })


# ─────────────────────────────────────────────────────────────────────────────
# 9. DOCTOR AVAILABILITY EDIT (weekly schedule)
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_doctor_availability')
def doctor_availability_edit(request, doctor_id):
    doctor = get_object_or_404(Doctor, pk=doctor_id)
    days = DoctorSchedule.DAY_CHOICES  # [(0,'Monday'), ...]

    if request.method == 'POST':
        for day_num, day_name in days:
            is_active = request.POST.get(f'is_active_{day_num}') == 'on'
            start_time_str = request.POST.get(f'start_time_{day_num}', '').strip()
            end_time_str = request.POST.get(f'end_time_{day_num}', '').strip()
            break_start_str = request.POST.get(f'break_start_{day_num}', '').strip()
            break_end_str = request.POST.get(f'break_end_{day_num}', '').strip()
            slot_duration = request.POST.get(f'slot_duration_{day_num}', '20').strip()
            max_appointments = request.POST.get(f'max_appointments_{day_num}', '20').strip()
            specialty = request.POST.get(f'specialty_{day_num}', '').strip()

            if not is_active:
                # Delete if exists
                DoctorSchedule.objects.filter(doctor=doctor, day_of_week=day_num).delete()
                continue

            if not start_time_str or not end_time_str:
                continue  # Skip if times not provided

            try:
                start_time = datetime.time.fromisoformat(start_time_str)
                end_time = datetime.time.fromisoformat(end_time_str)
                break_start = datetime.time.fromisoformat(break_start_str) if break_start_str else None
                break_end = datetime.time.fromisoformat(break_end_str) if break_end_str else None
                slot_dur = int(slot_duration) if slot_duration.isdigit() else 20
                max_appt = int(max_appointments) if max_appointments.isdigit() else 20
            except ValueError:
                continue

            DoctorSchedule.objects.update_or_create(
                doctor=doctor,
                day_of_week=day_num,
                defaults={
                    'start_time': start_time,
                    'end_time': end_time,
                    'break_start': break_start,
                    'break_end': break_end,
                    'slot_duration_minutes': slot_dur,
                    'max_appointments': max_appt,
                    'specialty': specialty,
                    'is_active': True,
                },
            )

        _log(request.user, AuditLog.Action.UPDATE,
             f'Weekly schedule updated for Dr. {doctor.full_name}')
        messages.success(request, 'Schedule saved successfully.')
        return redirect('doctor_availability_list')

    # Build existing schedule map
    existing = {s.day_of_week: s for s in DoctorSchedule.objects.filter(doctor=doctor)}

    return render(request, 'appointments/doctor_availability_edit.html', {
        'doctor': doctor,
        'days': days,
        'existing': existing,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 10. DOCTOR SCHEDULE EXCEPTIONS
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_doctor_availability')
def doctor_availability_exception(request, doctor_id):
    doctor = get_object_or_404(Doctor, pk=doctor_id)
    exceptions = DoctorScheduleException.objects.filter(doctor=doctor).order_by('-date')
    reasons = DoctorScheduleException.Reason.choices

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'delete':
            exc_id = request.POST.get('exc_id')
            DoctorScheduleException.objects.filter(pk=exc_id, doctor=doctor).delete()
            messages.success(request, 'Exception removed.')
            return redirect('doctor_availability_exception', doctor_id=doctor_id)

        date_str = request.POST.get('date', '').strip()
        is_available = request.POST.get('is_available') == 'on'
        reason = request.POST.get('reason', DoctorScheduleException.Reason.LEAVE)
        notes = request.POST.get('notes', '').strip()
        start_time_str = request.POST.get('start_time', '').strip()
        end_time_str = request.POST.get('end_time', '').strip()

        errors = []
        if not date_str:
            errors.append('Date is required.')

        if not errors:
            try:
                exc_date = datetime.date.fromisoformat(date_str)
                start_time = datetime.time.fromisoformat(start_time_str) if start_time_str else None
                end_time = datetime.time.fromisoformat(end_time_str) if end_time_str else None
            except ValueError:
                errors.append('Invalid date or time format.')

        if not errors:
            DoctorScheduleException.objects.update_or_create(
                doctor=doctor,
                date=exc_date,
                defaults={
                    'is_available': is_available,
                    'start_time': start_time,
                    'end_time': end_time,
                    'reason': reason,
                    'notes': notes,
                    'created_by': request.user,
                },
            )
            _log(request.user, AuditLog.Action.CREATE,
                 f'Schedule exception added for Dr. {doctor.full_name} on {exc_date}')
            messages.success(request, 'Exception saved.')
            return redirect('doctor_availability_exception', doctor_id=doctor_id)

        return render(request, 'appointments/doctor_availability_exception.html', {
            'doctor': doctor,
            'exceptions': exceptions,
            'reasons': reasons,
            'errors': errors,
            'post': request.POST,
        })

    return render(request, 'appointments/doctor_availability_exception.html', {
        'doctor': doctor,
        'exceptions': exceptions,
        'reasons': reasons,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 11. AJAX: GET DOCTOR SLOTS
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_appointment')
def get_doctor_slots(request):
    doctor_id = request.GET.get('doctor_id')
    date_str = request.GET.get('date')

    if not doctor_id or not date_str:
        return JsonResponse({'slots': [], 'error': 'Missing parameters'}, status=400)

    try:
        date = datetime.date.fromisoformat(date_str)
        doctor = Doctor.objects.get(pk=doctor_id)
    except (ValueError, Doctor.DoesNotExist):
        return JsonResponse({'slots': [], 'error': 'Invalid parameters'}, status=400)

    dow = date.weekday()  # 0=Mon

    # Check schedule exception first
    exception = DoctorScheduleException.objects.filter(doctor=doctor, date=date).first()
    if exception and not exception.is_available:
        return JsonResponse({
            'slots': [],
            'unavailable': True,
            'reason': exception.get_reason_display(),
        })

    # Get weekly schedule
    try:
        schedule = DoctorSchedule.objects.get(doctor=doctor, day_of_week=dow, is_active=True)
    except DoctorSchedule.DoesNotExist:
        return JsonResponse({'slots': [], 'no_schedule': True})

    # Use exception times if modified
    if exception and exception.is_available and exception.start_time:
        start = exception.start_time
        end = exception.end_time or schedule.end_time
    else:
        start = schedule.start_time
        end = schedule.end_time

    slot_mins = schedule.slot_duration_minutes or 20

    # Generate all potential slots
    slots = []
    current = datetime.datetime.combine(date, start)
    end_dt = datetime.datetime.combine(date, end)

    while current + datetime.timedelta(minutes=slot_mins) <= end_dt:
        t = current.time()
        # Skip break time
        if schedule.break_start and schedule.break_end:
            if schedule.break_start <= t < schedule.break_end:
                current += datetime.timedelta(minutes=slot_mins)
                continue
        slots.append(t.strftime('%H:%M'))
        current += datetime.timedelta(minutes=slot_mins)

    # Remove already booked slots
    booked = set(
        Appointment.objects.filter(
            doctor=doctor,
            appointment_date=date,
            status__in=[
                Appointment.Status.SCHEDULED,
                Appointment.Status.CONFIRMED,
                Appointment.Status.CHECKED_IN,
                Appointment.Status.WAITING,
                Appointment.Status.IN_CONSULTATION,
            ],
        ).values_list('appointment_time', flat=True)
    )
    booked_strs = {t.strftime('%H:%M') for t in booked}
    available_slots = [s for s in slots if s not in booked_strs]

    return JsonResponse({
        'slots': available_slots,
        'schedule': {
            'start': start.strftime('%H:%M'),
            'end': end.strftime('%H:%M'),
            'slot_duration': slot_mins,
            'max_appointments': schedule.max_appointments,
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# 12. MARK NO-SHOW (POST only)
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_appointments')
@require_POST
def appt_no_show(request, pk):
    appt = get_object_or_404(Appointment, pk=pk)
    if appt.status not in [
        Appointment.Status.SCHEDULED, Appointment.Status.CONFIRMED,
        Appointment.Status.CHECKED_IN, Appointment.Status.WAITING,
    ]:
        messages.error(request, 'Cannot mark this appointment as No Show.')
        return redirect('appt_detail', pk=pk)

    prev = appt.status
    appt.status = Appointment.Status.NO_SHOW
    appt.save(update_fields=['status', 'updated_at'])
    AppointmentStatusLog.objects.create(
        appointment=appt,
        from_status=prev,
        to_status=Appointment.Status.NO_SHOW,
        changed_by=request.user,
    )
    _log(request.user, AuditLog.Action.UPDATE,
         f'Appointment {appt.appointment_number} marked No Show')
    messages.success(request, 'Appointment marked as No Show.')
    return redirect('appt_detail', pk=pk)


# ─────────────────────────────────────────────────────────────────────────────
# REPORTS
# ─────────────────────────────────────────────────────────────────────────────

def _csv_response(filename):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ── Report 1: Daily Schedule ──────────────────────────────────────────────────

@hms_permission_required('core.read_appointment')
def report_appt_daily_schedule(request):
    today = timezone.localdate()
    date_str = request.GET.get('date', str(today))
    doctor_id = request.GET.get('doctor_id', '')

    try:
        report_date = datetime.date.fromisoformat(date_str)
    except (ValueError, TypeError):
        report_date = today

    qs = Appointment.objects.filter(appointment_date=report_date).select_related(
        'patient', 'doctor', 'department'
    ).order_by('appointment_time')

    if doctor_id:
        qs = qs.filter(doctor_id=doctor_id)

    doctors = Doctor.objects.filter(active=True)

    if request.GET.get('export') == 'csv':
        response = _csv_response(f'daily_schedule_{report_date}.csv')
        writer = csv.writer(response)
        writer.writerow([
            'Appt #', 'Time', 'Patient', 'Card #', 'Doctor',
            'Department', 'Visit Type', 'Priority', 'Status', 'Chief Complaint',
        ])
        for a in qs:
            writer.writerow([
                a.appointment_number, a.appointment_time.strftime('%H:%M'),
                a.patient.full_name, a.patient.card_number,
                f'Dr. {a.doctor.full_name}', a.department.name,
                a.visit_type, a.priority, a.status, a.chief_complaint,
            ])
        return response

    return render(request, 'appointments/reports/daily_schedule.html', {
        'appointments': qs,
        'report_date': report_date,
        'doctor_id': doctor_id,
        'doctors': doctors,
        'date_str': date_str,
    })


# ── Report 2: Department Schedule ─────────────────────────────────────────────

@hms_permission_required('core.read_appointment')
def report_appt_dept_schedule(request):
    today = timezone.localdate()
    date_str = request.GET.get('date', str(today))
    dept_id = request.GET.get('dept_id', '')

    try:
        report_date = datetime.date.fromisoformat(date_str)
    except (ValueError, TypeError):
        report_date = today

    qs = Appointment.objects.filter(appointment_date=report_date).select_related(
        'patient', 'doctor', 'department'
    ).order_by('department__name', 'appointment_time')

    if dept_id:
        qs = qs.filter(department_id=dept_id)

    departments = Department.objects.filter(is_active=True)

    if request.GET.get('export') == 'csv':
        response = _csv_response(f'dept_schedule_{report_date}.csv')
        writer = csv.writer(response)
        writer.writerow([
            'Department', 'Appt #', 'Time', 'Patient', 'Card #',
            'Doctor', 'Visit Type', 'Priority', 'Status',
        ])
        for a in qs:
            writer.writerow([
                a.department.name, a.appointment_number,
                a.appointment_time.strftime('%H:%M'),
                a.patient.full_name, a.patient.card_number,
                f'Dr. {a.doctor.full_name}', a.visit_type, a.priority, a.status,
            ])
        return response

    return render(request, 'appointments/reports/dept_schedule.html', {
        'appointments': qs,
        'report_date': report_date,
        'dept_id': dept_id,
        'departments': departments,
        'date_str': date_str,
    })


# ── Report 3: Appointment List (date range) ────────────────────────────────────

@hms_permission_required('core.read_appointment')
def report_appt_list(request):
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today))
    date_to_str = request.GET.get('date_to', str(today))
    status_filter = request.GET.get('status', '')
    doctor_id = request.GET.get('doctor_id', '')

    try:
        date_from = datetime.date.fromisoformat(date_from_str)
    except (ValueError, TypeError):
        date_from = today
    try:
        date_to = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_to = today

    qs = Appointment.objects.filter(
        appointment_date__gte=date_from,
        appointment_date__lte=date_to,
    ).select_related('patient', 'doctor', 'department').order_by('appointment_date', 'appointment_time')

    if status_filter:
        qs = qs.filter(status=status_filter)
    if doctor_id:
        qs = qs.filter(doctor_id=doctor_id)

    doctors = Doctor.objects.filter(active=True)

    if request.GET.get('export') == 'csv':
        response = _csv_response(f'appointments_{date_from}_to_{date_to}.csv')
        writer = csv.writer(response)
        writer.writerow([
            'Appt #', 'Date', 'Time', 'Patient', 'Card #',
            'Doctor', 'Department', 'Visit Type', 'Priority',
            'Referral Source', 'Status',
        ])
        for a in qs:
            writer.writerow([
                a.appointment_number,
                a.appointment_date.strftime('%Y-%m-%d'),
                a.appointment_time.strftime('%H:%M'),
                a.patient.full_name, a.patient.card_number,
                f'Dr. {a.doctor.full_name}', a.department.name,
                a.visit_type, a.priority, a.referral_source, a.status,
            ])
        return response

    return render(request, 'appointments/reports/appointment_list.html', {
        'appointments': qs,
        'date_from': date_from,
        'date_to': date_to,
        'date_from_str': date_from_str,
        'date_to_str': date_to_str,
        'status_filter': status_filter,
        'doctor_id': doctor_id,
        'doctors': doctors,
        'statuses': Appointment.Status.choices,
        'total': qs.count(),
    })


# ── Report 4: Cancelled Appointments ─────────────────────────────────────────

@hms_permission_required('core.read_appointment')
def report_appt_cancelled(request):
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today))
    date_to_str = request.GET.get('date_to', str(today))

    try:
        date_from = datetime.date.fromisoformat(date_from_str)
    except (ValueError, TypeError):
        date_from = today
    try:
        date_to = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_to = today

    qs = Appointment.objects.filter(
        status=Appointment.Status.CANCELLED,
        appointment_date__gte=date_from,
        appointment_date__lte=date_to,
    ).select_related('patient', 'doctor', 'department', 'cancelled_by').order_by('appointment_date')

    if request.GET.get('export') == 'csv':
        response = _csv_response(f'cancelled_{date_from}_to_{date_to}.csv')
        writer = csv.writer(response)
        writer.writerow([
            'Appt #', 'Date', 'Patient', 'Card #', 'Doctor',
            'Cancelled By', 'Cancelled At', 'Reason',
        ])
        for a in qs:
            writer.writerow([
                a.appointment_number,
                a.appointment_date.strftime('%Y-%m-%d'),
                a.patient.full_name, a.patient.card_number,
                f'Dr. {a.doctor.full_name}',
                a.cancelled_by.get_full_name() if a.cancelled_by else '',
                a.cancelled_at.strftime('%Y-%m-%d %H:%M') if a.cancelled_at else '',
                a.cancellation_reason,
            ])
        return response

    return render(request, 'appointments/reports/cancelled.html', {
        'appointments': qs,
        'date_from': date_from,
        'date_to': date_to,
        'date_from_str': date_from_str,
        'date_to_str': date_to_str,
        'total': qs.count(),
    })


# ── Report 5: No-Show Appointments ────────────────────────────────────────────

@hms_permission_required('core.read_appointment')
def report_appt_no_show(request):
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today))
    date_to_str = request.GET.get('date_to', str(today))
    doctor_id = request.GET.get('doctor_id', '')

    try:
        date_from = datetime.date.fromisoformat(date_from_str)
    except (ValueError, TypeError):
        date_from = today
    try:
        date_to = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_to = today

    qs = Appointment.objects.filter(
        status=Appointment.Status.NO_SHOW,
        appointment_date__gte=date_from,
        appointment_date__lte=date_to,
    ).select_related('patient', 'doctor', 'department').order_by('appointment_date')

    if doctor_id:
        qs = qs.filter(doctor_id=doctor_id)

    doctors = Doctor.objects.filter(active=True)

    if request.GET.get('export') == 'csv':
        response = _csv_response(f'no_shows_{date_from}_to_{date_to}.csv')
        writer = csv.writer(response)
        writer.writerow(['Appt #', 'Date', 'Time', 'Patient', 'Card #', 'Doctor', 'Phone'])
        for a in qs:
            writer.writerow([
                a.appointment_number,
                a.appointment_date.strftime('%Y-%m-%d'),
                a.appointment_time.strftime('%H:%M'),
                a.patient.full_name, a.patient.card_number,
                f'Dr. {a.doctor.full_name}', a.phone_number,
            ])
        return response

    return render(request, 'appointments/reports/no_show.html', {
        'appointments': qs,
        'date_from': date_from,
        'date_to': date_to,
        'date_from_str': date_from_str,
        'date_to_str': date_to_str,
        'doctor_id': doctor_id,
        'doctors': doctors,
        'total': qs.count(),
    })


# ── Report 6: Doctor Workload ─────────────────────────────────────────────────

@hms_permission_required('core.read_appointment')
def report_appt_workload(request):
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today))
    date_to_str = request.GET.get('date_to', str(today))

    try:
        date_from = datetime.date.fromisoformat(date_from_str)
    except (ValueError, TypeError):
        date_from = today
    try:
        date_to = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_to = today

    base_qs = Appointment.objects.filter(
        appointment_date__gte=date_from,
        appointment_date__lte=date_to,
    )

    doctors = Doctor.objects.filter(active=True).select_related('department')
    workload = []
    for doc in doctors:
        doc_qs = base_qs.filter(doctor=doc)
        total = doc_qs.count()
        if total == 0:
            continue
        completed = doc_qs.filter(status=Appointment.Status.COMPLETED).count()
        cancelled = doc_qs.filter(status=Appointment.Status.CANCELLED).count()
        no_show = doc_qs.filter(status=Appointment.Status.NO_SHOW).count()
        scheduled = doc_qs.filter(status__in=[
            Appointment.Status.SCHEDULED, Appointment.Status.CONFIRMED
        ]).count()
        workload.append({
            'doctor': doc,
            'total': total,
            'completed': completed,
            'cancelled': cancelled,
            'no_show': no_show,
            'scheduled': scheduled,
            'completion_rate': round(completed / total * 100, 1) if total else 0,
        })

    workload.sort(key=lambda x: x['total'], reverse=True)

    if request.GET.get('export') == 'csv':
        response = _csv_response(f'workload_{date_from}_to_{date_to}.csv')
        writer = csv.writer(response)
        writer.writerow([
            'Doctor', 'Department', 'Total', 'Completed',
            'Cancelled', 'No Show', 'Scheduled', 'Completion %',
        ])
        for row in workload:
            writer.writerow([
                f'Dr. {row["doctor"].full_name}',
                row['doctor'].department.name,
                row['total'], row['completed'],
                row['cancelled'], row['no_show'], row['scheduled'],
                row['completion_rate'],
            ])
        return response

    return render(request, 'appointments/reports/workload.html', {
        'workload': workload,
        'date_from': date_from,
        'date_to': date_to,
        'date_from_str': date_from_str,
        'date_to_str': date_to_str,
        'grand_total': sum(r['total'] for r in workload),
    })
