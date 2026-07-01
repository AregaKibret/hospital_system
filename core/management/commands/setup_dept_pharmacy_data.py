"""
Management command: seed department pharmacy data.

Usage:
    python manage.py setup_dept_pharmacy_data
    python manage.py setup_dept_pharmacy_data --clear
"""
import random
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    DepartmentStock, DepartmentStockBatch, DepartmentStore,
    DepartmentTransfer, DepartmentTransferItem, DepartmentUsage,
    Medication, MedicationBatch, TransferRequest, TransferRequestItem,
)

User = get_user_model()


def _rnd_date(days_ahead_min, days_ahead_max):
    return date.today() + timedelta(days=random.randint(days_ahead_min, days_ahead_max))


def _past_dt(days_ago):
    return timezone.now() - timedelta(days=random.uniform(0, days_ago))


class Command(BaseCommand):
    help = 'Seed department pharmacy mock data'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Delete existing dept pharmacy data first')

    def handle(self, *args, **options):
        if options['clear']:
            DepartmentUsage.objects.all().delete()
            DepartmentTransferItem.objects.all().delete()
            DepartmentTransfer.objects.all().delete()
            TransferRequestItem.objects.all().delete()
            TransferRequest.objects.all().delete()
            DepartmentStockBatch.objects.all().delete()
            DepartmentStock.objects.all().delete()
            DepartmentStore.objects.all().delete()
            self.stdout.write('Cleared existing dept pharmacy data.')

        admin = User.objects.filter(is_superuser=True).first()
        if not admin:
            self.stdout.write(self.style.WARNING('No superuser found, creating one...'))
            admin = User.objects.create_superuser('admin', 'admin@hospital.local', 'admin123')

        # ── 1. Department Stores ─────────────────────────────────────────────
        store_defs = [
            ('Operating Room (OR)', DepartmentStore.StoreType.OR, 'Block A, Floor 1'),
            ('Emergency Department', DepartmentStore.StoreType.ER, 'Block B, Ground Floor'),
            ('ICU Store', DepartmentStore.StoreType.ICU, 'Block C, Floor 2'),
            ('Inpatient Ward A', DepartmentStore.StoreType.WARD, 'Block D, Floor 1'),
            ('Inpatient Ward B', DepartmentStore.StoreType.WARD, 'Block D, Floor 2'),
            ('Labour & Delivery', DepartmentStore.StoreType.LABOUR, 'Block E, Floor 1'),
            ('Pediatric Ward', DepartmentStore.StoreType.PEDIATRIC, 'Block F, Floor 1'),
            ('NICU', DepartmentStore.StoreType.NICU, 'Block C, Floor 3'),
            ('OPD', DepartmentStore.StoreType.OPD, 'Block A, Ground Floor'),
            ('Surgical Ward', DepartmentStore.StoreType.SURGICAL, 'Block G, Floor 1'),
        ]
        stores = []
        for name, stype, loc in store_defs:
            s, created = DepartmentStore.objects.get_or_create(name=name, defaults={'store_type': stype, 'location': loc})
            stores.append(s)
            if created:
                self.stdout.write(f'  Created store: {name}')

        # ── 2. Get pharmacy medications & batches ────────────────────────────
        medications = list(Medication.objects.filter(is_active=True)[:20])
        if not medications:
            self.stdout.write(self.style.ERROR('No medications found. Run setup_med_inventory_data first.'))
            return

        pharm_batches = list(MedicationBatch.objects.filter(is_active=True, quantity_available__gt=0, expiration_date__gt=date.today())[:40])
        if not pharm_batches:
            self.stdout.write(self.style.ERROR('No active pharmacy batches found. Run setup_med_inventory_data first.'))
            return

        # Build a dict: med_id -> list of batches
        med_batches = {}
        for b in pharm_batches:
            med_batches.setdefault(b.medication_id, []).append(b)

        # ── 3. Seed department stock (with FIFO simulated transfers) ─────────
        transfer_counter = DepartmentTransfer.objects.count() + 1
        req_counter = TransferRequest.objects.count() + 1

        for store in stores:
            meds_for_store = random.sample(medications, min(8, len(medications)))
            for med in meds_for_store:
                batches = med_batches.get(med.id)
                if not batches:
                    continue
                batch = random.choice(batches)

                qty_to_transfer = random.randint(20, 80)
                qty_to_transfer = min(qty_to_transfer, batch.quantity_available)
                if qty_to_transfer <= 0:
                    continue

                min_qty = random.randint(5, 15)

                # Create transfer record (already completed)
                transfer = DepartmentTransfer.objects.create(
                    transfer_number=f'TRF-{transfer_counter:06d}',
                    transfer_type=DepartmentTransfer.TransferType.PHARM_TO_DEPT,
                    dest_store=store,
                    status=DepartmentTransfer.Status.COMPLETED,
                    transfer_date=timezone.now() - timedelta(days=random.randint(7, 60)),
                    received_date=timezone.now() - timedelta(days=random.randint(1, 6)),
                    prepared_by=admin,
                    received_by=admin,
                    notes='Initial stock setup',
                )
                transfer_counter += 1

                DepartmentTransferItem.objects.create(
                    transfer=transfer,
                    medication=med,
                    source_batch=batch,
                    batch_number=batch.batch_number,
                    expiration_date=batch.expiration_date,
                    quantity_transferred=qty_to_transfer,
                    unit_cost=med.purchase_price,
                )

                # Add dept stock
                stock, _ = DepartmentStock.objects.get_or_create(
                    department_store=store, medication=med,
                    defaults={'quantity_available': 0, 'minimum_quantity': min_qty},
                )
                stock.quantity_available += qty_to_transfer
                stock.save()

                # Add dept batch
                db_batch, created = DepartmentStockBatch.objects.get_or_create(
                    dept_stock=stock,
                    source_batch=batch,
                    batch_number=batch.batch_number,
                    defaults={
                        'expiration_date': batch.expiration_date,
                        'quantity_available': 0,
                        'received_date': date.today() - timedelta(days=random.randint(1, 30)),
                    },
                )
                db_batch.quantity_available += qty_to_transfer
                db_batch.save()

        self.stdout.write(f'  Seeded stock for {len(stores)} stores.')

        # ── 4. Simulate usage history ────────────────────────────────────────
        usage_types = [
            DepartmentUsage.UsageType.ADMINISTRATION,
            DepartmentUsage.UsageType.EMERGENCY,
            DepartmentUsage.UsageType.SURGICAL,
            DepartmentUsage.UsageType.WASTAGE,
        ]
        usage_counter = DepartmentUsage.objects.count() + 1

        for store in stores:
            stock_items = list(store.stock_items.select_related('medication')[:6])
            for _ in range(random.randint(10, 25)):
                if not stock_items:
                    break
                stock = random.choice(stock_items)
                if stock.quantity_available <= 0:
                    continue
                qty = random.randint(1, min(5, stock.quantity_available))
                u_type = random.choice(usage_types)
                db_batch = stock.batches.filter(is_active=True, quantity_available__gt=0).order_by('expiration_date').first()
                DepartmentUsage.objects.create(
                    usage_number=f'USE-{usage_counter:07d}',
                    department_store=store,
                    medication=stock.medication,
                    dept_batch=db_batch,
                    quantity_used=qty,
                    usage_type=u_type,
                    responsible_staff=admin,
                    usage_date=_past_dt(30),
                    reason='Routine use' if u_type == DepartmentUsage.UsageType.ADMINISTRATION else '',
                )
                usage_counter += 1
                # Update stock
                stock.quantity_available = max(0, stock.quantity_available - qty)
                stock.save()
                if db_batch:
                    db_batch.quantity_available = max(0, db_batch.quantity_available - qty)
                    db_batch.save()

        self.stdout.write(f'  Seeded usage records.')

        # ── 5. Pending transfer requests ─────────────────────────────────────
        for store in random.sample(stores, 3):
            meds_wanted = random.sample(medications, random.randint(2, 4))
            req = TransferRequest.objects.create(
                request_number=f'REQ-{req_counter:06d}',
                requesting_store=store,
                priority=random.choice([TransferRequest.Priority.NORMAL, TransferRequest.Priority.URGENT]),
                status=TransferRequest.Status.PENDING,
                requested_by=admin,
                request_date=timezone.now() - timedelta(hours=random.randint(1, 48)),
                notes='Stock running low',
            )
            req_counter += 1
            for med in meds_wanted:
                TransferRequestItem.objects.create(
                    transfer_request=req,
                    medication=med,
                    quantity_requested=random.randint(20, 50),
                )
            self.stdout.write(f'  Created pending request {req.request_number} for {store.name}')

        self.stdout.write(self.style.SUCCESS('Department pharmacy mock data seeded successfully!'))
        self.stdout.write(f'  Stores: {DepartmentStore.objects.count()}')
        self.stdout.write(f'  Stock items: {DepartmentStock.objects.count()}')
        self.stdout.write(f'  Transfers: {DepartmentTransfer.objects.count()}')
        self.stdout.write(f'  Usage records: {DepartmentUsage.objects.count()}')
        self.stdout.write(f'  Pending requests: {TransferRequest.objects.filter(status="pending").count()}')
