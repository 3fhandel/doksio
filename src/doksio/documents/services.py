from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from doksio.audit.services import RecordAuditEvent
from doksio.documents.mentions import display_name_for_user, mentioned_users_from_text
from doksio.documents.metadata import effective_metadata_fields
from doksio.documents.models import (
    Document,
    DocumentComment,
    DocumentFile,
    DocumentMetadataField,
    DocumentSpace,
    DocumentTag,
    DocumentTagAssignment,
)
from doksio.documents.thumbnails import (
    create_preview_for_document_file,
    create_thumbnail_for_document_file,
)
from doksio.storage.services import StoreImmutableFile
from doksio.tenancy.models import Tenant


class DuplicateDocumentError(ValueError):
    def __init__(self, existing_file: DocumentFile) -> None:
        self.existing_file = existing_file
        self.existing_document = existing_file.document
        super().__init__(
            f"Diese Datei existiert bereits als Dokument {self.existing_document.id}."
        )


def _iter_file_chunks(file_obj: BinaryIO, chunk_size: int = 1024 * 1024):
    if hasattr(file_obj, "chunks"):
        yield from file_obj.chunks()
        return

    while chunk := file_obj.read(chunk_size):
        yield chunk


def _buffer_and_hash_file(
    file_obj: BinaryIO,
    buffered_file: SpooledTemporaryFile,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_size = 0
    for chunk in _iter_file_chunks(file_obj):
        digest.update(chunk)
        byte_size += len(chunk)
        buffered_file.write(chunk)
    buffered_file.seek(0)
    return digest.hexdigest(), byte_size


def _fallback_title_from_filename(original_filename: str) -> str:
    title = original_filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
    return title or original_filename or "Unbenanntes Dokument"


def _normalized_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _converted_pdf_filename(original_filename: str) -> str:
    path = Path(original_filename.rsplit("/", 1)[-1])
    stem = path.stem.strip()
    return f"{stem or 'document'}.pdf"


def _convert_tiff_to_pdf(
    *,
    file_obj: BinaryIO,
    original_filename: str,
) -> tuple[SpooledTemporaryFile, str, str]:
    try:
        from PIL import Image, ImageOps, ImageSequence
    except ImportError as exc:
        raise ValueError("TIFF-Konvertierung ist nicht verfügbar.") from exc

    output = SpooledTemporaryFile(max_size=10 * 1024 * 1024)  # noqa: SIM115
    try:
        with Image.open(file_obj) as image:
            pages = []
            for frame in ImageSequence.Iterator(image):
                page = ImageOps.exif_transpose(frame)
                page.load()
                if page.mode == "RGBA":
                    background = Image.new("RGB", page.size, "white")
                    background.paste(page, mask=page.getchannel("A"))
                    page = background
                elif page.mode != "RGB":
                    page = page.convert("RGB")
                pages.append(page.copy())

            if not pages:
                raise ValueError("TIFF enthält keine Seiten.")

            first_page, *remaining_pages = pages
            first_page.save(
                output,
                format="PDF",
                save_all=True,
                append_images=remaining_pages,
                resolution=300,
            )
    except Exception as exc:
        output.close()
        raise ValueError("TIFF konnte nicht in PDF konvertiert werden.") from exc

    output.seek(0)
    return output, _converted_pdf_filename(original_filename), "application/pdf"


def _prepare_upload_file_for_storage(
    *,
    file_obj: BinaryIO,
    original_filename: str,
    content_type: str,
) -> tuple[BinaryIO, str, str]:
    if _normalized_content_type(content_type) == "image/tiff":
        return _convert_tiff_to_pdf(
            file_obj=file_obj,
            original_filename=original_filename,
        )
    return file_obj, original_filename, content_type


def _build_space_path(parent: DocumentSpace | None, slug: str) -> str:
    if parent is None:
        return f"/{slug}"
    return f"{parent.path.rstrip('/')}/{slug}"


def _decimal_string(value: str) -> str:
    try:
        return format(Decimal(str(value)), "f")
    except (InvalidOperation, ValueError):
        return str(value)


def _tax_breakdown_value(
    einvoice_data: dict,
    *,
    rate: str,
    amount_key: str,
) -> str:
    normalized_rate = Decimal(rate)
    for row in einvoice_data.get("tax_breakdown", []):
        try:
            row_rate = Decimal(str(row.get("rate", "")))
        except (InvalidOperation, ValueError):
            continue
        if row_rate == normalized_rate:
            return _decimal_string(row.get(amount_key, ""))
    return ""


def _tax_breakdown_summary(einvoice_data: dict) -> str:
    rows = []
    for row in einvoice_data.get("tax_breakdown", []):
        rate = row.get("rate") or "ohne Satz"
        net_amount = row.get("net_amount") or "-"
        tax_amount = row.get("tax_amount") or "-"
        rows.append(f"{rate} %: netto {net_amount}, Steuer {tax_amount}")
    return "\n".join(rows)


def _einvoice_value_for_source(einvoice_data: dict, source: str) -> str:
    if not source:
        return ""

    source_map = {
        DocumentMetadataField.EInvoiceSource.INVOICE_NUMBER: "invoice_number",
        DocumentMetadataField.EInvoiceSource.INVOICE_DATE: "invoice_date",
        DocumentMetadataField.EInvoiceSource.SELLER_NAME: "seller_name",
        DocumentMetadataField.EInvoiceSource.BUYER_NAME: "buyer_name",
        DocumentMetadataField.EInvoiceSource.CURRENCY: "currency",
        DocumentMetadataField.EInvoiceSource.LINE_TOTAL_AMOUNT: "line_total_amount",
        DocumentMetadataField.EInvoiceSource.TAX_BASIS_TOTAL_AMOUNT: (
            "tax_basis_total_amount"
        ),
        DocumentMetadataField.EInvoiceSource.TAX_TOTAL_AMOUNT: "tax_total_amount",
        DocumentMetadataField.EInvoiceSource.GRAND_TOTAL_AMOUNT: "grand_total_amount",
        DocumentMetadataField.EInvoiceSource.DUE_PAYABLE_AMOUNT: "due_payable_amount",
    }
    if source in source_map:
        return str(einvoice_data.get(source_map[source], ""))
    if source == DocumentMetadataField.EInvoiceSource.TAX_BREAKDOWN_SUMMARY:
        return _tax_breakdown_summary(einvoice_data)
    if source == DocumentMetadataField.EInvoiceSource.TAX_NET_0:
        return _tax_breakdown_value(
            einvoice_data,
            rate="0",
            amount_key="net_amount",
        )
    if source == DocumentMetadataField.EInvoiceSource.TAX_NET_7:
        return _tax_breakdown_value(
            einvoice_data,
            rate="7",
            amount_key="net_amount",
        )
    if source == DocumentMetadataField.EInvoiceSource.TAX_NET_19:
        return _tax_breakdown_value(
            einvoice_data,
            rate="19",
            amount_key="net_amount",
        )
    if source == DocumentMetadataField.EInvoiceSource.TAX_AMOUNT_7:
        return _tax_breakdown_value(
            einvoice_data,
            rate="7",
            amount_key="tax_amount",
        )
    if source == DocumentMetadataField.EInvoiceSource.TAX_AMOUNT_19:
        return _tax_breakdown_value(
            einvoice_data,
            rate="19",
            amount_key="tax_amount",
        )
    return ""


def _coerce_einvoice_metadata_value(
    value: str,
    field: DocumentMetadataField,
) -> str | bool:
    if field.field_type == DocumentMetadataField.FieldType.BOOLEAN:
        return value.lower() in {"1", "true", "ja", "yes"}
    if field.field_type == DocumentMetadataField.FieldType.NUMBER:
        return _decimal_string(value)
    if (
        field.field_type == DocumentMetadataField.FieldType.DATE
        and len(value) == 8
        and value.isdigit()
    ):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _metadata_from_einvoice(
    *,
    space: DocumentSpace,
    einvoice_data: dict,
) -> dict:
    metadata = {}
    for field in effective_metadata_fields(space):
        if not field.einvoice_source:
            continue
        value = _einvoice_value_for_source(einvoice_data, field.einvoice_source)
        if value in ("", None):
            continue
        metadata[field.slug] = _coerce_einvoice_metadata_value(str(value), field)
    return metadata


def _format_einvoice_title_date(raw_date: str) -> str:
    if len(raw_date) == 8 and raw_date.isdigit():
        return f"{raw_date[6:]}.{raw_date[4:6]}.{raw_date[:4]}"
    if len(raw_date) == 10 and raw_date[4] == "-" and raw_date[7] == "-":
        return f"{raw_date[8:]}.{raw_date[5:7]}.{raw_date[:4]}"
    return raw_date


def _title_from_einvoice(einvoice_data: dict) -> str:
    seller_name = str(einvoice_data.get("seller_name", "")).strip()
    invoice_number = str(einvoice_data.get("invoice_number", "")).strip()
    invoice_date = str(einvoice_data.get("invoice_date", "")).strip()

    if not seller_name or not invoice_number:
        return ""

    name_prefix = seller_name[:12].strip()
    title = f"{name_prefix}: {invoice_number}"
    if invoice_date:
        title = f"{title} vom {_format_einvoice_title_date(invoice_date)}"
    return title


def _schedule_search_index_rebuild(document: Document) -> None:
    document_id = document.id

    def rebuild() -> None:
        from doksio.search.services import RebuildDocumentSearchIndex

        refreshed_document = Document.objects.get(id=document_id)
        RebuildDocumentSearchIndex(document=refreshed_document).execute()

    transaction.on_commit(rebuild)


@dataclass(frozen=True)
class CreateDocumentSpace:
    tenant: Tenant
    name: str
    parent: DocumentSpace | None = None
    slug: str | None = None
    description: str = ""
    datev_document_image_export_enabled: bool = False
    space_kind: str = DocumentSpace.SpaceKind.GENERAL
    review_assist_enabled: bool = False
    is_active: bool = True

    @transaction.atomic
    def execute(self) -> DocumentSpace:
        if self.parent and self.parent.tenant_id != self.tenant.id:
            raise ValueError("Parent document space belongs to a different tenant.")

        slug = self.slug or slugify(self.name)
        if not slug:
            raise ValueError("Document space slug cannot be empty.")

        path = _build_space_path(self.parent, slug)
        document_space, _created = DocumentSpace.objects.get_or_create(
            tenant=self.tenant,
            path=path,
            defaults={
                "parent": self.parent,
                "name": self.name,
                "slug": slug,
                "description": self.description,
                "datev_document_image_export_enabled": (
                    self.datev_document_image_export_enabled
                ),
                "space_kind": self.space_kind,
                "review_assist_enabled": self.review_assist_enabled,
                "is_active": self.is_active,
            },
        )
        return document_space


@dataclass(frozen=True)
class UpdateDocumentSpace:
    document_space: DocumentSpace
    name: str
    parent: DocumentSpace | None = None
    slug: str | None = None
    description: str = ""
    datev_document_image_export_enabled: bool = False
    space_kind: str = DocumentSpace.SpaceKind.GENERAL
    review_assist_enabled: bool = False
    is_active: bool = True

    @transaction.atomic
    def execute(self) -> DocumentSpace:
        if self.parent and self.parent.tenant_id != self.document_space.tenant_id:
            raise ValueError("Parent document space belongs to a different tenant.")
        if self.parent and self.parent.path.startswith(
            f"{self.document_space.path.rstrip('/')}/"
        ):
            raise ValueError("Document space cannot be moved below itself.")

        old_path = self.document_space.path
        slug = self.slug or slugify(self.name)
        if not slug:
            raise ValueError("Document space slug cannot be empty.")

        new_path = _build_space_path(self.parent, slug)
        self.document_space.parent = self.parent
        self.document_space.name = self.name
        self.document_space.slug = slug
        self.document_space.path = new_path
        self.document_space.description = self.description
        self.document_space.datev_document_image_export_enabled = (
            self.datev_document_image_export_enabled
        )
        self.document_space.space_kind = self.space_kind
        self.document_space.review_assist_enabled = self.review_assist_enabled
        self.document_space.is_active = self.is_active
        self.document_space.save(
            update_fields=[
                "parent",
                "name",
                "slug",
                "path",
                "description",
                "datev_document_image_export_enabled",
                "space_kind",
                "review_assist_enabled",
                "is_active",
                "updated_at",
            ]
        )

        if old_path != new_path:
            descendants = DocumentSpace.objects.filter(
                tenant=self.document_space.tenant,
                path__startswith=f"{old_path.rstrip('/')}/",
            ).order_by("path")
            for descendant in descendants:
                descendant.path = descendant.path.replace(old_path, new_path, 1)
                descendant.save(update_fields=["path", "updated_at"])

        return self.document_space


@dataclass(frozen=True)
class CreateDocumentMetadataField:
    tenant: Tenant
    space: DocumentSpace
    name: str
    slug: str
    field_type: str
    help_text: str = ""
    choices: list[str] | None = None
    allow_custom_choices: bool = False
    einvoice_source: str = DocumentMetadataField.EInvoiceSource.NONE
    sort_order: int = 100
    is_required: bool = False
    is_active: bool = True
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> DocumentMetadataField:
        if self.space.tenant_id != self.tenant.id:
            raise ValueError("Metadata field space belongs to a different tenant.")

        metadata_field = DocumentMetadataField.objects.create(
            tenant=self.tenant,
            space=self.space,
            name=self.name,
            slug=self.slug,
            field_type=self.field_type,
            help_text=self.help_text,
            choices=self.choices or [],
            allow_custom_choices=self.allow_custom_choices,
            einvoice_source=self.einvoice_source,
            sort_order=self.sort_order,
            is_required=self.is_required,
            is_active=self.is_active,
        )
        RecordAuditEvent(
            tenant=self.tenant,
            actor=self.actor,
            event_type="document_metadata_field.created",
            object_type="documents.DocumentMetadataField",
            object_id=str(metadata_field.id),
            data={
                "space_id": self.space.id,
                "name": metadata_field.name,
                "slug": metadata_field.slug,
                "field_type": metadata_field.field_type,
                "allow_custom_choices": metadata_field.allow_custom_choices,
                "einvoice_source": metadata_field.einvoice_source,
            },
        ).execute()
        return metadata_field


@dataclass(frozen=True)
class UpdateDocumentMetadataField:
    metadata_field: DocumentMetadataField
    name: str
    slug: str
    field_type: str
    help_text: str = ""
    choices: list[str] | None = None
    allow_custom_choices: bool = False
    einvoice_source: str = DocumentMetadataField.EInvoiceSource.NONE
    sort_order: int = 100
    is_required: bool = False
    is_active: bool = True
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> DocumentMetadataField:
        self.metadata_field.name = self.name
        self.metadata_field.slug = self.slug
        self.metadata_field.field_type = self.field_type
        self.metadata_field.help_text = self.help_text
        self.metadata_field.choices = self.choices or []
        self.metadata_field.allow_custom_choices = self.allow_custom_choices
        self.metadata_field.einvoice_source = self.einvoice_source
        self.metadata_field.sort_order = self.sort_order
        self.metadata_field.is_required = self.is_required
        self.metadata_field.is_active = self.is_active
        self.metadata_field.save(
            update_fields=[
                "name",
                "slug",
                "field_type",
                "help_text",
                "choices",
                "allow_custom_choices",
                "einvoice_source",
                "sort_order",
                "is_required",
                "is_active",
                "updated_at",
            ]
        )
        RecordAuditEvent(
            tenant=self.metadata_field.tenant,
            actor=self.actor,
            event_type="document_metadata_field.updated",
            object_type="documents.DocumentMetadataField",
            object_id=str(self.metadata_field.id),
            data={
                "space_id": self.metadata_field.space_id,
                "name": self.metadata_field.name,
                "slug": self.metadata_field.slug,
                "field_type": self.metadata_field.field_type,
                "allow_custom_choices": self.metadata_field.allow_custom_choices,
                "einvoice_source": self.metadata_field.einvoice_source,
                "is_active": self.metadata_field.is_active,
            },
        ).execute()
        return self.metadata_field


@dataclass(frozen=True)
class AddDocumentMetadataChoice:
    metadata_field: DocumentMetadataField
    value: str
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> DocumentMetadataField:
        if self.metadata_field.field_type != DocumentMetadataField.FieldType.CHOICE:
            raise ValueError("Metadata field is not a choice field.")
        if not self.metadata_field.allow_custom_choices:
            raise ValueError("Metadata field does not allow custom choices.")

        value = self.value.strip()
        if not value:
            return self.metadata_field

        existing_values = {
            choice.casefold(): choice for choice in self.metadata_field.choices
        }
        if value.casefold() in existing_values:
            return self.metadata_field

        previous_choices = list(self.metadata_field.choices)
        self.metadata_field.choices = [*previous_choices, value]
        self.metadata_field.save(update_fields=["choices", "updated_at"])
        RecordAuditEvent(
            tenant=self.metadata_field.tenant,
            actor=self.actor,
            event_type="document_metadata_field.choice_added",
            object_type="documents.DocumentMetadataField",
            object_id=str(self.metadata_field.id),
            data={
                "space_id": self.metadata_field.space_id,
                "name": self.metadata_field.name,
                "slug": self.metadata_field.slug,
                "choice": value,
                "previous_choices": previous_choices,
            },
        ).execute()
        return self.metadata_field


@dataclass(frozen=True)
class EnsureDefaultDocumentSpaces:
    tenant: Tenant

    def execute(self) -> dict[str, DocumentSpace]:
        general = CreateDocumentSpace(
            tenant=self.tenant,
            name="Allgemein",
            slug="allgemein",
            space_kind=DocumentSpace.SpaceKind.GENERAL,
        ).execute()
        invoices = CreateDocumentSpace(
            tenant=self.tenant,
            name="Rechnungen",
            slug="rechnungen",
            space_kind=DocumentSpace.SpaceKind.INVOICES,
        ).execute()
        incoming_invoices = CreateDocumentSpace(
            tenant=self.tenant,
            parent=invoices,
            name="Eingangsrechnungen",
            slug="eingangsrechnungen",
            space_kind=DocumentSpace.SpaceKind.INVOICES,
        ).execute()
        outgoing_invoices = CreateDocumentSpace(
            tenant=self.tenant,
            parent=invoices,
            name="Ausgangsrechnungen",
            slug="ausgangsrechnungen",
            space_kind=DocumentSpace.SpaceKind.INVOICES,
        ).execute()
        personnel = CreateDocumentSpace(
            tenant=self.tenant,
            name="Personalakten",
            slug="personalakten",
            space_kind=DocumentSpace.SpaceKind.PERSONNEL,
        ).execute()
        contracts = CreateDocumentSpace(
            tenant=self.tenant,
            name="Verträge",
            slug="vertraege",
            space_kind=DocumentSpace.SpaceKind.CONTRACTS,
        ).execute()

        return {
            "general": general,
            "invoices": invoices,
            "incoming_invoices": incoming_invoices,
            "outgoing_invoices": outgoing_invoices,
            "personnel": personnel,
            "contracts": contracts,
        }


@dataclass(frozen=True)
class CreateDocumentFromUpload:
    tenant: Tenant
    title: str
    space: DocumentSpace
    file_obj: BinaryIO
    original_filename: str
    content_type: str
    created_by: get_user_model() | None = None
    auto_start_ocr: bool | None = None
    ocr_title_policy: dict | None = None
    auto_extract_einvoice: bool = True
    auto_start_workflows: bool = True
    document_date: date | None = None

    def execute(self) -> tuple[Document, DocumentFile]:
        if self.space.tenant_id != self.tenant.id:
            raise ValueError("Document space belongs to a different tenant.")

        prepared_file, prepared_filename, prepared_content_type = (
            _prepare_upload_file_for_storage(
                file_obj=self.file_obj,
                original_filename=self.original_filename,
                content_type=self.content_type,
            )
        )
        try:
            with SpooledTemporaryFile(max_size=10 * 1024 * 1024) as buffered_file:
                sha256, byte_size = _buffer_and_hash_file(prepared_file, buffered_file)
                existing_file = (
                    DocumentFile.objects.select_related("document", "document__space")
                    .filter(
                        tenant=self.tenant,
                        file_kind=DocumentFile.Kind.ORIGINAL,
                        sha256=sha256,
                        byte_size=byte_size,
                    )
                    .order_by("created_at", "id")
                    .first()
                )
                if existing_file is not None:
                    RecordAuditEvent(
                        tenant=self.tenant,
                        actor=self.created_by,
                        event_type="document_duplicate.detected",
                        object_type="documents.DocumentFile",
                        object_id=str(existing_file.id),
                        data={
                            "existing_document_id": existing_file.document_id,
                            "existing_document_title": existing_file.document.title,
                            "existing_space_path": existing_file.document.space.path,
                            "sha256": sha256,
                            "byte_size": byte_size,
                            "original_filename": prepared_filename,
                        },
                    ).execute()
                    raise DuplicateDocumentError(existing_file)

                buffered_file.seek(0)
                return self._create_document(
                    buffered_file,
                    original_filename=prepared_filename,
                    content_type=prepared_content_type,
                )
        finally:
            if prepared_file is not self.file_obj and hasattr(prepared_file, "close"):
                prepared_file.close()

    @transaction.atomic
    def _create_document(
        self,
        file_obj: BinaryIO,
        *,
        original_filename: str,
        content_type: str,
    ) -> tuple[Document, DocumentFile]:
        title = self.title.strip()
        title_source = Document.TitleSource.MANUAL
        if not title:
            title = _fallback_title_from_filename(original_filename)
            title_source = Document.TitleSource.FILENAME

        document = Document.objects.create(
            tenant=self.tenant,
            space=self.space,
            title=title,
            title_source=title_source,
            document_date=self.document_date,
            created_by=self.created_by,
        )
        RecordAuditEvent(
            tenant=self.tenant,
            actor=self.created_by,
            event_type="document.created",
            object_type="documents.Document",
            object_id=str(document.id),
            data={
                "title": document.title,
                "title_source": document.title_source,
                "space_id": self.space.id,
                "space_path": self.space.path,
            },
        ).execute()

        document_file = StoreImmutableFile(
            tenant=self.tenant,
            document=document,
            file_obj=file_obj,
            original_filename=original_filename,
            content_type=content_type,
            created_by=self.created_by,
        ).execute()

        create_thumbnail_for_document_file(
            document_file,
            actor=self.created_by,
        )
        create_preview_for_document_file(
            document_file,
            actor=self.created_by,
        )

        if self.auto_extract_einvoice:
            self._attach_einvoice_data(document=document, document_file=document_file)

        should_auto_start_ocr = (
            getattr(settings, "OCR_AUTO_START_ON_UPLOAD", True)
            if self.auto_start_ocr is None
            else self.auto_start_ocr
        )
        if should_auto_start_ocr:
            from doksio.ocr.services import (
                StartOcrForDocumentFile,
                supports_ocr_content_type,
            )

            if supports_ocr_content_type(document_file.content_type):
                transaction.on_commit(
                    lambda: StartOcrForDocumentFile(
                        document_file=document_file,
                        actor=self.created_by,
                        title_policy=self.ocr_title_policy,
                    ).execute()
                )

        if self.auto_start_workflows:
            from doksio.workflows.services import StartMatchingWorkflowsForDocument

            transaction.on_commit(
                lambda: StartMatchingWorkflowsForDocument(
                    document=document,
                    actor=self.created_by,
                ).execute()
            )

        _schedule_search_index_rebuild(document)
        return document, document_file

    def _attach_einvoice_data(
        self,
        document: Document,
        document_file: DocumentFile,
    ) -> None:
        if document_file.content_type != "application/pdf":
            return

        from doksio.einvoices.zugferd import extract_einvoice_from_pdf

        try:
            with default_storage.open(document_file.storage_key, "rb") as stored_file:
                extracted_invoice = extract_einvoice_from_pdf(stored_file)
        except Exception:
            return

        if extracted_invoice is None:
            return

        metadata_from_einvoice = _metadata_from_einvoice(
            space=document.space,
            einvoice_data=extracted_invoice.data,
        )
        document.einvoice_data = extracted_invoice.data
        title_from_einvoice = _title_from_einvoice(extracted_invoice.data)
        update_fields = ["einvoice_data", "metadata", "updated_at"]
        if title_from_einvoice and document.title_source != Document.TitleSource.MANUAL:
            document.title = title_from_einvoice
            document.title_source = Document.TitleSource.OCR
            update_fields.extend(["title", "title_source"])
        if metadata_from_einvoice:
            document.metadata = {
                **document.metadata,
                **metadata_from_einvoice,
            }
        document.save(update_fields=update_fields)
        RecordAuditEvent(
            tenant=self.tenant,
            actor=self.created_by,
            event_type="document_einvoice.detected",
            object_type="documents.Document",
            object_id=str(document.id),
            data={
                "document_id": document.id,
                "source_filename": extracted_invoice.source_filename,
                "syntax": extracted_invoice.data.get("syntax", ""),
                "profile": extracted_invoice.data.get("profile", ""),
                "invoice_number": extracted_invoice.data.get("invoice_number", ""),
                "title": title_from_einvoice,
                "metadata": metadata_from_einvoice,
            },
        ).execute()


@dataclass(frozen=True)
class UpdateDocumentCoreMetadata:
    document: Document
    title: str
    document_date: date | None
    space: DocumentSpace
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> Document:
        title = self.title.strip()
        if not title:
            raise ValueError("Document title cannot be empty.")
        if self.space.tenant_id != self.document.tenant_id:
            raise ValueError("Document space belongs to a different tenant.")

        previous_title = self.document.title
        previous_title_source = self.document.title_source
        previous_document_date = self.document.document_date
        previous_space = self.document.space
        space_changed = self.space.id != previous_space.id
        self.document.title = title
        if title != previous_title:
            self.document.title_source = Document.TitleSource.MANUAL
        self.document.document_date = self.document_date
        self.document.space = self.space
        self.document.save(
            update_fields=[
                "title",
                "title_source",
                "document_date",
                "space",
                "updated_at",
            ]
        )
        if space_changed:
            from doksio.workflows.services import (
                CancelRunningWorkflowsForDocument,
                StartMatchingWorkflowsForDocument,
            )

            CancelRunningWorkflowsForDocument(
                document=self.document,
                actor=self.actor,
                reason="document_moved",
            ).execute()
            transaction.on_commit(
                lambda: StartMatchingWorkflowsForDocument(
                    document=self.document,
                    actor=self.actor,
                ).execute()
            )

        RecordAuditEvent(
            tenant=self.document.tenant,
            actor=self.actor,
            event_type="document_core_metadata.updated",
            object_type="documents.Document",
            object_id=str(self.document.id),
            data={
                "document_id": self.document.id,
                "title": self.document.title,
                "previous_title": previous_title,
                "title_source": self.document.title_source,
                "previous_title_source": previous_title_source,
                "space_id": self.document.space_id,
                "space_path": self.document.space.path,
                "previous_space_id": previous_space.id,
                "previous_space_path": previous_space.path,
                "space_changed": space_changed,
                "document_date": (
                    self.document.document_date.isoformat()
                    if self.document.document_date
                    else None
                ),
                "previous_document_date": (
                    previous_document_date.isoformat()
                    if previous_document_date
                    else None
                ),
            },
        ).execute()
        _schedule_search_index_rebuild(self.document)
        return self.document


@dataclass(frozen=True)
class DeleteDocument:
    document: Document
    reason: str
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> Document:
        reason = self.reason.strip()
        if not reason:
            raise ValueError("Delete reason cannot be empty.")
        if self.document.status == Document.Status.DELETED:
            return self.document

        from doksio.workflows.services import CancelRunningWorkflowsForDocument

        previous_status = self.document.status
        self.document.status = Document.Status.DELETED
        self.document.deleted_reason = reason
        self.document.deleted_at = timezone.now()
        self.document.deleted_by = self.actor
        self.document.save(
            update_fields=[
                "status",
                "deleted_reason",
                "deleted_at",
                "deleted_by",
                "updated_at",
            ]
        )
        cancelled_instances = CancelRunningWorkflowsForDocument(
            document=self.document,
            actor=self.actor,
            reason="document_deleted",
        ).execute()
        RecordAuditEvent(
            tenant=self.document.tenant,
            actor=self.actor,
            event_type="document.deleted",
            object_type="documents.Document",
            object_id=str(self.document.id),
            data={
                "document_id": self.document.id,
                "title": self.document.title,
                "previous_status": previous_status,
                "status": self.document.status,
                "space_id": self.document.space_id,
                "space_path": self.document.space.path,
                "reason": reason,
                "cancelled_workflow_instance_ids": [
                    instance.id for instance in cancelled_instances
                ],
            },
        ).execute()
        return self.document


@dataclass(frozen=True)
class DeleteDocumentSpace:
    class Strategy:
        MOVE = "move"
        DELETE_DOCUMENTS = "delete_documents"

    document_space: DocumentSpace
    strategy: str
    target_space: DocumentSpace | None = None
    delete_reason: str = ""
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> list[DocumentSpace]:
        subtree_filter = Q(path=self.document_space.path) | Q(
            path__startswith=f"{self.document_space.path.rstrip('/')}/"
        )
        subtree_spaces = list(
            DocumentSpace.objects.select_for_update()
            .filter(
                tenant=self.document_space.tenant,
                deleted_at__isnull=True,
            )
            .filter(subtree_filter)
            .order_by("-path")
        )
        subtree_ids = [space.id for space in subtree_spaces]
        if not subtree_ids:
            return []

        documents = list(
            Document.objects.select_for_update()
            .filter(tenant=self.document_space.tenant, space_id__in=subtree_ids)
            .select_related("space")
            .order_by("id")
        )

        if self.strategy == self.Strategy.MOVE:
            if self.target_space is None:
                raise ValueError("Target document space is required.")
            if self.target_space.tenant_id != self.document_space.tenant_id:
                raise ValueError("Target document space belongs to a different tenant.")
            if self.target_space.id in subtree_ids:
                raise ValueError("Target document space cannot be deleted.")
            self._move_documents(documents)
        elif self.strategy == self.Strategy.DELETE_DOCUMENTS:
            if not self.delete_reason.strip():
                raise ValueError("Delete reason is required.")
            self._delete_documents(documents)
        else:
            raise ValueError("Unknown document space delete strategy.")

        deleted_at = timezone.now()
        for space in subtree_spaces:
            space.is_active = False
            space.deleted_at = deleted_at
            space.deleted_by = self.actor
            space.deleted_strategy = self.strategy
            space.save(
                update_fields=[
                    "is_active",
                    "deleted_at",
                    "deleted_by",
                    "deleted_strategy",
                    "updated_at",
                ]
            )

        RecordAuditEvent(
            tenant=self.document_space.tenant,
            actor=self.actor,
            event_type="document_space.deleted",
            object_type="documents.DocumentSpace",
            object_id=str(self.document_space.id),
            data={
                "document_space_id": self.document_space.id,
                "document_space_path": self.document_space.path,
                "deleted_space_ids": subtree_ids,
                "strategy": self.strategy,
                "target_space_id": self.target_space.id if self.target_space else None,
                "target_space_path": (
                    self.target_space.path if self.target_space else ""
                ),
                "document_count": len(documents),
            },
        ).execute()
        return subtree_spaces

    def _move_documents(self, documents: list[Document]) -> None:
        assert self.target_space is not None
        for document in documents:
            if document.status == Document.Status.DELETED:
                previous_space = document.space
                document.space = self.target_space
                document.save(update_fields=["space", "updated_at"])
                RecordAuditEvent(
                    tenant=document.tenant,
                    actor=self.actor,
                    event_type="document_core_metadata.updated",
                    object_type="documents.Document",
                    object_id=str(document.id),
                    data={
                        "document_id": document.id,
                        "title": document.title,
                        "previous_title": document.title,
                        "title_source": document.title_source,
                        "previous_title_source": document.title_source,
                        "space_id": document.space_id,
                        "space_path": document.space.path,
                        "previous_space_id": previous_space.id,
                        "previous_space_path": previous_space.path,
                        "space_changed": True,
                        "document_date": (
                            document.document_date.isoformat()
                            if document.document_date
                            else None
                        ),
                        "previous_document_date": (
                            document.document_date.isoformat()
                            if document.document_date
                            else None
                        ),
                    },
                ).execute()
                _schedule_search_index_rebuild(document)
                continue

            UpdateDocumentCoreMetadata(
                document=document,
                title=document.title,
                document_date=document.document_date,
                space=self.target_space,
                actor=self.actor,
            ).execute()

    def _delete_documents(self, documents: list[Document]) -> None:
        for document in documents:
            DeleteDocument(
                document=document,
                reason=self.delete_reason,
                actor=self.actor,
            ).execute()


@dataclass(frozen=True)
class EmptyDocumentSpace:
    document_space: DocumentSpace
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> int:
        DocumentSpace.objects.select_for_update().get(
            id=self.document_space.id,
            tenant=self.document_space.tenant,
            deleted_at__isnull=True,
        )
        documents = Document.objects.select_for_update().filter(
            tenant=self.document_space.tenant,
            space=self.document_space,
        )
        document_ids = list(documents.values_list("id", flat=True))
        if not document_ids:
            RecordAuditEvent(
                tenant=self.document_space.tenant,
                actor=self.actor,
                event_type="document_space.emptied",
                object_type="documents.DocumentSpace",
                object_id=str(self.document_space.id),
                data={
                    "document_space_id": self.document_space.id,
                    "document_space_path": self.document_space.path,
                    "document_count": 0,
                    "hard_delete": True,
                },
            ).execute()
            return 0

        files = DocumentFile.objects.filter(document_id__in=document_ids)
        file_ids = list(files.values_list("id", flat=True))
        storage_keys = list(files.values_list("storage_key", flat=True))

        from doksio.exports.models import ExportRunItem

        ExportRunItem.objects.filter(
            Q(document_id__in=document_ids) | Q(document_file_id__in=file_ids),
        ).delete()

        DocumentFile.objects.filter(
            id__in=file_ids,
            derivative_of__isnull=False,
        ).delete()
        DocumentFile.objects.filter(id__in=file_ids).delete()
        deleted_count, _deleted_by_model = documents.delete()

        RecordAuditEvent(
            tenant=self.document_space.tenant,
            actor=self.actor,
            event_type="document_space.emptied",
            object_type="documents.DocumentSpace",
            object_id=str(self.document_space.id),
            data={
                "document_space_id": self.document_space.id,
                "document_space_path": self.document_space.path,
                "document_ids": document_ids,
                "document_count": len(document_ids),
                "storage_key_count": len(storage_keys),
                "hard_delete": True,
                "deleted_model_count": deleted_count,
            },
        ).execute()
        transaction.on_commit(lambda: _delete_storage_keys(storage_keys))
        return len(document_ids)


def _delete_storage_keys(storage_keys: list[str]) -> None:
    for storage_key in storage_keys:
        if storage_key:
            default_storage.delete(storage_key)


@dataclass(frozen=True)
class AddDocumentComment:
    document: Document
    body: str
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> DocumentComment:
        body = self.body.strip()
        if not body:
            raise ValueError("Document comment body cannot be empty.")

        comment = DocumentComment.objects.create(
            tenant=self.document.tenant,
            document=self.document,
            body=body,
            created_by=self.actor,
        )
        mentioned_users = [
            user
            for user in mentioned_users_from_text(body, self.document.tenant)
            if user != self.actor
        ]
        if mentioned_users:
            comment.mentioned_users.set(mentioned_users)
            self._notify_mentioned_users(comment, mentioned_users)

        RecordAuditEvent(
            tenant=self.document.tenant,
            actor=self.actor,
            event_type="document_comment.created",
            object_type="documents.DocumentComment",
            object_id=str(comment.id),
            data={
                "document_id": self.document.id,
                "body_length": len(body),
                "mentioned_user_ids": [user.id for user in mentioned_users],
            },
        ).execute()
        _schedule_search_index_rebuild(self.document)
        return comment

    def _notify_mentioned_users(
        self,
        comment: DocumentComment,
        mentioned_users: list,
    ) -> None:
        from doksio.accounts.models import Notification
        from doksio.accounts.services import CreateNotification

        actor_name = display_name_for_user(self.actor) if self.actor else "Jemand"
        link_url = reverse(
            "documents:detail",
            kwargs={
                "tenant_slug": self.document.tenant.slug,
                "document_id": self.document.id,
            },
        )
        for user in mentioned_users:
            CreateNotification(
                tenant=self.document.tenant,
                recipient=user,
                notification_type=Notification.Type.DOCUMENT_COMMENT_MENTION,
                title="Du wurdest erwähnt",
                body=f"{actor_name} hat dich in einem Kommentar erwähnt.",
                link_url=link_url,
                document=self.document,
                document_comment=comment,
            ).execute()


def _normalize_tag_name(name: str) -> tuple[str, str]:
    normalized_name = " ".join(name.strip().split())
    slug = slugify(normalized_name)
    if not normalized_name or not slug:
        raise ValueError("Document tag name cannot be empty.")
    return normalized_name, slug


@dataclass(frozen=True)
class SetDocumentTags:
    document: Document
    tag_names: list[str]
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> list[DocumentTagAssignment]:
        tags: list[DocumentTag] = []
        seen_slugs: set[str] = set()
        for raw_name in self.tag_names:
            name, slug = _normalize_tag_name(raw_name)
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            tag, _created = DocumentTag.objects.get_or_create(
                tenant=self.document.tenant,
                slug=slug,
                defaults={"name": name},
            )
            tags.append(tag)

        current_assignments = DocumentTagAssignment.objects.filter(
            document=self.document,
        ).select_related("tag")
        current_by_slug = {
            assignment.tag.slug: assignment for assignment in current_assignments
        }

        desired_slugs = {tag.slug for tag in tags}
        for slug, assignment in current_by_slug.items():
            if slug not in desired_slugs:
                assignment.delete()

        assignments: list[DocumentTagAssignment] = []
        for tag in tags:
            assignment, _created = DocumentTagAssignment.objects.get_or_create(
                tenant=self.document.tenant,
                document=self.document,
                tag=tag,
                defaults={"created_by": self.actor},
            )
            assignments.append(assignment)

        RecordAuditEvent(
            tenant=self.document.tenant,
            actor=self.actor,
            event_type="document_tags.updated",
            object_type="documents.Document",
            object_id=str(self.document.id),
            data={
                "document_id": self.document.id,
                "tags": [tag.name for tag in tags],
            },
        ).execute()
        _schedule_search_index_rebuild(self.document)
        return assignments


@dataclass(frozen=True)
class UpdateDocumentMetadata:
    document: Document
    metadata: dict
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> Document:
        previous_metadata = self.document.metadata.copy()
        self.document.metadata = self.metadata
        self.document.save(update_fields=["metadata", "updated_at"])
        RecordAuditEvent(
            tenant=self.document.tenant,
            actor=self.actor,
            event_type="document_metadata.updated",
            object_type="documents.Document",
            object_id=str(self.document.id),
            data={
                "document_id": self.document.id,
                "metadata": self.document.metadata,
                "previous_metadata": previous_metadata,
            },
        ).execute()
        _schedule_search_index_rebuild(self.document)
        return self.document
