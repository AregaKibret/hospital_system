"""
Central audit logging utility.
Usage:
    from core.audit import log_action
    from core.models import AuditLog

    log_action(
        request.user, AuditLog.Action.CREATE, AuditLog.Module.BILLING,
        object_type='Invoice', object_id=inv.id, object_repr=str(inv),
        description='Invoice created for patient ABC',
        extra_data={'total': str(inv.total_amount)},
        request=request,
    )
"""
import threading

_local = threading.local()


def set_current_request(request):
    _local.request = request


def get_current_request():
    return getattr(_local, 'request', None)


def get_client_ip(request):
    if not request:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _user_context(user):
    """Extract name / role / department from a user object."""
    if user is None:
        return 'System', '', ''
    if not hasattr(user, 'pk') or not user.is_authenticated:
        return str(user), '', ''

    user_name = user.get_full_name() or user.username
    user_role = ''
    department = ''

    primary_group = user.groups.first()
    if primary_group:
        user_role = primary_group.name

    try:
        profile = user.userprofile
        if profile.department:
            department = profile.department.name
    except Exception:
        pass

    return user_name, user_role, department


def log_action(
    user,
    action,
    module,
    *,
    object_type='',
    object_id='',
    object_repr='',
    changes=None,
    extra_data=None,
    description='',
    severity=None,
    request=None,
):
    """Create an AuditLog entry. Never raises — failures are silently ignored so
    they never interrupt a user-facing request."""
    try:
        from core.models import AuditLog  # local import to avoid circular refs

        if request is None:
            request = get_current_request()

        if severity is None:
            severity = _default_severity(action)

        user_name, user_role, department = _user_context(user)

        ip_address = get_client_ip(request)
        user_agent = ''
        if request:
            user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]

        AuditLog.objects.create(
            user=user if (user and hasattr(user, 'pk') and user.is_authenticated) else None,
            user_name=user_name,
            user_role=user_role,
            department=department,
            action=action,
            module=module,
            object_type=object_type,
            object_id=str(object_id) if object_id else '',
            object_repr=str(object_repr)[:500] if object_repr else '',
            changes=changes,
            extra_data=extra_data,
            description=str(description)[:500] if description else '',
            ip_address=ip_address,
            user_agent=user_agent,
            severity=severity,
        )
    except Exception:
        pass


def _default_severity(action):
    from core.models import AuditLog
    critical_actions = {
        AuditLog.Action.LOGIN_FAILED,
        AuditLog.Action.DELETE,
        AuditLog.Action.DISPOSE,
        AuditLog.Action.CANCEL,
        AuditLog.Action.REFUND,
        AuditLog.Action.DEACTIVATE,
        AuditLog.Action.PASSWORD_CHANGE,
    }
    warning_actions = {
        AuditLog.Action.ADJUST,
        AuditLog.Action.WAIVE,
        AuditLog.Action.APPROVE,
        AuditLog.Action.REJECT,
    }
    if action in critical_actions:
        return AuditLog.Severity.CRITICAL
    if action in warning_actions:
        return AuditLog.Severity.WARNING
    return AuditLog.Severity.INFO


def build_changes(old_obj, new_obj, fields):
    """Compare field values on two objects and return a changes dict.

    fields: list of field names to compare.
    Returns: {field_name: {'old': old_val, 'new': new_val}} for changed fields only.
    """
    changes = {}
    for field in fields:
        old_val = getattr(old_obj, field, None)
        new_val = getattr(new_obj, field, None)
        if str(old_val) != str(new_val):
            changes[field] = {'old': str(old_val), 'new': str(new_val)}
    return changes or None
