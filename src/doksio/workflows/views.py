from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.policies import has_tenant_permission
from doksio.tenancy.services import get_tenant_for_user
from doksio.workflows.forms import WorkflowStepForm, WorkflowTemplateForm
from doksio.workflows.models import WorkflowStep, WorkflowTemplate
from doksio.workflows.services import (
    CreateWorkflowStep,
    CreateWorkflowTemplate,
    UpdateWorkflowStep,
    UpdateWorkflowTemplate,
)


def _tenant_login_redirect(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_url = reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant_slug})
    return redirect(f"{login_url}?next={request.get_full_path()}")


def _require_workflow_management(request: HttpRequest, tenant_slug: str):
    if not request.user.is_authenticated:
        return None, _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not has_tenant_permission(
        request.user,
        tenant,
        TenantPermissions.WORKFLOWS_MANAGE,
    ):
        raise PermissionDenied
    return tenant, None


def workflow_templates(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    tenant, response = _require_workflow_management(request, tenant_slug)
    if response is not None:
        return response

    templates = (
        WorkflowTemplate.objects.filter(tenant=tenant)
        .prefetch_related("steps")
        .order_by("name")
    )
    return render(
        request,
        "workflows/settings_templates.html",
        {
            "tenant": tenant,
            "templates": templates,
            "active_settings_section": "workflows",
        },
    )


def workflow_template_create(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    tenant, response = _require_workflow_management(request, tenant_slug)
    if response is not None:
        return response

    if request.method == "POST":
        form = WorkflowTemplateForm(request.POST, tenant=tenant)
        if form.is_valid():
            template = CreateWorkflowTemplate(
                tenant=tenant,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                description=form.cleaned_data["description"],
                trigger_type=form.cleaned_data["trigger_type"],
                trigger_document_space=form.cleaned_data["trigger_document_space"],
                trigger_include_child_spaces=form.cleaned_data[
                    "trigger_include_child_spaces"
                ],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Workflow wurde erstellt.")
            return redirect(
                "workflows:settings_template_edit",
                tenant_slug=tenant.slug,
                template_id=template.id,
            )
    else:
        form = WorkflowTemplateForm(tenant=tenant)

    return render(
        request,
        "workflows/settings_template_form.html",
        {
            "tenant": tenant,
            "form": form,
            "form_title": "Workflow erstellen",
            "submit_label": "Workflow erstellen",
            "active_settings_section": "workflows",
        },
    )


def workflow_template_edit(
    request: HttpRequest,
    tenant_slug: str,
    template_id: int,
) -> HttpResponse:
    tenant, response = _require_workflow_management(request, tenant_slug)
    if response is not None:
        return response

    template = get_object_or_404(
        WorkflowTemplate.objects.prefetch_related("steps__assigned_role"),
        id=template_id,
        tenant=tenant,
    )
    if request.method == "POST":
        form = WorkflowTemplateForm(request.POST, tenant=tenant, template=template)
        if form.is_valid():
            UpdateWorkflowTemplate(
                template=template,
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                trigger_type=form.cleaned_data["trigger_type"],
                trigger_document_space=form.cleaned_data["trigger_document_space"],
                trigger_include_child_spaces=form.cleaned_data[
                    "trigger_include_child_spaces"
                ],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Workflow wurde gespeichert.")
            return redirect(
                "workflows:settings_template_edit",
                tenant_slug=tenant.slug,
                template_id=template.id,
            )
    else:
        form = WorkflowTemplateForm(
            tenant=tenant,
            template=template,
            initial={
                "name": template.name,
                "slug": template.slug,
                "description": template.description,
                "trigger_type": template.trigger_type,
                "trigger_document_space": template.trigger_document_space,
                "trigger_include_child_spaces": (
                    template.trigger_include_child_spaces
                ),
                "is_active": template.is_active,
            },
        )
        form.fields["slug"].disabled = True

    return render(
        request,
        "workflows/settings_template_form.html",
        {
            "tenant": tenant,
            "template": template,
            "form": form,
            "form_title": "Workflow bearbeiten",
            "submit_label": "Workflow speichern",
            "active_settings_section": "workflows",
        },
    )


def workflow_step_create(
    request: HttpRequest,
    tenant_slug: str,
    template_id: int,
) -> HttpResponse:
    tenant, response = _require_workflow_management(request, tenant_slug)
    if response is not None:
        return response

    template = get_object_or_404(WorkflowTemplate, id=template_id, tenant=tenant)
    if request.method == "POST":
        form = WorkflowStepForm(request.POST, tenant=tenant)
        if form.is_valid():
            CreateWorkflowStep(
                template=template,
                name=form.cleaned_data["name"],
                step_type=form.cleaned_data["step_type"],
                assigned_role=form.cleaned_data["assigned_role"],
                instructions=form.cleaned_data["instructions"],
                sort_order=form.cleaned_data["sort_order"],
                comment_policy=form.cleaned_data["comment_policy"],
                actor=request.user,
            ).execute()
            messages.success(request, "Workflow-Schritt wurde erstellt.")
            return redirect(
                "workflows:settings_template_edit",
                tenant_slug=tenant.slug,
                template_id=template.id,
            )
    else:
        form = WorkflowStepForm(tenant=tenant)

    return render(
        request,
        "workflows/settings_step_form.html",
        {
            "tenant": tenant,
            "template": template,
            "form": form,
            "form_title": "Workflow-Schritt erstellen",
            "submit_label": "Schritt erstellen",
            "active_settings_section": "workflows",
        },
    )


def workflow_step_edit(
    request: HttpRequest,
    tenant_slug: str,
    template_id: int,
    step_id: int,
) -> HttpResponse:
    tenant, response = _require_workflow_management(request, tenant_slug)
    if response is not None:
        return response

    template = get_object_or_404(WorkflowTemplate, id=template_id, tenant=tenant)
    step = get_object_or_404(WorkflowStep, id=step_id, template=template, tenant=tenant)
    if request.method == "POST":
        form = WorkflowStepForm(request.POST, tenant=tenant)
        if form.is_valid():
            UpdateWorkflowStep(
                step=step,
                name=form.cleaned_data["name"],
                step_type=form.cleaned_data["step_type"],
                assigned_role=form.cleaned_data["assigned_role"],
                instructions=form.cleaned_data["instructions"],
                sort_order=form.cleaned_data["sort_order"],
                comment_policy=form.cleaned_data["comment_policy"],
                actor=request.user,
            ).execute()
            messages.success(request, "Workflow-Schritt wurde gespeichert.")
            return redirect(
                "workflows:settings_template_edit",
                tenant_slug=tenant.slug,
                template_id=template.id,
            )
    else:
        form = WorkflowStepForm(
            tenant=tenant,
            initial={
                "name": step.name,
                "step_type": step.step_type,
                "assigned_role": step.assigned_role,
                "instructions": step.instructions,
                "sort_order": step.sort_order,
                "comment_policy": step.comment_policy,
            },
        )

    return render(
        request,
        "workflows/settings_step_form.html",
        {
            "tenant": tenant,
            "template": template,
            "step": step,
            "form": form,
            "form_title": "Workflow-Schritt bearbeiten",
            "submit_label": "Schritt speichern",
            "active_settings_section": "workflows",
        },
    )
