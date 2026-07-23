from __future__ import annotations

from datetime import date

import pytest

from doksio.documents.forms import DocumentCoreMetadataForm, DocumentMetadataForm
from doksio.documents.models import DocumentMetadataField
from doksio.documents.services import CreateDocumentMetadataField, CreateDocumentSpace
from doksio.exports.forms import DocumentImageExportForm
from doksio.search.forms import DocumentSearchForm
from doksio.tenancy.models import Tenant


def _assert_smart_date_widget(field):
    attrs = field.widget.attrs
    assert field.widget.input_type == "text"
    assert attrs["data-smart-date"] == "true"
    assert attrs["placeholder"] == "TT.MM.JJJJ"
    assert field.widget.format == "%d.%m.%Y"
    assert "%d.%m.%Y" in field.input_formats


@pytest.mark.django_db
def test_document_core_date_uses_smart_date_widget():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")

    form = DocumentCoreMetadataForm(tenant=tenant)

    _assert_smart_date_widget(form.fields["document_date"])


@pytest.mark.django_db
def test_document_metadata_date_uses_smart_date_widget():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    metadata_field = CreateDocumentMetadataField(
        tenant=tenant,
        space=space,
        name="Leistungsdatum",
        slug="leistungsdatum",
        field_type=DocumentMetadataField.FieldType.DATE,
    ).execute()

    form = DocumentMetadataForm(metadata_fields=[metadata_field], metadata={})

    _assert_smart_date_widget(form.fields["metadata_leistungsdatum"])


@pytest.mark.django_db
def test_search_date_filters_use_smart_date_widgets():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")

    form = DocumentSearchForm(tenant=tenant)

    _assert_smart_date_widget(form.fields["document_date_from"])
    _assert_smart_date_widget(form.fields["document_date_to"])


@pytest.mark.django_db
def test_search_date_filters_accept_german_date_input():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")

    form = DocumentSearchForm(
        data={
            "document_date_from": "23.07.2026",
            "document_date_to": "2026-07-24",
        },
        tenant=tenant,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["document_date_from"] == date(2026, 7, 23)
    assert form.cleaned_data["document_date_to"] == date(2026, 7, 24)


@pytest.mark.django_db
def test_export_date_filters_use_smart_date_widgets(django_user_model):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    user = django_user_model.objects.create_user(username="admin")

    form = DocumentImageExportForm(tenant=tenant, user=user)

    _assert_smart_date_widget(form.fields["document_date_from"])
    _assert_smart_date_widget(form.fields["document_date_to"])
