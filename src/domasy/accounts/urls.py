from __future__ import annotations

from django.urls import path

from domasy.accounts import views

app_name = "accounts"

urlpatterns = [
    path("s/", views.system_login, name="system_login"),
    path("s/logout/", views.sign_out, name="logout"),
    path("t/<slug:tenant_slug>/", views.tenant_login, name="tenant_login"),
]
