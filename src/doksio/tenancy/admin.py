from __future__ import annotations

from django.contrib import admin
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html

from doksio.accounts.models import TenantMembership, TenantRole
from doksio.documents.models import DocumentSpace
from doksio.tenancy.models import Tenant
from doksio.tenancy.services import ProvisionTenantDefaults


class TenantRoleInline(admin.TabularInline):
    model = TenantRole
    extra = 0
    can_delete = False
    fields = [
        "name",
        "slug",
        "is_system_role",
        "is_active",
        "can_access_all_document_spaces",
    ]
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class TenantMembershipInline(admin.TabularInline):
    model = TenantMembership
    extra = 0
    can_delete = False
    fields = ["user", "role", "is_active", "created_at"]
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class DocumentSpaceInline(admin.TabularInline):
    model = DocumentSpace
    extra = 0
    can_delete = False
    fields = ["path", "name", "parent", "space_kind", "is_active"]
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "slug",
        "is_active",
        "tenant_entry",
        "member_count",
        "role_count",
        "document_box_count",
        "document_count",
        "created_at",
    ]
    list_filter = ["is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    readonly_fields = [
        "created_at",
        "updated_at",
        "tenant_entry",
        "member_count",
        "role_count",
        "document_box_count",
        "document_count",
    ]
    fieldsets = [
        (
            "Tenant",
            {
                "fields": [
                    "name",
                    "slug",
                    "is_active",
                    "tenant_entry",
                ]
            },
        ),
        (
            "Status",
            {
                "fields": [
                    "member_count",
                    "role_count",
                    "document_box_count",
                    "document_count",
                ]
            },
        ),
        (
            "System",
            {
                "classes": ["collapse"],
                "fields": ["created_at", "updated_at"],
            },
        ),
    ]
    inlines = [TenantRoleInline, DocumentSpaceInline, TenantMembershipInline]
    actions = ["provision_default_structure", "activate_tenants", "deactivate_tenants"]

    def get_inlines(self, request, obj=None):
        if obj is None:
            return []
        return super().get_inlines(request, obj)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _member_count=Count("memberships", distinct=True),
                _role_count=Count("roles", distinct=True),
                _document_box_count=Count("document_spaces", distinct=True),
                _document_count=Count("documents", distinct=True),
            )
        )

    def save_model(self, request, obj, form, change) -> None:
        super().save_model(request, obj, form, change)
        if not change:
            ProvisionTenantDefaults(tenant=obj).execute()
            self.message_user(
                request,
                "Standard-Rollen und Dokumentenboxen wurden angelegt.",
            )

    @admin.display(description="Tenant-URL")
    def tenant_entry(self, obj: Tenant):
        if not obj.pk:
            return "-"
        url = reverse("documents:dashboard", kwargs={"tenant_slug": obj.slug})
        return format_html('<a href="{}">/t/{}/dashboard/</a>', url, obj.slug)

    @admin.display(description="Benutzer", ordering="_member_count")
    def member_count(self, obj: Tenant) -> int:
        return getattr(obj, "_member_count", obj.memberships.count())

    @admin.display(description="Rollen", ordering="_role_count")
    def role_count(self, obj: Tenant) -> int:
        return getattr(obj, "_role_count", obj.roles.count())

    @admin.display(description="Dokumentenboxen", ordering="_document_box_count")
    def document_box_count(self, obj: Tenant) -> int:
        return getattr(obj, "_document_box_count", obj.document_spaces.count())

    @admin.display(description="Dokumente", ordering="_document_count")
    def document_count(self, obj: Tenant) -> int:
        return getattr(obj, "_document_count", obj.documents.count())

    @admin.action(description="Standardstruktur anlegen/auffrischen")
    def provision_default_structure(self, request, queryset) -> None:
        for tenant in queryset:
            ProvisionTenantDefaults(tenant=tenant).execute()
        self.message_user(
            request,
            f"Standardstruktur für {queryset.count()} Tenant(s) angelegt/aufgefrischt.",
        )

    @admin.action(description="Ausgewählte Tenants aktivieren")
    def activate_tenants(self, request, queryset) -> None:
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} Tenant(s) aktiviert.")

    @admin.action(description="Ausgewählte Tenants deaktivieren")
    def deactivate_tenants(self, request, queryset) -> None:
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} Tenant(s) deaktiviert.")
