from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0023_public_mention_groups"),
        ("workflows", "0011_materialize_child_spaces"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowtemplate",
            name="supervisor_roles",
            field=models.ManyToManyField(
                blank=True,
                related_name="supervised_workflow_templates",
                to="accounts.tenantrole",
            ),
        ),
    ]
