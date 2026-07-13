from __future__ import annotations

from django.db import migrations


def add_reports_permission(apps, schema_editor) -> None:
    permission_model = apps.get_model("accounts", "TenantPermission")
    role_model = apps.get_model("accounts", "TenantRole")
    permission, _created = permission_model.objects.update_or_create(
        code="reports.view",
        defaults={
            "label": "Auswertungen anzeigen",
            "category": "Auswertungen",
            "description": (
                "Controlling-Auswertungen zu Dokumenten und Workflows ansehen."
            ),
            "sort_order": 85,
        },
    )
    for role in role_model.objects.filter(slug="admin", is_system_role=True):
        role.permissions.add(permission)


def remove_reports_permission(apps, schema_editor) -> None:
    permission_model = apps.get_model("accounts", "TenantPermission")
    permission_model.objects.filter(code="reports.view").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_notification"),
    ]

    operations = [
        migrations.RunPython(add_reports_permission, remove_reports_permission),
    ]

