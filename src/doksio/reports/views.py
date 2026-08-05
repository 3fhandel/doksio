from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.policies import has_tenant_permission
from doksio.reports.services import BuildTenantReports
from doksio.tenancy.services import get_tenant_for_user
from doksio.workflows.models import WorkflowTemplate
from doksio.workflows.policies import supervised_workflow_templates_for_user


def _tenant_login_redirect(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_url = reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant_slug})
    return redirect(f"{login_url}?next={request.get_full_path()}")


def reports_overview(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    can_view_all = has_tenant_permission(
        request.user,
        tenant,
        TenantPermissions.REPORTS_VIEW,
    )
    templates = WorkflowTemplate.objects.filter(tenant=tenant).order_by("name")
    if not can_view_all:
        templates = supervised_workflow_templates_for_user(
            templates,
            request.user,
            tenant,
        )
    if not can_view_all and not templates.exists():
        raise PermissionDenied

    selected_template = None
    raw_workflow = request.GET.get("workflow", "")
    if raw_workflow:
        try:
            selected_template = templates.get(id=int(raw_workflow))
        except (ValueError, WorkflowTemplate.DoesNotExist):
            raise PermissionDenied
    elif not can_view_all:
        selected_template = templates.first()

    raw_days = request.GET.get("days", "30")
    try:
        days = int(raw_days)
    except ValueError:
        days = 30
    if days not in {7, 30, 90}:
        days = 30

    report = BuildTenantReports(
        tenant=tenant,
        days=days,
        workflow_template=selected_template,
    ).execute()
    return render(
        request,
        "reports/overview.html",
        {
            "tenant": tenant,
            "days": days,
            "day_options": [7, 30, 90],
            "report": report,
            "workflow_templates": templates,
            "selected_workflow": selected_template,
            "can_view_all_workflows": can_view_all,
        },
    )
