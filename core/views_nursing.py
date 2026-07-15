"""
Nursing Module — dashboard, ward-scoped patient list, structured nursing
assessments (EMR-integrated), shift handover, ward consumables (with
billing), and a read-only pending-doctor-orders view.

Consumes the existing Facility module (Ward/Room/Bed/Admission — built
earlier and NOT reimplemented here) and the existing MAR (Medication
Administration Record, `views_prescription.py`) rather than duplicating
either.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .forms import (
    FallRiskAssessmentForm, FluidBalanceRecordForm, GlasgowComaScaleForm,
    NursingAssessmentForm, NursingCarePlanForm, NursingHandoverNoteForm,
    NutritionalAssessmentForm, PainAssessmentForm, PressureUlcerAssessmentForm,
)
from .models import (
    Admission, AuditLog, DepartmentStock, DepartmentStore, DepartmentUsage, FallRiskAssessment,
    FluidBalanceRecord, GlasgowComaScale, ImagingOrder, InventoryItem,
    Invoice, InvoiceItem, LabOrder, MAREntry, NursingAssessment, NursingCarePlan,
    NursingHandoverNote, NutritionalAssessment, PainAssessment, PressureUlcerAssessment,
    ProcedureOrder, TransferRequest, Visit, Ward,
)


def _get_visit(visit_id):
    return get_object_or_404(
        Visit.objects.select_related('patient', 'doctor', 'department'), pk=visit_id,
    )


def _log(user, action, desc, request=None):
    log_action(user, action, AuditLog.Module.NURSING, description=desc, request=request)


def _nurse_wards(user):
    """Wards this nurse is presumed to work in — inferred from any ward
    they're currently the assigned_nurse for, plus (if they hold store
    management perms) every active ward store. Kept simple for v1: no
    explicit nurse-to-ward roster model exists yet."""
    admitted_ward_ids = Admission.objects.filter(
        status=Admission.Status.ADMITTED, assigned_nurse=user,
    ).values_list('bed__room__ward_id', flat=True).distinct()
    return Ward.objects.filter(id__in=admitted_ward_ids)


def _attach_consumable_invoice_item(usage, visit, created_by):
    """Bill a visit for a billable ward consumable — same find-or-create
    Invoice pattern as _attach_invoice_item() in views_doctor.py."""
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
            patient=visit.patient, visit=visit, created_by=created_by, status=Invoice.Status.ISSUED,
        )

    item = InvoiceItem.objects.create(
        invoice=invoice,
        description=usage.item_name,
        service_type=InvoiceItem.ServiceType.OTHER,
        quantity=usage.quantity_used,
        unit_price=usage.inventory_item.selling_price,
    )
    invoice.total_amount = (invoice.total_amount or Decimal('0')) + item.total
    invoice.save(update_fields=['total_amount'])
    usage.invoice_item = item
    usage.save(update_fields=['invoice_item'])
    return item


def _usage_num():
    n = (DepartmentUsage.objects.order_by('-id').values_list('id', flat=True).first() or 0) + 1
    return f"USE-{n:06d}"


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_nursing_dashboard')
def nursing_dashboard(request):
    now = timezone.now()
    today = timezone.localdate()
    vitals_cutoff = now - timedelta(hours=8)

    my_wards = _nurse_wards(request.user)
    my_ward_stores = DepartmentStore.objects.filter(
        store_type=DepartmentStore.StoreType.WARD, is_active=True,
    )

    my_patients = Admission.objects.filter(
        status=Admission.Status.ADMITTED, assigned_nurse=request.user,
    ).select_related('patient', 'bed__room__ward', 'visit__doctor')

    all_admitted = Admission.objects.filter(status=Admission.Status.ADMITTED).select_related(
        'patient', 'bed__room__ward',
    )
    by_ward = {}
    for adm in all_admitted:
        ward = adm.bed.room.ward
        by_ward.setdefault(ward, []).append(adm)

    new_admissions = all_admitted.filter(admitted_at__date=today).order_by('-admitted_at')

    meds_due = MAREntry.objects.filter(
        status=MAREntry.Status.SCHEDULED, scheduled_time__lte=now,
        visit__admissions__status=Admission.Status.ADMITTED,
    ).select_related('prescription_item', 'visit__patient').order_by('scheduled_time')[:25]

    admitted_visit_ids = all_admitted.values_list('visit_id', flat=True)
    vitals_due = all_admitted.exclude(
        visit__vital_signs__recorded_at__gte=vitals_cutoff,
    ).select_related('patient', 'bed__room__ward')[:25]

    pending_labs = LabOrder.objects.filter(
        visit_id__in=admitted_visit_ids,
    ).exclude(status__in=['Cancelled', 'Released', 'Completed']).select_related('visit__patient')[:15]
    pending_imaging = ImagingOrder.objects.filter(
        visit_id__in=admitted_visit_ids,
    ).exclude(status__in=['Cancelled', 'Completed']).select_related('visit__patient')[:15]
    pending_procedures = ProcedureOrder.objects.filter(
        visit_id__in=admitted_visit_ids,
    ).exclude(status__in=['Completed', 'Cancelled']).select_related('visit__patient')[:15]

    pending_requests = TransferRequest.objects.filter(
        requesting_store__in=my_ward_stores,
        status__in=[TransferRequest.Status.PENDING_WARD_APPROVAL, TransferRequest.Status.PENDING],
    ).select_related('requesting_store').order_by('-request_date')[:15]

    low_stock = []
    for store in my_ward_stores:
        low_stock.extend(store.low_stock_items)

    handovers = NursingHandoverNote.objects.filter(
        ward__in=my_wards or Ward.objects.filter(status=Ward.Status.ACTIVE),
    ).select_related('ward', 'from_nurse', 'patient').order_by('-handover_date', '-created_at')[:10]

    return render(request, 'nursing/dashboard.html', {
        'today': today,
        'my_patients': my_patients,
        'by_ward': by_ward,
        'new_admissions': new_admissions,
        'meds_due': meds_due,
        'vitals_due': vitals_due,
        'pending_labs': pending_labs,
        'pending_imaging': pending_imaging,
        'pending_procedures': pending_procedures,
        'pending_requests': pending_requests,
        'low_stock': low_stock,
        'handovers': handovers,
        'total_admitted': all_admitted.count(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Patient List
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_nursing_dashboard')
def nursing_patient_list(request):
    mode = request.GET.get('mode', 'inpatient')
    ward_id = request.GET.get('ward', '').strip()
    room_id = request.GET.get('room', '').strip()
    bed_id = request.GET.get('bed', '').strip()
    dept_id = request.GET.get('department', '').strip()
    doctor_id = request.GET.get('doctor', '').strip()
    priority = request.GET.get('priority', '').strip()
    admission_status = request.GET.get('status', '').strip()
    date_str = request.GET.get('date', '').strip()
    search = request.GET.get('q', '').strip()

    admissions = None
    visits = None

    if mode == 'inpatient':
        admissions = Admission.objects.select_related(
            'patient', 'bed__room__ward', 'visit__doctor', 'visit__department', 'assigned_nurse',
        ).order_by('-admitted_at')
        if admission_status:
            admissions = admissions.filter(status=admission_status)
        else:
            admissions = admissions.filter(status=Admission.Status.ADMITTED)
        if ward_id:
            admissions = admissions.filter(bed__room__ward_id=ward_id)
        if room_id:
            admissions = admissions.filter(bed__room_id=room_id)
        if bed_id:
            admissions = admissions.filter(bed_id=bed_id)
        if dept_id:
            admissions = admissions.filter(visit__department_id=dept_id)
        if doctor_id:
            admissions = admissions.filter(visit__doctor_id=doctor_id)
        if priority:
            admissions = admissions.filter(priority=priority)
        if date_str:
            admissions = admissions.filter(admitted_at__date=date_str)
        if search:
            admissions = admissions.filter(
                Q(patient__first_name__icontains=search) | Q(patient__last_name__icontains=search)
                | Q(patient__card_number__icontains=search)
            )
    else:
        visits = Visit.objects.select_related('patient', 'doctor', 'department').order_by('-created_at')
        if dept_id:
            visits = visits.filter(department_id=dept_id)
        if doctor_id:
            visits = visits.filter(doctor_id=doctor_id)
        if date_str:
            visits = visits.filter(created_at__date=date_str)
        if search:
            visits = visits.filter(
                Q(patient__first_name__icontains=search) | Q(patient__last_name__icontains=search)
                | Q(patient__card_number__icontains=search)
            )
        visits = visits[:100]

    from .models import Department, Doctor
    return render(request, 'nursing/patient_list.html', {
        'mode': mode,
        'admissions': admissions,
        'visits': visits,
        'wards': Ward.objects.filter(status=Ward.Status.ACTIVE).order_by('name'),
        'departments': Department.objects.all().order_by('name'),
        'doctors': Doctor.objects.filter(active=True).order_by('first_name'),
        'priority_choices': Admission.Priority.choices,
        'status_choices': Admission.Status.choices,
        'filters': {
            'ward': ward_id, 'room': room_id, 'bed': bed_id, 'department': dept_id,
            'doctor': doctor_id, 'priority': priority, 'status': admission_status,
            'date': date_str, 'q': search,
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# Structured Nursing Assessments — all append-only (no edit view), matching
# the PeriopNursingAddendum precedent.
# ─────────────────────────────────────────────────────────────────────────────

def _assessment_create(request, visit_id, model_cls, form_cls, template, label):
    visit = _get_visit(visit_id)
    if request.method == 'POST':
        form = form_cls(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.visit = visit
            obj.recorded_by = request.user
            obj.save()
            _log(
                request.user, AuditLog.Action.CREATE,
                f'{label} recorded for {visit.patient.full_name}', request=request,
            )
            messages.success(request, f'{label} saved.')
            return redirect('visit_detail', visit_id=visit_id)
    else:
        form = form_cls()
    return render(request, template, {'form': form, 'visit': visit, 'label': label})


@hms_permission_required('core.write_nursing_note')
def nursing_assessment_create(request, visit_id):
    return _assessment_create(
        request, visit_id, NursingAssessment, NursingAssessmentForm,
        'nursing/nursing_assessment_form.html', 'Nursing Assessment',
    )


@hms_permission_required('core.write_nursing_note')
def pain_assessment_create(request, visit_id):
    return _assessment_create(
        request, visit_id, PainAssessment, PainAssessmentForm,
        'nursing/pain_assessment_form.html', 'Pain Assessment',
    )


@hms_permission_required('core.write_nursing_note')
def fall_risk_assessment_create(request, visit_id):
    return _assessment_create(
        request, visit_id, FallRiskAssessment, FallRiskAssessmentForm,
        'nursing/fall_risk_assessment_form.html', 'Fall Risk Assessment',
    )


@hms_permission_required('core.write_nursing_note')
def pressure_ulcer_assessment_create(request, visit_id):
    return _assessment_create(
        request, visit_id, PressureUlcerAssessment, PressureUlcerAssessmentForm,
        'nursing/pressure_ulcer_assessment_form.html', 'Pressure Ulcer Risk Assessment',
    )


@hms_permission_required('core.write_nursing_note')
def nutritional_assessment_create(request, visit_id):
    return _assessment_create(
        request, visit_id, NutritionalAssessment, NutritionalAssessmentForm,
        'nursing/nutritional_assessment_form.html', 'Nutritional Assessment',
    )


@hms_permission_required('core.write_nursing_note')
def fluid_balance_create(request, visit_id):
    return _assessment_create(
        request, visit_id, FluidBalanceRecord, FluidBalanceRecordForm,
        'nursing/fluid_balance_form.html', 'Fluid Balance / Intake & Output Record',
    )


@hms_permission_required('core.write_nursing_note')
def gcs_create(request, visit_id):
    return _assessment_create(
        request, visit_id, GlasgowComaScale, GlasgowComaScaleForm,
        'nursing/gcs_form.html', 'Glasgow Coma Scale',
    )


@hms_permission_required('core.write_nursing_note')
def care_plan_create(request, visit_id):
    visit = _get_visit(visit_id)
    if request.method == 'POST':
        form = NursingCarePlanForm(request.POST)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.visit = visit
            plan.created_by = request.user
            plan.save()
            _log(request.user, AuditLog.Action.CREATE, f'Nursing care plan created for {visit.patient.full_name}', request=request)
            messages.success(request, 'Nursing care plan saved.')
            return redirect('visit_detail', visit_id=visit_id)
    else:
        form = NursingCarePlanForm()
    return render(request, 'nursing/care_plan_form.html', {'form': form, 'visit': visit})


@hms_permission_required('core.write_nursing_note')
@require_POST
def care_plan_update_status(request, plan_id):
    plan = get_object_or_404(NursingCarePlan, pk=plan_id)
    status = request.POST.get('status', '')
    evaluation = request.POST.get('evaluation', '').strip()
    if status in dict(NursingCarePlan.Status.choices):
        plan.status = status
        if evaluation:
            plan.evaluation = evaluation
        plan.updated_by = request.user
        plan.save()
        _log(request.user, AuditLog.Action.UPDATE, f'Nursing care plan updated ({status}) for {plan.visit.patient.full_name}', request=request)
        messages.success(request, 'Care plan updated.')
    return redirect('visit_detail', visit_id=plan.visit_id)


# ─────────────────────────────────────────────────────────────────────────────
# Shift Handover
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_nursing_dashboard')
def handover_list(request):
    ward_id = request.GET.get('ward', '').strip()
    notes = NursingHandoverNote.objects.select_related('ward', 'from_nurse', 'to_nurse', 'patient').order_by('-handover_date', '-created_at')
    if ward_id:
        notes = notes.filter(ward_id=ward_id)
    return render(request, 'nursing/handover_list.html', {
        'notes': notes[:100],
        'wards': Ward.objects.filter(status=Ward.Status.ACTIVE).order_by('name'),
        'ward_id': ward_id,
    })


@hms_permission_required('core.write_nursing_note')
def handover_create(request):
    if request.method == 'POST':
        form = NursingHandoverNoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.from_nurse = request.user
            note.save()
            _log(request.user, AuditLog.Action.CREATE, f'Shift handover note created for {note.ward.name}', request=request)
            messages.success(request, 'Handover note saved.')
            return redirect('nursing_handover_list')
    else:
        form = NursingHandoverNoteForm()
    return render(request, 'nursing/handover_form.html', {'form': form})


# ─────────────────────────────────────────────────────────────────────────────
# Ward Consumables — deducts DepartmentStock, bills the patient if billable
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.record_dept_usage')
def ward_consumable_use(request, visit_id):
    visit = _get_visit(visit_id)
    store_id = request.GET.get('store') or request.POST.get('store')
    stores = DepartmentStore.objects.filter(store_type=DepartmentStore.StoreType.WARD, is_active=True)
    store = stores.filter(id=store_id).first() if store_id else stores.first()

    consumables = InventoryItem.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST' and store:
        item_id = request.POST.get('inventory_item')
        qty_str = request.POST.get('quantity', '0')
        try:
            qty = int(qty_str)
        except (TypeError, ValueError):
            qty = 0

        item = InventoryItem.objects.filter(id=item_id).first() if item_id else None
        if not item or qty <= 0:
            messages.error(request, 'Select a consumable and enter a quantity greater than zero.')
        else:
            dept_stock, _ = DepartmentStock.objects.get_or_create(
                department_store=store, inventory_item=item, defaults={'quantity_available': 0},
            )
            if dept_stock.quantity_available < qty:
                messages.error(request, f'Insufficient ward stock for "{item.name}". Available: {dept_stock.quantity_available}.')
            else:
                with transaction.atomic():
                    dept_stock.quantity_available -= qty
                    dept_stock.save(update_fields=['quantity_available'])

                    from .models import DepartmentUsage
                    usage = DepartmentUsage.objects.create(
                        usage_number=_usage_num(),
                        department_store=store,
                        inventory_item=item,
                        quantity_used=qty,
                        usage_type=DepartmentUsage.UsageType.ADMINISTRATION,
                        patient=visit.patient,
                        visit=visit,
                        responsible_staff=request.user,
                        usage_date=timezone.now(),
                        reason=request.POST.get('reason', '').strip(),
                    )
                    if item.is_billable:
                        _attach_consumable_invoice_item(usage, visit, request.user)

                _log(
                    request.user, AuditLog.Action.ISSUE,
                    f'Consumable "{item.name}" x{qty} used for {visit.patient.full_name}'
                    + (' (billed)' if item.is_billable else ''),
                    request=request,
                )
                messages.success(request, f'Recorded {qty} x "{item.name}" for {visit.patient.full_name}.')
                return redirect('visit_detail', visit_id=visit_id)

    return render(request, 'nursing/consumable_use_form.html', {
        'visit': visit, 'stores': stores, 'store': store, 'consumables': consumables,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Pending Doctor Orders — read-only aggregation
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_nursing_dashboard')
def nursing_pending_orders(request):
    admitted_visit_ids = Admission.objects.filter(
        status=Admission.Status.ADMITTED,
    ).values_list('visit_id', flat=True)

    lab_orders = LabOrder.objects.filter(
        visit_id__in=admitted_visit_ids,
    ).exclude(status__in=['Cancelled', 'Released', 'Completed']).select_related('visit__patient', 'ordered_by').order_by('-ordered_at')

    imaging_orders = ImagingOrder.objects.filter(
        visit_id__in=admitted_visit_ids,
    ).exclude(status__in=['Cancelled', 'Completed']).select_related('visit__patient', 'ordered_by').order_by('-ordered_at')

    procedure_orders = ProcedureOrder.objects.filter(
        visit_id__in=admitted_visit_ids,
    ).exclude(status__in=['Completed', 'Cancelled']).select_related('visit__patient', 'ordered_by').order_by('-ordered_at')

    return render(request, 'nursing/pending_orders.html', {
        'lab_orders': lab_orders,
        'imaging_orders': imaging_orders,
        'procedure_orders': procedure_orders,
    })
