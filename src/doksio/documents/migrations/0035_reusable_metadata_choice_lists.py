import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify


def migrate_field_choices(apps, schema_editor):
    ChoiceList = apps.get_model("documents", "DocumentMetadataChoiceList")
    ChoiceItem = apps.get_model("documents", "DocumentMetadataChoiceItem")
    MetadataField = apps.get_model("documents", "DocumentMetadataField")

    for field in MetadataField.objects.filter(field_type="choice").iterator():
        base_slug = (
            slugify(f"{field.space_id}-{field.slug}")[:70] or f"liste-{field.id}"
        )
        slug = base_slug
        suffix = 2
        while ChoiceList.objects.filter(tenant_id=field.tenant_id, slug=slug).exists():
            slug = f"{base_slug[:74]}-{suffix}"
            suffix += 1
        choice_list = ChoiceList.objects.create(
            tenant_id=field.tenant_id,
            name=field.name,
            slug=slug,
        )
        for index, value in enumerate(field.choices or []):
            ChoiceItem.objects.create(
                choice_list=choice_list,
                value=value,
                label=value,
                sort_order=index * 10,
            )
        field.choice_list_id = choice_list.id
        field.save(update_fields=["choice_list"])


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0034_document_reminders"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentMetadataChoiceList",
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
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(max_length=80)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="metadata_choice_lists",
                        to="tenancy.tenant",
                    ),
                ),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.CreateModel(
            name="DocumentMetadataChoiceItem",
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
                ("value", models.CharField(max_length=255)),
                ("label", models.CharField(max_length=255)),
                ("sort_order", models.PositiveIntegerField(default=100)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "choice_list",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="documents.documentmetadatachoicelist",
                    ),
                ),
            ],
            options={"ordering": ["sort_order", "id"]},
        ),
        migrations.AddField(
            model_name="documentmetadatafield",
            name="choice_list",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="metadata_fields",
                to="documents.documentmetadatachoicelist",
            ),
        ),
        migrations.AddConstraint(
            model_name="documentmetadatachoicelist",
            constraint=models.UniqueConstraint(
                fields=("tenant", "slug"),
                name="unique_tenant_metadata_choice_list_slug",
            ),
        ),
        migrations.AddConstraint(
            model_name="documentmetadatachoiceitem",
            constraint=models.UniqueConstraint(
                fields=("choice_list", "value"),
                name="unique_metadata_choice_item_value",
            ),
        ),
        migrations.AddIndex(
            model_name="documentmetadatachoiceitem",
            index=models.Index(
                fields=["choice_list", "is_active", "sort_order"],
                name="documents_choice_item_idx",
            ),
        ),
        migrations.RunPython(migrate_field_choices, migrations.RunPython.noop),
    ]
