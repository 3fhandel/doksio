from __future__ import annotations

from doksio.accounts.models import Notification, UserProfile, default_keyboard_shortcuts
from doksio.accounts.permissions import TenantPermissions
from doksio.documents.policies import (
    can_administer_tenant,
    can_view_audit,
    can_view_reports,
    has_tenant_permission,
)
from doksio.tenancy.services import get_tenant_for_user
from doksio.workflows.models import WorkflowTask
from doksio.workflows.policies import filter_workflow_tasks_for_user


def user_profile(request):
    context = {
        "keyboard_shortcuts": {},
        "sidebar_open_workflow_tasks_count": 0,
        "unread_notifications_count": 0,
        "recent_unread_notifications": [],
    }
    if not request.user.is_authenticated:
        return context

    profile = UserProfile.objects.filter(user=request.user).first()
    shortcuts = default_keyboard_shortcuts()
    if profile is not None:
        shortcuts.update(profile.keyboard_shortcuts or {})
    context["keyboard_shortcuts"] = shortcuts

    tenant_slug = None
    if request.resolver_match is not None:
        tenant_slug = request.resolver_match.kwargs.get("tenant_slug")
    if tenant_slug:
        tenant = get_tenant_for_user(request.user, tenant_slug)
        if tenant is not None:
            context["can_manage_settings"] = can_administer_tenant(
                request.user,
                tenant,
            )
            context["can_view_audit"] = can_view_audit(request.user, tenant)
            context["can_view_reports"] = can_view_reports(request.user, tenant)
            context["can_export_documents"] = has_tenant_permission(
                request.user,
                tenant,
                TenantPermissions.DOCUMENTS_EXPORT,
            )
            context["can_batch_import_documents"] = has_tenant_permission(
                request.user,
                tenant,
                TenantPermissions.DOCUMENTS_BATCH_IMPORT,
            )
            unread_notifications = Notification.objects.filter(
                tenant=tenant,
                recipient=request.user,
                read_at__isnull=True,
            ).select_related("document", "workflow_task")
            context["unread_notifications_count"] = unread_notifications.count()
            context["recent_unread_notifications"] = unread_notifications[:3]
            workflow_tasks = filter_workflow_tasks_for_user(
                WorkflowTask.objects.filter(
                    tenant=tenant,
                    status=WorkflowTask.Status.OPEN,
                ),
                request.user,
                tenant,
            )
            context["sidebar_open_workflow_tasks_count"] = workflow_tasks.count()

    return context
