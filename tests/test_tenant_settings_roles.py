from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from domasy.accounts.models import TenantMembership, TenantPermission, TenantRole
from domasy.accounts.permissions import TenantPermissions
from domasy.accounts.services import EnsureDefaultTenantRoles
from domasy.documents.services import CreateDocumentSpace
from domasy.tenancy.models import Tenant


@pytest.mark.django_db
def test_tenant_admin_can_view_roles_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    admin_user = get_user_model().objects.create_user(
        username="admin",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin_user,
        role=roles["admin"],
    )
    client.force_login(admin_user)

    response = client.get(
        reverse("documents:settings_roles", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 200
    assert "Neue Rolle" in response.content.decode()


@pytest.mark.django_db
def test_tenant_admin_can_create_role_from_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    admin_user = get_user_model().objects.create_user(
        username="admin",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin_user,
        role=roles["admin"],
    )
    view_permission = TenantPermission.objects.get(
        code=TenantPermissions.DOCUMENTS_VIEW
    )
    client.force_login(admin_user)

    response = client.post(
        reverse("documents:settings_role_create", kwargs={"tenant_slug": tenant.slug}),
        {
            "name": "Sachbearbeitung",
            "slug": "sachbearbeitung",
            "description": "Fachliche Bearbeitung",
            "permissions": [str(view_permission.id)],
        },
    )

    role = TenantRole.objects.get(tenant=tenant, slug="sachbearbeitung")
    assert response.status_code == 302
    assert role.permissions.filter(code=TenantPermissions.DOCUMENTS_VIEW).exists()


@pytest.mark.django_db
def test_tenant_admin_can_limit_role_to_document_boxes_from_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    box = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    admin_user = get_user_model().objects.create_user(
        username="admin",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin_user,
        role=roles["admin"],
    )
    view_permission = TenantPermission.objects.get(
        code=TenantPermissions.DOCUMENTS_VIEW
    )
    client.force_login(admin_user)

    response = client.post(
        reverse("documents:settings_role_create", kwargs={"tenant_slug": tenant.slug}),
        {
            "name": "Buchhaltung",
            "slug": "buchhaltung",
            "description": "Fachliche Bearbeitung",
            "permissions": [str(view_permission.id)],
            "document_spaces": [str(box.id)],
            "can_access_all_document_spaces": "",
        },
    )

    role = TenantRole.objects.get(tenant=tenant, slug="buchhaltung")
    assert response.status_code == 302
    assert list(role.document_spaces.all()) == [box]
    assert role.can_access_all_document_spaces is False


@pytest.mark.django_db
def test_tenant_admin_can_update_role_permissions_from_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    admin_user = get_user_model().objects.create_user(
        username="admin",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin_user,
        role=roles["admin"],
    )
    upload_permission = TenantPermission.objects.get(
        code=TenantPermissions.DOCUMENTS_UPLOAD
    )
    client.force_login(admin_user)

    response = client.post(
        reverse(
            "documents:settings_role_edit",
            kwargs={"tenant_slug": tenant.slug, "role_id": roles["viewer"].id},
        ),
        {
            "name": "Viewer",
            "description": "Read-mostly",
            "is_active": "on",
            "permissions": [str(upload_permission.id)],
        },
    )

    roles["viewer"].refresh_from_db()
    assert response.status_code == 302
    assert roles["viewer"].permissions.filter(
        code=TenantPermissions.DOCUMENTS_UPLOAD
    ).exists()


@pytest.mark.django_db
def test_tenant_member_cannot_access_roles_settings(client):
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
        reverse("documents:settings_roles", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 403
