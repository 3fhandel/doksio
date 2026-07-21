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
from doksio.documents.models import Document, DocumentRelation, DocumentSpace
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


def _metadata_value_is_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _complete_metadata_step_is_satisfied(
    *,
    step: WorkflowStep,
    document: Document,
) -> bool:
    required_slugs = list(
        step.required_metadata_fields.filter(
            is_active=True,
            space__deleted_at__isnull=True,
        ).values_list("slug", flat=True)
    )
    if not required_slugs:
        return True
    document_metadata = (
        Document.objects.filter(id=document.id)
        .values_list("metadata", flat=True)
        .first()
        or {}
    )
    return all(
        _metadata_value_is_filled(document_metadata.get(slug))
        for slug in required_slugs
    )


def _related_documents_for_workflow_step(
    *,
    step: WorkflowStep,
    document: Document,
):
    relations = DocumentRelation.objects.select_related(
        "first_document",
        "second_document",
    ).filter(
        Q(first_document=document) | Q(second_document=document),
        tenant=document.tenant,
    )
    related_documents = []
    allowed_space_ids = set(
        step.required_related_document_spaces.values_list("id", flat=True)
    )
    for relation in relations:
        related_document = relation.other_document(document)
        if related_document.status != Document.Status.ACTIVE:
            continue
        if allowed_space_ids and related_document.space_id not in allowed_space_ids:
            continue
        if step.related_document_requires_completed_workflow:
            has_completed_workflow = WorkflowInstance.objects.filter(
                document=related_document,
                status=WorkflowInstance.Status.COMPLETED,
            ).exists()
            if not has_completed_workflow:
                continue
        related_documents.append(related_document)
    return related_documents


def _document_relation_step_is_satisfied(
    *,
    step: WorkflowStep,
    document: Document,
) -> bool:
    return (
        len(_related_documents_for_workflow_step(step=step, document=document))
        >= step.min_related_documents
    )


def _advance_instance_to_step(
    *,
    instance: WorkflowInstance,
    step: WorkflowStep | None,
    actor=None,
) -> None:
    if step is None:
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
        return

    instance.current_step = step
    instance.save(update_fields=["current_step", "updated_at"])
    if (
        step.step_type == WorkflowStep.StepType.COMPLETE_METADATA
        and _complete_metadata_step_is_satisfied(step=step, document=instance.document)
    ):
        next_step = _next_step(step)
        RecordAuditEvent(
            tenant=instance.tenant,
            actor=actor,
            event_type="workflow_step.auto_completed",
            object_type="workflows.WorkflowStep",
            object_id=str(step.id),
            data={
                "instance_id": instance.id,
                "document_id": instance.document_id,
                "workflow_template_name": instance.template.name,
                "step_id": step.id,
                "step_name": step.name,
                "next_step_id": next_step.id if next_step else None,
                "next_step_name": next_step.name if next_step else None,
            },
        ).execute()
        _advance_instance_to_step(instance=instance, step=next_step, actor=actor)
        return
    if (
        step.step_type == WorkflowStep.StepType.REQUIRE_DOCUMENT_RELATION
        and _document_relation_step_is_satisfied(step=step, document=instance.document)
    ):
        next_step = _next_step(step)
        RecordAuditEvent(
            tenant=instance.tenant,
            actor=actor,
            event_type="workflow_step.auto_completed",
            object_type="workflows.WorkflowStep",
            object_id=str(step.id),
            data={
                "instance_id": instance.id,
                "document_id": instance.document_id,
                "workflow_template_name": instance.template.name,
                "step_id": step.id,
                "step_name": step.name,
                "reason": "document_relation_satisfied",
                "next_step_id": next_step.id if next_step else None,
                "next_step_name": next_step.name if next_step else None,
            },
        ).execute()
        _advance_instance_to_step(instance=instance, step=next_step, actor=actor)
        return

    _create_task_for_step(instance=instance, step=step)


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


def _candidate_recipients_for_workflow_started(instance: WorkflowInstance):
    user_model = get_user_model()
    memberships = TenantMembership.objects.filter(
        tenant=instance.tenant,
        is_active=True,
        user__is_active=True,
    ).filter(
        Q(role__permissions__code=TenantPermissions.WORKFLOWS_MANAGE)
        | Q(roles__permissions__code=TenantPermissions.WORKFLOWS_MANAGE)
    )
    return user_model.objects.filter(
        id__in=memberships.values("user_id"),
        is_active=True,
    ).distinct()


def _create_notifications_for_workflow_started(instance: WorkflowInstance) -> None:
    from doksio.documents.policies import filter_documents_for_user

    link_url = reverse(
        "documents:detail",
        kwargs={
            "tenant_slug": instance.tenant.slug,
            "document_id": instance.document_id,
        },
    )
    for recipient in _candidate_recipients_for_workflow_started(instance):
        can_see_document = filter_documents_for_user(
            Document.objects.filter(id=instance.document_id),
            recipient,
            instance.tenant,
        ).exists()
        if not can_see_document:
            continue

        CreateNotification(
            tenant=instance.tenant,
            recipient=recipient,
            notification_type=Notification.Type.WORKFLOW_STARTED,
            title="Workflow gestartet",
            body=f"{instance.template.name} für {instance.document.title}",
            link_url=link_url,
            document=instance.document,
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
    required_metadata_fields: list | None = None
    required_related_document_spaces: list | None = None
    min_related_documents: int = 1
    related_document_requires_completed_workflow: bool = False
    relation_picker_default_document_space: DocumentSpace | None = None
    relation_picker_default_include_child_spaces: bool = True
    relation_picker_default_workflow_status: str = (
        WorkflowStep.RelationPickerWorkflowStatus.ANY
    )
    relation_picker_filters_editable: bool = True
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
        required_metadata_fields = list(self.required_metadata_fields or [])
        required_related_document_spaces = list(
            self.required_related_document_spaces or []
        )
        if any(
            field.tenant_id != self.template.tenant_id
            for field in required_metadata_fields
        ):
            raise ValueError("Required metadata field belongs to a different tenant.")
        if any(
            space.tenant_id != self.template.tenant_id
            for space in required_related_document_spaces
        ):
            raise ValueError("Required document space belongs to a different tenant.")
        if (
            self.relation_picker_default_document_space
            and self.relation_picker_default_document_space.tenant_id
            != self.template.tenant_id
        ):
            raise ValueError("Default document space belongs to a different tenant.")

        step = WorkflowStep.objects.create(
            tenant=self.template.tenant,
            template=self.template,
            name=self.name,
            step_type=self.step_type,
            assigned_role=self.assigned_role,
            instructions=self.instructions,
            sort_order=self.sort_order,
            comment_policy=self.comment_policy,
            min_related_documents=max(self.min_related_documents or 1, 1),
            related_document_requires_completed_workflow=(
                self.related_document_requires_completed_workflow
            ),
            relation_picker_default_document_space=(
                self.relation_picker_default_document_space
            ),
            relation_picker_default_include_child_spaces=(
                self.relation_picker_default_include_child_spaces
            ),
            relation_picker_default_workflow_status=(
                self.relation_picker_default_workflow_status
                or WorkflowStep.RelationPickerWorkflowStatus.ANY
            ),
            relation_picker_filters_editable=self.relation_picker_filters_editable,
        )
        step.required_metadata_fields.set(required_metadata_fields)
        step.required_related_document_spaces.set(required_related_document_spaces)
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
    required_metadata_fields: list | None = None
    required_related_document_spaces: list | None = None
    min_related_documents: int = 1
    related_document_requires_completed_workflow: bool = False
    relation_picker_default_document_space: DocumentSpace | None = None
    relation_picker_default_include_child_spaces: bool = True
    relation_picker_default_workflow_status: str = (
        WorkflowStep.RelationPickerWorkflowStatus.ANY
    )
    relation_picker_filters_editable: bool = True
    instructions: str = ""
    sort_order: int = 100
    comment_policy: str = WorkflowStep.CommentPolicy.OPTIONAL
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> WorkflowStep:
        if (
            self.assigned_role
            and self.assigned_role.tenant_id != self.step.tenant_id
        ):
            raise ValueError("Assigned role belongs to a different tenant.")
        required_metadata_fields = list(self.required_metadata_fields or [])
        required_related_document_spaces = list(
            self.required_related_document_spaces or []
        )
        if any(
            field.tenant_id != self.step.tenant_id
            for field in required_metadata_fields
        ):
            raise ValueError("Required metadata field belongs to a different tenant.")
        if any(
            space.tenant_id != self.step.tenant_id
            for space in required_related_document_spaces
        ):
            raise ValueError("Required document space belongs to a different tenant.")
        if (
            self.relation_picker_default_document_space
            and self.relation_picker_default_document_space.tenant_id
            != self.step.tenant_id
        ):
            raise ValueError("Default document space belongs to a different tenant.")

        previous_assigned_role_id = self.step.assigned_role_id
        self.step.name = self.name
        self.step.step_type = self.step_type
        self.step.assigned_role = self.assigned_role
        self.step.instructions = self.instructions
        self.step.sort_order = self.sort_order
        self.step.comment_policy = self.comment_policy
        self.step.min_related_documents = max(self.min_related_documents or 1, 1)
        self.step.related_document_requires_completed_workflow = (
            self.related_document_requires_completed_workflow
        )
        self.step.relation_picker_default_document_space = (
            self.relation_picker_default_document_space
        )
        self.step.relation_picker_default_include_child_spaces = (
            self.relation_picker_default_include_child_spaces
        )
        self.step.relation_picker_default_workflow_status = (
            self.relation_picker_default_workflow_status
            or WorkflowStep.RelationPickerWorkflowStatus.ANY
        )
        self.step.relation_picker_filters_editable = (
            self.relation_picker_filters_editable
        )
        self.step.save(
            update_fields=[
                "name",
                "step_type",
                "assigned_role",
                "instructions",
                "sort_order",
                "comment_policy",
                "min_related_documents",
                "related_document_requires_completed_workflow",
                "relation_picker_default_document_space",
                "relation_picker_default_include_child_spaces",
                "relation_picker_default_workflow_status",
                "relation_picker_filters_editable",
                "updated_at",
            ]
        )
        self.step.required_metadata_fields.set(required_metadata_fields)
        self.step.required_related_document_spaces.set(required_related_document_spaces)
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
class ReorderWorkflowSteps:
    template: WorkflowTemplate
    step_ids: list[int]
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> list[WorkflowStep]:
        current_steps = list(
            WorkflowStep.objects.select_for_update()
            .filter(template=self.template, tenant=self.template.tenant)
            .order_by("sort_order", "id")
        )
        current_ids = {step.id for step in current_steps}
        submitted_ids = [int(step_id) for step_id in self.step_ids]
        if set(submitted_ids) != current_ids or len(submitted_ids) != len(current_ids):
            raise ValueError("Submitted workflow step order is incomplete.")

        steps_by_id = {step.id: step for step in current_steps}
        ordered_steps = [steps_by_id[step_id] for step_id in submitted_ids]
        previous_order = [step.id for step in current_steps]
        next_order = []
        updated_at = timezone.now()
        for index, step in enumerate(ordered_steps, start=1):
            step.sort_order = index * 10
            step.updated_at = updated_at
            next_order.append(step.id)
        WorkflowStep.objects.bulk_update(ordered_steps, ["sort_order", "updated_at"])

        RecordAuditEvent(
            tenant=self.template.tenant,
            actor=self.actor,
            event_type="workflow_steps.reordered",
            object_type="workflows.WorkflowTemplate",
            object_id=str(self.template.id),
            data={
                "template_id": self.template.id,
                "template_name": self.template.name,
                "previous_order": previous_order,
                "next_order": next_order,
            },
        ).execute()
        return ordered_steps


@dataclass(frozen=True)
class DeleteWorkflowStep:
    step: WorkflowStep
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> None:
        if self.step.tasks.exists() or self.step.current_instances.exists():
            raise ValueError(
                "Dieser Schritt wurde bereits verwendet und kann nicht gelöscht "
                "werden."
            )

        tenant = self.step.tenant
        step_id = self.step.id
        template_id = self.step.template_id
        step_name = self.step.name
        step_type = self.step.step_type
        sort_order = self.step.sort_order

        self.step.delete()

        RecordAuditEvent(
            tenant=tenant,
            actor=self.actor,
            event_type="workflow_step.deleted",
            object_type="workflows.WorkflowStep",
            object_id=str(step_id),
            data={
                "template_id": template_id,
                "name": step_name,
                "step_type": step_type,
                "sort_order": sort_order,
            },
        ).execute()


@dataclass(frozen=True)
class DeleteWorkflowTemplate:
    template: WorkflowTemplate
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> None:
        if self.template.instances.exists():
            raise ValueError(
                "Dieser Workflow wurde bereits verwendet und kann nicht gelöscht "
                "werden. Deaktiviere ihn stattdessen."
            )

        tenant = self.template.tenant
        template_id = self.template.id
        template_name = self.template.name
        template_slug = self.template.slug
        step_ids = list(self.template.steps.values_list("id", flat=True))

        self.template.delete()

        RecordAuditEvent(
            tenant=tenant,
            actor=self.actor,
            event_type="workflow_template.deleted",
            object_type="workflows.WorkflowTemplate",
            object_id=str(template_id),
            data={
                "name": template_name,
                "slug": template_slug,
                "step_ids": step_ids,
            },
        ).execute()


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
            current_step=None,
            started_by=self.actor,
        )
        _advance_instance_to_step(
            instance=instance,
            step=first_step,
            actor=self.actor,
        )
        _create_notifications_for_workflow_started(instance)

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
        if (
            self.task.step.step_type == WorkflowStep.StepType.COMPLETE_METADATA
            and not _complete_metadata_step_is_satisfied(
                step=self.task.step,
                document=self.task.document,
            )
        ):
            raise ValueError(
                "Bitte zuerst die erforderlichen Metadaten vollständig ausfüllen."
            )
        if (
            self.task.step.step_type == WorkflowStep.StepType.REQUIRE_DOCUMENT_RELATION
            and not _document_relation_step_is_satisfied(
                step=self.task.step,
                document=self.task.document,
            )
        ):
            raise ValueError(
                "Bitte zuerst die erforderlichen Dokumente verknüpfen."
            )

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
        _advance_instance_to_step(instance=instance, step=next_step, actor=self.actor)

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


@dataclass(frozen=True)
class RefreshRelationWorkflowTasksForDocument:
    document: Document
    actor: get_user_model() | None = None

    @transaction.atomic
    def execute(self) -> int:
        tasks = list(
            WorkflowTask.objects.select_related("instance", "step")
            .filter(
                tenant=self.document.tenant,
                document=self.document,
                status=WorkflowTask.Status.OPEN,
                step__step_type=WorkflowStep.StepType.REQUIRE_DOCUMENT_RELATION,
            )
            .order_by("created_at", "id")
        )
        completed_count = 0
        for task in tasks:
            if not _document_relation_step_is_satisfied(
                step=task.step,
                document=task.document,
            ):
                continue
            task.status = WorkflowTask.Status.COMPLETED
            task.completed_by = self.actor
            task.completed_at = timezone.now()
            task.completion_comment = ""
            task.save(
                update_fields=[
                    "status",
                    "completed_by",
                    "completed_at",
                    "completion_comment",
                    "updated_at",
                ]
            )
            next_step = _next_step(task.step)
            RecordAuditEvent(
                tenant=task.tenant,
                actor=self.actor,
                event_type="workflow_task.auto_completed",
                object_type="workflows.WorkflowTask",
                object_id=str(task.id),
                data={
                    "document_id": task.document_id,
                    "instance_id": task.instance_id,
                    "step_id": task.step_id,
                    "reason": "document_relation_satisfied",
                },
            ).execute()
            _advance_instance_to_step(
                instance=task.instance,
                step=next_step,
                actor=self.actor,
            )
            completed_count += 1
        return completed_count
