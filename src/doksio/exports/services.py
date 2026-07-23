"""Application services for exports."""

from __future__ import annotations

import csv
import hashlib
import re
import tempfile
import uuid
from dataclasses import dataclass
from io import StringIO
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.files.storage import default_storage
from django.db.models import QuerySet
from django.utils import timezone

from doksio.audit.services import RecordAuditEvent
from doksio.documents.models import Document, DocumentFile
from doksio.exports.models import ExportRun, ExportRunItem
from doksio.tenancy.models import Tenant


SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._ -]+")


@dataclass(frozen=True)
class DocumentImageExportPackage:
    filename: str
    export_run: ExportRun


def _safe_filename(value: str, fallback: str) -> str:
    cleaned = SAFE_FILENAME_PATTERN.sub("_", value.strip()).strip(" .")
    return cleaned or fallback


def _unique_zip_path(path: str, used_paths: set[str]) -> str:
    if path not in used_paths:
        used_paths.add(path)
        return path

    directory, _, filename = path.rpartition("/")
    stem, dot, extension = filename.rpartition(".")
    if not dot:
        stem = filename
        extension = ""
    for counter in range(2, 10_000):
        candidate_name = f"{stem}-{counter}.{extension}" if extension else f"{stem}-{counter}"
        candidate = f"{directory}/{candidate_name}" if directory else candidate_name
        if candidate not in used_paths:
            used_paths.add(candidate)
            return candidate
    raise RuntimeError("Could not create unique export filename.")


def _document_filename(document: Document, document_file: DocumentFile) -> str:
    date_prefix = (
        document.document_date.isoformat()
        if document.document_date
        else document.created_at.date().isoformat()
    )
    title = _safe_filename(document.title, f"dokument-{document.id}")
    original = _safe_filename(
        document_file.original_filename,
        f"dokument-{document.id}.bin",
    )
    extension = ""
    if "." in original:
        extension = "." + original.rsplit(".", 1)[1]
    return f"{date_prefix}_{document.id}_{title}{extension}"


def _csv_bytes(rows: list[dict[str, object]], fieldnames: list[str]) -> bytes:
    buffer = StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        delimiter=";",
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _export_storage_key(tenant: Tenant, export_run: ExportRun, filename: str) -> str:
    safe_filename = _safe_filename(filename, f"export-{export_run.id}.zip")
    return (
        f"tenants/{tenant.id}/exports/{export_run.id}/"
        f"{uuid.uuid4()}/{safe_filename}"
    )


@dataclass(frozen=True)
class CreateDocumentImageExportRun:
    tenant: Tenant
    documents: QuerySet[Document]
    created_by: get_user_model()
    filters: dict

    def execute(self) -> ExportRun:
        document_ids = list(self.documents.values_list("id", flat=True))
        return ExportRun.objects.create(
            tenant=self.tenant,
            export_type=ExportRun.ExportType.DATEV_DOCUMENT_IMAGES,
            status=ExportRun.Status.PROCESSING,
            filters={**self.filters, "document_ids": document_ids},
            total_count=len(document_ids),
            created_by=self.created_by,
        )


@dataclass(frozen=True)
class BuildDocumentImageExport:
    export_run: ExportRun

    def execute(self) -> DocumentImageExportPackage:
        export_run = self.export_run
        tenant = export_run.tenant
        document_ids = export_run.filters.get("document_ids") or []
        timestamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        filename = f"doksio-datev-belegbilder-{tenant.slug}-{timestamp}.zip"

        manifest_rows: list[dict[str, object]] = []
        log_rows: list[dict[str, object]] = []
        used_paths: set[str] = set()
        exported_count = 0
        skipped_count = 0

        documents = Document.objects.filter(
            tenant=tenant,
            id__in=document_ids,
        ).select_related("space").prefetch_related("files")
        with tempfile.TemporaryFile() as zip_file:
            with ZipFile(zip_file, "w", compression=ZIP_DEFLATED) as archive:
                for document in documents:
                    if self._already_exported(document):
                        skipped_count += 1
                        self._create_item(
                            export_run=export_run,
                            document=document,
                            document_file=None,
                            status=ExportRunItem.Status.SKIPPED,
                            message="Dokument wurde bereits erfolgreich exportiert.",
                        )
                        log_rows.append(
                            self._log_row(
                                document,
                                "skipped",
                                "Dokument wurde bereits erfolgreich exportiert.",
                            )
                        )
                        self._mark_processed(export_run)
                        continue

                    document_file = self._original_file_for_document(document)
                    if document_file is None:
                        skipped_count += 1
                        self._create_item(
                            export_run=export_run,
                            document=document,
                            document_file=None,
                            status=ExportRunItem.Status.SKIPPED,
                            message="Kein Originaldatei-Artefakt vorhanden.",
                        )
                        log_rows.append(self._log_row(document, "skipped", "Kein Original vorhanden."))
                        self._mark_processed(export_run)
                        continue

                    exported_filename = _unique_zip_path(
                        f"belege/{_document_filename(document, document_file)}",
                        used_paths,
                    )
                    try:
                        with default_storage.open(document_file.storage_key, "rb") as stored_file:
                            with archive.open(exported_filename, "w") as target_file:
                                for chunk in iter(
                                    lambda: stored_file.read(1024 * 1024),
                                    b"",
                                ):
                                    target_file.write(chunk)
                    except FileNotFoundError:
                        skipped_count += 1
                        self._create_item(
                            export_run=export_run,
                            document=document,
                            document_file=document_file,
                            status=ExportRunItem.Status.FAILED,
                            message="Datei wurde im Storage nicht gefunden.",
                            exported_filename=exported_filename,
                        )
                        log_rows.append(self._log_row(document, "failed", "Datei im Storage nicht gefunden."))
                        self._mark_processed(export_run)
                        continue

                    exported_count += 1
                    self._create_item(
                        export_run=export_run,
                        document=document,
                        document_file=document_file,
                        status=ExportRunItem.Status.EXPORTED,
                        message="Exportiert.",
                        exported_filename=exported_filename,
                    )
                    manifest_rows.append(self._manifest_row(document, document_file, exported_filename))
                    log_rows.append(self._log_row(document, "exported", "Exportiert."))
                    RecordAuditEvent(
                        tenant=tenant,
                        actor=export_run.created_by,
                        event_type="document.exported",
                        object_type="documents.Document",
                        object_id=str(document.id),
                        data={
                            "document_id": document.id,
                            "export_run_id": export_run.id,
                            "export_type": export_run.export_type,
                            "exported_filename": exported_filename,
                        },
                    ).execute()
                    self._mark_processed(export_run)

                archive.writestr(
                    "manifest.csv",
                    _csv_bytes(
                        manifest_rows,
                        [
                            "doksio_document_id",
                            "doksio_file_id",
                            "exported_filename",
                            "original_filename",
                            "document_title",
                            "document_box",
                            "document_date",
                            "created_at",
                            "content_type",
                            "sha256",
                        ],
                    ),
                )
                archive.writestr(
                    "export-log.csv",
                    _csv_bytes(
                        log_rows,
                        [
                            "doksio_document_id",
                            "document_title",
                            "status",
                            "message",
                        ],
                    ),
                )

            zip_file.flush()
            zip_file.seek(0, 2)
            zip_size = zip_file.tell()
            zip_file.seek(0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: zip_file.read(1024 * 1024), b""):
                digest.update(chunk)
            zip_file.seek(0)
            storage_key = default_storage.save(
                _export_storage_key(tenant, export_run, filename),
                File(zip_file, name=filename),
            )
            export_run.status = (
                ExportRun.Status.COMPLETED
                if skipped_count == 0
                else ExportRun.Status.COMPLETED_WITH_WARNINGS
            )
            export_run.filename = filename
            export_run.storage_key = storage_key
            export_run.byte_size = zip_size
            export_run.sha256 = digest.hexdigest()
            export_run.item_count = exported_count + skipped_count
            export_run.exported_count = exported_count
            export_run.warning_count = skipped_count
            export_run.processed_count = exported_count + skipped_count
            export_run.completed_at = timezone.now()
            export_run.save(
                update_fields=[
                    "status",
                    "filename",
                    "storage_key",
                    "byte_size",
                    "sha256",
                    "item_count",
                    "exported_count",
                    "warning_count",
                    "processed_count",
                    "completed_at",
                    "updated_at",
                ]
            )
            RecordAuditEvent(
                tenant=tenant,
                actor=export_run.created_by,
                event_type="export_run.created",
                object_type="exports.ExportRun",
                object_id=str(export_run.id),
                data={
                    "export_type": export_run.export_type,
                    "filename": filename,
                    "exported_count": exported_count,
                    "warning_count": skipped_count,
                },
            ).execute()

        return DocumentImageExportPackage(
            filename=filename,
            export_run=export_run,
        )

    def _already_exported(self, document: Document) -> bool:
        return ExportRunItem.objects.filter(
            tenant=self.export_run.tenant,
            document=document,
            status=ExportRunItem.Status.EXPORTED,
            export_run__export_type=ExportRun.ExportType.DATEV_DOCUMENT_IMAGES,
        ).exists()

    def _original_file_for_document(self, document: Document) -> DocumentFile | None:
        original_files = [
            file
            for file in document.files.all()
            if file.file_kind == DocumentFile.Kind.ORIGINAL
        ]
        return original_files[-1] if original_files else None

    def _create_item(
        self,
        *,
        export_run: ExportRun,
        document: Document,
        document_file: DocumentFile | None,
        status: str,
        message: str,
        exported_filename: str = "",
    ) -> ExportRunItem:
        return ExportRunItem.objects.create(
            tenant=self.export_run.tenant,
            export_run=export_run,
            document=document,
            document_file=document_file,
            status=status,
            message=message,
            exported_filename=exported_filename,
        )

    def _mark_processed(self, export_run: ExportRun) -> None:
        export_run.processed_count += 1
        export_run.save(update_fields=["processed_count", "updated_at"])

    def _manifest_row(
        self,
        document: Document,
        document_file: DocumentFile,
        exported_filename: str,
    ) -> dict[str, object]:
        return {
            "doksio_document_id": document.id,
            "doksio_file_id": document_file.id,
            "exported_filename": exported_filename,
            "original_filename": document_file.original_filename,
            "document_title": document.title,
            "document_box": document.space.path,
            "document_date": document.document_date.isoformat() if document.document_date else "",
            "created_at": document.created_at.isoformat(),
            "content_type": document_file.content_type,
            "sha256": document_file.sha256,
        }

    def _log_row(
        self,
        document: Document,
        status: str,
        message: str,
    ) -> dict[str, object]:
        return {
            "doksio_document_id": document.id,
            "document_title": document.title,
            "status": status,
            "message": message,
        }
