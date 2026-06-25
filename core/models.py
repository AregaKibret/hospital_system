from django.conf import settings
from django.db import models


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Department'
        verbose_name_plural = 'Departments'

    def __str__(self):
        return self.name


class Doctor(models.Model):
    employee_id = models.CharField(
        max_length=20,
        unique=True,
        # null=True is intentional here: unique + optional requires NULL
        # (blank strings would violate the unique constraint for multiple rows)
        null=True,
        blank=True,
    )
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name='doctors',
    )
    mobile = models.CharField(max_length=20, blank=True, default='')
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['last_name', 'first_name']
        verbose_name = 'Doctor'
        verbose_name_plural = 'Doctors'

    def __str__(self):
        return f"Dr. {self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Patient(models.Model):
    class Sex(models.TextChoices):
        MALE = 'Male', 'Male'
        FEMALE = 'Female', 'Female'
        OTHER = 'Other', 'Other'

    card_number = models.CharField(max_length=20, unique=True, blank=True)
    first_name = models.CharField(max_length=50)
    middle_name = models.CharField(max_length=50, blank=True)
    last_name = models.CharField(max_length=50)
    sex = models.CharField(max_length=10, choices=Sex.choices)
    date_of_birth = models.DateField(null=True, blank=True)
    mobile = models.CharField(max_length=20)
    emergency_contact = models.CharField(max_length=20, blank=True)
    nationality = models.CharField(max_length=50, blank=True)
    region = models.CharField(max_length=50, blank=True)
    city = models.CharField(max_length=50, blank=True)
    subcity = models.CharField(max_length=50, blank=True)
    wereda = models.CharField(max_length=50, blank=True)
    house_no = models.CharField(max_length=20, blank=True)
    occupation = models.CharField(max_length=100, blank=True)
    education = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Patient'
        verbose_name_plural = 'Patients'
        indexes = [
            models.Index(fields=['last_name', 'first_name']),
            models.Index(fields=['card_number']),
        ]

    def __str__(self):
        return f"{self.card_number} — {self.first_name} {self.last_name}"

    def save(self, *args, **kwargs):
        if not self.card_number:
            # NOTE: race condition possible under concurrent inserts;
            # acceptable for single-instance deployments. Use a DB sequence
            # (or SELECT ... FOR UPDATE) if concurrent registration is expected.
            last = Patient.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.card_number = f"PAT-{next_id:06d}"
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(p for p in parts if p)


class Visit(models.Model):
    class VisitType(models.TextChoices):
        OPD = 'OPD', 'OPD'
        EMERGENCY = 'Emergency', 'Emergency'
        INPATIENT = 'Inpatient', 'Inpatient'

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name='visits',
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name='visits',
    )
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.PROTECT,
        related_name='visits',
    )
    visit_type = models.CharField(
        max_length=20,
        choices=VisitType.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Visit'
        verbose_name_plural = 'Visits'
        indexes = [
            models.Index(fields=['patient', '-created_at']),
        ]

    def __str__(self):
        return f"Visit #{self.pk} — {self.patient} ({self.visit_type})"


class Queue(models.Model):
    visit = models.OneToOneField(
        Visit,
        on_delete=models.CASCADE,
        related_name='queue',
    )
    queue_number = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['queue_number']
        verbose_name = 'Queue Entry'
        verbose_name_plural = 'Queue Entries'

    def __str__(self):
        return f"Queue #{self.queue_number}"


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='staff',
    )
    phone = models.CharField(max_length=20, blank=True)
    employee_id = models.CharField(max_length=20, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} — {self.role_name}"

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.username

    @property
    def role_name(self):
        group = self.user.groups.first()
        return group.name if group else 'No Role'

    @property
    def role_group(self):
        return self.user.groups.first()


class HMSPermissions(models.Model):
    """Unmanaged model whose sole purpose is to register HMS custom permissions."""

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = 'HMS Permission'
        verbose_name_plural = 'HMS Permissions'
        permissions = [
            # Queue / Triage
            ('manage_queue',              'Can manage patient queue'),
            ('perform_triage',            'Can perform triage assessment'),

            # Clinical notes
            ('write_clinical_note',       'Can write clinical notes'),
            ('read_clinical_note',        'Can read clinical notes'),
            ('write_nursing_note',        'Can write nursing notes'),
            ('read_nursing_note',         'Can read nursing notes'),

            # Vitals & care plans
            ('record_vital_signs',        'Can record vital signs'),
            ('read_vital_signs',          'Can read vital signs'),
            ('write_care_plan',           'Can write care plans'),
            ('read_care_plan',            'Can read care plans'),

            # Diagnosis & prescriptions
            ('write_diagnosis',           'Can write diagnosis'),
            ('read_diagnosis',            'Can read diagnosis records'),
            ('write_prescription',        'Can write prescriptions'),
            ('read_prescription',         'Can read prescriptions'),

            # Laboratory
            ('request_lab_test',          'Can request laboratory tests'),
            ('read_lab_request',          'Can read lab requests'),
            ('process_lab_test',          'Can process lab tests / enter results'),
            ('write_lab_result',          'Can write lab results'),
            ('read_lab_result',           'Can read lab results'),

            # Imaging / Radiology
            ('request_imaging',           'Can request imaging studies'),
            ('read_imaging_request',      'Can read imaging requests'),
            ('process_imaging',           'Can process imaging studies'),
            ('write_imaging_report',      'Can write imaging reports'),
            ('read_imaging_report',       'Can read imaging reports'),

            # Anesthesia
            ('write_anesthesia_record',   'Can write anesthesia records'),
            ('read_anesthesia_record',    'Can read anesthesia records'),

            # Billing & Finance
            ('read_billing',              'Can read billing records'),
            ('create_invoice',            'Can create invoices'),
            ('manage_billing',            'Can manage all billing records'),
            ('process_payment',           'Can process payments'),
            ('read_financial_report',     'Can read financial reports'),

            # Pharmacy
            ('read_medication_inventory',        'Can read medication inventory'),
            ('manage_medication',                'Can manage medication inventory'),
            ('dispense_medication',              'Can dispense medications'),
            ('read_prescription_for_dispensing', 'Can read prescriptions for dispensing'),
            ('process_pharmacy_sale',            'Can process pharmacy sales'),

            # Store / Inventory
            ('read_inventory',            'Can read store inventory'),
            ('manage_inventory',          'Can manage store inventory'),
            ('create_purchase_order',     'Can create purchase orders'),
            ('approve_purchase_order',    'Can approve purchase orders'),

            # HR
            ('read_employee',             'Can read employee records'),
            ('manage_employees',          'Can manage employee records'),
            ('manage_attendance',         'Can manage attendance'),
            ('manage_payroll',            'Can manage payroll'),

            # Appointments
            ('read_appointment',          'Can read appointments'),
            ('manage_appointments',       'Can manage appointments'),

            # Reports
            ('read_clinical_reports',     'Can read clinical reports'),
            ('read_department_reports',   'Can read department reports'),

            # System administration
            ('manage_users',              'Can manage system users'),
            ('manage_roles',              'Can manage roles and permissions'),
            ('system_configuration',      'Can access system configuration'),
            ('read_audit_log',            'Can read audit logs'),
            ('manage_departments',        'Can manage departments'),
        ]
