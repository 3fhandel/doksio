from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0015_document_export_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="workflow_notifications_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
