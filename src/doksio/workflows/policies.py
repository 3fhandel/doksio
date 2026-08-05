from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Q, QuerySet

from doksio.accounts.access import AccessControl
from doksio.accounts.permissions import TenantPermissions
from doksio.documents.models import Document
from doksio.documents.policies import filter_documents_for_user, has_tenant_permission
from doksio.tenancy.models import Tenant
from doksio.workflows.models import WorkflowTask, WorkflowTemplate


def supervised_workflow_templates_for_user(
    templates: QuerySet[WorkflowTemplate],
    user: AbstractBaseUser | AnonymousUser,
    tenant: Tenant,
) -> QuerySet[WorkflowTemplate]:
    if not user.is_authenticated or not user.is_active:
        return templates.none()
    templates = templates.filter(tenant=tenant)
    if user.is_superuser:
        return templates

    membership = AccessControl(user=user, tenant=tenant).membership
    if membership is None:
        return templates.none()
    role_ids = list(
        membership.roles.filter(is_active=True).values_list("id", flat=True)
    )
    if membership.role_id and membership.role.is_active:
        role_ids.append(membership.role_id)
    if not role_ids:
        return templates.none()
    return templates.filter(supervisor_roles__id__in=set(role_ids)).distinct()


def is_workflow_supervisor(
    user: AbstractBaseUser | AnonymousUser,
    template: WorkflowTemplate,
) -> bool:
    return supervised_workflow_templates_for_user(
        WorkflowTemplate.objects.filter(id=template.id),
        user,
        template.tenant,
    ).exists()


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
    if is_workflow_supervisor(user, task.instance.template):
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
