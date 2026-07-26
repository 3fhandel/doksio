from django.contrib import admin

from doksio.alarms.models import DocumentAlarm, DocumentAlarmMatch


@admin.register(DocumentAlarm)
class DocumentAlarmAdmin(admin.ModelAdmin):
    list_display = ["name", "tenant", "owner", "is_active", "updated_at"]
    list_filter = ["tenant", "is_active", "notify_in_app", "notify_email"]
    search_fields = ["name", "search_term", "owner__email"]


@admin.register(DocumentAlarmMatch)
class DocumentAlarmMatchAdmin(admin.ModelAdmin):
    list_display = ["alarm", "document", "matched_at"]
    readonly_fields = ["alarm", "document", "matched_at"]
