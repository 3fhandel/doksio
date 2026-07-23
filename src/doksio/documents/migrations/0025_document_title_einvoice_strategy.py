from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0024_documenttitlerule"),
    ]

    operations = [
        migrations.AddField(
            model_name="documenttitlerule",
            name="einvoice_format",
            field=models.CharField(
                blank=True,
                default=("{seller_name:.12}: {invoice_number}{invoice_date_suffix}"),
                max_length=1000,
            ),
        ),
        migrations.AddField(
            model_name="documenttitlerule",
            name="fallback_strategy",
            field=models.CharField(
                choices=[
                    ("automatic", "OCR-Automatik"),
                    ("regex", "OCR-RegEx"),
                    ("disabled", "Dateiname beibehalten"),
                ],
                default="automatic",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="documenttitlerule",
            name="strategy",
            field=models.CharField(
                choices=[
                    ("automatic", "Automatisch aus dem OCR-Volltext"),
                    ("regex", "RegEx auf dem OCR-Volltext"),
                    ("einvoice", "Aus eRechnungsdaten"),
                    ("disabled", "Keine automatische Titelfindung"),
                ],
                default="automatic",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="document",
            name="title_source",
            field=models.CharField(
                choices=[
                    ("manual", "Manuell"),
                    ("filename", "Dateiname"),
                    ("ocr", "OCR"),
                    ("einvoice", "eRechnung"),
                ],
                default="manual",
                max_length=20,
            ),
        ),
    ]
