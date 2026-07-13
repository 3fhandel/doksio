from __future__ import annotations

import shlex
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db.models import Count, Q
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from doksio.accounts.forms import (
    TenantMembershipCreateForm,
    TenantMembershipUpdateForm,
    TenantRoleCreateForm,
    TenantRoleUpdateForm,
)
from doksio.accounts.models import TenantMembership, TenantRole
from doksio.accounts.permissions import TenantPermissions
from doksio.accounts.services import (
    AddTenantMember,
    CreateTenantRole,
    UpdateTenantMembership,
    UpdateTenantRole,
)
from doksio.audit.models import AuditEvent
from doksio.documents.forms import (
    DocumentCommentForm,
    DocumentCoreMetadataForm,
    DocumentDeleteForm,
    DocumentSpaceDeleteForm,
    DocumentMetadataFieldForm,
    DocumentMetadataForm,
    DocumentSpaceForm,
    DocumentSpaceUpdateForm,
    DocumentTagForm,
    DocumentUploadForm,
)
from doksio.documents.models import (
    Document,
    DocumentFile,
    DocumentMetadataField,
    DocumentSpace,
)
from doksio.documents.metadata import effective_metadata_fields
from doksio.documents.mentions import mention_suggestions_for_tenant
from doksio.documents.policies import (
    can_administer_tenant,
    can_delete_document,
    can_download_document_file,
    can_manage_document_spaces,
    can_manage_members,
    can_manage_roles,
    can_upload_document,
    can_view_audit,
    can_view_document,
    filter_document_spaces_for_user,
    filter_documents_for_user,
)
from doksio.documents.services import (
    AddDocumentComment,
    AddDocumentMetadataChoice,
    CreateDocumentFromUpload,
    CreateDocumentMetadataField,
    CreateDocumentSpace,
    DeleteDocument,
    DeleteDocumentSpace,
    DuplicateDocumentError,
    SetDocumentTags,
    UpdateDocumentCoreMetadata,
    UpdateDocumentMetadata,
    UpdateDocumentMetadataField,
    UpdateDocumentSpace,
)
from doksio.ingestion.forms import ImportSourceForm, TenantSmtpSettingsForm
from doksio.ingestion.models import ImportJob, ImportSource, TenantSmtpSettings
from doksio.ingestion.services import ResolveManualUploadDocumentSpace
from doksio.ocr.services import StartOcrForDocumentFile
from doksio.pagination import paginate_queryset
from doksio.project.url_helpers import build_public_url
from doksio.tenancy.services import get_default_tenant_for_user, get_tenant_for_user
from doksio.workflows.forms import CompleteWorkflowTaskForm, StartWorkflowForm
from doksio.workflows.models import WorkflowInstance, WorkflowTask, WorkflowTemplate
from doksio.workflows.policies import (
    can_complete_workflow_task,
    can_use_workflows,
    filter_workflow_tasks_for_user,
)
from doksio.workflows.services import CompleteWorkflowTask, StartWorkflowForDocument

DOCUMENT_LOG_EVENT_LABELS = {
    "document.created": "Dokument erstellt",
    "document_file.stored": "Datei gespeichert",
    "document_core_metadata.updated": "Kerndaten aktualisiert",
    "document_metadata.updated": "Metadaten aktualisiert",
    "document_comment.created": "Kommentar hinzugefügt",
    "document_space.deleted": "Dokumentenbox gelöscht",
    "document_tags.updated": "Tags aktualisiert",
    "document.deleted": "Dokument gelöscht",
    "document.exported": "Dokument exportiert",
    "export_run.created": "Exportlauf erzeugt",
    "export_run.downloaded": "Export heruntergeladen",
    "workflow_instance.started": "Workflow gestartet",
    "workflow_instance.cancelled": "Workflow abgebrochen",
    "workflow_task.completed": "Workflow-Schritt erledigt",
}

PDF_PREVIEW_CONTENT_TYPES = {"application/pdf"}
IMAGE_PREVIEW_CONTENT_TYPES = {
    "image/avif",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def _with_workflow_counts(documents):
    return documents.annotate(
        workflow_total_count=Count("workflow_instances", distinct=True),
        workflow_completed_count=Count(
            "workflow_instances",
            filter=Q(
                workflow_instances__status=WorkflowInstance.Status.COMPLETED,
            ),
            distinct=True,
        ),
        workflow_open_count=Count(
            "workflow_instances",
            filter=Q(
                workflow_instances__status=WorkflowInstance.Status.RUNNING,
            ),
            distinct=True,
        ),
    )


def _shell_quote(value: object) -> str:
    return shlex.quote("" if value is None else str(value))


def _powershell_quote(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text.replace("'", "''") + "'"


def _render_folder_import_bash_script(
    import_source: ImportSource,
    api_url: str,
) -> str:
    settings = import_source.settings or {}
    folder = settings.get("folder", {})
    return f"""#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR={_shell_quote(folder.get("path", ""))}
FILE_PATTERN={_shell_quote(folder.get("file_pattern", "*"))}
RECURSIVE={_shell_quote("1" if folder.get("recursive") else "0")}
POLL_INTERVAL={_shell_quote(folder.get("poll_interval_seconds", 300))}
AFTER_IMPORT={_shell_quote(folder.get("after_import", "archive"))}
ARCHIVE_DIR={_shell_quote(folder.get("archive_path", ""))}
ERROR_DIR={_shell_quote(folder.get("error_path", ""))}
API_URL={_shell_quote(api_url)}
IMPORT_TOKEN={_shell_quote(import_source.token)}
LOG_FILE="${{DOKSIO_IMPORT_LOG:-$SOURCE_DIR/doksio-folder-import.log}}"

if [[ -z "$SOURCE_DIR" || ! -d "$SOURCE_DIR" ]]; then
  echo "Quellordner existiert nicht: $SOURCE_DIR" >&2
  exit 1
fi

[[ -n "$ARCHIVE_DIR" ]] && mkdir -p "$ARCHIVE_DIR"
[[ -n "$ERROR_DIR" ]] && mkdir -p "$ERROR_DIR"
touch "$LOG_FILE"

log() {{
  local level="$1"
  shift
  printf '%s [%s] %s\\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$*" | tee -a "$LOG_FILE"
}}

safe_move() {{
  local source="$1"
  local target_dir="$2"
  local filename="$3"

  if [[ -z "$target_dir" ]]; then
    return 0
  fi

  mkdir -p "$target_dir"
  local target="$target_dir/$filename"
  if [[ -e "$target" ]]; then
    local stem="${{filename%.*}}"
    local extension=""
    if [[ "$filename" == *.* ]]; then
      extension=".${{filename##*.}}"
    fi
    target="$target_dir/${{stem}}-$(date '+%Y%m%d%H%M%S')$extension"
  fi
  mv "$source" "$target"
  log INFO "Datei verschoben: $source -> $target"
}}

process_file() {{
  local file="$1"
  local filename
  filename="$(basename "$file")"
  log INFO "Import startet: $file"

  local response_file
  response_file="$(mktemp)"
  if curl --fail --silent --show-error \\
    --request PUT \\
    --header "X-Doksio-Import-Token: $IMPORT_TOKEN" \\
    --header "X-Doksio-Filename: $filename" \\
    --header "Content-Type: application/octet-stream" \\
    --data-binary "@$file" \\
    "$API_URL" > "$response_file"; then
    log INFO "Import erfolgreich: $file $(tr -d '\\n' < "$response_file")"
    case "$AFTER_IMPORT" in
      archive)
        safe_move "$file" "$ARCHIVE_DIR" "$filename"
        ;;
      delete)
        rm -f "$file"
        log INFO "Datei gelöscht: $file"
        ;;
      keep)
        log INFO "Datei bleibt im Quellordner: $file"
        ;;
    esac
  else
    log ERROR "Import fehlgeschlagen: $file $(tr -d '\\n' < "$response_file")"
    if [[ -n "$ERROR_DIR" ]]; then
      safe_move "$file" "$ERROR_DIR" "$filename"
    fi
  fi
  rm -f "$response_file"
}}

log INFO "Doksio Ordner-Agent gestartet: $SOURCE_DIR"
while true; do
  if [[ "$RECURSIVE" == "1" ]]; then
    while IFS= read -r -d '' file; do
      process_file "$file"
    done < <(find "$SOURCE_DIR" -type f -name "$FILE_PATTERN" -print0)
  else
    while IFS= read -r -d '' file; do
      process_file "$file"
    done < <(
      find "$SOURCE_DIR" -type f -name "$FILE_PATTERN" \\
        ! -path "$SOURCE_DIR/*/*" -print0
    )
  fi
  sleep "$POLL_INTERVAL"
done
"""


def _render_folder_import_powershell_script(
    import_source: ImportSource,
    api_url: str,
) -> str:
    settings = import_source.settings or {}
    folder = settings.get("folder", {})
    recursive_switch = "$true" if folder.get("recursive") else "$false"
    return f"""$ErrorActionPreference = "Stop"

$SourceDir = {_powershell_quote(folder.get("path", ""))}
$FilePattern = {_powershell_quote(folder.get("file_pattern", "*"))}
$Recursive = {recursive_switch}
$PollIntervalSeconds = {_powershell_quote(folder.get("poll_interval_seconds", 300))}
$AfterImport = {_powershell_quote(folder.get("after_import", "archive"))}
$ArchiveDir = {_powershell_quote(folder.get("archive_path", ""))}
$ErrorDir = {_powershell_quote(folder.get("error_path", ""))}
$ApiUrl = {_powershell_quote(api_url)}
$ImportToken = {_powershell_quote(import_source.token)}
$LogFile = if ($env:DOKSIO_IMPORT_LOG) {{ $env:DOKSIO_IMPORT_LOG }} else {{ Join-Path $SourceDir "doksio-folder-import.log" }}

if (-not $SourceDir -or -not (Test-Path -LiteralPath $SourceDir -PathType Container)) {{
    Write-Error "Quellordner existiert nicht: $SourceDir"
    exit 1
}}

if ($ArchiveDir) {{ New-Item -ItemType Directory -Force -Path $ArchiveDir | Out-Null }}
if ($ErrorDir) {{ New-Item -ItemType Directory -Force -Path $ErrorDir | Out-Null }}
New-Item -ItemType File -Force -Path $LogFile | Out-Null

function Write-DoksioLog {{
    param([string] $Level, [string] $Message)
    $Line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Level] $Message"
    Write-Host $Line
    Add-Content -LiteralPath $LogFile -Value $Line
}}

function Move-DoksioFile {{
    param([string] $Source, [string] $TargetDir, [string] $Filename)
    if (-not $TargetDir) {{ return }}
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

    $Target = Join-Path $TargetDir $Filename
    if (Test-Path -LiteralPath $Target) {{
        $Stem = [System.IO.Path]::GetFileNameWithoutExtension($Filename)
        $Extension = [System.IO.Path]::GetExtension($Filename)
        $Target = Join-Path $TargetDir "$Stem-$(Get-Date -Format 'yyyyMMddHHmmss')$Extension"
    }}

    Move-Item -LiteralPath $Source -Destination $Target -Force
    Write-DoksioLog "INFO" "Datei verschoben: $Source -> $Target"
}}

function Invoke-DoksioImport {{
    param([System.IO.FileInfo] $File)

    $Filename = $File.Name
    Write-DoksioLog "INFO" "Import startet: $($File.FullName)"
    try {{
        $Headers = @{{
            "X-Doksio-Import-Token" = $ImportToken
            "X-Doksio-Filename" = $Filename
        }}
        $Response = Invoke-WebRequest `
            -Uri $ApiUrl `
            -Method Put `
            -Headers $Headers `
            -ContentType "application/octet-stream" `
            -InFile $File.FullName `
            -UseBasicParsing

        Write-DoksioLog "INFO" "Import erfolgreich: $($File.FullName) $($Response.Content)"
        switch ($AfterImport) {{
            "archive" {{ Move-DoksioFile -Source $File.FullName -TargetDir $ArchiveDir -Filename $Filename }}
            "delete" {{
                Remove-Item -LiteralPath $File.FullName -Force
                Write-DoksioLog "INFO" "Datei gelöscht: $($File.FullName)"
            }}
            "keep" {{ Write-DoksioLog "INFO" "Datei bleibt im Quellordner: $($File.FullName)" }}
        }}
    }} catch {{
        Write-DoksioLog "ERROR" "Import fehlgeschlagen: $($File.FullName) $($_.Exception.Message)"
        if ($ErrorDir) {{
            Move-DoksioFile -Source $File.FullName -TargetDir $ErrorDir -Filename $Filename
        }}
    }}
}}

Write-DoksioLog "INFO" "Doksio Ordner-Agent gestartet: $SourceDir"
while ($true) {{
    $Files = Get-ChildItem -LiteralPath $SourceDir -File -Filter $FilePattern -Recurse:$Recursive
    foreach ($File in $Files) {{
        Invoke-DoksioImport -File $File
    }}
    Start-Sleep -Seconds ([int] $PollIntervalSeconds)
}}
"""


def _safe_return_url(
    request: HttpRequest,
    fallback_url: str,
    value: str | None = None,
) -> str:
    candidate = value or request.GET.get("back") or request.META.get("HTTP_REFERER")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback_url


def _document_nav_param(document_ids) -> str:
    return ",".join(str(document_id) for document_id in document_ids)


def _parse_document_nav_param(raw_value: str) -> list[int]:
    document_ids = []
    for raw_id in raw_value.split(","):
        try:
            document_ids.append(int(raw_id))
        except ValueError:
            continue
    return document_ids


def _document_detail_context_url(
    *,
    tenant_slug: str,
    document_id: int,
    back_url: str,
    nav_param: str,
) -> str:
    url = reverse(
        "documents:detail",
        kwargs={"tenant_slug": tenant_slug, "document_id": document_id},
    )
    query_data = {"back": back_url}
    if nav_param:
        query_data["nav"] = nav_param
    query = urlencode(query_data)
    return f"{url}?{query}"


def _document_navigation_context(
    *,
    request: HttpRequest,
    tenant,
    document: Document,
    back_url: str,
) -> dict:
    nav_param = request.GET.get("nav", "")
    document_ids = _parse_document_nav_param(nav_param)
    if document.id not in document_ids:
        return {"document_nav_param": nav_param}

    index = document_ids.index(document.id)
    document_nav_current = index + 1
    document_nav_total = len(document_ids)
    previous_document_url = ""
    next_document_url = ""
    for previous_id in reversed(document_ids[:index]):
        candidate = Document.objects.filter(id=previous_id, tenant=tenant).first()
        if candidate is not None and can_view_document(request.user, candidate):
            previous_document_url = _document_detail_context_url(
                tenant_slug=tenant.slug,
                document_id=candidate.id,
                back_url=back_url,
                nav_param=nav_param,
            )
            break
    for next_id in document_ids[index + 1 :]:
        candidate = Document.objects.filter(id=next_id, tenant=tenant).first()
        if candidate is not None and can_view_document(request.user, candidate):
            next_document_url = _document_detail_context_url(
                tenant_slug=tenant.slug,
                document_id=candidate.id,
                back_url=back_url,
                nav_param=nav_param,
            )
            break
    return {
        "document_nav_param": nav_param,
        "document_nav_current": document_nav_current,
        "document_nav_total": document_nav_total,
        "previous_document_url": previous_document_url,
        "next_document_url": next_document_url,
    }


def _tenant_login_redirect(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_url = reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant_slug})
    return redirect(f"{login_url}?next={request.get_full_path()}")


def _system_login_redirect(request: HttpRequest) -> HttpResponse:
    login_url = reverse("accounts:system_login")
    return redirect(f"{login_url}?next={request.get_full_path()}")


def _document_log_entries(document: Document):
    events = (
        AuditEvent.objects.filter(tenant=document.tenant)
        .filter(
            Q(object_type="documents.Document", object_id=str(document.id))
            | Q(data__document_id=document.id)
        )
        .select_related("actor")
        .order_by("-created_at", "-id")
    )
    return [
        {
            "event": event,
            "label": DOCUMENT_LOG_EVENT_LABELS.get(event.event_type, event.event_type),
        }
        for event in events
    ]


AUDIT_EVENT_LABELS = DOCUMENT_LOG_EVENT_LABELS | {
    "export_run.created": "Exportlauf erzeugt",
}


def _document_preview(document: Document) -> tuple[DocumentFile | None, str]:
    pdf_file = (
        document.files.filter(content_type__in=PDF_PREVIEW_CONTENT_TYPES)
        .order_by("file_kind", "-version", "-created_at")
        .first()
    )
    if pdf_file is not None:
        return pdf_file, "pdf"

    image_file = (
        document.files.filter(content_type__in=IMAGE_PREVIEW_CONTENT_TYPES)
        .order_by("file_kind", "-version", "-created_at")
        .first()
    )
    if image_file is not None:
        return image_file, "image"
    return None, ""


def dashboard_redirect(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return _system_login_redirect(request)

    tenant = get_default_tenant_for_user(request.user)
    if tenant is None:
        return render(
            request,
            "documents/dashboard.html",
            {
                "tenant": None,
                "documents": Document.objects.none(),
                "documents_count": 0,
                "documents_page_obj": None,
                "workflow_tasks": WorkflowTask.objects.none(),
                "workflow_tasks_count": 0,
                "workflow_documents_count": 0,
                "workflow_tasks_page_obj": None,
                "document_nav": "",
                "workflow_task_document_nav": "",
            },
        )
    return redirect("documents:dashboard", tenant_slug=tenant.slug)


def _open_workflow_tasks_for_user(request: HttpRequest, tenant):
    return filter_workflow_tasks_for_user(
        WorkflowTask.objects.filter(
            tenant=tenant,
            status=WorkflowTask.Status.OPEN,
        )
        .select_related(
            "assigned_role",
            "document",
            "document__space",
            "instance__template",
            "step",
        )
        .prefetch_related("document__files")
        .order_by("created_at", "id"),
        request.user,
        tenant,
    )


def _workflow_task_filter_options(workflow_tasks_queryset):
    template_ids = (
        workflow_tasks_queryset.order_by()
        .values_list("instance__template_id", flat=True)
        .distinct()
    )
    return list(
        WorkflowTemplate.objects.filter(id__in=template_ids).order_by("name", "id")
    )


def _selected_workflow_template_id(
    request: HttpRequest,
    workflow_options,
) -> int | None:
    raw_value = request.GET.get("workflow", "").strip()
    if not raw_value:
        return None

    try:
        selected_id = int(raw_value)
    except ValueError:
        return None

    option_ids = {template.id for template in workflow_options}
    if selected_id not in option_ids:
        return None
    return selected_id


def dashboard(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    documents_queryset = _with_workflow_counts(
        filter_documents_for_user(
            Document.objects.filter(tenant=tenant)
            .select_related("space")
            .prefetch_related("files")
            .order_by("-created_at", "-id"),
            request.user,
            tenant,
        )
    )
    documents_page_obj = paginate_queryset(
        request,
        documents_queryset,
        page_param="uploads_page",
        per_page=10,
    )
    workflow_tasks_queryset = _open_workflow_tasks_for_user(request, tenant)
    workflow_filter_options = _workflow_task_filter_options(workflow_tasks_queryset)
    selected_workflow_id = _selected_workflow_template_id(
        request,
        workflow_filter_options,
    )
    if selected_workflow_id is not None:
        workflow_tasks_queryset = workflow_tasks_queryset.filter(
            instance__template_id=selected_workflow_id,
        )
    workflow_tasks_page_obj = paginate_queryset(
        request,
        workflow_tasks_queryset,
        page_param="tasks_page",
        per_page=10,
    )
    workflow_documents_count = (
        workflow_tasks_queryset.order_by()
        .values("document_id")
        .distinct()
        .count()
    )
    document_nav = _document_nav_param(
        document.id for document in documents_page_obj.object_list
    )
    workflow_task_document_nav = _document_nav_param(
        dict.fromkeys(task.document_id for task in workflow_tasks_page_obj.object_list)
    )
    return render(
        request,
        "documents/dashboard.html",
        {
            "tenant": tenant,
            "documents": documents_page_obj.object_list,
            "documents_count": documents_page_obj.paginator.count,
            "documents_page_obj": documents_page_obj,
            "workflow_tasks": workflow_tasks_page_obj.object_list,
            "workflow_tasks_count": workflow_tasks_page_obj.paginator.count,
            "workflow_documents_count": workflow_documents_count,
            "workflow_tasks_page_obj": workflow_tasks_page_obj,
            "workflow_filter_options": workflow_filter_options,
            "selected_workflow_id": selected_workflow_id,
            "document_nav": document_nav,
            "workflow_task_document_nav": workflow_task_document_nav,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def task_list(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    workflow_tasks_queryset = _open_workflow_tasks_for_user(request, tenant)
    workflow_filter_options = _workflow_task_filter_options(workflow_tasks_queryset)
    selected_workflow_id = _selected_workflow_template_id(
        request,
        workflow_filter_options,
    )
    if selected_workflow_id is not None:
        workflow_tasks_queryset = workflow_tasks_queryset.filter(
            instance__template_id=selected_workflow_id,
        )
    workflow_tasks_page_obj = paginate_queryset(
        request,
        workflow_tasks_queryset,
        per_page=25,
    )
    workflow_documents_count = (
        workflow_tasks_queryset.order_by()
        .values("document_id")
        .distinct()
        .count()
    )
    workflow_task_document_nav = _document_nav_param(
        dict.fromkeys(task.document_id for task in workflow_tasks_page_obj.object_list)
    )
    return render(
        request,
        "documents/task_list.html",
        {
            "tenant": tenant,
            "workflow_tasks": workflow_tasks_page_obj.object_list,
            "workflow_tasks_count": workflow_tasks_page_obj.paginator.count,
            "workflow_documents_count": workflow_documents_count,
            "workflow_tasks_page_obj": workflow_tasks_page_obj,
            "workflow_filter_options": workflow_filter_options,
            "selected_workflow_id": selected_workflow_id,
            "workflow_task_document_nav": workflow_task_document_nav,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_list(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    documents_queryset = _with_workflow_counts(
        filter_documents_for_user(
            Document.objects.filter(tenant=tenant)
            .select_related("space")
            .prefetch_related("files")
            .order_by("-created_at", "-id"),
            request.user,
            tenant,
        )
    )
    documents_page_obj = paginate_queryset(
        request,
        documents_queryset,
        per_page=25,
    )
    document_nav = _document_nav_param(
        document.id for document in documents_page_obj.object_list
    )
    return render(
        request,
        "documents/document_list.html",
        {
            "tenant": tenant,
            "documents": documents_page_obj.object_list,
            "documents_count": documents_page_obj.paginator.count,
            "documents_page_obj": documents_page_obj,
            "document_nav": document_nav,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_upload(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    if not can_upload_document(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = DocumentUploadForm(
            request.POST,
            request.FILES,
            tenant=tenant,
            user=request.user,
        )
        if form.is_valid():
            uploaded_files = form.cleaned_data["file"]
            selected_document_space = form.cleaned_data["space"]
            imported_documents = []
            duplicate_documents = []
            manual_title = form.cleaned_data["title"]
            if len(uploaded_files) > 1 and manual_title:
                messages.info(
                    request,
                    (
                        "Der manuelle Titel wird nur beim Einzelupload verwendet. "
                        "Für den Stapel setzt Doksio die Titel automatisch."
                    ),
                )
                manual_title = ""

            for uploaded_file in uploaded_files:
                document_space = selected_document_space
                upload_source = None
                if document_space is None:
                    try:
                        (
                            document_space,
                            upload_source,
                        ) = ResolveManualUploadDocumentSpace(
                            tenant=tenant,
                            original_filename=uploaded_file.name,
                        ).execute()
                    except ValueError:
                        form.add_error(
                            "space",
                            (
                                "Bitte wähle eine Dokumentenbox oder hinterlege "
                                "eine aktive Upload-Importstrategie."
                            ),
                        )
                        break
                    else:
                        user_can_upload_to_space = filter_document_spaces_for_user(
                            DocumentSpace.objects.filter(id=document_space.id),
                            request.user,
                            tenant,
                            TenantPermissions.DOCUMENTS_UPLOAD,
                        ).exists()
                        if not user_can_upload_to_space:
                            form.add_error(
                                "space",
                                (
                                    "Die Upload-Importstrategie verweist auf eine "
                                    "Dokumentenbox, für die du keine Berechtigung hast."
                                ),
                            )
                            break

                try:
                    document, _document_file = CreateDocumentFromUpload(
                        tenant=tenant,
                        title=manual_title,
                        space=document_space,
                        file_obj=uploaded_file,
                        original_filename=uploaded_file.name,
                        content_type=uploaded_file.content_type
                        or "application/octet-stream",
                        created_by=request.user,
                        auto_start_ocr=(
                            upload_source.auto_start_ocr
                            if upload_source is not None
                            else None
                        ),
                        auto_start_workflows=(
                            upload_source.start_workflows
                            if upload_source is not None
                            else True
                        ),
                    ).execute()
                    if upload_source is not None and upload_source.default_tags:
                        SetDocumentTags(
                            document=document,
                            tag_names=upload_source.default_tags,
                            actor=request.user,
                        ).execute()
                except DuplicateDocumentError as exc:
                    duplicate_documents.append(exc.existing_document)
                    if len(uploaded_files) == 1:
                        messages.warning(
                            request,
                            (
                                "Diese Datei existiert bereits. "
                                "Das vorhandene Dokument wurde geöffnet."
                            ),
                        )
                        return redirect(
                            "documents:detail",
                            tenant_slug=tenant.slug,
                            document_id=exc.existing_document.id,
                        )
                else:
                    imported_documents.append(document)

            if not form.errors:
                if duplicate_documents:
                    messages.warning(
                        request,
                        (
                            f"{len(duplicate_documents)} Datei"
                            f"{'en' if len(duplicate_documents) != 1 else ''} "
                            "wurden als Dublette erkannt und übersprungen."
                        ),
                    )
                if len(imported_documents) == 1:
                    messages.success(request, "Dokument wurde gespeichert.")
                    return redirect(
                        "documents:detail",
                        tenant_slug=tenant.slug,
                        document_id=imported_documents[0].id,
                    )
                if imported_documents:
                    messages.success(
                        request,
                        (
                            f"{len(imported_documents)} Dokumente wurden "
                            "gespeichert."
                        ),
                    )
                return redirect("documents:dashboard", tenant_slug=tenant.slug)
    else:
        form = DocumentUploadForm(tenant=tenant, user=request.user)

    return render(
        request,
        "documents/document_upload.html",
        {
            "tenant": tenant,
            "form": form,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_detail(
    request: HttpRequest,
    tenant_slug: str,
    document_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    document_queryset = Document.objects.select_related("space").prefetch_related(
        "files",
        "files__ocr_jobs",
        "comments__created_by",
        "comments__mentioned_users",
        "tag_assignments__tag",
        "space__metadata_fields",
    )
    document = get_object_or_404(
        document_queryset,
        id=document_id,
        tenant=tenant,
        status=Document.Status.ACTIVE,
    )
    if not can_view_document(request.user, document):
        raise PermissionDenied

    back_url = _safe_return_url(
        request,
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug}),
    )
    navigation_context = _document_navigation_context(
        request=request,
        tenant=tenant,
        document=document,
        back_url=back_url,
    )
    comment_form = DocumentCommentForm()
    metadata_fields = effective_metadata_fields(document.space)
    metadata_form = DocumentMetadataForm(
        metadata_fields=metadata_fields,
        metadata=document.metadata,
    )
    tag_form = DocumentTagForm(
        tenant=tenant,
        initial={
            "tag_names": ", ".join(
                assignment.tag.name for assignment in document.tag_assignments.all()
            )
        },
    )
    start_workflow_form = StartWorkflowForm(tenant=tenant)
    complete_workflow_task_form = CompleteWorkflowTaskForm()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_comment":
            comment_form = DocumentCommentForm(request.POST)
            if comment_form.is_valid():
                AddDocumentComment(
                    document=document,
                    body=comment_form.cleaned_data["body"],
                    actor=request.user,
                ).execute()
                messages.success(request, "Kommentar wurde hinzugefügt.")
                return redirect(request.get_full_path())
        elif action == "update_tags":
            tag_form = DocumentTagForm(request.POST, tenant=tenant)
            if tag_form.is_valid():
                SetDocumentTags(
                    document=document,
                    tag_names=tag_form.cleaned_data["tag_names"],
                    actor=request.user,
                ).execute()
                messages.success(request, "Tags wurden aktualisiert.")
                return redirect(request.get_full_path())
        elif action == "update_metadata":
            metadata_form = DocumentMetadataForm(
                request.POST,
                metadata_fields=metadata_fields,
                metadata=document.metadata,
            )
            if metadata_form.is_valid():
                for field, value in metadata_form.custom_choice_values().items():
                    AddDocumentMetadataChoice(
                        metadata_field=field,
                        value=value,
                        actor=request.user,
                    ).execute()
                UpdateDocumentMetadata(
                    document=document,
                    metadata=metadata_form.cleaned_metadata(),
                    actor=request.user,
                ).execute()
                messages.success(request, "Metadaten wurden aktualisiert.")
                return redirect(request.get_full_path())
        elif action == "start_ocr":
            if not can_upload_document(request.user, tenant):
                raise PermissionDenied
            document_file = get_object_or_404(
                DocumentFile,
                id=request.POST.get("file_id"),
                tenant=tenant,
                document=document,
            )
            StartOcrForDocumentFile(
                document_file=document_file,
                actor=request.user,
            ).execute()
            messages.success(request, "OCR wurde gestartet.")
            return redirect(request.get_full_path())
        elif action == "start_workflow":
            if not can_use_workflows(request.user, tenant):
                raise PermissionDenied
            start_workflow_form = StartWorkflowForm(request.POST, tenant=tenant)
            if start_workflow_form.is_valid():
                StartWorkflowForDocument(
                    template=start_workflow_form.cleaned_data["template"],
                    document=document,
                    actor=request.user,
                ).execute()
                messages.success(request, "Workflow wurde gestartet.")
                return redirect(request.get_full_path())
        elif action == "complete_workflow_task":
            complete_workflow_task_form = CompleteWorkflowTaskForm(request.POST)
            if complete_workflow_task_form.is_valid():
                task = get_object_or_404(
                    WorkflowTask.objects.select_related(
                        "tenant",
                        "document",
                        "step",
                        "instance",
                        "assigned_role",
                    ),
                    id=complete_workflow_task_form.cleaned_data["task_id"],
                    tenant=tenant,
                    document=document,
                    status=WorkflowTask.Status.OPEN,
                )
                if not can_complete_workflow_task(request.user, task):
                    raise PermissionDenied
                CompleteWorkflowTask(
                    task=task,
                    actor=request.user,
                    comment=complete_workflow_task_form.cleaned_data["comment"],
                ).execute()
                messages.success(request, "Workflow-Aufgabe wurde erledigt.")
                return redirect(request.get_full_path())

    preview_file, preview_kind = _document_preview(document)
    preview_ocr_job = preview_file.latest_ocr_job if preview_file is not None else None
    workflow_instances = list(
        document.workflow_instances.select_related(
            "template",
            "current_step",
        ).prefetch_related("tasks__step", "tasks__assigned_role")
    )
    open_workflow_instances = [
        instance
        for instance in workflow_instances
        if instance.status == WorkflowInstance.Status.RUNNING
    ]
    open_workflow_tasks_queryset = document.workflow_tasks.filter(
        status=WorkflowTask.Status.OPEN,
    ).select_related("step", "assigned_role", "instance__template")
    open_workflow_tasks = [
        task
        for task in open_workflow_tasks_queryset
        if can_complete_workflow_task(request.user, task)
    ]
    workflow_templates_available = WorkflowTemplate.objects.filter(
        tenant=tenant,
        is_active=True,
        trigger_type=WorkflowTemplate.TriggerType.MANUAL,
    ).exists()
    comments = list(document.comments.all())

    return render(
        request,
        "documents/document_detail.html",
        {
            "tenant": tenant,
            "document": document,
            "back_url": back_url,
            **navigation_context,
            "preview_file": preview_file,
            "preview_kind": preview_kind,
            "preview_ocr_job": preview_ocr_job,
            "comment_form": comment_form,
            "metadata_form": metadata_form,
            "tag_form": tag_form,
            "start_workflow_form": start_workflow_form,
            "complete_workflow_task_form": complete_workflow_task_form,
            "workflow_instances": workflow_instances,
            "open_workflow_instances": open_workflow_instances,
            "open_workflow_tasks": open_workflow_tasks,
            "workflow_templates_available": workflow_templates_available,
            "comments": comments,
            "comments_count": len(comments),
            "latest_comment": comments[-1] if comments else None,
            "mention_suggestions": mention_suggestions_for_tenant(tenant),
            "document_log_entries": _document_log_entries(document),
            "can_use_workflows": can_use_workflows(request.user, tenant),
            "can_start_ocr": can_upload_document(request.user, tenant),
            "can_delete_document": can_delete_document(request.user, document),
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_delete(
    request: HttpRequest,
    tenant_slug: str,
    document_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    document = get_object_or_404(
        Document.objects.select_related("space"),
        id=document_id,
        tenant=tenant,
        status=Document.Status.ACTIVE,
    )
    if not can_delete_document(request.user, document):
        raise PermissionDenied

    back_url = _safe_return_url(
        request,
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug}),
        value=request.GET.get("back"),
    )
    detail_url = _document_detail_context_url(
        tenant_slug=tenant.slug,
        document_id=document.id,
        back_url=back_url,
        nav_param=request.GET.get("nav", ""),
    )
    if request.method == "POST":
        form = DocumentDeleteForm(request.POST)
        if form.is_valid():
            DeleteDocument(
                document=document,
                reason=form.cleaned_data["reason"],
                actor=request.user,
            ).execute()
            messages.success(request, "Dokument wurde gelöscht.")
            return redirect(back_url)
    else:
        form = DocumentDeleteForm()

    return render(
        request,
        "documents/document_delete.html",
        {
            "tenant": tenant,
            "document": document,
            "form": form,
            "detail_url": detail_url,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_core_metadata_edit(
    request: HttpRequest,
    tenant_slug: str,
    document_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    document = get_object_or_404(
        Document.objects.select_related("space"),
        id=document_id,
        tenant=tenant,
        status=Document.Status.ACTIVE,
    )
    if not can_view_document(request.user, document):
        raise PermissionDenied

    back_url = _safe_return_url(
        request,
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug}),
    )
    detail_url = _document_detail_context_url(
        tenant_slug=tenant.slug,
        document_id=document.id,
        back_url=back_url,
        nav_param=request.GET.get("nav", ""),
    )
    form = DocumentCoreMetadataForm(
        request.POST or None,
        tenant=tenant,
        user=request.user,
        initial={
            "title": document.title,
            "document_date": document.document_date,
            "space": document.space_id,
        },
    )
    if request.method == "POST" and form.is_valid():
        UpdateDocumentCoreMetadata(
            document=document,
            title=form.cleaned_data["title"],
            document_date=form.cleaned_data["document_date"],
            space=form.cleaned_data["space"],
            actor=request.user,
        ).execute()
        messages.success(request, "Kerndaten wurden aktualisiert.")
        return redirect(detail_url)

    return render(
        request,
        "documents/document_core_metadata_form.html",
        {
            "tenant": tenant,
            "document": document,
            "form": form,
            "detail_url": detail_url,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_file_download(
    request: HttpRequest,
    tenant_slug: str,
    file_id: int,
) -> FileResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    document_file = get_object_or_404(
        DocumentFile.objects.select_related("document", "tenant"),
        id=file_id,
        tenant=tenant,
    )
    if not can_download_document_file(request.user, document_file):
        raise PermissionDenied

    file_handle = default_storage.open(document_file.storage_key, "rb")
    return FileResponse(
        file_handle,
        as_attachment=request.GET.get("inline") != "1",
        filename=document_file.original_filename,
        content_type=document_file.content_type,
    )


def audit_log(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_view_audit(request.user, tenant):
        raise PermissionDenied

    import_jobs = (
        ImportJob.objects.filter(tenant=tenant)
        .select_related("source", "document_space", "document")
        .order_by("-received_at", "-id")
    )
    audit_events = (
        AuditEvent.objects.filter(tenant=tenant)
        .select_related("actor")
        .order_by("-created_at", "-id")
    )
    return render(
        request,
        "documents/audit_log.html",
        {
            "tenant": tenant,
            "import_jobs_page_obj": paginate_queryset(
                request,
                import_jobs,
                page_param="imports_page",
                per_page=15,
            ),
            "audit_events_page_obj": paginate_queryset(
                request,
                audit_events,
                page_param="audit_page",
                per_page=25,
            ),
            "audit_event_labels": AUDIT_EVENT_LABELS,
        },
    )


def index(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("documents:dashboard_redirect")
    login_url = reverse("accounts:system_login")
    next_url = reverse("documents:dashboard_redirect")
    return redirect(f"{login_url}?next={next_url}")


def tenant_settings_document_boxes(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    spaces = (
        DocumentSpace.objects.filter(tenant=tenant, deleted_at__isnull=True)
        .annotate(document_count=Count("documents", distinct=True))
        .order_by("path")
    )
    return render(
        request,
        "documents/settings_document_boxes.html",
        {
            "tenant": tenant,
            "spaces": spaces,
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_document_box_create(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = DocumentSpaceForm(request.POST, tenant=tenant)
        if form.is_valid():
            CreateDocumentSpace(
                tenant=tenant,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                parent=form.cleaned_data["parent"],
                description=form.cleaned_data["description"],
                datev_document_image_export_enabled=form.cleaned_data[
                    "datev_document_image_export_enabled"
                ],
            ).execute()
            messages.success(request, "Dokumentenbox wurde erstellt.")
            return redirect(
                "documents:settings_document_boxes",
                tenant_slug=tenant.slug,
            )
    else:
        form = DocumentSpaceForm(tenant=tenant)

    return render(
        request,
        "documents/settings_document_box_form.html",
        {
            "tenant": tenant,
            "form": form,
            "form_title": "Dokumentenbox erstellen",
            "submit_label": "Box erstellen",
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_document_box_edit(
    request: HttpRequest,
    tenant_slug: str,
    box_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    document_space = get_object_or_404(DocumentSpace, id=box_id, tenant=tenant)

    if request.method == "POST":
        form = DocumentSpaceUpdateForm(
            request.POST,
            tenant=tenant,
            document_space=document_space,
        )
        if form.is_valid():
            UpdateDocumentSpace(
                document_space=document_space,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                parent=form.cleaned_data["parent"],
                description=form.cleaned_data["description"],
                datev_document_image_export_enabled=form.cleaned_data[
                    "datev_document_image_export_enabled"
                ],
                review_assist_enabled=document_space.review_assist_enabled,
                is_active=form.cleaned_data["is_active"],
            ).execute()
            messages.success(request, "Dokumentenbox wurde aktualisiert.")
            return redirect(
                "documents:settings_document_boxes",
                tenant_slug=tenant.slug,
            )
    else:
        form = DocumentSpaceUpdateForm(
            tenant=tenant,
            document_space=document_space,
            initial={
                "name": document_space.name,
                "slug": document_space.slug,
                "parent": document_space.parent_id,
                "description": document_space.description,
                "datev_document_image_export_enabled": (
                    document_space.datev_document_image_export_enabled
                ),
                "is_active": document_space.is_active,
            },
        )

    return render(
        request,
        "documents/settings_document_box_form.html",
        {
            "tenant": tenant,
            "form": form,
            "document_space": document_space,
            "metadata_fields": document_space.metadata_fields.order_by(
                "sort_order",
                "name",
            ),
            "inherited_metadata_fields": [
                field
                for field in effective_metadata_fields(document_space)
                if field.space_id != document_space.id
            ],
            "form_title": "Dokumentenbox bearbeiten",
            "submit_label": "Box speichern",
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_document_box_delete(
    request: HttpRequest,
    tenant_slug: str,
    box_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    document_space = get_object_or_404(
        DocumentSpace,
        id=box_id,
        tenant=tenant,
        deleted_at__isnull=True,
    )
    subtree_filter = Q(path=document_space.path) | Q(
        path__startswith=f"{document_space.path.rstrip('/')}/"
    )
    subtree_spaces = list(
        DocumentSpace.objects.filter(
            tenant=tenant,
            deleted_at__isnull=True,
        )
        .filter(subtree_filter)
        .order_by("path")
    )
    subtree_ids = [space.id for space in subtree_spaces]
    document_count = Document.objects.filter(
        tenant=tenant,
        space_id__in=subtree_ids,
    ).count()
    active_document_count = Document.objects.filter(
        tenant=tenant,
        space_id__in=subtree_ids,
        status=Document.Status.ACTIVE,
    ).count()

    if request.method == "POST":
        form = DocumentSpaceDeleteForm(
            request.POST,
            tenant=tenant,
            document_space=document_space,
        )
        if form.is_valid():
            DeleteDocumentSpace(
                document_space=document_space,
                strategy=form.cleaned_data["strategy"],
                target_space=form.cleaned_data["target_space"],
                delete_reason=form.cleaned_data["delete_reason"],
                actor=request.user,
            ).execute()
            messages.success(request, "Dokumentenbox wurde gelöscht.")
            return redirect(
                "documents:settings_document_boxes",
                tenant_slug=tenant.slug,
            )
    else:
        form = DocumentSpaceDeleteForm(
            tenant=tenant,
            document_space=document_space,
            initial={"strategy": DocumentSpaceDeleteForm.Strategy.MOVE},
        )

    return render(
        request,
        "documents/settings_document_box_delete.html",
        {
            "tenant": tenant,
            "document_space": document_space,
            "subtree_spaces": subtree_spaces,
            "document_count": document_count,
            "active_document_count": active_document_count,
            "form": form,
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_import_sources(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    import_sources = ImportSource.objects.filter(tenant=tenant).select_related(
        "document_space"
    )
    return render(
        request,
        "documents/settings_import_sources.html",
        {
            "tenant": tenant,
            "import_sources": import_sources,
            "active_settings_section": "import",
        },
    )


def tenant_settings_smtp(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    smtp_settings = TenantSmtpSettings.objects.filter(tenant=tenant).first()
    if request.method == "POST":
        form = TenantSmtpSettingsForm(request.POST)
        if form.is_valid():
            smtp_settings, _created = TenantSmtpSettings.objects.update_or_create(
                tenant=tenant,
                defaults={
                    "host": form.cleaned_data["host"],
                    "port": form.cleaned_data["port"] or 587,
                    "security": form.cleaned_data["security"]
                    or TenantSmtpSettings.Security.STARTTLS,
                    "username": form.cleaned_data["username"],
                    "password": form.cleaned_data["password"],
                    "from_email": form.cleaned_data["from_email"],
                    "from_name": form.cleaned_data["from_name"],
                    "is_active": form.cleaned_data["is_active"],
                },
            )
            messages.success(request, "SMTP-Einstellungen wurden gespeichert.")
            return redirect("documents:settings_smtp", tenant_slug=tenant.slug)
    else:
        form = TenantSmtpSettingsForm(
            initial=TenantSmtpSettingsForm.initial_from_settings(smtp_settings),
        )

    return render(
        request,
        "documents/settings_smtp.html",
        {
            "tenant": tenant,
            "form": form,
            "smtp_settings": smtp_settings,
            "active_settings_section": "smtp",
        },
    )


def tenant_settings_import_source_create(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = ImportSourceForm(request.POST, tenant=tenant)
        if form.is_valid():
            ImportSource.objects.create(
                tenant=tenant,
                document_space=form.cleaned_data["document_space"],
                name=form.cleaned_data["name"],
                source_type=form.cleaned_data["source_type"],
                target_strategy=form.cleaned_data["target_strategy"],
                settings=form.import_settings,
                auto_start_ocr=form.cleaned_data["auto_start_ocr"],
                extract_einvoice=True,
                start_workflows=form.cleaned_data["start_workflows"],
                default_tags=form.cleaned_data["default_tags_text"],
                is_active=form.cleaned_data["is_active"],
            )
            messages.success(request, "Importquelle wurde erstellt.")
            return redirect(
                "documents:settings_import_sources",
                tenant_slug=tenant.slug,
            )
    else:
        form = ImportSourceForm(tenant=tenant)

    return render(
        request,
        "documents/settings_import_source_form.html",
        {
            "tenant": tenant,
            "form": form,
            "form_title": "Importquelle erstellen",
            "submit_label": "Quelle erstellen",
            "active_settings_section": "import",
            "http_import_url": "",
        },
    )


def tenant_settings_import_source_edit(
    request: HttpRequest,
    tenant_slug: str,
    source_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    import_source = get_object_or_404(
        ImportSource,
        id=source_id,
        tenant=tenant,
    )
    if request.method == "POST":
        form = ImportSourceForm(request.POST, tenant=tenant)
        if form.is_valid():
            import_source.name = form.cleaned_data["name"]
            import_source.source_type = form.cleaned_data["source_type"]
            import_source.target_strategy = form.cleaned_data["target_strategy"]
            import_source.document_space = form.cleaned_data["document_space"]
            import_source.settings = form.import_settings
            import_source.auto_start_ocr = form.cleaned_data["auto_start_ocr"]
            import_source.extract_einvoice = True
            import_source.start_workflows = form.cleaned_data["start_workflows"]
            import_source.default_tags = form.cleaned_data["default_tags_text"]
            import_source.is_active = form.cleaned_data["is_active"]
            import_source.save(
                update_fields=[
                    "name",
                    "source_type",
                    "target_strategy",
                    "document_space",
                    "settings",
                    "auto_start_ocr",
                    "extract_einvoice",
                    "start_workflows",
                    "default_tags",
                    "is_active",
                    "updated_at",
                ]
            )
            messages.success(request, "Importquelle wurde aktualisiert.")
            return redirect(
                "documents:settings_import_sources",
                tenant_slug=tenant.slug,
            )
    else:
        form = ImportSourceForm(
            tenant=tenant,
            initial=ImportSourceForm.initial_from_source(import_source),
        )
    http_import_url = build_public_url(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": import_source.id},
        )
    )

    return render(
        request,
        "documents/settings_import_source_form.html",
        {
            "tenant": tenant,
            "import_source": import_source,
            "form": form,
            "form_title": "Importquelle bearbeiten",
            "submit_label": "Quelle speichern",
            "active_settings_section": "import",
            "http_import_url": http_import_url,
        },
    )


def tenant_settings_import_source_script(
    request: HttpRequest,
    tenant_slug: str,
    source_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    import_source = get_object_or_404(
        ImportSource,
        id=source_id,
        tenant=tenant,
        source_type=ImportSource.SourceType.FOLDER,
    )
    api_url = build_public_url(
        reverse(
            "ingestion:http_import",
            kwargs={"tenant_slug": tenant.slug, "source_id": import_source.id},
        )
    )
    platform = request.GET.get("platform", "bash")
    if platform == "windows":
        script = _render_folder_import_powershell_script(import_source, api_url)
        filename = f"doksio-folder-import-{import_source.id}.ps1"
        content_type = "text/plain; charset=utf-8"
    else:
        script = _render_folder_import_bash_script(import_source, api_url)
        filename = f"doksio-folder-import-{import_source.id}.sh"
        content_type = "text/x-shellscript; charset=utf-8"

    response = HttpResponse(
        script,
        content_type=content_type,
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def tenant_settings_metadata_field_create(
    request: HttpRequest,
    tenant_slug: str,
    box_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    document_space = get_object_or_404(DocumentSpace, id=box_id, tenant=tenant)
    if request.method == "POST":
        form = DocumentMetadataFieldForm(
            request.POST,
            tenant=tenant,
            document_space=document_space,
        )
        if form.is_valid():
            CreateDocumentMetadataField(
                tenant=tenant,
                space=document_space,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                field_type=form.cleaned_data["field_type"],
                help_text=form.cleaned_data["help_text"],
                choices=form.cleaned_data["choices"],
                allow_custom_choices=form.cleaned_data["allow_custom_choices"],
                einvoice_source=form.cleaned_data["einvoice_source"],
                sort_order=form.cleaned_data["sort_order"],
                is_required=form.cleaned_data["is_required"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Metadatenfeld wurde erstellt.")
            return redirect(
                "documents:settings_document_box_edit",
                tenant_slug=tenant.slug,
                box_id=document_space.id,
            )
    else:
        form = DocumentMetadataFieldForm(
            tenant=tenant,
            document_space=document_space,
        )

    return render(
        request,
        "documents/settings_metadata_field_form.html",
        {
            "tenant": tenant,
            "document_space": document_space,
            "form": form,
            "form_title": "Metadatenfeld erstellen",
            "submit_label": "Feld erstellen",
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_metadata_field_edit(
    request: HttpRequest,
    tenant_slug: str,
    box_id: int,
    field_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    document_space = get_object_or_404(DocumentSpace, id=box_id, tenant=tenant)
    metadata_field = get_object_or_404(
        DocumentMetadataField,
        id=field_id,
        tenant=tenant,
        space=document_space,
    )
    if request.method == "POST":
        form = DocumentMetadataFieldForm(
            request.POST,
            tenant=tenant,
            document_space=document_space,
            metadata_field=metadata_field,
        )
        if form.is_valid():
            UpdateDocumentMetadataField(
                metadata_field=metadata_field,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                field_type=form.cleaned_data["field_type"],
                help_text=form.cleaned_data["help_text"],
                choices=form.cleaned_data["choices"],
                allow_custom_choices=form.cleaned_data["allow_custom_choices"],
                einvoice_source=form.cleaned_data["einvoice_source"],
                sort_order=form.cleaned_data["sort_order"],
                is_required=form.cleaned_data["is_required"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Metadatenfeld wurde aktualisiert.")
            return redirect(
                "documents:settings_document_box_edit",
                tenant_slug=tenant.slug,
                box_id=document_space.id,
            )
    else:
        form = DocumentMetadataFieldForm(
            tenant=tenant,
            document_space=document_space,
            metadata_field=metadata_field,
            initial={
                "name": metadata_field.name,
                "slug": metadata_field.slug,
                "field_type": metadata_field.field_type,
                "help_text": metadata_field.help_text,
                "choices_text": "\n".join(metadata_field.choices),
                "allow_custom_choices": metadata_field.allow_custom_choices,
                "einvoice_source": metadata_field.einvoice_source,
                "sort_order": metadata_field.sort_order,
                "is_required": metadata_field.is_required,
                "is_active": metadata_field.is_active,
            },
        )

    return render(
        request,
        "documents/settings_metadata_field_form.html",
        {
            "tenant": tenant,
            "document_space": document_space,
            "metadata_field": metadata_field,
            "form": form,
            "form_title": "Metadatenfeld bearbeiten",
            "submit_label": "Feld speichern",
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_overview(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_administer_tenant(request.user, tenant):
        raise PermissionDenied

    return render(
        request,
        "documents/settings_overview.html",
        {
            "tenant": tenant,
            "active_settings_section": "overview",
        },
    )


def tenant_settings_members(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_members(request.user, tenant):
        raise PermissionDenied

    memberships = (
        TenantMembership.objects.select_related("user", "tenant", "role")
        .prefetch_related("roles")
        .filter(tenant=tenant)
        .order_by("user__username")
    )

    return render(
        request,
        "documents/settings_members.html",
        {
            "tenant": tenant,
            "memberships": memberships,
            "active_settings_section": "members",
        },
    )


def tenant_settings_member_create(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_members(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = TenantMembershipCreateForm(request.POST, tenant=tenant)
        if form.is_valid():
            AddTenantMember(
                tenant=tenant,
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
                roles=list(form.cleaned_data["roles"]),
                actor=request.user,
            ).execute()
            messages.success(request, "Benutzer wurde hinzugefügt.")
            return redirect("documents:settings_members", tenant_slug=tenant.slug)
    else:
        form = TenantMembershipCreateForm(tenant=tenant)

    return render(
        request,
        "documents/settings_member_form.html",
        {
            "tenant": tenant,
            "form": form,
            "form_title": "Benutzer hinzufügen",
            "submit_label": "Benutzer hinzufügen",
            "active_settings_section": "members",
        },
    )


def tenant_settings_member_edit(
    request: HttpRequest,
    tenant_slug: str,
    membership_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_members(request.user, tenant):
        raise PermissionDenied

    membership = get_object_or_404(
        TenantMembership.objects.select_related("tenant", "user", "role")
        .prefetch_related("roles"),
        id=membership_id,
        tenant=tenant,
    )
    if request.method == "POST":
        form = TenantMembershipUpdateForm(request.POST, tenant=tenant)
        if form.is_valid():
            UpdateTenantMembership(
                membership=membership,
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
                roles=list(form.cleaned_data["roles"]),
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Benutzer wurde aktualisiert.")
            return redirect("documents:settings_members", tenant_slug=tenant.slug)
    else:
        form = TenantMembershipUpdateForm(
            tenant=tenant,
            initial={
                "email": membership.user.email,
                "roles": membership.roles.all(),
                "is_active": membership.is_active,
            },
        )

    return render(
        request,
        "documents/settings_member_form.html",
        {
            "tenant": tenant,
            "membership": membership,
            "form": form,
            "form_title": "Benutzer bearbeiten",
            "submit_label": "Benutzer speichern",
            "active_settings_section": "members",
        },
    )


def tenant_settings_roles(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_roles(request.user, tenant):
        raise PermissionDenied

    roles = (
        TenantRole.objects.prefetch_related("permissions", "document_spaces")
        .filter(tenant=tenant)
        .order_by("name")
    )
    return render(
        request,
        "documents/settings_roles.html",
        {
            "tenant": tenant,
            "roles": roles,
            "active_settings_section": "roles",
        },
    )


def tenant_settings_role_create(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_roles(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = TenantRoleCreateForm(request.POST, tenant=tenant)
        if form.is_valid():
            CreateTenantRole(
                tenant=tenant,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                description=form.cleaned_data["description"],
                permissions=list(form.cleaned_data["permissions"]),
                document_spaces=list(form.cleaned_data["document_spaces"]),
                can_access_all_document_spaces=form.cleaned_data[
                    "can_access_all_document_spaces"
                ],
                actor=request.user,
            ).execute()
            messages.success(request, "Rolle wurde erstellt.")
            return redirect("documents:settings_roles", tenant_slug=tenant.slug)
    else:
        form = TenantRoleCreateForm(tenant=tenant)

    return render(
        request,
        "documents/settings_role_form.html",
        {
            "tenant": tenant,
            "form": form,
            "form_title": "Rolle erstellen",
            "submit_label": "Rolle erstellen",
            "active_settings_section": "roles",
        },
    )


def tenant_settings_role_edit(
    request: HttpRequest,
    tenant_slug: str,
    role_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_roles(request.user, tenant):
        raise PermissionDenied

    role = get_object_or_404(
        TenantRole.objects.prefetch_related("permissions", "document_spaces"),
        id=role_id,
        tenant=tenant,
    )
    if request.method == "POST":
        form = TenantRoleUpdateForm(request.POST, tenant=tenant)
        if form.is_valid():
            UpdateTenantRole(
                role=role,
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                permissions=list(form.cleaned_data["permissions"]),
                document_spaces=list(form.cleaned_data["document_spaces"]),
                can_access_all_document_spaces=form.cleaned_data[
                    "can_access_all_document_spaces"
                ],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Rolle wurde aktualisiert.")
            return redirect("documents:settings_roles", tenant_slug=tenant.slug)
    else:
        form = TenantRoleUpdateForm(
            initial={
                "name": role.name,
                "description": role.description,
                "permissions": role.permissions.all(),
                "can_access_all_document_spaces": (
                    role.can_access_all_document_spaces
                ),
                "document_spaces": role.document_spaces.all(),
                "is_active": role.is_active,
            },
            tenant=tenant,
        )

    return render(
        request,
        "documents/settings_role_form.html",
        {
            "tenant": tenant,
            "role": role,
            "form": form,
            "form_title": "Rolle bearbeiten",
            "submit_label": "Rolle speichern",
            "active_settings_section": "roles",
        },
    )
