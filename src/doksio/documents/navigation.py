from __future__ import annotations

import hashlib
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from doksio.documents.models import DocumentNavigationContext


def create_document_navigation(
    *,
    request,
    tenant,
    document_ids,
    namespace: str,
) -> str:
    source_query = request.GET.copy()
    for page_parameter in ("page", "uploads_page", "tasks_page"):
        source_query.pop(page_parameter, None)
    source = f"{namespace}:{request.path}?{source_query.urlencode()}"
    source_key = hashlib.sha256(source.encode()).hexdigest()
    ids = list(dict.fromkeys(int(document_id) for document_id in document_ids))
    context, created = DocumentNavigationContext.objects.get_or_create(
        tenant=tenant,
        user=request.user,
        source_key=source_key,
        defaults={"document_ids": ids},
    )
    if not created and context.document_ids != ids:
        context.document_ids = ids
        context.save(update_fields=["document_ids", "updated_at"])
    if created:
        stale_before = timezone.now() - timedelta(days=7)
        DocumentNavigationContext.objects.filter(
            tenant=tenant,
            user=request.user,
            updated_at__lt=stale_before,
        ).delete()
    return str(context.token)


def document_ids_from_navigation(*, token: str, tenant, user) -> list[int]:
    if not token:
        return []
    try:
        context = DocumentNavigationContext.objects.get(
            token=token,
            tenant=tenant,
            user=user,
        )
    except (DocumentNavigationContext.DoesNotExist, ValidationError, ValueError):
        return []
    return [
        int(document_id)
        for document_id in context.document_ids
        if isinstance(document_id, int) or str(document_id).isdigit()
    ]
