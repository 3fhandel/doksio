from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0008_userprofile_display_name"),
        ("documents", "0010_document_soft_delete_fields"),
        ("tenancy", "0001_initial"),
        ("workflows", "0003_remove_workflowstep_requires_comment_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Notification",
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
                (
                    "notification_type",
                    models.CharField(
                        choices=[
                            (
                                "workflow_task_created",
                                "Workflow-Aufgabe erstellt",
                            )
                        ],
                        max_length=80,
                    ),
                ),
                ("title", models.CharField(max_length=180)),
                ("body", models.TextField(blank=True)),
                ("link_url", models.CharField(blank=True, max_length=500)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to="documents.document",
                    ),
                ),
                (
                    "recipient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="doksio_notifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to="tenancy.tenant",
                    ),
                ),
                (
                    "workflow_task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to="workflows.workflowtask",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="notification",
            index=models.Index(
                fields=["tenant", "recipient", "read_at"],
                name="accounts_no_tenant__2b6358_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="notification",
            index=models.Index(
                fields=["recipient", "created_at"],
                name="accounts_no_recipie_59193a_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="notification",
            index=models.Index(
                fields=["workflow_task", "recipient"],
                name="accounts_no_workflo_ecf0d0_idx",
            ),
        ),
    ]
