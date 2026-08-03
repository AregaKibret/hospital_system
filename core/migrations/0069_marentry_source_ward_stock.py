from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0068_specialty_assignment'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='marentry',
            name='source',
            field=models.CharField(
                choices=[
                    ('pharmacy', 'Pharmacy Dispensed'),
                    ('ward_stock', 'Ward/Floor Stock'),
                    ('patient_supplied', 'Patient-Supplied'),
                ],
                default='pharmacy',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='marentry',
            name='ward_stock_item',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='mar_entries',
                to='core.departmentstock',
            ),
        ),
        migrations.AddField(
            model_name='marentry',
            name='ward_stock_batch',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='mar_entries',
                to='core.departmentstockbatch',
            ),
        ),
        migrations.AddField(
            model_name='marentry',
            name='charge_amount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=10),
        ),
    ]
