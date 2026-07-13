import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0001_initial"),
        ("ingestion", "0003_importsource_target_strategy"),
    ]

    operations = [
        migrations.CreateModel(
            name="TenantSmtpSettings",
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
                ("host", models.CharField(blank=True, max_length=255)),
                ("port", models.PositiveIntegerField(default=587)),
                (
                    "security",
                    models.CharField(
                        choices=[
                            ("ssl", "SSL/TLS"),
                            ("starttls", "STARTTLS"),
                            ("none", "Keine Verschlüsselung"),
                        ],
                        default="starttls",
                        max_length=20,
                    ),
                ),
                ("username", models.CharField(blank=True, max_length=255)),
                ("password", models.CharField(blank=True, max_length=255)),
                ("from_email", models.EmailField(blank=True, max_length=254)),
                ("from_name", models.CharField(blank=True, max_length=160)),
                ("is_active", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "tenant",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="smtp_settings",
                        to="tenancy.tenant",
                    ),
                ),
            ],
            options={
                "verbose_name": "Tenant SMTP setting",
                "verbose_name_plural": "Tenant SMTP settings",
            },
        ),
    ]
