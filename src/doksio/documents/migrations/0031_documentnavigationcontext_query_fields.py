from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0030_documentofficeconversionjob"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentnavigationcontext",
            name="namespace",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="documentnavigationcontext",
            name="query_string",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="documentnavigationcontext",
            name="total_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
