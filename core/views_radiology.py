from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .forms import ImagingServiceForm
from .models import AuditLog, ImagingOrder, ImagingService


# ── Radiology Dashboard ───────────────────────────────────────────────────────

@hms_permission_required('core.read_imaging_request')
def radiology_dashboard(request):
    today = timezone.localdate()
    type_filter = request.GET.get('type', 'all')
    status_filter = request.GET.get('status', 'all')
    search = request.GET.get('q', '').strip()

    # Unpaid orders belong to Billing, not the radiology processing queue: an
    # imaging order only reaches this queue once payment has cleared (or been
    # credit-approved / waived), mirroring the lab billing workflow.
    qs = (
        ImagingOrder.objects
        .select_related('visit__patient', 'visit__department', 'ordered_by')
        .exclude(status=ImagingOrder.Status.WAITING_PAYMENT)
        .order_by('-ordered_at')
    )

    if type_filter != 'all':
        qs = qs.filter(imaging_type=type_filter)

    if status_filter != 'all':
        qs = qs.filter(status=status_filter)

    if search:
        qs = qs.filter(
            Q(visit__patient__first_name__icontains=search)
            | Q(visit__patient__last_name__icontains=search)
            | Q(visit__patient__card_number__icontains=search)
            | Q(body_part__icontains=search)
            | Q(imaging_type__icontains=search)
        )

    # Stats — pending counts by imaging type (queue-scoped, i.e. paid only)
    queue_orders = ImagingOrder.objects.exclude(status=ImagingOrder.Status.WAITING_PAYMENT)
    pending_qs = queue_orders.filter(status='Pending')
    xray_count = pending_qs.filter(imaging_type='X-Ray').count()
    ct_count = pending_qs.filter(imaging_type='CT Scan').count()
    mri_count = pending_qs.filter(imaging_type='MRI').count()
    us_count = pending_qs.filter(imaging_type='Ultrasound').count()
    total_pending = pending_qs.count()
    in_progress = queue_orders.filter(status='In Progress').count()
    completed_today = queue_orders.filter(status='Completed', reported_at__date=today).count()
    stat_pending = queue_orders.filter(status__in=['Pending', 'In Progress'], priority='STAT').count()

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    imaging_types = ImagingOrder.ImagingType.choices
    status_choices = [
        c for c in ImagingOrder.Status.choices if c[0] != ImagingOrder.Status.WAITING_PAYMENT
    ]

    return render(request, 'radiology/dashboard.html', {
        'page_obj': page_obj,
        'type_filter': type_filter,
        'status_filter': status_filter,
        'search': search,
        'xray_count': xray_count,
        'ct_count': ct_count,
        'mri_count': mri_count,
        'us_count': us_count,
        'total_pending': total_pending,
        'in_progress': in_progress,
        'completed_today': completed_today,
        'stat_pending': stat_pending,
        'imaging_types': imaging_types,
        'status_choices': status_choices,
        'today': today,
    })


# ── Imaging Order Detail ──────────────────────────────────────────────────────

@hms_permission_required('core.read_imaging_request')
def imaging_order_detail(request, order_id):
    order = get_object_or_404(
        ImagingOrder.objects.select_related(
            'visit__patient', 'visit__department', 'visit__doctor', 'ordered_by'
        ),
        pk=order_id,
    )
    can_process = request.user.has_perm('core.process_imaging')
    status_choices = ImagingOrder.Status.choices
    return render(request, 'radiology/order_detail.html', {
        'order': order,
        'can_process': can_process,
        'status_choices': status_choices,
    })


# ── Enter Imaging Report ──────────────────────────────────────────────────────

@hms_permission_required('core.process_imaging')
@require_POST
def imaging_report_enter(request, order_id):
    order = get_object_or_404(ImagingOrder, pk=order_id)
    if not order.payment_cleared:
        messages.error(request, 'Payment must be cleared before entering a report.')
        return redirect('imaging_order_detail', order_id=order_id)
    report_text = request.POST.get('report', '').strip()
    if report_text:
        order.report = report_text
        order.status = ImagingOrder.Status.COMPLETED
        order.reported_at = timezone.now()
        order.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.RADIOLOGY,
            object_type='ImagingOrder', object_id=order.pk,
            object_repr=f'{order.imaging_type} — {order.visit.patient.full_name}',
            description=f'Imaging report entered for {order.imaging_type} ({order.visit.patient.full_name})',
            request=request,
        )
        messages.success(request, f'Report entered for "{order.imaging_type} — {order.body_part}".')
    else:
        messages.error(request, 'Report text cannot be empty.')
    return redirect('radiology_dashboard')


# ── Update Imaging Order Status ───────────────────────────────────────────────

@hms_permission_required('core.process_imaging')
@require_POST
def imaging_order_update_status(request, order_id):
    order = get_object_or_404(ImagingOrder, pk=order_id)
    if not order.payment_cleared:
        messages.error(request, 'Payment must be cleared before processing this order.')
        return redirect('imaging_order_detail', order_id=order_id)
    new_status = request.POST.get('status', '').strip()
    valid_statuses = [s for s, _ in ImagingOrder.Status.choices if s != ImagingOrder.Status.WAITING_PAYMENT]
    if new_status in valid_statuses:
        order.status = new_status
        if new_status == ImagingOrder.Status.COMPLETED and not order.reported_at:
            order.reported_at = timezone.now()
        order.save()
        messages.success(request, f'Order status updated to "{new_status}".')
    else:
        messages.error(request, 'Invalid status value.')
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'radiology_dashboard'
    if next_url.startswith('http'):
        return redirect(next_url)
    return redirect('radiology_dashboard')


# ─────────────────────────────────────────────────────────────────────────────
# Imaging Service Management
# ─────────────────────────────────────────────────────────────────────────────

@hms_permission_required('core.manage_imaging_services')
def imaging_service_list(request):
    modality_f = request.GET.get('modality', '')
    active_f = request.GET.get('active', '')
    search = request.GET.get('q', '').strip()

    qs = ImagingService.objects.all()
    if modality_f:
        qs = qs.filter(modality=modality_f)
    if active_f == '1':
        qs = qs.filter(is_active=True)
    elif active_f == '0':
        qs = qs.filter(is_active=False)
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search) | Q(keywords__icontains=search))

    paginator = Paginator(qs, 30)
    page_obj = paginator.get_page(request.GET.get('page'))

    modality_counts = (
        ImagingService.objects.values('modality')
        .annotate(count=Count('id'))
        .order_by('modality')
    )

    return render(request, 'radiology/service_list.html', {
        'page_obj': page_obj,
        'modality_f': modality_f,
        'active_f': active_f,
        'search': search,
        'modalities': ImagingOrder.ImagingType.choices,
        'modality_counts': modality_counts,
    })


@hms_permission_required('core.manage_imaging_services')
def imaging_service_create(request):
    if request.method == 'POST':
        form = ImagingServiceForm(request.POST)
        if form.is_valid():
            svc = form.save(commit=False)
            svc.created_by = request.user
            svc.save()
            log_action(
                request.user, AuditLog.Action.CREATE, AuditLog.Module.RADIOLOGY,
                object_type='ImagingService', object_id=svc.pk,
                object_repr=str(svc),
                description=f'Imaging service created: {svc.name} [{svc.code}] ETB {svc.standard_price}',
                request=request,
            )
            messages.success(request, f'Imaging service "{svc.name}" created successfully.')
            return redirect('imaging_service_detail', pk=svc.pk)
    else:
        form = ImagingServiceForm()
    return render(request, 'radiology/service_form.html', {'form': form, 'action': 'Create'})


@hms_permission_required('core.manage_imaging_services')
def imaging_service_edit(request, pk):
    svc = get_object_or_404(ImagingService, pk=pk)
    if request.method == 'POST':
        form = ImagingServiceForm(request.POST, instance=svc)
        if form.is_valid():
            svc = form.save()
            log_action(
                request.user, AuditLog.Action.UPDATE, AuditLog.Module.RADIOLOGY,
                object_type='ImagingService', object_id=svc.pk,
                object_repr=str(svc),
                description=f'Imaging service updated: {svc.name} [{svc.code}]',
                request=request,
            )
            messages.success(request, f'Imaging service "{svc.name}" updated.')
            return redirect('imaging_service_detail', pk=svc.pk)
    else:
        form = ImagingServiceForm(instance=svc)
    return render(request, 'radiology/service_form.html', {'form': form, 'svc': svc, 'action': 'Edit'})


@hms_permission_required('core.manage_imaging_services')
def imaging_service_toggle(request, pk):
    """Activate / deactivate an imaging service (POST only)."""
    svc = get_object_or_404(ImagingService, pk=pk)
    if request.method == 'POST':
        svc.is_active = not svc.is_active
        svc.save(update_fields=['is_active'])
        state = 'activated' if svc.is_active else 'deactivated'
        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.RADIOLOGY,
            object_type='ImagingService', object_id=svc.pk,
            object_repr=str(svc),
            description=f'Imaging service {state}: {svc.name}',
            request=request,
        )
        messages.success(request, f'"{svc.name}" has been {state}.')
    return redirect('imaging_service_detail', pk=pk)


@hms_permission_required('core.manage_imaging_services')
def imaging_service_detail(request, pk):
    svc = get_object_or_404(ImagingService, pk=pk)
    recent_orders = (
        ImagingOrder.objects.filter(imaging_service=svc)
        .select_related('visit__patient')
        .order_by('-ordered_at')[:15]
    )
    stats = ImagingOrder.objects.filter(imaging_service=svc).aggregate(
        total=Count('id'),
        completed=Count('id', filter=Q(status=ImagingOrder.Status.COMPLETED)),
        revenue=Sum('unit_price', filter=Q(payment_status=ImagingOrder.PaymentStatus.PAID)),
    )
    return render(request, 'radiology/service_detail.html', {
        'svc': svc,
        'recent_orders': recent_orders,
        'stats': stats,
    })


# ─────────────────────────────────────────────────────────────────────────────
# AJAX — Fast imaging service search (used by doctor ordering form)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def imaging_service_search_api(request):
    """Return JSON list of matching active imaging services for AJAX autocomplete."""
    q = request.GET.get('q', '').strip()
    if not q or len(q) < 1:
        return JsonResponse({'results': []})

    qs = ImagingService.objects.filter(is_active=True).filter(
        Q(name__icontains=q) |
        Q(short_name__icontains=q) |
        Q(code__icontains=q) |
        Q(keywords__icontains=q) |
        Q(modality__icontains=q) |
        Q(body_part__icontains=q)
    ).values(
        'id', 'name', 'short_name', 'code', 'modality', 'body_part',
        'standard_price', 'emergency_price', 'turnaround_hours',
    ).order_by('modality', 'name')[:25]

    results = []
    for svc in qs:
        results.append({
            'id':              svc['id'],
            'name':            svc['name'],
            'short_name':      svc['short_name'] or '',
            'code':            svc['code'],
            'modality':        svc['modality'],
            'body_part':       svc['body_part'] or '',
            'price':           str(svc['standard_price']),
            'emergency_price': str(svc['emergency_price']) if svc['emergency_price'] else str(svc['standard_price']),
            'turnaround_hours': svc['turnaround_hours'],
        })
    return JsonResponse({'results': results})
