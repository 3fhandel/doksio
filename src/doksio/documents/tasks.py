from __future__ import annotations

import uuid
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from doksio.documents.models import DocumentBoxScanOptimizationJob, DocumentSpace
from doksio.documents.services import (
    ClaimDocumentBoxScanOptimizationJob,
    CreateDocumentBoxScanOptimizationJob,
    OptimizeDocumentBoxScans,
    RunDocumentBoxScanOptimizationBatch,
)


@shared_task
def process_document_box_scan_optimization_job(
    job_id: int,
    *,
    resume_reason: str = "",
    lease_token_value: str = "",
) -> dict:
    lease_token = (
        uuid.UUID(lease_token_value)
        if lease_token_value
        else uuid.uuid4()
    )
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
