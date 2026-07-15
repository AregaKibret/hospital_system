from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import (
    AuditLog, CashSession, Invoice, InvoiceItem, InvoiceItemRefund,
    Patient, Payment, PaymentAllocation, ServiceChargeSettings, Visit,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def invoice_status_from_items(invoice):
    """Derive the invoice's overall status from its items' individual
    payment_status — items are the single source of truth (see signals.py).

    Fully Paid   → every non-cancelled/non-refunded item is Paid or Credit Approved.
    Partial Paid → at least one item is paid/credit/partial, but not all.
    Pending      → no item has any payment progress yet.
    """
    ItemPS = InvoiceItem.PaymentStatus
    statuses = list(invoice.items.values_list('payment_status', flat=True))
    if not statuses:
        return invoice.status

    relevant = [s for s in statuses if s not in (ItemPS.CANCELLED, ItemPS.REFUNDED)]
    if not relevant:
        return Invoice.Status.CANCELLED

    cleared = {ItemPS.PAID, ItemPS.CREDIT}
    if all(s in cleared for s in relevant):
        return Invoice.Status.PAID
    if any(s in cleared or s == ItemPS.PARTIAL for s in relevant):
        return Invoice.Status.PARTIAL
    if invoice.payment_type == Invoice.PaymentType.CREDIT and invoice.status == Invoice.Status.CREDIT_PENDING:
        return Invoice.Status.CREDIT_PENDING
    return Invoice.Status.ISSUED


def _recalc_invoice(invoice):
    """Refresh paid_amount and set correct status. Call inside atomic block."""
    total_payments = invoice.payments.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    total_refunds = InvoiceItemRefund.objects.filter(
        invoice_item__invoice=invoice,
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    invoice.paid_amount = total_payments - total_refunds
    if invoice.status != Invoice.Status.CANCELLED:
        invoice.status = invoice_status_from_items(invoice)
    invoice.save()


def _cascade_status_to_items(invoice, item_status, *, only_unsettled=True, user=None, reason=''):
    """Push a whole-invoice status change (Waive, whole-invoice credit
    approval, Cancel) down to every item so department gating — which reads
    item.payment_status, not invoice.status — stays consistent."""
    qs = invoice.items.all()
    if only_unsettled:
        qs = qs.exclude(payment_status__in=[InvoiceItem.PaymentStatus.PAID, InvoiceItem.PaymentStatus.CANCELLED,
                                             InvoiceItem.PaymentStatus.REFUNDED])
    for item in qs:
        item.payment_status = item_status
        if item_status == InvoiceItem.PaymentStatus.CREDIT and user:
            item.credit_approved_by = user
            item.credit_approved_at = timezone.now()
            item.credit_reason = reason
        if item_status == InvoiceItem.PaymentStatus.CANCELLED and user:
            item.cancelled_by = user
            item.cancelled_at = timezone.now()
            item.cancel_reason = reason
        item.save()


# ── Billing Dashboard ─────────────────────────────────────────────────────────

@hms_permission_required('core.read_billing')
def billing_dashboard(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)

    todays_invoices_count = Invoice.objects.filter(created_at__date=today).count()

    collected_today = (
        Payment.objects.filter(payment_date=today)
        .aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    )
    cash_today = (
        Payment.objects.filter(payment_date=today, payment_method=Payment.Method.CASH)
        .aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    )

    outstanding_qs = Invoice.objects.filter(
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.CREDIT_PENDING]
    )
    outstanding_total = sum(inv.balance for inv in outstanding_qs)
    credit_count = Invoice.objects.filter(
        status=Invoice.Status.CREDIT_PENDING
    ).count()
    overdue_count = Invoice.objects.filter(
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.CREDIT_PENDING],
        due_date__lt=today,
    ).count()

    monthly_total = (
        Invoice.objects.filter(created_at__date__gte=month_start)
        .aggregate(t=Sum('total_amount'))['t'] or Decimal('0.00')
    )

    recent_invoices = (
        Invoice.objects.select_related('patient', 'created_by')
        .filter(created_at__date__gte=month_start)
        .order_by('-created_at')[:10]
    )

    open_session = None
    if request.user.has_perm('core.manage_cash_sessions'):
        open_session = CashSession.objects.filter(
            cashier=request.user, date=today, status=CashSession.Status.OPEN
        ).first()

    return render(request, 'billing/dashboard.html', {
        'today': today,
        'todays_invoices_count': todays_invoices_count,
        'collected_today': collected_today,
        'cash_today': cash_today,
        'outstanding_total': outstanding_total,
        'credit_count': credit_count,
        'overdue_count': overdue_count,
        'monthly_total': monthly_total,
        'recent_invoices': recent_invoices,
        'open_session': open_session,
    })


# ── Invoice List ──────────────────────────────────────────────────────────────

@hms_permission_required('core.read_billing')
def invoice_list(request):
    search = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all')
    type_filter = request.GET.get('type', 'all')

    qs = Invoice.objects.select_related('patient', 'created_by').order_by('-created_at')

    if search:
        qs = qs.filter(
            Q(invoice_number__icontains=search)
            | Q(patient__first_name__icontains=search)
            | Q(patient__last_name__icontains=search)
            | Q(patient__card_number__icontains=search)
        )
    if status_filter != 'all':
        qs = qs.filter(status=status_filter)
    if type_filter != 'all':
        qs = qs.filter(payment_type=type_filter)

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'billing/invoice_list.html', {
        'page_obj': page_obj,
        'search': search,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'status_choices': Invoice.Status.choices,
        'type_choices': Invoice.PaymentType.choices,
    })


# ── Invoice Create ────────────────────────────────────────────────────────────

@hms_permission_required('core.create_invoice')
def invoice_create(request):
    if request.method == 'POST':
        data = request.POST
        try:
            patient = get_object_or_404(Patient, pk=data.get('patient_id'))
            visit = None
            if data.get('visit_id'):
                visit = get_object_or_404(Visit, pk=data['visit_id'], patient=patient)

            payment_type = data.get('payment_type', Invoice.PaymentType.CASH)
            credit_reason = data.get('credit_reason', '').strip()
            due_date_val = data.get('due_date') or None

            status = Invoice.Status.DRAFT
            if payment_type == Invoice.PaymentType.CREDIT:
                status = Invoice.Status.CREDIT_PENDING

            invoice = Invoice.objects.create(
                patient=patient,
                visit=visit,
                created_by=request.user,
                notes=data.get('notes', '').strip(),
                due_date=due_date_val,
                payment_type=payment_type,
                credit_reason=credit_reason,
                status=status,
            )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.BILLING,
                object_type='Invoice', object_id=invoice.pk, object_repr=invoice.invoice_number,
                description=f'Invoice {invoice.invoice_number} created for {patient.full_name} ({payment_type})',
                extra_data={'patient': patient.full_name, 'payment_type': payment_type,
                            'total': str(invoice.total_amount)},
                request=request,
            )
            messages.success(request, f'Invoice {invoice.invoice_number} created.')
            return redirect('invoice_detail', invoice_id=invoice.pk)
        except Exception as exc:
            messages.error(request, f'Error creating invoice: {exc}')

    patient_search = request.GET.get('patient_q', '').strip()
    patients = []
    if patient_search:
        patients = Patient.objects.filter(
            Q(first_name__icontains=patient_search)
            | Q(last_name__icontains=patient_search)
            | Q(card_number__icontains=patient_search)
        )[:10]

    selected_patient, visits = None, []
    if request.GET.get('patient_id'):
        try:
            selected_patient = Patient.objects.get(pk=request.GET['patient_id'])
            visits = Visit.objects.filter(patient=selected_patient).order_by('-created_at')[:10]
        except Patient.DoesNotExist:
            pass

    return render(request, 'billing/invoice_create.html', {
        'patients': patients,
        'patient_search': patient_search,
        'selected_patient': selected_patient,
        'visits': visits,
        'payment_type_choices': Invoice.PaymentType.choices,
        'today': timezone.localdate(),
    })


# ── Invoice Detail ────────────────────────────────────────────────────────────

@hms_permission_required('core.read_billing')
def invoice_detail(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related('patient', 'visit', 'created_by', 'credit_approved_by'),
        pk=invoice_id,
    )
    items = invoice.items.order_by('pk')
    payments = (
        invoice.payments.select_related('received_by')
        .prefetch_related('allocations__invoice_item')
        .order_by('payment_date', 'created_at')
    )

    return render(request, 'billing/invoice_detail.html', {
        'invoice': invoice,
        'items': items,
        'payments': payments,
        'can_edit':   request.user.has_perm('core.create_invoice'),
        'can_manage': request.user.has_perm('core.manage_billing'),
        'can_pay':    request.user.has_perm('core.process_payment'),
        'can_approve_credit': request.user.has_perm('core.approve_credit_invoice'),
        'service_types': InvoiceItem.ServiceType.choices,
        'today': timezone.localdate(),
    })


# ── Invoice Add / Delete Item ─────────────────────────────────────────────────

@hms_permission_required('core.create_invoice')
@require_POST
def invoice_add_item(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    if invoice.status in [Invoice.Status.CANCELLED, Invoice.Status.PAID, Invoice.Status.WAIVED]:
        messages.error(request, 'Cannot add items to a cancelled, paid, or waived invoice.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    try:
        description = request.POST.get('description', '').strip()
        if not description:
            messages.error(request, 'Description is required.')
            return redirect('invoice_detail', invoice_id=invoice.pk)

        with transaction.atomic():
            InvoiceItem.objects.create(
                invoice=invoice,
                description=description,
                service_type=request.POST.get('service_type', InvoiceItem.ServiceType.OTHER),
                quantity=Decimal(request.POST.get('quantity', '1')),
                unit_price=Decimal(request.POST.get('unit_price', '0')),
            )
            invoice.total_amount = (
                invoice.items.aggregate(t=Sum('total'))['t'] or Decimal('0.00')
            )
            invoice.save()
        messages.success(request, f'Item "{description}" added.')
    except Exception as exc:
        messages.error(request, f'Error adding item: {exc}')

    return redirect('invoice_detail', invoice_id=invoice.pk)


@hms_permission_required('core.create_invoice')
@require_POST
def invoice_delete_item(request, item_id):
    item = get_object_or_404(InvoiceItem, pk=item_id)
    invoice = item.invoice
    if invoice.status in [Invoice.Status.CANCELLED, Invoice.Status.PAID, Invoice.Status.WAIVED]:
        messages.error(request, 'Cannot remove items from a cancelled, paid, or waived invoice.')
        return redirect('invoice_detail', invoice_id=invoice.pk)
    if not item.is_payable:
        messages.error(request, 'Cannot remove an item that has already been paid, credited, cancelled, or refunded — use a refund instead.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    with transaction.atomic():
        item.delete()
        invoice.total_amount = (
            invoice.items.aggregate(t=Sum('total'))['t'] or Decimal('0.00')
        )
        invoice.save()
    messages.success(request, 'Item removed.')
    return redirect('invoice_detail', invoice_id=invoice.pk)


# ── Item-Level Credit Approval ─────────────────────────────────────────────────

@hms_permission_required('core.approve_credit_invoice')
@require_POST
def invoice_item_credit_approve(request, item_id):
    """Approve credit for a single billable item, independent of the rest
    of the invoice — e.g. the CBC test is credit-approved while the X-ray
    is still pending cash payment."""
    item = get_object_or_404(InvoiceItem.objects.select_related('invoice'), pk=item_id)
    invoice = item.invoice
    reason = request.POST.get('reason', '').strip()

    if not item.is_payable:
        messages.error(request, f'"{item.description}" is already settled and cannot be credit-approved.')
        return redirect('invoice_detail', invoice_id=invoice.pk)
    if not reason:
        messages.error(request, 'A credit reason is required.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    reference = f'CR-{item.pk}-{int(timezone.now().timestamp())}'
    with transaction.atomic():
        item.payment_status = InvoiceItem.PaymentStatus.CREDIT
        item.credit_approved_by = request.user
        item.credit_approved_at = timezone.now()
        item.credit_reason = reason
        item.save()
        if invoice.payment_type != Invoice.PaymentType.CREDIT:
            invoice.payment_type = Invoice.PaymentType.CREDIT
        _recalc_invoice(invoice)

    log_action(
        request.user, AuditLog.Action.APPROVE, AuditLog.Module.BILLING,
        object_type='InvoiceItem', object_id=item.pk, object_repr=item.description,
        description=f'Credit approved for "{item.description}" (ETB {item.total}) on invoice {invoice.invoice_number}. Reason: {reason}. Ref: {reference}',
        extra_data={
            'invoice': invoice.invoice_number, 'patient': invoice.patient.full_name,
            'amount': str(item.total), 'reference': reference,
        },
        request=request,
    )
    messages.success(request, f'Credit approved for "{item.description}".')
    return redirect('invoice_detail', invoice_id=invoice.pk)


# ── Item-Level Cancel ──────────────────────────────────────────────────────────

@hms_permission_required('core.manage_billing')
@require_POST
def invoice_item_cancel(request, item_id):
    """Cancel a single not-yet-paid billable item. The item stays on the
    invoice for the audit trail but is excluded from the balance owed and
    will never unlock its owning department order."""
    item = get_object_or_404(InvoiceItem.objects.select_related('invoice'), pk=item_id)
    invoice = item.invoice
    reason = request.POST.get('reason', '').strip()

    if item.payment_status == InvoiceItem.PaymentStatus.PAID:
        messages.error(request, f'"{item.description}" has already been paid — refund it instead of cancelling.')
        return redirect('invoice_detail', invoice_id=invoice.pk)
    if item.payment_status in (InvoiceItem.PaymentStatus.CANCELLED, InvoiceItem.PaymentStatus.REFUNDED):
        messages.warning(request, f'"{item.description}" is already {item.payment_status.lower()}.')
        return redirect('invoice_detail', invoice_id=invoice.pk)
    if not reason:
        messages.error(request, 'A cancellation reason is required.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    reference = f'CNL-{item.pk}-{int(timezone.now().timestamp())}'
    with transaction.atomic():
        item.payment_status = InvoiceItem.PaymentStatus.CANCELLED
        item.cancelled_by = request.user
        item.cancelled_at = timezone.now()
        item.cancel_reason = reason
        item.save()
        _recalc_invoice(invoice)

    log_action(
        request.user, AuditLog.Action.CANCEL, AuditLog.Module.BILLING,
        object_type='InvoiceItem', object_id=item.pk, object_repr=item.description,
        description=f'Cancelled item "{item.description}" (ETB {item.total}) on invoice {invoice.invoice_number}. Reason: {reason}. Ref: {reference}',
        extra_data={
            'invoice': invoice.invoice_number, 'patient': invoice.patient.full_name,
            'amount': str(item.total), 'reference': reference,
        },
        request=request,
    )
    messages.success(request, f'"{item.description}" cancelled.')
    return redirect('invoice_detail', invoice_id=invoice.pk)


# ── Item-Level Refund ──────────────────────────────────────────────────────────

@hms_permission_required('core.manage_billing')
@require_POST
def invoice_item_refund(request, item_id):
    """Refund all or part of what has been paid on a single item."""
    item = get_object_or_404(InvoiceItem.objects.select_related('invoice'), pk=item_id)
    invoice = item.invoice
    reason = request.POST.get('reason', '').strip()

    if item.paid_amount <= 0:
        messages.error(request, f'"{item.description}" has no payment recorded to refund.')
        return redirect('invoice_detail', invoice_id=invoice.pk)
    if not reason:
        messages.error(request, 'A refund reason is required.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    try:
        amount = Decimal(request.POST.get('amount', str(item.paid_amount)))
    except Exception:
        messages.error(request, 'Invalid refund amount.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    if amount <= 0 or amount > item.paid_amount:
        messages.error(request, f'Refund amount must be between 0 and ETB {item.paid_amount:,.2f}.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    with transaction.atomic():
        refund = InvoiceItemRefund.objects.create(
            invoice_item=item, amount=amount, reason=reason,
            reference_number=f'RFD-{item.pk}-{int(timezone.now().timestamp())}',
            refunded_by=request.user,
        )
        item.paid_amount = item.paid_amount - amount
        if item.paid_amount <= 0:
            item.paid_amount = Decimal('0.00')
            item.payment_status = InvoiceItem.PaymentStatus.REFUNDED
        else:
            item.payment_status = InvoiceItem.PaymentStatus.PARTIAL
        item.save()
        _recalc_invoice(invoice)

    log_action(
        request.user, AuditLog.Action.REFUND, AuditLog.Module.BILLING,
        object_type='InvoiceItem', object_id=item.pk, object_repr=item.description,
        description=f'Refunded ETB {amount:,.2f} for "{item.description}" on invoice {invoice.invoice_number}. Reason: {reason}',
        extra_data={
            'invoice': invoice.invoice_number, 'patient': invoice.patient.full_name,
            'amount': str(amount), 'reference': refund.reference_number,
        },
        request=request,
    )
    messages.success(request, f'Refunded ETB {amount:,.2f} for "{item.description}".')
    return redirect('invoice_detail', invoice_id=invoice.pk)


# ── Invoice Status Update ─────────────────────────────────────────────────────

@hms_permission_required('core.manage_billing')
@require_POST
def invoice_update_status(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    new_status = request.POST.get('status', '').strip()
    valid = [s for s, _ in Invoice.Status.choices]

    if new_status not in valid:
        messages.error(request, 'Invalid status.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    if invoice.status == Invoice.Status.PAID and new_status != Invoice.Status.CANCELLED:
        messages.warning(request, 'Invoice is already paid.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    old_status = invoice.status
    invoice.status = new_status

    with transaction.atomic():
        invoice.save()
        # A whole-invoice Waive/Cancel must cascade to every still-unsettled
        # item — department gating reads item.payment_status, not invoice.status.
        if new_status == Invoice.Status.WAIVED:
            _cascade_status_to_items(invoice, InvoiceItem.PaymentStatus.CREDIT, user=request.user,
                                      reason='Invoice waived.')
        elif new_status == Invoice.Status.CANCELLED:
            _cascade_status_to_items(invoice, InvoiceItem.PaymentStatus.CANCELLED, user=request.user,
                                      reason='Invoice cancelled.')

    action_map = {
        Invoice.Status.ISSUED:    AuditLog.Action.ISSUE,
        Invoice.Status.CANCELLED: AuditLog.Action.CANCEL,
        Invoice.Status.WAIVED:    AuditLog.Action.WAIVE,
    }
    audit_action = action_map.get(new_status, AuditLog.Action.UPDATE)
    log_action(
        request.user, audit_action, AuditLog.Module.BILLING,
        object_type='Invoice', object_id=invoice.pk, object_repr=invoice.invoice_number,
        description=f'Invoice {invoice.invoice_number} status changed: {old_status} → {new_status}',
        changes={'status': {'old': old_status, 'new': new_status}},
        extra_data={
            'patient': invoice.patient.full_name, 'total': str(invoice.total_amount),
            'reference': invoice.invoice_number,
        },
        request=request,
    )
    messages.success(request, f'Status updated to "{new_status}".')
    return redirect('invoice_detail', invoice_id=invoice.pk)


# ── Cash Payment ──────────────────────────────────────────────────────────────

@hms_permission_required('core.process_payment')
def payment_create(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related('patient', 'visit'), pk=invoice_id
    )

    if invoice.status in [Invoice.Status.CANCELLED, Invoice.Status.WAIVED, Invoice.Status.PAID]:
        messages.error(request, f'Cannot record payment for a {invoice.status.lower()} invoice.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    if request.method == 'POST':
        data = request.POST
        try:
            item_ids = data.getlist('item_ids')
            if not item_ids:
                raise ValueError('Select at least one item to pay for.')

            selected_items = list(
                invoice.items.filter(pk__in=item_ids).order_by('pk')
            )
            if len(selected_items) != len(set(item_ids)):
                raise ValueError('One or more selected items could not be found on this invoice.')
            not_payable = [i for i in selected_items if not i.is_payable]
            if not_payable:
                names = ', '.join(i.description for i in not_payable)
                raise ValueError(f'These items are already settled and cannot be paid again: {names}.')

            selected_balance = sum(i.balance for i in selected_items)

            # ── Service charge (server-authoritative — never trust the client) ──
            sc_settings = ServiceChargeSettings.get_solo()
            sc_item_ids = set()
            if sc_settings.enabled:
                if sc_settings.allow_cashier_override:
                    sc_item_ids = {int(pk) for pk in data.getlist('service_charge_item_ids') if pk.isdigit()}
                else:
                    sc_item_ids = {
                        i.pk for i in selected_items
                        if sc_settings.default_checked_for(i.service_type)
                    }
            sc_rate = sc_settings.percentage / Decimal('100') if sc_settings.enabled else Decimal('0')
            sc_items = [i for i in selected_items if i.pk in sc_item_ids]
            service_charge_amount = (
                (sum(i.balance for i in sc_items) * sc_rate).quantize(Decimal('0.01'))
                if sc_items else Decimal('0.00')
            )

            amount = Decimal(data.get('amount', '0'))
            if amount <= 0:
                raise ValueError('Payment amount must be greater than zero.')
            grand_total = selected_balance + service_charge_amount
            if amount > grand_total:
                raise ValueError(
                    f'Amount (ETB {amount:,.2f}) exceeds the total of selected items plus '
                    f'service charge (ETB {grand_total:,.2f}).'
                )

            method = data.get('payment_method', Payment.Method.CASH)
            cash_received = None
            change_given = None

            if method == Payment.Method.CASH:
                raw_cash = data.get('cash_received', '').strip()
                if raw_cash:
                    cash_received = Decimal(raw_cash)
                    if cash_received < amount:
                        raise ValueError(f'Cash received (ETB {cash_received:,.2f}) is less than amount due (ETB {amount:,.2f}).')
                    change_given = max(Decimal('0.00'), cash_received - amount)

            with transaction.atomic():
                # Auto-issue Draft invoices on first payment
                if invoice.status == Invoice.Status.DRAFT:
                    invoice.status = Invoice.Status.ISSUED
                    invoice.save(update_fields=['status'])
                    log_action(
                        request.user, AuditLog.Action.ISSUE, AuditLog.Module.BILLING,
                        object_type='Invoice', object_id=invoice.pk,
                        object_repr=invoice.invoice_number,
                        description=f'Invoice {invoice.invoice_number} auto-issued on payment',
                        request=request,
                    )

                payment = Payment.objects.create(
                    invoice=invoice,
                    amount=amount,
                    payment_method=method,
                    reference_number=data.get('reference_number', '').strip(),
                    cash_received=cash_received,
                    change_given=change_given,
                    received_by=request.user,
                    payment_date=data.get('payment_date') or timezone.localdate(),
                    notes=data.get('notes', '').strip(),
                )

                # Allocate the payment across selected items, in order, until
                # the amount runs out — each item is settled fully before
                # moving to the next, and any item not fully covered is left
                # Partially Paid rather than guessed at proportionally.
                remaining = amount
                paid_items_desc = []
                for item in selected_items:
                    if remaining <= 0:
                        break
                    item_balance = item.balance
                    if item_balance <= 0:
                        continue
                    pay_now = min(remaining, item_balance)
                    item.paid_amount = item.paid_amount + pay_now
                    item.payment_status = (
                        InvoiceItem.PaymentStatus.PAID if item.paid_amount >= item.total
                        else InvoiceItem.PaymentStatus.PARTIAL
                    )
                    item.save()
                    PaymentAllocation.objects.create(payment=payment, invoice_item=item, amount=pay_now)
                    paid_items_desc.append(f'{item.description} (ETB {pay_now:,.2f})')
                    remaining -= pay_now

                # Whatever is left after fully settling the selected items
                # (up to the service charge owed) becomes a dedicated,
                # separately-tracked "Service Charge" invoice item — same
                # partial-payment machinery as any other item.
                sc_item = None
                sc_paid_now = Decimal('0.00')
                if service_charge_amount > 0 and remaining > 0:
                    sc_paid_now = min(remaining, service_charge_amount)
                    sc_names = ', '.join(i.description for i in sc_items)
                    sc_item = InvoiceItem.objects.create(
                        invoice=invoice,
                        description=f'{sc_settings.charge_name} ({sc_settings.percentage}%) — {sc_names}',
                        service_type=InvoiceItem.ServiceType.SERVICE_CHARGE,
                        quantity=1,
                        unit_price=service_charge_amount,
                        paid_amount=sc_paid_now,
                        payment_status=(
                            InvoiceItem.PaymentStatus.PAID if sc_paid_now >= service_charge_amount
                            else InvoiceItem.PaymentStatus.PARTIAL
                        ),
                    )
                    invoice.total_amount = invoice.total_amount + service_charge_amount
                    invoice.save(update_fields=['total_amount'])
                    PaymentAllocation.objects.create(payment=payment, invoice_item=sc_item, amount=sc_paid_now)
                    remaining -= sc_paid_now

                _recalc_invoice(invoice)

            log_action(
                request.user, AuditLog.Action.PAYMENT, AuditLog.Module.PAYMENT,
                object_type='Payment', object_id=payment.pk,
                object_repr=f'{invoice.invoice_number} / {payment.receipt_number}',
                description=(
                    f'Payment ETB {amount:,.2f} ({method}) for invoice {invoice.invoice_number} '
                    f'— patient {invoice.patient.full_name} — items: {"; ".join(paid_items_desc)}'
                    + (f' — service charge ETB {sc_paid_now:,.2f} of ETB {service_charge_amount:,.2f}'
                       f' ({sc_settings.percentage}%) applied to: {sc_names}' if sc_item else '')
                ),
                extra_data={
                    'invoice': invoice.invoice_number,
                    'patient': invoice.patient.full_name,
                    'amount': str(amount),
                    'method': method,
                    'receipt': payment.receipt_number,
                    'cash_received': str(cash_received) if cash_received else None,
                    'change_given': str(change_given) if change_given else None,
                    'items_paid': paid_items_desc,
                    'service_charge_percentage': str(sc_settings.percentage) if sc_item else None,
                    'service_charge_amount': str(service_charge_amount) if sc_item else None,
                    'service_charge_collected': str(sc_paid_now) if sc_item else None,
                    'service_charge_items': [i.description for i in sc_items] if sc_item else [],
                },
                request=request,
            )

            # Advance patient flow status when invoice is fully paid
            invoice.refresh_from_db()
            if invoice.status == Invoice.Status.PAID and invoice.visit_id:
                try:
                    from .patient_flow import advance_to_payment_completed
                    advance_to_payment_completed(invoice.visit, performed_by=request.user, invoice=invoice)
                except Exception:
                    pass
                # Notify pharmacy: mark linked prescriptions as PAID
                try:
                    from .models import Prescription as Rx
                    Rx.objects.filter(
                        invoice=invoice,
                        billing_status=Rx.BillingStatus.PENDING_PAYMENT,
                    ).update(billing_status=Rx.BillingStatus.PAID)
                except Exception:
                    pass

            try:
                from .notifications import notify_payment_completed
                notify_payment_completed(invoice, sender_user=request.user)
            except Exception:
                pass

            # Support ?return_to=split to go back to split view after payment
            if request.GET.get('return_to') == 'split':
                from django.urls import reverse as _reverse
                return redirect(_reverse('invoice_split_view') + f'?highlight={invoice.pk}')
            return redirect('invoice_receipt', invoice_id=invoice.pk, payment_id=payment.pk)

        except Exception as exc:
            messages.error(request, f'Error processing payment: {exc}')

    return_to = request.GET.get('return_to', '')
    items = list(invoice.items.order_by('pk'))
    sc_settings = ServiceChargeSettings.get_solo()
    for item in items:
        item.sc_default_checked = sc_settings.default_checked_for(item.service_type)
    prior_payments = invoice.payments.select_related('received_by').prefetch_related('allocations__invoice_item').order_by('created_at')
    return render(request, 'billing/payment_form.html', {
        'invoice': invoice,
        'items': items,
        'prior_payments': prior_payments,
        'payment_methods': Payment.Method.choices,
        'today': timezone.localdate(),
        'return_to': return_to,
        'sc_settings': sc_settings,
    })


# ── Payment Receipt ───────────────────────────────────────────────────────────

@hms_permission_required('core.read_billing')
def invoice_receipt(request, invoice_id, payment_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related('patient', 'visit', 'created_by'), pk=invoice_id
    )
    payment = get_object_or_404(
        invoice.payments.select_related('received_by').prefetch_related('allocations__invoice_item'),
        pk=payment_id,
    )
    items = invoice.items.order_by('pk')
    all_payments = invoice.payments.select_related('received_by').order_by('created_at')
    payment_allocations = payment.allocations.all()
    return render(request, 'billing/receipt.html', {
        'invoice': invoice,
        'payment': payment,
        'items': items,
        'all_payments': all_payments,
        'payment_allocations': payment_allocations,
        'now': timezone.now(),
    })


# ── Apply Discount ────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_billing')
@require_POST
def invoice_apply_discount(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    if invoice.status in [Invoice.Status.PAID, Invoice.Status.CANCELLED]:
        messages.error(request, 'Cannot apply discount to a paid or cancelled invoice.')
        return redirect('invoice_detail', invoice_id=invoice.pk)
    try:
        discount = Decimal(request.POST.get('discount', '0'))
        if discount < 0:
            raise ValueError('Discount cannot be negative.')
        if discount > invoice.total_amount:
            raise ValueError('Discount cannot exceed the invoice total.')
        reason = request.POST.get('discount_reason', '').strip()
        old_discount = invoice.discount
        invoice.discount = discount
        invoice.save(update_fields=['discount'])
        _recalc_invoice(invoice)
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.BILLING,
            object_type='Invoice', object_id=invoice.pk,
            object_repr=invoice.invoice_number,
            description=f'Discount updated: ETB {old_discount} → ETB {discount}. Reason: {reason or "not specified"}',
            changes={'discount': {'old': str(old_discount), 'new': str(discount)}},
            request=request,
        )
        messages.success(request, f'Discount of ETB {discount:,.2f} applied.')
    except Exception as exc:
        messages.error(request, f'Error applying discount: {exc}')
    return redirect('invoice_detail', invoice_id=invoice.pk)


# ── Credit Invoice ────────────────────────────────────────────────────────────

@hms_permission_required('core.create_invoice')
def credit_invoice_create(request, invoice_id):
    invoice = get_object_or_404(Invoice.objects.select_related('patient'), pk=invoice_id)

    if invoice.status not in [Invoice.Status.DRAFT, Invoice.Status.ISSUED]:
        messages.error(request, 'Only Draft or Issued invoices can be converted to credit.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    if request.method == 'POST':
        data = request.POST
        due_date = data.get('due_date') or None
        reason = data.get('credit_reason', '').strip()

        if not reason:
            messages.error(request, 'Credit reason is required.')
            return render(request, 'billing/credit_form.html', {
                'invoice': invoice, 'today': timezone.localdate(), 'post': data,
            })

        invoice.payment_type = Invoice.PaymentType.CREDIT
        invoice.credit_reason = reason
        invoice.due_date = due_date
        invoice.status = Invoice.Status.CREDIT_PENDING
        invoice.save()
        log_action(
            request.user, AuditLog.Action.CREATE, AuditLog.Module.BILLING,
            object_type='Invoice', object_id=invoice.pk, object_repr=invoice.invoice_number,
            description=f'Invoice {invoice.invoice_number} converted to credit — {invoice.patient.full_name}',
            extra_data={'reason': reason, 'due_date': str(due_date), 'total': str(invoice.total_amount)},
            request=request,
        )
        messages.success(request, f'Invoice {invoice.invoice_number} marked as Credit Pending.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    return render(request, 'billing/credit_form.html', {
        'invoice': invoice, 'today': timezone.localdate(),
    })


@hms_permission_required('core.approve_credit_invoice')
@require_POST
def credit_approve(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)

    if invoice.status != Invoice.Status.CREDIT_PENDING:
        messages.error(request, 'Only Credit Pending invoices can be approved.')
        return redirect('invoice_detail', invoice_id=invoice.pk)

    with transaction.atomic():
        invoice.credit_approved_by = request.user
        invoice.credit_approved_at = timezone.now()
        invoice.save()
        # Approving credit for the whole invoice clears whatever is still
        # unpaid — items already settled by cash stay Paid.
        _cascade_status_to_items(
            invoice, InvoiceItem.PaymentStatus.CREDIT, user=request.user,
            reason=invoice.credit_reason or 'Whole-invoice credit approved.',
        )
        _recalc_invoice(invoice)

    log_action(
        request.user, AuditLog.Action.APPROVE, AuditLog.Module.BILLING,
        object_type='Invoice', object_id=invoice.pk, object_repr=invoice.invoice_number,
        description=f'Credit invoice {invoice.invoice_number} approved — {invoice.patient.full_name}',
        extra_data={
            'patient': invoice.patient.full_name, 'total': str(invoice.total_amount),
            'reference': invoice.invoice_number,
        },
        request=request,
    )
    messages.success(request, f'Credit invoice {invoice.invoice_number} approved.')
    return redirect('invoice_detail', invoice_id=invoice.pk)


@hms_permission_required('core.read_billing')
def credit_list(request):
    search = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'credit')
    today = timezone.localdate()

    qs = Invoice.objects.filter(
        payment_type=Invoice.PaymentType.CREDIT
    ).select_related('patient', 'created_by', 'credit_approved_by').order_by('-created_at')

    if status_filter == 'credit':
        qs = qs.filter(status=Invoice.Status.CREDIT_PENDING)
    elif status_filter == 'partial':
        qs = qs.filter(status=Invoice.Status.PARTIAL)
    elif status_filter == 'overdue':
        qs = qs.filter(
            status__in=[Invoice.Status.CREDIT_PENDING, Invoice.Status.PARTIAL],
            due_date__lt=today,
        )
    elif status_filter != 'all':
        qs = qs.filter(status=status_filter)

    if search:
        qs = qs.filter(
            Q(invoice_number__icontains=search)
            | Q(patient__first_name__icontains=search)
            | Q(patient__last_name__icontains=search)
            | Q(patient__card_number__icontains=search)
        )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'billing/credit_list.html', {
        'page_obj': page_obj,
        'search': search,
        'status_filter': status_filter,
        'today': today,
    })


# ── Cash Session Management ───────────────────────────────────────────────────

@hms_permission_required('core.manage_cash_sessions')
def cash_session_list(request):
    today = timezone.localdate()
    qs = CashSession.objects.select_related('cashier').order_by('-date', '-opened_at')

    date_from = request.GET.get('date_from', '')
    date_to   = request.GET.get('date_to', '')
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)

    my_open = CashSession.objects.filter(
        cashier=request.user, date=today, status=CashSession.Status.OPEN
    ).first()

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'billing/cash_session_list.html', {
        'page_obj': page_obj,
        'my_open': my_open,
        'today': today,
        'date_from': date_from,
        'date_to': date_to,
    })


@hms_permission_required('core.manage_cash_sessions')
def cash_session_open(request):
    today = timezone.localdate()
    existing = CashSession.objects.filter(
        cashier=request.user, date=today, status=CashSession.Status.OPEN
    ).first()
    if existing:
        messages.warning(request, 'You already have an open session for today.')
        return redirect('cash_session_list')

    if request.method == 'POST':
        opening_balance = Decimal(request.POST.get('opening_balance', '0') or '0')
        session = CashSession.objects.create(
            cashier=request.user,
            date=today,
            opening_balance=opening_balance,
            status=CashSession.Status.OPEN,
            notes=request.POST.get('notes', '').strip(),
        )
        log_action(
            request.user, AuditLog.Action.OPEN_SESSION, AuditLog.Module.FINANCE,
            object_type='CashSession', object_id=session.pk,
            object_repr=f'Session {today}',
            description=f'Cash session opened — opening balance ETB {opening_balance:,.2f}',
            extra_data={'opening_balance': str(opening_balance), 'date': str(today)},
            request=request,
        )
        messages.success(request, f'Cash session opened. Opening balance: ETB {opening_balance:,.2f}')
        return redirect('cash_session_list')

    return render(request, 'billing/cash_session_open.html', {'today': today})


@hms_permission_required('core.manage_cash_sessions')
def cash_session_close(request, session_id):
    session = get_object_or_404(CashSession, pk=session_id, cashier=request.user)
    if session.status != CashSession.Status.OPEN:
        messages.error(request, 'This session is already closed.')
        return redirect('cash_session_list')

    total_collected = session.total_collected
    expected = session.opening_balance + total_collected

    if request.method == 'POST':
        closing_balance = Decimal(request.POST.get('closing_balance', '0') or '0')
        discrepancy = closing_balance - expected

        session.closing_balance = closing_balance
        session.expected_closing = expected
        session.discrepancy = discrepancy
        session.status = CashSession.Status.CLOSED
        session.closed_at = timezone.now()
        session.notes = (session.notes + '\n' + request.POST.get('notes', '')).strip()
        session.save()

        sev = AuditLog.Severity.WARNING if abs(discrepancy) > Decimal('0.01') else AuditLog.Severity.INFO
        log_action(
            request.user, AuditLog.Action.CLOSE_SESSION, AuditLog.Module.FINANCE,
            object_type='CashSession', object_id=session.pk,
            object_repr=f'Session {session.date}',
            description=f'Cash session closed — collected ETB {total_collected:,.2f}, discrepancy ETB {discrepancy:,.2f}',
            extra_data={
                'total_collected': str(total_collected),
                'opening_balance': str(session.opening_balance),
                'closing_balance': str(closing_balance),
                'expected_closing': str(expected),
                'discrepancy': str(discrepancy),
            },
            severity=sev,
            request=request,
        )
        if abs(discrepancy) > Decimal('0.01'):
            messages.warning(
                request,
                f'Session closed with discrepancy of ETB {discrepancy:,.2f}. '
                f'Expected: ETB {expected:,.2f}, Actual: ETB {closing_balance:,.2f}.'
            )
        else:
            messages.success(request, f'Session closed. Cash collected: ETB {total_collected:,.2f}.')
        return redirect('cash_session_list')

    return render(request, 'billing/cash_session_close.html', {
        'session': session,
        'total_collected': total_collected,
        'expected': expected,
    })


# ── Reports ───────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_financial_report')
def report_cash_collection(request):
    today = timezone.localdate()
    date_from = request.GET.get('date_from', today.replace(day=1).isoformat())
    date_to   = request.GET.get('date_to',   today.isoformat())

    payments_qs = (
        Payment.objects
        .filter(
            payment_method=Payment.Method.CASH,
            payment_date__gte=date_from,
            payment_date__lte=date_to,
        )
        .select_related('received_by', 'invoice__patient')
        .order_by('payment_date', 'received_by__username')
    )

    total_cash = payments_qs.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    tx_count = payments_qs.count()

    # Group by cashier
    by_cashier = (
        payments_qs
        .values('received_by__id', 'received_by__first_name', 'received_by__last_name')
        .annotate(total=Sum('amount'), count=Count('id'))
        .order_by('-total')
    )

    # Group by date
    by_date = (
        payments_qs
        .values('payment_date')
        .annotate(total=Sum('amount'), count=Count('id'))
        .order_by('payment_date')
    )

    return render(request, 'billing/report_cash_collection.html', {
        'payments': payments_qs[:200],
        'total_cash': total_cash,
        'tx_count': tx_count,
        'by_cashier': by_cashier,
        'by_date': by_date,
        'date_from': date_from,
        'date_to': date_to,
        'today': today,
    })


@hms_permission_required('core.read_financial_report')
def report_credit(request):
    today = timezone.localdate()
    status_filter = request.GET.get('status', 'all')

    qs = Invoice.objects.filter(
        payment_type=Invoice.PaymentType.CREDIT
    ).select_related('patient', 'credit_approved_by').order_by('-created_at')

    if status_filter == 'outstanding':
        qs = qs.filter(status__in=[Invoice.Status.CREDIT_PENDING, Invoice.Status.PARTIAL])
    elif status_filter == 'overdue':
        qs = qs.filter(
            status__in=[Invoice.Status.CREDIT_PENDING, Invoice.Status.PARTIAL],
            due_date__lt=today,
        )
    elif status_filter == 'paid':
        qs = qs.filter(status=Invoice.Status.PAID)

    total_credit   = qs.aggregate(t=Sum('total_amount'))['t'] or Decimal('0.00')
    total_paid     = qs.aggregate(t=Sum('paid_amount'))['t'] or Decimal('0.00')
    total_remaining = total_credit - total_paid

    # Annotate overdue days
    invoices_annotated = []
    for inv in qs[:200]:
        overdue_days = 0
        if inv.due_date and inv.due_date < today and inv.balance > 0:
            overdue_days = (today - inv.due_date).days
        invoices_annotated.append({'inv': inv, 'overdue_days': overdue_days})

    return render(request, 'billing/report_credit.html', {
        'invoices': invoices_annotated,
        'total_credit': total_credit,
        'total_paid': total_paid,
        'total_remaining': total_remaining,
        'status_filter': status_filter,
        'today': today,
        'filter_choices': [
            ('all',         'All Credit'),
            ('outstanding', 'Outstanding'),
            ('overdue',     'Overdue'),
            ('paid',        'Paid'),
        ],
    })


@hms_permission_required('core.read_financial_report')
def report_outstanding(request):
    today = timezone.localdate()

    qs = Invoice.objects.filter(
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.CREDIT_PENDING]
    ).select_related('patient', 'created_by').order_by('due_date', '-created_at')

    # Aging buckets
    bucket_0_30   = []
    bucket_31_60  = []
    bucket_61_90  = []
    bucket_90plus = []
    no_due        = []

    for inv in qs:
        if not inv.due_date:
            no_due.append(inv)
            continue
        days = (today - inv.due_date).days
        if days <= 0:
            no_due.append(inv)
        elif days <= 30:
            bucket_0_30.append(inv)
        elif days <= 60:
            bucket_31_60.append(inv)
        elif days <= 90:
            bucket_61_90.append(inv)
        else:
            bucket_90plus.append(inv)

    def _sum(lst):
        return sum(i.balance for i in lst)

    buckets = [
        {'label': 'Not yet due',   'invoices': no_due,        'total': _sum(no_due)},
        {'label': '1–30 days',     'invoices': bucket_0_30,   'total': _sum(bucket_0_30)},
        {'label': '31–60 days',    'invoices': bucket_31_60,  'total': _sum(bucket_31_60)},
        {'label': '61–90 days',    'invoices': bucket_61_90,  'total': _sum(bucket_61_90)},
        {'label': '90+ days',      'invoices': bucket_90plus, 'total': _sum(bucket_90plus)},
    ]
    grand_total = sum(b['total'] for b in buckets)

    return render(request, 'billing/report_outstanding.html', {
        'buckets': buckets,
        'grand_total': grand_total,
        'total_count': qs.count(),
        'today': today,
    })


# ── Invoice Split View ────────────────────────────────────────────────────────

UNPAID_STATUSES = [
    Invoice.Status.DRAFT,
    Invoice.Status.ISSUED,
    Invoice.Status.PARTIAL,
    Invoice.Status.CREDIT_PENDING,
]


@hms_permission_required('core.read_billing')
def invoice_split_view(request):
    today = timezone.localdate()
    paid_count = Invoice.objects.filter(status=Invoice.Status.PAID).count()
    paid_total = Invoice.objects.filter(status=Invoice.Status.PAID).aggregate(t=Sum('paid_amount'))['t'] or Decimal('0')
    unpaid_qs = Invoice.objects.filter(status__in=UNPAID_STATUSES)
    unpaid_count = unpaid_qs.count()
    outstanding = sum(inv.balance for inv in unpaid_qs[:500])  # cap scan at 500

    highlight_id = request.GET.get('highlight', '')
    return render(request, 'billing/split_view.html', {
        'paid_count': paid_count,
        'paid_total': paid_total,
        'unpaid_count': unpaid_count,
        'outstanding': outstanding,
        'today': today,
        'highlight_id': highlight_id,
    })


@hms_permission_required('core.read_billing')
def invoice_panel_paid(request):
    q = request.GET.get('q', '').strip()
    date_f = request.GET.get('date', '')

    qs = (
        Invoice.objects
        .filter(status=Invoice.Status.PAID)
        .select_related('patient', 'visit__department', 'visit__doctor')
        .order_by('-updated_at')
    )
    if q:
        qs = qs.filter(
            Q(invoice_number__icontains=q)
            | Q(patient__first_name__icontains=q)
            | Q(patient__last_name__icontains=q)
            | Q(patient__card_number__icontains=q)
        )
    if date_f:
        qs = qs.filter(updated_at__date=date_f)

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    inv_ids = [inv.pk for inv in page_obj]
    lp_map: dict = {}
    if inv_ids:
        for p in (Payment.objects
                  .filter(invoice_id__in=inv_ids)
                  .select_related('received_by')
                  .order_by('-created_at')):
            if p.invoice_id not in lp_map:
                lp_map[p.invoice_id] = p

    rows = [{'inv': inv, 'last_payment': lp_map.get(inv.pk)} for inv in page_obj]

    highlight_id = request.GET.get('highlight', '')
    return render(request, 'billing/_panel_paid.html', {
        'rows': rows,
        'page_obj': page_obj,
        'q': q,
        'date_f': date_f,
        'highlight_id': highlight_id,
    })


@hms_permission_required('core.read_billing')
def invoice_panel_unpaid(request):
    q = request.GET.get('q', '').strip()
    status_f = request.GET.get('status', '')

    qs = (
        Invoice.objects
        .filter(status__in=UNPAID_STATUSES)
        .select_related('patient', 'visit__department', 'visit__doctor', 'created_by')
        .order_by('-created_at')
    )
    if q:
        qs = qs.filter(
            Q(invoice_number__icontains=q)
            | Q(patient__first_name__icontains=q)
            | Q(patient__last_name__icontains=q)
            | Q(patient__card_number__icontains=q)
        )
    if status_f in UNPAID_STATUSES:
        qs = qs.filter(status=status_f)

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'billing/_panel_unpaid.html', {
        'page_obj': page_obj,
        'q': q,
        'status_f': status_f,
        'unpaid_statuses': UNPAID_STATUSES,
        'can_pay': request.user.has_perm('core.process_payment'),
        'can_edit': request.user.has_perm('core.create_invoice'),
        'can_manage': request.user.has_perm('core.manage_billing'),
    })


# ── Service Charge Settings (Administrator) ─────────────────────────────────

@hms_permission_required('core.system_configuration')
def service_charge_settings_edit(request):
    sc_settings = ServiceChargeSettings.get_solo()
    service_types = InvoiceItem.ServiceType.choices

    if request.method == 'POST':
        old = {
            'enabled': sc_settings.enabled,
            'percentage': str(sc_settings.percentage),
            'charge_name': sc_settings.charge_name,
            'allow_cashier_override': sc_settings.allow_cashier_override,
            'default_disabled_service_types': list(sc_settings.default_disabled_service_types),
        }
        try:
            percentage = Decimal(request.POST.get('percentage', '4.00'))
            if percentage < 0 or percentage > 100:
                raise ValueError('Percentage must be between 0 and 100.')
            charge_name = request.POST.get('charge_name', '').strip() or 'Service Charge'
            disabled_types = request.POST.getlist('disabled_service_types')
            valid_types = {v for v, _ in service_types}
            disabled_types = [t for t in disabled_types if t in valid_types]

            sc_settings.enabled = bool(request.POST.get('enabled'))
            sc_settings.percentage = percentage
            sc_settings.charge_name = charge_name
            sc_settings.allow_cashier_override = bool(request.POST.get('allow_cashier_override'))
            sc_settings.default_disabled_service_types = disabled_types
            sc_settings.updated_by = request.user
            sc_settings.save()

            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.BILLING,
                object_type='ServiceChargeSettings', object_id=sc_settings.pk,
                object_repr='Service Charge Settings',
                description='Service Charge Settings updated',
                changes={
                    'enabled': {'old': old['enabled'], 'new': sc_settings.enabled},
                    'percentage': {'old': old['percentage'], 'new': str(sc_settings.percentage)},
                    'charge_name': {'old': old['charge_name'], 'new': sc_settings.charge_name},
                    'allow_cashier_override': {'old': old['allow_cashier_override'], 'new': sc_settings.allow_cashier_override},
                    'default_disabled_service_types': {
                        'old': old['default_disabled_service_types'],
                        'new': sc_settings.default_disabled_service_types,
                    },
                },
                request=request,
            )
            messages.success(request, 'Service Charge Settings updated successfully.')
            return redirect('service_charge_settings_edit')
        except (ValueError, ArithmeticError) as exc:
            messages.error(request, f'Error updating settings: {exc}')

    return render(request, 'billing/service_charge_settings.html', {
        'sc_settings': sc_settings,
        'service_types': service_types,
    })
