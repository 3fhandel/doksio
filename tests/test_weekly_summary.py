from datetime import date

import pytest
from django.contrib.auth import get_user_model

from doksio.accounts.models import (
    TenantMembership,
    UserProfile,
    WeeklySummaryDelivery,
)
from doksio.accounts.services import EnsureDefaultTenantRoles, SendWeeklySummary
from doksio.ingestion.models import TenantSmtpSettings
from doksio.tenancy.models import Tenant


@pytest.mark.django_db
def test_weekly_summary_is_sent_once_per_tenant_user_and_week(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        email="alice@example.test",
        password="secret",
    )
    TenantMembership.objects.create(tenant=tenant, user=user, role=roles["member"])
    UserProfile.objects.create(
        user=user,
        display_name="Alice Beispiel",
        weekly_summary_enabled=True,
    )
    TenantSmtpSettings.objects.create(
        tenant=tenant,
        host="smtp.example.test",
        from_email="doksio@example.test",
        is_active=True,
    )
    sent_messages = []
    monkeypatch.setattr(
        "doksio.accounts.services.EmailMultiAlternatives.send",
        lambda self: sent_messages.append(self) or 1,
    )

    first_delivery = SendWeeklySummary(
        tenant=tenant,
        recipient=user,
        week_start=date(2026, 11, 23),
    ).execute()
    second_delivery = SendWeeklySummary(
        tenant=tenant,
        recipient=user,
        week_start=date(2026, 11, 23),
    ).execute()

    assert first_delivery is not None
    assert second_delivery is None
    assert len(sent_messages) == 1
    assert sent_messages[0].subject == "Deine Doksio-Wochenübersicht – Acme GmbH"
    assert "Hallo Alice Beispiel" in sent_messages[0].body
    assert "0 offene Workflow-Aufgaben" in sent_messages[0].body
    assert WeeklySummaryDelivery.objects.filter(
        tenant=tenant,
        recipient=user,
        week_start=date(2026, 11, 23),
    ).count() == 1


@pytest.mark.django_db
def test_weekly_summary_respects_disabled_profile_setting(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    user = get_user_model().objects.create_user(
        username="alice",
        email="alice@example.test",
    )
    UserProfile.objects.create(user=user, weekly_summary_enabled=False)
    TenantSmtpSettings.objects.create(
        tenant=tenant,
        host="smtp.example.test",
        from_email="doksio@example.test",
        is_active=True,
    )
    sent_messages = []
    monkeypatch.setattr(
        "doksio.accounts.services.EmailMultiAlternatives.send",
        lambda self: sent_messages.append(self) or 1,
    )

    delivery = SendWeeklySummary(
        tenant=tenant,
        recipient=user,
        week_start=date(2026, 11, 23),
    ).execute()

    assert delivery is None
    assert sent_messages == []
