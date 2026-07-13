from __future__ import annotations

from django.contrib import admin

from doksio.workflows.models import (
    WorkflowInstance,
    WorkflowStep,
    WorkflowTask,
    WorkflowTemplate,
)


class WorkflowStepInline(admin.TabularInline):
    model = WorkflowStep
    extra = 0


@admin.register(WorkflowTemplate)
class WorkflowTemplateAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "tenant",
        "trigger_type",
        "trigger_document_space",
        "is_active",
    ]
    list_filter = ["trigger_type", "is_active", "tenant"]
    search_fields = ["name", "slug", "tenant__name"]
    inlines = [WorkflowStepInline]


@admin.register(WorkflowInstance)
class WorkflowInstanceAdmin(admin.ModelAdmin):
    list_display = ["template", "document", "tenant", "status", "created_at"]
    list_filter = ["status", "tenant"]
    search_fields = ["template__name", "document__title"]


@admin.register(WorkflowTask)
class WorkflowTaskAdmin(admin.ModelAdmin):
    list_display = ["title", "document", "tenant", "assigned_role", "status"]
    list_filter = ["status", "tenant", "assigned_role"]
    search_fields = ["title", "document__title"]
