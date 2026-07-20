from __future__ import annotations

from celery import shared_task

from doksio.documents.models import DocumentSpace
from doksio.documents.services import OptimizeDocumentBoxScans


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
