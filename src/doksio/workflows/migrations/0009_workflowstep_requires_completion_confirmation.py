from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0008_workflowstep_relation_picker_filters_editable"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowstep",
            name="requires_completion_confirmation",
            field=models.BooleanField(default=False),
        ),
    ]
