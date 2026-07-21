from django.contrib.auth import get_user_model
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import (
    Admission, AdmissionRequest, DepositTransaction, ImagingOrder,
    InpatientDepositAccount, InvoiceItem, LabOrder, MedicationOrder, SurgeryOrder, UserProfile,
)

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


# ── Inpatient Deposit Auto-Deduction ─────────────────────────────────────────

# Service types that should NOT trigger a deposit deduction
_DEPOSIT_EXEMPT_SERVICE_TYPES = {
    InvoiceItem.ServiceType.DEPOSIT,
    InvoiceItem.ServiceType.SERVICE_CHARGE,
    InvoiceItem.ServiceType.SURGERY_BOOKING,
}


@receiver(post_save, sender=InvoiceItem)
def auto_deduct_inpatient_charge(sender, instance, created, **kwargs):
    """When a new billable InvoiceItem is created for a patient with an active
    inpatient deposit account, record the charge as a CHARGE_DEDUCTION and
    reduce the available balance immediately."""
    if not created:
        return
    if instance.service_type in _DEPOSIT_EXEMPT_SERVICE_TYPES:
        return
    try:
        invoice = instance.invoice
        if invoice is None:
            return
        patient = invoice.patient
        if patient is None:
            return
        # Find an active admission with a deposit account for this patient
        admission = (
            Admission.objects.filter(patient=patient, status=Admission.Status.ADMITTED)
            .select_related('deposit_account')
            .first()
        )
        if admission is None:
            return
        account = getattr(admission, 'deposit_account', None)
        if account is None or account.status != InpatientDepositAccount.Status.ACTIVE:
            return
        amount = instance.total or instance.unit_price or 0
        if amount <= 0:
            return
        from decimal import Decimal
        amount = Decimal(str(amount))
        account.total_charges = account.total_charges + amount
        account.save(update_fields=['total_charges', 'updated_at'])
        DepositTransaction.objects.create(
            account=account,
            tx_type=DepositTransaction.TxType.CHARGE_DEDUCTION,
            amount=amount,
            description=instance.description or instance.get_service_type_display(),
            invoice_item=instance,
            invoice=invoice,
            balance_after=account.available_balance,
            performed_by=invoice.created_by if invoice.created_by_id else User.objects.filter(is_superuser=True).first(),
        )
    except Exception:
        pass


@receiver(post_save, sender=InvoiceItem)
def advance_admission_status_on_deposit_paid(sender, instance, **kwargs):
    """When a DEPOSIT invoice item is fully paid, advance the linked
    AdmissionRequest from AWAITING_DEPOSIT to AWAITING_BED."""
    if instance.service_type != InvoiceItem.ServiceType.DEPOSIT:
        return
    if instance.payment_status != InvoiceItem.PaymentStatus.PAID:
        return
    try:
        req = instance.deposit_for_requests.select_related('patient').first()
        if req is None:
            return
        if req.status == AdmissionRequest.Status.AWAITING_DEPOSIT:
            req.status = AdmissionRequest.Status.AWAITING_BED
            req.save(update_fields=['status', 'updated_at'])
    except Exception:
        pass
