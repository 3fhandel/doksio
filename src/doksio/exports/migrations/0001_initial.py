from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0013_documentspace_datev_document_image_export_enabled"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExportRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("export_type", models.CharField(choices=[("datev_document_images", "DATEV Belegbilder")], default="datev_document_images", max_length=60)),
                ("status", models.CharField(choices=[("processing", "In Verarbeitung"), ("completed", "Abgeschlossen"), ("completed_with_warnings", "Mit Warnungen"), ("failed", "Fehlgeschlagen")], default="processing", max_length=40)),
                ("filters", models.JSONField(blank=True, default=dict)),
                ("filename", models.CharField(blank=True, max_length=255)),
                ("item_count", models.PositiveIntegerField(default=0)),
                ("exported_count", models.PositiveIntegerField(default=0)),
                ("warning_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_export_runs", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="export_runs", to="tenancy.tenant")),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="ExportRunItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("exported", "Exportiert"), ("skipped", "Übersprungen"), ("failed", "Fehlgeschlagen")], default="exported", max_length=30)),
                ("exported_filename", models.CharField(blank=True, max_length=500)),
                ("message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("document", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="export_items", to="documents.document")),
                ("document_file", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="export_items", to="documents.documentfile")),
                ("export_run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="exports.exportrun")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="export_run_items", to="tenancy.tenant")),
            ],
            options={
                "ordering": ["export_run", "document_id"],
            },
        ),
        migrations.AddIndex(
            model_name="exportrun",
            index=models.Index(fields=["tenant", "export_type", "-created_at"], name="exports_exp_tenant__614252_idx"),
        ),
        migrations.AddIndex(
            model_name="exportrun",
            index=models.Index(fields=["tenant", "status"], name="exports_exp_tenant__46d711_idx"),
        ),
        migrations.AddIndex(
            model_name="exportrunitem",
            index=models.Index(fields=["tenant", "export_run", "status"], name="exports_exp_tenant__c004e7_idx"),
        ),
        migrations.AddIndex(
            model_name="exportrunitem",
            index=models.Index(fields=["tenant", "document"], name="exports_exp_tenant__4f1313_idx"),
        ),
    ]
