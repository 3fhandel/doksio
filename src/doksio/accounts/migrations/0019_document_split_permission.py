from django.db import migrations


def add_permission(apps, schema_editor) -> None:
    permission_model = apps.get_model("accounts", "TenantPermission")
    role_model = apps.get_model("accounts", "TenantRole")
    permission, _created = permission_model.objects.update_or_create(
        code="documents.split",
        defaults={
            "label": "Dokumente aufteilen",
            "category": "Dokumente",
            "description": (
                "Mehrseitige PDF-Dokumente in mehrere neue Dokumente aufteilen."
            ),
            "sort_order": 27,
        },
    )
    for role in role_model.objects.filter(slug__in=["admin", "member"]):
        role.permissions.add(permission)


def remove_permission(apps, schema_editor) -> None:
    permission_model = apps.get_model("accounts", "TenantPermission")
    permission_model.objects.filter(code="documents.split").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0018_userprofile_notification_preferences_and_more"),
    ]

    operations = [
        migrations.RunPython(add_permission, remove_permission),
    ]
