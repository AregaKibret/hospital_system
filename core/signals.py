from django.contrib.auth import get_user_model
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import ImagingOrder, InvoiceItem, LabOrder, MedicationOrder, SurgeryOrder, UserProfile

User = get_user_model()


def get_client_ip(request):
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Auto-create a UserProfile whenever a new User is saved."""
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    try:
        from core.audit import log_action
        from core.models import AuditLog
        log_action(
            user, AuditLog.Action.LOGIN, AuditLog.Module.AUTH,
            object_type='User', object_id=user.pk,
            object_repr=user.get_full_name() or user.username,
            description=f'Successful login by {user.username}',
            severity=AuditLog.Severity.INFO,
            request=request,
        )
    except Exception:
        pass
    # Create UserSession record
    try:
        from core.models import UserSession
        session_key = request.session.session_key or ''
        ip = get_client_ip(request)
        agent_str = request.META.get('HTTP_USER_AGENT', '')
        # Basic UA parsing without external library
        browser = ''
        os_str = ''
        device = 'Desktop'
        agent_lower = agent_str.lower()
        if 'mobile' in agent_lower or 'android' in agent_lower:
            device = 'Mobile'
        elif 'tablet' in agent_lower or 'ipad' in agent_lower:
            device = 'Tablet'
        for b in ['Chrome', 'Firefox', 'Safari', 'Edge', 'Opera']:
            if b.lower() in agent_lower:
                browser = b
                break
        for o in ['Windows', 'Linux', 'Mac OS', 'Android', 'iOS']:
            if o.lower() in agent_lower:
                os_str = o
                break
        if session_key:
            UserSession.objects.get_or_create(
                session_key=session_key,
                defaults={
                    'user': user,
                    'ip_address': ip or None,
                    'user_agent': agent_str[:500],
                    'browser': browser,
                    'os': os_str,
                    'device_type': device,
                }
            )
    except Exception:
        pass


@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    try:
        from core.audit import log_action
        from core.models import AuditLog
        if user:
            log_action(
                user, AuditLog.Action.LOGOUT, AuditLog.Module.AUTH,
                object_type='User', object_id=user.pk,
                object_repr=user.get_full_name() or user.username,
                description=f'User {user.username} logged out',
                severity=AuditLog.Severity.INFO,
                request=request,
            )
    except Exception:
        pass
    try:
        from core.models import UserSession
        session_key = request.session.session_key or ''
        if session_key:
            UserSession.objects.filter(
                session_key=session_key, is_active=True
            ).update(
                is_active=False,
                logout_at=timezone.now(),
                logout_type=UserSession.LogoutType.MANUAL,
            )
    except Exception:
        pass


@receiver(user_login_failed)
def on_user_login_failed(sender, credentials, request, **kwargs):
    try:
        from core.audit import log_action
        from core.models import AuditLog
        username = credentials.get('username', 'unknown')
        log_action(
            None, AuditLog.Action.LOGIN_FAILED, AuditLog.Module.AUTH,
            object_type='User',
            object_repr=username,
            description=f'Failed login attempt for username: {username}',
            severity=AuditLog.Severity.CRITICAL,
            request=request,
        )
    except Exception:
        pass


# ── Order payment_status sync — ITEM level ─────────────────────────────────────
# Billing is the single source of truth for payment, tracked per invoice line
# item (InvoiceItem.payment_status). Whenever a specific item's payment status
# changes — via item-level cash payment, credit approval, cancellation, or
# refund — the ONE order/consumable linked to that exact item is synced.
# Order-side code must never set payment_status directly.

# InvoiceItem.PaymentStatus -> order PaymentStatus attribute name.
# Cancelled/Refunded charges do not unlock processing — they leave the order
# waiting, since the underlying service was removed/reversed, not paid.
_ITEM_TO_ORDER_STATUS_KEY = {
    InvoiceItem.PaymentStatus.PENDING_PAYMENT: 'PENDING_PAYMENT',
    InvoiceItem.PaymentStatus.PARTIAL:         'PARTIAL',
    InvoiceItem.PaymentStatus.PAID:            'PAID',
    InvoiceItem.PaymentStatus.CREDIT:          'CREDIT',
    InvoiceItem.PaymentStatus.CANCELLED:       'PENDING_PAYMENT',
    InvoiceItem.PaymentStatus.REFUNDED:        'PENDING_PAYMENT',
}

# related_name on InvoiceItem -> (waiting status, next status) for models that
# have a pipeline status to advance once payment clears. None/None for models
# where payment_status alone gates processing (no status to advance).
_LINKED_ORDER_CONFIG = {
    'lab_order':        (LabOrder.Status.WAITING_PAYMENT, LabOrder.Status.SAMPLE_PENDING),
    'imaging_order':    (ImagingOrder.Status.WAITING_PAYMENT, ImagingOrder.Status.PENDING),
    'medication_order': (None, None),
    'surgery_order':    (None, None),
}


def _sync_linked_order_from_item(item):
    for related_name, (waiting_status, next_status) in _LINKED_ORDER_CONFIG.items():
        order = getattr(item, related_name, None)
        if order is None:
            continue
        model = type(order)
        target = getattr(model.PaymentStatus, _ITEM_TO_ORDER_STATUS_KEY[item.payment_status])
        order.payment_status = target
        update_fields = ['payment_status']
        cleared = (model.PaymentStatus.PAID, model.PaymentStatus.CREDIT, model.PaymentStatus.WAIVED)
        if waiting_status and target in cleared and order.status == waiting_status:
            order.status = next_status
            update_fields.append('status')
        order.save(update_fields=update_fields)


@receiver(post_save, sender=InvoiceItem)
def sync_order_payment_on_item_save(sender, instance, **kwargs):
    try:
        _sync_linked_order_from_item(instance)
    except Exception:
        pass
