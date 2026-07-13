from __future__ import annotations

from django.conf import settings
from django.db import models


class ExportRun(models.Model):
    """One tenant-scoped export run."""

    class ExportType(models.TextChoices):
        DATEV_DOCUMENT_IMAGES = "datev_document_images", "DATEV Belegbilder"

    class Status(models.TextChoices):
        PROCESSING = "processing", "In Verarbeitung"
        COMPLETED = "completed", "Abgeschlossen"
        COMPLETED_WITH_WARNINGS = "completed_with_warnings", "Mit Warnungen"
        FAILED = "failed", "Fehlgeschlagen"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="export_runs",
    )
    export_type = models.CharField(
        max_length=60,
        choices=ExportType.choices,
        default=ExportType.DATEV_DOCUMENT_IMAGES,
    )
    status = models.CharField(
        max_length=40,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    filters = models.JSONField(default=dict, blank=True)
    filename = models.CharField(max_length=255, blank=True)
    storage_key = models.CharField(max_length=500, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    item_count = models.PositiveIntegerField(default=0)
    exported_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_export_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "export_type", "-created_at"]),
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_export_type_display()} #{self.id}"


class ExportRunItem(models.Model):
    """Per-document result of an export run."""

    class Status(models.TextChoices):
        EXPORTED = "exported", "Exportiert"
        SKIPPED = "skipped", "Übersprungen"
        FAILED = "failed", "Fehlgeschlagen"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="export_run_items",
    )
    export_run = models.ForeignKey(
        ExportRun,
        on_delete=models.CASCADE,
        related_name="items",
    )
    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.PROTECT,
        related_name="export_items",
    )
    document_file = models.ForeignKey(
        "documents.DocumentFile",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="export_items",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.EXPORTED,
    )
    exported_filename = models.CharField(max_length=500, blank=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["export_run", "document_id"]
        indexes = [
            models.Index(fields=["tenant", "export_run", "status"]),
            models.Index(fields=["tenant", "document"]),
        ]

    def __str__(self) -> str:
        return f"{self.export_run_id}: {self.document_id} ({self.status})"
