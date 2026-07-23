from __future__ import annotations

from django.contrib import admin

from doksio.exports.models import ExportRun, ExportRunItem


class ExportRunItemInline(admin.TabularInline):
    model = ExportRunItem
    extra = 0
    readonly_fields = [
        "tenant",
        "document",
        "document_file",
        "status",
        "exported_filename",
        "message",
        "created_at",
    ]
    can_delete = False


@admin.register(ExportRun)
class ExportRunAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "tenant",
        "export_type",
        "status",
        "processed_count",
        "total_count",
        "exported_count",
        "warning_count",
        "created_at",
    ]
    list_filter = ["export_type", "status", "tenant"]
    search_fields = ["filename"]
    readonly_fields = [
        "storage_key",
        "byte_size",
        "sha256",
        "processed_count",
        "total_count",
        "created_at",
        "updated_at",
        "completed_at",
    ]
    inlines = [ExportRunItemInline]


@admin.register(ExportRunItem)
class ExportRunItemAdmin(admin.ModelAdmin):
    list_display = ["id", "tenant", "export_run", "document", "status"]
    list_filter = ["status", "tenant"]
    search_fields = ["exported_filename", "message"]
