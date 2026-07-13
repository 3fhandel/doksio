from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.audit.models import AuditEvent
from doksio.documents.models import Document, DocumentFile, DocumentTagAssignment
from doksio.documents.services import CreateDocumentSpace, DuplicateDocumentError
from doksio.ingestion.models import ImportJob, ImportSource, TenantSmtpSettings
from doksio.ingestion.services import ImportDocument
from doksio.ocr.models import OcrJob
from doksio.tenancy.models import Tenant

MINIMAL_PDF_BYTES = b"%PDF-1.4\n% Doksio test PDF\n%%EOF\n"


@pytest.mark.django_db
def test_import_document_creates_document_job_and_default_tags():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        default_tags=["api", "eingang"],
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    document, import_job = ImportDocument(
        tenant=tenant,
        source=source,
        document_space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
    ).execute()

    assert document.title == "rechnung"
    assert document.space == space
    assert import_job.status == ImportJob.Status.IMPORTED
    assert import_job.document == document
    assert set(
        DocumentTagAssignment.objects.values_list("tag__name", flat=True)
    ) == {"api", "eingang"}


@pytest.mark.django_db
def test_import_document_rejects_duplicate_file_by_checksum():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    ImportDocument(
        tenant=tenant,
        source=source,
        document_space=space,
        file_obj=BytesIO(b"same invoice content"),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
    ).execute()

    with pytest.raises(DuplicateDocumentError):
        ImportDocument(
            tenant=tenant,
            source=source,
            document_space=space,
            file_obj=BytesIO(b"same invoice content"),
            original_filename="rechnung-kopie.pdf",
            content_type="application/pdf",
        ).execute()

    assert Document.objects.count() == 1
    assert ImportJob.objects.filter(status=ImportJob.Status.FAILED).count() == 1
    duplicate_event = AuditEvent.objects.get(
        event_type="document_duplicate.detected"
    )
    assert duplicate_event.data["original_filename"] == "rechnung-kopie.pdf"


@pytest.mark.django_db
def test_http_import_endpoint_imports_with_valid_token(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        token="secret-token",
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    response = client.put(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=MINIMAL_PDF_BYTES,
        content_type="application/pdf",
        headers={
            "X-Doksio-Import-Token": "secret-token",
            "X-Doksio-Filename": "rechnung.pdf",
        },
    )

    document = Document.objects.get()
    import_job = ImportJob.objects.get()
    assert response.status_code == 201
    assert "/api/v1/import/" in reverse(
        "ingestion:http_import",
        kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
    )
    assert response.json()["document_id"] == document.id
    assert document.title == "rechnung"
    assert import_job.status == ImportJob.Status.IMPORTED


@pytest.mark.django_db
def test_folder_import_source_can_upload_through_api_endpoint(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Scan Ordner",
        source_type=ImportSource.SourceType.FOLDER,
        token="secret-token",
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    response = client.put(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=MINIMAL_PDF_BYTES,
        content_type="application/pdf",
        headers={
            "X-Doksio-Import-Token": "secret-token",
            "X-Doksio-Filename": "rechnung.pdf",
        },
    )

    assert response.status_code == 201
    assert Document.objects.get().title == "rechnung"
    assert ImportJob.objects.get().source == source


@pytest.mark.django_db
def test_http_import_endpoint_generates_filename_when_header_is_missing(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        token="secret-token",
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    response = client.put(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=MINIMAL_PDF_BYTES,
        content_type="application/pdf",
        headers={"X-Doksio-Import-Token": "secret-token"},
    )

    document = Document.objects.get()
    document_file = DocumentFile.objects.get(document=document)
    assert response.status_code == 201
    assert document.title.startswith("api-import-")
    assert document.title_source == Document.TitleSource.FILENAME
    assert document_file.original_filename.startswith("api-import-")
    assert document_file.original_filename.endswith(".pdf")


@pytest.mark.django_db
def test_http_import_endpoint_detects_content_type_when_header_is_generic(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        token="secret-token",
        settings={
            "common": {
                "allowed_content_types": ["application/pdf"],
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    response = client.put(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=MINIMAL_PDF_BYTES,
        content_type="application/octet-stream",
        headers={
            "X-Doksio-Import-Token": "secret-token",
            "X-Doksio-Filename": "rechnung.pdf",
        },
    )

    document_file = DocumentFile.objects.get()
    import_job = ImportJob.objects.get()
    assert response.status_code == 201
    assert document_file.content_type == "application/pdf"
    assert import_job.content_type == "application/pdf"


@pytest.mark.django_db
def test_http_import_endpoint_detects_content_type_when_header_is_form_encoded(
    client,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        token="secret-token",
        settings={
            "common": {
                "allowed_content_types": ["application/pdf"],
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    response = client.put(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=MINIMAL_PDF_BYTES,
        content_type="application/x-www-form-urlencoded",
        headers={
            "X-Doksio-Import-Token": "secret-token",
            "X-Doksio-Filename": "rechnung.pdf",
        },
    )

    document_file = DocumentFile.objects.get()
    import_job = ImportJob.objects.get()
    assert response.status_code == 201
    assert document_file.content_type == "application/pdf"
    assert import_job.content_type == "application/pdf"


@pytest.mark.django_db
def test_http_import_endpoint_detects_content_type_from_body_without_filename(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        token="secret-token",
        settings={
            "common": {
                "allowed_content_types": ["application/pdf"],
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    response = client.put(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=MINIMAL_PDF_BYTES,
        content_type="application/octet-stream",
        headers={"X-Doksio-Import-Token": "secret-token"},
    )

    document_file = DocumentFile.objects.get()
    assert response.status_code == 201
    assert document_file.content_type == "application/pdf"
    assert document_file.original_filename.startswith("api-import-")
    assert document_file.original_filename.endswith(".pdf")


@pytest.mark.django_db
def test_http_import_endpoint_detects_content_type_from_form_encoded_body(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        token="secret-token",
        settings={
            "common": {
                "allowed_content_types": ["application/pdf"],
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    response = client.put(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=MINIMAL_PDF_BYTES,
        content_type="application/x-www-form-urlencoded",
        headers={"X-Doksio-Import-Token": "secret-token"},
    )

    document_file = DocumentFile.objects.get()
    assert response.status_code == 201
    assert document_file.content_type == "application/pdf"
    assert document_file.original_filename.endswith(".pdf")


@pytest.mark.django_db
def test_http_import_endpoint_rejects_pdf_content_type_with_non_pdf_body(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        token="secret-token",
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    response = client.put(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=b"Zwischen.pdf",
        content_type="application/pdf",
        headers={"X-Doksio-Import-Token": "secret-token"},
    )

    assert response.status_code == 400
    assert "kein gültiges PDF" in response.json()["error"]
    assert "--data-binary" in response.json()["error"]
    assert not ImportJob.objects.exists()
    assert not Document.objects.exists()


@pytest.mark.django_db
def test_http_import_endpoint_rejects_invalid_token(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        token="secret-token",
    )

    response = client.put(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=MINIMAL_PDF_BYTES,
        content_type="application/pdf",
        headers={"X-Doksio-Import-Token": "wrong"},
    )

    assert response.status_code == 403
    assert Document.objects.count() == 0
    assert ImportJob.objects.count() == 0


@pytest.mark.django_db
@override_settings(DOKSIO_PUBLIC_BASE_URL="https://doksio.example.test")
def test_tenant_admin_can_create_import_source_from_import_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:settings_import_source_create",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "name": "API Eingang",
            "source_type": ImportSource.SourceType.HTTP_API,
            "target_strategy": ImportSource.TargetStrategy.FIXED,
            "document_space": str(space.id),
            "allowed_content_types_text": "application/pdf",
            "max_file_size_mb": "25",
            "auto_start_ocr": "on",
            "start_workflows": "on",
            "default_tags_text": "api\neingang",
            "is_active": "on",
        },
    )

    import_source = ImportSource.objects.get()
    assert response.status_code == 302
    assert import_source.document_space == space
    assert import_source.default_tags == ["api", "eingang"]
    assert import_source.settings == {
        "title": {
            "strategy": ImportSource.OcrTitleStrategy.AUTOMATIC,
            "regex_search": "",
            "regex_replace": "",
        },
        "common": {
            "max_file_size_mb": 25,
            "allowed_content_types": ["application/pdf"],
        },
    }

    response = client.get(
        reverse(
            "documents:settings_import_sources",
            kwargs={"tenant_slug": tenant.slug},
        )
    )
    content = response.content.decode()
    assert "Import" in content
    assert "API Eingang" in content
    assert space.path in content
    assert reverse(
        "ingestion:http_import",
        kwargs={"tenant_slug": tenant.slug, "source_id": import_source.id},
    ) in content

    response = client.get(
        reverse(
            "documents:settings_import_source_edit",
            kwargs={"tenant_slug": tenant.slug, "source_id": import_source.id},
        )
    )
    content = response.content.decode()
    absolute_import_url = "https://doksio.example.test" + reverse(
        "ingestion:http_import",
        kwargs={"tenant_slug": tenant.slug, "source_id": import_source.id},
    )
    assert absolute_import_url in content
    assert "http://testserver" not in content
    assert '--header "X-Doksio-Filename' not in content
    assert "Dateiname-Header optional" in content


@pytest.mark.django_db
def test_import_source_form_highlights_missing_required_fields(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
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
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:settings_import_source_create",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "name": "API Eingang",
            "source_type": ImportSource.SourceType.HTTP_API,
            "target_strategy": ImportSource.TargetStrategy.FIXED,
            "is_active": "on",
        },
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Bitte prüfe die markierten Felder." in content
    assert "Ziel-Dokumentenbox" in content


@pytest.mark.django_db
def test_tenant_admin_can_create_import_source_with_routing_rules(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    fallback_space = CreateDocumentSpace(tenant=tenant, name="Posteingang").execute()
    invoice_space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:settings_import_source_create",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "name": "API Eingang",
            "source_type": ImportSource.SourceType.HTTP_API,
            "target_strategy": ImportSource.TargetStrategy.RULES,
            "document_space": str(fallback_space.id),
            "routing_rules_text": f"rechnung-*.pdf => {invoice_space.path}",
            "is_active": "on",
        },
    )

    import_source = ImportSource.objects.get()
    assert response.status_code == 302
    assert import_source.target_strategy == ImportSource.TargetStrategy.RULES
    assert import_source.document_space == fallback_space
    assert import_source.settings["routing_rules"] == [
        {
            "pattern": "rechnung-*.pdf",
            "document_space_id": invoice_space.id,
            "document_space_path": invoice_space.path,
        }
    ]


@pytest.mark.django_db
def test_http_import_endpoint_uses_fixed_headers_and_configured_filters(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        token="secret-token",
        settings={
            "common": {
                "max_file_size_mb": 1,
                "allowed_content_types": ["application/pdf"],
            },
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    response = client.post(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=MINIMAL_PDF_BYTES,
        content_type="application/pdf",
        headers={
            "X-Doksio-Import-Token": "secret-token",
            "X-Doksio-Filename": "rechnung.pdf",
        },
    )

    document = Document.objects.get()
    assert response.status_code == 201
    assert document.title == "rechnung"

    response = client.post(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=b"image content",
        content_type="image/png",
        headers={
            "X-Doksio-Import-Token": "secret-token",
            "X-Doksio-Filename": "rechnung.png",
        },
    )

    assert response.status_code == 415


@pytest.mark.django_db
def test_http_import_endpoint_routes_document_space_by_filename_rule(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    fallback_space = CreateDocumentSpace(tenant=tenant, name="Posteingang").execute()
    invoice_space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=fallback_space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        target_strategy=ImportSource.TargetStrategy.RULES,
        token="secret-token",
        settings={
            "routing_rules": [
                {
                    "pattern": "rechnung-*.pdf",
                    "document_space_id": invoice_space.id,
                    "document_space_path": invoice_space.path,
                }
            ]
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    response = client.post(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
        ),
        data=MINIMAL_PDF_BYTES,
        content_type="application/pdf",
        headers={
            "X-Doksio-Import-Token": "secret-token",
            "X-Doksio-Filename": "rechnung-2026.pdf",
        },
    )

    document = Document.objects.get()
    import_job = ImportJob.objects.get()
    assert response.status_code == 201
    assert document.space == invoice_space
    assert import_job.document_space == invoice_space


@pytest.mark.django_db(transaction=True)
def test_import_document_passes_source_ocr_title_policy_to_ocr_job(monkeypatch):
    monkeypatch.setattr("doksio.ocr.tasks.run_ocr_job.delay", lambda job_id: None)
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="API Eingang",
        source_type=ImportSource.SourceType.HTTP_API,
        settings={
            "title": {
                "strategy": ImportSource.OcrTitleStrategy.REGEX,
                "regex_search": r"Rechnung Nr\. (\d+)",
                "regex_replace": r"Rechnung \1",
            }
        },
        auto_start_ocr=True,
        extract_einvoice=False,
        start_workflows=False,
    )

    document, _import_job = ImportDocument(
        tenant=tenant,
        source=source,
        document_space=space,
        file_obj=BytesIO(MINIMAL_PDF_BYTES),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
    ).execute()

    ocr_job = OcrJob.objects.get(document_file__document=document)
    assert ocr_job.metadata["title_policy"] == source.settings["title"]


@pytest.mark.django_db
def test_tenant_admin_can_create_folder_and_email_import_source_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:settings_import_source_create",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "name": "Scan Ordner",
            "source_type": ImportSource.SourceType.FOLDER,
            "target_strategy": ImportSource.TargetStrategy.FIXED,
            "document_space": str(space.id),
            "folder_path": "/imports/scans",
            "folder_file_pattern": "*.pdf",
            "folder_recursive": "on",
            "folder_poll_interval_seconds": "120",
            "folder_after_import": "archive",
            "folder_archive_path": "/imports/archive",
            "folder_error_path": "/imports/error",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    source = ImportSource.objects.get(name="Scan Ordner")
    assert source.settings["folder"]["path"] == "/imports/scans"
    assert source.settings["folder"]["recursive"] is True

    response = client.post(
        reverse(
            "documents:settings_import_source_create",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "name": "Rechnungsmail",
            "source_type": ImportSource.SourceType.EMAIL,
            "target_strategy": ImportSource.TargetStrategy.FIXED,
            "document_space": str(space.id),
            "email_host": "imap.example.test",
            "email_port": "993",
            "email_security": "ssl",
            "email_username": "rechnung@example.test",
            "email_password": "mail-secret",
            "email_mailbox": "INBOX",
            "email_search_criteria": "UNSEEN",
            "email_attachment_pattern": "*.pdf",
            "email_poll_interval_seconds": "300",
            "email_mark_seen": "on",
            "email_delete_after_import": "on",
            "email_move_processed_to": "Archiv/Doksio",
            "email_success_reply_enabled": "on",
            "email_success_reply_subject": "Import erfolgreich",
            "email_success_reply_body": "Ihre Dokumente wurden importiert.",
            "email_unprocessable_action": "delete",
            "email_unprocessable_reply_enabled": "on",
            "email_unprocessable_reply_subject": "Import nicht möglich",
            "email_unprocessable_reply_body": "Bitte senden Sie einen Anhang.",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    source = ImportSource.objects.get(name="Rechnungsmail")
    assert source.settings["email"]["host"] == "imap.example.test"
    assert source.settings["email"]["password"] == "mail-secret"
    assert source.settings["email"]["delete_after_import"] is True
    assert source.settings["email"]["success_reply_enabled"] is True
    assert source.settings["email"]["success_reply_subject"] == "Import erfolgreich"
    assert source.settings["email"]["unprocessable_action"] == "delete"
    assert source.settings["email"]["unprocessable_reply_enabled"] is True


@pytest.mark.django_db
def test_tenant_admin_can_update_smtp_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
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
    client.force_login(user)

    response = client.post(
        reverse("documents:settings_smtp", kwargs={"tenant_slug": tenant.slug}),
        {
            "host": "smtp.example.test",
            "port": "587",
            "security": TenantSmtpSettings.Security.STARTTLS,
            "username": "doksio@example.test",
            "password": "smtp-secret",
            "from_email": "doksio@example.test",
            "from_name": "Doksio Import",
            "is_active": "on",
        },
    )

    smtp_settings = TenantSmtpSettings.objects.get(tenant=tenant)
    assert response.status_code == 302
    assert smtp_settings.host == "smtp.example.test"
    assert smtp_settings.port == 587
    assert smtp_settings.security == TenantSmtpSettings.Security.STARTTLS
    assert smtp_settings.username == "doksio@example.test"
    assert smtp_settings.password == "smtp-secret"
    assert smtp_settings.from_email == "doksio@example.test"
    assert smtp_settings.from_name == "Doksio Import"
    assert smtp_settings.is_active is True

    response = client.get(
        reverse("documents:settings_smtp", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "SMTP" in content
    assert "smtp.example.test" in content
    assert "doksio@example.test" in content


@pytest.mark.django_db
@override_settings(DOKSIO_PUBLIC_BASE_URL="https://doksio.example.test")
def test_tenant_admin_can_download_folder_import_script(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Scan Ordner",
        source_type=ImportSource.SourceType.FOLDER,
        token="secret-token",
        settings={
            "folder": {
                "path": "/imports/scans",
                "file_pattern": "*.pdf",
                "recursive": False,
                "poll_interval_seconds": 300,
                "after_import": "archive",
                "archive_path": "/imports/archive",
                "error_path": "/imports/error",
            }
        },
    )
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:settings_import_source_script",
            kwargs={
                "tenant_slug": tenant.slug,
                "source_id": source.id,
            },
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert 'filename="doksio-folder-import-' in response["Content-Disposition"]
    assert response["Content-Disposition"].endswith('.sh"')
    assert "X-Doksio-Import-Token: $IMPORT_TOKEN" in content
    assert "SOURCE_DIR=/imports/scans" in content
    assert 'LOG_FILE="${DOKSIO_IMPORT_LOG:-$SOURCE_DIR/doksio-folder-import.log}"' in content
    assert "log INFO \"Doksio Ordner-Agent gestartet: $SOURCE_DIR\"" in content
    assert 'should_skip_file "$file" && continue' in content
    assert 'path_in_dir "$file" "$ARCHIVE_DIR"' in content
    assert 'path_in_dir "$file" "$ERROR_DIR"' in content
    assert "API_URL=https://doksio.example.test" in content
    assert "http://testserver" not in content
    assert "curl --fail" in content


@pytest.mark.django_db
@override_settings(DOKSIO_PUBLIC_BASE_URL="https://doksio.example.test")
def test_tenant_admin_can_download_windows_folder_import_script(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Scan Ordner",
        source_type=ImportSource.SourceType.FOLDER,
        token="secret-token",
        settings={
            "folder": {
                "path": "C:\\Imports\\Scans",
                "file_pattern": "*.pdf",
                "recursive": True,
                "poll_interval_seconds": 300,
                "after_import": "archive",
                "archive_path": "C:\\Imports\\Archive",
                "error_path": "C:\\Imports\\Error",
            }
        },
    )
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:settings_import_source_script",
            kwargs={
                "tenant_slug": tenant.slug,
                "source_id": source.id,
            },
        )
        + "?platform=windows"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert response["Content-Disposition"].endswith('.ps1"')
    assert "$SourceDir = 'C:\\Imports\\Scans'" in content
    assert "$Recursive = $true" in content
    assert "$LogFile = if ($env:DOKSIO_IMPORT_LOG)" in content
    assert "function Test-DoksioSkippedFile" in content
    assert "Test-DoksioPathInDirectory -Path $File.FullName -Directory $ArchiveDir" in content
    assert "if (Test-DoksioSkippedFile -File $File) { continue }" in content
    assert "Write-DoksioLog \"INFO\" \"Doksio Ordner-Agent gestartet: $SourceDir\"" in content
    assert "Invoke-WebRequest" in content
    assert '"X-Doksio-Import-Token" = $ImportToken' in content
    assert "https://doksio.example.test" in content
    assert "http://testserver" not in content


@pytest.mark.django_db
def test_tenant_admin_can_view_logs_and_import_jobs(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    ImportJob.objects.create(
        tenant=tenant,
        document_space=space,
        original_filename="rechnung.pdf",
        content_type="application/pdf",
        status=ImportJob.Status.FAILED,
        message="Testfehler",
    )
    AuditEvent.objects.create(
        tenant=tenant,
        actor=user,
        event_type="import_job.failed",
        object_type="ingestion.ImportJob",
        object_id="1",
        data={"message": "Testfehler"},
    )
    client.force_login(user)

    response = client.get(
        reverse("documents:audit_log", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Logs/Audit" in content
    assert "rechnung.pdf" in content
    assert "import_job.failed" in content
