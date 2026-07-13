from __future__ import annotations

from django.urls import path

from doksio.accounts import views

app_name = "accounts"

urlpatterns = [
    path("s/", views.system_login, name="system_login"),
    path("s/logout/", views.sign_out, name="logout"),
    path("t/<slug:tenant_slug>/", views.tenant_login, name="tenant_login"),
    path("t/<slug:tenant_slug>/profile/", views.profile, name="profile"),
    path(
        "t/<slug:tenant_slug>/profile/account/",
        views.profile_account,
        name="profile_account",
    ),
    path(
        "t/<slug:tenant_slug>/profile/notifications/",
        views.profile_notifications,
        name="profile_notifications",
    ),
    path(
        "t/<slug:tenant_slug>/profile/shortcuts/",
        views.profile_shortcuts,
        name="profile_shortcuts",
    ),
]
