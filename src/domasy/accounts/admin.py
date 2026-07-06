from __future__ import annotations

from django.contrib import admin

from domasy.accounts.models import TenantMembership, TenantPermission, TenantRole


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
