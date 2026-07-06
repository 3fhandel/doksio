from __future__ import annotations

from django.conf import settings
from django.db import models


class TenantPermission(models.Model):
    """Permission code that can be assigned to tenant-scoped roles."""

    code = models.CharField(max_length=120, unique=True)
    label = models.CharField(max_length=255)
    category = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["category", "sort_order", "code"]

    def __str__(self) -> str:
        return self.label


class TenantRole(models.Model):
    """Tenant-scoped role with an explicit permission set."""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="roles",
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(
        TenantPermission,
        blank=True,
        related_name="roles",
    )
    document_spaces = models.ManyToManyField(
        "documents.DocumentSpace",
        blank=True,
        related_name="access_roles",
    )
    can_access_all_document_spaces = models.BooleanField(default=True)
    is_system_role = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "slug"],
                name="unique_tenant_role_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.name


class TenantMembership(models.Model):
    """Connects a platform user to one tenant with a tenant-scoped role."""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenant_memberships",
    )
    role = models.ForeignKey(
        TenantRole,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    roles = models.ManyToManyField(
        TenantRole,
        blank=True,
        related_name="multi_role_memberships",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant__name", "user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "user"],
                name="unique_tenant_user_membership",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "role"]),
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.tenant} ({self.role})"

    def save(self, *args, **kwargs) -> None:
        super().save(*args, **kwargs)
        if self.role_id and not self.roles.filter(id=self.role_id).exists():
            self.roles.add(self.role)
