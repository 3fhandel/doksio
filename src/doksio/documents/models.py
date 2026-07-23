from __future__ import annotations

import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


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
    deleted_at = models.DateTimeField(blank=True, null=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="deleted_document_spaces",
    )
    deleted_strategy = models.CharField(max_length=30, blank=True)
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


class DocumentTitleRule(models.Model):
    """Tenant default or document-space override for automatic title discovery."""

    class Strategy(models.TextChoices):
        AUTOMATIC = "automatic", "Automatisch aus dem OCR-Volltext"
        REGEX = "regex", "RegEx auf dem OCR-Volltext"
        EINVOICE = "einvoice", "Aus eRechnungsdaten"
        DISABLED = "disabled", "Keine automatische Titelfindung"

    class FallbackStrategy(models.TextChoices):
        AUTOMATIC = "automatic", "OCR-Automatik"
        REGEX = "regex", "OCR-RegEx"
        DISABLED = "disabled", "Dateiname beibehalten"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_title_rules",
    )
    document_space = models.ForeignKey(
        DocumentSpace,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="title_rules",
    )
    strategy = models.CharField(
        max_length=20,
        choices=Strategy.choices,
        default=Strategy.AUTOMATIC,
    )
    regex_search = models.CharField(max_length=1000, blank=True)
    regex_replace = models.CharField(max_length=1000, blank=True)
    einvoice_format = models.CharField(
        max_length=1000,
        blank=True,
        default="{seller_name:.12}: {invoice_number}{invoice_date_suffix}",
    )
    fallback_strategy = models.CharField(
        max_length=20,
        choices=FallbackStrategy.choices,
        default=FallbackStrategy.AUTOMATIC,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["document_space__path", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "document_space"],
                name="unique_tenant_document_space_title_rule",
            ),
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(document_space__isnull=True),
                name="unique_tenant_default_title_rule",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "document_space"]),
        ]

    def __str__(self) -> str:
        scope = self.document_space.path if self.document_space else "Standard"
        return f"{self.tenant}: {scope} ({self.get_strategy_display()})"

    def clean(self) -> None:
        super().clean()
        if (
            self.document_space_id
            and self.tenant_id
            and self.document_space.tenant_id != self.tenant_id
        ):
            raise ValidationError(
                {"document_space": "Die Dokumentenbox gehört nicht zu diesem Tenant."}
            )
        uses_regex = self.strategy == self.Strategy.REGEX or (
            self.strategy == self.Strategy.EINVOICE
            and self.fallback_strategy == self.FallbackStrategy.REGEX
        )
        if uses_regex:
            if not self.regex_search.strip():
                raise ValidationError(
                    {
                        "regex_search": (
                            "Für die RegEx-Strategie ist ein Suchmuster erforderlich."
                        )
                    }
                )
            try:
                re.compile(self.regex_search)
            except re.error as error:
                raise ValidationError(
                    {"regex_search": f"Ungültiger regulärer Ausdruck: {error}"}
                ) from error
        if self.strategy == self.Strategy.EINVOICE:
            from doksio.documents.title_rules import validate_einvoice_title_format

            try:
                validate_einvoice_title_format(self.einvoice_format)
            except ValueError as error:
                raise ValidationError({"einvoice_format": str(error)}) from error

    def as_policy(self) -> dict[str, str]:
        return {
            "strategy": self.strategy,
            "regex_search": self.regex_search,
            "regex_replace": self.regex_replace,
            "einvoice_format": self.einvoice_format,
            "fallback_strategy": self.fallback_strategy,
        }


class Document(models.Model):
    """Logical document container around immutable files and mutable metadata."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Aktiv"
        DELETED = "deleted", "Gelöscht"

    class TitleSource(models.TextChoices):
        MANUAL = "manual", "Manuell"
        FILENAME = "filename", "Dateiname"
        OCR = "ocr", "OCR"
        EINVOICE = "einvoice", "eRechnung"

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


class DocumentRelation(models.Model):
    """Neutral relation between two tenant documents."""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_relations",
    )
    first_document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="relations_as_first",
    )
    second_document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="relations_as_second",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_document_relations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["first_document", "second_document"],
                name="unique_document_relation_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(first_document=models.F("second_document")),
                name="document_relation_distinct_documents",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "first_document"],
                name="documents_d_tenant__91d4fa_idx",
            ),
            models.Index(
                fields=["tenant", "second_document"],
                name="documents_d_tenant__a02a1d_idx",
            ),
            models.Index(
                fields=["tenant", "created_at"],
                name="documents_d_tenant__e08d0c_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.first_document_id} <-> {self.second_document_id}"

    def other_document(self, document: Document) -> Document:
        if document.id == self.first_document_id:
            return self.second_document
        return self.first_document


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
        PREVIEW = "preview", "Vorschau"
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
    viewer_settings = models.JSONField(default=dict, blank=True)
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


class DocumentImportBatch(models.Model):
    """Staging area for multi-file imports before documents are created."""

    class Status(models.TextChoices):
        OPEN = "open", "Offen"
        COMPLETED = "completed", "Abgeschlossen"
        DISCARDED = "discarded", "Verworfen"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_import_batches",
    )
    title = models.CharField(max_length=255)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_document_import_batches",
    )
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.title


class DocumentImportBatchItem(models.Model):
    """Single staged file in a document import batch."""

    class Status(models.TextChoices):
        STAGED = "staged", "Bereit"
        IMPORTED = "imported", "Importiert"
        DUPLICATE = "duplicate", "Dublette"
        SKIPPED = "skipped", "Übersprungen"
        ERROR = "error", "Fehler"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_import_batch_items",
    )
    batch = models.ForeignKey(
        DocumentImportBatch,
        on_delete=models.CASCADE,
        related_name="items",
    )
    source_storage_key = models.CharField(max_length=500)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    byte_size = models.PositiveBigIntegerField(default=0)
    suggested_space = models.ForeignKey(
        DocumentSpace,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="suggested_import_batch_items",
    )
    target_space = models.ForeignKey(
        DocumentSpace,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="targeted_import_batch_items",
    )
    suggestion_reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.STAGED,
    )
    message = models.TextField(blank=True)
    imported_document = models.ForeignKey(
        Document,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="import_batch_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["tenant", "batch", "status"]),
            models.Index(fields=["tenant", "status", "created_at"]),
        ]

    def __str__(self) -> str:
        return self.original_filename


class DocumentBoxScanOptimizationJob(models.Model):
    """Tenant maintenance job for compacting stored scan PDFs in one box."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Wartet"
        RUNNING = "running", "Läuft"
        COMPLETED = "completed", "Abgeschlossen"
        FAILED = "failed", "Fehlgeschlagen"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_box_scan_optimization_jobs",
    )
    document_space = models.ForeignKey(
        DocumentSpace,
        on_delete=models.CASCADE,
        related_name="scan_optimization_jobs",
    )
    include_children = models.BooleanField(default=True)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    total_documents = models.PositiveIntegerField(default=0)
    processed_documents = models.PositiveIntegerField(default=0)
    last_document_id = models.PositiveIntegerField(default=0)
    max_document_id = models.PositiveIntegerField(default=0)
    candidates = models.PositiveIntegerField(default=0)
    optimized = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    bytes_before = models.PositiveBigIntegerField(default=0)
    bytes_after = models.PositiveBigIntegerField(default=0)
    batch_size = models.PositiveIntegerField(default=100)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_scan_optimization_jobs",
    )
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    heartbeat_at = models.DateTimeField(blank=True, null=True)
    lease_token = models.UUIDField(blank=True, null=True, editable=False)
    lease_expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "status", "-created_at"]),
            models.Index(fields=["tenant", "document_space", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.document_space.path} {self.get_status_display()}"

    @property
    def saved_bytes(self) -> int:
        return max(self.bytes_before - self.bytes_after, 0)

    @property
    def progress_percent(self) -> int:
        if not self.total_documents:
            return 0
        return min(100, round(self.processed_documents / self.total_documents * 100))

    @property
    def is_resumable(self) -> bool:
        if self.status not in {self.Status.QUEUED, self.Status.RUNNING}:
            return False
        now = timezone.now()
        if self.lease_expires_at is not None:
            return self.lease_expires_at <= now
        stale_after = timedelta(
            seconds=getattr(
                settings,
                "SCAN_OPTIMIZATION_STALE_AFTER_SECONDS",
                120,
            )
        )
        last_activity = self.heartbeat_at or self.updated_at
        return last_activity <= now - stale_after


class DocumentBoxTitleRefreshJob(models.Model):
    """Tenant maintenance job for recalculating document titles in one box."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Wartet"
        RUNNING = "running", "Läuft"
        COMPLETED = "completed", "Abgeschlossen"
        FAILED = "failed", "Fehlgeschlagen"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_box_title_refresh_jobs",
    )
    document_space = models.ForeignKey(
        DocumentSpace,
        on_delete=models.CASCADE,
        related_name="title_refresh_jobs",
    )
    include_children = models.BooleanField(default=True)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    total_documents = models.PositiveIntegerField(default=0)
    processed_documents = models.PositiveIntegerField(default=0)
    last_document_id = models.PositiveIntegerField(default=0)
    max_document_id = models.PositiveIntegerField(default=0)
    updated_titles = models.PositiveIntegerField(default=0)
    unchanged_titles = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    batch_size = models.PositiveIntegerField(default=100)
    rule_snapshot = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_title_refresh_jobs",
    )
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    heartbeat_at = models.DateTimeField(blank=True, null=True)
    lease_token = models.UUIDField(blank=True, null=True, editable=False)
    lease_expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "status", "-created_at"]),
            models.Index(fields=["tenant", "document_space", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.document_space.path} {self.get_status_display()}"

    @property
    def progress_percent(self) -> int:
        if not self.total_documents:
            return 0
        return min(100, round(self.processed_documents / self.total_documents * 100))

    @property
    def is_resumable(self) -> bool:
        if self.status not in {self.Status.QUEUED, self.Status.RUNNING}:
            return False
        now = timezone.now()
        if self.lease_expires_at is not None:
            return self.lease_expires_at <= now
        stale_after = timedelta(
            seconds=getattr(
                settings,
                "TITLE_REFRESH_STALE_AFTER_SECONDS",
                120,
            )
        )
        last_activity = self.heartbeat_at or self.updated_at
        return last_activity <= now - stale_after
