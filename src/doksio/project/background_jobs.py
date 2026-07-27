from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from doksio.documents.models import (
    DocumentBoxScanOptimizationJob,
    DocumentBoxTitleRefreshJob,
    DocumentOfficeConversionJob,
)
from doksio.exports.models import ExportRun
from doksio.ocr.models import OcrJob
from doksio.project.celery import app


@dataclass(frozen=True)
class BackgroundJob:
    task_id: str
    state: str
    worker: str
    task_name: str
    job_type: str
    object_id: int
    title: str
    created_at: Any
    progress: str

    @property
    def can_cancel(self) -> bool:
        return bool(self.task_id) or self.state in {"queued", "interrupted"}


TASK_TYPES = {
    "doksio.ocr.tasks.run_ocr_job": "ocr",
    "doksio.documents.tasks.process_document_box_scan_optimization_job": "scan",
    "doksio.documents.tasks.process_document_box_title_refresh_job": "titles",
    "doksio.documents.tasks.convert_office_document": "office",
    "doksio.exports.tasks.build_document_image_export": "export",
}


def _task_object_id(task: dict) -> int | None:
    args = task.get("args") or []
    if isinstance(args, str):
        return None
    try:
        return int(args[0])
    except (IndexError, TypeError, ValueError):
        return None


def _job_details(job_type: str, object_id: int, tenant_id: int):
    if job_type == "ocr":
        job = (
            OcrJob.objects.select_related("document_file__document")
            .filter(id=object_id, tenant_id=tenant_id)
            .first()
        )
        if job:
            return job, f"OCR: {job.document_file.document.title}", ""
    elif job_type == "scan":
        job = (
            DocumentBoxScanOptimizationJob.objects.select_related("document_space")
            .filter(id=object_id, tenant_id=tenant_id)
            .first()
        )
        if job:
            return (
                job,
                f"Scan-Speicher optimieren: {job.document_space.path}",
                f"{job.processed_documents}/{job.total_documents}",
            )
    elif job_type == "titles":
        job = (
            DocumentBoxTitleRefreshJob.objects.select_related("document_space")
            .filter(id=object_id, tenant_id=tenant_id)
            .first()
        )
        if job:
            return (
                job,
                f"Titel neu berechnen: {job.document_space.path}",
                f"{job.processed_documents}/{job.total_documents}",
            )
    elif job_type == "export":
        job = ExportRun.objects.filter(id=object_id, tenant_id=tenant_id).first()
        if job:
            return (
                job,
                f"Export: {job.get_export_type_display()}",
                f"{job.processed_count}/{job.total_count}",
            )
    elif job_type == "office":
        job = (
            DocumentOfficeConversionJob.objects.select_related(
                "source_file__document"
            )
            .filter(id=object_id, tenant_id=tenant_id)
            .first()
        )
        if job:
            return (
                job,
                f"Office-Konvertierung: {job.source_file.document.title}",
                "",
            )
    return None


def tenant_background_jobs(tenant) -> tuple[list[BackgroundJob], str]:
    worker_error = ""
    try:
        inspector = app.control.inspect(timeout=1)
        snapshots = {
            "running": inspector.active() or {},
            "queued": inspector.reserved() or {},
            "scheduled": inspector.scheduled() or {},
        }
    except Exception as error:
        snapshots = {}
        worker_error = str(error)

    jobs = []
    for state, workers in snapshots.items():
        for worker, tasks in workers.items():
            for raw_task in tasks:
                task = raw_task.get("request", raw_task)
                task_name = task.get("name", "")
                job_type = TASK_TYPES.get(task_name)
                object_id = _task_object_id(task)
                if not job_type or object_id is None:
                    continue
                details = _job_details(job_type, object_id, tenant.id)
                if details is None:
                    continue
                job, title, progress = details
                jobs.append(
                    BackgroundJob(
                        task_id=str(task.get("id", "")),
                        state=state,
                        worker=worker,
                        task_name=task_name,
                        job_type=job_type,
                        object_id=object_id,
                        title=title,
                        created_at=job.created_at,
                        progress=progress,
                    )
                )
    known_jobs = {(job.job_type, job.object_id) for job in jobs}
    database_jobs = [
        *(
            ("ocr", job, f"OCR: {job.document_file.document.title}", "")
            for job in OcrJob.objects.select_related("document_file__document").filter(
                tenant=tenant,
                status__in=[OcrJob.Status.PENDING, OcrJob.Status.RUNNING],
            )
        ),
        *(
            (
                "scan",
                job,
                f"Scan-Speicher optimieren: {job.document_space.path}",
                f"{job.processed_documents}/{job.total_documents}",
            )
            for job in DocumentBoxScanOptimizationJob.objects.select_related(
                "document_space"
            ).filter(
                tenant=tenant,
                status__in=[
                    DocumentBoxScanOptimizationJob.Status.QUEUED,
                    DocumentBoxScanOptimizationJob.Status.RUNNING,
                ],
            )
        ),
        *(
            (
                "titles",
                job,
                f"Titel neu berechnen: {job.document_space.path}",
                f"{job.processed_documents}/{job.total_documents}",
            )
            for job in DocumentBoxTitleRefreshJob.objects.select_related(
                "document_space"
            ).filter(
                tenant=tenant,
                status__in=[
                    DocumentBoxTitleRefreshJob.Status.QUEUED,
                    DocumentBoxTitleRefreshJob.Status.RUNNING,
                ],
            )
        ),
        *(
            (
                "export",
                job,
                f"Export: {job.get_export_type_display()}",
                f"{job.processed_count}/{job.total_count}",
            )
            for job in ExportRun.objects.filter(
                tenant=tenant,
                status=ExportRun.Status.PROCESSING,
            )
        ),
        *(
            (
                "office",
                job,
                f"Office-Konvertierung: {job.source_file.document.title}",
                "",
            )
            for job in DocumentOfficeConversionJob.objects.select_related(
                "source_file__document"
            ).filter(
                tenant=tenant,
                status__in=[
                    DocumentOfficeConversionJob.Status.PENDING,
                    DocumentOfficeConversionJob.Status.RUNNING,
                ],
            )
        ),
    ]
    for job_type, job, title, progress in database_jobs:
        if (job_type, job.id) in known_jobs:
            continue
        is_interrupted = (
            job_type in {"scan", "titles"}
            and getattr(job, "status", "") == "running"
            and job.is_resumable
        )
        jobs.append(
            BackgroundJob(
                task_id="",
                state=(
                    "interrupted"
                    if is_interrupted
                    else (
                        "running"
                        if getattr(job, "status", "") in {"running"}
                        else "queued"
                    )
                ),
                worker="Noch keinem Worker zugeordnet",
                task_name="",
                job_type=job_type,
                object_id=job.id,
                title=title,
                created_at=job.created_at,
                progress=progress,
            )
        )
    jobs.sort(key=lambda job: job.created_at, reverse=True)
    return jobs, worker_error


def cancel_background_job(*, tenant, job_type: str, object_id: int, task_id: str) -> str:
    details = _job_details(job_type, object_id, tenant.id)
    if details is None:
        raise ValueError("Der Hintergrundjob wurde nicht gefunden.")

    if task_id:
        app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    now = timezone.now()
    if job_type == "ocr":
        OcrJob.objects.filter(id=object_id, tenant=tenant).update(
            status=OcrJob.Status.FAILED,
            error_message="Durch einen Administrator abgebrochen.",
            completed_at=now,
            updated_at=now,
        )
    elif job_type == "scan":
        DocumentBoxScanOptimizationJob.objects.filter(
            id=object_id,
            tenant=tenant,
        ).update(
            status=DocumentBoxScanOptimizationJob.Status.FAILED,
            error_message="Durch einen Administrator abgebrochen.",
            completed_at=now,
            heartbeat_at=now,
            lease_token=None,
            lease_expires_at=None,
            updated_at=now,
        )
    elif job_type == "titles":
        DocumentBoxTitleRefreshJob.objects.filter(
            id=object_id,
            tenant=tenant,
        ).update(
            status=DocumentBoxTitleRefreshJob.Status.FAILED,
            error_message="Durch einen Administrator abgebrochen.",
            completed_at=now,
            heartbeat_at=now,
            lease_token=None,
            lease_expires_at=None,
            updated_at=now,
        )
    elif job_type == "export":
        ExportRun.objects.filter(id=object_id, tenant=tenant).update(
            status=ExportRun.Status.FAILED,
            completed_at=now,
            updated_at=now,
        )
    elif job_type == "office":
        DocumentOfficeConversionJob.objects.filter(
            id=object_id,
            tenant=tenant,
        ).update(
            status=DocumentOfficeConversionJob.Status.FAILED,
            error_message="Durch einen Administrator abgebrochen.",
            completed_at=now,
            updated_at=now,
        )
    return details[1]
