import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='visit',
            name='visit_date',
        ),
        migrations.RemoveField(
            model_name='visit',
            name='status',
        ),
        migrations.AddField(
            model_name='visit',
            name='department',
            field=models.ForeignKey(
                default=1,
                on_delete=django.db.models.deletion.CASCADE,
                to='core.department',
            ),
            preserve_default=False,
        ),
    ]
