"""
Inpatient Admission & Bed Management — the formal Admission Request pipeline
(diagnosis/reason capture → optional review → deposit/credit gate → bed
assignment) that sits in front of the existing Facility module's one-step
"Quick Admit" fast path (views_facility.admission_create). Both converge on
the same Admission row via views_facility._perform_admission().
"""
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .forms import AdmissionDepositRuleForm, AdmissionRequestForm
from .models import (
    Admission, AdmissionDepositRule, AdmissionRequest, AuditLog, Bed, Department,
    Invoice, InvoiceItem, Visit, Ward,
)
from .views_facility import _available_beds_qs, _perform_admission, _redirect_after_bed_action


# ── Deposit resolution & billing ─────────────────────────────────────────────

def resolve_deposit_amount(ward=None, department=None, priority=None):
    """Return (amount, is_exempt, matched_rule) for the given scope. The most
    specific active rule wins (ward+priority > ward > department > global
    fallback where all three are blank); is_exempt short-circuits to 'no
    deposit required' regardless of amount."""
    candidates = AdmissionDepositRule.objects.filter(is_active=True)
    matches = []
    for rule in candidates:
        if rule.ward_id and rule.ward_id != (ward.pk if ward else None):
            continue
        if rule.department_id and rule.department_id != (department.pk if department else None):
            continue
        if rule.priority and rule.priority != priority:
            continue
        matches.append(rule)
    if not matches:
        return Decimal('0'), False, None
    best = max(matches, key=lambda r: r.specificity)
    return (Decimal('0') if best.is_exempt else best.amount), best.is_exempt, best


def _attach_deposit_invoice_item(admission_request, amount, created_by):
    """Find or create an open invoice for the request's visit and append the
    deposit as a billable line item — mirrors _attach_invoice_item() in
    views_doctor.py / _attach_consumable_invoice_item() in views_nursing.py."""
    visit = admission_request.visit
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
        description=f'Admission Deposit — {admission_request.patient.full_name}',
        service_type=InvoiceItem.ServiceType.DEPOSIT,
        quantity=1,
        unit_price=amount,
    )
    invoice.total_amount = (invoice.total_amount or Decimal('0')) + item.total
    invoice.save(update_fields=['total_amount'])
    return item


def _requires_review(admission_request):
    """A lower-authority request (Nurse/Reception) is routed through an
    optional review stage before the deposit is raised; a request made
    directly by a Doctor (or from the ED/OR) is presumed already clinically
    validated and skips straight to the deposit gate."""
    return admission_request.request_source in (
        AdmissionRequest.Source.NURSE, AdmissionRequest.Source.RECEPTION,
    )


def _advance_to_deposit_stage(admission_request, user):
    """Resolve and (if non-exempt) raise the deposit charge, then move the
    request to AWAITING_DEPOSIT (or straight past it if exempt)."""
    ward = None
    if admission_request.admitting_department_id:
        ward = Ward.objects.filter(department_id=admission_request.admitting_department_id).first()
    amount, is_exempt, rule = resolve_deposit_amount(
        ward=ward, department=admission_request.admitting_department, priority=admission_request.priority,
    )
    admission_request.deposit_rule_snapshot = rule
    if is_exempt or amount <= 0:
        admission_request.status = AdmissionRequest.Status.AWAITING_BED
    else:
        item = _attach_deposit_invoice_item(admission_request, amount, user)
        admission_request.deposit_invoice_item = item
        admission_request.status = AdmissionRequest.Status.AWAITING_DEPOSIT
    admission_request.save()


# ── Admission Request pipeline ───────────────────────────────────────────────

@hms_permission_required('core.request_admission')
def admission_request_create(request, visit_id):
    visit = get_object_or_404(Visit.objects.select_related('patient'), pk=visit_id)
    patient = visit.patient

    if Admission.objects.filter(patient=patient, status=Admission.Status.ADMITTED).exists():
        messages.warning(request, f'{patient.full_name} already has an active admission.')
        return _redirect_after_bed_action(request, visit.pk)

    if request.method == 'POST':
        form = AdmissionRequestForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                admission_request = form.save(commit=False)
                admission_request.visit = visit
                admission_request.patient = patient
                admission_request.requested_by = request.user
                if _requires_review(admission_request):
                    admission_request.status = AdmissionRequest.Status.PENDING
                    admission_request.save()
                else:
                    admission_request.status = AdmissionRequest.Status.APPROVED
                    admission_request.save()
                    _advance_to_deposit_stage(admission_request, request.user)
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.ADMISSION,
                object_type='AdmissionRequest', object_id=admission_request.pk, object_repr=patient.full_name,
                description=f'Admission requested for {patient.full_name} ({admission_request.get_priority_display()})',
                request=request,
            )
            messages.success(request, f'Admission request created for {patient.full_name}.')
            return redirect('admission_request_detail', req_id=admission_request.pk)
    else:
        form = AdmissionRequestForm(initial={
            'admitting_doctor': getattr(request.user, 'doctor_profile', None),
        })

    return render(request, 'admissions/request_form.html', {
        'form': form, 'visit': visit, 'patient': patient,
    })


@hms_permission_required('core.view_admission_dashboard')
def admission_request_list(request):
    status_filter = request.GET.get('status', '')
    dept_filter = request.GET.get('department', '')
    priority_filter = request.GET.get('priority', '')

    qs = AdmissionRequest.objects.select_related(
        'patient', 'visit', 'admitting_department', 'admitting_doctor', 'requested_by',
    ).exclude(status__in=[AdmissionRequest.Status.ADMITTED, AdmissionRequest.Status.REJECTED, AdmissionRequest.Status.CANCELLED])

    if status_filter:
        qs = qs.filter(status=status_filter)
    if dept_filter:
        qs = qs.filter(admitting_department_id=dept_filter)
    if priority_filter:
        qs = qs.filter(priority=priority_filter)

    return render(request, 'admissions/request_list.html', {
        'requests': qs.order_by('-created_at'),
        'status_choices': AdmissionRequest.Status.choices,
        'priority_choices': AdmissionRequest.Priority.choices,
        'departments': Department.objects.filter(is_active=True),
        'status_filter': status_filter, 'dept_filter': dept_filter, 'priority_filter': priority_filter,
    })


@hms_permission_required('core.view_admission_dashboard')
def admission_request_detail(request, req_id):
    req = get_object_or_404(
        AdmissionRequest.objects.select_related(
            'patient', 'visit', 'admitting_department', 'admitting_doctor',
            'requested_by', 'reviewed_by', 'deposit_invoice_item', 'deposit_rule_snapshot',
        ),
        pk=req_id,
    )
    return render(request, 'admissions/request_detail.html', {
        'req': req,
        'can_review': _requires_review(req) and req.status == AdmissionRequest.Status.PENDING,
    })


@require_POST
@hms_permission_required('core.review_admission_request')
def admission_request_review(request, req_id):
    req = get_object_or_404(AdmissionRequest, pk=req_id)
    if req.status != AdmissionRequest.Status.PENDING:
        messages.error(request, 'This request is not awaiting review.')
        return redirect('admission_request_detail', req_id=req.pk)

    decision = request.POST.get('decision', '')
    req.reviewed_by = request.user
    req.reviewed_at = timezone.now()

    if decision == 'approve':
        req.status = AdmissionRequest.Status.APPROVED
        req.save()
        _advance_to_deposit_stage(req, request.user)
        log_action(
            request.user, AuditLog.Action.APPROVE, AuditLog.Module.ADMISSION,
            object_type='AdmissionRequest', object_id=req.pk, object_repr=req.patient.full_name,
            description=f'Admission request for {req.patient.full_name} approved', request=request,
        )
        messages.success(request, 'Admission request approved.')
    elif decision == 'reject':
        req.status = AdmissionRequest.Status.REJECTED
        req.rejection_reason = request.POST.get('rejection_reason', '').strip()
        req.save()
        log_action(
            request.user, AuditLog.Action.REJECT, AuditLog.Module.ADMISSION,
            object_type='AdmissionRequest', object_id=req.pk, object_repr=req.patient.full_name,
            description=f'Admission request for {req.patient.full_name} rejected: {req.rejection_reason or "—"}',
            request=request,
        )
        messages.warning(request, 'Admission request rejected.')
    else:
        messages.error(request, 'Invalid decision.')
    return redirect('admission_request_detail', req_id=req.pk)


@require_POST
@hms_permission_required('core.approve_credit_invoice')
def admission_deposit_credit_approve(request, req_id):
    req = get_object_or_404(AdmissionRequest.objects.select_related('deposit_invoice_item'), pk=req_id)
    item = req.deposit_invoice_item
    if item is None:
        messages.error(request, 'This request has no deposit charge to credit-approve.')
        return redirect('admission_request_detail', req_id=req.pk)
    if item.payment_status == InvoiceItem.PaymentStatus.CREDIT:
        messages.info(request, 'Credit already approved for this deposit.')
        return redirect('admission_request_detail', req_id=req.pk)
    if item.payment_status not in (InvoiceItem.PaymentStatus.PENDING_PAYMENT, InvoiceItem.PaymentStatus.PARTIAL):
        messages.error(request, f'Cannot approve credit: deposit status is {item.payment_status}.')
        return redirect('admission_request_detail', req_id=req.pk)

    reason = request.POST.get('reason', '').strip()
    item.payment_status = InvoiceItem.PaymentStatus.CREDIT
    item.credit_approved_by = request.user
    item.credit_approved_at = timezone.now()
    item.credit_reason = reason
    item.save(update_fields=['payment_status', 'credit_approved_by', 'credit_approved_at', 'credit_reason'])

    req.status = AdmissionRequest.Status.AWAITING_BED
    req.save(update_fields=['status', 'updated_at'])

    log_action(
        request.user, AuditLog.Action.APPROVE, AuditLog.Module.ADMISSION,
        object_type='AdmissionRequest', object_id=req.pk, object_repr=req.patient.full_name,
        description=f'Deposit credit-approved for {req.patient.full_name}. Reason: {reason or "Not specified"}',
        request=request,
    )
    messages.success(request, f'Deposit credit approved. {req.patient.full_name} can now be assigned a bed.')
    return redirect('admission_request_detail', req_id=req.pk)


@hms_permission_required('core.assign_bed')
def admission_request_assign_bed(request, req_id):
    req = get_object_or_404(AdmissionRequest.objects.select_related('patient', 'visit', 'admitting_department'), pk=req_id)
    if req.status not in (AdmissionRequest.Status.AWAITING_BED, AdmissionRequest.Status.AWAITING_DEPOSIT):
        messages.error(request, 'This request is not ready for bed assignment.')
        return redirect('admission_request_detail', req_id=req.pk)
    if not req.deposit_cleared:
        messages.error(request, 'Deposit is not yet paid or credit-approved for this request.')
        return redirect('admission_request_detail', req_id=req.pk)

    department = request.GET.get('department', str(req.admitting_department_id or ''))
    ward_type = request.GET.get('ward_type', '')
    room_type = request.GET.get('room_type', '')
    gender = req.patient.sex if req.patient.sex in ('Male', 'Female') else ''
    available_beds = _available_beds_qs(department, ward_type, room_type, gender)
    if req.requires_isolation:
        available_beds = available_beds.filter(ward__is_isolation=True)
    if req.requires_icu:
        available_beds = available_beds.filter(ward__is_icu_hdu=True)

    if request.method == 'POST':
        bed_id = request.POST.get('bed_id')
        bed = available_beds.filter(pk=bed_id).first()
        if not bed:
            messages.error(request, 'That bed is no longer available. Please select another.')
            return redirect('admission_request_assign_bed', req_id=req_id)
        try:
            with transaction.atomic():
                admission = _perform_admission(
                    req.patient, req.visit, bed, request.user,
                    admission_request=req, priority=req.priority,
                )
                req.status = AdmissionRequest.Status.ADMITTED
                req.save(update_fields=['status', 'updated_at'])
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.ADMISSION,
                object_type='Admission', object_id=admission.pk, object_repr=f'{req.patient.full_name} → {bed.bed_code}',
                description=f'{req.patient.full_name} admitted to bed {bed.bed_code} ({bed.room.ward.name}) via admission request',
                request=request,
            )
            messages.success(request, f'{req.patient.full_name} admitted to bed {bed.bed_code}.')
            return redirect('admission_request_detail', req_id=req.pk)
        except Exception as exc:
            messages.error(request, f'Error: {exc}')

    return render(request, 'admissions/assign_bed.html', {
        'req': req, 'available_beds': available_beds,
        'departments': Department.objects.filter(is_active=True), 'ward_types': Ward.WardType.choices,
        'department_f': department, 'ward_type_f': ward_type, 'room_type_f': room_type,
    })


@require_POST
@hms_permission_required('core.request_admission')
def admission_request_cancel(request, req_id):
    req = get_object_or_404(AdmissionRequest, pk=req_id)
    if req.status in (AdmissionRequest.Status.ADMITTED, AdmissionRequest.Status.CANCELLED, AdmissionRequest.Status.REJECTED):
        messages.error(request, 'This request can no longer be cancelled.')
        return redirect('admission_request_detail', req_id=req.pk)
    req.status = AdmissionRequest.Status.CANCELLED
    req.cancelled_at = timezone.now()
    req.cancelled_by = request.user
    req.save()
    log_action(
        request.user, AuditLog.Action.CANCEL, AuditLog.Module.ADMISSION,
        object_type='AdmissionRequest', object_id=req.pk, object_repr=req.patient.full_name,
        description=f'Admission request for {req.patient.full_name} cancelled', request=request,
    )
    messages.warning(request, 'Admission request cancelled.')
    return redirect('admission_request_list')


# ── Discharge approval (doctor sign-off gating bed release) ─────────────────

@require_POST
@hms_permission_required('core.manage_discharge')
def admission_approve_discharge(request, admission_id):
    admission = get_object_or_404(Admission.objects.select_related('patient'), pk=admission_id)
    if admission.status != Admission.Status.ADMITTED:
        messages.error(request, 'This admission is not currently active.')
        return redirect('facility_dashboard')
    admission.discharge_approved_by = request.user
    admission.discharge_approved_at = timezone.now()
    admission.save(update_fields=['discharge_approved_by', 'discharge_approved_at'])
    log_action(
        request.user, AuditLog.Action.APPROVE, AuditLog.Module.ADMISSION,
        object_type='Admission', object_id=admission.pk, object_repr=admission.patient.full_name,
        description=f'Discharge approved for {admission.patient.full_name}', request=request,
    )
    messages.success(request, f'Discharge approved for {admission.patient.full_name}.')
    return _redirect_after_bed_action(request, admission.visit_id)


# ── Deposit Rule configuration ───────────────────────────────────────────────

@hms_permission_required('core.manage_deposit_rules')
def deposit_rule_list(request):
    rules = AdmissionDepositRule.objects.select_related('ward', 'department').order_by('-is_active', '-id')
    return render(request, 'admissions/deposit_rule_list.html', {'rules': rules})


@hms_permission_required('core.manage_deposit_rules')
def deposit_rule_create(request):
    if request.method == 'POST':
        form = AdmissionDepositRuleForm(request.POST)
        if form.is_valid():
            rule = form.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.ADMISSION,
                object_type='AdmissionDepositRule', object_id=rule.pk, object_repr=str(rule),
                description=f'Deposit rule created: {rule}', request=request,
            )
            messages.success(request, 'Deposit rule created.')
            return redirect('deposit_rule_list')
    else:
        form = AdmissionDepositRuleForm()
    return render(request, 'admissions/deposit_rule_form.html', {'form': form, 'action': 'Create'})


@hms_permission_required('core.manage_deposit_rules')
def deposit_rule_edit(request, rule_id):
    rule = get_object_or_404(AdmissionDepositRule, pk=rule_id)
    if request.method == 'POST':
        form = AdmissionDepositRuleForm(request.POST, instance=rule)
        if form.is_valid():
            form.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.ADMISSION,
                object_type='AdmissionDepositRule', object_id=rule.pk, object_repr=str(rule),
                description=f'Deposit rule updated: {rule}', request=request,
            )
            messages.success(request, 'Deposit rule updated.')
            return redirect('deposit_rule_list')
    else:
        form = AdmissionDepositRuleForm(instance=rule)
    return render(request, 'admissions/deposit_rule_form.html', {'form': form, 'rule': rule, 'action': 'Edit'})


# ── Dashboard ─────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_admission_dashboard')
def admission_dashboard(request):
    today = timezone.localdate()

    pending_requests = AdmissionRequest.objects.filter(status=AdmissionRequest.Status.PENDING).count()
    awaiting_deposit = AdmissionRequest.objects.filter(status=AdmissionRequest.Status.AWAITING_DEPOSIT).count()
    awaiting_bed = AdmissionRequest.objects.filter(
        status__in=[AdmissionRequest.Status.AWAITING_BED, AdmissionRequest.Status.DEPOSIT_PAID, AdmissionRequest.Status.CREDIT_APPROVED],
    ).count()

    current_inpatients = Admission.objects.filter(status=Admission.Status.ADMITTED).count()

    total_beds = Bed.objects.count()
    occupied_beds = Bed.objects.filter(status=Bed.Status.OCCUPIED).count()
    bed_occupancy_rate = round((occupied_beds / total_beds) * 100, 1) if total_beds else 0

    total_wards = Ward.objects.count()
    occupied_ward_beds = Bed.objects.filter(status=Bed.Status.OCCUPIED, ward__is_icu_hdu=True).count()
    icu_beds = Bed.objects.filter(ward__is_icu_hdu=True).count()
    icu_occupancy_rate = round((occupied_ward_beds / icu_beds) * 100, 1) if icu_beds else 0

    emergency_admissions_today = AdmissionRequest.objects.filter(
        request_source=AdmissionRequest.Source.EMERGENCY, created_at__date=today,
    ).count()
    todays_admissions = Admission.objects.filter(admitted_at__date=today).count()
    todays_discharges = Admission.objects.filter(status=Admission.Status.DISCHARGED, discharged_at__date=today).count()
    recent_transfers = Admission.objects.filter(status=Admission.Status.TRANSFERRED).select_related(
        'patient', 'bed__room__ward',
    ).order_by('-discharged_at')[:8]

    recent_requests = AdmissionRequest.objects.select_related('patient', 'admitting_department').exclude(
        status__in=[AdmissionRequest.Status.ADMITTED, AdmissionRequest.Status.REJECTED, AdmissionRequest.Status.CANCELLED],
    ).order_by('-created_at')[:10]

    return render(request, 'admissions/dashboard.html', {
        'pending_requests': pending_requests, 'awaiting_deposit': awaiting_deposit, 'awaiting_bed': awaiting_bed,
        'current_inpatients': current_inpatients,
        'total_beds': total_beds, 'occupied_beds': occupied_beds, 'bed_occupancy_rate': bed_occupancy_rate,
        'total_wards': total_wards, 'icu_occupancy_rate': icu_occupancy_rate,
        'emergency_admissions_today': emergency_admissions_today,
        'todays_admissions': todays_admissions, 'todays_discharges': todays_discharges,
        'recent_transfers': recent_transfers, 'recent_requests': recent_requests,
    })
