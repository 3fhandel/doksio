from __future__ import annotations

from django.conf import settings
from django.db import models


def default_keyboard_shortcuts() -> dict[str, str]:
    return {
        "dashboard": "Alt+1",
        "tasks": "Alt+2",
        "documents": "Alt+3",
        "search": "Alt+4",
        "upload": "Alt+U",
        "document_previous": "Alt+ArrowLeft",
        "document_next": "Alt+ArrowRight",
        "document_back": "Alt+Backspace",
        "document_edit_core": "Alt+E",
        "workflow_complete": "Alt+Enter",
    }


class UserProfile(models.Model):
    """Personal settings for a platform user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="doksio_profile",
    )
    display_name = models.CharField(max_length=150, blank=True)
    keyboard_shortcuts = models.JSONField(default=default_keyboard_shortcuts)
    notifications_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self) -> str:
        return f"Profil {self.user}"


class Notification(models.Model):
    """In-app notification for a tenant user."""

    class Type(models.TextChoices):
        WORKFLOW_TASK_CREATED = "workflow_task_created", "Workflow-Aufgabe erstellt"
        DOCUMENT_COMMENT_MENTION = (
            "document_comment_mention",
            "Kommentar-Erwähnung",
        )

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="doksio_notifications",
    )
    notification_type = models.CharField(max_length=80, choices=Type.choices)
    title = models.CharField(max_length=180)
    body = models.TextField(blank=True)
    link_url = models.CharField(max_length=500, blank=True)
    document = models.ForeignKey(
        "documents.Document",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    workflow_task = models.ForeignKey(
        "workflows.WorkflowTask",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    document_comment = models.ForeignKey(
        "documents.DocumentComment",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    read_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "recipient", "read_at"]),
            models.Index(fields=["recipient", "created_at"]),
            models.Index(fields=["workflow_task", "recipient"]),
            models.Index(fields=["document_comment", "recipient"]),
        ]

    def __str__(self) -> str:
        return self.title


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
