from __future__ import annotations

from email.message import EmailMessage
from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from doksio.accounts.models import Notification, TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.audit.models import AuditEvent
from doksio.documents.models import Document, DocumentFile, DocumentTagAssignment
from doksio.documents.services import CreateDocumentSpace, DuplicateDocumentError
from doksio.ingestion.models import (
    EmailAutoReplyRecipient,
    ImportJob,
    ImportSource,
    TenantSmtpSettings,
)
from doksio.ingestion.services import ImportDocument, ProcessEmailImportSource
from doksio.ocr.models import OcrJob
from doksio.tenancy.models import Tenant

MINIMAL_PDF_BYTES = b"%PDF-1.4\n% Doksio test PDF\n%%EOF\n"


class FakeImapConnection:
    def __init__(self, messages: dict[bytes, bytes]) -> None:
        self.messages = messages
        self.actions = []
        self.created_mailboxes = []
        self.closed = False
        self.logged_out = False

    def select(self, mailbox):
        self.actions.append(("select", mailbox))
        return "OK", [b""]

    def search(self, _charset, *criteria):
        self.actions.append(("search", criteria))
        return "OK", [b" ".join(self.messages.keys())]

    def fetch(self, message_id, _query):
        self.actions.append(("fetch", message_id))
        return "OK", [(b"RFC822", self.messages[message_id])]

    def store(self, message_id, command, flags):
        self.actions.append(("store", message_id, command, flags))
        return "OK", [b""]

    def copy(self, message_id, mailbox):
        self.actions.append(("copy", message_id, mailbox))
        return "OK", [b""]

    def create(self, mailbox):
        self.created_mailboxes.append(mailbox)
        return "OK", [b""]

    def expunge(self):
        self.actions.append(("expunge",))
        return "OK", [b""]

    def close(self):
        self.closed = True
        return "OK", [b""]

    def logout(self):
        self.logged_out = True
        return "OK", [b""]


def _raw_email(
    *,
    subject: str = "Rechnung",
    message_id: str = "<mail-1@example.test>",
    sender: str = "sender@example.test",
    attachment_name: str | None = "rechnung.pdf",
    attachment_content: bytes = MINIMAL_PDF_BYTES,
) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["Message-ID"] = message_id
    message.set_content("Bitte importieren.")
    if attachment_name is not None:
        message.add_attachment(
            attachment_content,
            maintype="application",
            subtype="pdf",
            filename=attachment_name,
        )
    return message.as_bytes()


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
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    admin = get_user_model().objects.create_user(username="admin")
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin,
        role=roles["admin"],
    )
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
    notification = Notification.objects.get(
        recipient=admin,
        notification_type=Notification.Type.IMPORT_FAILED,
    )
    assert notification.title == "Importfehler"
    assert "rechnung-kopie.pdf" in notification.body
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
def test_http_import_endpoint_returns_conflict_for_duplicate_document(client):
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
    url = reverse(
        "ingestion:http_import",
        kwargs={"tenant_slug": tenant.slug, "source_id": source.id},
    )
    headers = {
        "X-Doksio-Import-Token": "secret-token",
        "X-Doksio-Filename": "rechnung.pdf",
    }

    first_response = client.put(
        url,
        data=MINIMAL_PDF_BYTES,
        content_type="application/pdf",
        headers=headers,
    )
    second_response = client.put(
        url,
        data=MINIMAL_PDF_BYTES,
        content_type="application/pdf",
        headers={**headers, "X-Doksio-Filename": "rechnung-kopie.pdf"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert second_response.json()["code"] == "duplicate_document"
    assert second_response.json()["duplicate"] is True
    assert second_response.json()["existing_document_id"] == Document.objects.get().id
    assert Document.objects.count() == 1
    assert ImportJob.objects.filter(status=ImportJob.Status.FAILED).count() == 1


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
def test_tenant_admin_can_test_import_ocr_title_regex(client):
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
            "documents:settings_import_regex_test",
            kwargs={"tenant_slug": tenant.slug},
        ),
        data={
            "regex_search": r"Rechnung Nr\. (?P<number>\d+)",
            "regex_replace": r"Rechnung \g<number>",
            "sample_text": "Lieferant\nRechnung Nr. 4711\nVielen Dank",
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "matched": True,
        "title": "Rechnung 4711",
        "match": "Rechnung Nr. 4711",
    }


@pytest.mark.django_db
def test_import_ocr_title_regex_test_reports_no_match(client):
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
            "documents:settings_import_regex_test",
            kwargs={"tenant_slug": tenant.slug},
        ),
        data={
            "regex_search": r"Rechnung Nr\. (\d+)",
            "regex_replace": r"Rechnung \1",
            "sample_text": "Angebot 42",
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "matched": False, "title": ""}


@pytest.mark.django_db
def test_import_ocr_title_regex_test_reports_invalid_regex(client):
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
            "documents:settings_import_regex_test",
            kwargs={"tenant_slug": tenant.slug},
        ),
        data={
            "regex_search": r"Rechnung Nr\. (",
            "regex_replace": r"Rechnung \1",
            "sample_text": "Rechnung Nr. 4711",
        },
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "RegEx-Fehler" in response.json()["error"]


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
def test_process_email_import_source_imports_matching_attachment():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Rechnungsmail",
        source_type=ImportSource.SourceType.EMAIL,
        settings={
            "email": {
                "mailbox": "INBOX",
                "search_criteria": "UNSEEN",
                "attachment_pattern": "*.pdf",
                "mark_seen": True,
                "delete_after_import": False,
                "move_processed_to": "",
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )
    imap = FakeImapConnection({b"1": _raw_email()})

    result = ProcessEmailImportSource(
        source=source,
        imap_factory=lambda _settings: imap,
    ).execute()

    document = Document.objects.get()
    assert result.checked_messages == 1
    assert result.imported_documents == 1
    assert result.failed_attachments == 0
    assert document.title == "rechnung"
    assert (
        DocumentFile.objects.get(document=document).original_filename
        == "rechnung.pdf"
    )
    assert ("store", b"1", "+FLAGS", "\\Seen") in imap.actions
    assert source.settings["email"]["last_result"]["imported_documents"] == 1


@pytest.mark.django_db
def test_process_email_import_source_matches_attachment_pattern_case_insensitive():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Rechnungsmail",
        source_type=ImportSource.SourceType.EMAIL,
        settings={
            "email": {
                "mailbox": "INBOX",
                "search_criteria": "UNSEEN",
                "attachment_pattern": "*.pdf",
                "mark_seen": True,
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )
    imap = FakeImapConnection({b"1": _raw_email(attachment_name="RECHNUNG.PDF")})

    result = ProcessEmailImportSource(
        source=source,
        imap_factory=lambda _settings: imap,
    ).execute()

    assert result.checked_messages == 1
    assert result.matched_attachments == 1
    assert result.imported_documents == 1
    assert DocumentFile.objects.get().original_filename == "RECHNUNG.PDF"


@pytest.mark.django_db
def test_process_email_import_source_reports_ignored_attachments():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Rechnungsmail",
        source_type=ImportSource.SourceType.EMAIL,
        settings={
            "email": {
                "mailbox": "INBOX",
                "search_criteria": "UNSEEN",
                "attachment_pattern": "*.pdf",
                "unprocessable_action": "keep",
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )
    imap = FakeImapConnection({b"1": _raw_email(attachment_name="rechnung.txt")})

    result = ProcessEmailImportSource(
        source=source,
        imap_factory=lambda _settings: imap,
    ).execute()

    assert result.checked_messages == 1
    assert result.ignored_attachments == 1
    assert result.unprocessable_messages == 1
    assert "rechnung.txt" in result.errors[0]
    assert source.settings["email"]["last_result"]["ignored_attachments"] == 1


@pytest.mark.django_db
def test_process_email_import_source_treats_duplicate_as_processed():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Rechnungsmail",
        source_type=ImportSource.SourceType.EMAIL,
        settings={
            "email": {
                "mailbox": "INBOX",
                "search_criteria": "UNSEEN",
                "attachment_pattern": "*.pdf",
                "move_processed_to": "Archiv/Doksio",
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )
    ImportDocument(
        tenant=tenant,
        source=source,
        document_space=space,
        file_obj=BytesIO(MINIMAL_PDF_BYTES),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
    ).execute()
    imap = FakeImapConnection(
        {b"1": _raw_email(attachment_name="rechnung-kopie.pdf")}
    )

    result = ProcessEmailImportSource(
        source=source,
        imap_factory=lambda _settings: imap,
    ).execute()

    assert result.checked_messages == 1
    assert result.imported_documents == 0
    assert result.duplicate_documents == 1
    assert result.failed_attachments == 0
    assert Document.objects.count() == 1
    assert ("copy", b"1", "Archiv/Doksio") in imap.actions
    assert ("store", b"1", "+FLAGS", "\\Deleted") in imap.actions
    assert ("expunge",) in imap.actions


@pytest.mark.django_db
def test_process_email_import_source_handles_unprocessable_message(monkeypatch):
    sent_messages = []
    monkeypatch.setattr(
        "doksio.ingestion.services.EmailMultiAlternatives.send",
        lambda self: sent_messages.append(self) or 1,
    )
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    TenantSmtpSettings.objects.create(
        tenant=tenant,
        host="smtp.example.test",
        port=587,
        security=TenantSmtpSettings.Security.STARTTLS,
        username="doksio@example.test",
        password="secret",
        from_email="doksio@example.test",
        from_name="Doksio",
        is_active=True,
    )
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Rechnungsmail",
        source_type=ImportSource.SourceType.EMAIL,
        settings={
            "email": {
                "mailbox": "INBOX",
                "search_criteria": "UNSEEN",
                "attachment_pattern": "*.pdf",
                "unprocessable_action": "delete",
                "unprocessable_reply_enabled": True,
                "unprocessable_reply_subject": "Import nicht möglich",
                "unprocessable_reply_body": "Bitte senden Sie einen PDF-Anhang.",
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )
    imap = FakeImapConnection(
        {b"1": _raw_email(attachment_name=None, sender="absender@example.test")}
    )

    result = ProcessEmailImportSource(
        source=source,
        imap_factory=lambda _settings: imap,
    ).execute()

    assert result.checked_messages == 1
    assert result.unprocessable_messages == 1
    assert not Document.objects.exists()
    assert ("store", b"1", "+FLAGS", "\\Deleted") in imap.actions
    assert sent_messages[0].to == ["absender@example.test"]
    assert sent_messages[0].subject == "Import nicht möglich"
    assert sent_messages[0].alternatives[0][1] == "text/html"
    assert "Nicht importierbare Mail" not in sent_messages[0].alternatives[0][0]
    assert "Bitte senden Sie einen PDF-Anhang." in sent_messages[0].alternatives[0][0]
    assert "Falls der Button nicht funktioniert" not in sent_messages[0].alternatives[0][0]
    assert "https://github.com/3fhandel/doksio" in sent_messages[0].alternatives[0][0]
    mime_message = sent_messages[0].message()
    assert "multipart/related" in {
        part.get_content_type() for part in mime_message.walk()
    }
    assert any(
        part.get("Content-ID") == "<doksio-logo>"
        for part in mime_message.walk()
    )


@pytest.mark.django_db
def test_email_auto_reply_can_be_limited_once_per_sender_and_reply_type(
    monkeypatch,
):
    sent_messages = []
    monkeypatch.setattr(
        "doksio.ingestion.services.EmailMultiAlternatives.send",
        lambda self: sent_messages.append(self) or 1,
    )
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    TenantSmtpSettings.objects.create(
        tenant=tenant,
        host="smtp.example.test",
        from_email="doksio@example.test",
        is_active=True,
    )
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Rechnungsmail",
        source_type=ImportSource.SourceType.EMAIL,
        settings={
            "email": {
                "mailbox": "INBOX",
                "search_criteria": "ALL",
                "attachment_pattern": "*.pdf",
                "mark_seen": True,
                "success_reply_enabled": True,
                "success_reply_once_per_sender": True,
                "success_reply_subject": "Import erfolgreich",
                "success_reply_body": "Ihre Dokumente wurden importiert.",
                "unprocessable_action": "mark_seen",
                "unprocessable_reply_enabled": True,
                "unprocessable_reply_once_per_sender": True,
                "unprocessable_reply_subject": "Import nicht möglich",
                "unprocessable_reply_body": "Bitte senden Sie einen PDF-Anhang.",
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )
    imap = FakeImapConnection(
        {
            b"1": _raw_email(
                message_id="<mail-1@example.test>",
                sender="Max Mustermann <ABSENDER@example.test>",
                attachment_name="rechnung-1.pdf",
                attachment_content=MINIMAL_PDF_BYTES + b"1",
            ),
            b"2": _raw_email(
                message_id="<mail-2@example.test>",
                sender="absender@example.test",
                attachment_name="rechnung-2.pdf",
                attachment_content=MINIMAL_PDF_BYTES + b"2",
            ),
            b"3": _raw_email(
                message_id="<mail-3@example.test>",
                sender="absender@example.test",
                attachment_name=None,
            ),
        }
    )

    result = ProcessEmailImportSource(
        source=source,
        imap_factory=lambda _settings: imap,
    ).execute()

    assert result.imported_documents == 2
    assert result.unprocessable_messages == 1
    assert len(sent_messages) == 2
    assert {message.subject for message in sent_messages} == {
        "Import erfolgreich",
        "Import nicht möglich",
    }
    assert set(
        EmailAutoReplyRecipient.objects.values_list(
            "recipient",
            "reply_type",
        )
    ) == {
        (
            "absender@example.test",
            EmailAutoReplyRecipient.ReplyType.SUCCESS,
        ),
        (
            "absender@example.test",
            EmailAutoReplyRecipient.ReplyType.UNPROCESSABLE,
        ),
    }
    assert not EmailAutoReplyRecipient.objects.filter(sent_at=None).exists()
    assert AuditEvent.objects.filter(
        event_type="email_import_reply.suppressed"
    ).count() == 1


@pytest.mark.django_db
def test_failed_email_auto_reply_does_not_block_later_retry(monkeypatch):
    def fail_to_send(_message):
        raise RuntimeError("SMTP nicht erreichbar")

    monkeypatch.setattr(
        "doksio.ingestion.services.EmailMultiAlternatives.send",
        fail_to_send,
    )
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    TenantSmtpSettings.objects.create(
        tenant=tenant,
        host="smtp.example.test",
        from_email="doksio@example.test",
        is_active=True,
    )
    source = ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Rechnungsmail",
        source_type=ImportSource.SourceType.EMAIL,
        settings={
            "email": {
                "mailbox": "INBOX",
                "search_criteria": "UNSEEN",
                "attachment_pattern": "*.pdf",
                "unprocessable_action": "keep",
                "unprocessable_reply_enabled": True,
                "unprocessable_reply_once_per_sender": True,
                "unprocessable_reply_body": "Bitte senden Sie einen Anhang.",
            }
        },
        auto_start_ocr=False,
        extract_einvoice=False,
        start_workflows=False,
    )

    with pytest.raises(RuntimeError, match="SMTP nicht erreichbar"):
        ProcessEmailImportSource(
            source=source,
            imap_factory=lambda _settings: FakeImapConnection(
                {b"1": _raw_email(attachment_name=None)}
            ),
        ).execute()

    assert not EmailAutoReplyRecipient.objects.exists()


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
            "folder_run_mode": "once",
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
    assert source.settings["folder"]["run_mode"] == "once"

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
            "email_success_reply_once_per_sender": "on",
            "email_success_reply_subject": "Import erfolgreich",
            "email_success_reply_body": "Ihre Dokumente wurden importiert.",
            "email_unprocessable_action": "delete",
            "email_unprocessable_reply_enabled": "on",
            "email_unprocessable_reply_once_per_sender": "on",
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
    assert source.settings["email"]["success_reply_once_per_sender"] is True
    assert source.settings["email"]["success_reply_subject"] == "Import erfolgreich"
    assert source.settings["email"]["unprocessable_action"] == "delete"
    assert source.settings["email"]["unprocessable_reply_enabled"] is True
    assert (
        source.settings["email"]["unprocessable_reply_once_per_sender"] is True
    )


@pytest.mark.django_db
def test_tenant_admin_can_reset_email_auto_reply_recipient(client):
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
        name="Rechnungsmail",
        source_type=ImportSource.SourceType.EMAIL,
    )
    recipient = EmailAutoReplyRecipient.objects.create(
        tenant=tenant,
        source=source,
        recipient="absender@example.test",
        reply_type=EmailAutoReplyRecipient.ReplyType.SUCCESS,
    )
    client.force_login(user)

    edit_response = client.get(
        reverse(
            "documents:settings_import_source_edit",
            kwargs={
                "tenant_slug": tenant.slug,
                "source_id": source.id,
            },
        )
    )

    assert edit_response.status_code == 200
    assert "Pro Absender nur einmal senden" in edit_response.content.decode()
    assert "absender@example.test" in edit_response.content.decode()

    response = client.post(
        reverse(
            "documents:settings_import_source_auto_reply_recipients_reset",
            kwargs={
                "tenant_slug": tenant.slug,
                "source_id": source.id,
            },
        ),
        {"recipient_id": str(recipient.id)},
    )

    assert response.status_code == 302
    assert not EmailAutoReplyRecipient.objects.exists()
    assert AuditEvent.objects.filter(
        event_type="email_import_reply.recipients_reset",
        actor=user,
    ).exists()


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
def test_tenant_admin_can_send_smtp_test_mail(client, monkeypatch):
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
    TenantSmtpSettings.objects.create(
        tenant=tenant,
        host="smtp.example.test",
        port=587,
        security=TenantSmtpSettings.Security.STARTTLS,
        username="doksio@example.test",
        password="smtp-secret",
        from_email="doksio@example.test",
        from_name="Doksio",
        is_active=True,
    )
    sent_messages = []

    def fake_send(message, fail_silently=False):
        sent_messages.append(message)
        return 1

    monkeypatch.setattr(
        "doksio.documents.views.EmailMultiAlternatives.send",
        fake_send,
    )
    client.force_login(user)

    response = client.post(
        reverse("documents:settings_smtp", kwargs={"tenant_slug": tenant.slug}),
        {
            "action": "send_test",
            "recipient": "admin@example.test",
        },
    )

    assert response.status_code == 302
    assert len(sent_messages) == 1
    assert sent_messages[0].to == ["admin@example.test"]
    assert sent_messages[0].subject == "Doksio SMTP-Test"
    assert sent_messages[0].alternatives[0][1] == "text/html"
    assert "SMTP-Verbindung erfolgreich" in sent_messages[0].alternatives[0][0]
    assert AuditEvent.objects.filter(
        tenant=tenant,
        event_type="smtp.test_sent",
        data__recipient="admin@example.test",
    ).exists()


@pytest.mark.django_db
def test_tenant_admin_cannot_send_smtp_test_without_active_settings(
    client,
    monkeypatch,
):
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
    sent_messages = []

    def fake_send(message, fail_silently=False):
        sent_messages.append(message)
        return 1

    monkeypatch.setattr(
        "doksio.documents.views.EmailMultiAlternatives.send",
        fake_send,
    )
    client.force_login(user)

    response = client.post(
        reverse("documents:settings_smtp", kwargs={"tenant_slug": tenant.slug}),
        {
            "action": "send_test",
            "recipient": "admin@example.test",
        },
    )

    assert response.status_code == 200
    assert sent_messages == []
    assert "kein aktiver SMTP-Versand" in response.content.decode()


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
                "run_mode": "once",
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
    assert "RUN_MODE=once" in content
    assert (
        'LOG_FILE="${DOKSIO_IMPORT_LOG:-$SOURCE_DIR/doksio-folder-import.log}"'
        in content
    )
    assert "log INFO \"Doksio Ordner-Agent gestartet: $SOURCE_DIR\"" in content
    assert "process_pending_files() {" in content
    assert 'if [[ "$RUN_MODE" == "once" ]]; then' in content
    assert 'log INFO "Einmallauf beendet."' in content
    assert 'should_skip_file "$file" && continue' in content
    assert 'path_in_dir "$file" "$ARCHIVE_DIR"' in content
    assert 'path_in_dir "$file" "$ERROR_DIR"' in content
    assert "API_URL=https://doksio.example.test" in content
    assert "http://testserver" not in content
    assert "curl --silent --show-error" in content
    assert '--write-out "%{http_code}"' in content
    assert 'elif [[ "$http_status" == "409" ]]; then' in content
    assert "Dublette aus Quellordner gelöscht" in content


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
                "run_mode": "once",
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
    assert "$RunMode = 'once'" in content
    assert "$LogFile = if ($env:DOKSIO_IMPORT_LOG)" in content
    assert "function Test-DoksioSkippedFile" in content
    assert "function Invoke-DoksioPendingFiles" in content
    assert 'if ($RunMode -eq "once")' in content
    assert 'Write-DoksioLog "INFO" "Einmallauf beendet."' in content
    assert (
        "Test-DoksioPathInDirectory -Path $File.FullName -Directory $ArchiveDir"
        in content
    )
    assert "if (Test-DoksioSkippedFile -File $File) { continue }" in content
    assert (
        "Write-DoksioLog \"INFO\" "
        "\"Doksio Ordner-Agent gestartet: $SourceDir\""
        in content
    )
    assert "Invoke-WebRequest" in content
    assert '"X-Doksio-Import-Token" = $ImportToken' in content
    assert "$StatusCode -eq 409" in content
    assert "Dublette aus Quellordner gelöscht" in content
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
