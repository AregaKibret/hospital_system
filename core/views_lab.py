from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .forms import LabSampleForm, LabServiceForm
from .models import (
    AuditLog, Department, Invoice, InvoiceItem, LabOrder, LabSample,
    LabService, Visit,
)


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

    qs = (
        LabOrder.objects
        .select_related('visit__patient', 'visit__department', 'ordered_by', 'lab_service')
        .order_by('-ordered_at')
    )

    if status_filter == 'stat':
        qs = qs.filter(priority='STAT')
    elif status_filter != 'all':
        qs = qs.filter(status=status_filter)

    if pay_filter == 'unpaid':
        qs = qs.filter(payment_status=LabOrder.PaymentStatus.PENDING_PAYMENT)
    elif pay_filter == 'paid':
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
    stat_pending    = all_orders.filter(priority='STAT').exclude(status__in=['Released', 'Completed', 'Cancelled']).count()
    waiting_payment = all_orders.filter(payment_status=LabOrder.PaymentStatus.PENDING_PAYMENT).count()
    sample_pending  = all_orders.filter(status=LabOrder.Status.SAMPLE_PENDING).count()
    processing      = all_orders.filter(status__in=[LabOrder.Status.SAMPLE_COLLECTED, LabOrder.Status.PROCESSING]).count()
    result_ready    = all_orders.filter(status=LabOrder.Status.RESULT_READY).count()
    completed_today = all_orders.filter(released_at__date=today).count()

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    status_tabs = [
        {'key': 'all',             'label': 'All',              'count': all_orders.count()},
        {'key': 'Waiting Payment', 'label': 'Waiting Payment',  'count': waiting_payment},
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
# Lab Reception (payment verification + handoff)
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.lab_reception')
def lab_reception(request):
    today = timezone.localdate()
    pay_filter = request.GET.get('pay', 'all')
    priority_f = request.GET.get('priority', '')
    search = request.GET.get('q', '').strip()

    qs = (
        LabOrder.objects
        .select_related('visit__patient', 'visit__department', 'ordered_by', 'lab_service',
                        'invoice_item__invoice')
        .order_by('priority', '-ordered_at')
    )

    if pay_filter == 'unpaid':
        qs = qs.filter(payment_status=LabOrder.PaymentStatus.PENDING_PAYMENT)
    elif pay_filter == 'paid':
        qs = qs.filter(payment_status__in=[
            LabOrder.PaymentStatus.PAID,
            LabOrder.PaymentStatus.CREDIT,
            LabOrder.PaymentStatus.WAIVED,
        ])

    if priority_f:
        qs = qs.filter(priority=priority_f)

    if search:
        qs = qs.filter(
            Q(visit__patient__first_name__icontains=search)
            | Q(visit__patient__last_name__icontains=search)
            | Q(visit__patient__card_number__icontains=search)
            | Q(test_name__icontains=search)
        )

    qs = qs.exclude(status__in=[
        LabOrder.Status.RELEASED, LabOrder.Status.COMPLETED, LabOrder.Status.CANCELLED,
    ])

    paginator = Paginator(qs, 30)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'lab/reception.html', {
        'page_obj':   page_obj,
        'pay_filter': pay_filter,
        'priority_f': priority_f,
        'search':     search,
        'today':      today,
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

    return render(request, 'lab/order_detail.html', {
        'order':        order,
        'sample':       sample,
        'can_process':  can_process,
        'can_collect':  can_collect,
        'can_release':  can_release,
        'sample_form':  sample_form,
        'status_choices': LabOrder.Status.choices,
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
    order = get_object_or_404(LabOrder, pk=order_id)
    result_text = request.POST.get('result', '').strip()
    if result_text:
        order.result = result_text
        order.status = LabOrder.Status.RESULT_READY
        order.resulted_at = timezone.now()
        order.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
            object_type='LabOrder', object_id=order.pk,
            object_repr=f'{order.test_name} — {order.visit.patient.full_name}',
            description=f'Lab result entered for {order.test_name} ({order.visit.patient.full_name})',
            request=request,
        )
        messages.success(request, f'Result entered for "{order.test_name}". Awaiting release.')
    else:
        messages.error(request, 'Result text cannot be empty.')
    return redirect('lab_order_detail', order_id=order_id)


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
    section_f = request.GET.get('section', '')
    active_f  = request.GET.get('active', '')
    search    = request.GET.get('q', '').strip()

    qs = LabService.objects.all()
    if section_f:
        qs = qs.filter(section=section_f)
    if active_f == '1':
        qs = qs.filter(is_active=True)
    elif active_f == '0':
        qs = qs.filter(is_active=False)
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search) | Q(category__icontains=search))

    paginator = Paginator(qs, 30)
    page_obj  = paginator.get_page(request.GET.get('page'))

    section_counts = (
        LabService.objects.values('section')
        .annotate(count=Count('id'))
        .order_by('section')
    )

    return render(request, 'lab/service_list.html', {
        'page_obj':       page_obj,
        'section_f':      section_f,
        'active_f':       active_f,
        'search':         search,
        'sections':       LabService.Section.choices,
        'section_counts': section_counts,
    })


@hms_permission_required('core.manage_lab_services')
def lab_service_create(request):
    if request.method == 'POST':
        form = LabServiceForm(request.POST)
        if form.is_valid():
            svc = form.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.LABORATORY,
                object_type='LabService', object_id=svc.pk,
                object_repr=str(svc),
                description=f'Lab service created: {svc.name} [{svc.code}]',
                request=request,
            )
            messages.success(request, f'Lab service "{svc.name}" created.')
            return redirect('lab_service_list')
    else:
        form = LabServiceForm()
    return render(request, 'lab/service_form.html', {'form': form, 'action': 'Create'})


@hms_permission_required('core.manage_lab_services')
def lab_service_edit(request, pk):
    svc = get_object_or_404(LabService, pk=pk)
    old_price = svc.standard_price
    if request.method == 'POST':
        form = LabServiceForm(request.POST, instance=svc)
        if form.is_valid():
            svc = form.save()
            desc = f'Lab service updated: {svc.name} [{svc.code}]'
            if old_price != svc.standard_price:
                desc += f' (price: {old_price} → {svc.standard_price})'
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.LABORATORY,
                object_type='LabService', object_id=svc.pk,
                object_repr=str(svc),
                description=desc,
                request=request,
            )
            messages.success(request, f'Lab service "{svc.name}" updated.')
            return redirect('lab_service_detail', pk=svc.pk)
    else:
        form = LabServiceForm(instance=svc)
    return render(request, 'lab/service_form.html', {'form': form, 'svc': svc, 'action': 'Edit'})


@hms_permission_required('core.manage_lab_services')
def lab_service_detail(request, pk):
    svc = get_object_or_404(LabService, pk=pk)
    recent_orders = (
        LabOrder.objects.filter(lab_service=svc)
        .select_related('visit__patient')
        .order_by('-ordered_at')[:10]
    )
    stats = LabOrder.objects.filter(lab_service=svc).aggregate(
        total=Count('id'),
        released=Count('id', filter=Q(status__in=[LabOrder.Status.RELEASED, LabOrder.Status.COMPLETED])),
        revenue=Sum('unit_price', filter=Q(payment_status=LabOrder.PaymentStatus.PAID)),
    )
    return render(request, 'lab/service_detail.html', {
        'svc': svc, 'recent_orders': recent_orders, 'stats': stats,
    })
