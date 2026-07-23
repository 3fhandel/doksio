from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


def migrate_import_title_settings(apps, schema_editor):
    DocumentTitleRule = apps.get_model("documents", "DocumentTitleRule")
    ImportSource = apps.get_model("ingestion", "ImportSource")

    sources = ImportSource.objects.order_by("updated_at", "id")
    for source in sources.iterator():
        settings = dict(source.settings or {})
        title = settings.pop("title", None)
        if not isinstance(title, dict):
            continue

        document_space_ids = {source.document_space_id}
        for routing_rule in settings.get("routing_rules", []):
            document_space_id = routing_rule.get("document_space_id")
            if document_space_id:
                document_space_ids.add(document_space_id)

        defaults = {
            "strategy": title.get("strategy", "automatic"),
            "regex_search": title.get("regex_search", ""),
            "regex_replace": title.get("regex_replace", ""),
        }
        for document_space_id in document_space_ids:
            DocumentTitleRule.objects.update_or_create(
                tenant_id=source.tenant_id,
                document_space_id=document_space_id,
                defaults=defaults,
            )

        source.settings = settings
        source.save(update_fields=["settings"])


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0023_documentboxscanoptimizationjob_lease_fields"),
        ("ingestion", "0005_emailautoreplyrecipient"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentTitleRule",
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
                (
                    "strategy",
                    models.CharField(
                        choices=[
                            ("automatic", "Automatisch aus dem OCR-Volltext"),
                            ("regex", "RegEx auf dem OCR-Volltext"),
                            ("disabled", "Keine automatische Titelfindung"),
                        ],
                        default="automatic",
                        max_length=20,
                    ),
                ),
                ("regex_search", models.CharField(blank=True, max_length=1000)),
                ("regex_replace", models.CharField(blank=True, max_length=1000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "document_space",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="title_rules",
                        to="documents.documentspace",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_title_rules",
                        to="tenancy.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["document_space__path", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="documenttitlerule",
            constraint=models.UniqueConstraint(
                fields=("tenant", "document_space"),
                name="unique_tenant_document_space_title_rule",
            ),
        ),
        migrations.AddConstraint(
            model_name="documenttitlerule",
            constraint=models.UniqueConstraint(
                condition=models.Q(("document_space__isnull", True)),
                fields=("tenant",),
                name="unique_tenant_default_title_rule",
            ),
        ),
        migrations.AddIndex(
            model_name="documenttitlerule",
            index=models.Index(
                fields=["tenant", "document_space"],
                name="documents_d_tenant__4c022c_idx",
            ),
        ),
        migrations.RunPython(
            migrate_import_title_settings,
            migrations.RunPython.noop,
        ),
    ]
