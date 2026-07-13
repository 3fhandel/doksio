from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exports", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="exportrun",
            name="byte_size",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="exportrun",
            name="sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="exportrun",
            name="storage_key",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
