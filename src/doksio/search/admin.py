from __future__ import annotations

from django.contrib import admin

from doksio.search.models import DocumentSearchIndex


@admin.register(DocumentSearchIndex)
class DocumentSearchIndexAdmin(admin.ModelAdmin):
    list_display = ("document", "tenant", "updated_at")
    list_filter = ("tenant",)
    search_fields = ("document__title", "combined_text")
    readonly_fields = (
        "tenant",
        "document",
        "title",
        "filenames_text",
        "tags_text",
        "comments_text",
        "ocr_text",
        "metadata_text",
        "combined_text",
        "updated_at",
    )

