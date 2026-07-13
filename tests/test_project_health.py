from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.project.version import build_version
from doksio.tenancy.models import Tenant


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
    assert "Build 20260713-1336" in response.content.decode()
    assert "app-mobile-sidebar-toggle" in response.content.decode()
    build_version.cache_clear()
