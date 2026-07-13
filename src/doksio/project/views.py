from __future__ import annotations

from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from doksio.documents.policies import can_administer_tenant
from doksio.project.status import build_system_status
from doksio.tenancy.services import get_tenant_for_user


def _is_system_admin(user) -> bool:
    return user.is_authenticated and user.is_superuser


@user_passes_test(_is_system_admin, login_url="/s/")
def system_status(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "system/status.html",
        {
            "tenant": None,
            "status": build_system_status(),
            "status_scope": "system",
        },
    )


def tenant_status(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    if not can_administer_tenant(request.user, tenant):
        raise PermissionDenied

    return render(
        request,
        "system/status.html",
        {
            "tenant": tenant,
            "status": build_system_status(tenant=tenant),
            "status_scope": "tenant",
            "can_manage_settings": True,
        },
    )
