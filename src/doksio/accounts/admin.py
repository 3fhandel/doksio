from __future__ import annotations

from django.contrib import admin

from doksio.accounts.models import (
    Notification,
    TenantMembership,
    TenantPermission,
    TenantRole,
    UserProfile,
)


@admin.register(TenantPermission)
class TenantPermissionAdmin(admin.ModelAdmin):
    list_display = ["code", "label", "category", "sort_order"]
    list_filter = ["category"]
    search_fields = ["code", "label", "description"]


@admin.register(TenantRole)
class TenantRoleAdmin(admin.ModelAdmin):
    list_display = ["name", "tenant", "slug", "is_system_role", "is_active"]
    list_filter = ["tenant", "is_system_role", "is_active"]
    search_fields = ["name", "slug", "tenant__name"]
    filter_horizontal = ["permissions"]


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "tenant", "role", "is_active", "created_at"]
    list_filter = ["tenant", "role", "is_active"]
    search_fields = ["user__username", "user__email", "tenant__name", "tenant__slug"]


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "notifications_enabled", "updated_at"]
    search_fields = ["user__username", "user__email"]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "tenant", "recipient", "notification_type", "read_at"]
    list_filter = ["tenant", "notification_type", "read_at"]
    search_fields = ["title", "body", "recipient__username", "recipient__email"]
    readonly_fields = ["created_at"]
