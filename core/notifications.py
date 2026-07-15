"""Helpers to create in-app notifications."""
from django.contrib.auth import get_user_model

User = get_user_model()


def notify(recipient, title, message='', notif_type='general', url='', sender=None, object_type='', object_id=None):
    """Create a single notification. Silently ignores errors."""
    try:
        from .models import Notification
        Notification.objects.create(
            recipient=recipient,
            sender=sender,
            notif_type=notif_type,
            title=title,
            message=message,
            url=url,
            object_type=object_type,
            object_id=object_id,
        )
    except Exception:
        pass


def notify_group(group_name, title, message='', notif_type='general', url='', sender=None, object_type='', object_id=None):
    """Notify all users in a Django group."""
    try:
        from django.contrib.auth.models import Group
        try:
            group = Group.objects.get(name=group_name)
        except Group.DoesNotExist:
            return
        for user in group.user_set.filter(is_active=True):
            notify(user, title, message, notif_type, url, sender, object_type, object_id)
    except Exception:
        pass


def notify_role(role_name, title, message='', notif_type='general', url='', sender=None, object_type='', object_id=None):
    """Alias for notify_group (roles = Django groups in this system)."""
    notify_group(role_name, title, message, notif_type, url, sender, object_type, object_id)


def notify_new_prescription(prescription, sender_user=None):
    """Notify pharmacy staff when a prescription is sent."""
    try:
        from django.urls import reverse as r
        url = r('rx_detail', kwargs={'rx_id': prescription.pk})
        notify_group(
            'Pharmacist',
            f'New Prescription: {prescription.prescription_number}',
            f'Patient: {prescription.patient.full_name} — {prescription.items.count()} item(s) from Dr. {prescription.prescribed_by.get_full_name() or prescription.prescribed_by.username}',
            notif_type='prescription',
            url=url,
            sender=sender_user,
            object_type='Prescription',
            object_id=prescription.pk,
        )
    except Exception:
        pass


def notify_payment_completed(invoice, sender_user=None):
    """Notify prescribing doctors and lab/radiology staff when invoice is paid."""
    try:
        from django.urls import reverse as r
        # Notify prescribing doctors on linked prescriptions
        for rx in invoice.prescriptions.select_related('prescribed_by'):
            notify(
                rx.prescribed_by,
                f'Payment Confirmed — {rx.prescription_number}',
                f'Patient {rx.patient.full_name} invoice paid. Pharmacy will now dispense.',
                notif_type='payment',
                url=r('rx_detail', kwargs={'rx_id': rx.pk}),
                sender=sender_user,
                object_type='Invoice',
                object_id=invoice.pk,
            )
        # Notify lab staff for lab orders on this invoice
        from .models import LabOrder
        lab_orders = LabOrder.objects.filter(
            invoice_item__invoice=invoice
        ).select_related('ordered_by').distinct()
        lab_notified_users = set()
        for order in lab_orders:
            if order.ordered_by_id not in lab_notified_users:
                lab_notified_users.add(order.ordered_by_id)
                notify(
                    order.ordered_by,
                    'Lab Payment Confirmed',
                    f'Payment received for {invoice.patient.full_name}. Lab tests approved for processing.',
                    notif_type='payment',
                    url=r('invoice_detail', kwargs={'invoice_id': invoice.pk}),
                    sender=sender_user,
                    object_type='Invoice',
                    object_id=invoice.pk,
                )
    except Exception:
        pass


def notify_lab_result_ready(lab_order, sender_user=None):
    """Notify ordering doctor when lab result is released."""
    try:
        from django.urls import reverse as r
        notify(
            lab_order.ordered_by,
            f'Lab Result Ready: {lab_order.test_name}',
            f'Result for patient {lab_order.visit.patient.full_name} is ready for review.',
            notif_type='lab_result',
            url=r('lab_order_detail', kwargs={'order_id': lab_order.pk}),
            sender=sender_user,
            object_type='LabOrder',
            object_id=lab_order.pk,
        )
    except Exception:
        pass


def notify_appointment_created(appointment, sender_user=None):
    """Notify doctor when a new appointment is created for them."""
    try:
        if not appointment.doctor:
            return
        notify(
            appointment.doctor,
            f'New Appointment: {appointment.patient.full_name}',
            f'{appointment.appointment_date} at {appointment.appointment_time} — {appointment.reason_for_visit or "Consultation"}',
            notif_type='appointment',
            url='',
            sender=sender_user,
            object_type='Appointment',
            object_id=appointment.pk,
        )
    except Exception:
        pass
