from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ocr", "0002_ocrjob_metadata")]

    operations = [
        migrations.AddField(
            model_name="ocrjob",
            name="layout_storage_key",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="ocrjob",
            name="layout_byte_size",
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
