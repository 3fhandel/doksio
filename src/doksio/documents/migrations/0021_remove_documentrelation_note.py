from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0020_documentrelation"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="documentrelation",
            name="note",
        ),
    ]
