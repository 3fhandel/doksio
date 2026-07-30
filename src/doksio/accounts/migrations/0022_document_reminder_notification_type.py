from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0021_documentviewhistory"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="notification_type",
            field=models.CharField(
                choices=[
                    ("workflow_started", "Workflow gestartet"),
                    ("workflow_task_created", "Workflow-Aufgabe erstellt"),
                    ("document_comment_mention", "Kommentar-Erwähnung"),
                    ("import_failed", "Importfehler"),
                    ("document_alarm", "Dokumentenalarm"),
                    ("document_reminder", "Wiedervorlage"),
                ],
                max_length=80,
            ),
        ),
    ]
