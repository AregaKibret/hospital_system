"""Management command: seed medication inventory with realistic mock data."""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    InventoryCategory, InventoryItem, MedicationBatch, Medication,
    StockTransaction, StorageLocation, Supplier,
)


class Command(BaseCommand):
    help = 'Seed medication inventory module with realistic mock data'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Clear existing data before seeding')

    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('Clearing existing data...')
            StockTransaction.objects.all().delete()
            MedicationBatch.objects.all().delete()
            InventoryItem.objects.filter(medication_details__isnull=False).delete()
            Medication.objects.all().delete()
            StorageLocation.objects.all().delete()
            Supplier.objects.all().delete()
            self.stdout.write(self.style.WARNING('Cleared.'))

        admin = User.objects.filter(is_superuser=True).first()
        if not admin:
            admin = User.objects.first()

        today = date.today()

        # ── Suppliers ──────────────────────────────────────────────────────
        self.stdout.write('Creating suppliers...')
        suppliers_data = [
            ('SUP-001', 'Ethiopian Pharmaceuticals Supply Agency', 'Ato Bekele Tadesse', '+251-11-518-5555', 'info@epsa.gov.et', 'Addis Ababa, Bole Sub-city'),
            ('SUP-002', 'Addis Pharmaceuticals Factory', 'Ato Tesfaye Haile', '+251-11-234-5678', 'sales@apf.com.et', 'Addis Ababa, Akaki'),
            ('SUP-003', 'Julphar Ethiopia PLC', 'Ms. Meron Alemu', '+251-11-667-8901', 'ethiopia@julphar.com', 'Addis Ababa, Bole'),
            ('SUP-004', 'Medtech Ethiopia Imports', 'Ato Girma Kebede', '+251-91-234-5678', 'medtech@ethionet.et', 'Addis Ababa, Kazanchis'),
            ('SUP-005', 'Nobel Pharmaceuticals', 'Dr. Sara Wolde', '+251-11-445-6789', 'nobel@pharma.et', 'Addis Ababa, Piassa'),
            ('SUP-006', 'Pan African Pharmaceuticals', 'Ato Mulugeta Desta', '+251-91-876-5432', 'panaf@pharma.com', 'Addis Ababa, Lebu'),
            ('SUP-007', 'Sino-Ethiopian Medical Supplies', 'Ms. Chen Wei', '+251-11-789-0123', 'sino@medsupp.com', 'Addis Ababa, CMC'),
            ('SUP-008', 'HealthPlus Distribution', 'Ato Dawit Girma', '+251-91-345-6789', 'health@plus.et', 'Addis Ababa, Megenagna'),
        ]
        suppliers = {}
        for code, name, contact, phone, email, address in suppliers_data:
            s, _ = Supplier.objects.get_or_create(
                code=code,
                defaults=dict(name=name, contact_person=contact, phone=phone, email=email, address=address)
            )
            suppliers[code] = s
        self.stdout.write(self.style.SUCCESS(f'  {len(suppliers)} suppliers ready'))

        # ── Categories ─────────────────────────────────────────────────────
        self.stdout.write('Creating categories...')
        category_names = [
            'Analgesics & Antipyretics',
            'Antibiotics & Antimicrobials',
            'Antihypertensives',
            'Antidiabetics',
            'Antiparasitics & Antiprotozoals',
            'Gastrointestinal Agents',
            'Respiratory Agents',
            'Vitamins & Supplements',
            'Cardiovascular Agents',
            'Neurological Agents',
            'Dermatological Preparations',
            'Ophthalmic Preparations',
        ]
        categories = {}
        for name in category_names:
            cat, _ = InventoryCategory.objects.get_or_create(name=name)
            categories[name] = cat
        self.stdout.write(self.style.SUCCESS(f'  {len(categories)} categories ready'))

        # ── Storage Locations ──────────────────────────────────────────────
        self.stdout.write('Creating storage locations...')
        locations_data = [
            ('Main Pharmacy Store', 'Pharmacy Wing', 'A', 'BIN-01', 'room_temp'),
            ('Refrigerated Storage', 'Pharmacy Wing', 'B', 'BIN-02', 'refrigerated'),
            ('Cold Chain Store', 'Laboratory Block', 'C', 'BIN-03', 'frozen'),
            ('Controlled Substance Safe', 'Pharmacy Office', 'D', 'BIN-04', 'cool_dark'),
        ]
        locations = []
        for name, warehouse, shelf, bin_no, condition in locations_data:
            loc, _ = StorageLocation.objects.get_or_create(
                name=name,
                defaults=dict(warehouse=warehouse, shelf=shelf, bin_number=bin_no, storage_condition=condition)
            )
            locations.append(loc)
        self.stdout.write(self.style.SUCCESS(f'  {len(locations)} locations ready'))

        # ── Medications ────────────────────────────────────────────────────
        self.stdout.write('Creating medications...')
        medications_data = [
            # (name, generic, code, strength, drug_type, route, category_key, manufacturer, purchase_price, selling_price, min_stock, reorder_level, reorder_qty)
            ('Paracetamol 500mg Tabs', 'Paracetamol', 'MED-001', '500 mg', 'tablet', 'oral', 'Analgesics & Antipyretics', 'Addis Pharma', Decimal('1.50'), Decimal('2.50'), 100, 200, 500),
            ('Ibuprofen 400mg Tabs', 'Ibuprofen', 'MED-002', '400 mg', 'tablet', 'oral', 'Analgesics & Antipyretics', 'Nobel Pharma', Decimal('2.00'), Decimal('3.50'), 80, 150, 400),
            ('Diclofenac 75mg Injection', 'Diclofenac Sodium', 'MED-003', '75 mg/3ml', 'injection', 'im', 'Analgesics & Antipyretics', 'Julphar', Decimal('15.00'), Decimal('25.00'), 30, 60, 100),
            ('Amoxicillin 500mg Caps', 'Amoxicillin', 'MED-004', '500 mg', 'capsule', 'oral', 'Antibiotics & Antimicrobials', 'Addis Pharma', Decimal('5.00'), Decimal('8.00'), 100, 200, 500),
            ('Ciprofloxacin 500mg Tabs', 'Ciprofloxacin', 'MED-005', '500 mg', 'tablet', 'oral', 'Antibiotics & Antimicrobials', 'Nobel Pharma', Decimal('8.00'), Decimal('13.00'), 80, 150, 300),
            ('Metronidazole 500mg Tabs', 'Metronidazole', 'MED-006', '500 mg', 'tablet', 'oral', 'Antibiotics & Antimicrobials', 'Pan African', Decimal('3.00'), Decimal('5.00'), 100, 200, 400),
            ('Ceftriaxone 1g Injection', 'Ceftriaxone', 'MED-007', '1 g', 'injection', 'iv', 'Antibiotics & Antimicrobials', 'Julphar', Decimal('45.00'), Decimal('70.00'), 30, 50, 100),
            ('Amlodipine 5mg Tabs', 'Amlodipine', 'MED-008', '5 mg', 'tablet', 'oral', 'Antihypertensives', 'Addis Pharma', Decimal('3.50'), Decimal('6.00'), 80, 150, 300),
            ('Enalapril 10mg Tabs', 'Enalapril Maleate', 'MED-009', '10 mg', 'tablet', 'oral', 'Antihypertensives', 'Nobel Pharma', Decimal('4.00'), Decimal('7.00'), 60, 120, 250),
            ('Hydrochlorothiazide 25mg Tabs', 'Hydrochlorothiazide', 'MED-010', '25 mg', 'tablet', 'oral', 'Antihypertensives', 'Pan African', Decimal('2.50'), Decimal('4.50'), 60, 120, 250),
            ('Metformin 500mg Tabs', 'Metformin HCl', 'MED-011', '500 mg', 'tablet', 'oral', 'Antidiabetics', 'Addis Pharma', Decimal('3.00'), Decimal('5.50'), 100, 200, 400),
            ('Glibenclamide 5mg Tabs', 'Glibenclamide', 'MED-012', '5 mg', 'tablet', 'oral', 'Antidiabetics', 'Nobel Pharma', Decimal('2.00'), Decimal('3.50'), 80, 150, 300),
            ('Insulin Regular 100IU/ml', 'Insulin (Human Regular)', 'MED-013', '100 IU/ml', 'injection', 'sc', 'Antidiabetics', 'Novo Nordisk', Decimal('120.00'), Decimal('180.00'), 20, 40, 60),
            ('Artemether/Lumefantrine 80/480mg', 'Artemether + Lumefantrine', 'MED-014', '80/480 mg', 'tablet', 'oral', 'Antiparasitics & Antiprotozoals', 'Addis Pharma', Decimal('25.00'), Decimal('40.00'), 50, 100, 200),
            ('Albendazole 400mg Tabs', 'Albendazole', 'MED-015', '400 mg', 'tablet', 'oral', 'Antiparasitics & Antiprotozoals', 'Pan African', Decimal('4.00'), Decimal('7.00'), 60, 120, 250),
            ('Omeprazole 20mg Caps', 'Omeprazole', 'MED-016', '20 mg', 'capsule', 'oral', 'Gastrointestinal Agents', 'Nobel Pharma', Decimal('5.00'), Decimal('9.00'), 80, 150, 300),
            ('Metoclopramide 10mg Tabs', 'Metoclopramide', 'MED-017', '10 mg', 'tablet', 'oral', 'Gastrointestinal Agents', 'Addis Pharma', Decimal('1.50'), Decimal('2.50'), 60, 120, 250),
            ('Salbutamol Inhaler 100mcg', 'Salbutamol', 'MED-018', '100 mcg/dose', 'inhaler', 'inhalation', 'Respiratory Agents', 'GSK Ethiopia', Decimal('35.00'), Decimal('55.00'), 30, 60, 100),
            ('Dexamethasone 4mg Injection', 'Dexamethasone', 'MED-019', '4 mg/ml', 'injection', 'iv', 'Respiratory Agents', 'Julphar', Decimal('20.00'), Decimal('35.00'), 30, 60, 120),
            ('Vitamin B-Complex Tabs', 'Vitamin B Complex', 'MED-020', 'Standard', 'tablet', 'oral', 'Vitamins & Supplements', 'Addis Pharma', Decimal('1.00'), Decimal('2.00'), 100, 200, 500),
            ('Folic Acid 5mg Tabs', 'Folic Acid', 'MED-021', '5 mg', 'tablet', 'oral', 'Vitamins & Supplements', 'Pan African', Decimal('0.50'), Decimal('1.00'), 100, 200, 500),
            ('Ferrous Sulphate 200mg Tabs', 'Ferrous Sulphate', 'MED-022', '200 mg', 'tablet', 'oral', 'Vitamins & Supplements', 'Addis Pharma', Decimal('1.00'), Decimal('1.80'), 100, 200, 500),
            ('Atorvastatin 20mg Tabs', 'Atorvastatin', 'MED-023', '20 mg', 'tablet', 'oral', 'Cardiovascular Agents', 'Nobel Pharma', Decimal('7.00'), Decimal('12.00'), 60, 120, 250),
            ('Aspirin 81mg Tabs', 'Acetylsalicylic Acid', 'MED-024', '81 mg', 'tablet', 'oral', 'Cardiovascular Agents', 'Addis Pharma', Decimal('1.00'), Decimal('2.00'), 100, 200, 400),
            ('Digoxin 0.25mg Tabs', 'Digoxin', 'MED-025', '0.25 mg', 'tablet', 'oral', 'Cardiovascular Agents', 'Julphar', Decimal('5.00'), Decimal('9.00'), 40, 80, 200),
            ('Diazepam 5mg Tabs', 'Diazepam', 'MED-026', '5 mg', 'tablet', 'oral', 'Neurological Agents', 'Pan African', Decimal('3.00'), Decimal('5.00'), 40, 80, 200),
            ('Phenobarbitone 30mg Tabs', 'Phenobarbital', 'MED-027', '30 mg', 'tablet', 'oral', 'Neurological Agents', 'Nobel Pharma', Decimal('2.00'), Decimal('4.00'), 40, 80, 200),
            ('Hydrocortisone Cream 1%', 'Hydrocortisone', 'MED-028', '1% w/w', 'cream', 'topical', 'Dermatological Preparations', 'Addis Pharma', Decimal('12.00'), Decimal('20.00'), 30, 60, 120),
            ('Chloramphenicol Eye Drops 0.5%', 'Chloramphenicol', 'MED-029', '0.5% w/v', 'eye_drops', 'ophthalmic', 'Ophthalmic Preparations', 'Nobel Pharma', Decimal('18.00'), Decimal('30.00'), 30, 60, 100),
            ('Normal Saline 0.9% 500ml', 'Sodium Chloride', 'MED-030', '0.9% w/v', 'infusion', 'iv', 'Gastrointestinal Agents', 'Julphar', Decimal('25.00'), Decimal('40.00'), 50, 100, 200),
        ]

        med_objects = {}
        sup_list = list(suppliers.values())
        for (name, generic, code, strength, drug_type, route, cat_key, manuf,
             purchase_price, selling_price, min_stock, reorder_level, reorder_qty) in medications_data:
            med = Medication.objects.filter(inventory_item__item_code=code).first()
            if not med:
                item = InventoryItem.objects.create(
                    name=name,
                    item_code=code,
                    generic_name=generic,
                    item_type=InventoryItem.ItemType.MEDICATION,
                    category=categories.get(cat_key),
                    dosage_form=drug_type,
                    strength=strength,
                    manufacturer=manuf,
                    supplier=random.choice(sup_list),
                    unit='Unit',
                    unit_purchase='Pack',
                    dispensing_unit='Tablet' if drug_type in ('tablet', 'capsule') else 'Unit',
                    consumption_factor=100,
                    unit_cost=purchase_price,
                    selling_price=selling_price,
                    min_stock=min_stock // 2,
                    max_stock=min_stock * 10,
                    reorder_level=reorder_level,
                    reorder_quantity=reorder_qty,
                    safety_stock=min_stock,
                    storage_location=random.choice(locations),
                    is_active=True,
                )
                med = Medication.objects.create(
                    inventory_item=item,
                    brand_name=name,
                    generic_name=generic,
                    strength=strength,
                    drug_type=drug_type,
                    route=route,
                    dosage_form=drug_type,
                    pack_description='1 Pack',
                    units_per_pack=100,
                    storage_condition='refrigerated' if 'Insulin' in name or 'Eye Drops' in name else 'room_temp',
                    prescription_required=drug_type in ('injection', 'inhaler') or 'Diazepam' in name or 'Phenobarb' in name,
                    controlled_substance='Diazepam' in name or 'Phenobarb' in name,
                    registration_number=f'REG-ETH-{code}',
                )
            med_objects[code] = med

        self.stdout.write(self.style.SUCCESS(f'  {len(med_objects)} medications ready'))

        # ── Batches ────────────────────────────────────────────────────────
        self.stdout.write('Creating batches...')
        batch_count = 0
        tx_count = 0

        for idx, med in enumerate(med_objects.values()):
            sup = med.supplier or sup_list[0]
            loc = med.location or locations[0]
            reorder = med.reorder_level or 200

            # Create 2-4 batches per medication
            num_batches = random.randint(2, 4)
            for b_num in range(num_batches):
                # Vary expiry dates: one near-expiry, one expired for some meds
                if b_num == 0 and idx % 5 == 0:
                    # expired batch
                    exp_date = today - timedelta(days=random.randint(15, 90))
                    qty = random.randint(10, 50)
                elif b_num == 1 and idx % 7 == 0:
                    # near-expiry batch
                    exp_date = today + timedelta(days=random.randint(5, 28))
                    qty = random.randint(20, 80)
                else:
                    # normal future batch
                    exp_date = today + timedelta(days=random.randint(180, 730))
                    qty = random.randint(reorder, reorder * 3)

                mfg_date = exp_date - timedelta(days=365 * random.randint(1, 3))
                batch_number = f'BN-{med.code}-{today.year}-{b_num + 1:02d}'

                if MedicationBatch.objects.filter(medication=med, batch_number=batch_number).exists():
                    continue

                received_date = today - timedelta(days=random.randint(30, 180))

                batch = MedicationBatch.objects.create(
                    medication=med,
                    batch_number=batch_number,
                    lot_number=f'LOT-{random.randint(1000, 9999)}',
                    manufacturing_date=mfg_date if mfg_date < today else None,
                    expiration_date=exp_date,
                    quantity_received=qty,
                    quantity_available=qty,
                    purchase_price=med.purchase_price,
                    supplier=sup,
                    location=loc,
                    received_date=received_date,
                    received_by=admin,
                    purchase_order_ref=f'PO-{random.randint(10000, 99999)}',
                    invoice_number=f'INV-{random.randint(10000, 99999)}',
                    is_active=True,
                )
                batch_count += 1

                # Opening/purchase transaction
                balance = med.current_stock
                tx_ref = f'TXN-{(StockTransaction.objects.count() + 1):07d}'
                StockTransaction.objects.create(
                    medication=med,
                    batch=batch,
                    transaction_type=StockTransaction.TxType.PURCHASE,
                    quantity_in=qty,
                    quantity_out=0,
                    balance_after=balance,
                    unit_cost=med.purchase_price,
                    total_value=med.purchase_price * qty,
                    reference_number=tx_ref,
                    notes=f'Initial stock receipt — batch {batch.batch_number}',
                    performed_by=admin,
                    transaction_date=timezone.make_aware(
                        timezone.datetime.combine(received_date, timezone.datetime.min.time())
                    ),
                )
                tx_count += 1

                # Add a few dispensing transactions for non-expired batches
                if exp_date > today and qty > 10:
                    dispense_count = random.randint(1, 3)
                    for _ in range(dispense_count):
                        disp_qty = random.randint(1, max(1, qty // 10))
                        if disp_qty >= batch.quantity_available:
                            break
                        batch.quantity_available -= disp_qty
                        batch.save()
                        new_balance = med.current_stock
                        tx_ref = f'TXN-{(StockTransaction.objects.count() + 1):07d}'
                        tx_date = today - timedelta(days=random.randint(1, 60))
                        StockTransaction.objects.create(
                            medication=med,
                            batch=batch,
                            transaction_type=StockTransaction.TxType.DISPENSE,
                            quantity_in=0,
                            quantity_out=disp_qty,
                            balance_after=new_balance,
                            unit_cost=med.purchase_price,
                            total_value=med.purchase_price * disp_qty,
                            reference_number=tx_ref,
                            notes='Dispensed to patient',
                            performed_by=admin,
                            transaction_date=timezone.make_aware(
                                timezone.datetime.combine(tx_date, timezone.datetime.min.time())
                            ),
                        )
                        tx_count += 1

        self.stdout.write(self.style.SUCCESS(f'  {batch_count} batches created'))
        self.stdout.write(self.style.SUCCESS(f'  {tx_count} transactions recorded'))

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('Medication Inventory Data Setup Complete'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'  Suppliers:     {Supplier.objects.count()}')
        self.stdout.write(f'  Categories:    {InventoryCategory.objects.count()}')
        self.stdout.write(f'  Locations:     {StorageLocation.objects.count()}')
        self.stdout.write(f'  Medications:   {Medication.objects.count()}')
        self.stdout.write(f'  Batches:       {MedicationBatch.objects.count()}')
        self.stdout.write(f'  Transactions:  {StockTransaction.objects.count()}')

        low = sum(1 for m in Medication.objects.filter(inventory_item__is_active=True) if m.is_low_stock)
        exp = MedicationBatch.objects.filter(
            is_active=True, quantity_available__gt=0, expiration_date__lt=date.today()
        ).count()
        near = MedicationBatch.objects.filter(
            is_active=True, quantity_available__gt=0,
            expiration_date__gt=date.today(),
            expiration_date__lte=date.today() + timedelta(days=30),
        ).count()
        self.stdout.write('')
        self.stdout.write(f'  Low stock:     {low} medications')
        self.stdout.write(f'  Expired:       {exp} batches')
        self.stdout.write(f'  Near expiry:   {near} batches (within 30d)')
        self.stdout.write('')
        self.stdout.write('  Login as Pharmacy Admin to access the module at /med-inventory/')
