"""Root URL configuration for DoMaSy."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import include, path

from domasy.documents.views import index


def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", index, name="index"),
    path("", include("domasy.accounts.urls")),
    path("s/admin/", admin.site.urls),
    path("", include("domasy.documents.urls")),
    path("", include("domasy.search.urls")),
    path("", include("domasy.workflows.urls")),
    path("s/health/", health, name="health"),
]
