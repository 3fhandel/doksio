from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from doksio.accounts.models import TenantMembership
from doksio.tenancy.models import Tenant


@dataclass(frozen=True)
class AccessControl:
    user: AbstractBaseUser | AnonymousUser
    tenant: Tenant

    def can(self, permission_code: str) -> bool:
        if not self.user.is_authenticated or not self.user.is_active:
            return False
        if not self.tenant.is_active:
            return False
        if self.user.is_superuser:
            return True

        membership = self.membership
        if membership is None:
            return False
        if membership.roles.filter(
            is_active=True,
            permissions__code=permission_code,
        ).exists():
            return True
        return bool(
            membership.role.is_active
            and membership.role.permissions.filter(code=permission_code).exists()
        )

    @property
    def membership(self) -> TenantMembership | None:
        return (
            TenantMembership.objects.select_related("role", "tenant", "user")
            .prefetch_related("roles__permissions")
            .filter(
                user=self.user,
                tenant=self.tenant,
                is_active=True,
            )
            .first()
        )
