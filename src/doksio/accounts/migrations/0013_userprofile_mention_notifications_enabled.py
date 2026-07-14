from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_rename_accounts_no_documen_a51d99_idx_accounts_no_documen_691434_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="mention_notifications_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
