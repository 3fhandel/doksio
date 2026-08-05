from django.db import migrations


def materialize_child_spaces(apps, schema_editor):
    WorkflowTemplate = apps.get_model("workflows", "WorkflowTemplate")
    DocumentSpace = apps.get_model("documents", "DocumentSpace")

    for template in WorkflowTemplate.objects.filter(
        include_child_spaces=True
    ).prefetch_related("document_spaces"):
        selected_spaces = list(template.document_spaces.all())
        if not selected_spaces:
            continue
        descendant_ids = set()
        for space in selected_spaces:
            descendant_ids.update(
                DocumentSpace.objects.filter(
                    tenant_id=template.tenant_id,
                    path__startswith=f"{space.path.rstrip('/')}/",
                ).values_list("id", flat=True)
            )
        if descendant_ids:
            template.document_spaces.add(*descendant_ids)


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0010_workflow_document_spaces"),
    ]

    operations = [
        migrations.RunPython(materialize_child_spaces, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="workflowtemplate",
            name="include_child_spaces",
        ),
    ]
