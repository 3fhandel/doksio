import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("documents", "0035_reusable_metadata_choice_lists")]

    operations = [
        migrations.CreateModel(
            name="DocumentBoxOcrLayoutJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("include_children", models.BooleanField(default=True)),
                ("status", models.CharField(choices=[("queued", "Wartet"), ("running", "Läuft"), ("completed", "Abgeschlossen"), ("failed", "Fehlgeschlagen")], default="queued", max_length=30)),
                ("total_documents", models.PositiveIntegerField(default=0)),
                ("processed_documents", models.PositiveIntegerField(default=0)),
                ("last_document_id", models.PositiveIntegerField(default=0)),
                ("max_document_id", models.PositiveIntegerField(default=0)),
                ("generated", models.PositiveIntegerField(default=0)),
                ("skipped", models.PositiveIntegerField(default=0)),
                ("errors", models.PositiveIntegerField(default=0)),
                ("batch_size", models.PositiveIntegerField(default=5)),
                ("error_message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_ocr_layout_jobs", to=settings.AUTH_USER_MODEL)),
                ("document_space", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ocr_layout_jobs", to="documents.documentspace")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="document_box_ocr_layout_jobs", to="tenancy.tenant")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(model_name="documentboxocrlayoutjob", index=models.Index(fields=["tenant", "status", "-created_at"], name="doc_ocr_layout_status_idx")),
        migrations.AddIndex(model_name="documentboxocrlayoutjob", index=models.Index(fields=["tenant", "document_space", "status"], name="doc_ocr_layout_space_idx")),
    ]
