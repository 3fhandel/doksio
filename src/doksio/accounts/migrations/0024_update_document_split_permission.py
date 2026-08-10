from django.db import migrations


def update_permission(apps, schema_editor) -> None:
    permission_model = apps.get_model("accounts", "TenantPermission")
    permission_model.objects.filter(code="documents.split").update(
        label="Dokumente aufteilen und zusammenführen",
        description=(
            "PDF-Dokumente aufteilen oder mehrere PDFs zu einem neuen Dokument "
            "zusammenführen."
        ),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0023_public_mention_groups"),
    ]

    operations = [
        migrations.RunPython(update_permission, migrations.RunPython.noop),
    ]
