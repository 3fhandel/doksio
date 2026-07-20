from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from doksio.accounts.forms import UserProfileForm
from doksio.accounts.models import Notification, TenantMembership, UserProfile
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.tenancy.models import Tenant


def _create_tenant_user():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    return tenant, user


@pytest.mark.django_db
def test_profile_view_creates_profile_and_renders_shortcuts(client):
    tenant, user = _create_tenant_user()
    client.force_login(user)

    response = client.get(
        reverse("accounts:profile_shortcuts", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert UserProfile.objects.filter(user=user).exists()
    assert "Account" in content
    assert "Tastenkürzel" in content
    assert "Dashboard öffnen" in content
    assert "Benachrichtigungen" in content
    assert "data-shortcut-capture" in content
    assert "data-shortcut-clear" in content
    assert "profile-shortcuts.js" in content


@pytest.mark.django_db
def test_profile_entry_redirects_to_account(client):
    tenant, user = _create_tenant_user()
    client.force_login(user)

    response = client.get(
        reverse("accounts:profile", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 302
    assert response.url == reverse(
        "accounts:profile_account",
        kwargs={"tenant_slug": tenant.slug},
    )


@pytest.mark.django_db
def test_profile_view_saves_account_data(client):
    tenant, user = _create_tenant_user()
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile_account", kwargs={"tenant_slug": tenant.slug}),
        {
            "display_name": "Alice Beispiel",
            "email": "alice@example.test",
        },
    )

    user.refresh_from_db()
    profile = UserProfile.objects.get(user=user)
    assert response.status_code == 302
    assert profile.display_name == "Alice Beispiel"
    assert user.email == "alice@example.test"


@pytest.mark.django_db
def test_profile_account_view_renders_account_fields(client):
    tenant, user = _create_tenant_user()
    client.force_login(user)

    response = client.get(
        reverse("accounts:profile_account", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Anzeigename" in content
    assert "Vorname" not in content
    assert "Nachname" not in content
    assert "Passwort ändern" in content
    assert "Dashboard öffnen" not in content


@pytest.mark.django_db
def test_identity_provider_profile_hides_password_change(client):
    tenant, user = _create_tenant_user()
    UserProfile.objects.create(user=user, oidc_subject="authentik-user-1")
    client.force_login(user)

    response = client.get(
        reverse("accounts:profile_account", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Login über Identity Provider" in content
    assert "lokale Passwortänderung in Doksio ist deshalb deaktiviert" in content
    assert "Der Anzeigename wird über den Identity Provider verwaltet" in content
    assert "Die E-Mail-Adresse wird über den Identity Provider verwaltet" in content
    assert "Passwort ändern" not in content
    assert "Aktuelles Passwort" not in content


@pytest.mark.django_db
def test_profile_view_changes_password(client):
    tenant, user = _create_tenant_user()
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile_account", kwargs={"tenant_slug": tenant.slug}),
        {
            "display_name": "",
            "email": "",
            "current_password": "secret",
            "new_password1": "Stronger-local-pass-42",
            "new_password2": "Stronger-local-pass-42",
        },
    )

    user.refresh_from_db()
    assert response.status_code == 302
    assert user.check_password("Stronger-local-pass-42") is True


@pytest.mark.django_db
def test_identity_provider_profile_does_not_change_local_password(client):
    tenant, user = _create_tenant_user()
    user.email = "alice@example.test"
    user.save(update_fields=["email"])
    UserProfile.objects.create(
        user=user,
        display_name="Alice IdP",
        oidc_subject="authentik-user-1",
    )
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile_account", kwargs={"tenant_slug": tenant.slug}),
        {
            "display_name": "Manipulierter Name",
            "email": "changed@example.test",
            "current_password": "secret",
            "new_password1": "Stronger-local-pass-42",
            "new_password2": "Stronger-local-pass-42",
        },
    )

    user.refresh_from_db()
    profile = UserProfile.objects.get(user=user)
    assert response.status_code == 302
    assert user.email == "alice@example.test"
    assert user.check_password("secret") is True
    assert user.check_password("Stronger-local-pass-42") is False
    assert profile.display_name == "Alice IdP"


@pytest.mark.django_db
def test_profile_view_saves_notifications(client):
    tenant, user = _create_tenant_user()
    UserProfile.objects.create(
        user=user,
        notifications_enabled=True,
        mention_notifications_enabled=True,
    )
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile_notifications", kwargs={"tenant_slug": tenant.slug}),
        {
            "notifications_enabled": "on",
        },
    )

    profile = UserProfile.objects.get(user=user)
    assert response.status_code == 302
    assert profile.notifications_enabled is True
    assert profile.mention_notifications_enabled is False


@pytest.mark.django_db
def test_profile_notifications_view_lists_and_marks_notifications_read(client):
    tenant, user = _create_tenant_user()
    notification = Notification.objects.create(
        tenant=tenant,
        recipient=user,
        notification_type=Notification.Type.WORKFLOW_TASK_CREATED,
        title="Neue Workflow-Aufgabe",
        body="Sachlich prüfen für Rechnung",
        link_url="/t/acme/documents/1/",
    )
    client.force_login(user)

    response = client.get(
        reverse("accounts:profile_notifications", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Posteingang" in content
    assert "1 ungelesen" in content
    assert "Neue Workflow-Aufgabe" in content
    assert "Sachlich prüfen für Rechnung" in content

    response = client.post(
        reverse("accounts:profile_notifications", kwargs={"tenant_slug": tenant.slug}),
        {
            "action": "mark_read",
            "notification_id": notification.id,
        },
    )

    notification.refresh_from_db()
    assert response.status_code == 302
    assert notification.read_at is not None


@pytest.mark.django_db
def test_profile_view_saves_keyboard_shortcuts(client):
    tenant, user = _create_tenant_user()
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile_shortcuts", kwargs={"tenant_slug": tenant.slug}),
        {
            "shortcut_dashboard": "alt+1",
            "shortcut_tasks": "Alt+2",
            "shortcut_documents": "Alt+3",
            "shortcut_search": "Alt+S",
            "shortcut_upload": "Alt+U",
            "shortcut_document_previous": "Alt+ArrowLeft",
            "shortcut_document_next": "Alt+ArrowRight",
            "shortcut_document_back": "Alt+Backspace",
            "shortcut_document_edit_core": "Alt+E",
            "shortcut_workflow_complete": "Alt+Enter",
        },
    )

    profile = UserProfile.objects.get(user=user)
    assert response.status_code == 302
    assert profile.keyboard_shortcuts["dashboard"] == "Alt+1"
    assert profile.keyboard_shortcuts["search"] == "Alt+S"


@pytest.mark.django_db
def test_dashboard_exposes_profile_link_and_shortcut_config(client):
    tenant, user = _create_tenant_user()
    UserProfile.objects.create(
        user=user,
        display_name="Alice Beispiel",
        keyboard_shortcuts={"dashboard": "Alt+D", "upload": "Alt+U"},
    )
    client.force_login(user)

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert reverse("accounts:profile", kwargs={"tenant_slug": tenant.slug}) in content
    assert "app-account-button" in content
    assert "app-account-menu" in content
    assert "Alice Beispiel" in content
    assert reverse("accounts:logout") in content
    assert "doksio-keyboard-shortcuts" in content
    assert "Alt+D" in content
    assert 'data-shortcut-action="dashboard"' in content


@pytest.mark.django_db
def test_user_profile_form_rejects_duplicate_shortcuts():
    user = get_user_model().objects.create_user(username="alice")
    profile = UserProfile.objects.create(user=user)

    form = UserProfileForm(
        {
            "notifications_enabled": "on",
            "shortcut_dashboard": "Alt+1",
            "shortcut_tasks": "Alt+1",
        },
        profile=profile,
    )

    assert form.is_valid() is False
    assert "shortcut_tasks" in form.errors
