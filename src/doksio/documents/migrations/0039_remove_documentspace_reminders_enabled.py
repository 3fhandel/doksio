from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0038_comment_mentioned_roles"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="documentspace",
            name="reminders_enabled",
        ),
    ]
