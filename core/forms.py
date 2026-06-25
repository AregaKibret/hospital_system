from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from .models import Department, Patient, UserProfile, Visit

User = get_user_model()


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = [
            'first_name', 'middle_name', 'last_name',
            'sex', 'date_of_birth', 'mobile', 'emergency_contact',
            'nationality', 'region', 'city', 'subcity', 'wereda', 'house_no',
            'occupation', 'education',
        ]


class VisitForm(forms.ModelForm):
    class Meta:
        model = Visit
        fields = ['department', 'doctor', 'visit_type']


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
    employee_id = forms.CharField(max_length=20, required=False)

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
                    'department': self.cleaned_data.get('department'),
                    'phone': self.cleaned_data.get('phone', ''),
                    'employee_id': self.cleaned_data.get('employee_id', ''),
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
    employee_id = forms.CharField(max_length=20, required=False)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['role'].initial = self.instance.groups.first()
            profile = getattr(self.instance, 'profile', None)
            if profile:
                self.fields['department'].initial = profile.department
                self.fields['phone'].initial = profile.phone
                self.fields['employee_id'].initial = profile.employee_id

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.groups.set([self.cleaned_data['role']])
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'department': self.cleaned_data.get('department'),
                    'phone': self.cleaned_data.get('phone', ''),
                    'employee_id': self.cleaned_data.get('employee_id', ''),
                },
            )
        return user


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
