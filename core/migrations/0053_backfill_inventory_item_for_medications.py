from django.db import migrations


def backfill_inventory_items(apps, schema_editor):
    Medication = apps.get_model('core', 'Medication')
    InventoryItem = apps.get_model('core', 'InventoryItem')

    existing_codes = set(InventoryItem.objects.values_list('item_code', flat=True))

    for med in Medication.objects.filter(inventory_item__isnull=True):
        item_code = med.code
        if item_code in existing_codes:
            item_code = f'{med.code}-MED{med.pk}'
        existing_codes.add(item_code)

        item = InventoryItem.objects.create(
            name=med.name,
            item_code=item_code,
            generic_name=med.generic_name,
            item_type='Medication',
            category=None,
            dosage_form=med.dosage_form,
            strength=med.strength,
            barcode=med.barcode,
            manufacturer=med.manufacturer,
            unit=med.unit_of_measure,
            unit_purchase=med.purchase_unit,
            dispensing_unit=med.dispensing_unit,
            consumption_factor=med.conversion_factor or 1,
            reorder_level=med.reorder_level,
            reorder_quantity=med.reorder_quantity,
            min_stock=med.minimum_stock,
            max_stock=med.maximum_stock or None,
            safety_stock=med.safety_stock,
            unit_cost=med.purchase_price,
            selling_price=med.selling_price,
            selling_price_is_manual=med.selling_price_is_manual,
            supplier=med.supplier,
            storage_location=med.location,
            is_active=med.is_active,
        )
        med.inventory_item_id = item.pk
        med.save(update_fields=['inventory_item'])


def reverse_backfill(apps, schema_editor):
    Medication = apps.get_model('core', 'Medication')
    InventoryItem = apps.get_model('core', 'InventoryItem')

    item_ids = list(Medication.objects.filter(inventory_item__isnull=False).values_list('inventory_item_id', flat=True))
    Medication.objects.update(inventory_item=None)
    InventoryItem.objects.filter(id__in=item_ids, item_type='Medication').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0052_inventory_item_master_step1'),
    ]

    operations = [
        migrations.RunPython(backfill_inventory_items, reverse_backfill),
    ]
