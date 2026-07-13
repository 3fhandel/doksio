from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.documents.services import CreateDocumentFromUpload, CreateDocumentSpace
from doksio.ingestion.models import ImportJob, ImportSource
from doksio.ocr.models import OcrJob
from doksio.project.status import StatusCheck
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
