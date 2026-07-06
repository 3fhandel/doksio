from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Q, QuerySet

from domasy.accounts.access import AccessControl
from domasy.accounts.permissions import TenantPermissions
from domasy.documents.models import Document
from domasy.documents.policies import filter_documents_for_user, has_tenant_permission
from domasy.tenancy.models import Tenant
from domasy.workflows.models import WorkflowTask


def can_use_workflows(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> bool:
    return has_tenant_permission(user, tenant, TenantPermissions.WORKFLOWS_USE)


def can_manage_workflows(
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> bool:
    return has_tenant_permission(user, tenant, TenantPermissions.WORKFLOWS_MANAGE)


def can_complete_workflow_task(
    user: AbstractBaseUser | AnonymousUser,
    task: WorkflowTask,
) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_authenticated and user.is_active and user.is_superuser:
        return True
    if task.assigned_to_id == user.id:
        return True
    if task.assigned_role_id is None:
        return can_use_workflows(user, task.tenant)

    membership = AccessControl(user=user, tenant=task.tenant).membership
    if membership is None:
        return False
    if membership.roles.filter(id=task.assigned_role_id, is_active=True).exists():
        return True
    return bool(
        membership.role_id == task.assigned_role_id and membership.role.is_active
    )


def filter_workflow_tasks_for_user(
    tasks: QuerySet[WorkflowTask],
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> QuerySet[WorkflowTask]:
    if not user.is_authenticated or not user.is_active:
        return tasks.none()
    if user.is_superuser:
        return tasks

    visible_documents = filter_documents_for_user(
        Document.objects.filter(tenant=tenant),
        user,
        tenant,
    )
    tasks = tasks.filter(document__in=visible_documents)

    membership = AccessControl(user=user, tenant=tenant).membership
    if membership is None:
        return tasks.none()

    role_ids = list(
        membership.roles.filter(is_active=True).values_list("id", flat=True)
    )
    if not role_ids and membership.role.is_active:
        role_ids = [membership.role_id]

    task_filter = Q(assigned_to=user)
    if role_ids:
        task_filter |= Q(assigned_role_id__in=role_ids)
    if can_use_workflows(user, tenant):
        task_filter |= Q(assigned_role__isnull=True)

    return tasks.filter(task_filter)
