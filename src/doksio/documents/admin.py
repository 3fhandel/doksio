from __future__ import annotations

from django.contrib import admin

from doksio.documents.models import (
    Document,
    DocumentBoxScanOptimizationJob,
    DocumentComment,
    DocumentFile,
    DocumentMetadataField,
    DocumentSpace,
    DocumentTag,
    DocumentTagAssignment,
)


@admin.register(DocumentSpace)
class DocumentSpaceAdmin(admin.ModelAdmin):
    list_display = ["path", "tenant", "parent", "space_kind", "is_active"]
    list_filter = ["tenant", "space_kind", "is_active"]
    search_fields = ["name", "slug", "path"]
    readonly_fields = ["path", "created_at", "updated_at"]


class DocumentFileInline(admin.TabularInline):
    model = DocumentFile
    extra = 0
    can_delete = False
    readonly_fields = [
        "tenant",
        "file_kind",
        "version",
        "storage_key",
        "original_filename",
        "content_type",
        "byte_size",
        "sha256",
        "derivative_of",
        "created_by",
        "created_at",
    ]
    fields = readonly_fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ["title", "tenant", "space", "status", "deleted_at", "created_at"]
    list_filter = ["tenant", "space", "status"]
    search_fields = ["title"]
    readonly_fields = ["deleted_at", "deleted_by", "deleted_reason"]
    inlines = [DocumentFileInline]


@admin.register(DocumentMetadataField)
class DocumentMetadataFieldAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "slug",
        "tenant",
        "space",
        "field_type",
        "allow_custom_choices",
        "einvoice_source",
        "is_required",
        "is_active",
    ]
    list_filter = [
        "tenant",
        "space",
        "field_type",
        "allow_custom_choices",
        "einvoice_source",
        "is_required",
        "is_active",
    ]
    search_fields = ["name", "slug", "space__path"]


@admin.register(DocumentTag)
class DocumentTagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "tenant", "created_at"]
    list_filter = ["tenant"]
    search_fields = ["name", "slug"]


@admin.register(DocumentTagAssignment)
class DocumentTagAssignmentAdmin(admin.ModelAdmin):
    list_display = ["document", "tag", "tenant", "created_by", "created_at"]
    list_filter = ["tenant", "tag"]
    search_fields = ["document__title", "tag__name"]


@admin.register(DocumentComment)
class DocumentCommentAdmin(admin.ModelAdmin):
    list_display = ["document", "tenant", "created_by", "created_at"]
    list_filter = ["tenant", "created_at"]
    search_fields = ["document__title", "body"]
    readonly_fields = ["tenant", "document", "body", "created_by", "created_at"]


@admin.register(DocumentFile)
class DocumentFileAdmin(admin.ModelAdmin):
    list_display = [
        "document",
        "tenant",
        "file_kind",
        "version",
        "original_filename",
        "byte_size",
        "created_at",
    ]
    list_filter = ["tenant", "file_kind", "content_type"]
    search_fields = ["document__title", "original_filename", "sha256", "storage_key"]
    readonly_fields = [
        "tenant",
        "document",
        "file_kind",
        "version",
        "storage_key",
        "original_filename",
        "content_type",
        "byte_size",
        "sha256",
        "derivative_of",
        "created_by",
        "created_at",
    ]


@admin.register(DocumentBoxScanOptimizationJob)
class DocumentBoxScanOptimizationJobAdmin(admin.ModelAdmin):
    list_display = [
        "document_space",
        "tenant",
        "status",
        "processed_documents",
        "total_documents",
        "optimized",
        "errors",
        "created_at",
    ]
    list_filter = ["tenant", "status", "include_children"]
    search_fields = ["document_space__path", "error_message"]
    readonly_fields = [
        "tenant",
        "document_space",
        "include_children",
        "status",
        "total_documents",
        "processed_documents",
        "last_document_id",
        "max_document_id",
        "candidates",
        "optimized",
        "skipped",
        "errors",
        "bytes_before",
        "bytes_after",
        "batch_size",
        "error_message",
        "created_by",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request) -> bool:
        return False
