from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import AuditLog, ImagingOrder


# ── Radiology Dashboard ───────────────────────────────────────────────────────

@hms_permission_required('core.read_imaging_request')
def radiology_dashboard(request):
    today = timezone.localdate()
    type_filter = request.GET.get('type', 'all')
    status_filter = request.GET.get('status', 'all')
    search = request.GET.get('q', '').strip()

    qs = (
        ImagingOrder.objects
        .select_related('visit__patient', 'visit__department', 'ordered_by')
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

    # Stats — pending counts by imaging type
    all_orders = ImagingOrder.objects.all()
    pending_qs = all_orders.filter(status='Pending')
    xray_count = pending_qs.filter(imaging_type='X-Ray').count()
    ct_count = pending_qs.filter(imaging_type='CT Scan').count()
    mri_count = pending_qs.filter(imaging_type='MRI').count()
    us_count = pending_qs.filter(imaging_type='Ultrasound').count()
    total_pending = pending_qs.count()
    in_progress = all_orders.filter(status='In Progress').count()
    completed_today = all_orders.filter(status='Completed', reported_at__date=today).count()
    stat_pending = all_orders.filter(status__in=['Pending', 'In Progress'], priority='STAT').count()

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    imaging_types = ImagingOrder.ImagingType.choices
    status_choices = ImagingOrder.Status.choices

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
    new_status = request.POST.get('status', '').strip()
    valid_statuses = [s for s, _ in ImagingOrder.Status.choices]
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


# URL_PATTERNS_TO_ADD (radiology):
# path('radiology/', views_radiology.radiology_dashboard, name='radiology_dashboard'),
# path('radiology/order/<int:order_id>/', views_radiology.imaging_order_detail, name='imaging_order_detail'),
# path('radiology/order/<int:order_id>/report/', views_radiology.imaging_report_enter, name='imaging_report_enter'),
# path('radiology/order/<int:order_id>/status/', views_radiology.imaging_order_update_status, name='imaging_order_update_status'),
