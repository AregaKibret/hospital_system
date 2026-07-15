import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .card_utils import perform_checkin_billing
from .decorators import hms_permission_required
from .forms import PatientForm
from .models import AuditLog, Appointment, Department, Doctor, DoctorSchedule, Invoice, Patient, Visit, Queue


# ---------------------------------------------------------------------------
# 1. Receptionist Dashboard
# ---------------------------------------------------------------------------

@login_required
def receptionist_dashboard(request):
    """Main dashboard for the receptionist / front desk module."""
    today = timezone.localdate()

    today_appointments = (
        Appointment.objects
        .filter(appointment_date=today)
        .select_related('patient', 'doctor', 'department')
        .order_by('appointment_time')
    )

    today_count = today_appointments.count()
    waiting_count = today_appointments.filter(status=Appointment.Status.WAITING).count()
    scheduled_count = today_appointments.filter(
        status__in=[Appointment.Status.SCHEDULED, Appointment.Status.CONFIRMED]
    ).count()
    completed_count = today_appointments.filter(status=Appointment.Status.COMPLETED).count()
    new_patients_today = Patient.objects.filter(created_at__date=today).count()

    upcoming_appointments = (
        Appointment.objects
        .filter(
            appointment_date__gt=today,
            status__in=[Appointment.Status.SCHEDULED, Appointment.Status.CONFIRMED],
        )
        .select_related('patient', 'doctor')
        .order_by('appointment_date', 'appointment_time')[:10]
    )

    recent_registrations = Patient.objects.order_by('-created_at')[:5]

    context = {
        'today': today,
        'today_appointments': today_appointments,
        'today_count': today_count,
        'waiting_count': waiting_count,
        'scheduled_count': scheduled_count,
        'completed_count': completed_count,
        'new_patients_today': new_patients_today,
        'upcoming_appointments': upcoming_appointments,
        'recent_registrations': recent_registrations,
    }
    return render(request, 'receptionist/dashboard.html', context)


# ---------------------------------------------------------------------------
# 2. Appointment List
# ---------------------------------------------------------------------------

@hms_permission_required('core.read_appointment')
def appointment_list(request):
    """Paginated, filterable list of appointments."""
    qs = (
        Appointment.objects
        .select_related('patient', 'doctor', 'department')
        .order_by('appointment_date', 'appointment_time')
    )

    # --- filters ---
    status_filter = request.GET.get('status', '')
    date_filter = request.GET.get('date', '')
    search = request.GET.get('q', '').strip()
    doctor_id = request.GET.get('doctor_id', '')

    if status_filter:
        qs = qs.filter(status=status_filter)

    if date_filter:
        try:
            from datetime import date as date_type
            parsed_date = date_type.fromisoformat(date_filter)
            qs = qs.filter(appointment_date=parsed_date)
        except ValueError:
            pass  # ignore malformed date strings

    if search:
        qs = qs.filter(
            Q(patient__first_name__icontains=search)
            | Q(patient__last_name__icontains=search)
            | Q(patient__middle_name__icontains=search)
            | Q(patient__card_number__icontains=search)
            | Q(patient__mobile__icontains=search)
            | Q(walkin_name__icontains=search)
            | Q(phone_number__icontains=search)
            | Q(appointment_number__icontains=search)
        )

    if doctor_id:
        qs = qs.filter(doctor_id=doctor_id)

    # --- status tab counts (before pagination, without status filter) ---
    base_qs = Appointment.objects.select_related('patient', 'doctor', 'department')
    if date_filter:
        try:
            from datetime import date as date_type
            parsed_date = date_type.fromisoformat(date_filter)
            base_qs = base_qs.filter(appointment_date=parsed_date)
        except ValueError:
            pass
    if search:
        base_qs = base_qs.filter(
            Q(patient__first_name__icontains=search)
            | Q(patient__last_name__icontains=search)
            | Q(patient__middle_name__icontains=search)
            | Q(patient__card_number__icontains=search)
            | Q(patient__mobile__icontains=search)
            | Q(walkin_name__icontains=search)
            | Q(phone_number__icontains=search)
        )
    if doctor_id:
        base_qs = base_qs.filter(doctor_id=doctor_id)

    status_counts = {
        row['status']: row['count']
        for row in base_qs.values('status').annotate(count=Count('id'))
    }
    all_count = sum(status_counts.values())

    status_tabs = [
        {'label': 'All', 'value': '', 'count': all_count},
        {'label': 'Scheduled', 'value': Appointment.Status.SCHEDULED,
         'count': status_counts.get(Appointment.Status.SCHEDULED, 0)},
        {'label': 'Confirmed', 'value': Appointment.Status.CONFIRMED,
         'count': status_counts.get(Appointment.Status.CONFIRMED, 0)},
        {'label': 'Waiting', 'value': Appointment.Status.WAITING,
         'count': status_counts.get(Appointment.Status.WAITING, 0)},
        {'label': 'In Progress', 'value': Appointment.Status.IN_PROGRESS,
         'count': status_counts.get(Appointment.Status.IN_PROGRESS, 0)},
        {'label': 'Completed', 'value': Appointment.Status.COMPLETED,
         'count': status_counts.get(Appointment.Status.COMPLETED, 0)},
        {'label': 'No Show', 'value': Appointment.Status.NO_SHOW,
         'count': status_counts.get(Appointment.Status.NO_SHOW, 0)},
        {'label': 'Cancelled', 'value': Appointment.Status.CANCELLED,
         'count': status_counts.get(Appointment.Status.CANCELLED, 0)},
    ]

    paginator = Paginator(qs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    doctors = Doctor.objects.filter(active=True).order_by('last_name', 'first_name')

    context = {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'date_filter': date_filter,
        'search': search,
        'doctor_id': doctor_id,
        'doctors': doctors,
        'status_tabs': status_tabs,
    }
    return render(request, 'receptionist/appointment_list.html', context)


# ---------------------------------------------------------------------------
# 3. Appointment Create
# ---------------------------------------------------------------------------

@hms_permission_required('core.manage_appointments')
def appointment_create(request):
    """Create a new appointment. Optionally pre-fills patient from ?patient=ID."""
    doctors = Doctor.objects.filter(active=True).select_related('department', 'specialization').order_by('last_name', 'first_name')
    departments = Department.objects.all()
    appointment_types = Appointment.AppointmentType.choices
    selected_patient = None

    # Pre-selected patient from query param
    pre_patient = None
    patient_param = request.GET.get('patient') or request.POST.get('patient')
    if patient_param:
        try:
            pre_patient = Patient.objects.get(pk=int(patient_param))
        except (Patient.DoesNotExist, ValueError, TypeError):
            pre_patient = None

    if request.method == 'POST':
        # The form's <select>/hidden inputs are named "patient"/"doctor"
        # (see receptionist/appointment_form.html) — not "patient_id"/"doctor_id".
        patient_id = request.POST.get('patient')
        doctor_id = request.POST.get('doctor')
        appointment_date = request.POST.get('appointment_date')
        appointment_time = request.POST.get('appointment_time')
        appointment_type = request.POST.get('appointment_type', Appointment.AppointmentType.NEW)
        chief_complaint = request.POST.get('chief_complaint', '').strip()
        notes = request.POST.get('notes', '').strip()

        errors = []
        if not patient_id:
            errors.append('Please select a patient.')
        if not doctor_id:
            errors.append('Please select a doctor.')
        if not appointment_date:
            errors.append('Appointment date is required.')
        if not appointment_time:
            errors.append('Appointment time is required.')

        if errors:
            for err in errors:
                messages.error(request, err)
            if patient_id:
                try:
                    selected_patient = Patient.objects.get(pk=patient_id)
                except (Patient.DoesNotExist, ValueError, TypeError):
                    pass
        else:
            try:
                patient = Patient.objects.get(pk=patient_id)
                doctor = Doctor.objects.get(pk=doctor_id)

                appointment = Appointment(
                    patient=patient,
                    doctor=doctor,
                    department=doctor.department,
                    appointment_date=appointment_date,
                    appointment_time=appointment_time,
                    appointment_type=appointment_type,
                    chief_complaint=chief_complaint,
                    notes=notes,
                    status=Appointment.Status.SCHEDULED,
                    created_by=request.user,
                )
                appointment.save()
                log_action(
                    request.user, AuditLog.Action.CREATE, AuditLog.Module.APPOINTMENT,
                    object_type='Appointment', object_id=appointment.pk,
                    object_repr=appointment.appointment_number,
                    description=f'Appointment {appointment.appointment_number} created for {patient.full_name}',
                    request=request,
                )
                messages.success(
                    request,
                    f'Appointment {appointment.appointment_number} created successfully for {patient.full_name}.',
                )
                return redirect('appointment_detail', appt_id=appointment.pk)
            except Patient.DoesNotExist:
                messages.error(request, 'Selected patient not found.')
            except Doctor.DoesNotExist:
                messages.error(request, 'Selected doctor not found.')
            except Exception as exc:
                messages.error(request, f'Error creating appointment: {exc}')

    context = {
        'doctors': doctors,
        'departments': departments,
        'appointment_types': appointment_types,
        'pre_patient': pre_patient,
        'selected_patient': selected_patient,
    }
    return render(request, 'receptionist/appointment_form.html', context)


# ---------------------------------------------------------------------------
# 3b. Walk-In Appointment (no patient registration required yet)
# ---------------------------------------------------------------------------

@hms_permission_required('core.manage_appointments')
def appointment_create_walkin(request):
    """Book an appointment for a caller/walk-in who has no MRN yet.

    Only name + phone are required — patient/doctor/department stay null
    until the receptionist registers the patient when they arrive.
    """
    doctors = Doctor.objects.filter(active=True).select_related('department', 'specialization').order_by('last_name', 'first_name')
    departments = Department.objects.all()
    appointment_types = Appointment.AppointmentType.choices

    if request.method == 'POST':
        walkin_name = request.POST.get('walkin_name', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()
        doctor_id = request.POST.get('doctor_id', '').strip()
        department_id = request.POST.get('department_id', '').strip()
        appointment_date = request.POST.get('appointment_date', '').strip()
        appointment_time = request.POST.get('appointment_time', '').strip()
        appointment_type = request.POST.get('appointment_type', Appointment.AppointmentType.NEW)
        reason_for_visit = request.POST.get('reason_for_visit', '').strip()
        notes = request.POST.get('notes', '').strip()

        errors = []
        if not walkin_name:
            errors.append('Patient full name is required.')
        if not phone_number:
            errors.append('Mobile phone number is required.')

        doctor = None
        if doctor_id:
            try:
                doctor = Doctor.objects.select_related('department').get(pk=doctor_id)
            except Doctor.DoesNotExist:
                errors.append('Selected doctor not found.')

        department = None
        if department_id:
            try:
                department = Department.objects.get(pk=department_id)
            except Department.DoesNotExist:
                errors.append('Selected department not found.')
        elif doctor:
            department = doctor.department

        if not errors:
            # Date/time are optional per spec — default to "today, 6pm" (same
            # convention used by the New Appointment time field) rather than
            # leaving the DB columns null, since nothing downstream (ordering,
            # calendar, reports) expects a dateless appointment.
            appointment = Appointment.objects.create(
                patient=None,
                walkin_name=walkin_name,
                phone_number=phone_number,
                doctor=doctor,
                department=department,
                appointment_date=appointment_date or timezone.localdate(),
                appointment_time=appointment_time or '18:00',
                appointment_type=appointment_type,
                reason_for_visit=reason_for_visit,
                notes=notes,
                status=Appointment.Status.SCHEDULED,
                created_by=request.user,
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.APPOINTMENT,
                object_type='Appointment', object_id=appointment.pk,
                object_repr=appointment.appointment_number,
                description=f'Walk-in appointment {appointment.appointment_number} booked for '
                            f'"{walkin_name}" ({phone_number}) — no patient record yet',
                request=request,
            )
            messages.success(
                request,
                f'Walk-in appointment {appointment.appointment_number} booked for {walkin_name}. '
                f'No patient record was created — complete registration when they arrive.',
            )
            return redirect('appointment_detail', appt_id=appointment.pk)

        for err in errors:
            messages.error(request, err)

        return render(request, 'receptionist/appointment_walkin_form.html', {
            'doctors': doctors,
            'departments': departments,
            'appointment_types': appointment_types,
            'post': request.POST,
        })

    return render(request, 'receptionist/appointment_walkin_form.html', {
        'doctors': doctors,
        'departments': departments,
        'appointment_types': appointment_types,
    })


# ---------------------------------------------------------------------------
# 3c. Register Patient (completes registration for a walk-in appointment)
# ---------------------------------------------------------------------------

@hms_permission_required('core.add_patient')
def register_patient_for_appointment(request, appt_id):
    """Standard patient registration, pre-filled from & linked back to a
    walk-in appointment. Creates exactly one Patient row and links the
    existing Appointment to it — no new appointment is created."""
    appointment = get_object_or_404(Appointment, pk=appt_id)

    if not appointment.is_unregistered:
        messages.info(request, 'This appointment is already linked to a registered patient.')
        return redirect('appointment_detail', appt_id=appt_id)

    # Best-effort name split for prefill only — receptionist can correct it.
    name_parts = appointment.walkin_name.split()
    initial = {'mobile': appointment.phone_number}
    if name_parts:
        initial['first_name'] = name_parts[0]
        if len(name_parts) > 1:
            initial['last_name'] = name_parts[-1]
        if len(name_parts) > 2:
            initial['middle_name'] = ' '.join(name_parts[1:-1])

    if request.method == 'POST':
        form = PatientForm(request.POST)
        if form.is_valid():
            patient = form.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.PATIENT,
                object_type='Patient', object_id=patient.pk,
                object_repr=patient.full_name,
                description=f'New patient registered: {patient.full_name} (card {patient.card_number}) '
                            f'from walk-in appointment {appointment.appointment_number}',
                extra_data={'card_number': patient.card_number},
                request=request,
            )
            appointment.patient = patient
            if not appointment.phone_number:
                appointment.phone_number = patient.mobile
            appointment.save(update_fields=['patient', 'phone_number', 'updated_at'])
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.APPOINTMENT,
                object_type='Appointment', object_id=appointment.pk,
                object_repr=appointment.appointment_number,
                description=f'Walk-in appointment {appointment.appointment_number} linked to newly '
                            f'registered patient {patient.full_name} ({patient.card_number})',
                request=request,
            )
            messages.success(
                request,
                f'{patient.full_name} registered (card {patient.card_number}) and linked to '
                f'appointment {appointment.appointment_number}.',
            )
            next_url = reverse('patient_next_action', kwargs={'patient_id': patient.pk})
            return redirect(f'{next_url}?appointment_id={appointment.pk}')
    else:
        form = PatientForm(initial=initial)

    return render(request, 'receptionist/register_patient_for_appointment.html', {
        'form': form,
        'appointment': appointment,
    })


# ---------------------------------------------------------------------------
# 4. Appointment Detail
# ---------------------------------------------------------------------------

@hms_permission_required('core.read_appointment')
def appointment_detail(request, appt_id):
    """View appointment details; handle status update and cancellation via POST."""
    appointment = get_object_or_404(
        Appointment.objects.select_related('patient', 'doctor', 'department', 'visit'),
        pk=appt_id,
    )

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_status':
            if not request.user.has_perm('core.manage_appointments'):
                messages.error(request, 'You do not have permission to update appointment status.')
                return redirect('appointment_detail', appt_id=appt_id)
            new_status = request.POST.get('status')
            valid_statuses = [choice[0] for choice in Appointment.Status.choices]
            if new_status in valid_statuses:
                old_status = appointment.status
                appointment.status = new_status
                appointment.save(update_fields=['status', 'updated_at'])
                messages.success(
                    request,
                    f'Appointment status updated from {old_status} to {new_status}.',
                )
            else:
                messages.error(request, f'Invalid status: {new_status}')

        elif action == 'cancel':
            if not request.user.has_perm('core.manage_appointments'):
                messages.error(request, 'You do not have permission to cancel appointments.')
                return redirect('appointment_detail', appt_id=appt_id)
            if appointment.status not in (Appointment.Status.COMPLETED, Appointment.Status.CANCELLED):
                appointment.status = Appointment.Status.CANCELLED
                appointment.save(update_fields=['status', 'updated_at'])
                messages.success(request, 'Appointment has been cancelled.')
            else:
                messages.error(request, f'Cannot cancel an appointment with status "{appointment.status}".')

        return redirect('appointment_detail', appt_id=appt_id)

    context = {
        'appointment': appointment,
        'status_choices': Appointment.Status.choices,
    }
    return render(request, 'receptionist/appointment_detail.html', context)


# ---------------------------------------------------------------------------
# 5. Appointment Edit
# ---------------------------------------------------------------------------

@hms_permission_required('core.manage_appointments')
def appointment_edit(request, appt_id):
    """Edit an existing appointment."""
    appointment = get_object_or_404(Appointment, pk=appt_id)
    doctors = Doctor.objects.filter(active=True).select_related('department', 'specialization').order_by('last_name', 'first_name')
    departments = Department.objects.all()
    appointment_types = Appointment.AppointmentType.choices
    selected_patient = appointment.patient

    if request.method == 'POST':
        # The form's <select> inputs are named "patient"/"doctor"
        # (see receptionist/appointment_form.html) — not "patient_id"/"doctor_id".
        patient_id = request.POST.get('patient')
        doctor_id = request.POST.get('doctor')
        appointment_date = request.POST.get('appointment_date')
        appointment_time = request.POST.get('appointment_time')
        appointment_type = request.POST.get('appointment_type', appointment.appointment_type)
        chief_complaint = request.POST.get('chief_complaint', '').strip()
        notes = request.POST.get('notes', '').strip()
        status = request.POST.get('status', appointment.status)

        errors = []
        if not patient_id:
            errors.append('Please select a patient.')
        if not doctor_id:
            errors.append('Please select a doctor.')
        if not appointment_date:
            errors.append('Appointment date is required.')
        if not appointment_time:
            errors.append('Appointment time is required.')

        if errors:
            for err in errors:
                messages.error(request, err)
            if patient_id:
                try:
                    selected_patient = Patient.objects.get(pk=patient_id)
                except (Patient.DoesNotExist, ValueError, TypeError):
                    pass
        else:
            try:
                patient = Patient.objects.get(pk=patient_id)
                doctor = Doctor.objects.get(pk=doctor_id)
                valid_statuses = [choice[0] for choice in Appointment.Status.choices]
                if status not in valid_statuses:
                    status = appointment.status

                appointment.patient = patient
                appointment.doctor = doctor
                appointment.department = doctor.department
                appointment.appointment_date = appointment_date
                appointment.appointment_time = appointment_time
                appointment.appointment_type = appointment_type
                appointment.chief_complaint = chief_complaint
                appointment.notes = notes
                appointment.status = status
                appointment.save()
                log_action(
                    request.user, AuditLog.Action.UPDATE, AuditLog.Module.APPOINTMENT,
                    object_type='Appointment', object_id=appointment.pk,
                    object_repr=appointment.appointment_number,
                    description=f'Appointment {appointment.appointment_number} updated',
                    request=request,
                )
                messages.success(
                    request,
                    f'Appointment {appointment.appointment_number} updated successfully.',
                )
                return redirect('appointment_detail', appt_id=appointment.pk)
            except Patient.DoesNotExist:
                messages.error(request, 'Selected patient not found.')
            except Doctor.DoesNotExist:
                messages.error(request, 'Selected doctor not found.')
            except Exception as exc:
                messages.error(request, f'Error updating appointment: {exc}')

    context = {
        'appointment': appointment,
        'doctors': doctors,
        'departments': departments,
        'appointment_types': appointment_types,
        'status_choices': Appointment.Status.choices,
        'is_edit': True,
        'selected_patient': selected_patient,
    }
    return render(request, 'receptionist/appointment_form.html', context)


# ---------------------------------------------------------------------------
# 6. Appointment Start Visit (mark arrived → redirect to create_visit)
# ---------------------------------------------------------------------------

@require_POST
@hms_permission_required('core.manage_appointments')
def appointment_start_visit(request, appt_id):
    """Mark appointment as Waiting (patient arrived) and redirect to create visit."""
    appointment = get_object_or_404(Appointment, pk=appt_id)

    if appointment.status in (Appointment.Status.CANCELLED, Appointment.Status.COMPLETED):
        messages.error(
            request,
            f'Cannot start a visit for an appointment with status "{appointment.status}".',
        )
        return redirect('appointment_detail', appt_id=appt_id)

    if appointment.is_unregistered:
        messages.info(
            request,
            f'{appointment.display_patient_name} has not been registered yet. '
            f'Please complete registration first.',
        )
        return redirect('register_patient_for_appointment', appt_id=appt_id)

    appointment.status = Appointment.Status.WAITING
    appointment.save(update_fields=['status', 'updated_at'])
    log_action(
        request.user, AuditLog.Action.UPDATE, AuditLog.Module.APPOINTMENT,
        object_type='Appointment', object_id=appointment.pk,
        object_repr=appointment.appointment_number,
        description=f'Patient {appointment.patient.full_name} marked as arrived for appointment '
                    f'{appointment.appointment_number}',
        request=request,
    )
    messages.success(
        request,
        f'Patient {appointment.patient.full_name} marked as arrived. Please create a visit.',
    )
    # Redirect to the create-visit URL with the patient's ID
    return redirect('create_visit', patient_id=appointment.patient.pk)


# ---------------------------------------------------------------------------
# 7. Appointment Confirm (POST only)
# ---------------------------------------------------------------------------

@require_POST
@hms_permission_required('core.manage_appointments')
def appointment_confirm(request, appt_id):
    """Confirm a scheduled appointment."""
    appointment = get_object_or_404(Appointment, pk=appt_id)

    if appointment.status == Appointment.Status.SCHEDULED:
        appointment.status = Appointment.Status.CONFIRMED
        appointment.save(update_fields=['status', 'updated_at'])
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.APPOINTMENT,
            object_type='Appointment', object_id=appointment.pk,
            object_repr=appointment.appointment_number,
            description=f'Appointment {appointment.appointment_number} confirmed',
            request=request,
        )
        messages.success(
            request,
            f'Appointment {appointment.appointment_number} confirmed successfully.',
        )
    else:
        messages.error(
            request,
            f'Appointment cannot be confirmed from status "{appointment.status}".',
        )

    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER', '')
    return redirect(next_url) if next_url else redirect('appointment_detail', appt_id=appt_id)


# ---------------------------------------------------------------------------
# 8. Appointment Cancel (POST only)
# ---------------------------------------------------------------------------

@require_POST
@hms_permission_required('core.manage_appointments')
def appointment_cancel(request, appt_id):
    """Cancel an appointment."""
    appointment = get_object_or_404(Appointment, pk=appt_id)

    if appointment.status in (Appointment.Status.COMPLETED, Appointment.Status.CANCELLED):
        messages.error(
            request,
            f'Appointment cannot be cancelled from status "{appointment.status}".',
        )
    else:
        appointment.status = Appointment.Status.CANCELLED
        appointment.save(update_fields=['status', 'updated_at'])
        log_action(
            request.user, AuditLog.Action.CANCEL, AuditLog.Module.APPOINTMENT,
            object_type='Appointment', object_id=appointment.pk,
            object_repr=appointment.appointment_number,
            description=f'Appointment {appointment.appointment_number} cancelled',
            request=request,
        )
        messages.success(
            request,
            f'Appointment {appointment.appointment_number} has been cancelled.',
        )

    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER', '')
    return redirect(next_url) if next_url else redirect('appointment_list')


# ---------------------------------------------------------------------------
# 9. Appointment Mark Arrived (POST only)
# ---------------------------------------------------------------------------

@require_POST
@hms_permission_required('core.manage_appointments')
def appointment_mark_arrived(request, appt_id):
    """Check the patient in: verify status, auto-determine card/consultation
    charges, send them to Billing, and land on a summary screen — see
    card_utils.perform_checkin_billing for the full decision pipeline."""
    appointment = get_object_or_404(Appointment, pk=appt_id)

    if appointment.status in (Appointment.Status.CANCELLED, Appointment.Status.COMPLETED,
                               Appointment.Status.NO_SHOW):
        messages.error(
            request,
            f'Cannot check in a patient with appointment status "{appointment.status}".',
        )
        return redirect('appointment_detail', appt_id=appt_id)

    if appointment.is_unregistered:
        messages.info(
            request,
            f'{appointment.display_patient_name} has not been registered yet. '
            f'Please complete registration first.',
        )
        return redirect('register_patient_for_appointment', appt_id=appt_id)

    waive_card_fee = (
        bool(request.POST.get('waive_card_fee'))
        and request.user.has_perm('core.override_card_expiry')
    )

    try:
        result = perform_checkin_billing(appointment, request.user, waive_card_fee=waive_card_fee, request=request)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('appointment_detail', appt_id=appt_id)

    if appointment.status != Appointment.Status.WAITING:
        appointment.status = Appointment.Status.WAITING
        appointment.save(update_fields=['status', 'updated_at'])
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.APPOINTMENT,
            object_type='Appointment', object_id=appointment.pk,
            object_repr=appointment.appointment_number,
            description=f'Patient {appointment.patient.full_name} checked in for appointment '
                        f'{appointment.appointment_number}',
            request=request,
        )

    if not result.already_checked_in:
        messages.success(
            request,
            f'Patient {appointment.patient.full_name} checked in at '
            f'{timezone.localtime().strftime("%H:%M")}.',
        )

    return redirect('appointment_checkin_summary', appt_id=appt_id)


# ---------------------------------------------------------------------------
# 9b. Appointment Check-In Summary (billing breakdown)
# ---------------------------------------------------------------------------

@hms_permission_required('core.manage_appointments')
def appointment_checkin_summary(request, appt_id):
    from .models import InvoiceItem

    appointment = get_object_or_404(
        Appointment.objects.select_related('patient', 'doctor', 'department', 'visit', 'visit__patient_card', 'visit__consultation_type'),
        pk=appt_id,
    )
    visit = appointment.visit
    invoice = Invoice.objects.filter(visit=visit).first() if visit else None
    card_fee_item = invoice.items.filter(service_type=InvoiceItem.ServiceType.CARD_FEE).first() if invoice else None
    consultation_fee_item = invoice.items.filter(service_type=InvoiceItem.ServiceType.CONSULTATION).first() if invoice else None

    card_status_reason = None
    if visit and visit.patient_card:
        card = visit.patient_card
        if card_fee_item:
            card_status_reason = (
                f'No valid card was on file (or it had expired), so the {card.card_type.name} '
                f'fee was generated automatically.'
            )
        else:
            card_status_reason = (
                f'The patient already had a valid {card.card_type.name}, so no registration/renewal fee was charged.'
            )

    return render(request, 'receptionist/appointment_checkin_summary.html', {
        'appointment': appointment,
        'visit': visit,
        'invoice': invoice,
        'card_fee_item': card_fee_item,
        'consultation_fee_item': consultation_fee_item,
        'card_status_reason': card_status_reason,
    })


# ---------------------------------------------------------------------------
# 10. Doctor Schedule View
# ---------------------------------------------------------------------------

@hms_permission_required('core.manage_appointments')
def doctor_schedule_view(request):
    """Display all doctors with their weekly schedules."""
    doctors = (
        Doctor.objects
        .filter(active=True)
        .prefetch_related('schedules')
        .order_by('last_name', 'first_name')
    )
    all_schedules = (
        DoctorSchedule.objects
        .select_related('doctor')
        .order_by('doctor__last_name', 'day_of_week')
    )

    context = {
        'doctors': doctors,
        'all_schedules': all_schedules,
        'day_choices': DoctorSchedule.DAY_CHOICES,
    }
    return render(request, 'receptionist/doctor_schedule.html', context)


# ---------------------------------------------------------------------------
# 11. Doctor Schedule Edit
# ---------------------------------------------------------------------------

@hms_permission_required('core.manage_appointments')
def doctor_schedule_edit(request, doctor_id):
    """View and edit all 7-day schedules for a specific doctor."""
    doctor = get_object_or_404(Doctor, pk=doctor_id, active=True)

    # Build a dict of existing schedules keyed by day_of_week for easy lookup
    existing_schedules = {
        s.day_of_week: s
        for s in DoctorSchedule.objects.filter(doctor=doctor)
    }

    if request.method == 'POST':
        saved_count = 0
        errors = []

        for day_num, day_name in DoctorSchedule.DAY_CHOICES:
            is_active = request.POST.get(f'day_{day_num}_active') == 'on'
            start_time = request.POST.get(f'day_{day_num}_start', '').strip()
            end_time = request.POST.get(f'day_{day_num}_end', '').strip()
            slot_duration = request.POST.get(f'day_{day_num}_slot', '20').strip()
            max_appointments = request.POST.get(f'day_{day_num}_max', '20').strip()

            schedule = existing_schedules.get(day_num)

            if not is_active:
                # If unchecked and a schedule exists, mark it inactive
                if schedule:
                    schedule.is_active = False
                    schedule.save(update_fields=['is_active'])
                continue

            # Validate required fields for active days
            if not start_time or not end_time:
                errors.append(f'{day_name}: Start and end times are required for active days.')
                continue

            try:
                slot_duration_int = int(slot_duration) if slot_duration else 20
                max_appointments_int = int(max_appointments) if max_appointments else 20
            except ValueError:
                errors.append(f'{day_name}: Slot duration and max appointments must be numbers.')
                continue

            if schedule:
                schedule.start_time = start_time
                schedule.end_time = end_time
                schedule.slot_duration_minutes = slot_duration_int
                schedule.max_appointments = max_appointments_int
                schedule.is_active = True
                schedule.save()
            else:
                DoctorSchedule.objects.create(
                    doctor=doctor,
                    day_of_week=day_num,
                    start_time=start_time,
                    end_time=end_time,
                    slot_duration_minutes=slot_duration_int,
                    max_appointments=max_appointments_int,
                    is_active=True,
                )
            saved_count += 1

        for err in errors:
            messages.error(request, err)

        if not errors:
            messages.success(
                request,
                f'Schedule for Dr. {doctor.full_name} updated successfully ({saved_count} active day(s)).',
            )
            return redirect('doctor_schedule_view')

    # Prepare schedule data for each day of the week
    schedule_rows = []
    for day_num, day_name in DoctorSchedule.DAY_CHOICES:
        sched = existing_schedules.get(day_num)
        schedule_rows.append({
            'day_num': day_num,
            'day_name': day_name,
            'schedule': sched,
            'is_active': sched.is_active if sched else False,
            'start_time': sched.start_time.strftime('%H:%M') if sched and sched.start_time else '',
            'end_time': sched.end_time.strftime('%H:%M') if sched and sched.end_time else '',
            'slot_duration': sched.slot_duration_minutes if sched else 20,
            'max_appointments': sched.max_appointments if sched else 20,
        })

    context = {
        'doctor': doctor,
        'schedule_rows': schedule_rows,
    }
    return render(request, 'receptionist/doctor_schedule_edit.html', context)


# ---------------------------------------------------------------------------
# 12. Doctor Availability API (AJAX)
# ---------------------------------------------------------------------------

@login_required
def doctor_availability_api(request):
    """
    AJAX endpoint returning taken time slots for a doctor on a given date.

    GET params:
        doctor_id  — required
        date       — required, YYYY-MM-DD

    Returns JSON:
        {
            "taken_slots": ["09:00", "09:20", ...],
            "schedule": {
                "start": "08:00",
                "end": "17:00",
                "slot_duration": 20,
                "max_appointments": 20
            }
        }
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    doctor_id = request.GET.get('doctor_id')
    date_str = request.GET.get('date')

    if not doctor_id or not date_str:
        return JsonResponse({'error': 'doctor_id and date are required.'}, status=400)

    try:
        doctor = Doctor.objects.get(pk=int(doctor_id), active=True)
    except (Doctor.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'error': 'Doctor not found.'}, status=404)

    try:
        from datetime import date as date_type
        appt_date = date_type.fromisoformat(date_str)
    except ValueError:
        return JsonResponse({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

    # Day of week: Monday=0 ... Sunday=6 (matches DoctorSchedule.DAY_CHOICES)
    day_of_week = appt_date.weekday()

    schedule_data = {'start': None, 'end': None, 'slot_duration': 20, 'max_appointments': 20}
    try:
        sched = DoctorSchedule.objects.get(doctor=doctor, day_of_week=day_of_week, is_active=True)
        schedule_data = {
            'start': sched.start_time.strftime('%H:%M'),
            'end': sched.end_time.strftime('%H:%M'),
            'slot_duration': sched.slot_duration_minutes,
            'max_appointments': sched.max_appointments,
        }
    except DoctorSchedule.DoesNotExist:
        pass  # Doctor has no schedule for this day; return empty

    # Get taken appointment times for this doctor + date (excluding Cancelled / No Show)
    taken_appointments = Appointment.objects.filter(
        doctor=doctor,
        appointment_date=appt_date,
    ).exclude(
        status__in=[Appointment.Status.CANCELLED, Appointment.Status.NO_SHOW]
    ).values_list('appointment_time', flat=True)

    taken_slots = [t.strftime('%H:%M') for t in taken_appointments]

    return JsonResponse({
        'taken_slots': taken_slots,
        'schedule': schedule_data,
    })


# ---------------------------------------------------------------------------
# 13. Patient Flow
# ---------------------------------------------------------------------------

@hms_permission_required('core.read_appointment')
def patient_flow(request):
    """
    Today's patient flow — appointments and visits grouped by status.
    Provides a real-time view of patients currently in the facility.
    """
    today = timezone.localdate()

    # Today's appointments grouped by status
    today_appointments = (
        Appointment.objects
        .filter(appointment_date=today)
        .select_related('patient', 'doctor', 'department')
        .order_by('appointment_time')
    )

    # Group appointments by status for display
    appointments_by_status = {}
    for status_value, status_label in Appointment.Status.choices:
        appointments_by_status[status_value] = {
            'label': status_label,
            'appointments': [],
        }
    for appt in today_appointments:
        if appt.status in appointments_by_status:
            appointments_by_status[appt.status]['appointments'].append(appt)

    # Today's visits with queue information
    today_visits = (
        Visit.objects
        .filter(created_at__date=today)
        .select_related('patient', 'doctor', 'department', 'queue')
        .order_by('-created_at')
    )

    # Queue entries for today
    today_queue = (
        Queue.objects
        .filter(visit__created_at__date=today)
        .select_related('visit__patient', 'visit__doctor', 'visit__department')
        .order_by('queue_number')
    )

    # Summary counts
    waiting_count = today_appointments.filter(status=Appointment.Status.WAITING).count()
    in_progress_count = today_appointments.filter(status=Appointment.Status.IN_PROGRESS).count()
    completed_count = today_appointments.filter(status=Appointment.Status.COMPLETED).count()
    scheduled_count = today_appointments.filter(
        status__in=[Appointment.Status.SCHEDULED, Appointment.Status.CONFIRMED]
    ).count()
    no_show_count = today_appointments.filter(status=Appointment.Status.NO_SHOW).count()
    cancelled_count = today_appointments.filter(status=Appointment.Status.CANCELLED).count()

    context = {
        'today': today,
        'today_appointments': today_appointments,
        'appointments_by_status': appointments_by_status,
        'today_visits': today_visits,
        'today_queue': today_queue,
        'waiting_count': waiting_count,
        'in_progress_count': in_progress_count,
        'completed_count': completed_count,
        'scheduled_count': scheduled_count,
        'no_show_count': no_show_count,
        'cancelled_count': cancelled_count,
        'total_today': today_appointments.count(),
    }
    return render(request, 'receptionist/patient_flow.html', context)
