"""
Management command: python manage.py setup_rbac

Creates / updates all HMS role Groups with correct permissions,
seeds default hospital departments, and provisions one realistic
sample user per role for testing.

    python manage.py migrate
    python manage.py setup_rbac
"""

from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand

from core.models import Department, UserProfile
from core.permissions import (
    DEPARTMENT_DEFAULTS, ROLE_DEPARTMENTS, ROLE_NAMES, ROLE_PERMISSIONS,
)


class Command(BaseCommand):
    help = 'Set up RBAC groups, permissions, departments, and sample users.'

    def _get_perm(self, app_codename: str) -> Permission | None:
        try:
            app_label, codename = app_codename.split('.', 1)
        except ValueError:
            self.stderr.write(f'  Bad perm format: {app_codename}')
            return None
        perm = Permission.objects.filter(
            content_type__app_label=app_label, codename=codename
        ).first()
        if perm is None:
            self.stderr.write(f'  WARNING: permission not found — {app_codename}')
        return perm

    def handle(self, *args, **options):
        self._seed_departments()
        self._setup_groups()
        self._create_sample_users()
        self.stdout.write(self.style.SUCCESS('\nRBAC setup complete.'))

    # ── Departments ───────────────────────────────────────────────────────────

    def _seed_departments(self):
        self.stdout.write('\n--- Seeding default departments ---')
        for name, dept_type in DEPARTMENT_DEFAULTS:
            dept, created = Department.objects.get_or_create(
                name=name,
                defaults={'dept_type': dept_type, 'is_active': True},
            )
            label = 'created' if created else 'exists '
            self.stdout.write(f'  {label}  {name}')

    # ── Groups ────────────────────────────────────────────────────────────────

    def _setup_groups(self):
        self.stdout.write('\n--- Creating / updating role groups ---')
        for role_name in ROLE_NAMES:
            group, created = Group.objects.get_or_create(name=role_name)
            label = 'created' if created else 'updated'
            perm_codenames = ROLE_PERMISSIONS.get(role_name, set())
            perms = [p for ap in perm_codenames if (p := self._get_perm(ap))]
            group.permissions.set(perms)
            self.stdout.write(f'  {label:7s}  {role_name:<30s}  ({len(perms)} permissions)')

    # ── Sample users ──────────────────────────────────────────────────────────

    def _create_sample_users(self):
        self.stdout.write('\n--- Creating sample users ---')

        # (username, password, first, last, email, role, job_title, phone, employee_id)
        sample_users = [
            # Administration
            ('sysadmin',            'Test@1234', 'System',    'Administrator', 'sysadmin@hospital.et',           'Administrator',          'System Administrator',   '+251900112233', 'EMP-ADM001'),
            ('miriam.getachew',     'Test@1234', 'Miriam',    'Getachew',      'miriam.getachew@hospital.et',     'Medical Director',       'Medical Director',       '+251911000001', 'EMP-MD001'),
            # Reception
            ('sara.tadesse',        'Test@1234', 'Sara',      'Tadesse',       'sara.tadesse@hospital.et',        'Receptionist',           'Front Desk Officer',     '+251933445566', 'EMP-R001'),
            # Medical
            ('bekele.haile',        'Test@1234', 'Bekele',    'Haile',         'bekele.haile@hospital.et',        'Doctor',                 'General Practitioner',   '+251922334455', 'EMP-D001'),
            ('abiy.mekonen',        'Test@1234', 'Abiy',      'Mekonen',       'abiy.mekonen@hospital.et',        'Ward Doctor',            'Ward Physician',         '+251911000002', 'EMP-D002'),
            ('tesfaye.girma',       'Test@1234', 'Tesfaye',   'Girma',         'tesfaye.girma@hospital.et',       'Emergency Doctor',       'Emergency Physician',    '+251911000003', 'EMP-D003'),
            ('dawit.woldemariam',   'Test@1234', 'Dawit',     'Woldemariam',   'dawit.wold@hospital.et',          'Surgeon',                'General Surgeon',        '+251911000004', 'EMP-D004'),
            # Nursing
            ('tigist.alemu',        'Test@1234', 'Tigist',    'Alemu',         'tigist.alemu@hospital.et',        'Nurse',                  'Registered Nurse',       '+251911223344', 'EMP-N001'),
            ('selamawit.tesfaye',   'Test@1234', 'Selamawit', 'Tesfaye',       'selamawit.t@hospital.et',         'Ward Nurse',             'Ward Nurse',             '+251911000005', 'EMP-N002'),
            ('ayantu.bekele',       'Test@1234', 'Ayantu',    'Bekele',        'ayantu.b@hospital.et',            'OR Nurse',               'OR Scrub Nurse',         '+251911000006', 'EMP-N003'),
            ('chaltu.negash',       'Test@1234', 'Chaltu',    'Negash',        'chaltu.n@hospital.et',            'Emergency Nurse',        'Emergency Nurse',        '+251911000007', 'EMP-N004'),
            ('yeshi.tesfaw',        'Test@1234', 'Yeshi',     'Tesfaw',        'yeshi.tesfaw@hospital.et',        'Triage Staff',           'Triage Nurse',           '+251955667789', 'EMP-TR001'),
            # Laboratory
            ('meseret.kebede',      'Test@1234', 'Meseret',   'Kebede',        'meseret.kebede@hospital.et',      'Laboratory Staff',       'Lab Technician',         '+251944556677', 'EMP-L001'),
            ('teklu.assefa',        'Test@1234', 'Teklu',     'Assefa',        'teklu.a@hospital.et',             'Lab Supervisor',         'Lab Supervisor',         '+251911000008', 'EMP-L002'),
            # Radiology
            ('yonas.girma',         'Test@1234', 'Yonas',     'Girma',         'yonas.girma@hospital.et',         'Radiologist',            'Radiologist',            '+251966778899', 'EMP-RA001'),
            ('frehiwot.lemma',      'Test@1234', 'Frehiwot',  'Lemma',         'frehiwot.l@hospital.et',          'Radiology Technician',   'Radiology Technician',   '+251911000009', 'EMP-RA002'),
            # Anesthesia
            ('dawit.abebe',         'Test@1234', 'Dawit',     'Abebe',         'dawit.abebe@hospital.et',         'Anesthesia Team',        'Anesthesiologist',       '+251955667788', 'EMP-A001'),
            # Pharmacy
            ('abebe.mulatu',        'Test@1234', 'Abebe',     'Mulatu',        'abebe.mulatu@hospital.et',        'Pharmacy Admin',         'Pharmacy Manager',       '+251911223345', 'EMP-PA001'),
            ('liya.habtamu',        'Test@1234', 'Liya',      'Habtamu',       'liya.h@hospital.et',              'Pharmacist',             'Pharmacist',             '+251911000010', 'EMP-PH001'),
            ('selam.tadesse',       'Test@1234', 'Selam',     'Tadesse',       'selam.tadesse@hospital.et',       'Pharmacy Sales',         'Pharmacy Dispenser',     '+251922334456', 'EMP-PS001'),
            # Store
            ('kedir.seid',          'Test@1234', 'Kedir',     'Seid',          'kedir.seid@hospital.et',          'Store Manager',          'Store Manager',          '+251933445567', 'EMP-SM001'),
            ('biruk.tilahun',       'Test@1234', 'Biruk',     'Tilahun',       'biruk.t@hospital.et',             'Store Staff',            'Storekeeper',            '+251911000011', 'EMP-ST001'),
            ('hailemariam.desta',   'Test@1234', 'Hailemariam','Desta',         'haile.d@hospital.et',             'Store Officer',          'Store Officer',          '+251911000012', 'EMP-SO001'),
            # Finance
            ('fikadu.wolde',        'Test@1234', 'Fikadu',    'Wolde',         'fikadu.w@hospital.et',            'Finance Manager',        'Finance Manager',        '+251911000013', 'EMP-FM001'),
            ('hana.tesfaye',        'Test@1234', 'Hana',      'Tesfaye',       'hana.tesfaye@hospital.et',        'Finance Staff',          'Accountant',             '+251977889900', 'EMP-F001'),
            ('meron.bekele',        'Test@1234', 'Meron',     'Bekele',        'meron.bekele@hospital.et',        'Cashier',                'Cashier',                '+251988990011', 'EMP-C001'),
            # HR
            ('tewodros.haile',      'Test@1234', 'Tewodros',  'Haile',         'tewodros.h@hospital.et',          'HR Manager',             'HR Manager',             '+251911000014', 'EMP-HM001'),
            ('lidiya.worku',        'Test@1234', 'Lidiya',    'Worku',         'lidiya.worku@hospital.et',        'HR Staff',               'HR Officer',             '+251999001122', 'EMP-HR001'),
            ('alemitu.sisay',       'Test@1234', 'Alemitu',   'Sisay',         'alemitu.s@hospital.et',           'HR Officer',             'HR Data Entry',          '+251911000015', 'EMP-HO001'),
            # Dept stores
            ('yewbdar.girma',       'Test@1234', 'Yewbdar',   'Girma',         'yewbdar.g@hospital.et',           'Dept Medication Manager','Dept Medication Manager','+251911000016', 'EMP-DM001'),
            ('mekdes.alemu',        'Test@1234', 'Mekdes',    'Alemu',         'mekdes.a@hospital.et',            'Ward Store User',        'Ward Store Attendant',   '+251911000017', 'EMP-WS001'),
            ('robel.tesfaye',       'Test@1234', 'Robel',     'Tesfaye',       'robel.t@hospital.et',             'Emergency Store Staff',  'Emergency Store Staff',  '+251911000018', 'EMP-ES001'),
        ]

        for (username, password, first, last, email, role_name, job_title, phone, emp_id) in sample_users:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': first,
                    'last_name':  last,
                    'email':      email,
                    'is_active':  True,
                },
            )
            if created:
                user.set_password(password)
                user.save()

            try:
                group = Group.objects.get(name=role_name)
                user.groups.set([group])
            except Group.DoesNotExist:
                self.stderr.write(f'  Group not found: {role_name}')

            # Resolve default department for this role
            dept_name = ROLE_DEPARTMENTS.get(role_name, '')
            dept = Department.objects.filter(name=dept_name).first() if dept_name else None

            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'department':   dept,
                    'phone':        phone,
                    'employee_id':  emp_id,
                    'job_title':    job_title,
                    'access_level': UserProfile.AccessLevel.STANDARD,
                },
            )
            action = 'created' if created else 'exists '
            self.stdout.write(f'  {action}  {username:<25s}  [{role_name}]')

        # Attach Administrator group to every superuser
        self.stdout.write('\n--- Attaching Administrator group to superusers ---')
        admin_group, _ = Group.objects.get_or_create(name='Administrator')
        for su in User.objects.filter(is_superuser=True):
            su.groups.add(admin_group)
            UserProfile.objects.get_or_create(
                user=su,
                defaults={
                    'employee_id':  'EMP-SU001',
                    'job_title':    'System Administrator',
                    'access_level': UserProfile.AccessLevel.FULL,
                },
            )
            self.stdout.write(f'  {su.username} -> Administrator group')
