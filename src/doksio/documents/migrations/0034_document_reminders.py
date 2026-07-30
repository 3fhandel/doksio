import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0022_document_reminder_notification_type"),
        ("documents", "0033_document_inboxes"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentspace",
            name="reminders_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="DocumentReminder",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("remind_on", models.DateField()),
                ("note", models.CharField(max_length=500)),
                ("notified_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reminders",
                        to="documents.document",
                    ),
                ),
                (
                    "recipient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_reminders",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_reminders",
                        to="tenancy.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["remind_on", "id"],
                "indexes": [
                    models.Index(
                        fields=["completed_at", "notified_at", "remind_on"],
                        name="documents_reminder_due_idx",
                    ),
                    models.Index(
                        fields=["tenant", "recipient", "remind_on"],
                        name="documents_reminder_user_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("completed_at__isnull", True)),
                        fields=("document", "recipient"),
                        name="unique_active_document_reminder_per_user",
                    ),
                ],
            },
        ),
    ]
