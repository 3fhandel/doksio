from django.db import migrations


def add_permission(apps, schema_editor) -> None:
    permission_model = apps.get_model("accounts", "TenantPermission")
    role_model = apps.get_model("accounts", "TenantRole")
    permission, _created = permission_model.objects.update_or_create(
        code="documents.batch_import",
        defaults={
            "label": "Stapelimporte durchführen",
            "category": "Dokumente",
            "description": (
                "Mehrere Dokumente in einem Import-Stapel hochladen, prüfen "
                "und Dokumentenboxen zuordnen."
            ),
            "sort_order": 25,
        },
    )
    for role in role_model.objects.filter(slug__in=["admin", "member"]):
        role.permissions.add(permission)


def remove_permission(apps, schema_editor) -> None:
    permission_model = apps.get_model("accounts", "TenantPermission")
    permission_model.objects.filter(code="documents.batch_import").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0016_userprofile_workflow_notifications_enabled"),
    ]

    operations = [
        migrations.RunPython(add_permission, remove_permission),
    ]
