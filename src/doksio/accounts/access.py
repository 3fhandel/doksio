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

        permission_cache = getattr(
            self.user,
            "_doksio_tenant_permission_cache",
            None,
        )
        if permission_cache is None:
            permission_cache = {}
            self.user._doksio_tenant_permission_cache = permission_cache
        cache_key = (self.tenant.id, permission_code)
        if cache_key in permission_cache:
            return permission_cache[cache_key]

        membership = self.membership
        if membership is None:
            permission_cache[cache_key] = False
            return False
        allowed = any(
            role.is_active
            and any(
                permission.code == permission_code
                for permission in role.permissions.all()
            )
            for role in membership.roles.all()
        )
        if not allowed and membership.role.is_active:
            allowed = any(
                permission.code == permission_code
                for permission in membership.role.permissions.all()
            )
        permission_cache[cache_key] = allowed
        return allowed

    @property
    def membership(self) -> TenantMembership | None:
        membership_cache = getattr(
            self.user,
            "_doksio_tenant_membership_cache",
            None,
        )
        if membership_cache is None:
            membership_cache = {}
            self.user._doksio_tenant_membership_cache = membership_cache
        if self.tenant.id in membership_cache:
            return membership_cache[self.tenant.id]

        membership = (
            TenantMembership.objects.select_related("role", "tenant", "user")
            .prefetch_related("role__permissions", "roles__permissions")
            .filter(
                user=self.user,
                tenant=self.tenant,
                is_active=True,
            )
            .first()
        )
        membership_cache[self.tenant.id] = membership
        return membership
