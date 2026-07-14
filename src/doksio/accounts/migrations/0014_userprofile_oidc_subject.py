from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_userprofile_mention_notifications_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="oidc_subject",
            field=models.CharField(
                blank=True,
                max_length=255,
                null=True,
                unique=True,
            ),
        ),
    ]
