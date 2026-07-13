from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.policies import has_tenant_permission
from doksio.reports.services import BuildTenantReports
from doksio.tenancy.services import get_tenant_for_user


def _tenant_login_redirect(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_url = reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant_slug})
    return redirect(f"{login_url}?next={request.get_full_path()}")


def reports_overview(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    if not has_tenant_permission(request.user, tenant, TenantPermissions.REPORTS_VIEW):
        raise PermissionDenied

    raw_days = request.GET.get("days", "30")
    try:
        days = int(raw_days)
    except ValueError:
        days = 30
    if days not in {7, 30, 90}:
        days = 30

    report = BuildTenantReports(tenant=tenant, days=days).execute()
    return render(
        request,
        "reports/overview.html",
        {
            "tenant": tenant,
            "days": days,
            "day_options": [7, 30, 90],
            "report": report,
        },
    )

