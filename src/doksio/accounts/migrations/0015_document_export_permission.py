from django.db import migrations


def add_document_export_permission(apps, schema_editor):
    tenant_permission = apps.get_model("accounts", "TenantPermission")
    tenant_role = apps.get_model("accounts", "TenantRole")

    permission, _created = tenant_permission.objects.update_or_create(
        code="documents.export",
        defaults={
            "label": "Dokumente exportieren",
            "category": "Dokumente",
            "description": (
                "Exportpakete erzeugen und gespeicherte Exporte herunterladen."
            ),
            "sort_order": 37,
        },
    )
    for role in tenant_role.objects.filter(slug="admin", is_system_role=True):
        role.permissions.add(permission)


def remove_document_export_permission(apps, schema_editor):
    tenant_permission = apps.get_model("accounts", "TenantPermission")
    tenant_permission.objects.filter(code="documents.export").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0014_userprofile_oidc_subject"),
    ]

    operations = [
        migrations.RunPython(
            add_document_export_permission,
            reverse_code=remove_document_export_permission,
        ),
    ]
