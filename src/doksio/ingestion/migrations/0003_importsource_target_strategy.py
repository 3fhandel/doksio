from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ingestion", "0002_importsource_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="importsource",
            name="target_strategy",
            field=models.CharField(
                choices=[
                    ("fixed", "Feste Dokumentenbox"),
                    ("rules", "Regeln"),
                    ("intelligent", "Intelligent"),
                ],
                default="fixed",
                max_length=30,
            ),
        ),
    ]
