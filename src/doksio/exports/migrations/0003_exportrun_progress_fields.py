from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exports", "0002_exportrun_artifact_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="exportrun",
            name="processed_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="exportrun",
            name="total_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
