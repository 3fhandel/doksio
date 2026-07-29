from __future__ import annotations

import hashlib
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Min
from django.http import QueryDict
from django.utils import timezone

from doksio.accounts.models import DocumentViewHistory
from doksio.accounts.permissions import TenantPermissions
from doksio.documents.models import Document, DocumentNavigationContext
from doksio.documents.policies import filter_documents_for_user
from doksio.workflows.models import WorkflowTask
from doksio.workflows.policies import filter_workflow_tasks_for_user


def create_document_navigation(
    *,
    request,
    tenant,
    namespace: str,
    total_count: int,
) -> str:
    source_query = request.GET.copy()
    for page_parameter in ("page", "uploads_page", "tasks_page"):
        source_query.pop(page_parameter, None)
    query_string = source_query.urlencode()
    source = f"{namespace}:{request.path}?{query_string}"
    source_key = hashlib.sha256(source.encode()).hexdigest()
    defaults = {
        "namespace": namespace,
        "query_string": query_string,
        "total_count": total_count,
        "document_ids": [],
    }
    context, created = DocumentNavigationContext.objects.get_or_create(
        tenant=tenant,
        user=request.user,
        source_key=source_key,
        defaults=defaults,
    )
    if not created and any(
        (
            context.namespace != namespace,
            context.query_string != query_string,
            context.total_count != total_count,
            bool(context.document_ids),
        )
    ):
        context.namespace = namespace
        context.query_string = query_string
        context.total_count = total_count
        context.document_ids = []
        context.save(
            update_fields=[
                "namespace",
                "query_string",
                "total_count",
                "document_ids",
                "updated_at",
            ]
        )
    if created:
        stale_before = timezone.now() - timedelta(days=7)
        DocumentNavigationContext.objects.filter(
            tenant=tenant,
            user=request.user,
            updated_at__lt=stale_before,
        ).delete()
    return str(context.token)


def navigation_context_from_token(*, token: str, tenant, user):
    if not token:
        return None
    try:
        return DocumentNavigationContext.objects.get(
            token=token,
            tenant=tenant,
            user=user,
        )
    except (DocumentNavigationContext.DoesNotExist, ValidationError, ValueError):
        return None


def document_ids_from_navigation(*, token: str, tenant, user) -> list[int]:
    context = navigation_context_from_token(
        token=token,
        tenant=tenant,
        user=user,
    )
    if context is None:
        return []
    return [
        int(document_id)
        for document_id in context.document_ids
        if isinstance(document_id, int) or str(document_id).isdigit()
    ]


def document_ids_for_navigation_context(*, context, tenant, user):
    query_data = QueryDict(context.query_string)
    if context.namespace in {"documents", "dashboard-documents"}:
        documents = filter_documents_for_user(
            Document.objects.filter(tenant=tenant).order_by("-created_at", "-id"),
            user,
            tenant,
        )
        return documents.values_list("id", flat=True)

    if context.namespace in {"tasks", "dashboard-tasks"}:
        tasks = filter_workflow_tasks_for_user(
            WorkflowTask.objects.filter(
                tenant=tenant,
                status=WorkflowTask.Status.OPEN,
            ),
            user,
            tenant,
        )
        workflow_id = query_data.get("workflow", "").strip()
        if workflow_id.isdigit():
            tasks = tasks.filter(instance__template_id=int(workflow_id))
        return (
            tasks.order_by()
            .values("document_id")
            .annotate(
                first_created_at=Min("created_at"),
                first_task_id=Min("id"),
            )
            .order_by("first_created_at", "first_task_id")
            .values_list("document_id", flat=True)
        )

    if context.namespace == "history":
        accessible_documents = filter_documents_for_user(
            Document.objects.filter(tenant=tenant),
            user,
            tenant,
            TenantPermissions.DOCUMENTS_VIEW,
        )
        return (
            DocumentViewHistory.objects.filter(
                tenant=tenant,
                user=user,
                document__in=accessible_documents,
            )
            .order_by("-last_viewed_at", "-id")
            .values_list("document_id", flat=True)[:20]
        )

    if context.namespace == "search":
        from doksio.search.forms import DocumentSearchForm
        from doksio.search.services import SearchDocuments

        form = DocumentSearchForm(query_data, tenant=tenant, user=user)
        if not form.is_valid():
            return Document.objects.none().values_list("id", flat=True)
        return SearchDocuments(
            tenant=tenant,
            filters=form.cleaned_data,
            user=user,
        ).execute().values_list("id", flat=True)

    return Document.objects.none().values_list("id", flat=True)
