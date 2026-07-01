from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Department(models.Model):
    class DeptType(models.TextChoices):
        ADMINISTRATION = 'administration', 'Administration'
        CLINICAL       = 'clinical',       'Medical / Clinical'
        NURSING        = 'nursing',        'Nursing'
        EMERGENCY      = 'emergency',      'Emergency'
        LABORATORY     = 'laboratory',     'Laboratory'
        RADIOLOGY      = 'radiology',      'Radiology'
        PHARMACY       = 'pharmacy',       'Pharmacy'
        OPERATING_ROOM = 'or',             'Operating Room'
        WARD           = 'ward',           'Ward / Inpatient'
        ICU            = 'icu',            'Intensive Care Unit'
        OPD            = 'opd',            'Outpatient'
        FINANCE        = 'finance',        'Finance'
        HR             = 'hr',             'Human Resources'
        STORE          = 'store',          'Store / Inventory'
        MATERNITY      = 'maternity',      'Maternity / Labour'
        PEDIATRICS     = 'pediatrics',     'Pediatrics'
        OTHER          = 'other',          'Other'

    name        = models.CharField(max_length=100, unique=True)
    dept_type   = models.CharField(max_length=20, choices=DeptType.choices, default=DeptType.OTHER)
    description = models.TextField(blank=True)
    head        = models.CharField(max_length=100, blank=True)
    phone       = models.CharField(max_length=30, blank=True)
    location    = models.CharField(max_length=200, blank=True)
    is_active   = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Department'
        verbose_name_plural = 'Departments'

    def __str__(self):
        return self.name


class Doctor(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='doctor_profile',
    )
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
    blood_group = models.CharField(
        max_length=10,
        choices=[
            ('A+', 'A+'), ('A-', 'A-'), ('B+', 'B+'), ('B-', 'B-'),
            ('AB+', 'AB+'), ('AB-', 'AB-'), ('O+', 'O+'), ('O-', 'O-'),
            ('Unknown', 'Unknown'),
        ],
        blank=True, default='',
    )
    is_active = models.BooleanField(default=True)
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

    @property
    def age_display(self):
        """Human-readable age derived from date_of_birth.

        Examples: '41 yrs', '1 yr 3 mo', '8 mo', '15 d', None if DOB missing.
        """
        from datetime import date as _date
        if not self.date_of_birth:
            return None
        today = _date.today()
        dob = self.date_of_birth
        if dob > today:
            return None

        years = (today.year - dob.year
                 - ((today.month, today.day) < (dob.month, dob.day)))

        if years >= 2:
            return f"{years} yrs"

        months = (today.year - dob.year) * 12 + (today.month - dob.month)
        if today.day < dob.day:
            months -= 1
        months = max(months, 0)

        if months >= 12:
            extra = months - 12
            return f"1 yr {extra} mo" if extra else "1 yr"
        if months >= 1:
            return f"{months} mo"
        return f"{(today - dob).days} d"


class Visit(models.Model):
    class VisitType(models.TextChoices):
        NEW_VISIT  = 'New Visit',  'New Visit'
        REVISIT    = 'Revisit',    'Revisit'
        REPAYMENT  = 'Repayment',  'Repayment'

    class Status(models.TextChoices):
        REGISTERED            = 'registered',            'Registered'
        VISIT_CREATED         = 'visit_created',         'Visit Created'
        WAITING_PAYMENT       = 'waiting_payment',       'Waiting for Payment'
        PAYMENT_COMPLETED     = 'payment_completed',     'Payment Completed'
        WAITING_DOCTOR        = 'waiting_doctor',        'Waiting for Doctor'
        CONSULTATION_STARTED  = 'consultation_started',  'Consultation Started'
        INVESTIGATION_ORDERED = 'investigation_ordered', 'Investigation Ordered'
        INVESTIGATION_COMPLETED = 'investigation_completed', 'Investigation Completed'
        TREATMENT_STARTED     = 'treatment_started',     'Treatment Started'
        PROCEDURE_SCHEDULED   = 'procedure_scheduled',   'Procedure / Surgery Scheduled'
        COMPLETED             = 'completed',             'Consultation Completed'
        DISCHARGED            = 'discharged',            'Discharged'
        FOLLOW_UP_REQUIRED    = 'follow_up_required',    'Follow-up Required'

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
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.VISIT_CREATED,
        db_index=True,
    )
    chief_complaint = models.TextField(blank=True)
    # For Revisit: link to the prior consultation this visit follows up on
    previous_visit = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='follow_ups',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Visit'
        verbose_name_plural = 'Visits'
        indexes = [
            models.Index(fields=['patient', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return f"Visit #{self.pk} — {self.patient} ({self.visit_type})"


class Queue(models.Model):
    class Status(models.TextChoices):
        WAITING = 'Waiting', 'Waiting'
        CALLED = 'Called', 'Called'
        IN_PROGRESS = 'In Progress', 'In Progress'
        COMPLETED = 'Completed', 'Completed'
        NO_SHOW = 'No Show', 'No Show'

    visit = models.OneToOneField(
        Visit,
        on_delete=models.CASCADE,
        related_name='queue',
    )
    queue_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.WAITING)
    called_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['queue_number']
        verbose_name = 'Queue Entry'
        verbose_name_plural = 'Queue Entries'

    def __str__(self):
        return f"Queue #{self.queue_number} ({self.status})"


class DoctorSchedule(models.Model):
    DAY_CHOICES = [
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'),
        (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    ]

    doctor = models.ForeignKey(
        Doctor, on_delete=models.CASCADE, related_name='schedules',
    )
    day_of_week = models.SmallIntegerField(choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_start = models.TimeField(null=True, blank=True)
    break_end   = models.TimeField(null=True, blank=True)
    slot_duration_minutes = models.PositiveIntegerField(default=20)
    max_appointments = models.PositiveIntegerField(default=20)
    specialty = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['day_of_week', 'start_time']
        unique_together = [['doctor', 'day_of_week']]
        verbose_name = 'Doctor Schedule'
        verbose_name_plural = 'Doctor Schedules'

    def __str__(self):
        return f"Dr. {self.doctor.full_name} — {self.get_day_of_week_display()} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    @property
    def day_name(self):
        return dict(self.DAY_CHOICES).get(self.day_of_week, '')


class Appointment(models.Model):
    class AppointmentType(models.TextChoices):
        NEW = 'New Consultation', 'New Consultation'
        FOLLOWUP = 'Follow-up', 'Follow-up'
        EMERGENCY = 'Emergency', 'Emergency'
        PROCEDURE = 'Procedure', 'Procedure'
        REVIEW = 'Review', 'Review'

    class VisitType(models.TextChoices):
        NEW_VISIT  = 'New Visit',  'New Visit'
        REVISIT    = 'Revisit',    'Revisit'
        FOLLOW_UP  = 'Follow-up',  'Follow-up'

    class Priority(models.TextChoices):
        NORMAL    = 'Normal',    'Normal'
        URGENT    = 'Urgent',    'Urgent'
        EMERGENCY = 'Emergency', 'Emergency'

    class ReferralSource(models.TextChoices):
        RECEPTION   = 'Reception',   'Reception / Walk-in'
        CALL_CENTER = 'Call Center', 'Call Center'
        REFERRAL    = 'Referral',    'Doctor Referral'
        ONLINE      = 'Online',      'Online / Portal'
        OTHER       = 'Other',       'Other'

    class Status(models.TextChoices):
        SCHEDULED       = 'Scheduled',       'Scheduled'
        CONFIRMED       = 'Confirmed',       'Confirmed'
        CHECKED_IN      = 'Checked In',      'Checked In'
        WAITING         = 'Waiting',         'Waiting'
        IN_CONSULTATION = 'In Consultation', 'In Consultation'
        IN_PROGRESS     = 'In Progress',     'In Progress'
        COMPLETED       = 'Completed',       'Completed'
        NO_SHOW         = 'No Show',         'No Show'
        CANCELLED       = 'Cancelled',       'Cancelled'
        RESCHEDULED     = 'Rescheduled',     'Rescheduled'

    appointment_number = models.CharField(max_length=20, unique=True, blank=True)
    patient = models.ForeignKey(
        Patient, on_delete=models.PROTECT, related_name='appointments',
    )
    doctor = models.ForeignKey(
        Doctor, on_delete=models.PROTECT, related_name='appointments',
    )
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name='appointments',
    )
    appointment_date = models.DateField()
    appointment_time = models.TimeField()
    appointment_type = models.CharField(
        max_length=20, choices=AppointmentType.choices, default=AppointmentType.NEW,
    )
    visit_type = models.CharField(
        max_length=20, choices=VisitType.choices, default=VisitType.NEW_VISIT,
    )
    priority = models.CharField(
        max_length=15, choices=Priority.choices, default=Priority.NORMAL,
    )
    referral_source = models.CharField(
        max_length=20, choices=ReferralSource.choices, default=ReferralSource.RECEPTION,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SCHEDULED,
    )
    chief_complaint = models.TextField(blank=True)
    reason_for_visit = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    visit = models.ForeignKey(
        Visit, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='appointment',
    )
    # Timing
    checked_in_at           = models.DateTimeField(null=True, blank=True)
    consultation_started_at = models.DateTimeField(null=True, blank=True)
    consultation_ended_at   = models.DateTimeField(null=True, blank=True)
    # Cancellation
    cancelled_by        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='appointments_cancelled',
    )
    cancelled_at        = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    # Rescheduling
    rescheduled_from = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rescheduled_to',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='appointments_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['appointment_date', 'appointment_time']
        verbose_name = 'Appointment'
        verbose_name_plural = 'Appointments'
        indexes = [
            models.Index(fields=['appointment_date', 'doctor']),
            models.Index(fields=['patient', 'appointment_date']),
            models.Index(fields=['status', 'appointment_date']),
        ]

    def __str__(self):
        return f"{self.appointment_number} — {self.patient} with Dr. {self.doctor.full_name} on {self.appointment_date}"

    def save(self, *args, **kwargs):
        if not self.appointment_number:
            from django.utils import timezone
            today = timezone.localdate()
            last = Appointment.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.appointment_number = f"APT-{today:%Y%m}-{next_id:04d}"
        super().save(*args, **kwargs)

    @property
    def status_color(self):
        return {
            'Scheduled':       'blue',
            'Confirmed':       'indigo',
            'Checked In':      'cyan',
            'Waiting':         'amber',
            'In Consultation': 'purple',
            'In Progress':     'purple',
            'Completed':       'green',
            'No Show':         'slate',
            'Cancelled':       'red',
            'Rescheduled':     'orange',
        }.get(self.status, 'slate')

    @property
    def priority_color(self):
        return {'Normal': 'green', 'Urgent': 'amber', 'Emergency': 'red'}.get(self.priority, 'slate')


class UserProfile(models.Model):
    class AccessLevel(models.TextChoices):
        READ_ONLY = 'read_only', 'Read Only'
        STANDARD  = 'standard',  'Standard'
        ELEVATED  = 'elevated',  'Elevated'
        FULL      = 'full',      'Full Access'

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
    phone          = models.CharField(max_length=20, blank=True)
    employee_id    = models.CharField(max_length=20, blank=True)
    job_title      = models.CharField(max_length=100, blank=True)
    access_level   = models.CharField(max_length=20, choices=AccessLevel.choices, default=AccessLevel.STANDARD)
    notes          = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True, null=True)
    updated_at     = models.DateTimeField(auto_now=True)

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

    @property
    def access_level_color(self):
        return {
            'read_only': 'slate',
            'standard':  'blue',
            'elevated':  'amber',
            'full':      'red',
        }.get(self.access_level, 'slate')


class ClinicalNote(models.Model):
    class NoteType(models.TextChoices):
        HP = 'H&P', 'History & Physical'
        PROGRESS = 'Progress', 'Progress Note'
        CONSULTATION = 'Consultation', 'Consultation Note'
        PROCEDURE = 'Procedure', 'Procedure Note'
        DISCHARGE = 'Discharge', 'Discharge Summary'
        REFERRAL = 'Referral', 'Referral Form'
        NURSING = 'Nursing', 'Nursing Assessment'
        OTHER = 'Other', 'Other'

    visit = models.ForeignKey(
        Visit, on_delete=models.PROTECT, related_name='clinical_notes',
    )
    authored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='clinical_notes',
    )
    note_type = models.CharField(max_length=20, choices=NoteType.choices, default=NoteType.HP)
    chief_complaint = models.TextField(blank=True)
    content = models.TextField()
    assessment = models.TextField(blank=True)
    plan = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Clinical Note'
        verbose_name_plural = 'Clinical Notes'

    def __str__(self):
        return f"{self.get_note_type_display()} — {self.visit} ({self.created_at:%Y-%m-%d})"


class Diagnosis(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'Active', 'Active'
        RESOLVED = 'Resolved', 'Resolved'
        CHRONIC = 'Chronic', 'Chronic'

    visit = models.ForeignKey(
        Visit, on_delete=models.PROTECT, related_name='diagnoses',
    )
    authored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='diagnoses',
    )
    icd_code = models.CharField(max_length=20, blank=True)
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Diagnosis'
        verbose_name_plural = 'Diagnoses'

    def __str__(self):
        code = f" [{self.icd_code}]" if self.icd_code else ''
        return f"{self.description}{code} ({self.status})"


# ── Laboratory Service Master ──────────────────────────────────────────────────

class LabService(models.Model):
    """Catalogue of laboratory tests with configurable pricing."""

    class SampleType(models.TextChoices):
        BLOOD   = 'Blood',  'Blood'
        URINE   = 'Urine',  'Urine'
        STOOL   = 'Stool',  'Stool'
        SWAB    = 'Swab',   'Swab'
        TISSUE  = 'Tissue', 'Tissue'
        CSF     = 'CSF',    'Cerebrospinal Fluid'
        SPUTUM  = 'Sputum', 'Sputum'
        OTHER   = 'Other',  'Other'

    class Section(models.TextChoices):
        HEMATOLOGY    = 'Hematology',    'Hematology'
        CHEMISTRY     = 'Chemistry',     'Chemistry'
        MICROBIOLOGY  = 'Microbiology',  'Microbiology'
        IMMUNOLOGY    = 'Immunology',    'Immunology'
        PATHOLOGY     = 'Pathology',     'Pathology'
        PARASITOLOGY  = 'Parasitology',  'Parasitology'
        SEROLOGY      = 'Serology',      'Serology'
        GENETICS      = 'Genetics',      'Genetics'
        OTHER         = 'Other',         'Other'

    name                    = models.CharField(max_length=200)
    code                    = models.CharField(max_length=30, unique=True)
    category                = models.CharField(max_length=100, blank=True)
    section                 = models.CharField(max_length=20, choices=Section.choices, default=Section.OTHER)
    sample_type             = models.CharField(max_length=20, choices=SampleType.choices, default=SampleType.BLOOD)
    sample_requirements     = models.TextField(blank=True)
    preparation_instructions = models.TextField(blank=True)
    turnaround_hours        = models.PositiveIntegerField(default=24)

    # Pricing
    standard_price   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    insurance_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    corporate_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    emergency_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tax_percent      = models.DecimalField(max_digits=5,  decimal_places=2, default=0)

    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['section', 'name']
        verbose_name = 'Lab Service'
        verbose_name_plural = 'Lab Services'

    def __str__(self):
        return f"[{self.code}] {self.name}"

    def get_price(self, price_category='standard'):
        if price_category == 'insurance' and self.insurance_price is not None:
            return self.insurance_price
        if price_category == 'corporate' and self.corporate_price is not None:
            return self.corporate_price
        if price_category == 'emergency' and self.emergency_price is not None:
            return self.emergency_price
        return self.standard_price


class LabOrder(models.Model):
    class Priority(models.TextChoices):
        ROUTINE = 'Routine', 'Routine'
        URGENT  = 'Urgent',  'Urgent'
        STAT    = 'STAT',    'STAT'

    class Status(models.TextChoices):
        # New workflow statuses
        WAITING_PAYMENT  = 'Waiting Payment',  'Waiting Payment'
        SAMPLE_PENDING   = 'Sample Pending',   'Sample Pending'
        SAMPLE_COLLECTED = 'Sample Collected', 'Sample Collected'
        PROCESSING       = 'Processing',       'Processing'
        RESULT_READY     = 'Result Ready',     'Result Ready'
        RELEASED         = 'Released',         'Released'
        CANCELLED        = 'Cancelled',        'Cancelled'
        # Legacy statuses (existing data)
        PENDING          = 'Pending',          'Pending'
        IN_PROGRESS      = 'In Progress',      'In Progress'
        COMPLETED        = 'Completed',        'Completed'

    class PaymentStatus(models.TextChoices):
        PENDING_PAYMENT = 'Pending Payment', 'Pending Payment'
        PAID            = 'Paid',            'Paid'
        PARTIAL         = 'Partial',         'Partially Paid'
        CREDIT          = 'Credit',          'Credit'
        WAIVED          = 'Waived',          'Waived'

    visit      = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='lab_orders')
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='lab_orders',
    )
    # Service master link (optional — allows free-text fallback)
    lab_service = models.ForeignKey(
        LabService, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders',
    )
    # Billing link
    invoice_item = models.OneToOneField(
        'InvoiceItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='lab_order',
    )
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    test_category  = models.CharField(max_length=100, blank=True)
    test_name      = models.CharField(max_length=200)
    priority       = models.CharField(max_length=20, choices=Priority.choices, default=Priority.ROUTINE)
    clinical_notes = models.TextField(blank=True)
    status         = models.CharField(max_length=20, choices=Status.choices, default=Status.WAITING_PAYMENT)
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING_PAYMENT,
    )
    result     = models.TextField(blank=True)
    ordered_at = models.DateTimeField(auto_now_add=True)
    resulted_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lab_results_released',
    )

    class Meta:
        ordering = ['-ordered_at']
        verbose_name = 'Lab Order'
        verbose_name_plural = 'Lab Orders'

    def __str__(self):
        return f"{self.test_name} [{self.priority}] — {self.status}"

    @property
    def can_collect_sample(self):
        return self.payment_status in (
            self.PaymentStatus.PAID, self.PaymentStatus.CREDIT, self.PaymentStatus.WAIVED,
        ) and self.status in (self.Status.SAMPLE_PENDING, self.Status.SAMPLE_COLLECTED)

    @property
    def payment_cleared(self):
        return self.payment_status in (
            self.PaymentStatus.PAID, self.PaymentStatus.CREDIT, self.PaymentStatus.WAIVED,
        )


class LabSample(models.Model):
    """Sample collection record linked one-to-one with a lab order."""

    class SampleType(models.TextChoices):
        BLOOD  = 'Blood',  'Blood'
        URINE  = 'Urine',  'Urine'
        STOOL  = 'Stool',  'Stool'
        SWAB   = 'Swab',   'Swab'
        TISSUE = 'Tissue', 'Tissue'
        CSF    = 'CSF',    'Cerebrospinal Fluid'
        SPUTUM = 'Sputum', 'Sputum'
        OTHER  = 'Other',  'Other'

    lab_order        = models.OneToOneField(LabOrder, on_delete=models.CASCADE, related_name='sample')
    sample_type      = models.CharField(max_length=20, choices=SampleType.choices)
    barcode          = models.CharField(max_length=60, blank=True, unique=True)
    collected_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='lab_samples_collected',
    )
    collected_at     = models.DateTimeField(default=timezone.now)
    patient_verified = models.BooleanField(default=True)
    notes            = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Lab Sample'
        verbose_name_plural = 'Lab Samples'

    def __str__(self):
        label = self.barcode or str(self.pk)
        return f"Sample {label} — {self.lab_order.test_name}"


class ImagingOrder(models.Model):
    class ImagingType(models.TextChoices):
        XRAY = 'X-Ray', 'X-Ray'
        CT = 'CT Scan', 'CT Scan'
        MRI = 'MRI', 'MRI'
        ULTRASOUND = 'Ultrasound', 'Ultrasound'
        ECG = 'ECG', 'ECG'
        ECHO = 'Echo', 'Echocardiogram'
        DOPPLER = 'Doppler', 'Doppler'
        OTHER = 'Other', 'Other'

    class Priority(models.TextChoices):
        ROUTINE = 'Routine', 'Routine'
        URGENT = 'Urgent', 'Urgent'
        STAT = 'STAT', 'STAT'

    class Status(models.TextChoices):
        PENDING = 'Pending', 'Pending'
        IN_PROGRESS = 'In Progress', 'In Progress'
        COMPLETED = 'Completed', 'Completed'
        CANCELLED = 'Cancelled', 'Cancelled'

    visit = models.ForeignKey(
        Visit, on_delete=models.PROTECT, related_name='imaging_orders',
    )
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='imaging_orders',
    )
    imaging_type = models.CharField(max_length=20, choices=ImagingType.choices)
    body_part = models.CharField(max_length=100)
    clinical_indication = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.ROUTINE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    report = models.TextField(blank=True)
    ordered_at = models.DateTimeField(auto_now_add=True)
    reported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-ordered_at']
        verbose_name = 'Imaging Order'
        verbose_name_plural = 'Imaging Orders'

    def __str__(self):
        return f"{self.imaging_type} — {self.body_part} [{self.priority}]"


class MedicationOrder(models.Model):
    class Route(models.TextChoices):
        PO = 'PO', 'Oral (PO)'
        IV = 'IV', 'Intravenous (IV)'
        IM = 'IM', 'Intramuscular (IM)'
        SC = 'SC', 'Subcutaneous (SC)'
        TOP = 'TOP', 'Topical'
        INH = 'INH', 'Inhaled'
        OTHER = 'OTHER', 'Other'

    class Status(models.TextChoices):
        ACTIVE = 'Active', 'Active'
        COMPLETED = 'Completed', 'Completed'
        DISCONTINUED = 'Discontinued', 'Discontinued'

    visit = models.ForeignKey(
        Visit, on_delete=models.PROTECT, related_name='medication_orders',
    )
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='medication_orders',
    )
    drug_name = models.CharField(max_length=200)
    dosage = models.CharField(max_length=100)
    route = models.CharField(max_length=10, choices=Route.choices, default=Route.PO)
    frequency = models.CharField(max_length=100)
    duration = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    instructions = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    ordered_at = models.DateTimeField(auto_now_add=True)
    discontinued_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-ordered_at']
        verbose_name = 'Medication Order'
        verbose_name_plural = 'Medication Orders'

    def __str__(self):
        return f"{self.drug_name} {self.dosage} {self.route} — {self.status}"


class ProcedureOrder(models.Model):
    class ProcedureType(models.TextChoices):
        SURGERY = 'Surgery', 'Surgery'
        PROCEDURE = 'Procedure', 'Procedure'
        PHYSIO = 'Physio', 'Physiotherapy'

    class Status(models.TextChoices):
        SCHEDULED = 'Scheduled', 'Scheduled'
        IN_PROGRESS = 'In Progress', 'In Progress'
        COMPLETED = 'Completed', 'Completed'
        CANCELLED = 'Cancelled', 'Cancelled'
        POSTPONED = 'Postponed', 'Postponed'

    visit = models.ForeignKey(
        Visit, on_delete=models.PROTECT, related_name='procedure_orders',
    )
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='procedure_orders',
    )
    procedure_type = models.CharField(max_length=20, choices=ProcedureType.choices)
    procedure_code = models.CharField(max_length=30, blank=True)
    procedure_name = models.CharField(max_length=255)
    scheduled_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    post_op_notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    ordered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-ordered_at']
        verbose_name = 'Procedure Order'
        verbose_name_plural = 'Procedure Orders'

    def __str__(self):
        return f"{self.procedure_type}: {self.procedure_name} — {self.status}"


class VitalSign(models.Model):
    visit = models.ForeignKey(
        Visit, on_delete=models.PROTECT, related_name='vital_signs',
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='vital_signs_recorded',
    )
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    bp_systolic = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse = models.PositiveSmallIntegerField(null=True, blank=True)
    respiratory_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    spo2 = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    height = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']
        verbose_name = 'Vital Sign'
        verbose_name_plural = 'Vital Signs'

    def __str__(self):
        return f"Vitals — {self.visit} @ {self.recorded_at:%Y-%m-%d %H:%M}"

    @property
    def bmi(self):
        if self.weight and self.height and self.height > 0:
            h_m = float(self.height) / 100
            return round(float(self.weight) / (h_m ** 2), 1)
        return None

    @property
    def blood_pressure(self):
        if self.bp_systolic and self.bp_diastolic:
            return f"{self.bp_systolic}/{self.bp_diastolic}"
        return '—'


# ── Queue (extend existing with status) ──────────────────────────────────────
# NOTE: Queue.status added via migration; existing Queue model stays unchanged


# ── Triage ────────────────────────────────────────────────────────────────────

class TriageAssessment(models.Model):
    class Severity(models.TextChoices):
        LEVEL_1 = '1', 'Level 1 — Resuscitation'
        LEVEL_2 = '2', 'Level 2 — Emergent'
        LEVEL_3 = '3', 'Level 3 — Urgent'
        LEVEL_4 = '4', 'Level 4 — Less Urgent'
        LEVEL_5 = '5', 'Level 5 — Non-Urgent'

    visit = models.OneToOneField(
        Visit, on_delete=models.PROTECT, related_name='triage',
    )
    triaged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='triage_assessments',
    )
    severity = models.CharField(max_length=1, choices=Severity.choices, default=Severity.LEVEL_3)
    chief_complaint = models.TextField()
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    bp_systolic = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse = models.PositiveSmallIntegerField(null=True, blank=True)
    respiratory_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    spo2 = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Triage Assessment'
        verbose_name_plural = 'Triage Assessments'

    def __str__(self):
        return f"Triage L{self.severity} — {self.visit.patient} ({self.created_at:%Y-%m-%d})"

    @property
    def severity_color(self):
        return {
            '1': 'red', '2': 'orange', '3': 'yellow', '4': 'blue', '5': 'green',
        }.get(self.severity, 'slate')


# ── Pharmacy ──────────────────────────────────────────────────────────────────

class PharmacyStock(models.Model):
    class DosageForm(models.TextChoices):
        TABLET = 'Tablet', 'Tablet'
        CAPSULE = 'Capsule', 'Capsule'
        SYRUP = 'Syrup', 'Syrup'
        INJECTION = 'Injection', 'Injection'
        CREAM = 'Cream', 'Cream'
        DROPS = 'Drops', 'Drops'
        INHALER = 'Inhaler', 'Inhaler'
        SUPPOSITORY = 'Suppository', 'Suppository'
        PATCH = 'Patch', 'Patch'
        OTHER = 'Other', 'Other'

    drug_name = models.CharField(max_length=200)
    generic_name = models.CharField(max_length=200, blank=True)
    category = models.CharField(max_length=100, blank=True)
    dosage_form = models.CharField(max_length=20, choices=DosageForm.choices, default=DosageForm.TABLET)
    strength = models.CharField(max_length=100, blank=True)
    quantity_in_stock = models.IntegerField(default=0)
    unit = models.CharField(max_length=50, default='Tablets')
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reorder_level = models.IntegerField(default=50)
    batch_number = models.CharField(max_length=50, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    supplier = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['drug_name']
        verbose_name = 'Pharmacy Stock'
        verbose_name_plural = 'Pharmacy Stock'

    def __str__(self):
        return f"{self.drug_name} {self.strength} ({self.quantity_in_stock} {self.unit})"

    @property
    def is_low_stock(self):
        return self.quantity_in_stock <= self.reorder_level

    @property
    def is_expired(self):
        from django.utils import timezone
        if self.expiry_date:
            return self.expiry_date <= timezone.localdate()
        return False


class Dispensing(models.Model):
    class Status(models.TextChoices):
        DISPENSED = 'Dispensed', 'Dispensed'
        PARTIAL = 'Partial', 'Partial'
        CANCELLED = 'Cancelled', 'Cancelled'

    medication_order = models.OneToOneField(
        MedicationOrder, on_delete=models.PROTECT,
        related_name='dispensing', null=True, blank=True,
    )
    pharmacy_stock = models.ForeignKey(
        PharmacyStock, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='dispensings',
    )
    patient = models.ForeignKey(
        Patient, on_delete=models.PROTECT, related_name='dispensings',
    )
    drug_name = models.CharField(max_length=200)
    quantity_dispensed = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    dispensed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='dispensings',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DISPENSED)
    notes = models.TextField(blank=True)
    dispensed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-dispensed_at']
        verbose_name = 'Dispensing Record'
        verbose_name_plural = 'Dispensing Records'

    def __str__(self):
        return f"{self.drug_name} × {self.quantity_dispensed} — {self.patient}"


# ── Billing ───────────────────────────────────────────────────────────────────

class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT          = 'Draft',          'Draft'
        ISSUED         = 'Issued',         'Issued'
        CREDIT_PENDING = 'Credit Pending', 'Credit Pending'
        PARTIAL        = 'Partial',        'Partially Paid'
        PAID           = 'Paid',           'Fully Paid'
        OVERPAID       = 'Overpaid',       'Overpaid'
        REFUNDED       = 'Refunded',       'Refunded'
        CANCELLED      = 'Cancelled',      'Cancelled'
        WAIVED         = 'Waived',         'Waived'

    class PaymentType(models.TextChoices):
        CASH   = 'Cash',   'Cash'
        CREDIT = 'Credit', 'Credit'

    invoice_number  = models.CharField(max_length=20, unique=True, blank=True)
    patient         = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='invoices')
    visit           = models.ForeignKey(Visit, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')
    created_by      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='invoices_created',
    )
    status          = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    payment_type    = models.CharField(max_length=10, choices=PaymentType.choices, default=PaymentType.CASH)
    total_amount    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes           = models.TextField(blank=True)
    due_date        = models.DateField(null=True, blank=True)
    # Credit-specific fields
    credit_approved_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_credits',
    )
    credit_approved_at  = models.DateTimeField(null=True, blank=True)
    credit_reason       = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'
        indexes = [models.Index(fields=['patient', '-created_at'])]

    def __str__(self):
        return f"{self.invoice_number} — {self.patient} ({self.status})"

    @property
    def balance(self):
        return self.total_amount - self.paid_amount - self.discount

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            from django.utils import timezone
            last = Invoice.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.invoice_number = f"INV-{timezone.localdate():%Y%m}-{next_id:04d}"
        super().save(*args, **kwargs)


class InvoiceItem(models.Model):
    class ServiceType(models.TextChoices):
        CONSULTATION = 'Consultation', 'Consultation'
        LAB = 'Laboratory', 'Laboratory'
        IMAGING = 'Imaging', 'Imaging'
        MEDICATION = 'Medication', 'Medication'
        PROCEDURE = 'Procedure', 'Procedure'
        BED = 'Bed / Room', 'Bed / Room'
        NURSING = 'Nursing Care', 'Nursing Care'
        OTHER = 'Other', 'Other'

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=255)
    service_type = models.CharField(max_length=20, choices=ServiceType.choices, default=ServiceType.OTHER)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        verbose_name = 'Invoice Item'
        verbose_name_plural = 'Invoice Items'

    def __str__(self):
        return f"{self.description} × {self.quantity}"

    def save(self, *args, **kwargs):
        self.total = self.quantity * self.unit_price
        super().save(*args, **kwargs)


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH         = 'Cash',         'Cash'
        BANK_TRANSFER = 'Bank Transfer', 'Bank Transfer'
        MOBILE_MONEY  = 'Mobile Money',  'Mobile Money'
        CARD          = 'Card',          'Card'
        INSURANCE     = 'Insurance',     'Insurance'
        OTHER         = 'Other',         'Other'

    invoice          = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='payments')
    amount           = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method   = models.CharField(max_length=20, choices=Method.choices, default=Method.CASH)
    reference_number = models.CharField(max_length=100, blank=True)
    receipt_number   = models.CharField(max_length=30, blank=True)
    # Cash-specific
    cash_received    = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    change_given     = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    received_by      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='payments_received',
    )
    payment_date     = models.DateField(default=None)
    notes            = models.TextField(blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'

    def __str__(self):
        return f"{self.invoice.invoice_number} — {self.payment_method} {self.amount}"

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            from django.utils import timezone
            ts = timezone.now().strftime('%Y%m%d%H%M%S')
            self.receipt_number = f"RCP-{ts}-{(self.pk or 0):04d}"
        super().save(*args, **kwargs)
        # Fix receipt_number after pk is set on first save
        if self.receipt_number.endswith('-0000') and self.pk:
            self.receipt_number = f"RCP-{timezone.now().strftime('%Y%m%d%H%M%S')}-{self.pk:04d}"
            Payment.objects.filter(pk=self.pk).update(receipt_number=self.receipt_number)


class CashSession(models.Model):
    class Status(models.TextChoices):
        OPEN       = 'Open',       'Open'
        CLOSED     = 'Closed',     'Closed'
        RECONCILED = 'Reconciled', 'Reconciled'

    cashier           = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_sessions',
    )
    date              = models.DateField()
    opening_balance   = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    closing_balance   = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    expected_closing  = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discrepancy       = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status            = models.CharField(max_length=15, choices=Status.choices, default=Status.OPEN)
    notes             = models.TextField(blank=True)
    opened_at         = models.DateTimeField(auto_now_add=True)
    closed_at         = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date', '-opened_at']
        verbose_name = 'Cash Session'
        verbose_name_plural = 'Cash Sessions'

    def __str__(self):
        return f"{self.cashier.get_full_name()} — {self.date} ({self.status})"

    @property
    def total_collected(self):
        from django.db.models import Sum
        return (
            Payment.objects
            .filter(payment_method=Payment.Method.CASH, payment_date=self.date, received_by=self.cashier)
            .aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
        )


# ── Store / Inventory ─────────────────────────────────────────────────────────

class InventoryCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Inventory Category'
        verbose_name_plural = 'Inventory Categories'

    def __str__(self):
        return self.name


class InventoryItem(models.Model):
    class ItemType(models.TextChoices):
        MEDICAL_SUPPLY = 'Medical Supply',  'Medical Supply'
        LAB_SUPPLY     = 'Lab Supply',      'Lab Supply'
        SURGICAL       = 'Surgical',        'Surgical Supply'
        EQUIPMENT      = 'Equipment',       'Equipment / Asset'
        MEDICATION     = 'Medication',      'Medication'
        GENERAL        = 'General',         'General Store'
        OTHER          = 'Other',           'Other'

    # Core
    name        = models.CharField(max_length=200)
    item_type   = models.CharField(max_length=20, choices=ItemType.choices, default=ItemType.GENERAL)
    category    = models.ForeignKey(
        InventoryCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='items',
    )
    subcategory = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    sku         = models.CharField(max_length=50, blank=True)
    barcode     = models.CharField(max_length=100, blank=True)
    brand       = models.CharField(max_length=100, blank=True)
    manufacturer = models.CharField(max_length=200, blank=True)

    # Units & packaging
    unit               = models.CharField(max_length=50, default='units')
    unit_purchase      = models.CharField(max_length=50, blank=True, help_text='e.g. Carton')
    units_per_purchase = models.DecimalField(max_digits=10, decimal_places=2, default=1,
                                             help_text='How many storage units per purchase unit')

    # Stock control
    quantity_in_stock = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    quantity_damaged  = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    quantity_reserved = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    reorder_level     = models.DecimalField(max_digits=12, decimal_places=2, default=10)
    min_stock         = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    max_stock         = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    safety_stock      = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Pricing
    unit_cost        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    selling_price    = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Supplier & location
    supplier_name     = models.CharField(max_length=200, blank=True)
    supplier          = models.ForeignKey(
        'Supplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_items',
    )
    storage_location  = models.ForeignKey(
        'StorageLocation', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='inventory_items',
    )

    # Expiry tracking
    has_expiry = models.BooleanField(default=False)

    is_active  = models.BooleanField(default=True)
    notes      = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['item_type', 'name']
        verbose_name = 'Inventory Item'
        verbose_name_plural = 'Inventory Items'

    def __str__(self):
        return f"{self.name} ({self.quantity_in_stock} {self.unit})"

    @property
    def is_low_stock(self):
        return self.quantity_in_stock <= self.reorder_level

    @property
    def is_out_of_stock(self):
        return self.quantity_in_stock <= 0

    @property
    def available_quantity(self):
        return self.quantity_in_stock - self.quantity_reserved

    @property
    def inventory_value(self):
        return (self.quantity_in_stock or 0) * (self.unit_cost or 0)

    @property
    def stock_status(self):
        if self.is_out_of_stock:
            return 'Out of Stock'
        if self.is_low_stock:
            return 'Low Stock'
        return 'In Stock'


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'Draft', 'Draft'
        SUBMITTED = 'Submitted', 'Submitted'
        APPROVED = 'Approved', 'Approved'
        RECEIVED = 'Received', 'Received'
        CANCELLED = 'Cancelled', 'Cancelled'

    po_number = models.CharField(max_length=20, unique=True, blank=True)
    supplier_name = models.CharField(max_length=200)
    supplier_contact = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='purchase_orders',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_purchase_orders',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    ordered_at = models.DateTimeField(auto_now_add=True)
    expected_delivery = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-ordered_at']
        verbose_name = 'Purchase Order'
        verbose_name_plural = 'Purchase Orders'

    def __str__(self):
        return f"{self.po_number} — {self.supplier_name} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.po_number:
            from django.utils import timezone
            last = PurchaseOrder.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.po_number = f"PO-{timezone.localdate():%Y%m}-{next_id:04d}"
        super().save(*args, **kwargs)


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items')
    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='po_items',
    )
    item_name = models.CharField(max_length=200)
    quantity_ordered = models.IntegerField(default=1)
    quantity_received = models.IntegerField(default=0)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        verbose_name = 'Purchase Order Item'
        verbose_name_plural = 'Purchase Order Items'

    def __str__(self):
        return f"{self.item_name} × {self.quantity_ordered}"

    def save(self, *args, **kwargs):
        self.total = self.quantity_ordered * self.unit_cost
        super().save(*args, **kwargs)


# ── Inventory Batch (non-medication items with expiry) ─────────────────────────

class InventoryBatch(models.Model):
    """Batch/lot tracking for InventoryItems that have has_expiry=True."""

    inventory_item    = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='batches')
    batch_number      = models.CharField(max_length=80)
    lot_number        = models.CharField(max_length=80, blank=True)
    manufacturing_date = models.DateField(null=True, blank=True)
    expiration_date   = models.DateField(null=True, blank=True)
    quantity_received = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    quantity_available = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    purchase_price    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    supplier          = models.ForeignKey(
        'Supplier', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='inv_batches',
    )
    location          = models.ForeignKey(
        'StorageLocation', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='inv_batches',
    )
    received_date     = models.DateField(default=timezone.localdate)
    received_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='inv_batches_received',
    )
    purchase_order_ref = models.CharField(max_length=50, blank=True)
    invoice_number    = models.CharField(max_length=80, blank=True)
    is_active         = models.BooleanField(default=True)
    notes             = models.TextField(blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['expiration_date', 'batch_number']
        unique_together = [('inventory_item', 'batch_number')]
        verbose_name = 'Inventory Batch'
        verbose_name_plural = 'Inventory Batches'

    def __str__(self):
        return f"{self.inventory_item.name} — Batch {self.batch_number}"

    @property
    def days_to_expiry(self):
        if not self.expiration_date:
            return None
        return (self.expiration_date - timezone.localdate()).days

    @property
    def is_expired(self):
        d = self.days_to_expiry
        return d is not None and d < 0

    @property
    def expiry_status(self):
        d = self.days_to_expiry
        if d is None:
            return 'No Expiry'
        if d < 0:
            return 'Expired'
        if d <= 30:
            return 'Expiring Soon (30d)'
        if d <= 60:
            return 'Expiring (60d)'
        if d <= 90:
            return 'Expiring (90d)'
        return 'Good'


# ── Inventory Transaction Log ──────────────────────────────────────────────────

class InventoryTransaction(models.Model):
    class TxType(models.TextChoices):
        PURCHASE        = 'purchase',        'Purchase / Receiving'
        ISSUE           = 'issue',           'Issue to Department'
        RETURN          = 'return',          'Return from Department'
        ADJUSTMENT_IN   = 'adjustment_in',   'Adjustment (In)'
        ADJUSTMENT_OUT  = 'adjustment_out',  'Adjustment (Out)'
        TRANSFER_IN     = 'transfer_in',     'Transfer In'
        TRANSFER_OUT    = 'transfer_out',    'Transfer Out'
        DAMAGED         = 'damaged',         'Damaged / Disposed'
        EXPIRED         = 'expired',         'Expired Disposal'
        COUNT_ADJUST    = 'count_adjust',    'Physical Count Adjustment'

    inventory_item   = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='transactions')
    batch            = models.ForeignKey(
        InventoryBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transactions',
    )
    transaction_type = models.CharField(max_length=20, choices=TxType.choices)
    quantity_in      = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    quantity_out     = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    balance_after    = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    unit_cost        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    reference_number = models.CharField(max_length=100, blank=True)
    department       = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='inv_transactions',
    )
    notes            = models.TextField(blank=True)
    performed_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='inv_transactions',
    )
    transaction_date = models.DateTimeField(default=timezone.now)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-transaction_date']
        verbose_name = 'Inventory Transaction'
        verbose_name_plural = 'Inventory Transactions'

    def __str__(self):
        return f"{self.inventory_item.name} — {self.transaction_type} ({self.transaction_date:%Y-%m-%d})"


# ── Equipment Asset Management ─────────────────────────────────────────────────

class EquipmentAsset(models.Model):
    class AssetStatus(models.TextChoices):
        ACTIVE      = 'Active',      'Active / In Use'
        IN_REPAIR   = 'In Repair',   'In Repair'
        IDLE        = 'Idle',        'Idle / Stored'
        RETIRED     = 'Retired',     'Retired'
        DISPOSED    = 'Disposed',    'Disposed'
        MISSING     = 'Missing',     'Missing'

    inventory_item  = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assets',
    )
    name            = models.CharField(max_length=200)
    asset_code      = models.CharField(max_length=50, unique=True)
    serial_number   = models.CharField(max_length=100, blank=True)
    model_number    = models.CharField(max_length=100, blank=True)
    brand           = models.CharField(max_length=100, blank=True)
    category        = models.ForeignKey(
        InventoryCategory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assets',
    )
    department      = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='equipment_assets',
    )
    location_detail = models.CharField(max_length=200, blank=True, help_text='Room/Ward/Shelf')
    status          = models.CharField(max_length=20, choices=AssetStatus.choices, default=AssetStatus.ACTIVE)

    # Procurement
    purchase_date   = models.DateField(null=True, blank=True)
    purchase_price  = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    supplier        = models.ForeignKey(
        'Supplier', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='equipment_assets',
    )
    purchase_order  = models.ForeignKey(
        PurchaseOrder, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='equipment_assets',
    )
    invoice_number  = models.CharField(max_length=100, blank=True)

    # Warranty & maintenance
    warranty_expiry    = models.DateField(null=True, blank=True)
    last_maintenance   = models.DateField(null=True, blank=True)
    next_maintenance   = models.DateField(null=True, blank=True)
    maintenance_notes  = models.TextField(blank=True)

    is_active     = models.BooleanField(default=True)
    notes         = models.TextField(blank=True)
    created_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assets_created',
    )
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'asset_code']
        verbose_name = 'Equipment Asset'
        verbose_name_plural = 'Equipment Assets'

    def __str__(self):
        return f"{self.asset_code} — {self.name}"

    @property
    def warranty_status(self):
        if not self.warranty_expiry:
            return 'Unknown'
        d = (self.warranty_expiry - timezone.localdate()).days
        if d < 0:
            return 'Expired'
        if d <= 90:
            return 'Expiring Soon'
        return 'Valid'

    @property
    def maintenance_overdue(self):
        if not self.next_maintenance:
            return False
        return self.next_maintenance < timezone.localdate()


class EquipmentMaintenance(models.Model):
    class MaintenanceType(models.TextChoices):
        PREVENTIVE  = 'Preventive',  'Preventive'
        CORRECTIVE  = 'Corrective',  'Corrective'
        EMERGENCY   = 'Emergency',   'Emergency'
        CALIBRATION = 'Calibration', 'Calibration'
        INSPECTION  = 'Inspection',  'Inspection'

    asset             = models.ForeignKey(EquipmentAsset, on_delete=models.CASCADE, related_name='maintenance_records')
    maintenance_type  = models.CharField(max_length=20, choices=MaintenanceType.choices)
    description       = models.TextField()
    technician_name   = models.CharField(max_length=200, blank=True)
    technician_contact = models.CharField(max_length=100, blank=True)
    cost              = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    maintenance_date  = models.DateField(default=timezone.localdate)
    next_due          = models.DateField(null=True, blank=True)
    status_after      = models.CharField(max_length=20, choices=EquipmentAsset.AssetStatus.choices, blank=True)
    notes             = models.TextField(blank=True)
    recorded_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='maintenance_recorded',
    )
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-maintenance_date']
        verbose_name = 'Equipment Maintenance'
        verbose_name_plural = 'Equipment Maintenance Records'

    def __str__(self):
        return f"{self.asset.asset_code} — {self.maintenance_type} ({self.maintenance_date})"


# ── Purchase Request System ────────────────────────────────────────────────────

class PurchaseRequest(models.Model):
    class Status(models.TextChoices):
        DRAFT     = 'Draft',     'Draft'
        PENDING   = 'Pending',   'Pending Approval'
        APPROVED  = 'Approved',  'Approved'
        REJECTED  = 'Rejected',  'Rejected'
        CONVERTED = 'Converted', 'Converted to PO'
        CANCELLED = 'Cancelled', 'Cancelled'

    class Priority(models.TextChoices):
        NORMAL    = 'Normal',    'Normal'
        URGENT    = 'Urgent',    'Urgent'
        EMERGENCY = 'Emergency', 'Emergency'

    request_number  = models.CharField(max_length=20, unique=True, blank=True)
    department      = models.ForeignKey(Department, on_delete=models.PROTECT, related_name='purchase_requests')
    requested_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='purchase_requests',
    )
    status          = models.CharField(max_length=15, choices=Status.choices, default=Status.DRAFT)
    priority        = models.CharField(max_length=15, choices=Priority.choices, default=Priority.NORMAL)
    reason          = models.TextField(blank=True)
    required_by     = models.DateField(null=True, blank=True)

    approved_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='approved_purchase_requests',
    )
    approval_notes  = models.TextField(blank=True)
    approval_date   = models.DateTimeField(null=True, blank=True)
    purchase_order  = models.OneToOneField(
        PurchaseOrder, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='purchase_request',
    )

    request_date    = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-request_date']
        verbose_name = 'Purchase Request'
        verbose_name_plural = 'Purchase Requests'

    def __str__(self):
        return f"{self.request_number} — {self.department} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.request_number:
            last = PurchaseRequest.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.request_number = f"PR-{timezone.localdate():%Y%m}-{next_id:04d}"
        super().save(*args, **kwargs)


class PurchaseRequestItem(models.Model):
    purchase_request      = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name='items')
    inventory_item        = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='purchase_request_items',
    )
    item_name             = models.CharField(max_length=200)
    quantity_requested    = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    quantity_approved     = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unit                  = models.CharField(max_length=50, blank=True)
    estimated_unit_cost   = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes                 = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = 'Purchase Request Item'
        verbose_name_plural = 'Purchase Request Items'

    def __str__(self):
        return f"{self.item_name} × {self.quantity_requested}"


# ── Physical Stock Count ───────────────────────────────────────────────────────

class StockCountSession(models.Model):
    class CountType(models.TextChoices):
        FULL    = 'Full',    'Full Count'
        PARTIAL = 'Partial', 'Partial / Category'
        SPOT    = 'Spot',    'Spot Check'

    class Status(models.TextChoices):
        PLANNED     = 'Planned',     'Planned'
        IN_PROGRESS = 'In Progress', 'In Progress'
        SUBMITTED   = 'Submitted',   'Submitted for Approval'
        APPROVED    = 'Approved',    'Approved'
        CANCELLED   = 'Cancelled',   'Cancelled'

    count_number  = models.CharField(max_length=20, unique=True, blank=True)
    count_type    = models.CharField(max_length=10, choices=CountType.choices, default=CountType.FULL)
    category      = models.ForeignKey(
        InventoryCategory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_counts',
    )
    location      = models.ForeignKey(
        'StorageLocation', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_counts',
    )
    status        = models.CharField(max_length=15, choices=Status.choices, default=Status.PLANNED)
    notes         = models.TextField(blank=True)
    created_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='stock_counts_created',
    )
    approved_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='stock_counts_approved',
    )
    approval_notes = models.TextField(blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    completed_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Stock Count Session'
        verbose_name_plural = 'Stock Count Sessions'

    def __str__(self):
        return f"{self.count_number} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.count_number:
            last = StockCountSession.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.count_number = f"SC-{timezone.localdate():%Y%m}-{next_id:04d}"
        super().save(*args, **kwargs)


class StockCountItem(models.Model):
    count_session    = models.ForeignKey(StockCountSession, on_delete=models.CASCADE, related_name='items')
    inventory_item   = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='count_items')
    system_quantity  = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    counted_quantity = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    is_approved      = models.BooleanField(default=False)
    notes            = models.CharField(max_length=300, blank=True)

    class Meta:
        unique_together = [('count_session', 'inventory_item')]
        verbose_name = 'Stock Count Item'
        verbose_name_plural = 'Stock Count Items'

    def __str__(self):
        return f"{self.count_session.count_number} — {self.inventory_item.name}"

    @property
    def variance(self):
        if self.counted_quantity is None:
            return None
        return self.counted_quantity - self.system_quantity

    @property
    def has_variance(self):
        v = self.variance
        return v is not None and v != 0


# ── Human Resources ───────────────────────────────────────────────────────────

class Employee(models.Model):
    class EmploymentType(models.TextChoices):
        PERMANENT = 'Permanent', 'Permanent'
        CONTRACT = 'Contract', 'Contract'
        PART_TIME = 'Part Time', 'Part Time'
        INTERN = 'Intern', 'Intern'

    class EmploymentStatus(models.TextChoices):
        ACTIVE = 'Active', 'Active'
        ON_LEAVE = 'On Leave', 'On Leave'
        TERMINATED = 'Terminated', 'Terminated'
        RESIGNED = 'Resigned', 'Resigned'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='employee_profile',
    )
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='employees',
    )
    position = models.CharField(max_length=100, blank=True)
    employment_type = models.CharField(
        max_length=20, choices=EmploymentType.choices, default=EmploymentType.PERMANENT,
    )
    employment_status = models.CharField(
        max_length=20, choices=EmploymentStatus.choices, default=EmploymentStatus.ACTIVE,
    )
    hire_date = models.DateField(null=True, blank=True)
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    national_id = models.CharField(max_length=50, blank=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user__last_name', 'user__first_name']
        verbose_name = 'Employee'
        verbose_name_plural = 'Employees'

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} — {self.position or 'Staff'}"

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.username


class Attendance(models.Model):
    class AttendanceStatus(models.TextChoices):
        PRESENT = 'Present', 'Present'
        ABSENT = 'Absent', 'Absent'
        LATE = 'Late', 'Late'
        HALF_DAY = 'Half Day', 'Half Day'
        ON_LEAVE = 'On Leave', 'On Leave'

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='attendances')
    date = models.DateField()
    time_in = models.TimeField(null=True, blank=True)
    time_out = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=AttendanceStatus.choices, default=AttendanceStatus.PRESENT)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='attendance_records',
    )

    class Meta:
        ordering = ['-date', 'employee']
        unique_together = [['employee', 'date']]
        verbose_name = 'Attendance'
        verbose_name_plural = 'Attendance Records'

    def __str__(self):
        return f"{self.employee} — {self.date} ({self.status})"

    @property
    def hours_worked(self):
        if self.time_in and self.time_out:
            from datetime import datetime, date
            ti = datetime.combine(date.today(), self.time_in)
            to = datetime.combine(date.today(), self.time_out)
            delta = to - ti
            return round(delta.total_seconds() / 3600, 1)
        return None


class LeaveRequest(models.Model):
    class LeaveType(models.TextChoices):
        ANNUAL = 'Annual', 'Annual Leave'
        SICK = 'Sick', 'Sick Leave'
        MATERNITY = 'Maternity', 'Maternity Leave'
        PATERNITY = 'Paternity', 'Paternity Leave'
        EMERGENCY = 'Emergency', 'Emergency Leave'
        UNPAID = 'Unpaid', 'Unpaid Leave'

    class Status(models.TextChoices):
        PENDING = 'Pending', 'Pending'
        APPROVED = 'Approved', 'Approved'
        REJECTED = 'Rejected', 'Rejected'
        CANCELLED = 'Cancelled', 'Cancelled'

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='leave_requests')
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    days_requested = models.PositiveIntegerField(default=1)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='leave_reviews',
    )
    review_notes = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-requested_at']
        verbose_name = 'Leave Request'
        verbose_name_plural = 'Leave Requests'

    def __str__(self):
        return f"{self.employee} — {self.leave_type} {self.start_date}–{self.end_date} ({self.status})"


# ── Anesthesia ────────────────────────────────────────────────────────────────

class AnesthesiaRecord(models.Model):
    class AnesthesiaType(models.TextChoices):
        GENERAL = 'General', 'General Anesthesia'
        REGIONAL = 'Regional', 'Regional Anesthesia'
        LOCAL = 'Local', 'Local Anesthesia'
        SPINAL = 'Spinal', 'Spinal Block'
        EPIDURAL = 'Epidural', 'Epidural Block'
        SEDATION = 'Sedation', 'Conscious Sedation'

    class ASA(models.TextChoices):
        I = 'ASA I', 'ASA I — Normal healthy patient'
        II = 'ASA II', 'ASA II — Mild systemic disease'
        III = 'ASA III', 'ASA III — Severe systemic disease'
        IV = 'ASA IV', 'ASA IV — Severe disease, constant threat to life'
        V = 'ASA V', 'ASA V — Moribund patient'

    visit = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='anesthesia_records')
    procedure_order = models.ForeignKey(
        ProcedureOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name='anesthesia_records',
    )
    anesthesiologist = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='anesthesia_records',
    )
    anesthesia_type = models.CharField(max_length=20, choices=AnesthesiaType.choices)
    asa_classification = models.CharField(max_length=10, choices=ASA.choices, blank=True)
    pre_op_assessment = models.TextField(blank=True)
    intra_op_notes = models.TextField(blank=True)
    post_op_notes = models.TextField(blank=True)
    complications = models.TextField(blank=True)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Anesthesia Record'
        verbose_name_plural = 'Anesthesia Records'

    def __str__(self):
        return f"{self.anesthesia_type} — {self.visit.patient} ({self.created_at:%Y-%m-%d})"


# ── Medication Inventory Management ──────────────────────────────────────────

class Supplier(models.Model):
    name            = models.CharField(max_length=200)
    code            = models.CharField(max_length=20, unique=True)
    contact_person  = models.CharField(max_length=100, blank=True)
    phone           = models.CharField(max_length=20, blank=True)
    email           = models.EmailField(blank=True)
    address         = models.TextField(blank=True)
    is_active       = models.BooleanField(default=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class MedicationCategory(models.Model):
    name        = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Medication Categories'

    def __str__(self):
        return self.name


class StorageLocation(models.Model):
    class Condition(models.TextChoices):
        ROOM_TEMP       = 'Room Temperature', 'Room Temperature'
        REFRIGERATED    = 'Refrigerated',     'Refrigerated (2–8 °C)'
        FROZEN          = 'Frozen',           'Frozen (< −18 °C)'
        LIGHT_PROTECTED = 'Light Protected',  'Light Protected'

    name              = models.CharField(max_length=100)
    warehouse         = models.CharField(max_length=100, blank=True)
    shelf             = models.CharField(max_length=50, blank=True)
    bin_number        = models.CharField(max_length=50, blank=True)
    storage_condition = models.CharField(max_length=20, choices=Condition.choices, default=Condition.ROOM_TEMP)

    class Meta:
        ordering = ['name']

    def __str__(self):
        parts = [self.name]
        if self.warehouse:  parts.append(self.warehouse)
        if self.shelf:      parts.append(f'Shelf {self.shelf}')
        return ' / '.join(parts)


class Medication(models.Model):
    class DrugType(models.TextChoices):
        TABLET      = 'Tablet',      'Tablet'
        CAPSULE     = 'Capsule',     'Capsule'
        SYRUP       = 'Syrup',       'Syrup'
        INJECTION   = 'Injection',   'Injection'
        CREAM       = 'Cream',       'Cream'
        DROPS       = 'Drops',       'Drops'
        INHALER     = 'Inhaler',     'Inhaler'
        SUPPOSITORY = 'Suppository', 'Suppository'
        PATCH       = 'Patch',       'Patch'
        POWDER      = 'Powder',      'Powder'
        GEL         = 'Gel',         'Gel'
        OINTMENT    = 'Ointment',    'Ointment'
        SOLUTION    = 'Solution',    'Solution'
        SUSPENSION  = 'Suspension',  'Suspension'
        OTHER       = 'Other',       'Other'

    class Route(models.TextChoices):
        ORAL        = 'Oral',           'Oral'
        IV          = 'Intravenous',    'Intravenous (IV)'
        IM          = 'Intramuscular',  'Intramuscular (IM)'
        SC          = 'Subcutaneous',   'Subcutaneous (SC)'
        TOPICAL     = 'Topical',        'Topical'
        INHALATION  = 'Inhalation',     'Inhalation'
        SUBLINGUAL  = 'Sublingual',     'Sublingual'
        RECTAL      = 'Rectal',         'Rectal'
        NASAL       = 'Nasal',          'Nasal'
        OPHTHALMIC  = 'Ophthalmic',     'Ophthalmic'
        OTIC        = 'Otic',           'Otic'
        OTHER       = 'Other',          'Other'

    class StorageCondition(models.TextChoices):
        ROOM_TEMP       = 'Room Temperature', 'Room Temperature'
        REFRIGERATED    = 'Refrigerated',     'Refrigerated (2–8 °C)'
        FROZEN          = 'Frozen',           'Frozen (< −18 °C)'
        LIGHT_PROTECTED = 'Light Protected',  'Light Protected'

    # Basic
    name                = models.CharField(max_length=200)          # Brand name
    generic_name        = models.CharField(max_length=200)
    scientific_name     = models.CharField(max_length=200, blank=True)
    code                = models.CharField(max_length=50, unique=True)
    barcode             = models.CharField(max_length=100, blank=True)
    category            = models.ForeignKey(MedicationCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='medications')
    therapeutic_class   = models.CharField(max_length=100, blank=True)
    drug_type           = models.CharField(max_length=20, choices=DrugType.choices, default=DrugType.TABLET)
    strength            = models.CharField(max_length=100)          # e.g. 500 mg
    dosage_form         = models.CharField(max_length=100, blank=True)
    route               = models.CharField(max_length=20, choices=Route.choices, default=Route.ORAL)
    manufacturer        = models.CharField(max_length=200, blank=True)
    country_of_origin   = models.CharField(max_length=100, blank=True)
    supplier            = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL, related_name='medications')

    # Packaging
    unit_of_measure     = models.CharField(max_length=50, default='Tablet')
    pack_description    = models.CharField(max_length=200, blank=True)  # e.g. 1 Box = 10 Strips × 10 Tablets
    units_per_pack      = models.PositiveIntegerField(default=1)
    purchase_unit       = models.CharField(max_length=50, blank=True, default='Box')
    dispensing_unit     = models.CharField(max_length=50, blank=True, default='Tablet')
    conversion_factor   = models.PositiveIntegerField(default=1)    # dispensing units per purchase unit

    # Pricing (ETB)
    purchase_price      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    selling_price       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    wholesale_price     = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    insurance_price     = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Stock control levels (in dispensing units)
    minimum_stock       = models.PositiveIntegerField(default=0)
    maximum_stock       = models.PositiveIntegerField(default=0)
    reorder_level       = models.PositiveIntegerField(default=0)
    reorder_quantity    = models.PositiveIntegerField(default=0)
    safety_stock        = models.PositiveIntegerField(default=0)

    # Regulatory
    registration_number = models.CharField(max_length=100, blank=True)
    regulatory_approval = models.CharField(max_length=100, blank=True)
    controlled_substance = models.BooleanField(default=False)
    prescription_required = models.BooleanField(default=True)

    # Storage
    location            = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.SET_NULL, related_name='medications')
    storage_condition   = models.CharField(max_length=20, choices=StorageCondition.choices, default=StorageCondition.ROOM_TEMP)

    # Meta
    is_active   = models.BooleanField(default=True)
    notes       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} — {self.strength}"

    @property
    def current_stock(self):
        from django.db.models import Sum
        return (
            self.batches.filter(is_active=True, quantity_available__gt=0)
            .aggregate(total=Sum('quantity_available'))['total'] or 0
        )

    @property
    def is_low_stock(self):
        s = self.current_stock
        return self.reorder_level > 0 and 0 < s <= self.reorder_level

    @property
    def is_out_of_stock(self):
        return self.current_stock == 0

    @property
    def stock_status(self):
        s = self.current_stock
        if s == 0:
            return 'Out of Stock'
        if self.reorder_level and s <= self.reorder_level:
            return 'Low Stock'
        if self.maximum_stock and s >= self.maximum_stock:
            return 'Overstock'
        return 'Available'

    @property
    def inventory_value(self):
        return self.current_stock * self.purchase_price

    @property
    def near_expiry_batches(self):
        from datetime import date, timedelta
        threshold = date.today() + timedelta(days=90)
        return self.batches.filter(
            is_active=True, quantity_available__gt=0,
            expiration_date__lte=threshold, expiration_date__gte=date.today(),
        )

    @property
    def expired_batches(self):
        from datetime import date
        return self.batches.filter(
            is_active=True, quantity_available__gt=0,
            expiration_date__lt=date.today(),
        )


class MedicationBatch(models.Model):
    medication          = models.ForeignKey(Medication, on_delete=models.CASCADE, related_name='batches')
    batch_number        = models.CharField(max_length=100)
    lot_number          = models.CharField(max_length=100, blank=True)
    manufacturing_date  = models.DateField(null=True, blank=True)
    expiration_date     = models.DateField()
    quantity_received   = models.PositiveIntegerField()
    quantity_available  = models.PositiveIntegerField()
    purchase_price      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    supplier            = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL, related_name='batches')
    location            = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.SET_NULL, related_name='batches')
    received_date       = models.DateField(null=True, blank=True)
    received_by         = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='batches_received')
    purchase_order_ref  = models.CharField(max_length=100, blank=True)
    invoice_number      = models.CharField(max_length=100, blank=True)
    is_active           = models.BooleanField(default=True)
    notes               = models.TextField(blank=True)
    created_at          = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['expiration_date']
        unique_together = [('medication', 'batch_number')]

    def __str__(self):
        return f"Batch {self.batch_number} — {self.medication.name}"

    @property
    def days_to_expiry(self):
        from datetime import date
        return (self.expiration_date - date.today()).days

    @property
    def is_expired(self):
        from datetime import date
        return self.expiration_date < date.today()

    @property
    def expiry_status(self):
        d = self.days_to_expiry
        if d < 0:   return 'Expired'
        if d <= 30: return 'Near Expiry'
        if d <= 60: return 'Expiring Soon'
        if d <= 90: return 'Within 90 Days'
        return 'Valid'

    @property
    def expiry_color(self):
        s = self.expiry_status
        return {'Expired': 'red', 'Near Expiry': 'orange',
                'Expiring Soon': 'amber', 'Within 90 Days': 'yellow'}.get(s, 'green')


class StockTransaction(models.Model):
    class TxType(models.TextChoices):
        OPENING         = 'opening',          'Opening Balance'
        PURCHASE        = 'purchase',          'Purchase Receipt'
        DISPENSE        = 'dispense',          'Dispensing to Patient'
        ADJUSTMENT_IN   = 'adjustment_in',     'Adjustment (In)'
        ADJUSTMENT_OUT  = 'adjustment_out',    'Adjustment (Out)'
        TRANSFER_IN     = 'transfer_in',       'Transfer In'
        TRANSFER_OUT    = 'transfer_out',      'Transfer Out'
        DAMAGE          = 'damage',            'Damage / Loss'
        EXPIRED_DISPOSAL= 'expired_disposal',  'Expired Disposal'
        RETURN          = 'return_in',         'Return to Stock'

    medication      = models.ForeignKey(Medication, on_delete=models.CASCADE, related_name='transactions')
    batch           = models.ForeignKey(MedicationBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name='transactions')
    transaction_type = models.CharField(max_length=20, choices=TxType.choices)
    quantity_in     = models.PositiveIntegerField(default=0)
    quantity_out    = models.PositiveIntegerField(default=0)
    balance_after   = models.IntegerField(default=0)
    unit_cost       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_value     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reference_number = models.CharField(max_length=100, blank=True)
    notes           = models.TextField(blank=True)
    source_location = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.SET_NULL, related_name='outgoing_transactions')
    dest_location   = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.SET_NULL, related_name='incoming_transactions')
    patient         = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.SET_NULL, related_name='med_transactions')
    performed_by    = models.ForeignKey('auth.User', on_delete=models.PROTECT, related_name='stock_transactions')
    transaction_date = models.DateTimeField(default=None, null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-transaction_date']

    def __str__(self):
        return (f"{self.get_transaction_type_display()} — "
                f"{self.medication.name} — {self.transaction_date:%Y-%m-%d}")


# ── Department Pharmacy / Temporary Stores ───────────────────────────────────

class DepartmentStore(models.Model):
    """A department's temporary medication store (OR, Ward, ER, ICU, etc.)."""

    class StoreType(models.TextChoices):
        OR        = 'or',        'Operating Room (OR)'
        WARD      = 'ward',      'Inpatient Ward'
        ER        = 'er',        'Emergency Department'
        POD       = 'pod',       'Post-Operative Department'
        ICU       = 'icu',       'Intensive Care Unit'
        OPD       = 'opd',       'Outpatient Department'
        LABOUR    = 'labour',    'Labour & Delivery'
        PEDIATRIC = 'pediatric', 'Pediatric Ward'
        NICU      = 'nicu',      'Neonatal ICU'
        SURGICAL  = 'surgical',  'Surgical Ward'
        MEDICAL   = 'medical',   'Medical Ward'
        PSYCHIATRIC = 'psychiatric', 'Psychiatric Ward'
        ONCOLOGY  = 'oncology',  'Oncology Ward'
        OTHER     = 'other',     'Other'

    name         = models.CharField(max_length=200)
    store_type   = models.CharField(max_length=20, choices=StoreType.choices, default=StoreType.OTHER)
    location     = models.CharField(max_length=200, blank=True)
    phone        = models.CharField(max_length=30, blank=True)
    notes        = models.TextField(blank=True)
    is_active    = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['store_type', 'name']

    def __str__(self):
        return self.name

    @property
    def low_stock_items(self):
        return [s for s in self.stock_items.select_related('medication') if s.is_low_stock]

    @property
    def out_of_stock_items(self):
        return [s for s in self.stock_items.select_related('medication') if s.is_out_of_stock]

    @property
    def near_expiry_batches(self):
        from datetime import date, timedelta
        d30 = date.today() + timedelta(days=30)
        return DepartmentStockBatch.objects.filter(
            dept_stock__department_store=self,
            is_active=True, quantity_available__gt=0,
            expiration_date__gt=date.today(),
            expiration_date__lte=d30,
        ).select_related('dept_stock__medication')

    @property
    def expired_batches(self):
        from datetime import date
        return DepartmentStockBatch.objects.filter(
            dept_stock__department_store=self,
            is_active=True, quantity_available__gt=0,
            expiration_date__lt=date.today(),
        ).select_related('dept_stock__medication')

    @property
    def pending_requests(self):
        return self.transfer_requests.filter(status=TransferRequest.Status.PENDING)


class DepartmentStock(models.Model):
    """Current stock level for one medication in one department store."""
    department_store   = models.ForeignKey(DepartmentStore, on_delete=models.CASCADE, related_name='stock_items')
    medication         = models.ForeignKey(Medication, on_delete=models.CASCADE, related_name='dept_stocks')
    quantity_available = models.IntegerField(default=0)
    minimum_quantity   = models.IntegerField(default=10)
    last_updated       = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('department_store', 'medication')

    def __str__(self):
        return f"{self.department_store.name} — {self.medication.name}: {self.quantity_available}"

    @property
    def is_low_stock(self):
        return 0 < self.quantity_available <= self.minimum_quantity

    @property
    def is_out_of_stock(self):
        return self.quantity_available <= 0

    @property
    def stock_status(self):
        if self.is_out_of_stock:
            return 'Out of Stock'
        if self.is_low_stock:
            return 'Low Stock'
        return 'Available'


class DepartmentStockBatch(models.Model):
    """Tracks a specific batch of medication inside a department store."""
    dept_stock         = models.ForeignKey(DepartmentStock, on_delete=models.CASCADE, related_name='batches')
    source_batch       = models.ForeignKey(MedicationBatch, null=True, blank=True, on_delete=models.SET_NULL)
    batch_number       = models.CharField(max_length=100)
    expiration_date    = models.DateField()
    quantity_available = models.IntegerField(default=0)
    received_date      = models.DateField()
    is_active          = models.BooleanField(default=True)

    class Meta:
        ordering = ['expiration_date']

    def __str__(self):
        return f"{self.batch_number} ({self.dept_stock.medication.name})"

    @property
    def days_to_expiry(self):
        from datetime import date
        return (self.expiration_date - date.today()).days

    @property
    def is_expired(self):
        from datetime import date
        return self.expiration_date < date.today()

    @property
    def expiry_status(self):
        d = self.days_to_expiry
        if d < 0:
            return 'Expired'
        if d <= 7:
            return 'Near Expiry'
        if d <= 30:
            return 'Expiring Soon'
        if d <= 90:
            return 'Within 90 Days'
        return 'Good'


class TransferRequest(models.Model):
    """A department's request for medications from the main pharmacy."""

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending Review'
        APPROVED  = 'approved',  'Approved'
        REJECTED  = 'rejected',  'Rejected'
        FULFILLED = 'fulfilled', 'Fulfilled'
        PARTIAL   = 'partial',   'Partially Fulfilled'
        CANCELLED = 'cancelled', 'Cancelled'

    class Priority(models.TextChoices):
        NORMAL    = 'normal',    'Normal'
        URGENT    = 'urgent',    'Urgent'
        EMERGENCY = 'emergency', 'Emergency'

    request_number    = models.CharField(max_length=20, unique=True)
    requesting_store  = models.ForeignKey(DepartmentStore, on_delete=models.CASCADE, related_name='transfer_requests')
    status            = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    priority          = models.CharField(max_length=15, choices=Priority.choices, default=Priority.NORMAL)
    requested_by      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='transfer_requests_made')
    approved_by       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='transfer_requests_approved')
    request_date      = models.DateTimeField(auto_now_add=True)
    required_by       = models.DateField(null=True, blank=True)
    approval_date     = models.DateTimeField(null=True, blank=True)
    fulfillment_date  = models.DateTimeField(null=True, blank=True)
    notes             = models.TextField(blank=True)
    rejection_reason  = models.TextField(blank=True)

    class Meta:
        ordering = ['-request_date']

    def __str__(self):
        return f"{self.request_number} — {self.requesting_store.name}"

    @property
    def priority_color(self):
        return {'normal': 'blue', 'urgent': 'amber', 'emergency': 'red'}.get(self.priority, 'slate')


class TransferRequestItem(models.Model):
    """One medication line in a transfer request."""
    transfer_request   = models.ForeignKey(TransferRequest, on_delete=models.CASCADE, related_name='items')
    medication         = models.ForeignKey(Medication, on_delete=models.CASCADE)
    quantity_requested = models.IntegerField()
    quantity_approved  = models.IntegerField(null=True, blank=True)
    notes              = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.medication.name} × {self.quantity_requested}"


class DepartmentTransfer(models.Model):
    """Actual medication transfer between pharmacy and a department (or dept to dept)."""

    class TransferType(models.TextChoices):
        PHARM_TO_DEPT = 'pharm_to_dept', 'Pharmacy → Department'
        DEPT_TO_PHARM = 'dept_to_pharm', 'Department → Pharmacy'
        DEPT_TO_DEPT  = 'dept_to_dept',  'Department → Department'

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending Receipt'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    transfer_number   = models.CharField(max_length=20, unique=True)
    transfer_type     = models.CharField(max_length=20, choices=TransferType.choices)
    source_store      = models.ForeignKey(DepartmentStore, null=True, blank=True, on_delete=models.SET_NULL, related_name='outgoing_transfers')
    dest_store        = models.ForeignKey(DepartmentStore, null=True, blank=True, on_delete=models.SET_NULL, related_name='incoming_transfers')
    transfer_request  = models.ForeignKey(TransferRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name='transfers')
    status            = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    transfer_date     = models.DateTimeField(null=True, blank=True)
    received_date     = models.DateTimeField(null=True, blank=True)
    prepared_by       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='transfers_prepared')
    received_by       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='transfers_received')
    notes             = models.TextField(blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.transfer_number} — {self.get_transfer_type_display()}"


class DepartmentTransferItem(models.Model):
    """One medication line in a department transfer."""
    transfer             = models.ForeignKey(DepartmentTransfer, on_delete=models.CASCADE, related_name='items')
    medication           = models.ForeignKey(Medication, on_delete=models.CASCADE)
    source_batch         = models.ForeignKey(MedicationBatch, null=True, blank=True, on_delete=models.SET_NULL)
    dept_batch           = models.ForeignKey(DepartmentStockBatch, null=True, blank=True, on_delete=models.SET_NULL)
    batch_number         = models.CharField(max_length=100)
    expiration_date      = models.DateField()
    quantity_transferred = models.IntegerField()
    unit_cost            = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.medication.name} × {self.quantity_transferred}"


class DepartmentUsage(models.Model):
    """Records medication usage / consumption within a department."""

    class UsageType(models.TextChoices):
        ADMINISTRATION = 'administration', 'Patient Administration'
        EMERGENCY      = 'emergency',      'Emergency Use'
        SURGICAL       = 'surgical',       'Surgical Procedure'
        WASTAGE        = 'wastage',        'Wastage / Spoilage'
        EXPIRED        = 'expired',        'Expired Disposal'
        RETURNED       = 'returned',       'Returned to Pharmacy'
        ADJUSTMENT     = 'adjustment',     'Stock Adjustment'

    usage_number      = models.CharField(max_length=20, unique=True)
    department_store  = models.ForeignKey(DepartmentStore, on_delete=models.CASCADE, related_name='usages')
    medication        = models.ForeignKey(Medication, on_delete=models.CASCADE, related_name='dept_usages')
    dept_batch        = models.ForeignKey(DepartmentStockBatch, null=True, blank=True, on_delete=models.SET_NULL)
    quantity_used     = models.IntegerField()
    usage_type        = models.CharField(max_length=20, choices=UsageType.choices)
    patient           = models.ForeignKey('Patient', null=True, blank=True, on_delete=models.SET_NULL)
    visit             = models.ForeignKey('Visit', null=True, blank=True, on_delete=models.SET_NULL)
    responsible_staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='dept_usages_performed')
    usage_date        = models.DateTimeField()
    reason            = models.CharField(max_length=200, blank=True)
    notes             = models.TextField(blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-usage_date']

    def __str__(self):
        return f"{self.usage_number} — {self.medication.name} @ {self.department_store.name}"


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
            ('collect_lab_sample',        'Can record sample collection'),
            ('release_lab_result',        'Can release lab results to patient'),
            ('manage_lab_services',       'Can manage lab service catalogue and pricing'),
            ('lab_reception',             'Can access lab reception / payment verification'),

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
            ('approve_credit_invoice',    'Can approve credit invoices'),
            ('manage_cash_sessions',      'Can open and close cash sessions'),
            ('read_financial_report',     'Can read financial reports'),

            # Pharmacy
            ('read_medication_inventory',        'Can read medication inventory'),
            ('manage_medication',                'Can manage medication inventory'),
            ('dispense_medication',              'Can dispense medications'),
            ('read_prescription_for_dispensing', 'Can read prescriptions for dispensing'),
            ('process_pharmacy_sale',            'Can process pharmacy sales'),
            ('approve_pharmacy_discount',        'Can approve pharmacy discounts'),
            ('approve_credit_sale',              'Can approve credit pharmacy sales'),
            ('manage_pharmacy_returns',          'Can process pharmacy returns/refunds'),
            ('read_pharmacy_reports',            'Can view pharmacy sales reports'),

            # Department Pharmacy / Temporary Stores
            ('view_dept_inventory',        'Can view department store inventory'),
            ('manage_dept_stores',         'Can create and edit department stores'),
            ('request_medication_transfer','Can request medication transfers'),
            ('approve_medication_transfer','Can approve / fulfill medication transfers'),
            ('record_dept_usage',          'Can record medication usage in department'),
            ('view_dept_reports',          'Can view department pharmacy reports'),

            # Medication Inventory (catalog & batches)
            ('manage_med_catalog',    'Can manage medication catalog'),
            ('receive_stock',         'Can receive stock / create batches'),
            ('manage_suppliers',      'Can manage suppliers'),
            ('view_inv_reports',      'Can view inventory reports'),
            ('export_inv_reports',    'Can export inventory reports'),

            # Store / Inventory
            ('read_inventory',            'Can read store inventory'),
            ('manage_inventory',          'Can manage store inventory'),
            ('create_purchase_order',     'Can create purchase orders'),
            ('approve_purchase_order',    'Can approve purchase orders'),
            ('create_purchase_request',   'Can create purchase requests'),
            ('approve_purchase_request',  'Can approve purchase requests'),
            ('issue_inventory',           'Can issue items from store'),
            ('manage_inventory_batches',  'Can receive and manage inventory batches'),
            ('manage_equipment_assets',   'Can manage equipment and asset records'),
            ('perform_stock_count',       'Can perform physical stock count'),
            ('approve_stock_count',       'Can approve stock count results'),
            ('view_store_reports',        'Can view store reports'),
            ('export_store_reports',      'Can export store reports'),

            # HR
            ('read_employee',             'Can read employee records'),
            ('manage_employees',          'Can manage employee records'),
            ('manage_attendance',         'Can manage attendance'),
            ('manage_payroll',            'Can manage payroll'),

            # Appointments
            ('read_appointment',            'Can read appointments'),
            ('manage_appointments',         'Can manage appointments'),
            ('manage_doctor_availability',  'Can manage doctor schedules and availability'),
            ('view_appointment_reports',    'Can view appointment reports'),
            ('export_appointment_reports',  'Can export appointment reports'),

            # Reports
            ('read_clinical_reports',     'Can read clinical reports'),
            ('read_department_reports',   'Can read department reports'),

            # System administration
            ('manage_users',              'Can manage system users'),
            ('manage_roles',              'Can manage roles and permissions'),
            ('system_configuration',      'Can access system configuration'),
            ('read_audit_log',            'Can read audit logs'),
            ('manage_departments',        'Can manage departments'),

            # Patient Flow
            ('view_patient_flow',          'Can view the patient flow dashboard'),
            ('manage_patient_flow',        'Can update patient journey status'),
            ('manage_discharge',           'Can create and manage discharge summaries'),
            ('view_dept_worklist',         'Can view department work list'),

            # Surgery / Procedure Management
            ('manage_procedure_master',    'Can manage procedure/surgery master list'),
            ('order_surgery',              'Can create and manage surgery orders'),
            ('approve_surgery_order',      'Can approve or reject surgery orders'),
            ('manage_or_schedule',         'Can manage OR schedule and room assignments'),
            ('write_surgery_anesthesia',   'Can write surgery anesthesia records'),
            ('read_surgery',               'Can read surgery orders and records'),
            ('write_operative_note',       'Can write operative notes'),
            ('manage_surgery_consumables', 'Can manage surgery consumables and supplies'),
            ('generate_surgery_billing',   'Can generate billing from surgery orders'),
            ('read_surgery_reports',       'Can read surgery reports'),
        ]


class AuditLog(models.Model):
    """Immutable audit trail — every data change, financial action, and access event."""

    class Action(models.TextChoices):
        CREATE          = 'create',          'Create'
        UPDATE          = 'update',          'Update'
        DELETE          = 'delete',          'Delete'
        VIEW            = 'view',            'View'
        LOGIN           = 'login',           'Login'
        LOGIN_FAILED    = 'login_failed',    'Login Failed'
        LOGOUT          = 'logout',          'Logout'
        APPROVE         = 'approve',         'Approve'
        REJECT          = 'reject',          'Reject'
        ISSUE           = 'issue',           'Issue'
        RECEIVE         = 'receive',         'Receive'
        TRANSFER        = 'transfer',        'Transfer'
        ADJUST          = 'adjust',          'Adjust'
        DISPOSE         = 'dispose',         'Dispose'
        PAYMENT         = 'payment',         'Payment'
        REFUND          = 'refund',          'Refund'
        CANCEL          = 'cancel',          'Cancel'
        WAIVE           = 'waive',           'Waive'
        DISPENSE        = 'dispense',        'Dispense'
        RETURN          = 'return',          'Return'
        ACCESS          = 'access',          'Record Access'
        PASSWORD_CHANGE = 'password_change', 'Password Change'
        ACTIVATE        = 'activate',        'Activate'
        DEACTIVATE      = 'deactivate',      'Deactivate'
        OPEN_SESSION    = 'open_session',    'Open Session'
        CLOSE_SESSION   = 'close_session',   'Close Session'

    class Module(models.TextChoices):
        PATIENT       = 'patient',       'Patient'
        APPOINTMENT   = 'appointment',   'Appointment'
        VISIT         = 'visit',         'Visit'
        DOCTOR        = 'doctor',        'Doctor / Clinical'
        NURSING       = 'nursing',       'Nursing'
        LABORATORY    = 'laboratory',    'Laboratory'
        RADIOLOGY     = 'radiology',     'Radiology'
        PHARMACY      = 'pharmacy',      'Pharmacy'
        MED_INVENTORY = 'med_inventory', 'Medication Inventory'
        DEPT_PHARMACY = 'dept_pharmacy', 'Dept Pharmacy'
        INVENTORY     = 'inventory',     'Store & Inventory'
        BILLING       = 'billing',       'Billing'
        PAYMENT       = 'payment',       'Payment'
        FINANCE       = 'finance',       'Finance'
        HR            = 'hr',            'Human Resources'
        ADMIN         = 'admin',         'Administration'
        AUTH          = 'auth',          'Authentication'
        USER          = 'user',          'User Management'
        ANESTHESIA    = 'anesthesia',    'Anesthesia'
        QUEUE         = 'queue',         'Queue / Triage'
        SURGERY       = 'surgery',       'Surgery'

    class Severity(models.TextChoices):
        INFO     = 'info',     'Info'
        WARNING  = 'warning',  'Warning'
        CRITICAL = 'critical', 'Critical'

    # Who
    user            = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='audit_logs'
    )
    user_name       = models.CharField(max_length=150)
    user_role       = models.CharField(max_length=100, blank=True)
    department      = models.CharField(max_length=100, blank=True)

    # What
    action          = models.CharField(max_length=30, choices=Action.choices, db_index=True)
    module          = models.CharField(max_length=30, choices=Module.choices, db_index=True)
    object_type     = models.CharField(max_length=100, blank=True)
    object_id       = models.CharField(max_length=50, blank=True)
    object_repr     = models.CharField(max_length=500, blank=True)
    changes         = models.JSONField(null=True, blank=True)
    extra_data      = models.JSONField(null=True, blank=True)
    description     = models.CharField(max_length=500, blank=True)

    # When / Where
    timestamp       = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address      = models.GenericIPAddressField(null=True, blank=True)
    user_agent      = models.CharField(max_length=500, blank=True)
    severity        = models.CharField(max_length=10, choices=Severity.choices, default=Severity.INFO, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        default_permissions = ('add', 'view')
        indexes = [
            models.Index(fields=['module', 'timestamp']),
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['action', 'module']),
            models.Index(fields=['severity', 'timestamp']),
        ]
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.user_name} — {self.action} {self.module}"


# ── Patient Flow / Journey Module ─────────────────────────────────────────────

class VisitJourneyEvent(models.Model):
    """Immutable chronological event log for a single visit's patient journey."""

    class EventType(models.TextChoices):
        STATUS_CHANGE   = 'status_change',   'Status Change'
        PAYMENT         = 'payment',         'Payment Event'
        CLINICAL        = 'clinical',        'Clinical Event'
        ORDER           = 'order',           'Order Placed'
        RESULT          = 'result',          'Result Received'
        DISCHARGE       = 'discharge',       'Discharge Event'
        ADMIN           = 'admin',           'Administrative'
        SYSTEM          = 'system',          'System Event'

    visit           = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name='journey_events')
    event_type      = models.CharField(max_length=20, choices=EventType.choices, default=EventType.SYSTEM)
    status_before   = models.CharField(max_length=30, blank=True)
    status_after    = models.CharField(max_length=30, blank=True)
    title           = models.CharField(max_length=200)
    description     = models.TextField(blank=True)
    performed_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='journey_events'
    )
    department      = models.CharField(max_length=100, blank=True)
    timestamp       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']
        verbose_name = 'Journey Event'
        verbose_name_plural = 'Journey Events'

    def __str__(self):
        return f"[{self.timestamp:%H:%M}] {self.visit_id} — {self.title}"


class DischargeSummary(models.Model):
    class DischargeType(models.TextChoices):
        REGULAR        = 'regular',        'Regular Discharge'
        AMA            = 'ama',            'Against Medical Advice'
        TRANSFER       = 'transfer',       'Transfer to Another Facility'
        DEATH          = 'death',          'Death'
        FOLLOW_UP      = 'follow_up',      'Discharged with Follow-up'

    class DischargeCondition(models.TextChoices):
        IMPROVED       = 'improved',       'Improved'
        STABLE         = 'stable',         'Stable'
        UNCHANGED      = 'unchanged',      'Unchanged'
        DETERIORATED   = 'deteriorated',   'Deteriorated'
        DECEASED       = 'deceased',       'Deceased'

    visit               = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name='discharge_summary')
    discharge_type      = models.CharField(max_length=20, choices=DischargeType.choices, default=DischargeType.REGULAR)
    discharge_condition = models.CharField(max_length=20, choices=DischargeCondition.choices, default=DischargeCondition.IMPROVED)

    # Diagnoses
    final_diagnosis     = models.TextField()
    secondary_diagnoses = models.TextField(blank=True)
    procedures_performed= models.TextField(blank=True)

    # Hospital course
    hospital_course     = models.TextField(blank=True, help_text='Brief summary of treatment during visit')
    treatment_summary   = models.TextField(blank=True)
    investigations_summary = models.TextField(blank=True)

    # Discharge instructions
    discharge_instructions = models.TextField(blank=True)
    diet_instructions   = models.TextField(blank=True)
    activity_restrictions = models.TextField(blank=True)
    wound_care          = models.TextField(blank=True)

    # Follow-up
    follow_up_date      = models.DateField(null=True, blank=True)
    follow_up_doctor    = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='discharge_follow_ups'
    )
    follow_up_instructions = models.TextField(blank=True)

    # Medications at discharge
    discharge_medications = models.TextField(blank=True, help_text='List of medications prescribed at discharge')

    # Transfer info (if transfer)
    transfer_facility   = models.CharField(max_length=200, blank=True)
    transfer_reason     = models.TextField(blank=True)

    authored_by         = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='discharge_summaries'
    )
    authored_at         = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Discharge Summary'
        verbose_name_plural = 'Discharge Summaries'

    def __str__(self):
        return f"Discharge — {self.visit.patient} ({self.authored_at:%Y-%m-%d})"


# ── Surgery / Procedure Module ────────────────────────────────────────────────

class ProcedureCategory(models.Model):
    name        = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active   = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Procedure Category'

    def __str__(self):
        return self.name


class ProcedureMaster(models.Model):
    class Complexity(models.TextChoices):
        MINOR    = 'minor',    'Minor'
        MODERATE = 'moderate', 'Moderate'
        MAJOR    = 'major',    'Major'
        CRITICAL = 'critical', 'Critical'

    class AnesthesiaType(models.TextChoices):
        GENERAL  = 'General',  'General Anesthesia'
        REGIONAL = 'Regional', 'Regional Anesthesia'
        LOCAL    = 'Local',    'Local Anesthesia'
        SPINAL   = 'Spinal',   'Spinal Block'
        EPIDURAL = 'Epidural', 'Epidural Block'
        SEDATION = 'Sedation', 'Conscious Sedation'
        NONE     = 'None',     'No Anesthesia'

    name                       = models.CharField(max_length=200)
    code                       = models.CharField(max_length=20, unique=True)
    category                   = models.ForeignKey(
        ProcedureCategory, on_delete=models.PROTECT, related_name='procedures', null=True, blank=True,
    )
    department                 = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='procedure_masters',
    )
    description                = models.TextField(blank=True)
    required_specialty         = models.CharField(max_length=100, blank=True)
    estimated_duration_minutes = models.PositiveIntegerField(default=60)
    complexity                 = models.CharField(
        max_length=10, choices=Complexity.choices, default=Complexity.MODERATE,
    )

    # Clinical information
    indications             = models.TextField(blank=True)
    contraindications       = models.TextField(blank=True)
    required_investigations = models.TextField(blank=True)
    required_preparation    = models.TextField(blank=True)
    required_equipment      = models.TextField(blank=True)
    required_medications    = models.TextField(blank=True)
    required_consumables    = models.TextField(blank=True)

    # Resource requirements
    or_type_required         = models.CharField(max_length=100, blank=True)
    required_instruments     = models.TextField(blank=True)
    required_implants        = models.TextField(blank=True)
    required_anesthesia_type = models.CharField(
        max_length=20, choices=AnesthesiaType.choices, default=AnesthesiaType.GENERAL,
    )

    # Financial
    procedure_price    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    surgeon_fee        = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    anesthesia_fee     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    facility_fee       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    consumable_charges = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    insurance_price    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    billing_category   = models.CharField(max_length=100, blank=True)

    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Procedure Master'

    def __str__(self):
        return f'{self.code} — {self.name}'

    @property
    def total_fee(self):
        return (
            self.procedure_price + self.surgeon_fee + self.anesthesia_fee
            + self.facility_fee + self.consumable_charges
        )


class ORRoom(models.Model):
    class RoomType(models.TextChoices):
        GENERAL    = 'general',    'General Surgery'
        CARDIAC    = 'cardiac',    'Cardiac / Thoracic'
        ORTHOPEDIC = 'orthopedic', 'Orthopedic'
        NEURO      = 'neuro',      'Neurosurgery'
        EYE        = 'eye',        'Ophthalmic'
        EMERGENCY  = 'emergency',  'Emergency OR'
        PROCEDURE  = 'procedure',  'Procedure Room'
        OTHER      = 'other',      'Other'

    name      = models.CharField(max_length=50, unique=True)
    room_type = models.CharField(
        max_length=15, choices=RoomType.choices, default=RoomType.GENERAL,
    )
    location  = models.CharField(max_length=100, blank=True)
    capacity  = models.PositiveSmallIntegerField(default=1)
    equipment = models.TextField(blank=True)
    notes     = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'OR Room'

    def __str__(self):
        return self.name


class SurgeryOrder(models.Model):
    class Status(models.TextChoices):
        ORDERED          = 'ordered',          'Ordered'
        PENDING_REVIEW   = 'pending_review',   'Pending Review'
        APPROVED         = 'approved',         'Approved'
        SCHEDULED        = 'scheduled',        'Scheduled'
        PATIENT_PREPARED = 'patient_prepared', 'Patient Prepared'
        IN_OR            = 'in_or',            'In Operating Room'
        COMPLETED        = 'completed',        'Completed'
        POST_OP          = 'post_op',          'Post-operative Care'
        CANCELLED        = 'cancelled',        'Cancelled'

    class Priority(models.TextChoices):
        EMERGENCY = 'emergency', 'Emergency'
        URGENT    = 'urgent',    'Urgent'
        ELECTIVE  = 'elective',  'Elective'

    class AnesthesiaType(models.TextChoices):
        GENERAL  = 'General',  'General Anesthesia'
        REGIONAL = 'Regional', 'Regional Anesthesia'
        LOCAL    = 'Local',    'Local Anesthesia'
        SPINAL   = 'Spinal',   'Spinal Block'
        EPIDURAL = 'Epidural', 'Epidural Block'
        SEDATION = 'Sedation', 'Conscious Sedation'

    order_number     = models.CharField(max_length=25, unique=True, blank=True)
    patient          = models.ForeignKey(
        Patient, on_delete=models.PROTECT, related_name='surgery_orders',
    )
    visit            = models.ForeignKey(
        Visit, on_delete=models.SET_NULL, null=True, blank=True, related_name='surgery_orders',
    )
    procedure_master = models.ForeignKey(
        ProcedureMaster, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_orders',
    )

    # Order details
    pre_op_diagnosis   = models.CharField(max_length=500)
    planned_procedure  = models.CharField(max_length=300)
    indication         = models.TextField(blank=True)
    priority           = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.ELECTIVE,
    )
    planned_date       = models.DateField(null=True, blank=True)
    preferred_time     = models.TimeField(null=True, blank=True)

    # Surgical team
    surgeon            = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='surgery_orders_as_surgeon',
    )
    assistant_surgeon  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_orders_as_assistant',
    )
    department         = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_orders',
    )

    # Clinical
    special_instructions = models.TextField(blank=True)
    previous_history     = models.TextField(blank=True)
    required_preparation = models.TextField(blank=True)

    # Anesthesia request
    anesthesia_type                 = models.CharField(
        max_length=20, choices=AnesthesiaType.choices, default=AnesthesiaType.GENERAL,
    )
    anesthesia_assessment_requested = models.BooleanField(default=False)

    # Status tracking
    status       = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ORDERED, db_index=True,
    )
    ordered_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='surgery_orders_created',
    )
    ordered_at   = models.DateTimeField(auto_now_add=True)
    approved_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_orders_approved',
    )
    approved_at  = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_orders_cancelled',
    )
    cancelled_at        = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    completed_at        = models.DateTimeField(null=True, blank=True)

    # Billing
    invoice = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_orders',
    )

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-ordered_at']
        verbose_name = 'Surgery Order'

    def __str__(self):
        return f'{self.order_number} — {self.planned_procedure} ({self.patient})'

    def save(self, *args, **kwargs):
        if not self.order_number:
            from django.utils import timezone as tz
            # Temporary save to get PK, then set order_number
            super().save(*args, **kwargs)
            self.order_number = f'SRG-{tz.localdate().strftime("%y%m")}-{self.pk:04d}'
            SurgeryOrder.objects.filter(pk=self.pk).update(order_number=self.order_number)
            return
        super().save(*args, **kwargs)

    @property
    def can_approve(self):
        return self.status in (self.Status.ORDERED, self.Status.PENDING_REVIEW)

    @property
    def can_schedule(self):
        return self.status == self.Status.APPROVED

    @property
    def can_cancel(self):
        return self.status not in (self.Status.COMPLETED, self.Status.CANCELLED)

    @property
    def is_active(self):
        return self.status not in (self.Status.COMPLETED, self.Status.CANCELLED)


class SurgerySchedule(models.Model):
    surgery_order         = models.OneToOneField(
        SurgeryOrder, on_delete=models.CASCADE, related_name='schedule',
    )
    or_room               = models.ForeignKey(
        ORRoom, on_delete=models.PROTECT, related_name='schedules',
    )
    scheduled_date        = models.DateField()
    scheduled_start_time  = models.TimeField()
    estimated_end_time    = models.TimeField(null=True, blank=True)
    actual_start_time     = models.DateTimeField(null=True, blank=True)
    actual_end_time       = models.DateTimeField(null=True, blank=True)

    # OR team
    scrub_nurse           = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='scrub_nurse_schedules',
    )
    circulating_nurse     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='circulating_nurse_schedules',
    )
    anesthesiologist      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='anesthesia_schedules',
    )

    # Checklists (JSON lists of {item, checked})
    equipment_checklist    = models.JSONField(default=list)
    instrument_checklist   = models.JSONField(default=list)
    patient_prep_checklist = models.JSONField(default=list)

    notes        = models.TextField(blank=True)
    scheduled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='surgery_schedules_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['scheduled_date', 'scheduled_start_time']
        verbose_name = 'Surgery Schedule'

    def __str__(self):
        return (
            f'OR Schedule: {self.surgery_order.order_number} '
            f'— {self.scheduled_date} {self.scheduled_start_time}'
        )


class SurgeryAnesthesiaRecord(models.Model):
    class ASA(models.TextChoices):
        I   = 'ASA I',   'ASA I — Normal healthy patient'
        II  = 'ASA II',  'ASA II — Mild systemic disease'
        III = 'ASA III', 'ASA III — Severe systemic disease'
        IV  = 'ASA IV',  'ASA IV — Severe disease, constant threat to life'
        V   = 'ASA V',   'ASA V — Moribund patient'

    surgery_order    = models.OneToOneField(
        SurgeryOrder, on_delete=models.CASCADE, related_name='surgery_anesthesia_record',
    )
    anesthesiologist = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='surgery_anesthesia_records',
    )

    # Pre-assessment
    asa_classification   = models.CharField(max_length=10, choices=ASA.choices, blank=True)
    pre_assessment_notes = models.TextField(blank=True)
    known_allergies      = models.TextField(blank=True)
    pre_medication       = models.TextField(blank=True)

    # Plan
    anesthesia_technique = models.CharField(max_length=100, blank=True)
    induction_agent      = models.CharField(max_length=100, blank=True)
    maintenance_agent    = models.CharField(max_length=100, blank=True)
    airway_management    = models.CharField(max_length=100, blank=True)

    # Intraoperative
    induction_time        = models.DateTimeField(null=True, blank=True)
    incision_time         = models.DateTimeField(null=True, blank=True)
    closure_time          = models.DateTimeField(null=True, blank=True)
    anesthesia_medications = models.JSONField(default=list)
    monitoring_notes      = models.TextField(blank=True)
    intraop_complications = models.TextField(blank=True)

    # Post-anesthesia
    extubation_time       = models.DateTimeField(null=True, blank=True)
    post_anesthesia_notes = models.TextField(blank=True)
    pacu_duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    pacu_complications    = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Surgery Anesthesia Record'

    def __str__(self):
        return f'Anesthesia: {self.surgery_order.order_number}'


class OperativeNote(models.Model):
    surgery_order = models.OneToOneField(
        SurgeryOrder, on_delete=models.CASCADE, related_name='operative_note',
    )
    procedure_performed = models.CharField(max_length=500)
    start_time          = models.DateTimeField(null=True, blank=True)
    end_time            = models.DateTimeField(null=True, blank=True)

    pre_op_diagnosis   = models.TextField()
    post_op_diagnosis  = models.TextField()
    anesthesia_type    = models.CharField(max_length=50, blank=True)
    assistant_surgeons = models.CharField(max_length=300, blank=True)

    findings           = models.TextField()
    procedure_steps    = models.TextField()
    complications      = models.TextField(blank=True)

    blood_loss_ml      = models.PositiveIntegerField(null=True, blank=True)
    urine_output_ml    = models.PositiveIntegerField(null=True, blank=True)
    specimens_collected = models.TextField(blank=True)
    implants_used       = models.TextField(blank=True)
    drains_placed       = models.TextField(blank=True)
    wound_closure       = models.CharField(max_length=200, blank=True)

    post_op_instructions = models.TextField(blank=True)

    authored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='operative_notes',
    )
    authored_at = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-authored_at']
        verbose_name = 'Operative Note'

    def __str__(self):
        return f'Op Note: {self.surgery_order.order_number}'


class SurgeryConsumable(models.Model):
    class ItemType(models.TextChoices):
        MEDICATION  = 'medication',  'Medication'
        SUPPLY      = 'supply',      'Surgical Supply'
        IMPLANT     = 'implant',     'Implant / Prosthetic'
        INSTRUMENT  = 'instrument',  'Instrument'
        DISPOSABLE  = 'disposable',  'Disposable Item'
        OTHER       = 'other',       'Other'

    surgery_order = models.ForeignKey(
        SurgeryOrder, on_delete=models.CASCADE, related_name='consumables',
    )
    item_type    = models.CharField(
        max_length=15, choices=ItemType.choices, default=ItemType.SUPPLY,
    )
    item_name    = models.CharField(max_length=200)
    quantity     = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit         = models.CharField(max_length=30, default='unit')
    batch_number = models.CharField(max_length=50, blank=True)
    expiry_date  = models.DateField(null=True, blank=True)
    unit_cost    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_cost   = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    recorded_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='surgery_consumables',
    )
    recorded_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['item_type', 'item_name']
        verbose_name = 'Surgery Consumable'

    def __str__(self):
        return f'{self.item_name} × {self.quantity} ({self.surgery_order.order_number})'

    def save(self, *args, **kwargs):
        self.total_cost = Decimal(str(self.quantity)) * Decimal(str(self.unit_cost))
        super().save(*args, **kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# PRESCRIPTION WORKFLOW
# ══════════════════════════════════════════════════════════════════════════════

class Prescription(models.Model):
    class Status(models.TextChoices):
        CREATED    = 'Created',              'Created'
        SENT       = 'Sent to Pharmacy',     'Sent to Pharmacy'
        REVIEWING  = 'Under Review',         'Under Review'
        READY      = 'Ready to Dispense',    'Ready to Dispense'
        DISPENSED  = 'Dispensed',            'Dispensed'
        PARTIAL    = 'Partially Dispensed',  'Partially Dispensed'
        CANCELLED  = 'Cancelled',            'Cancelled'

    prescription_number = models.CharField(max_length=20, unique=True, blank=True)
    visit               = models.ForeignKey(
        Visit, on_delete=models.PROTECT, related_name='prescriptions',
    )
    patient             = models.ForeignKey(
        Patient, on_delete=models.PROTECT, related_name='prescriptions',
    )
    prescribed_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='prescriptions_written',
    )
    diagnosis           = models.TextField(blank=True)
    status              = models.CharField(
        max_length=25, choices=Status.choices, default=Status.CREATED,
    )
    notes               = models.TextField(blank=True)
    sent_to_pharmacy_at = models.DateTimeField(null=True, blank=True)
    verified_by         = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='prescriptions_verified',
    )
    verified_at         = models.DateTimeField(null=True, blank=True)
    rejection_reason    = models.TextField(blank=True)
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Prescription'
        verbose_name_plural = 'Prescriptions'

    def __str__(self):
        return f"{self.prescription_number} — {self.patient.full_name}"

    def save(self, *args, **kwargs):
        if not self.prescription_number:
            last = Prescription.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.prescription_number = f"RX-{next_id:06d}"
        super().save(*args, **kwargs)

    @property
    def is_editable(self):
        return self.status == self.Status.CREATED

    @property
    def total_items(self):
        return self.items.count()

    @property
    def pending_items(self):
        return self.items.filter(status=PrescriptionItem.Status.PENDING).count()


class PrescriptionItem(models.Model):
    class Route(models.TextChoices):
        ORAL       = 'Oral',           'Oral'
        IV         = 'Intravenous',    'Intravenous (IV)'
        IM         = 'Intramuscular',  'Intramuscular (IM)'
        SC         = 'Subcutaneous',   'Subcutaneous (SC)'
        TOPICAL    = 'Topical',        'Topical'
        INHALATION = 'Inhalation',     'Inhalation'
        SUBLINGUAL = 'Sublingual',     'Sublingual'
        RECTAL     = 'Rectal',         'Rectal'
        OTHER      = 'Other',          'Other'

    class Frequency(models.TextChoices):
        ONCE_DAILY   = 'Once daily',         'Once daily (OD)'
        TWICE_DAILY  = 'Twice daily',        'Twice daily (BD)'
        THREE_DAILY  = 'Three times daily',  'Three times daily (TDS)'
        FOUR_DAILY   = 'Four times daily',   'Four times daily (QID)'
        EVERY_8H     = 'Every 8 hours',      'Every 8 hours'
        EVERY_12H    = 'Every 12 hours',     'Every 12 hours'
        PRN          = 'As needed',          'As needed (PRN)'
        STAT         = 'Immediately',        'Immediately (STAT)'
        AT_BEDTIME   = 'At bedtime',         'At bedtime (nocte)'

    class Instructions(models.TextChoices):
        BEFORE_MEALS  = 'Before meals',   'Before meals'
        AFTER_MEALS   = 'After meals',    'After meals'
        WITH_FOOD     = 'With food',      'With food'
        EMPTY_STOMACH = 'Empty stomach',  'On empty stomach'
        WITH_WATER    = 'With plenty of water', 'With plenty of water'
        OTHER         = 'See notes',      'See notes'

    class Status(models.TextChoices):
        PENDING      = 'Pending',         'Pending'
        BILLED       = 'Billed',          'Sent to Billing'
        DISPENSED    = 'Dispensed',       'Dispensed'
        PARTIAL      = 'Partial',         'Partially Dispensed'
        CANCELLED    = 'Cancelled',       'Cancelled'

    prescription        = models.ForeignKey(
        Prescription, on_delete=models.CASCADE, related_name='items',
    )
    medication          = models.ForeignKey(
        Medication, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='prescription_items',
    )
    drug_name           = models.CharField(max_length=200)
    dose                = models.CharField(max_length=100)
    route               = models.CharField(
        max_length=20, choices=Route.choices, default=Route.ORAL,
    )
    frequency           = models.CharField(
        max_length=30, choices=Frequency.choices, default=Frequency.TWICE_DAILY,
    )
    duration_days       = models.PositiveIntegerField(default=7)
    quantity            = models.PositiveIntegerField(default=1)
    meal_instruction    = models.CharField(
        max_length=30, choices=Instructions.choices, blank=True,
    )
    special_instructions = models.TextField(blank=True)
    is_urgent           = models.BooleanField(default=False)
    status              = models.CharField(
        max_length=25, choices=Status.choices, default=Status.PENDING,
    )
    quantity_dispensed  = models.PositiveIntegerField(default=0)
    order_index         = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order_index', 'pk']
        verbose_name = 'Prescription Item'

    def __str__(self):
        return f"{self.drug_name} {self.dose} — {self.frequency}"

    @property
    def remaining_quantity(self):
        return max(0, self.quantity - self.quantity_dispensed)

    @property
    def display_sig(self):
        """Short sig string, e.g. '1 tab oral, twice daily, 7 days'."""
        parts = [self.dose, self.get_route_display(), self.get_frequency_display()]
        if self.duration_days:
            parts.append(f"{self.duration_days} day{'s' if self.duration_days != 1 else ''}")
        return ', '.join(parts)


class RxDispenseRecord(models.Model):
    prescription_item = models.ForeignKey(
        PrescriptionItem, on_delete=models.PROTECT, related_name='dispense_records',
    )
    medication_batch  = models.ForeignKey(
        MedicationBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rx_dispense_records',
    )
    pharmacy_stock    = models.ForeignKey(
        PharmacyStock, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rx_dispense_records',
    )
    quantity_dispensed = models.PositiveIntegerField()
    unit_price         = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    dispensed_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='rx_dispense_records',
    )
    batch_number       = models.CharField(max_length=100, blank=True)
    expiry_date        = models.DateField(null=True, blank=True)
    notes              = models.TextField(blank=True)
    dispensed_at       = models.DateTimeField(auto_now_add=True)
    stock_confirmed    = models.BooleanField(
        default=True,
        help_text='True once inventory has been physically deducted after payment.',
    )
    confirmed_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='rx_dispense_records_confirmed',
    )
    confirmed_at       = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-dispensed_at']
        verbose_name = 'Dispense Record'

    def __str__(self):
        return (f"{self.prescription_item.drug_name} × {self.quantity_dispensed} "
                f"— {self.dispensed_at:%d %b %Y}")

    def save(self, *args, **kwargs):
        self.total_amount = Decimal(str(self.quantity_dispensed)) * Decimal(str(self.unit_price))
        super().save(*args, **kwargs)


class MAREntry(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = 'Scheduled', 'Scheduled'
        GIVEN     = 'Given',     'Given'
        MISSED    = 'Missed',    'Missed'
        HELD      = 'Held',      'Held'
        REFUSED   = 'Refused',   'Refused'

    prescription_item = models.ForeignKey(
        PrescriptionItem, on_delete=models.CASCADE, related_name='mar_entries',
    )
    visit             = models.ForeignKey(
        Visit, on_delete=models.CASCADE, related_name='mar_entries',
    )
    scheduled_time    = models.DateTimeField()
    administered_at   = models.DateTimeField(null=True, blank=True)
    administered_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='mar_entries',
    )
    dose_given        = models.CharField(max_length=100, blank=True)
    status            = models.CharField(
        max_length=15, choices=Status.choices, default=Status.SCHEDULED,
    )
    patient_response  = models.TextField(blank=True)
    reason_missed     = models.TextField(blank=True)
    notes             = models.TextField(blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scheduled_time']
        verbose_name = 'MAR Entry'
        verbose_name_plural = 'MAR Entries'

    def __str__(self):
        return (f"{self.prescription_item.drug_name} "
                f"@ {self.scheduled_time:%d %b %H:%M} — {self.status}")


# â”€â”€ Pharmacy POS / Sales â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class PharmacySale(models.Model):
    class SaleType(models.TextChoices):
        PRESCRIPTION = 'Prescription', 'Prescription Sale'
        WALK_IN      = 'Walk-in',      'Walk-in / OTC Sale'

    class Status(models.TextChoices):
        PENDING    = 'Pending',    'Pending Payment'
        DISPENSED  = 'Dispensed',  'Dispensed'
        CREDIT     = 'Credit',     'Dispensed on Credit'
        CANCELLED  = 'Cancelled',  'Cancelled'

    class PaymentMethod(models.TextChoices):
        CASH       = 'Cash',      'Cash'
        CREDIT     = 'Credit',    'Credit (Deferred)'
        CARD       = 'Card',      'Bank Card'
        TRANSFER   = 'Transfer',  'Bank Transfer'
        MOBILE     = 'Mobile',    'Mobile Payment'
        INSURANCE  = 'Insurance', 'Insurance'

    sale_number      = models.CharField(max_length=20, unique=True, blank=True)
    sale_type        = models.CharField(max_length=15, choices=SaleType.choices, default=SaleType.WALK_IN)
    patient          = models.ForeignKey(
        Patient, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pharmacy_sales',
    )
    prescription     = models.ForeignKey(
        'Prescription', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pharmacy_sales',
    )
    customer_name    = models.CharField(max_length=200, blank=True)
    customer_phone   = models.CharField(max_length=20, blank=True)
    status           = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    payment_method   = models.CharField(max_length=15, choices=PaymentMethod.choices, blank=True)
    subtotal         = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_received    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    change_given     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cashier          = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='pharmacy_sales_as_cashier',
    )
    dispensed_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pharmacy_sales_dispensed',
    )
    dispensed_at     = models.DateTimeField(null=True, blank=True)
    invoice          = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pharmacy_sales',
    )
    credit_due_date  = models.DateField(null=True, blank=True)
    notes            = models.TextField(blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Pharmacy Sale'

    def __str__(self):
        return f'{self.sale_number} â€” {self.customer_display}'

    @property
    def customer_display(self):
        if self.patient:
            return self.patient.full_name
        return self.customer_name or 'Walk-in Customer'

    def save(self, *args, **kwargs):
        if not self.sale_number:
            last = PharmacySale.objects.order_by('-pk').first()
            nxt = (last.pk + 1) if last else 1
            self.sale_number = f'PHS-{nxt:06d}'
        super().save(*args, **kwargs)


class PharmacySaleItem(models.Model):
    sale             = models.ForeignKey(PharmacySale, on_delete=models.CASCADE, related_name='items')
    medication       = models.ForeignKey(
        Medication, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pharmacy_sale_items',
    )
    medication_batch = models.ForeignKey(
        MedicationBatch, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pharmacy_sale_items',
    )
    pharmacy_stock   = models.ForeignKey(
        PharmacyStock, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pharmacy_sale_items',
    )
    drug_name        = models.CharField(max_length=200)
    quantity         = models.PositiveIntegerField()
    unit_price       = models.DecimalField(max_digits=10, decimal_places=2)
    discount         = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total            = models.DecimalField(max_digits=12, decimal_places=2)
    batch_number     = models.CharField(max_length=100, blank=True)
    expiry_date      = models.DateField(null=True, blank=True)
    dispensed        = models.BooleanField(default=False)
    dispensed_at     = models.DateTimeField(null=True, blank=True)
    dispensed_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pharmacy_sale_items_dispensed',
    )

    class Meta:
        ordering = ['pk']
        verbose_name = 'Pharmacy Sale Item'

    def __str__(self):
        return f'{self.drug_name} x {self.quantity}'

    def save(self, *args, **kwargs):
        self.total = (
            Decimal(str(self.quantity)) * Decimal(str(self.unit_price))
        ) - Decimal(str(self.discount or 0))
        super().save(*args, **kwargs)


class PharmacyReturn(models.Model):
    class ReturnType(models.TextChoices):
        WRONG_ITEM         = 'Wrong Item',         'Wrong Item Issued'
        DAMAGED            = 'Damaged',            'Damaged Product'
        BILLING_CORRECTION = 'Billing Correction', 'Billing Correction'
        OTHER              = 'Other',              'Other'

    class Status(models.TextChoices):
        PENDING  = 'Pending',  'Pending Approval'
        APPROVED = 'Approved', 'Approved'
        REJECTED = 'Rejected', 'Rejected'

    return_number  = models.CharField(max_length=20, unique=True, blank=True)
    original_sale  = models.ForeignKey(
        PharmacySale, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='returns',
    )
    patient        = models.ForeignKey(
        Patient, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pharmacy_returns',
    )
    customer_name  = models.CharField(max_length=200, blank=True)
    return_type    = models.CharField(max_length=25, choices=ReturnType.choices)
    status         = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    return_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    requested_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='pharmacy_returns_requested',
    )
    approved_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pharmacy_returns_approved',
    )
    reason         = models.TextField()
    notes          = models.TextField(blank=True)
    refund_method  = models.CharField(max_length=15, blank=True)
    restock        = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Pharmacy Return'

    def __str__(self):
        return self.return_number

    def save(self, *args, **kwargs):
        if not self.return_number:
            last = PharmacyReturn.objects.order_by('-pk').first()
            nxt = (last.pk + 1) if last else 1
            self.return_number = f'PHR-{nxt:06d}'
        super().save(*args, **kwargs)


class PharmacyReturnItem(models.Model):
    pharmacy_return = models.ForeignKey(PharmacyReturn, on_delete=models.CASCADE, related_name='items')
    original_item   = models.ForeignKey(
        PharmacySaleItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='return_items',
    )
    drug_name       = models.CharField(max_length=200)
    quantity        = models.PositiveIntegerField()
    unit_price      = models.DecimalField(max_digits=10, decimal_places=2)
    total           = models.DecimalField(max_digits=12, decimal_places=2)
    reason          = models.CharField(max_length=200, blank=True)
    restocked       = models.BooleanField(default=False)

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f'{self.drug_name} x {self.quantity}'


# ─────────────────────────────────────────────────────────────────────────────
# APPOINTMENT SCHEDULING — CONSULTATION MODULE
# ─────────────────────────────────────────────────────────────────────────────

class DoctorScheduleException(models.Model):
    class Reason(models.TextChoices):
        LEAVE      = 'Leave',      'Annual / Sick Leave'
        HOLIDAY    = 'Holiday',    'Public Holiday'
        CONFERENCE = 'Conference', 'Conference / Training'
        MODIFIED   = 'Modified',   'Modified Hours'
        OTHER      = 'Other',      'Other'

    doctor      = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name='schedule_exceptions')
    date        = models.DateField()
    is_available = models.BooleanField(default=False)
    start_time  = models.TimeField(null=True, blank=True)
    end_time    = models.TimeField(null=True, blank=True)
    reason      = models.CharField(max_length=20, choices=Reason.choices, default=Reason.LEAVE)
    notes       = models.TextField(blank=True)
    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_exceptions_created',
    )
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']
        unique_together = [['doctor', 'date']]
        verbose_name = 'Doctor Schedule Exception'

    def __str__(self):
        return f"Dr. {self.doctor.full_name} — {self.date} ({self.reason})"


class AppointmentStatusLog(models.Model):
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name='status_logs')
    from_status = models.CharField(max_length=20, blank=True)
    to_status   = models.CharField(max_length=20)
    changed_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='appointment_status_changes',
    )
    changed_at  = models.DateTimeField(auto_now_add=True)
    notes       = models.TextField(blank=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.appointment.appointment_number}: {self.from_status} → {self.to_status}"


# ─────────────────────────────────────────────────────────────────────────────
# OR SCHEDULING MODULE
# ─────────────────────────────────────────────────────────────────────────────

class OperatingRoom(models.Model):
    class RoomType(models.TextChoices):
        GENERAL      = 'General',      'General Surgery'
        CARDIAC      = 'Cardiac',      'Cardiac / Cardiothoracic'
        ORTHOPEDIC   = 'Orthopedic',   'Orthopedic'
        NEUROSURGERY = 'Neurosurgery', 'Neurosurgery'
        OPHTHALMIC   = 'Ophthalmic',   'Ophthalmology'
        OBSTETRIC    = 'Obstetric',    'Obstetrics / Gynecology'
        ENT          = 'ENT',          'ENT'
        UROLOGY      = 'Urology',      'Urology'
        LAPAROSCOPIC = 'Laparoscopic', 'Laparoscopic'
        OTHER        = 'Other',        'Other'

    name            = models.CharField(max_length=50, unique=True)
    room_number     = models.CharField(max_length=20, blank=True)
    room_type       = models.CharField(max_length=20, choices=RoomType.choices, default=RoomType.GENERAL)
    floor           = models.CharField(max_length=30, blank=True)
    location_detail = models.CharField(max_length=100, blank=True)
    capacity_hours  = models.PositiveIntegerField(default=8, help_text='Available hours per day')
    equipment_notes = models.TextField(blank=True)
    is_active       = models.BooleanField(default=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Operating Room'
        verbose_name_plural = 'Operating Rooms'

    def __str__(self):
        return self.name


class SurgeryRequest(models.Model):
    class Status(models.TextChoices):
        REQUESTED  = 'Requested',  'Requested'
        APPROVED   = 'Approved',   'Approved'
        SCHEDULED  = 'Scheduled',  'Scheduled'
        CANCELLED  = 'Cancelled',  'Cancelled'

    class Priority(models.TextChoices):
        EMERGENCY = 'Emergency', 'Emergency'
        URGENT    = 'Urgent',    'Urgent'
        ELECTIVE  = 'Elective',  'Elective'

    class AnesthesiaType(models.TextChoices):
        GENERAL   = 'General',   'General Anesthesia'
        SPINAL    = 'Spinal',    'Spinal'
        EPIDURAL  = 'Epidural',  'Epidural'
        LOCAL     = 'Local',     'Local Anesthesia'
        REGIONAL  = 'Regional',  'Regional Block'
        MAC       = 'MAC',       'Monitored Anesthesia Care'
        OTHER     = 'Other',     'Other'

    class AdmissionStatus(models.TextChoices):
        OUTPATIENT = 'Outpatient', 'Outpatient'
        INPATIENT  = 'Inpatient',  'Inpatient'
        EMERGENCY  = 'Emergency',  'Emergency'

    request_number   = models.CharField(max_length=25, unique=True, blank=True)
    patient          = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='surgery_requests')
    requested_by     = models.ForeignKey(
        Doctor, on_delete=models.PROTECT, related_name='surgery_requests_created',
    )
    department       = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name='surgery_requests', null=True, blank=True,
    )
    diagnosis        = models.TextField()
    procedure_name   = models.CharField(max_length=200)
    procedure_code   = models.CharField(max_length=50, blank=True)
    anesthesia_type  = models.CharField(max_length=20, choices=AnesthesiaType.choices, default=AnesthesiaType.GENERAL)
    priority         = models.CharField(max_length=15, choices=Priority.choices, default=Priority.ELECTIVE)
    admission_status = models.CharField(max_length=15, choices=AdmissionStatus.choices, default=AdmissionStatus.INPATIENT)
    expected_duration = models.PositiveIntegerField(default=60, help_text='Expected duration in minutes')
    required_equipment = models.TextField(blank=True)
    required_implants  = models.TextField(blank=True)
    preop_notes        = models.TextField(blank=True)
    anesthesia_notes   = models.TextField(blank=True)
    status             = models.CharField(max_length=15, choices=Status.choices, default=Status.REQUESTED)
    notes              = models.TextField(blank=True)
    approved_by        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_requests_approved',
    )
    approved_at        = models.DateTimeField(null=True, blank=True)
    rejection_reason   = models.TextField(blank=True)
    created_by         = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='surgery_requests_created',
    )
    created_at         = models.DateTimeField(auto_now_add=True)
    updated_at         = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Surgery Request'
        verbose_name_plural = 'Surgery Requests'
        indexes = [
            models.Index(fields=['status', 'priority']),
            models.Index(fields=['patient']),
        ]

    def __str__(self):
        return f"{self.request_number} — {self.procedure_name} for {self.patient}"

    def save(self, *args, **kwargs):
        if not self.request_number:
            from django.utils import timezone
            today = timezone.localdate()
            last = SurgeryRequest.objects.order_by('-id').first()
            nxt = (last.id + 1) if last else 1
            self.request_number = f"SR-{today:%Y%m}-{nxt:04d}"
        super().save(*args, **kwargs)

    @property
    def priority_color(self):
        return {'Emergency': 'red', 'Urgent': 'amber', 'Elective': 'blue'}.get(self.priority, 'slate')


class ORSchedule(models.Model):
    class Status(models.TextChoices):
        REQUESTED          = 'Requested',         'Requested'
        APPROVED           = 'Approved',          'Approved'
        SCHEDULED          = 'Scheduled',         'Scheduled'
        PATIENT_PREPARED   = 'Patient Prepared',  'Patient Prepared'
        IN_OR              = 'In OR',             'In OR'
        SURGERY_STARTED    = 'Surgery Started',   'Surgery Started'
        SURGERY_COMPLETED  = 'Surgery Completed', 'Surgery Completed'
        CANCELLED          = 'Cancelled',         'Cancelled'
        POSTPONED          = 'Postponed',         'Postponed'

    schedule_number  = models.CharField(max_length=25, unique=True, blank=True)
    surgery_request  = models.OneToOneField(
        SurgeryRequest, on_delete=models.PROTECT, related_name='or_schedule',
    )
    operating_room   = models.ForeignKey(
        OperatingRoom, on_delete=models.PROTECT, related_name='schedules',
    )
    date             = models.DateField()
    start_time       = models.TimeField()
    end_time         = models.TimeField()

    # Surgical team
    primary_surgeon   = models.ForeignKey(
        Doctor, on_delete=models.PROTECT, related_name='or_schedules_primary',
    )
    assistant_surgeon = models.ForeignKey(
        Doctor, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='or_schedules_assistant',
    )
    anesthetist       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='or_schedules_anesthetist',
    )
    scrub_nurse       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='or_schedules_scrub',
    )
    circulating_nurse = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='or_schedules_circulating',
    )

    status              = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    notes               = models.TextField(blank=True)
    cancellation_reason = models.TextField(blank=True)
    postpone_reason     = models.TextField(blank=True)

    # Timestamp milestones
    patient_prepared_at   = models.DateTimeField(null=True, blank=True)
    in_or_at              = models.DateTimeField(null=True, blank=True)
    surgery_started_at    = models.DateTimeField(null=True, blank=True)
    surgery_completed_at  = models.DateTimeField(null=True, blank=True)

    scheduled_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='or_schedules_created',
    )
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', 'start_time']
        verbose_name = 'OR Schedule'
        verbose_name_plural = 'OR Schedules'
        indexes = [
            models.Index(fields=['date', 'operating_room']),
            models.Index(fields=['date', 'primary_surgeon']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.schedule_number} — {self.surgery_request.procedure_name} in {self.operating_room.name} on {self.date}"

    def save(self, *args, **kwargs):
        if not self.schedule_number:
            from django.utils import timezone
            today = timezone.localdate()
            last = ORSchedule.objects.order_by('-id').first()
            nxt = (last.id + 1) if last else 1
            self.schedule_number = f"ORS-{today:%Y%m}-{nxt:04d}"
        super().save(*args, **kwargs)

    @property
    def duration_minutes(self):
        from datetime import datetime, date as date_type
        start = datetime.combine(date_type.today(), self.start_time)
        end   = datetime.combine(date_type.today(), self.end_time)
        return int((end - start).total_seconds() / 60)

    @property
    def status_color(self):
        return {
            'Requested':        'blue',
            'Approved':         'indigo',
            'Scheduled':        'cyan',
            'Patient Prepared': 'amber',
            'In OR':            'orange',
            'Surgery Started':  'purple',
            'Surgery Completed':'green',
            'Cancelled':        'red',
            'Postponed':        'slate',
        }.get(self.status, 'slate')


class ORScheduleStatusLog(models.Model):
    schedule    = models.ForeignKey(ORSchedule, on_delete=models.CASCADE, related_name='status_logs')
    from_status = models.CharField(max_length=25, blank=True)
    to_status   = models.CharField(max_length=25)
    changed_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='or_status_changes',
    )
    changed_at  = models.DateTimeField(auto_now_add=True)
    notes       = models.TextField(blank=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.schedule.schedule_number}: {self.from_status} → {self.to_status}"

