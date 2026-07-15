"""
One-time data fix for the item-level payment tracking migration
(0031_item_level_payment_tracking).

Before this migration, only the whole Invoice carried a payment status;
every InvoiceItem now also carries its own payment_status/paid_amount,
but the migration could only default new rows to "Pending Payment" —
it has no way to know which items an existing invoice's historical
payments actually covered. This command reconstructs that per-item state
from data that already exists (invoice.status, invoice.paid_amount,
invoice.payment_type/credit_approved_by) so departments keep reading a
correct payment_cleared signal on their linked orders.

Safe to re-run: only touches items still at the untouched default state
(Pending Payment, paid_amount 0, no credit/cancel metadata).
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Invoice, InvoiceItem


class Command(BaseCommand):
    help = 'Backfill InvoiceItem.payment_status/paid_amount for invoices created before item-level tracking existed.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without saving anything.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        ItemPS = InvoiceItem.PaymentStatus

        invoices = Invoice.objects.prefetch_related('items').select_related('credit_approved_by')
        touched_items = 0
        touched_invoices = 0

        with transaction.atomic():
            for invoice in invoices:
                items = [
                    item for item in invoice.items.order_by('pk')
                    if item.payment_status == ItemPS.PENDING_PAYMENT and item.paid_amount == 0
                    and item.credit_approved_at is None and item.cancelled_at is None
                ]
                if not items:
                    continue

                changed_this_invoice = False

                if invoice.status == Invoice.Status.CANCELLED:
                    for item in items:
                        item.payment_status = ItemPS.CANCELLED
                        item.cancel_reason = item.cancel_reason or 'Backfilled: parent invoice was already cancelled.'
                        if not dry_run:
                            item.save()
                        changed_this_invoice = True
                        touched_items += 1

                elif invoice.status == Invoice.Status.WAIVED:
                    for item in items:
                        item.payment_status = ItemPS.CREDIT
                        item.credit_reason = item.credit_reason or 'Backfilled: parent invoice was waived.'
                        if not dry_run:
                            item.save()
                        changed_this_invoice = True
                        touched_items += 1

                else:
                    # Mirrors the FIFO allocation payment_create uses going
                    # forward: apply the invoice's already-correct historical
                    # paid_amount across items in pk order until it runs out.
                    remaining = invoice.paid_amount or Decimal('0.00')
                    is_credit_approved = (
                        invoice.payment_type == Invoice.PaymentType.CREDIT and invoice.credit_approved_by
                    )
                    for item in items:
                        pay_now = min(remaining, item.total) if remaining > 0 else Decimal('0.00')
                        remaining -= pay_now

                        if pay_now >= item.total and item.total > 0:
                            new_status = ItemPS.PAID
                        elif is_credit_approved:
                            # Matches _cascade_status_to_items: whole-invoice
                            # credit approval clears every still-unsettled item,
                            # whether or not it already had a partial cash payment.
                            new_status = ItemPS.CREDIT
                        elif pay_now > 0:
                            new_status = ItemPS.PARTIAL
                        else:
                            new_status = ItemPS.PENDING_PAYMENT

                        if new_status == ItemPS.PENDING_PAYMENT and pay_now == 0:
                            continue  # nothing actually changed for this item

                        item.paid_amount = pay_now
                        item.payment_status = new_status
                        if new_status == ItemPS.CREDIT:
                            item.credit_approved_by = invoice.credit_approved_by
                            item.credit_approved_at = invoice.credit_approved_at
                            item.credit_reason = invoice.credit_reason or 'Backfilled: whole-invoice credit approved.'
                        if not dry_run:
                            item.save()
                        changed_this_invoice = True
                        touched_items += 1

                if changed_this_invoice:
                    touched_invoices += 1

            if dry_run:
                transaction.set_rollback(True)

        verb = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {touched_items} invoice item(s) across {touched_invoices} invoice(s).'
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no changes were saved.'))
