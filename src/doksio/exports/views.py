from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from doksio.accounts.permissions import TenantPermissions
from doksio.audit.services import RecordAuditEvent
from doksio.documents.policies import has_tenant_permission
from doksio.exports.forms import DocumentImageExportForm
from doksio.exports.models import ExportRun
from doksio.exports.services import BuildDocumentImageExport
from doksio.tenancy.services import get_tenant_for_user


def _tenant_login_redirect(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_url = reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant_slug})
    return redirect(f"{login_url}?next={request.get_full_path()}")


def document_image_export(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    if not has_tenant_permission(
        request.user,
        tenant,
        TenantPermissions.DOCUMENTS_DOWNLOAD,
    ):
        raise PermissionDenied

    if request.method == "POST":
        form = DocumentImageExportForm(request.POST, tenant=tenant, user=request.user)
        if form.is_valid():
            documents = form.documents_queryset()
            if not documents.exists():
                form.add_error(
                    None,
                    "Für die gewählten Filter gibt es keine exportierbaren Dokumente.",
                )
            else:
                package = BuildDocumentImageExport(
                    tenant=tenant,
                    documents=documents,
                    created_by=request.user,
                    filters=form.filters_payload(),
                ).execute()
                response = HttpResponse(
                    package.content,
                    content_type="application/zip",
                )
                response["Content-Disposition"] = (
                    f'attachment; filename="{package.filename}"'
                )
                return response
    else:
        form = DocumentImageExportForm(tenant=tenant, user=request.user)

    export_runs = ExportRun.objects.filter(
        tenant=tenant,
        export_type=ExportRun.ExportType.DATEV_DOCUMENT_IMAGES,
    ).select_related("created_by")[:10]
    return render(
        request,
        "exports/document_image_export.html",
        {
            "tenant": tenant,
            "form": form,
            "export_runs": export_runs,
        },
    )


def export_run_download(
    request: HttpRequest,
    tenant_slug: str,
    export_run_id: int,
) -> FileResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    if not has_tenant_permission(
        request.user,
        tenant,
        TenantPermissions.DOCUMENTS_DOWNLOAD,
    ):
        raise PermissionDenied

    export_run = get_object_or_404(
        ExportRun,
        id=export_run_id,
        tenant=tenant,
        export_type=ExportRun.ExportType.DATEV_DOCUMENT_IMAGES,
    )
    if not export_run.storage_key or not default_storage.exists(export_run.storage_key):
        raise Http404("Exportdatei wurde nicht gefunden.")

    RecordAuditEvent(
        tenant=tenant,
        actor=request.user,
        event_type="export_run.downloaded",
        object_type="exports.ExportRun",
        object_id=str(export_run.id),
        data={
            "filename": export_run.filename,
            "sha256": export_run.sha256,
            "byte_size": export_run.byte_size,
        },
    ).execute()
    return FileResponse(
        default_storage.open(export_run.storage_key, "rb"),
        as_attachment=True,
        filename=export_run.filename,
        content_type="application/zip",
    )
