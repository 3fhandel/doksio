from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from doksio.alarms.forms import DocumentAlarmForm
from doksio.alarms.models import DocumentAlarm
from doksio.audit.services import RecordAuditEvent
from doksio.pagination import paginate_queryset
from doksio.tenancy.services import get_tenant_for_user


def _tenant(request, tenant_slug):
    if not request.user.is_authenticated:
        return None
    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied
    return tenant


def alarm_list(request, tenant_slug):
    tenant = _tenant(request, tenant_slug)
    if tenant is None:
        return redirect("accounts:tenant_login", tenant_slug=tenant_slug)
    alarms = (
        DocumentAlarm.objects.filter(tenant=tenant, owner=request.user)
        .select_related("document_space")
        .prefetch_related("tags")
        .order_by("name", "id")
    )
    alarms_page_obj = paginate_queryset(
        request,
        alarms,
        page_param="page",
        per_page=25,
    )
    return render(
        request,
        "alarms/alarm_list.html",
        {
            "tenant": tenant,
            "alarms": alarms_page_obj.object_list,
            "alarms_page_obj": alarms_page_obj,
        },
    )


def alarm_create(request, tenant_slug):
    tenant = _tenant(request, tenant_slug)
    if tenant is None:
        return redirect("accounts:tenant_login", tenant_slug=tenant_slug)
    form = DocumentAlarmForm(
        request.POST or None,
        tenant=tenant,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        alarm = form.save(commit=False)
        alarm.tenant = tenant
        alarm.owner = request.user
        alarm.save()
        form.save_m2m()
        RecordAuditEvent(
            tenant=tenant,
            actor=request.user,
            event_type="document_alarm.created",
            object_type="alarms.DocumentAlarm",
            object_id=str(alarm.id),
            data={"name": alarm.name},
        ).execute()
        messages.success(request, "Alarm wurde angelegt.")
        return redirect("alarms:list", tenant_slug=tenant.slug)
    return render(
        request,
        "alarms/alarm_form.html",
        {"tenant": tenant, "form": form, "alarm": None},
    )


def alarm_update(request, tenant_slug, alarm_id):
    tenant = _tenant(request, tenant_slug)
    if tenant is None:
        return redirect("accounts:tenant_login", tenant_slug=tenant_slug)
    alarm = get_object_or_404(
        DocumentAlarm,
        tenant=tenant,
        owner=request.user,
        id=alarm_id,
    )
    form = DocumentAlarmForm(
        request.POST or None,
        instance=alarm,
        tenant=tenant,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        RecordAuditEvent(
            tenant=tenant,
            actor=request.user,
            event_type="document_alarm.updated",
            object_type="alarms.DocumentAlarm",
            object_id=str(alarm.id),
            data={"name": alarm.name},
        ).execute()
        messages.success(request, "Alarm wurde aktualisiert.")
        return redirect("alarms:list", tenant_slug=tenant.slug)
    return render(
        request,
        "alarms/alarm_form.html",
        {"tenant": tenant, "form": form, "alarm": alarm},
    )


def alarm_delete(request, tenant_slug, alarm_id):
    tenant = _tenant(request, tenant_slug)
    if tenant is None:
        return redirect("accounts:tenant_login", tenant_slug=tenant_slug)
    alarm = get_object_or_404(
        DocumentAlarm,
        tenant=tenant,
        owner=request.user,
        id=alarm_id,
    )
    if request.method == "POST":
        alarm_id = alarm.id
        alarm_name = alarm.name
        alarm.delete()
        RecordAuditEvent(
            tenant=tenant,
            actor=request.user,
            event_type="document_alarm.deleted",
            object_type="alarms.DocumentAlarm",
            object_id=str(alarm_id),
            data={"name": alarm_name},
        ).execute()
        messages.success(request, "Alarm wurde gelöscht.")
        return redirect("alarms:list", tenant_slug=tenant.slug)
    return render(
        request,
        "alarms/alarm_delete.html",
        {"tenant": tenant, "alarm": alarm},
    )
