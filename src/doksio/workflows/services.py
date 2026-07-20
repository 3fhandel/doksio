"""Application services for workflow actions."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from doksio.accounts.models import Notification, TenantMembership, TenantRole
from doksio.accounts.permissions import TenantPermissions
from doksio.accounts.services import CreateNotification
from doksio.audit.services import RecordAuditEvent
from doksio.documents.models import Document, DocumentSpace
from doksio.tenancy.models import Tenant
from doksio.workflows.models import (
    WorkflowInstance,
    WorkflowStep,
    WorkflowTask,
    WorkflowTemplate,
)


def _first_step(template: WorkflowTemplate) -> WorkflowStep | None:
    return template.steps.order_by("sort_order", "id").first()


def _next_step(step: WorkflowStep) -> WorkflowStep | None:
    return (
        step.template.steps.filter(sort_order__gt=step.sort_order)
        .order_by("sort_order", "id")
        .first()
    )


def _create_task_for_step(
    instance: WorkflowInstance,
    step: WorkflowStep,
) -> WorkflowTask:
    task = WorkflowTask.objects.create(
        tenant=instance.tenant,
        instance=instance,
        step=step,
        document=instance.document,
        title=step.name,
        assigned_role=step.assigned_role,
    )
    _create_notifications_for_task(task)
    return task


def _candidate_recipients_for_task(task: WorkflowTask):
    user_model = get_user_model()
    if task.assigned_to_id:
        return user_model.objects.filter(id=task.assigned_to_id, is_active=True)

    memberships = TenantMembership.objects.filter(
        tenant=task.tenant,
        is_active=True,
        user__is_active=True,
    )
    if task.assigned_role_id:
        memberships = memberships.filter(
            Q(role_id=task.assigned_role_id) | Q(roles__id=task.assigned_role_id)
        )
    else:
        memberships = memberships.filter(
            Q(role__permissions__code=TenantPermissions.WORKFLOWS_USE)
            | Q(roles__permissions__code=TenantPermissions.WORKFLOWS_USE)
        )

    return user_model.objects.filter(
        id__in=memberships.values("user_id"),
        is_active=True,
    ).distinct()


def _create_notifications_for_task(task: WorkflowTask) -> None:
    from doksio.workflows.policies import filter_workflow_tasks_for_user

    task_url = (
        reverse(
            "documents:detail",
            kwargs={
                "tenant_slug": task.tenant.slug,
                "document_id": task.document_id,
            },
        )
        + "?back="
        + reverse("documents:tasks", kwargs={"tenant_slug": task.tenant.slug})
    )
    for recipient in _candidate_recipients_for_task(task):
        can_see_task = filter_workflow_tasks_for_user(
            WorkflowTask.objects.filter(id=task.id),
            recipient,
            task.tenant,
        ).exists()
        if not can_see_task:
            continue

        CreateNotification(
            tenant=task.tenant,
            recipient=recipient,
            notification_type=Notification.Type.WORKFLOW_TASK_CREATED,
            title="Neue Workflow-Aufgabe",
            body=f"{task.title} für {task.document.title}",
            link_url=task_url,
            document=task.document,
            workflow_task=task,
        ).execute()


def workflow_template_matches_document(
    template: WorkflowTemplate,
    document: Document,
) -> bool:
    if template.tenant_id != document.tenant_id:
        return False
    if not template.is_active:
        return False
    if template.trigger_type != WorkflowTemplate.TriggerType.DOCUMENT_CREATED:
        return False
    if template.trigger_document_space_id is None:
        return True
    if template.trigger_document_space_id == document.space_id:
        return True
    if not template.trigger_include_child_spaces:
        return False
    return document.space.path.startswith(
        f"{template.trigger_document_space.path.rstrip('/')}/"
    )


@dataclass(frozen=True)
class CreateWorkflowTemplate:
    tenant: Tenant
    name: str
    slug: str
    description: str = ""
    trigger_type: str = WorkflowTemplate.TriggerType.MANUAL
    trigger_document_space: DocumentSpace | None = None
    trigger_include_child_spaces: bool = True
    is_active: bool = True
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> WorkflowTemplate:
        if (
            self.trigger_document_space
            and self.trigger_document_space.tenant_id != self.tenant.id
        ):
            raise ValueError("Trigger document space belongs to a different tenant.")

        template = WorkflowTemplate.objects.create(
            tenant=self.tenant,
            name=self.name,
            slug=self.slug,
            description=self.description,
            trigger_type=self.trigger_type,
            trigger_document_space=self.trigger_document_space,
            trigger_include_child_spaces=self.trigger_include_child_spaces,
            is_active=self.is_active,
        )
        RecordAuditEvent(
            tenant=self.tenant,
            actor=self.actor,
            event_type="workflow_template.created",
            object_type="workflows.WorkflowTemplate",
            object_id=str(template.id),
            data={
                "name": template.name,
                "slug": template.slug,
                "trigger_type": template.trigger_type,
                "trigger_document_space_id": template.trigger_document_space_id,
            },
        ).execute()
        return template


@dataclass(frozen=True)
class UpdateWorkflowTemplate:
    template: WorkflowTemplate
    name: str
    description: str
    trigger_type: str
    is_active: bool
    trigger_document_space: DocumentSpace | None = None
    trigger_include_child_spaces: bool = True
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> WorkflowTemplate:
        if (
            self.trigger_document_space
            and self.trigger_document_space.tenant_id != self.template.tenant_id
        ):
            raise ValueError("Trigger document space belongs to a different tenant.")

        self.template.name = self.name
        self.template.description = self.description
        self.template.trigger_type = self.trigger_type
        self.template.trigger_document_space = self.trigger_document_space
        self.template.trigger_include_child_spaces = self.trigger_include_child_spaces
        self.template.is_active = self.is_active
        self.template.save(
            update_fields=[
                "name",
                "description",
                "trigger_type",
                "trigger_document_space",
                "trigger_include_child_spaces",
                "is_active",
                "updated_at",
            ]
        )
        RecordAuditEvent(
            tenant=self.template.tenant,
            actor=self.actor,
            event_type="workflow_template.updated",
            object_type="workflows.WorkflowTemplate",
            object_id=str(self.template.id),
            data={
                "name": self.template.name,
                "slug": self.template.slug,
                "trigger_type": self.template.trigger_type,
                "trigger_document_space_id": self.template.trigger_document_space_id,
                "is_active": self.template.is_active,
            },
        ).execute()
        return self.template


@dataclass(frozen=True)
class CreateWorkflowStep:
    template: WorkflowTemplate
    name: str
    step_type: str
    assigned_role: TenantRole | None = None
    instructions: str = ""
    sort_order: int = 100
    comment_policy: str = WorkflowStep.CommentPolicy.OPTIONAL
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> WorkflowStep:
        if (
            self.assigned_role
            and self.assigned_role.tenant_id != self.template.tenant_id
        ):
            raise ValueError("Assigned role belongs to a different tenant.")

        step = WorkflowStep.objects.create(
            tenant=self.template.tenant,
            template=self.template,
            name=self.name,
            step_type=self.step_type,
            assigned_role=self.assigned_role,
            instructions=self.instructions,
            sort_order=self.sort_order,
            comment_policy=self.comment_policy,
        )
        RecordAuditEvent(
            tenant=self.template.tenant,
            actor=self.actor,
            event_type="workflow_step.created",
            object_type="workflows.WorkflowStep",
            object_id=str(step.id),
            data={
                "template_id": self.template.id,
                "name": step.name,
                "step_type": step.step_type,
                "sort_order": step.sort_order,
            },
        ).execute()
        return step


@dataclass(frozen=True)
class UpdateWorkflowStep:
    step: WorkflowStep
    name: str
    step_type: str
    assigned_role: TenantRole | None = None
    instructions: str = ""
    sort_order: int = 100
    comment_policy: str = WorkflowStep.CommentPolicy.OPTIONAL
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> WorkflowStep:
        if self.assigned_role and self.assigned_role.tenant_id != self.step.tenant_id:
            raise ValueError("Assigned role belongs to a different tenant.")

        previous_assigned_role_id = self.step.assigned_role_id
        self.step.name = self.name
        self.step.step_type = self.step_type
        self.step.assigned_role = self.assigned_role
        self.step.instructions = self.instructions
        self.step.sort_order = self.sort_order
        self.step.comment_policy = self.comment_policy
        self.step.save(
            update_fields=[
                "name",
                "step_type",
                "assigned_role",
                "instructions",
                "sort_order",
                "comment_policy",
                "updated_at",
            ]
        )
        updated_open_tasks = self._sync_open_tasks(previous_assigned_role_id)
        RecordAuditEvent(
            tenant=self.step.tenant,
            actor=self.actor,
            event_type="workflow_step.updated",
            object_type="workflows.WorkflowStep",
            object_id=str(self.step.id),
            data={
                "template_id": self.step.template_id,
                "name": self.step.name,
                "step_type": self.step.step_type,
                "sort_order": self.step.sort_order,
                "assigned_role_id": self.step.assigned_role_id,
                "previous_assigned_role_id": previous_assigned_role_id,
                "updated_open_tasks": updated_open_tasks,
            },
        ).execute()
        return self.step

    def _sync_open_tasks(self, previous_assigned_role_id: int | None) -> int:
        if previous_assigned_role_id == self.step.assigned_role_id:
            return 0
        return WorkflowTask.objects.filter(
            tenant=self.step.tenant,
            step=self.step,
            status=WorkflowTask.Status.OPEN,
        ).update(
            assigned_role=self.step.assigned_role,
            updated_at=timezone.now(),
        )


@dataclass(frozen=True)
class StartWorkflowForDocument:
    template: WorkflowTemplate
    document: Document
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> WorkflowInstance:
        if self.template.tenant_id != self.document.tenant_id:
            raise ValueError("Workflow template belongs to a different tenant.")
        if not self.template.is_active:
            raise ValueError("Workflow template is inactive.")

        first_step = _first_step(self.template)
        instance = WorkflowInstance.objects.create(
            tenant=self.document.tenant,
            template=self.template,
            document=self.document,
            current_step=first_step,
            started_by=self.actor,
        )
        if first_step is None:
            instance.status = WorkflowInstance.Status.COMPLETED
            instance.completed_at = timezone.now()
            instance.save(update_fields=["status", "completed_at", "updated_at"])
        else:
            _create_task_for_step(instance=instance, step=first_step)

        RecordAuditEvent(
            tenant=self.document.tenant,
            actor=self.actor,
            event_type="workflow_instance.started",
            object_type="workflows.WorkflowInstance",
            object_id=str(instance.id),
            data={
                "template_id": self.template.id,
                "template_name": self.template.name,
                "document_id": self.document.id,
                "current_step_id": first_step.id if first_step else None,
                "current_step_name": first_step.name if first_step else None,
            },
        ).execute()
        return instance


@dataclass(frozen=True)
class StartMatchingWorkflowsForDocument:
    document: Document
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> list[WorkflowInstance]:
        templates = (
            WorkflowTemplate.objects.select_related("trigger_document_space")
            .filter(
                tenant=self.document.tenant,
                is_active=True,
                trigger_type=WorkflowTemplate.TriggerType.DOCUMENT_CREATED,
            )
            .order_by("name", "id")
        )
        instances: list[WorkflowInstance] = []
        for template in templates:
            instance_exists = WorkflowInstance.objects.filter(
                template=template,
                document=self.document,
            ).exclude(status=WorkflowInstance.Status.CANCELLED).exists()
            if not instance_exists and workflow_template_matches_document(
                template,
                self.document,
            ):
                instances.append(
                    StartWorkflowForDocument(
                        template=template,
                        document=self.document,
                        actor=self.actor,
                    ).execute()
                )
        return instances


@dataclass(frozen=True)
class CancelRunningWorkflowsForDocument:
    document: Document
    actor: get_user_model() | None = None
    reason: str = ""

    @transaction.atomic
    def execute(self) -> list[WorkflowInstance]:
        cancelled_at = timezone.now()
        instances = list(
            WorkflowInstance.objects.select_related("template")
            .filter(
                document=self.document,
                tenant=self.document.tenant,
                status=WorkflowInstance.Status.RUNNING,
            )
            .order_by("created_at", "id")
        )
        for instance in instances:
            WorkflowTask.objects.filter(
                instance=instance,
                status=WorkflowTask.Status.OPEN,
            ).update(status=WorkflowTask.Status.CANCELLED, updated_at=cancelled_at)
            instance.status = WorkflowInstance.Status.CANCELLED
            instance.current_step = None
            instance.completed_at = cancelled_at
            instance.save(
                update_fields=[
                    "status",
                    "current_step",
                    "completed_at",
                    "updated_at",
                ]
            )
            RecordAuditEvent(
                tenant=self.document.tenant,
                actor=self.actor,
                event_type="workflow_instance.cancelled",
                object_type="workflows.WorkflowInstance",
                object_id=str(instance.id),
                data={
                    "document_id": self.document.id,
                    "template_id": instance.template_id,
                    "template_name": instance.template.name,
                    "reason": self.reason,
                },
            ).execute()
        return instances


@dataclass(frozen=True)
class CompleteWorkflowTask:
    task: WorkflowTask
    actor: get_user_model()
    comment: str = ""

    @transaction.atomic
    def execute(self) -> WorkflowTask:
        if self.task.status != WorkflowTask.Status.OPEN:
            raise ValueError("Workflow task is not open.")
        if (
            self.task.step.comment_policy == WorkflowStep.CommentPolicy.REQUIRED
            and not self.comment.strip()
        ):
            raise ValueError("This workflow task requires a comment.")

        comment = ""
        if self.task.step.comment_policy != WorkflowStep.CommentPolicy.DISABLED:
            comment = self.comment.strip()

        self.task.status = WorkflowTask.Status.COMPLETED
        self.task.completed_by = self.actor
        self.task.completion_comment = comment
        self.task.completed_at = timezone.now()
        self.task.save(
            update_fields=[
                "status",
                "completed_by",
                "completion_comment",
                "completed_at",
                "updated_at",
            ]
        )

        instance = self.task.instance
        next_step = _next_step(self.task.step)
        if next_step is None:
            instance.status = WorkflowInstance.Status.COMPLETED
            instance.current_step = None
            instance.completed_at = timezone.now()
            instance.save(
                update_fields=[
                    "status",
                    "current_step",
                    "completed_at",
                    "updated_at",
                ]
            )
        else:
            instance.current_step = next_step
            instance.save(update_fields=["current_step", "updated_at"])
            _create_task_for_step(instance=instance, step=next_step)

        RecordAuditEvent(
            tenant=self.task.tenant,
            actor=self.actor,
            event_type="workflow_task.completed",
            object_type="workflows.WorkflowTask",
            object_id=str(self.task.id),
            data={
                "instance_id": instance.id,
                "document_id": self.task.document_id,
                "workflow_template_name": instance.template.name,
                "step_id": self.task.step_id,
                "step_name": self.task.step.name,
                "next_step_id": next_step.id if next_step else None,
                "next_step_name": next_step.name if next_step else None,
                "comment_length": len(comment),
            },
        ).execute()
        return self.task
