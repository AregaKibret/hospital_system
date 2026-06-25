"""
Management command: python manage.py setup_rbac

Creates all HMS role Groups with correct permissions, then provisions
one realistic sample user per role for testing.

Run after migrations:
    python manage.py migrate
    python manage.py setup_rbac
"""

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from core.models import Department, UserProfile
from core.permissions import ROLE_NAMES, ROLE_PERMISSIONS


class Command(BaseCommand):
    help = 'Set up RBAC groups, permissions, and sample users.'

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _get_perm(self, app_codename: str) -> Permission | None:
        """
        Resolve 'app_label.codename' to a Permission object.
        Returns None (with a warning) if not found.
        """
        try:
            app_label, codename = app_codename.split('.', 1)
        except ValueError:
            self.stderr.write(f'  Bad perm format: {app_codename}')
            return None

        qs = Permission.objects.filter(
            content_type__app_label=app_label,
            codename=codename,
        )
        perm = qs.first()
        if perm is None:
            self.stderr.write(f'  WARNING: permission not found — {app_codename}')
        return perm

    # ── Main ─────────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        self._setup_groups()
        self._create_sample_users()
        self.stdout.write(self.style.SUCCESS('\nRBAC setup complete.'))

    # ── Groups ───────────────────────────────────────────────────────────────

    def _setup_groups(self):
        self.stdout.write('\n--- Creating / updating role groups ---')
        for role_name in ROLE_NAMES:
            group, created = Group.objects.get_or_create(name=role_name)
            label = 'created' if created else 'updated'

            perm_codenames = ROLE_PERMISSIONS.get(role_name, set())
            perms = []
            for ap in perm_codenames:
                p = self._get_perm(ap)
                if p:
                    perms.append(p)

            group.permissions.set(perms)
            self.stdout.write(f'  {label:7s}  {role_name}  ({len(perms)} permissions)')

    # ── Sample users ─────────────────────────────────────────────────────────

    def _create_sample_users(self):
        self.stdout.write('\n--- Creating sample users ---')

        # Ensure a few base departments exist
        dept_map = {}
        for dept_name in [
            'General Medicine', 'Cardiology', 'Neurology',
            'Pediatrics', 'Orthopedics', 'Radiology',
            'Laboratory', 'Pharmacy', 'Finance', 'Administration',
        ]:
            dept, _ = Department.objects.get_or_create(name=dept_name)
            dept_map[dept_name] = dept

        sample_users = [
            # (username, password, first, last, email, role, department, phone, employee_id)
            (
                'tigist.alemu', 'Test@1234',
                'Tigist', 'Alemu', 'tigist.alemu@hospital.et',
                'Nurse', 'General Medicine', '+251911223344', 'EMP-N001',
            ),
            (
                'bekele.haile', 'Test@1234',
                'Bekele', 'Haile', 'bekele.haile@hospital.et',
                'Doctor', 'Cardiology', '+251922334455', 'EMP-D001',
            ),
            (
                'sara.tadesse', 'Test@1234',
                'Sara', 'Tadesse', 'sara.tadesse@hospital.et',
                'Receptionist', 'Administration', '+251933445566', 'EMP-R001',
            ),
            (
                'meseret.kebede', 'Test@1234',
                'Meseret', 'Kebede', 'meseret.kebede@hospital.et',
                'Laboratory Staff', 'Laboratory', '+251944556677', 'EMP-L001',
            ),
            (
                'dawit.abebe', 'Test@1234',
                'Dawit', 'Abebe', 'dawit.abebe@hospital.et',
                'Anesthesia Team', 'General Medicine', '+251955667788', 'EMP-A001',
            ),
            (
                'yonas.girma', 'Test@1234',
                'Yonas', 'Girma', 'yonas.girma@hospital.et',
                'Radiologist', 'Radiology', '+251966778899', 'EMP-RA001',
            ),
            (
                'hana.tesfaye', 'Test@1234',
                'Hana', 'Tesfaye', 'hana.tesfaye@hospital.et',
                'Finance Staff', 'Finance', '+251977889900', 'EMP-F001',
            ),
            (
                'meron.bekele', 'Test@1234',
                'Meron', 'Bekele', 'meron.bekele@hospital.et',
                'Cashier', 'Finance', '+251988990011', 'EMP-C001',
            ),
            (
                'lidiya.worku', 'Test@1234',
                'Lidiya', 'Worku', 'lidiya.worku@hospital.et',
                'HR Staff', 'Administration', '+251999001122', 'EMP-H001',
            ),
            (
                'sysadmin', 'Test@1234',
                'System', 'Administrator', 'sysadmin@hospital.et',
                'Administrator', 'Administration', '+251900112233', 'EMP-ADM001',
            ),
            (
                'abebe.mulatu', 'Test@1234',
                'Abebe', 'Mulatu', 'abebe.mulatu@hospital.et',
                'Pharmacy Admin', 'Pharmacy', '+251911223345', 'EMP-PA001',
            ),
            (
                'selam.tadesse', 'Test@1234',
                'Selam', 'Tadesse', 'selam.tadesse@hospital.et',
                'Pharmacy Sales', 'Pharmacy', '+251922334456', 'EMP-PS001',
            ),
            (
                'kedir.seid', 'Test@1234',
                'Kedir', 'Seid', 'kedir.seid@hospital.et',
                'Store Staff', 'Administration', '+251933445567', 'EMP-ST001',
            ),
            (
                'miriam.getachew', 'Test@1234',
                'Miriam', 'Getachew', 'miriam.getachew@hospital.et',
                'Medical Director', 'General Medicine', '+251944556678', 'EMP-MD001',
            ),
            (
                'yeshi.tesfaw', 'Test@1234',
                'Yeshi', 'Tesfaw', 'yeshi.tesfaw@hospital.et',
                'Triage Staff', 'General Medicine', '+251955667789', 'EMP-TR001',
            ),
        ]

        for (
            username, password, first, last, email,
            role_name, dept_name, phone, emp_id,
        ) in sample_users:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': first,
                    'last_name': last,
                    'email': email,
                    'is_active': True,
                },
            )
            if created:
                user.set_password(password)
                user.save()

            # Assign role group (replace existing groups)
            try:
                group = Group.objects.get(name=role_name)
                user.groups.set([group])
            except Group.DoesNotExist:
                self.stderr.write(f'  Group not found: {role_name}')

            # Create / update profile
            dept = dept_map.get(dept_name)
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'department': dept,
                    'phone': phone,
                    'employee_id': emp_id,
                },
            )

            action = 'created' if created else 'exists '
            self.stdout.write(f'  {action}  {username:<20s}  [{role_name}]')

        # Ensure the existing "admin" superuser has Administrator group
        self.stdout.write('\n--- Attaching Administrator group to superusers ---')
        admin_group, _ = Group.objects.get_or_create(name='Administrator')
        for su in User.objects.filter(is_superuser=True):
            su.groups.add(admin_group)
            UserProfile.objects.get_or_create(
                user=su,
                defaults={
                    'employee_id': 'EMP-SU001',
                    'phone': '',
                },
            )
            self.stdout.write(f'  {su.username} -> Administrator group assigned')
