"""
Seed the HospitalProfile, SystemModule, SystemVersion, and LicenseInfo tables
with sensible defaults for a fresh deployment.

Usage:
    python manage.py setup_hospital_config
    python manage.py setup_hospital_config --force   # re-seed even if data exists
"""
import datetime

from django.core.management.base import BaseCommand

from core.models import HospitalProfile, LicenseInfo, SystemModule, SystemVersion


MODULES = [
    # (name, label, category, is_core, order)
    ('registration',    'Patient Registration',      'administrative', True,  1),
    ('appointments',    'Appointments',              'administrative', False, 2),
    ('queue',           'Queue Management',          'administrative', False, 3),
    ('consultation',    'Consultation / EMR',        'clinical',       True,  1),
    ('physical_exam',   'Physical Examination',      'clinical',       False, 2),
    ('prescriptions',   'Prescriptions',             'clinical',       False, 3),
    ('nursing',         'Nursing',                   'clinical',       False, 4),
    ('admission',       'Inpatient Admission',       'clinical',       False, 5),
    ('patient_flow',    'Patient Flow',              'clinical',       False, 6),
    ('laboratory',      'Laboratory',                'diagnostic',     False, 1),
    ('radiology',       'Radiology / Imaging',       'diagnostic',     False, 2),
    ('pharmacy',        'Pharmacy (Retail POS)',      'operations',     False, 1),
    ('med_inventory',   'Medication Inventory',      'operations',     False, 2),
    ('inventory',       'General Inventory',         'operations',     False, 3),
    ('dept_pharmacy',   'Department Pharmacy',       'operations',     False, 4),
    ('surgery',         'Surgery / Operating Room',  'operations',     False, 5),
    ('billing',         'Billing & Payments',        'financial',      True,  1),
    ('certificates',    'Medical Certificates',      'administrative', False, 4),
    ('hr',              'Human Resources',           'hr',             False, 1),
    ('reports',         'Reports',                   'administrative', True,  5),
    ('audit',           'Audit Log',                 'administrative', True,  6),
]


class Command(BaseCommand):
    help = 'Seed hospital configuration: profile, modules, version, license.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Re-seed even if data exists')

    def handle(self, *args, **options):
        force = options['force']
        self._seed_profile(force)
        self._seed_modules(force)
        self._seed_version(force)
        self.stdout.write(self.style.SUCCESS('Hospital configuration seeded successfully.'))

    def _seed_profile(self, force):
        if HospitalProfile.objects.exists() and not force:
            self.stdout.write('  HospitalProfile already exists — skipping (use --force to overwrite)')
            return
        HospitalProfile.objects.update_or_create(
            pk=1,
            defaults=dict(
                name='My Hospital',
                short_name='MH',
                tagline='Quality Healthcare for All',
                address='123 Hospital Street',
                city='Addis Ababa',
                country='Ethiopia',
                phone='+251 11 000 0000',
                email='info@myhospital.et',
                currency='ETB',
                currency_symbol='ETB',
                timezone='Africa/Addis_Ababa',
                language='en',
                report_footer='Thank you for choosing our hospital. For enquiries call our helpline.',
            ),
        )
        self.stdout.write('  ✓ HospitalProfile created')

    def _seed_modules(self, force):
        created = 0
        for name, label, category, is_core, order in MODULES:
            obj, was_created = SystemModule.objects.get_or_create(
                name=name,
                defaults=dict(label=label, category=category, is_core=is_core,
                               order=order, is_enabled=True),
            )
            if not was_created and force:
                obj.label = label
                obj.category = category
                obj.is_core = is_core
                obj.order = order
                obj.save(update_fields=['label', 'category', 'is_core', 'order'])
            if was_created:
                created += 1
        self.stdout.write(f'  ✓ SystemModules: {created} created, {len(MODULES) - created} already existed')

    def _seed_version(self, force):
        if SystemVersion.objects.exists() and not force:
            self.stdout.write('  SystemVersion already exists — skipping')
            return
        SystemVersion.objects.update_or_create(
            version='1.0.0',
            defaults=dict(
                build='20260721',
                release_date=datetime.date(2026, 7, 21),
                release_notes=(
                    'Initial production release.\n\n'
                    '• Full patient journey: registration → consultation → lab/imaging → pharmacy → billing → discharge\n'
                    '• Surgery / OR module with pre-deposit and settlement workflow\n'
                    '• Inpatient admission with bed management\n'
                    '• Medication inventory with batch tracking\n'
                    '• Comprehensive reporting suite\n'
                    '• Role-based access control with audit logging\n'
                    '• Hospital profile & module management\n'
                ),
                db_schema_ver='63',
                api_version='1.0',
                is_current=True,
            ),
        )
        self.stdout.write('  ✓ SystemVersion v1.0.0 created')
