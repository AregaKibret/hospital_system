"""
Best-practices migration:
  - Department.name unique=True
  - Doctor.mobile remove null=True (data-migrate NULLs → '')
  - Doctor.department / Visit.department / Visit.doctor on_delete CASCADE → PROTECT
  - Visit.created_at auto_now_add
  - Visit.visit_type / Patient.sex choices updated (TextChoices, no DB change)
  - Queue.queue_number IntegerField → PositiveIntegerField
  - UserProfile.updated_at auto_now
  - related_name attrs on all FKs
  - Meta ordering / verbose_name on all models
  - Indexes on Patient and Visit
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def fix_doctor_mobile_nulls(apps, schema_editor):
    Doctor = apps.get_model('core', 'Doctor')
    Doctor.objects.filter(mobile__isnull=True).update(mobile='')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_userprofile_hmspermissions'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── Department ───────────────────────────────────────────────────────
        migrations.AlterModelOptions(
            name='department',
            options={
                'ordering': ['name'],
                'verbose_name': 'Department',
                'verbose_name_plural': 'Departments',
            },
        ),
        migrations.AlterField(
            model_name='department',
            name='name',
            field=models.CharField(max_length=100, unique=True),
        ),

        # ── Doctor ────────────────────────────────────────────────────────────
        migrations.AlterModelOptions(
            name='doctor',
            options={
                'ordering': ['last_name', 'first_name'],
                'verbose_name': 'Doctor',
                'verbose_name_plural': 'Doctors',
            },
        ),
        migrations.AlterField(
            model_name='doctor',
            name='department',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='doctors',
                to='core.department',
            ),
        ),
        migrations.RunPython(fix_doctor_mobile_nulls, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='doctor',
            name='mobile',
            field=models.CharField(blank=True, default='', max_length=20),
        ),

        # ── Patient ───────────────────────────────────────────────────────────
        migrations.AlterModelOptions(
            name='patient',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Patient',
                'verbose_name_plural': 'Patients',
            },
        ),
        migrations.AlterField(
            model_name='patient',
            name='sex',
            field=models.CharField(
                choices=[('Male', 'Male'), ('Female', 'Female'), ('Other', 'Other')],
                max_length=10,
            ),
        ),
        migrations.AddIndex(
            model_name='patient',
            index=models.Index(fields=['last_name', 'first_name'], name='core_patien_last_na_idx'),
        ),
        migrations.AddIndex(
            model_name='patient',
            index=models.Index(fields=['card_number'], name='core_patien_card_nu_idx'),
        ),

        # ── Visit ─────────────────────────────────────────────────────────────
        migrations.AlterModelOptions(
            name='visit',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Visit',
                'verbose_name_plural': 'Visits',
            },
        ),
        migrations.AlterField(
            model_name='visit',
            name='department',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='visits',
                to='core.department',
            ),
        ),
        migrations.AlterField(
            model_name='visit',
            name='doctor',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='visits',
                to='core.doctor',
            ),
        ),
        migrations.AlterField(
            model_name='visit',
            name='patient',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='visits',
                to='core.patient',
            ),
        ),
        migrations.AlterField(
            model_name='visit',
            name='visit_type',
            field=models.CharField(
                choices=[('OPD', 'OPD'), ('Emergency', 'Emergency'), ('Inpatient', 'Inpatient')],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='visit',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name='visit',
            index=models.Index(fields=['patient', '-created_at'], name='core_visit_patient_idx'),
        ),

        # ── Queue ─────────────────────────────────────────────────────────────
        migrations.AlterModelOptions(
            name='queue',
            options={
                'ordering': ['queue_number'],
                'verbose_name': 'Queue Entry',
                'verbose_name_plural': 'Queue Entries',
            },
        ),
        migrations.AlterField(
            model_name='queue',
            name='queue_number',
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterField(
            model_name='queue',
            name='visit',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='queue',
                to='core.visit',
            ),
        ),

        # ── UserProfile ───────────────────────────────────────────────────────
        migrations.AlterModelOptions(
            name='userprofile',
            options={
                'verbose_name': 'User Profile',
                'verbose_name_plural': 'User Profiles',
            },
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='department',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='staff',
                to='core.department',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
