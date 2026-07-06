from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from domasy.accounts.models import TenantMembership
from domasy.accounts.services import EnsureDefaultTenantRoles
from domasy.audit.models import AuditEvent
from domasy.documents.models import (
    Document,
    DocumentComment,
    DocumentFile,
    DocumentMetadataField,
    DocumentSpace,
)
from domasy.documents.services import (
    AddDocumentComment,
    CreateDocumentFromUpload,
    CreateDocumentMetadataField,
    CreateDocumentSpace,
    SetDocumentTags,
    UpdateDocumentMetadata,
)
from domasy.ocr.models import OcrJob
from domasy.tenancy.models import Tenant
from domasy.tenancy.services import BootstrapDemoTenant


@pytest.mark.django_db
def test_bootstrap_demo_tenant_is_idempotent():
    first_tenant, first_created = BootstrapDemoTenant().execute()
    second_tenant, second_created = BootstrapDemoTenant().execute()

    assert first_created is True
    assert second_created is False
    assert first_tenant == second_tenant
    assert Tenant.objects.count() == 1
    assert DocumentSpace.objects.filter(tenant=first_tenant).count() == 6


@pytest.mark.django_db
def test_document_space_hierarchy_builds_paths():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    invoices = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    incoming = CreateDocumentSpace(
        tenant=tenant,
        parent=invoices,
        name="Eingangsrechnungen",
        slug="eingangsrechnungen",
    ).execute()

    assert invoices.path == "/rechnungen"
    assert incoming.path == "/rechnungen/eingangsrechnungen"


@pytest.mark.django_db
def test_document_space_allows_same_slug_under_different_parents():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    invoices = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    contracts = CreateDocumentSpace(
        tenant=tenant,
        name="Verträge",
        slug="vertraege",
    ).execute()

    first_archive = CreateDocumentSpace(
        tenant=tenant,
        parent=invoices,
        name="Archiv",
        slug="archiv",
    ).execute()
    second_archive = CreateDocumentSpace(
        tenant=tenant,
        parent=contracts,
        name="Archiv",
        slug="archiv",
    ).execute()

    assert first_archive.path == "/rechnungen/archiv"
    assert second_archive.path == "/vertraege/archiv"


@pytest.mark.django_db
def test_create_document_from_upload_creates_document_file_and_audit_events():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()

    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()

    assert document.tenant == tenant
    assert document.space == space
    assert document.title == "Invoice 4711"
    assert document_file.document == document
    assert document_file.version == 1
    assert list(
        AuditEvent.objects.order_by("created_at").values_list("event_type", flat=True)
    ) == [
        "document.created",
        "document_file.stored",
    ]


@pytest.mark.django_db
def test_document_upload_view_creates_document(client):
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
    client.force_login(user)

    response = client.post(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug}),
        {
            "title": "Invoice 4711",
            "space": space.id,
            "file": SimpleUploadedFile(
                "invoice.pdf",
                b"invoice content",
                content_type="application/pdf",
            ),
        },
    )

    document = Document.objects.get(tenant=tenant)
    document_file = DocumentFile.objects.get(document=document)
    assert response.status_code == 302
    assert response.headers["Location"] == reverse(
        "documents:detail",
        kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
    )
    assert document.title == "Invoice 4711"
    assert document.space == space
    assert document_file.original_filename == "invoice.pdf"


@pytest.mark.django_db
def test_document_upload_view_limits_boxes_by_additive_role_access(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    allowed_space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    denied_space = CreateDocumentSpace(tenant=tenant, name="Verträge").execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    roles["member"].can_access_all_document_spaces = False
    roles["member"].save(update_fields=["can_access_all_document_spaces"])
    roles["member"].document_spaces.set([allowed_space])
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    client.force_login(user)

    response = client.post(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug}),
        {
            "title": "Contract",
            "space": denied_space.id,
            "file": SimpleUploadedFile(
                "contract.pdf",
                b"contract content",
                content_type="application/pdf",
            ),
        },
    )

    assert response.status_code == 200
    assert not Document.objects.filter(tenant=tenant).exists()


@pytest.mark.django_db
def test_document_upload_view_allows_empty_title(client):
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
    client.force_login(user)

    response = client.post(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug}),
        {
            "title": "",
            "space": space.id,
            "file": SimpleUploadedFile(
                "scan-001.pdf",
                b"invoice content",
                content_type="application/pdf",
            ),
        },
    )

    document = Document.objects.get(tenant=tenant)
    assert response.status_code == 302
    assert document.title == "scan-001"
    assert document.title_source == Document.TitleSource.FILENAME


@pytest.mark.django_db
def test_document_download_view_returns_stored_file(client):
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
        role=roles["viewer"],
    )
    client.force_login(user)
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()

    response = client.get(
        reverse(
            "documents:download",
            kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
        )
    )

    assert document.title == "Invoice 4711"
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"invoice content"


@pytest.mark.django_db
def test_document_detail_renders_pdf_preview(client):
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
        role=roles["viewer"],
    )
    client.force_login(user)
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"%PDF-1.4\n"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "data-pdf-preview" in content
    assert "data-review-assist-toggle" not in content
    assert "?inline=1" in content
    assert reverse(
        "documents:download",
        kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
    ) in content


@pytest.mark.django_db
def test_document_detail_shows_review_assist_for_enabled_document_box(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        review_assist_enabled=True,
    ).execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"%PDF-1.4\n"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Prüfhilfe" in content
    assert "data-review-assist-toggle" in content


@pytest.mark.django_db
def test_document_detail_shows_preview_fulltext_collapsed(client):
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
        role=roles["viewer"],
    )
    client.force_login(user)
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"%PDF-1.4\n"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()
    OcrJob.objects.create(
        tenant=tenant,
        document_file=document_file,
        status=OcrJob.Status.SUCCEEDED,
        extracted_text="Erkannter Volltext\nZeile zwei",
    )

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "<summary>" in content
    assert "Volltext" in content
    assert "Erkannter Volltext" in content
    assert content.index("document-preview-stage") < content.index("document-fulltext")


@pytest.mark.django_db
def test_document_detail_renders_image_preview(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Belege").execute()
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
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Foto",
        space=space,
        file_obj=BytesIO(b"image content"),
        original_filename="foto.png",
        content_type="image/png",
    ).execute()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "data-pdf-preview" not in content
    assert "data-image-preview" in content
    assert "data-image-fit" in content
    assert "data-image-zoom-in" in content
    assert "data-image-zoom-out" in content
    assert "document-preview-image" in content
    assert "js/document-preview.js" in content
    assert reverse(
        "documents:download",
        kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
    ) in content
    assert "?inline=1" in content


@pytest.mark.django_db
def test_document_file_download_can_render_inline(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Belege").execute()
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
    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Foto",
        space=space,
        file_obj=BytesIO(b"image content"),
        original_filename="foto.png",
        content_type="image/png",
    ).execute()

    response = client.get(
        reverse(
            "documents:download",
            kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
        ),
        {"inline": "1"},
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/png"
    assert "attachment" not in response.headers.get("Content-Disposition", "")


@pytest.mark.django_db
def test_document_detail_falls_back_without_preview(client):
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
        role=roles["viewer"],
    )
    client.force_login(user)
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Note",
        space=space,
        file_obj=BytesIO(b"plain text"),
        original_filename="note.txt",
        content_type="text/plain",
    ).execute()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "data-pdf-preview" not in content
    assert "Keine Live-Vorschau verfügbar" in content


@pytest.mark.django_db
def test_add_document_comment_creates_comment_and_audit_event():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(username="alice")
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()

    comment = AddDocumentComment(
        document=document,
        body="Bitte prüfen.",
        actor=user,
    ).execute()

    assert comment.document == document
    assert comment.body == "Bitte prüfen."
    assert comment.created_by == user
    assert AuditEvent.objects.filter(event_type="document_comment.created").exists()


@pytest.mark.django_db
def test_set_document_tags_creates_and_replaces_assignments():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(username="alice")
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()

    SetDocumentTags(
        document=document,
        tag_names=["Dringend", "Rückfrage"],
        actor=user,
    ).execute()
    SetDocumentTags(
        document=document,
        tag_names=["Geprüft"],
        actor=user,
    ).execute()

    assert list(
        document.tag_assignments.order_by("tag__name").values_list(
            "tag__name",
            flat=True,
        )
    ) == ["Geprüft"]
    assert AuditEvent.objects.filter(event_type="document_tags.updated").count() == 2


@pytest.mark.django_db
def test_document_detail_accepts_comment_and_tags(client):
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
    client.force_login(user)
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()

    comment_response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "add_comment",
            "body": "Bitte prüfen.",
        },
    )
    tag_response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "update_tags",
            "tag_names": "Dringend, Rückfrage",
        },
    )

    assert comment_response.status_code == 302
    assert tag_response.status_code == 302
    assert DocumentComment.objects.get(document=document).body == "Bitte prüfen."
    assert set(
        document.tag_assignments.values_list("tag__name", flat=True)
    ) == {"Dringend", "Rückfrage"}

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    content = response.content.decode()
    assert "Kommentare" in content
    assert "1 vorhanden" in content


@pytest.mark.django_db
def test_create_document_metadata_field_from_box_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    box = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    user = get_user_model().objects.create_user(
        username="alice",
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
            "documents:settings_metadata_field_create",
            kwargs={"tenant_slug": tenant.slug, "box_id": box.id},
        ),
        {
            "name": "Belegdatum",
            "slug": "belegdatum",
            "field_type": DocumentMetadataField.FieldType.DATE,
            "help_text": "Datum auf dem Dokument",
            "choices_text": "",
            "sort_order": "10",
            "is_required": "on",
            "is_active": "on",
        },
    )

    metadata_field = DocumentMetadataField.objects.get(space=box)
    assert response.status_code == 302
    assert metadata_field.name == "Belegdatum"
    assert metadata_field.field_type == DocumentMetadataField.FieldType.DATE
    assert metadata_field.is_required is True


@pytest.mark.django_db
def test_document_detail_accepts_box_metadata(client):
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
    CreateDocumentMetadataField(
        tenant=tenant,
        space=space,
        name="Kategorie",
        slug="kategorie",
        field_type=DocumentMetadataField.FieldType.CHOICE,
        choices=["Rechnung", "Vertrag"],
        actor=user,
    ).execute()
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "update_metadata",
            "metadata_kategorie": "Rechnung",
        },
    )

    document.refresh_from_db()
    assert response.status_code == 302
    assert document.metadata == {"kategorie": "Rechnung"}
    assert AuditEvent.objects.filter(event_type="document_metadata.updated").exists()


@pytest.mark.django_db
def test_document_core_metadata_edit_prefills_fields(client):
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
        document_date=date(2026, 3, 14),
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:core_metadata_edit",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="title"' in content
    assert 'value="Invoice 4711"' in content
    assert 'name="document_date"' in content
    assert 'value="2026-03-14"' in content


@pytest.mark.django_db
def test_document_core_metadata_edit_updates_core_metadata(client):
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Alter Titel",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
        document_date=date(2026, 3, 14),
    ).execute()
    document.title_source = Document.TitleSource.OCR
    document.save(update_fields=["title_source"])
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:core_metadata_edit",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "title": "Neuer Titel",
            "document_date": "2026-04-01",
        },
    )

    document.refresh_from_db()
    event = AuditEvent.objects.get(event_type="document_core_metadata.updated")
    assert response.status_code == 302
    assert document.title == "Neuer Titel"
    assert document.title_source == Document.TitleSource.MANUAL
    assert document.document_date == date(2026, 4, 1)
    assert event.data["previous_title"] == "Alter Titel"
    assert event.data["title"] == "Neuer Titel"


@pytest.mark.django_db
def test_update_document_metadata_writes_audit_event():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(username="alice")
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()

    UpdateDocumentMetadata(
        document=document,
        metadata={"vorgang": "Prüfung"},
        actor=user,
    ).execute()

    document.refresh_from_db()
    assert document.metadata == {"vorgang": "Prüfung"}
    assert AuditEvent.objects.filter(event_type="document_metadata.updated").exists()


@pytest.mark.django_db
def test_document_upload_view_rejects_user_without_tenant_membership(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    client.force_login(user)

    response = client.get(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_system_admin_can_access_tenant_without_membership(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    user = get_user_model().objects.create_superuser(
        username="admin",
        password="secret",
    )
    client.force_login(user)

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_dashboard_shows_latest_10_uploads(client):
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
        role=roles["viewer"],
    )
    for index in range(12):
        CreateDocumentFromUpload(
            tenant=tenant,
            title=f"Dokument {index}",
            space=space,
            file_obj=BytesIO(b"content"),
            original_filename=f"document-{index}.pdf",
            content_type="application/pdf",
        ).execute()
    client.force_login(user)

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})
    )

    documents = list(response.context["documents"])
    assert response.status_code == 200
    assert len(documents) == 10
    assert documents[0].title == "Dokument 11"
    assert documents[-1].title == "Dokument 2"
    assert "Letzte Uploads" in response.content.decode()
    assert response.context["documents_count"] == 12

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug}),
        {"uploads_page": "2"},
    )

    documents = list(response.context["documents"])
    assert len(documents) == 2
    assert documents[0].title == "Dokument 1"
    assert documents[-1].title == "Dokument 0"


@pytest.mark.django_db
def test_document_list_paginates_documents(client):
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
        role=roles["viewer"],
    )
    for index in range(30):
        CreateDocumentFromUpload(
            tenant=tenant,
            title=f"Dokument {index}",
            space=space,
            file_obj=BytesIO(b"content"),
            original_filename=f"document-{index}.pdf",
            content_type="application/pdf",
        ).execute()
    client.force_login(user)

    response = client.get(
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug})
    )

    documents = list(response.context["documents"])
    assert response.status_code == 200
    assert len(documents) == 25
    assert response.context["documents_count"] == 30
    assert "Letzte Uploads" not in response.content.decode()

    response = client.get(
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug}),
        {"page": "2"},
    )

    documents = list(response.context["documents"])
    assert len(documents) == 5
    assert documents[0].title == "Dokument 4"


@pytest.mark.django_db
def test_tenant_admin_can_create_document_box_from_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
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
            "documents:settings_document_box_create",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "name": "Rechnungen",
            "slug": "rechnungen",
            "parent": "",
            "description": "",
            "review_assist_enabled": "on",
        },
    )

    assert response.status_code == 302
    box = DocumentSpace.objects.get(tenant=tenant)
    assert box.path == "/rechnungen"
    assert box.review_assist_enabled is True


@pytest.mark.django_db
def test_tenant_admin_can_update_document_box_from_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    box = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    child = CreateDocumentSpace(
        tenant=tenant,
        parent=box,
        name="Archiv",
        slug="archiv",
    ).execute()
    user = get_user_model().objects.create_user(
        username="alice",
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
            "documents:settings_document_box_edit",
            kwargs={"tenant_slug": tenant.slug, "box_id": box.id},
        ),
        {
            "name": "Buchhaltung",
            "slug": "buchhaltung",
            "parent": "",
            "description": "Finanzdokumente",
            "review_assist_enabled": "on",
            "is_active": "on",
        },
    )

    box.refresh_from_db()
    child.refresh_from_db()
    assert response.status_code == 302
    assert box.path == "/buchhaltung"
    assert box.review_assist_enabled is True
    assert child.path == "/buchhaltung/archiv"


@pytest.mark.django_db
def test_tenant_member_cannot_access_document_box_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
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
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:settings_document_boxes",
            kwargs={"tenant_slug": tenant.slug},
        )
    )

    assert response.status_code == 403
