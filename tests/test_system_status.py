from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.documents.services import CreateDocumentFromUpload, CreateDocumentSpace
from doksio.ingestion.models import ImportJob, ImportSource
from doksio.ocr.models import OcrJob
from doksio.project.status import StatusCheck, build_system_status
from doksio.tenancy.models import Tenant


def _disable_external_checks(monkeypatch):
    monkeypatch.setattr(
        "doksio.project.status._redis_check",
        lambda: StatusCheck("broker", "Redis/Broker", "ok", "Erreichbar"),
    )
    monkeypatch.setattr(
        "doksio.project.status._worker_check",
        lambda: StatusCheck("worker", "Celery Worker", "ok", "1 Worker aktiv"),
    )


@pytest.mark.django_db
def test_system_admin_can_view_global_status_page(client, monkeypatch):
    _disable_external_checks(monkeypatch)
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API",
        auto_start_ocr=False,
    )
    ImportJob.objects.create(
        tenant=tenant,
        source=source,
        document_space=space,
        original_filename="kaputt.pdf",
        content_type="application/pdf",
        status=ImportJob.Status.FAILED,
        message="Testfehler",
    )
    admin = get_user_model().objects.create_superuser(
        username="admin",
        password="secret",
    )
    client.force_login(admin)

    response = client.get("/s/status/")

    content = response.content.decode()
    assert response.status_code == 200
    assert "Globaler Betriebszustand" in content
    assert "Datenbank" in content
    assert "Redis/Broker" in content
    assert "Doksio belegt" in content
    assert "Nicht über S3 verfügbar" in content
    assert "kaputt.pdf" in content


@pytest.mark.django_db
def test_tenant_admin_can_view_tenant_scoped_status_page(client, monkeypatch):
    _disable_external_checks(monkeypatch)
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    other_tenant = Tenant.objects.create(name="Other GmbH", slug="other")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    other_space = CreateDocumentSpace(tenant=other_tenant, name="Rechnungen").execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="admin",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["admin"],
    )
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Beleg",
        space=space,
        file_obj=BytesIO(b"content"),
        original_filename="beleg.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()
    OcrJob.objects.create(
        tenant=tenant,
        document_file=document_file,
        status=OcrJob.Status.RUNNING,
    )
    ImportJob.objects.create(
        tenant=other_tenant,
        document_space=other_space,
        original_filename="fremd.pdf",
        content_type="application/pdf",
        status=ImportJob.Status.FAILED,
        message="Fremder Fehler",
    )
    client.force_login(user)

    response = client.get(f"/t/{tenant.slug}/status/")

    content = response.content.decode()
    assert response.status_code == 200
    assert "Betriebszustand und Verarbeitung für Acme GmbH" in content
    assert "OCR läuft" in content
    assert "Doksio belegt" in content
    assert document.title in content or "1" in content
    assert "fremd.pdf" not in content


@pytest.mark.django_db
def test_non_admin_tenant_user_cannot_view_tenant_status_page(client, monkeypatch):
    _disable_external_checks(monkeypatch)
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="viewer",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["viewer"],
    )
    client.force_login(user)

    response = client.get(f"/t/{tenant.slug}/status/")

    assert response.status_code == 403


@pytest.mark.django_db
def test_status_storage_usage_is_tenant_scoped(monkeypatch):
    _disable_external_checks(monkeypatch)
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    other_tenant = Tenant.objects.create(name="Other GmbH", slug="other")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    other_space = CreateDocumentSpace(tenant=other_tenant, name="Rechnungen").execute()

    CreateDocumentFromUpload(
        tenant=tenant,
        title="Beleg",
        space=space,
        file_obj=BytesIO(b"12345"),
        original_filename="beleg.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()
    CreateDocumentFromUpload(
        tenant=other_tenant,
        title="Fremd",
        space=other_space,
        file_obj=BytesIO(b"1234567890"),
        original_filename="fremd.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()

    global_status = build_system_status()
    tenant_status = build_system_status(tenant=tenant)

    assert global_status["storage"]["used_bytes"] == 15
    assert global_status["storage"]["used_human"] == "15 B"
    assert tenant_status["storage"]["used_bytes"] == 5
    assert tenant_status["storage"]["used_human"] == "5 B"


@pytest.mark.django_db
def test_status_treats_old_running_ocr_jobs_as_stale(monkeypatch):
    _disable_external_checks(monkeypatch)
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Beleg",
        space=space,
        file_obj=BytesIO(b"content"),
        original_filename="beleg.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()
    stale_job = OcrJob.objects.create(
        tenant=tenant,
        document_file=document_file,
        status=OcrJob.Status.RUNNING,
        started_at=timezone.now() - timezone.timedelta(hours=2),
    )
    OcrJob.objects.filter(id=stale_job.id).update(
        updated_at=timezone.now() - timezone.timedelta(hours=2),
    )

    status = build_system_status(tenant=tenant)

    assert status["ocr"]["running"] == 0
    assert status["ocr"]["stale_running"] == 1
    assert {
        "status": "stale_running",
        "label": "Verwaist",
        "count": 1,
    } in status["ocr"]["status_rows"]
