from __future__ import annotations

from celery import shared_task

from doksio.ingestion.models import ImportSource
from doksio.ingestion.services import (
    ProcessDueEmailImportSources,
    ProcessEmailImportSource,
)


@shared_task
def process_email_import_source(source_id: int) -> dict:
    source = ImportSource.objects.select_related("tenant", "document_space").get(
        id=source_id
    )
    result = ProcessEmailImportSource(source=source).execute()
    return {
        "checked_messages": result.checked_messages,
        "matched_attachments": result.matched_attachments,
        "ignored_attachments": result.ignored_attachments,
        "imported_documents": result.imported_documents,
        "duplicate_documents": result.duplicate_documents,
        "failed_attachments": result.failed_attachments,
        "unprocessable_messages": result.unprocessable_messages,
        "errors": result.errors,
    }


@shared_task
def process_due_email_import_sources() -> dict:
    result = ProcessDueEmailImportSources().execute()
    return {
        "checked_messages": result.checked_messages,
        "matched_attachments": result.matched_attachments,
        "ignored_attachments": result.ignored_attachments,
        "imported_documents": result.imported_documents,
        "duplicate_documents": result.duplicate_documents,
        "failed_attachments": result.failed_attachments,
        "unprocessable_messages": result.unprocessable_messages,
        "errors": result.errors,
    }
