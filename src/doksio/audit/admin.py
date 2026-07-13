from __future__ import annotations

from django.contrib import admin

from doksio.audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "tenant",
        "event_type",
        "object_type",
        "object_id",
        "actor",
    ]
    list_filter = ["event_type", "object_type", "tenant"]
    search_fields = ["event_type", "object_type", "object_id"]
    readonly_fields = [
        "tenant",
        "actor",
        "event_type",
        "object_type",
        "object_id",
        "data",
        "created_at",
    ]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
