from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.project.version import build_version
from doksio.tenancy.models import Tenant


def test_production_middleware_keeps_request_timing_enabled():
    from doksio.project.settings.production import MIDDLEWARE

    assert MIDDLEWARE[0] == "doksio.project.middleware.RequestTimingMiddleware"
    assert MIDDLEWARE.count(
        "doksio.project.middleware.RequestTimingMiddleware"
    ) == 1
    assert MIDDLEWARE.count("django.middleware.security.SecurityMiddleware") == 1
    assert MIDDLEWARE.index(
        "whitenoise.middleware.WhiteNoiseMiddleware"
    ) < MIDDLEWARE.index("django.contrib.sessions.middleware.SessionMiddleware")


def test_health_endpoint(client):
    response = client.get("/s/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
@override_settings(DOKSIO_BUILD_VERSION="20260713-1336")
def test_topbar_shows_build_version(client):
    build_version.cache_clear()
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
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

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Build 20260713-1336" in content
    assert 'data-bs-target="#doksioChangelogModal"' in content
    assert 'id="doksioChangelogModal"' in content
    assert "Doksio Änderungsprotokoll" in content
    assert "Neuerungen" in content
    assert "app-mobile-sidebar-toggle" in content
    build_version.cache_clear()


@pytest.mark.django_db
def test_page_footer_shows_rendering_time(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(username="alice")
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["viewer"],
    )
    client.force_login(user)

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert (
        f"Doksio DMS &copy; {timezone.now().year} - Sebastian Walter -"
        in content
    )
    assert re.search(r"Page Rendering Time \d+\.\d{3} s", content)
    assert re.fullmatch(r"app;dur=\d+\.\d{2}", response["Server-Timing"])
