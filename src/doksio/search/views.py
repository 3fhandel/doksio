from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.policies import can_administer_tenant, has_tenant_permission
from doksio.pagination import paginate_queryset
from doksio.search.forms import DocumentSearchForm
from doksio.search.services import SearchDocuments, build_search_match
from doksio.tenancy.services import get_tenant_for_user


def _tenant_login_redirect(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_url = reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant_slug})
    return redirect(f"{login_url}?next={request.get_full_path()}")


def document_search(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    if not has_tenant_permission(
        request.user,
        tenant,
        TenantPermissions.DOCUMENTS_VIEW,
    ):
        raise PermissionDenied

    form = DocumentSearchForm(request.GET or None, tenant=tenant, user=request.user)
    documents = []
    documents_count = 0
    documents_page_obj = None
    document_nav = ""
    has_search = bool(request.GET)
    if form.is_valid():
        documents_queryset = SearchDocuments(
            tenant=tenant,
            filters=form.cleaned_data,
            user=request.user,
        ).execute()
        documents_page_obj = paginate_queryset(
            request,
            documents_queryset,
            per_page=25,
        )
        documents = documents_page_obj.object_list
        for document in documents:
            document.search_match = build_search_match(
                document,
                form.cleaned_data.get("q", ""),
            )
        documents_count = documents_page_obj.paginator.count
        document_nav = ",".join(str(document.id) for document in documents)

    return render(
        request,
        "search/document_search.html",
        {
            "tenant": tenant,
            "form": form,
            "documents": documents,
            "documents_count": documents_count,
            "documents_page_obj": documents_page_obj,
            "document_nav": document_nav,
            "has_search": has_search,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )
