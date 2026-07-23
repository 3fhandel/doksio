from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.documents.models import DocumentBoxTitleRefreshJob, DocumentTitleRule
from doksio.documents.services import (
    CreateDocumentBoxTitleRefreshJob,
    CreateDocumentFromUpload,
    CreateDocumentSpace,
)
from doksio.documents.tasks import process_document_box_title_refresh_job
from doksio.documents.title_rules import (
    resolve_document_title_policy,
    title_from_einvoice_data,
)
from doksio.ocr.models import OcrJob
from doksio.ocr.services import StartOcrForDocumentFile, title_from_ocr_policy
from doksio.tenancy.models import Tenant


@pytest.mark.django_db
def test_title_policy_uses_box_rule_before_tenant_default():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    invoice_space = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
    ).execute()
    personnel_space = CreateDocumentSpace(
        tenant=tenant,
        name="Personal",
    ).execute()
    default_rule = DocumentTitleRule.objects.create(
        tenant=tenant,
        strategy=DocumentTitleRule.Strategy.DISABLED,
    )
    box_rule = DocumentTitleRule.objects.create(
        tenant=tenant,
        document_space=invoice_space,
        strategy=DocumentTitleRule.Strategy.REGEX,
        regex_search=r"Rechnung (\d+)",
        regex_replace=r"RE-\1",
    )

    assert resolve_document_title_policy(invoice_space) == box_rule.as_policy()
    assert resolve_document_title_policy(personnel_space) == default_rule.as_policy()


@pytest.mark.django_db
def test_title_policy_defaults_to_automatic_without_configuration():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Posteingang").execute()

    assert resolve_document_title_policy(space) == {
        "strategy": DocumentTitleRule.Strategy.AUTOMATIC,
        "regex_search": "",
        "regex_replace": "",
        "einvoice_format": ("{seller_name:.12}: {invoice_number}{invoice_date_suffix}"),
        "fallback_strategy": DocumentTitleRule.FallbackStrategy.AUTOMATIC,
    }


def test_title_from_einvoice_data_formats_placeholders():
    title = title_from_einvoice_data(
        {
            "seller_name": "Musterlieferant GmbH",
            "invoice_number": "RE-4711",
            "invoice_date": "20260707",
            "grand_total_amount": "345.10",
            "currency": "EUR",
        },
        (
            "{seller_name:.12}: {invoice_number} vom {invoice_date} "
            "über {grand_total_amount} {currency}"
        ),
    )

    assert title == "Musterliefer: RE-4711 vom 07.07.2026 über 345.10 EUR"


def test_title_from_einvoice_data_requires_used_placeholder_values():
    title = title_from_einvoice_data(
        {"seller_name": "Muster GmbH"},
        "{seller_name}: {invoice_number}",
    )

    assert title is None


def test_title_from_einvoice_data_rejects_unknown_placeholders():
    with pytest.raises(ValueError, match="Unbekannter Platzhalter"):
        title_from_einvoice_data(
            {"seller_name": "Muster GmbH"},
            "{unbekannt}",
        )


def test_einvoice_title_policy_uses_configured_ocr_fallback():
    title = title_from_ocr_policy(
        "Lieferant\nRechnung Nr. 4711\nDanke",
        {
            "strategy": DocumentTitleRule.Strategy.EINVOICE,
            "einvoice_format": "{seller_name}: {invoice_number}",
            "fallback_strategy": DocumentTitleRule.FallbackStrategy.REGEX,
            "regex_search": r"Rechnung Nr\. (\d+)",
            "regex_replace": r"Rechnung \1",
        },
    )

    assert title == "Rechnung 4711"


@pytest.mark.django_db
def test_manual_ocr_restart_uses_current_document_box_rule(monkeypatch):
    monkeypatch.setattr("doksio.ocr.tasks.run_ocr_job.delay", lambda job_id: None)
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    rule = DocumentTitleRule.objects.create(
        tenant=tenant,
        document_space=space,
        strategy=DocumentTitleRule.Strategy.DISABLED,
    )
    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="",
        space=space,
        file_obj=BytesIO(b"Beispieltext"),
        original_filename="beispiel.txt",
        content_type="text/plain",
        auto_start_ocr=False,
        auto_start_workflows=False,
    ).execute()

    job = StartOcrForDocumentFile(document_file=document_file).execute()

    assert job.metadata["title_policy"] == rule.as_policy()


@pytest.mark.django_db
def test_einvoice_rule_falls_back_to_ocr_for_regular_document():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    DocumentTitleRule.objects.create(
        tenant=tenant,
        document_space=space,
        strategy=DocumentTitleRule.Strategy.EINVOICE,
        einvoice_format="{seller_name}: {invoice_number}",
        fallback_strategy=DocumentTitleRule.FallbackStrategy.REGEX,
        regex_search=r"Rechnung Nr\. (\d+)",
        regex_replace=r"Rechnung \1",
    )
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="",
        space=space,
        file_obj=BytesIO(b"Lieferant\nRechnung Nr. 4711\nDanke"),
        original_filename="beispiel.txt",
        content_type="text/plain",
        auto_start_ocr=False,
        auto_start_workflows=False,
    ).execute()

    StartOcrForDocumentFile(
        document_file=document_file,
        run_inline=True,
    ).execute()

    document.refresh_from_db()
    assert document.title == "Rechnung 4711"
    assert document.title_source == document.TitleSource.OCR


@pytest.mark.django_db
def test_title_refresh_job_overwrites_manual_title_and_search_index(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    DocumentTitleRule.objects.create(
        tenant=tenant,
        document_space=space,
        strategy=DocumentTitleRule.Strategy.REGEX,
        regex_search=r"Rechnung Nr\. (\d+)",
        regex_replace=r"Rechnung \1",
    )
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Manuell gesetzter Titel",
        space=space,
        file_obj=BytesIO(b"Beispiel"),
        original_filename="scan-001.txt",
        content_type="text/plain",
        auto_start_ocr=False,
        auto_start_workflows=False,
    ).execute()
    OcrJob.objects.create(
        tenant=tenant,
        document_file=document_file,
        status=OcrJob.Status.SUCCEEDED,
        extracted_text="Lieferant\nRechnung Nr. 4711\nDanke",
    )
    job = CreateDocumentBoxTitleRefreshJob(
        tenant=tenant,
        document_space=space,
        include_children=False,
        batch_size=10,
    ).execute()
    monkeypatch.setattr(
        "doksio.documents.tasks.process_document_box_title_refresh_job.delay",
        lambda job_id: None,
    )

    result = process_document_box_title_refresh_job(job.id)

    document.refresh_from_db()
    job.refresh_from_db()
    document.search_index.refresh_from_db()
    assert result["status"] == DocumentBoxTitleRefreshJob.Status.COMPLETED
    assert document.title == "Rechnung 4711"
    assert document.title_source == document.TitleSource.OCR
    assert document.search_index.title == "Rechnung 4711"
    assert job.processed_documents == 1
    assert job.updated_titles == 1
    assert job.errors == 0


@pytest.mark.django_db
def test_title_refresh_uses_filename_when_rule_is_disabled(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Ablage").execute()
    DocumentTitleRule.objects.create(
        tenant=tenant,
        document_space=space,
        strategy=DocumentTitleRule.Strategy.DISABLED,
    )
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Alter Titel",
        space=space,
        file_obj=BytesIO(b"Beispiel"),
        original_filename="urspruenglicher-dateiname.txt",
        content_type="text/plain",
        auto_start_ocr=False,
        auto_start_workflows=False,
    ).execute()
    job = CreateDocumentBoxTitleRefreshJob(
        tenant=tenant,
        document_space=space,
        include_children=False,
    ).execute()
    monkeypatch.setattr(
        "doksio.documents.tasks.process_document_box_title_refresh_job.delay",
        lambda job_id: None,
    )

    process_document_box_title_refresh_job(job.id)

    document.refresh_from_db()
    assert document.title == "urspruenglicher-dateiname"
    assert document.title_source == document.TitleSource.FILENAME


@pytest.mark.django_db
def test_title_refresh_applies_snapshotted_einvoice_rule(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    rule = DocumentTitleRule.objects.create(
        tenant=tenant,
        document_space=space,
        strategy=DocumentTitleRule.Strategy.EINVOICE,
        einvoice_format="{seller_name:.6} - {invoice_number}",
        fallback_strategy=DocumentTitleRule.FallbackStrategy.DISABLED,
    )
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Alter manueller Titel",
        space=space,
        file_obj=BytesIO(b"Beispiel"),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
        auto_start_workflows=False,
    ).execute()
    document.einvoice_data = {
        "seller_name": "Musterlieferant GmbH",
        "invoice_number": "RE-2026-17",
    }
    document.save(update_fields=["einvoice_data", "updated_at"])
    job = CreateDocumentBoxTitleRefreshJob(
        tenant=tenant,
        document_space=space,
        include_children=False,
    ).execute()
    rule.einvoice_format = "{invoice_number}"
    rule.save(update_fields=["einvoice_format", "updated_at"])
    monkeypatch.setattr(
        "doksio.documents.tasks.process_document_box_title_refresh_job.delay",
        lambda job_id: None,
    )

    process_document_box_title_refresh_job(job.id)

    document.refresh_from_db()
    assert document.title == "Muster - RE-2026-17"
    assert document.title_source == document.TitleSource.EINVOICE


@pytest.mark.django_db
def test_tenant_admin_manages_central_title_rules(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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

    create_url = reverse(
        "documents:settings_title_rule_create",
        kwargs={"tenant_slug": tenant.slug},
    )
    response = client.post(
        create_url,
        {
            "document_space": "",
            "strategy": DocumentTitleRule.Strategy.DISABLED,
            "regex_search": "",
            "regex_replace": "",
        },
    )
    assert response.status_code == 302

    response = client.post(
        create_url,
        {
            "document_space": str(space.id),
            "strategy": DocumentTitleRule.Strategy.REGEX,
            "regex_search": r"Rechnung Nr\. (?P<number>\d+)",
            "regex_replace": r"Rechnung \g<number>",
        },
    )
    assert response.status_code == 302
    assert DocumentTitleRule.objects.filter(tenant=tenant).count() == 2

    response = client.get(
        reverse(
            "documents:settings_title_rules",
            kwargs={"tenant_slug": tenant.slug},
        )
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert "Titelfindung" in content
    assert "Tenant-Standard" in content
    assert space.path in content
    assert r"Rechnung Nr\. (?P&lt;number&gt;\d+)" in content


@pytest.mark.django_db
def test_tenant_admin_can_start_title_refresh_from_maintenance(
    client,
    monkeypatch,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    scheduled_job_ids = []
    monkeypatch.setattr(
        "doksio.documents.tasks.process_document_box_title_refresh_job.delay",
        lambda job_id: scheduled_job_ids.append(job_id),
    )
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:settings_title_refresh",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "space": str(space.id),
            "include_children": "on",
        },
    )

    assert response.status_code == 302
    job = DocumentBoxTitleRefreshJob.objects.get()
    assert job.document_space == space
    assert job.include_children is True
    assert job.created_by == user
    assert scheduled_job_ids == [job.id]

    response = client.get(
        reverse(
            "documents:settings_title_refresh",
            kwargs={"tenant_slug": tenant.slug},
        )
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert "Dokumenttitel neu berechnen" in content
    assert "manuell vergeben wurden" in content
    assert "0 / 0 Dokumente verarbeitet" in content


@pytest.mark.django_db
def test_title_rule_form_rejects_invalid_regex(client):
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

    response = client.post(
        reverse(
            "documents:settings_title_rule_create",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "document_space": "",
            "strategy": DocumentTitleRule.Strategy.REGEX,
            "regex_search": "(",
            "regex_replace": "",
        },
    )

    assert response.status_code == 200
    assert "Ungültiger regulärer Ausdruck" in response.content.decode()
    assert DocumentTitleRule.objects.count() == 0


@pytest.mark.django_db
def test_tenant_admin_can_test_einvoice_title_format(client):
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

    response = client.post(
        reverse(
            "documents:settings_title_einvoice_format_test",
            kwargs={"tenant_slug": tenant.slug},
        ),
        data={
            "einvoice_format": (
                "{seller_name:.12}: {invoice_number} vom {invoice_date}"
            ),
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "title": "Musterliefer: RE-4711 vom 07.07.2026",
    }
