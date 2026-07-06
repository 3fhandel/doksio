from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Q, QuerySet

from domasy.accounts.access import AccessControl
from domasy.accounts.permissions import TenantPermissions
from domasy.documents.models import Document, DocumentFile, DocumentSpace
from domasy.tenancy.models import Tenant


def is_system_admin(user: AbstractBaseUser | AnonymousUser) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_superuser)


def get_tenant_role(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> str | None:
    if not user.is_authenticated or not user.is_active or not tenant.is_active:
        return None

    membership = AccessControl(user=user, tenant=tenant).membership
    if membership is None:
        return None
    role = membership.roles.filter(is_active=True).order_by("name").first()
    return role.slug if role else membership.role.slug


def has_tenant_permission(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
    permission_code: str,
) -> bool:
    return AccessControl(user=user, tenant=tenant).can(permission_code)


def _role_allows_document_space(role, document: Document) -> bool:
    return _role_allows_space(role, document.space)


def _role_allows_space(role, document_space: DocumentSpace) -> bool:
    if role.can_access_all_document_spaces:
        return True
    spaces = list(role.document_spaces.all())
    return any(
        document_space.id == space.id
        or document_space.path.startswith(f"{space.path.rstrip('/')}/")
        for space in spaces
    )


def has_document_permission(
    user: AbstractBaseUser | AnonymousUser,
    document: Document,
    permission_code: str,
) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    if not document.tenant.is_active:
        return False
    if user.is_superuser:
        return True

    membership = AccessControl(user=user, tenant=document.tenant).membership
    if membership is None:
        return False

    roles = list(membership.roles.filter(is_active=True).prefetch_related(
        "permissions",
        "document_spaces",
    ))
    if not roles and membership.role.is_active:
        roles = [
            type(membership.role)
            .objects.prefetch_related("permissions", "document_spaces")
            .get(id=membership.role_id)
        ]

    return any(
        role.permissions.filter(code=permission_code).exists()
        and _role_allows_document_space(role, document)
        for role in roles
    )


def filter_documents_for_user(
    documents: QuerySet[Document],
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
    permission_code: str = TenantPermissions.DOCUMENTS_VIEW,
) -> QuerySet[Document]:
    if not user.is_authenticated or not user.is_active:
        return documents.none()
    if user.is_superuser:
        return documents

    membership = AccessControl(user=user, tenant=tenant).membership
    if membership is None:
        return documents.none()

    roles = membership.roles.filter(
        is_active=True,
        permissions__code=permission_code,
    ).prefetch_related("document_spaces")
    if roles.filter(can_access_all_document_spaces=True).exists():
        return documents

    allowed_space_query = Q()
    has_allowed_spaces = False
    for role in roles:
        for space in role.document_spaces.all():
            has_allowed_spaces = True
            allowed_space_query |= Q(space_id=space.id) | Q(
                space__path__startswith=f"{space.path.rstrip('/')}/"
            )
    if not has_allowed_spaces:
        return documents.none()
    return documents.filter(allowed_space_query)


def filter_document_spaces_for_user(
    spaces: QuerySet[DocumentSpace],
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
    permission_code: str,
) -> QuerySet[DocumentSpace]:
    if not user.is_authenticated or not user.is_active:
        return spaces.none()
    if user.is_superuser:
        return spaces

    membership = AccessControl(user=user, tenant=tenant).membership
    if membership is None:
        return spaces.none()

    roles = membership.roles.filter(
        is_active=True,
        permissions__code=permission_code,
    ).prefetch_related("document_spaces")
    if roles.filter(can_access_all_document_spaces=True).exists():
        return spaces

    allowed_space_query = Q()
    has_allowed_spaces = False
    for role in roles:
        for space in role.document_spaces.all():
            has_allowed_spaces = True
            allowed_space_query |= Q(id=space.id) | Q(
                path__startswith=f"{space.path.rstrip('/')}/"
            )
    if not has_allowed_spaces:
        return spaces.none()
    return spaces.filter(allowed_space_query)


def has_tenant_role(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
    allowed_roles: set[str],
) -> bool:
    if is_system_admin(user):
        return tenant.is_active
    membership = AccessControl(user=user, tenant=tenant).membership
    if membership is None:
        return False
    if membership.roles.filter(is_active=True, slug__in=allowed_roles).exists():
        return True
    return membership.role.is_active and membership.role.slug in allowed_roles


def can_administer_tenant(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> bool:
    return has_tenant_permission(
        user=user,
        tenant=tenant,
        permission_code=TenantPermissions.SETTINGS_VIEW,
    )


def can_upload_document(user: AbstractBaseUser | AnonymousUser, tenant: Tenant) -> bool:
    return has_tenant_permission(
        user,
        tenant,
        TenantPermissions.DOCUMENTS_UPLOAD,
    )


def can_view_document(
    user: AbstractBaseUser | AnonymousUser,
    document: Document,
) -> bool:
    return has_document_permission(
        user,
        document,
        TenantPermissions.DOCUMENTS_VIEW,
    )


def can_download_document_file(
    user: AbstractBaseUser | AnonymousUser,
    document_file: DocumentFile,
) -> bool:
    return has_document_permission(
        user,
        document_file.document,
        TenantPermissions.DOCUMENTS_DOWNLOAD,
    )


def can_manage_members(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> bool:
    return has_tenant_permission(
        user,
        tenant,
        TenantPermissions.SETTINGS_MEMBERS_MANAGE,
    )


def can_manage_roles(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> bool:
    return has_tenant_permission(
        user,
        tenant,
        TenantPermissions.SETTINGS_ROLES_MANAGE,
    )


def can_manage_document_spaces(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> bool:
    return has_tenant_permission(
        user,
        tenant,
        TenantPermissions.DOCUMENT_SPACES_MANAGE,
    )
