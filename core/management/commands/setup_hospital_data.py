"""
Management command: setup_hospital_data
Creates realistic demo data for all HMS modules:
  Queue, Triage, Pharmacy, Billing, Inventory, HR, Anesthesia
Run: python manage.py setup_hospital_data
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    AnesthesiaRecord, Appointment, Attendance, Department, Dispensing, Doctor,
    DoctorSchedule, Employee, InventoryCategory, InventoryItem, Invoice,
    InvoiceItem, LeaveRequest, MedicationOrder, Patient, Payment, PharmacyStock,
    ProcedureOrder, PurchaseOrder, PurchaseOrderItem, Queue, TriageAssessment,
    Visit,
)

User = get_user_model()
today = date.today()


class Command(BaseCommand):
    help = 'Seed realistic demo data for all HMS modules'

    def handle(self, *args, **options):
        self.stdout.write('Setting up hospital demo data...\n')
        self._setup_pharmacy_stock()
        self._setup_inventory()
        self._setup_employees()
        self._setup_queue_and_triage()
        self._setup_billing()
        self._setup_anesthesia()
        self._setup_receptionist_user()
        self._setup_appointments()
        self.stdout.write(self.style.SUCCESS('\nAll demo data created successfully.'))

    # ── Pharmacy Stock ────────────────────────────────────────────────────────

    def _setup_pharmacy_stock(self):
        drugs = [
            ('Paracetamol 500mg', 'Paracetamol', 'Analgesics', 'Tablet', '500mg', 850, 'Tablets', '2.50', '5.00', 100, '2027-03-31', 'Addis Pharma'),
            ('Amoxicillin 500mg', 'Amoxicillin', 'Antibiotics', 'Capsule', '500mg', 420, 'Capsules', '8.00', '15.00', 50, '2026-12-31', 'Ethiopian Pharma'),
            ('Metformin 500mg', 'Metformin HCl', 'Antidiabetics', 'Tablet', '500mg', 600, 'Tablets', '3.50', '7.00', 80, '2027-06-30', 'Addis Pharma'),
            ('Atenolol 50mg', 'Atenolol', 'Antihypertensives', 'Tablet', '50mg', 380, 'Tablets', '4.00', '8.00', 60, '2027-01-31', 'Ethiopian Pharma'),
            ('Omeprazole 20mg', 'Omeprazole', 'Antacids', 'Capsule', '20mg', 300, 'Capsules', '6.00', '12.00', 50, '2026-11-30', 'Addis Pharma'),
            ('Amlodipine 5mg', 'Amlodipine', 'Antihypertensives', 'Tablet', '5mg', 490, 'Tablets', '5.50', '11.00', 60, '2027-04-30', 'Ethiopian Pharma'),
            ('Ciprofloxacin 500mg', 'Ciprofloxacin', 'Antibiotics', 'Tablet', '500mg', 180, 'Tablets', '12.00', '22.00', 40, '2026-09-30', 'Addis Pharma'),
            ('Metronidazole 500mg', 'Metronidazole', 'Antiparasitics', 'Tablet', '500mg', 260, 'Tablets', '5.00', '10.00', 50, '2027-02-28', 'Ethiopian Pharma'),
            ('Furosemide 40mg', 'Furosemide', 'Diuretics', 'Tablet', '40mg', 320, 'Tablets', '3.00', '6.00', 60, '2027-05-31', 'Addis Pharma'),
            ('ORS Sachets', 'Oral Rehydration Salts', 'Fluids', 'Other', '20.5g', 500, 'Sachets', '3.50', '7.00', 100, '2027-12-31', 'UNICEF Supply'),
            ('IV Normal Saline 500ml', 'Sodium Chloride 0.9%', 'IV Fluids', 'Injection', '0.9% 500ml', 120, 'Bags', '45.00', '90.00', 30, '2027-08-31', 'B.Braun'),
            ('Artemether/Lumefantrine 20/120mg', 'A/L Combination', 'Antimalarials', 'Tablet', '20/120mg', 200, 'Tablets', '18.00', '35.00', 40, '2026-10-31', 'Ethiopian Pharma'),
            ('Doxycycline 100mg', 'Doxycycline', 'Antibiotics', 'Capsule', '100mg', 30, 'Capsules', '7.00', '14.00', 40, '2026-08-31', 'Addis Pharma'),
            ('Aspirin 100mg', 'Acetylsalicylic Acid', 'Antiplatelets', 'Tablet', '100mg', 450, 'Tablets', '1.50', '3.00', 80, '2027-09-30', 'Ethiopian Pharma'),
            ('Prednisolone 5mg', 'Prednisolone', 'Corticosteroids', 'Tablet', '5mg', 280, 'Tablets', '4.50', '9.00', 50, '2027-07-31', 'Addis Pharma'),
        ]
        created = 0
        for (name, generic, cat, form, strength, qty, unit, cost, price, reorder, expiry, supplier) in drugs:
            obj, new = PharmacyStock.objects.get_or_create(
                drug_name=name,
                defaults=dict(
                    generic_name=generic, category=cat, dosage_form=form, strength=strength,
                    quantity_in_stock=qty, unit=unit, unit_cost=Decimal(cost),
                    selling_price=Decimal(price), reorder_level=reorder,
                    expiry_date=date.fromisoformat(expiry), supplier=supplier,
                )
            )
            if new:
                created += 1
        self.stdout.write(f'  Pharmacy stock: {created} drugs created')

    # ── Inventory ─────────────────────────────────────────────────────────────

    def _setup_inventory(self):
        categories = [
            ('Medical Supplies', 'Consumable medical supplies and PPE'),
            ('Equipment', 'Medical equipment and instruments'),
            ('Office Supplies', 'Administrative office supplies'),
            ('Cleaning Supplies', 'Cleaning and sterilization products'),
            ('Linen', 'Bed linen, towels, and gowns'),
        ]
        cat_objs = {}
        for name, desc in categories:
            obj, _ = InventoryCategory.objects.get_or_create(name=name, defaults={'description': desc})
            cat_objs[name] = obj

        items = [
            ('Surgical Gloves (Large)', 'Medical Supplies', 'SG-L', 'Pairs', 450, '12.00', 100),
            ('Surgical Gloves (Medium)', 'Medical Supplies', 'SG-M', 'Pairs', 520, '12.00', 100),
            ('Disposable Syringes 5ml', 'Medical Supplies', 'SYR-5', 'Units', 800, '4.50', 200),
            ('Disposable Syringes 10ml', 'Medical Supplies', 'SYR-10', 'Units', 600, '5.50', 150),
            ('IV Cannula 18G', 'Medical Supplies', 'IVC-18', 'Units', 350, '18.00', 80),
            ('IV Cannula 20G', 'Medical Supplies', 'IVC-20', 'Units', 420, '18.00', 80),
            ('Bandage Roll 10cm', 'Medical Supplies', 'BND-10', 'Rolls', 280, '15.00', 50),
            ('Cotton Wool 500g', 'Medical Supplies', 'CTN-500', 'Packets', 150, '35.00', 30),
            ('Urinary Catheter 14Fr', 'Medical Supplies', 'UC-14', 'Units', 80, '55.00', 20),
            ('Nasogastric Tube 16Fr', 'Medical Supplies', 'NGT-16', 'Units', 45, '45.00', 15),
            ('Blood Pressure Cuff (Adult)', 'Equipment', 'BPC-ADL', 'Units', 8, '850.00', 2),
            ('Stethoscope', 'Equipment', 'STETH', 'Units', 12, '450.00', 3),
            ('Pulse Oximeter', 'Equipment', 'SPO2', 'Units', 6, '1200.00', 2),
            ('Thermometer (Digital)', 'Equipment', 'THERM-D', 'Units', 15, '180.00', 3),
            ('Bleach Solution 5L', 'Cleaning Supplies', 'BLC-5', 'Bottles', 40, '65.00', 10),
            ('Hand Sanitizer 500ml', 'Cleaning Supplies', 'HSAN-500', 'Bottles', 85, '45.00', 20),
            ('Patient Gown', 'Linen', 'PGN', 'Pieces', 120, '95.00', 30),
            ('Bed Sheet (Single)', 'Linen', 'BSH-SG', 'Pieces', 95, '120.00', 25),
            ('A4 Paper Ream', 'Office Supplies', 'A4-RM', 'Reams', 35, '280.00', 10),
            ('Patient Files', 'Office Supplies', 'PFILE', 'Units', 200, '25.00', 50),
        ]
        created = 0
        for (name, cat_name, sku, unit, qty, cost, reorder) in items:
            obj, new = InventoryItem.objects.get_or_create(
                sku=sku,
                defaults=dict(
                    name=name, category=cat_objs.get(cat_name),
                    unit=unit, quantity_in_stock=qty,
                    unit_cost=Decimal(cost), reorder_level=reorder,
                    supplier_name='General Medical Supplies Ltd',
                )
            )
            if new:
                created += 1
        self.stdout.write(f'  Inventory: {created} items created')

        # Create a sample purchase order
        admin_user = User.objects.filter(is_superuser=True).first() or User.objects.first()
        if admin_user:
            po, new = PurchaseOrder.objects.get_or_create(
                po_number='PO-202606-0001',
                defaults=dict(
                    supplier_name='General Medical Supplies Ltd',
                    supplier_contact='info@gms.et | +251 11 123 4567',
                    created_by=admin_user,
                    status='Submitted',
                    notes='Monthly restocking order',
                    total_amount=Decimal('0'),
                    expected_delivery=today + timedelta(days=7),
                )
            )
            if new:
                items_data = [
                    ('Surgical Gloves (Large)', 'SG-L', 200, '12.00'),
                    ('Disposable Syringes 5ml', 'SYR-5', 500, '4.50'),
                    ('IV Cannula 18G', 'IVC-18', 100, '18.00'),
                ]
                total = Decimal('0')
                for iname, sku, qty, cost in items_data:
                    inv_item = InventoryItem.objects.filter(sku=sku).first()
                    item_total = Decimal(str(qty)) * Decimal(cost)
                    PurchaseOrderItem.objects.create(
                        purchase_order=po, inventory_item=inv_item,
                        item_name=iname, quantity_ordered=qty,
                        unit_cost=Decimal(cost), total=item_total,
                    )
                    total += item_total
                po.total_amount = total
                po.save()
                self.stdout.write('  Purchase order created')

    # ── Employees ─────────────────────────────────────────────────────────────

    def _setup_employees(self):
        dept_map = {d.name: d for d in Department.objects.all()}
        staff_data = [
            ('almaz.tadesse', 'Almaz', 'Tadesse', 'Nurse', 'Nurse', 'Emergency', '2023-01-15', '18000'),
            ('habtamu.kassa', 'Habtamu', 'Kassa', 'Pharmacist', 'Pharmacy Admin', 'Pharmacy', '2022-06-01', '32000'),
            ('tigist.worku', 'Tigist', 'Worku', 'Cashier', 'Cashier', 'Finance', '2023-09-01', '15000'),
            ('dawit.solomon', 'Dawit', 'Solomon', 'Lab Technician', 'Laboratory Staff', 'Laboratory', '2021-03-10', '25000'),
            ('selamawit.getnet', 'Selamawit', 'Getnet', 'Radiographer', 'Radiologist', 'Radiology', '2022-11-15', '28000'),
            ('yonas.tesfaye', 'Yonas', 'Tesfaye', 'HR Officer', 'HR Staff', 'Administration', '2020-07-20', '22000'),
        ]
        created = 0
        for (uname, fname, lname, position, role_name, dept_name, hire_str, salary) in staff_data:
            user, _ = User.objects.get_or_create(
                username=uname,
                defaults=dict(first_name=fname, last_name=lname, email=f'{uname}@hospital.et'),
            )
            if user.pk and not user.has_usable_password():
                user.set_password('HMS@2024!')
                user.save()

            # Assign to group
            try:
                group = Group.objects.get(name=role_name)
                user.groups.add(group)
            except Group.DoesNotExist:
                pass

            dept = dept_map.get(dept_name)
            emp, new = Employee.objects.get_or_create(
                user=user,
                defaults=dict(
                    department=dept, position=position,
                    employment_type='Permanent', employment_status='Active',
                    hire_date=date.fromisoformat(hire_str),
                    basic_salary=Decimal(salary),
                )
            )
            if new:
                created += 1
                # Add attendance for past 5 working days
                for i in range(1, 6):
                    d = today - timedelta(days=i)
                    if d.weekday() < 5:  # Mon-Fri
                        from datetime import time
                        Attendance.objects.get_or_create(
                            employee=emp, date=d,
                            defaults=dict(
                                time_in=time(8, 0) if i != 3 else time(8, 35),
                                time_out=time(17, 0),
                                status='Present' if i != 3 else 'Late',
                            )
                        )

        # Create a pending leave request
        first_emp = Employee.objects.filter(employment_status='Active').first()
        if first_emp:
            LeaveRequest.objects.get_or_create(
                employee=first_emp,
                start_date=today + timedelta(days=7),
                defaults=dict(
                    leave_type='Annual',
                    end_date=today + timedelta(days=13),
                    days_requested=5,
                    reason='Family visit and rest',
                    status='Pending',
                )
            )

        self.stdout.write(f'  Employees: {created} created')

    # ── Queue & Triage ────────────────────────────────────────────────────────

    def _setup_queue_and_triage(self):
        visits = list(Visit.objects.select_related('patient').order_by('-created_at')[:8])
        if not visits:
            self.stdout.write('  Queue: no visits found, skipping')
            return

        nurse = User.objects.filter(groups__name='Nurse').first() or User.objects.filter(is_staff=True).first()
        if not nurse:
            nurse = User.objects.first()

        statuses = ['Waiting', 'Waiting', 'Called', 'In Progress', 'Completed', 'Waiting', 'Completed', 'No Show']
        for i, visit in enumerate(visits):
            q, new = Queue.objects.get_or_create(
                visit=visit,
                defaults=dict(
                    queue_number=i + 1,
                    status=statuses[i % len(statuses)],
                )
            )
            # Add triage to first 4 visits
            if i < 4 and nurse:
                severity = str(min(i + 2, 5))  # 2, 3, 4, 5
                complaints = [
                    'Chest pain with dyspnea, onset 2 hours ago',
                    'High fever 39.5°C, headache, body aches',
                    'Abdominal pain, right lower quadrant',
                    'Follow-up for hypertension medication review',
                ]
                TriageAssessment.objects.get_or_create(
                    visit=visit,
                    defaults=dict(
                        triaged_by=nurse,
                        severity=severity,
                        chief_complaint=complaints[i],
                        temperature=Decimal('37.5') + Decimal(str(i * 0.5)),
                        bp_systolic=120 + i * 5,
                        bp_diastolic=80 + i * 2,
                        pulse=78 + i * 4,
                        spo2=Decimal('98.0') - Decimal(str(i * 0.5)),
                    )
                )

        self.stdout.write(f'  Queue: {len(visits)} entries, 4 triage assessments')

    # ── Billing ───────────────────────────────────────────────────────────────

    def _setup_billing(self):
        cashier = User.objects.filter(groups__name='Cashier').first() or User.objects.filter(is_staff=True).first()
        if not cashier:
            cashier = User.objects.first()
        if not cashier:
            self.stdout.write('  Billing: no user found, skipping')
            return

        patients = list(Patient.objects.order_by('-created_at')[:5])
        if not patients:
            self.stdout.write('  Billing: no patients found, skipping')
            return

        invoice_specs = [
            (patients[0], 'Paid',    [('Consultation', 'Consultation', 1, '300'), ('CBC', 'Laboratory', 1, '250')]),
            (patients[1], 'Issued',  [('OPD Consultation', 'Consultation', 1, '300'), ('Urinalysis', 'Laboratory', 1, '150'), ('Metformin 500mg ×30', 'Medication', 30, '7')]),
            (patients[2], 'Partial', [('Emergency Consultation', 'Consultation', 1, '500'), ('Chest X-Ray', 'Imaging', 1, '500'), ('IV Fluids (3 bags)', 'Medication', 3, '90')]),
            (patients[3], 'Draft',   [('OPD Consultation', 'Consultation', 1, '300')]),
        ]

        created = 0
        for (patient, status, line_items) in invoice_specs:
            if Invoice.objects.filter(patient=patient).exists():
                continue
            inv = Invoice.objects.create(
                patient=patient,
                created_by=cashier,
                status=status,
                due_date=today + timedelta(days=30),
            )
            total = Decimal('0')
            for desc, svc_type, qty, price in line_items:
                item_total = Decimal(str(qty)) * Decimal(price)
                InvoiceItem.objects.create(
                    invoice=inv, description=desc, service_type=svc_type,
                    quantity=Decimal(str(qty)), unit_price=Decimal(price), total=item_total,
                )
                total += item_total
            inv.total_amount = total

            if status == 'Paid':
                inv.paid_amount = total
                Payment.objects.create(
                    invoice=inv, amount=total, payment_method='Cash',
                    received_by=cashier, payment_date=today - timedelta(days=1),
                )
            elif status == 'Partial':
                partial = (total / 2).quantize(Decimal('0.01'))
                inv.paid_amount = partial
                Payment.objects.create(
                    invoice=inv, amount=partial, payment_method='Mobile Money',
                    reference_number='TXN-001234', received_by=cashier, payment_date=today,
                )
            inv.save()
            created += 1

        # Dispense a medication to first patient with a medication order
        stock = PharmacyStock.objects.filter(quantity_in_stock__gt=0).first()
        med_order = MedicationOrder.objects.filter(status='Active').first()
        if stock and med_order and not hasattr(med_order, 'dispensing'):
            dispensing_user = User.objects.filter(groups__name__in=['Pharmacy Admin', 'Pharmacy Sales']).first() or cashier
            Dispensing.objects.get_or_create(
                medication_order=med_order,
                defaults=dict(
                    pharmacy_stock=stock,
                    patient=med_order.visit.patient,
                    drug_name=stock.drug_name,
                    quantity_dispensed=30,
                    unit_price=stock.selling_price,
                    total_amount=stock.selling_price * 30,
                    dispensed_by=dispensing_user,
                    status='Dispensed',
                )
            )

        self.stdout.write(f'  Billing: {created} invoices created')

    # ── Anesthesia ────────────────────────────────────────────────────────────

    def _setup_anesthesia(self):
        procedure = ProcedureOrder.objects.filter(status='Completed').first()
        if not procedure:
            self.stdout.write('  Anesthesia: no completed procedures found, skipping')
            return

        anesthesiologist = (
            User.objects.filter(groups__name='Anesthesia Team').first()
            or User.objects.filter(is_staff=True).first()
            or User.objects.first()
        )
        if not anesthesiologist:
            return

        AnesthesiaRecord.objects.get_or_create(
            visit=procedure.visit,
            anesthesiologist=anesthesiologist,
            defaults=dict(
                procedure_order=procedure,
                anesthesia_type='General',
                asa_classification='ASA II',
                pre_op_assessment='Patient assessed pre-operatively. BP 130/85, HR 78, SpO2 98% on room air. Fasted for 8 hours. Airway Mallampati class II. Consented for general anesthesia.',
                intra_op_notes='Induction with Propofol 2mg/kg IV. Intubated with size 7.5 ETT. Maintained with Isoflurane 1-2% in O2/N2O. Vecuronium 0.1mg/kg for muscle relaxation. Fentanyl 2mcg/kg for analgesia. Stable vitals throughout.',
                post_op_notes='Extubated in theatre after full reversal. GCS 15, SpO2 99% on 2L O2 via nasal prongs. Transferred to recovery room. Pain score 3/10.',
                complications='None',
                duration_minutes=95,
            )
        )
        self.stdout.write('  Anesthesia: 1 record created')

    # ── Receptionist User ─────────────────────────────────────────────────────

    def _setup_receptionist_user(self):
        user, created = User.objects.get_or_create(
            username='sara.abdulahi',
            defaults=dict(
                first_name='Sara',
                last_name='Abdulahi',
                email='sara.abdulahi@hospital.et',
                is_active=True,
            )
        )
        if created or not user.has_usable_password():
            user.set_password('HMS@2024!')
            user.save()

        try:
            group = Group.objects.get(name='Receptionist')
            user.groups.set([group])
        except Group.DoesNotExist:
            pass

        dept = Department.objects.filter(name__icontains='Admin').first() or Department.objects.first()
        Employee.objects.get_or_create(
            user=user,
            defaults=dict(
                department=dept,
                position='Front Desk Receptionist',
                employment_type='Permanent',
                employment_status='Active',
                hire_date=today - timedelta(days=365),
                basic_salary=Decimal('16000'),
            )
        )
        status = 'created' if created else 'already exists'
        self.stdout.write(
            f'  Receptionist user: sara.abdulahi ({status}) — password: HMS@2024!'
        )

    # ── Appointments ──────────────────────────────────────────────────────────

    def _setup_appointments(self):
        receptionist = User.objects.filter(username='sara.abdulahi').first() \
            or User.objects.filter(is_staff=True).first() \
            or User.objects.first()
        if not receptionist:
            self.stdout.write('  Appointments: no user found, skipping')
            return

        doctors = list(Doctor.objects.filter(active=True).select_related('department')[:6])
        patients = list(Patient.objects.order_by('?')[:10])
        if not doctors or not patients:
            self.stdout.write('  Appointments: no doctors or patients found, skipping')
            return

        # Create doctor schedules (Mon–Fri) for each active doctor
        sched_created = 0
        for doctor in doctors:
            for day in range(5):  # Mon=0 … Fri=4
                _, new = DoctorSchedule.objects.get_or_create(
                    doctor=doctor,
                    day_of_week=day,
                    defaults=dict(
                        start_time='08:00',
                        end_time='17:00',
                        slot_duration_minutes=20,
                        max_appointments=24,
                        is_active=True,
                    )
                )
                if new:
                    sched_created += 1

        # Spread appointments across past 3 days + today + next 5 days
        appt_specs = [
            # (days_offset, time,  type,               status,        complaint)
            (-3, '09:00', 'New Consultation',  'Completed',  'Persistent headache and dizziness'),
            (-3, '10:20', 'Follow-up',         'Completed',  'Hypertension medication review'),
            (-3, '11:40', 'New Consultation',  'No Show',    'Abdominal pain upper right quadrant'),
            (-2, '08:40', 'Follow-up',         'Completed',  'Post-op wound check'),
            (-2, '10:00', 'New Consultation',  'Completed',  'Chest tightness and shortness of breath'),
            (-2, '14:00', 'Emergency',         'Completed',  'High fever 40°C, convulsions'),
            (-1, '09:20', 'New Consultation',  'Completed',  'Lower back pain radiating to leg'),
            (-1, '11:00', 'Follow-up',         'Cancelled',  'Diabetes follow-up'),
            (-1, '15:30', 'Review',            'Completed',  'Lab results review — anemia workup'),
            ( 0, '08:00', 'New Consultation',  'Confirmed',  'Recurrent sore throat and tonsillar swelling'),
            ( 0, '09:20', 'Follow-up',         'Waiting',    'Hypertension 3-month check'),
            ( 0, '10:40', 'New Consultation',  'Waiting',    'Skin rash and itching'),
            ( 0, '11:00', 'Emergency',         'In Progress','Acute chest pain — rule out MI'),
            ( 0, '14:00', 'Procedure',         'Scheduled',  'Minor laceration repair'),
            ( 0, '15:20', 'Follow-up',         'Scheduled',  'Post-partum check'),
            ( 1, '08:40', 'New Consultation',  'Scheduled',  'Persistent cough > 3 weeks'),
            ( 1, '10:00', 'Follow-up',         'Scheduled',  'Thyroid function review'),
            ( 1, '11:20', 'New Consultation',  'Scheduled',  'Vision problems and headache'),
            ( 2, '09:00', 'Follow-up',         'Scheduled',  'Post-surgery follow-up week 2'),
            ( 3, '10:20', 'New Consultation',  'Scheduled',  'Arthritis pain management'),
            ( 4, '08:00', 'Review',            'Scheduled',  'Annual health check-up'),
            ( 5, '09:40', 'New Consultation',  'Scheduled',  'Pediatric growth and development'),
        ]

        created = 0
        for i, (offset, time_str, appt_type, status, complaint) in enumerate(appt_specs):
            appt_date = today + timedelta(days=offset)
            doctor = doctors[i % len(doctors)]
            patient = patients[i % len(patients)]

            if Appointment.objects.filter(
                doctor=doctor,
                appointment_date=appt_date,
                appointment_time=time_str,
            ).exists():
                continue

            from datetime import time as time_type
            h, m = map(int, time_str.split(':'))

            Appointment.objects.create(
                patient=patient,
                doctor=doctor,
                department=doctor.department,
                appointment_date=appt_date,
                appointment_time=time_type(h, m),
                appointment_type=appt_type,
                status=status,
                chief_complaint=complaint,
                created_by=receptionist,
            )
            created += 1

        self.stdout.write(
            f'  Appointments: {created} created, {sched_created} schedules created'
        )
