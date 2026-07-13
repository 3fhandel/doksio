from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import transaction

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.documents.services import EnsureDefaultDocumentSpaces
from doksio.tenancy.models import Tenant


def get_default_tenant() -> Tenant | None:
    return Tenant.objects.filter(is_active=True).order_by("name", "id").first()


def get_default_tenant_for_user(
    user: AbstractBaseUser | AnonymousUser,
) -> Tenant | None:
    if not user.is_authenticated or not user.is_active:
        return None

    if user.is_superuser:
        return get_default_tenant()

    membership = (
        TenantMembership.objects.select_related("tenant")
        .filter(
            user=user,
            is_active=True,
            tenant__is_active=True,
        )
        .order_by("tenant__name", "tenant_id")
        .first()
    )
    if membership is None:
        return None
    return membership.tenant


def get_tenant_for_user(
    user: AbstractBaseUser | AnonymousUser,
    slug: str,
) -> Tenant | None:
    if not user.is_authenticated or not user.is_active:
        return None

    if user.is_superuser:
        return Tenant.objects.filter(slug=slug, is_active=True).first()

    membership = (
        TenantMembership.objects.select_related("tenant")
        .filter(
            user=user,
            is_active=True,
            tenant__slug=slug,
            tenant__is_active=True,
        )
        .first()
    )
    if membership is None:
        return None
    return membership.tenant


@dataclass(frozen=True)
class BootstrapDemoTenant:
    name: str = "Demo GmbH"
    slug: str = "demo"

    def execute(self) -> tuple[Tenant, bool]:
        tenant, created = Tenant.objects.get_or_create(
            slug=self.slug,
            defaults={
                "name": self.name,
                "is_active": True,
            },
        )
        ProvisionTenantDefaults(tenant=tenant).execute()
        return tenant, created


@dataclass(frozen=True)
class ProvisionTenantDefaults:
    tenant: Tenant

    @transaction.atomic
    def execute(self) -> None:
        EnsureDefaultTenantRoles(tenant=self.tenant).execute()
        EnsureDefaultDocumentSpaces(tenant=self.tenant).execute()
