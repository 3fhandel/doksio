from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from doksio.accounts.models import Notification, TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.documents.forms import DocumentReminderForm
from doksio.documents.models import Document, DocumentReminder, DocumentSpace
from doksio.documents.tasks import dispatch_due_document_reminders
from doksio.tenancy.models import Tenant


def _document_setup():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        email="alice@example.test",
        password="secret",
    )
    TenantMembership.objects.create(tenant=tenant, user=user, role=roles["member"])
    space = DocumentSpace.objects.create(
        tenant=tenant,
        name="Verträge",
        slug="vertraege",
        path="/vertraege",
        reminders_enabled=True,
    )
    document = Document.objects.create(
        tenant=tenant,
        space=space,
        title="Mietvertrag",
        created_by=user,
    )
    return tenant, user, space, document


@pytest.mark.django_db
def test_reminder_form_uses_smart_german_date_input():
    form = DocumentReminderForm({"remind_on": "23.07.2026", "note": "Vertrag kündigen"})

    assert form.is_valid()
    assert form.fields["remind_on"].widget.attrs["data-smart-date"] == "true"
    assert form.cleaned_data["remind_on"].isoformat() == "2026-07-23"


@pytest.mark.django_db
def test_document_detail_creates_and_completes_personal_reminder(client):
    tenant, user, _space, document = _document_setup()
    client.force_login(user)
    detail_url = reverse(
        "documents:detail",
        kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
    )

    response = client.post(
        detail_url,
        {
            "action": "save_reminder",
            "remind_on": "23.07.2026",
            "note": "Vertrag kündigen",
        },
    )

    assert response.status_code == 302
    reminder = DocumentReminder.objects.get(document=document, recipient=user)
    assert reminder.note == "Vertrag kündigen"

    response = client.post(
        detail_url,
        {"action": "complete_reminder", "reminder_id": reminder.id},
    )

    assert response.status_code == 302
    reminder.refresh_from_db()
    assert reminder.completed_at is not None


@pytest.mark.django_db
def test_document_detail_hides_reminder_when_box_feature_is_disabled(client):
    tenant, user, space, document = _document_setup()
    space.reminders_enabled = False
    space.save(update_fields=["reminders_enabled"])
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    assert 'data-bs-target="#documentReminderModal"' not in response.content.decode()


@pytest.mark.django_db
def test_document_detail_opens_reminder_in_modal(client):
    tenant, user, _space, document = _document_setup()
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert 'data-bs-target="#documentReminderModal"' in content
    assert 'id="documentReminderModal"' in content
    assert "Eine Wiedervorlage erinnert dich am gewählten Datum" in content
    assert 'form="documentReminderSaveForm"' in content


@pytest.mark.django_db
def test_due_reminder_creates_notification_only_once():
    tenant, user, _space, document = _document_setup()
    reminder = DocumentReminder.objects.create(
        tenant=tenant,
        document=document,
        recipient=user,
        remind_on=timezone.localdate() - timedelta(days=1),
        note="Vertrag kündigen",
    )

    first_result = dispatch_due_document_reminders()
    second_result = dispatch_due_document_reminders()

    reminder.refresh_from_db()
    notification = Notification.objects.get(
        recipient=user,
        notification_type=Notification.Type.DOCUMENT_REMINDER,
    )
    assert first_result["notified"] == 1
    assert second_result["notified"] == 0
    assert reminder.notified_at is not None
    assert notification.document == document
    assert "Vertrag kündigen" in notification.body


@pytest.mark.django_db
def test_document_lists_show_personal_reminder_indicator(client):
    tenant, user, _space, document = _document_setup()
    DocumentReminder.objects.create(
        tenant=tenant,
        document=document,
        recipient=user,
        remind_on=timezone.localdate() + timedelta(days=2),
        note="Vertrag prüfen",
    )
    client.force_login(user)

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 200
    assert (
        'aria-label="Persönliche Wiedervorlage vorhanden"' in response.content.decode()
    )


@pytest.mark.django_db
def test_profile_reminders_lists_only_current_users_open_reminders(client):
    tenant, user, _space, document = _document_setup()
    other_user = get_user_model().objects.create_user(
        username="bob",
        password="secret",
    )
    own_reminder = DocumentReminder.objects.create(
        tenant=tenant,
        document=document,
        recipient=user,
        remind_on=timezone.localdate(),
        note="Eigener Termin",
    )
    DocumentReminder.objects.create(
        tenant=tenant,
        document=document,
        recipient=other_user,
        remind_on=timezone.localdate(),
        note="Fremder Termin",
    )
    client.force_login(user)

    url = reverse("accounts:profile_reminders", kwargs={"tenant_slug": tenant.slug})
    response = client.get(url)

    content = response.content.decode()
    assert response.status_code == 200
    assert "Eigener Termin" in content
    assert "Fremder Termin" not in content
    assert "Fällig" in content
    assert (
        f'href="{url}"' in content
        and "list-group-item list-group-item-action active" in content
    )

    response = client.post(
        url,
        {"action": "complete", "reminder_id": own_reminder.id},
    )

    assert response.status_code == 302
    own_reminder.refresh_from_db()
    assert own_reminder.completed_at is not None
