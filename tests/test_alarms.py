from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from doksio.accounts.models import Notification, TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.alarms.models import DocumentAlarm, DocumentAlarmMatch
from doksio.alarms.services import EvaluateDocumentAlarms
from doksio.documents.models import Document
from doksio.documents.services import CreateDocumentSpace
from doksio.search.models import DocumentSearchIndex
from doksio.tenancy.models import Tenant


def _tenant_user():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        email="alice@example.test",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["viewer"],
    )
    return tenant, user


@pytest.mark.django_db
def test_document_alarm_matches_search_term_and_child_box_only_once():
    tenant, user = _tenant_user()
    parent = CreateDocumentSpace(tenant=tenant, name="Eingang").execute()
    child = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        parent=parent,
    ).execute()
    document = Document.objects.create(
        tenant=tenant,
        space=child,
        title="Neue Rechnung",
        created_by=user,
    )
    DocumentSearchIndex.objects.create(
        tenant=tenant,
        document=document,
        title=document.title,
        ocr_text="Dieser Beleg enthält den gesuchten Wert XYZ.",
        combined_text="Neue Rechnung Dieser Beleg enthält den gesuchten Wert XYZ.",
    )
    alarm = DocumentAlarm.objects.create(
        tenant=tenant,
        owner=user,
        name="XYZ in Eingang",
        search_term="XYZ",
        document_space=parent,
        include_child_spaces=True,
        notify_in_app=True,
    )

    assert EvaluateDocumentAlarms(document=document).execute() == 1
    assert EvaluateDocumentAlarms(document=document).execute() == 0
    assert DocumentAlarmMatch.objects.filter(
        alarm=alarm,
        document=document,
    ).count() == 1
    notification = Notification.objects.get(
        notification_type=Notification.Type.DOCUMENT_ALARM,
        document=document,
        recipient=user,
    )
    assert alarm.name in notification.title
    assert document.title in notification.body


@pytest.mark.django_db
def test_document_alarm_does_not_match_another_box():
    tenant, user = _tenant_user()
    watched = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    other = CreateDocumentSpace(tenant=tenant, name="Personal").execute()
    document = Document.objects.create(
        tenant=tenant,
        space=other,
        title="XYZ",
        created_by=user,
    )
    DocumentSearchIndex.objects.create(
        tenant=tenant,
        document=document,
        title=document.title,
        combined_text="XYZ",
    )
    DocumentAlarm.objects.create(
        tenant=tenant,
        owner=user,
        name="Nur Rechnungen",
        search_term="XYZ",
        document_space=watched,
    )

    assert EvaluateDocumentAlarms(document=document).execute() == 0
    assert not DocumentAlarmMatch.objects.exists()


@pytest.mark.django_db
def test_document_alarm_uses_its_own_notification_channels(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    tenant, user = _tenant_user()
    space = CreateDocumentSpace(tenant=tenant, name="Eingang").execute()
    document = Document.objects.create(
        tenant=tenant,
        space=space,
        title="Wichtiger Beleg",
        created_by=user,
    )
    DocumentSearchIndex.objects.create(
        tenant=tenant,
        document=document,
        title=document.title,
        combined_text=document.title,
    )
    DocumentAlarm.objects.create(
        tenant=tenant,
        owner=user,
        name="E-Mail-Alarm",
        search_term="Wichtiger",
        notify_in_app=False,
        notify_email=True,
    )
    sent_emails = []
    monkeypatch.setattr(
        "doksio.accounts.services._send_notification_email",
        lambda **kwargs: sent_emails.append(kwargs),
    )

    with django_capture_on_commit_callbacks(execute=True):
        assert EvaluateDocumentAlarms(document=document).execute() == 1

    assert not Notification.objects.exists()
    assert len(sent_emails) == 1
    assert sent_emails[0]["recipient"] == user


@pytest.mark.django_db
def test_user_can_manage_only_own_alarms(client):
    tenant, user = _tenant_user()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    other_user = get_user_model().objects.create_user(
        username="bob",
        password="secret",
    )
    alarm = DocumentAlarm.objects.create(
        tenant=tenant,
        owner=other_user,
        name="Fremder Alarm",
    )
    client.force_login(user)

    list_url = reverse("alarms:list", kwargs={"tenant_slug": tenant.slug})
    response = client.get(list_url)
    assert response.status_code == 200
    assert "Fremder Alarm" not in response.content.decode()
    assert "Alarme" in response.content.decode()

    create_response = client.post(
        reverse("alarms:create", kwargs={"tenant_slug": tenant.slug}),
        {
            "name": "Mein Alarm",
            "search_term": "XYZ",
            "document_space": space.id,
            "include_child_spaces": "on",
            "notify_in_app": "on",
            "is_active": "on",
        },
    )
    assert create_response.status_code == 302
    created_alarm = DocumentAlarm.objects.get(owner=user)
    assert created_alarm.name == "Mein Alarm"
    assert created_alarm.document_space == space

    forbidden_response = client.get(
        reverse(
            "alarms:update",
            kwargs={"tenant_slug": tenant.slug, "alarm_id": alarm.id},
        )
    )
    assert forbidden_response.status_code == 404

    delete_response = client.post(
        reverse(
            "alarms:delete",
            kwargs={"tenant_slug": tenant.slug, "alarm_id": created_alarm.id},
        )
    )
    assert delete_response.status_code == 302
    assert not DocumentAlarm.objects.filter(id=created_alarm.id).exists()


@pytest.mark.django_db
def test_alarm_requires_at_least_one_notification_channel(client):
    tenant, user = _tenant_user()
    client.force_login(user)

    response = client.post(
        reverse("alarms:create", kwargs={"tenant_slug": tenant.slug}),
        {
            "name": "Stiller Alarm",
            "search_term": "XYZ",
            "is_active": "on",
        },
    )

    assert response.status_code == 200
    assert "Mindestens ein Benachrichtigungskanal" in response.content.decode()
    assert not DocumentAlarm.objects.exists()
