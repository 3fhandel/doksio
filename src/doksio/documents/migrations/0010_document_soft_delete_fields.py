import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0009_documentmetadatafield_einvoice_source"),
    ]

    operations = [
        migrations.AlterField(
            model_name="document",
            name="status",
            field=models.CharField(
                choices=[("active", "Aktiv"), ("deleted", "Gelöscht")],
                default="active",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="deleted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deleted_documents",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="deleted_reason",
            field=models.TextField(blank=True),
        ),
    ]
