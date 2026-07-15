import csv
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .forms import (
    LabCategoryForm, LabReferenceRangeForm, LabSampleForm, LabServiceForm, LabTestGroupForm,
)
from .lab_results import build_result_summary_text, ensure_result_entries, save_result_entries
from .models import (
    AuditLog, Department, Invoice, InvoiceItem, InventoryItem, InventoryTransaction,
    LabCategory, LabOrder, LabReferenceRange, LabSample, LabService, LabServicePriceHistory,
    LabTestGroup, LabTestResultOption, Visit,
)
from .report_export import export_excel


# ─────────────────────────────────────────────────────────────────────────────
# Helper: auto-generate sample barcode
# ─────────────────────────────────────────────────────────────────────────────

def _generate_barcode(order):
    ts = timezone.now().strftime('%Y%m%d%H%M%S')
    return f"LAB-{order.pk:06d}-{ts}"


# ─────────────────────────────────────────────────────────────────────────────
# Lab Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_lab_request')
def lab_dashboard(request):
    today = timezone.localdate()
    status_filter = request.GET.get('status', 'all')
    pay_filter = request.GET.get('pay', 'all')
    search = request.GET.get('q', '').strip()

    # Unpaid orders belong to Billing, not the lab processing queue: a lab
    # order only reaches this queue once payment has cleared (or been
    # credit-approved / waived), per the hospital billing workflow. Orders
    # still stuck at "Waiting Payment" are only visible via Lab Reception.
    qs = (
        LabOrder.objects
        .select_related('visit__patient', 'visit__department', 'ordered_by', 'lab_service')
        .exclude(status__in=[LabOrder.Status.WAITING_PAYMENT, LabOrder.Status.PENDING])
        .order_by('-ordered_at')
    )

    if status_filter == 'stat':
        qs = qs.filter(priority='STAT')
    elif status_filter != 'all':
        qs = qs.filter(status=status_filter)

    if pay_filter == 'paid':
        qs = qs.filter(payment_status=LabOrder.PaymentStatus.PAID)

    if search:
        qs = qs.filter(
            Q(visit__patient__first_name__icontains=search)
            | Q(visit__patient__last_name__icontains=search)
            | Q(visit__patient__card_number__icontains=search)
            | Q(test_name__icontains=search)
            | Q(test_category__icontains=search)
        )

    all_orders = LabOrder.objects.all()
    # Queue-scoped counts: only orders that have cleared payment belong in the
    # technician's active queue (see qs exclusion above).
    queue_orders    = all_orders.exclude(status__in=[LabOrder.Status.WAITING_PAYMENT, LabOrder.Status.PENDING])
    stat_pending    = queue_orders.filter(priority='STAT').exclude(status__in=['Released', 'Completed', 'Cancelled']).count()
    waiting_payment = all_orders.filter(payment_status=LabOrder.PaymentStatus.PENDING_PAYMENT).count()
    sample_pending  = queue_orders.filter(status=LabOrder.Status.SAMPLE_PENDING).count()
    processing      = queue_orders.filter(status__in=[LabOrder.Status.SAMPLE_COLLECTED, LabOrder.Status.PROCESSING]).count()
    result_ready    = queue_orders.filter(status=LabOrder.Status.RESULT_READY).count()
    completed_today = queue_orders.filter(released_at__date=today).count()

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    status_tabs = [
        {'key': 'all',             'label': 'All',              'count': queue_orders.count()},
        {'key': 'Sample Pending',  'label': 'Sample Pending',   'count': sample_pending},
        {'key': 'Processing',      'label': 'Processing',       'count': processing},
        {'key': 'Result Ready',    'label': 'Result Ready',     'count': result_ready},
        {'key': 'stat',            'label': 'STAT',             'count': stat_pending},
    ]

    return render(request, 'lab/dashboard.html', {
        'page_obj':       page_obj,
        'status_filter':  status_filter,
        'pay_filter':     pay_filter,
        'search':         search,
        'status_tabs':    status_tabs,
        'stat_pending':   stat_pending,
        'waiting_payment': waiting_payment,
        'sample_pending': sample_pending,
        'processing':     processing,
        'result_ready':   result_ready,
        'completed_today': completed_today,
        'today':          today,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Lab Order Detail
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_lab_request')
def lab_order_detail(request, order_id):
    order = get_object_or_404(
        LabOrder.objects.select_related(
            'visit__patient', 'visit__department', 'visit__doctor',
            'ordered_by', 'lab_service', 'invoice_item__invoice',
            'released_by',
        ),
        pk=order_id,
    )
    sample = getattr(order, 'sample', None)
    can_process  = request.user.has_perm('core.process_lab_test')
    can_collect  = request.user.has_perm('core.collect_lab_sample')
    can_release  = request.user.has_perm('core.release_lab_result')
    sample_form  = LabSampleForm() if can_collect and not sample else None
    result_entries = ensure_result_entries(order) if order.lab_service else []

    return render(request, 'lab/order_detail.html', {
        'order':        order,
        'sample':       sample,
        'can_process':  can_process,
        'can_collect':  can_collect,
        'can_release':  can_release,
        'sample_form':  sample_form,
        'result_entries': result_entries,
        'status_choices': LabOrder.Status.choices,
    })


@hms_permission_required('core.read_lab_result')
def lab_order_print(request, order_id):
    order = get_object_or_404(
        LabOrder.objects.select_related(
            'visit__patient', 'visit__department', 'ordered_by', 'lab_service__category',
            'resulted_by', 'released_by',
        ),
        pk=order_id,
    )
    entries = order.result_entries.select_related('analyte').order_by('display_order', 'id')
    return render(request, 'lab/order_print.html', {
        'order': order,
        'entries': entries,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Sample Collection
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.collect_lab_sample')
@require_POST
def lab_sample_collect(request, order_id):
    order = get_object_or_404(LabOrder, pk=order_id)

    if not order.payment_cleared:
        messages.error(request, 'Payment must be cleared before collecting a sample.')
        return redirect('lab_order_detail', order_id=order_id)

    if hasattr(order, 'sample'):
        messages.warning(request, 'Sample already recorded for this order.')
        return redirect('lab_order_detail', order_id=order_id)

    form = LabSampleForm(request.POST)
    if form.is_valid():
        sample = form.save(commit=False)
        sample.lab_order = order
        sample.collected_by = request.user
        if not sample.barcode:
            sample.barcode = _generate_barcode(order)
        sample.save()
        order.status = LabOrder.Status.SAMPLE_COLLECTED
        order.save(update_fields=['status'])
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
            object_type='LabOrder', object_id=order.pk,
            object_repr=f'{order.test_name} — {order.visit.patient.full_name}',
            description=f'Sample collected (barcode {sample.barcode}) for {order.test_name}',
            request=request,
        )
        messages.success(request, f'Sample recorded — barcode: {sample.barcode}')
    else:
        messages.error(request, 'Please correct the form errors.')
    return redirect('lab_order_detail', order_id=order_id)


# ─────────────────────────────────────────────────────────────────────────────
# Enter Lab Result
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.process_lab_test')
@require_POST
def lab_result_enter(request, order_id):
    """Save structured, per-analyte result values — the set of analytes and
    their expected input type/unit/reference range come entirely from the
    ordered LabService's catalog definition (see core.lab_results), never
    from free text typed by the technician."""
    order = get_object_or_404(LabOrder.objects.select_related('lab_service', 'visit__patient'), pk=order_id)
    entries = ensure_result_entries(order)

    submitted = {}
    has_any_value = False
    for entry in entries:
        value = request.POST.get(f'value_{entry.analyte_id}', '').strip()
        comments = request.POST.get(f'comments_{entry.analyte_id}', '').strip()
        submitted[str(entry.analyte_id)] = {'value': value, 'comments': comments}
        if value:
            has_any_value = True

    manual_critical = bool(request.POST.get('is_critical'))
    critical_notes = request.POST.get('critical_notes', '').strip()

    if not has_any_value:
        messages.error(request, 'Enter at least one result value.')
        return redirect('lab_order_detail', order_id=order_id)

    saved_entries, auto_critical = save_result_entries(order, submitted, request.user)
    is_critical = manual_critical or auto_critical

    order.result = build_result_summary_text(order)
    order.status = LabOrder.Status.RESULT_READY
    order.resulted_at = timezone.now()
    order.resulted_by = request.user
    order.is_critical = is_critical
    order.critical_notes = critical_notes
    if is_critical:
        order.critical_flagged_by = request.user
        order.critical_flagged_at = timezone.now()
    order.save()

    log_action(
        request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
        object_type='LabOrder', object_id=order.pk,
        object_repr=f'{order.test_name} — {order.visit.patient.full_name}',
        description=f'Lab result entered for {order.test_name} ({order.visit.patient.full_name}), '
                    f'{sum(1 for e in saved_entries if e.value)} value(s)'
                    + (f' — CRITICAL: {critical_notes or "auto-detected"}' if is_critical else ''),
        severity=AuditLog.Severity.CRITICAL if is_critical else None,
        request=request,
    )
    messages.success(
        request,
        f'Result entered for "{order.test_name}".' + (' Flagged as CRITICAL.' if is_critical else '') + ' Awaiting release.',
    )
    return redirect('lab_order_detail', order_id=order_id)


# ─────────────────────────────────────────────────────────────────────────────
# Laboratory Consumable / Reagent Usage
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.process_lab_test')
def lab_consumable_use(request, order_id):
    """Record reagents/consumables used while processing a lab order.

    Uses the shared inventory catalogue (InventoryItem, item_type=Lab Supply)
    so consumption is deducted from real stock and logged to InventoryTransaction —
    the same ledger the Stock Card / Stock Movement reports read from.
    """
    order = get_object_or_404(LabOrder.objects.select_related('visit__patient'), pk=order_id)
    lab_items = InventoryItem.objects.filter(
        item_type=InventoryItem.ItemType.LAB_SUPPLY, is_active=True,
    ).order_by('name')

    if request.method == 'POST':
        item_id = request.POST.get('inventory_item')
        qty_str = request.POST.get('quantity', '0')
        notes = request.POST.get('notes', '').strip()
        try:
            qty = Decimal(qty_str)
        except Exception:
            messages.error(request, 'Invalid quantity.')
            return redirect('lab_consumable_use', order_id=order_id)

        if not item_id or qty <= 0:
            messages.error(request, 'Select a consumable and enter a quantity greater than zero.')
            return redirect('lab_consumable_use', order_id=order_id)

        item = get_object_or_404(InventoryItem, pk=item_id, item_type=InventoryItem.ItemType.LAB_SUPPLY)
        if item.quantity_in_stock < qty:
            messages.error(request, f'Insufficient stock for "{item.name}". Available: {item.quantity_in_stock} {item.unit}.')
            return redirect('lab_consumable_use', order_id=order_id)

        lab_dept = Department.objects.filter(name='Laboratory').first()

        with transaction.atomic():
            item.quantity_in_stock -= qty
            item.save(update_fields=['quantity_in_stock'])
            InventoryTransaction.objects.create(
                inventory_item=item,
                transaction_type=InventoryTransaction.TxType.ISSUE,
                quantity_out=qty,
                balance_after=item.quantity_in_stock,
                unit_cost=item.unit_cost,
                reference_number=f'LAB-{order.pk}',
                department=lab_dept,
                notes=notes or f'Consumed for lab order #{order.pk} — {order.test_name} ({order.visit.patient.full_name}).',
                performed_by=request.user,
            )

        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
            object_type='LabOrder', object_id=order.pk,
            object_repr=f'{order.test_name} — {order.visit.patient.full_name}',
            description=f'Consumable "{item.name}" x{qty} {item.unit} used for {order.test_name}',
            request=request,
        )
        messages.success(request, f'Recorded {qty} {item.unit} of "{item.name}" used for this test.')
        return redirect('lab_order_detail', order_id=order_id)

    return render(request, 'lab/consumable_use_form.html', {
        'order': order,
        'lab_items': lab_items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Release Lab Result
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.release_lab_result')
@require_POST
def lab_result_release(request, order_id):
    order = get_object_or_404(LabOrder, pk=order_id)
    if order.status != LabOrder.Status.RESULT_READY:
        messages.error(request, 'Result must be ready before releasing.')
        return redirect('lab_order_detail', order_id=order_id)

    order.status = LabOrder.Status.RELEASED
    order.released_at = timezone.now()
    order.released_by = request.user
    order.save()
    log_action(
        request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
        object_type='LabOrder', object_id=order.pk,
        object_repr=f'{order.test_name} — {order.visit.patient.full_name}',
        description=f'Lab result released for {order.test_name} ({order.visit.patient.full_name})',
        request=request,
    )

    try:
        from .notifications import notify_lab_result_ready
        notify_lab_result_ready(order, sender_user=request.user)
    except Exception:
        pass

    messages.success(request, 'Result released successfully.')
    return redirect('lab_order_detail', order_id=order_id)


# ─────────────────────────────────────────────────────────────────────────────
# Update Lab Order Status
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.process_lab_test')
@require_POST
def lab_order_update_status(request, order_id):
    order = get_object_or_404(LabOrder, pk=order_id)
    new_status = request.POST.get('status', '').strip()
    valid_statuses = [s for s, _ in LabOrder.Status.choices]
    if new_status in valid_statuses:
        order.status = new_status
        if new_status in (LabOrder.Status.RELEASED, LabOrder.Status.COMPLETED) and not order.resulted_at:
            order.resulted_at = timezone.now()
        order.save()
        messages.success(request, f'Order status updated to "{new_status}".')
    else:
        messages.error(request, 'Invalid status value.')
    return redirect('lab_order_detail', order_id=order_id)


# ─────────────────────────────────────────────────────────────────────────────
# Cancel Lab Order
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.process_lab_test')
@require_POST
def lab_order_cancel(request, order_id):
    order = get_object_or_404(LabOrder, pk=order_id)
    reason = request.POST.get('reason', '').strip()

    if order.status in (LabOrder.Status.RELEASED, LabOrder.Status.CANCELLED):
        messages.error(request, 'Cannot cancel a released or already-cancelled order.')
        return redirect('lab_order_detail', order_id=order_id)

    with transaction.atomic():
        order.status = LabOrder.Status.CANCELLED
        order.save(update_fields=['status'])
        # Remove the invoice item if it exists and invoice is still editable
        if order.invoice_item_id:
            item = order.invoice_item
            inv = item.invoice
            if inv.status in (Invoice.Status.DRAFT, Invoice.Status.ISSUED):
                subtotal = item.total
                item.delete()
                inv.total_amount = max(Decimal('0'), inv.total_amount - subtotal)
                inv.save(update_fields=['total_amount'])
                order.invoice_item = None
                order.save(update_fields=['invoice_item'])

    log_action(
        request.user, AuditLog.Action.CANCEL, AuditLog.Module.LABORATORY,
        object_type='LabOrder', object_id=order.pk,
        object_repr=f'{order.test_name} — {order.visit.patient.full_name}',
        description=f'Lab order cancelled: {order.test_name}. Reason: {reason or "—"}',
        request=request,
    )
    messages.success(request, 'Lab order cancelled.')
    return redirect('lab_dashboard')


# ─────────────────────────────────────────────────────────────────────────────
# Lab Service Management
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_lab_services')
def lab_service_list(request):
    category_f = request.GET.get('category', '')
    panel_f    = request.GET.get('panel', '')
    active_f   = request.GET.get('active', '')
    search     = request.GET.get('q', '').strip()

    qs = LabService.objects.select_related('category', 'panel')
    if category_f:
        qs = qs.filter(category_id=category_f)
    if panel_f:
        qs = qs.filter(panel_id=panel_f)
    if active_f == '1':
        qs = qs.filter(is_active=True)
    elif active_f == '0':
        qs = qs.filter(is_active=False)
    if search:
        qs = qs.filter(
            Q(name__icontains=search) | Q(code__icontains=search) |
            Q(category__name__icontains=search) | Q(keywords__icontains=search)
        )

    paginator = Paginator(qs, 30)
    page_obj  = paginator.get_page(request.GET.get('page'))

    category_counts = (
        LabService.objects.values('category__id', 'category__name')
        .annotate(count=Count('id'))
        .order_by('category__name')
    )

    return render(request, 'lab/service_list.html', {
        'page_obj':        page_obj,
        'category_f':      category_f,
        'panel_f':         panel_f,
        'active_f':        active_f,
        'search':          search,
        'categories':      LabCategory.objects.filter(is_active=True).order_by('name'),
        'panels':          LabService.objects.filter(panel_tests__isnull=False).distinct().order_by('name'),
        'category_counts': category_counts,
    })


@hms_permission_required('core.manage_lab_services')
def lab_service_create(request):
    if request.method == 'POST':
        form = LabServiceForm(request.POST)
        if form.is_valid():
            svc = form.save(commit=False)
            svc.created_by = request.user
            svc.save()
            if svc.standard_price > 0:
                LabServicePriceHistory.objects.create(
                    lab_service=svc,
                    old_price=0,
                    new_price=svc.standard_price,
                    price_type='standard',
                    changed_by=request.user,
                    reason='Initial price on creation',
                )
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.LABORATORY,
                object_type='LabService', object_id=svc.pk,
                object_repr=str(svc),
                description=f'Lab service created: {svc.name} [{svc.code}] ETB {svc.standard_price}',
                request=request,
            )
            messages.success(request, f'Lab service "{svc.name}" created successfully.')
            return redirect('lab_service_detail', pk=svc.pk)
    else:
        form = LabServiceForm()
    return render(request, 'lab/service_form.html', {'form': form, 'action': 'Create'})


@hms_permission_required('core.manage_lab_services')
def lab_service_edit(request, pk):
    svc = get_object_or_404(LabService, pk=pk)
    old_prices = {
        'standard':  svc.standard_price,
        'insurance': svc.insurance_price,
        'corporate': svc.corporate_price,
        'emergency': svc.emergency_price,
    }
    if request.method == 'POST':
        form = LabServiceForm(request.POST, instance=svc)
        if form.is_valid():
            reason = form.cleaned_data.get('price_change_reason', '')
            svc = form.save()
            # Record price history for any changed price type
            price_fields = {
                'standard': svc.standard_price,
                'insurance': svc.insurance_price,
                'corporate': svc.corporate_price,
                'emergency': svc.emergency_price,
            }
            for ptype, new_val in price_fields.items():
                old_val = old_prices[ptype]
                if new_val is not None and old_val != new_val:
                    LabServicePriceHistory.objects.create(
                        lab_service=svc,
                        old_price=old_val or 0,
                        new_price=new_val,
                        price_type=ptype,
                        changed_by=request.user,
                        reason=reason,
                    )
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
                object_type='LabService', object_id=svc.pk,
                object_repr=str(svc),
                description=f'Lab service updated: {svc.name} [{svc.code}]',
                request=request,
            )
            messages.success(request, f'Lab service "{svc.name}" updated.')
            return redirect('lab_service_detail', pk=svc.pk)
    else:
        form = LabServiceForm(instance=svc)
    return render(request, 'lab/service_form.html', {'form': form, 'svc': svc, 'action': 'Edit'})


@hms_permission_required('core.manage_lab_services')
def lab_service_toggle(request, pk):
    """Activate / deactivate a lab service (POST only)."""
    svc = get_object_or_404(LabService, pk=pk)
    if request.method == 'POST':
        svc.is_active = not svc.is_active
        svc.save(update_fields=['is_active'])
        state = 'activated' if svc.is_active else 'deactivated'
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
            object_type='LabService', object_id=svc.pk,
            object_repr=str(svc),
            description=f'Lab service {state}: {svc.name}',
            request=request,
        )
        messages.success(request, f'"{svc.name}" has been {state}.')
    return redirect('lab_service_detail', pk=pk)


@hms_permission_required('core.manage_lab_services')
def lab_service_detail(request, pk):
    svc = get_object_or_404(LabService, pk=pk)
    recent_orders = (
        LabOrder.objects.filter(lab_service=svc)
        .select_related('visit__patient')
        .order_by('-ordered_at')[:15]
    )
    stats = LabOrder.objects.filter(lab_service=svc).aggregate(
        total=Count('id'),
        released=Count('id', filter=Q(status__in=[LabOrder.Status.RELEASED, LabOrder.Status.COMPLETED])),
        revenue=Sum('unit_price', filter=Q(payment_status=LabOrder.PaymentStatus.PAID)),
    )
    price_history = svc.price_history.select_related('changed_by').order_by('-changed_at')[:20]
    panel_tests = svc.panel_tests.select_related('category').order_by('display_order', 'name')
    result_options = svc.result_options.order_by('display_order')
    return render(request, 'lab/service_detail.html', {
        'svc': svc,
        'recent_orders': recent_orders,
        'stats': stats,
        'price_history': price_history,
        'panel_tests': panel_tests,
        'result_options': result_options,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Lab Category & Test Group management (admin configuration)
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_lab_services')
def lab_category_list(request):
    categories = LabCategory.objects.annotate(test_count=Count('lab_services')).order_by('display_order', 'name')
    return render(request, 'lab/category_list.html', {'categories': categories})


@hms_permission_required('core.manage_lab_services')
def lab_category_create(request):
    if request.method == 'POST':
        form = LabCategoryForm(request.POST)
        if form.is_valid():
            category = form.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.LABORATORY,
                object_type='LabCategory', object_id=category.pk, object_repr=str(category),
                description=f'Lab category created: {category.name}', request=request,
            )
            messages.success(request, f'Category "{category.name}" created.')
            return redirect('lab_category_list')
    else:
        form = LabCategoryForm()
    return render(request, 'lab/category_form.html', {'form': form, 'action': 'Create'})


@hms_permission_required('core.manage_lab_services')
def lab_category_edit(request, pk):
    category = get_object_or_404(LabCategory, pk=pk)
    if request.method == 'POST':
        form = LabCategoryForm(request.POST, instance=category)
        if form.is_valid():
            category = form.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
                object_type='LabCategory', object_id=category.pk, object_repr=str(category),
                description=f'Lab category updated: {category.name}', request=request,
            )
            messages.success(request, f'Category "{category.name}" updated.')
            return redirect('lab_category_list')
    else:
        form = LabCategoryForm(instance=category)
    return render(request, 'lab/category_form.html', {'form': form, 'category': category, 'action': 'Edit'})


@hms_permission_required('core.manage_lab_services')
def lab_test_group_list(request):
    groups = LabTestGroup.objects.select_related('category').annotate(test_count=Count('lab_services')).order_by('display_order', 'name')
    return render(request, 'lab/group_list.html', {'groups': groups})


@hms_permission_required('core.manage_lab_services')
def lab_test_group_create(request):
    if request.method == 'POST':
        form = LabTestGroupForm(request.POST)
        if form.is_valid():
            group = form.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.LABORATORY,
                object_type='LabTestGroup', object_id=group.pk, object_repr=str(group),
                description=f'Lab test group created: {group.name}', request=request,
            )
            messages.success(request, f'Group "{group.name}" created.')
            return redirect('lab_test_group_list')
    else:
        form = LabTestGroupForm()
    return render(request, 'lab/group_form.html', {'form': form, 'action': 'Create'})


@hms_permission_required('core.manage_lab_services')
def lab_test_group_edit(request, pk):
    group = get_object_or_404(LabTestGroup, pk=pk)
    if request.method == 'POST':
        form = LabTestGroupForm(request.POST, instance=group)
        if form.is_valid():
            group = form.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
                object_type='LabTestGroup', object_id=group.pk, object_repr=str(group),
                description=f'Lab test group updated: {group.name}', request=request,
            )
            messages.success(request, f'Group "{group.name}" updated.')
            return redirect('lab_test_group_list')
    else:
        form = LabTestGroupForm(instance=group)
    return render(request, 'lab/group_form.html', {'form': form, 'group': group, 'action': 'Edit'})


# ─────────────────────────────────────────────────────────────────────────────
# Lab Reference Range overrides (age/sex-specific) — per analyte
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_lab_services')
def lab_reference_range_create(request, service_id):
    analyte = get_object_or_404(LabService, pk=service_id)
    if request.method == 'POST':
        form = LabReferenceRangeForm(request.POST)
        if form.is_valid():
            rr = form.save(commit=False)
            rr.lab_service = analyte
            rr.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.LABORATORY,
                object_type='LabReferenceRange', object_id=rr.pk, object_repr=str(rr),
                description=f'Reference range override added for {analyte.name}: {rr}', request=request,
            )
            messages.success(request, 'Reference range override added.')
            return redirect('lab_service_detail', pk=analyte.pk)
    else:
        form = LabReferenceRangeForm()
    return render(request, 'lab/reference_range_form.html', {'form': form, 'analyte': analyte, 'action': 'Add'})


@hms_permission_required('core.manage_lab_services')
def lab_reference_range_edit(request, service_id, pk):
    analyte = get_object_or_404(LabService, pk=service_id)
    rr = get_object_or_404(LabReferenceRange, pk=pk, lab_service=analyte)
    if request.method == 'POST':
        form = LabReferenceRangeForm(request.POST, instance=rr)
        if form.is_valid():
            rr = form.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
                object_type='LabReferenceRange', object_id=rr.pk, object_repr=str(rr),
                description=f'Reference range override updated for {analyte.name}: {rr}', request=request,
            )
            messages.success(request, 'Reference range override updated.')
            return redirect('lab_service_detail', pk=analyte.pk)
    else:
        form = LabReferenceRangeForm(instance=rr)
    return render(request, 'lab/reference_range_form.html', {'form': form, 'analyte': analyte, 'range': rr, 'action': 'Edit'})


@hms_permission_required('core.manage_lab_services')
@require_POST
def lab_reference_range_delete(request, service_id, pk):
    analyte = get_object_or_404(LabService, pk=service_id)
    rr = get_object_or_404(LabReferenceRange, pk=pk, lab_service=analyte)
    log_action(
        request.user, AuditLog.Action.DELETE, AuditLog.Module.LABORATORY,
        object_type='LabReferenceRange', object_id=rr.pk, object_repr=str(rr),
        description=f'Reference range override removed for {analyte.name}: {rr}', request=request,
    )
    rr.delete()
    messages.success(request, 'Reference range override removed.')
    return redirect('lab_service_detail', pk=analyte.pk)


# ─────────────────────────────────────────────────────────────────────────────
# AJAX — Fast lab test search (used by doctor ordering form)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def lab_service_search_api(request):
    """Return JSON list of matching active lab services for AJAX autocomplete.

    Search spans keyword/name, code, category, panel name, and specimen type
    (per the Ordering spec: "Search tests by keyword / code / category /
    panel / specimen type"). Each result also carries `panel_test_ids` — the
    ids of every test that belongs to it as a panel — so the ordering cart
    can auto-expand a panel selection into its component tests.
    """
    q           = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '').strip()
    panel_id    = request.GET.get('panel', '').strip()
    specimen    = request.GET.get('specimen', '').strip()

    qs = LabService.objects.filter(is_active=True).select_related('category', 'panel')
    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(short_name__icontains=q) |
            Q(code__icontains=q) |
            Q(keywords__icontains=q) |
            Q(category__name__icontains=q) |
            Q(panel__name__icontains=q)
        )
    if category_id:
        qs = qs.filter(category_id=category_id)
    if panel_id:
        qs = qs.filter(panel_id=panel_id)
    if specimen:
        qs = qs.filter(sample_type=specimen)

    if not q and not category_id and not panel_id and not specimen:
        return JsonResponse({'results': []})

    qs = qs.prefetch_related('panel_tests').order_by('category__display_order', 'name')[:40]

    results = []
    for svc in qs:
        child_ids = list(svc.panel_tests.values_list('id', flat=True))
        results.append({
            'id':               svc.id,
            'name':             svc.name,
            'short_name':       svc.short_name or '',
            'code':             svc.code,
            'category':         svc.category.name if svc.category else '',
            'panel':            svc.panel.name if svc.panel else '',
            'sample_type':      svc.sample_type,
            'price':            str(svc.standard_price),
            'emergency_price':  str(svc.emergency_price) if svc.emergency_price else str(svc.standard_price),
            'turnaround_hours': svc.turnaround_hours,
            'container':        svc.container or '',
            'is_panel':         bool(child_ids),
            'panel_test_ids':   child_ids,
        })
    return JsonResponse({'results': results})


# ─────────────────────────────────────────────────────────────────────────────
# REPORTS — Lab test management
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_lab_reports')
def report_lab_usage(request):
    """How many times each test was ordered and revenue generated."""
    import datetime
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today.replace(day=1)))
    date_to_str   = request.GET.get('date_to',   str(today))
    category_f    = request.GET.get('category', '')

    try:
        date_from = datetime.date.fromisoformat(date_from_str)
        date_to   = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_from = today.replace(day=1)
        date_to   = today

    qs = (
        LabOrder.objects
        .filter(ordered_at__date__gte=date_from, ordered_at__date__lte=date_to)
        .exclude(lab_service=None)
        .values('lab_service__id', 'lab_service__name', 'lab_service__code', 'lab_service__category__name')
        .annotate(
            order_count=Count('id'),
            revenue=Sum('unit_price', filter=Q(payment_status=LabOrder.PaymentStatus.PAID)),
        )
        .order_by('-order_count')
    )
    if category_f:
        qs = qs.filter(lab_service__category_id=category_f)

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="lab_usage_{date_from}_to_{date_to}.csv"'
        w = csv.writer(response)
        w.writerow(['Test Name', 'Code', 'Category', 'Orders', 'Revenue (ETB)'])
        for r in qs:
            w.writerow([r['lab_service__name'], r['lab_service__code'], r['lab_service__category__name'],
                        r['order_count'], r['revenue'] or 0])
        return response

    return render(request, 'lab/reports/usage.html', {
        'rows': list(qs),
        'date_from': date_from, 'date_to': date_to,
        'date_from_str': date_from_str, 'date_to_str': date_to_str,
        'category_f': category_f,
        'categories': LabCategory.objects.order_by('name'),
        'grand_total': sum(r['order_count'] for r in qs),
        'grand_revenue': sum((r['revenue'] or 0) for r in qs),
    })


@hms_permission_required('core.view_lab_reports')
def report_lab_prices(request):
    """Current prices and price change history."""
    category_f = request.GET.get('category', '')
    qs = LabService.objects.select_related('category').prefetch_related('price_history').order_by('category__display_order', 'name')
    if category_f:
        qs = qs.filter(category_id=category_f)

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="lab_prices.csv"'
        w = csv.writer(response)
        w.writerow(['Test Name', 'Code', 'Category', 'Standard Price', 'Insurance', 'Emergency', 'Active'])
        for svc in qs:
            w.writerow([svc.name, svc.code, svc.category.name if svc.category else '',
                        svc.standard_price, svc.insurance_price or '', svc.emergency_price or '',
                        'Yes' if svc.is_active else 'No'])
        return response

    return render(request, 'lab/reports/prices.html', {
        'services': qs,
        'category_f': category_f,
        'categories': LabCategory.objects.order_by('name'),
    })


@hms_permission_required('core.view_lab_reports')
def report_lab_new_tests(request):
    """Recently added lab tests."""
    import datetime
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today.replace(day=1)))
    date_to_str   = request.GET.get('date_to',   str(today))

    try:
        date_from = datetime.date.fromisoformat(date_from_str)
        date_to   = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_from = today.replace(day=1)
        date_to   = today

    qs = LabService.objects.filter(
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    ).select_related('created_by').order_by('-created_at')

    return render(request, 'lab/reports/new_tests.html', {
        'services': qs,
        'date_from': date_from, 'date_to': date_to,
        'date_from_str': date_from_str, 'date_to_str': date_to_str,
        'total': qs.count(),
    })


@hms_permission_required('core.view_lab_reports')
def report_lab_inventory(request):
    """Lab Inventory Report — stock on hand + recent consumption for lab supplies."""
    items = InventoryItem.objects.filter(
        item_type=InventoryItem.ItemType.LAB_SUPPLY,
    ).select_related('category').order_by('name')

    status_f = request.GET.get('status', '')
    if status_f == 'low':
        items = [i for i in items if i.is_low_stock and not i.is_out_of_stock]
    elif status_f == 'out':
        items = [i for i in items if i.is_out_of_stock]

    total_value = sum(i.inventory_value for i in items)

    recent_tx = InventoryTransaction.objects.filter(
        inventory_item__item_type=InventoryItem.ItemType.LAB_SUPPLY,
    ).select_related('inventory_item', 'performed_by').order_by('-transaction_date')[:50]

    headers = ['Item', 'Category', 'Unit', 'In Stock', 'Reorder Level', 'Unit Cost', 'Value', 'Status']
    rows = [
        [item.name, str(item.category or ''), item.unit, item.quantity_in_stock,
         item.reorder_level, item.unit_cost, item.inventory_value, item.stock_status]
        for item in items
    ]

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="lab_inventory.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(rows)
        return response

    if request.GET.get('export') == 'excel':
        return export_excel('lab_inventory.xlsx', headers, rows, title='Lab Inventory')

    qp = '&'.join(f'{k}={v}' for k, v in request.GET.items() if k not in ('page', 'export'))
    return render(request, 'lab/reports/inventory.html', {
        'items': items,
        'status_f': status_f,
        'total_value': total_value,
        'recent_tx': recent_tx,
        'qp': qp,
    })


# ─────────────────────────────────────────────────────────────────────────────
# REPORTS — Master list, by category, by department, TAT, critical results
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.view_lab_reports')
def report_lab_master_list(request):
    """Laboratory Test Master List — every test with its full configuration."""
    category_f = request.GET.get('category', '')
    active_f   = request.GET.get('active', '1')

    qs = LabService.objects.select_related('category', 'panel', 'department').order_by(
        'category__display_order', 'panel__name', 'display_order', 'name',
    )
    if category_f:
        qs = qs.filter(category_id=category_f)
    if active_f == '1':
        qs = qs.filter(is_active=True)
    elif active_f == '0':
        qs = qs.filter(is_active=False)

    headers = [
        'Code', 'Name', 'Category', 'Panel', 'Specimen', 'Unit', 'Reference Range',
        'Critical Values', 'Result Type', 'TAT (hrs)', 'Standard Price', 'Department', 'Active',
    ]
    rows = [[
        s.code, s.name, s.category.name if s.category else '', s.panel.name if s.panel else '',
        s.get_sample_type_display(), s.unit_of_measurement, s.reference_range, s.critical_values,
        s.get_result_type_display(), s.turnaround_hours, s.standard_price,
        s.department.name if s.department else '', 'Yes' if s.is_active else 'No',
    ] for s in qs]

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="lab_master_list.csv"'
        w = csv.writer(response)
        w.writerow(headers)
        w.writerows(rows)
        return response
    if request.GET.get('export') == 'excel':
        return export_excel('lab_master_list.xlsx', headers, rows, title='Laboratory Test Master List')

    return render(request, 'lab/reports/master_list.html', {
        'services': qs, 'category_f': category_f, 'active_f': active_f,
        'categories': LabCategory.objects.order_by('name'), 'total': qs.count(),
    })


@hms_permission_required('core.view_lab_reports')
def report_lab_by_category(request):
    """Tests by Category — count and revenue-to-date grouped by category."""
    rows_qs = (
        LabCategory.objects.annotate(
            test_count=Count('lab_services', distinct=True),
            order_count=Count('lab_services__orders', distinct=True),
            revenue=Sum('lab_services__orders__unit_price', filter=Q(lab_services__orders__payment_status=LabOrder.PaymentStatus.PAID)),
        ).order_by('display_order', 'name')
    )
    headers = ['Category', 'Test Count', 'Orders', 'Revenue (ETB)']
    rows = [[c.name, c.test_count, c.order_count, c.revenue or 0] for c in rows_qs]

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="lab_tests_by_category.csv"'
        w = csv.writer(response)
        w.writerow(headers)
        w.writerows(rows)
        return response

    return render(request, 'lab/reports/by_category.html', {'categories': rows_qs})


@hms_permission_required('core.view_lab_reports')
def report_lab_by_department(request):
    """Tests by Department — tests tagged to a hospital Department."""
    qs = LabService.objects.select_related('department', 'category').order_by('department__name', 'name')
    dept_f = request.GET.get('department', '')
    if dept_f:
        qs = qs.filter(department_id=dept_f)

    headers = ['Test', 'Code', 'Department', 'Category', 'Active']
    rows = [[
        s.name, s.code, s.department.name if s.department else 'Unassigned',
        s.category.name if s.category else '', 'Yes' if s.is_active else 'No',
    ] for s in qs]

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="lab_tests_by_department.csv"'
        w = csv.writer(response)
        w.writerow(headers)
        w.writerows(rows)
        return response

    return render(request, 'lab/reports/by_department.html', {
        'services': qs, 'dept_f': dept_f,
        'departments': Department.objects.order_by('name'),
    })


@hms_permission_required('core.view_lab_reports')
def report_lab_tat(request):
    """Turnaround Time Report — actual (ordered->resulted) vs. each test's target TAT."""
    import datetime
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today.replace(day=1)))
    date_to_str   = request.GET.get('date_to',   str(today))
    try:
        date_from = datetime.date.fromisoformat(date_from_str)
        date_to   = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_from = today.replace(day=1)
        date_to   = today

    orders = (
        LabOrder.objects
        .filter(ordered_at__date__gte=date_from, ordered_at__date__lte=date_to, resulted_at__isnull=False)
        .exclude(lab_service=None)
        .select_related('lab_service', 'visit__patient')
        .order_by('-ordered_at')
    )

    rows = []
    for o in orders:
        actual_hours = round((o.resulted_at - o.ordered_at).total_seconds() / 3600, 1)
        target_hours = o.lab_service.turnaround_hours if o.lab_service else None
        within_target = target_hours is not None and actual_hours <= target_hours
        rows.append({
            'order': o, 'actual_hours': actual_hours, 'target_hours': target_hours,
            'within_target': within_target,
        })

    total = len(rows)
    within_count = sum(1 for r in rows if r['within_target'])

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="lab_tat_{date_from}_to_{date_to}.csv"'
        w = csv.writer(response)
        w.writerow(['Test', 'Patient', 'Ordered At', 'Resulted At', 'Actual Hours', 'Target Hours', 'Within Target'])
        for r in rows:
            o = r['order']
            w.writerow([
                o.test_name, o.visit.patient.full_name, o.ordered_at, o.resulted_at,
                r['actual_hours'], r['target_hours'] or '', 'Yes' if r['within_target'] else 'No',
            ])
        return response

    return render(request, 'lab/reports/tat.html', {
        'rows': rows, 'date_from': date_from, 'date_to': date_to,
        'date_from_str': date_from_str, 'date_to_str': date_to_str,
        'total': total, 'within_count': within_count,
        'pct_within': round(within_count / total * 100, 1) if total else 0,
    })


@hms_permission_required('core.view_lab_reports')
def report_lab_critical(request):
    """Critical Result Report — every lab order flagged critical at result entry."""
    import datetime
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today.replace(day=1)))
    date_to_str   = request.GET.get('date_to',   str(today))
    try:
        date_from = datetime.date.fromisoformat(date_from_str)
        date_to   = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_from = today.replace(day=1)
        date_to   = today

    orders = (
        LabOrder.objects
        .filter(is_critical=True, ordered_at__date__gte=date_from, ordered_at__date__lte=date_to)
        .select_related('visit__patient', 'critical_flagged_by', 'released_by')
        .order_by('-critical_flagged_at')
    )

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="lab_critical_results_{date_from}_to_{date_to}.csv"'
        w = csv.writer(response)
        w.writerow(['Test', 'Patient', 'Result', 'Critical Notes', 'Flagged By', 'Flagged At', 'Status'])
        for o in orders:
            w.writerow([
                o.test_name, o.visit.patient.full_name, o.result, o.critical_notes,
                o.critical_flagged_by.get_full_name() if o.critical_flagged_by else '',
                o.critical_flagged_at, o.status,
            ])
        return response

    return render(request, 'lab/reports/critical.html', {
        'orders': orders, 'date_from': date_from, 'date_to': date_to,
        'date_from_str': date_from_str, 'date_to_str': date_to_str,
        'total': orders.count(),
    })
