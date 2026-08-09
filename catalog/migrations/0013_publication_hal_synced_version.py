from django.db import migrations, models
from django.db.models import F


def baseline_existing_hal_records(apps, schema_editor):
    Publication = apps.get_model("catalog", "Publication")
    Publication.objects.exclude(hal_id="").update(hal_synced_version=F("version"))


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0012_halpayload_hal_payload_preprod_only_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="publication",
            name="hal_synced_version",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(
            baseline_existing_hal_records,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
