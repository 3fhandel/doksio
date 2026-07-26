from django.urls import path

from doksio.alarms import views

app_name = "alarms"

urlpatterns = [
    path("t/<slug:tenant_slug>/alarms/", views.alarm_list, name="list"),
    path("t/<slug:tenant_slug>/alarms/new/", views.alarm_create, name="create"),
    path(
        "t/<slug:tenant_slug>/alarms/<int:alarm_id>/",
        views.alarm_update,
        name="update",
    ),
    path(
        "t/<slug:tenant_slug>/alarms/<int:alarm_id>/delete/",
        views.alarm_delete,
        name="delete",
    ),
]
