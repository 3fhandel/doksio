from django.urls import path

from doksio.helpcenter import views

app_name = "helpcenter"

urlpatterns = [
    path("t/<slug:tenant_slug>/help/", views.help_overview, name="overview"),
]

