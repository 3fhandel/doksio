from __future__ import annotations

import json
import logging
import re
import shlex
import uuid
from urllib.parse import quote, urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.core.mail import get_connection
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin

from doksio.accounts.forms import (
    TenantMembershipCreateForm,
    TenantMembershipUpdateForm,
    TenantRoleCreateForm,
    TenantRoleUpdateForm,
)
from doksio.accounts.models import TenantMembership, TenantRole, UserProfile
from doksio.accounts.permissions import TenantPermissions
from doksio.accounts.services import (
    AddTenantMember,
    CreateTenantRole,
    DeleteTenantRole,
    SendTenantPasswordResetEmail,
    UpdateTenantMembership,
    UpdateTenantRole,
)
from doksio.audit.models import AuditEvent
from doksio.audit.services import RecordAuditEvent
from doksio.documents.forms import (
    DocumentBoxScanOptimizationForm,
    DocumentBoxTitleRefreshForm,
    DocumentCommentForm,
    DocumentCoreMetadataForm,
    DocumentDeleteForm,
    DocumentImportBatchItemForm,
    DocumentImportBatchUploadForm,
    DocumentMetadataFieldForm,
    DocumentMetadataForm,
    DocumentRelationForm,
    DocumentShareAttachmentForm,
    DocumentSpaceDeleteForm,
    DocumentSpaceEmptyForm,
    DocumentSpaceForm,
    DocumentSpaceUpdateForm,
    DocumentSplitForm,
    DocumentTagForm,
    DocumentTitleRuleForm,
    DocumentUploadForm,
)
from doksio.documents.mentions import mention_suggestions_for_tenant
from doksio.documents.metadata import effective_metadata_fields
from doksio.documents.models import (
    Document,
    DocumentBoxScanOptimizationJob,
    DocumentBoxTitleRefreshJob,
    DocumentFile,
    DocumentImportBatch,
    DocumentImportBatchItem,
    DocumentMetadataField,
    DocumentRelation,
    DocumentSpace,
    DocumentTitleRule,
)
from doksio.documents.policies import (
    can_administer_tenant,
    can_batch_import_documents,
    can_delete_document,
    can_download_document_file,
    can_manage_document_spaces,
    can_manage_members,
    can_manage_roles,
    can_split_document,
    can_upload_document,
    can_view_audit,
    can_view_document,
    filter_document_spaces_for_user,
    filter_documents_for_user,
)
from doksio.documents.services import (
    AddDocumentComment,
    AddDocumentMetadataChoice,
    AddDocumentRelation,
    CreateDocumentFromUpload,
    CreateDocumentImportBatch,
    CreateDocumentMetadataField,
    CreateDocumentSpace,
    DeleteDocument,
    DeleteDocumentSpace,
    DiscardDocumentImportBatch,
    DocumentSplitPart,
    DuplicateDocumentError,
    EmptyDocumentSpace,
    FinalizeDocumentImportBatch,
    RemoveDocumentRelation,
    SetDocumentTags,
    SplitPdfDocument,
    UpdateDocumentCoreMetadata,
    UpdateDocumentMetadata,
    UpdateDocumentMetadataField,
    UpdateDocumentSpace,
    pdf_page_count,
)
from doksio.documents.title_rules import (
    EINVOICE_TITLE_PLACEHOLDERS,
    INVOICE_OCR_TITLE_PLACEHOLDERS,
    title_from_einvoice_data,
    title_from_invoice_ocr_text,
)
from doksio.ingestion.forms import (
    ImportSourceForm,
    TenantSmtpSettingsForm,
    TenantSmtpTestForm,
)
from doksio.ingestion.models import (
    EmailAutoReplyRecipient,
    ImportJob,
    ImportSource,
    TenantSmtpSettings,
)
from doksio.ingestion.services import ResolveManualUploadDocumentSpace
from doksio.ocr.services import StartOcrForDocumentFile, title_from_ocr_policy
from doksio.pagination import paginate_queryset
from doksio.project.email import (
    BrandedEmailMultiAlternatives as EmailMultiAlternatives,
)
from doksio.project.email import (
    attach_branded_html,
)
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

logger = logging.getLogger(__name__)

DOCUMENT_LOG_EVENT_LABELS = {
    "document.created": "Dokument erstellt",
    "document_file.stored": "Datei gespeichert",
    "document_core_metadata.updated": "Kerndaten aktualisiert",
    "document_metadata.updated": "Metadaten aktualisiert",
    "document_comment.created": "Kommentar hinzugefügt",
    "document_space.deleted": "Dokumentenbox gelöscht",
    "document_tags.updated": "Tags aktualisiert",
    "document.deleted": "Dokument gelöscht",
    "document.shared": "Dokument geteilt",
    "document.exported": "Dokument exportiert",
    "document.email_received": "Per E-Mail empfangen",
    "document_import_batch.created": "Stapelimport angelegt",
    "document_import_batch.discarded": "Stapelimport verworfen",
    "document_import_batch.finalized": "Stapelimport abgeschlossen",
    "document_box.scan_optimization.completed": "Scan-Optimierung abgeschlossen",
    "document_file.scan_optimized": "Scan-PDF optimiert",
    "export_run.created": "Exportlauf erzeugt",
    "export_run.downloaded": "Export heruntergeladen",
    "workflow_instance.started": "Workflow gestartet",
    "workflow_instance.cancelled": "Workflow abgebrochen",
    "workflow_task.completed": "Workflow-Schritt erledigt",
}

PDF_PREVIEW_CONTENT_TYPES = {"application/pdf"}
BROWSER_IMAGE_PREVIEW_CONTENT_TYPES = {
    "image/avif",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def _with_workflow_counts(documents):
    return documents.annotate(
        comment_count=Count("comments", distinct=True),
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


def _metadata_value_is_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


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
RUN_MODE={_shell_quote(folder.get("run_mode", "service"))}
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
  printf '%s [%s] %s\\n' \\
    "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$*" | tee -a "$LOG_FILE"
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

handle_imported_file() {{
  local file="$1"
  local filename="$2"
  local duplicate="${{3:-0}}"

  if [[ "$duplicate" == "1" ]]; then
    if [[ -n "$ARCHIVE_DIR" ]]; then
      safe_move "$file" "$ARCHIVE_DIR" "$filename"
    else
      rm -f "$file"
      log INFO "Dublette aus Quellordner gelöscht: $file"
    fi
    return 0
  fi

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
}}

path_in_dir() {{
  local file="$1"
  local dir="$2"

  [[ -z "$dir" ]] && return 1
  [[ "$file" == "$dir"/* ]] && return 0
  return 1
}}

should_skip_file() {{
  local file="$1"

  if [[ "$file" == "$LOG_FILE" ]]; then
    return 0
  fi
  if path_in_dir "$file" "$ARCHIVE_DIR"; then
    return 0
  fi
  if path_in_dir "$file" "$ERROR_DIR"; then
    return 0
  fi
  return 1
}}

process_file() {{
  local file="$1"
  local filename
  filename="$(basename "$file")"
  log INFO "Import startet: $file"

  local response_file
  response_file="$(mktemp)"
  local http_status
  if http_status="$(curl --silent --show-error \\
    --output "$response_file" \\
    --write-out "%{{http_code}}" \\
    --request PUT \\
    --header "X-Doksio-Import-Token: $IMPORT_TOKEN" \\
    --header "X-Doksio-Filename: $filename" \\
    --header "Content-Type: application/octet-stream" \\
    --data-binary "@$file" \\
    "$API_URL")"; then
    if [[ "$http_status" =~ ^2 ]]; then
      log INFO "Import erfolgreich: $file $(tr -d '\\n' < "$response_file")"
      handle_imported_file "$file" "$filename" "0"
    elif [[ "$http_status" == "409" ]]; then
      log INFO "Dublette erkannt: $file $(tr -d '\\n' < "$response_file")"
      handle_imported_file "$file" "$filename" "1"
    else
      log ERROR \\
        "Import fehlgeschlagen ($http_status): $file $(tr -d '\\n' < "$response_file")"
      if [[ -n "$ERROR_DIR" ]]; then
        safe_move "$file" "$ERROR_DIR" "$filename"
      fi
    fi
  else
    log ERROR "Import fehlgeschlagen: $file $(tr -d '\\n' < "$response_file")"
    if [[ -n "$ERROR_DIR" ]]; then
      safe_move "$file" "$ERROR_DIR" "$filename"
    fi
  fi
  rm -f "$response_file"
}}

process_pending_files() {{
  if [[ "$RECURSIVE" == "1" ]]; then
    while IFS= read -r -d '' file; do
      should_skip_file "$file" && continue
      process_file "$file"
    done < <(find "$SOURCE_DIR" -type f -name "$FILE_PATTERN" -print0)
  else
    while IFS= read -r -d '' file; do
      should_skip_file "$file" && continue
      process_file "$file"
    done < <(
      find "$SOURCE_DIR" -type f -name "$FILE_PATTERN" \\
        ! -path "$SOURCE_DIR/*/*" -print0
    )
  fi
}}

log INFO "Doksio Ordner-Agent gestartet: $SOURCE_DIR"
while true; do
  process_pending_files
  if [[ "$RUN_MODE" == "once" ]]; then
    log INFO "Einmallauf beendet."
    exit 0
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
$RunMode = {_powershell_quote(folder.get("run_mode", "service"))}
$AfterImport = {_powershell_quote(folder.get("after_import", "archive"))}
$ArchiveDir = {_powershell_quote(folder.get("archive_path", ""))}
$ErrorDir = {_powershell_quote(folder.get("error_path", ""))}
$ApiUrl = {_powershell_quote(api_url)}
$ImportToken = {_powershell_quote(import_source.token)}
$LogFile = if ($env:DOKSIO_IMPORT_LOG) {{
    $env:DOKSIO_IMPORT_LOG
}} else {{
    Join-Path $SourceDir "doksio-folder-import.log"
}}

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
        $Timestamp = Get-Date -Format 'yyyyMMddHHmmss'
        $Target = Join-Path $TargetDir "$Stem-$Timestamp$Extension"
    }}

    Move-Item -LiteralPath $Source -Destination $Target -Force
    Write-DoksioLog "INFO" "Datei verschoben: $Source -> $Target"
}}

function Complete-DoksioImportedFile {{
    param(
        [System.IO.FileInfo] $File,
        [string] $Filename,
        [bool] $Duplicate = $false
    )

    if ($Duplicate) {{
        if ($ArchiveDir) {{
            Move-DoksioFile `
                -Source $File.FullName `
                -TargetDir $ArchiveDir `
                -Filename $Filename
        }} else {{
            Remove-Item -LiteralPath $File.FullName -Force
            Write-DoksioLog `
                "INFO" `
                "Dublette aus Quellordner gelöscht: $($File.FullName)"
        }}
        return
    }}

    switch ($AfterImport) {{
        "archive" {{
            Move-DoksioFile `
                -Source $File.FullName `
                -TargetDir $ArchiveDir `
                -Filename $Filename
        }}
        "delete" {{
            Remove-Item -LiteralPath $File.FullName -Force
            Write-DoksioLog "INFO" "Datei gelöscht: $($File.FullName)"
        }}
        "keep" {{
            Write-DoksioLog `
                "INFO" `
                "Datei bleibt im Quellordner: $($File.FullName)"
        }}
    }}
}}

function Test-DoksioPathInDirectory {{
    param([string] $Path, [string] $Directory)
    if (-not $Directory) {{ return $false }}

    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $TrimChars = @(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $FullDirectory = [System.IO.Path]::GetFullPath($Directory).TrimEnd($TrimChars)
    return $FullPath.StartsWith(
        $FullDirectory + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}}

function Test-DoksioSkippedFile {{
    param([System.IO.FileInfo] $File)

    $FullFileName = [System.IO.Path]::GetFullPath($File.FullName)
    $FullLogFile = [System.IO.Path]::GetFullPath($LogFile)
    if ($FullFileName -eq $FullLogFile) {{
        return $true
    }}
    if (Test-DoksioPathInDirectory -Path $File.FullName -Directory $ArchiveDir) {{
        return $true
    }}
    if (Test-DoksioPathInDirectory -Path $File.FullName -Directory $ErrorDir) {{
        return $true
    }}
    return $false
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

        Write-DoksioLog `
            "INFO" `
            "Import erfolgreich: $($File.FullName) $($Response.Content)"
        Complete-DoksioImportedFile -File $File -Filename $Filename
    }} catch {{
        $StatusCode = $null
        if ($_.Exception.Response) {{
            $StatusCode = [int] $_.Exception.Response.StatusCode
        }}
        if ($StatusCode -eq 409) {{
            Write-DoksioLog `
                "INFO" `
                "Dublette erkannt: $($File.FullName) $($_.Exception.Message)"
            Complete-DoksioImportedFile -File $File -Filename $Filename -Duplicate $true
            return
        }}

        Write-DoksioLog `
            "ERROR" `
            "Import fehlgeschlagen: $($File.FullName) $($_.Exception.Message)"
        if ($ErrorDir) {{
            Move-DoksioFile `
                -Source $File.FullName `
                -TargetDir $ErrorDir `
                -Filename $Filename
        }}
    }}
}}

function Invoke-DoksioPendingFiles {{
    $Files = Get-ChildItem `
        -LiteralPath $SourceDir `
        -File `
        -Filter $FilePattern `
        -Recurse:$Recursive
    foreach ($File in $Files) {{
        if (Test-DoksioSkippedFile -File $File) {{ continue }}
        Invoke-DoksioImport -File $File
    }}
}}

Write-DoksioLog "INFO" "Doksio Ordner-Agent gestartet: $SourceDir"
while ($true) {{
    Invoke-DoksioPendingFiles
    if ($RunMode -eq "once") {{
        Write-DoksioLog "INFO" "Einmallauf beendet."
        break
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
    entries = []
    for event in events:
        display_at = event.created_at
        if event.event_type == "document.email_received":
            received_at = parse_datetime(event.data.get("received_at", ""))
            if received_at is not None:
                display_at = received_at
        entries.append(
            {
                "event": event,
                "display_at": display_at,
                "label": DOCUMENT_LOG_EVENT_LABELS.get(
                    event.event_type,
                    event.event_type,
                ),
            }
        )
    return entries


AUDIT_EVENT_LABELS = DOCUMENT_LOG_EVENT_LABELS | {
    "export_run.created": "Exportlauf erzeugt",
    "document_title_rule.created": "Titelfindungsregel erstellt",
    "document_title_rule.updated": "Titelfindungsregel aktualisiert",
    "document_title_rule.deleted": "Titelfindungsregel gelöscht",
    "document_box.title_refresh.started": "Titelneuberechnung gestartet",
    "document_box.title_refresh.resumed": "Titelneuberechnung fortgesetzt",
    "document_box.title_refresh.resume_requested": (
        "Fortsetzung der Titelneuberechnung angefordert"
    ),
    "document_box.title_refresh.completed": "Titelneuberechnung abgeschlossen",
    "document_box.title_refresh.failed": "Titelneuberechnung fehlgeschlagen",
}


def _document_preview(document: Document) -> tuple[DocumentFile | None, str]:
    pdf_file = (
        document.files.filter(
            file_kind=DocumentFile.Kind.ORIGINAL,
            content_type__in=PDF_PREVIEW_CONTENT_TYPES,
        )
        .order_by("-version", "-created_at")
        .first()
    )
    if pdf_file is not None:
        return pdf_file, "pdf"

    image_file = (
        document.files.filter(
            file_kind=DocumentFile.Kind.ORIGINAL,
            content_type__in=BROWSER_IMAGE_PREVIEW_CONTENT_TYPES,
        )
        .order_by("-version", "-created_at")
        .first()
    )
    if image_file is not None:
        return image_file, "image"

    image_preview_file = (
        document.files.filter(
            file_kind=DocumentFile.Kind.PREVIEW,
            content_type__startswith="image/",
        )
        .order_by("-version", "-created_at")
        .first()
    )
    if image_preview_file is not None:
        return image_preview_file, "image"
    return None, ""


def _document_original_file(document: Document) -> DocumentFile | None:
    return (
        document.files.filter(file_kind=DocumentFile.Kind.ORIGINAL)
        .order_by("-version", "-created_at", "-id")
        .first()
    )


def _viewer_rotation(document_file: DocumentFile | None) -> int:
    if document_file is None:
        return 0
    try:
        rotation = int((document_file.viewer_settings or {}).get("rotation", 0))
    except (TypeError, ValueError):
        return 0
    if rotation not in {0, 90, 180, 270}:
        return 0
    return rotation


def _document_relations_for_display(document: Document, user) -> list[dict]:
    relations = (
        DocumentRelation.objects.select_related(
            "first_document",
            "first_document__space",
            "second_document",
            "second_document__space",
            "created_by",
        )
        .prefetch_related("first_document__files", "second_document__files")
        .filter(
            Q(first_document=document) | Q(second_document=document),
            tenant=document.tenant,
        )
        .order_by("-created_at", "-id")
    )
    visible_relations = []
    for relation in relations:
        related_document = relation.other_document(document)
        if can_view_document(user, related_document):
            visible_relations.append(
                {
                    "relation": relation,
                    "document": related_document,
                }
            )
    return visible_relations


def _related_document_count_for_step(document: Document, step) -> int:
    allowed_space_ids = set(
        step.required_related_document_spaces.values_list("id", flat=True)
    )
    count = 0
    relations = (
        DocumentRelation.objects.select_related("first_document", "second_document")
        .filter(
            Q(first_document=document) | Q(second_document=document),
            tenant=document.tenant,
        )
        .order_by("-created_at", "-id")
    )
    for relation in relations:
        related_document = relation.other_document(document)
        if related_document.status != Document.Status.ACTIVE:
            continue
        if allowed_space_ids and related_document.space_id not in allowed_space_ids:
            continue
        if (
            step.related_document_requires_completed_workflow
            and not WorkflowInstance.objects.filter(
                document=related_document,
                status=WorkflowInstance.Status.COMPLETED,
            ).exists()
        ):
            continue
        count += 1
    return count


def _smtp_from_email(smtp_settings: TenantSmtpSettings) -> str:
    from_email = smtp_settings.from_email or smtp_settings.username
    if smtp_settings.from_name and from_email:
        return f"{smtp_settings.from_name} <{from_email}>"
    return from_email


def _smtp_connection(smtp_settings: TenantSmtpSettings):
    return get_connection(
        host=smtp_settings.host,
        port=smtp_settings.port,
        username=smtp_settings.username or None,
        password=smtp_settings.password or None,
        use_tls=smtp_settings.security == TenantSmtpSettings.Security.STARTTLS,
        use_ssl=smtp_settings.security == TenantSmtpSettings.Security.SSL,
    )


def _send_document_attachment_email(
    *,
    document: Document,
    document_file: DocumentFile,
    smtp_settings: TenantSmtpSettings,
    recipient: str,
    message: str,
    document_url: str,
    actor,
) -> None:
    body_parts = []
    if message.strip():
        body_parts.append(message.strip())
    body_parts.append(f"Dokument in Doksio: {document_url}")
    body = "\n\n".join(body_parts)

    with default_storage.open(document_file.storage_key, "rb") as stored_file:
        attachment_content = stored_file.read()

    email = EmailMultiAlternatives(
        subject=f"Doksio Dokument: {document.title}",
        body=body,
        from_email=_smtp_from_email(smtp_settings),
        to=[recipient],
        connection=_smtp_connection(smtp_settings),
    )
    attach_branded_html(
        email,
        heading=document.title,
        content=message.strip() or "Ein Dokument wurde über Doksio mit dir geteilt.",
        tenant_name=document.tenant.name,
        action_url=document_url,
        action_label="Dokument in Doksio öffnen",
    )
    email.attach(
        document_file.original_filename,
        attachment_content,
        document_file.content_type,
    )
    email.send()

    RecordAuditEvent(
        tenant=document.tenant,
        actor=actor,
        event_type="document.shared",
        object_type="documents.Document",
        object_id=str(document.id),
        data={
            "document_id": document.id,
            "document_file_id": document_file.id,
            "mode": "email_attachment",
            "recipient": recipient,
        },
    ).execute()


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
        .annotate(document_comment_count=Count("document__comments", distinct=True))
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
        workflow_tasks_queryset.order_by().values("document_id").distinct().count()
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
        workflow_tasks_queryset.order_by().values("document_id").distinct().count()
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
                        (f"{len(imported_documents)} Dokumente wurden gespeichert."),
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


def document_import_batch_list(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    if not can_batch_import_documents(request.user, tenant):
        raise PermissionDenied

    batches = (
        DocumentImportBatch.objects.filter(tenant=tenant)
        .select_related("created_by")
        .annotate(
            items_count=Count("items", distinct=True),
            staged_count=Count(
                "items",
                filter=Q(items__status=DocumentImportBatchItem.Status.STAGED),
                distinct=True,
            ),
            error_count=Count(
                "items",
                filter=Q(items__status=DocumentImportBatchItem.Status.ERROR),
                distinct=True,
            ),
            imported_count=Count(
                "items",
                filter=Q(items__status=DocumentImportBatchItem.Status.IMPORTED),
                distinct=True,
            ),
        )
        .order_by("-created_at", "-id")
    )
    page_obj = paginate_queryset(
        request,
        batches,
        page_param="page",
        per_page=25,
    )
    return render(
        request,
        "documents/document_import_batch_list.html",
        {
            "tenant": tenant,
            "page_obj": page_obj,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_import_batch_upload(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    if not can_batch_import_documents(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = DocumentImportBatchUploadForm(request.POST, request.FILES)
        if form.is_valid():
            batch = CreateDocumentImportBatch(
                tenant=tenant,
                title=form.cleaned_data["title"],
                uploaded_files=form.cleaned_data["file"],
                created_by=request.user,
            ).execute()
            messages.success(
                request,
                (
                    f"{batch.items.count()} Datei"
                    f"{'en' if batch.items.count() != 1 else ''} "
                    "wurden in den Stapel übernommen."
                ),
            )
            return redirect(
                "documents:import_batch_detail",
                tenant_slug=tenant.slug,
                batch_id=batch.id,
            )
    else:
        form = DocumentImportBatchUploadForm()

    return render(
        request,
        "documents/document_import_batch_upload.html",
        {
            "tenant": tenant,
            "form": form,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_import_batch_discard(
    request: HttpRequest,
    tenant_slug: str,
    batch_id: int,
) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    if not can_batch_import_documents(request.user, tenant):
        raise PermissionDenied

    batch = get_object_or_404(DocumentImportBatch, id=batch_id, tenant=tenant)
    try:
        DiscardDocumentImportBatch(batch=batch, actor=request.user).execute()
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(
            "documents:import_batch_detail",
            tenant_slug=tenant.slug,
            batch_id=batch.id,
        )
    messages.success(request, "Stapelimport wurde verworfen.")
    return redirect("documents:import_batch_list", tenant_slug=tenant.slug)


def _batch_item_forms(
    *,
    request: HttpRequest,
    tenant,
    batch: DocumentImportBatch,
) -> list[tuple[DocumentImportBatchItem, DocumentImportBatchItemForm]]:
    forms = []
    for item in batch.items.select_related(
        "suggested_space",
        "target_space",
        "imported_document",
    ):
        initial = {
            "target_space": item.target_space_id or item.suggested_space_id,
            "skip": item.status == DocumentImportBatchItem.Status.SKIPPED,
        }
        forms.append(
            (
                item,
                DocumentImportBatchItemForm(
                    request.POST or None,
                    prefix=f"item-{item.id}",
                    item=item,
                    tenant=tenant,
                    user=request.user,
                    initial=initial,
                ),
            )
        )
    return forms


def _batch_preview_kind(item: DocumentImportBatchItem | None) -> str:
    if item is None:
        return ""
    if item.content_type in PDF_PREVIEW_CONTENT_TYPES:
        return "pdf"
    if item.content_type in BROWSER_IMAGE_PREVIEW_CONTENT_TYPES:
        return "image"
    return ""


def _selected_batch_preview_item(
    request: HttpRequest,
    batch: DocumentImportBatch,
) -> DocumentImportBatchItem | None:
    items = list(
        batch.items.select_related("target_space").filter(
            status__in=[
                DocumentImportBatchItem.Status.STAGED,
                DocumentImportBatchItem.Status.ERROR,
                DocumentImportBatchItem.Status.SKIPPED,
            ],
        )
    )
    if not items:
        return None
    preview_item_id = request.GET.get("preview")
    if preview_item_id:
        for item in items:
            if str(item.id) == preview_item_id:
                return item
    return items[0]


def document_import_batch_detail(
    request: HttpRequest,
    tenant_slug: str,
    batch_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    if not can_batch_import_documents(request.user, tenant):
        raise PermissionDenied

    batch = get_object_or_404(
        DocumentImportBatch.objects.select_related("created_by"),
        id=batch_id,
        tenant=tenant,
    )
    item_forms = _batch_item_forms(request=request, tenant=tenant, batch=batch)
    preview_item = _selected_batch_preview_item(request, batch)

    if request.method == "POST":
        if batch.status != DocumentImportBatch.Status.OPEN:
            messages.error(request, "Dieser Stapelimport ist nicht mehr offen.")
            return redirect(
                "documents:import_batch_detail",
                tenant_slug=tenant.slug,
                batch_id=batch.id,
            )
        action = request.POST.get("action", "save")
        forms_are_valid = all(form.is_valid() for _item, form in item_forms)
        if forms_are_valid:
            for item, form in item_forms:
                if item.status not in {
                    DocumentImportBatchItem.Status.STAGED,
                    DocumentImportBatchItem.Status.ERROR,
                    DocumentImportBatchItem.Status.SKIPPED,
                }:
                    continue
                if form.cleaned_data["skip"]:
                    item.status = DocumentImportBatchItem.Status.SKIPPED
                    item.message = "Manuell übersprungen."
                    item.save(update_fields=["status", "message", "updated_at"])
                    continue
                item.target_space = form.cleaned_data["target_space"]
                if item.status == DocumentImportBatchItem.Status.SKIPPED:
                    item.status = DocumentImportBatchItem.Status.STAGED
                    item.message = ""
                item.save(
                    update_fields=[
                        "target_space",
                        "status",
                        "message",
                        "updated_at",
                    ],
                )

            if action == "finalize":
                counts = FinalizeDocumentImportBatch(
                    batch=batch,
                    actor=request.user,
                ).execute()
                if counts["errors"]:
                    messages.warning(
                        request,
                        (
                            f"{counts['imported']} importiert, "
                            f"{counts['duplicates']} Dubletten, "
                            f"{counts['errors']} Fehler."
                        ),
                    )
                else:
                    messages.success(
                        request,
                        (
                            f"{counts['imported']} Dokumente importiert. "
                            f"{counts['duplicates']} Dubletten übersprungen."
                        ),
                    )
                return redirect(
                    "documents:import_batch_detail",
                    tenant_slug=tenant.slug,
                    batch_id=batch.id,
                )

            messages.success(request, "Stapel wurde aktualisiert.")
            return redirect(
                "documents:import_batch_detail",
                tenant_slug=tenant.slug,
                batch_id=batch.id,
            )

    item_forms = _batch_item_forms(request=request, tenant=tenant, batch=batch)
    preview_item = _selected_batch_preview_item(request, batch)
    status_counts = batch.items.values("status").annotate(total=Count("id"))
    counts_by_status = {row["status"]: row["total"] for row in status_counts}
    return render(
        request,
        "documents/document_import_batch_detail.html",
        {
            "tenant": tenant,
            "batch": batch,
            "item_forms": item_forms,
            "preview_item": preview_item,
            "preview_kind": _batch_preview_kind(preview_item),
            "counts_by_status": counts_by_status,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


@xframe_options_sameorigin
def document_import_batch_item_preview(
    request: HttpRequest,
    tenant_slug: str,
    batch_id: int,
    item_id: int,
) -> FileResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    if not can_batch_import_documents(request.user, tenant):
        raise PermissionDenied

    item = get_object_or_404(
        DocumentImportBatchItem.objects.select_related("batch"),
        id=item_id,
        batch_id=batch_id,
        tenant=tenant,
    )
    if not default_storage.exists(item.source_storage_key):
        raise Http404("Staging-Datei existiert nicht mehr.")

    file_handle = default_storage.open(item.source_storage_key, "rb")
    return FileResponse(
        file_handle,
        as_attachment=False,
        filename=item.original_filename,
        content_type=item.content_type,
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
    relation_form = DocumentRelationForm(document=document, user=request.user)
    share_attachment_form = DocumentShareAttachmentForm()
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
    share_attachment_modal_open = False
    document_share_url = request.build_absolute_uri(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    share_mail_subject = f"Doksio Dokument: {document.title}"
    share_mail_body = f"Link zum Dokument:\n{document_share_url}"
    share_mailto_url = (
        f"mailto:?subject={quote(share_mail_subject)}&body={quote(share_mail_body)}"
    )

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
        elif action == "add_relation":
            relation_form = DocumentRelationForm(
                request.POST,
                document=document,
                user=request.user,
            )
            if relation_form.is_valid():
                AddDocumentRelation(
                    document=document,
                    related_document=relation_form.cleaned_data["target_document_id"],
                    actor=request.user,
                ).execute()
                messages.success(request, "Dokument wurde verknüpft.")
                return redirect(request.get_full_path())
        elif action == "remove_relation":
            relation = get_object_or_404(
                DocumentRelation,
                id=request.POST.get("relation_id"),
                tenant=tenant,
            )
            related_document = relation.other_document(document)
            if document.id not in {
                relation.first_document_id,
                relation.second_document_id,
            } or not can_view_document(request.user, related_document):
                raise PermissionDenied
            RemoveDocumentRelation(relation=relation, actor=request.user).execute()
            messages.success(request, "Dokumentverknüpfung wurde entfernt.")
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
                    ).prefetch_related("step__required_metadata_fields"),
                    id=complete_workflow_task_form.cleaned_data["task_id"],
                    tenant=tenant,
                    document=document,
                    status=WorkflowTask.Status.OPEN,
                )
                if not can_complete_workflow_task(request.user, task):
                    raise PermissionDenied
                try:
                    CompleteWorkflowTask(
                        task=task,
                        actor=request.user,
                        comment=complete_workflow_task_form.cleaned_data["comment"],
                    ).execute()
                except ValueError as error:
                    messages.error(request, str(error))
                else:
                    messages.success(request, "Workflow-Aufgabe wurde erledigt.")
                    return redirect(request.get_full_path())
        elif action == "share_attachment_email":
            share_attachment_form = DocumentShareAttachmentForm(request.POST)
            if share_attachment_form.is_valid():
                document_file = _document_original_file(document)
                if document_file is None:
                    messages.error(request, "Dieses Dokument hat keine Originaldatei.")
                    return redirect(request.get_full_path())
                if not can_download_document_file(request.user, document_file):
                    raise PermissionDenied

                smtp_settings = TenantSmtpSettings.objects.filter(
                    tenant=tenant,
                    is_active=True,
                ).first()
                if smtp_settings is None:
                    messages.error(
                        request,
                        "Für diesen Mandanten sind keine aktiven "
                        "SMTP-Einstellungen hinterlegt.",
                    )
                    return redirect(request.get_full_path())

                _send_document_attachment_email(
                    document=document,
                    document_file=document_file,
                    smtp_settings=smtp_settings,
                    recipient=share_attachment_form.cleaned_data["recipient"],
                    message=share_attachment_form.cleaned_data["message"],
                    document_url=document_share_url,
                    actor=request.user,
                )
                messages.success(request, "Dokument wurde per E-Mail gesendet.")
                return redirect(request.get_full_path())
            share_attachment_modal_open = True

    preview_file, preview_kind = _document_preview(document)
    preview_ocr_job = preview_file.latest_ocr_job if preview_file is not None else None
    preview_rotation = _viewer_rotation(preview_file)
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
    open_workflow_tasks_queryset = (
        document.workflow_tasks.filter(
            status=WorkflowTask.Status.OPEN,
        )
        .select_related("step", "assigned_role", "instance__template")
        .prefetch_related(
            "step__required_metadata_fields",
            "step__required_related_document_spaces",
        )
    )
    open_workflow_tasks = [
        task
        for task in open_workflow_tasks_queryset
        if can_complete_workflow_task(request.user, task)
    ]
    for task in open_workflow_tasks:
        required_metadata_fields = list(task.step.required_metadata_fields.all())
        task.required_metadata_fields_for_completion = required_metadata_fields
        task.missing_metadata_fields_for_completion = [
            field
            for field in required_metadata_fields
            if not _metadata_value_is_filled(document.metadata.get(field.slug))
        ]
        task.related_documents_for_completion_count = _related_document_count_for_step(
            document,
            task.step,
        )
        task.missing_related_documents_for_completion_count = max(
            task.step.min_related_documents
            - task.related_documents_for_completion_count,
            0,
        )
        required_related_space_ids = list(
            task.step.required_related_document_spaces.values_list("id", flat=True)
        )
        task.relation_picker_default_space_id = (
            task.step.relation_picker_default_document_space_id
            or (required_related_space_ids[0] if required_related_space_ids else "")
        )
        task.relation_picker_default_include_children = (
            task.step.relation_picker_default_include_child_spaces
        )
        task.relation_picker_default_workflow_status = (
            task.step.relation_picker_default_workflow_status
        )
        task.relation_picker_filters_editable = (
            task.step.relation_picker_filters_editable
        )
    workflow_templates_available = WorkflowTemplate.objects.filter(
        tenant=tenant,
        is_active=True,
        trigger_type=WorkflowTemplate.TriggerType.MANUAL,
    ).exists()
    comments = list(document.comments.all())
    relation_picker_spaces = filter_document_spaces_for_user(
        DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
            deleted_at__isnull=True,
        ),
        request.user,
        tenant,
        TenantPermissions.DOCUMENTS_VIEW,
    ).order_by("path")
    share_attachment_file = _document_original_file(document)
    share_can_open_mail_client = (
        share_attachment_file is not None
        and can_download_document_file(request.user, share_attachment_file)
    )
    share_attachment_download_url = ""
    if share_can_open_mail_client and share_attachment_file is not None:
        share_attachment_download_url = (
            reverse(
                "documents:download",
                kwargs={
                    "tenant_slug": tenant.slug,
                    "file_id": share_attachment_file.id,
                },
            )
            + "?inline=1"
        )
    share_can_send_attachment = (
        share_attachment_file is not None
        and can_download_document_file(request.user, share_attachment_file)
        and TenantSmtpSettings.objects.filter(tenant=tenant, is_active=True).exists()
    )
    document_can_split = (
        share_attachment_file is not None
        and share_attachment_file.content_type == "application/pdf"
        and can_split_document(request.user, document)
    )

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
            "preview_rotation": preview_rotation,
            "comment_form": comment_form,
            "metadata_form": metadata_form,
            "relation_form": relation_form,
            "document_relations": _document_relations_for_display(
                document,
                request.user,
            ),
            "relation_picker_spaces": relation_picker_spaces,
            "share_attachment_form": share_attachment_form,
            "share_attachment_file": share_attachment_file,
            "share_attachment_download_url": share_attachment_download_url,
            "share_can_open_mail_client": share_can_open_mail_client,
            "share_can_send_attachment": share_can_send_attachment,
            "share_attachment_modal_open": share_attachment_modal_open,
            "document_can_split": document_can_split,
            "document_share_url": document_share_url,
            "share_mailto_url": share_mailto_url,
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


def document_split(
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
        Document.objects.select_related("space").prefetch_related("files"),
        id=document_id,
        tenant=tenant,
        status=Document.Status.ACTIVE,
    )
    if not can_split_document(request.user, document):
        raise PermissionDenied

    source_file = _document_original_file(document)
    if source_file is None or source_file.content_type != "application/pdf":
        raise Http404("Dieses Dokument kann nicht aufgeteilt werden.")

    page_count = pdf_page_count(source_file)
    if page_count < 2:
        messages.warning(request, "Dieses PDF enthält nicht genug Seiten.")
        return redirect(
            "documents:detail",
            tenant_slug=tenant.slug,
            document_id=document.id,
        )

    target_spaces = filter_document_spaces_for_user(
        DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
            deleted_at__isnull=True,
        ),
        request.user,
        tenant,
        TenantPermissions.DOCUMENTS_UPLOAD,
    ).order_by("path")
    if not target_spaces.exists():
        raise PermissionDenied

    detail_url = _document_detail_context_url(
        tenant_slug=tenant.slug,
        document_id=document.id,
        back_url=_safe_return_url(
            request,
            reverse("documents:list", kwargs={"tenant_slug": tenant.slug}),
            value=request.GET.get("back"),
        ),
        nav_param=request.GET.get("nav", ""),
    )
    form = DocumentSplitForm(
        request.POST or None,
        tenant=tenant,
        user=request.user,
        page_count=page_count,
        initial={"original_handling": "keep"},
    )
    if request.method == "POST" and form.is_valid():
        parts = [
            DocumentSplitPart(
                start_page=item["start_page"],
                end_page=item["end_page"],
                target_space=item["target_space"],
                title=item["title"],
            )
            for item in form.cleaned_data["split_payload"]
        ]
        created_documents = SplitPdfDocument(
            document=document,
            source_file=source_file,
            parts=parts,
            keep_original=form.cleaned_data["original_handling"] == "keep",
            actor=request.user,
        ).execute()
        messages.success(
            request,
            f"{len(created_documents)} Teildokumente wurden erstellt.",
        )
        return redirect(
            "documents:detail",
            tenant_slug=tenant.slug,
            document_id=created_documents[0].id,
        )

    return render(
        request,
        "documents/document_split.html",
        {
            "tenant": tenant,
            "document": document,
            "source_file": source_file,
            "page_count": page_count,
            "form": form,
            "target_spaces": target_spaces,
            "detail_url": detail_url,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_relation_picker_search(
    request: HttpRequest,
    tenant_slug: str,
    document_id: int,
) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

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

    query = request.GET.get("q", "").strip()
    space_id = request.GET.get("space", "").strip()
    include_children = request.GET.get("include_children", "1") == "1"
    workflow_status = request.GET.get("workflow_status", "any").strip()
    documents = _with_workflow_counts(
        filter_documents_for_user(
            Document.objects.select_related("space")
            .prefetch_related("files")
            .filter(tenant=tenant),
            request.user,
            tenant,
        ).exclude(id=document.id)
    )
    if space_id:
        selected_space = get_object_or_404(
            DocumentSpace,
            id=space_id,
            tenant=tenant,
            deleted_at__isnull=True,
        )
        if include_children:
            documents = documents.filter(
                Q(space=selected_space)
                | Q(space__path__startswith=f"{selected_space.path.rstrip('/')}/")
            )
        else:
            documents = documents.filter(space=selected_space)
    if workflow_status == "open":
        documents = documents.filter(workflow_open_count__gt=0)
    elif workflow_status == "completed":
        documents = documents.filter(
            workflow_total_count__gt=0,
            workflow_open_count=0,
            workflow_completed_count=F("workflow_total_count"),
        )
    elif workflow_status == "none":
        documents = documents.filter(workflow_total_count=0)
    if query:
        documents = documents.filter(
            Q(title__icontains=query)
            | Q(space__path__icontains=query)
            | Q(id=int(query) if query.isdigit() else 0)
        )

    def thumbnail_url(candidate: Document) -> str:
        thumbnail = next(
            (
                document_file
                for document_file in candidate.files.all()
                if document_file.file_kind == DocumentFile.Kind.THUMBNAIL
            ),
            None,
        )
        if thumbnail is None:
            return ""
        return (
            reverse(
                "documents:download",
                kwargs={"tenant_slug": tenant.slug, "file_id": thumbnail.id},
            )
            + "?inline=1"
        )

    results = [
        {
            "id": candidate.id,
            "title": candidate.title,
            "space": candidate.space.path,
            "document_date": (
                candidate.document_date.isoformat() if candidate.document_date else ""
            ),
            "created_at": timezone.localtime(candidate.created_at).strftime(
                "%d.%m.%Y %H:%M"
            ),
            "thumbnail_url": thumbnail_url(candidate),
            "workflow_open_count": candidate.workflow_open_count,
            "workflow_total_count": candidate.workflow_total_count,
        }
        for candidate in documents.order_by("-created_at", "-id")[:12]
    ]
    return JsonResponse({"results": results})


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


def document_file_viewer_settings(
    request: HttpRequest,
    tenant_slug: str,
    file_id: int,
) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=403)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    document_file = get_object_or_404(
        DocumentFile.objects.select_related("document", "document__space"),
        id=file_id,
        tenant=tenant,
    )
    if not can_view_document(request.user, document_file.document):
        raise PermissionDenied

    try:
        payload = json.loads(request.body.decode() or "{}")
        rotation = int(payload.get("rotation", 0))
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_payload"}, status=400)

    if rotation % 90 != 0:
        return JsonResponse({"error": "invalid_rotation"}, status=400)

    rotation = rotation % 360
    viewer_settings = dict(document_file.viewer_settings or {})
    viewer_settings["rotation"] = rotation
    document_file.viewer_settings = viewer_settings
    document_file.save(update_fields=["viewer_settings"])
    return JsonResponse({"ok": True, "rotation": rotation})


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


def tenant_settings_document_box_empty(
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
    document_count = Document.objects.filter(
        tenant=tenant,
        space=document_space,
    ).count()

    if request.method == "POST":
        form = DocumentSpaceEmptyForm(
            request.POST,
            document_space=document_space,
        )
        if form.is_valid():
            deleted_count = EmptyDocumentSpace(
                document_space=document_space,
                actor=request.user,
            ).execute()
            message = (
                "Dokumentenbox wurde geleert. "
                f"{deleted_count} Dokumente wurden hart gelöscht."
            )
            messages.success(
                request,
                message,
            )
            return redirect(
                "documents:settings_document_box_edit",
                tenant_slug=tenant.slug,
                box_id=document_space.id,
            )
    else:
        form = DocumentSpaceEmptyForm(document_space=document_space)

    return render(
        request,
        "documents/settings_document_box_empty.html",
        {
            "tenant": tenant,
            "document_space": document_space,
            "document_count": document_count,
            "form": form,
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
    test_form = TenantSmtpTestForm()
    if request.method == "POST":
        if request.POST.get("action") == "send_test":
            form = TenantSmtpSettingsForm(
                initial=TenantSmtpSettingsForm.initial_from_settings(smtp_settings),
            )
            test_form = TenantSmtpTestForm(request.POST)
            if test_form.is_valid():
                if smtp_settings is None or not smtp_settings.is_active:
                    messages.error(
                        request,
                        (
                            "Für diesen Mandanten ist kein aktiver "
                            "SMTP-Versand konfiguriert."
                        ),
                    )
                else:
                    recipient = test_form.cleaned_data["recipient"]
                    try:
                        test_message = EmailMultiAlternatives(
                            subject="Doksio SMTP-Test",
                            body=(
                                "Diese Testmail wurde aus den SMTP-Einstellungen "
                                f"des Mandanten {tenant.name} versendet."
                            ),
                            from_email=_smtp_from_email(smtp_settings),
                            to=[recipient],
                            connection=_smtp_connection(smtp_settings),
                        )
                        attach_branded_html(
                            test_message,
                            heading="SMTP-Verbindung erfolgreich",
                            content=(
                                "Diese Testmail wurde aus den SMTP-Einstellungen "
                                "deines Doksio-Mandanten versendet."
                            ),
                            tenant_name=tenant.name,
                        )
                        test_message.send(fail_silently=False)
                    except Exception as exc:
                        RecordAuditEvent(
                            tenant=tenant,
                            actor=request.user,
                            event_type="smtp.test_failed",
                            object_type="tenant_smtp_settings",
                            object_id=str(smtp_settings.id),
                            data={"recipient": recipient, "error": str(exc)},
                        ).execute()
                        messages.error(
                            request,
                            f"Testmail konnte nicht gesendet werden: {exc}",
                        )
                    else:
                        RecordAuditEvent(
                            tenant=tenant,
                            actor=request.user,
                            event_type="smtp.test_sent",
                            object_type="tenant_smtp_settings",
                            object_id=str(smtp_settings.id),
                            data={"recipient": recipient},
                        ).execute()
                        messages.success(
                            request,
                            f"Testmail wurde an {recipient} gesendet.",
                        )
                        return redirect(
                            "documents:settings_smtp",
                            tenant_slug=tenant.slug,
                        )
        else:
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
            "test_form": test_form,
            "smtp_settings": smtp_settings,
            "active_settings_section": "smtp",
        },
    )


def tenant_settings_maintenance(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = DocumentBoxScanOptimizationForm(request.POST, tenant=tenant)
        if form.is_valid():
            from doksio.documents.services import CreateDocumentBoxScanOptimizationJob
            from doksio.documents.tasks import (
                process_document_box_scan_optimization_job,
            )

            document_space = form.cleaned_data["space"]
            job = CreateDocumentBoxScanOptimizationJob(
                tenant=tenant,
                document_space=document_space,
                include_children=form.cleaned_data["include_children"],
                actor=request.user,
            ).execute()
            process_document_box_scan_optimization_job.delay(job.id)
            messages.success(
                request,
                (
                    "Scan-Optimierung wurde gestartet. "
                    "Der Fortschritt ist hier sichtbar."
                ),
            )
            return redirect("documents:settings_maintenance", tenant_slug=tenant.slug)
    else:
        form = DocumentBoxScanOptimizationForm(tenant=tenant)

    return render(
        request,
        "documents/settings_maintenance.html",
        {
            "tenant": tenant,
            "form": form,
            "scan_optimization_jobs": (
                tenant.document_box_scan_optimization_jobs.select_related(
                    "document_space",
                    "created_by",
                )[:8]
            ),
            "maintenance_section": "scan_storage",
            "active_settings_section": "maintenance",
        },
    )


def tenant_settings_scan_optimization_resume(
    request: HttpRequest,
    tenant_slug: str,
    job_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    job = get_object_or_404(
        DocumentBoxScanOptimizationJob.objects.select_related("document_space"),
        id=job_id,
        tenant=tenant,
    )
    if job.is_resumable:
        from doksio.documents.services import ClaimDocumentBoxScanOptimizationJob
        from doksio.documents.tasks import (
            process_document_box_scan_optimization_job,
        )

        lease_token = uuid.uuid4()
        try:
            claimed_job = ClaimDocumentBoxScanOptimizationJob(
                job_id=job.id,
                lease_token=lease_token,
                resume_reason="manual",
            ).execute()
            if claimed_job is not None:
                process_document_box_scan_optimization_job.delay(
                    job.id,
                    lease_token_value=str(lease_token),
                )
        except Exception:
            DocumentBoxScanOptimizationJob.objects.filter(
                id=job.id,
                lease_token=lease_token,
            ).update(
                status=job.status,
                started_at=job.started_at,
                heartbeat_at=job.heartbeat_at,
                lease_token=job.lease_token,
                lease_expires_at=job.lease_expires_at,
            )
            logger.exception(
                "Could not resume scan optimization job %s for tenant %s.",
                job.id,
                tenant.slug,
            )
            messages.error(
                request,
                (
                    "Die Scan-Optimierung konnte nicht fortgesetzt werden. "
                    "Bitte Worker- und Redis-Status prüfen."
                ),
            )
            return redirect("documents:settings_maintenance", tenant_slug=tenant.slug)
        if claimed_job is not None:
            RecordAuditEvent(
                tenant=tenant,
                actor=request.user,
                event_type="document_box.scan_optimization.resume_requested",
                object_type="documents.DocumentBoxScanOptimizationJob",
                object_id=str(job.id),
                data={
                    "space_path": job.document_space.path,
                    "processed_documents": job.processed_documents,
                    "total_documents": job.total_documents,
                },
            ).execute()
            messages.success(
                request,
                "Die Scan-Optimierung wird fortgesetzt.",
            )
        else:
            messages.info(
                request,
                "Der Wartungsjob wurde bereits von einem Worker übernommen.",
            )
    elif job.status in {
        DocumentBoxScanOptimizationJob.Status.COMPLETED,
        DocumentBoxScanOptimizationJob.Status.FAILED,
    }:
        messages.info(request, "Dieser Wartungsjob ist bereits beendet.")
    else:
        messages.info(request, "Der Wartungsjob wird derzeit noch verarbeitet.")

    return redirect("documents:settings_maintenance", tenant_slug=tenant.slug)


def tenant_settings_title_refresh(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = DocumentBoxTitleRefreshForm(request.POST, tenant=tenant)
        if form.is_valid():
            from doksio.documents.services import CreateDocumentBoxTitleRefreshJob
            from doksio.documents.tasks import process_document_box_title_refresh_job

            document_space = form.cleaned_data["space"]
            job = CreateDocumentBoxTitleRefreshJob(
                tenant=tenant,
                document_space=document_space,
                include_children=form.cleaned_data["include_children"],
                actor=request.user,
            ).execute()
            process_document_box_title_refresh_job.delay(job.id)
            messages.success(
                request,
                (
                    "Titelneuberechnung wurde gestartet. "
                    "Der Fortschritt ist hier sichtbar."
                ),
            )
            return redirect(
                "documents:settings_title_refresh",
                tenant_slug=tenant.slug,
            )
    else:
        form = DocumentBoxTitleRefreshForm(tenant=tenant)

    return render(
        request,
        "documents/settings_maintenance_titles.html",
        {
            "tenant": tenant,
            "form": form,
            "title_refresh_jobs": (
                tenant.document_box_title_refresh_jobs.select_related(
                    "document_space",
                    "created_by",
                )[:8]
            ),
            "maintenance_section": "titles",
            "active_settings_section": "maintenance",
        },
    )


def tenant_settings_title_refresh_resume(
    request: HttpRequest,
    tenant_slug: str,
    job_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    job = get_object_or_404(
        DocumentBoxTitleRefreshJob.objects.select_related("document_space"),
        id=job_id,
        tenant=tenant,
    )
    if job.is_resumable:
        from doksio.documents.services import ClaimDocumentBoxTitleRefreshJob
        from doksio.documents.tasks import process_document_box_title_refresh_job

        lease_token = uuid.uuid4()
        try:
            claimed_job = ClaimDocumentBoxTitleRefreshJob(
                job_id=job.id,
                lease_token=lease_token,
                resume_reason="manual",
            ).execute()
            if claimed_job is not None:
                process_document_box_title_refresh_job.delay(
                    job.id,
                    lease_token_value=str(lease_token),
                )
        except Exception:
            DocumentBoxTitleRefreshJob.objects.filter(
                id=job.id,
                lease_token=lease_token,
            ).update(
                status=job.status,
                started_at=job.started_at,
                heartbeat_at=job.heartbeat_at,
                lease_token=job.lease_token,
                lease_expires_at=job.lease_expires_at,
            )
            logger.exception(
                "Could not resume title refresh job %s for tenant %s.",
                job.id,
                tenant.slug,
            )
            messages.error(
                request,
                (
                    "Die Titelneuberechnung konnte nicht fortgesetzt werden. "
                    "Bitte Worker- und Redis-Status prüfen."
                ),
            )
            return redirect(
                "documents:settings_title_refresh",
                tenant_slug=tenant.slug,
            )
        if claimed_job is not None:
            RecordAuditEvent(
                tenant=tenant,
                actor=request.user,
                event_type="document_box.title_refresh.resume_requested",
                object_type="documents.DocumentBoxTitleRefreshJob",
                object_id=str(job.id),
                data={
                    "space_path": job.document_space.path,
                    "processed_documents": job.processed_documents,
                    "total_documents": job.total_documents,
                },
            ).execute()
            messages.success(
                request,
                "Die Titelneuberechnung wird fortgesetzt.",
            )
        else:
            messages.info(
                request,
                "Der Wartungsjob wurde bereits von einem Worker übernommen.",
            )
    elif job.status in {
        DocumentBoxTitleRefreshJob.Status.COMPLETED,
        DocumentBoxTitleRefreshJob.Status.FAILED,
    }:
        messages.info(request, "Dieser Wartungsjob ist bereits beendet.")
    else:
        messages.info(request, "Der Wartungsjob wird derzeit noch verarbeitet.")

    return redirect(
        "documents:settings_title_refresh",
        tenant_slug=tenant.slug,
    )


def tenant_settings_title_rules(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    rules = DocumentTitleRule.objects.filter(tenant=tenant).select_related(
        "document_space"
    )
    default_rule = rules.filter(document_space__isnull=True).first()
    box_rules = rules.filter(document_space__isnull=False).order_by(
        "document_space__path",
        "id",
    )
    available_box_count = (
        DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
            deleted_at__isnull=True,
        )
        .exclude(
            id__in=box_rules.values("document_space_id"),
        )
        .count()
    )
    return render(
        request,
        "documents/settings_title_rules.html",
        {
            "tenant": tenant,
            "default_rule": default_rule,
            "box_rules_page_obj": paginate_queryset(
                request,
                box_rules,
                page_param="page",
                per_page=25,
            ),
            "available_box_count": available_box_count,
            "active_settings_section": "title_rules",
        },
    )


def tenant_settings_title_rule_create(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = DocumentTitleRuleForm(request.POST, tenant=tenant)
        if form.is_valid():
            rule = form.save()
            RecordAuditEvent(
                tenant=tenant,
                actor=request.user,
                event_type="document_title_rule.created",
                object_type="documents.DocumentTitleRule",
                object_id=str(rule.id),
                data={
                    "document_space_id": rule.document_space_id,
                    "strategy": rule.strategy,
                    "einvoice_format": rule.einvoice_format,
                    "fallback_strategy": rule.fallback_strategy,
                    "invoice_ocr_format": rule.invoice_ocr_format,
                    "invoice_ocr_fallback_strategy": rule.invoice_ocr_fallback_strategy,
                },
            ).execute()
            messages.success(request, "Regel zur Titelfindung wurde erstellt.")
            return redirect(
                "documents:settings_title_rules",
                tenant_slug=tenant.slug,
            )
    else:
        form = DocumentTitleRuleForm(
            tenant=tenant,
            initial={"strategy": DocumentTitleRule.Strategy.AUTOMATIC},
        )

    return render(
        request,
        "documents/settings_title_rule_form.html",
        {
            "tenant": tenant,
            "form": form,
            "form_title": "Regel erstellen",
            "submit_label": "Regel erstellen",
            "einvoice_title_placeholders": EINVOICE_TITLE_PLACEHOLDERS,
            "invoice_ocr_title_placeholders": INVOICE_OCR_TITLE_PLACEHOLDERS,
            "active_settings_section": "title_rules",
        },
    )


def tenant_settings_title_rule_edit(
    request: HttpRequest,
    tenant_slug: str,
    rule_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    rule = get_object_or_404(
        DocumentTitleRule.objects.select_related("document_space"),
        id=rule_id,
        tenant=tenant,
    )
    if request.method == "POST":
        form = DocumentTitleRuleForm(
            request.POST,
            tenant=tenant,
            instance=rule,
            lock_scope=True,
        )
        if form.is_valid():
            rule = form.save()
            RecordAuditEvent(
                tenant=tenant,
                actor=request.user,
                event_type="document_title_rule.updated",
                object_type="documents.DocumentTitleRule",
                object_id=str(rule.id),
                data={
                    "document_space_id": rule.document_space_id,
                    "strategy": rule.strategy,
                    "einvoice_format": rule.einvoice_format,
                    "fallback_strategy": rule.fallback_strategy,
                    "invoice_ocr_format": rule.invoice_ocr_format,
                    "invoice_ocr_fallback_strategy": rule.invoice_ocr_fallback_strategy,
                },
            ).execute()
            messages.success(request, "Regel zur Titelfindung wurde aktualisiert.")
            return redirect(
                "documents:settings_title_rules",
                tenant_slug=tenant.slug,
            )
    else:
        form = DocumentTitleRuleForm(
            tenant=tenant,
            instance=rule,
            lock_scope=True,
        )

    scope_name = rule.document_space.path if rule.document_space else "Tenant-Standard"
    return render(
        request,
        "documents/settings_title_rule_form.html",
        {
            "tenant": tenant,
            "form": form,
            "rule": rule,
            "form_title": f"Regel bearbeiten: {scope_name}",
            "submit_label": "Regel speichern",
            "einvoice_title_placeholders": EINVOICE_TITLE_PLACEHOLDERS,
            "invoice_ocr_title_placeholders": INVOICE_OCR_TITLE_PLACEHOLDERS,
            "active_settings_section": "title_rules",
        },
    )


def tenant_settings_title_rule_delete(
    request: HttpRequest,
    tenant_slug: str,
    rule_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    rule = get_object_or_404(
        DocumentTitleRule.objects.select_related("document_space"),
        id=rule_id,
        tenant=tenant,
    )
    if request.method == "POST":
        scope_name = (
            rule.document_space.path if rule.document_space else "Tenant-Standard"
        )
        rule_id_value = rule.id
        document_space_id = rule.document_space_id
        rule.delete()
        RecordAuditEvent(
            tenant=tenant,
            actor=request.user,
            event_type="document_title_rule.deleted",
            object_type="documents.DocumentTitleRule",
            object_id=str(rule_id_value),
            data={
                "document_space_id": document_space_id,
                "scope": scope_name,
            },
        ).execute()
        messages.success(
            request,
            "Regel wurde entfernt. Es gilt wieder die übergeordnete Automatik.",
        )
        return redirect(
            "documents:settings_title_rules",
            tenant_slug=tenant.slug,
        )
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])

    return render(
        request,
        "documents/settings_title_rule_delete.html",
        {
            "tenant": tenant,
            "rule": rule,
            "active_settings_section": "title_rules",
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


def tenant_settings_title_regex_test(
    request: HttpRequest,
    tenant_slug: str,
) -> JsonResponse:
    if not request.user.is_authenticated:
        raise PermissionDenied
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse(
            {"ok": False, "error": "Ungültige Testdaten."},
            status=400,
        )

    pattern = str(payload.get("regex_search", "")).strip()
    replacement = str(payload.get("regex_replace", ""))
    sample_text = str(payload.get("sample_text", ""))
    if not pattern:
        return JsonResponse(
            {"ok": False, "error": "Bitte ein Suchmuster angeben."},
            status=400,
        )
    if not sample_text.strip():
        return JsonResponse(
            {"ok": False, "error": "Bitte OCR-Beispieltext einfügen."},
            status=400,
        )

    try:
        compiled_pattern = re.compile(pattern, flags=re.MULTILINE)
        match = compiled_pattern.search(sample_text)
        title = title_from_ocr_policy(
            sample_text,
            {
                "strategy": "regex",
                "regex_search": pattern,
                "regex_replace": replacement,
            },
        )
    except re.error as error:
        return JsonResponse(
            {"ok": False, "error": f"RegEx-Fehler: {error}"},
            status=400,
        )

    if match is None:
        return JsonResponse({"ok": True, "matched": False, "title": ""})
    return JsonResponse(
        {
            "ok": True,
            "matched": True,
            "title": title or "",
            "match": match.group(0),
        }
    )


def tenant_settings_title_einvoice_format_test(
    request: HttpRequest,
    tenant_slug: str,
) -> JsonResponse:
    if not request.user.is_authenticated:
        raise PermissionDenied
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse(
            {"ok": False, "error": "Ungültige Testdaten."},
            status=400,
        )

    format_string = str(payload.get("einvoice_format", ""))
    sample_data = {
        "invoice_number": "RE-4711",
        "invoice_date": "20260707",
        "seller_name": "Musterlieferant GmbH",
        "buyer_name": tenant.name,
        "currency": "EUR",
        "line_total_amount": "290.00",
        "tax_basis_total_amount": "290.00",
        "tax_total_amount": "55.10",
        "grand_total_amount": "345.10",
        "due_payable_amount": "345.10",
        "syntax": "CII",
        "profile": "EN 16931",
    }
    try:
        title = title_from_einvoice_data(sample_data, format_string)
    except ValueError as error:
        return JsonResponse(
            {"ok": False, "error": str(error)},
            status=400,
        )

    return JsonResponse(
        {
            "ok": True,
            "title": title or "",
        }
    )


def tenant_settings_title_invoice_ocr_test(
    request: HttpRequest,
    tenant_slug: str,
) -> JsonResponse:
    if not request.user.is_authenticated:
        raise PermissionDenied
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
        title = title_from_invoice_ocr_text(
            str(payload.get("sample_text", "")),
            str(payload.get("invoice_ocr_format", "")),
        )
    except (json.JSONDecodeError, ValueError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)

    return JsonResponse({"ok": True, "title": title or ""})


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
    auto_reply_recipients_page_obj = None
    if import_source.source_type == ImportSource.SourceType.EMAIL:
        auto_reply_recipients_page_obj = paginate_queryset(
            request,
            import_source.auto_reply_recipients.all(),
            page_param="auto_replies_page",
            per_page=25,
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
            "auto_reply_recipients_page_obj": auto_reply_recipients_page_obj,
        },
    )


def tenant_settings_import_source_auto_reply_recipients_reset(
    request: HttpRequest,
    tenant_slug: str,
    source_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    import_source = get_object_or_404(
        ImportSource,
        id=source_id,
        tenant=tenant,
        source_type=ImportSource.SourceType.EMAIL,
    )
    recipients = EmailAutoReplyRecipient.objects.filter(
        tenant=tenant,
        source=import_source,
    )
    recipient_id = request.POST.get("recipient_id", "").strip()
    if recipient_id:
        if not recipient_id.isdigit():
            raise Http404
        recipients = recipients.filter(id=recipient_id)

    deleted_recipients = list(recipients.values("recipient", "reply_type"))
    recipients.delete()
    RecordAuditEvent(
        tenant=tenant,
        actor=request.user,
        event_type="email_import_reply.recipients_reset",
        object_type="ingestion.ImportSource",
        object_id=str(import_source.id),
        data={
            "recipients": deleted_recipients,
            "reset_all": not bool(recipient_id),
        },
    ).execute()
    if deleted_recipients:
        messages.success(
            request,
            (
                "Der Absender kann die gewählte Auto-Antwort erneut erhalten."
                if recipient_id
                else "Die Liste der bereits benachrichtigten Absender wurde geleert."
            ),
        )

    return redirect(
        "documents:settings_import_source_edit",
        tenant_slug=tenant.slug,
        source_id=import_source.id,
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
                display_name=form.cleaned_data["display_name"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
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
        TenantMembership.objects.select_related(
            "tenant", "user", "role"
        ).prefetch_related("roles"),
        id=membership_id,
        tenant=tenant,
    )
    if request.method == "POST":
        form = TenantMembershipUpdateForm(request.POST, tenant=tenant)
        if form.is_valid():
            UpdateTenantMembership(
                membership=membership,
                email=form.cleaned_data["email"],
                display_name=form.cleaned_data["display_name"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                password=form.cleaned_data["password"],
                roles=list(form.cleaned_data["roles"]),
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Benutzer wurde aktualisiert.")
            return redirect("documents:settings_members", tenant_slug=tenant.slug)
    else:
        profile = UserProfile.objects.filter(user=membership.user).first()
        form = TenantMembershipUpdateForm(
            tenant=tenant,
            initial={
                "display_name": profile.display_name if profile else "",
                "first_name": membership.user.first_name,
                "last_name": membership.user.last_name,
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


def tenant_settings_member_send_password_reset(
    request: HttpRequest,
    tenant_slug: str,
    membership_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)
    if request.method != "POST":
        return redirect("documents:settings_members", tenant_slug=tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_members(request.user, tenant):
        raise PermissionDenied

    membership = get_object_or_404(
        TenantMembership.objects.select_related("tenant", "user"),
        id=membership_id,
        tenant=tenant,
    )
    try:
        SendTenantPasswordResetEmail(
            tenant=tenant,
            membership=membership,
            actor=request.user,
        ).execute()
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Passwort-Reset-Mail wurde gesendet.")
    return redirect("documents:settings_members", tenant_slug=tenant.slug)


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
        .annotate(
            system_role_order=Case(
                When(is_system_role=True, slug="admin", then=Value(0)),
                When(is_system_role=True, slug="member", then=Value(1)),
                When(is_system_role=True, slug="viewer", then=Value(2)),
                When(is_system_role=True, then=Value(10)),
                default=Value(20),
                output_field=IntegerField(),
            )
        )
        .order_by("system_role_order", "name", "id")
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
                "can_access_all_document_spaces": (role.can_access_all_document_spaces),
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


def tenant_settings_role_delete(
    request: HttpRequest,
    tenant_slug: str,
    role_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_roles(request.user, tenant):
        raise PermissionDenied

    role = get_object_or_404(TenantRole, id=role_id, tenant=tenant)
    if request.method == "POST":
        try:
            DeleteTenantRole(role=role, actor=request.user).execute()
        except ValueError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Rolle wurde gelöscht.")
        return redirect("documents:settings_roles", tenant_slug=tenant.slug)

    return render(
        request,
        "documents/settings_role_delete.html",
        {
            "tenant": tenant,
            "role": role,
            "active_settings_section": "roles",
        },
    )
