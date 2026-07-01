from django.core.management.base import BaseCommand

from core.models import Department, ORRoom, ProcedureCategory, ProcedureMaster


class Command(BaseCommand):
    help = 'Seed sample procedure categories, procedure master list, and OR rooms'

    def handle(self, *args, **options):
        self._seed_categories()
        self._seed_or_rooms()
        self._seed_procedures()
        self.stdout.write(self.style.SUCCESS('Surgery data seeded successfully.'))

    def _seed_categories(self):
        self.stdout.write('--- Seeding procedure categories ---')
        cats = [
            ('General Surgery',     'Abdominal and general surgical procedures'),
            ('Orthopedic Surgery',  'Bone, joint, and musculoskeletal procedures'),
            ('Cardiac Surgery',     'Heart and vascular surgical procedures'),
            ('Neurosurgery',        'Brain, spine, and nervous system procedures'),
            ('Obstetrics & Gynecology', 'Maternity and gynecological procedures'),
            ('Ophthalmology',       'Eye surgical procedures'),
            ('ENT Surgery',         'Ear, nose, and throat procedures'),
            ('Urology',             'Urinary tract and kidney procedures'),
            ('Plastic Surgery',     'Reconstructive and plastic procedures'),
            ('Minor Procedures',    'Minor clinical procedures performed at bedside or minor OR'),
        ]
        for name, desc in cats:
            obj, created = ProcedureCategory.objects.get_or_create(name=name, defaults={'description': desc})
            verb = 'created' if created else 'exists '
            self.stdout.write(f'  {verb}  {name}')

    def _seed_or_rooms(self):
        self.stdout.write('--- Seeding operating rooms ---')
        rooms = [
            ('OR-1',        'general',     '1st Floor, Surgical Wing',  4),
            ('OR-2',        'general',     '1st Floor, Surgical Wing',  4),
            ('OR-3 (Ortho)','orthopedic',  '1st Floor, Surgical Wing',  4),
            ('OR-4 (Cardiac)','cardiac',   '2nd Floor, Cardiac Unit',   6),
            ('OR-5 (Neuro)', 'neuro',      '2nd Floor, Neuro Wing',     5),
            ('OR-6 (Maternity)','general', 'Maternity Ward, 3rd Floor', 4),
            ('Minor OR',    'procedure',   'Outpatient Block',          2),
            ('Emergency OR','emergency',   'Emergency Department',      4),
        ]
        for name, rtype, location, cap in rooms:
            obj, created = ORRoom.objects.get_or_create(
                name=name,
                defaults={
                    'room_type': rtype,
                    'location': location,
                    'capacity': cap,
                    'is_active': True,
                }
            )
            verb = 'created' if created else 'exists '
            self.stdout.write(f'  {verb}  {name}')

    def _seed_procedures(self):
        self.stdout.write('--- Seeding procedure master list ---')

        gen_cat = ProcedureCategory.objects.filter(name='General Surgery').first()
        orth_cat = ProcedureCategory.objects.filter(name='Orthopedic Surgery').first()
        card_cat = ProcedureCategory.objects.filter(name='Cardiac Surgery').first()
        neuro_cat = ProcedureCategory.objects.filter(name='Neurosurgery').first()
        ob_cat = ProcedureCategory.objects.filter(name='Obstetrics & Gynecology').first()
        eye_cat = ProcedureCategory.objects.filter(name='Ophthalmology').first()
        minor_cat = ProcedureCategory.objects.filter(name='Minor Procedures').first()

        try:
            gen_dept = Department.objects.filter(name__icontains='surgical').first()
            or_dept = Department.objects.filter(name__icontains='operating').first()
        except Exception:
            gen_dept = None
            or_dept = None

        procedures = [
            # (code, name, category, complexity, duration_min, anesthesia, proc_price, surgeon_fee, anesthesia_fee, facility_fee, consumable_charges)
            ('APPEN-001', 'Appendectomy', gen_cat, 'major', 90, 'General', 8000, 5000, 3000, 4000, 2000),
            ('CHOLE-001', 'Laparoscopic Cholecystectomy', gen_cat, 'major', 120, 'General', 12000, 7000, 4000, 5000, 3000),
            ('HERNI-001', 'Inguinal Hernia Repair', gen_cat, 'moderate', 90, 'Spinal', 7000, 4500, 2500, 3500, 1500),
            ('GASTR-001', 'Gastrectomy (Partial)', gen_cat, 'critical', 240, 'General', 25000, 15000, 8000, 10000, 5000),
            ('COLON-001', 'Colectomy', gen_cat, 'major', 180, 'General', 20000, 12000, 7000, 8000, 4000),
            ('THY-001', 'Thyroidectomy (Total)', gen_cat, 'major', 150, 'General', 15000, 9000, 5000, 6000, 3000),

            ('HIPRE-001', 'Total Hip Replacement', orth_cat, 'major', 180, 'Spinal', 40000, 20000, 8000, 12000, 15000),
            ('KNERE-001', 'Total Knee Replacement', orth_cat, 'major', 150, 'Spinal', 35000, 18000, 7000, 10000, 12000),
            ('ORIF-001', 'ORIF — Tibia Fracture', orth_cat, 'moderate', 120, 'Spinal', 18000, 10000, 5000, 7000, 6000),
            ('AMPUTA-001', 'Below Knee Amputation', orth_cat, 'major', 120, 'Spinal', 15000, 9000, 5000, 6000, 3000),

            ('CABG-001', 'Coronary Artery Bypass Graft (CABG)', card_cat, 'critical', 360, 'General', 150000, 60000, 30000, 40000, 20000),
            ('VALVE-001', 'Mitral Valve Replacement', card_cat, 'critical', 300, 'General', 120000, 50000, 25000, 35000, 15000),

            ('CRANI-001', 'Craniotomy for Brain Tumor', neuro_cat, 'critical', 360, 'General', 80000, 40000, 20000, 25000, 10000),
            ('DISC-001', 'Lumbar Discectomy', neuro_cat, 'major', 180, 'General', 30000, 15000, 8000, 10000, 4000),

            ('CS-001', 'Caesarean Section (Primary)', ob_cat, 'major', 75, 'Spinal', 10000, 6000, 3000, 4000, 2000),
            ('TAH-001', 'Total Abdominal Hysterectomy', ob_cat, 'major', 150, 'General', 15000, 9000, 5000, 6000, 3000),
            ('LAP-001', 'Diagnostic Laparoscopy', ob_cat, 'moderate', 60, 'General', 8000, 5000, 3000, 3500, 2000),

            ('PHACO-001', 'Phacoemulsification (Cataract)', eye_cat, 'moderate', 45, 'Local', 12000, 8000, 2000, 3000, 4000),

            ('INC-001', 'Incision & Drainage (I&D)', minor_cat, 'minor', 20, 'Local', 1500, 1000, 0, 500, 200),
            ('WOUND-001', 'Wound Debridement', minor_cat, 'minor', 30, 'Local', 2000, 1200, 0, 600, 400),
            ('BIOPSY-001', 'Excisional Biopsy', minor_cat, 'minor', 30, 'Local', 3000, 1800, 0, 800, 500),
        ]

        for (code, name, cat, complexity, duration, anesthesia,
             proc_price, surgeon_fee, anes_fee, facility_fee, consumable_charges) in procedures:
            obj, created = ProcedureMaster.objects.get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'category': cat,
                    'complexity': complexity,
                    'estimated_duration_minutes': duration,
                    'required_anesthesia_type': anesthesia,
                    'procedure_price': proc_price,
                    'surgeon_fee': surgeon_fee,
                    'anesthesia_fee': anes_fee,
                    'facility_fee': facility_fee,
                    'consumable_charges': consumable_charges,
                    'is_active': True,
                }
            )
            verb = 'created' if created else 'exists '
            self.stdout.write(f'  {verb}  [{code}] {name}')
