from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from doksio.accounts.models import (
    Notification,
    TenantMembership,
    TenantPermission,
    TenantRole,
    UserProfile,
)
from doksio.accounts.permissions import DEFAULT_ROLE_PERMISSIONS, PERMISSION_DEFINITIONS
from doksio.audit.services import RecordAuditEvent
from doksio.documents.models import DocumentSpace
from doksio.tenancy.models import Tenant


@dataclass(frozen=True)
class EnsureTenantPermissionCatalog:
    def execute(self) -> dict[str, TenantPermission]:
        permissions: dict[str, TenantPermission] = {}
        for definition in PERMISSION_DEFINITIONS:
            permission, _created = TenantPermission.objects.update_or_create(
                code=definition.code,
                defaults={
                    "label": definition.label,
                    "category": definition.category,
                    "description": definition.description,
                    "sort_order": definition.sort_order,
                },
            )
            permissions[permission.code] = permission
        return permissions


@dataclass(frozen=True)
class EnsureDefaultTenantRoles:
    tenant: Tenant

    def execute(self) -> dict[str, TenantRole]:
        permission_catalog = EnsureTenantPermissionCatalog().execute()
        role_specs = {
            "admin": ("Admin", "Tenant administrator"),
            "member": ("Member", "Can upload and use documents"),
            "viewer": ("Viewer", "Read-only document access"),
        }
        roles: dict[str, TenantRole] = {}

        for slug, (name, description) in role_specs.items():
            role, created = TenantRole.objects.get_or_create(
                tenant=self.tenant,
                slug=slug,
                defaults={
                    "name": name,
                    "description": description,
                    "is_system_role": True,
                    "is_active": True,
                },
            )
            default_permissions = [
                permission_catalog[code]
                for code in DEFAULT_ROLE_PERMISSIONS[slug]
                if code in permission_catalog
            ]
            if created or not role.permissions.exists():
                role.permissions.set(default_permissions)
            roles[slug] = role

        return roles


@dataclass(frozen=True)
class AddTenantMember:
    tenant: Tenant
    username: str
    role: TenantRole | None = None
    roles: list[TenantRole] | None = None
    email: str = ""
    password: str = ""
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> TenantMembership:
        roles = list(self.roles or ([self.role] if self.role else []))
        if not roles:
            raise ValueError("At least one tenant role is required.")
        if any(role.tenant_id != self.tenant.id for role in roles):
            raise ValueError("Role does not belong to tenant.")

        user_model = get_user_model()
        user, user_created = user_model.objects.get_or_create(
            username=self.username,
            defaults={
                "email": self.email,
                "is_staff": False,
                "is_superuser": False,
            },
        )
        if user_created:
            user.set_password(self.password)
            user.save(update_fields=["password"])
        else:
            update_fields = []
            if self.email and user.email != self.email:
                user.email = self.email
                update_fields.append("email")
            if self.password:
                user.set_password(self.password)
                update_fields.append("password")
            if update_fields:
                user.save(update_fields=update_fields)

        primary_role = roles[0]
        membership, created = TenantMembership.objects.get_or_create(
            tenant=self.tenant,
            user=user,
            defaults={
                "role": primary_role,
                "is_active": True,
            },
        )

        previous_role_id = membership.role_id
        previous_active = membership.is_active
        if not created:
            membership.role = primary_role
            membership.is_active = True
            membership.save(update_fields=["role", "is_active", "updated_at"])
        membership.roles.set(roles)

        RecordAuditEvent(
            tenant=self.tenant,
            actor=self.actor,
            event_type=(
                "tenant_membership.created"
                if created
                else "tenant_membership.reactivated"
            ),
            object_type="accounts.TenantMembership",
            object_id=str(membership.id),
            data={
                "user_id": user.id,
                "username": user.get_username(),
                "role_id": membership.role_id,
                "role_slugs": list(membership.roles.values_list("slug", flat=True)),
                "previous_role_id": previous_role_id if not created else None,
                "previous_active": previous_active if not created else None,
                "user_created": user_created,
            },
        ).execute()

        return membership


@dataclass(frozen=True)
class UpdateTenantMembership:
    membership: TenantMembership
    is_active: bool
    role: TenantRole | None = None
    roles: list[TenantRole] | None = None
    email: str = ""
    password: str = ""
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> TenantMembership:
        roles = list(self.roles or ([self.role] if self.role else []))
        if not roles:
            raise ValueError("At least one tenant role is required.")
        if any(role.tenant_id != self.membership.tenant_id for role in roles):
            raise ValueError("Role does not belong to membership tenant.")

        previous_role_id = self.membership.role_id
        previous_role_slugs = list(self.membership.roles.values_list("slug", flat=True))
        previous_active = self.membership.is_active

        user = self.membership.user
        user_update_fields = []
        if self.email != user.email:
            user.email = self.email
            user_update_fields.append("email")
        if self.password:
            user.set_password(self.password)
            user_update_fields.append("password")
        if user_update_fields:
            user.save(update_fields=user_update_fields)

        self.membership.role = roles[0]
        self.membership.is_active = self.is_active
        self.membership.save(update_fields=["role", "is_active", "updated_at"])
        self.membership.roles.set(roles)

        RecordAuditEvent(
            tenant=self.membership.tenant,
            actor=self.actor,
            event_type="tenant_membership.updated",
            object_type="accounts.TenantMembership",
            object_id=str(self.membership.id),
            data={
                "user_id": self.membership.user_id,
                "username": self.membership.user.get_username(),
                "role_id": self.membership.role_id,
                "role_slugs": list(
                    self.membership.roles.values_list("slug", flat=True)
                ),
                "previous_role_slugs": previous_role_slugs,
                "previous_role_id": previous_role_id,
                "is_active": self.membership.is_active,
                "previous_active": previous_active,
            },
        ).execute()

        return self.membership


@dataclass(frozen=True)
class CreateTenantRole:
    tenant: Tenant
    name: str
    slug: str
    permissions: list[TenantPermission]
    document_spaces: list[DocumentSpace] | None = None
    can_access_all_document_spaces: bool = True
    description: str = ""
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> TenantRole:
        role = TenantRole.objects.create(
            tenant=self.tenant,
            name=self.name,
            slug=self.slug,
            description=self.description,
            can_access_all_document_spaces=self.can_access_all_document_spaces,
        )
        role.permissions.set(self.permissions)
        role.document_spaces.set(self.document_spaces or [])
        RecordAuditEvent(
            tenant=self.tenant,
            actor=self.actor,
            event_type="tenant_role.created",
            object_type="accounts.TenantRole",
            object_id=str(role.id),
            data={
                "name": role.name,
                "slug": role.slug,
                "permissions": list(role.permissions.values_list("code", flat=True)),
                "document_space_ids": list(
                    role.document_spaces.values_list("id", flat=True)
                ),
                "can_access_all_document_spaces": (
                    role.can_access_all_document_spaces
                ),
            },
        ).execute()
        return role


@dataclass(frozen=True)
class UpdateTenantRole:
    role: TenantRole
    name: str
    description: str
    permissions: list[TenantPermission]
    is_active: bool
    document_spaces: list[DocumentSpace] | None = None
    can_access_all_document_spaces: bool = True
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> TenantRole:
        previous_permissions = list(
            self.role.permissions.values_list("code", flat=True)
        )
        previous_document_space_ids = list(
            self.role.document_spaces.values_list("id", flat=True)
        )
        previous_can_access_all_document_spaces = (
            self.role.can_access_all_document_spaces
        )
        previous_active = self.role.is_active
        previous_name = self.role.name

        self.role.name = self.name
        self.role.description = self.description
        self.role.can_access_all_document_spaces = (
            self.can_access_all_document_spaces
        )
        self.role.is_active = self.is_active
        self.role.save(
            update_fields=[
                "name",
                "description",
                "can_access_all_document_spaces",
                "is_active",
                "updated_at",
            ]
        )
        self.role.permissions.set(self.permissions)
        self.role.document_spaces.set(self.document_spaces or [])

        RecordAuditEvent(
            tenant=self.role.tenant,
            actor=self.actor,
            event_type="tenant_role.updated",
            object_type="accounts.TenantRole",
            object_id=str(self.role.id),
            data={
                "name": self.role.name,
                "previous_name": previous_name,
                "slug": self.role.slug,
                "permissions": list(
                    self.role.permissions.values_list("code", flat=True)
                ),
                "previous_permissions": previous_permissions,
                "document_space_ids": list(
                    self.role.document_spaces.values_list("id", flat=True)
                ),
                "previous_document_space_ids": previous_document_space_ids,
                "can_access_all_document_spaces": (
                    self.role.can_access_all_document_spaces
                ),
                "previous_can_access_all_document_spaces": (
                    previous_can_access_all_document_spaces
                ),
                "is_active": self.role.is_active,
                "previous_active": previous_active,
            },
        ).execute()
        return self.role


@dataclass(frozen=True)
class CreateNotification:
    tenant: Tenant
    recipient: get_user_model()
    notification_type: str
    title: str
    body: str = ""
    link_url: str = ""
    document: object | None = None
    workflow_task: object | None = None
    document_comment: object | None = None

    @transaction.atomic
    def execute(self) -> Notification | None:
        profile = UserProfile.objects.filter(user=self.recipient).first()
        if profile is not None and not profile.notifications_enabled:
            return None

        notification, _created = Notification.objects.get_or_create(
            tenant=self.tenant,
            recipient=self.recipient,
            notification_type=self.notification_type,
            workflow_task=self.workflow_task,
            document_comment=self.document_comment,
            defaults={
                "title": self.title,
                "body": self.body,
                "link_url": self.link_url,
                "document": self.document,
            },
        )
        return notification


@dataclass(frozen=True)
class MarkNotificationRead:
    notification: Notification
    actor: get_user_model()

    @transaction.atomic
    def execute(self) -> Notification:
        if self.notification.recipient_id != self.actor.id:
            raise PermissionError("Cannot mark another user's notification as read.")
        if self.notification.read_at is None:
            self.notification.read_at = timezone.now()
            self.notification.save(update_fields=["read_at"])
        return self.notification


@dataclass(frozen=True)
class MarkAllNotificationsRead:
    tenant: Tenant
    actor: get_user_model()

    @transaction.atomic
    def execute(self) -> int:
        return Notification.objects.filter(
            tenant=self.tenant,
            recipient=self.actor,
            read_at__isnull=True,
        ).update(read_at=timezone.now())
