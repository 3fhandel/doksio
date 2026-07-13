from __future__ import annotations

from django.urls import path

from doksio.workflows import views

app_name = "workflows"

urlpatterns = [
    path(
        "t/<slug:tenant_slug>/settings/workflows/",
        views.workflow_templates,
        name="settings_templates",
    ),
    path(
        "t/<slug:tenant_slug>/settings/workflows/new/",
        views.workflow_template_create,
        name="settings_template_create",
    ),
    path(
        "t/<slug:tenant_slug>/settings/workflows/<int:template_id>/edit/",
        views.workflow_template_edit,
        name="settings_template_edit",
    ),
    path(
        "t/<slug:tenant_slug>/settings/workflows/<int:template_id>/steps/new/",
        views.workflow_step_create,
        name="settings_step_create",
    ),
    path(
        (
            "t/<slug:tenant_slug>/settings/workflows/<int:template_id>/"
            "steps/<int:step_id>/edit/"
        ),
        views.workflow_step_edit,
        name="settings_step_edit",
    ),
]
