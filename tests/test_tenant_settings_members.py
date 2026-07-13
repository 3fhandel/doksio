from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from doksio.accounts.models import TenantMembership, UserProfile
from doksio.accounts.permissions import TenantPermissions
from doksio.accounts.services import (
    AddTenantMember,
    EnsureDefaultTenantRoles,
    UpdateTenantMembership,
)
from doksio.audit.models import AuditEvent
from doksio.tenancy.models import Tenant


@pytest.mark.django_db
def test_add_tenant_member_creates_membership_and_audit_event():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(username="alice")

    membership = AddTenantMember(
        tenant=tenant,
        username=user.username,
        role=roles["admin"],
    ).execute()

    assert membership.tenant == tenant
    assert membership.user == user
    assert membership.role == roles["admin"]
    assert list(membership.roles.all()) == [roles["admin"]]
    assert membership.is_active is True
    assert AuditEvent.objects.get().event_type == "tenant_membership.created"


@pytest.mark.django_db
def test_add_tenant_user_creates_non_system_user_with_multiple_roles():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()

    membership = AddTenantMember(
        tenant=tenant,
        username="alice",
        email="alice@example.test",
        password="secret",
        roles=[roles["viewer"], roles["member"]],
    ).execute()

    user = get_user_model().objects.get(username="alice")
    assert user.email == "alice@example.test"
    assert user.is_staff is False
    assert user.is_superuser is False
    assert membership.role == roles["viewer"]
    assert set(membership.roles.all()) == {roles["viewer"], roles["member"]}


@pytest.mark.django_db
def test_update_tenant_membership_changes_role_status_and_writes_audit_event():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(username="alice")
    membership = TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )

    UpdateTenantMembership(
        membership=membership,
        role=roles["viewer"],
        is_active=False,
    ).execute()

    membership.refresh_from_db()
    assert membership.role == roles["viewer"]
    assert list(membership.roles.all()) == [roles["viewer"]]
    assert membership.is_active is False
    assert AuditEvent.objects.get().event_type == "tenant_membership.updated"


@pytest.mark.django_db
def test_update_tenant_membership_accepts_multiple_roles():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(username="alice")
    membership = TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["viewer"],
    )

    UpdateTenantMembership(
        membership=membership,
        roles=[roles["viewer"], roles["member"]],
        is_active=True,
    ).execute()

    membership.refresh_from_db()
    assert set(membership.roles.all()) == {roles["viewer"], roles["member"]}
    assert membership.roles.filter(
        permissions__code=TenantPermissions.DOCUMENTS_UPLOAD
    ).exists()


@pytest.mark.django_db
def test_tenant_admin_can_add_member_from_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    admin_user = get_user_model().objects.create_user(
        username="admin",
        password="secret",
    )
    target_user = get_user_model().objects.create_user(username="alice")
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin_user,
        role=roles["admin"],
    )
    client.force_login(admin_user)

    response = client.post(
        reverse(
            "documents:settings_member_create",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "username": target_user.username,
            "role": roles["viewer"].id,
        },
    )

    assert response.status_code == 302
    membership = TenantMembership.objects.get(tenant=tenant, user=target_user)
    assert membership.role == roles["viewer"]


@pytest.mark.django_db
def test_tenant_admin_can_view_members_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    admin_user = get_user_model().objects.create_user(
        username="admin",
        password="secret",
    )
    UserProfile.objects.create(user=admin_user, display_name="Admin Anzeige")
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin_user,
        role=roles["admin"],
    )
    client.force_login(admin_user)

    response = client.get(
        reverse("documents:settings_members", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Benutzer hinzufügen" in content
    assert "Admin Anzeige" in content


@pytest.mark.django_db
def test_tenant_admin_can_update_member_from_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    admin_user = get_user_model().objects.create_user(
        username="admin",
        password="secret",
    )
    target_user = get_user_model().objects.create_user(username="alice")
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin_user,
        role=roles["admin"],
    )
    target_membership = TenantMembership.objects.create(
        tenant=tenant,
        user=target_user,
        role=roles["member"],
    )
    client.force_login(admin_user)

    response = client.post(
        reverse(
            "documents:settings_member_edit",
            kwargs={
                "tenant_slug": tenant.slug,
                "membership_id": target_membership.id,
            },
        ),
        {
            "role": roles["viewer"].id,
            "is_active": "",
        },
    )

    assert response.status_code == 302
    target_membership.refresh_from_db()
    assert target_membership.role == roles["viewer"]
    assert target_membership.is_active is False


@pytest.mark.django_db
def test_tenant_member_cannot_access_members_settings(client):
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
        reverse("documents:settings_members", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 403
