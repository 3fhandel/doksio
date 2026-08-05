from django.db import migrations, models


def copy_trigger_spaces(apps, schema_editor):
    WorkflowTemplate = apps.get_model("workflows", "WorkflowTemplate")
    for template in WorkflowTemplate.objects.exclude(
        trigger_document_space_id=None
    ).iterator():
        template.document_spaces.add(template.trigger_document_space_id)


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0015_documentcomment_mentioned_users"),
        ("workflows", "0009_workflowstep_requires_completion_confirmation"),
    ]

    operations = [
        migrations.RenameField(
            model_name="workflowtemplate",
            old_name="trigger_include_child_spaces",
            new_name="include_child_spaces",
        ),
        migrations.AddField(
            model_name="workflowtemplate",
            name="document_spaces",
            field=models.ManyToManyField(
                blank=True,
                related_name="workflow_scope_templates",
                to="documents.documentspace",
            ),
        ),
        migrations.RunPython(copy_trigger_spaces, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="workflowtemplate",
            name="workflows_w_tenant__476195_idx",
        ),
        migrations.RemoveField(
            model_name="workflowtemplate",
            name="trigger_document_space",
        ),
        migrations.AlterField(
            model_name="workflowtemplate",
            name="document_spaces",
            field=models.ManyToManyField(
                blank=True,
                related_name="workflow_templates",
                to="documents.documentspace",
            ),
        ),
    ]
