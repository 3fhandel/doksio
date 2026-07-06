from __future__ import annotations

from django.conf import settings
from django.db import models


class WorkflowTemplate(models.Model):
    """Tenant-owned reusable workflow definition."""

    class TriggerType(models.TextChoices):
        MANUAL = "manual", "Manuell"
        DOCUMENT_CREATED = "document_created", "Dokument erstellt"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="workflow_templates",
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=100)
    description = models.TextField(blank=True)
    trigger_type = models.CharField(
        max_length=40,
        choices=TriggerType.choices,
        default=TriggerType.MANUAL,
    )
    trigger_document_space = models.ForeignKey(
        "documents.DocumentSpace",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="workflow_templates",
    )
    trigger_include_child_spaces = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "slug"],
                name="unique_tenant_workflow_template_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "is_active"]),
            models.Index(fields=["tenant", "trigger_type"]),
            models.Index(fields=["tenant", "trigger_document_space"]),
        ]

    def __str__(self) -> str:
        return self.name


class WorkflowStep(models.Model):
    """One ordered step inside a workflow template."""

    class StepType(models.TextChoices):
        TASK = "task", "Aufgabe"
        APPROVAL = "approval", "Freigabe"

    class CommentPolicy(models.TextChoices):
        DISABLED = "disabled", "Kein Kommentar"
        OPTIONAL = "optional", "Kommentar optional"
        REQUIRED = "required", "Kommentar erforderlich"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="workflow_steps",
    )
    template = models.ForeignKey(
        WorkflowTemplate,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    name = models.CharField(max_length=160)
    step_type = models.CharField(
        max_length=40,
        choices=StepType.choices,
        default=StepType.TASK,
    )
    assigned_role = models.ForeignKey(
        "accounts.TenantRole",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="workflow_steps",
    )
    instructions = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=100)
    comment_policy = models.CharField(
        max_length=20,
        choices=CommentPolicy.choices,
        default=CommentPolicy.OPTIONAL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["template", "sort_order", "id"]
        indexes = [
            models.Index(fields=["tenant", "template", "sort_order"]),
            models.Index(fields=["tenant", "assigned_role"]),
        ]

    def __str__(self) -> str:
        return f"{self.template}: {self.name}"


class WorkflowInstance(models.Model):
    """Running workflow attached to one document."""

    class Status(models.TextChoices):
        RUNNING = "running", "Läuft"
        COMPLETED = "completed", "Abgeschlossen"
        CANCELLED = "cancelled", "Abgebrochen"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="workflow_instances",
    )
    template = models.ForeignKey(
        WorkflowTemplate,
        on_delete=models.PROTECT,
        related_name="instances",
    )
    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="workflow_instances",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    current_step = models.ForeignKey(
        WorkflowStep,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="current_instances",
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="started_workflow_instances",
    )
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "document", "status"]),
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.template} für {self.document}"


class WorkflowTask(models.Model):
    """Actionable workflow task."""

    class Status(models.TextChoices):
        OPEN = "open", "Offen"
        COMPLETED = "completed", "Erledigt"
        CANCELLED = "cancelled", "Abgebrochen"

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="workflow_tasks",
    )
    instance = models.ForeignKey(
        WorkflowInstance,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    step = models.ForeignKey(
        WorkflowStep,
        on_delete=models.PROTECT,
        related_name="tasks",
    )
    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="workflow_tasks",
    )
    title = models.CharField(max_length=180)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.OPEN,
    )
    assigned_role = models.ForeignKey(
        "accounts.TenantRole",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="workflow_tasks",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="assigned_workflow_tasks",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="completed_workflow_tasks",
    )
    completion_comment = models.TextField(blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "assigned_role", "status"]),
            models.Index(fields=["tenant", "document", "status"]),
        ]

    def __str__(self) -> str:
        return self.title
