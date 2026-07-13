from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0010_document_soft_delete_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentmetadatafield",
            name="allow_custom_choices",
            field=models.BooleanField(default=False),
        ),
    ]
