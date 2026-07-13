from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import redis
from celery.exceptions import CeleryError
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import connection
from django.db.models import Count, Sum
from django.utils import timezone

from doksio.documents.models import Document, DocumentFile
from doksio.ingestion.models import ImportJob, ImportSource
from doksio.ocr.models import OcrJob
from doksio.project.celery import app as celery_app
from doksio.tenancy.models import Tenant
from doksio.workflows.models import WorkflowInstance, WorkflowTask


@dataclass(frozen=True)
class StatusCheck:
    key: str
    label: str
    state: str
    message: str
    meta: str = ""


def _ok(key: str, label: str, message: str, meta: str = "") -> StatusCheck:
    return StatusCheck(key=key, label=label, state="ok", message=message, meta=meta)


def _warn(key: str, label: str, message: str, meta: str = "") -> StatusCheck:
    return StatusCheck(
        key=key,
        label=label,
        state="warning",
        message=message,
        meta=meta,
    )


def _down(key: str, label: str, message: str, meta: str = "") -> StatusCheck:
    return StatusCheck(key=key, label=label, state="down", message=message, meta=meta)


def _database_check() -> StatusCheck:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return _down("database", "Datenbank", "Nicht erreichbar", str(exc))
    return _ok("database", "Datenbank", "Erreichbar", connection.vendor)


def _storage_check() -> StatusCheck:
    try:
        default_storage.exists("__doksio_status_probe__")
    except Exception as exc:
        return _down("storage", "Object Storage", "Nicht erreichbar", str(exc))
    return _ok(
        "storage",
        "Object Storage",
        "Erreichbar",
        getattr(settings, "AWS_STORAGE_BUCKET_NAME", ""),
    )


def _redis_check() -> StatusCheck:
    broker_url = getattr(settings, "CELERY_BROKER_URL", "")
    try:
        client = redis.Redis.from_url(
            broker_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
    except Exception as exc:
        return _down("broker", "Redis/Broker", "Nicht erreichbar", str(exc))
    return _ok("broker", "Redis/Broker", "Erreichbar", broker_url)


def _worker_check() -> StatusCheck:
    if getattr(settings, "OCR_RUN_INLINE", False):
        return _ok(
            "worker",
            "Celery Worker",
            "Nicht erforderlich",
            "OCR läuft in diesem Setup inline.",
        )

    try:
        replies = celery_app.control.inspect(timeout=1).ping() or {}
    except (CeleryError, OSError, redis.RedisError) as exc:
        return _down("worker", "Celery Worker", "Nicht erreichbar", str(exc))
    except Exception as exc:
        return _down("worker", "Celery Worker", "Nicht erreichbar", str(exc))

    if not replies:
        return _warn("worker", "Celery Worker", "Kein Worker antwortet")

    worker_count = len(replies)
    return _ok(
        "worker",
        "Celery Worker",
        f"{worker_count} Worker aktiv",
        ", ".join(sorted(replies.keys())),
    )


def _ocr_runtime_check() -> StatusCheck:
    language = getattr(settings, "OCR_LANGUAGE", "")
    timeout = getattr(settings, "OCR_COMMAND_TIMEOUT_SECONDS", "")
    if getattr(settings, "OCR_AUTO_START_ON_UPLOAD", True):
        message = "Automatisch aktiv"
    else:
        message = "Automatisch deaktiviert"
    return _ok("ocr", "OCR-Konfiguration", message, f"{language}, Timeout {timeout}s")


def _counts_by_status(queryset, statuses) -> dict[str, int]:
    counts = {status: 0 for status, _label in statuses}
    for row in queryset.values("status").annotate(count=Count("id")):
        counts[row["status"]] = row["count"]
    return counts


def _status_rows(counts: dict[str, int], statuses) -> list[dict[str, Any]]:
    return [
        {
            "status": status,
            "label": label,
            "count": counts.get(status, 0),
        }
        for status, label in statuses
    ]


def _format_bytes(byte_count: int) -> str:
    value = float(byte_count)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def build_system_status(*, tenant: Tenant | None = None) -> dict[str, Any]:
    now = timezone.now()
    last_24h = now - timedelta(hours=24)

    documents = Document.objects.all()
    document_files = DocumentFile.objects.all()
    import_jobs = ImportJob.objects.all()
    ocr_jobs = OcrJob.objects.all()
    import_sources = ImportSource.objects.all()
    workflow_instances = WorkflowInstance.objects.all()
    workflow_tasks = WorkflowTask.objects.all()
    tenants = Tenant.objects.all()

    if tenant is not None:
        documents = documents.filter(tenant=tenant)
        document_files = document_files.filter(tenant=tenant)
        import_jobs = import_jobs.filter(tenant=tenant)
        ocr_jobs = ocr_jobs.filter(tenant=tenant)
        import_sources = import_sources.filter(tenant=tenant)
        workflow_instances = workflow_instances.filter(tenant=tenant)
        workflow_tasks = workflow_tasks.filter(tenant=tenant)
        tenants = tenants.filter(id=tenant.id)

    import_counts = _counts_by_status(import_jobs, ImportJob.Status.choices)
    ocr_counts = _counts_by_status(ocr_jobs, OcrJob.Status.choices)
    workflow_counts = _counts_by_status(
        workflow_instances,
        WorkflowInstance.Status.choices,
    )

    checks = [
        _database_check(),
        _storage_check(),
        _redis_check(),
        _worker_check(),
        _ocr_runtime_check(),
    ]
    state_rank = {"ok": 0, "warning": 1, "down": 2}
    overall_state = max((check.state for check in checks), key=state_rank.get)

    recent_failed_imports = (
        import_jobs.filter(status=ImportJob.Status.FAILED)
        .select_related("tenant", "source", "document_space")
        .order_by("-updated_at", "-id")[:8]
    )
    recent_failed_ocr_jobs = (
        ocr_jobs.filter(status=OcrJob.Status.FAILED)
        .select_related("tenant", "document_file", "document_file__document")
        .order_by("-updated_at", "-id")[:8]
    )
    storage_used_bytes = document_files.aggregate(total=Sum("byte_size"))["total"] or 0

    return {
        "generated_at": now,
        "overall_state": overall_state,
        "checks": checks,
        "summary": {
            "tenants": tenants.count(),
            "active_tenants": tenants.filter(is_active=True).count(),
            "documents": documents.count(),
            "documents_last_24h": documents.filter(created_at__gte=last_24h).count(),
            "active_import_sources": import_sources.filter(is_active=True).count(),
            "imports_last_24h": import_jobs.filter(received_at__gte=last_24h).count(),
            "open_workflow_tasks": workflow_tasks.filter(
                status=WorkflowTask.Status.OPEN,
            ).count(),
        },
        "storage": {
            "used_bytes": storage_used_bytes,
            "used_human": _format_bytes(storage_used_bytes),
            "file_count": document_files.count(),
            "free_human": "Nicht über S3 verfügbar",
            "free_note": (
                "Die normale S3-API liefert keinen freien Speicherplatz. "
                "Für MinIO braucht es dafür Admin- oder Host-Monitoring."
            ),
        },
        "imports": {
            "status_rows": _status_rows(import_counts, ImportJob.Status.choices),
            "processing": import_counts.get(ImportJob.Status.PROCESSING, 0),
            "failed_recent": recent_failed_imports,
        },
        "ocr": {
            "status_rows": _status_rows(ocr_counts, OcrJob.Status.choices),
            "pending": ocr_counts.get(OcrJob.Status.PENDING, 0),
            "running": ocr_counts.get(OcrJob.Status.RUNNING, 0),
            "failed_recent": recent_failed_ocr_jobs,
        },
        "workflows": {
            "status_rows": _status_rows(
                workflow_counts,
                WorkflowInstance.Status.choices,
            ),
            "open_tasks": workflow_tasks.filter(
                status=WorkflowTask.Status.OPEN,
            ).count(),
            "completed_tasks_last_24h": workflow_tasks.filter(
                status=WorkflowTask.Status.COMPLETED,
                completed_at__gte=last_24h,
            ).count(),
        },
    }
