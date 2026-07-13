from __future__ import annotations

from django.urls import path

from doksio.reports import views

app_name = "reports"

urlpatterns = [
    path("t/<slug:tenant_slug>/reports/", views.reports_overview, name="overview"),
]

