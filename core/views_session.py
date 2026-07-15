"""Session management, active user monitoring, and session reports."""
import csv
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import AuditLog, UserSession

User = get_user_model()


# ── Active Session Dashboard ──────────────────────────────────────────────────

@hms_permission_required('core.manage_users')
def session_dashboard(request):
    """Show all currently active user sessions."""
    active = (
        UserSession.objects
        .filter(is_active=True)
        .select_related('user', 'user__profile')
        .order_by('-last_activity')
    )
    cutoff_2h = timezone.now() - timedelta(hours=2)
    recent = (
        UserSession.objects
        .filter(login_at__gte=cutoff_2h)
        .select_related('user', 'user__profile')
        .order_by('-login_at')[:50]
    )
    return render(request, 'session/dashboard.html', {
        'active_sessions': active,
        'active_count': active.count(),
        'recent_sessions': recent,
        'now': timezone.now(),
    })


@hms_permission_required('core.manage_users')
@require_POST
def force_logout(request, session_id):
    """Force a user session to expire."""
    sess = get_object_or_404(UserSession, pk=session_id, is_active=True)
    target_user = sess.user

    # Invalidate Django session
    try:
        django_session = Session.objects.get(session_key=sess.session_key)
        django_session.delete()
    except Session.DoesNotExist:
        pass

    sess.is_active = False
    sess.logout_at = timezone.now()
    sess.logout_type = UserSession.LogoutType.FORCED
    sess.forced_by = request.user
    sess.save(update_fields=['is_active', 'logout_at', 'logout_type', 'forced_by'])

    log_action(
        request.user, AuditLog.Action.UPDATE, AuditLog.Module.AUTH,
        object_type='UserSession', object_id=sess.pk,
        object_repr=target_user.username,
        description=f'Force logout: {target_user.get_full_name() or target_user.username} session terminated by {request.user.username}',
        request=request,
    )
    messages.success(request, f'{target_user.get_full_name() or target_user.username} has been logged out.')
    return redirect('session_dashboard')


# ── Session Reports ───────────────────────────────────────────────────────────

@hms_permission_required('core.manage_users')
def report_login_history(request):
    """Login history with filters."""
    import datetime
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today - timedelta(days=7)))
    date_to_str   = request.GET.get('date_to',   str(today))
    user_q        = request.GET.get('user', '').strip()
    try:
        date_from = datetime.date.fromisoformat(date_from_str)
        date_to   = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_from = today - timedelta(days=7)
        date_to   = today

    qs = (
        UserSession.objects
        .filter(login_at__date__gte=date_from, login_at__date__lte=date_to)
        .select_related('user', 'user__profile', 'forced_by')
        .order_by('-login_at')
    )
    if user_q:
        qs = qs.filter(
            Q(user__username__icontains=user_q) |
            Q(user__first_name__icontains=user_q) |
            Q(user__last_name__icontains=user_q)
        )

    if request.GET.get('export') == 'csv':
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="login_history_{date_from}_to_{date_to}.csv"'
        w = csv.writer(resp)
        w.writerow(['User', 'Role', 'IP Address', 'Browser', 'Device', 'Login Time', 'Logout Time', 'Duration', 'Logout Type'])
        for s in qs:
            role = s.user.groups.first().name if s.user.groups.exists() else ''
            dur = str(s.duration).split('.')[0] if s.logout_at else 'Active'
            w.writerow([
                s.user.get_full_name() or s.user.username, role,
                s.ip_address, s.browser, s.device_type,
                s.login_at.strftime('%d %b %Y %H:%M'),
                s.logout_at.strftime('%d %b %Y %H:%M') if s.logout_at else '',
                dur, s.logout_type,
            ])
        return resp

    return render(request, 'session/login_report.html', {
        'sessions': qs[:200],
        'date_from': date_from, 'date_to': date_to,
        'date_from_str': date_from_str, 'date_to_str': date_to_str,
        'user_q': user_q,
        'total': qs.count(),
    })


@hms_permission_required('core.manage_users')
def report_security(request):
    """Security report: failed logins, forced logouts, suspicious activity."""
    import datetime
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today - timedelta(days=7)))
    date_to_str   = request.GET.get('date_to',   str(today))
    try:
        date_from = datetime.date.fromisoformat(date_from_str)
        date_to   = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_from = today - timedelta(days=7)
        date_to   = today

    failed_logins = AuditLog.objects.filter(
        action=AuditLog.Action.LOGIN_FAILED,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    ).order_by('-created_at')[:100]

    forced_logouts = UserSession.objects.filter(
        logout_type=UserSession.LogoutType.FORCED,
        logout_at__date__gte=date_from,
        logout_at__date__lte=date_to,
    ).select_related('user', 'forced_by').order_by('-logout_at')[:50]

    timed_out = UserSession.objects.filter(
        logout_type=UserSession.LogoutType.TIMEOUT,
        logout_at__date__gte=date_from,
        logout_at__date__lte=date_to,
    ).count()

    return render(request, 'session/security_report.html', {
        'failed_logins': failed_logins,
        'failed_count': failed_logins.count(),
        'forced_logouts': forced_logouts,
        'timed_out': timed_out,
        'date_from': date_from, 'date_to': date_to,
        'date_from_str': date_from_str, 'date_to_str': date_to_str,
    })


@hms_permission_required('core.manage_users')
def report_user_activity(request):
    """User activity from AuditLog with filters."""
    import datetime
    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', str(today))
    date_to_str   = request.GET.get('date_to',   str(today))
    user_q        = request.GET.get('user', '').strip()
    module_f      = request.GET.get('module', '')
    try:
        date_from = datetime.date.fromisoformat(date_from_str)
        date_to   = datetime.date.fromisoformat(date_to_str)
    except (ValueError, TypeError):
        date_from = date_to = today

    qs = (
        AuditLog.objects
        .filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
        .select_related('user')
        .order_by('-created_at')
    )
    if user_q:
        qs = qs.filter(
            Q(user__username__icontains=user_q) |
            Q(user__first_name__icontains=user_q) |
            Q(user__last_name__icontains=user_q)
        )
    if module_f:
        qs = qs.filter(module=module_f)

    return render(request, 'session/activity_report.html', {
        'logs': qs[:300],
        'total': qs.count(),
        'date_from': date_from, 'date_to': date_to,
        'date_from_str': date_from_str, 'date_to_str': date_to_str,
        'user_q': user_q,
        'module_f': module_f,
        'modules': AuditLog.Module.choices,
    })


# ── Notifications ─────────────────────────────────────────────────────────────

from .models import Notification


@login_required
def notification_list(request):
    notifs = (
        Notification.objects
        .filter(recipient=request.user)
        .select_related('sender')
        .order_by('-created_at')[:100]
    )
    # Mark all as read when viewing the list
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return render(request, 'session/notifications.html', {
        'notifications': notifs,
    })


def notifications_unread_count(request):
    """AJAX endpoint — returns unread count as JSON. Requires login."""
    if not request.user.is_authenticated:
        return JsonResponse({'count': 0})
    count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    recent = list(
        Notification.objects
        .filter(recipient=request.user, is_read=False)
        .values('id', 'title', 'message', 'notif_type', 'url', 'created_at')
        .order_by('-created_at')[:5]
    )
    for n in recent:
        n['created_at'] = n['created_at'].strftime('%d %b %H:%M')
    return JsonResponse({'count': count, 'notifications': recent})


@require_POST
def notification_mark_read(request, notif_id):
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False})
    Notification.objects.filter(pk=notif_id, recipient=request.user).update(is_read=True)
    return JsonResponse({'ok': True})


@require_POST
def notification_mark_all_read(request):
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False})
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True})
    return redirect('notification_list')
