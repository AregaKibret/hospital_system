import datetime
import json
from collections import OrderedDict

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .forms import (
    ClinicalNoteForm, DiagnosisForm,
    MedicationOrderForm, ProcedureOrderForm, VitalSignForm,
)

from .models import (
    Admission, Appointment, AppointmentStatusLog, AuditLog, ClinicalNote, Diagnosis,
    Doctor, ImagingOrder, ImagingService, Invoice, InvoiceItem, LabOrder, LabService,
    Medication, MedicationOrder, Patient, PatientAttachment, PhysicalExamination,
    ProcedureOrder, Queue, Visit, VitalSign,
)


def _get_visit(visit_id):
    return get_object_or_404(
        Visit.objects.select_related('patient', 'doctor', 'department'),
        pk=visit_id,
    )


# ── Dashboard ─────────────────────────────────────────────────────────────────

def _get_doctor_profile(user):
    try:
        return user.doctor_profile
    except Doctor.DoesNotExist:
        return None


def _build_queue_visits(doctor):
    """Return active visits for this doctor with queue/appointment data, ordered by queue number."""
    active_statuses = [
        Visit.Status.WAITING_DOCTOR,
        Visit.Status.CONSULTATION_STARTED,
        Visit.Status.INVESTIGATION_ORDERED,
        Visit.Status.INVESTIGATION_COMPLETED,
        Visit.Status.TREATMENT_STARTED,
        Visit.Status.PROCEDURE_SCHEDULED,
        Visit.Status.PAYMENT_COMPLETED,
        Visit.Status.WAITING_PAYMENT,
        Visit.Status.REGISTERED,
        Visit.Status.VISIT_CREATED,
    ]
    qs = (
        Visit.objects
        .filter(doctor=doctor, status__in=active_statuses)
        .select_related('patient', 'department')
        .prefetch_related('queue', 'appointment')
        .order_by('queue__queue_number', 'created_at')
    )
    return qs


@hms_permission_required('core.write_clinical_note')
def doctor_dashboard(request):
    today = timezone.localdate()
    doctor = _get_doctor_profile(request.user)

    if doctor:
        my_visits = _build_queue_visits(doctor)
        today_visits = my_visits.filter(created_at__date=today)
        queue_count = my_visits.count()
        waiting_count = my_visits.filter(status=Visit.Status.WAITING_DOCTOR).count()
        in_consult_count = my_visits.filter(status=Visit.Status.CONSULTATION_STARTED).count()
        pending_labs = LabOrder.objects.filter(visit__doctor=doctor, status='Pending').count()
        pending_imaging = ImagingOrder.objects.filter(visit__doctor=doctor, status='Pending').count()
    else:
        today_visits = Visit.objects.filter(created_at__date=today).select_related('patient', 'department').prefetch_related('queue')
        queue_count = 0
        waiting_count = 0
        in_consult_count = 0
        pending_labs = LabOrder.objects.filter(status='Pending').count()
        pending_imaging = ImagingOrder.objects.filter(status='Pending').count()

    recent_notes = (
        ClinicalNote.objects.filter(authored_by=request.user)
        .select_related('visit__patient')
        .order_by('-created_at')[:5]
    )
    context = {
        'today_visits': today_visits,
        'pending_labs': pending_labs,
        'pending_imaging': pending_imaging,
        'recent_notes': recent_notes,
        'today': today,
        'doctor': doctor,
        'queue_count': queue_count,
        'waiting_count': waiting_count,
        'in_consult_count': in_consult_count,
    }
    return render(request, 'doctor/dashboard.html', context)


@hms_permission_required('core.write_clinical_note')
def doctor_queue_api(request):
    """JSON endpoint: returns the logged-in doctor's active patient queue."""
    doctor = _get_doctor_profile(request.user)
    if not doctor:
        return JsonResponse({'visits': [], 'total': 0, 'next_visit_id': None})

    q_search = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    sort_by = request.GET.get('sort', 'queue').strip()  # queue | waiting | checkin | appointment

    qs = _build_queue_visits(doctor)

    if q_search:
        qs = qs.filter(
            Q(patient__first_name__icontains=q_search)
            | Q(patient__last_name__icontains=q_search)
            | Q(patient__card_number__icontains=q_search)
        )

    if status_filter:
        qs = qs.filter(status=status_filter)

    if sort_by == 'waiting':
        qs = qs.order_by('queue__created_at')
    elif sort_by == 'checkin':
        qs = qs.order_by('created_at')
    else:
        qs = qs.order_by('queue__queue_number', 'created_at')

    now = timezone.now()
    visits_data = []
    next_visit_id = None

    for v in qs:
        queue = getattr(v, 'queue', None)
        appt_qs = v.appointment.all()
        appt = appt_qs.first() if appt_qs.exists() else None

        # Age calculation
        age = None
        if v.patient.date_of_birth:
            dob = v.patient.date_of_birth
            age = (now.date() - dob).days // 365

        # Waiting time in minutes from queue creation
        waiting_mins = None
        if queue:
            waiting_mins = int((now - queue.created_at).total_seconds() // 60)

        # Priority from appointment if linked
        priority = appt.priority if appt else 'Normal'

        # Appointment time
        appt_time = None
        if appt and appt.appointment_time:
            appt_time = appt.appointment_time.strftime('%H:%M')

        queue_status = queue.status if queue else None

        item = {
            'id': v.id,
            'queue_number': queue.queue_number if queue else None,
            'queue_status': queue_status,
            'patient_name': v.patient.full_name,
            'mrn': v.patient.card_number,
            'age': age,
            'sex': v.patient.sex,
            'visit_type': v.visit_type,
            'visit_status': v.status,
            'visit_status_display': v.get_status_display(),
            'priority': priority,
            'appointment_time': appt_time,
            'checkin_time': v.created_at.strftime('%H:%M'),
            'waiting_minutes': waiting_mins,
            'department': v.department.name if v.department else '',
            'url': f'/doctor/visit/{v.id}/',
        }
        visits_data.append(item)

        # First waiting/active patient is "next"
        if next_visit_id is None and v.status in [Visit.Status.WAITING_DOCTOR, Visit.Status.PAYMENT_COMPLETED]:
            next_visit_id = v.id

    return JsonResponse({
        'visits': visits_data,
        'total': len(visits_data),
        'next_visit_id': next_visit_id,
    })


# ── Patient List ──────────────────────────────────────────────────────────────

@hms_permission_required('core.write_clinical_note')
def doctor_patient_list(request):
    query = request.GET.get('q', '').strip()
    date_filter = request.GET.get('date', '').strip()

    doctor = _get_doctor_profile(request.user)
    visits = (
        Visit.objects.select_related('patient', 'doctor', 'department')
        .prefetch_related('queue')
        .order_by('-created_at')
    )
    if doctor:
        visits = visits.filter(doctor=doctor)

    if query:
        visits = visits.filter(
            Q(patient__first_name__icontains=query)
            | Q(patient__last_name__icontains=query)
            | Q(patient__card_number__icontains=query)
        )
    if date_filter:
        visits = visits.filter(created_at__date=date_filter)

    paginator = Paginator(visits, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'doctor/patient_list.html', {
        'page_obj': page_obj,
        'query': query,
        'date_filter': date_filter,
    })


# ── Visit / Patient Chart ─────────────────────────────────────────────────────

@hms_permission_required('core.read_clinical_note')
def visit_detail(request, visit_id):
    visit = _get_visit(visit_id)
    active_tab = request.GET.get('tab', 'notes')

    clinical_notes = list(visit.clinical_notes.select_related('authored_by', 'physical_exam').order_by('-created_at'))
    diagnoses = list(visit.diagnoses.select_related('authored_by').all())
    lab_orders = list(visit.lab_orders.select_related('ordered_by').all())
    imaging_orders = list(visit.imaging_orders.select_related('ordered_by').all())
    medication_orders = list(visit.medication_orders.select_related('ordered_by').all())
    procedure_orders = list(visit.procedure_orders.select_related('ordered_by').all())
    surgery_orders = list(visit.surgery_orders.select_related('surgeon').all())

    from .models import Admission
    current_admission = visit.admissions.select_related('bed__room__ward').filter(
        status=Admission.Status.ADMITTED,
    ).first()
    vital_signs = list(visit.vital_signs.select_related('recorded_by').all())

    from .models import Prescription
    prescriptions = list(
        visit.prescriptions.select_related('prescribed_by')
        .prefetch_related('items')
        .order_by('-created_at')
    )

    nursing_assessments = list(visit.nursing_assessments.select_related('recorded_by').order_by('-recorded_at'))
    pain_assessments = list(visit.pain_assessments.select_related('recorded_by').order_by('-recorded_at'))
    fall_risk_assessments = list(visit.fall_risk_assessments.select_related('recorded_by').order_by('-recorded_at'))
    pressure_ulcer_assessments = list(visit.pressure_ulcer_assessments.select_related('recorded_by').order_by('-recorded_at'))
    nutritional_assessments = list(visit.nutritional_assessments.select_related('recorded_by').order_by('-recorded_at'))
    fluid_balance_records = list(visit.fluid_balance_records.select_related('recorded_by').order_by('-record_date', '-recorded_at'))
    glasgow_coma_scales = list(visit.glasgow_coma_scales.select_related('recorded_by').order_by('-recorded_at'))
    nursing_care_plans = list(visit.nursing_care_plans.select_related('created_by').order_by('-created_at'))
    nursing_tab_count = (
        len(nursing_assessments) + len(pain_assessments) + len(fall_risk_assessments)
        + len(pressure_ulcer_assessments) + len(nutritional_assessments)
        + len(fluid_balance_records) + len(glasgow_coma_scales) + len(nursing_care_plans)
    )

    attachments = list(
        PatientAttachment.objects.select_related('category', 'uploaded_by')
        .filter(visit=visit, is_current=True, is_deleted=False)
        .order_by('-uploaded_at')
    )
    if not request.user.has_perm('core.view_confidential_attachments'):
        attachments = [a for a in attachments if not a.is_confidential]

    medical_certificates = list(visit.medical_certificates.filter(is_current=True))
    death_certificates = list(visit.death_certificates.filter(is_current=True))
    physical_exams = list(visit.physical_exams.select_related('examiner', 'template').order_by('-created_at'))

    tab_list = [
        {'id': 'notes',         'label': 'Notes',        'count': len(clinical_notes)},
        {'id': 'physical_exam', 'label': 'Physical Exam','count': len(physical_exams)},
        {'id': 'vitals',        'label': 'Vitals',       'count': len(vital_signs)},
        {'id': 'diagnosis',     'label': 'Diagnosis',    'count': len(diagnoses)},
        {'id': 'lab',           'label': 'Lab',          'count': len(lab_orders)},
        {'id': 'imaging',       'label': 'Imaging',      'count': len(imaging_orders)},
        {'id': 'medications',   'label': 'Meds',         'count': len(medication_orders)},
        {'id': 'prescriptions', 'label': 'Prescriptions','count': len(prescriptions)},
        {'id': 'procedures',    'label': 'Procedures',   'count': len(procedure_orders)},
        {'id': 'nursing',       'label': 'Nursing',      'count': nursing_tab_count},
        {'id': 'attachments',   'label': 'Attachments',  'count': len(attachments)},
    ]

    log_action(
        request.user, AuditLog.Action.ACCESS, AuditLog.Module.VISIT,
        object_type='Visit', object_id=visit.pk,
        object_repr=f'{visit.patient.full_name} — visit {visit.pk}',
        description=f'Clinical record accessed for {visit.patient.full_name}',
        request=request,
    )
    if visit.status == 'waiting_doctor':
        try:
            from .patient_flow import advance_to_consultation_started
            advance_to_consultation_started(visit, performed_by=request.user)
        except Exception:
            pass
    context = {
        'visit': visit,
        'active_tab': active_tab,
        'tab_list': tab_list,
        'clinical_notes': clinical_notes,
        'diagnoses': diagnoses,
        'lab_orders': lab_orders,
        'imaging_orders': imaging_orders,
        'medication_orders': medication_orders,
        'procedure_orders': procedure_orders,
        'surgery_orders': surgery_orders,
        'current_admission': current_admission,
        'vital_signs': vital_signs,
        'latest_vitals': vital_signs[0] if vital_signs else None,
        'active_diagnoses': [d for d in diagnoses if d.status in ('Active', 'Chronic')],
        'prescriptions': prescriptions,
        'appointment_types': Appointment.AppointmentType.choices,
        'today': timezone.localdate(),
        'nursing_assessments': nursing_assessments,
        'pain_assessments': pain_assessments,
        'fall_risk_assessments': fall_risk_assessments,
        'pressure_ulcer_assessments': pressure_ulcer_assessments,
        'nutritional_assessments': nutritional_assessments,
        'fluid_balance_records': fluid_balance_records,
        'glasgow_coma_scales': glasgow_coma_scales,
        'nursing_care_plans': nursing_care_plans,
        'attachments': attachments,
        'medical_certificates': medical_certificates,
        'death_certificates': death_certificates,
        'physical_exams': physical_exams,
    }
    return render(request, 'doctor/visit_detail.html', context)


# ── Clinical Note ─────────────────────────────────────────────────────────────

@hms_permission_required('core.write_clinical_note')
def clinical_note_create(request, visit_id):
    from .views_physical_exam import EXAM_SYSTEMS
    visit = _get_visit(visit_id)
    if request.method == 'POST':
        form = ClinicalNoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.visit = visit
            note.authored_by = request.user
            note.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.DOCTOR,
                object_type='ClinicalNote', object_id=note.pk,
                object_repr=f'{visit.patient.full_name} — note {note.pk}',
                description=f'Clinical note added for {visit.patient.full_name}',
                request=request,
            )
            # Save linked physical examination if any systems were documented
            findings = {}
            for key, label, group, qn, qf in EXAM_SYSTEMS:
                status = request.POST.get(f'exam_{key}_status', '').strip()
                comment = request.POST.get(f'exam_{key}_comment', '').strip()
                if status or comment:
                    findings[key] = {'status': status, 'comment': comment}
            overall_summary = request.POST.get('exam_overall_summary', '').strip()
            if findings or overall_summary:
                PhysicalExamination.objects.create(
                    visit=visit,
                    clinical_note=note,
                    examiner=request.user,
                    findings=findings,
                    overall_summary=overall_summary,
                    is_complete='exam_complete' in request.POST,
                )
            messages.success(request, 'Clinical note saved.')
            return redirect('visit_detail', visit_id=visit_id)
    else:
        form = ClinicalNoteForm()
    grouped = OrderedDict()
    for key, label, group, qn, qf in EXAM_SYSTEMS:
        if group not in grouped:
            grouped[group] = []
        grouped[group].append((key, label, qn))
    return render(request, 'doctor/clinical_note_form.html', {
        'form': form,
        'visit': visit,
        'grouped_exam_systems': grouped,
    })


# ── Diagnosis ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.write_diagnosis')
def diagnosis_create(request, visit_id):
    visit = _get_visit(visit_id)
    if request.method == 'POST':
        form = DiagnosisForm(request.POST)
        if form.is_valid():
            dx = form.save(commit=False)
            dx.visit = visit
            dx.authored_by = request.user
            dx.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.DOCTOR,
                object_type='Diagnosis', object_id=dx.pk,
                object_repr=f'{visit.patient.full_name} — {dx.icd_code or "dx"}',
                description=f'Diagnosis recorded for {visit.patient.full_name}: {dx.icd_code or dx.description[:50]}',
                request=request,
            )
            messages.success(request, 'Diagnosis added.')
            return redirect('visit_detail', visit_id=visit_id)
    else:
        form = DiagnosisForm()
    return render(request, 'doctor/diagnosis_form.html', {'form': form, 'visit': visit})


# ── Lab Order ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.request_lab_test')
def lab_order_multi_create(request, visit_id):
    """Multi-test lab ordering with AJAX search. Replaces free-text ordering."""
    visit = _get_visit(visit_id)

    if request.method == 'POST':
        service_ids   = request.POST.getlist('service_ids')
        priority      = request.POST.get('priority', LabOrder.Priority.ROUTINE)
        clinical_notes = request.POST.get('clinical_notes', '').strip()

        if not service_ids:
            messages.error(request, 'Please select at least one laboratory test.')
            return redirect('lab_order_multi_create', visit_id=visit_id)

        # Note: panel -> child-test auto-inclusion happens client-side, at
        # selection time, in lab_order_multi_form.html — clicking a panel
        # adds all its component tests to the cart for the doctor to review
        # (and remove individual lines) before submitting. The server trusts
        # exactly whatever service_ids are submitted here; it does not
        # silently re-expand a panel id into its children on submit, which
        # would risk double-billing a panel that already carries its own
        # bundle price alongside each child's individual price.
        services = LabService.objects.filter(pk__in=service_ids, is_active=True)
        if not services.exists():
            messages.error(request, 'No valid tests selected.')
            return redirect('lab_order_multi_create', visit_id=visit_id)

        from django.db import transaction as db_transaction
        created_orders = []
        with db_transaction.atomic():
            for svc in services:
                price_cat = 'emergency' if priority == LabOrder.Priority.STAT else 'standard'
                unit_price = svc.get_price(price_cat)
                order = LabOrder.objects.create(
                    visit=visit,
                    ordered_by=request.user,
                    lab_service=svc,
                    test_name=svc.name,
                    test_category=svc.category.name if svc.category else '',
                    priority=priority,
                    clinical_notes=clinical_notes,
                    unit_price=unit_price,
                    status=LabOrder.Status.WAITING_PAYMENT,
                )
                if unit_price > 0:
                    _attach_invoice_item(
                        order, visit, request.user,
                        order.test_name, InvoiceItem.ServiceType.LAB,
                    )
                created_orders.append(order)
                log_action(
                    request.user, AuditLog.Action.CREATE, AuditLog.Module.LABORATORY,
                    object_type='LabOrder', object_id=order.pk,
                    object_repr=f'{visit.patient.full_name} — {order.test_name}',
                    description=f'Lab order: {order.test_name} for {visit.patient.full_name} (ETB {unit_price})',
                    request=request,
                )

        total = sum(o.unit_price for o in created_orders)
        messages.success(
            request,
            f'{len(created_orders)} test(s) ordered. Total: ETB {total:,.2f} added to invoice.'
        )
        return redirect('visit_detail', visit_id=visit_id)

    return render(request, 'doctor/lab_order_multi_form.html', {
        'visit': visit,
        'priority_choices': LabOrder.Priority.choices,
    })


def _attach_invoice_item(order, visit, created_by, description, service_type, quantity=1):
    """Find or create an open invoice for this visit and append a billable line item.

    Shared by lab, imaging, and medication orders. A visit's earlier invoice
    (e.g. the consultation fee) may already be Paid/Cancelled/Waived by the
    time a new charge is ordered — that invoice must not be reopened, so a
    fresh invoice is created for the new charge instead of silently dropping it.
    """
    from decimal import Decimal
    invoice = (
        Invoice.objects
        .filter(visit=visit)
        .exclude(status__in=[
            Invoice.Status.CANCELLED, Invoice.Status.REFUNDED,
            Invoice.Status.PAID, Invoice.Status.OVERPAID, Invoice.Status.WAIVED,
        ])
        .order_by('-created_at')
        .first()
    )
    if invoice is None:
        invoice = Invoice.objects.create(
            patient=visit.patient,
            visit=visit,
            created_by=created_by,
            status=Invoice.Status.ISSUED,
        )

    item = InvoiceItem.objects.create(
        invoice=invoice,
        description=description,
        service_type=service_type,
        quantity=quantity,
        unit_price=order.unit_price,
    )
    invoice.total_amount = (invoice.total_amount or Decimal('0')) + item.total
    invoice.save(update_fields=['total_amount'])

    order.invoice_item = item
    order.save(update_fields=['invoice_item'])


# ── Imaging Order ─────────────────────────────────────────────────────────────

@hms_permission_required('core.request_imaging')
def imaging_order_create(request, visit_id):
    """Multi-study imaging ordering with AJAX search, mirroring lab ordering."""
    visit = _get_visit(visit_id)

    if request.method == 'POST':
        service_ids          = request.POST.getlist('service_ids')
        priority              = request.POST.get('priority', ImagingOrder.Priority.ROUTINE)
        clinical_indication   = request.POST.get('clinical_indication', '').strip()

        if not service_ids:
            messages.error(request, 'Please select at least one imaging study.')
            return redirect('imaging_order_create', visit_id=visit_id)

        services = ImagingService.objects.filter(pk__in=service_ids, is_active=True)
        if not services.exists():
            messages.error(request, 'No valid studies selected.')
            return redirect('imaging_order_create', visit_id=visit_id)

        from django.db import transaction as db_transaction
        created_orders = []
        with db_transaction.atomic():
            for svc in services:
                price_cat = 'emergency' if priority == ImagingOrder.Priority.STAT else 'standard'
                unit_price = svc.get_price(price_cat)
                order = ImagingOrder.objects.create(
                    visit=visit,
                    ordered_by=request.user,
                    imaging_service=svc,
                    imaging_type=svc.modality,
                    body_part=svc.body_part or svc.name,
                    clinical_indication=clinical_indication,
                    priority=priority,
                    unit_price=unit_price,
                    status=ImagingOrder.Status.WAITING_PAYMENT,
                )
                if unit_price > 0:
                    _attach_invoice_item(
                        order, visit, request.user,
                        svc.name, InvoiceItem.ServiceType.IMAGING,
                    )
                created_orders.append(order)
                log_action(
                    request.user, AuditLog.Action.CREATE, AuditLog.Module.RADIOLOGY,
                    object_type='ImagingOrder', object_id=order.pk,
                    object_repr=f'{visit.patient.full_name} — {order.imaging_type} {order.body_part}',
                    description=f'Imaging order: {svc.name} for {visit.patient.full_name} (ETB {unit_price})',
                    request=request,
                )

        total = sum(o.unit_price for o in created_orders)
        messages.success(
            request,
            f'{len(created_orders)} study(ies) ordered. Total: ETB {total:,.2f} added to invoice.'
        )
        return redirect('visit_detail', visit_id=visit_id)

    return render(request, 'doctor/imaging_order_multi_form.html', {
        'visit': visit,
        'priority_choices': ImagingOrder.Priority.choices,
    })


# ── Medication Order ──────────────────────────────────────────────────────────

@hms_permission_required('core.write_prescription')
def medication_order_create(request, visit_id):
    visit = _get_visit(visit_id)
    if request.method == 'POST':
        form = MedicationOrderForm(request.POST)
        if form.is_valid():
            order = form.save(commit=False)
            order.visit = visit
            order.ordered_by = request.user

            # Pull pricing from pharmacy stock if a catalogue item was selected
            stock = form.cleaned_data.get('pharmacy_stock')
            if stock:
                order.pharmacy_stock = stock
                if not order.drug_name:
                    order.drug_name = f"{stock.drug_name} {stock.strength}".strip()
                order.unit_price = stock.selling_price

            if order.unit_price <= 0:
                # Nothing to bill (e.g. free ward stock) — treat as cleared
                # so it isn't stuck waiting on a charge that doesn't exist.
                order.payment_status = MedicationOrder.PaymentStatus.WAIVED

            order.save()

            if order.unit_price > 0:
                _attach_invoice_item(
                    order, visit, request.user,
                    f'{order.drug_name} ({order.dosage}, {order.frequency}) x{order.quantity}',
                    InvoiceItem.ServiceType.MEDICATION,
                    quantity=order.quantity,
                )

            # Auto-generate MAR entries if patient is currently admitted
            is_admitted = Admission.objects.filter(
                visit=visit, status=Admission.Status.ADMITTED,
            ).exists()
            if is_admitted:
                from .views_prescription import generate_mar_entries_from_medication_order
                entries = generate_mar_entries_from_medication_order(order, ordered_at=timezone.now())
                mar_msg = f' {len(entries)} dose(s) scheduled in MAR.' if entries else ' PRN order — nurse records doses as needed.'
            else:
                mar_msg = ''

            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.DOCTOR,
                object_type='MedicationOrder', object_id=order.pk,
                object_repr=f'{visit.patient.full_name} — {order.drug_name}',
                description=f'Medication ordered: {order.drug_name} for {visit.patient.full_name} (ETB {order.unit_price})' + (' [MAR entries created]' if is_admitted else ''),
                request=request,
            )
            base_msg = f'Medication order created. ETB {order.unit_price * order.quantity:,.2f} added to invoice.' if order.unit_price > 0 else 'Medication order created.'
            messages.success(request, base_msg + mar_msg)
            return redirect('visit_detail', visit_id=visit_id)
    else:
        form = MedicationOrderForm()
    return render(request, 'doctor/medication_order_form.html', {'form': form, 'visit': visit})


@hms_permission_required('core.write_prescription')
@require_POST
def medication_order_discontinue(request, visit_id, order_id):
    order = get_object_or_404(MedicationOrder, pk=order_id, visit_id=visit_id)
    reason = request.POST.get('reason', '')
    order.status = MedicationOrder.Status.DISCONTINUED
    order.discontinued_reason = reason
    order.save()
    messages.success(request, f'Medication "{order.drug_name}" discontinued.')
    return redirect('visit_detail', visit_id=visit_id)


# ── Procedure Order ───────────────────────────────────────────────────────────

@hms_permission_required('core.write_clinical_note')
def procedure_order_create(request, visit_id):
    visit = _get_visit(visit_id)
    if request.method == 'POST':
        form = ProcedureOrderForm(request.POST)
        if form.is_valid():
            order = form.save(commit=False)
            order.visit = visit
            order.ordered_by = request.user
            order.save()
            messages.success(request, 'Procedure order created.')
            return redirect('visit_detail', visit_id=visit_id)
    else:
        form = ProcedureOrderForm()
    return render(request, 'doctor/procedure_order_form.html', {'form': form, 'visit': visit})


# ── Vital Signs ───────────────────────────────────────────────────────────────

@hms_permission_required('core.record_vital_signs')
def vital_sign_create(request, visit_id):
    visit = _get_visit(visit_id)
    if request.method == 'POST':
        form = VitalSignForm(request.POST)
        if form.is_valid():
            vs = form.save(commit=False)
            vs.visit = visit
            vs.recorded_by = request.user
            vs.save()
            messages.success(request, 'Vital signs recorded.')
            return redirect('visit_detail', visit_id=visit_id)
    else:
        form = VitalSignForm()
    return render(request, 'doctor/vital_sign_form.html', {'form': form, 'visit': visit})


# ── Patient History ───────────────────────────────────────────────────────────

@hms_permission_required('core.read_clinical_note')
def patient_history(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    visits = (
        patient.visits
        .select_related('doctor', 'department')
        .prefetch_related('clinical_notes', 'diagnoses', 'vital_signs')
        .order_by('-created_at')
    )
    log_action(
        request.user, AuditLog.Action.ACCESS, AuditLog.Module.PATIENT,
        object_type='Patient', object_id=patient.pk,
        object_repr=patient.full_name,
        description=f'Patient medical history accessed: {patient.full_name}',
        request=request,
    )
    return render(request, 'doctor/patient_history.html', {
        'patient': patient,
        'visits': visits,
    })


# ── Follow-Up Appointment (Doctor-initiated) ──────────────────────────────────

@hms_permission_required('core.manage_appointments')
def schedule_followup(request, visit_id):
    visit = _get_visit(visit_id)
    patient = visit.patient

    if request.method == 'POST':
        date_str = request.POST.get('appointment_date', '').strip()
        time_str = request.POST.get('appointment_time', '').strip() or '18:00'
        appointment_type = request.POST.get('appointment_type', Appointment.AppointmentType.FOLLOWUP_CONSULTATION)
        priority_in = request.POST.get('priority', Appointment.Priority.NORMAL)
        priority = Appointment.Priority.URGENT if priority_in == Appointment.Priority.URGENT else Appointment.Priority.NORMAL
        reason_for_visit = request.POST.get('reason_for_visit', '').strip()
        notes = request.POST.get('notes', '').strip()

        errors = []
        if not date_str:
            errors.append('Follow-up date is required.')

        appt_date = None
        appt_time = None
        if not errors:
            try:
                appt_date = datetime.date.fromisoformat(date_str)
                appt_time = datetime.time.fromisoformat(time_str)
            except ValueError:
                errors.append('Invalid date or time format.')

        if not errors:
            appt = Appointment.objects.create(
                patient=patient,
                doctor=visit.doctor,
                department=visit.department,
                appointment_date=appt_date,
                appointment_time=appt_time,
                appointment_type=appointment_type,
                visit_type=Appointment.VisitType.FOLLOW_UP,
                priority=priority,
                referral_source=Appointment.ReferralSource.REFERRAL,
                reason_for_visit=reason_for_visit,
                notes=notes,
                phone_number=patient.mobile or '',
                status=Appointment.Status.SCHEDULED,
                visit=visit,
                created_by=request.user,
            )
            AppointmentStatusLog.objects.create(
                appointment=appt,
                from_status='',
                to_status=Appointment.Status.SCHEDULED,
                changed_by=request.user,
                notes='Follow-up appointment scheduled by treating doctor',
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.APPOINTMENT,
                object_type='Appointment', object_id=appt.pk,
                object_repr=appt.appointment_number,
                description=f'Follow-up appointment {appt.appointment_number} scheduled for '
                            f'{patient.full_name} by Dr. {visit.doctor.full_name} (from visit #{visit.pk})',
                request=request,
            )
            messages.success(request, f'Follow-up appointment {appt.appointment_number} scheduled successfully.')
            return redirect('appt_slip', pk=appt.pk)

        return render(request, 'doctor/schedule_followup_form.html', {
            'visit': visit,
            'patient': patient,
            'errors': errors,
            'post': request.POST,
            'appointment_types': Appointment.AppointmentType.choices,
            'today': timezone.localdate(),
        })

    # GET
    return render(request, 'doctor/schedule_followup_form.html', {
        'visit': visit,
        'patient': patient,
        'appointment_types': Appointment.AppointmentType.choices,
        'today': timezone.localdate(),
    })
