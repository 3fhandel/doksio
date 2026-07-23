from __future__ import annotations

from django.contrib import admin

from doksio.ingestion.models import (
    EmailAutoReplyRecipient,
    ImportJob,
    ImportSource,
    TenantSmtpSettings,
)


@admin.register(ImportSource)
class ImportSourceAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "tenant",
        "document_space",
        "source_type",
        "target_strategy",
        "is_active",
        "updated_at",
    ]
    list_filter = ["tenant", "source_type", "is_active"]
    search_fields = ["name", "document_space__path", "tenant__name"]


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    list_display = [
        "original_filename",
        "tenant",
        "source",
        "document_space",
        "status",
        "received_at",
    ]
    list_filter = ["tenant", "status", "source"]
    search_fields = ["original_filename", "message", "document__title"]
    readonly_fields = ["received_at", "processed_at", "created_at", "updated_at"]


@admin.register(TenantSmtpSettings)
class TenantSmtpSettingsAdmin(admin.ModelAdmin):
    list_display = ["tenant", "host", "from_email", "is_active", "updated_at"]
    list_filter = ["is_active", "security"]
    search_fields = ["tenant__name", "host", "from_email"]


@admin.register(EmailAutoReplyRecipient)
class EmailAutoReplyRecipientAdmin(admin.ModelAdmin):
    list_display = ["recipient", "tenant", "source", "reply_type", "sent_at"]
    list_filter = ["tenant", "source", "reply_type"]
    search_fields = ["recipient", "subject", "source__name"]
    readonly_fields = ["created_at", "sent_at"]
