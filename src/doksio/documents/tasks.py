from __future__ import annotations

import uuid
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from doksio.accounts.models import Notification
from doksio.accounts.services import CreateNotification
from doksio.documents.models import (
    Document,
    DocumentBoxOcrLayoutJob,
    DocumentBoxScanOptimizationJob,
    DocumentBoxTitleRefreshJob,
    DocumentOfficeConversionJob,
    DocumentReminder,
    DocumentSpace,
)
from doksio.documents.services import (
    ClaimDocumentBoxScanOptimizationJob,
    ClaimDocumentBoxTitleRefreshJob,
    CreateDocumentBoxScanOptimizationJob,
    CreateDocumentBoxTitleRefreshJob,
    OptimizeDocumentBoxScans,
    RunDocumentBoxScanOptimizationBatch,
    RunDocumentBoxTitleRefreshBatch,
)


@shared_task
def process_document_box_ocr_layout_job(job_id: int) -> dict:
    from doksio.ocr.services import CreateOcrJob, RunOcrJob

    with transaction.atomic():
        job = DocumentBoxOcrLayoutJob.objects.select_for_update().get(id=job_id)
        if job.status != DocumentBoxOcrLayoutJob.Status.QUEUED:
            return {"job_id": job.id, "status": job.status, "claimed": False}
        now = timezone.now()
        job.status = DocumentBoxOcrLayoutJob.Status.RUNNING
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        job.save(update_fields=["status", "started_at", "heartbeat_at", "updated_at"])

    box_filter = Q(space=job.document_space)
    if job.include_children:
        box_filter |= Q(space__path__startswith=f"{job.document_space.path}/")
    documents = list(
        Document.objects.filter(
            box_filter,
            tenant=job.tenant,
            status=Document.Status.ACTIVE,
            id__gt=job.last_document_id,
            id__lte=job.max_document_id,
        )
        .prefetch_related("files__ocr_jobs")
        .order_by("id")[: job.batch_size]
    )
    if not documents:
        job.last_document_id = job.max_document_id
        job.save(update_fields=["last_document_id", "updated_at"])
    for document in documents:
        generated = 0
        skipped = 0
        errors = 0
        original_file = next(
            (
                file
                for file in document.files.all()
                if file.file_kind == file.Kind.ORIGINAL
                and file.content_type == "application/pdf"
            ),
            None,
        )
        existing_layout = bool(
            original_file
            and any(
                ocr_job.status == ocr_job.Status.SUCCEEDED
                and ocr_job.layout_storage_key
                for ocr_job in original_file.ocr_jobs.all()
            )
        )
        if original_file is None or existing_layout:
            skipped = 1
        else:
            try:
                ocr_job = CreateOcrJob(
                    document_file=original_file,
                    actor=job.created_by,
                    metadata={"layout_backfill": True, "maintenance_job_id": job.id},
                ).execute()
                RunOcrJob(job=ocr_job).execute()
                ocr_job.refresh_from_db(fields=["layout_storage_key"])
                if ocr_job.layout_storage_key:
                    generated = 1
                else:
                    skipped = 1
            except Exception as error:
                errors = 1
                job.error_message = str(error)[:1000]

        job.processed_documents += 1
        job.last_document_id = document.id
        job.generated += generated
        job.skipped += skipped
        job.errors += errors
        job.heartbeat_at = timezone.now()
        job.save(
            update_fields=[
                "processed_documents",
                "last_document_id",
                "generated",
                "skipped",
                "errors",
                "error_message",
                "heartbeat_at",
                "updated_at",
            ]
        )

    has_more = job.last_document_id < job.max_document_id
    if has_more:
        job.status = DocumentBoxOcrLayoutJob.Status.QUEUED
        job.save(update_fields=["status", "updated_at"])
        process_document_box_ocr_layout_job.delay(job.id)
    else:
        job.status = DocumentBoxOcrLayoutJob.Status.COMPLETED
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at", "updated_at"])
    return {
        "job_id": job.id,
        "status": job.status,
        "processed_documents": job.processed_documents,
        "total_documents": job.total_documents,
        "generated": job.generated,
        "skipped": job.skipped,
        "errors": job.errors,
    }


@shared_task
def dispatch_due_document_reminders() -> dict:
    due_ids = list(
        DocumentReminder.objects.filter(
            completed_at__isnull=True,
            notified_at__isnull=True,
            remind_on__lte=timezone.localdate(),
            document__status="active",
            tenant__is_active=True,
            recipient__is_active=True,
        ).values_list("id", flat=True)
    )
    notified = 0
    for reminder_id in due_ids:
        with transaction.atomic():
            reminder = (
                DocumentReminder.objects.select_for_update()
                .select_related("tenant", "document", "document__space", "recipient")
                .filter(
                    id=reminder_id,
                    completed_at__isnull=True,
                    notified_at__isnull=True,
                    document__status="active",
                )
                .first()
            )
            if reminder is None:
                continue
            due_label = reminder.remind_on.strftime("%d.%m.%Y")
            CreateNotification(
                tenant=reminder.tenant,
                recipient=reminder.recipient,
                notification_type=Notification.Type.DOCUMENT_REMINDER,
                title=f"Wiedervorlage: {reminder.document.title}",
                body=f"{reminder.note}\nFällig am {due_label}",
                link_url=reverse(
                    "documents:detail",
                    kwargs={
                        "tenant_slug": reminder.tenant.slug,
                        "document_id": reminder.document_id,
                    },
                ),
                document=reminder.document,
            ).execute()
            reminder.notified_at = timezone.now()
            reminder.save(update_fields=["notified_at", "updated_at"])
            notified += 1
    return {"checked": len(due_ids), "notified": notified}


@shared_task
def convert_office_document(job_id: int) -> int:
    from doksio.documents.office_conversion import RunOfficeConversion

    job = DocumentOfficeConversionJob.objects.select_related(
        "source_file__document",
        "tenant",
    ).get(id=job_id)
    RunOfficeConversion(job=job).execute()
    return job_id


@shared_task
def process_document_box_scan_optimization_job(
    job_id: int,
    *,
    resume_reason: str = "",
    lease_token_value: str = "",
) -> dict:
    lease_token = uuid.UUID(lease_token_value) if lease_token_value else uuid.uuid4()
    job = ClaimDocumentBoxScanOptimizationJob(
        job_id=job_id,
        lease_token=lease_token,
        resume_reason=resume_reason,
    ).execute()
    if job is None:
        current_job = DocumentBoxScanOptimizationJob.objects.get(id=job_id)
        return _scan_optimization_job_result(current_job, claimed=False)

    job = RunDocumentBoxScanOptimizationBatch(
        job=job,
        actor=job.created_by,
        lease_token=lease_token,
    ).execute()
    should_continue = (
        job.status == DocumentBoxScanOptimizationJob.Status.RUNNING
        and job.processed_documents < job.total_documents
    )
    if should_continue:
        process_document_box_scan_optimization_job.delay(job.id)
    return _scan_optimization_job_result(job, claimed=True)


def _scan_optimization_job_result(
    job: DocumentBoxScanOptimizationJob,
    *,
    claimed: bool,
) -> dict:
    return {
        "job_id": job.id,
        "status": job.status,
        "claimed": claimed,
        "processed_documents": job.processed_documents,
        "total_documents": job.total_documents,
        "candidates": job.candidates,
        "optimized": job.optimized,
        "skipped": job.skipped,
        "errors": job.errors,
        "bytes_before": job.bytes_before,
        "bytes_after": job.bytes_after,
        "saved_bytes": job.saved_bytes,
    }


@shared_task
def resume_stale_scan_optimization_jobs() -> dict:
    now = timezone.now()
    cutoff = now - timedelta(
        seconds=getattr(
            settings,
            "SCAN_OPTIMIZATION_STALE_AFTER_SECONDS",
            120,
        )
    )
    recoverable_jobs = (
        DocumentBoxScanOptimizationJob.objects.filter(
            status__in=[
                DocumentBoxScanOptimizationJob.Status.QUEUED,
                DocumentBoxScanOptimizationJob.Status.RUNNING,
            ]
        )
        .filter(
            Q(lease_expires_at__lte=now)
            | Q(
                lease_expires_at__isnull=True,
                heartbeat_at__lte=cutoff,
            )
            | Q(
                lease_expires_at__isnull=True,
                heartbeat_at__isnull=True,
                updated_at__lte=cutoff,
            )
        )
        .order_by("id")
    )
    job_ids = []
    for job_id in recoverable_jobs.values_list("id", flat=True):
        lease_token = uuid.uuid4()
        claimed_job = ClaimDocumentBoxScanOptimizationJob(
            job_id=job_id,
            lease_token=lease_token,
            resume_reason="automatic",
        ).execute()
        if claimed_job is None:
            continue
        process_document_box_scan_optimization_job.delay(
            job_id,
            lease_token_value=str(lease_token),
        )
        job_ids.append(job_id)
    return {"resumed_job_ids": job_ids, "count": len(job_ids)}


@shared_task
def process_document_box_title_refresh_job(
    job_id: int,
    *,
    resume_reason: str = "",
    lease_token_value: str = "",
) -> dict:
    lease_token = uuid.UUID(lease_token_value) if lease_token_value else uuid.uuid4()
    job = ClaimDocumentBoxTitleRefreshJob(
        job_id=job_id,
        lease_token=lease_token,
        resume_reason=resume_reason,
    ).execute()
    if job is None:
        current_job = DocumentBoxTitleRefreshJob.objects.get(id=job_id)
        return _title_refresh_job_result(current_job, claimed=False)

    job = RunDocumentBoxTitleRefreshBatch(
        job=job,
        actor=job.created_by,
        lease_token=lease_token,
    ).execute()
    should_continue = (
        job.status == DocumentBoxTitleRefreshJob.Status.RUNNING
        and job.processed_documents < job.total_documents
    )
    if should_continue:
        process_document_box_title_refresh_job.delay(job.id)
    return _title_refresh_job_result(job, claimed=True)


def _title_refresh_job_result(
    job: DocumentBoxTitleRefreshJob,
    *,
    claimed: bool,
) -> dict:
    return {
        "job_id": job.id,
        "status": job.status,
        "claimed": claimed,
        "processed_documents": job.processed_documents,
        "total_documents": job.total_documents,
        "updated_titles": job.updated_titles,
        "unchanged_titles": job.unchanged_titles,
        "errors": job.errors,
    }


@shared_task
def resume_stale_title_refresh_jobs() -> dict:
    now = timezone.now()
    cutoff = now - timedelta(
        seconds=getattr(
            settings,
            "TITLE_REFRESH_STALE_AFTER_SECONDS",
            120,
        )
    )
    recoverable_jobs = (
        DocumentBoxTitleRefreshJob.objects.filter(
            status__in=[
                DocumentBoxTitleRefreshJob.Status.QUEUED,
                DocumentBoxTitleRefreshJob.Status.RUNNING,
            ]
        )
        .filter(
            Q(lease_expires_at__lte=now)
            | Q(
                lease_expires_at__isnull=True,
                heartbeat_at__lte=cutoff,
            )
            | Q(
                lease_expires_at__isnull=True,
                heartbeat_at__isnull=True,
                updated_at__lte=cutoff,
            )
        )
        .order_by("id")
    )
    job_ids = []
    for job_id in recoverable_jobs.values_list("id", flat=True):
        lease_token = uuid.uuid4()
        claimed_job = ClaimDocumentBoxTitleRefreshJob(
            job_id=job_id,
            lease_token=lease_token,
            resume_reason="automatic",
        ).execute()
        if claimed_job is None:
            continue
        process_document_box_title_refresh_job.delay(
            job_id,
            lease_token_value=str(lease_token),
        )
        job_ids.append(job_id)
    return {"resumed_job_ids": job_ids, "count": len(job_ids)}


@shared_task
def start_document_box_scan_optimization(
    document_space_id: int,
    *,
    include_children: bool = True,
    actor_id: int | None = None,
) -> dict:
    from django.contrib.auth import get_user_model

    document_space = DocumentSpace.objects.select_related("tenant").get(
        id=document_space_id,
    )
    actor = None
    if actor_id is not None:
        actor = get_user_model().objects.filter(id=actor_id).first()
    job = CreateDocumentBoxScanOptimizationJob(
        tenant=document_space.tenant,
        document_space=document_space,
        include_children=include_children,
        actor=actor,
    ).execute()
    process_document_box_scan_optimization_job.delay(job.id)
    return {"job_id": job.id, "status": job.status}


@shared_task
def start_document_box_title_refresh(
    document_space_id: int,
    *,
    include_children: bool = True,
    actor_id: int | None = None,
) -> dict:
    from django.contrib.auth import get_user_model

    document_space = DocumentSpace.objects.select_related("tenant").get(
        id=document_space_id,
    )
    actor = None
    if actor_id is not None:
        actor = get_user_model().objects.filter(id=actor_id).first()
    job = CreateDocumentBoxTitleRefreshJob(
        tenant=document_space.tenant,
        document_space=document_space,
        include_children=include_children,
        actor=actor,
    ).execute()
    process_document_box_title_refresh_job.delay(job.id)
    return {"job_id": job.id, "status": job.status}


@shared_task
def optimize_document_box_scans(
    document_space_id: int,
    *,
    include_children: bool = True,
    actor_id: int | None = None,
) -> dict:
    from django.contrib.auth import get_user_model

    document_space = DocumentSpace.objects.select_related("tenant").get(
        id=document_space_id,
    )
    actor = None
    if actor_id is not None:
        actor = get_user_model().objects.filter(id=actor_id).first()
    result = OptimizeDocumentBoxScans(
        tenant=document_space.tenant,
        document_space=document_space,
        include_children=include_children,
        actor=actor,
    ).execute()
    return {
        "candidates": result.candidates,
        "optimized": result.optimized,
        "skipped": result.skipped,
        "errors": result.errors,
        "bytes_before": result.bytes_before,
        "bytes_after": result.bytes_after,
        "saved_bytes": result.saved_bytes,
    }
