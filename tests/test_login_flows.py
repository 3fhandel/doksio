from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from doksio.accounts.models import TenantMembership, UserProfile
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.tenancy.models import Tenant


def test_system_paths_are_under_s_namespace():
    assert reverse("accounts:system_login") == "/s/"
    assert reverse("accounts:logout") == "/s/logout/"
    assert reverse("accounts:system_oidc_login") == "/s/oidc/login/"
    assert reverse("accounts:tenant_claim_oidc_login") == "/s/oidc/tenant-login/"
    assert reverse("accounts:oidc_callback") == "/s/oidc/callback/"
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
    assert reverse(
        "accounts:tenant_oidc_login",
        kwargs={"tenant_slug": tenant.slug},
    ) == "/t/acme/oidc/login/"


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


@pytest.mark.django_db
@override_settings(
    DOKSIO_OIDC_ENABLED=True,
    DOKSIO_OIDC_ISSUER_URL="https://auth.example.test/application/o/doksio",
    DOKSIO_OIDC_CLIENT_ID="client-id",
    DOKSIO_OIDC_CLIENT_SECRET="client-secret",
    DOKSIO_PUBLIC_BASE_URL="https://doksio.example.test",
)
def test_tenant_oidc_login_redirects_to_provider(client, monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    monkeypatch.setattr(
        "doksio.accounts.oidc.oidc_discovery",
        lambda: {
            "authorization_endpoint": "https://auth.example.test/authorize",
        },
    )

    response = client.get(
        reverse("accounts:tenant_oidc_login", kwargs={"tenant_slug": tenant.slug})
        + "?next=/t/acme/search/"
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith("https://auth.example.test/authorize?")
    assert "client_id=client-id" in location
    assert "scope=openid+email+profile" in location
    assert (
        "redirect_uri=https%3A%2F%2Fdoksio.example.test%2Fs%2Foidc%2Fcallback%2F"
        in location
    )
    oidc_context = client.session["doksio_oidc_login"]
    assert oidc_context["tenant_slug"] == tenant.slug
    assert oidc_context["next_url"] == "/t/acme/search/"


@pytest.mark.django_db
@override_settings(
    DOKSIO_OIDC_ENABLED=True,
    DOKSIO_OIDC_ISSUER_URL="https://auth.example.test/application/o/doksio",
    DOKSIO_OIDC_CLIENT_ID="client-id",
    DOKSIO_OIDC_CLIENT_SECRET="client-secret",
    DOKSIO_PUBLIC_BASE_URL="https://doksio.example.test",
)
def test_tenant_claim_oidc_login_redirects_to_provider(client, monkeypatch):
    monkeypatch.setattr(
        "doksio.accounts.oidc.oidc_discovery",
        lambda: {
            "authorization_endpoint": "https://auth.example.test/authorize",
        },
    )

    response = client.get(reverse("accounts:tenant_claim_oidc_login"))

    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith("https://auth.example.test/authorize?")
    oidc_context = client.session["doksio_oidc_login"]
    assert oidc_context["mode"] == "tenant_claim"
    assert oidc_context["tenant_slug"] == ""


@pytest.mark.django_db
@override_settings(
    DOKSIO_OIDC_ENABLED=True,
    DOKSIO_OIDC_ISSUER_URL="https://auth.example.test/application/o/doksio",
    DOKSIO_OIDC_CLIENT_ID="client-id",
    DOKSIO_OIDC_CLIENT_SECRET="client-secret",
)
def test_oidc_callback_logs_in_existing_tenant_member(client, monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        email="alice@example.test",
    )
    TenantMembership.objects.create(tenant=tenant, user=user, role=roles["member"])
    session = client.session
    session["doksio_oidc_login"] = {
        "state": "state-token",
        "nonce": "nonce-token",
        "mode": "tenant",
        "tenant_slug": tenant.slug,
        "next_url": "/t/acme/search/",
    }
    session.save()
    monkeypatch.setattr(
        "doksio.accounts.views.exchange_oidc_code",
        lambda code: {"access_token": f"access-{code}"},
    )
    monkeypatch.setattr(
        "doksio.accounts.views.fetch_oidc_userinfo",
        lambda _token: {
            "sub": "authentik-user-1",
            "preferred_username": "alice",
            "email": "alice@example.test",
            "given_name": "Alice",
            "family_name": "Beispiel",
            "name": "Alice Beispiel",
        },
    )

    response = client.get(
        reverse("accounts:oidc_callback") + "?code=abc&state=state-token"
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/t/acme/search/"
    user.refresh_from_db()
    profile = UserProfile.objects.get(user=user)
    assert user.first_name == "Alice"
    assert user.last_name == "Beispiel"
    assert profile.display_name == "Alice Beispiel"
    assert profile.oidc_subject == "authentik-user-1"


@pytest.mark.django_db
@override_settings(
    DOKSIO_OIDC_ENABLED=True,
    DOKSIO_OIDC_ISSUER_URL="https://auth.example.test/application/o/doksio",
    DOKSIO_OIDC_CLIENT_ID="client-id",
    DOKSIO_OIDC_CLIENT_SECRET="client-secret",
    DOKSIO_OIDC_TENANT_CLAIM="doksio_tenants",
)
def test_oidc_callback_uses_tenant_claim_for_generic_login(client, monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        email="alice@example.test",
    )
    TenantMembership.objects.create(tenant=tenant, user=user, role=roles["member"])
    session = client.session
    session["doksio_oidc_login"] = {
        "state": "state-token",
        "nonce": "nonce-token",
        "mode": "tenant_claim",
        "tenant_slug": "",
        "next_url": "",
    }
    session.save()
    monkeypatch.setattr(
        "doksio.accounts.views.exchange_oidc_code",
        lambda _code: {"access_token": "access-token"},
    )
    monkeypatch.setattr(
        "doksio.accounts.views.fetch_oidc_userinfo",
        lambda _token: {
            "sub": "authentik-user-1",
            "preferred_username": "alice",
            "email": "alice@example.test",
            "doksio_tenants": ["acme"],
        },
    )

    response = client.get(
        reverse("accounts:oidc_callback") + "?code=abc&state=state-token"
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse(
        "documents:dashboard",
        kwargs={"tenant_slug": tenant.slug},
    )


@pytest.mark.django_db
@override_settings(
    DOKSIO_OIDC_ENABLED=True,
    DOKSIO_OIDC_ISSUER_URL="https://auth.example.test/application/o/doksio",
    DOKSIO_OIDC_CLIENT_ID="client-id",
    DOKSIO_OIDC_CLIENT_SECRET="client-secret",
)
def test_system_oidc_callback_requires_superuser(client, monkeypatch):
    get_user_model().objects.create_user(
        username="alice",
        email="alice@example.test",
    )
    session = client.session
    session["doksio_oidc_login"] = {
        "state": "state-token",
        "nonce": "nonce-token",
        "mode": "system",
        "tenant_slug": "",
        "next_url": "",
    }
    session.save()
    monkeypatch.setattr(
        "doksio.accounts.views.exchange_oidc_code",
        lambda _code: {"access_token": "access-token"},
    )
    monkeypatch.setattr(
        "doksio.accounts.views.fetch_oidc_userinfo",
        lambda _token: {
            "sub": "authentik-user-1",
            "preferred_username": "alice",
            "email": "alice@example.test",
        },
    )

    response = client.get(
        reverse("accounts:oidc_callback") + "?code=abc&state=state-token"
    )

    assert response.status_code == 403
