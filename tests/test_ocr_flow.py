from __future__ import annotations

import subprocess
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.audit.models import AuditEvent
from doksio.documents.models import Document, DocumentFile
from doksio.documents.services import CreateDocumentFromUpload, CreateDocumentSpace
from doksio.ocr.models import OcrJob
from doksio.ocr.services import (
    CreateOcrJob,
    LocalOcrProvider,
    OcrExtraction,
    RunOcrJob,
    StartOcrForDocumentFile,
    extract_document_title,
)
from doksio.tenancy.models import Tenant


class StaticOcrProvider:
    def extract(self, document_file):
        return OcrExtraction(
            text=f"Text aus {document_file.original_filename}",
            engine="test-provider",
            language="deu",
        )


def test_local_ocr_provider_auto_orients_images_before_tesseract(
    monkeypatch,
    tmp_path,
):
    input_path = tmp_path / "scan.jpg"
    input_path.write_bytes(b"image")
    commands = []

    def fake_which(command):
        return f"/usr/bin/{command}" if command in {"magick", "tesseract"} else None

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["/usr/bin/magick", "identify", "-format"]:
            return subprocess.CompletedProcess(command, 0, "2000 3000", "")
        if command[0].endswith("magick"):
            Path(command[-1]).write_bytes(b"prepared")
            return subprocess.CompletedProcess(command, 0, "", "")
        if str(tmp_path / "scan.ocr.form-ocr.png") in command:
            return subprocess.CompletedProcess(command, 0, "Mehr Formulartext", "")
        if str(tmp_path / "scan.ocr.form-ocr.detail-top-left.png") in command:
            return subprocess.CompletedProcess(command, 0, "Detailtext", "")
        return subprocess.CompletedProcess(command, 0, "Guter OCR-Text", "")

    monkeypatch.setattr("doksio.ocr.services.shutil.which", fake_which)
    monkeypatch.setattr("doksio.ocr.services.subprocess.run", fake_run)

    extraction = LocalOcrProvider()._extract_image(
        input_path=input_path,
        language="deu+eng",
    )

    assert "Guter OCR-Text" in extraction.text
    assert "Mehr Formulartext" in extraction.text
    assert "Detailtext" in extraction.text
    assert commands[0] == [
        "/usr/bin/magick",
        str(input_path),
        "-auto-orient",
        str(tmp_path / "scan.ocr.png"),
    ]
    assert commands[1][:3] == [
        "/usr/bin/tesseract",
        str(tmp_path / "scan.ocr.png"),
        "stdout",
    ]
    assert commands[2] == [
        "/usr/bin/magick",
        str(tmp_path / "scan.ocr.png"),
        "-colorspace",
        "Gray",
        "-normalize",
        "-sharpen",
        "0x1",
        "-density",
        "300",
        str(tmp_path / "scan.ocr.form-ocr.png"),
    ]
    assert commands[3][-2:] == ["--psm", "6"]
    assert commands[4] == [
        "/usr/bin/magick",
        "identify",
        "-format",
        "%w %h",
        str(tmp_path / "scan.ocr.form-ocr.png"),
    ]
    assert commands[5] == [
        "/usr/bin/magick",
        str(tmp_path / "scan.ocr.form-ocr.png"),
        "-crop",
        "1200x990+0+240",
        "+repage",
        "-resize",
        "250%",
        "-threshold",
        "70%",
        str(tmp_path / "scan.ocr.form-ocr.detail-top-left.png"),
    ]
    assert commands[6][-2:] == ["--psm", "6"]


@override_settings(
    OCR_IMAGE_MAX_EDGE=1000,
    OCR_IMAGE_MAX_PAGES=2,
    OCR_IMAGE_ENHANCED_MAX_PAGES=0,
    OCR_TESSERACT_TIMEOUT_SECONDS=9,
)
def test_local_ocr_provider_converts_tiff_pages_before_tesseract(
    monkeypatch,
    tmp_path,
):
    from PIL import Image

    input_path = tmp_path / "scan.tif"
    first_page = Image.new("RGB", (2400, 1200), "white")
    second_page = Image.new("RGB", (900, 600), "white")
    third_page = Image.new("RGB", (900, 600), "white")
    first_page.save(
        input_path,
        format="TIFF",
        save_all=True,
        append_images=[second_page, third_page],
    )
    commands = []
    timeouts = []

    def fake_which(command):
        return f"/usr/bin/{command}" if command == "tesseract" else None

    def fake_run(command, **kwargs):
        commands.append(command)
        timeouts.append(kwargs["timeout"])
        assert command[1].endswith(".png")
        return subprocess.CompletedProcess(command, 0, f"Text {len(commands)}", "")

    monkeypatch.setattr("doksio.ocr.services.shutil.which", fake_which)
    monkeypatch.setattr("doksio.ocr.services.subprocess.run", fake_run)

    extraction = LocalOcrProvider()._extract_image(
        input_path=input_path,
        language="deu+eng",
    )

    assert extraction.text.strip() == "Text 1\n\nText 2"
    assert len(commands) == 2
    assert all(str(input_path) not in command for command in commands)
    assert commands[0][1] == str(tmp_path / "scan.ocr-p001.png")
    assert commands[1][1] == str(tmp_path / "scan.ocr-p002.png")
    assert timeouts == [9, 9]
    with Image.open(tmp_path / "scan.ocr-p001.png") as prepared:
        assert max(prepared.size) == 1000


def test_extract_document_title_joins_hyphenated_line_breaks():
    assert (
        extract_document_title("Arbeitsunfähigkeits-\nbescheinigung\n\nWeitere Daten")
        == "Arbeitsunfähigkeitsbescheinigung"
    )


def test_extract_document_title_ignores_form_field_labels():
    assert (
        extract_document_title(
            "Name, Vorname des Versicherten\n"
            "Gutschner geb. am\n"
            "Arbeitsunfähigkeits-\n"
            "bescheinigung"
        )
        == "Arbeitsunfähigkeitsbescheinigung"
    )


@pytest.mark.django_db
def test_run_ocr_job_stores_extracted_text_and_audit_event():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(username="alice")
    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()
    job = CreateOcrJob(document_file=document_file, actor=user).execute()

    RunOcrJob(job=job, provider=StaticOcrProvider()).execute()

    job.refresh_from_db()
    assert job.status == OcrJob.Status.SUCCEEDED
    assert job.engine == "test-provider"
    assert job.extracted_text == "Text aus invoice.pdf"
    assert AuditEvent.objects.filter(event_type="ocr_job.succeeded").exists()


@pytest.mark.django_db
def test_run_ocr_job_prefills_document_date_from_extracted_text():
    class DatedOcrProvider:
        def extract(self, document_file):
            return OcrExtraction(
                text="Rechnungsdatum: 14.03.2026\nLeistung",
                engine="test-provider",
                language="deu",
            )

    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()
    job = CreateOcrJob(document_file=document_file).execute()

    RunOcrJob(job=job, provider=DatedOcrProvider()).execute()

    document_file.document.refresh_from_db()
    assert document_file.document.document_date == date(2026, 3, 14)
    assert AuditEvent.objects.filter(
        event_type="document_date.prefilled_from_ocr"
    ).exists()


@pytest.mark.django_db
def test_run_ocr_job_prefills_title_when_upload_title_was_empty():
    class TitledOcrProvider:
        def extract(self, document_file):
            return OcrExtraction(
                text="Betreff: Wartungsvertrag 2026\nBelegdatum: 14.03.2026",
                engine="test-provider",
                language="deu",
            )

    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Verträge").execute()
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="",
        space=space,
        file_obj=BytesIO(b"contract content"),
        original_filename="scan-001.pdf",
        content_type="application/pdf",
    ).execute()
    job = CreateOcrJob(document_file=document_file).execute()

    assert document.title == "scan-001"
    assert document.title_source == Document.TitleSource.FILENAME

    RunOcrJob(job=job, provider=TitledOcrProvider()).execute()

    document.refresh_from_db()
    assert document.title == "Wartungsvertrag 2026"
    assert document.title_source == Document.TitleSource.OCR
    assert AuditEvent.objects.filter(
        event_type="document_title.prefilled_from_ocr"
    ).exists()


@pytest.mark.django_db
def test_run_ocr_job_does_not_overwrite_manual_title():
    class TitledOcrProvider:
        def extract(self, document_file):
            return OcrExtraction(
                text="Betreff: OCR Titel",
                engine="test-provider",
                language="deu",
            )

    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Verträge").execute()
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Manueller Titel",
        space=space,
        file_obj=BytesIO(b"contract content"),
        original_filename="scan-001.pdf",
        content_type="application/pdf",
    ).execute()
    job = CreateOcrJob(document_file=document_file).execute()

    RunOcrJob(job=job, provider=TitledOcrProvider()).execute()

    document.refresh_from_db()
    assert document.title == "Manueller Titel"
    assert document.title_source == Document.TitleSource.MANUAL


@pytest.mark.django_db
def test_run_ocr_job_updates_existing_ocr_title():
    class BetterTitledOcrProvider:
        def extract(self, document_file):
            return OcrExtraction(
                text="Betreff: Besserer OCR Titel",
                engine="test-provider",
                language="deu",
            )

    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Verträge").execute()
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="",
        space=space,
        file_obj=BytesIO(b"contract content"),
        original_filename="scan-001.pdf",
        content_type="application/pdf",
    ).execute()
    document.title = "Schlechter OCR Titel"
    document.title_source = Document.TitleSource.OCR
    document.save(update_fields=["title", "title_source"])
    job = CreateOcrJob(document_file=document_file).execute()

    RunOcrJob(job=job, provider=BetterTitledOcrProvider()).execute()

    document.refresh_from_db()
    assert document.title == "Besserer OCR Titel"
    assert document.title_source == Document.TitleSource.OCR


@pytest.mark.django_db
def test_run_ocr_job_does_not_overwrite_existing_document_date():
    class DatedOcrProvider:
        def extract(self, document_file):
            return OcrExtraction(
                text="Belegdatum: 14.03.2026",
                engine="test-provider",
                language="deu",
            )

    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        document_date=date(2026, 1, 1),
    ).execute()
    job = CreateOcrJob(document_file=document_file).execute()

    RunOcrJob(job=job, provider=DatedOcrProvider()).execute()

    document_file.document.refresh_from_db()
    assert document_file.document.document_date == date(2026, 1, 1)


@pytest.mark.django_db
def test_run_ocr_job_records_failure():
    class FailingOcrProvider:
        def extract(self, document_file):
            raise RuntimeError("OCR kaputt")

    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()
    job = CreateOcrJob(document_file=document_file).execute()

    RunOcrJob(job=job, provider=FailingOcrProvider()).execute()

    job.refresh_from_db()
    assert job.status == OcrJob.Status.FAILED
    assert job.error_message == "OCR kaputt"
    assert AuditEvent.objects.filter(event_type="ocr_job.failed").exists()


@pytest.mark.django_db
@override_settings(OCR_AUTO_START_ON_UPLOAD=True, OCR_RUN_INLINE=True)
def test_create_document_from_upload_starts_ocr_automatically_for_supported_files(
    django_capture_on_commit_callbacks,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(username="alice")

    with django_capture_on_commit_callbacks(execute=True):
        _document, document_file = CreateDocumentFromUpload(
            tenant=tenant,
            title="Notiz",
            space=space,
            file_obj=BytesIO(b"Automatischer OCR-Text"),
            original_filename="notiz.txt",
            content_type="text/plain",
            created_by=user,
        ).execute()

    job = OcrJob.objects.get(document_file=document_file)
    assert job.status == OcrJob.Status.SUCCEEDED
    assert job.extracted_text == "Automatischer OCR-Text"


@pytest.mark.django_db
@override_settings(OCR_AUTO_START_ON_UPLOAD=True, OCR_RUN_INLINE=True)
def test_create_document_from_upload_skips_automatic_ocr_for_unsupported_files():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()

    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Tabelle",
        space=space,
        file_obj=BytesIO(b"spreadsheet"),
        original_filename="tabelle.xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    ).execute()

    assert not OcrJob.objects.filter(document_file=document_file).exists()


@pytest.mark.django_db
def test_start_ocr_for_tiff_uses_jpeg_preview_derivative(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Scans").execute()
    monkeypatch.setattr(
        "doksio.documents.thumbnails._render_thumbnail_bytes",
        lambda _document_file: b"thumbnail-bytes",
    )
    monkeypatch.setattr(
        "doksio.documents.thumbnails._render_image_preview",
        lambda _document_file: b"preview-jpeg-bytes",
    )
    monkeypatch.setattr("doksio.ocr.tasks.run_ocr_job.delay", lambda _job_id: None)
    _document, original_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Scan",
        space=space,
        file_obj=BytesIO(b"tiff content"),
        original_filename="scan.tif",
        content_type="image/tiff",
        auto_start_ocr=False,
    ).execute()
    preview_file = DocumentFile.objects.get(
        document=original_file.document,
        file_kind=DocumentFile.Kind.PREVIEW,
    )

    job = StartOcrForDocumentFile(
        document_file=original_file,
        run_inline=False,
    ).execute()

    assert job.document_file == preview_file
    assert job.document_file.content_type == "image/jpeg"


@pytest.mark.django_db
@override_settings(OCR_RUN_INLINE=True)
def test_document_detail_can_start_ocr_for_text_file(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Notiz",
        space=space,
        file_obj=BytesIO(b"Hallo OCR"),
        original_filename="notiz.txt",
        content_type="text/plain",
        created_by=user,
    ).execute()
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "start_ocr",
            "file_id": document_file.id,
        },
    )

    job = OcrJob.objects.get(document_file=document_file)
    assert response.status_code == 302
    assert job.status == OcrJob.Status.SUCCEEDED
    assert job.extracted_text == "Hallo OCR"
