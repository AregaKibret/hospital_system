from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0070_marentry_medication_order_direct'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── SurgeryPreOpChecklist ────────────────────────────────────────────
        migrations.CreateModel(
            name='SurgeryPreOpChecklist',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('cbc_done', models.BooleanField(default=False, verbose_name='CBC Done')),
                ('coagulation_done', models.BooleanField(default=False, verbose_name='Coagulation Profile Done')),
                ('blood_group_done', models.BooleanField(default=False, verbose_name='Blood Group & Cross-match Done')),
                ('blood_sugar_done', models.BooleanField(default=False, verbose_name='Blood Sugar Done')),
                ('rft_done', models.BooleanField(default=False, verbose_name='RFT Done')),
                ('lft_done', models.BooleanField(default=False, verbose_name='LFT Done')),
                ('electrolytes_done', models.BooleanField(default=False, verbose_name='Electrolytes Done')),
                ('cxr_done', models.BooleanField(default=False, verbose_name='Chest X-Ray Done')),
                ('ecg_done', models.BooleanField(default=False, verbose_name='ECG Done')),
                ('other_imaging_done', models.BooleanField(default=False, verbose_name='Other Imaging Done')),
                ('anesthesia_clearance', models.BooleanField(default=False, verbose_name='Anesthesia Assessment & Clearance')),
                ('medical_clearance', models.BooleanField(default=False, verbose_name='Medical Clearance')),
                ('surgical_consent_signed', models.BooleanField(default=False, verbose_name='Surgical Consent Signed')),
                ('anesthesia_consent_signed', models.BooleanField(default=False, verbose_name='Anesthesia Consent Signed')),
                ('blood_required', models.BooleanField(default=False, verbose_name='Blood Required')),
                ('blood_available', models.BooleanField(default=False, verbose_name='Blood Available/Reserved')),
                ('units_prepared', models.PositiveSmallIntegerField(default=0, verbose_name='Units Prepared')),
                ('npo_confirmed', models.BooleanField(default=False, verbose_name='NPO Status Confirmed')),
                ('iv_access', models.BooleanField(default=False, verbose_name='IV Access Established')),
                ('site_marked', models.BooleanField(default=False, verbose_name='Surgical Site Marked')),
                ('patient_identified', models.BooleanField(default=False, verbose_name='Patient Identity Verified')),
                ('pre_medication_given', models.BooleanField(default=False, verbose_name='Pre-Medication Given')),
                ('antibiotic_prophylaxis', models.BooleanField(default=False, verbose_name='Antibiotic Prophylaxis Given')),
                ('dvt_prophylaxis', models.BooleanField(default=False, verbose_name='DVT Prophylaxis Given')),
                ('allergies_reviewed', models.BooleanField(default=False, verbose_name='Allergies Reviewed')),
                ('implants_available', models.BooleanField(default=False, verbose_name='Implants/Prosthetics Available')),
                ('who_sign_in_done', models.BooleanField(default=False, verbose_name='WHO Sign-In Completed (before anaesthesia)')),
                ('who_time_out_done', models.BooleanField(default=False, verbose_name='WHO Time-Out Completed (before incision)')),
                ('who_sign_out_done', models.BooleanField(default=False, verbose_name='WHO Sign-Out Completed (before leaving OR)')),
                ('emergency_override', models.BooleanField(default=False, verbose_name='Emergency Override Activated')),
                ('override_reason', models.TextField(blank=True)),
                ('override_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('surgery_order', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='preop_checklist',
                    to='core.surgeryorder',
                )),
                ('override_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='preop_overrides',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='preop_checklists_created',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='preop_checklists_updated',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Pre-Operative Checklist',
            },
        ),

        # ── SurgeryPACURecord ────────────────────────────────────────────────
        migrations.CreateModel(
            name='SurgeryPACURecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                # PeriopDocumentBase fields
                ('version', models.PositiveSmallIntegerField(default=1)),
                ('is_current', models.BooleanField(default=True, db_index=True)),
                ('doc_status', models.CharField(
                    choices=[('draft', 'Draft'), ('finalized', 'Finalized')],
                    default='draft', max_length=15,
                )),
                ('supersedes', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='superseded_by',
                    to='core.surgerypacurecord',
                )),
                ('revision_reason', models.TextField(blank=True)),
                ('signature_name', models.CharField(blank=True, max_length=200)),
                ('finalized_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                # Clinical fields
                ('arrival_time', models.DateTimeField(blank=True, null=True)),
                ('arrival_bp_systolic', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('arrival_bp_diastolic', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('arrival_pulse', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('arrival_rr', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('arrival_temp', models.DecimalField(blank=True, decimal_places=1, max_digits=4, null=True)),
                ('arrival_spo2', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('arrival_pain_score', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('consciousness_level', models.CharField(
                    blank=True, max_length=20,
                    choices=[
                        ('alert', 'Alert & Oriented'), ('drowsy', 'Drowsy but Rousable'),
                        ('responding', 'Responding to Commands'), ('unresponsive', 'Unresponsive'),
                    ],
                )),
                ('airway_status', models.CharField(blank=True, max_length=100)),
                ('o2_delivery_method', models.CharField(blank=True, max_length=100)),
                ('o2_flow_rate', models.CharField(blank=True, max_length=50)),
                ('wound_condition', models.TextField(blank=True)),
                ('drain_type', models.CharField(blank=True, max_length=100)),
                ('drain_output_ml', models.PositiveIntegerField(blank=True, null=True)),
                ('bleeding_notes', models.TextField(blank=True)),
                ('analgesics_given', models.TextField(blank=True)),
                ('antiemetics_given', models.TextField(blank=True)),
                ('iv_fluids_given', models.TextField(blank=True)),
                ('other_medications', models.TextField(blank=True)),
                ('aldrete_activity', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('aldrete_respiration', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('aldrete_circulation', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('aldrete_consciousness', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('aldrete_spo2', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('discharge_time', models.DateTimeField(blank=True, null=True)),
                ('discharge_destination', models.CharField(
                    blank=True, max_length=10,
                    choices=[('ward', 'Surgical Ward'), ('icu', 'ICU'), ('hdu', 'HDU'), ('step', 'Step-Down Unit')],
                )),
                ('discharge_criteria_met', models.BooleanField(default=False)),
                ('pacu_duration_minutes', models.PositiveIntegerField(blank=True, null=True)),
                ('complications', models.TextField(blank=True)),
                ('monitoring_notes', models.TextField(blank=True)),
                ('notes', models.TextField(blank=True)),
                # FK fields
                ('surgery_order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pacu_records',
                    to='core.surgeryorder',
                )),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='pacu_records_created',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pacu_records_updated',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('finalized_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pacu_records_finalized',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'PACU / Recovery Record',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='surgerypacurecord',
            constraint=models.UniqueConstraint(
                condition=models.Q(is_current=True),
                fields=['surgery_order'],
                name='unique_current_pacu_record_per_order',
            ),
        ),
    ]
