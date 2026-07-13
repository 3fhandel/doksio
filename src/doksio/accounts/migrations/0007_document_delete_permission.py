from django.db import migrations


def add_document_delete_permission(apps, schema_editor):
    tenant_permission = apps.get_model("accounts", "TenantPermission")
    tenant_role = apps.get_model("accounts", "TenantRole")
    permission, _created = tenant_permission.objects.update_or_create(
        code="documents.delete",
        defaults={
            "label": "Dokumente löschen",
            "category": "Dokumente",
            "description": (
                "Dokumente logisch löschen und laufende Workflows abbrechen."
            ),
            "sort_order": 35,
        },
    )
    for role in tenant_role.objects.filter(slug="admin", is_system_role=True):
        role.permissions.add(permission)


def remove_document_delete_permission(apps, schema_editor):
    tenant_permission = apps.get_model("accounts", "TenantPermission")
    tenant_permission.objects.filter(code="documents.delete").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_userprofile"),
    ]

    operations = [
        migrations.RunPython(
            add_document_delete_permission,
            reverse_code=remove_document_delete_permission,
        ),
    ]
