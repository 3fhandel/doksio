from __future__ import annotations

from django.conf import settings
from django.db import models


class OcrJob(models.Model):
    """One local OCR/text extraction run for one immutable document file."""

    class Status(models.TextChoices):
        PENDING = "pending", "Wartet"
        RUNNING = "running", "Läuft"
        SUCCEEDED = "succeeded", "Erfolgreich"
        FAILED = "failed", "Fehlgeschlagen"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="ocr_jobs",
    )
    document_file = models.ForeignKey(
        "documents.DocumentFile",
        on_delete=models.CASCADE,
        related_name="ocr_jobs",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    engine = models.CharField(max_length=120, blank=True)
    language = models.CharField(max_length=40, blank=True)
    extracted_text = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_ocr_jobs",
    )
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "document_file", "created_at"]),
            models.Index(fields=["tenant", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"OCR {self.document_file_id} {self.status}"
