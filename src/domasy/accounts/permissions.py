from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDefinition:
    code: str
    label: str
    category: str
    sort_order: int
    description: str = ""


class TenantPermissions:
    DOCUMENTS_VIEW = "documents.view"
    DOCUMENTS_UPLOAD = "documents.upload"
    DOCUMENTS_DOWNLOAD = "documents.download"
    DOCUMENT_SPACES_MANAGE = "document_spaces.manage"
    SETTINGS_VIEW = "settings.view"
    SETTINGS_MEMBERS_MANAGE = "settings.members.manage"
    SETTINGS_ROLES_MANAGE = "settings.roles.manage"
    AUDIT_VIEW = "audit.view"
    WORKFLOWS_USE = "workflows.use"
    WORKFLOWS_MANAGE = "workflows.manage"


PERMISSION_DEFINITIONS = [
    PermissionDefinition(
        code=TenantPermissions.DOCUMENTS_VIEW,
        label="Dokumente anzeigen",
        category="Dokumente",
        sort_order=10,
    ),
    PermissionDefinition(
        code=TenantPermissions.DOCUMENTS_UPLOAD,
        label="Dokumente hochladen",
        category="Dokumente",
        sort_order=20,
    ),
    PermissionDefinition(
        code=TenantPermissions.DOCUMENTS_DOWNLOAD,
        label="Dokumente herunterladen",
        category="Dokumente",
        sort_order=30,
    ),
    PermissionDefinition(
        code=TenantPermissions.DOCUMENT_SPACES_MANAGE,
        label="Dokumentenboxen verwalten",
        category="Einstellungen",
        sort_order=40,
    ),
    PermissionDefinition(
        code=TenantPermissions.SETTINGS_VIEW,
        label="Einstellungen anzeigen",
        category="Einstellungen",
        sort_order=50,
    ),
    PermissionDefinition(
        code=TenantPermissions.SETTINGS_MEMBERS_MANAGE,
        label="Benutzer verwalten",
        category="Einstellungen",
        sort_order=60,
    ),
    PermissionDefinition(
        code=TenantPermissions.SETTINGS_ROLES_MANAGE,
        label="Rollen verwalten",
        category="Einstellungen",
        sort_order=70,
    ),
    PermissionDefinition(
        code=TenantPermissions.AUDIT_VIEW,
        label="Audit anzeigen",
        category="Einstellungen",
        sort_order=80,
    ),
    PermissionDefinition(
        code=TenantPermissions.WORKFLOWS_USE,
        label="Workflows nutzen",
        category="Workflows",
        sort_order=90,
    ),
    PermissionDefinition(
        code=TenantPermissions.WORKFLOWS_MANAGE,
        label="Workflows verwalten",
        category="Workflows",
        sort_order=100,
    ),
]


DEFAULT_ROLE_PERMISSIONS = {
    "admin": {
        TenantPermissions.DOCUMENTS_VIEW,
        TenantPermissions.DOCUMENTS_UPLOAD,
        TenantPermissions.DOCUMENTS_DOWNLOAD,
        TenantPermissions.DOCUMENT_SPACES_MANAGE,
        TenantPermissions.SETTINGS_VIEW,
        TenantPermissions.SETTINGS_MEMBERS_MANAGE,
        TenantPermissions.SETTINGS_ROLES_MANAGE,
        TenantPermissions.AUDIT_VIEW,
        TenantPermissions.WORKFLOWS_USE,
        TenantPermissions.WORKFLOWS_MANAGE,
    },
    "member": {
        TenantPermissions.DOCUMENTS_VIEW,
        TenantPermissions.DOCUMENTS_UPLOAD,
        TenantPermissions.DOCUMENTS_DOWNLOAD,
        TenantPermissions.WORKFLOWS_USE,
    },
    "viewer": {
        TenantPermissions.DOCUMENTS_VIEW,
        TenantPermissions.DOCUMENTS_DOWNLOAD,
    },
}
