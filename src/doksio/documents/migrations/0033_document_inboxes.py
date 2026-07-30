import django.db.models.deletion
from django.db import migrations, models


PERMISSIONS = [
    ("inboxes.view", "Posteingänge anzeigen", 10),
    ("inboxes.process", "Posteingänge bearbeiten", 20),
    ("inboxes.access_all", "Auf alle Posteingänge zugreifen", 30),
    ("inboxes.manage", "Posteingänge verwalten", 40),
]


def create_inbox_permissions(apps, schema_editor):
    TenantPermission = apps.get_model("accounts", "TenantPermission")
    TenantRole = apps.get_model("accounts", "TenantRole")
    permissions = {}
    for code, label, sort_order in PERMISSIONS:
        permission, _created = TenantPermission.objects.get_or_create(
            code=code,
            defaults={
                "label": label,
                "category": "Posteingänge",
                "sort_order": sort_order,
            },
        )
        permissions[code] = permission

    for role in TenantRole.objects.all().prefetch_related("permissions"):
        existing_codes = {
            permission.code for permission in role.permissions.all()
        }
        if role.slug == "admin":
            role.permissions.add(*permissions.values())
        elif "documents.batch_import" in existing_codes:
            role.permissions.add(
                permissions["inboxes.view"],
                permissions["inboxes.process"],
                permissions["inboxes.access_all"],
            )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0021_documentviewhistory"),
        ("documents", "0032_documentspace_advanced_review_assist_enabled_and_more"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentInbox",
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
                ("name", models.CharField(max_length=160)),
                ("slug", models.SlugField(max_length=80)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "access_roles",
                    models.ManyToManyField(
                        blank=True,
                        related_name="document_inboxes",
                        to="accounts.tenantrole",
                    ),
                ),
                (
                    "allowed_target_spaces",
                    models.ManyToManyField(
                        blank=True,
                        related_name="target_for_document_inboxes",
                        to="documents.documentspace",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_inboxes",
                        to="tenancy.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["name", "id"],
                "indexes": [
                    models.Index(
                        fields=["tenant", "is_active", "name"],
                        name="documents_d_tenant__d345c2_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant", "slug"),
                        name="unique_document_inbox_slug_per_tenant",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="documentimportbatch",
            name="inbox",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="batches",
                to="documents.documentinbox",
            ),
        ),
        migrations.RunPython(
            create_inbox_permissions,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
