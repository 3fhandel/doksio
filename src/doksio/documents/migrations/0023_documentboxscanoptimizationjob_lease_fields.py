from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0022_documentboxscanoptimizationjob_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentboxscanoptimizationjob",
            name="heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documentboxscanoptimizationjob",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documentboxscanoptimizationjob",
            name="lease_token",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
    ]
