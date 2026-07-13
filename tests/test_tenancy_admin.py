from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from doksio.accounts.models import TenantRole
from doksio.documents.models import DocumentSpace
from doksio.tenancy.models import Tenant
from doksio.tenancy.services import ProvisionTenantDefaults


@pytest.mark.django_db
def test_provision_tenant_defaults_is_idempotent():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")

    ProvisionTenantDefaults(tenant=tenant).execute()
    ProvisionTenantDefaults(tenant=tenant).execute()

    assert set(TenantRole.objects.filter(tenant=tenant).values_list("slug", flat=True)) == {
        "admin",
        "member",
        "viewer",
    }
    assert DocumentSpace.objects.filter(tenant=tenant).count() == 6


@pytest.mark.django_db
def test_system_admin_can_create_tenant_with_default_structure(client):
    admin_user = get_user_model().objects.create_superuser(
        username="admin",
        password="secret",
    )
    client.force_login(admin_user)

    response = client.post(
        reverse("admin:tenancy_tenant_add"),
        {
            "name": "Muster GmbH",
            "slug": "muster",
            "is_active": "on",
            "_save": "Speichern",
        },
    )

    tenant = Tenant.objects.get(slug="muster")
    assert response.status_code == 302
    assert tenant.name == "Muster GmbH"
    assert set(TenantRole.objects.filter(tenant=tenant).values_list("slug", flat=True)) == {
        "admin",
        "member",
        "viewer",
    }
    assert DocumentSpace.objects.filter(tenant=tenant).count() == 6


@pytest.mark.django_db
def test_system_admin_tenant_changelist_shows_operational_overview(client):
    admin_user = get_user_model().objects.create_superuser(
        username="admin",
        password="secret",
    )
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    ProvisionTenantDefaults(tenant=tenant).execute()
    client.force_login(admin_user)

    response = client.get(reverse("admin:tenancy_tenant_changelist"))

    content = response.content.decode()
    assert response.status_code == 200
    assert "/t/acme/dashboard/" in content
    assert "Dokumentenboxen" in content
    assert "Benutzer" in content
