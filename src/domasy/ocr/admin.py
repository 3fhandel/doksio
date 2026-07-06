from __future__ import annotations

from django.contrib import admin

from domasy.ocr.models import OcrJob


@admin.register(OcrJob)
class OcrJobAdmin(admin.ModelAdmin):
    list_display = [
        "document_file",
        "tenant",
        "status",
        "engine",
        "language",
        "created_at",
    ]
    list_filter = ["tenant", "status", "engine", "language"]
    search_fields = [
        "document_file__document__title",
        "document_file__original_filename",
        "extracted_text",
        "error_message",
    ]
    readonly_fields = [
        "tenant",
        "document_file",
        "status",
        "engine",
        "language",
        "extracted_text",
        "error_message",
        "created_by",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    ]
