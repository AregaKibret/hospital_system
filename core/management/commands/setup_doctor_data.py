"""
Creates realistic mock clinical data for doctor module testing.

- Links bekele.haile and miriam.getachew users to Doctor records
- Creates vital signs, clinical notes, diagnoses, lab/imaging/medication/procedure orders
  across existing visits
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    ClinicalNote, Department, Diagnosis, Doctor,
    ImagingOrder, LabOrder, MedicationOrder, Patient,
    ProcedureOrder, Visit, VitalSign,
)

User = get_user_model()

CLINICAL_NOTES = [
    {
        'type': 'H&P',
        'complaint': 'Persistent headache and elevated blood pressure',
        'content': (
            'Patient presents with a 3-day history of persistent headache, predominantly occipital, '
            'associated with dizziness. No nausea or vomiting. No history of fever or neck stiffness. '
            'PMH: Hypertension diagnosed 2 years ago. Medications: Amlodipine 5mg OD. '
            'Family history: Father had hypertension and stroke. '
            'Examination: BP 158/96 mmHg, Pulse 88 bpm, Temperature 36.8°C. '
            'Cardiovascular: Regular rhythm, no murmurs. CNS: Alert and oriented, no focal deficit.'
        ),
        'assessment': 'Uncontrolled hypertension. Tension-type headache secondary to elevated BP.',
        'plan': (
            'Increase Amlodipine to 10mg OD. Add Lisinopril 10mg OD. '
            'Dietary counseling: low-salt diet. Daily BP monitoring. '
            'Follow-up in 2 weeks. Urgent review if severe headache, visual disturbance, or chest pain.'
        ),
    },
    {
        'type': 'Progress',
        'complaint': 'Follow-up for type 2 diabetes',
        'content': (
            'Patient returns for scheduled follow-up. Reports good medication compliance. '
            'Fasting blood glucose at home averaging 7.2–8.5 mmol/L. No hypoglycemic episodes. '
            'No polyuria, polydipsia, or visual changes. Foot examination normal — no ulcers or neuropathy. '
            'Weight: 74 kg (unchanged from last visit). HbA1c ordered at last visit returned at 7.9%.'
        ),
        'assessment': 'Type 2 diabetes mellitus — suboptimal glycaemic control.',
        'plan': (
            'Increase Metformin to 1000mg BID. Continue dietary modification. '
            'Refer to dietician. Repeat HbA1c in 3 months. Annual eye exam due — refer ophthalmology. '
            'Foot care education reinforced.'
        ),
    },
    {
        'type': 'Consultation',
        'complaint': 'Fever, chills and rigors for 4 days',
        'content': (
            'Referred from OPD with 4-day history of high-grade fever with chills and rigors. '
            'Peak temperature 39.4°C at home. Associated headache, myalgia, and loss of appetite. '
            'No cough, no diarrhoea. Travel history: returned from rural area 5 days ago. '
            'No prior antimalarial treatment. '
            'Examination: Temp 38.9°C, Pulse 102 bpm, RR 20/min. '
            'Pallor present. Spleen palpable 2 cm below costal margin.'
        ),
        'assessment': 'Clinical malaria — probable P. falciparum given recent rural travel.',
        'plan': (
            'Malaria RDT and thick/thin blood film stat. '
            'Start empiric Artemether/Lumefantrine 80/480mg BID × 3 days pending results. '
            'IV fluids if unable to tolerate oral. Paracetamol 500mg TID for fever. '
            'Admit if RDT positive or deteriorates. Review in 24 hours.'
        ),
    },
    {
        'type': 'Progress',
        'complaint': 'Epigastric pain and heartburn',
        'content': (
            'Patient with 2-week history of burning epigastric pain, worse after meals and on lying down. '
            'Associated with acid regurgitation. No dysphagia, no haematemesis, no melaena. '
            'Takes NSAIDs regularly for back pain. '
            'Examination: Mild epigastric tenderness on palpation. No guarding or rigidity. '
            'Bowel sounds normal.'
        ),
        'assessment': 'NSAID-induced peptic ulcer disease / gastritis.',
        'plan': (
            'Stop NSAIDs. Start Omeprazole 40mg OD before breakfast × 8 weeks. '
            'H. pylori testing (CLO test or stool antigen). '
            'Dietary advice: avoid spicy food, caffeine, alcohol. Elevate head of bed. '
            'Refer gastroenterology if no improvement in 4 weeks.'
        ),
    },
]

DIAGNOSES = [
    ('I10', 'Essential Hypertension', 'Chronic'),
    ('E11.9', 'Type 2 Diabetes Mellitus', 'Chronic'),
    ('B54', 'Unspecified Malaria', 'Active'),
    ('J18.9', 'Community-Acquired Pneumonia', 'Active'),
    ('K29.5', 'Gastritis', 'Active'),
    ('A01.0', 'Typhoid Fever', 'Active'),
    ('N39.0', 'Urinary Tract Infection', 'Resolved'),
    ('J06.9', 'Acute Upper Respiratory Tract Infection', 'Resolved'),
    ('D64.9', 'Anaemia, unspecified', 'Active'),
    ('I50.9', 'Heart Failure, unspecified', 'Chronic'),
]

LAB_ORDERS = [
    ('Haematology', 'Complete Blood Count (CBC)'),
    ('Haematology', 'Peripheral Blood Film'),
    ('Chemistry', 'Fasting Blood Glucose'),
    ('Chemistry', 'HbA1c'),
    ('Chemistry', 'Liver Function Tests'),
    ('Chemistry', 'Renal Function Tests'),
    ('Chemistry', 'Serum Electrolytes'),
    ('Chemistry', 'Lipid Panel'),
    ('Microbiology', 'Malaria RDT'),
    ('Microbiology', 'Blood Culture & Sensitivity'),
    ('Microbiology', 'Urine Culture & Sensitivity'),
    ('Microbiology', 'Widal Test'),
    ('Microbiology', 'Stool Microscopy & Culture'),
    ('Serology', 'HIV Screening'),
    ('Serology', 'HBsAg'),
    ('Chemistry', 'Thyroid Function (TSH, T3, T4)'),
]

IMAGING_ORDERS = [
    ('X-Ray', 'Chest', 'Rule out pneumonia / TB'),
    ('Ultrasound', 'Abdomen', 'Hepatosplenomegaly assessment'),
    ('X-Ray', 'Abdomen', 'Bowel obstruction evaluation'),
    ('CT Scan', 'Head', 'Intracranial pathology'),
    ('Ultrasound', 'Pelvis', 'Pelvic organ assessment'),
    ('Echo', 'Heart', 'Cardiac function assessment'),
    ('ECG', 'Heart', 'Arrhythmia / ischaemia screening'),
]

MEDICATIONS = [
    ('Paracetamol', '500mg', 'PO', 'TID', '5 days'),
    ('Amoxicillin', '500mg', 'PO', 'TID', '7 days'),
    ('Metformin', '500mg', 'PO', 'BID', 'Long-term'),
    ('Amlodipine', '5mg', 'PO', 'OD', 'Long-term'),
    ('Lisinopril', '10mg', 'PO', 'OD', 'Long-term'),
    ('Omeprazole', '20mg', 'PO', 'OD', '4 weeks'),
    ('Artemether/Lumefantrine', '80/480mg', 'PO', 'BID', '3 days'),
    ('Ciprofloxacin', '500mg', 'PO', 'BID', '7 days'),
    ('Furosemide', '40mg', 'PO', 'OD', 'Long-term'),
    ('Aspirin', '100mg', 'PO', 'OD', 'Long-term'),
    ('Doxycycline', '100mg', 'PO', 'BID', '7 days'),
    ('Salbutamol', '2.5mg', 'INH', 'Q4-6H PRN', 'PRN'),
]

VITAL_TEMPLATES = [
    {'temperature': Decimal('36.8'), 'bp_systolic': 120, 'bp_diastolic': 78, 'pulse': 72, 'rr': 16, 'spo2': Decimal('98.5'), 'weight': Decimal('68.0'), 'height': Decimal('170.0')},
    {'temperature': Decimal('37.2'), 'bp_systolic': 138, 'bp_diastolic': 88, 'pulse': 84, 'rr': 18, 'spo2': Decimal('97.0'), 'weight': Decimal('75.5'), 'height': Decimal('165.0')},
    {'temperature': Decimal('38.9'), 'bp_systolic': 110, 'bp_diastolic': 70, 'pulse': 104, 'rr': 22, 'spo2': Decimal('96.0'), 'weight': Decimal('60.0'), 'height': Decimal('162.0')},
    {'temperature': Decimal('36.6'), 'bp_systolic': 158, 'bp_diastolic': 96, 'pulse': 88, 'rr': 16, 'spo2': Decimal('99.0'), 'weight': Decimal('82.0'), 'height': Decimal('175.0')},
    {'temperature': Decimal('37.0'), 'bp_systolic': 126, 'bp_diastolic': 82, 'pulse': 76, 'rr': 14, 'spo2': Decimal('98.0'), 'weight': Decimal('55.0'), 'height': Decimal('158.0')},
]


class Command(BaseCommand):
    help = 'Creates mock clinical data for the doctor module demo'

    def handle(self, *args, **options):
        self.stdout.write('Setting up doctor data...')

        # 1. Get/create Cardiology department
        cardiology, _ = Department.objects.get_or_create(name='Cardiology')
        internal_med, _ = Department.objects.get_or_create(name='Internal Medicine')

        # 2. Link bekele.haile to a Doctor record
        bekele_user = self._get_user('bekele.haile')
        bekele_doctor = self._link_doctor(
            user=bekele_user,
            first_name='Bekele',
            last_name='Haile',
            department=internal_med,
            employee_id='DR-001',
        )

        # 3. Link miriam.getachew to a Doctor record
        miriam_user = self._get_user('miriam.getachew')
        self._link_doctor(
            user=miriam_user,
            first_name='Miriam',
            last_name='Getachew',
            department=cardiology,
            employee_id='DR-002',
        )

        # 4. Ensure some visits are linked to bekele's doctor record
        visits = list(Visit.objects.select_related('patient').order_by('-created_at')[:8])
        if not visits:
            self.stdout.write(self.style.WARNING('No visits found — run setup_rbac first to create sample patients.'))
            return

        # Reassign visits to bekele's doctor record
        updated = 0
        for visit in visits[:5]:
            if visit.doctor_id != bekele_doctor.id:
                visit.doctor = bekele_doctor
                visit.save(update_fields=['doctor'])
                updated += 1

        if updated:
            self.stdout.write(f'Linked {updated} existing visits to Dr. Bekele Haile')

        # 5. For each of the first 5 visits, create clinical data
        authored_by = bekele_user if bekele_user else self._get_any_user()
        if not authored_by:
            self.stdout.write(self.style.ERROR('No users found. Aborting.'))
            return

        total_vitals = total_notes = total_dx = total_labs = total_imaging = total_meds = total_procs = 0

        for i, visit in enumerate(visits[:5]):
            # --- Vitals ---
            if not visit.vital_signs.exists():
                vt = VITAL_TEMPLATES[i % len(VITAL_TEMPLATES)]
                VitalSign.objects.create(
                    visit=visit,
                    recorded_by=authored_by,
                    temperature=vt['temperature'],
                    bp_systolic=vt['bp_systolic'],
                    bp_diastolic=vt['bp_diastolic'],
                    pulse=vt['pulse'],
                    respiratory_rate=vt['rr'],
                    spo2=vt['spo2'],
                    weight=vt['weight'],
                    height=vt['height'],
                )
                total_vitals += 1

            # --- Clinical note ---
            if not visit.clinical_notes.exists():
                note_data = CLINICAL_NOTES[i % len(CLINICAL_NOTES)]
                ClinicalNote.objects.create(
                    visit=visit,
                    authored_by=authored_by,
                    note_type=note_data['type'],
                    chief_complaint=note_data['complaint'],
                    content=note_data['content'],
                    assessment=note_data['assessment'],
                    plan=note_data['plan'],
                )
                total_notes += 1

            # --- Diagnoses (1-2 per visit) ---
            if not visit.diagnoses.exists():
                dx_picks = random.sample(DIAGNOSES, min(2, len(DIAGNOSES)))
                for icd, desc, status in dx_picks:
                    Diagnosis.objects.create(
                        visit=visit,
                        authored_by=authored_by,
                        icd_code=icd,
                        description=desc,
                        status=status,
                    )
                    total_dx += 1

            # --- Lab orders (2-3 per visit) ---
            if not visit.lab_orders.exists():
                lab_picks = random.sample(LAB_ORDERS, min(3, len(LAB_ORDERS)))
                for cat, name in lab_picks:
                    prio = 'STAT' if i == 0 else ('Urgent' if i == 1 else 'Routine')
                    status = 'Completed' if i >= 2 else 'Pending'
                    LabOrder.objects.create(
                        visit=visit,
                        ordered_by=authored_by,
                        test_category=cat,
                        test_name=name,
                        priority=prio,
                        status=status,
                        result='Results pending.' if status == 'Pending' else 'Within normal limits.',
                    )
                    total_labs += 1

            # --- Imaging orders ---
            if not visit.imaging_orders.exists() and i < 4:
                img_type, body_part, indication = IMAGING_ORDERS[i % len(IMAGING_ORDERS)]
                ImagingOrder.objects.create(
                    visit=visit,
                    ordered_by=authored_by,
                    imaging_type=img_type,
                    body_part=body_part,
                    clinical_indication=indication,
                    priority='Routine' if i > 0 else 'Urgent',
                    status='Completed' if i >= 2 else 'Pending',
                    report='No acute pathology identified.' if i >= 2 else '',
                )
                total_imaging += 1

            # --- Medications (2-3 per visit) ---
            if not visit.medication_orders.exists():
                med_picks = random.sample(MEDICATIONS, min(3, len(MEDICATIONS)))
                for drug, dose, route, freq, duration in med_picks:
                    status = 'Active' if i <= 2 else 'Completed'
                    MedicationOrder.objects.create(
                        visit=visit,
                        ordered_by=authored_by,
                        drug_name=drug,
                        dosage=dose,
                        route=route,
                        frequency=freq,
                        duration=duration,
                        quantity=random.randint(1, 30),
                        status=status,
                    )
                    total_meds += 1

            # --- Procedure order (first visit only) ---
            if i == 0 and not visit.procedure_orders.exists():
                ProcedureOrder.objects.create(
                    visit=visit,
                    ordered_by=authored_by,
                    procedure_type='Procedure',
                    procedure_code='44950',
                    procedure_name='Appendectomy Consultation',
                    scheduled_date=date.today() + timedelta(days=3),
                    notes='Pre-operative evaluation required. NPO after midnight.',
                    status='Scheduled',
                )
                total_procs += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done. Created: {total_vitals} vital sign sets, {total_notes} notes, '
            f'{total_dx} diagnoses, {total_labs} lab orders, {total_imaging} imaging orders, '
            f'{total_meds} medication orders, {total_procs} procedure orders.'
        ))
        self.stdout.write(f'Login as bekele.haile / Test@1234 and visit /doctor/ to see the module.')

    def _get_user(self, username):
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(self.style.WARNING(f'User {username!r} not found — skipping link.'))
            return None

    def _get_any_user(self):
        return User.objects.filter(is_active=True).first()

    def _link_doctor(self, user, first_name, last_name, department, employee_id):
        # Check if doctor already linked to this user
        if user and hasattr(user, 'doctor_profile') and user.doctor_profile:
            return user.doctor_profile

        # Find or create doctor record by employee_id
        doctor, created = Doctor.objects.get_or_create(
            employee_id=employee_id,
            defaults={
                'first_name': first_name,
                'last_name': last_name,
                'department': department,
                'active': True,
            },
        )
        if user and doctor.user_id != user.pk:
            doctor.user = user
            doctor.save(update_fields=['user'])
            verb = 'Created' if created else 'Linked'
            self.stdout.write(f'{verb} Dr. {first_name} {last_name} -> {user.username}')
        return doctor
