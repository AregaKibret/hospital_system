from django.contrib.auth import get_user_model
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Invoice, LabOrder, UserProfile

User = get_user_model()


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


# ── Lab payment_status sync ────────────────────────────────────────────────────

def _sync_lab_orders_for_invoice(invoice):
    """Update payment_status on all LabOrders linked to this invoice."""
    status = invoice.status
    if status in (Invoice.Status.PAID, Invoice.Status.OVERPAID):
        lab_ps = LabOrder.PaymentStatus.PAID
    elif status == Invoice.Status.PARTIAL:
        lab_ps = LabOrder.PaymentStatus.PARTIAL
    elif status == Invoice.Status.CREDIT_PENDING:
        lab_ps = LabOrder.PaymentStatus.CREDIT
    elif status == Invoice.Status.WAIVED:
        lab_ps = LabOrder.PaymentStatus.WAIVED
    else:
        return

    orders = LabOrder.objects.filter(
        invoice_item__invoice=invoice,
        payment_status__in=[
            LabOrder.PaymentStatus.PENDING_PAYMENT,
            LabOrder.PaymentStatus.PARTIAL,
        ],
    )
    for order in orders:
        order.payment_status = lab_ps
        if lab_ps in (
            LabOrder.PaymentStatus.PAID,
            LabOrder.PaymentStatus.CREDIT,
            LabOrder.PaymentStatus.WAIVED,
        ) and order.status == LabOrder.Status.WAITING_PAYMENT:
            order.status = LabOrder.Status.SAMPLE_PENDING
        order.save(update_fields=['payment_status', 'status'])


@receiver(post_save, sender=Invoice)
def sync_lab_payment_on_invoice_save(sender, instance, **kwargs):
    try:
        _sync_lab_orders_for_invoice(instance)
    except Exception:
        pass
