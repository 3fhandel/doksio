from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from doksio.helpcenter.catalog import visible_help_topics
from doksio.tenancy.services import get_tenant_for_user


def _tenant_login_redirect(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_url = reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant_slug})
    return redirect(f"{login_url}?next={request.get_full_path()}")


def help_overview(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    return render(
        request,
        "helpcenter/overview.html",
        {
            "tenant": tenant,
            "help_topics": visible_help_topics(
                user=request.user,
                tenant=tenant,
            ),
        },
    )

