"""Application services for search indexing and querying."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import connection
from django.db.models import Count, DecimalField, F, Q, QuerySet
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.models import Document, DocumentMetadataField, DocumentSpace
from doksio.documents.policies import filter_documents_for_user, has_tenant_permission
from doksio.search.models import DocumentSearchIndex
from doksio.tenancy.models import Tenant


def _split_terms(query: str) -> list[str]:
    return [term for term in query.strip().split() if term]


def _fulltext_query(term: str) -> Q:
    return (
        Q(search_index__combined_text__icontains=term)
        | Q(title__icontains=term)
    )


def _postgres_search_query(query: str) -> SearchQuery:
    return SearchQuery(query, config="german", search_type="websearch")


def _box_filter(box: DocumentSpace, include_child_boxes: bool) -> Q:
    if include_child_boxes:
        return Q(space__path=box.path) | Q(space__path__startswith=f"{box.path}/")
    return Q(space=box)


def _contains_term(value: str, term: str) -> bool:
    return term.casefold() in value.casefold()


def _text_excerpt(value: str, term: str, word_window: int = 5) -> str:
    words = re.findall(r"\S+", value)
    for index, word in enumerate(words):
        if _contains_term(word, term):
            start = max(0, index - word_window)
            end = min(len(words), index + word_window + 1)
            excerpt = " ".join(words[start:end])
            prefix = "... " if start > 0 else ""
            suffix = " ..." if end < len(words) else ""
            return f"{prefix}{excerpt}{suffix}"
    return value[:220]


def _flatten_metadata(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        values = []
        for key, child_value in value.items():
            values.append(str(key))
            values.extend(_flatten_metadata(child_value))
        return values
    if isinstance(value, list):
        values = []
        for child_value in value:
            values.extend(_flatten_metadata(child_value))
        return values
    return [str(value)]


def _join_search_parts(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def build_search_match(document: Document, query: str) -> dict:
    terms = _split_terms(query)
    if not terms:
        return {}

    search_index = getattr(document, "search_index", None)
    for term in terms:
        if _contains_term(document.title, term):
            return {"source": "Titel", "term": term, "excerpt": document.title}

        if search_index is not None:
            indexed_sources = [
                ("Dateiname", search_index.filenames_text),
                ("Tag", search_index.tags_text),
                ("Metadaten", search_index.metadata_text),
                ("Kommentar", search_index.comments_text),
                ("Volltext", search_index.ocr_text),
            ]
            for source, value in indexed_sources:
                if _contains_term(value, term):
                    return {
                        "source": source,
                        "term": term,
                        "excerpt": _text_excerpt(value, term),
                    }

        for document_file in document.files.all():
            if _contains_term(document_file.original_filename, term):
                return {
                    "source": "Dateiname",
                    "term": term,
                    "excerpt": document_file.original_filename,
                }
            if _contains_term(document_file.sha256, term):
                return {
                    "source": "Checksumme",
                    "term": term,
                    "excerpt": document_file.sha256,
                }

        for assignment in document.tag_assignments.all():
            if _contains_term(assignment.tag.name, term):
                return {"source": "Tag", "term": term, "excerpt": assignment.tag.name}

        for comment in document.comments.all():
            if _contains_term(comment.body, term):
                return {
                    "source": "Kommentar",
                    "term": term,
                    "excerpt": _text_excerpt(comment.body, term),
                }

        for document_file in document.files.all():
            for ocr_job in document_file.ocr_jobs.all():
                if _contains_term(ocr_job.extracted_text, term):
                    return {
                        "source": "Volltext",
                        "term": term,
                        "excerpt": _text_excerpt(ocr_job.extracted_text, term),
                    }
    return {}


@dataclass(frozen=True)
class SearchDocuments:
    tenant: Tenant
    filters: dict
    user: AbstractBaseUser | AnonymousUser | None = None

    def execute(self) -> QuerySet[Document]:
        requested_document_status = self.filters.get("document_status") or "active"
        can_include_deleted = self._can_include_deleted()
        include_deleted = (
            requested_document_status in {"deleted", "all"} and can_include_deleted
        )
        documents = (
            Document.objects.filter(tenant=self.tenant)
            .select_related("space")
            .select_related("search_index")
            .prefetch_related("files", "tag_assignments__tag")
        )
        if self.user is not None:
            documents = filter_documents_for_user(
                documents,
                self.user,
                self.tenant,
                TenantPermissions.DOCUMENTS_VIEW,
                include_deleted=include_deleted,
            )
        elif not include_deleted:
            documents = documents.exclude(status=Document.Status.DELETED)

        if requested_document_status == "deleted" and can_include_deleted:
            documents = documents.filter(status=Document.Status.DELETED)
        elif requested_document_status != "all" or not can_include_deleted:
            documents = documents.filter(status=Document.Status.ACTIVE)

        query = self.filters.get("q", "")
        if query.strip() and connection.vendor == "postgresql":
            search_query = _postgres_search_query(query)
            documents = documents.filter(search_index__search_vector=search_query)
            documents = documents.annotate(
                search_rank=SearchRank(
                    F("search_index__search_vector"),
                    search_query,
                )
            )
        else:
            for term in _split_terms(query):
                documents = documents.filter(_fulltext_query(term))

        tags = self.filters.get("tags")
        if tags:
            for tag in tags:
                documents = documents.filter(tag_assignments__tag=tag)

        document_date_from = self.filters.get("document_date_from")
        if document_date_from:
            documents = documents.filter(document_date__gte=document_date_from)

        document_date_to = self.filters.get("document_date_to")
        if document_date_to:
            documents = documents.filter(document_date__lte=document_date_to)

        box = self.filters.get("box")
        if box:
            documents = documents.filter(
                _box_filter(
                    box=box,
                    include_child_boxes=bool(
                        self.filters.get("include_child_boxes")
                    ),
                )
            )

        documents = self._apply_metadata_filters(documents)

        documents = documents.distinct()
        documents = documents.annotate(
            workflow_total_count=Count("workflow_instances", distinct=True),
            workflow_completed_count=Count(
                "workflow_instances",
                filter=Q(workflow_instances__status="completed"),
                distinct=True,
            ),
            workflow_open_count=Count(
                "workflow_instances",
                filter=Q(workflow_instances__status="running"),
                distinct=True,
            ),
        )
        workflow_status = self.filters.get("workflow_status")
        if workflow_status == "none":
            documents = documents.filter(workflow_total_count=0)
        elif workflow_status == "open":
            documents = documents.filter(workflow_open_count__gt=0)
        elif workflow_status == "completed":
            documents = documents.filter(
                workflow_total_count__gt=0,
                workflow_open_count=0,
            )
        sort = self.filters.get("sort") or "relevance"
        return self._sort(documents, sort=sort)

    def _apply_metadata_filters(
        self,
        documents: QuerySet[Document],
    ) -> QuerySet[Document]:
        for index, metadata_filter in enumerate(
            self.filters.get("metadata_filters") or []
        ):
            field = metadata_filter["field"]
            operator = metadata_filter["operator"]
            value = metadata_filter["value"]
            lookup_base = f"metadata__{field.slug}"

            if field.field_type in {
                DocumentMetadataField.FieldType.TEXT,
                DocumentMetadataField.FieldType.MULTILINE_TEXT,
            }:
                documents = documents.filter(**{f"{lookup_base}__icontains": value})
            elif field.field_type == DocumentMetadataField.FieldType.CHOICE:
                documents = documents.filter(**{lookup_base: value})
            elif field.field_type == DocumentMetadataField.FieldType.BOOLEAN:
                documents = documents.filter(**{lookup_base: value == "true"})
            elif field.field_type == DocumentMetadataField.FieldType.DATE:
                documents = documents.filter(**{f"{lookup_base}__{operator}": value})
            elif field.field_type == DocumentMetadataField.FieldType.NUMBER:
                alias = f"metadata_number_{field.slug}_{index}"
                documents = documents.annotate(
                    **{
                        alias: Cast(
                            KeyTextTransform(field.slug, "metadata"),
                            DecimalField(max_digits=20, decimal_places=6),
                        )
                    }
                ).filter(**{f"{alias}__{operator}": value})
        return documents

    def _can_include_deleted(self) -> bool:
        if self.user is None:
            return True
        return has_tenant_permission(
            self.user,
            self.tenant,
            TenantPermissions.DOCUMENTS_DELETE,
        )

    def _sort(self, documents: QuerySet[Document], sort: str) -> QuerySet[Document]:
        if sort == "created_asc":
            return documents.order_by("created_at", "id")
        if sort == "date_desc":
            return documents.order_by("-document_date", "-created_at", "-id")
        if sort == "date_asc":
            return documents.order_by("document_date", "created_at", "id")
        if sort == "title_asc":
            return documents.order_by("title", "-created_at", "-id")
        if sort == "created_desc":
            return documents.order_by("-created_at", "-id")
        if sort == "relevance" and connection.vendor == "postgresql":
            return documents.order_by("-search_rank", "-created_at", "-id")
        return documents.order_by("-created_at", "-id")


@dataclass(frozen=True)
class RebuildDocumentSearchIndex:
    document: Document

    def execute(self) -> DocumentSearchIndex:
        document = (
            Document.objects.filter(id=self.document.id)
            .select_related("tenant")
            .prefetch_related(
                "files",
                "files__ocr_jobs",
                "tag_assignments__tag",
                "comments",
            )
            .get()
        )
        filenames_text = _join_search_parts(
            *[
                f"{document_file.original_filename} {document_file.sha256}"
                for document_file in document.files.all()
            ]
        )
        ocr_text = _join_search_parts(
            *[
                ocr_job.extracted_text
                for document_file in document.files.all()
                for ocr_job in document_file.ocr_jobs.all()
                if ocr_job.extracted_text
            ]
        )
        tags_text = _join_search_parts(
            *[
                assignment.tag.name
                for assignment in document.tag_assignments.all()
            ]
        )
        comments_text = _join_search_parts(
            *[comment.body for comment in document.comments.all()]
        )
        metadata_text = _join_search_parts(*_flatten_metadata(document.metadata))
        combined_text = _join_search_parts(
            document.title,
            filenames_text,
            tags_text,
            comments_text,
            ocr_text,
            metadata_text,
        )
        search_index, _created = DocumentSearchIndex.objects.update_or_create(
            document=document,
            defaults={
                "tenant": document.tenant,
                "title": document.title,
                "filenames_text": filenames_text,
                "tags_text": tags_text,
                "comments_text": comments_text,
                "ocr_text": ocr_text,
                "metadata_text": metadata_text,
                "combined_text": combined_text,
            },
        )
        self._update_postgres_search_vector(search_index)
        return search_index

    def _update_postgres_search_vector(self, search_index: DocumentSearchIndex) -> None:
        if connection.vendor != "postgresql":
            return

        DocumentSearchIndex.objects.filter(id=search_index.id).update(
            search_vector=(
                SearchVector("title", weight="A", config="german")
                + SearchVector("filenames_text", weight="B", config="german")
                + SearchVector("tags_text", weight="B", config="german")
                + SearchVector("metadata_text", weight="B", config="german")
                + SearchVector("comments_text", weight="C", config="german")
                + SearchVector("ocr_text", weight="D", config="german")
            )
        )


__all__ = [
    "RebuildDocumentSearchIndex",
    "SearchDocuments",
    "build_search_match",
]
