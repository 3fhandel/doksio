from __future__ import annotations

from django.urls import path

from doksio.ingestion import views

app_name = "ingestion"

urlpatterns = [
    path(
        "t/<slug:tenant_slug>/api/v1/import/<int:source_id>/",
        views.http_import,
        name="http_import",
    ),
]
