from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from doksio.audit.services import RecordAuditEvent
from doksio.documents.models import DocumentFile, DocumentOfficeConversionJob
from doksio.documents.thumbnails import create_thumbnail_for_document_file
from doksio.storage.services import StoreImmutableFile

OFFICE_DOCUMENT_CONTENT_TYPES = {
    "application/msword",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def normalized_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def supports_office_conversion(content_type: str) -> bool:
    return normalized_content_type(content_type) in OFFICE_DOCUMENT_CONTENT_TYPES


def office_pdf_derivative(source_file: DocumentFile) -> DocumentFile | None:
    return (
        source_file.derivatives.filter(
            file_kind=DocumentFile.Kind.DERIVATIVE,
            content_type="application/pdf",
        )
        .order_by("-created_at", "-id")
        .first()
    )


@dataclass(frozen=True)
class StartOfficeConversion:
    source_file: DocumentFile
    actor: get_user_model() | None = None
    auto_start_ocr: bool = True
    run_inline: bool | None = None

    @transaction.atomic
    def execute(self) -> DocumentOfficeConversionJob:
        if self.source_file.file_kind != DocumentFile.Kind.ORIGINAL:
            raise ValueError("Nur Originaldateien können konvertiert werden.")
        if not supports_office_conversion(self.source_file.content_type):
            raise ValueError("Dieser Dateityp ist kein unterstütztes Office-Dokument.")

        existing_job = (
            DocumentOfficeConversionJob.objects.select_for_update()
            .filter(
                source_file=self.source_file,
                status__in=[
                    DocumentOfficeConversionJob.Status.PENDING,
                    DocumentOfficeConversionJob.Status.RUNNING,
                    DocumentOfficeConversionJob.Status.SUCCEEDED,
                ],
            )
            .order_by("-created_at", "-id")
            .first()
        )
        if existing_job is not None:
            return existing_job

        job = DocumentOfficeConversionJob.objects.create(
            tenant=self.source_file.tenant,
            source_file=self.source_file,
            auto_start_ocr=self.auto_start_ocr,
            created_by=self.actor,
        )
        should_run_inline = (
            getattr(settings, "OFFICE_CONVERSION_RUN_INLINE", False)
            if self.run_inline is None
            else self.run_inline
        )
        if should_run_inline:
            return RunOfficeConversion(job=job).execute()

        from doksio.documents.tasks import convert_office_document

        transaction.on_commit(lambda: convert_office_document.delay(job.id))
        return job


@dataclass(frozen=True)
class RunOfficeConversion:
    job: DocumentOfficeConversionJob

    def execute(self) -> DocumentOfficeConversionJob:
        if self.job.status == DocumentOfficeConversionJob.Status.SUCCEEDED:
            return self.job
        if (
            self.job.status == DocumentOfficeConversionJob.Status.FAILED
            and self.job.error_message == "Durch einen Administrator abgebrochen."
        ):
            return self.job

        self._mark_running()
        try:
            output_file = office_pdf_derivative(self.job.source_file)
            if output_file is None:
                pdf_bytes = self._convert_to_pdf()
                output_file = StoreImmutableFile(
                    tenant=self.job.tenant,
                    document=self.job.source_file.document,
                    file_obj=BytesIO(pdf_bytes),
                    original_filename=(
                        f"{Path(self.job.source_file.original_filename).stem}.pdf"
                    ),
                    content_type="application/pdf",
                    file_kind=DocumentFile.Kind.DERIVATIVE,
                    derivative_of=self.job.source_file,
                    created_by=self.job.created_by,
                ).execute()
            create_thumbnail_for_document_file(
                output_file,
                actor=self.job.created_by,
            )
        except Exception as error:
            return self._mark_failed(error)

        self._mark_succeeded(output_file)
        if self.job.auto_start_ocr:
            from doksio.ocr.services import StartOcrForDocumentFile

            StartOcrForDocumentFile(
                document_file=output_file,
                actor=self.job.created_by,
            ).execute()
        return self.job

    def _convert_to_pdf(self) -> bytes:
        executable = shutil.which("soffice") or shutil.which("libreoffice")
        if executable is None:
            raise RuntimeError(
                "LibreOffice ist nicht installiert; Office-Dokument kann nicht "
                "konvertiert werden."
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            input_directory = work_directory / "input"
            output_directory = work_directory / "output"
            profile_directory = work_directory / "profile"
            input_directory.mkdir()
            output_directory.mkdir()
            profile_directory.mkdir()
            input_path = input_directory / self.job.source_file.original_filename
            with default_storage.open(
                self.job.source_file.storage_key,
                "rb",
            ) as stored_file:
                input_path.write_bytes(stored_file.read())

            result = subprocess.run(
                [
                    executable,
                    f"-env:UserInstallation={profile_directory.as_uri()}",
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nolockcheck",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_directory),
                    str(input_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=getattr(
                    settings,
                    "OFFICE_CONVERSION_TIMEOUT_SECONDS",
                    120,
                ),
            )
            if result.returncode != 0:
                message = (result.stderr or result.stdout).strip()
                raise RuntimeError(
                    f"LibreOffice-Konvertierung fehlgeschlagen: {message}"
                )
            output_candidates = list(output_directory.glob("*.pdf"))
            if len(output_candidates) != 1:
                raise RuntimeError(
                    "LibreOffice hat kein eindeutiges PDF-Ergebnis erzeugt."
                )
            pdf_bytes = output_candidates[0].read_bytes()
            if not pdf_bytes.startswith(b"%PDF-"):
                raise RuntimeError("Das Konvertierungsergebnis ist keine gültige PDF.")
            return pdf_bytes

    def _mark_running(self) -> None:
        self.job.status = DocumentOfficeConversionJob.Status.RUNNING
        self.job.started_at = timezone.now()
        self.job.error_message = ""
        self.job.save(
            update_fields=["status", "started_at", "error_message", "updated_at"]
        )

    def _mark_succeeded(self, output_file: DocumentFile) -> None:
        self.job.status = DocumentOfficeConversionJob.Status.SUCCEEDED
        self.job.output_file = output_file
        self.job.completed_at = timezone.now()
        self.job.error_message = ""
        self.job.save(
            update_fields=[
                "status",
                "output_file",
                "completed_at",
                "error_message",
                "updated_at",
            ]
        )
        RecordAuditEvent(
            tenant=self.job.tenant,
            actor=self.job.created_by,
            event_type="document_office_conversion.succeeded",
            object_type="documents.DocumentOfficeConversionJob",
            object_id=str(self.job.id),
            data={
                "document_id": self.job.source_file.document_id,
                "source_file_id": self.job.source_file_id,
                "output_file_id": output_file.id,
            },
        ).execute()

    def _mark_failed(self, error: Exception) -> DocumentOfficeConversionJob:
        self.job.status = DocumentOfficeConversionJob.Status.FAILED
        self.job.error_message = str(error)
        self.job.completed_at = timezone.now()
        self.job.save(
            update_fields=[
                "status",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )
        RecordAuditEvent(
            tenant=self.job.tenant,
            actor=self.job.created_by,
            event_type="document_office_conversion.failed",
            object_type="documents.DocumentOfficeConversionJob",
            object_id=str(self.job.id),
            data={
                "document_id": self.job.source_file.document_id,
                "source_file_id": self.job.source_file_id,
                "error": str(error),
            },
        ).execute()
        return self.job
