"""Application services for local OCR processing."""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from doksio.audit.services import RecordAuditEvent
from doksio.documents.models import Document, DocumentFile
from doksio.documents.title_rules import (
    DEFAULT_INVOICE_OCR_TITLE_FORMAT,
    ocr_policy_with_einvoice_fallback,
    title_from_invoice_ocr_text,
)
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
    page_texts: tuple[str, ...] = ()
    layout_pages: tuple[dict, ...] = ()


def _normalized_word(
    text: str,
    left: float,
    top: float,
    width: float,
    height: float,
    confidence: float | None = None,
) -> dict | None:
    value = " ".join(text.split())
    if not value or width <= 0 or height <= 0:
        return None
    word = {
        "text": value,
        "x": round(max(0.0, min(1.0, left)), 6),
        "y": round(max(0.0, min(1.0, top)), 6),
        "width": round(max(0.0, min(1.0 - left, width)), 6),
        "height": round(max(0.0, min(1.0 - top, height)), 6),
    }
    if confidence is not None:
        word["confidence"] = round(confidence, 1)
    return word


def _layout_from_tesseract_tsv(tsv: str, page_number: int) -> dict:
    words = []
    rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))
    page_row = next((row for row in rows if row.get("level") == "1"), None)
    if page_row is None:
        return {"page": page_number, "words": []}
    try:
        image_width = float(page_row["width"])
        image_height = float(page_row["height"])
    except (KeyError, TypeError, ValueError):
        return {"page": page_number, "words": []}
    if image_width <= 0 or image_height <= 0:
        return {"page": page_number, "words": []}
    for row in rows:
        if row.get("level") != "5":
            continue
        try:
            word = _normalized_word(
                row.get("text", ""),
                float(row["left"]) / image_width,
                float(row["top"]) / image_height,
                float(row["width"]) / image_width,
                float(row["height"]) / image_height,
                float(row["conf"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if word:
            words.append(word)
    return {"page": page_number, "words": words}


def _layout_from_searchable_pdf(pdf_path: Path) -> tuple[dict, ...]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return ()
    pdf = pdfium.PdfDocument(pdf_path.read_bytes())
    pages = []
    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            text_page = page.get_textpage()
            try:
                page_width = float(page.get_width())
                page_height = float(page.get_height())
                chars = []
                for char_index in range(text_page.count_chars()):
                    value = text_page.get_text_range(char_index, 1)
                    left, bottom, right, top = text_page.get_charbox(char_index)
                    chars.append(
                        (value, left, page_height - top, right, page_height - bottom)
                    )
                words = []
                current = []
                for value, left, top, right, bottom in [*chars, (" ", 0, 0, 0, 0)]:
                    if value and not value.isspace():
                        current.append((value, left, top, right, bottom))
                        continue
                    if not current:
                        continue
                    word = _normalized_word(
                        "".join(item[0] for item in current),
                        min(item[1] for item in current) / page_width,
                        min(item[2] for item in current) / page_height,
                        (
                            max(item[3] for item in current)
                            - min(item[1] for item in current)
                        )
                        / page_width,
                        (
                            max(item[4] for item in current)
                            - min(item[2] for item in current)
                        )
                        / page_height,
                    )
                    if word:
                        words.append(word)
                    current = []
                pages.append({"page": page_index + 1, "words": words})
            finally:
                text_page.close()
                page.close()
    finally:
        pdf.close()
    return tuple(pages)


def _split_pdf_page_texts(text: str) -> tuple[str, ...]:
    pages = tuple(page.strip() for page in text.split("\f"))
    while pages and not pages[-1]:
        pages = pages[:-1]
    return pages


def _page_text_ranges(text: str, page_texts: tuple[str, ...]) -> list[list[int]]:
    ranges = []
    cursor = 0
    for page_text in page_texts:
        if not page_text:
            ranges.append([cursor, cursor])
            continue
        start = text.find(page_text, cursor)
        if start < 0:
            return []
        end = start + len(page_text)
        ranges.append([start, end])
        cursor = end
    return ranges


def _encode_layout_sidecar(layout_pages: tuple[dict, ...]) -> bytes:
    payload = {"version": 1, "pages": layout_pages}
    return gzip.compress(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        compresslevel=6,
    )


def load_ocr_layout(job: OcrJob) -> dict:
    if not job.layout_storage_key:
        return {"version": 1, "pages": []}
    try:
        with default_storage.open(job.layout_storage_key, "rb") as stored_file:
            return json.loads(gzip.decompress(stored_file.read()).decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": 1, "pages": []}


def find_ocr_layout_matches(job: OcrJob, query: str) -> list[dict]:
    query_words = [word.casefold() for word in query.split() if word]
    if not query_words:
        return []
    matches = []
    for page in load_ocr_layout(job).get("pages", []):
        words = page.get("words", []) if isinstance(page, dict) else []
        normalized_words = [str(word.get("text", "")).casefold() for word in words]
        width = len(query_words)
        for index in range(0, len(words) - width + 1):
            candidate = normalized_words[index : index + width]
            if width == 1:
                matched = query_words[0] in candidate[0]
            else:
                matched = candidate == query_words
            if not matched:
                continue
            rectangles = [
                {
                    key: word[key]
                    for key in ("x", "y", "width", "height")
                    if key in word
                }
                for word in words[index : index + width]
            ]
            if rectangles:
                matches.append(
                    {
                        "page": int(page.get("page", 1)),
                        "rectangles": rectangles,
                    }
                )
    return matches


def find_document_ocr_layout_matches(document: Document, query: str) -> list[dict]:
    for document_file in document.files.all():
        jobs = sorted(
            document_file.ocr_jobs.all(),
            key=lambda job: job.id,
            reverse=True,
        )
        for job in jobs:
            if job.status != OcrJob.Status.SUCCEEDED or not job.layout_storage_key:
                continue
            matches = find_ocr_layout_matches(job, query)
            if matches:
                return matches
    return []


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


def title_from_ocr_policy(text: str, policy: dict | None) -> str | None:
    policy = ocr_policy_with_einvoice_fallback(policy)
    strategy = policy.get("strategy", "automatic")
    if strategy == "automatic":
        invoice_title = title_from_invoice_ocr_text(
            text,
            DEFAULT_INVOICE_OCR_TITLE_FORMAT,
        )
        if invoice_title:
            return invoice_title
    if strategy == "invoice_ocr":
        title = title_from_invoice_ocr_text(
            text,
            str(
                policy.get(
                    "invoice_ocr_format",
                    DEFAULT_INVOICE_OCR_TITLE_FORMAT,
                )
            ),
        )
        if title:
            return title
        strategy = policy.get("invoice_ocr_fallback_strategy", "automatic")
    if strategy == "disabled":
        return None
    if strategy == "regex":
        pattern = str(policy.get("regex_search", "")).strip()
        if not pattern:
            return None
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match is None:
            return None
        replacement = str(policy.get("regex_replace", ""))
        if replacement:
            title = match.expand(replacement)
        elif match.groups():
            title = match.group(1)
        else:
            title = match.group(0)
        title = _normalize_title_candidate(title)
        return title[:255] if title else None
    return extract_document_title(text)


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
            return OcrExtraction(
                text=text,
                engine="pdftotext",
                language=language,
                page_texts=_split_pdf_page_texts(text),
            )

        ocrmypdf = shutil.which("ocrmypdf")
        if ocrmypdf is None:
            return self._extract_pdf_images(input_path=input_path, language=language)

        with tempfile.TemporaryDirectory() as temporary_directory:
            ocrmypdf_directory = Path(temporary_directory)
            output_pdf = ocrmypdf_directory / f"{input_path.stem}.ocr.pdf"
            sidecar = ocrmypdf_directory / f"{input_path.stem}.txt"
            try:
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
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                return self._extract_pdf_images(
                    input_path=input_path,
                    language=language,
                )

            sidecar_text = sidecar.read_text(encoding="utf-8", errors="replace")
            layout_pages = _layout_from_searchable_pdf(output_pdf)
        if not sidecar_text.strip():
            return self._extract_pdf_images(input_path=input_path, language=language)

        return OcrExtraction(
            text=sidecar_text,
            engine="ocrmypdf",
            language=language,
            page_texts=_split_pdf_page_texts(sidecar_text),
            layout_pages=layout_pages,
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

    def _extract_pdf_images(self, input_path: Path, language: str) -> OcrExtraction:
        tesseract = shutil.which("tesseract")
        if tesseract is None:
            raise RuntimeError(
                "Kein Text gefunden und weder ocrmypdf noch tesseract ist verfügbar."
            )

        page_texts = []
        layout_pages = []
        with tempfile.TemporaryDirectory() as temporary_directory:
            rendered_paths = self._render_pdf_pages_for_ocr(
                input_path=input_path,
                output_directory=Path(temporary_directory),
            )
            for page_path in rendered_paths:
                page_text, page_layout = self._run_tesseract_with_layout(
                    tesseract=tesseract,
                    input_path=page_path,
                    language=language,
                    page_number=len(page_texts) + 1,
                )
                page_texts.append(page_text.strip())
                layout_pages.append(page_layout)
        text = "\n\n".join(page for page in page_texts if page)
        return OcrExtraction(
            text=text,
            engine="pypdfium2+tesseract",
            language=language,
            page_texts=tuple(page_texts),
            layout_pages=tuple(layout_pages),
        )

    def _render_pdf_pages_for_ocr(
        self,
        input_path: Path,
        output_directory: Path,
    ) -> list[Path]:
        try:
            import pypdfium2 as pdfium
        except ImportError as error:
            raise RuntimeError("PDF-Rendering für OCR ist nicht verfügbar.") from error

        max_pages = getattr(settings, "OCR_IMAGE_MAX_PAGES", 25)
        max_edge = getattr(settings, "OCR_IMAGE_MAX_EDGE", 3000)
        rendered_paths = []
        pdf = pdfium.PdfDocument(input_path.read_bytes())
        try:
            for page_index in range(min(len(pdf), max_pages)):
                page = pdf[page_index]
                try:
                    bitmap = page.render(scale=3)
                    image = bitmap.to_pil()
                finally:
                    page.close()

                if max(image.size) > max_edge:
                    image.thumbnail((max_edge, max_edge))
                if image.mode != "RGB":
                    image = image.convert("RGB")

                page_path = output_directory / (
                    f"{input_path.stem}.pdf-ocr-p{page_index + 1:03}.png"
                )
                image.save(page_path, format="PNG", optimize=True)
                rendered_paths.append(page_path)
        finally:
            pdf.close()
        return rendered_paths

    def _extract_image(self, input_path: Path, language: str) -> OcrExtraction:
        tesseract = shutil.which("tesseract")
        if tesseract is None:
            raise RuntimeError("tesseract ist nicht installiert.")

        page_texts = []
        layout_pages = []
        ocr_input_paths = self._prepare_image_pages_for_ocr(input_path=input_path)
        enhanced_max_pages = getattr(settings, "OCR_IMAGE_ENHANCED_MAX_PAGES", 1)
        for page_index, ocr_input_path in enumerate(ocr_input_paths):
            page_text, page_layout = self._run_tesseract_with_layout(
                tesseract=tesseract,
                input_path=ocr_input_path,
                language=language,
                page_number=page_index + 1,
            )
            layout_pages.append(page_layout)
            combined_page_text = page_text
            if page_index >= enhanced_max_pages:
                page_texts.append(combined_page_text.strip())
                continue

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
                combined_page_text = self._merge_ocr_text(
                    combined_page_text,
                    form_text,
                )
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
                combined_page_text = self._merge_ocr_text(
                    combined_page_text,
                    detail_text,
                )
            page_texts.append(combined_page_text.strip())
        text = "\n\n".join(page for page in page_texts if page)
        return OcrExtraction(
            text=text,
            engine="tesseract",
            language=language,
            page_texts=tuple(page_texts),
            layout_pages=tuple(layout_pages),
        )

    def _run_tesseract_with_layout(
        self,
        *,
        tesseract: str,
        input_path: Path,
        language: str,
        page_number: int,
    ) -> tuple[str, dict]:
        output_base = input_path.with_name(f"{input_path.stem}.doksio-ocr")
        result = subprocess.run(
            [
                tesseract,
                str(input_path),
                str(output_base),
                "-l",
                language,
                "txt",
                "tsv",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=getattr(settings, "OCR_TESSERACT_TIMEOUT_SECONDS", 120),
        )
        text_path = output_base.with_suffix(".txt")
        tsv_path = output_base.with_suffix(".tsv")
        if not text_path.exists() or not tsv_path.exists():
            return result.stdout, {"page": page_number, "words": []}
        text = text_path.read_text(encoding="utf-8", errors="replace")
        tsv = tsv_path.read_text(encoding="utf-8", errors="replace")
        return text, _layout_from_tesseract_tsv(tsv, page_number)

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
            timeout=getattr(settings, "OCR_TESSERACT_TIMEOUT_SECONDS", 120),
        )
        return result.stdout

    def _prepare_image_pages_for_ocr(self, input_path: Path) -> list[Path]:
        try:
            from PIL import Image, ImageOps, ImageSequence
        except ImportError:
            return [self._prepare_image_for_ocr(input_path=input_path)]

        max_edge = getattr(settings, "OCR_IMAGE_MAX_EDGE", 3000)
        max_pages = getattr(settings, "OCR_IMAGE_MAX_PAGES", 25)
        prepared_paths = []
        try:
            with Image.open(input_path) as image:
                for page_index, frame in enumerate(ImageSequence.Iterator(image)):
                    if page_index >= max_pages:
                        break

                    page = ImageOps.exif_transpose(frame)
                    page.load()
                    page = self._normalize_pillow_image_for_ocr(page=page)
                    if max(page.size) > max_edge:
                        page.thumbnail((max_edge, max_edge))

                    prepared_path = input_path.with_name(
                        f"{input_path.stem}.ocr-p{page_index + 1:03}.png"
                    )
                    page.save(prepared_path, format="PNG", optimize=True)
                    prepared_paths.append(prepared_path)
        except Exception:
            return [self._prepare_image_for_ocr(input_path=input_path)]

        return prepared_paths or [input_path]

    def _normalize_pillow_image_for_ocr(self, *, page):
        try:
            from PIL import Image
        except ImportError:
            return page

        if page.mode == "RGBA":
            background = Image.new("RGB", page.size, "white")
            background.paste(page, mask=page.getchannel("A"))
            return background
        if page.mode not in ("RGB", "L"):
            return page.convert("RGB")
        return page

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
    metadata: dict | None = None

    @transaction.atomic
    def execute(self) -> OcrJob:
        job = OcrJob.objects.create(
            tenant=self.document_file.tenant,
            document_file=self.document_file,
            language=getattr(settings, "OCR_LANGUAGE", "deu+eng"),
            metadata=self.metadata or {},
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
        if extraction.layout_pages:
            sidecar = _encode_layout_sidecar(extraction.layout_pages)
            storage_key = (
                f"tenants/{self.job.tenant_id}/ocr/{self.job.id}/"
                f"layout-{uuid.uuid4().hex}.json.gz"
            )
            self.job.layout_storage_key = default_storage.save(
                storage_key,
                ContentFile(sidecar),
            )
            self.job.layout_byte_size = len(sidecar)
        self.job.metadata = {
            **(self.job.metadata or {}),
            "page_text_ranges": _page_text_ranges(
                extraction.text,
                extraction.page_texts,
            ),
        }
        self.job.error_message = ""
        self.job.completed_at = timezone.now()
        self.job.save(
            update_fields=[
                "status",
                "engine",
                "language",
                "extracted_text",
                "layout_storage_key",
                "layout_byte_size",
                "metadata",
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
        if not self.job.metadata.get("layout_backfill"):
            self._prefill_document_title(extraction)
            self._prefill_document_date(extraction)
            from doksio.documents.services import ApplyOcrMetadataRules

            ApplyOcrMetadataRules(
                document=self.job.document_file.document,
                ocr_text=extraction.text,
                actor=self.job.created_by,
            ).execute()
        transaction.on_commit(
            lambda: self._rebuild_document_search_index(),
        )
        return self.job

    def _rebuild_document_search_index(self) -> None:
        from doksio.search.services import RebuildDocumentSearchIndex

        RebuildDocumentSearchIndex(
            document=self.job.document_file.document,
        ).execute()
        from doksio.alarms.services import EvaluateDocumentAlarms

        EvaluateDocumentAlarms(
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

        title = title_from_ocr_policy(
            extraction.text,
            self.job.metadata.get("title_policy", {}),
        )
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
    title_policy: dict | None = None

    def execute(self) -> OcrJob:
        document_file = self._ocr_document_file()
        title_policy = self.title_policy
        if title_policy is None:
            from doksio.documents.title_rules import resolve_document_title_policy

            title_policy = resolve_document_title_policy(
                document_file.document.space,
            )
        job = CreateOcrJob(
            document_file=document_file,
            actor=self.actor,
            metadata={"title_policy": title_policy},
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

    def _ocr_document_file(self) -> DocumentFile:
        normalized_content_type = (
            self.document_file.content_type.split(";", 1)[0].strip().lower()
        )
        from doksio.documents.office_conversion import (
            office_pdf_derivative,
            supports_office_conversion,
        )

        if supports_office_conversion(normalized_content_type):
            converted_file = office_pdf_derivative(self.document_file)
            if converted_file is None:
                raise ValueError(
                    "Das Office-Dokument wurde noch nicht erfolgreich in PDF "
                    "konvertiert."
                )
            return converted_file
        if (
            self.document_file.file_kind == DocumentFile.Kind.ORIGINAL
            and normalized_content_type == "image/tiff"
        ):
            preview_file = (
                self.document_file.derivatives.filter(
                    file_kind=DocumentFile.Kind.PREVIEW,
                    content_type__startswith="image/",
                )
                .order_by("-created_at", "-id")
                .first()
            )
            if preview_file is not None:
                return preview_file
        return self.document_file
