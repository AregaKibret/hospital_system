import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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

        DAY_SURGERY    = 'day_surgery',    'Day Surgery Unit'
        BLOOD_BANK     = 'blood_bank',     'Blood Bank'
        PHYSIOTHERAPY  = 'physiotherapy',  'Physiotherapy'
        DENTAL         = 'dental',         'Dental Clinic'
        EYE_CLINIC     = 'eye_clinic',     'Eye Clinic'
        ENT_CLINIC     = 'ent_clinic',     'ENT Clinic'
        DERMATOLOGY    = 'dermatology',    'Dermatology Clinic'
        VACCINATION    = 'vaccination',    'Vaccination Clinic'
        CSSD           = 'cssd',           'Central Sterile Supply Department'
        MEDICAL_RECORDS = 'medical_records', 'Medical Records'

    name        = models.CharField(max_length=100, unique=True)
    dept_type   = models.CharField(max_length=20, choices=DeptType.choices, default=DeptType.OTHER)
    description = models.TextField(blank=True)
    head        = models.CharField(max_length=100, blank=True)
    phone       = models.CharField(max_length=30, blank=True)
    location    = models.CharField(max_length=200, blank=True)
    building    = models.ForeignKey('Building', on_delete=models.SET_NULL, null=True, blank=True, related_name='departments')
    floor       = models.ForeignKey('Floor', on_delete=models.SET_NULL, null=True, blank=True, related_name='departments')
    is_active   = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Department'
        verbose_name_plural = 'Departments'

    def __str__(self):
        return self.name


def _add_months(d, months):
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                       31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return d.replace(year=year, month=month, day=day)


class CardType(models.Model):
    """Admin-configurable patient card type (General OPD, Emergency, VIP, ...)
    — drives the registration/card fee and validity period charged when a
    patient's card is issued or renewed during visit creation."""
    class ValidityUnit(models.TextChoices):
        DAYS      = 'days',      'Days'
        MONTHS    = 'months',    'Months'
        YEARS     = 'years',     'Years'
        LIFETIME  = 'lifetime',  'Lifetime'
        ONE_VISIT = 'one_visit', 'One Visit'

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    renewal_fee = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Leave blank to use the same amount as the registration fee.',
    )
    validity_value = models.PositiveIntegerField(
        default=1, help_text='Ignored for Lifetime and One Visit.',
    )
    validity_unit = models.CharField(max_length=15, choices=ValidityUnit.choices, default=ValidityUnit.YEARS)
    is_hospital_wide = models.BooleanField(default=True)
    departments = models.ManyToManyField(
        Department, blank=True, related_name='card_types',
        help_text='Only used when not hospital-wide.',
    )
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False,
        help_text='Used automatically at appointment check-in, where the receptionist does not pick a card type by hand.',
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='card_types_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Card Type'
        verbose_name_plural = 'Card Types'

    def __str__(self):
        return f"{self.name} ({self.code})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            CardType.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)

    @property
    def effective_renewal_fee(self):
        return self.renewal_fee if self.renewal_fee is not None else self.fee

    def compute_expiry(self, from_date):
        """Return the expiry date for a card issued `from_date`, or None for
        Lifetime. One Visit cards expire the same day they're issued —
        every subsequent visit needs a fresh card."""
        if self.validity_unit == self.ValidityUnit.LIFETIME:
            return None
        if self.validity_unit == self.ValidityUnit.ONE_VISIT:
            return from_date
        if self.validity_unit == self.ValidityUnit.DAYS:
            return from_date + timezone.timedelta(days=self.validity_value)
        if self.validity_unit == self.ValidityUnit.MONTHS:
            return _add_months(from_date, self.validity_value)
        if self.validity_unit == self.ValidityUnit.YEARS:
            return _add_months(from_date, self.validity_value * 12)
        return from_date


class ConsultationType(models.Model):
    """Admin-configurable consultation type — each department can define its
    own set with its own fee (e.g. Internal Medicine's General Consultation
    vs. Gynecology's Antenatal Consultation)."""
    name = models.CharField(max_length=100)
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='consultation_types',
        help_text='Leave blank for a hospital-wide consultation type.',
    )
    fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False,
        help_text='Used automatically at appointment check-in for this department '
                   '(or hospital-wide) when the receptionist does not pick one by hand.',
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='consultation_types_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['department__name', 'name']
        verbose_name = 'Consultation Type'
        verbose_name_plural = 'Consultation Types'
        unique_together = ('name', 'department')

    def __str__(self):
        return f"{self.name} — {self.department.name if self.department else 'Hospital-wide'}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            ConsultationType.objects.exclude(pk=self.pk).filter(
                is_default=True, department=self.department,
            ).update(is_default=False)


class CardSettings(models.Model):
    """Singleton (pk=1) — global rules for card validity enforcement."""
    expiring_soon_warning_days = models.PositiveIntegerField(default=30)
    allow_admin_override_expired = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='card_settings_updates',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Card Settings'
        verbose_name_plural = 'Card Settings'

    def __str__(self):
        return f"Card Settings (warn {self.expiring_soon_warning_days}d before expiry)"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Specialization(models.Model):
    """Admin-configurable medical specialization (Cardiology, General Surgery,
    ...) — mandatory on a Doctor record, scoped to the department(s) it's
    available under."""
    name = models.CharField(max_length=100, unique=True)
    departments = models.ManyToManyField(
        Department, related_name='specializations',
        help_text='Departments this specialization can be assigned under. At least one is required.',
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    module_url_name = models.CharField(
        max_length=100, blank=True,
        help_text="URL name of this specialty's module dashboard (e.g. cardiology_dashboard) for automatic routing.",
    )
    color = models.CharField(
        max_length=20, blank=True, default='indigo',
        help_text='Tailwind color name for UI badges (e.g. indigo, blue, rose, emerald).',
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='specializations_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Specialization'
        verbose_name_plural = 'Specializations'

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
    specialization = models.ForeignKey(
        Specialization, on_delete=models.PROTECT, null=True, blank=True,
        related_name='doctors',
    )
    mobile = models.CharField(max_length=20, blank=True, default='')
    secondary_specializations = models.ManyToManyField(
        Specialization, related_name='secondary_doctors', blank=True,
        help_text='Additional specializations this doctor is qualified in.',
    )
    subspecialty = models.CharField(
        max_length=100, blank=True,
        help_text='Subspecialty detail, e.g. Interventional Cardiology, Paediatric Surgery.',
    )
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


class PatientCard(models.Model):
    """A card actually issued to a patient — one row per issue/renewal, so
    the full renewal history is preserved (`renewed_from` chains back)."""
    class Status(models.TextChoices):
        ACTIVE    = 'Active',    'Active'
        EXPIRED   = 'Expired',   'Expired'
        RENEWED   = 'Renewed',   'Renewed'
        CANCELLED = 'Cancelled', 'Cancelled'

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='cards')
    card_type = models.ForeignKey(CardType, on_delete=models.PROTECT, related_name='patient_cards')
    issued_date = models.DateField(default=timezone.localdate)
    expiry_date = models.DateField(null=True, blank=True)  # null = lifetime
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    fee_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fee_waived = models.BooleanField(default=False)
    invoice_item = models.ForeignKey(
        'InvoiceItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='patient_card',
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cards_issued',
    )
    renewed_from = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='renewals',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Patient Card'
        verbose_name_plural = 'Patient Cards'
        indexes = [models.Index(fields=['patient', 'card_type', 'status'])]

    def __str__(self):
        return f"{self.patient.card_number} — {self.card_type.name} ({self.status})"

    @property
    def is_expired(self):
        if self.expiry_date is None:
            return False
        return timezone.localdate() > self.expiry_date

    @property
    def is_expiring_soon(self):
        if self.expiry_date is None or self.is_expired:
            return False
        warning_days = CardSettings.get_solo().expiring_soon_warning_days
        return (self.expiry_date - timezone.localdate()).days <= warning_days

    @property
    def effective_status(self):
        """Live status — Active/Renewed/Cancelled rows can still turn out to
        be Expired once their expiry date has passed."""
        if self.status in (self.Status.CANCELLED, self.Status.RENEWED):
            return self.status
        if self.is_expired:
            return self.Status.EXPIRED
        return self.Status.ACTIVE


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
    specialty = models.ForeignKey(
        'Specialization', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='visits',
    )
    # For Revisit: link to the prior consultation this visit follows up on
    previous_visit = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='follow_ups',
    )
    card_type = models.ForeignKey(
        CardType, on_delete=models.SET_NULL, null=True, blank=True, related_name='visits',
    )
    consultation_type = models.ForeignKey(
        ConsultationType, on_delete=models.SET_NULL, null=True, blank=True, related_name='visits',
    )
    patient_card = models.ForeignKey(
        PatientCard, on_delete=models.SET_NULL, null=True, blank=True, related_name='visits',
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
        # Follow-up-specific types (Doctor's "Schedule Follow-Up" workflow)
        FOLLOWUP_CONSULTATION = 'Follow-up Consultation', 'Follow-up Consultation'
        POST_PROCEDURE_REVIEW = 'Post-Procedure Review', 'Post-Procedure Review'
        POST_OP_FOLLOWUP = 'Post-Operative Follow-up', 'Post-Operative Follow-up'
        WOUND_DRESSING = 'Wound Dressing', 'Wound Dressing'
        SUTURE_REMOVAL = 'Suture Removal', 'Suture Removal'
        LAB_RESULT_REVIEW = 'Laboratory Result Review', 'Laboratory Result Review'
        IMAGING_RESULT_REVIEW = 'Imaging Result Review', 'Imaging Result Review'
        CHRONIC_DISEASE_FOLLOWUP = 'Chronic Disease Follow-up', 'Chronic Disease Follow-up'
        MEDICATION_REVIEW = 'Medication Review', 'Medication Review'
        OTHER_CUSTOM = 'Other', 'Other (Custom)'

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
        null=True, blank=True,
    )
    doctor = models.ForeignKey(
        Doctor, on_delete=models.PROTECT, related_name='appointments',
        null=True, blank=True,
    )
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name='appointments',
        null=True, blank=True,
    )
    # Walk-in / unregistered booking — set only while `patient` is still null
    # (the receptionist secured a slot for someone with no MRN yet). Once the
    # patient completes registration, `patient` is linked and this is kept
    # only as a historical record of what was typed at booking time.
    walkin_name = models.CharField(max_length=150, blank=True)
    appointment_date = models.DateField()
    appointment_time = models.TimeField()
    appointment_type = models.CharField(
        max_length=30, choices=AppointmentType.choices, default=AppointmentType.NEW,
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
    specialty = models.ForeignKey(
        'Specialization', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='appointments',
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
        doctor_part = f"Dr. {self.doctor.full_name}" if self.doctor else "Unassigned Doctor"
        return f"{self.appointment_number} — {self.display_patient_name} with {doctor_part} on {self.appointment_date}"

    def save(self, *args, **kwargs):
        if not self.appointment_number:
            from django.utils import timezone
            today = timezone.localdate()
            last = Appointment.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.appointment_number = f"APT-{today:%Y%m}-{next_id:04d}"
        super().save(*args, **kwargs)

    @property
    def is_unregistered(self):
        """True for walk-in appointments booked before the patient has an MRN."""
        return self.patient_id is None

    @property
    def display_patient_name(self):
        if self.patient_id:
            return self.patient.full_name
        return self.walkin_name or 'Unregistered Patient'

    @property
    def display_identifier(self):
        """MRN for registered patients, phone number (walk-in) otherwise."""
        if self.patient_id:
            return self.patient.card_number
        return self.phone_number or 'No MRN yet'

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


# ── Physical Examination ──────────────────────────────────────────────────────

class PhysicalExamTemplate(models.Model):
    """Admin-configurable specialty examination template."""

    class Specialty(models.TextChoices):
        GENERAL       = 'General',       'General / Internal Medicine'
        SURGERY       = 'Surgery',       'Surgery / Pre-Op'
        PEDIATRICS    = 'Pediatrics',    'Pediatrics'
        GYNECOLOGY    = 'Gynecology',    'Gynecology & Obstetrics'
        ORTHOPEDICS   = 'Orthopedics',   'Orthopedics'
        ENT           = 'ENT',           'ENT'
        OPHTHALMOLOGY = 'Ophthalmology', 'Ophthalmology'
        NEUROLOGY     = 'Neurology',     'Neurology'
        CARDIOLOGY    = 'Cardiology',    'Cardiology'
        DERMATOLOGY   = 'Dermatology',   'Dermatology'
        UROLOGY       = 'Urology',       'Urology'
        EMERGENCY     = 'Emergency',     'Emergency Medicine'
        PSYCHIATRY    = 'Psychiatry',    'Psychiatry'
        CUSTOM        = 'Custom',        'Custom'

    name           = models.CharField(max_length=120)
    specialty      = models.CharField(max_length=30, choices=Specialty.choices, default=Specialty.GENERAL)
    description    = models.TextField(blank=True)
    systems_config = models.JSONField(default=list, help_text='List of {key, include, default_status}')
    is_active      = models.BooleanField(default=True)
    is_default     = models.BooleanField(default=False)
    created_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='exam_templates_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['specialty', 'name']
        verbose_name = 'Physical Exam Template'
        verbose_name_plural = 'Physical Exam Templates'

    def __str__(self):
        return f"{self.name} ({self.get_specialty_display()})"


class PhysicalExamination(models.Model):
    """Structured physical examination record linked to a visit."""

    visit = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='physical_exams')
    clinical_note = models.OneToOneField(
        ClinicalNote, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='physical_exam',
    )
    template = models.ForeignKey(
        PhysicalExamTemplate, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='examinations',
    )
    examiner        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='physical_exams_conducted',
    )
    findings        = models.JSONField(default=dict, help_text='system_key: {status, comment}')
    overall_summary = models.TextField(blank=True)
    is_complete     = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Physical Examination'
        verbose_name_plural = 'Physical Examinations'

    def __str__(self):
        return f"Physical Exam — {self.visit.patient} ({self.created_at:%Y-%m-%d})"


# ── Laboratory Service Master ──────────────────────────────────────────────────

class LabCategory(models.Model):
    """Top-level laboratory catalog category (Hematology, Clinical Chemistry,
    Electrolytes, ...). Fully admin-managed — replaces the old hardcoded
    LabService.Section choices so new categories never require a code change."""
    name          = models.CharField(max_length=100, unique=True)
    code          = models.CharField(max_length=20, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active     = models.BooleanField(default=True)
    notes         = models.TextField(blank=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Lab Category'
        verbose_name_plural = 'Lab Categories'

    def __str__(self):
        return self.name


class LabTestGroup(models.Model):
    """Optional secondary classification of a test within (or across) a
    category — separate from Panel, which expresses a real parent/child
    ordering hierarchy. Admin-managed, starts empty."""
    name          = models.CharField(max_length=100, unique=True)
    category      = models.ForeignKey(LabCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='groups')
    display_order = models.PositiveIntegerField(default=0)
    is_active     = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Lab Test Group'
        verbose_name_plural = 'Lab Test Groups'

    def __str__(self):
        return self.name


class LabService(models.Model):
    """Catalogue of laboratory tests with configurable pricing. A row may
    also act as a Panel (a named, priced bundle of child tests) simply by
    having other LabService rows point their `panel` FK at it — no separate
    boolean or model needed. This also covers nested panels and reflex
    tests (e.g. INR nested under PT) uniformly."""

    class SampleType(models.TextChoices):
        BLOOD   = 'Blood',  'Blood'
        URINE   = 'Urine',  'Urine'
        STOOL   = 'Stool',  'Stool'
        SWAB    = 'Swab',   'Swab'
        TISSUE  = 'Tissue', 'Tissue'
        CSF     = 'CSF',    'Cerebrospinal Fluid'
        SPUTUM  = 'Sputum', 'Sputum'
        PLASMA  = 'Plasma', 'Plasma'
        SERUM   = 'Serum',  'Serum'
        OTHER   = 'Other',  'Other'

    class ResultType(models.TextChoices):
        NUMERIC      = 'numeric',     'Numeric'
        QUALITATIVE  = 'qualitative', 'Qualitative'
        SELECT_LIST  = 'select',      'Select List'
        FREE_TEXT    = 'free_text',   'Free Text'

    class ResultInputType(models.TextChoices):
        NUMERIC_ENTRY           = 'numeric_entry',           'Numeric'
        DECIMAL_ENTRY           = 'decimal_entry',           'Decimal'
        TEXT_ENTRY              = 'text_entry',              'Text'
        DROPDOWN_SELECT         = 'dropdown_select',         'Dropdown List'
        RADIO_SELECT            = 'radio_select',            'Radio Button'
        CHECKBOX                = 'checkbox',                'Checkbox'
        POSITIVE_NEGATIVE       = 'positive_negative',       'Positive / Negative'
        REACTIVE_NONREACTIVE    = 'reactive_nonreactive',    'Reactive / Non-Reactive'
        PRESENT_ABSENT          = 'present_absent',          'Present / Absent'
        NORMAL_ABNORMAL         = 'normal_abnormal',         'Normal / Abnormal'
        POSITIVE_NEGATIVE_TRACE = 'positive_negative_trace', 'Positive / Negative / Trace'
        DATE_ENTRY              = 'date_entry',              'Date'
        TIME_ENTRY              = 'time_entry',              'Time'

    # Fixed option sets for the qualitative ResultInputTypes above — these
    # don't need admin-defined LabTestResultOption rows since the choices are
    # inherent to the type itself. DROPDOWN_SELECT/RADIO_SELECT/CHECKBOX are
    # the only types that read their options from LabTestResultOption.
    FIXED_RESULT_OPTIONS = {
        'positive_negative':       ['Positive', 'Negative'],
        'reactive_nonreactive':    ['Reactive', 'Non-Reactive'],
        'present_absent':          ['Present', 'Absent'],
        'normal_abnormal':         ['Normal', 'Abnormal'],
        'positive_negative_trace': ['Positive', 'Negative', 'Trace'],
    }

    name                    = models.CharField(max_length=200)
    short_name              = models.CharField(max_length=30, blank=True, help_text='Abbreviation e.g. CBC, LFT, TFT')
    code                    = models.CharField(max_length=30, unique=True)
    category                = models.ForeignKey(
        LabCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='lab_services',
    )
    group                   = models.ForeignKey(
        LabTestGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name='lab_services',
    )
    panel                   = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='panel_tests',
    )
    description             = models.TextField(blank=True)
    # Search
    keywords                = models.TextField(
        blank=True,
        help_text='Comma-separated search terms e.g. blood count, hemoglobin, CBC',
    )
    # Sample
    sample_type             = models.CharField(max_length=20, choices=SampleType.choices, default=SampleType.BLOOD)
    container               = models.CharField(max_length=100, blank=True, help_text='e.g. EDTA tube, Plain tube')
    sample_requirements     = models.TextField(blank=True)
    preparation_instructions = models.TextField(blank=True)
    turnaround_hours        = models.PositiveIntegerField(default=24)

    # Result configuration
    unit_of_measurement = models.CharField(max_length=50, blank=True)
    reference_range     = models.CharField(max_length=300, blank=True)
    critical_values      = models.CharField(max_length=300, blank=True)
    qc_reference_range   = models.CharField(max_length=300, blank=True)
    result_type          = models.CharField(max_length=15, choices=ResultType.choices, default=ResultType.NUMERIC)
    result_input_type    = models.CharField(max_length=25, choices=ResultInputType.choices, default=ResultInputType.NUMERIC_ENTRY)
    default_result        = models.CharField(max_length=100, blank=True)

    # Pricing
    standard_price   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    insurance_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    corporate_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    emergency_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tax_percent      = models.DecimalField(max_digits=5,  decimal_places=2, default=0)

    # Organization / display
    display_order = models.PositiveIntegerField(default=0)
    is_printable  = models.BooleanField(default=True)
    department    = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='lab_services',
    )

    # Inventory linkage (optional — reagents/consumables tracked in the general store)
    linked_inventory_item = models.ForeignKey(
        'InventoryItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='lab_services',
    )
    stock_item_reference = models.CharField(max_length=100, blank=True)
    analyzer = models.CharField(max_length=100, blank=True, help_text='e.g. Huma Count 30TS, Linear, ECL105')

    notes = models.TextField(blank=True)

    is_active   = models.BooleanField(default=True)
    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lab_services_created',
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category__display_order', 'display_order', 'name']
        verbose_name = 'Lab Service'
        verbose_name_plural = 'Lab Services'
        indexes = [
            models.Index(fields=['is_active', 'category']),
        ]

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

    def search_text(self):
        """Combined text for full-text search matching."""
        return ' '.join(filter(None, [
            self.name, self.short_name, self.code, self.keywords,
            self.category.name if self.category_id else '',
            self.group.name if self.group_id else '',
        ]))

    @property
    def is_panel(self):
        return self.panel_tests.exists()

    @property
    def fixed_result_options(self):
        """The built-in option pair/triple for this analyte's
        result_input_type (e.g. ['Positive', 'Negative']) — empty list for
        input types that use admin-defined LabTestResultOption rows instead
        (dropdown/radio/checkbox) or free entry (numeric/decimal/text/date/time)."""
        return self.FIXED_RESULT_OPTIONS.get(self.result_input_type, [])


class LabTestResultOption(models.Model):
    """One allowed qualitative result value for a Select-list test (e.g.
    Negative/Positive, or a urine-color option list)."""
    lab_service   = models.ForeignKey(LabService, on_delete=models.CASCADE, related_name='result_options')
    value         = models.CharField(max_length=200)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'id']
        verbose_name = 'Lab Test Result Option'

    def __str__(self):
        return f"{self.lab_service.code}: {self.value}"


class LabReferenceRange(models.Model):
    """Optional age/sex-specific override of an analyte's default
    reference_range/critical_values. If no row matches a given result's
    patient, interpretation falls back to the analyte's own plain
    reference_range/critical_values fields — this table only needs to be
    populated where age/sex actually changes the normal range."""
    class Sex(models.TextChoices):
        ANY    = 'any',    'Any'
        MALE   = 'male',   'Male'
        FEMALE = 'female', 'Female'

    lab_service      = models.ForeignKey(LabService, on_delete=models.CASCADE, related_name='reference_ranges')
    sex              = models.CharField(max_length=10, choices=Sex.choices, default=Sex.ANY)
    age_min_years    = models.PositiveSmallIntegerField(null=True, blank=True)
    age_max_years    = models.PositiveSmallIntegerField(null=True, blank=True)
    reference_range  = models.CharField(max_length=300)
    critical_values  = models.CharField(max_length=300, blank=True)
    notes            = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['lab_service', 'sex', 'age_min_years']
        verbose_name = 'Lab Reference Range Override'

    def __str__(self):
        age = ''
        if self.age_min_years is not None or self.age_max_years is not None:
            age = f" {self.age_min_years or 0}-{self.age_max_years or '+'}y"
        return f"{self.lab_service.code} ({self.get_sex_display()}{age}): {self.reference_range}"

    def matches(self, age_years, sex):
        if self.sex != self.Sex.ANY and sex and self.sex != sex:
            return False
        if age_years is not None:
            if self.age_min_years is not None and age_years < self.age_min_years:
                return False
            if self.age_max_years is not None and age_years > self.age_max_years:
                return False
        return True


class LabResultEntry(models.Model):
    """One structured result row per analyte per order — an order for a
    panel (e.g. CBC) gets one LabResultEntry per component test (WBC, RBC,
    HGB, ...); an order for a standalone test gets exactly one. This is the
    dynamic, database-driven replacement for free-text result entry:
    the set of rows, their input widget, unit, and reference range are all
    derived from the ordered LabService's catalog definition, never typed
    by the technician."""
    class Flag(models.TextChoices):
        NORMAL   = 'normal',   'Normal'
        HIGH     = 'high',     'High'
        LOW      = 'low',      'Low'
        ABNORMAL = 'abnormal', 'Abnormal'
        CRITICAL = 'critical', 'Critical'

    lab_order = models.ForeignKey('LabOrder', on_delete=models.CASCADE, related_name='result_entries')
    analyte   = models.ForeignKey(LabService, on_delete=models.PROTECT, related_name='result_entries')

    value            = models.CharField(max_length=200, blank=True)
    unit             = models.CharField(max_length=50, blank=True)
    reference_range  = models.CharField(max_length=300, blank=True)
    flag             = models.CharField(max_length=10, choices=Flag.choices, blank=True)
    is_critical      = models.BooleanField(default=False)
    comments         = models.CharField(max_length=300, blank=True)
    display_order    = models.PositiveIntegerField(default=0)

    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='lab_result_entries',
    )
    entered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['display_order', 'id']
        unique_together = ('lab_order', 'analyte')
        verbose_name = 'Lab Result Entry'
        verbose_name_plural = 'Lab Result Entries'

    def __str__(self):
        return f"{self.analyte.name}: {self.value or '—'}"


class LabServicePriceHistory(models.Model):
    """Immutable record of every price change on a lab service."""
    lab_service    = models.ForeignKey(LabService, on_delete=models.CASCADE, related_name='price_history')
    old_price      = models.DecimalField(max_digits=10, decimal_places=2)
    new_price      = models.DecimalField(max_digits=10, decimal_places=2)
    price_type     = models.CharField(max_length=20, default='standard',
                        choices=[('standard','Standard'),('insurance','Insurance'),
                                 ('corporate','Corporate'),('emergency','Emergency')])
    changed_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='lab_price_changes',
    )
    changed_at     = models.DateTimeField(auto_now_add=True)
    reason         = models.TextField(blank=True)

    class Meta:
        ordering = ['-changed_at']
        verbose_name = 'Lab Service Price History'

    def __str__(self):
        return f"{self.lab_service.name}: {self.old_price} → {self.new_price} ({self.changed_at:%Y-%m-%d})"


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
    resulted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lab_results_entered',
    )
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lab_results_released',
    )

    # Critical-result flagging — a human judgment call made at result-entry
    # time (results are free text, so there is no automatic range check).
    is_critical          = models.BooleanField(default=False)
    critical_flagged_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lab_results_flagged_critical',
    )
    critical_flagged_at  = models.DateTimeField(null=True, blank=True)
    critical_notes       = models.CharField(max_length=300, blank=True)

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
        WAITING_PAYMENT = 'Waiting Payment', 'Waiting Payment'
        PENDING = 'Pending', 'Pending'
        IN_PROGRESS = 'In Progress', 'In Progress'
        COMPLETED = 'Completed', 'Completed'
        CANCELLED = 'Cancelled', 'Cancelled'

    class PaymentStatus(models.TextChoices):
        PENDING_PAYMENT = 'Pending Payment', 'Pending Payment'
        PAID            = 'Paid',            'Paid'
        PARTIAL         = 'Partial',         'Partially Paid'
        CREDIT          = 'Credit',          'Credit'
        WAIVED          = 'Waived',          'Waived'

    visit = models.ForeignKey(
        Visit, on_delete=models.PROTECT, related_name='imaging_orders',
    )
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='imaging_orders',
    )
    # Service master link (optional — allows free-text fallback)
    imaging_service = models.ForeignKey(
        'ImagingService', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders',
    )
    # Billing link
    invoice_item = models.OneToOneField(
        'InvoiceItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='imaging_order',
    )
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    imaging_type = models.CharField(max_length=20, choices=ImagingType.choices)
    body_part = models.CharField(max_length=100)
    clinical_indication = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.ROUTINE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.WAITING_PAYMENT)
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING_PAYMENT,
    )
    report = models.TextField(blank=True)
    ordered_at = models.DateTimeField(auto_now_add=True)
    reported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-ordered_at']
        verbose_name = 'Imaging Order'
        verbose_name_plural = 'Imaging Orders'

    def __str__(self):
        return f"{self.imaging_type} — {self.body_part} [{self.priority}]"

    @property
    def payment_cleared(self):
        return self.payment_status in (
            self.PaymentStatus.PAID, self.PaymentStatus.CREDIT, self.PaymentStatus.WAIVED,
        )


class ImagingService(models.Model):
    """Catalogue of imaging/radiology services with configurable pricing."""

    name          = models.CharField(max_length=200)
    short_name    = models.CharField(max_length=30, blank=True, help_text='Abbreviation e.g. CXR, KUB')
    code          = models.CharField(max_length=30, unique=True)
    modality      = models.CharField(max_length=20, choices=ImagingOrder.ImagingType.choices)
    body_part     = models.CharField(max_length=100, blank=True)
    description   = models.TextField(blank=True)
    keywords      = models.TextField(
        blank=True,
        help_text='Comma-separated search terms e.g. chest xray, CXR, lung',
    )
    preparation_instructions = models.TextField(blank=True)
    turnaround_hours = models.PositiveIntegerField(default=24)

    # Pricing
    standard_price  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    insurance_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    corporate_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    emergency_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tax_percent     = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    is_active  = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='imaging_services_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['modality', 'name']
        verbose_name = 'Imaging Service'
        verbose_name_plural = 'Imaging Services'
        indexes = [
            models.Index(fields=['is_active', 'modality']),
        ]

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

    def search_text(self):
        return ' '.join(filter(None, [self.name, self.short_name, self.code, self.keywords]))


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

    class PaymentStatus(models.TextChoices):
        PENDING_PAYMENT = 'Pending Payment', 'Pending Payment'
        PAID            = 'Paid',            'Paid'
        PARTIAL         = 'Partial',         'Partially Paid'
        CREDIT          = 'Credit',          'Credit'
        WAIVED          = 'Waived',          'Waived'

    visit = models.ForeignKey(
        Visit, on_delete=models.PROTECT, related_name='medication_orders',
    )
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='medication_orders',
    )
    # Catalogue link (optional — allows free-text fallback for ward stock not in formulary)
    pharmacy_stock = models.ForeignKey(
        'PharmacyStock', on_delete=models.SET_NULL, null=True, blank=True, related_name='medication_orders',
    )
    # Billing link
    invoice_item = models.OneToOneField(
        'InvoiceItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='medication_order',
    )
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    drug_name = models.CharField(max_length=200)
    dosage = models.CharField(max_length=100)
    route = models.CharField(max_length=10, choices=Route.choices, default=Route.PO)
    frequency = models.CharField(max_length=100)
    duration = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    instructions = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING_PAYMENT,
    )
    ordered_at = models.DateTimeField(auto_now_add=True)
    discontinued_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-ordered_at']
        verbose_name = 'Medication Order'
        verbose_name_plural = 'Medication Orders'

    def __str__(self):
        return f"{self.drug_name} {self.dosage} {self.route} — {self.status}"

    @property
    def payment_cleared(self):
        return self.payment_status in (
            self.PaymentStatus.PAID, self.PaymentStatus.CREDIT, self.PaymentStatus.WAIVED,
        )


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
    # Bridge into the central Medication Inventory ledger (Medication/MedicationBatch/
    # StockTransaction) so Stock on Hand / Stock Card / Stock Movement reports — which
    # read that ledger — reflect real pharmacy stock movements instead of showing an
    # unrelated, unpopulated catalog.
    medication = models.OneToOneField(
        'Medication', on_delete=models.SET_NULL, null=True, blank=True, related_name='pharmacy_stock_link',
    )
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
        """Outstanding amount still owed. total_amount is the historical
        gross bill and is never mutated by item cancellation/refund — those
        amounts are excluded here instead, so the audit trail of what was
        originally charged stays intact."""
        from django.db.models import Sum as _Sum
        excluded = self.items.filter(
            payment_status__in=[InvoiceItem.PaymentStatus.CANCELLED, InvoiceItem.PaymentStatus.REFUNDED],
        ).aggregate(t=_Sum('total'))['t'] or 0
        return self.total_amount - excluded - self.paid_amount - self.discount

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
        DEPOSIT = 'Deposit', 'Admission Deposit'
        NURSING = 'Nursing Care', 'Nursing Care'
        SERVICE_CHARGE = 'Service Charge', 'Service Charge'
        CARD_FEE = 'Card Fee', 'Card Fee'
        SURGERY_BOOKING = 'Surgery Booking', 'Surgery Booking Deposit'
        SURGERY_DEPOSIT = 'Surgery Deposit', 'Surgery Pre-Deposit'
        OTHER = 'Other', 'Other'

    class PaymentStatus(models.TextChoices):
        PENDING_PAYMENT = 'Pending Payment', 'Pending Payment'
        PARTIAL         = 'Partially Paid', 'Partially Paid'
        PAID            = 'Paid',            'Paid'
        CREDIT          = 'Credit Approved',  'Credit Approved'
        CANCELLED       = 'Cancelled',        'Cancelled'
        REFUNDED        = 'Refunded',         'Refunded'

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=255)
    service_type = models.CharField(max_length=20, choices=ServiceType.choices, default=ServiceType.OTHER)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Item-level payment tracking — the source of truth for whether a
    # specific billable service (this line item) may be processed by its
    # owning department, independent of the rest of the invoice.
    payment_status  = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING_PAYMENT,
    )
    paid_amount     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='credit_approved_invoice_items',
    )
    credit_approved_at = models.DateTimeField(null=True, blank=True)
    credit_reason      = models.TextField(blank=True)
    cancelled_at       = models.DateTimeField(null=True, blank=True)
    cancelled_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='cancelled_invoice_items',
    )
    cancel_reason      = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Invoice Item'
        verbose_name_plural = 'Invoice Items'

    def __str__(self):
        return f"{self.description} × {self.quantity}"

    def save(self, *args, **kwargs):
        self.total = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    @property
    def balance(self):
        return self.total - self.paid_amount

    @property
    def payment_cleared(self):
        return self.payment_status in (self.PaymentStatus.PAID, self.PaymentStatus.CREDIT)

    @property
    def is_payable(self):
        """Can this item still receive a cash payment right now?"""
        return self.payment_status in (self.PaymentStatus.PENDING_PAYMENT, self.PaymentStatus.PARTIAL)


class PaymentAllocation(models.Model):
    """Records exactly which invoice items a given Payment settled, and how
    much of that payment went to each — the audit trail behind item-level
    partial payment."""
    payment      = models.ForeignKey('Payment', on_delete=models.CASCADE, related_name='allocations')
    invoice_item = models.ForeignKey(InvoiceItem, on_delete=models.CASCADE, related_name='payment_allocations')
    amount       = models.DecimalField(max_digits=12, decimal_places=2)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['invoice_item_id']
        verbose_name = 'Payment Allocation'

    def __str__(self):
        return f"{self.payment.receipt_number} → {self.invoice_item.description}: ETB {self.amount}"


class InvoiceItemRefund(models.Model):
    """A refund issued against a single, already-paid invoice item."""
    invoice_item = models.ForeignKey(InvoiceItem, on_delete=models.CASCADE, related_name='item_refunds')
    amount       = models.DecimalField(max_digits=12, decimal_places=2)
    reason       = models.TextField(blank=True)
    reference_number = models.CharField(max_length=100, blank=True)
    refunded_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='invoice_item_refunds',
    )
    refunded_at  = models.DateTimeField(default=timezone.now)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-refunded_at']
        verbose_name = 'Invoice Item Refund'

    def __str__(self):
        return f"Refund ETB {self.amount} — {self.invoice_item.description}"


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


class ServiceChargeSettings(models.Model):
    """Singleton (single row, pk=1) admin-editable configuration for the
    optional per-item service charge applied at payment time. Editable
    without a code change via the Service Charge Settings screen."""
    enabled = models.BooleanField(default=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('4.00'))
    charge_name = models.CharField(max_length=100, default='Service Charge')
    allow_cashier_override = models.BooleanField(default=True)
    # InvoiceItem.ServiceType values that default to UNCHECKED (everything
    # else defaults to checked). Default: Medication/Pharmacy only.
    default_disabled_service_types = models.JSONField(default=list)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='service_charge_settings_updates',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Service Charge Settings'
        verbose_name_plural = 'Service Charge Settings'

    def __str__(self):
        return f"Service Charge Settings ({self.percentage}% — {'enabled' if self.enabled else 'disabled'})"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={
            'default_disabled_service_types': [InvoiceItem.ServiceType.MEDICATION],
        })
        return obj

    def default_checked_for(self, service_type):
        return self.enabled and service_type not in (self.default_disabled_service_types or [])


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


class ItemGroup(models.Model):
    """Admin-configurable item grouping, orthogonal to InventoryCategory —
    e.g. a finer-grained clinical/packaging grouping within a category."""
    name        = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active   = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Item Group'

    def __str__(self):
        return self.name


class UnitOfMeasure(models.Model):
    """Curated reference list of units of measure, used as an autocomplete
    source for the free-text unit fields on InventoryItem (unit,
    unit_purchase, dispensing_unit stay CharField — this is a suggestion
    list, not a hard FK constraint, to avoid touching the extensive existing
    code that treats units as plain strings)."""
    name      = models.CharField(max_length=30, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Unit of Measure'
        verbose_name_plural = 'Units of Measure'

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
    item_code   = models.CharField(max_length=30, unique=True, null=True, blank=True,
                                    help_text='Business item code — the primary lookup key (distinct from sku).')
    generic_name = models.CharField(max_length=255, blank=True)
    item_type   = models.CharField(max_length=20, choices=ItemType.choices, default=ItemType.GENERAL)
    category    = models.ForeignKey(
        InventoryCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='items',
    )
    subcategory = models.CharField(max_length=100, blank=True)
    item_group  = models.ForeignKey(
        ItemGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name='items',
    )
    therapeutic_category = models.CharField(max_length=100, blank=True)
    dosage_form = models.CharField(max_length=50, blank=True)
    strength    = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    sku         = models.CharField(max_length=50, blank=True)
    barcode     = models.CharField(max_length=100, blank=True)
    brand       = models.CharField(max_length=100, blank=True)
    manufacturer = models.CharField(max_length=200, blank=True)

    # Units & packaging (3-tier: storage → purchase → dispensing)
    unit               = models.CharField(max_length=50, default='units')
    unit_purchase      = models.CharField(max_length=50, blank=True, help_text='e.g. Carton')
    units_per_purchase = models.DecimalField(max_digits=10, decimal_places=2, default=1,
                                             help_text='How many storage units per purchase unit')
    dispensing_unit    = models.CharField(max_length=50, blank=True, help_text='Unit the item is dispensed/consumed in')
    consumption_factor = models.DecimalField(max_digits=10, decimal_places=2, default=1,
                                              help_text='How many dispensing units per purchase unit')

    # Stock control
    quantity_in_stock = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    quantity_damaged  = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    quantity_reserved = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    reorder_level     = models.DecimalField(max_digits=12, decimal_places=2, default=10)
    reorder_quantity  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    min_stock         = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    max_stock         = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    safety_stock      = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Pricing (multi-tier selling price + cost)
    unit_cost        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    average_cost      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    selling_price    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    selling_price_is_manual = models.BooleanField(default=False)
    inpatient_price   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    emergency_price   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_type          = models.CharField(max_length=20, blank=True)

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

    # Billing — for consumables that should be charged to the patient when
    # used at the bedside (e.g. IV cannulas, catheters), as opposed to
    # overhead supplies (gloves, gauze) that aren't itemized on the bill.
    is_billable = models.BooleanField(default=False, help_text='Charge the patient (at selling_price) when a nurse records usage of this item.')

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


SIGNATURE_ALLOWED_EXTENSIONS = ('png', 'jpg', 'jpeg', 'svg')
SIGNATURE_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


def validate_signature_file(file):
    ext = os.path.splitext(file.name)[1].lower().lstrip('.')
    if ext not in SIGNATURE_ALLOWED_EXTENSIONS:
        raise ValidationError(f'Unsupported file type ".{ext}". Allowed: PNG, JPG/JPEG, SVG.')
    if file.size > SIGNATURE_MAX_BYTES:
        raise ValidationError('Signature file must be smaller than 2 MB.')


def employee_signature_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f'signatures/employee_{instance.employee_id}/{uuid.uuid4().hex}{ext}'


class EmployeeSignature(models.Model):
    """One row per uploaded signature file — never deleted, only deactivated,
    so the full upload/replace/remove history is always auditable. Exactly
    one row per employee has is_active=True at a time (enforced in the
    upload/remove views, both wrapped in transaction.atomic()).

    `signature_type` is deliberately open-ended (TextChoices) so new signing
    methods (digital certificate, MFA-verified electronic approval, etc.)
    can be added later without touching existing rows or call sites — every
    consumer just reads the current active row via `employee.signatures`.
    """
    class SignatureType(models.TextChoices):
        HANDWRITTEN          = 'handwritten',          'Handwritten Signature Image'
        DIGITAL               = 'digital',               'Digital Signature'
        ELECTRONIC_APPROVAL   = 'electronic_approval',   'Electronic Approval Signature'

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='signatures')
    signature_type = models.CharField(max_length=25, choices=SignatureType.choices, default=SignatureType.HANDWRITTEN)
    file = models.FileField(upload_to=employee_signature_upload_path, validators=[validate_signature_file])
    is_active = models.BooleanField(default=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='employee_signatures_uploaded',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    deactivated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='employee_signatures_deactivated',
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-uploaded_at']
        indexes = [models.Index(fields=['employee', 'is_active'])]

    def __str__(self):
        state = 'active' if self.is_active else 'inactive'
        return f"{self.employee.full_name} — {self.get_signature_type_display()} ({state})"


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


class GoodsReceipt(models.Model):
    """One row per supplier delivery/invoice — the receiving 'header' a
    goods-receipt cart submission (or the single-drug quick-receive
    fast path) always creates exactly one of, regardless of how many
    drugs/batches it contains."""
    class PaymentMethod(models.TextChoices):
        CASH        = 'cash',        'Cash'
        CREDIT      = 'credit',      'Credit'
        CONSIGNMENT = 'consignment', 'Consignment'

    goods_receipt_number = models.CharField(max_length=20, unique=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='goods_receipts')

    supplier_invoice_number = models.CharField(max_length=100)
    supplier_invoice_date   = models.DateField(null=True, blank=True)
    purchase_order_ref      = models.CharField(max_length=100, blank=True)
    delivery_note_number    = models.CharField(max_length=100, blank=True)

    payment_method = models.CharField(max_length=15, choices=PaymentMethod.choices, default=PaymentMethod.CASH)

    received_by   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='goods_receipts_received')
    received_date = models.DateTimeField(default=timezone.now)

    # Cash-specific — meaningful only when payment_method == CASH (paid in
    # full at the time of receiving, no SupplierPayable needed).
    cash_payment_date      = models.DateField(null=True, blank=True)
    cash_amount_paid       = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cash_cashier           = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='goods_receipts_cashiered',
    )
    cash_payment_reference = models.CharField(max_length=100, blank=True)

    notes      = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_date']
        unique_together = [('supplier', 'supplier_invoice_number')]
        verbose_name = 'Goods Receipt'

    def __str__(self):
        return f'{self.goods_receipt_number} — {self.supplier.name} ({self.get_payment_method_display()})'

    def save(self, *args, **kwargs):
        if not self.goods_receipt_number:
            last = GoodsReceipt.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.goods_receipt_number = f"GR-{timezone.localdate():%Y%m}-{next_id:04d}"
        super().save(*args, **kwargs)


class SupplierPayable(models.Model):
    """Money owed to a supplier for a Credit or Consignment goods receipt —
    never created for a Cash receipt (paid in full immediately, recorded
    directly on GoodsReceipt instead)."""
    goods_receipt = models.OneToOneField(GoodsReceipt, on_delete=models.CASCADE, related_name='payable')
    supplier      = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='payables')

    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_paid  = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    due_date           = models.DateField(null=True, blank=True)
    credit_period_days = models.PositiveIntegerField(null=True, blank=True)
    consignment_agreement_number = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_date']
        verbose_name = 'Supplier Payable'

    def __str__(self):
        return f'{self.supplier.name} — {self.goods_receipt.goods_receipt_number} ({self.status})'

    @property
    def outstanding_balance(self):
        return self.total_amount - self.amount_paid

    @property
    def is_overdue(self):
        return bool(self.due_date) and self.due_date < timezone.localdate() and self.outstanding_balance > 0

    @property
    def status(self):
        if self.amount_paid >= self.total_amount and self.total_amount > 0:
            return 'Fully Paid'
        if self.is_overdue:
            return 'Overdue'
        if self.amount_paid > 0:
            return 'Partially Paid'
        return 'Unpaid'

    def recalculate_amount_paid(self):
        from django.db.models import Sum
        self.amount_paid = self.payments.aggregate(t=Sum('amount'))['t'] or 0
        self.save(update_fields=['amount_paid', 'updated_at'])


class SupplierPayment(models.Model):
    """A single payment (partial or full) recorded against a SupplierPayable."""
    payable   = models.ForeignKey(SupplierPayable, on_delete=models.CASCADE, related_name='payments')
    amount    = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date      = models.DateField(default=timezone.localdate)
    payment_reference = models.CharField(max_length=100, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='supplier_payments_recorded')
    notes       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date']
        verbose_name = 'Supplier Payment'

    def __str__(self):
        return f'{self.payable.supplier.name} — ETB {self.amount} on {self.payment_date}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.payable.recalculate_amount_paid()


class PharmacyCreditSettings(models.Model):
    """Singleton (pk=1) — the one configurable value the credit-due/overdue
    dashboard alerts need."""
    reminder_days_before_due = models.PositiveIntegerField(default=7)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pharmacy_credit_settings_updates',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pharmacy Credit Settings'
        verbose_name_plural = 'Pharmacy Credit Settings'

    def __str__(self):
        return f'Pharmacy Credit Settings (remind {self.reminder_days_before_due}d before due)'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class PricingSettings(models.Model):
    """Singleton (pk=1) — the global default markup used to auto-calculate
    Selling Price from Purchase Price across Pharmacy (Medication /
    MedicationBatch) and Inventory (InventoryItem). Admin-editable without a
    code change. Changing this value does not retroactively touch existing
    records' selling prices — see the explicit 'recalculate existing prices'
    action, which only touches records that were never manually overridden."""
    default_markup_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('25.00'))
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pricing_settings_updates',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pricing Settings'
        verbose_name_plural = 'Pricing Settings'

    def __str__(self):
        return f'Pricing Settings ({self.default_markup_percent}% default markup)'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def compute_selling_price(self, purchase_price):
        purchase_price = Decimal(purchase_price or 0)
        markup = (purchase_price * self.default_markup_percent / Decimal('100')).quantize(Decimal('0.01'))
        return (purchase_price + markup).quantize(Decimal('0.01'))


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

    # Item Master link — the single source of truth for master/catalog data
    # (name, code, category, units, barcode, manufacturer, supplier, pricing,
    # stock levels, storage, active flag). Every field below with a matching
    # @property further down used to be a real column here; it's now read
    # (never written) through this relation so there is exactly one place
    # the data lives. Properties are getter-only on purpose — any code that
    # still tries `med.purchase_price = X` fails loudly instead of silently
    # writing to a column that no longer exists.
    inventory_item      = models.OneToOneField(
        'InventoryItem', on_delete=models.CASCADE, related_name='medication_details',
    )
    atc_code            = models.CharField(max_length=20, blank=True)

    # Basic (Medication Details — brand_name is deliberately independent of
    # inventory_item.name, not delegated, per product decision)
    brand_name          = models.CharField(max_length=200)
    generic_name        = models.CharField(max_length=200)
    scientific_name     = models.CharField(max_length=200, blank=True)
    therapeutic_class   = models.CharField(max_length=100, blank=True)
    drug_type           = models.CharField(max_length=20, choices=DrugType.choices, default=DrugType.TABLET)
    strength            = models.CharField(max_length=100)          # e.g. 500 mg
    dosage_form         = models.CharField(max_length=100, blank=True)
    route               = models.CharField(max_length=20, choices=Route.choices, default=Route.ORAL)
    country_of_origin   = models.CharField(max_length=100, blank=True)

    # Packaging (not on InventoryItem — kept as genuine, non-duplicated detail)
    pack_description    = models.CharField(max_length=200, blank=True)  # e.g. 1 Box = 10 Strips × 10 Tablets
    units_per_pack      = models.PositiveIntegerField(default=1)

    # Pricing (not on InventoryItem — kept as genuine, non-duplicated detail)
    wholesale_price     = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    insurance_price     = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Regulatory / clinical
    registration_number = models.CharField(max_length=100, blank=True)
    regulatory_approval = models.CharField(max_length=100, blank=True)
    controlled_substance = models.BooleanField(default=False)
    prescription_required = models.BooleanField(default=True)
    storage_condition   = models.CharField(max_length=20, choices=StorageCondition.choices, default=StorageCondition.ROOM_TEMP)

    # Meta
    notes       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['brand_name']

    def __str__(self):
        return f"{self.brand_name} — {self.strength}"

    # ── Item Master delegation (read-only) ──────────────────────────────────
    # Note: `name` intentionally aliases brand_name (a genuine, independently
    # stored field — see the class docstring), NOT inventory_item.name. Every
    # existing call site (templates, reports, search results) has always
    # meant "this medication's own display name" by `med.name`; brand_name is
    # exactly that, kept independent of whatever the Item Master's own name
    # gets edited to later via the Inventory module.
    @property
    def name(self):
        return self.brand_name

    @property
    def code(self):
        return self.inventory_item.item_code

    @property
    def barcode(self):
        return self.inventory_item.barcode

    @property
    def category(self):
        return self.inventory_item.category

    @property
    def category_id(self):
        return self.inventory_item.category_id

    @property
    def manufacturer(self):
        return self.inventory_item.manufacturer

    @property
    def supplier(self):
        return self.inventory_item.supplier

    @property
    def supplier_id(self):
        return self.inventory_item.supplier_id

    @property
    def unit_of_measure(self):
        return self.inventory_item.unit

    @property
    def purchase_unit(self):
        return self.inventory_item.unit_purchase

    @property
    def dispensing_unit(self):
        return self.inventory_item.dispensing_unit

    @property
    def conversion_factor(self):
        return self.inventory_item.consumption_factor

    @property
    def purchase_price(self):
        return self.inventory_item.unit_cost

    @property
    def selling_price(self):
        return self.inventory_item.selling_price

    @property
    def selling_price_is_manual(self):
        return self.inventory_item.selling_price_is_manual

    @property
    def minimum_stock(self):
        return self.inventory_item.min_stock

    @property
    def maximum_stock(self):
        return self.inventory_item.max_stock

    @property
    def reorder_level(self):
        return self.inventory_item.reorder_level

    @property
    def reorder_quantity(self):
        return self.inventory_item.reorder_quantity

    @property
    def safety_stock(self):
        return self.inventory_item.safety_stock

    @property
    def location(self):
        return self.inventory_item.storage_location

    @property
    def location_id(self):
        return self.inventory_item.storage_location_id

    @property
    def is_active(self):
        return self.inventory_item.is_active

    # ── Stock (unchanged — reads self.batches + the delegated properties above) ──
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
    class Ownership(models.TextChoices):
        PURCHASED   = 'purchased',   'Purchased'
        CONSIGNMENT = 'consignment', 'Consignment'

    class BatchStatus(models.TextChoices):
        ACTIVE    = 'active',    'Active'
        QUARANTINE = 'quarantine', 'Quarantined'
        RECALLED  = 'recalled',  'Recalled'

    medication          = models.ForeignKey(Medication, on_delete=models.CASCADE, related_name='batches')
    batch_number        = models.CharField(max_length=100)
    lot_number          = models.CharField(max_length=100, blank=True)
    manufacturing_date  = models.DateField(null=True, blank=True)
    expiration_date     = models.DateField()
    quantity_received   = models.PositiveIntegerField()
    quantity_available  = models.PositiveIntegerField()
    purchase_price      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    selling_price       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    selling_price_is_manual = models.BooleanField(default=False)
    supplier            = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL, related_name='batches')
    location            = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.SET_NULL, related_name='batches')
    received_date       = models.DateField(null=True, blank=True)
    received_by         = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='batches_received')
    purchase_order_ref  = models.CharField(max_length=100, blank=True)
    invoice_number      = models.CharField(max_length=100, blank=True)
    goods_receipt        = models.ForeignKey(GoodsReceipt, null=True, blank=True, on_delete=models.SET_NULL, related_name='batches')
    ownership           = models.CharField(max_length=15, choices=Ownership.choices, default=Ownership.PURCHASED)
    status              = models.CharField(max_length=15, choices=BatchStatus.choices, default=BatchStatus.ACTIVE)
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
    requires_ward_supervisor_approval = models.BooleanField(
        default=False, help_text='Requests from this store need a Ward Supervisor sign-off before pharmacy/store review.',
    )
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['store_type', 'name']

    def __str__(self):
        return self.name

    @property
    def low_stock_items(self):
        return [s for s in self.stock_items.select_related('medication', 'inventory_item') if s.is_low_stock]

    @property
    def out_of_stock_items(self):
        return [s for s in self.stock_items.select_related('medication', 'inventory_item') if s.is_out_of_stock]

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
        return self.transfer_requests.filter(status__in=[
            TransferRequest.Status.PENDING_WARD_APPROVAL, TransferRequest.Status.PENDING,
        ])


class DepartmentStock(models.Model):
    """Current stock level for one medication OR one general-inventory
    consumable in one department store — exactly one of `medication` /
    `inventory_item` is set per row, letting wards requisition and track
    both drugs and ward supplies (syringes, catheters, ...) through the
    same store/request/transfer pipeline."""
    department_store   = models.ForeignKey(DepartmentStore, on_delete=models.CASCADE, related_name='stock_items')
    medication         = models.ForeignKey(Medication, on_delete=models.CASCADE, related_name='dept_stocks', null=True, blank=True)
    inventory_item      = models.ForeignKey(
        'InventoryItem', on_delete=models.CASCADE, related_name='dept_stocks', null=True, blank=True,
    )
    quantity_available = models.IntegerField(default=0)
    minimum_quantity   = models.IntegerField(default=10)
    last_updated       = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['department_store', 'medication'], condition=models.Q(medication__isnull=False),
                name='uniq_dept_stock_medication',
            ),
            models.UniqueConstraint(
                fields=['department_store', 'inventory_item'], condition=models.Q(inventory_item__isnull=False),
                name='uniq_dept_stock_inventory_item',
            ),
        ]

    def __str__(self):
        item_name = self.medication.name if self.medication_id else self.inventory_item.name
        return f"{self.department_store.name} — {item_name}: {self.quantity_available}"

    @property
    def item_name(self):
        return self.medication.name if self.medication_id else self.inventory_item.name

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
    """A department's request for medications and/or consumables from the
    main pharmacy or store."""

    class Status(models.TextChoices):
        PENDING_WARD_APPROVAL = 'pending_ward_approval', 'Pending Ward Supervisor Approval'
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
    status            = models.CharField(max_length=25, choices=Status.choices, default=Status.PENDING)
    priority          = models.CharField(max_length=15, choices=Priority.choices, default=Priority.NORMAL)
    requested_by      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='transfer_requests_made')
    approved_by       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='transfer_requests_approved')
    request_date      = models.DateTimeField(auto_now_add=True)
    required_by       = models.DateField(null=True, blank=True)
    approval_date     = models.DateTimeField(null=True, blank=True)
    fulfillment_date  = models.DateTimeField(null=True, blank=True)
    notes             = models.TextField(blank=True)
    rejection_reason  = models.TextField(blank=True)

    # Optional ward-supervisor pre-approval stage — only used when
    # requesting_store.requires_ward_supervisor_approval is True.
    ward_supervisor_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transfer_requests_ward_approved',
    )
    ward_supervisor_approved_at = models.DateTimeField(null=True, blank=True)

    # Patient-specific requisition fields
    class RequestType(models.TextChoices):
        DEPT_STOCK       = 'dept_stock',       'Department Stock Request'
        PATIENT_SPECIFIC = 'patient_specific', 'Patient-Specific Request'

    request_type      = models.CharField(max_length=20, choices=RequestType.choices, default=RequestType.DEPT_STOCK)
    patient           = models.ForeignKey('Patient', null=True, blank=True, on_delete=models.SET_NULL, related_name='requisitions')
    visit             = models.ForeignKey('Visit',   null=True, blank=True, on_delete=models.SET_NULL, related_name='requisitions')
    requesting_doctor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='requisitions_as_doctor')
    clinical_reason   = models.TextField(blank=True)
    invoice           = models.ForeignKey('Invoice', null=True, blank=True, on_delete=models.SET_NULL, related_name='requisitions')

    class Meta:
        ordering = ['-request_date']

    def __str__(self):
        return f"{self.request_number} — {self.requesting_store.name}"

    @property
    def priority_color(self):
        return {'normal': 'blue', 'urgent': 'amber', 'emergency': 'red'}.get(self.priority, 'slate')


class TransferRequestItem(models.Model):
    """One medication OR consumable line in a transfer request — exactly
    one of `medication` / `inventory_item` is set."""
    transfer_request   = models.ForeignKey(TransferRequest, on_delete=models.CASCADE, related_name='items')
    medication         = models.ForeignKey(Medication, on_delete=models.CASCADE, null=True, blank=True)
    inventory_item      = models.ForeignKey('InventoryItem', on_delete=models.CASCADE, null=True, blank=True)
    quantity_requested = models.IntegerField()
    quantity_approved  = models.IntegerField(null=True, blank=True)
    quantity_issued    = models.IntegerField(default=0)
    notes              = models.CharField(max_length=200, blank=True)
    unit_price         = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_billable        = models.BooleanField(default=True)
    invoice_item       = models.ForeignKey('InvoiceItem', null=True, blank=True, on_delete=models.SET_NULL, related_name='requisition_items')

    def __str__(self):
        name = self.medication.name if self.medication_id else self.inventory_item.name
        return f"{name} × {self.quantity_requested}"

    @property
    def item_name(self):
        return self.medication.name if self.medication_id else self.inventory_item.name


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
    """One medication OR consumable line in a department transfer.
    Consumables generally have no batch/expiry, hence those fields are
    nullable — medication lines still populate them as before."""
    transfer             = models.ForeignKey(DepartmentTransfer, on_delete=models.CASCADE, related_name='items')
    medication           = models.ForeignKey(Medication, on_delete=models.CASCADE, null=True, blank=True)
    inventory_item        = models.ForeignKey('InventoryItem', on_delete=models.CASCADE, null=True, blank=True)
    source_batch         = models.ForeignKey(MedicationBatch, null=True, blank=True, on_delete=models.SET_NULL)
    dept_batch           = models.ForeignKey(DepartmentStockBatch, null=True, blank=True, on_delete=models.SET_NULL)
    batch_number         = models.CharField(max_length=100, blank=True)
    expiration_date      = models.DateField(null=True, blank=True)
    quantity_transferred = models.IntegerField()
    unit_cost            = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        name = self.medication.name if self.medication_id else self.inventory_item.name
        return f"{name} × {self.quantity_transferred}"


class DepartmentUsage(models.Model):
    """Records medication OR consumable usage / consumption within a
    department. When `inventory_item` is set and billable, `invoice_item`
    links the billing line created for the patient."""

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
    medication        = models.ForeignKey(Medication, on_delete=models.CASCADE, related_name='dept_usages', null=True, blank=True)
    inventory_item     = models.ForeignKey(
        'InventoryItem', on_delete=models.CASCADE, related_name='dept_usages', null=True, blank=True,
    )
    dept_batch        = models.ForeignKey(DepartmentStockBatch, null=True, blank=True, on_delete=models.SET_NULL)
    quantity_used     = models.IntegerField()
    usage_type        = models.CharField(max_length=20, choices=UsageType.choices)
    patient           = models.ForeignKey('Patient', null=True, blank=True, on_delete=models.SET_NULL)
    visit             = models.ForeignKey('Visit', null=True, blank=True, on_delete=models.SET_NULL)
    invoice_item      = models.ForeignKey(
        'InvoiceItem', null=True, blank=True, on_delete=models.SET_NULL, related_name='dept_usage',
    )
    responsible_staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='dept_usages_performed')
    usage_date        = models.DateTimeField()
    reason            = models.CharField(max_length=200, blank=True)
    notes             = models.TextField(blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-usage_date']

    def __str__(self):
        name = self.medication.name if self.medication_id else self.inventory_item.name
        return f"{self.usage_number} — {name} @ {self.department_store.name}"

    @property
    def item_name(self):
        return self.medication.name if self.medication_id else self.inventory_item.name


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
            ('view_lab_reports',          'Can view laboratory management reports'),

            # Imaging / Radiology
            ('request_imaging',           'Can request imaging studies'),
            ('read_imaging_request',      'Can read imaging requests'),
            ('process_imaging',           'Can process imaging studies'),
            ('write_imaging_report',      'Can write imaging reports'),
            ('read_imaging_report',       'Can read imaging reports'),
            ('manage_imaging_services',   'Can manage imaging service catalogue and pricing'),

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
            ('request_medication_transfer','Can request medication/consumable transfers'),
            ('approve_medication_transfer','Can approve / fulfill medication/consumable transfers'),
            ('ward_supervisor_approve',    'Can give ward-supervisor pre-approval on transfer requests'),
            ('record_dept_usage',          'Can record medication/consumable usage in department'),
            ('view_dept_reports',          'Can view department pharmacy reports'),
            ('view_patient_requisitions',  'Can view patient-specific requisitions'),
            ('manage_patient_requisitions','Can create and manage patient-specific requisitions'),

            # Nursing Module
            ('view_nursing_dashboard',     'Can view the nursing dashboard'),

            # Medication Inventory (catalog & batches)
            ('manage_med_catalog',    'Can manage medication catalog'),
            ('receive_stock',         'Can receive stock / create batches'),
            ('manage_suppliers',      'Can manage suppliers'),
            ('view_inv_reports',      'Can view inventory reports'),
            ('export_inv_reports',    'Can export inventory reports'),
            ('manage_supplier_payments', 'Can record payments against supplier payables'),

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
            ('manage_inventory_periods',  'Can open and close inventory periods (annual/quarterly/monthly)'),

            # HR
            ('read_employee',             'Can read employee records'),
            ('manage_employees',          'Can manage employee records'),
            ('manage_attendance',         'Can manage attendance'),
            ('manage_payroll',            'Can manage payroll'),
            ('manage_employee_signatures', 'Can upload/replace signatures for any employee'),
            ('delete_employee_signature',  'Can remove (deactivate) an employee signature'),

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
            ('manage_system_config',      'Can manage hospital profile and module settings'),
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
            ('write_postop_note',              'Can write post-operative notes'),
            ('read_postop_note',               'Can read post-operative notes'),
            ('add_periop_nursing_note',        'Can add nursing addenda to perioperative documents'),
            ('read_periop_document_history',   'Can view perioperative document version history'),
            ('counsel_surgery_patient',        'Can counsel patients about surgery cost and record patient decisions'),
            ('process_surgery_booking_deposit', 'Can process and record surgery booking deposits'),
            ('collect_surgery_pre_deposit',    'Can collect pre-surgery deposit when patient arrives'),
            ('settle_surgery_account',         'Can settle surgery account at discharge'),
            ('initiate_surgery_admission',     'Can initiate inpatient admission for surgical patients'),

            # Facility Management
            ('manage_facilities',        'Can add, edit, and delete buildings, wards, rooms, beds, and OR configuration'),
            ('view_facility',            'Can view facility lists and dashboard'),
            ('assign_bed',               'Can admit, transfer, and discharge patients to/from beds'),
            ('view_facility_reports',    'Can view facility management reports'),

            # Admission Management
            ('request_admission',        'Can create formal admission requests'),
            ('review_admission_request',  'Can approve or reject pending admission requests'),
            ('view_admission_dashboard', 'Can view the admission dashboard'),
            ('view_admission_reports',   'Can view admission management reports'),
            ('manage_deposit_rules',     'Can create and edit admission deposit rules'),
            ('view_inpatient_deposit',   'Can view inpatient deposit accounts and balances'),
            ('manage_inpatient_deposit', 'Can add deposits, adjustments, and process reconciliation'),
            ('view_deposit_reports',     'Can view inpatient deposit reports'),

            # Patient Attachment & Document Management
            ('upload_attachment',        'Can upload patient documents/attachments'),
            ('view_attachments',         'Can view patient documents/attachments'),
            ('view_confidential_attachments', 'Can view documents marked confidential'),
            ('replace_attachment',       'Can replace an attachment with a new version'),
            ('delete_attachment',        'Can delete (soft-delete) a patient attachment'),
            ('restore_attachment',       'Can restore a deleted patient attachment'),
            ('manage_attachment_categories', 'Can create, edit, and deactivate attachment categories'),
            ('view_attachment_reports',  'Can view attachment/document management reports'),

            # Card & Consultation Type Management
            ('manage_card_types',        'Can create, edit, and deactivate card types and consultation types'),
            ('override_card_expiry',     'Can waive the card renewal fee for an expired/missing card'),
            ('view_card_reports',        'Can view card and consultation type reports'),

            # Specialization Management
            ('manage_specializations',   'Can create, edit, and deactivate medical specializations'),
            ('manage_doctors',           'Can create and edit doctor staff records'),
            ('view_specialization_reports', 'Can view specialization reports'),

            # OB/GYN Module
            ('view_obgyn_dashboard',        'Can view the OB/GYN module dashboard'),
            ('manage_pregnancy',            'Can register and manage pregnancy records'),
            ('view_pregnancy',              'Can view pregnancy records'),
            ('manage_anc_visit',            'Can record and edit ANC visits'),
            ('view_anc_visit',              'Can view ANC visit records'),
            ('manage_delivery',             'Can record and edit delivery records'),
            ('view_delivery',               'Can view delivery records'),
            ('manage_newborn',              'Can record and edit newborn records'),
            ('view_newborn',                'Can view newborn records'),
            ('manage_pnc_visit',            'Can record and edit PNC visits'),
            ('view_pnc_visit',              'Can view PNC visit records'),
            ('manage_gyn_consultation',     'Can create and edit gynecology consultations'),
            ('view_gyn_consultation',       'Can view gynecology consultations'),
            ('manage_family_planning',      'Can record and manage family planning visits'),
            ('view_family_planning',        'Can view family planning records'),
            ('manage_infertility_case',     'Can open and manage infertility cases'),
            ('view_infertility_case',       'Can view infertility cases'),
            ('view_obgyn_reports',          'Can view OB/GYN reports'),
            ('export_obgyn_reports',        'Can export OB/GYN reports'),

            # Internal Medicine Module
            ('view_im_dashboard',           'Can view Internal Medicine dashboard'),
            ('manage_im_consultation',      'Can create and edit Internal Medicine consultations'),
            ('view_im_consultation',        'Can view Internal Medicine consultations'),
            ('manage_chronic_plan',         'Can create and edit chronic disease plans'),
            ('view_chronic_plan',           'Can view chronic disease plans'),
            ('manage_risk_assessment',      'Can create and edit clinical risk assessments'),
            ('view_im_reports',             'Can view Internal Medicine reports'),
            ('export_im_reports',           'Can export Internal Medicine reports'),

            # Pediatrics Module
            ('view_peds_dashboard',         'Can view Pediatrics dashboard'),
            ('manage_peds_consultation',    'Can create and edit pediatric consultations'),
            ('view_peds_consultation',      'Can view pediatric consultations'),
            ('manage_growth_record',        'Can record and edit growth records'),
            ('view_growth_record',          'Can view growth records'),
            ('manage_immunization',         'Can record and edit immunization records'),
            ('view_immunization',           'Can view immunization records'),
            ('manage_dev_assessment',       'Can create and edit developmental assessments'),
            ('view_peds_reports',           'Can view Pediatrics reports'),
            ('export_peds_reports',         'Can export Pediatrics reports'),

            # General Surgery Module
            ('view_surgery_consult_dashboard', 'Can view General Surgery dashboard'),
            ('manage_surgical_consultation',   'Can create and edit surgical consultations'),
            ('view_surgical_consultation',     'Can view surgical consultations'),
            ('manage_preop_assessment',        'Can complete pre-operative assessments'),
            ('manage_operative_note',          'Can write and edit operative notes'),
            ('view_operative_note',            'Can view operative notes'),
            ('manage_postop_note',             'Can write and edit post-operative notes'),
            ('view_postop_note',               'Can view post-operative notes'),
            ('manage_wound_followup',          'Can record wound follow-up visits'),
            ('view_surgery_reports',           'Can view General Surgery reports'),
            ('export_surgery_reports',         'Can export General Surgery reports'),

            # Cardiology Module
            ('view_cardiology_dashboard',      'Can view Cardiology dashboard'),
            ('manage_cardiology_consultation', 'Can create and edit Cardiology consultations'),
            ('view_cardiology_consultation',   'Can view Cardiology consultations'),
            ('manage_ecg_record',              'Can create and edit ECG records'),
            ('view_ecg_record',                'Can view ECG records'),
            ('manage_echo_report',             'Can create and edit Echo reports'),
            ('view_echo_report',               'Can view Echo reports'),
            ('manage_cardiac_procedure',       'Can record cardiac procedures'),
            ('view_cardiology_reports',        'Can view Cardiology reports'),
            ('export_cardiology_reports',      'Can export Cardiology reports'),

            # Specialty Assignment & Referrals
            ('manage_specialty_referral',      'Can create and manage specialty referrals'),
            ('view_specialty_referral',        'Can view specialty referrals'),
            ('accept_specialty_referral',      'Can accept incoming specialty referrals'),
            ('view_specialty_reports',         'Can view specialty assignment reports'),
            ('export_specialty_reports',       'Can export specialty reports'),

            # Medical / Death Certificate Management
            ('write_medical_certificate',    'Can create and edit medical certificates'),
            ('finalize_medical_certificate', 'Can finalize and sign medical certificates'),
            ('read_medical_certificate',     'Can view and print medical certificates'),
            ('void_medical_certificate',     'Can void a finalized medical certificate'),
            ('write_death_certificate',      'Can create and edit death certificates'),
            ('finalize_death_certificate',   'Can finalize and sign death certificates'),
            ('read_death_certificate',       'Can view and print death certificates'),
            ('void_death_certificate',       'Can void a finalized death certificate'),
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
        FACILITY      = 'facility',      'Facility Management'
        CARD_MANAGEMENT = 'card_management', 'Card & Consultation Type Management'
        ADMISSION     = 'admission',     'Admission Management'
        DOCUMENT      = 'document',      'Document / Attachment'
        MEDICAL_CERTIFICATE = 'medical_certificate', 'Medical Certificate'
        DEATH_CERTIFICATE   = 'death_certificate',   'Death Certificate'

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


# ── Medical / Death Certificate Module ────────────────────────────────────────

class CertificateDocumentBase(models.Model):
    """Shared versioning/audit/signature scaffolding for legal clinical
    certificates (Medical Certificate, Death Certificate) — the same
    draft → finalize → immutable-version pattern used by PeriopDocumentBase
    for perioperative documentation, plus an explicit Void action since a
    finalized certificate issued in error needs to be flagged invalid
    without silently disappearing or being overwritten."""

    class DocStatus(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        FINALIZED = 'finalized', 'Finalized'

    certificate_number = models.CharField(max_length=30, unique=True, blank=True)
    verification_code  = models.CharField(max_length=20, unique=True, blank=True)

    version          = models.PositiveSmallIntegerField(default=1)
    is_current       = models.BooleanField(default=True, db_index=True)
    doc_status       = models.CharField(max_length=10, choices=DocStatus.choices, default=DocStatus.DRAFT)
    supersedes       = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='%(class)s_superseded_by',
    )
    revision_reason  = models.TextField(blank=True)

    is_void      = models.BooleanField(default=False)
    voided_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='%(class)s_voided',
    )
    voided_at    = models.DateTimeField(null=True, blank=True)
    void_reason  = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='%(class)s_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='%(class)s_updated',
    )
    updated_at = models.DateTimeField(auto_now=True)

    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='%(class)s_finalized',
    )
    finalized_at   = models.DateTimeField(null=True, blank=True)
    signature_name = models.CharField(max_length=200, blank=True)

    class Meta:
        abstract = True

    @property
    def is_finalized(self):
        return self.doc_status == self.DocStatus.FINALIZED


class MedicalCertificate(CertificateDocumentBase):
    class Fitness(models.TextChoices):
        FIT_WORK_SCHOOL       = 'fit_work_school',       'Fit for Work / School'
        FIT_WITH_RESTRICTIONS = 'fit_with_restrictions', 'Fit with Restrictions'
        UNFIT_TEMPORARY       = 'unfit_temporary',       'Temporarily Unfit'
        UNFIT                 = 'unfit',                 'Unfit'

    visit   = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='medical_certificates')
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='medical_certificates')

    date_of_examination = models.DateField()
    medical_findings     = models.TextField(blank=True)
    diagnosis             = models.TextField(blank=True)

    rest_from  = models.DateField(null=True, blank=True)
    rest_to    = models.DateField(null=True, blank=True)
    days_off   = models.PositiveIntegerField(null=True, blank=True)

    fitness_status    = models.CharField(max_length=25, choices=Fitness.choices, default=Fitness.FIT_WORK_SCHOOL)
    work_restrictions = models.TextField(blank=True)

    follow_up_date = models.DateField(null=True, blank=True)
    remarks        = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Medical Certificate'

    def __str__(self):
        return f"Medical Certificate {self.certificate_number or '(draft)'} — {self.patient}"


class DeathCertificate(CertificateDocumentBase):
    class MannerOfDeath(models.TextChoices):
        NATURAL                = 'natural',                'Natural'
        ACCIDENT               = 'accident',                'Accident'
        SUICIDE                = 'suicide',                 'Suicide'
        HOMICIDE                = 'homicide',                'Homicide'
        PENDING_INVESTIGATION  = 'pending_investigation',    'Pending Investigation'
        OTHER                  = 'other',                    'Other'

    visit             = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='death_certificates')
    patient           = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='death_certificates')
    admission         = models.ForeignKey(
        'Admission', on_delete=models.SET_NULL, null=True, blank=True, related_name='death_certificates',
    )
    discharge_summary = models.ForeignKey(
        DischargeSummary, on_delete=models.SET_NULL, null=True, blank=True, related_name='death_certificates',
    )

    date_of_death  = models.DateField()
    time_of_death  = models.TimeField()
    place_of_death = models.CharField(max_length=200, blank=True)
    ward           = models.CharField(max_length=100, blank=True)
    room           = models.CharField(max_length=100, blank=True)
    bed            = models.CharField(max_length=100, blank=True)

    immediate_cause         = models.TextField()
    underlying_cause        = models.TextField(blank=True)
    contributing_conditions = models.TextField(blank=True)
    manner_of_death         = models.CharField(max_length=25, choices=MannerOfDeath.choices, default=MannerOfDeath.NATURAL)
    duration_of_illness     = models.CharField(max_length=100, blank=True)

    attending_physician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='death_certificates_attended',
    )
    clinical_notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Death Certificate'

    def __str__(self):
        return f"Death Certificate {self.certificate_number or '(draft)'} — {self.patient}"


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
        GENERAL     = 'general',     'General Surgery'
        CARDIAC     = 'cardiac',     'Cardiac / Thoracic'
        ORTHOPEDIC  = 'orthopedic',  'Orthopedic'
        NEURO       = 'neuro',       'Neurosurgery'
        EYE         = 'eye',         'Ophthalmic'
        GYNECOLOGY  = 'gynecology', 'Gynecology'
        ENT         = 'ent',         'ENT'
        EMERGENCY   = 'emergency',  'Emergency OR'
        PROCEDURE   = 'procedure',  'Procedure Room'
        OTHER       = 'other',      'Other'

    class Availability(models.TextChoices):
        AVAILABLE   = 'available',   'Available'
        MAINTENANCE = 'maintenance', 'Under Maintenance'
        CLOSED      = 'closed',      'Closed'

    name      = models.CharField(max_length=50, unique=True)
    code      = models.CharField(max_length=20, blank=True)
    room_type = models.CharField(
        max_length=15, choices=RoomType.choices, default=RoomType.GENERAL,
    )
    department = models.ForeignKey('Department', on_delete=models.SET_NULL, null=True, blank=True, related_name='or_rooms')
    building   = models.ForeignKey('Building', on_delete=models.SET_NULL, null=True, blank=True, related_name='or_rooms')
    floor      = models.ForeignKey('Floor', on_delete=models.SET_NULL, null=True, blank=True, related_name='or_rooms')
    location  = models.CharField(max_length=100, blank=True)
    capacity  = models.PositiveSmallIntegerField(default=1)
    equipment = models.TextField(blank=True)
    notes     = models.TextField(blank=True)
    # `availability_status` is the new richer 3-state field the Facility
    # module surfaces; `is_active` is kept in sync in save() below so every
    # pre-existing `is_active` check elsewhere (surgery scheduling, OR
    # dashboards) keeps working unmodified.
    availability_status = models.CharField(max_length=15, choices=Availability.choices, default=Availability.AVAILABLE)
    daily_open_time  = models.TimeField(null=True, blank=True)
    daily_close_time = models.TimeField(null=True, blank=True)
    max_surgeries_per_day = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'OR Room'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.is_active = (self.availability_status == self.Availability.AVAILABLE)
        super().save(*args, **kwargs)


class SurgeryOrder(models.Model):
    class Status(models.TextChoices):
        ORDERED            = 'ordered',            'Ordered'
        PENDING_REVIEW     = 'pending_review',     'Pending Review'
        APPROVED           = 'approved',           'Approved'
        SCHEDULED          = 'scheduled',          'Scheduled'
        PATIENT_PREPARED   = 'patient_prepared',   'Patient Prepared'
        IN_OR              = 'in_or',              'In Operating Room'
        COMPLETED          = 'completed',          'Completed'
        POST_OP            = 'post_op',            'Post-operative Care'
        CANCELLED          = 'cancelled',          'Cancelled'
        # Surgery booking workflow statuses
        AWAITING_DECISION  = 'awaiting_decision',  'Awaiting Patient Decision'
        BOOKING_DEPOSIT    = 'booking_deposit',    'Booking Deposit Pending'
        AWAITING_ADMISSION = 'awaiting_admission', 'Awaiting Inpatient Admission'
        RECOVERY           = 'recovery',           'Recovery'
        DISCHARGED         = 'discharged',         'Discharged'

    class PatientDecision(models.TextChoices):
        PENDING  = 'pending',  'Pending'
        AGREED   = 'agreed',   'Agreed'
        DECLINED = 'declined', 'Declined'

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

    class PaymentStatus(models.TextChoices):
        PENDING_PAYMENT = 'Pending Payment', 'Pending Payment'
        PAID            = 'Paid',            'Paid'
        PARTIAL         = 'Partial',         'Partially Paid'
        CREDIT          = 'Credit',          'Credit'
        WAIVED          = 'Waived',          'Waived'

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

    # Estimated Costs (set at order creation, editable until billing)
    estimated_procedure_fee  = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    estimated_surgeon_fee    = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    estimated_anesthesia_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    estimated_facility_fee   = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))

    @property
    def estimated_total(self):
        return (
            self.estimated_procedure_fee + self.estimated_surgeon_fee
            + self.estimated_anesthesia_fee + self.estimated_facility_fee
        )

    # Billing
    invoice = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_orders',
    )
    # Core procedure charge (procedure/surgeon/anesthesia/facility fees bundled
    # as one line item) — gates OR progression independently of consumables,
    # which are billed/gated per-item via SurgeryConsumable.invoice_item.
    invoice_item = models.OneToOneField(
        'InvoiceItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='surgery_order',
    )
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING_PAYMENT,
    )

    notes = models.TextField(blank=True)

    # ── Reception Counseling ─────────────────────────────────────────────────
    counseling_notes   = models.TextField(blank=True)
    counseling_done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_counselings',
    )
    counseling_done_at = models.DateTimeField(null=True, blank=True)

    # ── Patient Decision ─────────────────────────────────────────────────────
    patient_decision       = models.CharField(
        max_length=10, choices=PatientDecision.choices, default=PatientDecision.PENDING,
    )
    patient_decision_at    = models.DateTimeField(null=True, blank=True)
    patient_decision_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_patient_decisions',
    )
    patient_decline_reason = models.TextField(blank=True)

    # ── Surgery Booking Deposit ──────────────────────────────────────────────
    booking_deposit_amount       = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0'),
    )
    booking_deposit_invoice      = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_booking_deposit_orders',
    )
    booking_deposit_invoice_item = models.ForeignKey(
        'InvoiceItem', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_booking_deposit_order',
    )
    booking_deposit_paid    = models.BooleanField(default=False)
    booking_deposit_paid_at = models.DateTimeField(null=True, blank=True)

    # ── Admission Bridge ─────────────────────────────────────────────────────
    surgery_admission_request = models.ForeignKey(
        'AdmissionRequest', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_orders',
    )

    # ── Recovery / Discharge Timestamps ─────────────────────────────────────
    recovered_at          = models.DateTimeField(null=True, blank=True)
    discharged_surgery_at = models.DateTimeField(null=True, blank=True)

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
    def payment_cleared(self):
        if self.payment_status in (
            self.PaymentStatus.PAID, self.PaymentStatus.CREDIT, self.PaymentStatus.WAIVED,
        ):
            return True
        # No invoice item linked yet — billing hasn't been raised for this
        # surgery. Don't block OR entry; the charge will be settled post-op.
        if not self.invoice_item_id:
            return True
        # Fallback: read the linked invoice item directly in case the
        # post_save signal that syncs payment_status was swallowed.
        try:
            return self.invoice_item.payment_cleared
        except Exception:
            pass
        return False

    @property
    def can_approve(self):
        return self.status in (self.Status.ORDERED, self.Status.PENDING_REVIEW)

    @property
    def can_cancel(self):
        return self.status not in (
            self.Status.COMPLETED, self.Status.CANCELLED, self.Status.DISCHARGED,
        )

    @property
    def is_active(self):
        return self.status not in (
            self.Status.COMPLETED, self.Status.CANCELLED, self.Status.DISCHARGED,
        )

    @property
    def booking_deposit_cleared(self):
        if not self.booking_deposit_amount:
            return True
        return self.booking_deposit_paid

    @property
    def can_send_to_reception(self):
        return self.status in (
            self.Status.ORDERED, self.Status.PENDING_REVIEW, self.Status.APPROVED,
        )

    @property
    def can_schedule(self):
        return self.status == self.Status.APPROVED

    @property
    def current_anesthesia_record(self):
        return self.anesthesia_records.filter(is_current=True).first()

    @property
    def current_operative_note(self):
        return self.operative_notes.filter(is_current=True).first()

    @property
    def current_postop_note(self):
        return self.postop_notes.filter(is_current=True).first()


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


class PeriopDocumentBase(models.Model):
    """Shared versioning/audit scaffolding for the perioperative documentation
    set (Pre-Op Anesthetic Assessment, OR Operative Note, Post-Op Note).

    A finalized row is never mutated again — editing a finalized document
    creates a new row with version = old.version + 1, supersedes = old, and
    flips old.is_current to False, all inside one transaction. This is the
    only mechanism in the codebase that guarantees "no finalized clinical
    documentation is ever deleted or overwritten," per legal/audit
    requirements for perioperative records.
    """
    class DocStatus(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        FINALIZED = 'finalized', 'Finalized'

    version          = models.PositiveSmallIntegerField(default=1)
    is_current       = models.BooleanField(default=True, db_index=True)
    doc_status       = models.CharField(max_length=10, choices=DocStatus.choices, default=DocStatus.DRAFT)
    supersedes       = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='%(class)s_superseded_by',
    )
    revision_reason  = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='%(class)s_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='%(class)s_updated',
    )
    updated_at = models.DateTimeField(auto_now=True)

    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='%(class)s_finalized',
    )
    finalized_at   = models.DateTimeField(null=True, blank=True)
    signature_name = models.CharField(max_length=200, blank=True)

    class Meta:
        abstract = True

    @property
    def is_finalized(self):
        return self.doc_status == self.DocStatus.FINALIZED


class SurgeryAnesthesiaRecord(PeriopDocumentBase):
    class ASA(models.TextChoices):
        I   = 'ASA I',   'ASA I — Normal healthy patient'
        II  = 'ASA II',  'ASA II — Mild systemic disease'
        III = 'ASA III', 'ASA III — Severe systemic disease'
        IV  = 'ASA IV',  'ASA IV — Severe disease, constant threat to life'
        V   = 'ASA V',   'ASA V — Moribund patient'

    class Smoking(models.TextChoices):
        NEVER   = 'never',   'Never'
        FORMER  = 'former',  'Former'
        CURRENT = 'current', 'Current'
        UNKNOWN = 'unknown', 'Unknown'

    class Alcohol(models.TextChoices):
        NONE       = 'none',       'None'
        OCCASIONAL = 'occasional', 'Occasional'
        REGULAR    = 'regular',    'Regular'
        HEAVY      = 'heavy',      'Heavy'
        UNKNOWN    = 'unknown',    'Unknown'

    class Pregnancy(models.TextChoices):
        NA           = 'na',            'N/A'
        NOT_PREGNANT = 'not_pregnant',  'Not Pregnant'
        PREGNANT     = 'pregnant',      'Pregnant'
        UNKNOWN      = 'unknown',       'Unknown'

    class Mallampati(models.TextChoices):
        I   = 'I',   'Class I'
        II  = 'II',  'Class II'
        III = 'III', 'Class III'
        IV  = 'IV',  'Class IV'

    class NeckMobility(models.TextChoices):
        NORMAL   = 'normal',   'Normal'
        REDUCED  = 'reduced',  'Reduced'
        SEVERE   = 'severe',   'Severely Reduced'

    class Fitness(models.TextChoices):
        FIT             = 'fit',             'Fit for Surgery'
        FIT_PRECAUTIONS = 'fit_precautions', 'Fit with Precautions'
        NOT_FIT         = 'not_fit',         'Not Fit'
        DEFERRED        = 'deferred',        'Deferred'

    surgery_order    = models.ForeignKey(
        SurgeryOrder, on_delete=models.CASCADE, related_name='anesthesia_records',
    )

    # ── Medical History ──────────────────────────────────────────────────
    present_illness_history           = models.TextField(blank=True)
    past_medical_history              = models.TextField(blank=True)
    previous_surgeries_history        = models.TextField(blank=True)
    previous_anesthesia_history       = models.TextField(blank=True)
    previous_anesthesia_complications = models.TextField(blank=True)
    known_allergies                   = models.TextField(blank=True)  # drug & food allergies
    current_medications               = models.TextField(blank=True)
    chronic_diseases                  = models.TextField(blank=True)
    smoking_status      = models.CharField(max_length=10, choices=Smoking.choices, blank=True)
    smoking_pack_years   = models.PositiveIntegerField(null=True, blank=True)
    alcohol_use          = models.CharField(max_length=10, choices=Alcohol.choices, blank=True)
    alcohol_details       = models.TextField(blank=True)
    pregnancy_status      = models.CharField(max_length=15, choices=Pregnancy.choices, blank=True)

    # ── Physical Examination (snapshot at assessment time) ───────────────
    pe_bp_systolic       = models.PositiveSmallIntegerField(null=True, blank=True)
    pe_bp_diastolic      = models.PositiveSmallIntegerField(null=True, blank=True)
    pe_pulse             = models.PositiveSmallIntegerField(null=True, blank=True)
    pe_respiratory_rate  = models.PositiveSmallIntegerField(null=True, blank=True)
    pe_temperature       = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    pe_spo2              = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    pe_weight_kg         = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    pe_height_cm         = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    general_appearance   = models.TextField(blank=True)
    cvs_exam             = models.TextField(blank=True)
    respiratory_exam     = models.TextField(blank=True)
    neuro_exam           = models.TextField(blank=True)

    # ── Airway Assessment ─────────────────────────────────────────────────
    mallampati_class             = models.CharField(max_length=5, choices=Mallampati.choices, blank=True)
    mouth_opening                = models.CharField(max_length=100, blank=True)
    neck_mobility                = models.CharField(max_length=10, choices=NeckMobility.choices, blank=True)
    thyromental_distance_cm      = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    dentition_notes               = models.CharField(max_length=200, blank=True)
    difficult_airway_anticipated = models.BooleanField(default=False)
    difficult_airway_notes        = models.TextField(blank=True)

    # ── ASA Physical Status ───────────────────────────────────────────────
    asa_classification   = models.CharField(max_length=10, choices=ASA.choices, blank=True)
    pre_assessment_notes = models.TextField(blank=True)

    # ── Lab & Investigation Review ────────────────────────────────────────
    lab_review_cbc          = models.TextField(blank=True)
    lab_review_blood_group  = models.TextField(blank=True)
    lab_review_coagulation  = models.TextField(blank=True)
    lab_review_blood_sugar  = models.TextField(blank=True)
    lab_review_rft          = models.TextField(blank=True)
    lab_review_lft          = models.TextField(blank=True)
    lab_review_ecg          = models.TextField(blank=True)
    lab_review_cxr          = models.TextField(blank=True)
    lab_review_other        = models.TextField(blank=True)

    # ── Anesthesia Plan ────────────────────────────────────────────────────
    anesthesia_type_planned  = models.CharField(max_length=20, blank=True)
    anesthesia_technique     = models.CharField(max_length=100, blank=True)
    airway_management        = models.CharField(max_length=100, blank=True)
    monitoring_plan          = models.TextField(blank=True)
    blood_products_required  = models.CharField(max_length=200, blank=True)
    special_equipment_needed = models.TextField(blank=True)
    risk_assessment          = models.TextField(blank=True)
    pre_medication           = models.TextField(blank=True)
    npo_fasting_status       = models.CharField(max_length=150, blank=True)

    # ── Assessment Summary ─────────────────────────────────────────────────
    final_assessment    = models.TextField(blank=True)
    fitness_for_surgery = models.CharField(max_length=20, choices=Fitness.choices, blank=True)
    recommendations     = models.TextField(blank=True)
    assessment_comments  = models.TextField(blank=True)

    # ── Intraoperative (unchanged from earlier phase) ─────────────────────
    induction_agent      = models.CharField(max_length=100, blank=True)
    maintenance_agent    = models.CharField(max_length=100, blank=True)
    induction_time        = models.DateTimeField(null=True, blank=True)
    incision_time         = models.DateTimeField(null=True, blank=True)
    closure_time          = models.DateTimeField(null=True, blank=True)
    anesthesia_medications = models.JSONField(default=list)
    monitoring_notes      = models.TextField(blank=True)
    intraop_complications = models.TextField(blank=True)

    # ── Post-anesthesia (unchanged from earlier phase) ────────────────────
    extubation_time       = models.DateTimeField(null=True, blank=True)
    post_anesthesia_notes = models.TextField(blank=True)
    pacu_duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    pacu_complications    = models.TextField(blank=True)

    class Meta:
        ordering = ['-version']
        verbose_name = 'Surgery Anesthesia Record'
        constraints = [
            models.UniqueConstraint(
                fields=['surgery_order'], condition=models.Q(is_current=True),
                name='unique_current_anesthesia_record_per_order',
            ),
        ]

    def __str__(self):
        return f'Anesthesia: {self.surgery_order.order_number} (v{self.version})'

    @property
    def pre_op_bmi(self):
        if self.pe_weight_kg and self.pe_height_cm:
            height_m = float(self.pe_height_cm) / 100
            if height_m > 0:
                return round(float(self.pe_weight_kg) / (height_m ** 2), 1)
        return None


class OperativeNote(PeriopDocumentBase):
    class CountsCorrect(models.TextChoices):
        YES = 'yes', 'Yes'
        NO  = 'no',  'No'
        NA  = 'na',  'Not Applicable'

    class WoundClass(models.TextChoices):
        CLEAN              = 'clean',              'Clean'
        CLEAN_CONTAMINATED = 'clean_contaminated', 'Clean-Contaminated'
        CONTAMINATED       = 'contaminated',        'Contaminated'
        DIRTY_INFECTED     = 'dirty_infected',      'Dirty-Infected'

    surgery_order = models.ForeignKey(
        SurgeryOrder, on_delete=models.CASCADE, related_name='operative_notes',
    )
    procedure_performed = models.CharField(max_length=500)

    # ── Procedure Timing ───────────────────────────────────────────────────
    time_patient_entered_or = models.DateTimeField(null=True, blank=True)
    time_surgery_start      = models.DateTimeField(null=True, blank=True)
    time_surgery_end        = models.DateTimeField(null=True, blank=True)
    time_patient_left_or    = models.DateTimeField(null=True, blank=True)

    pre_op_diagnosis   = models.TextField()
    post_op_diagnosis  = models.TextField()
    anesthesia_type    = models.CharField(max_length=50, blank=True)
    # Team members: assistant surgeon(s) recorded as real users; scrub nurse /
    # circulating nurse / anesthetist are NOT duplicated here — they're read
    # from the linked SurgerySchedule / current anesthesia record instead.
    assistant_surgeons = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='operative_notes_assisted',
    )

    # ── Operative Details ──────────────────────────────────────────────────
    findings              = models.TextField()  # Surgical Findings
    incision_type          = models.CharField(max_length=200, blank=True)
    surgical_technique     = models.CharField(max_length=200, blank=True)
    procedure_steps        = models.TextField()
    complications          = models.TextField(blank=True)
    specimens_collected     = models.TextField(blank=True)
    implants_used           = models.TextField(blank=True)
    drains_placed           = models.TextField(blank=True)  # Drains Inserted
    blood_loss_ml            = models.PositiveIntegerField(null=True, blank=True)
    urine_output_ml          = models.PositiveIntegerField(null=True, blank=True)
    blood_transfusion_given  = models.BooleanField(default=False)
    blood_transfusion_details = models.TextField(blank=True)
    counts_correct           = models.CharField(max_length=5, choices=CountsCorrect.choices, blank=True)
    wound_classification      = models.CharField(max_length=20, choices=WoundClass.choices, blank=True)
    wound_closure             = models.CharField(max_length=200, blank=True)  # Closure Technique
    dressings_applied         = models.CharField(max_length=200, blank=True)

    # ── Procedure Narrative ─────────────────────────────────────────────────
    intraop_events      = models.TextField(blank=True)
    unexpected_findings = models.TextField(blank=True)
    recommendations     = models.TextField(blank=True)

    post_op_instructions = models.TextField(blank=True)

    class Meta:
        ordering = ['-version']
        verbose_name = 'Operative Note'
        constraints = [
            models.UniqueConstraint(
                fields=['surgery_order'], condition=models.Q(is_current=True),
                name='unique_current_operative_note_per_order',
            ),
        ]

    def __str__(self):
        return f'Op Note: {self.surgery_order.order_number} (v{self.version})'


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
    # Optional catalogue link — if set, recording this consumable deducts real
    # stock and logs an InventoryTransaction; leave blank for untracked items.
    inventory_item = models.ForeignKey(
        'InventoryItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='surgery_consumables',
    )
    item_name    = models.CharField(max_length=200)
    quantity     = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit         = models.CharField(max_length=30, default='unit')
    batch_number = models.CharField(max_length=50, blank=True)
    expiry_date  = models.DateField(null=True, blank=True)
    unit_cost    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_cost   = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Billing link — set once surgery_billing_generate creates the matching
    # invoice line item, so this specific consumable's payment status can be
    # tracked and gated independently from the rest of the surgery bill.
    invoice_item = models.OneToOneField(
        'InvoiceItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='surgery_consumable',
    )
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


class SurgeryPreDeposit(models.Model):
    """Pre-surgery deposit collected when the patient arrives for surgery (day before / day of).
    Covers procedure + all in-hospital charges during the stay. At discharge the total
    is reconciled: surplus is refunded, deficit is collected. Credit patients skip the
    upfront payment and are billed in full at discharge."""

    class Status(models.TextChoices):
        ACTIVE   = 'active',   'Active'
        SETTLED  = 'settled',  'Settled'
        REFUNDED = 'refunded', 'Refunded'

    surgery_order    = models.OneToOneField(
        SurgeryOrder, on_delete=models.PROTECT, related_name='pre_deposit',
    )
    patient          = models.ForeignKey(
        Patient, on_delete=models.PROTECT, related_name='surgery_pre_deposits',
    )
    is_credit        = models.BooleanField(default=False)
    deposit_amount   = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    credit_limit     = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    payment_method   = models.CharField(max_length=30, blank=True)
    reference_number = models.CharField(max_length=100, blank=True)
    invoice          = models.ForeignKey(
        'Invoice', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_pre_deposits',
    )
    invoice_item     = models.ForeignKey(
        'InvoiceItem', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_pre_deposit',
    )
    collected_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='surgery_pre_deposits_collected',
    )
    collected_at     = models.DateTimeField(auto_now_add=True)
    notes            = models.TextField(blank=True)
    credit_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_credits_approved',
    )
    credit_approved_at = models.DateTimeField(null=True, blank=True)
    status           = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    # Settlement (filled at discharge)
    total_charges    = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    balance_refund   = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    balance_due      = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    settlement_invoice = models.ForeignKey(
        'Invoice', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_settlements',
    )
    settled_at       = models.DateTimeField(null=True, blank=True)
    settled_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='surgery_pre_deposits_settled',
    )
    settlement_notes = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Surgery Pre-Deposit'

    def __str__(self):
        if self.is_credit:
            return f'Credit — {self.surgery_order.order_number} ({self.patient})'
        return f'Deposit ETB {self.deposit_amount:,.2f} — {self.surgery_order.order_number}'

    @property
    def running_balance(self):
        return self.deposit_amount - self.total_charges


class PostOperativeNote(PeriopDocumentBase):
    class Consciousness(models.TextChoices):
        ALERT        = 'alert',        'Alert'
        DROWSY       = 'drowsy',       'Drowsy'
        SEDATED      = 'sedated',      'Sedated'
        UNRESPONSIVE = 'unresponsive', 'Unresponsive'

    class Disposition(models.TextChoices):
        PACU      = 'pacu',      'Recovery Room (PACU)'
        ICU       = 'icu',       'ICU'
        WARD      = 'ward',      'Ward'
        HDU       = 'hdu',       'HDU'
        DISCHARGE = 'discharge', 'Discharge'

    surgery_order = models.ForeignKey(
        SurgeryOrder, on_delete=models.CASCADE, related_name='postop_notes',
    )

    # ── Patient Status ─────────────────────────────────────────────────────
    consciousness_level  = models.CharField(max_length=15, choices=Consciousness.choices, blank=True)
    pain_score           = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
    )
    po_bp_systolic       = models.PositiveSmallIntegerField(null=True, blank=True)
    po_bp_diastolic      = models.PositiveSmallIntegerField(null=True, blank=True)
    po_pulse             = models.PositiveSmallIntegerField(null=True, blank=True)
    po_respiratory_rate  = models.PositiveSmallIntegerField(null=True, blank=True)
    po_temperature       = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    po_spo2              = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    airway_status         = models.CharField(max_length=150, blank=True)
    o2_requirement        = models.CharField(max_length=150, blank=True)
    neuro_status          = models.TextField(blank=True)

    # ── Surgical Assessment ────────────────────────────────────────────────
    wound_condition               = models.TextField(blank=True)
    drain_status                  = models.TextField(blank=True)
    catheters_present             = models.TextField(blank=True)
    bleeding_assessment           = models.TextField(blank=True)
    post_op_complications         = models.TextField(blank=True)
    immediate_post_op_diagnosis   = models.TextField(blank=True)

    # ── Post-Operative Orders ──────────────────────────────────────────────
    diet_orders             = models.CharField(max_length=200, blank=True)
    iv_fluids_orders        = models.TextField(blank=True)
    medication_orders       = models.TextField(blank=True)
    antibiotic_orders       = models.TextField(blank=True)
    pain_management_plan    = models.TextField(blank=True)
    dvt_prophylaxis         = models.TextField(blank=True)
    physiotherapy_orders    = models.TextField(blank=True)
    nursing_instructions    = models.TextField(blank=True)
    activity_level          = models.CharField(max_length=200, blank=True)
    follow_up_instructions   = models.TextField(blank=True)
    lab_orders               = models.TextField(blank=True)
    imaging_orders           = models.TextField(blank=True)

    # ── Disposition ────────────────────────────────────────────────────────
    disposition       = models.CharField(max_length=15, choices=Disposition.choices, blank=True)
    disposition_notes = models.TextField(blank=True)

    # ── Follow-up Plan ─────────────────────────────────────────────────────
    review_date                      = models.DateField(null=True, blank=True)
    dressing_change_plan             = models.CharField(max_length=200, blank=True)
    drain_removal_plan               = models.CharField(max_length=200, blank=True)
    suture_removal_plan              = models.CharField(max_length=200, blank=True)
    additional_procedures_planned    = models.TextField(blank=True)
    outpatient_followup_instructions = models.TextField(blank=True)

    class Meta:
        ordering = ['-version']
        verbose_name = 'Post-Operative Note'
        constraints = [
            models.UniqueConstraint(
                fields=['surgery_order'], condition=models.Q(is_current=True),
                name='unique_current_postop_note_per_order',
            ),
        ]

    def __str__(self):
        return f'Post-Op Note: {self.surgery_order.order_number} (v{self.version})'


class PeriopNursingAddendum(models.Model):
    """Append-only nursing documentation attached to a perioperative document.
    Nurses may add notes here but this model has no edit or delete view —
    that is the entire enforcement mechanism behind "nurses can view
    finalized forms and add nursing documentation, but cannot modify surgeon
    or anesthetist documentation."""
    class DocumentType(models.TextChoices):
        ANESTHESIA = 'anesthesia', 'Pre-Op Anesthetic Assessment'
        OPERATIVE  = 'operative',  'OR Operative Note'
        POSTOP     = 'postop',     'Post-Operative Note'

    surgery_order = models.ForeignKey(
        SurgeryOrder, on_delete=models.CASCADE, related_name='nursing_addenda',
    )
    document_type = models.CharField(max_length=15, choices=DocumentType.choices)
    note          = models.TextField()
    created_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='periop_nursing_addenda',
    )
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Perioperative Nursing Addendum'

    def __str__(self):
        return f'Nursing addendum ({self.document_type}): {self.surgery_order.order_number}'


# ── Pre-Operative Checklist ───────────────────────────────────────────────────

class SurgeryPreOpChecklist(models.Model):
    """Tracks all pre-operative requirements for a surgery order.
    Acts as the gate that must be cleared (or emergency-overridden) before
    the patient can be moved into the Operating Room."""

    surgery_order = models.OneToOneField(
        SurgeryOrder, on_delete=models.CASCADE, related_name='preop_checklist',
    )

    # ── Laboratory ────────────────────────────────────────────────────────────
    cbc_done            = models.BooleanField(default=False, verbose_name='CBC Done')
    coagulation_done    = models.BooleanField(default=False, verbose_name='Coagulation Profile Done')
    blood_group_done    = models.BooleanField(default=False, verbose_name='Blood Group & Cross-match Done')
    blood_sugar_done    = models.BooleanField(default=False, verbose_name='Blood Sugar Done')
    rft_done            = models.BooleanField(default=False, verbose_name='RFT Done')
    lft_done            = models.BooleanField(default=False, verbose_name='LFT Done')
    electrolytes_done   = models.BooleanField(default=False, verbose_name='Electrolytes Done')

    # ── Imaging & Investigations ───────────────────────────────────────────────
    cxr_done            = models.BooleanField(default=False, verbose_name='Chest X-Ray Done')
    ecg_done            = models.BooleanField(default=False, verbose_name='ECG Done')
    other_imaging_done  = models.BooleanField(default=False, verbose_name='Other Imaging Done')

    # ── Clinical Clearances ───────────────────────────────────────────────────
    anesthesia_clearance     = models.BooleanField(default=False, verbose_name='Anesthesia Assessment & Clearance')
    medical_clearance        = models.BooleanField(default=False, verbose_name='Medical Clearance')
    surgical_consent_signed  = models.BooleanField(default=False, verbose_name='Surgical Consent Signed')
    anesthesia_consent_signed = models.BooleanField(default=False, verbose_name='Anesthesia Consent Signed')

    # ── Blood Products ────────────────────────────────────────────────────────
    blood_required    = models.BooleanField(default=False, verbose_name='Blood Required')
    blood_available   = models.BooleanField(default=False, verbose_name='Blood Available/Reserved')
    units_prepared    = models.PositiveSmallIntegerField(default=0, verbose_name='Units Prepared')

    # ── Pre-Op Preparation ────────────────────────────────────────────────────
    npo_confirmed              = models.BooleanField(default=False, verbose_name='NPO Status Confirmed')
    iv_access                  = models.BooleanField(default=False, verbose_name='IV Access Established')
    site_marked                = models.BooleanField(default=False, verbose_name='Surgical Site Marked')
    patient_identified         = models.BooleanField(default=False, verbose_name='Patient Identity Verified')
    pre_medication_given       = models.BooleanField(default=False, verbose_name='Pre-Medication Given')
    antibiotic_prophylaxis     = models.BooleanField(default=False, verbose_name='Antibiotic Prophylaxis Given')
    dvt_prophylaxis            = models.BooleanField(default=False, verbose_name='DVT Prophylaxis Given')
    allergies_reviewed         = models.BooleanField(default=False, verbose_name='Allergies Reviewed')
    implants_available         = models.BooleanField(default=False, verbose_name='Implants/Prosthetics Available')

    # ── WHO Surgical Safety Checklist ─────────────────────────────────────────
    who_sign_in_done   = models.BooleanField(default=False, verbose_name='WHO Sign-In Completed (before anaesthesia)')
    who_time_out_done  = models.BooleanField(default=False, verbose_name='WHO Time-Out Completed (before incision)')
    who_sign_out_done  = models.BooleanField(default=False, verbose_name='WHO Sign-Out Completed (before leaving OR)')

    # ── Emergency Override ────────────────────────────────────────────────────
    emergency_override = models.BooleanField(default=False, verbose_name='Emergency Override Activated')
    override_reason    = models.TextField(blank=True)
    override_by        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='preop_overrides',
    )
    override_at        = models.DateTimeField(null=True, blank=True)

    notes      = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='preop_checklists_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='preop_checklists_updated',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pre-Operative Checklist'

    def __str__(self):
        return f'Pre-op checklist: {self.surgery_order.order_number}'

    @property
    def mandatory_items_done(self):
        """The absolute minimum required before OR entry."""
        return (
            self.surgical_consent_signed and
            self.anesthesia_clearance and
            self.patient_identified and
            self.npo_confirmed
        )

    @property
    def is_cleared(self):
        return self.mandatory_items_done or self.emergency_override

    @property
    def completion_percent(self):
        fields = [
            self.cbc_done, self.coagulation_done, self.blood_group_done,
            self.surgical_consent_signed, self.anesthesia_clearance, self.medical_clearance,
            self.patient_identified, self.npo_confirmed, self.iv_access, self.site_marked,
            self.antibiotic_prophylaxis, self.allergies_reviewed,
        ]
        done = sum(1 for f in fields if f)
        return int(done / len(fields) * 100)


# ── PACU / Recovery Room Record ───────────────────────────────────────────────

class SurgeryPACURecord(PeriopDocumentBase):
    """Post-Anesthesia Care Unit (PACU / Recovery Room) documentation.
    Extends PeriopDocumentBase for the same draft→finalize versioning pattern
    used by anesthesia and operative notes."""

    class Consciousness(models.TextChoices):
        ALERT        = 'alert',        'Alert & Oriented'
        DROWSY       = 'drowsy',       'Drowsy but Rousable'
        RESPONDING   = 'responding',   'Responding to Commands'
        UNRESPONSIVE = 'unresponsive', 'Unresponsive'

    class DischargeDestination(models.TextChoices):
        WARD  = 'ward',  'Surgical Ward'
        ICU   = 'icu',   'Intensive Care Unit'
        HDU   = 'hdu',   'High Dependency Unit'
        STEP  = 'step',  'Step-Down Unit'

    surgery_order = models.ForeignKey(
        SurgeryOrder, on_delete=models.CASCADE, related_name='pacu_records',
    )

    # ── Arrival Vitals ────────────────────────────────────────────────────────
    arrival_time        = models.DateTimeField(null=True, blank=True)
    arrival_bp_systolic  = models.PositiveSmallIntegerField(null=True, blank=True)
    arrival_bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    arrival_pulse        = models.PositiveSmallIntegerField(null=True, blank=True)
    arrival_rr           = models.PositiveSmallIntegerField(null=True, blank=True)
    arrival_temp         = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    arrival_spo2         = models.PositiveSmallIntegerField(null=True, blank=True)
    arrival_pain_score   = models.PositiveSmallIntegerField(null=True, blank=True)

    # ── Airway & Consciousness ────────────────────────────────────────────────
    consciousness_level  = models.CharField(max_length=20, choices=Consciousness.choices, blank=True)
    airway_status        = models.CharField(max_length=100, blank=True)
    o2_delivery_method   = models.CharField(max_length=100, blank=True)
    o2_flow_rate         = models.CharField(max_length=50, blank=True)

    # ── Wound & Drains ────────────────────────────────────────────────────────
    wound_condition   = models.TextField(blank=True)
    drain_type        = models.CharField(max_length=100, blank=True)
    drain_output_ml   = models.PositiveIntegerField(null=True, blank=True)
    bleeding_notes    = models.TextField(blank=True)

    # ── Medications in PACU ───────────────────────────────────────────────────
    analgesics_given     = models.TextField(blank=True)
    antiemetics_given    = models.TextField(blank=True)
    iv_fluids_given      = models.TextField(blank=True)
    other_medications    = models.TextField(blank=True)

    # ── Aldrete Score (standard PACU discharge readiness score, each 0–2) ─────
    aldrete_activity       = models.PositiveSmallIntegerField(null=True, blank=True)
    aldrete_respiration    = models.PositiveSmallIntegerField(null=True, blank=True)
    aldrete_circulation    = models.PositiveSmallIntegerField(null=True, blank=True)
    aldrete_consciousness  = models.PositiveSmallIntegerField(null=True, blank=True)
    aldrete_spo2           = models.PositiveSmallIntegerField(null=True, blank=True)

    # ── Discharge from PACU ───────────────────────────────────────────────────
    discharge_time         = models.DateTimeField(null=True, blank=True)
    discharge_destination  = models.CharField(max_length=10, choices=DischargeDestination.choices, blank=True)
    discharge_criteria_met = models.BooleanField(default=False)
    pacu_duration_minutes  = models.PositiveIntegerField(null=True, blank=True)

    complications = models.TextField(blank=True)
    monitoring_notes = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering  = ['-created_at']
        verbose_name = 'PACU / Recovery Record'
        constraints = [
            models.UniqueConstraint(
                fields=['surgery_order'],
                condition=models.Q(is_current=True),
                name='unique_current_pacu_record_per_order',
            )
        ]

    def __str__(self):
        return f'PACU record v{self.version}: {self.surgery_order.order_number}'

    @property
    def aldrete_total(self):
        scores = [
            self.aldrete_activity, self.aldrete_respiration,
            self.aldrete_circulation, self.aldrete_consciousness, self.aldrete_spo2,
        ]
        if all(s is not None for s in scores):
            return sum(scores)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# FACILITY & HOSPITAL LOCATION MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

class Building(models.Model):
    name      = models.CharField(max_length=100, unique=True)
    code      = models.CharField(max_length=20, blank=True)
    notes     = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Building'

    def __str__(self):
        return self.name


class Floor(models.Model):
    building    = models.ForeignKey(Building, on_delete=models.CASCADE, related_name='floors')
    name        = models.CharField(max_length=50)
    level_order = models.SmallIntegerField(default=0)
    notes       = models.TextField(blank=True)
    is_active   = models.BooleanField(default=True)

    class Meta:
        ordering = ['building__name', 'level_order']
        unique_together = ('building', 'name')
        verbose_name = 'Floor'

    def __str__(self):
        return f'{self.building.name} — {self.name}'


class Ward(models.Model):
    class WardType(models.TextChoices):
        MEDICAL   = 'medical',   'Medical Ward'
        SURGICAL  = 'surgical',  'Surgical Ward'
        PEDIATRIC = 'pediatric', 'Pediatric Ward'
        MATERNITY = 'maternity', 'Maternity Ward'
        ICU       = 'icu',       'ICU'
        NICU      = 'nicu',      'NICU'
        PICU      = 'picu',      'PICU'
        HDU       = 'hdu',       'HDU'
        ISOLATION = 'isolation', 'Isolation Ward'
        OTHER     = 'other',     'Other'

    class Gender(models.TextChoices):
        MALE  = 'male',  'Male'
        FEMALE = 'female', 'Female'
        MIXED = 'mixed', 'Mixed'

    class Status(models.TextChoices):
        ACTIVE      = 'active',      'Active'
        INACTIVE    = 'inactive',    'Inactive'
        MAINTENANCE = 'maintenance', 'Under Maintenance'

    name       = models.CharField(max_length=100)
    code       = models.CharField(max_length=20, unique=True)
    ward_type  = models.CharField(max_length=15, choices=WardType.choices, default=WardType.MEDICAL)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='wards')
    building   = models.ForeignKey(Building, on_delete=models.SET_NULL, null=True, blank=True, related_name='wards')
    floor      = models.ForeignKey(Floor, on_delete=models.SET_NULL, null=True, blank=True, related_name='wards')
    gender_restriction = models.CharField(max_length=10, choices=Gender.choices, default=Gender.MIXED)
    is_isolation = models.BooleanField(default=False)
    is_icu_hdu   = models.BooleanField(default=False)
    status     = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    notes      = models.TextField(blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Ward'

    def __str__(self):
        return f'{self.name} ({self.code})'

    @property
    def total_beds(self):
        return Bed.objects.filter(room__ward=self).count()


class Room(models.Model):
    class RoomType(models.TextChoices):
        STANDARD  = 'standard',  'Standard Room'
        PRIVATE   = 'private',   'Private Room'
        VIP       = 'vip',       'VIP Room'
        ISOLATION = 'isolation', 'Isolation Room'
        ICU       = 'icu',       'ICU Room'
        RECOVERY  = 'recovery',  'Recovery Room'

    class Privacy(models.TextChoices):
        PRIVATE      = 'private',      'Private'
        SEMI_PRIVATE = 'semi_private', 'Semi-private'
        COMMON       = 'common',       'Common'

    class Status(models.TextChoices):
        ACTIVE      = 'active',      'Active'
        CLOSED      = 'closed',      'Closed'
        MAINTENANCE = 'maintenance', 'Under Maintenance'

    ward         = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name='rooms')
    room_number  = models.CharField(max_length=20)
    room_name    = models.CharField(max_length=100, blank=True)
    room_type    = models.CharField(max_length=15, choices=RoomType.choices, default=RoomType.STANDARD)
    capacity     = models.PositiveSmallIntegerField(default=1)
    privacy_type = models.CharField(max_length=15, choices=Privacy.choices, default=Privacy.SEMI_PRIVATE)
    is_ac        = models.BooleanField(default=False)
    is_isolation = models.BooleanField(default=False)
    status       = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ['ward__name', 'room_number']
        unique_together = ('ward', 'room_number')
        verbose_name = 'Room'

    def __str__(self):
        return f'{self.ward.name} — Room {self.room_number}'

    @property
    def bed_count(self):
        return self.beds.count()


class Bed(models.Model):
    class Status(models.TextChoices):
        AVAILABLE   = 'available',   'Available'
        OCCUPIED    = 'occupied',    'Occupied'
        RESERVED    = 'reserved',    'Reserved'
        CLEANING    = 'cleaning',    'Cleaning'
        MAINTENANCE = 'maintenance', 'Maintenance'
        OUT_OF_SERVICE = 'out_of_service', 'Out of Service'

    room    = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='beds')
    ward    = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name='beds', editable=False)
    bed_number = models.CharField(max_length=20)
    bed_code   = models.CharField(max_length=30, unique=True)
    current_patient = models.ForeignKey(
        'Patient', on_delete=models.SET_NULL, null=True, blank=True, related_name='current_beds',
    )
    status  = models.CharField(max_length=15, choices=Status.choices, default=Status.AVAILABLE)

    class Meta:
        ordering = ['room__ward__name', 'room__room_number', 'bed_number']
        unique_together = ('room', 'bed_number')
        verbose_name = 'Bed'

    def __str__(self):
        return f'{self.bed_code} ({self.room})'

    def save(self, *args, **kwargs):
        self.ward = self.room.ward
        super().save(*args, **kwargs)


class Admission(models.Model):
    """One row per bed-stay segment. A transfer closes the current segment
    (status=Transferred) and opens a new one on the destination bed — this
    keeps bed-occupancy history fully reconstructable per patient."""
    class Status(models.TextChoices):
        ADMITTED    = 'admitted',    'Admitted'
        DISCHARGED  = 'discharged',  'Discharged'
        TRANSFERRED = 'transferred', 'Transferred'

    patient = models.ForeignKey('Patient', on_delete=models.PROTECT, related_name='admissions')
    visit   = models.ForeignKey('Visit', on_delete=models.SET_NULL, null=True, blank=True, related_name='admissions')
    bed     = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name='admissions')
    status  = models.CharField(max_length=15, choices=Status.choices, default=Status.ADMITTED)

    admitted_at = models.DateTimeField(default=timezone.now)
    admitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='admissions_performed',
    )
    discharged_at = models.DateTimeField(null=True, blank=True)
    discharged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='discharges_performed',
    )
    transfer_reason = models.TextField(blank=True)

    class Priority(models.TextChoices):
        ROUTINE  = 'routine',  'Routine'
        HIGH     = 'high',     'High'
        CRITICAL = 'critical', 'Critical'

    assigned_nurse = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='nursing_assignments',
    )
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.ROUTINE)

    # Discharge gating (bed release requires both to be set — see
    # views_facility.patient_discharge): a doctor's sign-off, independent of
    # whether a full DischargeSummary document was ever authored.
    discharge_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='admission_discharges_approved',
    )
    discharge_approved_at = models.DateTimeField(null=True, blank=True)

    # Set once the formal AdmissionRequest pipeline (as opposed to the
    # one-step "Quick Admit" fast path) produces this admission.
    admission_request = models.OneToOneField(
        'AdmissionRequest', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='admission',
    )

    class Meta:
        ordering = ['-admitted_at']
        verbose_name = 'Admission'

    def __str__(self):
        return f'{self.patient} — {self.bed.bed_code} ({self.status})'


# ══════════════════════════════════════════════════════════════════════════════
# ADMISSION REQUEST & DEPOSIT MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

class AdmissionDepositRule(models.Model):
    """Admin-configurable deposit policy. Resolution picks the most specific
    active rule matching (ward, department, priority); a rule with all three
    blank/null acts as the global fallback. is_exempt short-circuits to "no
    deposit required" regardless of amount."""
    ward       = models.ForeignKey(Ward, on_delete=models.CASCADE, null=True, blank=True, related_name='deposit_rules')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, null=True, blank=True, related_name='deposit_rules')
    priority   = models.CharField(
        max_length=10, choices=Admission.Priority.choices, blank=True,
        help_text='Leave blank to match any priority.',
    )
    amount     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_exempt  = models.BooleanField(default=False)
    is_active  = models.BooleanField(default=True)
    notes      = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        verbose_name = 'Admission Deposit Rule'

    def __str__(self):
        scope = self.ward.name if self.ward else (self.department.name if self.department else 'Global')
        return f'Deposit rule: {scope} ({"Exempt" if self.is_exempt else self.amount})'

    @property
    def specificity(self):
        return (1 if self.ward_id else 0) + (1 if self.department_id else 0) + (1 if self.priority else 0)


class AdmissionRequest(models.Model):
    """The pre-bed-assignment phase of an inpatient admission: captures the
    clinical request, drives the deposit gate, and — once cleared — is used
    to assign the actual bed and create the Admission row."""
    class Status(models.TextChoices):
        PENDING           = 'pending',            'Pending Admission'
        APPROVED          = 'approved',            'Approved'
        AWAITING_DEPOSIT  = 'awaiting_deposit',    'Awaiting Deposit'
        DEPOSIT_PAID      = 'deposit_paid',        'Deposit Paid'
        CREDIT_APPROVED   = 'credit_approved',     'Credit Approved'
        AWAITING_BED      = 'awaiting_bed',        'Awaiting Bed'
        ADMITTED          = 'admitted',            'Admitted'
        REJECTED          = 'rejected',            'Rejected'
        CANCELLED         = 'cancelled',           'Cancelled'

    class Priority(models.TextChoices):
        ROUTINE   = 'routine',   'Routine'
        URGENT    = 'urgent',    'Urgent'
        EMERGENCY = 'emergency', 'Emergency'

    class Source(models.TextChoices):
        RECEPTION = 'reception', 'Reception'
        DOCTOR    = 'doctor',    'Doctor'
        NURSE     = 'nurse',     'Nurse'
        EMERGENCY = 'emergency', 'Emergency Department'
        OR        = 'or',        'Operating Room'

    visit   = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='admission_requests')
    patient = models.ForeignKey('Patient', on_delete=models.PROTECT, related_name='admission_requests')

    admission_diagnosis  = models.CharField(max_length=255)
    reason_for_admission = models.TextField()
    admitting_department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='admission_requests')
    admitting_doctor      = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True, related_name='admission_requests')
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.ROUTINE)
    expected_los_days = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Expected length of stay (days)')

    requires_isolation = models.BooleanField(default=False)
    requires_icu        = models.BooleanField(default=False)
    requires_oxygen      = models.BooleanField(default=False)
    special_requirements_notes = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    request_source = models.CharField(max_length=15, choices=Source.choices, default=Source.RECEPTION)
    requested_by   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='admission_requests_made')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='admission_requests_reviewed',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    deposit_rule_snapshot = models.ForeignKey(AdmissionDepositRule, on_delete=models.SET_NULL, null=True, blank=True, related_name='requests_applied_to')
    deposit_invoice_item  = models.ForeignKey(InvoiceItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='deposit_for_requests')

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='admission_requests_cancelled',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Admission Request'

    def __str__(self):
        return f'Admission request — {self.patient.full_name} ({self.get_status_display()})'

    @property
    def deposit_cleared(self):
        """True once no deposit was ever required, or the deposit line item
        has been paid or credit-approved."""
        if self.deposit_invoice_item_id is None:
            return True
        return self.deposit_invoice_item.payment_cleared

    @property
    def deposit_amount(self):
        return self.deposit_invoice_item.total if self.deposit_invoice_item_id else 0


# ══════════════════════════════════════════════════════════════════════════════
# INPATIENT DEPOSIT ACCOUNT
# ══════════════════════════════════════════════════════════════════════════════

class InpatientDepositAccount(models.Model):
    """Wallet-style ledger for an inpatient stay. Created when the bed is
    assigned; every subsequent billable service is recorded as a CHARGE_DEDUCTION
    entry so the balance decreases in real time. Closed at discharge after
    final reconciliation."""

    class Status(models.TextChoices):
        ACTIVE  = 'active',  'Active'
        SETTLED = 'settled', 'Settled'
        CLOSED  = 'closed',  'Closed'

    admission         = models.OneToOneField('Admission', on_delete=models.CASCADE, related_name='deposit_account')
    patient           = models.ForeignKey('Patient', on_delete=models.PROTECT, related_name='deposit_accounts')
    admission_request = models.OneToOneField(
        'AdmissionRequest', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='deposit_account',
    )

    initial_deposit     = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    additional_deposits = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    total_charges       = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    total_refunded      = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))

    low_balance_threshold = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('500'),
        help_text='Alert staff when the available balance falls below this amount.',
    )

    status     = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    notes      = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='deposit_accounts_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Inpatient Deposit Account'
        ordering = ['-created_at']

    def __str__(self):
        return f'Deposit — {self.patient.full_name} ({self.admission_id})'

    @property
    def total_deposited(self):
        return self.initial_deposit + self.additional_deposits

    @property
    def available_balance(self):
        return self.total_deposited - self.total_charges + self.total_refunded

    @property
    def outstanding_balance(self):
        bal = self.available_balance
        return abs(bal) if bal < Decimal('0') else Decimal('0')

    @property
    def refund_due(self):
        bal = self.available_balance
        return bal if bal > Decimal('0') else Decimal('0')

    @property
    def is_low_balance(self):
        bal = self.available_balance
        return Decimal('0') < bal <= self.low_balance_threshold

    @property
    def is_exhausted(self):
        return self.available_balance <= Decimal('0') and self.total_charges > Decimal('0')

    @property
    def utilization_pct(self):
        if self.total_deposited <= 0:
            return 0
        return min(100, int(self.total_charges / self.total_deposited * 100))


class DepositTransaction(models.Model):
    """Immutable ledger entry for every financial event on an
    InpatientDepositAccount."""

    class TxType(models.TextChoices):
        INITIAL_DEPOSIT    = 'initial',    'Initial Deposit'
        ADDITIONAL_DEPOSIT = 'additional', 'Additional Deposit'
        CHARGE_DEDUCTION   = 'deduction',  'Charge Deduction'
        REVERSAL           = 'reversal',   'Charge Reversal'
        REFUND             = 'refund',     'Refund Issued'
        ADJUSTMENT_IN      = 'adj_in',     'Adjustment (Credit)'
        ADJUSTMENT_OUT     = 'adj_out',    'Adjustment (Debit)'

    account       = models.ForeignKey(InpatientDepositAccount, on_delete=models.CASCADE, related_name='transactions')
    tx_type       = models.CharField(max_length=15, choices=TxType.choices)
    amount        = models.DecimalField(max_digits=12, decimal_places=2)
    description   = models.CharField(max_length=255)
    invoice_item  = models.ForeignKey(
        'InvoiceItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='deposit_transactions',
    )
    invoice       = models.ForeignKey(
        'Invoice', on_delete=models.SET_NULL, null=True, blank=True, related_name='deposit_transactions',
    )
    payment       = models.ForeignKey(
        'Payment', on_delete=models.SET_NULL, null=True, blank=True, related_name='deposit_transactions',
    )
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    notes         = models.TextField(blank=True)
    performed_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='deposit_transactions_performed',
    )
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Deposit Transaction'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.get_tx_type_display()} ETB {self.amount:,.2f} — {self.account_id}'

    @property
    def is_credit(self):
        return self.tx_type in (
            self.TxType.INITIAL_DEPOSIT, self.TxType.ADDITIONAL_DEPOSIT,
            self.TxType.REVERSAL, self.TxType.ADJUSTMENT_IN,
        )

    @property
    def is_debit(self):
        return not self.is_credit


# ══════════════════════════════════════════════════════════════════════════════
# PRESCRIPTION WORKFLOW
# ══════════════════════════════════════════════════════════════════════════════

class Prescription(models.Model):
    class Status(models.TextChoices):
        CREATED         = 'Created',              'Created'
        SENT            = 'Sent to Pharmacy',     'Sent to Pharmacy'
        REVIEWING       = 'Under Review',         'Under Review'
        READY           = 'Ready to Dispense',    'Ready to Dispense'
        WAITING_PAYMENT = 'Waiting Payment',      'Waiting for Payment'
        DISPENSED       = 'Dispensed',            'Dispensed'
        PARTIAL         = 'Partially Dispensed',  'Partially Dispensed'
        CANCELLED       = 'Cancelled',            'Cancelled'

    class BillingStatus(models.TextChoices):
        NOT_BILLED      = 'not_billed',       'Not Billed'
        PENDING_PAYMENT = 'pending_payment',  'Pending Payment'
        PARTIALLY_PAID  = 'partially_paid',   'Partially Paid'
        PAID            = 'paid',             'Paid'
        CREDIT_APPROVED = 'credit_approved',  'Credit Approved'
        WAIVED          = 'waived',           'Waived'
        CANCELLED       = 'cancelled',        'Cancelled'

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
    billing_status      = models.CharField(
        max_length=20, choices=BillingStatus.choices, default=BillingStatus.NOT_BILLED,
    )
    billing_amount      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    invoice             = models.ForeignKey(
        'Invoice', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='prescriptions',
    )
    credit_approved_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='credit_approved_prescriptions',
    )
    credit_approved_at  = models.DateTimeField(null=True, blank=True)
    credit_reason       = models.TextField(blank=True)
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
    invoice_item        = models.OneToOneField(
        'InvoiceItem', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='prescription_item',
    )
    unit_price          = models.DecimalField(max_digits=10, decimal_places=2, default=0)

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

    class Source(models.TextChoices):
        PHARMACY         = 'pharmacy',         'Pharmacy Dispensed'
        WARD_STOCK       = 'ward_stock',       'Ward/Floor Stock'
        PATIENT_SUPPLIED = 'patient_supplied', 'Patient-Supplied'

    # Either prescription_item (pharmacy workflow) OR medication_order (direct ward workflow).
    # Exactly one should be set per entry.
    prescription_item = models.ForeignKey(
        PrescriptionItem, on_delete=models.CASCADE, related_name='mar_entries',
        null=True, blank=True,
    )
    medication_order  = models.ForeignKey(
        'MedicationOrder', on_delete=models.CASCADE, related_name='mar_entries',
        null=True, blank=True,
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
    # Medication source — determines billing/inventory behaviour on administration
    source            = models.CharField(
        max_length=20, choices=Source.choices, default=Source.PHARMACY,
    )
    # Ward stock links (only when source == WARD_STOCK)
    ward_stock_item   = models.ForeignKey(
        'DepartmentStock', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='mar_entries',
    )
    ward_stock_batch  = models.ForeignKey(
        'DepartmentStockBatch', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='mar_entries',
    )
    # Amount charged to deposit account (ward stock administrations only)
    charge_amount     = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))

    patient_response  = models.TextField(blank=True)
    reason_missed     = models.TextField(blank=True)
    notes             = models.TextField(blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scheduled_time']
        verbose_name = 'MAR Entry'
        verbose_name_plural = 'MAR Entries'

    @property
    def drug_name(self):
        if self.prescription_item_id:
            return self.prescription_item.drug_name
        if self.medication_order_id:
            return self.medication_order.drug_name
        return '—'

    @property
    def dose_display(self):
        if self.prescription_item_id:
            return self.prescription_item.dose
        if self.medication_order_id:
            return self.medication_order.dosage
        return ''

    @property
    def route_display(self):
        if self.prescription_item_id:
            return self.prescription_item.get_route_display()
        if self.medication_order_id:
            return self.medication_order.get_route_display()
        return ''

    def __str__(self):
        return (f"{self.drug_name} "
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


# ── Session, Notification, and Record Locking ────────────────────────────────

class UserSession(models.Model):
    """Tracks each browser login session."""
    class LogoutType(models.TextChoices):
        MANUAL  = 'manual',  'Manual Logout'
        TIMEOUT = 'timeout', 'Session Timeout'
        FORCED  = 'forced',  'Force Logout by Admin'
        EXPIRED = 'expired', 'Session Expired'

    user           = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='user_sessions')
    session_key    = models.CharField(max_length=40, unique=True)
    ip_address     = models.GenericIPAddressField(null=True, blank=True)
    user_agent     = models.TextField(blank=True)
    browser        = models.CharField(max_length=100, blank=True)
    os             = models.CharField(max_length=100, blank=True)
    device_type    = models.CharField(max_length=20, blank=True)  # Desktop, Mobile, Tablet
    login_at       = models.DateTimeField(auto_now_add=True)
    last_activity  = models.DateTimeField(auto_now_add=True)
    logout_at      = models.DateTimeField(null=True, blank=True)
    is_active      = models.BooleanField(default=True)
    logout_type    = models.CharField(max_length=10, choices=LogoutType.choices, blank=True)
    forced_by      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='forced_logouts',
    )

    class Meta:
        ordering = ['-login_at']
        verbose_name = 'User Session'

    def __str__(self):
        return f"{self.user.username} — {self.login_at:%d %b %Y %H:%M} ({'Active' if self.is_active else 'Closed'})"

    @property
    def duration(self):
        end = self.logout_at or timezone.now()
        return end - self.login_at


class Notification(models.Model):
    class NotifType(models.TextChoices):
        PRESCRIPTION = 'prescription', 'New Prescription'
        PAYMENT      = 'payment',      'Payment Completed'
        LAB_RESULT   = 'lab_result',   'Lab Result Ready'
        APPOINTMENT  = 'appointment',  'New Appointment'
        SURGERY      = 'surgery',      'Surgery Update'
        INVENTORY    = 'inventory',    'Inventory Alert'
        SYSTEM       = 'system',       'System'
        GENERAL      = 'general',      'General'

    recipient    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    sender       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sent_notifications',
    )
    notif_type   = models.CharField(max_length=20, choices=NotifType.choices, default=NotifType.GENERAL)
    title        = models.CharField(max_length=200)
    message      = models.TextField(blank=True)
    url          = models.CharField(max_length=500, blank=True)
    object_type  = models.CharField(max_length=50, blank=True)
    object_id    = models.PositiveIntegerField(null=True, blank=True)
    is_read      = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Notification'
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
        ]

    def __str__(self):
        return f"{self.title} → {self.recipient.username}"


class RecordLock(models.Model):
    """Soft concurrent-edit lock. Warning only — does not hard-block saves."""
    content_type = models.ForeignKey('contenttypes.ContentType', on_delete=models.CASCADE)
    object_id    = models.PositiveIntegerField()
    locked_by    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='record_locks')
    locked_at    = models.DateTimeField(auto_now_add=True)
    expires_at   = models.DateTimeField()

    class Meta:
        unique_together = [('content_type', 'object_id')]
        verbose_name = 'Record Lock'

    def __str__(self):
        return f"{self.content_type} #{self.object_id} locked by {self.locked_by.username}"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @classmethod
    def acquire(cls, obj, user, minutes=5):
        """Acquire or refresh a lock. Returns (lock, acquired, held_by_user)."""
        from django.contrib.contenttypes.models import ContentType
        from datetime import timedelta
        ct = ContentType.objects.get_for_model(obj)
        expires = timezone.now() + timedelta(minutes=minutes)
        # Clean expired locks first
        cls.objects.filter(content_type=ct, object_id=obj.pk, expires_at__lt=timezone.now()).delete()
        lock, created = cls.objects.get_or_create(
            content_type=ct, object_id=obj.pk,
            defaults={'locked_by': user, 'expires_at': expires},
        )
        if created or lock.locked_by == user:
            if not created:
                lock.expires_at = expires
                lock.save(update_fields=['expires_at'])
            return lock, True, True
        return lock, False, False

    @classmethod
    def release(cls, obj, user):
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(obj)
        cls.objects.filter(content_type=ct, object_id=obj.pk, locked_by=user).delete()

    @classmethod
    def get_lock(cls, obj):
        """Returns active lock or None."""
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(obj)
        cls.objects.filter(content_type=ct, object_id=obj.pk, expires_at__lt=timezone.now()).delete()
        return cls.objects.filter(content_type=ct, object_id=obj.pk).select_related('locked_by').first()


# ─────────────────────────────────────────────────────────────────────────────
# PHYSICAL INVENTORY COUNT & INVENTORY PERIOD MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────
# Spans all three existing stock domains (General Store / InventoryItem,
# Medication / MedicationBatch, Department Store / DepartmentStock) without
# altering any of them — a count line just points at whichever one of the
# three source rows it's counting, and approval writes back into that same
# domain's existing balance + ledger using the exact mechanics each domain
# already uses elsewhere (mirrors StockCountSession/StockCountItem's
# approval logic in views_store.py, generalized across domains).

class InventoryPeriod(models.Model):
    class PeriodType(models.TextChoices):
        ANNUAL    = 'Annual',    'Annual'
        QUARTERLY = 'Quarterly', 'Quarterly'
        MONTHLY   = 'Monthly',   'Monthly'

    class Status(models.TextChoices):
        OPEN   = 'Open',   'Open'
        CLOSED = 'Closed', 'Closed'

    name        = models.CharField(max_length=100, unique=True, help_text='e.g. "2026 Inventory Period"')
    period_type = models.CharField(max_length=10, choices=PeriodType.choices, default=PeriodType.ANNUAL)
    start_date  = models.DateField()
    end_date    = models.DateField()
    status      = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    previous_period = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='next_periods',
    )
    opened_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='inventory_periods_opened',
    )
    closed_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='inventory_periods_closed',
    )
    closed_at   = models.DateTimeField(null=True, blank=True)
    notes       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Inventory Period'
        verbose_name_plural = 'Inventory Periods'

    def __str__(self):
        return f"{self.name} ({self.status})"


class PhysicalCount(models.Model):
    """A physical inventory count session — the generalized, multi-domain
    successor to StockCountSession (which remains untouched and still works
    standalone for quick General-Store-only counts)."""
    class CountType(models.TextChoices):
        ANNUAL    = 'Annual',    'Annual Inventory Count'
        QUARTERLY = 'Quarterly', 'Quarterly Inventory Count'
        MONTHLY   = 'Monthly',   'Monthly Inventory Count'
        CYCLE     = 'Cycle',     'Cycle Count (Selected Items)'
        EMERGENCY = 'Emergency', 'Emergency Stock Verification'

    class Domain(models.TextChoices):
        GENERAL_STORE   = 'general_store',   'Main / General Store'
        MEDICATION      = 'medication',      'Pharmacy (Medication Inventory)'
        DEPARTMENT_STORE = 'department_store', 'Department Store (Ward / OR / ER / ICU ...)'

    class Status(models.TextChoices):
        PLANNED     = 'Planned',     'Planned'
        IN_PROGRESS = 'In Progress', 'In Progress'
        SUBMITTED   = 'Submitted',   'Submitted for Approval'
        APPROVED    = 'Approved',    'Approved'
        CANCELLED   = 'Cancelled',   'Cancelled'

    count_number = models.CharField(max_length=20, unique=True, blank=True)
    period       = models.ForeignKey(
        InventoryPeriod, on_delete=models.PROTECT, null=True, blank=True, related_name='physical_counts',
    )
    count_type   = models.CharField(max_length=10, choices=CountType.choices, default=CountType.ANNUAL)
    domain       = models.CharField(max_length=20, choices=Domain.choices, default=Domain.GENERAL_STORE)
    category     = models.ForeignKey(
        InventoryCategory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='physical_counts', help_text='Optional — narrows a General Store count to one category (cycle count).',
    )
    department_store = models.ForeignKey(
        DepartmentStore, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='physical_counts', help_text='Required when domain = Department Store.',
    )
    freeze_snapshot = models.BooleanField(
        default=True,
        help_text='Freeze system quantities at the moment the count starts, so later transactions during counting do not change what staff are reconciling against.',
    )
    status       = models.CharField(max_length=15, choices=Status.choices, default=Status.PLANNED)
    notes        = models.TextField(blank=True)
    created_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='physical_counts_created',
    )
    approved_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='physical_counts_approved',
    )
    approval_notes = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Physical Inventory Count'
        verbose_name_plural = 'Physical Inventory Counts'

    def __str__(self):
        return f"{self.count_number} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.count_number:
            last = PhysicalCount.objects.order_by('-id').first()
            next_id = (last.id + 1) if last else 1
            self.count_number = f"PIC-{timezone.localdate():%Y%m}-{next_id:04d}"
        super().save(*args, **kwargs)

    @property
    def item_count(self):
        return self.lines.count()

    @property
    def variance_count(self):
        return sum(1 for line in self.lines.all() if line.has_variance)


class PhysicalCountLine(models.Model):
    """One counted item within a PhysicalCount. Exactly one of
    inventory_item / medication_batch / department_stock is set, matching
    the parent PhysicalCount.domain. Item details are snapshotted at count
    time so the count sheet and later reports stay stable even if the
    source catalog record is edited or removed years later."""
    physical_count   = models.ForeignKey(PhysicalCount, on_delete=models.CASCADE, related_name='lines')

    inventory_item    = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='physical_count_lines',
    )
    medication_batch   = models.ForeignKey(
        MedicationBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name='physical_count_lines',
    )
    department_stock   = models.ForeignKey(
        DepartmentStock, on_delete=models.SET_NULL, null=True, blank=True, related_name='physical_count_lines',
    )

    # Snapshot fields (authoritative for display/reporting — independent of
    # whether the source row above still exists or has since changed)
    item_code       = models.CharField(max_length=100, blank=True)
    item_name       = models.CharField(max_length=255)
    category_name   = models.CharField(max_length=100, blank=True)
    unit_of_measure = models.CharField(max_length=50, blank=True)
    batch_number    = models.CharField(max_length=100, blank=True)
    expiration_date = models.DateField(null=True, blank=True)

    system_quantity   = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    physical_quantity = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    unit_cost         = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    counted_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='physical_count_lines_counted',
    )
    counted_at  = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='physical_count_lines_verified',
    )
    remarks     = models.CharField(max_length=300, blank=True)
    is_approved = models.BooleanField(default=False)

    class Meta:
        ordering = ['item_name']
        verbose_name = 'Physical Count Line'
        verbose_name_plural = 'Physical Count Lines'

    def __str__(self):
        return f"{self.physical_count.count_number} — {self.item_name}"

    @property
    def variance(self):
        if self.physical_quantity is None:
            return None
        return self.physical_quantity - self.system_quantity

    @property
    def has_variance(self):
        v = self.variance
        return v is not None and v != 0

    @property
    def adjustment_value(self):
        v = self.variance
        if v is None:
            return None
        return v * (self.unit_cost or 0)


class InventoryAdjustment(models.Model):
    """Permanent, cross-domain adjustment record created only when a
    PhysicalCount is approved and a line has a variance — this is the
    canonical adjustment audit trail the new reports read from, layered on
    top of (not replacing) each domain's own native ledger entry."""
    class AdjustmentType(models.TextChoices):
        INCREASE = 'Increase', 'Increase'
        DECREASE = 'Decrease', 'Decrease'

    physical_count_line = models.ForeignKey(
        PhysicalCountLine, on_delete=models.PROTECT, related_name='adjustments',
    )
    domain             = models.CharField(max_length=20, choices=PhysicalCount.Domain.choices)
    item_description   = models.CharField(max_length=255)
    previous_quantity  = models.DecimalField(max_digits=14, decimal_places=4)
    physical_quantity  = models.DecimalField(max_digits=14, decimal_places=4)
    difference         = models.DecimalField(max_digits=14, decimal_places=4)
    adjustment_type    = models.CharField(max_length=10, choices=AdjustmentType.choices)
    unit_cost          = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    adjustment_value   = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reason             = models.TextField(blank=True)
    approved_by        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='inventory_adjustments_approved',
    )
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Inventory Adjustment'
        verbose_name_plural = 'Inventory Adjustments'

    def __str__(self):
        return f"{self.item_description}: {self.adjustment_type} {self.difference}"


class InventoryPeriodBalance(models.Model):
    """Opening/closing balance snapshot rows for an InventoryPeriod — a
    period's Closing rows are copied verbatim into the next period's
    Opening rows when that next period is created, so "the verified closing
    balance becomes the opening balance" is a literal, traceable copy, not
    just a claim. Historical rows are never deleted or overwritten."""
    class BalanceType(models.TextChoices):
        OPENING = 'Opening', 'Opening Balance'
        CLOSING = 'Closing', 'Closing Balance'

    period       = models.ForeignKey(InventoryPeriod, on_delete=models.PROTECT, related_name='balances')
    balance_type = models.CharField(max_length=10, choices=BalanceType.choices)
    domain       = models.CharField(max_length=20, choices=PhysicalCount.Domain.choices)

    inventory_item    = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='period_balances',
    )
    medication        = models.ForeignKey(
        Medication, on_delete=models.SET_NULL, null=True, blank=True, related_name='period_balances',
    )
    department_stock  = models.ForeignKey(
        DepartmentStock, on_delete=models.SET_NULL, null=True, blank=True, related_name='period_balances',
    )

    item_code     = models.CharField(max_length=100, blank=True)
    item_name     = models.CharField(max_length=255)
    category_name = models.CharField(max_length=100, blank=True)

    quantity   = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    unit_cost  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    valuation  = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    carried_from_period = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='carried_to',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['domain', 'item_name']
        verbose_name = 'Inventory Period Balance'
        verbose_name_plural = 'Inventory Period Balances'
        indexes = [models.Index(fields=['period', 'balance_type', 'domain'])]

    def __str__(self):
        return f"{self.period.name} — {self.balance_type} — {self.item_name}: {self.quantity}"


# ══════════════════════════════════════════════════════════════════════════════
# NURSING MODULE
# ══════════════════════════════════════════════════════════════════════════════
# Structured nursing documentation — each assessment type is its own small,
# append-only model (mirrors the PeriopNursingAddendum pattern above: no edit
# view is provided for any of these, so "append-only" is enforced by omission
# rather than a DB constraint). All become part of the visit's EMR via their
# `visit` FK and are surfaced on visit_detail.html's Nursing section.

class NursingAssessment(models.Model):
    """Initial or Daily general nursing assessment — a broad systems review,
    distinct from the focused scored assessments below."""
    class AssessmentType(models.TextChoices):
        INITIAL = 'initial', 'Initial Nursing Assessment'
        DAILY   = 'daily',   'Daily Nursing Assessment'

    visit                  = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='nursing_assessments')
    assessment_type        = models.CharField(max_length=10, choices=AssessmentType.choices, default=AssessmentType.DAILY)
    general_appearance     = models.CharField(max_length=150, blank=True)
    level_of_consciousness = models.CharField(max_length=100, blank=True)
    skin_condition         = models.CharField(max_length=150, blank=True)
    mobility               = models.CharField(max_length=150, blank=True)
    respiratory_status     = models.CharField(max_length=150, blank=True)
    cardiovascular_status  = models.CharField(max_length=150, blank=True)
    elimination_status     = models.CharField(max_length=150, blank=True)
    psychosocial_status    = models.CharField(max_length=150, blank=True)
    notes                  = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='nursing_assessments')
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']
        verbose_name = 'Nursing Assessment'

    def __str__(self):
        return f'{self.get_assessment_type_display()} — {self.visit.patient.full_name} ({self.recorded_at:%d %b %Y})'


class PainAssessment(models.Model):
    class Character(models.TextChoices):
        SHARP     = 'sharp',     'Sharp'
        DULL      = 'dull',      'Dull'
        BURNING   = 'burning',   'Burning'
        THROBBING = 'throbbing', 'Throbbing'
        CRAMPING  = 'cramping',  'Cramping'
        OTHER     = 'other',     'Other'

    visit              = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='pain_assessments')
    pain_score         = models.PositiveSmallIntegerField(validators=[MaxValueValidator(10)])
    location           = models.CharField(max_length=150, blank=True)
    character          = models.CharField(max_length=15, choices=Character.choices, blank=True)
    onset              = models.CharField(max_length=100, blank=True)
    intervention       = models.CharField(max_length=200, blank=True)
    reassessment_score = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(10)])
    notes              = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='pain_assessments')
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']
        verbose_name = 'Pain Assessment'

    def __str__(self):
        return f'Pain {self.pain_score}/10 — {self.visit.patient.full_name} ({self.recorded_at:%d %b %Y %H:%M})'


class FallRiskAssessment(models.Model):
    """Morse Fall Scale. Total 0-125; risk_level uses standard Morse bands
    (Low <25, Moderate 25-44, High >=45)."""
    class AmbulatoryAid(models.IntegerChoices):
        NONE_BEDREST_ASSIST  = 0,  'None / Bed Rest / Nurse Assist'
        CRUTCHES_CANE_WALKER = 15, 'Crutches / Cane / Walker'
        FURNITURE            = 30, 'Furniture'

    class Gait(models.IntegerChoices):
        NORMAL_BEDREST_WHEELCHAIR = 0,  'Normal / Bed Rest / Wheelchair'
        WEAK                      = 10, 'Weak'
        IMPAIRED                  = 20, 'Impaired'

    class MentalStatus(models.IntegerChoices):
        ORIENTED       = 0,  'Oriented to Own Ability'
        OVERESTIMATES  = 15, 'Overestimates / Forgets Limitations'

    YES_NO_25 = [(0, 'No'), (25, 'Yes')]
    YES_NO_15 = [(0, 'No'), (15, 'Yes')]
    YES_NO_20 = [(0, 'No'), (20, 'Yes')]

    visit               = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='fall_risk_assessments')
    history_of_falling  = models.PositiveSmallIntegerField(choices=YES_NO_25, default=0)
    secondary_diagnosis = models.PositiveSmallIntegerField(choices=YES_NO_15, default=0)
    ambulatory_aid      = models.PositiveSmallIntegerField(choices=AmbulatoryAid.choices, default=AmbulatoryAid.NONE_BEDREST_ASSIST)
    iv_therapy          = models.PositiveSmallIntegerField(choices=YES_NO_20, default=0)
    gait                = models.PositiveSmallIntegerField(choices=Gait.choices, default=Gait.NORMAL_BEDREST_WHEELCHAIR)
    mental_status       = models.PositiveSmallIntegerField(choices=MentalStatus.choices, default=MentalStatus.ORIENTED)
    notes               = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='fall_risk_assessments')
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']
        verbose_name = 'Fall Risk Assessment'

    def __str__(self):
        return f'Fall Risk {self.total_score} ({self.risk_level}) — {self.visit.patient.full_name}'

    @property
    def total_score(self):
        return (
            self.history_of_falling + self.secondary_diagnosis + self.ambulatory_aid
            + self.iv_therapy + self.gait + self.mental_status
        )

    @property
    def risk_level(self):
        score = self.total_score
        if score >= 45:
            return 'High'
        if score >= 25:
            return 'Moderate'
        return 'Low'


class PressureUlcerAssessment(models.Model):
    """Braden Scale. Five subscales scored 1-4, Friction/Shear scored 1-3.
    Total 6-23; lower = higher risk (High <=12, Moderate 13-14, Low >=15)."""
    SCALE_1_4 = [(1, '1 — Most Severe'), (2, '2'), (3, '3'), (4, '4 — No Impairment')]

    class FrictionShear(models.IntegerChoices):
        PROBLEM            = 1, '1 — Problem'
        POTENTIAL_PROBLEM  = 2, '2 — Potential Problem'
        NO_PROBLEM         = 3, '3 — No Apparent Problem'

    visit               = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='pressure_ulcer_assessments')
    sensory_perception  = models.PositiveSmallIntegerField(choices=SCALE_1_4, default=4)
    moisture            = models.PositiveSmallIntegerField(choices=SCALE_1_4, default=4)
    activity            = models.PositiveSmallIntegerField(choices=SCALE_1_4, default=4)
    mobility            = models.PositiveSmallIntegerField(choices=SCALE_1_4, default=4)
    nutrition           = models.PositiveSmallIntegerField(choices=SCALE_1_4, default=4)
    friction_shear      = models.PositiveSmallIntegerField(choices=FrictionShear.choices, default=FrictionShear.NO_PROBLEM)
    notes               = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='pressure_ulcer_assessments')
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']
        verbose_name = 'Pressure Ulcer Risk Assessment (Braden)'

    def __str__(self):
        return f'Braden {self.total_score} ({self.risk_level}) — {self.visit.patient.full_name}'

    @property
    def total_score(self):
        return (
            self.sensory_perception + self.moisture + self.activity
            + self.mobility + self.nutrition + self.friction_shear
        )

    @property
    def risk_level(self):
        score = self.total_score
        if score <= 12:
            return 'High'
        if score <= 14:
            return 'Moderate'
        return 'Low'


class NutritionalAssessment(models.Model):
    """MUST (Malnutrition Universal Screening Tool). Total 0-6;
    0=Low risk, 1=Medium risk, >=2=High risk."""
    visit                = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='nutritional_assessments')
    bmi_score            = models.PositiveSmallIntegerField(
        choices=[(0, 'BMI > 20 (0)'), (1, 'BMI 18.5 - 20 (1)'), (2, 'BMI < 18.5 (2)')], default=0,
    )
    weight_loss_score    = models.PositiveSmallIntegerField(
        choices=[(0, 'Unplanned weight loss < 5% (0)'), (1, '5 - 10% (1)'), (2, '> 10% (2)')], default=0,
    )
    acute_disease_score  = models.PositiveSmallIntegerField(
        choices=[(0, 'No (0)'), (2, 'Acutely ill and no nutritional intake > 5 days (2)')], default=0,
    )
    dietary_notes        = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='nutritional_assessments')
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']
        verbose_name = 'Nutritional Assessment (MUST)'

    def __str__(self):
        return f'MUST {self.total_score} ({self.risk_level}) — {self.visit.patient.full_name}'

    @property
    def total_score(self):
        return self.bmi_score + self.weight_loss_score + self.acute_disease_score

    @property
    def risk_level(self):
        score = self.total_score
        if score == 0:
            return 'Low'
        if score == 1:
            return 'Medium'
        return 'High'


class FluidBalanceRecord(models.Model):
    """One row per shift/day of intake & output totals, not per single
    reading — matches how fluid balance charts are kept at the bedside."""
    class Shift(models.TextChoices):
        MORNING   = 'morning',   'Morning'
        AFTERNOON = 'afternoon', 'Afternoon'
        NIGHT     = 'night',     'Night'

    visit         = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='fluid_balance_records')
    record_date   = models.DateField(default=timezone.localdate)
    shift         = models.CharField(max_length=10, choices=Shift.choices, blank=True)
    intake_oral   = models.DecimalField(max_digits=7, decimal_places=1, default=0, help_text='mL')
    intake_iv     = models.DecimalField(max_digits=7, decimal_places=1, default=0, help_text='mL')
    intake_other  = models.DecimalField(max_digits=7, decimal_places=1, default=0, help_text='mL')
    output_urine  = models.DecimalField(max_digits=7, decimal_places=1, default=0, help_text='mL')
    output_drain  = models.DecimalField(max_digits=7, decimal_places=1, default=0, help_text='mL')
    output_other  = models.DecimalField(max_digits=7, decimal_places=1, default=0, help_text='mL')
    notes         = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='fluid_balance_records')
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-record_date', '-recorded_at']
        verbose_name = 'Fluid Balance / Intake & Output Record'

    def __str__(self):
        return f'I&O {self.record_date} ({self.get_shift_display() or "—"}) — {self.visit.patient.full_name}'

    @property
    def total_intake(self):
        return self.intake_oral + self.intake_iv + self.intake_other

    @property
    def total_output(self):
        return self.output_urine + self.output_drain + self.output_other

    @property
    def balance(self):
        return self.total_intake - self.total_output


class GlasgowComaScale(models.Model):
    """Total score 3-15 (Eye 1-4 + Verbal 1-5 + Motor 1-6)."""
    class EyeOpening(models.IntegerChoices):
        NONE        = 1, '1 — None'
        TO_PAIN     = 2, '2 — To Pain'
        TO_SPEECH   = 3, '3 — To Speech'
        SPONTANEOUS = 4, '4 — Spontaneous'

    class VerbalResponse(models.IntegerChoices):
        NONE             = 1, '1 — None'
        INCOMPREHENSIBLE = 2, '2 — Incomprehensible Sounds'
        INAPPROPRIATE    = 3, '3 — Inappropriate Words'
        CONFUSED         = 4, '4 — Confused'
        ORIENTED         = 5, '5 — Oriented'

    class MotorResponse(models.IntegerChoices):
        NONE       = 1, '1 — None'
        EXTENSION  = 2, '2 — Extension to Pain'
        FLEXION    = 3, '3 — Abnormal Flexion'
        WITHDRAWAL = 4, '4 — Withdrawal from Pain'
        LOCALIZES  = 5, '5 — Localizes Pain'
        OBEYS      = 6, '6 — Obeys Commands'

    visit           = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='glasgow_coma_scales')
    eye_opening     = models.PositiveSmallIntegerField(choices=EyeOpening.choices, default=EyeOpening.SPONTANEOUS)
    verbal_response = models.PositiveSmallIntegerField(choices=VerbalResponse.choices, default=VerbalResponse.ORIENTED)
    motor_response  = models.PositiveSmallIntegerField(choices=MotorResponse.choices, default=MotorResponse.OBEYS)
    notes           = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='glasgow_coma_scales')
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']
        verbose_name = 'Glasgow Coma Scale'

    def __str__(self):
        return f'GCS {self.total_score} — {self.visit.patient.full_name} ({self.recorded_at:%d %b %Y %H:%M})'

    @property
    def total_score(self):
        return self.eye_opening + self.verbal_response + self.motor_response


class NursingCarePlan(models.Model):
    """The only nursing-documentation type here that isn't append-only —
    care plans evolve over a stay, so a status-update view is expected
    (see plan) on top of the initial create."""
    class Status(models.TextChoices):
        ACTIVE   = 'active',   'Active'
        ONGOING  = 'ongoing',  'Ongoing'
        RESOLVED = 'resolved', 'Resolved'

    visit         = models.ForeignKey(Visit, on_delete=models.PROTECT, related_name='nursing_care_plans')
    problem       = models.CharField(max_length=300)
    goal          = models.CharField(max_length=300)
    interventions = models.TextField()
    evaluation    = models.TextField(blank=True)
    status        = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    target_date   = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='nursing_care_plans_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='nursing_care_plans_updated',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Nursing Care Plan'

    def __str__(self):
        return f'{self.problem} ({self.status}) — {self.visit.patient.full_name}'


class NursingHandoverNote(models.Model):
    """Shift handover — ward-level by default, optionally scoped to a
    specific patient and/or a specific incoming nurse."""
    class Shift(models.TextChoices):
        MORNING   = 'morning',   'Morning'
        AFTERNOON = 'afternoon', 'Afternoon'
        NIGHT     = 'night',     'Night'

    ward          = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name='handover_notes')
    shift         = models.CharField(max_length=10, choices=Shift.choices)
    handover_date = models.DateField(default=timezone.localdate)
    from_nurse    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='handovers_given',
    )
    to_nurse      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='handovers_received',
    )
    patient       = models.ForeignKey(
        Patient, on_delete=models.SET_NULL, null=True, blank=True, related_name='handover_notes',
    )
    content       = models.TextField()
    priority_flag = models.BooleanField(default=False)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-handover_date', '-created_at']
        verbose_name = 'Nursing Handover Note'

    def __str__(self):
        return f'{self.ward.name} — {self.get_shift_display()} handover ({self.handover_date})'


# ══════════════════════════════════════════════════════════════════════════════
# PATIENT ATTACHMENT & DOCUMENT MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

class AttachmentCategory(models.Model):
    """Admin-configurable document category. `restricted_to_roles` empty means
    any user with core.upload_attachment may use this category; non-empty
    restricts upload to members of those groups — the mechanism the spec's
    per-role category restrictions (e.g. Reception limited to ID/referral
    documents) are enforced through, with zero hardcoding."""
    name        = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active   = models.BooleanField(default=True)
    is_required_for_admission = models.BooleanField(
        default=False, help_text='Flag categories every admitted patient should have on file — drives the Missing Required Documents report.',
    )
    restricted_to_roles = models.ManyToManyField(Group, blank=True, related_name='attachment_categories')

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Attachment Category'
        verbose_name_plural = 'Attachment Categories'

    def __str__(self):
        return self.name

    def allowed_for(self, user):
        if not self.restricted_to_roles.exists():
            return True
        return self.restricted_to_roles.filter(user=user).exists()


ATTACHMENT_ALLOWED_EXTENSIONS = (
    'pdf', 'jpg', 'jpeg', 'png', 'tiff', 'tif', 'bmp',
    'doc', 'docx', 'xls', 'xlsx', 'csv', 'dcm', 'zip',
)
ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024  # 25 MB


def validate_attachment_file(file):
    ext = os.path.splitext(file.name)[1].lower().lstrip('.')
    if ext not in ATTACHMENT_ALLOWED_EXTENSIONS:
        raise ValidationError(f'Unsupported file type ".{ext}". Allowed: {", ".join(ATTACHMENT_ALLOWED_EXTENSIONS).upper()}.')
    if file.size > ATTACHMENT_MAX_BYTES:
        raise ValidationError(f'File must be smaller than {ATTACHMENT_MAX_BYTES // (1024 * 1024)} MB.')


def patient_attachment_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f'attachments/patient_{instance.patient_id}/{uuid.uuid4().hex}{ext}'


class PatientAttachment(models.Model):
    """A single uploaded document/image/scan tied to a patient, optionally
    linked to the specific visit/admission/surgery/lab/imaging/procedure it
    relates to. `patient` is always set (denormalized) since LabOrder and
    ImagingOrder only FK to Visit, not Patient — patient-level queries must
    not depend on an optional link being present.

    Replacing a file never overwrites a row — it creates a new row with
    version = old.version + 1, previous_version pointing at the old row, and
    flips the old row's is_current to False, preserving full version
    history. Deletion is always soft (is_deleted), reserved for
    Administrators — see core.delete_attachment."""
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='attachments')

    title       = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category    = models.ForeignKey(AttachmentCategory, on_delete=models.PROTECT, related_name='attachments')

    file      = models.FileField(upload_to=patient_attachment_upload_path, validators=[validate_attachment_file])
    file_name = models.CharField(max_length=255, blank=True)
    file_type = models.CharField(max_length=20, blank=True)
    file_size = models.PositiveIntegerField(default=0)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='patient_attachments_uploaded',
    )
    department  = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='patient_attachments',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    # Optional links to the clinical event this document relates to.
    visit         = models.ForeignKey('Visit', on_delete=models.SET_NULL, null=True, blank=True, related_name='attachments')
    admission     = models.ForeignKey('Admission', on_delete=models.SET_NULL, null=True, blank=True, related_name='attachments')
    surgery       = models.ForeignKey('SurgeryOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='attachments')
    lab_order     = models.ForeignKey('LabOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='attachments')
    imaging_order = models.ForeignKey('ImagingOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='attachments')
    procedure_order = models.ForeignKey('ProcedureOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='attachments')

    is_confidential = models.BooleanField(default=False)

    version          = models.PositiveIntegerField(default=1)
    previous_version = models.OneToOneField(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='next_version',
    )
    is_current = models.BooleanField(default=True)

    is_deleted    = models.BooleanField(default=False)
    deleted_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='patient_attachments_deleted',
    )
    deleted_at    = models.DateTimeField(null=True, blank=True)
    delete_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Patient Attachment'
        indexes = [
            models.Index(fields=['patient', 'is_current', 'is_deleted']),
            models.Index(fields=['category']),
        ]

    def __str__(self):
        return f'{self.patient.full_name} — {self.title} (v{self.version})'

    def save(self, *args, **kwargs):
        if self.file:
            self.file_name = self.file_name or os.path.basename(self.file.name)
            self.file_type = os.path.splitext(self.file.name)[1].lower().lstrip('.')
            try:
                self.file_size = self.file.size
            except (ValueError, OSError):
                pass
        super().save(*args, **kwargs)


class AttachmentComment(models.Model):
    attachment = models.ForeignKey(PatientAttachment, on_delete=models.CASCADE, related_name='comments')
    author     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='attachment_comments')
    comment    = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Attachment Comment'

    def __str__(self):
        return f'{self.author} on {self.attachment_id}: {self.comment[:40]}'



# ── Hospital Configuration ────────────────────────────────────────────────────

class HospitalProfile(models.Model):
    """Singleton: hospital identity & branding — one row per deployment."""

    name          = models.CharField(max_length=200, default='Hospital Management System')
    short_name    = models.CharField(max_length=60, blank=True, help_text='Abbreviated name for tight spaces')
    tagline       = models.CharField(max_length=200, blank=True)
    logo          = models.ImageField(upload_to='hospital/', null=True, blank=True)
    stamp         = models.ImageField(upload_to='hospital/', null=True, blank=True, help_text='Official stamp/seal for documents')

    # Contact
    address       = models.TextField(blank=True)
    city          = models.CharField(max_length=100, blank=True)
    region        = models.CharField(max_length=100, blank=True)
    country       = models.CharField(max_length=100, default='Ethiopia')
    phone         = models.CharField(max_length=50, blank=True)
    phone_alt     = models.CharField(max_length=50, blank=True)
    email         = models.EmailField(blank=True)
    website       = models.URLField(blank=True)

    # Legal / Financial
    license_number = models.CharField(max_length=100, blank=True, verbose_name='License Number')
    tin            = models.CharField(max_length=50,  blank=True, verbose_name='TIN')
    currency       = models.CharField(max_length=10,  default='ETB')
    currency_symbol = models.CharField(max_length=5,  default='ETB')

    # Locale
    timezone  = models.CharField(max_length=60, default='Africa/Addis_Ababa')
    language  = models.CharField(max_length=10, default='en')

    # Print document headers / footers
    report_header = models.TextField(blank=True, help_text='Shown at top of printed reports')
    report_footer = models.TextField(blank=True, help_text='Shown at bottom of printed documents')

    # Appearance
    primary_color = models.CharField(max_length=7, default='#2563eb', help_text='Hex colour for UI accent')

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Hospital Profile'

    def __str__(self):
        return self.name

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def display_address(self):
        parts = [p for p in [self.address, self.city, self.region, self.country] if p]
        return ', '.join(parts)


class SystemModule(models.Model):
    """Feature flags — enable/disable whole functional areas per deployment."""

    class Category(models.TextChoices):
        CLINICAL       = 'clinical',        'Clinical'
        DIAGNOSTIC     = 'diagnostic',      'Diagnostic'
        OPERATIONS     = 'operations',      'Operations'
        ADMINISTRATIVE = 'administrative',  'Administrative'
        FINANCIAL      = 'financial',       'Financial'
        HR             = 'hr',              'Human Resources'

    name        = models.SlugField(unique=True, help_text='Unique machine-readable key')
    label       = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    category    = models.CharField(max_length=20, choices=Category.choices, default=Category.ADMINISTRATIVE)
    icon_svg    = models.TextField(blank=True, help_text='SVG path data for the module icon')
    is_enabled  = models.BooleanField(default=True)
    is_core     = models.BooleanField(default=False, help_text='Core modules cannot be disabled')
    order       = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['category', 'order', 'label']
        verbose_name = 'System Module'

    def __str__(self):
        return f"{self.label} ({'on' if self.is_enabled else 'off'})"

    @classmethod
    def enabled_set(cls):
        """Return a set of enabled module name slugs (cached per process cycle)."""
        return set(cls.objects.filter(is_enabled=True).values_list('name', flat=True))


class SystemVersion(models.Model):
    """Track software versions and release history."""

    version       = models.CharField(max_length=20, help_text='SemVer e.g. 1.0.0')
    build         = models.CharField(max_length=50, blank=True)
    release_date  = models.DateField()
    release_notes = models.TextField(blank=True)
    db_schema_ver = models.CharField(max_length=20, blank=True, verbose_name='DB Schema Version')
    api_version   = models.CharField(max_length=20, blank=True)
    is_current    = models.BooleanField(default=False)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-release_date', '-id']
        verbose_name = 'System Version'

    def __str__(self):
        return f"v{self.version}"

    def save(self, *args, **kwargs):
        if self.is_current:
            SystemVersion.objects.exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)


class LicenseInfo(models.Model):
    """License record for this deployment."""

    class LicenseType(models.TextChoices):
        TRIAL        = 'trial',        'Trial'
        BASIC        = 'basic',        'Basic'
        STANDARD     = 'standard',     'Standard'
        PROFESSIONAL = 'professional', 'Professional'
        ENTERPRISE   = 'enterprise',   'Enterprise'

    class Status(models.TextChoices):
        ACTIVE    = 'active',    'Active'
        EXPIRED   = 'expired',   'Expired'
        SUSPENDED = 'suspended', 'Suspended'

    hospital_name      = models.CharField(max_length=200)
    license_key        = models.CharField(max_length=255, unique=True)
    license_type       = models.CharField(max_length=20, choices=LicenseType.choices, default=LicenseType.STANDARD)
    max_users          = models.PositiveIntegerField(default=0, help_text='0 = unlimited')
    max_branches       = models.PositiveIntegerField(default=1)
    issued_at          = models.DateField()
    expiration_date    = models.DateField(null=True, blank=True)
    support_expiration = models.DateField(null=True, blank=True)
    status             = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    notes              = models.TextField(blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'License'
        ordering = ['-issued_at']

    def __str__(self):
        return f"{self.hospital_name} — {self.get_license_type_display()} ({self.status})"

    @property
    def is_active(self):
        if self.status != self.Status.ACTIVE:
            return False
        if self.expiration_date and self.expiration_date < timezone.localdate():
            return False
        return True


# ═══════════════════════════════════════════════════════════════════════════════
#  OB/GYN MODULE
# ═══════════════════════════════════════════════════════════════════════════════

class Pregnancy(models.Model):
    """Central pregnancy record — one row per pregnancy episode."""

    class Status(models.TextChoices):
        ACTIVE    = 'active',    'Active / Ongoing'
        DELIVERED = 'delivered', 'Delivered'
        ABORTED   = 'aborted',   'Aborted'
        ECTOPIC   = 'ectopic',   'Ectopic'
        MOLAR     = 'molar',     'Molar Pregnancy'
        CLOSED    = 'closed',    'Closed'

    class BloodGroup(models.TextChoices):
        A_POS  = 'A+',  'A+'
        A_NEG  = 'A-',  'A-'
        B_POS  = 'B+',  'B+'
        B_NEG  = 'B-',  'B-'
        AB_POS = 'AB+', 'AB+'
        AB_NEG = 'AB-', 'AB-'
        O_POS  = 'O+',  'O+'
        O_NEG  = 'O-',  'O-'
        UNKNOWN = 'Unknown', 'Unknown'

    patient        = models.ForeignKey('Patient',  on_delete=models.CASCADE, related_name='pregnancies')
    registration_number = models.CharField(max_length=30, unique=True, blank=True)
    lmp            = models.DateField(verbose_name='Last Menstrual Period', null=True, blank=True)
    edd            = models.DateField(verbose_name='Estimated Due Date', null=True, blank=True)
    edd_by_scan    = models.DateField(verbose_name='EDD by Ultrasound', null=True, blank=True)
    gravida        = models.PositiveSmallIntegerField(default=1, help_text='Total pregnancies including this one')
    para           = models.PositiveSmallIntegerField(default=0, help_text='Prior deliveries ≥ 20 weeks')
    abortion       = models.PositiveSmallIntegerField(default=0)
    living_children= models.PositiveSmallIntegerField(default=0)
    blood_group    = models.CharField(max_length=10, choices=BloodGroup.choices, blank=True, default='')
    rhesus         = models.CharField(max_length=10, choices=[('Positive','Positive'),('Negative','Negative'),('Unknown','Unknown')], blank=True, default='Unknown')
    height_cm      = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    pre_pregnancy_weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    risk_factors   = models.TextField(blank=True)
    medical_history= models.TextField(blank=True, verbose_name='Relevant Medical History')
    surgical_history = models.TextField(blank=True)
    family_history = models.TextField(blank=True)
    allergies      = models.TextField(blank=True)
    status         = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    registered_by  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='pregnancies_registered')
    notes          = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Pregnancy'
        verbose_name_plural = 'Pregnancies'

    def __str__(self):
        return f"Pregnancy #{self.pk} — {self.patient} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.registration_number:
            super().save(*args, **kwargs)
            self.registration_number = f"ANC-{self.pk:06d}"
            Pregnancy.objects.filter(pk=self.pk).update(registration_number=self.registration_number)
            return
        if self.lmp and not self.edd:
            from datetime import timedelta
            self.edd = self.lmp + timedelta(days=280)
        super().save(*args, **kwargs)

    @property
    def gestational_age_weeks(self):
        if not self.lmp:
            return None
        from datetime import date
        delta = date.today() - self.lmp
        return delta.days // 7

    @property
    def ga_display(self):
        weeks = self.gestational_age_weeks
        if weeks is None:
            return 'N/A'
        days = (self.lmp and ((timezone.localdate() - self.lmp).days % 7)) or 0
        return f"{weeks}w {days}d"


class ANCVisit(models.Model):
    """Antenatal care visit — one row per ANC appointment."""

    class Presentation(models.TextChoices):
        CEPHALIC    = 'cephalic',    'Cephalic'
        BREECH      = 'breech',      'Breech'
        TRANSVERSE  = 'transverse',  'Transverse'
        OBLIQUE     = 'oblique',     'Oblique'
        UNSTABLE    = 'unstable',    'Unstable'
        UNKNOWN     = 'unknown',     'Not Assessed'

    class FHRStatus(models.TextChoices):
        HEARD    = 'heard',    'Heard'
        NOT_HEARD= 'not_heard','Not Heard'
        NA       = 'na',       'N/A'

    pregnancy    = models.ForeignKey(Pregnancy, on_delete=models.CASCADE, related_name='anc_visits')
    visit        = models.ForeignKey('Visit', on_delete=models.SET_NULL, null=True, blank=True, related_name='anc_visits')
    visit_number = models.PositiveSmallIntegerField(default=1)
    visit_date   = models.DateField()
    ga_weeks     = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Gestational Age (weeks)')
    ga_days      = models.PositiveSmallIntegerField(null=True, blank=True, default=0)
    weight_kg    = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    bp_systolic  = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse        = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature  = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    fundal_height_cm = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    presentation = models.CharField(max_length=20, choices=Presentation.choices, default=Presentation.UNKNOWN)
    fetal_heart_rate = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='FHR (bpm)')
    fhr_status   = models.CharField(max_length=10, choices=FHRStatus.choices, default=FHRStatus.NA)
    fetal_movement = models.BooleanField(null=True, blank=True)
    oedema       = models.CharField(max_length=100, blank=True)
    urine_protein= models.CharField(max_length=20, blank=True, choices=[('negative','Negative'),('trace','Trace'),('+1','+1'),('+2','+2'),('+3','+3')])
    urine_glucose= models.CharField(max_length=20, blank=True, choices=[('negative','Negative'),('positive','Positive')])
    hb_level     = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, verbose_name='Haemoglobin (g/dL)')
    iron_given   = models.BooleanField(default=False)
    folic_given  = models.BooleanField(default=False)
    tt_given     = models.BooleanField(default=False, verbose_name='TT Vaccination Given')
    ipt_given    = models.BooleanField(default=False, verbose_name='IPT Given')
    llin_given   = models.BooleanField(default=False, verbose_name='LLIN Given')
    clinical_notes  = models.TextField(blank=True)
    management_plan = models.TextField(blank=True)
    next_visit_date = models.DateField(null=True, blank=True)
    seen_by      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='anc_visits_seen')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['visit_date']
        verbose_name = 'ANC Visit'
        verbose_name_plural = 'ANC Visits'

    def __str__(self):
        return f"ANC Visit #{self.visit_number} — {self.pregnancy} on {self.visit_date}"


class DeliveryRecord(models.Model):
    """Labor and delivery record — one per delivery event."""

    class DeliveryMode(models.TextChoices):
        SVD        = 'svd',        'Spontaneous Vaginal Delivery (SVD)'
        ASSISTED   = 'assisted',   'Assisted Vaginal Delivery'
        ELECTIVE_CS= 'elective_cs','Elective Caesarean Section'
        EMERGENCY_CS='emergency_cs','Emergency Caesarean Section'
        BREECH     = 'breech',     'Breech Delivery'
        STILLBIRTH = 'stillbirth', 'Stillbirth'
        ABORTION   = 'abortion',   'Abortion / Miscarriage'

    class Outcome(models.TextChoices):
        LIVE_BIRTH   = 'live_birth',   'Live Birth'
        STILLBIRTH   = 'stillbirth',   'Stillbirth'
        NEONATAL_DEATH='neonatal_death','Neonatal Death'
        MATERNAL_DEATH='maternal_death','Maternal Death'

    class Episiotomy(models.TextChoices):
        NONE       = 'none',     'None'
        MEDIOLATERAL='mediolateral','Mediolateral'
        MEDIAN     = 'median',   'Median'

    pregnancy       = models.OneToOneField(Pregnancy, on_delete=models.CASCADE, related_name='delivery')
    visit           = models.ForeignKey('Visit', on_delete=models.SET_NULL, null=True, blank=True, related_name='deliveries')
    admission        = models.ForeignKey('Admission', on_delete=models.SET_NULL, null=True, blank=True, related_name='deliveries')
    delivery_date   = models.DateField()
    delivery_time   = models.TimeField(null=True, blank=True)
    delivery_mode   = models.CharField(max_length=20, choices=DeliveryMode.choices)
    outcome         = models.CharField(max_length=20, choices=Outcome.choices, default=Outcome.LIVE_BIRTH)
    ga_at_delivery  = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='GA at Delivery (weeks)')
    blood_loss_ml   = models.PositiveIntegerField(null=True, blank=True, verbose_name='Estimated Blood Loss (mL)')
    placenta_complete = models.BooleanField(null=True, blank=True)
    episiotomy      = models.CharField(max_length=20, choices=Episiotomy.choices, default=Episiotomy.NONE)
    tear_degree     = models.CharField(max_length=50, blank=True)
    repair_done     = models.BooleanField(null=True, blank=True)
    complications   = models.TextField(blank=True)
    oxytocin_given  = models.BooleanField(default=False)
    bp_systolic_delivery = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic_delivery= models.PositiveSmallIntegerField(null=True, blank=True)
    attended_by     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='deliveries_attended')
    midwife         = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='deliveries_midwifed')
    notes           = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-delivery_date']
        verbose_name = 'Delivery Record'

    def __str__(self):
        return f"Delivery — {self.pregnancy.patient} on {self.delivery_date}"


class NewbornRecord(models.Model):
    """Newborn record — one per neonate delivered."""

    class Sex(models.TextChoices):
        MALE    = 'male',    'Male'
        FEMALE  = 'female',  'Female'
        AMBIGUOUS = 'ambiguous', 'Ambiguous'

    class Condition(models.TextChoices):
        ALIVE   = 'alive',   'Alive & Well'
        DISTRESS= 'distress','In Distress'
        DEAD    = 'dead',    'Dead at Birth'

    delivery       = models.ForeignKey(DeliveryRecord, on_delete=models.CASCADE, related_name='newborns')
    birth_order    = models.PositiveSmallIntegerField(default=1, help_text='1 = singleton, 2+ = twin/triplet order')
    sex            = models.CharField(max_length=15, choices=Sex.choices)
    birth_weight_g = models.PositiveIntegerField(null=True, blank=True, verbose_name='Birth Weight (g)')
    birth_length_cm= models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    head_circumference_cm = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    apgar_1min     = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)])
    apgar_5min     = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)])
    apgar_10min    = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)])
    condition      = models.CharField(max_length=15, choices=Condition.choices, default=Condition.ALIVE)
    resuscitation  = models.BooleanField(default=False)
    resuscitation_notes = models.TextField(blank=True)
    congenital_anomaly = models.BooleanField(default=False)
    anomaly_description = models.TextField(blank=True)
    vitamin_k_given= models.BooleanField(default=False)
    eye_prophylaxis= models.BooleanField(default=False)
    bcg_given      = models.BooleanField(default=False)
    hepatitis_b_given = models.BooleanField(default=False)
    breastfed_immediately = models.BooleanField(null=True, blank=True)
    nicu_admission = models.BooleanField(default=False)
    notes          = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['birth_order']
        verbose_name = 'Newborn Record'

    def __str__(self):
        return f"Newborn #{self.birth_order} — {self.delivery}"


class PNCVisit(models.Model):
    """Postnatal care visit."""

    class Status(models.TextChoices):
        NORMAL     = 'normal',    'Normal'
        CONCERNS   = 'concerns',  'With Concerns'
        REFERRED   = 'referred',  'Referred'

    delivery     = models.ForeignKey(DeliveryRecord, on_delete=models.CASCADE, related_name='pnc_visits')
    visit        = models.ForeignKey('Visit', on_delete=models.SET_NULL, null=True, blank=True, related_name='pnc_visits')
    visit_number = models.PositiveSmallIntegerField(default=1)
    visit_date   = models.DateField()
    days_postpartum = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_systolic  = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature  = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    weight_kg    = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    lochia       = models.CharField(max_length=100, blank=True)
    uterus_involution = models.CharField(max_length=100, blank=True)
    perineum     = models.CharField(max_length=100, blank=True)
    breastfeeding = models.BooleanField(null=True, blank=True)
    breast_condition = models.CharField(max_length=100, blank=True)
    newborn_condition = models.CharField(max_length=200, blank=True)
    family_planning_counseled = models.BooleanField(default=False)
    fp_method_chosen = models.CharField(max_length=100, blank=True)
    immunization_updated = models.BooleanField(default=False)
    hiv_test_done = models.BooleanField(default=False)
    status       = models.CharField(max_length=15, choices=Status.choices, default=Status.NORMAL)
    clinical_notes  = models.TextField(blank=True)
    next_visit_date = models.DateField(null=True, blank=True)
    seen_by      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='pnc_visits_seen')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['visit_date']
        verbose_name = 'PNC Visit'
        verbose_name_plural = 'PNC Visits'

    def __str__(self):
        return f"PNC Visit #{self.visit_number} — {self.delivery.pregnancy.patient} on {self.visit_date}"


class GynecologyConsultation(models.Model):
    """Gynecology OPD/IPD consultation record."""

    class Category(models.TextChoices):
        MENSTRUAL    = 'menstrual',    'Menstrual Disorder'
        CONTRACEPTION= 'contraception','Contraception / Family Planning'
        STI          = 'sti',          'STI / Vaginal Infection'
        FIBROID      = 'fibroid',      'Fibroid / Uterine Mass'
        OVARIAN_CYST = 'ovarian_cyst', 'Ovarian Cyst'
        ENDOMETRIOSIS= 'endometriosis','Endometriosis'
        CANCER_SCREEN= 'cancer_screen','Cancer Screening (Pap/VIA)'
        INFERTILITY  = 'infertility',  'Infertility'
        MENOPAUSE    = 'menopause',    'Menopause'
        PELVIC_PAIN  = 'pelvic_pain',  'Pelvic Pain'
        INCONTINENCE = 'incontinence', 'Urinary Incontinence / Prolapse'
        OTHER        = 'other',        'Other'

    patient        = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='gyn_consultations')
    visit          = models.ForeignKey('Visit', on_delete=models.SET_NULL, null=True, blank=True, related_name='gyn_consultations')
    consultation_date = models.DateField()
    category       = models.CharField(max_length=25, choices=Category.choices, default=Category.OTHER)
    chief_complaint= models.TextField()
    # Menstrual history
    menarche_age   = models.PositiveSmallIntegerField(null=True, blank=True)
    cycle_length_days = models.PositiveSmallIntegerField(null=True, blank=True)
    menstrual_duration_days = models.PositiveSmallIntegerField(null=True, blank=True)
    lmp            = models.DateField(null=True, blank=True)
    menstrual_pattern = models.CharField(max_length=200, blank=True)
    dysmenorrhoea  = models.BooleanField(null=True, blank=True)
    # Obstetric summary
    gravida        = models.PositiveSmallIntegerField(null=True, blank=True)
    para           = models.PositiveSmallIntegerField(null=True, blank=True)
    # Contraception
    current_contraception = models.CharField(max_length=200, blank=True)
    # Examination
    bmi            = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    bp_systolic    = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic   = models.PositiveSmallIntegerField(null=True, blank=True)
    per_speculum   = models.TextField(blank=True, verbose_name='Per Speculum Exam')
    per_vaginum    = models.TextField(blank=True, verbose_name='Per Vaginum Exam')
    uterus_size    = models.CharField(max_length=100, blank=True)
    adnexa         = models.CharField(max_length=200, blank=True)
    # Investigations & plan
    investigations_ordered = models.TextField(blank=True)
    diagnosis      = models.TextField(blank=True)
    treatment_plan = models.TextField(blank=True)
    via_result     = models.CharField(max_length=100, blank=True, verbose_name='VIA/VILI Result')
    pap_result     = models.CharField(max_length=100, blank=True, verbose_name='Pap Smear Result')
    referral       = models.CharField(max_length=200, blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    seen_by        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='gyn_consultations_seen')
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-consultation_date', '-created_at']
        verbose_name = 'Gynecology Consultation'

    def __str__(self):
        return f"Gyn Consult — {self.patient} ({self.get_category_display()}) on {self.consultation_date}"


class FamilyPlanningVisit(models.Model):
    """Family planning counseling and service visit."""

    class Method(models.TextChoices):
        OCP         = 'ocp',         'Oral Contraceptive Pill'
        POP         = 'pop',         'Progestin-Only Pill'
        INJECTABLE  = 'injectable',  'Injectable (Depo-Provera)'
        IMPLANT     = 'implant',     'Implant (Norplant/Implanon)'
        IUCD        = 'iucd',        'IUCD / Copper-T'
        CONDOM_MALE = 'condom_male', 'Male Condom'
        CONDOM_FEMALE='condom_female','Female Condom'
        LAM         = 'lam',         'LAM (Lactational Amenorrhoea)'
        STERILIZATION='sterilization','Permanent Sterilization'
        NATURAL     = 'natural',     'Natural Family Planning'
        NONE        = 'none',        'None / Discontinuation'
        OTHER       = 'other',       'Other'

    class VisitType(models.TextChoices):
        NEW         = 'new',         'New Acceptor'
        CONTINUING  = 'continuing',  'Continuing User'
        CHANGE      = 'change',      'Method Change'
        DISCONTINUE = 'discontinue', 'Discontinuation'
        FOLLOW_UP   = 'follow_up',   'Follow-up / Complication'

    patient        = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='fp_visits')
    visit          = models.ForeignKey('Visit', on_delete=models.SET_NULL, null=True, blank=True, related_name='fp_visits')
    visit_date     = models.DateField()
    visit_type     = models.CharField(max_length=20, choices=VisitType.choices, default=VisitType.NEW)
    method         = models.CharField(max_length=20, choices=Method.choices)
    previous_method= models.CharField(max_length=20, choices=Method.choices, blank=True, default='')
    counseling_done= models.BooleanField(default=True)
    bp_systolic    = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic   = models.PositiveSmallIntegerField(null=True, blank=True)
    weight_kg      = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    lmp            = models.DateField(null=True, blank=True)
    pregnancy_test = models.CharField(max_length=20, blank=True, choices=[('positive','Positive'),('negative','Negative'),('not_done','Not Done')])
    complications  = models.TextField(blank=True)
    side_effects   = models.TextField(blank=True)
    notes          = models.TextField(blank=True)
    next_visit_date= models.DateField(null=True, blank=True)
    seen_by        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='fp_visits_seen')
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-visit_date']
        verbose_name = 'Family Planning Visit'

    def __str__(self):
        return f"FP Visit — {self.patient} ({self.get_method_display()}) on {self.visit_date}"


class InfertilityCase(models.Model):
    """Infertility investigation and management case."""

    class Type(models.TextChoices):
        PRIMARY   = 'primary',   'Primary Infertility'
        SECONDARY = 'secondary', 'Secondary Infertility'

    class Status(models.TextChoices):
        ACTIVE    = 'active',    'Active'
        PREGNANT  = 'pregnant',  'Achieved Pregnancy'
        REFERRED  = 'referred',  'Referred to Specialist'
        CLOSED    = 'closed',    'Closed'

    patient        = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='infertility_cases')
    partner_name   = models.CharField(max_length=150, blank=True)
    partner_dob    = models.DateField(null=True, blank=True)
    partner_contact= models.CharField(max_length=30, blank=True)
    infertility_type = models.CharField(max_length=15, choices=Type.choices)
    duration_years = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Duration of Infertility (years)')
    status         = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    # Female factors
    menstrual_history = models.TextField(blank=True)
    tubal_factor   = models.BooleanField(null=True, blank=True)
    ovulatory_factor = models.BooleanField(null=True, blank=True)
    uterine_factor = models.BooleanField(null=True, blank=True)
    cervical_factor= models.BooleanField(null=True, blank=True)
    endometriosis_factor = models.BooleanField(null=True, blank=True)
    # Male factors
    male_factor    = models.BooleanField(null=True, blank=True)
    semen_analysis = models.TextField(blank=True)
    # Investigations
    hsg_result     = models.TextField(blank=True, verbose_name='HSG Result')
    hormone_profile= models.TextField(blank=True)
    ultrasound_findings = models.TextField(blank=True)
    # Treatment
    treatment_plan = models.TextField(blank=True)
    ovulation_induction = models.BooleanField(default=False)
    iui_done       = models.BooleanField(default=False, verbose_name='IUI Done')
    referral_reason= models.TextField(blank=True)
    notes          = models.TextField(blank=True)
    opened_by      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='infertility_cases_opened')
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Infertility Case'

    def __str__(self):
        return f"Infertility Case — {self.patient} ({self.get_infertility_type_display()})"


# ═══════════════════════════════════════════════════════════════════════════════
#  INTERNAL MEDICINE MODULE
# ═══════════════════════════════════════════════════════════════════════════════

class InternalMedicineConsultation(models.Model):
    class ClinicType(models.TextChoices):
        GENERAL       = 'general',       'General Medical'
        HYPERTENSION  = 'hypertension',  'Hypertension Clinic'
        DIABETES      = 'diabetes',      'Diabetes Clinic'
        CARDIAC       = 'cardiac',       'Cardiac Clinic'
        ENDOCRINOLOGY = 'endocrinology', 'Endocrinology'
        GASTRO        = 'gastro',        'Gastroenterology'
        INFECTIOUS    = 'infectious',    'Infectious Diseases'
        RESPIRATORY   = 'respiratory',   'Respiratory Diseases'
        NEPHROLOGY    = 'nephrology',    'Nephrology'
        RHEUMATOLOGY  = 'rheumatology',  'Rheumatology'
        CHRONIC_FU    = 'chronic_fu',    'Chronic Disease Follow-up'

    class VisitType(models.TextChoices):
        NEW     = 'new',     'New Patient'
        FOLLOWUP= 'followup','Follow-up'
        REVIEW  = 'review',  'Medication Review'

    patient          = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='im_consultations')
    visit            = models.ForeignKey('Visit', on_delete=models.SET_NULL, null=True, blank=True, related_name='im_consultations')
    consultation_date= models.DateField()
    clinic_type      = models.CharField(max_length=20, choices=ClinicType.choices, default=ClinicType.GENERAL)
    visit_type       = models.CharField(max_length=10, choices=VisitType.choices, default=VisitType.NEW)
    clinician        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='im_consultations')

    # Vitals
    bp_systolic    = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic   = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse          = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature    = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    weight_kg      = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    height_cm      = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    spo2           = models.PositiveSmallIntegerField(null=True, blank=True)
    rr             = models.PositiveSmallIntegerField(null=True, blank=True)
    fbs            = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True, verbose_name='Fasting Blood Sugar (mmol/L)')
    rbs            = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True, verbose_name='Random Blood Sugar (mmol/L)')
    hba1c          = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, verbose_name='HbA1c (%)')

    # SOAP
    chief_complaint          = models.TextField()
    history_of_presenting    = models.TextField(blank=True)
    past_medical_history     = models.TextField(blank=True)
    family_history           = models.TextField(blank=True)
    social_history           = models.TextField(blank=True)
    current_medications      = models.TextField(blank=True)
    allergies                = models.TextField(blank=True)
    review_of_systems        = models.TextField(blank=True)
    physical_examination     = models.TextField(blank=True)

    # Assessment & Plan
    diagnosis       = models.TextField(blank=True)
    icd10_code      = models.CharField(max_length=15, blank=True)
    plan            = models.TextField(blank=True)
    investigations  = models.TextField(blank=True)
    referral        = models.CharField(max_length=200, blank=True)
    follow_up_date  = models.DateField(null=True, blank=True)
    follow_up_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-consultation_date', '-created_at']
        verbose_name = 'Internal Medicine Consultation'

    def __str__(self):
        return f"IM – {self.patient} ({self.get_clinic_type_display()}) {self.consultation_date}"

    @property
    def bmi(self):
        if self.weight_kg and self.height_cm and float(self.height_cm) > 0:
            h = float(self.height_cm) / 100
            return round(float(self.weight_kg) / (h * h), 1)
        return None

    @property
    def bp_display(self):
        if self.bp_systolic and self.bp_diastolic:
            return f"{self.bp_systolic}/{self.bp_diastolic}"
        return '—'


class ChronicDiseasePlan(models.Model):
    class Disease(models.TextChoices):
        HYPERTENSION = 'hypertension', 'Hypertension'
        DM_TYPE1     = 'dm_t1',        'Type 1 Diabetes Mellitus'
        DM_TYPE2     = 'dm_t2',        'Type 2 Diabetes Mellitus'
        HEART_FAILURE= 'heart_failure','Heart Failure'
        CKD          = 'ckd',          'Chronic Kidney Disease'
        COPD         = 'copd',         'COPD'
        ASTHMA       = 'asthma',       'Asthma'
        THYROID      = 'thyroid',      'Thyroid Disease'
        RHEUMATOID   = 'rheumatoid',   'Rheumatoid Arthritis'
        EPILEPSY     = 'epilepsy',     'Epilepsy'
        OTHER        = 'other',        'Other'

    class Stage(models.TextChoices):
        MILD         = 'mild',    'Mild / Stage 1'
        MODERATE     = 'moderate','Moderate / Stage 2'
        SEVERE       = 'severe',  'Severe / Stage 3'
        VERY_SEVERE  = 'very_severe', 'Very Severe / Stage 4'
        UNSPECIFIED  = 'unspecified', 'Unspecified'

    patient              = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='chronic_plans')
    disease              = models.CharField(max_length=20, choices=Disease.choices)
    stage                = models.CharField(max_length=20, choices=Stage.choices, default=Stage.UNSPECIFIED)
    date_diagnosed       = models.DateField(null=True, blank=True)
    treatment_goals      = models.TextField(blank=True)
    medications          = models.TextField(blank=True)
    lifestyle_mods       = models.TextField(blank=True, verbose_name='Lifestyle Modifications')
    monitoring_frequency = models.CharField(max_length=100, blank=True)
    target_bp            = models.CharField(max_length=20, blank=True)
    target_hba1c         = models.CharField(max_length=20, blank=True)
    target_glucose       = models.CharField(max_length=50, blank=True)
    is_controlled        = models.BooleanField(default=False)
    last_review          = models.DateField(null=True, blank=True)
    next_review          = models.DateField(null=True, blank=True)
    notes                = models.TextField(blank=True)
    clinician            = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='chronic_plans')
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['disease']
        verbose_name = 'Chronic Disease Plan'

    def __str__(self):
        return f"CDP – {self.patient} – {self.get_disease_display()}"


class ClinicalRiskAssessment(models.Model):
    class RiskType(models.TextChoices):
        CARDIOVASCULAR = 'cardiovascular', 'Cardiovascular Risk'
        DIABETES       = 'diabetes',       'Diabetes Risk'
        STROKE         = 'stroke',         'Stroke Risk (CHA₂DS₂-VASc)'
        RENAL          = 'renal',          'Renal Risk'
        FALLS          = 'falls',          'Falls Risk'
        SEPSIS         = 'sepsis',         'Sepsis Risk (qSOFA)'
        OTHER          = 'other',          'Other'

    class RiskLevel(models.TextChoices):
        LOW       = 'low',       'Low'
        MODERATE  = 'moderate',  'Moderate'
        HIGH      = 'high',      'High'
        VERY_HIGH = 'very_high', 'Very High'

    patient         = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='risk_assessments')
    consultation    = models.ForeignKey(InternalMedicineConsultation, on_delete=models.SET_NULL, null=True, blank=True, related_name='risk_assessments')
    assessment_date = models.DateField()
    risk_type       = models.CharField(max_length=20, choices=RiskType.choices)
    score_name      = models.CharField(max_length=60, blank=True, help_text='e.g. Framingham, QRISK3, CHA2DS2-VASc')
    score_value     = models.CharField(max_length=20, blank=True)
    risk_level      = models.CharField(max_length=15, choices=RiskLevel.choices)
    risk_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    recommendations = models.TextField(blank=True)
    clinician       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='risk_assessments')
    notes           = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-assessment_date']
        verbose_name = 'Clinical Risk Assessment'

    def __str__(self):
        return f"Risk – {self.patient} – {self.get_risk_type_display()} ({self.assessment_date})"


# ═══════════════════════════════════════════════════════════════════════════════
#  PEDIATRICS MODULE
# ═══════════════════════════════════════════════════════════════════════════════

class PediatricConsultation(models.Model):
    class VisitType(models.TextChoices):
        NEW           = 'new',          'New Patient'
        FOLLOWUP      = 'followup',     'Follow-up'
        SICK          = 'sick',         'Sick Child'
        WELL_CHILD    = 'well',         'Well Child / Routine'
        IMMUNIZATION  = 'immunization', 'Immunization Only'
        EMERGENCY     = 'emergency',    'Emergency / Acute'
        NEONATAL      = 'neonatal',     'Neonatal / Newborn'
        DEVELOPMENTAL = 'developmental','Developmental Assessment'
        NUTRITION     = 'nutrition',    'Nutrition Assessment'

    patient          = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='peds_consultations')
    visit            = models.ForeignKey('Visit', on_delete=models.SET_NULL, null=True, blank=True, related_name='peds_consultations')
    consultation_date= models.DateField()
    visit_type       = models.CharField(max_length=20, choices=VisitType.choices, default=VisitType.NEW)
    clinician        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='peds_consultations')

    # Vitals & Anthropometry
    weight_kg          = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    height_cm          = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    head_circumference = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    muac_cm            = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, verbose_name='MUAC (cm)')
    temperature        = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    pulse              = models.PositiveSmallIntegerField(null=True, blank=True)
    rr                 = models.PositiveSmallIntegerField(null=True, blank=True)
    spo2               = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_systolic        = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic       = models.PositiveSmallIntegerField(null=True, blank=True)

    # WHO Z-scores
    weight_for_age_z    = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    height_for_age_z    = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    weight_for_height_z = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)

    # History
    chief_complaint       = models.TextField()
    history               = models.TextField(blank=True)
    birth_history         = models.TextField(blank=True)
    feeding_history       = models.TextField(blank=True)
    immunization_history  = models.TextField(blank=True)
    developmental_history = models.TextField(blank=True)
    family_history        = models.TextField(blank=True)
    social_history        = models.TextField(blank=True)

    # Examination
    general_appearance  = models.TextField(blank=True)
    systems_examination = models.TextField(blank=True)

    # Assessment & Plan
    diagnosis          = models.TextField(blank=True)
    plan               = models.TextField(blank=True)
    counseling_given   = models.TextField(blank=True)
    follow_up_date     = models.DateField(null=True, blank=True)
    referral           = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-consultation_date', '-created_at']
        verbose_name = 'Pediatric Consultation'

    def __str__(self):
        return f"Peds – {self.patient} ({self.get_visit_type_display()}) {self.consultation_date}"

    @property
    def nutrition_status(self):
        if self.weight_for_height_z is None:
            return 'Unknown'
        z = float(self.weight_for_height_z)
        if z < -3: return 'SAM'
        if z < -2: return 'MAM'
        if z < 2:  return 'Normal'
        return 'Overweight'


class GrowthRecord(models.Model):
    patient          = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='growth_records')
    record_date      = models.DateField()
    age_months       = models.PositiveSmallIntegerField(null=True, blank=True)
    weight_kg        = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    height_cm        = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    head_circumference= models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    muac_cm          = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    weight_for_age_z = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    height_for_age_z = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    weight_for_height_z = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    notes            = models.TextField(blank=True)
    recorded_by      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='growth_records')
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-record_date']
        verbose_name = 'Growth Record'

    def __str__(self):
        return f"Growth – {self.patient} on {self.record_date}"


class ImmunizationRecord(models.Model):
    class Status(models.TextChoices):
        GIVEN            = 'given',           'Given'
        DUE              = 'due',             'Due'
        OVERDUE          = 'overdue',         'Overdue'
        MISSED           = 'missed',          'Missed'
        CONTRAINDICATED  = 'contraindicated', 'Contraindicated'

    patient        = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='immunizations')
    vaccine_name   = models.CharField(max_length=100)
    dose_number    = models.PositiveSmallIntegerField(default=1)
    due_date       = models.DateField(null=True, blank=True)
    date_given     = models.DateField(null=True, blank=True)
    status         = models.CharField(max_length=20, choices=Status.choices, default=Status.DUE)
    batch_number   = models.CharField(max_length=50, blank=True)
    site           = models.CharField(max_length=50, blank=True)
    administered_by= models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='immunizations_given')
    reaction       = models.TextField(blank=True)
    notes          = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['due_date', 'vaccine_name']
        verbose_name = 'Immunization Record'

    def __str__(self):
        return f"Vaccine – {self.patient} – {self.vaccine_name} dose {self.dose_number}"


class DevelopmentalAssessment(models.Model):
    class Outcome(models.TextChoices):
        NORMAL   = 'normal',   'Normal Development'
        DELAY    = 'delay',    'Developmental Delay'
        REGRESSION='regression','Developmental Regression'
        REFERRAL = 'referral', 'Referred for Specialist'

    patient         = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='dev_assessments')
    assessment_date = models.DateField()
    age_months      = models.PositiveSmallIntegerField()
    clinician       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='dev_assessments')
    gross_motor     = models.TextField(blank=True)
    fine_motor      = models.TextField(blank=True)
    language        = models.TextField(blank=True)
    social_personal = models.TextField(blank=True)
    cognitive       = models.TextField(blank=True)
    milestones_met  = models.TextField(blank=True)
    milestones_missed = models.TextField(blank=True)
    outcome         = models.CharField(max_length=15, choices=Outcome.choices, default=Outcome.NORMAL)
    recommendations = models.TextField(blank=True)
    next_assessment = models.DateField(null=True, blank=True)
    notes           = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-assessment_date']
        verbose_name = 'Developmental Assessment'

    def __str__(self):
        return f"Dev Assessment – {self.patient} at {self.age_months}m ({self.assessment_date})"


# ═══════════════════════════════════════════════════════════════════════════════
#  GENERAL SURGERY MODULE
# ═══════════════════════════════════════════════════════════════════════════════

class SurgicalConsultation(models.Model):
    class Urgency(models.TextChoices):
        ELECTIVE  = 'elective',  'Elective'
        URGENT    = 'urgent',    'Urgent (within 24–72 h)'
        EMERGENCY = 'emergency', 'Emergency (immediate)'

    class Decision(models.TextChoices):
        FOR_SURGERY   = 'for_surgery',  'Listed for Surgery'
        CONSERVATIVE  = 'conservative', 'Conservative Management'
        FURTHER_WORKUP= 'workup',       'Further Work-up Needed'
        REFERRED_OUT  = 'referred_out', 'Referred Out'
        DEFERRED      = 'deferred',     'Deferred / Patient Declined'

    patient           = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='surgical_consultations')
    visit             = models.ForeignKey('Visit', on_delete=models.SET_NULL, null=True, blank=True, related_name='surgical_consultations')
    consultation_date = models.DateField()
    surgeon           = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='surgical_consultations')

    # Vitals
    bp_systolic   = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic  = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse         = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature   = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    weight_kg     = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    spo2          = models.PositiveSmallIntegerField(null=True, blank=True)

    # Clinical
    chief_complaint        = models.TextField()
    history                = models.TextField(blank=True)
    past_surgical_history  = models.TextField(blank=True)
    past_medical_history   = models.TextField(blank=True)
    medications            = models.TextField(blank=True)
    allergies              = models.TextField(blank=True)
    physical_examination   = models.TextField(blank=True)
    local_examination      = models.TextField(blank=True)

    # Surgical plan
    provisional_diagnosis  = models.TextField(blank=True)
    proposed_procedure     = models.CharField(max_length=300, blank=True)
    urgency                = models.CharField(max_length=15, choices=Urgency.choices, default=Urgency.ELECTIVE)
    decision               = models.CharField(max_length=20, choices=Decision.choices, default=Decision.FOR_SURGERY)
    pre_op_investigations  = models.TextField(blank=True)
    consent_obtained       = models.BooleanField(default=False)
    anaesthesia_referral   = models.BooleanField(default=False)
    blood_required         = models.BooleanField(default=False)
    units_required         = models.PositiveSmallIntegerField(null=True, blank=True)
    notes                  = models.TextField(blank=True)
    follow_up_date         = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-consultation_date', '-created_at']
        verbose_name = 'Surgical Consultation'

    def __str__(self):
        return f"Surg Consult – {self.patient} – {self.proposed_procedure or 'TBD'} ({self.consultation_date})"


class PreOperativeAssessment(models.Model):
    class ASA(models.TextChoices):
        ASA1 = 'I',   'ASA I – Normal healthy patient'
        ASA2 = 'II',  'ASA II – Mild systemic disease'
        ASA3 = 'III', 'ASA III – Severe systemic disease'
        ASA4 = 'IV',  'ASA IV – Severe systemic disease, life threat'
        ASA5 = 'V',   'ASA V – Moribund patient'
        ASA6 = 'VI',  'ASA VI – Brain-dead (organ donation)'

    consultation      = models.OneToOneField(SurgicalConsultation, on_delete=models.CASCADE, related_name='preop_assessment')
    assessment_date   = models.DateField()
    assessed_by       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='preop_assessments')
    asa_class         = models.CharField(max_length=5, choices=ASA.choices, blank=True)

    # Checklist
    hb_done           = models.BooleanField(default=False, verbose_name='Hb / FBC done')
    xmatch_done       = models.BooleanField(default=False, verbose_name='Group & X-match done')
    ecg_done          = models.BooleanField(default=False, verbose_name='ECG done')
    cxr_done          = models.BooleanField(default=False, verbose_name='CXR done')
    lft_done          = models.BooleanField(default=False, verbose_name='LFTs done')
    rft_done          = models.BooleanField(default=False, verbose_name='RFTs / Electrolytes done')
    clotting_done     = models.BooleanField(default=False, verbose_name='Clotting studies done')
    urinalysis_done   = models.BooleanField(default=False, verbose_name='Urinalysis done')
    consent_signed    = models.BooleanField(default=False)
    npo_confirmed     = models.BooleanField(default=False, verbose_name='NPO confirmed')
    site_marked       = models.BooleanField(default=False, verbose_name='Surgical site marked')
    iv_access         = models.BooleanField(default=False, verbose_name='IV access secured')
    pre_med_given     = models.BooleanField(default=False, verbose_name='Pre-medication given')
    antibiotic_given  = models.BooleanField(default=False, verbose_name='Prophylactic antibiotic given')
    dvt_prophylaxis   = models.BooleanField(default=False, verbose_name='DVT prophylaxis given')

    airway_notes      = models.TextField(blank=True)
    anaesthesia_plan  = models.TextField(blank=True)
    special_concerns  = models.TextField(blank=True)
    notes             = models.TextField(blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Pre-operative Assessment'

    def __str__(self):
        return f"Pre-op – {self.consultation.patient} ({self.assessment_date})"


class SurgicalOperativeNote(models.Model):
    class WoundClass(models.TextChoices):
        CLEAN             = 'clean',             'Clean'
        CLEAN_CONTAMINATED= 'clean_contaminated','Clean-Contaminated'
        CONTAMINATED      = 'contaminated',      'Contaminated'
        DIRTY             = 'dirty',             'Dirty / Infected'

    consultation      = models.OneToOneField(SurgicalConsultation, on_delete=models.CASCADE, related_name='operative_note')
    procedure_date    = models.DateField()
    start_time        = models.TimeField(null=True, blank=True)
    end_time          = models.TimeField(null=True, blank=True)
    surgeon           = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='operative_notes')
    assistant_surgeon = models.CharField(max_length=150, blank=True)
    anaesthetist      = models.CharField(max_length=150, blank=True)
    scrub_nurse       = models.CharField(max_length=150, blank=True)

    procedure_performed  = models.CharField(max_length=300)
    anaesthesia_type     = models.CharField(max_length=100, blank=True)
    position             = models.CharField(max_length=100, blank=True)
    incision             = models.CharField(max_length=200, blank=True)
    findings             = models.TextField(blank=True)
    procedure_details    = models.TextField()
    closure              = models.TextField(blank=True)
    wound_class          = models.CharField(max_length=25, choices=WoundClass.choices, blank=True)
    ebl                  = models.PositiveIntegerField(null=True, blank=True, verbose_name='Estimated Blood Loss (mL)')
    specimens_sent       = models.TextField(blank=True)
    drains_placed        = models.TextField(blank=True)
    complications        = models.TextField(blank=True)
    post_op_orders       = models.TextField(blank=True)
    notes                = models.TextField(blank=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Operative Note'

    def __str__(self):
        return f"Surgical Op Note – {self.consultation.patient} – {self.procedure_performed} ({self.procedure_date})"

    @property
    def duration_display(self):
        if self.start_time and self.end_time:
            from datetime import datetime, date as d
            start = datetime.combine(d.today(), self.start_time)
            end = datetime.combine(d.today(), self.end_time)
            diff = end - start
            h, rem = divmod(int(diff.total_seconds()), 3600)
            m = rem // 60
            return f"{h}h {m}m"
        return '—'


class SurgicalPostOpNote(models.Model):
    class Status(models.TextChoices):
        STABLE    = 'stable',    'Stable'
        IMPROVING = 'improving', 'Improving'
        CONCERNS  = 'concerns',  'With Concerns'
        CRITICAL  = 'critical',  'Critical'
        DISCHARGED= 'discharged','Discharged'

    consultation      = models.ForeignKey(SurgicalConsultation, on_delete=models.CASCADE, related_name='postop_notes')
    note_date         = models.DateField()
    pod               = models.PositiveSmallIntegerField(verbose_name='Post-op Day', default=1)
    written_by        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='postop_notes')

    bp_systolic   = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic  = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse         = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature   = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    spo2          = models.PositiveSmallIntegerField(null=True, blank=True)

    pain_score        = models.PositiveSmallIntegerField(null=True, blank=True, help_text='0–10 VAS')
    oral_intake       = models.CharField(max_length=100, blank=True)
    urine_output      = models.CharField(max_length=100, blank=True)
    drain_output      = models.CharField(max_length=100, blank=True)
    wound_condition   = models.TextField(blank=True)
    subjective        = models.TextField(blank=True)
    objective         = models.TextField(blank=True)
    assessment        = models.TextField(blank=True)
    plan              = models.TextField(blank=True)
    status            = models.CharField(max_length=15, choices=Status.choices, default=Status.STABLE)
    discharge_planned = models.BooleanField(default=False)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['pod']
        verbose_name = 'Surgical Post-op Note'

    def __str__(self):
        return f"Surgical Post-op Day {self.pod} – {self.consultation.patient} ({self.note_date})"


class WoundFollowUp(models.Model):
    class WoundStatus(models.TextChoices):
        HEALING         = 'healing',         'Healing Well'
        INFECTED        = 'infected',        'Signs of Infection'
        DEHISCED        = 'dehisced',        'Wound Dehiscence'
        GRANULATING     = 'granulating',     'Granulating'
        HEALED          = 'healed',          'Fully Healed'

    consultation  = models.ForeignKey(SurgicalConsultation, on_delete=models.CASCADE, related_name='wound_followups')
    visit_date    = models.DateField()
    pod           = models.PositiveSmallIntegerField(verbose_name='Post-op Day', null=True, blank=True)
    seen_by       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='wound_followups')
    wound_status  = models.CharField(max_length=20, choices=WoundStatus.choices)
    wound_findings= models.TextField(blank=True)
    sutures_removed=models.BooleanField(default=False)
    dressing_done = models.BooleanField(default=False)
    dressing_type = models.CharField(max_length=100, blank=True)
    plan          = models.TextField(blank=True)
    next_visit    = models.DateField(null=True, blank=True)
    notes         = models.TextField(blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['visit_date']
        verbose_name = 'Wound Follow-up'

    def __str__(self):
        return f"Wound FU – {self.consultation.patient} – {self.get_wound_status_display()} ({self.visit_date})"


# ── Cardiology Module ──────────────────────────────────────────────────────────

class CardiologyConsultation(models.Model):
    class ClinicType(models.TextChoices):
        GENERAL        = 'general',        'General Cardiology'
        HEART_FAILURE  = 'heart_failure',  'Heart Failure Clinic'
        HYPERTENSION   = 'hypertension',   'Hypertension Clinic'
        ARRHYTHMIA     = 'arrhythmia',     'Arrhythmia / Electrophysiology'
        INTERVENTIONAL = 'interventional', 'Interventional Cardiology'
        PREVENTIVE     = 'preventive',     'Preventive Cardiology'
        PAEDIATRIC     = 'paediatric',     'Paediatric Cardiology'
        REHAB          = 'rehab',          'Cardiac Rehabilitation'

    class VisitType(models.TextChoices):
        NEW      = 'new',      'New Patient'
        FOLLOWUP = 'followup', 'Follow-up'
        URGENT   = 'urgent',   'Urgent / Referral'
        REVIEW   = 'review',   'Results Review'

    class NYHAClass(models.TextChoices):
        I   = 'I',   'Class I – No limitation'
        II  = 'II',  'Class II – Slight limitation'
        III = 'III', 'Class III – Marked limitation'
        IV  = 'IV',  'Class IV – Symptoms at rest'
        NA  = 'na',  'Not Applicable'

    patient             = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='cardiology_consultations')
    visit               = models.ForeignKey(Visit, on_delete=models.SET_NULL, null=True, blank=True, related_name='cardiology_consultations')
    consultation_date   = models.DateField()
    clinic_type         = models.CharField(max_length=20, choices=ClinicType.choices, default=ClinicType.GENERAL)
    visit_type          = models.CharField(max_length=10, choices=VisitType.choices, default=VisitType.NEW)
    cardiologist        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cardiology_consultations')

    # Vitals
    bp_systolic         = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic        = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse               = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature         = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    weight_kg           = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    height_cm           = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    spo2                = models.PositiveSmallIntegerField(null=True, blank=True)
    rr                  = models.PositiveSmallIntegerField(null=True, blank=True)

    # Cardiac-specific vitals
    nyha_class          = models.CharField(max_length=5, choices=NYHAClass.choices, default=NYHAClass.NA)
    jvp                 = models.CharField(max_length=100, blank=True, verbose_name='JVP')
    heart_sounds        = models.CharField(max_length=200, blank=True)
    murmur              = models.CharField(max_length=200, blank=True)
    peripheral_pulses   = models.CharField(max_length=200, blank=True)
    pedal_edema         = models.CharField(max_length=100, blank=True)

    # Symptoms (checkboxes stored as boolean)
    chest_pain          = models.BooleanField(default=False)
    dyspnea             = models.BooleanField(default=False)
    orthopnea           = models.BooleanField(default=False)
    pnd                 = models.BooleanField(default=False, verbose_name='Paroxysmal Nocturnal Dyspnea')
    palpitations        = models.BooleanField(default=False)
    syncope             = models.BooleanField(default=False)
    presyncope          = models.BooleanField(default=False)
    fatigue             = models.BooleanField(default=False)
    ankle_swelling      = models.BooleanField(default=False)

    # History
    chief_complaint         = models.TextField()
    history                 = models.TextField(blank=True)
    past_cardiac_history    = models.TextField(blank=True)
    past_medical_history    = models.TextField(blank=True)
    family_history          = models.TextField(blank=True)
    social_history          = models.TextField(blank=True)
    current_medications     = models.TextField(blank=True)
    allergies               = models.CharField(max_length=300, blank=True)

    # Risk factors
    hypertension            = models.BooleanField(default=False)
    diabetes                = models.BooleanField(default=False)
    dyslipidaemia           = models.BooleanField(default=False)
    smoking                 = models.BooleanField(default=False)
    obesity                 = models.BooleanField(default=False)
    family_hx_cad           = models.BooleanField(default=False, verbose_name='Family Hx of CAD')
    ckd                     = models.BooleanField(default=False, verbose_name='CKD')

    # Examination & plan
    physical_examination    = models.TextField(blank=True)
    ecg_findings            = models.TextField(blank=True, verbose_name='ECG Findings')
    investigations          = models.TextField(blank=True)
    diagnosis               = models.TextField(blank=True)
    icd10_code              = models.CharField(max_length=20, blank=True)
    plan                    = models.TextField(blank=True)
    referral                = models.TextField(blank=True)
    follow_up_date          = models.DateField(null=True, blank=True)
    notes                   = models.TextField(blank=True)
    created_at              = models.DateTimeField(auto_now_add=True)
    updated_at              = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-consultation_date', '-created_at']
        verbose_name = 'Cardiology Consultation'

    def __str__(self):
        return f"Cardiology – {self.patient} ({self.consultation_date})"

    @property
    def bmi(self):
        if self.weight_kg and self.height_cm and float(self.height_cm) > 0:
            h = float(self.height_cm) / 100
            return round(float(self.weight_kg) / (h * h), 1)
        return None

    @property
    def bp_display(self):
        if self.bp_systolic and self.bp_diastolic:
            return f"{self.bp_systolic}/{self.bp_diastolic}"
        return '—'


class ECGRecord(models.Model):
    class Rhythm(models.TextChoices):
        SINUS_NORMAL  = 'sinus_normal',  'Normal Sinus Rhythm'
        SINUS_TACHY   = 'sinus_tachy',   'Sinus Tachycardia'
        SINUS_BRADY   = 'sinus_brady',   'Sinus Bradycardia'
        AF            = 'af',            'Atrial Fibrillation'
        AFLUTTER      = 'aflutter',      'Atrial Flutter'
        SVT           = 'svt',           'SVT'
        VT            = 'vt',            'Ventricular Tachycardia'
        VF            = 'vf',            'Ventricular Fibrillation'
        HEART_BLOCK   = 'heart_block',   'Heart Block'
        PACED         = 'paced',         'Paced Rhythm'
        OTHER         = 'other',         'Other'

    class Axis(models.TextChoices):
        NORMAL    = 'normal',    'Normal Axis'
        LAD       = 'lad',       'Left Axis Deviation'
        RAD       = 'rad',       'Right Axis Deviation'
        EXTREME   = 'extreme',   'Extreme Axis Deviation'

    consultation    = models.ForeignKey(CardiologyConsultation, on_delete=models.CASCADE, related_name='ecg_records', null=True, blank=True)
    patient         = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='ecg_records')
    recording_date  = models.DateField()
    recording_time  = models.TimeField(null=True, blank=True)
    recorded_by     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='ecg_recordings')

    heart_rate      = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Heart Rate (bpm)')
    rhythm          = models.CharField(max_length=20, choices=Rhythm.choices, default=Rhythm.SINUS_NORMAL)
    axis            = models.CharField(max_length=10, choices=Axis.choices, default=Axis.NORMAL)

    pr_interval     = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='PR interval (ms)')
    qrs_duration    = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='QRS duration (ms)')
    qt_interval     = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='QT interval (ms)')
    qtc_interval    = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='QTc interval (ms)')

    st_changes      = models.BooleanField(default=False, verbose_name='ST changes present')
    st_details      = models.TextField(blank=True, verbose_name='ST change details')
    t_wave_changes  = models.BooleanField(default=False)
    t_wave_details  = models.TextField(blank=True)
    lbbb            = models.BooleanField(default=False, verbose_name='LBBB')
    rbbb            = models.BooleanField(default=False, verbose_name='RBBB')
    lvh             = models.BooleanField(default=False, verbose_name='LVH')
    rvh             = models.BooleanField(default=False, verbose_name='RVH')
    q_waves         = models.BooleanField(default=False, verbose_name='Pathological Q waves')
    q_wave_details  = models.TextField(blank=True)

    interpretation  = models.TextField(blank=True)
    notes           = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recording_date', '-created_at']
        verbose_name = 'ECG Record'

    def __str__(self):
        return f"ECG – {self.patient} – {self.get_rhythm_display()} ({self.recording_date})"


class EchoReport(models.Model):
    class EchoType(models.TextChoices):
        TTE   = 'tte',   'Transthoracic Echo (TTE)'
        TEE   = 'tee',   'Transoesophageal Echo (TEE)'
        STRESS= 'stress','Stress Echo'
        DOBU  = 'dobu',  'Dobutamine Stress Echo'

    class LVFunction(models.TextChoices):
        NORMAL        = 'normal',        'Normal (EF ≥ 55%)'
        MILDLY_RED    = 'mildly_red',    'Mildly Reduced (EF 45–54%)'
        MODERATELY_RED= 'moderately_red','Moderately Reduced (EF 30–44%)'
        SEVERELY_RED  = 'severely_red',  'Severely Reduced (EF < 30%)'
        HYPERDYNAMIC  = 'hyperdynamic',  'Hyperdynamic (EF > 70%)'

    consultation    = models.ForeignKey(CardiologyConsultation, on_delete=models.CASCADE, related_name='echo_reports', null=True, blank=True)
    patient         = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='echo_reports')
    study_date      = models.DateField()
    echo_type       = models.CharField(max_length=10, choices=EchoType.choices, default=EchoType.TTE)
    performed_by    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='echo_reports')

    # LV dimensions
    lvedd           = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name='LVEDd (mm)')
    lvesd           = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name='LVESd (mm)')
    ivsd            = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name='IVSd (mm)')
    pwd             = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name='PWd (mm)')
    lv_mass         = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True, verbose_name='LV Mass (g)')

    # Function
    ef_percent      = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Ejection Fraction (%)')
    lv_function     = models.CharField(max_length=20, choices=LVFunction.choices, blank=True)
    wall_motion     = models.TextField(blank=True, verbose_name='Wall Motion Abnormalities')
    diastolic_function = models.TextField(blank=True)

    # Chambers
    la_size         = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name='LA size (mm)')
    ra_size         = models.CharField(max_length=50, blank=True, verbose_name='RA size')
    rvedd           = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name='RVEDd (mm)')
    rv_function     = models.CharField(max_length=100, blank=True)
    tapse           = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, verbose_name='TAPSE (mm)')
    pasp            = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='PASP (mmHg)')

    # Valves
    aortic_valve    = models.TextField(blank=True)
    mitral_valve    = models.TextField(blank=True)
    tricuspid_valve = models.TextField(blank=True)
    pulmonary_valve = models.TextField(blank=True)
    pericardium     = models.TextField(blank=True)
    aortic_root     = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, verbose_name='Aortic Root (mm)')

    impression      = models.TextField(blank=True)
    recommendation  = models.TextField(blank=True)
    notes           = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-study_date', '-created_at']
        verbose_name = 'Echocardiogram Report'

    def __str__(self):
        return f"Echo – {self.patient} – EF {self.ef_percent}% ({self.study_date})"


class CardiacProcedure(models.Model):
    class ProcedureType(models.TextChoices):
        CORONARY_ANGIO  = 'coronary_angio',  'Coronary Angiography'
        PCI             = 'pci',             'Percutaneous Coronary Intervention (PCI)'
        PACEMAKER_IMPL  = 'pacemaker_impl',  'Pacemaker Implantation'
        PACEMAKER_CHECK = 'pacemaker_check', 'Pacemaker/ICD Check'
        CARDIOVERSION   = 'cardioversion',   'Electrical Cardioversion (DCCV)'
        ABLATION        = 'ablation',        'Cardiac Ablation'
        HOLTER          = 'holter',          'Holter Monitor'
        STRESS_TEST     = 'stress_test',     'Exercise Stress Test'
        TILT_TEST       = 'tilt_test',       'Tilt Table Test'
        PERICARDIO      = 'pericardio',      'Pericardiocentesis'
        OTHER           = 'other',           'Other'

    class Outcome(models.TextChoices):
        SUCCESS     = 'success',     'Successful'
        PARTIAL     = 'partial',     'Partially Successful'
        FAILED      = 'failed',      'Failed'
        ABORTED     = 'aborted',     'Aborted'
        COMPLICATION= 'complication','Complication Encountered'

    consultation        = models.ForeignKey(CardiologyConsultation, on_delete=models.CASCADE, related_name='cardiac_procedures', null=True, blank=True)
    patient             = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='cardiac_procedures')
    procedure_date      = models.DateField()
    procedure_type      = models.CharField(max_length=25, choices=ProcedureType.choices)
    performed_by        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cardiac_procedures')
    assistant           = models.CharField(max_length=150, blank=True)

    indication          = models.TextField(blank=True)
    technique           = models.TextField(blank=True)
    findings            = models.TextField(blank=True)
    outcome             = models.CharField(max_length=15, choices=Outcome.choices, default=Outcome.SUCCESS)
    complications       = models.TextField(blank=True)
    post_procedure_plan = models.TextField(blank=True)
    contrast_used       = models.CharField(max_length=100, blank=True)
    radiation_dose      = models.CharField(max_length=100, blank=True)
    notes               = models.TextField(blank=True)
    created_at          = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-procedure_date', '-created_at']
        verbose_name = 'Cardiac Procedure'

    def __str__(self):
        return f"{self.get_procedure_type_display()} – {self.patient} ({self.procedure_date})"


# ── Specialty Referral ────────────────────────────────────────────────────────

class SpecialtyReferral(models.Model):
    """Internal referral from one specialty to another within the same hospital."""

    class Urgency(models.TextChoices):
        ROUTINE   = 'routine',   'Routine'
        URGENT    = 'urgent',    'Urgent'
        EMERGENCY = 'emergency', 'Emergency'

    class Status(models.TextChoices):
        PENDING     = 'pending',     'Pending'
        ACCEPTED    = 'accepted',    'Accepted'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED   = 'completed',   'Completed'
        DECLINED    = 'declined',    'Declined'

    patient          = models.ForeignKey(Patient, on_delete=models.CASCADE,   related_name='specialty_referrals')
    from_visit       = models.ForeignKey(Visit,   on_delete=models.SET_NULL,  null=True, blank=True, related_name='outgoing_referrals')
    from_specialty   = models.ForeignKey(Specialization, on_delete=models.PROTECT, related_name='outgoing_referrals')
    to_specialty     = models.ForeignKey(Specialization, on_delete=models.PROTECT, related_name='incoming_referrals')
    from_doctor      = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True, related_name='referrals_made')
    to_doctor        = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True, related_name='referrals_received')
    urgency          = models.CharField(max_length=20, choices=Urgency.choices, default=Urgency.ROUTINE, db_index=True)
    status           = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    reason           = models.TextField()
    clinical_summary = models.TextField(blank=True, help_text='Clinical notes, diagnoses, and relevant results to transfer.')
    response_notes   = models.TextField(blank=True)
    appointment      = models.ForeignKey(Appointment, on_delete=models.SET_NULL, null=True, blank=True, related_name='referrals')
    created_by       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='referrals_created')
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)
    accepted_at      = models.DateTimeField(null=True, blank=True)
    completed_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Specialty Referral'
        verbose_name_plural = 'Specialty Referrals'

    def __str__(self):
        return f'Referral #{self.pk} — {self.patient} → {self.to_specialty} ({self.get_status_display()})'
