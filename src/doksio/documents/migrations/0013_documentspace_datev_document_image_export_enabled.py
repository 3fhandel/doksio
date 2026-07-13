from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0012_document_documents_d_tenant__4a07cd_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentspace",
            name="datev_document_image_export_enabled",
            field=models.BooleanField(default=False),
        ),
    ]
