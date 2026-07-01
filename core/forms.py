from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from .models import (
    ClinicalNote, Department, Diagnosis, ImagingOrder, LabOrder, LabSample,
    LabService, MedicationOrder, Patient, ProcedureOrder, UserProfile, Visit,
    VitalSign,
)

User = get_user_model()


_AGE_UNIT_CHOICES = [('Years', 'Years'), ('Months', 'Months'), ('Days', 'Days')]


class PatientForm(forms.ModelForm):
    # Non-model convenience fields — used only in the UI; DOB is what's stored.
    age_value = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=150,
        label='Age',
        widget=forms.NumberInput(attrs={'placeholder': 'e.g. 32', 'id': 'id_age_value'}),
    )
    age_unit = forms.ChoiceField(
        required=False,
        choices=_AGE_UNIT_CHOICES,
        initial='Years',
        label='',
        widget=forms.Select(attrs={'id': 'id_age_unit'}),
    )

    class Meta:
        model = Patient
        fields = [
            'first_name', 'middle_name', 'last_name',
            'sex', 'date_of_birth', 'mobile', 'emergency_contact',
            'nationality', 'region', 'city', 'subcity', 'wereda', 'house_no',
            'occupation', 'education',
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean(self):
        import calendar
        from datetime import date, timedelta

        cleaned = super().clean()
        dob        = cleaned.get('date_of_birth')
        age_value  = cleaned.get('age_value')
        age_unit   = cleaned.get('age_unit') or 'Years'
        today      = date.today()

        if dob:
            if dob > today:
                self.add_error('date_of_birth', 'Date of birth cannot be in the future.')
            elif (today - dob).days > 150 * 365:
                self.add_error('date_of_birth', 'Date of birth appears too far in the past.')
        elif age_value is not None and age_value >= 0:
            # JS normally keeps DOB in sync; this is a server-side fallback.
            try:
                if age_unit == 'Years':
                    try:
                        estimated = today.replace(year=today.year - age_value)
                    except ValueError:
                        estimated = today.replace(year=today.year - age_value, day=28)
                elif age_unit == 'Months':
                    total_months = today.year * 12 + today.month - 1 - age_value
                    y, m = divmod(total_months, 12)
                    m += 1
                    d = min(today.day, calendar.monthrange(y, m)[1])
                    estimated = today.replace(year=y, month=m, day=d)
                else:  # Days
                    estimated = today - timedelta(days=age_value)
                cleaned['date_of_birth'] = estimated
            except (ValueError, OverflowError):
                pass

        return cleaned


class VisitForm(forms.ModelForm):
    class Meta:
        model = Visit
        fields = ['department', 'doctor', 'visit_type', 'previous_visit']

    def __init__(self, *args, patient=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Limit previous_visit choices to this patient's prior visits only
        if patient:
            self.fields['previous_visit'].queryset = (
                Visit.objects.filter(patient=patient)
                .select_related('doctor', 'department')
                .order_by('-created_at')
            )
            self.fields['previous_visit'].label_from_instance = lambda v: (
                f"Visit #{v.pk} — {v.visit_type} — "
                f"Dr. {v.doctor.last_name} ({v.department.name}) — "
                f"{v.created_at.strftime('%d %b %Y')}"
            )
        else:
            self.fields['previous_visit'].queryset = Visit.objects.none()
        self.fields['previous_visit'].required = False
        self.fields['previous_visit'].empty_label = '— Select previous visit (optional) —'


# ── User management forms ─────────────────────────────────────────────────────

class UserCreateForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        min_length=8,
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        label='Confirm Password',
    )
    role = forms.ModelChoiceField(
        queryset=Group.objects.all().order_by('name'),
        empty_label='— Select Role —',
    )
    department = forms.ModelChoiceField(
        queryset=Department.objects.all().order_by('name'),
        required=False,
        empty_label='— No Department —',
    )
    phone = forms.CharField(max_length=20, required=False)
    employee_id = forms.CharField(max_length=20, required=False, label='Employee ID')
    job_title = forms.CharField(max_length=100, required=False, label='Job Title')
    access_level = forms.ChoiceField(
        choices=UserProfile.AccessLevel.choices,
        initial=UserProfile.AccessLevel.STANDARD,
    )
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get('password')
        cpw = cleaned.get('confirm_password')
        if pw and cpw and pw != cpw:
            self.add_error('confirm_password', 'Passwords do not match.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
            user.groups.set([self.cleaned_data['role']])
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'department':   self.cleaned_data.get('department'),
                    'phone':        self.cleaned_data.get('phone', ''),
                    'employee_id':  self.cleaned_data.get('employee_id', ''),
                    'job_title':    self.cleaned_data.get('job_title', ''),
                    'access_level': self.cleaned_data.get('access_level', UserProfile.AccessLevel.STANDARD),
                    'notes':        self.cleaned_data.get('notes', ''),
                },
            )
        return user


class UserUpdateForm(forms.ModelForm):
    role = forms.ModelChoiceField(
        queryset=Group.objects.all().order_by('name'),
        empty_label='— Select Role —',
    )
    department = forms.ModelChoiceField(
        queryset=Department.objects.all().order_by('name'),
        required=False,
        empty_label='— No Department —',
    )
    phone = forms.CharField(max_length=20, required=False)
    employee_id = forms.CharField(max_length=20, required=False, label='Employee ID')
    job_title = forms.CharField(max_length=100, required=False, label='Job Title')
    access_level = forms.ChoiceField(
        choices=UserProfile.AccessLevel.choices,
        initial=UserProfile.AccessLevel.STANDARD,
    )
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['role'].initial = self.instance.groups.first()
            profile = getattr(self.instance, 'profile', None)
            if profile:
                self.fields['department'].initial   = profile.department
                self.fields['phone'].initial        = profile.phone
                self.fields['employee_id'].initial  = profile.employee_id
                self.fields['job_title'].initial    = profile.job_title
                self.fields['access_level'].initial = profile.access_level
                self.fields['notes'].initial        = profile.notes

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.groups.set([self.cleaned_data['role']])
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'department':   self.cleaned_data.get('department'),
                    'phone':        self.cleaned_data.get('phone', ''),
                    'employee_id':  self.cleaned_data.get('employee_id', ''),
                    'job_title':    self.cleaned_data.get('job_title', ''),
                    'access_level': self.cleaned_data.get('access_level', UserProfile.AccessLevel.STANDARD),
                    'notes':        self.cleaned_data.get('notes', ''),
                },
            )
        return user


# ── Doctor / Clinical forms ───────────────────────────────────────────────────

class ClinicalNoteForm(forms.ModelForm):
    class Meta:
        model = ClinicalNote
        fields = ['note_type', 'chief_complaint', 'content', 'assessment', 'plan']
        widgets = {
            'chief_complaint': forms.Textarea(attrs={'rows': 3}),
            'content': forms.Textarea(attrs={'rows': 6}),
            'assessment': forms.Textarea(attrs={'rows': 4}),
            'plan': forms.Textarea(attrs={'rows': 4}),
        }


class DiagnosisForm(forms.ModelForm):
    class Meta:
        model = Diagnosis
        fields = ['icd_code', 'description', 'status', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class LabOrderForm(forms.ModelForm):
    # Optional service picker — if chosen, name/category/price fill automatically
    lab_service = forms.ModelChoiceField(
        queryset=LabService.objects.filter(is_active=True).order_by('section', 'name'),
        required=False,
        empty_label='— Type test name manually —',
        label='Lab Service',
    )

    class Meta:
        model = LabOrder
        fields = ['lab_service', 'test_category', 'test_name', 'priority', 'clinical_notes']
        widgets = {
            'clinical_notes': forms.Textarea(attrs={'rows': 3}),
        }


class LabServiceForm(forms.ModelForm):
    class Meta:
        model = LabService
        fields = [
            'name', 'code', 'category', 'section', 'sample_type',
            'sample_requirements', 'preparation_instructions', 'turnaround_hours',
            'standard_price', 'insurance_price', 'corporate_price', 'emergency_price',
            'tax_percent', 'is_active',
        ]
        widgets = {
            'sample_requirements':     forms.Textarea(attrs={'rows': 3}),
            'preparation_instructions': forms.Textarea(attrs={'rows': 3}),
        }


class LabSampleForm(forms.ModelForm):
    class Meta:
        model = LabSample
        fields = ['sample_type', 'barcode', 'patient_verified', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 2}),
        }


class ImagingOrderForm(forms.ModelForm):
    class Meta:
        model = ImagingOrder
        fields = ['imaging_type', 'body_part', 'clinical_indication', 'priority']
        widgets = {
            'clinical_indication': forms.Textarea(attrs={'rows': 3}),
        }


class MedicationOrderForm(forms.ModelForm):
    class Meta:
        model = MedicationOrder
        fields = ['drug_name', 'dosage', 'route', 'frequency', 'duration', 'quantity', 'instructions']
        widgets = {
            'instructions': forms.Textarea(attrs={'rows': 3}),
        }


class ProcedureOrderForm(forms.ModelForm):
    class Meta:
        model = ProcedureOrder
        fields = ['procedure_type', 'procedure_code', 'procedure_name', 'scheduled_date', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
            'scheduled_date': forms.DateInput(attrs={'type': 'date'}),
        }


class VitalSignForm(forms.ModelForm):
    class Meta:
        model = VitalSign
        fields = [
            'temperature', 'bp_systolic', 'bp_diastolic',
            'pulse', 'respiratory_rate', 'spo2', 'weight', 'height',
        ]


class PasswordResetForm(forms.Form):
    new_password = forms.CharField(
        widget=forms.PasswordInput(),
        min_length=8,
        label='New Password',
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(),
        label='Confirm New Password',
    )

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get('new_password')
        cpw = cleaned.get('confirm_password')
        if pw and cpw and pw != cpw:
            self.add_error('confirm_password', 'Passwords do not match.')
        return cleaned
