from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0017_documentfile_preview_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentfile",
            name="viewer_settings",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
