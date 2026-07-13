"""Application services for document ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from typing import BinaryIO

from django.contrib.auth import get_user_model
from django.utils import timezone

from doksio.audit.services import RecordAuditEvent
from doksio.documents.models import Document, DocumentSpace
from doksio.documents.services import CreateDocumentFromUpload, SetDocumentTags
from doksio.ingestion.models import ImportJob, ImportSource
from doksio.tenancy.models import Tenant


def ocr_title_policy_from_source(source: ImportSource | None) -> dict:
    if source is None:
        return {"strategy": ImportSource.OcrTitleStrategy.AUTOMATIC}
    return (source.settings or {}).get(
        "title",
        {"strategy": ImportSource.OcrTitleStrategy.AUTOMATIC},
    )


@dataclass(frozen=True)
class ResolveImportDocumentSpace:
    tenant: Tenant
    source: ImportSource
    original_filename: str

    def execute(self) -> DocumentSpace:
        if self.source.tenant_id != self.tenant.id:
            raise ValueError("Import source belongs to a different tenant.")

        if self.source.target_strategy == ImportSource.TargetStrategy.RULES:
            resolved_space = self._space_from_rules()
            if resolved_space is not None:
                return resolved_space

        return self.source.document_space

    def _space_from_rules(self) -> DocumentSpace | None:
        rules = (self.source.settings or {}).get("routing_rules", [])
        filename = self.original_filename.rsplit("/", 1)[-1]
        for rule in rules:
            pattern = rule.get("pattern", "")
            if pattern and fnmatch(filename, pattern):
                document_space_id = rule.get("document_space_id")
                if not document_space_id:
                    continue
                return DocumentSpace.objects.get(
                    id=document_space_id,
                    tenant=self.tenant,
                    is_active=True,
                )
        return None


@dataclass(frozen=True)
class ResolveManualUploadDocumentSpace:
    tenant: Tenant
    original_filename: str

    def execute(self) -> tuple[DocumentSpace, ImportSource]:
        sources = ImportSource.objects.filter(
            tenant=self.tenant,
            source_type=ImportSource.SourceType.UPLOAD,
            is_active=True,
        )
        strategy_order = {
            ImportSource.TargetStrategy.RULES: 0,
            ImportSource.TargetStrategy.INTELLIGENT: 1,
            ImportSource.TargetStrategy.FIXED: 2,
        }
        for source in sorted(
            sources,
            key=lambda source: (
                strategy_order.get(source.target_strategy, 99),
                source.name.lower(),
                source.id,
            ),
        ):
            document_space = ResolveImportDocumentSpace(
                tenant=self.tenant,
                source=source,
                original_filename=self.original_filename,
            ).execute()
            return document_space, source
        raise ValueError("Keine aktive Upload-Importstrategie gefunden.")


@dataclass(frozen=True)
class ImportDocument:
    tenant: Tenant
    document_space: DocumentSpace
    file_obj: BinaryIO
    original_filename: str
    content_type: str
    source: ImportSource | None = None
    title: str = ""
    actor: get_user_model() | None = None
    metadata: dict | None = None

    def execute(self) -> tuple[Document, ImportJob]:
        if self.document_space.tenant_id != self.tenant.id:
            raise ValueError("Import document space belongs to a different tenant.")
        if self.source and self.source.tenant_id != self.tenant.id:
            raise ValueError("Import source belongs to a different tenant.")
        if (
            self.source
            and self.source.target_strategy == ImportSource.TargetStrategy.FIXED
            and self.source.document_space_id != self.document_space.id
        ):
            raise ValueError("Import source belongs to a different document space.")

        import_job = ImportJob.objects.create(
            tenant=self.tenant,
            source=self.source,
            document_space=self.document_space,
            original_filename=self.original_filename,
            content_type=self.content_type,
            status=ImportJob.Status.PROCESSING,
            metadata=self.metadata or {},
        )
        RecordAuditEvent(
            tenant=self.tenant,
            actor=self.actor,
            event_type="import_job.received",
            object_type="ingestion.ImportJob",
            object_id=str(import_job.id),
            data={
                "source_id": self.source.id if self.source else None,
                "document_space_id": self.document_space.id,
                "original_filename": self.original_filename,
                "content_type": self.content_type,
            },
        ).execute()

        try:
            document, _document_file = CreateDocumentFromUpload(
                tenant=self.tenant,
                title=self.title,
                space=self.document_space,
                file_obj=self.file_obj,
                original_filename=self.original_filename,
                content_type=self.content_type,
                created_by=self.actor,
                auto_start_ocr=(
                    self.source.auto_start_ocr if self.source is not None else None
                ),
                ocr_title_policy=ocr_title_policy_from_source(self.source),
                auto_extract_einvoice=True,
                auto_start_workflows=(
                    self.source.start_workflows if self.source is not None else True
                ),
            ).execute()
            if self.source and self.source.default_tags:
                SetDocumentTags(
                    document=document,
                    tag_names=self.source.default_tags,
                    actor=self.actor,
                ).execute()
            import_job.document = document
            import_job.status = ImportJob.Status.IMPORTED
            import_job.message = "Dokument wurde importiert."
            import_job.processed_at = timezone.now()
            import_job.save(
                update_fields=[
                    "document",
                    "status",
                    "message",
                    "processed_at",
                    "updated_at",
                ]
            )
            RecordAuditEvent(
                tenant=self.tenant,
                actor=self.actor,
                event_type="import_job.imported",
                object_type="ingestion.ImportJob",
                object_id=str(import_job.id),
                data={
                    "document_id": document.id,
                    "source_id": self.source.id if self.source else None,
                },
            ).execute()
            return document, import_job
        except Exception as exc:
            import_job.status = ImportJob.Status.FAILED
            import_job.message = str(exc)
            import_job.processed_at = timezone.now()
            import_job.save(
                update_fields=["status", "message", "processed_at", "updated_at"]
            )
            RecordAuditEvent(
                tenant=self.tenant,
                actor=self.actor,
                event_type="import_job.failed",
                object_type="ingestion.ImportJob",
                object_id=str(import_job.id),
                data={
                    "source_id": self.source.id if self.source else None,
                    "error": str(exc),
                },
            ).execute()
            raise
