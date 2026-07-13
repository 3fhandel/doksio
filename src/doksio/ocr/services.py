"""Application services for local OCR processing."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from doksio.audit.services import RecordAuditEvent
from doksio.documents.models import Document, DocumentFile
from doksio.ocr.models import OcrJob

DATE_PATTERNS = [
    re.compile(r"\b(?P<day>\d{1,2})[.\/-](?P<month>\d{1,2})[.\/-](?P<year>\d{2,4})\b"),
    re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b"),
]
DATE_LABEL_PATTERN = re.compile(
    r"\b(belegdatum|rechnungsdatum|datum|date)\b",
    re.IGNORECASE,
)
TITLE_LABEL_PATTERN = re.compile(
    (
        r"\b(titel|betreff|subject|rechnung|angebot|gutschrift|lieferschein|"
        r"bescheinigung\w*|arbeitsunfähigkeit\w*|arbeitsunfähigkeits\w*)\b"
    ),
    re.IGNORECASE,
)
TITLE_PREFIX_PATTERN = re.compile(
    r"^\s*(titel|betreff|subject)\s*[:\-]\s*",
    re.IGNORECASE,
)
TITLE_NOISE_PATTERN = re.compile(
    (
        r"\b(summe|gesamt|betrag|iban|bic|ust|steuer|telefon|email|www|"
        r"name|vorname|versicherten|krankenkasse|kostenträger|"
        r"versicherten-nr|betriebsstätten-nr|arzt-nr|status|geb|tag|"
        r"angaben|diagnose|dauer|übersandt|uebersandt)\b"
    ),
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OcrExtraction:
    text: str
    engine: str
    language: str


def supports_ocr_content_type(content_type: str) -> bool:
    normalized_content_type = content_type.split(";", 1)[0].strip().lower()
    return (
        normalized_content_type == "application/pdf"
        or normalized_content_type.startswith("image/")
        or normalized_content_type.startswith("text/")
    )


def _normalize_year(year: int) -> int:
    if year < 100:
        return 2000 + year if year < 70 else 1900 + year
    return year


def _parse_date_match(match: re.Match) -> date | None:
    try:
        return date(
            _normalize_year(int(match.group("year"))),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None


def extract_document_date(text: str) -> date | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    labeled_lines = [line for line in lines if DATE_LABEL_PATTERN.search(line)]
    for line in [*labeled_lines, *lines]:
        for pattern in DATE_PATTERNS:
            match = pattern.search(line)
            if match:
                parsed_date = _parse_date_match(match)
                if parsed_date is not None:
                    return parsed_date
    return None


def _normalize_title_candidate(candidate: str) -> str:
    title = TITLE_PREFIX_PATTERN.sub("", candidate).strip(" \t:-")
    return " ".join(title.split())


def _is_title_candidate(candidate: str) -> bool:
    if len(candidate) < 4 or len(candidate) > 120:
        return False
    if TITLE_NOISE_PATTERN.search(candidate):
        return False
    if DATE_LABEL_PATTERN.search(candidate) and any(
        pattern.search(candidate) for pattern in DATE_PATTERNS
    ):
        return False
    return any(character.isalpha() for character in candidate)


def extract_document_title(text: str) -> str | None:
    raw_lines = [line.strip() for line in text.splitlines()]
    lines = []
    index = 0
    while index < len(raw_lines):
        line = raw_lines[index]
        if line.endswith("-") and index + 1 < len(raw_lines):
            next_line = raw_lines[index + 1].strip()
            if next_line:
                line = f"{line[:-1]}{next_line}"
                index += 1
        lines.append(_normalize_title_candidate(line))
        index += 1
    lines = [line for line in lines if line]

    labeled_lines = [line for line in lines if TITLE_LABEL_PATTERN.search(line)]
    for line in [*labeled_lines, *lines]:
        if _is_title_candidate(line):
            return line[:255]
    return None


class LocalOcrProvider:
    """Local OCR/text extraction adapter backed by CLI tools."""

    def extract(self, document_file: DocumentFile) -> OcrExtraction:
        language = getattr(settings, "OCR_LANGUAGE", "deu+eng")
        content_type = document_file.content_type.split(";", 1)[0].strip().lower()
        if content_type.startswith("text/"):
            text = default_storage.open(document_file.storage_key, "rb").read()
            return OcrExtraction(
                text=text.decode("utf-8", errors="replace"),
                engine="plain-text",
                language=language,
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / document_file.original_filename
            with default_storage.open(document_file.storage_key, "rb") as source:
                input_path.write_bytes(source.read())

            if content_type == "application/pdf":
                return self._extract_pdf(input_path=input_path, language=language)
            if content_type.startswith("image/"):
                return self._extract_image(input_path=input_path, language=language)

        raise ValueError(
            f"OCR unterstützt diesen Dateityp noch nicht: {document_file.content_type}"
        )

    def _extract_pdf(self, input_path: Path, language: str) -> OcrExtraction:
        text = self._extract_pdf_text(input_path=input_path)
        if text.strip():
            return OcrExtraction(text=text, engine="pdftotext", language=language)

        ocrmypdf = shutil.which("ocrmypdf")
        if ocrmypdf is None:
            raise RuntimeError(
                "Kein Text gefunden und ocrmypdf ist nicht installiert."
            )

        output_pdf = input_path.with_suffix(".ocr.pdf")
        sidecar = input_path.with_suffix(".txt")
        subprocess.run(
            [
                ocrmypdf,
                "--skip-text",
                "--sidecar",
                str(sidecar),
                "-l",
                language,
                str(input_path),
                str(output_pdf),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=getattr(settings, "OCR_COMMAND_TIMEOUT_SECONDS", 300),
        )
        return OcrExtraction(
            text=sidecar.read_text(encoding="utf-8", errors="replace"),
            engine="ocrmypdf",
            language=language,
        )

    def _extract_pdf_text(self, input_path: Path) -> str:
        pdftotext = shutil.which("pdftotext")
        if pdftotext is None:
            return ""

        result = subprocess.run(
            [pdftotext, str(input_path), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=getattr(settings, "OCR_COMMAND_TIMEOUT_SECONDS", 300),
        )
        if result.returncode != 0:
            return ""
        return result.stdout

    def _extract_image(self, input_path: Path, language: str) -> OcrExtraction:
        tesseract = shutil.which("tesseract")
        if tesseract is None:
            raise RuntimeError("tesseract ist nicht installiert.")

        ocr_input_path = self._prepare_image_for_ocr(input_path=input_path)
        text = self._run_tesseract(
            tesseract=tesseract,
            input_path=ocr_input_path,
            language=language,
        )
        enhanced_input_path = self._prepare_enhanced_image_for_ocr(
            input_path=ocr_input_path
        )
        if enhanced_input_path != ocr_input_path:
            form_text = self._run_tesseract(
                tesseract=tesseract,
                input_path=enhanced_input_path,
                language=language,
                psm=getattr(settings, "OCR_IMAGE_FORM_PSM", "6"),
            )
            text = self._merge_ocr_text(text, form_text)
        detail_source_path = (
            enhanced_input_path
            if enhanced_input_path != ocr_input_path
            else ocr_input_path
        )
        for detail_input_path in self._prepare_detail_regions_for_ocr(
            input_path=detail_source_path
        ):
            detail_text = self._run_tesseract(
                tesseract=tesseract,
                input_path=detail_input_path,
                language=language,
                psm=getattr(settings, "OCR_IMAGE_DETAIL_PSM", "6"),
            )
            text = self._merge_ocr_text(text, detail_text)
        return OcrExtraction(
            text=text,
            engine="tesseract",
            language=language,
        )

    def _run_tesseract(
        self,
        *,
        tesseract: str,
        input_path: Path,
        language: str,
        psm: str | None = None,
    ) -> str:
        command = [tesseract, str(input_path), "stdout", "-l", language]
        if psm:
            command.extend(["--psm", str(psm)])
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=getattr(settings, "OCR_COMMAND_TIMEOUT_SECONDS", 300),
        )
        return result.stdout

    def _prepare_image_for_ocr(self, input_path: Path) -> Path:
        magick = shutil.which("magick")
        if magick is None:
            return input_path

        prepared_path = input_path.with_name(f"{input_path.stem}.ocr.png")
        subprocess.run(
            [
                magick,
                str(input_path),
                "-auto-orient",
                str(prepared_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=getattr(settings, "OCR_COMMAND_TIMEOUT_SECONDS", 300),
        )
        return prepared_path

    def _image_size(self, input_path: Path) -> tuple[int, int]:
        magick = shutil.which("magick")
        if magick is None:
            return 0, 0

        result = subprocess.run(
            [
                magick,
                "identify",
                "-format",
                "%w %h",
                str(input_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=getattr(settings, "OCR_COMMAND_TIMEOUT_SECONDS", 300),
        )
        width, height = result.stdout.strip().split()
        return int(width), int(height)

    def _prepare_enhanced_image_for_ocr(self, input_path: Path) -> Path:
        magick = shutil.which("magick")
        if magick is None:
            return input_path

        prepared_path = input_path.with_name(
            f"{input_path.stem}.form-ocr{input_path.suffix}"
        )
        subprocess.run(
            [
                magick,
                str(input_path),
                "-colorspace",
                "Gray",
                "-normalize",
                "-sharpen",
                "0x1",
                "-density",
                "300",
                str(prepared_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=getattr(settings, "OCR_COMMAND_TIMEOUT_SECONDS", 300),
        )
        return prepared_path

    def _prepare_detail_regions_for_ocr(self, input_path: Path) -> list[Path]:
        magick = shutil.which("magick")
        if magick is None:
            return []

        width, height = self._image_size(input_path=input_path)
        if not width or not height:
            return []

        region_width = max(1, round(width * 0.60))
        region_height = max(1, round(height * 0.33))
        region_y = max(0, round(height * 0.08))
        prepared_path = input_path.with_name(
            f"{input_path.stem}.detail-top-left{input_path.suffix}"
        )
        subprocess.run(
            [
                magick,
                str(input_path),
                "-crop",
                f"{region_width}x{region_height}+0+{region_y}",
                "+repage",
                "-resize",
                "250%",
                "-threshold",
                "70%",
                str(prepared_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=getattr(settings, "OCR_COMMAND_TIMEOUT_SECONDS", 300),
        )
        return [prepared_path]

    def _merge_ocr_text(self, primary_text: str, secondary_text: str) -> str:
        merged_lines = []
        seen_lines = set()
        for line in [*primary_text.splitlines(), "", *secondary_text.splitlines()]:
            normalized_line = " ".join(line.split()).casefold()
            if normalized_line and normalized_line in seen_lines:
                continue
            if normalized_line:
                seen_lines.add(normalized_line)
            merged_lines.append(line)
        return "\n".join(merged_lines).strip() + "\n"


@dataclass(frozen=True)
class CreateOcrJob:
    document_file: DocumentFile
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> OcrJob:
        job = OcrJob.objects.create(
            tenant=self.document_file.tenant,
            document_file=self.document_file,
            language=getattr(settings, "OCR_LANGUAGE", "deu+eng"),
            created_by=self.actor,
        )
        RecordAuditEvent(
            tenant=self.document_file.tenant,
            actor=self.actor,
            event_type="ocr_job.created",
            object_type="ocr.OcrJob",
            object_id=str(job.id),
            data={
                "document_file_id": self.document_file.id,
                "document_id": self.document_file.document_id,
            },
        ).execute()
        return job


@dataclass(frozen=True)
class RunOcrJob:
    job: OcrJob
    provider: LocalOcrProvider | None = None

    @transaction.atomic
    def _mark_running(self) -> None:
        self.job.status = OcrJob.Status.RUNNING
        self.job.started_at = timezone.now()
        self.job.save(update_fields=["status", "started_at", "updated_at"])

    @transaction.atomic
    def _mark_succeeded(self, extraction: OcrExtraction) -> OcrJob:
        self.job.status = OcrJob.Status.SUCCEEDED
        self.job.engine = extraction.engine
        self.job.language = extraction.language
        self.job.extracted_text = extraction.text
        self.job.error_message = ""
        self.job.completed_at = timezone.now()
        self.job.save(
            update_fields=[
                "status",
                "engine",
                "language",
                "extracted_text",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )
        RecordAuditEvent(
            tenant=self.job.tenant,
            actor=self.job.created_by,
            event_type="ocr_job.succeeded",
            object_type="ocr.OcrJob",
            object_id=str(self.job.id),
            data={
                "document_file_id": self.job.document_file_id,
                "text_length": len(extraction.text),
                "engine": extraction.engine,
            },
        ).execute()
        self._prefill_document_title(extraction)
        self._prefill_document_date(extraction)
        transaction.on_commit(
            lambda: self._rebuild_document_search_index(),
        )
        return self.job

    def _rebuild_document_search_index(self) -> None:
        from doksio.search.services import RebuildDocumentSearchIndex

        RebuildDocumentSearchIndex(
            document=self.job.document_file.document,
        ).execute()

    @transaction.atomic
    def _prefill_document_title(self, extraction: OcrExtraction) -> None:
        document = self.job.document_file.document
        if document.title_source not in {
            Document.TitleSource.FILENAME,
            Document.TitleSource.OCR,
        }:
            return

        title = extract_document_title(extraction.text)
        if title is None:
            return

        previous_title = document.title
        document.title = title
        document.title_source = Document.TitleSource.OCR
        document.save(update_fields=["title", "title_source", "updated_at"])
        RecordAuditEvent(
            tenant=self.job.tenant,
            actor=self.job.created_by,
            event_type="document_title.prefilled_from_ocr",
            object_type="documents.Document",
            object_id=str(document.id),
            data={
                "document_id": document.id,
                "document_file_id": self.job.document_file_id,
                "ocr_job_id": self.job.id,
                "title": title,
                "previous_title": previous_title,
            },
        ).execute()

    @transaction.atomic
    def _prefill_document_date(self, extraction: OcrExtraction) -> None:
        document = self.job.document_file.document
        if document.document_date is not None:
            return

        document_date = extract_document_date(extraction.text)
        if document_date is None:
            return

        document.document_date = document_date
        document.save(update_fields=["document_date", "updated_at"])
        RecordAuditEvent(
            tenant=self.job.tenant,
            actor=self.job.created_by,
            event_type="document_date.prefilled_from_ocr",
            object_type="documents.Document",
            object_id=str(document.id),
            data={
                "document_id": document.id,
                "document_file_id": self.job.document_file_id,
                "ocr_job_id": self.job.id,
                "document_date": document_date.isoformat(),
            },
        ).execute()

    @transaction.atomic
    def _mark_failed(self, error: Exception) -> OcrJob:
        self.job.status = OcrJob.Status.FAILED
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
            event_type="ocr_job.failed",
            object_type="ocr.OcrJob",
            object_id=str(self.job.id),
            data={
                "document_file_id": self.job.document_file_id,
                "error": str(error),
            },
        ).execute()
        return self.job

    def execute(self) -> OcrJob:
        self._mark_running()
        provider = self.provider or LocalOcrProvider()
        try:
            extraction = provider.extract(self.job.document_file)
        except Exception as error:
            return self._mark_failed(error)
        return self._mark_succeeded(extraction)


@dataclass(frozen=True)
class StartOcrForDocumentFile:
    document_file: DocumentFile
    actor: get_user_model() | None = None
    run_inline: bool | None = None

    def execute(self) -> OcrJob:
        job = CreateOcrJob(
            document_file=self.document_file,
            actor=self.actor,
        ).execute()
        should_run_inline = (
            getattr(settings, "OCR_RUN_INLINE", False)
            if self.run_inline is None
            else self.run_inline
        )
        if should_run_inline:
            return RunOcrJob(job=job).execute()

        from doksio.ocr.tasks import run_ocr_job

        run_ocr_job.delay(job.id)
        return job
