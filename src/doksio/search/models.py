from __future__ import annotations

from django.contrib.postgres.search import SearchVectorField
from django.db import models


class DocumentSearchIndex(models.Model):
    """Denormalized search document for fast tenant-scoped lookups."""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_search_indexes",
    )
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="search_index",
    )
    title = models.CharField(max_length=255)
    filenames_text = models.TextField(blank=True)
    tags_text = models.TextField(blank=True)
    comments_text = models.TextField(blank=True)
    ocr_text = models.TextField(blank=True)
    metadata_text = models.TextField(blank=True)
    combined_text = models.TextField(blank=True)
    search_vector = SearchVectorField(blank=True, null=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["document_id"]
        indexes = [
            models.Index(fields=["tenant", "document"]),
            models.Index(fields=["tenant", "updated_at"]),
            models.Index(fields=["tenant", "title"]),
        ]

    def __str__(self) -> str:
        return f"Search index for document {self.document_id}"
