from __future__ import annotations

import secrets

from django.db import models


def generate_import_token() -> str:
    return secrets.token_urlsafe(32)


class ImportSource(models.Model):
    """Configurable source and rule set for incoming documents."""

    class SourceType(models.TextChoices):
        HTTP_API = "http_api", "HTTP/API"
        UPLOAD = "upload", "Upload"
        EMAIL = "email", "E-Mail"
        FOLDER = "folder", "Ordner"

    class TargetStrategy(models.TextChoices):
        FIXED = "fixed", "Feste Dokumentenbox"
        RULES = "rules", "Regeln"
        INTELLIGENT = "intelligent", "Intelligent"

    class OcrTitleStrategy(models.TextChoices):
        AUTOMATIC = "automatic", "Titel durch OCR setzen (Automatik)"
        REGEX = "regex", "Titel durch OCR setzen (RegEx)"
        DISABLED = "disabled", "Titel nicht durch OCR setzen"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="import_sources",
    )
    document_space = models.ForeignKey(
        "documents.DocumentSpace",
        on_delete=models.CASCADE,
        related_name="import_sources",
    )
    name = models.CharField(max_length=160)
    source_type = models.CharField(
        max_length=40,
        choices=SourceType.choices,
        default=SourceType.HTTP_API,
    )
    target_strategy = models.CharField(
        max_length=30,
        choices=TargetStrategy.choices,
        default=TargetStrategy.FIXED,
    )
    token = models.CharField(max_length=120, default=generate_import_token)
    settings = models.JSONField(default=dict, blank=True)
    auto_start_ocr = models.BooleanField(default=True)
    extract_einvoice = models.BooleanField(default=True)
    start_workflows = models.BooleanField(default=True)
    default_tags = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["document_space__path", "name"]
        indexes = [
            models.Index(fields=["tenant", "source_type", "is_active"]),
            models.Index(fields=["tenant", "document_space"]),
            models.Index(fields=["token"]),
        ]

    def __str__(self) -> str:
        return f"{self.document_space.path}: {self.name}"


class ImportJob(models.Model):
    """Audit-friendly processing log for one incoming document."""

    class Status(models.TextChoices):
        RECEIVED = "received", "Empfangen"
        PROCESSING = "processing", "In Verarbeitung"
        IMPORTED = "imported", "Importiert"
        FAILED = "failed", "Fehlgeschlagen"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="import_jobs",
    )
    source = models.ForeignKey(
        ImportSource,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="jobs",
    )
    document_space = models.ForeignKey(
        "documents.DocumentSpace",
        on_delete=models.PROTECT,
        related_name="import_jobs",
    )
    document = models.ForeignKey(
        "documents.Document",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="import_jobs",
    )
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "source", "status"]),
            models.Index(fields=["tenant", "document_space", "received_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.get_status_display()})"


class TenantSmtpSettings(models.Model):
    """Tenant-wide SMTP configuration for outbound import replies."""

    class Security(models.TextChoices):
        SSL = "ssl", "SSL/TLS"
        STARTTLS = "starttls", "STARTTLS"
        NONE = "none", "Keine Verschlüsselung"

    tenant = models.OneToOneField(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="smtp_settings",
    )
    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(default=587)
    security = models.CharField(
        max_length=20,
        choices=Security.choices,
        default=Security.STARTTLS,
    )
    username = models.CharField(max_length=255, blank=True)
    password = models.CharField(max_length=255, blank=True)
    from_email = models.EmailField(blank=True)
    from_name = models.CharField(max_length=160, blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tenant SMTP setting"
        verbose_name_plural = "Tenant SMTP settings"

    def __str__(self) -> str:
        return f"{self.tenant}: {self.host or 'SMTP nicht konfiguriert'}"


class EmailAutoReplyRecipient(models.Model):
    """Tracks recipients protected by per-sender auto-reply limits."""

    class ReplyType(models.TextChoices):
        SUCCESS = "success", "Erfolgreicher Import"
        UNPROCESSABLE = "unprocessable", "Nicht importierbare Mail"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="email_auto_reply_recipients",
    )
    source = models.ForeignKey(
        ImportSource,
        on_delete=models.CASCADE,
        related_name="auto_reply_recipients",
    )
    recipient = models.EmailField()
    reply_type = models.CharField(max_length=30, choices=ReplyType.choices)
    subject = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at", "-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "recipient", "reply_type"],
                name="unique_email_auto_reply_recipient",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "source", "reply_type"],
                name="ingestion_e_tenant__6393c4_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.recipient}: {self.get_reply_type_display()}"
