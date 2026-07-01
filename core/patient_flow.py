"""
Utilities for recording patient journey events and updating Visit.status.
Import this instead of touching models directly from views.
"""
from django.db import transaction
from django.utils import timezone


def log_journey_event(
    visit,
    title,
    event_type='system',
    description='',
    performed_by=None,
    new_status=None,
    department='',
):
    """
    Record a journey event and optionally advance Visit.status.
    Never raises — silently swallows errors so it never breaks the main flow.
    """
    try:
        from .models import VisitJourneyEvent

        old_status = visit.status if new_status else ''

        with transaction.atomic():
            if new_status and visit.status != new_status:
                old_status = visit.status
                visit.status = new_status
                visit.save(update_fields=['status', 'updated_at'])

            dept = department
            if not dept and performed_by:
                try:
                    profile = performed_by.userprofile
                    dept = profile.department.name if profile.department else ''
                except Exception:
                    pass

            VisitJourneyEvent.objects.create(
                visit=visit,
                event_type=event_type,
                status_before=old_status,
                status_after=new_status or old_status,
                title=title,
                description=description,
                performed_by=performed_by,
                department=dept,
            )
    except Exception:
        pass  # journey logging must never break the calling view


def advance_to_waiting_payment(visit, performed_by=None):
    from .models import Visit
    log_journey_event(
        visit, 'Visit created — awaiting payment',
        event_type='status_change',
        new_status=Visit.Status.WAITING_PAYMENT,
        performed_by=performed_by,
    )


def advance_to_payment_completed(visit, performed_by=None, invoice=None):
    from .models import Visit
    desc = f'Invoice #{invoice.invoice_number} paid' if invoice else 'Payment received'
    log_journey_event(
        visit, 'Payment completed',
        event_type='payment',
        description=desc,
        new_status=Visit.Status.PAYMENT_COMPLETED,
        performed_by=performed_by,
    )
    # Immediately advance to waiting_doctor
    log_journey_event(
        visit, 'Patient ready for doctor consultation',
        event_type='status_change',
        new_status=Visit.Status.WAITING_DOCTOR,
        performed_by=performed_by,
    )


def advance_to_consultation_started(visit, performed_by=None):
    from .models import Visit
    if visit.status not in (Visit.Status.CONSULTATION_STARTED,):
        log_journey_event(
            visit, f'Consultation started by Dr. {performed_by.get_full_name() if performed_by else "Unknown"}',
            event_type='clinical',
            new_status=Visit.Status.CONSULTATION_STARTED,
            performed_by=performed_by,
        )
