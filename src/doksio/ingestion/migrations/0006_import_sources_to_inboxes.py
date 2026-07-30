from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0033_document_inboxes"),
        ("ingestion", "0005_emailautoreplyrecipient"),
    ]

    operations = [
        migrations.AddField(
            model_name="importsource",
            name="document_inbox",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="import_sources",
                to="documents.documentinbox",
            ),
        ),
        migrations.AlterField(
            model_name="importsource",
            name="document_space",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="import_sources",
                to="documents.documentspace",
            ),
        ),
        migrations.AddField(
            model_name="importjob",
            name="inbox_item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="import_jobs",
                to="documents.documentimportbatchitem",
            ),
        ),
        migrations.AlterField(
            model_name="importjob",
            name="document_space",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="import_jobs",
                to="documents.documentspace",
            ),
        ),
        migrations.AlterModelOptions(
            name="importsource",
            options={"ordering": ["name", "id"]},
        ),
        migrations.AlterField(
            model_name="importsource",
            name="target_strategy",
            field=models.CharField(
                choices=[
                    ("fixed", "Feste Dokumentenbox"),
                    ("rules", "Regeln"),
                    ("intelligent", "Intelligent"),
                    ("inbox", "Posteingang"),
                ],
                default="fixed",
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="importjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("received", "Empfangen"),
                    ("processing", "In Verarbeitung"),
                    ("staged", "Im Posteingang"),
                    ("imported", "Importiert"),
                    ("failed", "Fehlgeschlagen"),
                ],
                default="received",
                max_length=30,
            ),
        ),
    ]
