from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0069_marentry_source_ward_stock'),
    ]

    operations = [
        # Make prescription_item nullable (direct ward orders have no prescription)
        migrations.AlterField(
            model_name='marentry',
            name='prescription_item',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='mar_entries',
                to='core.prescriptionitem',
            ),
        ),
        # Add direct link to MedicationOrder
        migrations.AddField(
            model_name='marentry',
            name='medication_order',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='mar_entries',
                to='core.medicationorder',
            ),
        ),
    ]
