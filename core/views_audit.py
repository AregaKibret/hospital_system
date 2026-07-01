from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

from .decorators import hms_permission_required
from .models import AuditLog, Department

User = get_user_model()

# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_filters(request):
    """Extract common filter parameters from GET."""
    return {
        'q':          request.GET.get('q', '').strip(),
        'user_id':    request.GET.get('user_id', ''),
        'module':     request.GET.get('module', ''),
        'action':     request.GET.get('action', ''),
        'severity':   request.GET.get('severity', ''),
        'date_from':  request.GET.get('date_from', ''),
        'date_to':    request.GET.get('date_to', ''),
        'department': request.GET.get('department', ''),
    }


def _apply_filters(qs, filters):
    if filters['q']:
        qs = qs.filter(
            Q(user_name__icontains=filters['q']) |
            Q(object_repr__icontains=filters['q']) |
            Q(description__icontains=filters['q']) |
            Q(ip_address__icontains=filters['q'])
        )
    if filters['user_id']:
        qs = qs.filter(user_id=filters['user_id'])
    if filters['module']:
        qs = qs.filter(module=filters['module'])
    if filters['action']:
        qs = qs.filter(action=filters['action'])
    if filters['severity']:
        qs = qs.filter(severity=filters['severity'])
    if filters['department']:
        qs = qs.filter(department=filters['department'])
    if filters['date_from']:
        try:
            from django.utils.dateparse import parse_date
            d = parse_date(filters['date_from'])
            if d:
                qs = qs.filter(timestamp__date__gte=d)
        except Exception:
            pass
    if filters['date_to']:
        try:
            from django.utils.dateparse import parse_date
            d = parse_date(filters['date_to'])
            if d:
                qs = qs.filter(timestamp__date__lte=d)
        except Exception:
            pass
    return qs


# ── views ────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_audit_log')
def audit_dashboard(request):
    now   = timezone.now()
    today = now.date()
    week_ago = now - timedelta(days=7)

    total_today   = AuditLog.objects.filter(timestamp__date=today).count()
    failed_logins = AuditLog.objects.filter(
        timestamp__gte=week_ago, action=AuditLog.Action.LOGIN_FAILED
    ).count()
    critical_week = AuditLog.objects.filter(
        timestamp__gte=week_ago, severity=AuditLog.Severity.CRITICAL
    ).count()
    total_week    = AuditLog.objects.filter(timestamp__gte=week_ago).count()

    # Failed logins in last 24 hours — potential security concern
    suspicious = AuditLog.objects.filter(
        action=AuditLog.Action.LOGIN_FAILED,
        timestamp__gte=now - timedelta(hours=24),
    ).order_by('-timestamp')[:10]

    # Recent logs
    recent = AuditLog.objects.select_related('user').order_by('-timestamp')[:20]

    # By-module breakdown for the week
    module_stats = (
        AuditLog.objects.filter(timestamp__gte=week_ago)
        .values('module')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )

    # Severity breakdown today
    severity_stats = (
        AuditLog.objects.filter(timestamp__date=today)
        .values('severity')
        .annotate(count=Count('id'))
    )

    context = {
        'total_today':   total_today,
        'failed_logins': failed_logins,
        'critical_week': critical_week,
        'total_week':    total_week,
        'suspicious':    suspicious,
        'recent':        recent,
        'module_stats':  module_stats,
        'severity_stats': severity_stats,
        'module_choices':   AuditLog.Module.choices,
        'action_choices':   AuditLog.Action.choices,
        'severity_choices': AuditLog.Severity.choices,
    }
    return render(request, 'audit/dashboard.html', context)


@hms_permission_required('core.read_audit_log')
def audit_log_list(request):
    filters = _parse_filters(request)
    qs = AuditLog.objects.select_related('user').order_by('-timestamp')
    qs = _apply_filters(qs, filters)

    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(qs, 50)
    page_num  = request.GET.get('page', 1)
    page_obj  = paginator.get_page(page_num)

    users       = User.objects.filter(is_active=True).order_by('first_name', 'username')
    departments = AuditLog.objects.values_list('department', flat=True).distinct().order_by('department')

    context = {
        'page_obj':         page_obj,
        'filters':          filters,
        'users':            users,
        'departments':      [d for d in departments if d],
        'module_choices':   AuditLog.Module.choices,
        'action_choices':   AuditLog.Action.choices,
        'severity_choices': AuditLog.Severity.choices,
        'total_count':      qs.count(),
    }
    return render(request, 'audit/log_list.html', context)


@hms_permission_required('core.read_audit_log')
def audit_log_detail(request, log_id):
    from django.shortcuts import get_object_or_404
    log = get_object_or_404(AuditLog, pk=log_id)
    return render(request, 'audit/log_detail.html', {'log': log})


@hms_permission_required('core.read_audit_log')
def report_user_activity(request):
    filters = _parse_filters(request)
    now = timezone.now()
    date_from = filters['date_from'] or (now - timedelta(days=30)).strftime('%Y-%m-%d')
    date_to   = filters['date_to']   or now.strftime('%Y-%m-%d')

    qs = AuditLog.objects.all()
    if filters['user_id']:
        qs = qs.filter(user_id=filters['user_id'])
    try:
        from django.utils.dateparse import parse_date
        df = parse_date(date_from)
        dt = parse_date(date_to)
        if df:
            qs = qs.filter(timestamp__date__gte=df)
        if dt:
            qs = qs.filter(timestamp__date__lte=dt)
    except Exception:
        pass

    login_logs = qs.filter(module=AuditLog.Module.AUTH).order_by('-timestamp')[:100]

    # Per-user action counts
    user_stats = (
        qs.values('user_name', 'user_role', 'department')
        .annotate(
            total=Count('id'),
            logins=Count('id', filter=Q(action=AuditLog.Action.LOGIN)),
            failed=Count('id', filter=Q(action=AuditLog.Action.LOGIN_FAILED)),
            creates=Count('id', filter=Q(action=AuditLog.Action.CREATE)),
            updates=Count('id', filter=Q(action=AuditLog.Action.UPDATE)),
        )
        .order_by('-total')[:50]
    )

    users = User.objects.filter(is_active=True).order_by('first_name', 'username')

    context = {
        'login_logs':  login_logs,
        'user_stats':  user_stats,
        'filters':     filters,
        'users':       users,
        'date_from':   date_from,
        'date_to':     date_to,
    }
    return render(request, 'audit/report_user_activity.html', context)


@hms_permission_required('core.read_audit_log')
def report_financial_audit(request):
    filters = _parse_filters(request)
    now = timezone.now()
    date_from = filters['date_from'] or (now - timedelta(days=30)).strftime('%Y-%m-%d')
    date_to   = filters['date_to']   or now.strftime('%Y-%m-%d')

    fin_modules = [AuditLog.Module.BILLING, AuditLog.Module.PAYMENT, AuditLog.Module.FINANCE]
    qs = AuditLog.objects.filter(module__in=fin_modules).order_by('-timestamp')

    try:
        from django.utils.dateparse import parse_date
        df = parse_date(date_from)
        dt = parse_date(date_to)
        if df:
            qs = qs.filter(timestamp__date__gte=df)
        if dt:
            qs = qs.filter(timestamp__date__lte=dt)
    except Exception:
        pass

    if filters['action']:
        qs = qs.filter(action=filters['action'])
    if filters['user_id']:
        qs = qs.filter(user_id=filters['user_id'])

    # Counts by action
    action_stats = (
        qs.values('action')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    users = User.objects.filter(is_active=True).order_by('first_name', 'username')
    fin_actions = [
        AuditLog.Action.CREATE, AuditLog.Action.UPDATE, AuditLog.Action.CANCEL,
        AuditLog.Action.WAIVE, AuditLog.Action.PAYMENT, AuditLog.Action.REFUND,
        AuditLog.Action.APPROVE, AuditLog.Action.ISSUE,
        AuditLog.Action.OPEN_SESSION, AuditLog.Action.CLOSE_SESSION,
    ]

    from django.core.paginator import Paginator
    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    context = {
        'page_obj':     page_obj,
        'action_stats': action_stats,
        'filters':      filters,
        'users':        users,
        'date_from':    date_from,
        'date_to':      date_to,
        'fin_actions':  [(a.value, a.label) for a in AuditLog.Action if a in fin_actions],
    }
    return render(request, 'audit/report_financial.html', context)


@hms_permission_required('core.read_audit_log')
def report_inventory_audit(request):
    filters = _parse_filters(request)
    now = timezone.now()
    date_from = filters['date_from'] or (now - timedelta(days=30)).strftime('%Y-%m-%d')
    date_to   = filters['date_to']   or now.strftime('%Y-%m-%d')

    inv_modules = [
        AuditLog.Module.PHARMACY, AuditLog.Module.MED_INVENTORY,
        AuditLog.Module.DEPT_PHARMACY, AuditLog.Module.INVENTORY,
    ]
    qs = AuditLog.objects.filter(module__in=inv_modules).order_by('-timestamp')

    try:
        from django.utils.dateparse import parse_date
        df = parse_date(date_from)
        dt = parse_date(date_to)
        if df:
            qs = qs.filter(timestamp__date__gte=df)
        if dt:
            qs = qs.filter(timestamp__date__lte=dt)
    except Exception:
        pass

    if filters['module']:
        qs = qs.filter(module=filters['module'])
    if filters['action']:
        qs = qs.filter(action=filters['action'])
    if filters['user_id']:
        qs = qs.filter(user_id=filters['user_id'])

    action_stats = (
        qs.values('action', 'module')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    users = User.objects.filter(is_active=True).order_by('first_name', 'username')
    inv_action_choices = [
        (a, l) for a, l in AuditLog.Action.choices
        if a in ('receive', 'issue', 'transfer', 'adjust', 'dispose', 'return',
                 'create', 'update', 'delete', 'dispense')
    ]
    inv_module_choices = [(m, l) for m, l in AuditLog.Module.choices
                          if m in ('pharmacy', 'med_inventory', 'dept_pharmacy', 'inventory')]

    from django.core.paginator import Paginator
    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    context = {
        'page_obj':          page_obj,
        'action_stats':      action_stats,
        'filters':           filters,
        'users':             users,
        'date_from':         date_from,
        'date_to':           date_to,
        'inv_action_choices': inv_action_choices,
        'inv_module_choices': inv_module_choices,
    }
    return render(request, 'audit/report_inventory.html', context)


@hms_permission_required('core.read_audit_log')
def report_patient_access(request):
    filters = _parse_filters(request)
    now = timezone.now()
    date_from = filters['date_from'] or (now - timedelta(days=30)).strftime('%Y-%m-%d')
    date_to   = filters['date_to']   or now.strftime('%Y-%m-%d')

    clinical_modules = [
        AuditLog.Module.PATIENT, AuditLog.Module.VISIT,
        AuditLog.Module.DOCTOR, AuditLog.Module.LABORATORY,
        AuditLog.Module.RADIOLOGY, AuditLog.Module.NURSING,
        AuditLog.Module.ANESTHESIA,
    ]
    qs = AuditLog.objects.filter(module__in=clinical_modules).order_by('-timestamp')

    try:
        from django.utils.dateparse import parse_date
        df = parse_date(date_from)
        dt = parse_date(date_to)
        if df:
            qs = qs.filter(timestamp__date__gte=df)
        if dt:
            qs = qs.filter(timestamp__date__lte=dt)
    except Exception:
        pass

    if filters['user_id']:
        qs = qs.filter(user_id=filters['user_id'])
    if filters['module']:
        qs = qs.filter(module=filters['module'])
    if filters['q']:
        qs = qs.filter(
            Q(object_repr__icontains=filters['q']) |
            Q(description__icontains=filters['q']) |
            Q(user_name__icontains=filters['q'])
        )

    # Access events specifically
    access_logs = qs.filter(action=AuditLog.Action.VIEW).order_by('-timestamp')

    # Who accessed patient records most
    top_accessors = (
        qs.filter(action=AuditLog.Action.VIEW)
        .values('user_name', 'user_role', 'department')
        .annotate(count=Count('id'))
        .order_by('-count')[:20]
    )

    users = User.objects.filter(is_active=True).order_by('first_name', 'username')
    clinical_module_choices = [(m, l) for m, l in AuditLog.Module.choices if m in
                               ('patient', 'visit', 'doctor', 'laboratory', 'radiology', 'nursing', 'anesthesia')]

    from django.core.paginator import Paginator
    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    context = {
        'page_obj':                page_obj,
        'access_logs':             access_logs[:20],
        'top_accessors':           top_accessors,
        'filters':                 filters,
        'users':                   users,
        'date_from':               date_from,
        'date_to':                 date_to,
        'clinical_module_choices': clinical_module_choices,
    }
    return render(request, 'audit/report_patient_access.html', context)
