from __future__ import annotations

from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage

from doksio.audit.models import AuditEvent
from doksio.documents.models import Document, DocumentFile
from doksio.documents.services import CreateDocumentSpace
from doksio.storage.services import StoreImmutableFile
from doksio.tenancy.models import Tenant


@pytest.mark.django_db
def test_store_immutable_file_creates_document_file_and_audit_event():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document = Document.objects.create(
        tenant=tenant,
        space=space,
        title="Invoice 4711",
    )

    document_file = StoreImmutableFile(
        tenant=tenant,
        document=document,
        file_obj=BytesIO(b"hello invoice"),
        original_filename="../Invoice 4711.pdf",
        content_type="application/pdf",
    ).execute()

    assert document_file.tenant == tenant
    assert document_file.document == document
    assert document_file.file_kind == DocumentFile.Kind.ORIGINAL
    assert document_file.version == 1
    assert document_file.original_filename == "Invoice_4711.pdf"
    assert document_file.content_type == "application/pdf"
    assert document_file.byte_size == 13
    assert (
        document_file.sha256
        == "6b24be62ea18de2cf5ff07c5a52d929b831d0b6402cab42f9aba3f9f571ece8f"
    )
    assert default_storage.exists(document_file.storage_key)

    audit_event = AuditEvent.objects.get()
    assert audit_event.tenant == tenant
    assert audit_event.event_type == "document_file.stored"
    assert audit_event.object_id == str(document_file.id)
    assert audit_event.data["sha256"] == document_file.sha256


@pytest.mark.django_db
def test_store_immutable_file_increments_versions_per_document_and_kind():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document = Document.objects.create(
        tenant=tenant,
        space=space,
        title="Invoice 4711",
    )

    first_file = StoreImmutableFile(
        tenant=tenant,
        document=document,
        file_obj=BytesIO(b"first"),
        original_filename="invoice.pdf",
    ).execute()
    second_file = StoreImmutableFile(
        tenant=tenant,
        document=document,
        file_obj=BytesIO(b"second"),
        original_filename="invoice-corrected.pdf",
    ).execute()

    assert first_file.version == 1
    assert second_file.version == 2
    assert first_file.storage_key != second_file.storage_key


@pytest.mark.django_db
def test_store_immutable_file_rejects_cross_tenant_document():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    other_tenant = Tenant.objects.create(name="Other GmbH", slug="other")
    other_space = CreateDocumentSpace(tenant=other_tenant, name="Rechnungen").execute()
    document = Document.objects.create(
        tenant=other_tenant,
        space=other_space,
        title="Invoice 4711",
    )

    with pytest.raises(ValueError, match="different tenant"):
        StoreImmutableFile(
            tenant=tenant,
            document=document,
            file_obj=BytesIO(b"wrong tenant"),
            original_filename="invoice.pdf",
        ).execute()


@pytest.mark.django_db
def test_document_file_artifact_fields_are_immutable():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document = Document.objects.create(
        tenant=tenant,
        space=space,
        title="Invoice 4711",
    )
    document_file = StoreImmutableFile(
        tenant=tenant,
        document=document,
        file_obj=BytesIO(b"immutable"),
        original_filename="invoice.pdf",
    ).execute()

    document_file.sha256 = "0" * 64

    with pytest.raises(ValidationError):
        document_file.save()
