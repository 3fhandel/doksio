from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.tenancy.models import Tenant


@pytest.mark.django_db
def test_help_overview_requires_tenant_login(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    url = reverse("helpcenter:overview", kwargs={"tenant_slug": tenant.slug})

    response = client.get(url)

    assert response.status_code == 302
    assert reverse(
        "accounts:tenant_login",
        kwargs={"tenant_slug": tenant.slug},
    ) in response.url


@pytest.mark.django_db
def test_help_overview_shows_user_topics_and_hides_restricted_topics(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["viewer"],
    )
    client.force_login(user)

    response = client.get(
        reverse("helpcenter:overview", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Erste Schritte" in content
    assert "Dokumente finden" in content
    assert "Aufgaben und Workflows" in content
    assert "Was ist ein Workflow?" in content
    assert "Supervisor-Übersicht" in content
    assert "Supervisor-Aufgaben erscheinen nicht" in content
    assert "Workflow, Schritt und Aufgabe" in content
    assert "Wiedervorlagen" in content
    assert "Profil &gt; Wiedervorlagen" in content
    assert "Ein entfernter Wert bleibt am Dokument" in content
    assert "springt die Vorschau direkt zu dieser Seite" in content
    assert "zwischen Fundstellen wechseln" in content
    assert "automatisch in die Dokumentensuche übernommen" in content
    assert "Cmd+F auf dem Mac" in content
    assert "Dokumentenbox direkt neben dem Suchbegriff" in content
    assert "Tags kannst du dort durchsuchen" in content
    assert "Bei Auswahllisten-Metadaten kannst du direkt" in content
    assert "bleibt die aktuelle Belegvorschau rechts sichtbar" in content
    assert "direkt im Arbeitsbereich" in content
    assert "Beim Lösen einer bestehenden Verknüpfung" in content
    assert "Schnellvorschau der Originaldatei" in content
    assert "Ein Klick auf ein Seitenthumbnail" in content
    assert "zentralen Titelfindung der jeweiligen Zielbox" in content
    assert "Scans mit vorhandenen OCR-Suchmarkierungen" in content
    assert "wechselt zwischen Dokumenten der geöffneten Liste" in content
    assert 'id="administration"' not in content
    assert 'id="stapelimport"' not in content
    assert (
        'class="app-sidebar-link active" '
        f'href="{reverse("helpcenter:overview", kwargs={"tenant_slug": tenant.slug})}"'
        in content
    )


@pytest.mark.django_db
def test_help_overview_shows_administration_to_tenant_admin(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="admin",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["admin"],
    )
    client.force_login(user)

    response = client.get(
        reverse("helpcenter:overview", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert 'id="administration"' in content
    assert 'id="stapelimport"' in content
    assert "Workflow-Supervisor" in content
    assert "erscheinen dadurch nicht unter „Meine Aufgaben“" in content
    assert "Zugriff auf alle Dokumentenboxen nicht automatisch" in content
    assert "aktuell zugeordneten Benutzer" in content
    assert "Metadatenfelder löschen" in content
    assert "in ein kompatibles Feld desselben Typs übertragen" in content


@pytest.mark.django_db
def test_contextual_help_matches_current_page(client):
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
    client.force_login(user)

    response = client.get(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert 'data-bs-target="#contextualHelp"' in content
    assert 'id="contextualHelpLabel">Dokumente hochladen</h2>' in content
    assert "Dateien können ausgewählt oder per Drag-and-drop" in content
    assert (
        reverse("helpcenter:overview", kwargs={"tenant_slug": tenant.slug})
        + "#dokumente-hochladen"
        in content
    )
