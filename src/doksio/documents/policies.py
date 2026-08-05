from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Q, QuerySet

from doksio.accounts.access import AccessControl
from doksio.accounts.permissions import TenantPermissions
from doksio.documents.models import (
    Document,
    DocumentFile,
    DocumentInbox,
    DocumentImportBatch,
    DocumentSpace,
)
from doksio.tenancy.models import Tenant


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
    if document.status == Document.Status.DELETED:
        return False
    if user.is_superuser:
        return True

    membership = AccessControl(user=user, tenant=document.tenant).membership
    if membership is None:
        return False

    if permission_code == TenantPermissions.DOCUMENTS_VIEW:
        supervisor_role_ids = list(
            membership.roles.filter(is_active=True).values_list("id", flat=True)
        )
        if membership.role_id and membership.role.is_active:
            supervisor_role_ids.append(membership.role_id)
        if supervisor_role_ids and document.workflow_instances.filter(
            template__supervisor_roles__id__in=set(supervisor_role_ids),
        ).exists():
            return True

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
    include_deleted: bool = False,
) -> QuerySet[Document]:
    if not user.is_authenticated or not user.is_active:
        return documents.none()
    if not include_deleted:
        documents = documents.exclude(status=Document.Status.DELETED)
    if user.is_superuser:
        return documents

    membership = AccessControl(user=user, tenant=tenant).membership
    if membership is None:
        return documents.none()

    supervisor_query = None
    if permission_code == TenantPermissions.DOCUMENTS_VIEW:
        supervisor_role_ids = list(
            membership.roles.filter(is_active=True).values_list("id", flat=True)
        )
        if membership.role_id and membership.role.is_active:
            supervisor_role_ids.append(membership.role_id)
        if supervisor_role_ids:
            supervisor_query = Q(
                workflow_instances__template__supervisor_roles__id__in=set(
                    supervisor_role_ids
                )
            )

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
        if supervisor_query is None:
            return documents.none()
        return documents.filter(supervisor_query).distinct()
    if supervisor_query is not None:
        allowed_space_query |= supervisor_query
    return documents.filter(allowed_space_query).distinct()


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


def filter_navigable_document_spaces_for_user(
    spaces: QuerySet[DocumentSpace],
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
    permission_code: str,
) -> QuerySet[DocumentSpace]:
    """Include accessible spaces and their ancestor shells for tree navigation."""
    accessible_spaces = filter_document_spaces_for_user(
        spaces,
        user,
        tenant,
        permission_code,
    )
    accessible_rows = list(accessible_spaces.values_list("id", "path"))
    if not accessible_rows:
        return spaces.none()

    accessible_ids = [space_id for space_id, _path in accessible_rows]
    ancestor_paths = set()
    for _space_id, path in accessible_rows:
        path_parts = [part for part in path.split("/") if part]
        for depth in range(1, len(path_parts)):
            ancestor_paths.add(f"/{'/'.join(path_parts[:depth])}")
    return spaces.filter(Q(id__in=accessible_ids) | Q(path__in=ancestor_paths))


def filter_document_inboxes_for_user(
    inboxes: QuerySet[DocumentInbox],
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
    permission_code: str = TenantPermissions.INBOXES_VIEW,
) -> QuerySet[DocumentInbox]:
    if not user.is_authenticated or not user.is_active or not tenant.is_active:
        return inboxes.none()
    if user.is_superuser:
        return inboxes

    membership = AccessControl(user=user, tenant=tenant).membership
    if membership is None:
        return inboxes.none()
    roles = membership.roles.filter(
        is_active=True,
        permissions__code=permission_code,
    ).distinct()
    if not roles.exists() and membership.role.is_active:
        roles = type(membership.role).objects.filter(
            id=membership.role_id,
            permissions__code=permission_code,
        )
    if not roles.exists():
        return inboxes.none()
    if roles.filter(
        permissions__code=TenantPermissions.INBOXES_ACCESS_ALL
    ).exists():
        return inboxes
    return inboxes.filter(access_roles__in=roles).distinct()


def can_access_document_inbox(
    user: AbstractBaseUser | AnonymousUser,
    inbox: DocumentInbox,
    permission_code: str = TenantPermissions.INBOXES_VIEW,
) -> bool:
    return filter_document_inboxes_for_user(
        DocumentInbox.objects.filter(id=inbox.id, tenant=inbox.tenant),
        user,
        inbox.tenant,
        permission_code,
    ).exists()


def can_access_document_import_batch(
    user: AbstractBaseUser | AnonymousUser,
    batch: DocumentImportBatch,
    permission_code: str = TenantPermissions.INBOXES_PROCESS,
) -> bool:
    if batch.inbox_id is None:
        return can_batch_import_documents(user, batch.tenant)
    return can_access_document_inbox(user, batch.inbox, permission_code)


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


def can_batch_import_documents(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> bool:
    return has_tenant_permission(
        user,
        tenant,
        TenantPermissions.DOCUMENTS_BATCH_IMPORT,
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


def can_delete_document(
    user: AbstractBaseUser | AnonymousUser,
    document: Document,
) -> bool:
    return has_document_permission(
        user,
        document,
        TenantPermissions.DOCUMENTS_DELETE,
    )


def can_split_document(
    user: AbstractBaseUser | AnonymousUser,
    document: Document,
) -> bool:
    return has_document_permission(
        user,
        document,
        TenantPermissions.DOCUMENTS_SPLIT,
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


def can_view_audit(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> bool:
    return has_tenant_permission(
        user,
        tenant,
        TenantPermissions.AUDIT_VIEW,
    )


def can_view_reports(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> bool:
    return has_tenant_permission(
        user,
        tenant,
        TenantPermissions.REPORTS_VIEW,
    )
