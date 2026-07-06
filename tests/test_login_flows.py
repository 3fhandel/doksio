from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from domasy.accounts.models import TenantMembership
from domasy.accounts.services import EnsureDefaultTenantRoles
from domasy.tenancy.models import Tenant


def test_system_paths_are_under_s_namespace():
    assert reverse("accounts:system_login") == "/s/"
    assert reverse("accounts:logout") == "/s/logout/"
    assert reverse("admin:index") == "/s/admin/"
    assert reverse("documents:dashboard_redirect") == "/s/dashboard/"
    assert reverse("health") == "/s/health/"


@pytest.mark.django_db
def test_tenant_login_path_is_tenant_root():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")

    assert reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant.slug}) == (
        "/t/acme/"
    )
    assert reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug}) == (
        "/t/acme/dashboard/"
    )


@pytest.mark.django_db
def test_tenant_document_page_redirects_anonymous_user_to_tenant_login(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")

    response = client.get(
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 302
    assert response.headers["Location"].startswith(
        reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant.slug})
    )


@pytest.mark.django_db
def test_tenant_login_allows_active_tenant_member(client):
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

    response = client.post(
        reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant.slug}),
        {
            "username": "alice",
            "password": "secret",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse(
        "documents:dashboard",
        kwargs={"tenant_slug": tenant.slug},
    )


@pytest.mark.django_db
def test_tenant_login_rejects_user_without_membership(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )

    response = client.post(
        reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant.slug}),
        {
            "username": "alice",
            "password": "secret",
        },
    )

    assert response.status_code == 200
    assert "keinen Zugriff" in response.content.decode()


@pytest.mark.django_db
def test_system_login_allows_superuser(client):
    get_user_model().objects.create_superuser(
        username="admin",
        password="secret",
    )

    response = client.post(
        reverse("accounts:system_login"),
        {
            "username": "admin",
            "password": "secret",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("admin:index")


@pytest.mark.django_db
def test_system_login_rejects_regular_user(client):
    get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )

    response = client.post(
        reverse("accounts:system_login"),
        {
            "username": "alice",
            "password": "secret",
        },
    )

    assert response.status_code == 200
    assert "nur für System-Admins" in response.content.decode()
