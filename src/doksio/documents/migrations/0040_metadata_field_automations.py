from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0039_remove_documentspace_reminders_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentmetadatafield",
            name="auto_link_matching_values",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="documentmetadatafield",
            name="regex_pattern",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="documentmetadatafield",
            name="regex_replacement",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="documentrelation",
            name="automatic_metadata_field",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="automatic_relations",
                to="documents.documentmetadatafield",
            ),
        ),
    ]
