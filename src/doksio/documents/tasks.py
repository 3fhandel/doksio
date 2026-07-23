from __future__ import annotations

from celery import shared_task

from doksio.documents.models import DocumentBoxScanOptimizationJob, DocumentSpace
from doksio.documents.services import (
    CreateDocumentBoxScanOptimizationJob,
    OptimizeDocumentBoxScans,
    RunDocumentBoxScanOptimizationBatch,
)


@shared_task
def process_document_box_scan_optimization_job(job_id: int) -> dict:
    job = DocumentBoxScanOptimizationJob.objects.select_related(
        "tenant",
        "document_space",
        "created_by",
    ).get(id=job_id)
    job = RunDocumentBoxScanOptimizationBatch(
        job=job,
        actor=job.created_by,
    ).execute()
    should_continue = (
        job.status == DocumentBoxScanOptimizationJob.Status.RUNNING
        and job.processed_documents < job.total_documents
    )
    if should_continue:
        process_document_box_scan_optimization_job.delay(job.id)
    return {
        "job_id": job.id,
        "status": job.status,
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
