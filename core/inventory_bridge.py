"""
Bridges the legacy walk-in PharmacyStock model into the central Medication
Inventory ledger (Medication / MedicationBatch / StockTransaction).

PharmacyStock is a flat "one row per drug" model with no transaction history —
every dispense/sale/adjustment mutates `quantity_in_stock` directly. The
Medication Inventory reports (Stock on Hand, Stock Card, Stock Movement) read
Medication/MedicationBatch/StockTransaction instead, so without this bridge
those reports stay empty regardless of how much real pharmacy activity occurs.

Each PharmacyStock row is mirrored 1:1 to one Medication catalog entry and one
MedicationBatch ("the bridge batch"), kept in sync on every stock change. This
does not replace PharmacyStock as the day-to-day pharmacy model — it just gives
every mutation a place to log a proper, reportable ledger entry.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from .models import InventoryCategory, InventoryItem, Medication, MedicationBatch, StockTransaction

# Dosage forms shared verbatim between PharmacyStock.DosageForm and Medication.DrugType
_DRUG_TYPE_MAP = {
    'Tablet': 'Tablet', 'Capsule': 'Capsule', 'Syrup': 'Syrup', 'Injection': 'Injection',
    'Cream': 'Cream', 'Drops': 'Drops', 'Inhaler': 'Inhaler', 'Suppository': 'Suppository',
    'Patch': 'Patch',
}


def _next_medication_code():
    last = InventoryItem.objects.filter(item_code__startswith='PS-').order_by('-id').first()
    next_id = 1
    if last:
        try:
            next_id = int(last.item_code.split('-')[-1]) + 1
        except ValueError:
            next_id = InventoryItem.objects.count() + 1
    return f'PS-{next_id:05d}'


def ensure_medication_link(stock, user):
    """Return the Medication catalog entry mirroring this PharmacyStock row,
    creating it (plus its linked InventoryItem, bridge batch, and opening
    balance transaction) on first use. The Item Master (InventoryItem) is the
    single source of truth for master/catalog data — this creates that row
    first, then a Medication Details row referencing it, never duplicating
    data across the two."""
    if stock.medication_id:
        return stock.medication

    category = None
    if stock.category:
        category, _ = InventoryCategory.objects.get_or_create(name=stock.category.strip())

    item = InventoryItem.objects.create(
        name=stock.drug_name,
        generic_name=stock.generic_name or stock.drug_name,
        item_code=_next_medication_code(),
        item_type=InventoryItem.ItemType.MEDICATION,
        category=category,
        dosage_form=stock.dosage_form or '',
        strength=stock.strength or 'N/A',
        unit=stock.unit or 'Tablet',
        unit_cost=stock.unit_cost,
        selling_price=stock.selling_price,
        reorder_level=stock.reorder_level,
        is_active=True,
        notes=f'Auto-created from Pharmacy Stock #{stock.pk} ({stock.drug_name}).',
    )
    med = Medication.objects.create(
        inventory_item=item,
        brand_name=stock.drug_name,
        generic_name=stock.generic_name or stock.drug_name,
        drug_type=_DRUG_TYPE_MAP.get(stock.dosage_form, 'Other'),
        strength=stock.strength or 'N/A',
        dosage_form=stock.dosage_form or '',
        notes=f'Auto-created from Pharmacy Stock #{stock.pk} ({stock.drug_name}).',
    )

    opening_qty = max(stock.quantity_in_stock, 0)
    batch = MedicationBatch.objects.create(
        medication=med,
        batch_number=stock.batch_number or f'OPEN-{stock.pk}',
        expiration_date=stock.expiry_date or (date.today() + timedelta(days=730)),
        quantity_received=opening_qty,
        quantity_available=opening_qty,
        purchase_price=stock.unit_cost,
        received_date=date.today(),
        received_by=user,
        notes='Opening balance migrated from Pharmacy Stock.',
    )

    if opening_qty > 0:
        StockTransaction.objects.create(
            medication=med,
            batch=batch,
            transaction_type=StockTransaction.TxType.OPENING,
            quantity_in=opening_qty,
            quantity_out=0,
            balance_after=opening_qty,
            unit_cost=stock.unit_cost,
            total_value=stock.unit_cost * opening_qty,
            reference_number=f'OPEN-PS-{stock.pk}',
            notes='Opening balance migrated from Pharmacy Stock.',
            performed_by=user,
            transaction_date=timezone.now(),
        )

    stock.medication = med
    stock.save(update_fields=['medication'])
    return med


def record_pharmacy_stock_transaction(
    stock, tx_type, user, qty_in=0, qty_out=0,
    reference='', notes='', patient=None,
):
    """Log a StockTransaction for a PharmacyStock movement.

    Call this AFTER `stock.quantity_in_stock` has already been updated and
    saved by the caller — the bridge batch is set to match that authoritative
    value so the two never drift apart.
    """
    med = ensure_medication_link(stock, user)
    batch = med.batches.order_by('-pk').first()
    if batch is None:
        batch = MedicationBatch.objects.create(
            medication=med,
            batch_number=stock.batch_number or f'OPEN-{stock.pk}',
            expiration_date=stock.expiry_date or (date.today() + timedelta(days=730)),
            quantity_received=max(stock.quantity_in_stock, 0),
            quantity_available=max(stock.quantity_in_stock, 0),
            purchase_price=stock.unit_cost,
            received_date=date.today(),
            received_by=user,
        )
    else:
        batch.quantity_available = max(stock.quantity_in_stock, 0)
        batch.save(update_fields=['quantity_available'])

    unit_cost = stock.unit_cost or Decimal('0')
    StockTransaction.objects.create(
        medication=med,
        batch=batch,
        transaction_type=tx_type,
        quantity_in=qty_in,
        quantity_out=qty_out,
        balance_after=max(stock.quantity_in_stock, 0),
        unit_cost=unit_cost,
        total_value=unit_cost * (qty_in or qty_out),
        reference_number=reference,
        notes=notes,
        patient=patient,
        performed_by=user,
        transaction_date=timezone.now(),
    )
