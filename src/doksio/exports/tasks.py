from __future__ import annotations

from celery import shared_task
from django.utils import timezone

from doksio.audit.services import RecordAuditEvent
from doksio.exports.models import ExportRun
from doksio.exports.services import BuildDocumentImageExport


@shared_task
def build_document_image_export(export_run_id: int) -> int:
    export_run = ExportRun.objects.select_related("tenant", "created_by").get(
        id=export_run_id,
    )
    try:
        BuildDocumentImageExport(export_run=export_run).execute()
    except Exception as exc:
        export_run.status = ExportRun.Status.FAILED
        export_run.completed_at = timezone.now()
        export_run.save(update_fields=["status", "completed_at", "updated_at"])
        RecordAuditEvent(
            tenant=export_run.tenant,
            actor=export_run.created_by,
            event_type="export_run.failed",
            object_type="exports.ExportRun",
            object_id=str(export_run.id),
            data={
                "export_type": export_run.export_type,
                "error": str(exc),
            },
        ).execute()
        raise
    return export_run_id
