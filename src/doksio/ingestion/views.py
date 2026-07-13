from __future__ import annotations

import mimetypes
from io import BytesIO
from uuid import uuid4

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from doksio.documents.services import DuplicateDocumentError
from doksio.ingestion.models import ImportSource
from doksio.ingestion.services import ImportDocument, ResolveImportDocumentSpace
from doksio.tenancy.models import Tenant

GENERIC_CONTENT_TYPES = {
    "",
    "application/octet-stream",
    "application/x-www-form-urlencoded",
    "binary/octet-stream",
}


def _source_common_settings(source: ImportSource) -> dict:
    return (source.settings or {}).get("common", {})


def _token_from_request(request: HttpRequest) -> str:
    return request.headers.get("X-Doksio-Import-Token", "").strip()


def _uploaded_file_from_request(request: HttpRequest):
    if request.FILES:
        return next(iter(request.FILES.values()))
    if request.body:
        return BytesIO(request.body)
    return None


def _normalized_content_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _filename_header_from_request(request: HttpRequest) -> str:
    return request.headers.get("X-Doksio-Filename", "").strip()


def _uploaded_file_name(request: HttpRequest, uploaded_file) -> str:
    if hasattr(uploaded_file, "name") and uploaded_file.name:
        return uploaded_file.name
    return _filename_header_from_request(request)


def _filename_from_request(
    request: HttpRequest,
    uploaded_file,
) -> str:
    uploaded_file_name = _uploaded_file_name(request, uploaded_file)
    if uploaded_file_name:
        return uploaded_file_name
    extension = mimetypes.guess_extension(
        _content_type_from_request(request, uploaded_file)
    )
    return f"api-import-{uuid4().hex}{extension or '.bin'}"


def _uploaded_file_head(uploaded_file, size: int = 512) -> bytes:
    position = uploaded_file.tell()
    try:
        return uploaded_file.read(size)
    finally:
        uploaded_file.seek(position)


def _content_type_from_magic(uploaded_file) -> str:
    head = _uploaded_file_head(uploaded_file)
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if head.lstrip().startswith(b"<?xml"):
        return "application/xml"
    return ""


def _content_type_from_request(request: HttpRequest, uploaded_file) -> str:
    uploaded_content_type = _normalized_content_type(
        getattr(uploaded_file, "content_type", "") or ""
    )
    if uploaded_content_type not in GENERIC_CONTENT_TYPES:
        return uploaded_content_type

    request_content_type = _normalized_content_type(
        request.headers.get("Content-Type", "")
    )
    if (
        request_content_type not in GENERIC_CONTENT_TYPES
        and not request_content_type.startswith("multipart/")
    ):
        return request_content_type

    guessed_content_type, _encoding = mimetypes.guess_type(
        _uploaded_file_name(request, uploaded_file)
    )
    if guessed_content_type:
        return guessed_content_type

    magic_content_type = _content_type_from_magic(uploaded_file)
    if magic_content_type:
        return magic_content_type

    return "application/octet-stream"


def _uploaded_file_size(request: HttpRequest, uploaded_file) -> int:
    if hasattr(uploaded_file, "size"):
        return uploaded_file.size
    return len(request.body)


def _uploaded_file_starts_with(uploaded_file, prefix: bytes) -> bool:
    position = uploaded_file.tell()
    try:
        return uploaded_file.read(len(prefix)) == prefix
    finally:
        uploaded_file.seek(position)


def _validate_http_settings(
    request: HttpRequest,
    source: ImportSource,
    uploaded_file,
) -> JsonResponse | None:
    common_settings = _source_common_settings(source)

    max_file_size_mb = common_settings.get("max_file_size_mb")
    if max_file_size_mb:
        max_file_size_bytes = int(max_file_size_mb) * 1024 * 1024
        if _uploaded_file_size(request, uploaded_file) > max_file_size_bytes:
            return JsonResponse({"error": "Datei ist zu groß."}, status=413)

    allowed_content_types = common_settings.get("allowed_content_types") or []
    if allowed_content_types:
        content_type = _content_type_from_request(request, uploaded_file)
        if content_type not in allowed_content_types:
            return JsonResponse(
                {"error": "Dateityp ist für diese Importquelle nicht erlaubt."},
                status=415,
            )
    content_type = _content_type_from_request(request, uploaded_file)
    if content_type == "application/pdf" and not _uploaded_file_starts_with(
        uploaded_file,
        b"%PDF",
    ):
        return JsonResponse(
            {
                "error": (
                    "Die übergebene Datei ist kein gültiges PDF. "
                    "Bei curl bitte --data-binary \"@datei.pdf\" verwenden."
                )
            },
            status=400,
        )
    return None


@csrf_exempt
@require_http_methods(["POST", "PUT"])
def http_import(request: HttpRequest, tenant_slug: str, source_id: int) -> JsonResponse:
    tenant = get_object_or_404(Tenant, slug=tenant_slug, is_active=True)
    source = get_object_or_404(
        ImportSource.objects.select_related("document_space"),
        id=source_id,
        tenant=tenant,
        source_type__in=[
            ImportSource.SourceType.HTTP_API,
            ImportSource.SourceType.FOLDER,
        ],
        is_active=True,
    )
    if not source.token or _token_from_request(request) != source.token:
        return JsonResponse({"error": "Ungültiger Import-Token."}, status=403)

    uploaded_file = _uploaded_file_from_request(request)
    if uploaded_file is None:
        return JsonResponse({"error": "Keine Datei übergeben."}, status=400)
    settings_error = _validate_http_settings(request, source, uploaded_file)
    if settings_error is not None:
        return settings_error

    try:
        original_filename = _filename_from_request(request, uploaded_file)
        document_space = ResolveImportDocumentSpace(
            tenant=tenant,
            source=source,
            original_filename=original_filename,
        ).execute()
        document, import_job = ImportDocument(
            tenant=tenant,
            source=source,
            document_space=document_space,
            file_obj=uploaded_file,
            original_filename=original_filename,
            content_type=_content_type_from_request(request, uploaded_file),
            metadata={
                "method": request.method,
                "remote_addr": request.META.get("REMOTE_ADDR", ""),
            },
        ).execute()
    except DuplicateDocumentError as exc:
        return JsonResponse(
            {
                "error": str(exc),
                "code": "duplicate_document",
                "duplicate": True,
                "existing_document_id": exc.existing_document.id,
                "existing_document_title": exc.existing_document.title,
            },
            status=409,
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(
        {
            "document_id": document.id,
            "import_job_id": import_job.id,
            "status": import_job.status,
            "title": document.title,
        },
        status=201,
    )
