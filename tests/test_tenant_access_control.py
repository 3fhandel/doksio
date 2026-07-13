from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model

from doksio.accounts.access import AccessControl
from doksio.accounts.models import TenantMembership, TenantRole
from doksio.accounts.permissions import TenantPermissions
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.documents.policies import can_view_document
from doksio.documents.services import CreateDocumentFromUpload, CreateDocumentSpace
from doksio.tenancy.models import Tenant


@pytest.mark.django_db
def test_access_control_uses_tenant_role_permissions():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(username="alice")
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )

    access = AccessControl(user=user, tenant=tenant)

    assert access.can(TenantPermissions.DOCUMENTS_UPLOAD) is True
    assert access.can(TenantPermissions.SETTINGS_ROLES_MANAGE) is False


@pytest.mark.django_db
def test_access_control_reacts_to_role_permission_changes():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(username="alice")
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    roles["member"].permissions.remove(
        roles["member"].permissions.get(code=TenantPermissions.DOCUMENTS_UPLOAD)
    )

    assert (
        AccessControl(user=user, tenant=tenant).can(TenantPermissions.DOCUMENTS_UPLOAD)
        is False
    )


@pytest.mark.django_db
def test_default_role_bootstrap_does_not_overwrite_existing_role_permissions():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    upload_permission = roles["member"].permissions.get(
        code=TenantPermissions.DOCUMENTS_UPLOAD,
    )
    roles["member"].permissions.remove(upload_permission)

    EnsureDefaultTenantRoles(tenant=tenant).execute()

    assert not roles["member"].permissions.filter(
        code=TenantPermissions.DOCUMENTS_UPLOAD,
    ).exists()


@pytest.mark.django_db
def test_document_box_role_permissions_are_additive():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    EnsureDefaultTenantRoles(tenant=tenant).execute()
    view_permission = (
        TenantRole.objects.filter(tenant=tenant, slug="viewer")
        .first()
        .permissions.get(code=TenantPermissions.DOCUMENTS_VIEW)
    )
    first_box = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    second_box = CreateDocumentSpace(tenant=tenant, name="Verträge").execute()
    third_box = CreateDocumentSpace(tenant=tenant, name="Personal").execute()
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

    first_document, _first_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Rechnung",
        space=first_box,
        file_obj=BytesIO(b"rechnung content"),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
    ).execute()
    second_document, _second_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Vertrag",
        space=second_box,
        file_obj=BytesIO(b"vertrag content"),
        original_filename="vertrag.pdf",
        content_type="application/pdf",
    ).execute()
    third_document, _third_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Personal",
        space=third_box,
        file_obj=BytesIO(b"personal content"),
        original_filename="personal.pdf",
        content_type="application/pdf",
    ).execute()

    assert can_view_document(user, first_document) is True
    assert can_view_document(user, second_document) is True
    assert can_view_document(user, third_document) is False


@pytest.mark.django_db
def test_role_without_global_or_box_access_does_not_grant_document_access():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    EnsureDefaultTenantRoles(tenant=tenant).execute()
    view_permission = (
        TenantRole.objects.filter(tenant=tenant, slug="viewer")
        .first()
        .permissions.get(code=TenantPermissions.DOCUMENTS_VIEW)
    )
    box = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    role = TenantRole.objects.create(
        tenant=tenant,
        name="Workflow",
        slug="workflow",
        can_access_all_document_spaces=False,
    )
    role.permissions.set([view_permission])
    user = get_user_model().objects.create_user(username="alice")
    membership = TenantMembership.objects.create(tenant=tenant, user=user, role=role)
    membership.roles.set([role])
    document, _file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Rechnung",
        space=box,
        file_obj=BytesIO(b"content"),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
    ).execute()

    assert can_view_document(user, document) is False
