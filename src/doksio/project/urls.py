"""Root URL configuration for Doksio."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import include, path

from doksio.documents.views import index


def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", index, name="index"),
    path("", include("doksio.accounts.urls")),
    path("s/admin/", admin.site.urls),
    path("", include("doksio.documents.urls")),
    path("", include("doksio.ingestion.urls")),
    path("", include("doksio.search.urls")),
    path("", include("doksio.workflows.urls")),
    path("", include("doksio.reports.urls")),
    path("", include("doksio.exports.urls")),
    path("s/health/", health, name="health"),
]
