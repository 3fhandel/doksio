"""Application services for search indexing and querying."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Count, Q, QuerySet

from domasy.accounts.permissions import TenantPermissions
from domasy.documents.models import Document, DocumentSpace
from domasy.documents.policies import filter_documents_for_user
from domasy.tenancy.models import Tenant


def _split_terms(query: str) -> list[str]:
    return [term for term in query.strip().split() if term]


def _fulltext_query(term: str) -> Q:
    return (
        Q(title__icontains=term)
        | Q(files__original_filename__icontains=term)
        | Q(files__sha256__icontains=term)
        | Q(files__ocr_jobs__extracted_text__icontains=term)
        | Q(tag_assignments__tag__name__icontains=term)
        | Q(comments__body__icontains=term)
    )


def _box_filter(box: DocumentSpace, include_child_boxes: bool) -> Q:
    if include_child_boxes:
        return Q(space__path=box.path) | Q(space__path__startswith=f"{box.path}/")
    return Q(space=box)


@dataclass(frozen=True)
class SearchDocuments:
    tenant: Tenant
    filters: dict
    user: AbstractBaseUser | AnonymousUser | None = None

    def execute(self) -> QuerySet[Document]:
        documents = (
            Document.objects.filter(tenant=self.tenant)
            .select_related("space")
            .prefetch_related("files", "tag_assignments__tag")
        )
        if self.user is not None:
            documents = filter_documents_for_user(
                documents,
                self.user,
                self.tenant,
                TenantPermissions.DOCUMENTS_VIEW,
            )

        for term in _split_terms(self.filters.get("q", "")):
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

        ocr_status = self.filters.get("ocr_status")
        if ocr_status == "none":
            documents = documents.annotate(ocr_job_count=Count("files__ocr_jobs"))
            documents = documents.filter(ocr_job_count=0)
        elif ocr_status:
            documents = documents.filter(files__ocr_jobs__status=ocr_status)

        documents = documents.distinct()
        sort = self.filters.get("sort") or "relevance"
        return self._sort(documents, sort=sort)

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
        return documents.order_by("-created_at", "-id")


__all__ = ["SearchDocuments"]
