from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .forms import (
    ClinicalNoteForm, DiagnosisForm, ImagingOrderForm,
    LabOrderForm, MedicationOrderForm, ProcedureOrderForm, VitalSignForm,
)
from .models import (
    AuditLog, ClinicalNote, Diagnosis, ImagingOrder, Invoice, InvoiceItem,
    LabOrder, MedicationOrder, Patient, ProcedureOrder, Visit, VitalSign,
)


def _get_visit(visit_id):
    return get_object_or_404(
        Visit.objects.select_related('patient', 'doctor', 'department'),
        pk=visit_id,
    )


# ── Dashboard ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.write_clinical_note')
def doctor_dashboard(request):
    today = timezone.localdate()
    today_visits = (
        Visit.objects.filter(created_at__date=today)
        .select_related('patient', 'doctor', 'department')
        .prefetch_related('queue')
        .order_by('-created_at')
    )
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
    }
    return render(request, 'doctor/dashboard.html', context)


# ── Patient List ──────────────────────────────────────────────────────────────

@hms_permission_required('core.write_clinical_note')
def doctor_patient_list(request):
    query = request.GET.get('q', '').strip()
    date_filter = request.GET.get('date', '').strip()

    visits = (
        Visit.objects.select_related('patient', 'doctor', 'department')
        .prefetch_related('queue')
        .order_by('-created_at')
    )
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

    clinical_notes = list(visit.clinical_notes.select_related('authored_by').all())
    diagnoses = list(visit.diagnoses.select_related('authored_by').all())
    lab_orders = list(visit.lab_orders.select_related('ordered_by').all())
    imaging_orders = list(visit.imaging_orders.select_related('ordered_by').all())
    medication_orders = list(visit.medication_orders.select_related('ordered_by').all())
    procedure_orders = list(visit.procedure_orders.select_related('ordered_by').all())
    vital_signs = list(visit.vital_signs.select_related('recorded_by').all())

    from .models import Prescription
    prescriptions = list(
        visit.prescriptions.select_related('prescribed_by')
        .prefetch_related('items')
        .order_by('-created_at')
    )

    tab_list = [
        {'id': 'notes',         'label': 'Notes',        'count': len(clinical_notes)},
        {'id': 'vitals',        'label': 'Vitals',       'count': len(vital_signs)},
        {'id': 'diagnosis',     'label': 'Diagnosis',    'count': len(diagnoses)},
        {'id': 'lab',           'label': 'Lab',          'count': len(lab_orders)},
        {'id': 'imaging',       'label': 'Imaging',      'count': len(imaging_orders)},
        {'id': 'medications',   'label': 'Meds',         'count': len(medication_orders)},
        {'id': 'prescriptions', 'label': 'Prescriptions','count': len(prescriptions)},
        {'id': 'procedures',    'label': 'Procedures',   'count': len(procedure_orders)},
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
        'vital_signs': vital_signs,
        'latest_vitals': vital_signs[0] if vital_signs else None,
        'active_diagnoses': [d for d in diagnoses if d.status in ('Active', 'Chronic')],
        'prescriptions': prescriptions,
    }
    return render(request, 'doctor/visit_detail.html', context)


# ── Clinical Note ─────────────────────────────────────────────────────────────

@hms_permission_required('core.write_clinical_note')
def clinical_note_create(request, visit_id):
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
            messages.success(request, 'Clinical note saved.')
            return redirect('visit_detail', visit_id=visit_id)
    else:
        form = ClinicalNoteForm()
    return render(request, 'doctor/clinical_note_form.html', {'form': form, 'visit': visit})


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
def lab_order_create(request, visit_id):
    visit = _get_visit(visit_id)
    if request.method == 'POST':
        form = LabOrderForm(request.POST)
        if form.is_valid():
            order = form.save(commit=False)
            order.visit = visit
            order.ordered_by = request.user

            # Pull pricing from service master if selected
            svc = form.cleaned_data.get('lab_service')
            if svc:
                order.lab_service = svc
                if not order.test_name:
                    order.test_name = svc.name
                if not order.test_category:
                    order.test_category = svc.section
                price_cat = 'emergency' if order.priority == 'STAT' else 'standard'
                order.unit_price = svc.get_price(price_cat)
            order.status = LabOrder.Status.WAITING_PAYMENT
            order.save()

            # Auto-create or update invoice item
            if order.unit_price > 0:
                _attach_invoice_item(order, visit, request.user)

            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.LABORATORY,
                object_type='LabOrder', object_id=order.pk,
                object_repr=f'{visit.patient.full_name} — {order.test_name}',
                description=f'Lab order: {order.test_name} for {visit.patient.full_name} (ETB {order.unit_price})',
                request=request,
            )
            messages.success(request, f'Lab order submitted. ETB {order.unit_price} added to invoice.')
            return redirect('visit_detail', visit_id=visit_id)
    else:
        form = LabOrderForm()
    return render(request, 'doctor/lab_order_form.html', {'form': form, 'visit': visit})


def _attach_invoice_item(order, visit, created_by):
    """Find or create the visit invoice and append a lab line item."""
    from decimal import Decimal
    # Find open (non-cancelled, non-refunded) invoice for this visit
    invoice = (
        Invoice.objects
        .filter(visit=visit)
        .exclude(status__in=[Invoice.Status.CANCELLED, Invoice.Status.REFUNDED])
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

    if invoice.status in (Invoice.Status.PAID, Invoice.Status.OVERPAID,
                          Invoice.Status.CANCELLED, Invoice.Status.REFUNDED, Invoice.Status.WAIVED):
        return  # don't modify a finalized invoice

    item = InvoiceItem.objects.create(
        invoice=invoice,
        description=order.test_name,
        service_type=InvoiceItem.ServiceType.LAB,
        quantity=1,
        unit_price=order.unit_price,
    )
    invoice.total_amount = (invoice.total_amount or Decimal('0')) + item.total
    invoice.save(update_fields=['total_amount'])

    order.invoice_item = item
    order.save(update_fields=['invoice_item'])


# ── Imaging Order ─────────────────────────────────────────────────────────────

@hms_permission_required('core.request_imaging')
def imaging_order_create(request, visit_id):
    visit = _get_visit(visit_id)
    if request.method == 'POST':
        form = ImagingOrderForm(request.POST)
        if form.is_valid():
            order = form.save(commit=False)
            order.visit = visit
            order.ordered_by = request.user
            order.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.RADIOLOGY,
                object_type='ImagingOrder', object_id=order.pk,
                object_repr=f'{visit.patient.full_name} — {order.study_type}',
                description=f'Imaging order: {order.study_type} for {visit.patient.full_name}',
                request=request,
            )
            messages.success(request, 'Imaging order submitted.')
            return redirect('visit_detail', visit_id=visit_id)
    else:
        form = ImagingOrderForm()
    return render(request, 'doctor/imaging_order_form.html', {'form': form, 'visit': visit})


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
            order.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.DOCTOR,
                object_type='MedicationOrder', object_id=order.pk,
                object_repr=f'{visit.patient.full_name} — {order.drug_name}',
                description=f'Medication ordered: {order.drug_name} for {visit.patient.full_name}',
                request=request,
            )
            messages.success(request, 'Medication order created.')
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
