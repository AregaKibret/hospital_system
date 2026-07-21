import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0053_backfill_inventory_item_for_medications'),
    ]

    operations = [
        # Preserve existing brand-name data by renaming, not drop+recreate.
        migrations.RenameField(
            model_name='medication',
            old_name='name',
            new_name='brand_name',
        ),
        migrations.AlterField(
            model_name='medication',
            name='inventory_item',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='medication_details',
                to='core.inventoryitem',
            ),
        ),
        migrations.RemoveField(model_name='medication', name='code'),
        migrations.RemoveField(model_name='medication', name='barcode'),
        migrations.RemoveField(model_name='medication', name='category'),
        migrations.RemoveField(model_name='medication', name='manufacturer'),
        migrations.RemoveField(model_name='medication', name='supplier'),
        migrations.RemoveField(model_name='medication', name='unit_of_measure'),
        migrations.RemoveField(model_name='medication', name='purchase_unit'),
        migrations.RemoveField(model_name='medication', name='dispensing_unit'),
        migrations.RemoveField(model_name='medication', name='conversion_factor'),
        migrations.RemoveField(model_name='medication', name='purchase_price'),
        migrations.RemoveField(model_name='medication', name='selling_price'),
        migrations.RemoveField(model_name='medication', name='selling_price_is_manual'),
        migrations.RemoveField(model_name='medication', name='minimum_stock'),
        migrations.RemoveField(model_name='medication', name='maximum_stock'),
        migrations.RemoveField(model_name='medication', name='reorder_level'),
        migrations.RemoveField(model_name='medication', name='reorder_quantity'),
        migrations.RemoveField(model_name='medication', name='safety_stock'),
        migrations.RemoveField(model_name='medication', name='location'),
        migrations.RemoveField(model_name='medication', name='is_active'),
    ]
