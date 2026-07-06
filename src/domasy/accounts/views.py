from __future__ import annotations

from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from domasy.accounts.forms import SystemLoginForm, TenantLoginForm
from domasy.tenancy.models import Tenant
from domasy.tenancy.services import get_tenant_for_user


def _safe_next_url(request: HttpRequest, fallback: str) -> str:
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback


def system_login(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect("admin:index")

    if request.method == "POST":
        form = SystemLoginForm(request=request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect(_safe_next_url(request, reverse("admin:index")))
    else:
        form = SystemLoginForm(request=request)

    return render(
        request,
        "accounts/system_login.html",
        {
            "form": form,
            "next": request.GET.get("next", ""),
        },
    )


def tenant_login(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    tenant = get_object_or_404(Tenant, slug=tenant_slug, is_active=True)
    fallback_url = reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})

    if request.user.is_authenticated and get_tenant_for_user(request.user, tenant.slug):
        return redirect(_safe_next_url(request, fallback_url))

    if request.method == "POST":
        form = TenantLoginForm(request=request, tenant=tenant, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect(_safe_next_url(request, fallback_url))
    else:
        form = TenantLoginForm(request=request, tenant=tenant)

    return render(
        request,
        "accounts/tenant_login.html",
        {
            "form": form,
            "tenant": tenant,
            "next": request.GET.get("next", ""),
        },
    )


def sign_out(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("accounts:system_login")
