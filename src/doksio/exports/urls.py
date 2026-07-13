from __future__ import annotations

from django.urls import path

from doksio.exports import views

app_name = "exports"

urlpatterns = [
    path(
        "t/<slug:tenant_slug>/exports/datev-belegbilder/",
        views.document_image_export,
        name="document_images",
    ),
    path(
        "t/<slug:tenant_slug>/exports/runs/<int:export_run_id>/download/",
        views.export_run_download,
        name="run_download",
    ),
]
