from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class DocumentSpace(models.Model):
    """Hierarchical tenant-specific document area."""

    class SpaceKind(models.TextChoices):
        GENERAL = "general", "General"
        INVOICES = "invoices", "Invoices"
        PERSONNEL = "personnel", "Personnel"
        CONTRACTS = "contracts", "Contracts"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="document_spaces",
    )
    parent = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=80)
    path = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    datev_document_image_export_enabled = models.BooleanField(default=False)
    space_kind = models.CharField(
        max_length=30,
        choices=SpaceKind.choices,
        default=SpaceKind.GENERAL,
    )
    review_assist_enabled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "path"],
                name="unique_tenant_document_space_path",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "parent"]),
            models.Index(fields=["tenant", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.path


class Document(models.Model):
    """Logical document container around immutable files and mutable metadata."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Aktiv"
        DELETED = "deleted", "Gelöscht"

    class TitleSource(models.TextChoices):
        MANUAL = "manual", "Manuell"
        FILENAME = "filename", "Dateiname"
        OCR = "ocr", "OCR"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="documents",
    )
    space = models.ForeignKey(
        DocumentSpace,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    title = models.CharField(max_length=255)
    title_source = models.CharField(
        max_length=20,
        choices=TitleSource.choices,
        default=TitleSource.MANUAL,
    )
    document_date = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    metadata = models.JSONField(default=dict, blank=True)
    einvoice_data = models.JSONField(default=dict, blank=True)
    deleted_reason = models.TextField(blank=True)
    deleted_at = models.DateTimeField(blank=True, null=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="deleted_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_documents",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "space"]),
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "status", "-created_at", "-id"]),
            models.Index(fields=["tenant", "space", "status", "-created_at", "-id"]),
            models.Index(fields=["tenant", "document_date", "-created_at", "-id"]),
        ]

    def __str__(self) -> str:
        return self.title


class DocumentMetadataField(models.Model):
    """Configurable metadata field belonging to one document box."""

    class FieldType(models.TextChoices):
        TEXT = "text", "Text"
        MULTILINE_TEXT = "multiline_text", "Mehrzeiliger Text"
        DATE = "date", "Datum"
        NUMBER = "number", "Zahl"
        BOOLEAN = "boolean", "Ja/Nein"
        CHOICE = "choice", "Auswahl"

    class EInvoiceSource(models.TextChoices):
        NONE = "", "Keine automatische Übernahme"
        INVOICE_NUMBER = "invoice_number", "Rechnungsnummer"
        INVOICE_DATE = "invoice_date", "Rechnungsdatum"
        SELLER_NAME = "seller_name", "Verkäufer"
        BUYER_NAME = "buyer_name", "Käufer"
        CURRENCY = "currency", "Währung"
        LINE_TOTAL_AMOUNT = "line_total_amount", "Zeilensumme netto"
        TAX_BASIS_TOTAL_AMOUNT = "tax_basis_total_amount", "Steuerbasis gesamt"
        TAX_TOTAL_AMOUNT = "tax_total_amount", "Steuerbetrag gesamt"
        GRAND_TOTAL_AMOUNT = "grand_total_amount", "Gesamtbetrag"
        DUE_PAYABLE_AMOUNT = "due_payable_amount", "Zahlbetrag"
        TAX_BREAKDOWN_SUMMARY = "tax_breakdown_summary", "Steueraufteilung"
        TAX_NET_0 = "tax_net_0", "Netto 0 %"
        TAX_NET_7 = "tax_net_7", "Netto 7 %"
        TAX_NET_19 = "tax_net_19", "Netto 19 %"
        TAX_AMOUNT_7 = "tax_amount_7", "Steuer 7 %"
        TAX_AMOUNT_19 = "tax_amount_19", "Steuer 19 %"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_metadata_fields",
    )
    space = models.ForeignKey(
        DocumentSpace,
        on_delete=models.CASCADE,
        related_name="metadata_fields",
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80)
    field_type = models.CharField(
        max_length=30,
        choices=FieldType.choices,
        default=FieldType.TEXT,
    )
    help_text = models.CharField(max_length=255, blank=True)
    choices = models.JSONField(default=list, blank=True)
    allow_custom_choices = models.BooleanField(default=False)
    einvoice_source = models.CharField(
        max_length=80,
        choices=EInvoiceSource.choices,
        blank=True,
        default=EInvoiceSource.NONE,
    )
    is_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["space__path", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["space", "slug"],
                name="unique_document_box_metadata_field_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "space", "is_active"]),
            models.Index(fields=["tenant", "slug"]),
        ]

    def __str__(self) -> str:
        return f"{self.space.path}: {self.name}"


class DocumentTag(models.Model):
    """Tenant-owned tag that can be assigned to documents."""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_tags",
    )
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80)
    color = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "slug"],
                name="unique_tenant_document_tag_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "name"]),
        ]

    def __str__(self) -> str:
        return self.name


class DocumentTagAssignment(models.Model):
    """Connects one tag to one document."""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_tag_assignments",
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="tag_assignments",
    )
    tag = models.ForeignKey(
        DocumentTag,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_document_tag_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["tag__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "tag"],
                name="unique_document_tag_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "document"]),
            models.Index(fields=["tenant", "tag"]),
        ]

    def __str__(self) -> str:
        return f"{self.document} #{self.tag}"


class DocumentComment(models.Model):
    """Append-only comment attached to one document."""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_comments",
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    body = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_document_comments",
    )
    mentioned_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="mentioned_document_comments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["tenant", "document", "created_at"]),
            models.Index(fields=["tenant", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Comment on {self.document_id} at {self.created_at}"


class DocumentFile(models.Model):
    """Immutable file artifact belonging to a document."""

    class Kind(models.TextChoices):
        ORIGINAL = "original", "Original"
        DERIVATIVE = "derivative", "Derivative"
        THUMBNAIL = "thumbnail", "Thumbnail"

    IMMUTABLE_FIELDS = (
        "tenant_id",
        "document_id",
        "file_kind",
        "version",
        "storage_key",
        "original_filename",
        "content_type",
        "byte_size",
        "sha256",
        "derivative_of_id",
        "created_by_id",
    )

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="document_files",
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.PROTECT,
        related_name="files",
    )
    file_kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.ORIGINAL,
    )
    version = models.PositiveIntegerField()
    storage_key = models.CharField(max_length=500, unique=True)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    byte_size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    derivative_of = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="derivatives",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_document_files",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document_id", "file_kind", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "file_kind", "version"],
                name="unique_document_file_kind_version",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "sha256"]),
            models.Index(fields=["tenant", "file_kind"]),
            models.Index(fields=["tenant", "document", "file_kind", "-version"]),
            models.Index(fields=["tenant", "sha256", "byte_size", "file_kind"]),
        ]

    def __str__(self) -> str:
        return f"{self.document} {self.file_kind} v{self.version}"

    def save(self, *args, **kwargs) -> None:
        if self.pk is not None:
            existing = type(self).objects.get(pk=self.pk)
            changed_fields = [
                field
                for field in self.IMMUTABLE_FIELDS
                if getattr(existing, field) != getattr(self, field)
            ]
            if changed_fields:
                raise ValidationError(
                    {
                        field: "Document file artifacts are immutable."
                        for field in changed_fields
                    }
                )

        super().save(*args, **kwargs)

    @property
    def latest_ocr_job(self):
        jobs = self.ocr_jobs.all()
        return jobs[0] if jobs else None
