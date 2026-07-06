from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from domasy.accounts.models import TenantMembership, TenantRole
from domasy.accounts.permissions import TenantPermissions
from domasy.accounts.services import EnsureDefaultTenantRoles
from domasy.documents.services import (
    AddDocumentComment,
    CreateDocumentFromUpload,
    CreateDocumentSpace,
    SetDocumentTags,
)
from domasy.ocr.models import OcrJob
from domasy.search.services import SearchDocuments
from domasy.tenancy.models import Tenant


def _create_document(tenant, space, title, filename=None, document_date=None):
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title=title,
        space=space,
        file_obj=BytesIO(b"content"),
        original_filename=filename or f"{title}.pdf",
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

    for query in ["Wartungspaket", "dringend", "Projekt", "lieferant-alpha"]:
        results = SearchDocuments(
            tenant=tenant,
            filters={"q": query},
        ).execute()
        assert list(results) == [document]


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
    _document, document_file = _create_document(tenant, space, "Eingangsrechnung")
    OcrJob.objects.create(
        tenant=tenant,
        document_file=document_file,
        status=OcrJob.Status.SUCCEEDED,
        extracted_text="Spezialmaschine",
    )
    client.force_login(user)

    response = client.get(
        reverse("search:documents", kwargs={"tenant_slug": tenant.slug}),
        {"q": "Spezialmaschine"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Eingangsrechnung" in content
    assert "1 Treffer" in content


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
