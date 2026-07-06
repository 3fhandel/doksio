from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import BinaryIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.text import slugify

from domasy.audit.services import RecordAuditEvent
from domasy.documents.models import (
    Document,
    DocumentComment,
    DocumentFile,
    DocumentMetadataField,
    DocumentSpace,
    DocumentTag,
    DocumentTagAssignment,
)
from domasy.storage.services import StoreImmutableFile
from domasy.tenancy.models import Tenant


def _fallback_title_from_filename(original_filename: str) -> str:
    title = original_filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
    return title or original_filename or "Unbenanntes Dokument"


def _build_space_path(parent: DocumentSpace | None, slug: str) -> str:
    if parent is None:
        return f"/{slug}"
    return f"{parent.path.rstrip('/')}/{slug}"


@dataclass(frozen=True)
class CreateDocumentSpace:
    tenant: Tenant
    name: str
    parent: DocumentSpace | None = None
    slug: str | None = None
    description: str = ""
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
                "is_active": self.metadata_field.is_active,
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
    document_date: date | None = None

    @transaction.atomic
    def execute(self) -> tuple[Document, DocumentFile]:
        if self.space.tenant_id != self.tenant.id:
            raise ValueError("Document space belongs to a different tenant.")

        title = self.title.strip()
        title_source = Document.TitleSource.MANUAL
        if not title:
            title = _fallback_title_from_filename(self.original_filename)
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
            file_obj=self.file_obj,
            original_filename=self.original_filename,
            content_type=self.content_type,
            created_by=self.created_by,
        ).execute()

        should_auto_start_ocr = (
            getattr(settings, "OCR_AUTO_START_ON_UPLOAD", True)
            if self.auto_start_ocr is None
            else self.auto_start_ocr
        )
        if should_auto_start_ocr:
            from domasy.ocr.services import (
                StartOcrForDocumentFile,
                supports_ocr_content_type,
            )

            if supports_ocr_content_type(document_file.content_type):
                transaction.on_commit(
                    lambda: StartOcrForDocumentFile(
                        document_file=document_file,
                        actor=self.created_by,
                    ).execute()
                )

        from domasy.workflows.services import StartMatchingWorkflowsForDocument

        transaction.on_commit(
            lambda: StartMatchingWorkflowsForDocument(
                document=document,
                actor=self.created_by,
            ).execute()
        )

        return document, document_file


@dataclass(frozen=True)
class UpdateDocumentCoreMetadata:
    document: Document
    title: str
    document_date: date | None
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> Document:
        title = self.title.strip()
        if not title:
            raise ValueError("Document title cannot be empty.")

        previous_title = self.document.title
        previous_title_source = self.document.title_source
        previous_document_date = self.document.document_date
        self.document.title = title
        if title != previous_title:
            self.document.title_source = Document.TitleSource.MANUAL
        self.document.document_date = self.document_date
        self.document.save(
            update_fields=[
                "title",
                "title_source",
                "document_date",
                "updated_at",
            ]
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
        return self.document


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
        RecordAuditEvent(
            tenant=self.document.tenant,
            actor=self.actor,
            event_type="document_comment.created",
            object_type="documents.DocumentComment",
            object_id=str(comment.id),
            data={
                "document_id": self.document.id,
                "body_length": len(body),
            },
        ).execute()
        return comment


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
        return self.document
