from __future__ import annotations

from django.urls import path

from domasy.search import views

app_name = "search"

urlpatterns = [
    path("t/<slug:tenant_slug>/search/", views.document_search, name="documents"),
]
