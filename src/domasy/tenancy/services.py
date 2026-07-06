from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from domasy.accounts.models import TenantMembership
from domasy.accounts.services import EnsureDefaultTenantRoles
from domasy.documents.services import EnsureDefaultDocumentSpaces
from domasy.tenancy.models import Tenant


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
        EnsureDefaultTenantRoles(tenant=tenant).execute()
        EnsureDefaultDocumentSpaces(tenant=tenant).execute()
        return tenant, created
