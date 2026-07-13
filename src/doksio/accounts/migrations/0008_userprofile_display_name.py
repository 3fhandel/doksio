from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_document_delete_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="display_name",
            field=models.CharField(blank=True, max_length=150),
        ),
    ]
