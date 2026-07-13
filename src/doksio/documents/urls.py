from __future__ import annotations

from django.urls import path

from doksio.documents import views

app_name = "documents"

urlpatterns = [
    path("s/dashboard/", views.dashboard_redirect, name="dashboard_redirect"),
    path("t/<slug:tenant_slug>/dashboard/", views.dashboard, name="dashboard"),
    path("t/<slug:tenant_slug>/tasks/", views.task_list, name="tasks"),
    path("t/<slug:tenant_slug>/documents/", views.document_list, name="list"),
    path("t/<slug:tenant_slug>/logs/", views.audit_log, name="audit_log"),
    path(
        "t/<slug:tenant_slug>/documents/upload/",
        views.document_upload,
        name="upload",
    ),
    path(
        "t/<slug:tenant_slug>/documents/<int:document_id>/",
        views.document_detail,
        name="detail",
    ),
    path(
        "t/<slug:tenant_slug>/documents/<int:document_id>/core/edit/",
        views.document_core_metadata_edit,
        name="core_metadata_edit",
    ),
    path(
        "t/<slug:tenant_slug>/documents/<int:document_id>/delete/",
        views.document_delete,
        name="delete",
    ),
    path(
        "t/<slug:tenant_slug>/documents/files/<int:file_id>/download/",
        views.document_file_download,
        name="download",
    ),
    path(
        "t/<slug:tenant_slug>/settings/",
        views.tenant_settings_overview,
        name="settings_overview",
    ),
    path(
        "t/<slug:tenant_slug>/settings/users/",
        views.tenant_settings_members,
        name="settings_members",
    ),
    path(
        "t/<slug:tenant_slug>/settings/users/new/",
        views.tenant_settings_member_create,
        name="settings_member_create",
    ),
    path(
        "t/<slug:tenant_slug>/settings/users/<int:membership_id>/edit/",
        views.tenant_settings_member_edit,
        name="settings_member_edit",
    ),
    path(
        "t/<slug:tenant_slug>/settings/users/<int:membership_id>/send-password-reset/",
        views.tenant_settings_member_send_password_reset,
        name="settings_member_send_password_reset",
    ),
    path(
        "t/<slug:tenant_slug>/settings/roles/",
        views.tenant_settings_roles,
        name="settings_roles",
    ),
    path(
        "t/<slug:tenant_slug>/settings/roles/new/",
        views.tenant_settings_role_create,
        name="settings_role_create",
    ),
    path(
        "t/<slug:tenant_slug>/settings/roles/<int:role_id>/edit/",
        views.tenant_settings_role_edit,
        name="settings_role_edit",
    ),
    path(
        "t/<slug:tenant_slug>/settings/document-boxes/",
        views.tenant_settings_document_boxes,
        name="settings_document_boxes",
    ),
    path(
        "t/<slug:tenant_slug>/settings/document-boxes/new/",
        views.tenant_settings_document_box_create,
        name="settings_document_box_create",
    ),
    path(
        "t/<slug:tenant_slug>/settings/document-boxes/<int:box_id>/edit/",
        views.tenant_settings_document_box_edit,
        name="settings_document_box_edit",
    ),
    path(
        "t/<slug:tenant_slug>/settings/document-boxes/<int:box_id>/delete/",
        views.tenant_settings_document_box_delete,
        name="settings_document_box_delete",
    ),
    path(
        "t/<slug:tenant_slug>/settings/document-boxes/<int:box_id>/metadata-fields/new/",
        views.tenant_settings_metadata_field_create,
        name="settings_metadata_field_create",
    ),
    path(
        "t/<slug:tenant_slug>/settings/import/",
        views.tenant_settings_import_sources,
        name="settings_import_sources",
    ),
    path(
        "t/<slug:tenant_slug>/settings/smtp/",
        views.tenant_settings_smtp,
        name="settings_smtp",
    ),
    path(
        "t/<slug:tenant_slug>/settings/import/new/",
        views.tenant_settings_import_source_create,
        name="settings_import_source_create",
    ),
    path(
        "t/<slug:tenant_slug>/settings/import/<int:source_id>/edit/",
        views.tenant_settings_import_source_edit,
        name="settings_import_source_edit",
    ),
    path(
        "t/<slug:tenant_slug>/settings/import/<int:source_id>/script/",
        views.tenant_settings_import_source_script,
        name="settings_import_source_script",
    ),
    path(
        (
            "t/<slug:tenant_slug>/settings/document-boxes/<int:box_id>/"
            "metadata-fields/<int:field_id>/edit/"
        ),
        views.tenant_settings_metadata_field_edit,
        name="settings_metadata_field_edit",
    ),
]
