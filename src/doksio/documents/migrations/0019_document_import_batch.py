import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0018_documentfile_viewer_settings"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentImportBatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Offen"), ("completed", "Abgeschlossen")],
                        default="open",
                        max_length=30,
                    ),
                ),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_document_import_batches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_import_batches",
                        to="tenancy.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["tenant", "status", "-created_at"],
                        name="documents_d_tenant__859d70_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="DocumentImportBatchItem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("source_storage_key", models.CharField(max_length=500)),
                ("original_filename", models.CharField(max_length=255)),
                ("content_type", models.CharField(max_length=120)),
                ("byte_size", models.PositiveBigIntegerField(default=0)),
                ("suggestion_reason", models.CharField(blank=True, max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("staged", "Bereit"),
                            ("imported", "Importiert"),
                            ("duplicate", "Dublette"),
                            ("skipped", "Übersprungen"),
                            ("error", "Fehler"),
                        ],
                        default="staged",
                        max_length=30,
                    ),
                ),
                ("message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="documents.documentimportbatch",
                    ),
                ),
                (
                    "imported_document",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="import_batch_items",
                        to="documents.document",
                    ),
                ),
                (
                    "suggested_space",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="suggested_import_batch_items",
                        to="documents.documentspace",
                    ),
                ),
                (
                    "target_space",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="targeted_import_batch_items",
                        to="documents.documentspace",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_import_batch_items",
                        to="tenancy.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
                "indexes": [
                    models.Index(
                        fields=["tenant", "batch", "status"],
                        name="documents_d_tenant__cc8274_idx",
                    ),
                    models.Index(
                        fields=["tenant", "status", "created_at"],
                        name="documents_d_tenant__3e2cfd_idx",
                    ),
                ],
            },
        ),
    ]
