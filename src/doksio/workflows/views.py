from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Max
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.policies import has_tenant_permission
from doksio.tenancy.services import get_tenant_for_user
from doksio.workflows.forms import WorkflowStepForm, WorkflowTemplateForm
from doksio.workflows.models import WorkflowStep, WorkflowTemplate
from doksio.workflows.services import (
    CreateWorkflowStep,
    CreateWorkflowTemplate,
    DeleteWorkflowStep,
    DeleteWorkflowTemplate,
    ReorderWorkflowSteps,
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


def workflow_template_delete(
    request: HttpRequest,
    tenant_slug: str,
    template_id: int,
) -> HttpResponse:
    tenant, response = _require_workflow_management(request, tenant_slug)
    if response is not None:
        return response

    template = get_object_or_404(
        WorkflowTemplate.objects.prefetch_related("steps"),
        id=template_id,
        tenant=tenant,
    )
    instance_count = template.instances.count()
    can_delete = instance_count == 0
    if request.method == "POST":
        confirmation = request.POST.get("confirmation", "").strip()
        if confirmation != template.name:
            messages.error(
                request,
                "Bitte den Workflow-Namen exakt als Bestätigung eingeben.",
            )
        elif not can_delete:
            messages.error(
                request,
                "Dieser Workflow wurde bereits verwendet und kann nicht gelöscht "
                "werden. Deaktiviere ihn stattdessen.",
            )
        else:
            try:
                DeleteWorkflowTemplate(template=template, actor=request.user).execute()
            except ValueError as error:
                messages.error(request, str(error))
            else:
                messages.success(request, "Workflow wurde gelöscht.")
                return redirect(
                    "workflows:settings_templates",
                    tenant_slug=tenant.slug,
                )

    return render(
        request,
        "workflows/settings_template_delete.html",
        {
            "tenant": tenant,
            "template": template,
            "instance_count": instance_count,
            "can_delete": can_delete,
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
                required_metadata_fields=list(
                    form.cleaned_data["required_metadata_fields"]
                ),
                required_related_document_spaces=list(
                    form.cleaned_data["required_related_document_spaces"]
                ),
                min_related_documents=form.cleaned_data["min_related_documents"],
                related_document_requires_completed_workflow=form.cleaned_data[
                    "related_document_requires_completed_workflow"
                ],
                relation_picker_default_document_space=form.cleaned_data[
                    "relation_picker_default_document_space"
                ],
                relation_picker_default_include_child_spaces=form.cleaned_data[
                    "relation_picker_default_include_child_spaces"
                ],
                relation_picker_default_workflow_status=form.cleaned_data[
                    "relation_picker_default_workflow_status"
                ],
                relation_picker_filters_editable=form.cleaned_data[
                    "relation_picker_filters_editable"
                ],
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
        next_sort_order = (
            template.steps.aggregate(max_sort_order=Max("sort_order"))[
                "max_sort_order"
            ]
            or 0
        ) + 10
        form = WorkflowStepForm(tenant=tenant, initial={"sort_order": next_sort_order})

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


@require_POST
def workflow_steps_reorder(
    request: HttpRequest,
    tenant_slug: str,
    template_id: int,
) -> JsonResponse:
    tenant, response = _require_workflow_management(request, tenant_slug)
    if response is not None:
        return JsonResponse({"ok": False, "error": "login_required"}, status=401)

    template = get_object_or_404(WorkflowTemplate, id=template_id, tenant=tenant)
    step_ids = request.POST.getlist("step_ids")
    try:
        steps = ReorderWorkflowSteps(
            template=template,
            step_ids=[int(step_id) for step_id in step_ids],
            actor=request.user,
        ).execute()
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "invalid_order"}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "steps": [
                {"id": step.id, "sort_order": step.sort_order}
                for step in sorted(steps, key=lambda item: (item.sort_order, item.id))
            ],
        }
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
                required_metadata_fields=list(
                    form.cleaned_data["required_metadata_fields"]
                ),
                required_related_document_spaces=list(
                    form.cleaned_data["required_related_document_spaces"]
                ),
                min_related_documents=form.cleaned_data["min_related_documents"],
                related_document_requires_completed_workflow=form.cleaned_data[
                    "related_document_requires_completed_workflow"
                ],
                relation_picker_default_document_space=form.cleaned_data[
                    "relation_picker_default_document_space"
                ],
                relation_picker_default_include_child_spaces=form.cleaned_data[
                    "relation_picker_default_include_child_spaces"
                ],
                relation_picker_default_workflow_status=form.cleaned_data[
                    "relation_picker_default_workflow_status"
                ],
                relation_picker_filters_editable=form.cleaned_data[
                    "relation_picker_filters_editable"
                ],
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
                "required_metadata_fields": step.required_metadata_fields.all(),
                "required_related_document_spaces": (
                    step.required_related_document_spaces.all()
                ),
                "min_related_documents": step.min_related_documents,
                "related_document_requires_completed_workflow": (
                    step.related_document_requires_completed_workflow
                ),
                "relation_picker_default_document_space": (
                    step.relation_picker_default_document_space
                ),
                "relation_picker_default_include_child_spaces": (
                    step.relation_picker_default_include_child_spaces
                ),
                "relation_picker_default_workflow_status": (
                    step.relation_picker_default_workflow_status
                ),
                "relation_picker_filters_editable": (
                    step.relation_picker_filters_editable
                ),
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


def workflow_step_delete(
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
    task_count = step.tasks.count()
    current_instance_count = step.current_instances.count()
    can_delete = task_count == 0 and current_instance_count == 0
    if request.method == "POST":
        confirmation = request.POST.get("confirmation", "").strip()
        if confirmation != step.name:
            messages.error(
                request,
                "Bitte den Schrittnamen exakt als Bestätigung eingeben.",
            )
        elif not can_delete:
            messages.error(
                request,
                "Dieser Schritt wurde bereits verwendet und kann nicht gelöscht "
                "werden.",
            )
        else:
            try:
                DeleteWorkflowStep(step=step, actor=request.user).execute()
            except ValueError as error:
                messages.error(request, str(error))
            else:
                messages.success(request, "Workflow-Schritt wurde gelöscht.")
                return redirect(
                    "workflows:settings_template_edit",
                    tenant_slug=tenant.slug,
                    template_id=template.id,
                )

    return render(
        request,
        "workflows/settings_step_delete.html",
        {
            "tenant": tenant,
            "template": template,
            "step": step,
            "task_count": task_count,
            "current_instance_count": current_instance_count,
            "can_delete": can_delete,
            "active_settings_section": "workflows",
        },
    )
