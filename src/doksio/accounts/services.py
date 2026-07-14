from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

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
from doksio.ingestion.models import TenantSmtpSettings
from doksio.project.url_helpers import build_public_url
from doksio.tenancy.models import Tenant


def _tenant_smtp_from_email(smtp_settings: TenantSmtpSettings) -> str:
    from_email = smtp_settings.from_email or smtp_settings.username
    if smtp_settings.from_name and from_email:
        return f"{smtp_settings.from_name} <{from_email}>"
    return from_email


def _tenant_smtp_connection(smtp_settings: TenantSmtpSettings):
    return get_connection(
        backend="django.core.mail.backends.smtp.EmailBackend",
        host=smtp_settings.host,
        port=smtp_settings.port,
        username=smtp_settings.username or None,
        password=smtp_settings.password or None,
        use_tls=smtp_settings.security == TenantSmtpSettings.Security.STARTTLS,
        use_ssl=smtp_settings.security == TenantSmtpSettings.Security.SSL,
    )


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
    display_name: str = ""
    first_name: str = ""
    last_name: str = ""
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
                "first_name": self.first_name,
                "last_name": self.last_name,
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
            if self.first_name != user.first_name:
                user.first_name = self.first_name
                update_fields.append("first_name")
            if self.last_name != user.last_name:
                user.last_name = self.last_name
                update_fields.append("last_name")
            if self.password:
                user.set_password(self.password)
                update_fields.append("password")
            if update_fields:
                user.save(update_fields=update_fields)
        if self.display_name:
            profile, _created = UserProfile.objects.get_or_create(user=user)
            if profile.display_name != self.display_name:
                profile.display_name = self.display_name
                profile.save(update_fields=["display_name", "updated_at"])

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
    display_name: str = ""
    first_name: str = ""
    last_name: str = ""
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
        if self.first_name != user.first_name:
            user.first_name = self.first_name
            user_update_fields.append("first_name")
        if self.last_name != user.last_name:
            user.last_name = self.last_name
            user_update_fields.append("last_name")
        if self.password:
            user.set_password(self.password)
            user_update_fields.append("password")
        if user_update_fields:
            user.save(update_fields=user_update_fields)
        profile, _created = UserProfile.objects.get_or_create(user=user)
        if profile.display_name != self.display_name:
            profile.display_name = self.display_name
            profile.save(update_fields=["display_name", "updated_at"])

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
                "display_name": profile.display_name,
            },
        ).execute()

        return self.membership


@dataclass(frozen=True)
class SendTenantPasswordResetEmail:
    tenant: Tenant
    membership: TenantMembership
    actor: get_user_model() | None = None

    def execute(self) -> None:
        if self.membership.tenant_id != self.tenant.id:
            raise ValueError("Membership does not belong to tenant.")
        user = self.membership.user
        if not user.email:
            raise ValueError("Für diesen Benutzer ist keine E-Mail-Adresse hinterlegt.")
        smtp_settings = TenantSmtpSettings.objects.filter(
            tenant=self.tenant,
            is_active=True,
        ).first()
        if smtp_settings is None:
            raise ValueError(
                "Für diesen Mandanten ist kein aktiver SMTP-Versand konfiguriert."
            )

        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_path = reverse(
            "accounts:tenant_password_reset_confirm",
            kwargs={
                "tenant_slug": self.tenant.slug,
                "uidb64": uidb64,
                "token": token,
            },
        )
        reset_url = build_public_url(reset_path)
        context = {
            "tenant": self.tenant,
            "user": user,
            "reset_url": reset_url,
            "actor": self.actor,
        }
        subject = render_to_string(
            "accounts/password_reset_email_subject.txt",
            context,
        ).strip()
        body = render_to_string(
            "accounts/password_reset_email.txt",
            context,
        )
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=_tenant_smtp_from_email(smtp_settings),
            to=[user.email],
            connection=_tenant_smtp_connection(smtp_settings),
        )
        message.send()
        RecordAuditEvent(
            tenant=self.tenant,
            actor=self.actor,
            event_type="tenant_membership.password_reset_email_sent",
            object_type="accounts.TenantMembership",
            object_id=str(self.membership.id),
            data={
                "user_id": user.id,
                "username": user.get_username(),
                "email": user.email,
            },
        ).execute()


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
        if (
            profile is not None
            and self.notification_type == Notification.Type.DOCUMENT_COMMENT_MENTION
            and not profile.mention_notifications_enabled
        ):
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
