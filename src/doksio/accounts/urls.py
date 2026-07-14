from __future__ import annotations

from django.urls import path

from doksio.accounts import views

app_name = "accounts"

urlpatterns = [
    path("s/", views.system_login, name="system_login"),
    path("s/oidc/login/", views.system_oidc_login, name="system_oidc_login"),
    path(
        "s/oidc/tenant-login/",
        views.tenant_claim_oidc_login,
        name="tenant_claim_oidc_login",
    ),
    path("s/oidc/callback/", views.oidc_callback, name="oidc_callback"),
    path("s/logout/", views.sign_out, name="logout"),
    path("t/<slug:tenant_slug>/", views.tenant_login, name="tenant_login"),
    path(
        "t/<slug:tenant_slug>/oidc/login/",
        views.tenant_oidc_login,
        name="tenant_oidc_login",
    ),
    path(
        "t/<slug:tenant_slug>/password-reset/<uidb64>/<token>/",
        views.tenant_password_reset_confirm,
        name="tenant_password_reset_confirm",
    ),
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
