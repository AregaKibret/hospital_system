"""
Seed the database with realistic Ethiopian hospital mock data.
Run: python manage.py seed_data
"""
import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

User = get_user_model()

# ── Ethiopian names ────────────────────────────────────────────────────────────
FIRST_NAMES_M = [
    'Abebe', 'Tadesse', 'Kebede', 'Girma', 'Tesfaye', 'Mulugeta', 'Yohannes',
    'Haile', 'Dawit', 'Solomon', 'Bereket', 'Ermias', 'Nahom', 'Mikael', 'Henok',
]
FIRST_NAMES_F = [
    'Tigist', 'Hiwot', 'Selam', 'Mekdes', 'Bethlehem', 'Sara', 'Rahel',
    'Meron', 'Kidist', 'Almaz', 'Yeshi', 'Ayantu', 'Selamawit', 'Mahlet', 'Miriam',
]
LAST_NAMES = [
    'Alemu', 'Bekele', 'Tesfaye', 'Haile', 'Girma', 'Tadesse', 'Wolde',
    'Gebre', 'Desta', 'Negash', 'Mengiste', 'Zeleke', 'Abate', 'Mersha', 'Demeke',
    'Kassahun', 'Worku', 'Teferi', 'Shiferaw', 'Getachew',
]
ETHIOPIAN_CITIES = ['Addis Ababa', 'Dire Dawa', 'Mekelle', 'Gondar', 'Bahir Dar', 'Adama']
SUBCITIES = ['Bole', 'Kirkos', 'Yeka', 'Arada', 'Gullele', 'Kolfe', 'Lideta', 'Nifas Silk']

BLOOD_GROUPS = ['A+', 'A-', 'B+', 'B-', 'AB+', 'O+', 'O-', 'Unknown']
OCCUPATIONS = ['Farmer', 'Teacher', 'Merchant', 'Government Employee', 'Student', 'Housewife', 'Engineer', 'Driver']

CHIEF_COMPLAINTS = [
    'Fever and headache for 3 days', 'Abdominal pain', 'Chest pain and shortness of breath',
    'Cough and cold for 1 week', 'Back pain', 'Dizziness and nausea',
    'Skin rash', 'Swelling of legs', 'Painful urination', 'Eye pain and redness',
    'Joint pain', 'Vomiting and diarrhea', 'Toothache', 'Ear pain', 'Sore throat',
]

DIAGNOSES = [
    ('J06.9', 'Acute upper respiratory infection'), ('A09', 'Gastroenteritis'),
    ('K29.7', 'Gastritis'), ('I10', 'Essential hypertension'),
    ('E11.9', 'Type 2 diabetes mellitus'), ('J18.9', 'Pneumonia'),
    ('N39.0', 'Urinary tract infection'), ('M54.5', 'Low back pain'),
    ('B54', 'Malaria'), ('A15.0', 'Pulmonary tuberculosis'),
]

LAB_SERVICES_DATA = [
    ('Complete Blood Count', 'CBC', 'Hematology', 'Blood', 250),
    ('Blood Glucose - Fasting', 'FBS', 'Chemistry', 'Blood', 80),
    ('Blood Glucose - Random', 'RBS', 'Chemistry', 'Blood', 80),
    ('Urinalysis', 'U/A', 'Urinalysis', 'Urine', 60),
    ('Malaria Blood Film', 'MBF', 'Parasitology', 'Blood', 100),
    ('Widal Test', 'Widal', 'Serology', 'Blood', 150),
    ('HIV Test', 'HIV', 'Serology', 'Blood', 50),
    ('Hepatitis B Surface Antigen', 'HBsAg', 'Serology', 'Blood', 120),
    ('Liver Function Test', 'LFT', 'Chemistry', 'Blood', 350),
    ('Renal Function Test', 'RFT', 'Chemistry', 'Blood', 300),
    ('Lipid Profile', 'LIPID', 'Chemistry', 'Blood', 400),
    ('Thyroid Function Test', 'TFT', 'Chemistry', 'Blood', 500),
    ('Stool Examination', 'STOOL', 'Parasitology', 'Stool', 80),
    ('Sputum AFB', 'AFB', 'Microbiology', 'Sputum', 100),
    ('ESR', 'ESR', 'Hematology', 'Blood', 70),
    ('CRP', 'CRP', 'Chemistry', 'Blood', 200),
    ('Pregnancy Test (Urine)', 'UPT', 'Immunology', 'Urine', 50),
    ('Blood Type & Cross Match', 'XMATCH', 'Blood Bank', 'Blood', 300),
]

# name, code, modality, body_part, price
IMAGING_SERVICES_DATA = [
    ('Chest X-Ray (PA View)',      'CXR',       'X-Ray',      'Chest',   350),
    ('Abdominal X-Ray (KUB)',      'KUB',       'X-Ray',      'Abdomen', 400),
    ('Skull X-Ray',                'SKULL',     'X-Ray',      'Head',    350),
    ('Limb X-Ray',                 'LIMB-XR',   'X-Ray',      'Limb',    350),
    ('CT Brain (Plain)',           'CT-BRAIN',  'CT Scan',    'Head',    2500),
    ('CT Abdomen & Pelvis',        'CT-AP',     'CT Scan',    'Abdomen', 3500),
    ('MRI Brain',                  'MRI-BRAIN', 'MRI',        'Head',    5000),
    ('MRI Spine',                  'MRI-SPINE', 'MRI',        'Spine',   5500),
    ('Abdominal Ultrasound',       'US-ABD',    'Ultrasound', 'Abdomen', 600),
    ('Obstetric Ultrasound',       'US-OB',     'Ultrasound', 'Pelvis',  700),
    ('Pelvic Ultrasound',          'US-PEL',    'Ultrasound', 'Pelvis',  650),
    ('ECG (12-Lead)',              'ECG',       'ECG',        'Heart',   150),
    ('Echocardiogram',             'ECHO',      'Echo',       'Heart',   1200),
    ('Doppler Study (Lower Limb)', 'DOP-LL',    'Doppler',    'Leg',     900),
]

# name, item_type, category, unit, qty, reorder_level, unit_cost
STORE_ITEMS_DATA = [
    ('EDTA Tubes (Purple Top)',       'Lab Supply', 'Lab Reagents & Consumables', 'box',  40, 10, 350),
    ('Reagent Strips — Glucose',      'Lab Supply', 'Lab Reagents & Consumables', 'box',  25, 8,  620),
    ('Blood Culture Bottles',         'Lab Supply', 'Lab Reagents & Consumables', 'unit', 60, 15, 180),
    ('Microscope Slides',             'Lab Supply', 'Lab Reagents & Consumables', 'box',  30, 10, 250),
    ('Rapid Malaria Test Kits',       'Lab Supply', 'Lab Reagents & Consumables', 'box',  20, 5,  900),
    ('Surgical Sutures (Assorted)',   'Surgical',   'Surgical Supplies',          'box',  35, 10, 450),
    ('Sterile Surgical Gloves',       'Surgical',   'Surgical Supplies',          'box',  50, 15, 320),
    ('Scalpel Blades (#22)',          'Surgical',   'Surgical Supplies',          'box',  40, 10, 280),
    ('IV Cannula (18G)',              'Surgical',   'Surgical Supplies',          'box',  45, 12, 190),
    ('Surgical Gauze Rolls',          'Surgical',   'Surgical Supplies',          'box',  30, 10, 150),
    ('Disposable Syringes (5ml)',     'Medical Supply', 'Medical Consumables',    'box',  80, 20, 120),
    ('Cotton Wool Rolls',             'Medical Supply', 'Medical Consumables',    'roll', 60, 15, 90),
    ('Adhesive Bandages',             'Medical Supply', 'Medical Consumables',    'box',  55, 15, 110),
    ('Face Masks (Surgical)',         'General',    'General Supplies',          'box',  70, 20, 200),
    ('Hand Sanitizer (500ml)',        'General',    'General Supplies',          'bottle', 45, 10, 150),
]

MEDICATIONS_DATA = [
    ('Amoxicillin 500mg Capsule', 'Amoxicillin', 8),
    ('Ciprofloxacin 500mg Tablet', 'Ciprofloxacin', 12),
    ('Metronidazole 400mg Tablet', 'Metronidazole', 5),
    ('Paracetamol 500mg Tablet', 'Paracetamol', 3),
    ('Ibuprofen 400mg Tablet', 'Ibuprofen', 6),
    ('Omeprazole 20mg Capsule', 'Omeprazole', 10),
    ('Metformin 500mg Tablet', 'Metformin', 7),
    ('Amlodipine 5mg Tablet', 'Amlodipine', 9),
    ('Atenolol 50mg Tablet', 'Atenolol', 8),
    ('Artemether/Lumefantrine 80/480mg', 'Coartem', 45),
    ('Prednisolone 5mg Tablet', 'Prednisolone', 4),
    ('Vitamin C 500mg Tablet', 'Vitamin C', 3),
    ('ORS Sachets', 'ORS', 15),
    ('Cotrimoxazole 480mg Tablet', 'Cotrimoxazole', 5),
    ('Doxycycline 100mg Capsule', 'Doxycycline', 10),
]


class Command(BaseCommand):
    help = 'Seed database with realistic Ethiopian hospital mock data'

    def add_arguments(self, parser):
        parser.add_argument('--patients', type=int, default=20, help='Number of patients to create')
        parser.add_argument('--visits', type=int, default=30, help='Number of visits to create')
        parser.add_argument('--clear', action='store_true', help='Clear existing data before seeding')

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== Hospital System Seed Data ===\n'))

        if options['clear']:
            self._clear_data()

        self._setup_rbac()
        departments = self._create_departments()
        users = self._create_users(departments)
        doctors = self._create_doctors(departments, users)
        lab_services = self._create_lab_services()
        self._create_imaging_services()
        self._create_store_inventory()
        self._create_department_stores()
        medications = self._create_medications(departments)
        patients = self._create_patients(options['patients'])
        visits = self._create_visits(patients, departments, doctors, options['visits'])
        self._create_clinical_data(visits, doctors, lab_services, medications, users)

        self.stdout.write(self.style.SUCCESS('\n=== Seed complete ==='))
        self.stdout.write(self.style.SUCCESS('Login credentials:'))
        for uname, pwd in [
            ('admin', 'admin123'), ('dr.abebe', 'doctor123'), ('dr.tigist', 'doctor123'),
            ('receptionist', 'staff123'), ('pharmacist', 'staff123'),
            ('lab_staff', 'staff123'), ('cashier', 'staff123'),
        ]:
            self.stdout.write(f'  {uname:<20} / {pwd}')

    # ── RBAC ──────────────────────────────────────────────────────────────────

    def _setup_rbac(self):
        self.stdout.write('Setting up roles (RBAC)...')
        try:
            from django.core.management import call_command
            call_command('setup_rbac', verbosity=0)
            self.stdout.write(self.style.SUCCESS('  RBAC roles configured'))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  RBAC setup skipped: {e}'))

    # ── Departments ───────────────────────────────────────────────────────────

    def _create_departments(self):
        from core.models import Department
        self.stdout.write('Creating departments...')
        depts_data = [
            ('Outpatient Department (OPD)', 'opd', 'Dr. Abebe Alemu', 'Ground Floor, Block A'),
            ('Emergency Department', 'emergency', 'Dr. Tigist Bekele', 'Ground Floor, Block B'),
            ('Laboratory', 'laboratory', 'Mr. Dawit Haile', 'Block C, Room 101'),
            ('Radiology', 'radiology', 'Dr. Mulugeta Tadesse', 'Block C, Room 201'),
            ('Pharmacy', 'pharmacy', 'Ms. Hiwot Gebre', 'Ground Floor, Block D'),
            ('Internal Medicine', 'clinical', 'Dr. Yohannes Bekele', 'Block A, 2nd Floor'),
            ('Pediatrics', 'pediatrics', 'Dr. Mekdes Zeleke', 'Block B, 2nd Floor'),
            ('Surgery', 'clinical', 'Dr. Haile Worku', 'Block A, 3rd Floor'),
            ('Maternity / Labour', 'maternity', 'Dr. Selam Desta', 'Block E'),
            ('Administration', 'administration', 'Ato Tesfaye Girma', 'Admin Block'),
            ('Finance Department', 'finance', 'Ato Kebede Negash', 'Admin Block, Room 5'),
            ('Nursing Department', 'nursing', 'Sr. Tigist Mersha', 'Block A & B'),
        ]
        depts = {}
        for name, dept_type, head, location in depts_data:
            dept, _ = Department.objects.get_or_create(
                name=name,
                defaults={'dept_type': dept_type, 'head': head, 'location': location, 'is_active': True}
            )
            depts[name] = dept
        self.stdout.write(self.style.SUCCESS(f'  {len(depts)} departments ready'))
        return depts

    # ── Users ─────────────────────────────────────────────────────────────────

    def _create_users(self, departments):
        from core.models import UserProfile
        self.stdout.write('Creating staff users...')

        def get_group(name):
            try:
                return Group.objects.get(name=name)
            except Group.DoesNotExist:
                return Group.objects.create(name=name)

        staff = [
            ('admin', 'System', 'Administrator', 'admin@hospital.et', 'admin123', 'Administrator',
             departments.get('Administration'), 'EMP-001', 'System Administrator', True, True),
            ('dr.abebe', 'Abebe', 'Alemu', 'abebe@hospital.et', 'doctor123', 'Doctor',
             departments.get('Outpatient Department (OPD)'), 'EMP-002', 'General Practitioner', False, True),
            ('dr.tigist', 'Tigist', 'Bekele', 'tigist@hospital.et', 'doctor123', 'Doctor',
             departments.get('Emergency Department'), 'EMP-003', 'Emergency Physician', False, True),
            ('dr.yohannes', 'Yohannes', 'Bekele', 'yohannes@hospital.et', 'doctor123', 'Doctor',
             departments.get('Internal Medicine'), 'EMP-004', 'Internist', False, True),
            ('dr.mekdes', 'Mekdes', 'Zeleke', 'mekdes@hospital.et', 'doctor123', 'Doctor',
             departments.get('Pediatrics'), 'EMP-005', 'Pediatrician', False, True),
            ('receptionist', 'Selam', 'Haile', 'selam@hospital.et', 'staff123', 'Receptionist',
             departments.get('Outpatient Department (OPD)'), 'EMP-006', 'Senior Receptionist', False, True),
            ('pharmacist', 'Hiwot', 'Gebre', 'hiwot@hospital.et', 'staff123', 'Pharmacist',
             departments.get('Pharmacy'), 'EMP-007', 'Senior Pharmacist', False, True),
            ('lab_staff', 'Dawit', 'Haile', 'dawit@hospital.et', 'staff123', 'Laboratory Staff',
             departments.get('Laboratory'), 'EMP-008', 'Lab Technician', False, True),
            ('cashier', 'Kebede', 'Negash', 'kebede@hospital.et', 'staff123', 'Cashier',
             departments.get('Finance Department'), 'EMP-009', 'Senior Cashier', False, True),
            ('nurse1', 'Meron', 'Tadesse', 'meron@hospital.et', 'staff123', 'Nurse',
             departments.get('Nursing Department'), 'EMP-010', 'Senior Nurse', False, True),
        ]

        users = {}
        for (uname, fname, lname, email, pwd, role, dept,
             emp_id, job_title, is_super, is_staff) in staff:
            user, created = User.objects.get_or_create(
                username=uname,
                defaults={
                    'first_name': fname, 'last_name': lname,
                    'email': email, 'is_superuser': is_super,
                    'is_staff': is_staff, 'is_active': True,
                }
            )
            if created:
                user.set_password(pwd)
                user.save()
            else:
                user.first_name = fname
                user.last_name = lname
                user.is_superuser = is_super
                user.is_staff = is_staff
                user.save()
                user.set_password(pwd)
                user.save()

            group = get_group(role)
            user.groups.set([group])

            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'department': dept,
                    'employee_id': emp_id,
                    'job_title': job_title,
                    'access_level': 'full' if is_super else 'standard',
                }
            )
            users[uname] = user

        self.stdout.write(self.style.SUCCESS(f'  {len(users)} users ready'))
        return users

    # ── Doctors ───────────────────────────────────────────────────────────────

    def _create_doctors(self, departments, users):
        from core.models import Doctor
        self.stdout.write('Creating doctor profiles...')
        doctors_data = [
            ('dr.abebe', 'Abebe', 'Alemu', 'Outpatient Department (OPD)', 'EMP-002', '+251911100001'),
            ('dr.tigist', 'Tigist', 'Bekele', 'Emergency Department', 'EMP-003', '+251911100002'),
            ('dr.yohannes', 'Yohannes', 'Bekele', 'Internal Medicine', 'EMP-004', '+251911100003'),
            ('dr.mekdes', 'Mekdes', 'Zeleke', 'Pediatrics', 'EMP-005', '+251911100004'),
        ]
        doctors = []
        for uname, fname, lname, dept_name, emp_id, mobile in doctors_data:
            user = users.get(uname)
            dept = departments.get(dept_name)
            if not dept:
                continue
            doc, _ = Doctor.objects.get_or_create(
                employee_id=emp_id,
                defaults={
                    'user': user, 'first_name': fname, 'last_name': lname,
                    'department': dept, 'mobile': mobile, 'active': True,
                }
            )
            doctors.append(doc)
        self.stdout.write(self.style.SUCCESS(f'  {len(doctors)} doctors ready'))
        return doctors

    # ── Lab Services ──────────────────────────────────────────────────────────

    def _create_lab_services(self):
        from core.models import LabCategory, LabService
        self.stdout.write('Creating lab services...')
        services = []
        # Source section labels -> canonical LabCategory names (matches the
        # mapping used by migration 0042's section->category backfill).
        category_name_map = {
            'Chemistry': 'Clinical Chemistry',
        }
        category_cache = {}
        for name, code, section_str, sample, price in LAB_SERVICES_DATA:
            category_name = category_name_map.get(section_str, section_str)
            if category_name not in category_cache:
                category_cache[category_name], _ = LabCategory.objects.get_or_create(
                    name=category_name, defaults={'is_active': True},
                )
            svc, _ = LabService.objects.get_or_create(
                code=code,
                defaults={
                    'name': name, 'short_name': code,
                    'category': category_cache[category_name],
                    'sample_type': LabService.SampleType.BLOOD if sample == 'Blood' else
                                   LabService.SampleType.URINE if sample == 'Urine' else
                                   LabService.SampleType.STOOL if sample == 'Stool' else
                                   LabService.SampleType.SPUTUM if sample == 'Sputum' else
                                   LabService.SampleType.BLOOD,
                    'standard_price': Decimal(str(price)),
                    'turnaround_hours': 2,
                    'is_active': True,
                }
            )
            services.append(svc)
        self.stdout.write(self.style.SUCCESS(f'  {len(services)} lab services ready'))
        return services

    def _create_imaging_services(self):
        from core.models import ImagingService
        self.stdout.write('Creating imaging services...')
        services = []
        for name, code, modality, body_part, price in IMAGING_SERVICES_DATA:
            svc, _ = ImagingService.objects.get_or_create(
                code=code,
                defaults={
                    'name': name, 'short_name': code,
                    'modality': modality,
                    'body_part': body_part,
                    'standard_price': Decimal(str(price)),
                    'turnaround_hours': 4,
                    'is_active': True,
                }
            )
            services.append(svc)
        self.stdout.write(self.style.SUCCESS(f'  {len(services)} imaging services ready'))
        return services

    # ── General Store / Lab / OR Inventory ────────────────────────────────────

    def _create_store_inventory(self):
        from core.models import (
            Department, EquipmentAsset, InventoryCategory, InventoryItem,
            InventoryTransaction, PurchaseOrder, PurchaseOrderItem, StorageLocation,
            Supplier,
        )
        User = get_user_model()
        admin = User.objects.filter(username='admin').first() or User.objects.filter(is_superuser=True).first()
        if not admin:
            self.stdout.write(self.style.WARNING('  Store inventory seeding skipped: no admin user found'))
            return

        self.stdout.write('Creating store/lab/OR inventory...')

        suppliers = {}
        for name, code in [
            ('MedSupply Ethiopia PLC', 'SUP-001'),
            ('Addis Pharma Distributors', 'SUP-002'),
            ('Global Surgical Supplies', 'SUP-003'),
        ]:
            sup, _ = Supplier.objects.get_or_create(code=code, defaults={'name': name, 'is_active': True})
            suppliers[code] = sup

        locations = {}
        for name, warehouse in [('Main Store', 'Central Warehouse'), ('Lab Store', 'Laboratory'), ('OR Store', 'Operating Room')]:
            loc, _ = StorageLocation.objects.get_or_create(name=name, defaults={'warehouse': warehouse})
            locations[name] = loc

        type_map = {
            'Lab Supply':      ('Lab Store', InventoryItem.ItemType.LAB_SUPPLY),
            'Surgical':        ('OR Store', InventoryItem.ItemType.SURGICAL),
            'Medical Supply':  ('Main Store', InventoryItem.ItemType.MEDICAL_SUPPLY),
            'General':         ('Main Store', InventoryItem.ItemType.GENERAL),
        }

        items = []
        for name, item_type_key, category_name, unit, qty, reorder, cost in STORE_ITEMS_DATA:
            location_name, item_type = type_map[item_type_key]
            category, _ = InventoryCategory.objects.get_or_create(name=category_name)
            sup = suppliers['SUP-001'] if item_type == InventoryItem.ItemType.LAB_SUPPLY else (
                suppliers['SUP-003'] if item_type == InventoryItem.ItemType.SURGICAL else suppliers['SUP-002']
            )
            item, created = InventoryItem.objects.get_or_create(
                name=name,
                defaults={
                    'item_type': item_type,
                    'category': category,
                    'unit': unit,
                    'quantity_in_stock': qty,
                    'reorder_level': reorder,
                    'unit_cost': Decimal(str(cost)),
                    'supplier': sup,
                    'supplier_name': sup.name,
                    'storage_location': locations[location_name],
                    'is_active': True,
                }
            )
            items.append(item)
            if created and item.quantity_in_stock > 0:
                InventoryTransaction.objects.create(
                    inventory_item=item,
                    transaction_type=InventoryTransaction.TxType.ADJUSTMENT_IN,
                    quantity_in=item.quantity_in_stock,
                    balance_after=item.quantity_in_stock,
                    unit_cost=item.unit_cost,
                    reference_number=f'OPEN-{item.pk}',
                    notes='Opening balance at initial stock setup.',
                    performed_by=admin,
                )

        # A handful of realistic movements so every report has something to show
        if items:
            lab_dept = Department.objects.filter(name='Laboratory').first()
            er_dept = Department.objects.filter(name='Emergency Department').first()

            gauze = next((i for i in items if 'Gauze' in i.name and i.item_type == 'Surgical'), items[0])
            if gauze.quantity_in_stock >= 5:
                gauze.quantity_in_stock -= 5
                gauze.save(update_fields=['quantity_in_stock'])
                InventoryTransaction.objects.create(
                    inventory_item=gauze, transaction_type=InventoryTransaction.TxType.ISSUE,
                    quantity_out=5, balance_after=gauze.quantity_in_stock, unit_cost=gauze.unit_cost,
                    reference_number='ISSUE-SEED-1', department=er_dept,
                    notes='Issued to Emergency Department.', performed_by=admin,
                )

            syringes = next((i for i in items if 'Syringe' in i.name), None)
            if syringes and syringes.quantity_in_stock >= 3:
                syringes.quantity_damaged = (syringes.quantity_damaged or 0) + 3
                syringes.quantity_in_stock -= 3
                syringes.save(update_fields=['quantity_in_stock', 'quantity_damaged'])
                InventoryTransaction.objects.create(
                    inventory_item=syringes, transaction_type=InventoryTransaction.TxType.DAMAGED,
                    quantity_out=3, balance_after=syringes.quantity_in_stock, unit_cost=syringes.unit_cost,
                    reference_number='DMG-SEED-1', department=lab_dept,
                    notes='Damaged in transit — box crushed.', performed_by=admin,
                )

            reagent = next((i for i in items if 'Reagent' in i.name), None)
            if reagent:
                reagent.quantity_in_stock += 10
                reagent.save(update_fields=['quantity_in_stock'])
                InventoryTransaction.objects.create(
                    inventory_item=reagent, transaction_type=InventoryTransaction.TxType.PURCHASE,
                    quantity_in=10, balance_after=reagent.quantity_in_stock, unit_cost=reagent.unit_cost,
                    reference_number='PO-SEED-0001', department=lab_dept,
                    notes='Restock delivery received.', performed_by=admin,
                )

        # Purchase orders (assorted statuses) for the Purchase Report
        po_data = [
            (Supplier.objects.get(code='SUP-001').name, PurchaseOrder.Status.RECEIVED, Decimal('12500')),
            (Supplier.objects.get(code='SUP-002').name, PurchaseOrder.Status.APPROVED, Decimal('8600')),
            (Supplier.objects.get(code='SUP-003').name, PurchaseOrder.Status.SUBMITTED, Decimal('15400')),
        ]
        for supplier_name, status, total in po_data:
            po, created = PurchaseOrder.objects.get_or_create(
                supplier_name=supplier_name, status=status, total_amount=total,
                defaults={'created_by': admin, 'notes': 'Seed data purchase order.'},
            )

        # A couple of equipment assets
        if items:
            for name, code, dept_name in [
                ('Autoclave Sterilizer', 'EQ-0001', 'Laboratory'),
                ('Patient Monitor', 'EQ-0002', 'Emergency Department'),
            ]:
                dept = Department.objects.filter(name=dept_name).first()
                EquipmentAsset.objects.get_or_create(
                    asset_code=code,
                    defaults={
                        'name': name, 'department': dept,
                        'status': EquipmentAsset.AssetStatus.ACTIVE,
                        'purchase_price': Decimal('45000'),
                        'created_by': admin,
                    },
                )

        self.stdout.write(self.style.SUCCESS(f'  {len(items)} store items, {len(suppliers)} suppliers, {len(locations)} locations ready'))

    def _create_department_stores(self):
        from core.models import DepartmentStore
        self.stdout.write('Creating department stores...')
        dept_stores_data = [
            ('Emergency Department Store', DepartmentStore.StoreType.ER, 'Ground Floor, Block B'),
            ('Main Ward Store', DepartmentStore.StoreType.WARD, 'Block A, 2nd Floor'),
            ('Operating Room Store', DepartmentStore.StoreType.OR, 'Block A, 3rd Floor'),
        ]
        count = 0
        for name, store_type, location in dept_stores_data:
            _, created = DepartmentStore.objects.get_or_create(
                name=name, defaults={'store_type': store_type, 'location': location, 'is_active': True},
            )
            count += created
        self.stdout.write(self.style.SUCCESS(f'  {len(dept_stores_data)} department stores ready ({count} created)'))

    # ── Medications / Pharmacy Stock ──────────────────────────────────────────

    def _create_medications(self, departments):
        from core.models import PharmacyStock
        self.stdout.write('Creating pharmacy stock...')
        pharmacy_dept = departments.get('Pharmacy')
        items = []
        for drug_name, generic, unit_price in MEDICATIONS_DATA:
            stock, _ = PharmacyStock.objects.get_or_create(
                drug_name=drug_name,
                defaults={
                    'generic_name': generic,
                    'dosage_form': PharmacyStock.DosageForm.TABLET if 'Tablet' in drug_name else
                                   PharmacyStock.DosageForm.CAPSULE if 'Capsule' in drug_name else
                                   PharmacyStock.DosageForm.OTHER if 'Sachet' in drug_name or 'ORS' in drug_name else
                                   PharmacyStock.DosageForm.TABLET,
                    'unit_cost': Decimal(str(unit_price)),
                    'selling_price': Decimal(str(unit_price)),
                    'quantity_in_stock': random.randint(200, 1000),
                    'reorder_level': 50,
                    'expiry_date': date.today() + timedelta(days=random.randint(180, 730)),
                }
            )
            items.append(stock)
        self.stdout.write(self.style.SUCCESS(f'  {len(items)} medications ready'))
        return items

    # ── Patients ──────────────────────────────────────────────────────────────

    def _create_patients(self, count):
        from core.models import Patient
        self.stdout.write(f'Creating {count} patients...')
        patients = []
        for i in range(count):
            sex = random.choice(['Male', 'Female'])
            fname = random.choice(FIRST_NAMES_M if sex == 'Male' else FIRST_NAMES_F)
            lname = random.choice(LAST_NAMES)
            dob = date.today() - timedelta(days=random.randint(365 * 5, 365 * 75))
            city = random.choice(ETHIOPIAN_CITIES)
            p = Patient.objects.create(
                first_name=fname,
                middle_name=random.choice(LAST_NAMES),
                last_name=lname,
                sex=sex,
                date_of_birth=dob,
                mobile=f'+2519{random.randint(10000000, 99999999)}',
                nationality='Ethiopian',
                region='Addis Ababa' if city == 'Addis Ababa' else 'Other',
                city=city,
                subcity=random.choice(SUBCITIES) if city == 'Addis Ababa' else '',
                blood_group=random.choice(BLOOD_GROUPS),
                occupation=random.choice(OCCUPATIONS),
                is_active=True,
            )
            patients.append(p)
        self.stdout.write(self.style.SUCCESS(f'  {len(patients)} patients created'))
        return patients

    # ── Visits ────────────────────────────────────────────────────────────────

    def _create_visits(self, patients, departments, doctors, count):
        from core.models import Visit
        self.stdout.write(f'Creating {count} visits...')
        opd = departments.get('Outpatient Department (OPD)')
        emergency = departments.get('Emergency Department')
        internal = departments.get('Internal Medicine')
        peds = departments.get('Pediatrics')

        dept_doctor_map = []
        for doc in doctors:
            dept_doctor_map.append((doc.department, doc))

        visits = []
        for i in range(count):
            patient = random.choice(patients)
            dept, doctor = random.choice(dept_doctor_map)
            days_ago = random.randint(0, 60)
            created_dt = timezone.now() - timedelta(days=days_ago)
            status = random.choice([
                Visit.Status.COMPLETED, Visit.Status.COMPLETED, Visit.Status.COMPLETED,
                Visit.Status.WAITING_DOCTOR, Visit.Status.CONSULTATION_STARTED,
                Visit.Status.DISCHARGED, Visit.Status.INVESTIGATION_ORDERED,
            ])
            v = Visit.objects.create(
                patient=patient,
                department=dept,
                doctor=doctor,
                visit_type=Visit.VisitType.NEW_VISIT if i % 4 != 0 else Visit.VisitType.REVISIT,
                status=status,
                chief_complaint=random.choice(CHIEF_COMPLAINTS),
            )
            # Fix created_at to a past date
            Visit.objects.filter(pk=v.pk).update(created_at=created_dt, updated_at=created_dt)
            v.refresh_from_db()
            visits.append(v)

        self.stdout.write(self.style.SUCCESS(f'  {len(visits)} visits created'))
        return visits

    # ── Clinical Data ─────────────────────────────────────────────────────────

    def _create_clinical_data(self, visits, doctors, lab_services, medications, users):
        from core.models import (
            Visit, VitalSign, ClinicalNote, Diagnosis, LabOrder,
            Prescription, PrescriptionItem, Invoice, InvoiceItem, Payment,
        )
        admin_user = users.get('admin')
        cashier = users.get('cashier')
        pharmacist = users.get('pharmacist')
        lab_user = users.get('lab_staff')

        self.stdout.write('Creating clinical data (vitals, notes, labs, prescriptions, invoices)...')

        completed_visits = [v for v in visits if v.status in (
            Visit.Status.COMPLETED, Visit.Status.DISCHARGED, Visit.Status.INVESTIGATION_ORDERED
        )]

        inv_count = 0
        rx_count = 0
        lab_count = 0

        for visit in completed_visits:
            doctor = visit.doctor

            # Vitals
            VitalSign.objects.get_or_create(
                visit=visit,
                defaults={
                    'recorded_by': doctor.user or admin_user,
                    'temperature': Decimal(str(round(random.uniform(36.0, 38.9), 1))),
                    'pulse': random.randint(60, 110),
                    'bp_systolic': random.randint(100, 160),
                    'bp_diastolic': random.randint(60, 100),
                    'respiratory_rate': random.randint(14, 22),
                    'spo2': Decimal(str(random.randint(94, 100))),
                    'weight': Decimal(str(round(random.uniform(45, 95), 1))),
                }
            )

            # Clinical note
            icd, diag_name = random.choice(DIAGNOSES)
            ClinicalNote.objects.get_or_create(
                visit=visit,
                note_type=ClinicalNote.NoteType.HP,
                defaults={
                    'authored_by': doctor.user or admin_user,
                    'content': (
                        f'Patient presents with {visit.chief_complaint}.\n'
                        f'Vitals within acceptable range. Physical examination performed.\n'
                        f'Assessment: {diag_name}\n'
                        f'Plan: Prescribed medications and advised follow-up in 1 week.'
                    ),
                }
            )

            # Diagnosis
            Diagnosis.objects.get_or_create(
                visit=visit,
                icd_code=icd,
                defaults={
                    'description': diag_name,
                    'authored_by': doctor.user or admin_user,
                    'status': Diagnosis.Status.ACTIVE,
                }
            )

            # Lab orders (50% of completed visits)
            if random.random() > 0.5 and lab_services:
                num_tests = random.randint(1, 3)
                chosen = random.sample(lab_services, min(num_tests, len(lab_services)))
                for svc in chosen:
                    lo_status = random.choice([
                        LabOrder.Status.PENDING, LabOrder.Status.SAMPLE_COLLECTED,
                        LabOrder.Status.COMPLETED, LabOrder.Status.COMPLETED,
                        LabOrder.Status.RELEASED,
                    ])
                    lo = LabOrder.objects.create(
                        visit=visit,
                        test_name=svc.name,
                        lab_service=svc,
                        ordered_by=doctor.user or admin_user,
                        priority=LabOrder.Priority.ROUTINE,
                        status=lo_status,
                        unit_price=svc.standard_price,
                        payment_status=LabOrder.PaymentStatus.PAID,
                    )
                    if lo_status in (LabOrder.Status.COMPLETED, LabOrder.Status.RELEASED):
                        LabOrder.objects.filter(pk=lo.pk).update(
                            result='Within normal limits. No significant abnormalities detected.',
                            resulted_at=timezone.now() - timedelta(hours=random.randint(1, 12)),
                            released_at=timezone.now() - timedelta(hours=random.randint(0, 6)),
                            released_by=lab_user,
                        )
                    lab_count += 1

            # Prescription (60% of completed visits)
            if random.random() > 0.4 and medications:
                rx = Prescription.objects.create(
                    visit=visit,
                    patient=visit.patient,
                    prescribed_by=doctor.user or admin_user,
                    status=Prescription.Status.SENT,
                    billing_status=Prescription.BillingStatus.PAID,
                    notes='Take with food. Complete the full course.',
                )
                total_rx = Decimal('0')
                num_meds = random.randint(1, 3)
                chosen_meds = random.sample(medications, min(num_meds, len(medications)))
                for stock in chosen_meds:
                    qty = random.randint(6, 30)
                    unit_price = stock.selling_price or Decimal('5')
                    total_line = unit_price * qty
                    total_rx += total_line
                    PrescriptionItem.objects.create(
                        prescription=rx,
                        drug_name=stock.drug_name,
                        dose='1 tablet',
                        frequency=random.choice([
                            PrescriptionItem.Frequency.ONCE_DAILY,
                            PrescriptionItem.Frequency.TWICE_DAILY,
                            PrescriptionItem.Frequency.THREE_DAILY,
                        ]),
                        duration_days=random.randint(5, 14),
                        quantity=qty,
                        route=PrescriptionItem.Route.ORAL,
                        unit_price=unit_price,
                        status=PrescriptionItem.Status.DISPENSED,
                        quantity_dispensed=qty,
                    )

                # Invoice for prescription
                invoice = Invoice.objects.create(
                    patient=visit.patient,
                    visit=visit,
                    created_by=cashier or admin_user,
                    status=Invoice.Status.PAID,
                    payment_type=Invoice.PaymentType.CASH,
                    total_amount=total_rx,
                    paid_amount=total_rx,
                )
                InvoiceItem.objects.create(
                    invoice=invoice,
                    description=f'Medications — Rx #{rx.prescription_number}',
                    service_type=InvoiceItem.ServiceType.MEDICATION,
                    quantity=1,
                    unit_price=total_rx,
                )
                Payment.objects.create(
                    invoice=invoice,
                    amount=total_rx,
                    payment_method=random.choice(['Cash', 'Bank Transfer', 'Mobile Money']),
                    received_by=cashier or admin_user,
                    payment_date=date.today(),
                    notes='Paid in full',
                )
                Prescription.objects.filter(pk=rx.pk).update(
                    billing_amount=total_rx,
                    invoice=invoice,
                )
                rx_count += 1
                inv_count += 1

            # Consultation invoice (80% of completed visits)
            if random.random() > 0.2:
                consult_fee = Decimal(str(random.choice([150, 200, 250, 300, 500])))
                inv = Invoice.objects.create(
                    patient=visit.patient,
                    visit=visit,
                    created_by=cashier or admin_user,
                    status=random.choice([Invoice.Status.PAID, Invoice.Status.PAID, Invoice.Status.ISSUED]),
                    payment_type=Invoice.PaymentType.CASH,
                    total_amount=consult_fee,
                    paid_amount=consult_fee,
                )
                InvoiceItem.objects.create(
                    invoice=inv,
                    description='Consultation Fee — OPD',
                    service_type=InvoiceItem.ServiceType.CONSULTATION,
                    quantity=1,
                    unit_price=consult_fee,
                )
                if inv.status == Invoice.Status.PAID:
                    Payment.objects.create(
                        invoice=inv,
                        amount=consult_fee,
                        payment_method='Cash',
                        received_by=cashier or admin_user,
                        payment_date=date.today(),
                    )
                inv_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'  {lab_count} lab orders, {rx_count} prescriptions, {inv_count} invoices created'
        ))

    def _clear_data(self):
        self.stdout.write(self.style.WARNING('Clearing existing data...'))
        from core.models import (
            Payment, Invoice, InvoiceItem, Prescription, PrescriptionItem,
            LabOrder, VitalSign, ClinicalNote, Diagnosis, Visit, Patient,
            Doctor, PharmacyStock, LabService, Notification, UserSession,
        )
        Payment.objects.all().delete()
        InvoiceItem.objects.all().delete()
        Invoice.objects.all().delete()
        PrescriptionItem.objects.all().delete()
        Prescription.objects.all().delete()
        LabOrder.objects.all().delete()
        VitalSign.objects.all().delete()
        ClinicalNote.objects.all().delete()
        Diagnosis.objects.all().delete()
        Visit.objects.all().delete()
        Patient.objects.all().delete()
        Doctor.objects.all().delete()
        PharmacyStock.objects.all().delete()
        LabService.objects.all().delete()
        Notification.objects.all().delete()
        UserSession.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()
        self.stdout.write(self.style.SUCCESS('  Data cleared'))
