from __future__ import annotations

from datetime import date
from io import BytesIO
from urllib.parse import quote

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from doksio.accounts.models import TenantMembership, TenantRole
from doksio.accounts.permissions import TenantPermissions
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.documents.models import DocumentMetadataField
from doksio.documents.services import (
    AddDocumentComment,
    CreateDocumentFromUpload,
    CreateDocumentMetadataField,
    CreateDocumentSpace,
    DeleteDocument,
    SetDocumentTags,
)
from doksio.ocr.models import OcrJob
from doksio.search.services import (
    RebuildDocumentSearchIndex,
    SearchDocuments,
    build_search_match,
)
from doksio.tenancy.models import Tenant
from doksio.workflows.models import WorkflowInstance, WorkflowTemplate


def _create_document(tenant, space, title, filename=None, document_date=None):
    original_filename = filename or f"{title}.pdf"
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title=title,
        space=space,
        file_obj=BytesIO(f"content {title} {original_filename}".encode()),
        original_filename=original_filename,
        content_type="application/pdf",
        document_date=document_date,
    ).execute()
    return document, document_file


@pytest.mark.django_db
def test_search_documents_finds_ocr_text_tags_comments_and_filename():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document, document_file = _create_document(
        tenant,
        space,
        "Eingangsrechnung",
        filename="lieferant-alpha.pdf",
    )
    SetDocumentTags(document=document, tag_names=["dringend"]).execute()
    AddDocumentComment(
        document=document,
        body="Bitte mit Projekt Alpha prüfen",
    ).execute()
    OcrJob.objects.create(
        tenant=tenant,
        document_file=document_file,
        status=OcrJob.Status.SUCCEEDED,
        extracted_text="Leistung für Wartungspaket",
    )
    RebuildDocumentSearchIndex(document=document).execute()

    document.refresh_from_db()
    assert "Wartungspaket" in document.search_index.ocr_text

    for query in ["Wartungspaket", "dringend", "Projekt", "lieferant-alpha"]:
        results = SearchDocuments(
            tenant=tenant,
            filters={"q": query},
        ).execute()
        assert list(results) == [document]


@pytest.mark.django_db
def test_build_search_match_returns_ocr_context_window():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document, document_file = _create_document(
        tenant,
        space,
        "Eingangsrechnung",
    )
    OcrJob.objects.create(
        tenant=tenant,
        document_file=document_file,
        status=OcrJob.Status.SUCCEEDED,
        extracted_text=(
            "eins zwei drei vier fünf Spezialmaschine "
            "sechs sieben acht neun zehn elf"
        ),
    )

    match = build_search_match(document, "Spezialmaschine")

    assert match["source"] == "Volltext"
    assert match["excerpt"] == (
        "eins zwei drei vier fünf Spezialmaschine sechs sieben acht neun zehn ..."
    )


@pytest.mark.django_db
def test_search_documents_filters_by_tag_and_document_date_range():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    matching, _matching_file = _create_document(
        tenant,
        space,
        "März Rechnung",
        document_date=date(2026, 3, 14),
    )
    outside_range, _outside_file = _create_document(
        tenant,
        space,
        "Januar Rechnung",
        document_date=date(2026, 1, 10),
    )
    SetDocumentTags(document=matching, tag_names=["bezahlt"]).execute()
    SetDocumentTags(document=outside_range, tag_names=["bezahlt"]).execute()
    tag = matching.tag_assignments.get().tag

    results = SearchDocuments(
        tenant=tenant,
        filters={
            "tags": [tag],
            "document_date_from": date(2026, 3, 1),
            "document_date_to": date(2026, 3, 31),
        },
    ).execute()

    assert list(results) == [matching]


@pytest.mark.django_db
def test_search_documents_filters_document_box_with_optional_children():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    root_box = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    child_box = CreateDocumentSpace(
        tenant=tenant,
        parent=root_box,
        name="Eingang",
        slug="eingang",
    ).execute()
    root_document, _root_file = _create_document(tenant, root_box, "Root")
    child_document, _child_file = _create_document(tenant, child_box, "Child")

    without_children = SearchDocuments(
        tenant=tenant,
        filters={"box": root_box, "include_child_boxes": False},
    ).execute()
    with_children = SearchDocuments(
        tenant=tenant,
        filters={"box": root_box, "include_child_boxes": True},
    ).execute()

    assert list(without_children) == [root_document]
    assert set(with_children) == {root_document, child_document}


@pytest.mark.django_db
def test_document_search_filters_by_inherited_box_metadata(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    parent_box = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    child_box = CreateDocumentSpace(
        tenant=tenant,
        parent=parent_box,
        name="Eingang",
        slug="eingang",
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
    CreateDocumentMetadataField(
        tenant=tenant,
        space=parent_box,
        name="Kostenstelle",
        slug="kostenstelle",
        field_type=DocumentMetadataField.FieldType.CHOICE,
        choices=["4711", "9999"],
    ).execute()
    CreateDocumentMetadataField(
        tenant=tenant,
        space=child_box,
        name="Projekt",
        slug="projekt",
        field_type=DocumentMetadataField.FieldType.TEXT,
    ).execute()
    matching_document, _matching_file = _create_document(
        tenant,
        child_box,
        "Passende Rechnung",
    )
    matching_document.metadata = {
        "kostenstelle": "4711",
        "projekt": "Umbau Nord",
    }
    matching_document.save(update_fields=["metadata"])
    other_document, _other_file = _create_document(
        tenant,
        child_box,
        "Andere Rechnung",
    )
    other_document.metadata = {
        "kostenstelle": "9999",
        "projekt": "Umbau Süd",
    }
    other_document.save(update_fields=["metadata"])
    client.force_login(user)

    response = client.get(
        reverse("search:documents", kwargs={"tenant_slug": tenant.slug}),
        {
            "box": child_box.id,
            "include_child_boxes": "on",
            "metadata_kostenstelle": "4711",
            "metadata_projekt": "Nord",
        },
    )

    content = response.content.decode()
    documents = list(response.context["documents"])
    assert response.status_code == 200
    assert documents == [matching_document]
    assert "Metadaten" in content
    assert "Kostenstelle" in content
    assert "Projekt" in content
    assert "Passende Rechnung" in content
    assert "Andere Rechnung" not in content


@pytest.mark.django_db
def test_search_documents_respects_additive_document_box_roles():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    EnsureDefaultTenantRoles(tenant=tenant).execute()
    view_permission = (
        TenantRole.objects.get(tenant=tenant, slug="viewer")
        .permissions.get(code=TenantPermissions.DOCUMENTS_VIEW)
    )
    first_box = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    second_box = CreateDocumentSpace(tenant=tenant, name="Verträge").execute()
    third_box = CreateDocumentSpace(tenant=tenant, name="Personal").execute()
    first_document, _first_file = _create_document(tenant, first_box, "Rechnung")
    second_document, _second_file = _create_document(tenant, second_box, "Vertrag")
    third_document, _third_file = _create_document(tenant, third_box, "Personal")
    first_role = TenantRole.objects.create(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
        can_access_all_document_spaces=False,
    )
    second_role = TenantRole.objects.create(
        tenant=tenant,
        name="Verträge",
        slug="vertraege",
        can_access_all_document_spaces=False,
    )
    first_role.permissions.set([view_permission])
    second_role.permissions.set([view_permission])
    first_role.document_spaces.set([first_box])
    second_role.document_spaces.set([second_box])
    user = get_user_model().objects.create_user(username="alice")
    membership = TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=first_role,
    )
    membership.roles.set([first_role, second_role])

    results = SearchDocuments(tenant=tenant, filters={}, user=user).execute()

    assert set(results) == {first_document, second_document}
    assert third_document not in results


@pytest.mark.django_db
def test_document_search_view_renders_results_for_tenant_member(client):
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
    document, document_file = _create_document(tenant, space, "Eingangsrechnung")
    OcrJob.objects.create(
        tenant=tenant,
        document_file=document_file,
        status=OcrJob.Status.SUCCEEDED,
        extracted_text="Spezialmaschine",
    )
    RebuildDocumentSearchIndex(document=document).execute()
    client.force_login(user)

    response = client.get(
        reverse("search:documents", kwargs={"tenant_slug": tenant.slug}),
        {"q": "Spezialmaschine"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Eingangsrechnung" in content
    assert "1 Treffer" in content
    assert content.count("1 Treffer") == 1
    assert "search-panel" in content
    assert "search-result-row" in content
    assert "search-filter-drawer" in content
    assert "details-summary-title" in content
    assert "Ablage" in content
    assert "Zeitraum und Status" in content
    assert "Darstellung" in content
    assert "PDF" in content
    assert "Fundstelle: Volltext" in content
    assert "Spezialmaschine" in content
    assert (
            reverse(
                "documents:detail",
                kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
            )
        in content
    )
    assert f"back={quote(response.wsgi_request.get_full_path(), safe='/')}" in content
    assert f"nav={document.id}" in content


@pytest.mark.django_db
def test_document_search_view_shows_workflow_counts(client):
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
    open_document, _open_file = _create_document(
        tenant,
        space,
        "Workflow offen",
    )
    completed_document, _completed_file = _create_document(
        tenant,
        space,
        "Workflow fertig",
    )
    plain_document, _plain_file = _create_document(
        tenant,
        space,
        "Workflow ohne",
    )
    template = WorkflowTemplate.objects.create(
        tenant=tenant,
        name="Prüfung",
        slug="pruefung",
    )
    WorkflowInstance.objects.create(
        tenant=tenant,
        template=template,
        document=open_document,
        status=WorkflowInstance.Status.RUNNING,
    )
    WorkflowInstance.objects.create(
        tenant=tenant,
        template=template,
        document=completed_document,
        status=WorkflowInstance.Status.COMPLETED,
    )
    client.force_login(user)

    response = client.get(
        reverse("search:documents", kwargs={"tenant_slug": tenant.slug}),
        {"q": "Workflow"},
    )

    documents = list(response.context["documents"])
    content = response.content.decode()
    assert response.status_code == 200
    assert set(documents) == {open_document, completed_document, plain_document}
    assert "search-result-workflow-open" in content
    assert "search-result-workflow-done" in content
    assert "0/1" in content
    assert "1/1" in content


@pytest.mark.django_db
def test_search_documents_filters_by_workflow_status():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    open_document, _open_file = _create_document(
        tenant,
        space,
        "Workflow offen",
    )
    completed_document, _completed_file = _create_document(
        tenant,
        space,
        "Workflow fertig",
    )
    plain_document, _plain_file = _create_document(
        tenant,
        space,
        "Workflow ohne",
    )
    template = WorkflowTemplate.objects.create(
        tenant=tenant,
        name="Prüfung",
        slug="pruefung",
    )
    WorkflowInstance.objects.create(
        tenant=tenant,
        template=template,
        document=open_document,
        status=WorkflowInstance.Status.RUNNING,
    )
    WorkflowInstance.objects.create(
        tenant=tenant,
        template=template,
        document=completed_document,
        status=WorkflowInstance.Status.COMPLETED,
    )

    open_results = SearchDocuments(
        tenant=tenant,
        filters={"workflow_status": "open"},
    ).execute()
    completed_results = SearchDocuments(
        tenant=tenant,
        filters={"workflow_status": "completed"},
    ).execute()
    none_results = SearchDocuments(
        tenant=tenant,
        filters={"workflow_status": "none"},
    ).execute()

    assert list(open_results) == [open_document]
    assert list(completed_results) == [completed_document]
    assert list(none_results) == [plain_document]


@pytest.mark.django_db
def test_search_documents_blank_query_does_not_order_by_missing_rank(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document, _document_file = _create_document(
        tenant,
        space,
        "Rechnung ohne Suchbegriff",
    )
    monkeypatch.setattr("doksio.search.services.connection.vendor", "postgresql")

    results = SearchDocuments(
        tenant=tenant,
        filters={"q": "", "sort": "relevance"},
    ).execute()

    assert list(results) == [document]


@pytest.mark.django_db
def test_search_documents_filters_deleted_documents():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    active_document, _active_file = _create_document(
        tenant,
        space,
        "Status aktiv",
    )
    deleted_document, _deleted_file = _create_document(
        tenant,
        space,
        "Status gelöscht",
    )
    DeleteDocument(
        document=deleted_document,
        reason="Testupload",
    ).execute()

    default_results = SearchDocuments(
        tenant=tenant,
        filters={"q": "Status"},
    ).execute()
    deleted_results = SearchDocuments(
        tenant=tenant,
        filters={"q": "Status", "document_status": "deleted"},
    ).execute()
    all_results = SearchDocuments(
        tenant=tenant,
        filters={"q": "Status", "document_status": "all"},
    ).execute()

    assert list(default_results) == [active_document]
    assert list(deleted_results) == [deleted_document]
    assert set(all_results) == {active_document, deleted_document}


@pytest.mark.django_db
def test_document_search_view_shows_deleted_documents_for_delete_permission(client):
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
    deleted_document, _deleted_file = _create_document(
        tenant,
        space,
        "Gelöschte Rechnung",
    )
    DeleteDocument(
        document=deleted_document,
        reason="Testupload",
        actor=user,
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse("search:documents", kwargs={"tenant_slug": tenant.slug}),
        {"q": "Gelöschte", "document_status": "deleted"},
    )

    content = response.content.decode()
    detail_url = reverse(
        "documents:detail",
        kwargs={"tenant_slug": tenant.slug, "document_id": deleted_document.id},
    )
    assert response.status_code == 200
    assert response.context["documents_count"] == 1
    assert "Gelöschte Rechnung" in content
    assert "Gelöscht" in content
    assert "Grund Testupload" in content
    assert detail_url not in content


@pytest.mark.django_db
def test_document_search_view_hides_deleted_documents_without_delete_permission(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    admin = get_user_model().objects.create_user(username="admin")
    viewer = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=viewer,
        role=roles["viewer"],
    )
    deleted_document, _deleted_file = _create_document(
        tenant,
        space,
        "Gelöschte Rechnung",
    )
    DeleteDocument(
        document=deleted_document,
        reason="Testupload",
        actor=admin,
    ).execute()
    client.force_login(viewer)

    response = client.get(
        reverse("search:documents", kwargs={"tenant_slug": tenant.slug}),
        {"q": "Gelöschte", "document_status": "deleted"},
    )

    assert response.status_code == 200
    assert response.context["documents_count"] == 0
    assert "Gelöschte Rechnung" not in response.content.decode()


@pytest.mark.django_db
def test_document_search_view_paginates_results(client):
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
        _create_document(tenant, space, f"Suchdokument {index}")
    client.force_login(user)

    response = client.get(
        reverse("search:documents", kwargs={"tenant_slug": tenant.slug}),
        {"q": "Suchdokument", "page": "2"},
    )

    documents = list(response.context["documents"])
    content = response.content.decode()
    assert response.status_code == 200
    assert len(documents) == 5
    assert response.context["documents_count"] == 30
    assert "30 Treffer" in content
    assert "page=1" in content
    assert "q=Suchdokument" in content
