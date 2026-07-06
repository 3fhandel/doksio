from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from domasy.accounts.models import TenantMembership
from domasy.accounts.services import EnsureDefaultTenantRoles
from domasy.audit.models import AuditEvent
from domasy.documents.models import Document
from domasy.documents.services import CreateDocumentFromUpload, CreateDocumentSpace
from domasy.ocr.models import OcrJob
from domasy.ocr.services import (
    CreateOcrJob,
    OcrExtraction,
    RunOcrJob,
)
from domasy.tenancy.models import Tenant


class StaticOcrProvider:
    def extract(self, document_file):
        return OcrExtraction(
            text=f"Text aus {document_file.original_filename}",
            engine="test-provider",
            language="deu",
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
