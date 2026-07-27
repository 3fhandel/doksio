from __future__ import annotations

import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from django.core.files.storage import default_storage

from doksio.audit.models import AuditEvent
from doksio.documents.models import (
    DocumentFile,
    DocumentOfficeConversionJob,
)
from doksio.documents.office_conversion import (
    RunOfficeConversion,
    StartOfficeConversion,
    supports_office_conversion,
)
from doksio.documents.services import CreateDocumentFromUpload, CreateDocumentSpace
from doksio.documents.views import _document_preview
from doksio.storage.services import StoreImmutableFile
from doksio.tenancy.models import Tenant

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@pytest.fixture(autouse=True)
def prevent_queued_office_tasks(monkeypatch):
    monkeypatch.setattr(
        "doksio.documents.tasks.convert_office_document.delay",
        lambda _job_id: None,
    )


def _fake_libreoffice_run(command, **_kwargs):
    output_directory = Path(command[command.index("--outdir") + 1])
    input_path = Path(command[-1])
    (output_directory / f"{input_path.stem}.pdf").write_bytes(
        b"%PDF-1.4\nconverted office document"
    )
    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


@pytest.mark.django_db
def test_office_conversion_preserves_original_and_stores_pdf_derivative(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Dokumente").execute()
    _document, source_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Schreiben",
        space=space,
        file_obj=BytesIO(b"original docx bytes"),
        original_filename="schreiben.docx",
        content_type=DOCX_CONTENT_TYPE,
        auto_start_ocr=False,
    ).execute()
    monkeypatch.setattr(
        "doksio.documents.office_conversion.shutil.which",
        lambda _name: "/usr/bin/soffice",
    )
    monkeypatch.setattr(
        "doksio.documents.office_conversion.subprocess.run",
        _fake_libreoffice_run,
    )
    monkeypatch.setattr(
        "doksio.documents.office_conversion.create_thumbnail_for_document_file",
        lambda *_args, **_kwargs: None,
    )

    job = StartOfficeConversion(
        source_file=source_file,
        auto_start_ocr=False,
        run_inline=True,
    ).execute()

    source_file.refresh_from_db()
    job.refresh_from_db()
    output_file = job.output_file
    assert job.status == DocumentOfficeConversionJob.Status.SUCCEEDED
    assert output_file is not None
    assert output_file.file_kind == DocumentFile.Kind.DERIVATIVE
    assert output_file.content_type == "application/pdf"
    assert output_file.original_filename == "schreiben.pdf"
    assert output_file.derivative_of == source_file
    with default_storage.open(source_file.storage_key, "rb") as stored_original:
        assert stored_original.read() == b"original docx bytes"
    with default_storage.open(output_file.storage_key, "rb") as stored_pdf:
        assert stored_pdf.read().startswith(b"%PDF-")
    assert AuditEvent.objects.filter(
        tenant=tenant,
        event_type="document_office_conversion.succeeded",
        object_id=str(job.id),
    ).exists()


@pytest.mark.django_db
def test_office_conversion_reuses_existing_pdf_derivative(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Dokumente").execute()
    _document, source_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Schreiben",
        space=space,
        file_obj=BytesIO(b"original odt bytes"),
        original_filename="schreiben.odt",
        content_type="application/vnd.oasis.opendocument.text",
        auto_start_ocr=False,
    ).execute()
    monkeypatch.setattr(
        "doksio.documents.office_conversion.shutil.which",
        lambda _name: "/usr/bin/soffice",
    )
    monkeypatch.setattr(
        "doksio.documents.office_conversion.subprocess.run",
        _fake_libreoffice_run,
    )
    monkeypatch.setattr(
        "doksio.documents.office_conversion.create_thumbnail_for_document_file",
        lambda *_args, **_kwargs: None,
    )
    job = StartOfficeConversion(
        source_file=source_file,
        auto_start_ocr=False,
        run_inline=True,
    ).execute()
    output_file_id = job.output_file_id

    rerun_job = RunOfficeConversion(job=job).execute()

    assert rerun_job.output_file_id == output_file_id
    assert source_file.derivatives.filter(
        file_kind=DocumentFile.Kind.DERIVATIVE,
        content_type="application/pdf",
    ).count() == 1


@pytest.mark.parametrize(
    "content_type",
    [
        "application/msword",
        "application/vnd.oasis.opendocument.text",
        DOCX_CONTENT_TYPE,
    ],
)
def test_supported_office_content_types(content_type):
    assert supports_office_conversion(content_type)


def test_spreadsheets_are_not_treated_as_supported_office_documents():
    assert not supports_office_conversion(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@pytest.mark.django_db
def test_office_upload_queues_conversion_after_commit(
    django_capture_on_commit_callbacks,
    monkeypatch,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Dokumente").execute()
    queued_job_ids = []
    monkeypatch.setattr(
        "doksio.documents.tasks.convert_office_document.delay",
        queued_job_ids.append,
    )

    with django_capture_on_commit_callbacks(execute=True):
        _document, source_file = CreateDocumentFromUpload(
            tenant=tenant,
            title="Schreiben",
            space=space,
            file_obj=BytesIO(b"original docx bytes"),
            original_filename="schreiben.docx",
            content_type=DOCX_CONTENT_TYPE,
            auto_start_ocr=True,
        ).execute()

    job = DocumentOfficeConversionJob.objects.get(source_file=source_file)
    assert job.status == DocumentOfficeConversionJob.Status.PENDING
    assert job.auto_start_ocr is True
    assert queued_job_ids == [job.id]


@pytest.mark.django_db
def test_document_preview_uses_converted_office_pdf():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Dokumente").execute()
    document, source_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Schreiben",
        space=space,
        file_obj=BytesIO(b"original docx bytes"),
        original_filename="schreiben.docx",
        content_type=DOCX_CONTENT_TYPE,
        auto_start_ocr=False,
    ).execute()
    converted_file = StoreImmutableFile(
        tenant=tenant,
        document=document,
        file_obj=BytesIO(b"%PDF-1.4\nconverted"),
        original_filename="schreiben.pdf",
        content_type="application/pdf",
        file_kind=DocumentFile.Kind.DERIVATIVE,
        derivative_of=source_file,
    ).execute()

    preview_file, preview_kind = _document_preview(document)

    assert preview_file == converted_file
    assert preview_kind == "pdf"


@pytest.mark.django_db
def test_successful_office_conversion_starts_ocr_for_pdf_derivative(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Dokumente").execute()
    _document, source_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Schreiben",
        space=space,
        file_obj=BytesIO(b"original docx bytes"),
        original_filename="schreiben.docx",
        content_type=DOCX_CONTENT_TYPE,
        auto_start_ocr=False,
    ).execute()
    monkeypatch.setattr(
        "doksio.documents.office_conversion.shutil.which",
        lambda _name: "/usr/bin/soffice",
    )
    monkeypatch.setattr(
        "doksio.documents.office_conversion.subprocess.run",
        _fake_libreoffice_run,
    )
    monkeypatch.setattr(
        "doksio.documents.office_conversion.create_thumbnail_for_document_file",
        lambda *_args, **_kwargs: None,
    )
    ocr_files = []
    monkeypatch.setattr(
        "doksio.ocr.services.StartOcrForDocumentFile.execute",
        lambda service: ocr_files.append(service.document_file),
    )

    StartOfficeConversion(
        source_file=source_file,
        auto_start_ocr=True,
        run_inline=True,
    ).execute()

    assert len(ocr_files) == 1
    assert ocr_files[0].file_kind == DocumentFile.Kind.DERIVATIVE
    assert ocr_files[0].content_type == "application/pdf"
    assert ocr_files[0].derivative_of == source_file
