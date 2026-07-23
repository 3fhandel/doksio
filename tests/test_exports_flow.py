from __future__ import annotations

from datetime import date
from io import BytesIO
from zipfile import ZipFile

import pytest
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from doksio.accounts.models import TenantMembership
from doksio.accounts.permissions import TenantPermissions
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.audit.models import AuditEvent
from doksio.documents.services import CreateDocumentFromUpload, CreateDocumentSpace
from doksio.exports.models import ExportRun, ExportRunItem
from doksio.exports.tasks import build_document_image_export
from doksio.tenancy.models import Tenant


@pytest.mark.django_db
def test_document_image_export_starts_zip_export_for_enabled_boxes(
    client,
    monkeypatch,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        datev_document_image_export_enabled=True,
    ).execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    roles["member"].permissions.add(
        roles["admin"].permissions.get(code=TenantPermissions.DOCUMENTS_EXPORT)
    )
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["admin"],
    )
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        space=space,
        file_obj=SimpleUploadedFile(
            "rechnung.pdf",
            b"invoice content",
            content_type="application/pdf",
        ),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
        title="Rechnung 4711",
        created_by=user,
        auto_start_ocr=False,
        auto_extract_einvoice=False,
        auto_start_workflows=False,
    ).execute()
    document.document_date = date(2026, 7, 12)
    document.save(update_fields=["document_date", "updated_at"])
    client.force_login(user)
    monkeypatch.setattr(
        "doksio.exports.views.build_document_image_export.delay",
        lambda export_run_id: build_document_image_export(export_run_id),
    )

    response = client.post(
        reverse("exports:document_images", kwargs={"tenant_slug": tenant.slug}),
        {
            "document_space": space.id,
            "include_children": "on",
        },
    )

    assert response.status_code == 302

    export_run = ExportRun.objects.get()
    assert export_run.status == ExportRun.Status.COMPLETED
    assert export_run.exported_count == 1
    assert export_run.warning_count == 0
    assert export_run.filename
    assert export_run.storage_key
    assert len(export_run.sha256) == 64
    assert default_storage.exists(export_run.storage_key)
    with default_storage.open(export_run.storage_key, "rb") as package_file:
        package_content = package_file.read()
    with ZipFile(BytesIO(package_content)) as archive:
        names = archive.namelist()
        assert "manifest.csv" in names
        assert "export-log.csv" in names
        beleg_names = [name for name in names if name.startswith("belege/")]
        assert len(beleg_names) == 1
        assert archive.read(beleg_names[0]) == b"invoice content"
        manifest = archive.read("manifest.csv").decode("utf-8-sig")
        assert "Rechnung 4711" in manifest
        assert "rechnung.pdf" in manifest
        assert str(document.id) in manifest

    assert export_run.byte_size == len(package_content)
    assert ExportRunItem.objects.filter(
        export_run=export_run,
        document=document,
        status=ExportRunItem.Status.EXPORTED,
    ).exists()
    assert AuditEvent.objects.filter(event_type="export_run.created").exists()
    assert AuditEvent.objects.filter(
        event_type="document.exported",
        object_id=str(document.id),
    ).exists()

    audit_response = client.get(
        reverse("documents:audit_log", kwargs={"tenant_slug": tenant.slug})
    )
    assert audit_response.status_code == 200
    audit_content = audit_response.content.decode()
    assert "Exportlauf erzeugt" in audit_content
    assert "Dokument exportiert" in audit_content

    list_response = client.get(
        reverse("exports:document_images", kwargs={"tenant_slug": tenant.slug})
    )
    assert list_response.status_code == 200
    list_content = list_response.content.decode()
    assert "Herunterladen" in list_content
    assert export_run.filename in list_content

    download_response = client.get(
        reverse(
            "exports:run_download",
            kwargs={"tenant_slug": tenant.slug, "export_run_id": export_run.id},
        )
    )
    assert download_response.status_code == 200
    assert download_response["Content-Type"] == "application/zip"
    downloaded_content = b"".join(download_response.streaming_content)
    assert downloaded_content == package_content
    assert AuditEvent.objects.filter(event_type="export_run.downloaded").exists()


@pytest.mark.django_db
def test_document_image_export_ignores_boxes_without_export_flag(
    client,
    monkeypatch,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    enabled_space = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        datev_document_image_export_enabled=True,
    ).execute()
    disabled_space = CreateDocumentSpace(
        tenant=tenant,
        name="Personal",
        datev_document_image_export_enabled=False,
    ).execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    roles["member"].permissions.add(
        roles["admin"].permissions.get(code=TenantPermissions.DOCUMENTS_EXPORT)
    )
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    CreateDocumentFromUpload(
        tenant=tenant,
        space=enabled_space,
        file_obj=SimpleUploadedFile("rechnung.pdf", b"invoice"),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
        title="Rechnung",
        created_by=user,
        auto_start_ocr=False,
        auto_extract_einvoice=False,
        auto_start_workflows=False,
    ).execute()
    disabled_document, _disabled_file = CreateDocumentFromUpload(
        tenant=tenant,
        space=disabled_space,
        file_obj=SimpleUploadedFile("personal.pdf", b"personnel"),
        original_filename="personal.pdf",
        content_type="application/pdf",
        title="Personalakte",
        created_by=user,
        auto_start_ocr=False,
        auto_extract_einvoice=False,
        auto_start_workflows=False,
    ).execute()
    client.force_login(user)
    monkeypatch.setattr(
        "doksio.exports.views.build_document_image_export.delay",
        lambda export_run_id: build_document_image_export(export_run_id),
    )

    response = client.post(
        reverse("exports:document_images", kwargs={"tenant_slug": tenant.slug}),
        {},
    )

    assert response.status_code == 302
    export_run = ExportRun.objects.get()
    with default_storage.open(export_run.storage_key, "rb") as package_file:
        package_content = package_file.read()
    with ZipFile(BytesIO(package_content)) as archive:
        manifest = archive.read("manifest.csv").decode("utf-8-sig")
        assert "Rechnung" in manifest
        assert "Personalakte" not in manifest

    assert not ExportRunItem.objects.filter(document=disabled_document).exists()


@pytest.mark.django_db
def test_document_image_export_requires_export_permission(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["viewer"],
    )
    client.force_login(user)

    response = client.get(
        reverse("exports:document_images", kwargs={"tenant_slug": tenant.slug})
    )
    sidebar_response = client.get(
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 403
    assert "Exporte" not in sidebar_response.content.decode()


@pytest.mark.django_db
def test_document_image_export_does_not_export_same_document_twice(
    client,
    monkeypatch,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        datev_document_image_export_enabled=True,
    ).execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    roles["member"].permissions.add(
        roles["admin"].permissions.get(code=TenantPermissions.DOCUMENTS_EXPORT)
    )
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        space=space,
        file_obj=SimpleUploadedFile("rechnung.pdf", b"invoice"),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
        title="Rechnung",
        created_by=user,
        auto_start_ocr=False,
        auto_extract_einvoice=False,
        auto_start_workflows=False,
    ).execute()
    client.force_login(user)
    monkeypatch.setattr(
        "doksio.exports.views.build_document_image_export.delay",
        lambda export_run_id: build_document_image_export(export_run_id),
    )
    url = reverse("exports:document_images", kwargs={"tenant_slug": tenant.slug})

    first_response = client.post(url, {"document_space": space.id})
    second_response = client.post(url, {"document_space": space.id})

    assert first_response.status_code == 302
    assert second_response.status_code == 200
    assert second_response["Content-Type"].startswith("text/html")
    assert "keine exportierbaren Dokumente" in second_response.content.decode()
    assert ExportRun.objects.count() == 1
    assert ExportRunItem.objects.filter(
        document=document,
        status=ExportRunItem.Status.EXPORTED,
    ).count() == 1
