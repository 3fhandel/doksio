import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ingestion", "0004_tenantsmtpsettings"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailAutoReplyRecipient",
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
                ("recipient", models.EmailField(max_length=254)),
                (
                    "reply_type",
                    models.CharField(
                        choices=[
                            ("success", "Erfolgreicher Import"),
                            ("unprocessable", "Nicht importierbare Mail"),
                        ],
                        max_length=30,
                    ),
                ),
                ("subject", models.CharField(blank=True, max_length=255)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="auto_reply_recipients",
                        to="ingestion.importsource",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_auto_reply_recipients",
                        to="tenancy.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["-sent_at", "-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["tenant", "source", "reply_type"],
                        name="ingestion_e_tenant__6393c4_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("source", "recipient", "reply_type"),
                        name="unique_email_auto_reply_recipient",
                    ),
                ],
            },
        ),
    ]
