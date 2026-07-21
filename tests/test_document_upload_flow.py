from __future__ import annotations

import json
import sys
from datetime import date
from io import BytesIO
from urllib.parse import quote

import pytest
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from doksio.accounts.models import Notification, TenantMembership, UserProfile
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.audit.models import AuditEvent
from doksio.documents.models import (
    Document,
    DocumentComment,
    DocumentFile,
    DocumentImportBatch,
    DocumentImportBatchItem,
    DocumentMetadataField,
    DocumentRelation,
    DocumentSpace,
)
from doksio.documents.services import (
    AddDocumentComment,
    CreateDocumentFromUpload,
    CreateDocumentMetadataField,
    CreateDocumentSpace,
    OptimizeDocumentBoxScans,
    SetDocumentTags,
    UpdateDocumentMetadata,
    pdf_page_count,
)
from doksio.einvoices.zugferd import extract_einvoice_from_pdf
from doksio.exports.models import ExportRun, ExportRunItem
from doksio.ingestion.models import ImportSource, TenantSmtpSettings
from doksio.ocr.models import OcrJob
from doksio.search.models import DocumentSearchIndex
from doksio.tenancy.models import Tenant
from doksio.tenancy.services import BootstrapDemoTenant
from doksio.workflows.models import (
    WorkflowInstance,
    WorkflowStep,
    WorkflowTask,
    WorkflowTemplate,
)
from doksio.workflows.services import (
    CreateWorkflowStep,
    CreateWorkflowTemplate,
    StartWorkflowForDocument,
)


def _single_page_tiff_bytes() -> bytes:
    from PIL import Image

    output = BytesIO()
    image = Image.new("1", (32, 32), 1)
    image.save(output, format="TIFF")
    return output.getvalue()


def _large_bilevel_tiff_bytes() -> bytes:
    from PIL import Image, ImageDraw

    output = BytesIO()
    image = Image.new("1", (1200, 1600), 1)
    draw = ImageDraw.Draw(image)
    for y_position in range(120, 1450, 80):
        draw.line((80, y_position, 1120, y_position), fill=0, width=2)
    image.save(output, format="TIFF", compression="group4")
    return output.getvalue()


def _bloated_scanned_pdf_bytes() -> bytes:
    from PIL import Image, ImageDraw

    output = BytesIO()
    image = Image.new("1", (1200, 1600), 1)
    draw = ImageDraw.Draw(image)
    for y_position in range(120, 1450, 80):
        draw.line((80, y_position, 1120, y_position), fill=0, width=2)
    image.convert("RGB").save(output, format="PDF", resolution=300)
    return output.getvalue()


def _sample_pdf_bytes(page_count: int = 3) -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    try:
        for page_number in range(page_count):
            pdf.new_page(595 + page_number, 842)
        output = BytesIO()
        pdf.save(output)
        return output.getvalue()
    finally:
        pdf.close()


def _zugferd_pdf_bytes() -> bytes:
    return b"""%PDF-1.4
<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
  xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
  xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
  xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocumentContext>
    <ram:GuidelineSpecifiedDocumentContextParameter>
      <ram:ID>urn:factur-x.eu:1p0:basic</ram:ID>
    </ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument>
    <ram:ID>RE-4711</ram:ID>
    <ram:IssueDateTime>
      <udt:DateTimeString format="102">20260707</udt:DateTimeString>
    </ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty>
        <ram:Name>Muster GmbH</ram:Name>
      </ram:SellerTradeParty>
      <ram:BuyerTradeParty>
        <ram:Name>Acme GmbH</ram:Name>
      </ram:BuyerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:ApplicableTradeTax>
        <ram:CalculatedAmount>7.00</ram:CalculatedAmount>
        <ram:TypeCode>VAT</ram:TypeCode>
        <ram:BasisAmount>100.00</ram:BasisAmount>
        <ram:CategoryCode>S</ram:CategoryCode>
        <ram:RateApplicablePercent>7.00</ram:RateApplicablePercent>
      </ram:ApplicableTradeTax>
      <ram:ApplicableTradeTax>
        <ram:CalculatedAmount>38.00</ram:CalculatedAmount>
        <ram:TypeCode>VAT</ram:TypeCode>
        <ram:BasisAmount>200.00</ram:BasisAmount>
        <ram:CategoryCode>S</ram:CategoryCode>
        <ram:RateApplicablePercent>19.00</ram:RateApplicablePercent>
      </ram:ApplicableTradeTax>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:LineTotalAmount>300.00</ram:LineTotalAmount>
        <ram:TaxBasisTotalAmount>300.00</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount>45.00</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>345.00</ram:GrandTotalAmount>
        <ram:DuePayableAmount>345.00</ram:DuePayableAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
%%EOF
"""


def _zugferd_xml_bytes() -> bytes:
    content = _zugferd_pdf_bytes()
    start = content.index(b"<?xml")
    end_marker = b"</rsm:CrossIndustryInvoice>"
    end = content.index(end_marker) + len(end_marker)
    return content[start:end]


def test_extract_einvoice_from_pdf_accepts_facturx_tuple(monkeypatch):
    class FakeFacturx:
        @staticmethod
        def get_xml_from_pdf(_pdf_file, **_kwargs):
            return "factur-x.xml", _zugferd_xml_bytes()

    monkeypatch.setitem(sys.modules, "facturx", FakeFacturx)

    extracted_invoice = extract_einvoice_from_pdf(BytesIO(b"%PDF-1.4\n"))

    assert extracted_invoice is not None
    assert extracted_invoice.source_filename == "factur-x.xml"
    assert extracted_invoice.data["invoice_number"] == "RE-4711"
    assert extracted_invoice.data["seller_name"] == "Muster GmbH"


@pytest.mark.django_db
def test_bootstrap_demo_tenant_is_idempotent():
    first_tenant, first_created = BootstrapDemoTenant().execute()
    second_tenant, second_created = BootstrapDemoTenant().execute()

    assert first_created is True
    assert second_created is False
    assert first_tenant == second_tenant
    assert Tenant.objects.count() == 1
    assert DocumentSpace.objects.filter(tenant=first_tenant).count() == 6


@pytest.mark.django_db
def test_document_space_hierarchy_builds_paths():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    invoices = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    incoming = CreateDocumentSpace(
        tenant=tenant,
        parent=invoices,
        name="Eingangsrechnungen",
        slug="eingangsrechnungen",
    ).execute()

    assert invoices.path == "/rechnungen"
    assert incoming.path == "/rechnungen/eingangsrechnungen"


@pytest.mark.django_db
def test_document_space_allows_same_slug_under_different_parents():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    invoices = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    contracts = CreateDocumentSpace(
        tenant=tenant,
        name="Verträge",
        slug="vertraege",
    ).execute()

    first_archive = CreateDocumentSpace(
        tenant=tenant,
        parent=invoices,
        name="Archiv",
        slug="archiv",
    ).execute()
    second_archive = CreateDocumentSpace(
        tenant=tenant,
        parent=contracts,
        name="Archiv",
        slug="archiv",
    ).execute()

    assert first_archive.path == "/rechnungen/archiv"
    assert second_archive.path == "/vertraege/archiv"


@pytest.mark.django_db
def test_create_document_from_upload_creates_document_file_and_audit_events():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()

    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()

    assert document.tenant == tenant
    assert document.space == space
    assert document.title == "Invoice 4711"
    assert document_file.document == document
    assert document_file.version == 1
    assert list(
        AuditEvent.objects.order_by("created_at").values_list("event_type", flat=True)
    ) == [
        "document.created",
        "document_file.stored",
    ]


@pytest.mark.django_db
def test_create_document_from_upload_creates_thumbnail_derivative(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()

    monkeypatch.setattr(
        "doksio.documents.thumbnails._render_thumbnail_bytes",
        lambda _document_file: b"thumbnail-bytes",
    )

    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Bildbeleg",
        space=space,
        file_obj=BytesIO(b"image content"),
        original_filename="receipt.png",
        content_type="image/png",
        auto_start_ocr=False,
    ).execute()

    thumbnail = DocumentFile.objects.get(
        document=document,
        file_kind=DocumentFile.Kind.THUMBNAIL,
    )
    assert thumbnail.derivative_of == document_file
    assert thumbnail.content_type == "image/jpeg"
    assert thumbnail.original_filename == "receipt-thumbnail.jpg"
    assert thumbnail.byte_size == len(b"thumbnail-bytes")


@pytest.mark.django_db
def test_create_document_from_upload_converts_tiff_to_pdf_original(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()

    monkeypatch.setattr(
        "doksio.documents.thumbnails._render_thumbnail_bytes",
        lambda _document_file: b"thumbnail-bytes",
    )

    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Scan",
        space=space,
        file_obj=BytesIO(_single_page_tiff_bytes()),
        original_filename="scan.tiff",
        content_type="image/tiff",
        auto_start_ocr=False,
    ).execute()

    thumbnail = DocumentFile.objects.get(
        document=document,
        file_kind=DocumentFile.Kind.THUMBNAIL,
    )
    assert document_file.file_kind == DocumentFile.Kind.ORIGINAL
    assert document_file.content_type == "application/pdf"
    assert document_file.original_filename == "scan.pdf"
    assert document_file.byte_size > 0
    assert document_file.sha256
    assert thumbnail.derivative_of == document_file
    assert not DocumentFile.objects.filter(content_type="image/tiff").exists()


@pytest.mark.django_db
def test_create_document_from_upload_attaches_zugferd_invoice_data():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()

    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="",
        space=space,
        file_obj=BytesIO(_zugferd_pdf_bytes()),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()

    document.refresh_from_db()
    assert document.title == "Muster GmbH: RE-4711 vom 07.07.2026"
    assert document.title_source == Document.TitleSource.OCR
    assert document.einvoice_data["source"] == "zugferd"
    assert document.einvoice_data["syntax"] == "CII"
    assert document.einvoice_data["profile"] == "urn:factur-x.eu:1p0:basic"
    assert document.einvoice_data["invoice_number"] == "RE-4711"
    assert document.einvoice_data["seller_name"] == "Muster GmbH"
    assert document.einvoice_data["buyer_name"] == "Acme GmbH"
    assert document.einvoice_data["grand_total_amount"] == "345.00"
    assert document.einvoice_data["tax_breakdown"] == [
        {
            "category": "S",
            "rate": "7.00",
            "net_amount": "100.00",
            "tax_amount": "7.00",
        },
        {
            "category": "S",
            "rate": "19.00",
            "net_amount": "200.00",
            "tax_amount": "38.00",
        },
    ]
    assert AuditEvent.objects.filter(
        event_type="document_einvoice.detected",
        object_id=str(document.id),
    ).exists()


@pytest.mark.django_db
def test_create_document_from_upload_keeps_manual_title_for_einvoice():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()

    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Manueller Titel",
        space=space,
        file_obj=BytesIO(_zugferd_pdf_bytes()),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()

    document.refresh_from_db()
    assert document.title == "Manueller Titel"
    assert document.title_source == Document.TitleSource.MANUAL
    assert document.einvoice_data["invoice_number"] == "RE-4711"


@pytest.mark.django_db
def test_document_upload_view_creates_document(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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

    response = client.post(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug}),
        {
            "title": "Invoice 4711",
            "space": space.id,
            "file": SimpleUploadedFile(
                "invoice.pdf",
                b"invoice content",
                content_type="application/pdf",
            ),
        },
    )

    document = Document.objects.get(tenant=tenant)
    document_file = DocumentFile.objects.get(document=document)
    assert response.status_code == 302
    assert response.headers["Location"] == reverse(
        "documents:detail",
        kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
    )
    assert document.title == "Invoice 4711"
    assert document.space == space
    assert document_file.original_filename == "invoice.pdf"


@pytest.mark.django_db
def test_document_upload_view_creates_multiple_documents(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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

    response = client.post(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug}),
        {
            "title": "Ignorierter Stapeltitel",
            "space": space.id,
            "file": [
                SimpleUploadedFile(
                    "invoice-1.pdf",
                    b"invoice content 1",
                    content_type="application/pdf",
                ),
                SimpleUploadedFile(
                    "invoice-2.pdf",
                    b"invoice content 2",
                    content_type="application/pdf",
                ),
            ],
        },
    )

    documents = Document.objects.filter(tenant=tenant).order_by("title")
    assert response.status_code == 302
    assert response.headers["Location"] == reverse(
        "documents:dashboard",
        kwargs={"tenant_slug": tenant.slug},
    )
    assert list(documents.values_list("title", flat=True)) == [
        "invoice-1",
        "invoice-2",
    ]
    assert DocumentFile.objects.filter(document__tenant=tenant).count() == 2


@pytest.mark.django_db
def test_document_batch_import_permission_defaults():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()

    assert roles["admin"].permissions.filter(code="documents.batch_import").exists()
    assert roles["member"].permissions.filter(code="documents.batch_import").exists()
    assert not roles["viewer"].permissions.filter(
        code="documents.batch_import",
    ).exists()
    assert roles["admin"].permissions.filter(code="documents.split").exists()
    assert roles["member"].permissions.filter(code="documents.split").exists()
    assert not roles["viewer"].permissions.filter(code="documents.split").exists()


@pytest.mark.django_db
def test_document_import_batch_upload_creates_staged_items_with_suggestions(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    inbox = CreateDocumentSpace(tenant=tenant, name="Posteingang").execute()
    invoices = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    ImportSource.objects.create(
        tenant=tenant,
        document_space=inbox,
        name="Upload-Regeln",
        source_type=ImportSource.SourceType.UPLOAD,
        target_strategy=ImportSource.TargetStrategy.RULES,
        settings={
            "routing_rules": [
                {
                    "pattern": "rechnung-*",
                    "document_space_id": invoices.id,
                },
            ],
        },
    )
    client.force_login(user)

    response = client.post(
        reverse("documents:import_batch_upload", kwargs={"tenant_slug": tenant.slug}),
        {
            "title": "Teststapel",
            "file": [
                SimpleUploadedFile(
                    "rechnung-1.pdf",
                    b"%PDF-1.4\nfirst",
                    content_type="application/pdf",
                ),
                SimpleUploadedFile(
                    "vertrag.pdf",
                    b"%PDF-1.4\nsecond",
                    content_type="application/pdf",
                ),
            ],
        },
    )

    batch = DocumentImportBatch.objects.get(tenant=tenant)
    assert response.status_code == 302
    assert response.url == reverse(
        "documents:import_batch_detail",
        kwargs={"tenant_slug": tenant.slug, "batch_id": batch.id},
    )
    assert batch.title == "Teststapel"
    assert batch.items.count() == 2
    suggested_item = batch.items.get(original_filename="rechnung-1.pdf")
    fallback_item = batch.items.get(original_filename="vertrag.pdf")
    assert suggested_item.suggested_space == invoices
    assert suggested_item.target_space == invoices
    assert fallback_item.suggested_space == inbox
    assert default_storage.exists(suggested_item.source_storage_key)


@pytest.mark.django_db
def test_document_import_batch_detail_finalizes_documents(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    invoices = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    personnel = CreateDocumentSpace(tenant=tenant, name="Personal").execute()
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
    batch = DocumentImportBatch.objects.create(
        tenant=tenant,
        title="Teststapel",
        created_by=user,
    )
    first_storage_key = "tests/import-batches/first.pdf"
    second_storage_key = "tests/import-batches/second.pdf"
    default_storage.delete(first_storage_key)
    default_storage.delete(second_storage_key)
    first_storage_key = default_storage.save(
        first_storage_key,
        SimpleUploadedFile("first.pdf", b"first"),
    )
    second_storage_key = default_storage.save(
        second_storage_key,
        SimpleUploadedFile("second.pdf", b"second"),
    )
    first_item = DocumentImportBatchItem.objects.create(
        tenant=tenant,
        batch=batch,
        source_storage_key=first_storage_key,
        original_filename="first.pdf",
        content_type="application/pdf",
        byte_size=5,
        target_space=invoices,
    )
    second_item = DocumentImportBatchItem.objects.create(
        tenant=tenant,
        batch=batch,
        source_storage_key=second_storage_key,
        original_filename="second.pdf",
        content_type="application/pdf",
        byte_size=6,
        target_space=invoices,
    )

    response = client.post(
        reverse(
            "documents:import_batch_detail",
            kwargs={"tenant_slug": tenant.slug, "batch_id": batch.id},
        ),
        {
            "action": "finalize",
            f"item-{first_item.id}-target_space": str(invoices.id),
            f"item-{second_item.id}-target_space": str(personnel.id),
        },
    )

    assert response.status_code == 302
    batch.refresh_from_db()
    first_item.refresh_from_db()
    second_item.refresh_from_db()
    assert batch.status == DocumentImportBatch.Status.COMPLETED
    assert first_item.status == DocumentImportBatchItem.Status.IMPORTED
    assert first_item.imported_document.space == invoices
    assert second_item.status == DocumentImportBatchItem.Status.IMPORTED
    assert second_item.imported_document.space == personnel
    assert Document.objects.filter(tenant=tenant).count() == 2
    assert not default_storage.exists(first_storage_key)
    assert not default_storage.exists(second_storage_key)


@pytest.mark.django_db
def test_document_import_batch_list_shows_open_batches(client):
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
    batch = DocumentImportBatch.objects.create(
        tenant=tenant,
        title="Offener Stapel",
        created_by=user,
    )
    DocumentImportBatchItem.objects.create(
        tenant=tenant,
        batch=batch,
        source_storage_key="tests/import-batches/open.pdf",
        original_filename="open.pdf",
        content_type="application/pdf",
        byte_size=4,
    )
    client.force_login(user)

    response = client.get(
        reverse("documents:import_batch_list", kwargs={"tenant_slug": tenant.slug}),
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Offener Stapel" in content
    assert "Wieder aufnehmen" in content
    assert reverse(
        "documents:import_batch_detail",
        kwargs={"tenant_slug": tenant.slug, "batch_id": batch.id},
    ) in content


@pytest.mark.django_db
def test_document_import_batch_detail_renders_staged_file_preview(client):
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
    batch = DocumentImportBatch.objects.create(
        tenant=tenant,
        title="Offener Stapel",
        created_by=user,
    )
    storage_key = "tests/import-batches/preview.pdf"
    default_storage.delete(storage_key)
    storage_key = default_storage.save(
        storage_key,
        SimpleUploadedFile("preview.pdf", b"%PDF-1.4\npreview"),
    )
    item = DocumentImportBatchItem.objects.create(
        tenant=tenant,
        batch=batch,
        source_storage_key=storage_key,
        original_filename="preview.pdf",
        content_type="application/pdf",
        byte_size=16,
    )
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:import_batch_detail",
            kwargs={"tenant_slug": tenant.slug, "batch_id": batch.id},
        )
    )

    preview_url = reverse(
        "documents:import_batch_item_preview",
        kwargs={
            "tenant_slug": tenant.slug,
            "batch_id": batch.id,
            "item_id": item.id,
        },
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert "import-batch-preview" in content
    assert preview_url in content
    assert "preview.pdf" in content


@pytest.mark.django_db
def test_document_import_batch_item_preview_returns_staged_file(client):
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
    batch = DocumentImportBatch.objects.create(
        tenant=tenant,
        title="Offener Stapel",
        created_by=user,
    )
    storage_key = "tests/import-batches/inline.pdf"
    default_storage.delete(storage_key)
    storage_key = default_storage.save(
        storage_key,
        SimpleUploadedFile("inline.pdf", b"%PDF-1.4\ninline"),
    )
    item = DocumentImportBatchItem.objects.create(
        tenant=tenant,
        batch=batch,
        source_storage_key=storage_key,
        original_filename="inline.pdf",
        content_type="application/pdf",
        byte_size=15,
    )
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:import_batch_item_preview",
            kwargs={
                "tenant_slug": tenant.slug,
                "batch_id": batch.id,
                "item_id": item.id,
            },
        )
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["X-Frame-Options"] == "SAMEORIGIN"
    assert b"".join(response.streaming_content) == b"%PDF-1.4\ninline"


@pytest.mark.django_db
def test_document_import_batch_discard_deletes_staged_files(client):
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
    batch = DocumentImportBatch.objects.create(
        tenant=tenant,
        title="Weg damit",
        created_by=user,
    )
    storage_key = "tests/import-batches/discard.pdf"
    default_storage.delete(storage_key)
    storage_key = default_storage.save(
        storage_key,
        SimpleUploadedFile("discard.pdf", b"discard"),
    )
    item = DocumentImportBatchItem.objects.create(
        tenant=tenant,
        batch=batch,
        source_storage_key=storage_key,
        original_filename="discard.pdf",
        content_type="application/pdf",
        byte_size=7,
    )

    response = client.post(
        reverse(
            "documents:import_batch_discard",
            kwargs={"tenant_slug": tenant.slug, "batch_id": batch.id},
        ),
    )

    assert response.status_code == 302
    assert response.url == reverse(
        "documents:import_batch_list",
        kwargs={"tenant_slug": tenant.slug},
    )
    batch.refresh_from_db()
    item.refresh_from_db()
    assert batch.status == DocumentImportBatch.Status.DISCARDED
    assert item.status == DocumentImportBatchItem.Status.SKIPPED
    assert not default_storage.exists(storage_key)
    assert AuditEvent.objects.filter(
        tenant=tenant,
        event_type="document_import_batch.discarded",
        object_id=str(batch.id),
    ).exists()


@pytest.mark.django_db
def test_document_upload_view_renders_dropzone_and_multiple_input(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug}),
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "upload-dropzone" in content
    assert "data-upload-title-field" in content
    assert "Optional beim Einzelupload" in content
    assert "Titel je Dokument automatisch" in content
    assert "multiple" in content
    assert "document-upload.js" in content


@pytest.mark.django_db
def test_document_upload_view_uses_upload_import_strategy_without_selected_box(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    ImportSource.objects.create(
        tenant=tenant,
        document_space=space,
        name="Upload intelligent",
        source_type=ImportSource.SourceType.UPLOAD,
        target_strategy=ImportSource.TargetStrategy.INTELLIGENT,
        auto_start_ocr=False,
        start_workflows=False,
    )
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

    response = client.post(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug}),
        {
            "title": "Invoice 4712",
            "space": "",
            "file": SimpleUploadedFile(
                "invoice-4712.pdf",
                b"invoice content via upload strategy",
                content_type="application/pdf",
            ),
        },
    )

    document = Document.objects.get(tenant=tenant)
    assert response.status_code == 302
    assert document.title == "Invoice 4712"
    assert document.space == space


@pytest.mark.django_db
def test_document_upload_view_requires_box_without_upload_strategy(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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

    response = client.post(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug}),
        {
            "title": "Invoice 4713",
            "space": "",
            "file": SimpleUploadedFile(
                "invoice-4713.pdf",
                b"invoice content without upload strategy",
                content_type="application/pdf",
            ),
        },
    )

    assert response.status_code == 200
    assert not Document.objects.filter(tenant=tenant).exists()
    assert "aktive Upload-Importstrategie" in response.content.decode()


@pytest.mark.django_db
def test_document_upload_view_limits_boxes_by_additive_role_access(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    allowed_space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    denied_space = CreateDocumentSpace(tenant=tenant, name="Verträge").execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    roles["member"].can_access_all_document_spaces = False
    roles["member"].save(update_fields=["can_access_all_document_spaces"])
    roles["member"].document_spaces.set([allowed_space])
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

    response = client.post(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug}),
        {
            "title": "Contract",
            "space": denied_space.id,
            "file": SimpleUploadedFile(
                "contract.pdf",
                b"contract content",
                content_type="application/pdf",
            ),
        },
    )

    assert response.status_code == 200
    assert not Document.objects.filter(tenant=tenant).exists()


@pytest.mark.django_db
def test_document_upload_view_allows_empty_title(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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

    response = client.post(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug}),
        {
            "title": "",
            "space": space.id,
            "file": SimpleUploadedFile(
                "scan-001.pdf",
                b"invoice content",
                content_type="application/pdf",
            ),
        },
    )

    document = Document.objects.get(tenant=tenant)
    assert response.status_code == 302
    assert document.title == "scan-001"
    assert document.title_source == Document.TitleSource.FILENAME


@pytest.mark.django_db
def test_document_download_view_returns_stored_file(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()

    response = client.get(
        reverse(
            "documents:download",
            kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
        )
    )

    assert document.title == "Invoice 4711"
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"invoice content"


@pytest.mark.django_db
def test_document_detail_renders_pdf_preview(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"%PDF-1.4\n"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "data-pdf-preview" in content
    assert "data-review-assist-toggle" in content
    assert 'data-viewer-rotation="0"' in content
    assert "data-viewer-rotate-left" in content
    assert "data-viewer-rotate-right" in content
    assert "?inline=1" in content
    assert (
        reverse(
            "documents:download",
            kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
        )
        in content
    )
    assert "Teilen" in content
    assert "Link kopieren" in content
    assert "Mail mit Link" in content
    assert response.context["document_share_url"].endswith(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    assert "back=" not in response.context["document_share_url"]
    assert "mailto:?subject=" in content


@pytest.mark.django_db
def test_document_detail_uses_saved_viewer_rotation(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"%PDF-1.4\n"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()
    document_file.viewer_settings = {"rotation": 90}
    document_file.save(update_fields=["viewer_settings"])

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    assert 'data-viewer-rotation="90"' in response.content.decode()


@pytest.mark.django_db
def test_document_file_viewer_settings_persist_rotation(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"%PDF-1.4\n"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()

    response = client.post(
        reverse(
            "documents:file_viewer_settings",
            kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
        ),
        data='{"rotation": 450}',
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["rotation"] == 90
    document_file.refresh_from_db()
    assert document_file.viewer_settings["rotation"] == 90

    response = client.post(
        reverse(
            "documents:file_viewer_settings",
            kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
        ),
        data='{"rotation": 45}',
        content_type="application/json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_document_detail_can_send_original_file_as_email_attachment(
    client,
    monkeypatch,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    TenantSmtpSettings.objects.create(
        tenant=tenant,
        host="smtp.example.test",
        port=587,
        security=TenantSmtpSettings.Security.STARTTLS,
        username="doksio@example.test",
        password="secret",
        from_email="doksio@example.test",
        from_name="Doksio",
        is_active=True,
    )
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()
    sent_messages = []

    def fake_send(email_message):
        sent_messages.append(email_message)
        return 1

    monkeypatch.setattr("doksio.documents.views.EmailMessage.send", fake_send)
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "share_attachment_email",
            "recipient": "kunde@example.test",
            "message": "Bitte prüfen.",
        },
    )

    assert response.status_code == 302
    assert len(sent_messages) == 1
    email_message = sent_messages[0]
    assert email_message.to == ["kunde@example.test"]
    assert email_message.subject == "Doksio Dokument: Invoice 4711"
    assert "Bitte prüfen." in email_message.body
    assert "Dokument in Doksio:" in email_message.body
    assert email_message.attachments[0][0] == "invoice.pdf"
    assert email_message.attachments[0][1] == b"invoice content"
    assert email_message.attachments[0][2] == "application/pdf"
    assert AuditEvent.objects.filter(
        event_type="document.shared",
        object_id=str(document.id),
    ).exists()


@pytest.mark.django_db
def test_document_detail_offers_native_share_with_original_attachment(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()
    client.force_login(user)

    detail_response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    content = detail_response.content.decode()
    expected_file_url = (
        reverse(
            "documents:download",
            kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
        )
        + "?inline=1"
    )
    assert "Über System teilen" in content
    assert "data-share-document-file" in content
    assert f'data-share-file-url="{expected_file_url}"' in content
    assert 'data-share-file-name="invoice.pdf"' in content
    assert 'data-share-file-type="application/pdf"' in content


@pytest.mark.django_db
def test_document_detail_can_link_related_document(client, monkeypatch):
    monkeypatch.setattr(
        "doksio.documents.thumbnails._render_thumbnail_bytes",
        lambda _document_file: b"thumbnail-bytes",
    )
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    warehouse_space = CreateDocumentSpace(tenant=tenant, name="Wareneingang").execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Rechnung",
        space=space,
        file_obj=BytesIO(b"invoice"),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
    ).execute()
    related_document, _related_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Beleg",
        space=warehouse_space,
        file_obj=BytesIO(b"delivery note"),
        original_filename="beleg.pdf",
        content_type="application/pdf",
    ).execute()
    unrelated_document, _unrelated_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Sonstiges",
        space=space,
        file_obj=BytesIO(b"other document"),
        original_filename="sonstiges.pdf",
        content_type="application/pdf",
    ).execute()
    workflow_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Wareneingang",
        slug="wareneingang",
    ).execute()
    CreateWorkflowStep(
        template=workflow_template,
        name="Prüfen",
        step_type=WorkflowStep.StepType.TASK,
    ).execute()
    StartWorkflowForDocument(
        template=workflow_template,
        document=related_document,
    ).execute()
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "add_relation",
            "target_document_id": related_document.id,
        },
    )

    assert response.status_code == 302
    relation = DocumentRelation.objects.get(tenant=tenant)
    assert {relation.first_document_id, relation.second_document_id} == {
        document.id,
        related_document.id,
    }
    detail_response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    content = detail_response.content.decode()
    assert "Verknüpfte Dokumente" in content
    assert "Beleg" in content
    assert "Dokument auswählen" in content
    assert "Diese Dokumentverknüpfung wirklich lösen?" in content
    thumbnail = related_document.files.get(file_kind=DocumentFile.Kind.THUMBNAIL)
    assert (
        reverse(
            "documents:download",
            kwargs={"tenant_slug": tenant.slug, "file_id": thumbnail.id},
        )
        + "?inline=1"
    ) in content

    picker_response = client.get(
        reverse(
            "documents:relation_picker_search",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "q": "",
            "space": warehouse_space.id,
            "workflow_status": "open",
        },
    )
    assert picker_response.status_code == 200
    results = picker_response.json()["results"]
    assert [item["id"] for item in results] == [related_document.id]
    assert results[0]["thumbnail_url"]
    assert results[0]["workflow_open_count"] == 1
    assert unrelated_document.id not in [item["id"] for item in results]


@pytest.mark.django_db
def test_document_detail_relation_task_prefills_picker_filters(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    invoice_space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    delivery_space = CreateDocumentSpace(tenant=tenant, name="Lieferscheine").execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Rechnung",
        space=invoice_space,
        file_obj=BytesIO(b"invoice"),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
    ).execute()
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Lieferschein verknüpfen",
        step_type=WorkflowStep.StepType.REQUIRE_DOCUMENT_RELATION,
        assigned_role=roles["member"],
        required_related_document_spaces=[delivery_space],
        relation_picker_default_document_space=delivery_space,
        relation_picker_default_workflow_status=(
            WorkflowStep.RelationPickerWorkflowStatus.COMPLETED
        ),
        relation_picker_default_include_child_spaces=True,
        relation_picker_filters_editable=False,
    ).execute()
    StartWorkflowForDocument(template=template, document=document).execute()
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Dokumentverknüpfung erforderlich" in content
    assert f'data-relation-default-space="{delivery_space.id}"' in content
    assert 'data-relation-default-workflow-status="completed"' in content
    assert 'data-relation-default-include-children="1"' in content
    assert 'data-relation-filters-editable="0"' in content


@pytest.mark.django_db
def test_document_detail_links_to_split_view_for_pdf_with_permission(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Stapelscan",
        space=space,
        file_obj=BytesIO(_sample_pdf_bytes(3)),
        original_filename="stapelscan.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    assert "Dokument aufteilen" in response.content.decode()
    assert reverse(
        "documents:split",
        kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
    ) in response.content.decode()

    split_response = client.get(
        reverse(
            "documents:split",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    split_content = split_response.content.decode()
    assert split_response.status_code == 200
    assert "Seitenraster" in split_content
    assert "data-document-split" in split_content
    assert "Originaldokument behalten" in split_content


@pytest.mark.django_db
def test_document_split_creates_new_pdf_documents(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    invoices = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    personnel = CreateDocumentSpace(tenant=tenant, name="Personal").execute()
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
    source_document, _source_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Stapelscan",
        space=invoices,
        file_obj=BytesIO(_sample_pdf_bytes(3)),
        original_filename="stapelscan.pdf",
        content_type="application/pdf",
        document_date=date(2026, 7, 20),
        created_by=user,
    ).execute()
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:split",
            kwargs={"tenant_slug": tenant.slug, "document_id": source_document.id},
        ),
        {
            "split_payload": json.dumps(
                [
                    {
                        "start_page": 1,
                        "end_page": 1,
                        "title": "Rechnung",
                        "target_space_id": invoices.id,
                    },
                    {
                        "start_page": 2,
                        "end_page": 3,
                        "title": "Personalakte",
                        "target_space_id": personnel.id,
                    },
                ]
            ),
            "original_handling": "keep",
        },
    )

    assert response.status_code == 302
    created_documents = list(
        Document.objects.filter(tenant=tenant)
        .exclude(id=source_document.id)
        .order_by("id")
    )
    assert [document.title for document in created_documents] == [
        "Rechnung",
        "Personalakte",
    ]
    assert [document.space for document in created_documents] == [invoices, personnel]
    assert [document.document_date for document in created_documents] == [
        date(2026, 7, 20),
        date(2026, 7, 20),
    ]
    created_files = [
        document.files.get(file_kind=DocumentFile.Kind.ORIGINAL)
        for document in created_documents
    ]
    assert [pdf_page_count(document_file) for document_file in created_files] == [1, 2]
    source_document.refresh_from_db()
    assert source_document.status == Document.Status.ACTIVE
    assert AuditEvent.objects.filter(
        event_type="document.split",
        object_id=str(source_document.id),
        data__keep_original=True,
    ).exists()


@pytest.mark.django_db
def test_document_split_can_delete_original_after_success(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    source_document, _source_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Stapelscan",
        space=space,
        file_obj=BytesIO(_sample_pdf_bytes(2)),
        original_filename="stapelscan.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:split",
            kwargs={"tenant_slug": tenant.slug, "document_id": source_document.id},
        ),
        {
            "split_payload": json.dumps(
                [
                    {
                        "start_page": 1,
                        "end_page": 1,
                        "target_space_id": space.id,
                    },
                    {
                        "start_page": 2,
                        "end_page": 2,
                        "target_space_id": space.id,
                    },
                ]
            ),
            "original_handling": "delete",
        },
    )

    assert response.status_code == 302
    source_document.refresh_from_db()
    assert source_document.status == Document.Status.DELETED
    assert source_document.deleted_reason == "Aufgeteilt"


@pytest.mark.django_db
def test_document_detail_shows_review_assist_without_document_box_setting(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
    ).execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"%PDF-1.4\n"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Prüfhilfe" in content
    assert "data-review-assist-toggle" in content


@pytest.mark.django_db
def test_document_detail_shows_preview_fulltext_collapsed(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"%PDF-1.4\n"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
    ).execute()
    OcrJob.objects.create(
        tenant=tenant,
        document_file=document_file,
        status=OcrJob.Status.SUCCEEDED,
        extracted_text="Erkannter Volltext\nZeile zwei",
    )

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "<summary>" in content
    assert "Volltext" in content
    assert "Erkannter Volltext" in content
    assert content.index("document-preview-stage") < content.index("document-fulltext")


@pytest.mark.django_db
def test_document_detail_shows_einvoice_data_collapsed(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(_zugferd_pdf_bytes()),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "eRechnung" in content
    assert "Daten erkannt" in content
    assert "Rechnungsnummer" in content
    assert "RE-4711" in content
    assert "Muster GmbH" in content
    assert "Umsätze nach Steuer" in content
    assert "7.00 %" in content
    assert "19.00 %" in content
    assert "urn:factur-x.eu:1p0:basic" not in content
    assert "Quelldatei" not in content
    assert content.index("document-preview-stage") < content.index("eRechnung")


@pytest.mark.django_db
def test_document_detail_renders_image_preview(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Belege").execute()
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
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Foto",
        space=space,
        file_obj=BytesIO(b"image content"),
        original_filename="foto.png",
        content_type="image/png",
    ).execute()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "data-pdf-preview" not in content
    assert "data-image-preview" in content
    assert "data-image-fit" in content
    assert "data-image-zoom-in" in content
    assert "data-image-zoom-out" in content
    assert "document-preview-image" in content
    assert "js/document-preview.js" in content
    assert (
        reverse(
            "documents:download",
            kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
        )
        in content
    )
    assert "?inline=1" in content


@pytest.mark.django_db
def test_document_file_download_can_render_inline(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Belege").execute()
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
    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Foto",
        space=space,
        file_obj=BytesIO(b"image content"),
        original_filename="foto.png",
        content_type="image/png",
    ).execute()

    response = client.get(
        reverse(
            "documents:download",
            kwargs={"tenant_slug": tenant.slug, "file_id": document_file.id},
        ),
        {"inline": "1"},
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/png"
    assert "attachment" not in response.headers.get("Content-Disposition", "")


@pytest.mark.django_db
def test_document_detail_falls_back_without_preview(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Note",
        space=space,
        file_obj=BytesIO(b"plain text"),
        original_filename="note.txt",
        content_type="text/plain",
    ).execute()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "data-pdf-preview" not in content
    assert "Keine Live-Vorschau verfügbar" in content


@pytest.mark.django_db
def test_add_document_comment_creates_comment_and_audit_event():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(username="alice")
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()

    comment = AddDocumentComment(
        document=document,
        body="Bitte prüfen.",
        actor=user,
    ).execute()

    assert comment.document == document
    assert comment.body == "Bitte prüfen."
    assert comment.created_by == user
    assert AuditEvent.objects.filter(event_type="document_comment.created").exists()


@pytest.mark.django_db
def test_add_document_comment_mentions_tenant_user_and_notifies():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    author = get_user_model().objects.create_user(username="alice")
    mentioned_user = get_user_model().objects.create_user(username="bob")
    other_tenant_user = get_user_model().objects.create_user(username="charlie")
    other_tenant = Tenant.objects.create(name="Other GmbH", slug="other")
    other_role = EnsureDefaultTenantRoles(tenant=other_tenant).execute()["member"]

    TenantMembership.objects.create(
        tenant=tenant,
        user=author,
        role=roles["member"],
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=mentioned_user,
        role=roles["member"],
    )
    TenantMembership.objects.create(
        tenant=other_tenant,
        user=other_tenant_user,
        role=other_role,
    )
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=author,
    ).execute()

    comment = AddDocumentComment(
        document=document,
        body="Bitte @bob prüfen, nicht @charlie oder @alice.",
        actor=author,
    ).execute()

    assert list(comment.mentioned_users.all()) == [mentioned_user]
    notification = Notification.objects.get(recipient=mentioned_user)
    assert notification.notification_type == Notification.Type.DOCUMENT_COMMENT_MENTION
    assert notification.document == document
    assert notification.document_comment == comment
    assert not Notification.objects.filter(recipient=author).exists()
    assert not Notification.objects.filter(recipient=other_tenant_user).exists()


@pytest.mark.django_db
def test_add_document_comment_respects_disabled_mention_notifications():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    author = get_user_model().objects.create_user(username="alice")
    mentioned_user = get_user_model().objects.create_user(username="bob")
    UserProfile.objects.create(
        user=mentioned_user,
        notifications_enabled=True,
        mention_notifications_enabled=False,
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=author,
        role=roles["member"],
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=mentioned_user,
        role=roles["member"],
    )
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=author,
    ).execute()

    comment = AddDocumentComment(
        document=document,
        body="Bitte @bob prüfen.",
        actor=author,
    ).execute()

    assert list(comment.mentioned_users.all()) == [mentioned_user]
    assert not Notification.objects.filter(recipient=mentioned_user).exists()


@pytest.mark.django_db
def test_add_document_comment_can_send_mention_email_without_in_app_notification(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    author = get_user_model().objects.create_user(username="alice")
    mentioned_user = get_user_model().objects.create_user(
        username="bob",
        email="bob@example.test",
    )
    UserProfile.objects.create(
        user=mentioned_user,
        notification_preferences={
            "workflow_started": {"in_app": False, "email": False},
            "workflow_task_created": {"in_app": False, "email": False},
            "document_comment_mention": {"in_app": False, "email": True},
            "import_failed": {"in_app": False, "email": False},
        },
    )
    TenantSmtpSettings.objects.create(
        tenant=tenant,
        host="smtp.example.test",
        port=587,
        security=TenantSmtpSettings.Security.STARTTLS,
        username="mailer@example.test",
        password="secret",
        from_email="doksio@example.test",
        from_name="Doksio",
        is_active=True,
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=author,
        role=roles["member"],
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=mentioned_user,
        role=roles["member"],
    )
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=author,
    ).execute()
    sent_messages = []

    def fake_send(message):
        sent_messages.append(message)
        return 1

    monkeypatch.setattr(
        "doksio.accounts.services.EmailMultiAlternatives.send",
        fake_send,
    )

    with django_capture_on_commit_callbacks(execute=True):
        comment = AddDocumentComment(
            document=document,
            body="Bitte @bob prüfen.",
            actor=author,
        ).execute()

    assert list(comment.mentioned_users.all()) == [mentioned_user]
    assert not Notification.objects.filter(recipient=mentioned_user).exists()
    assert len(sent_messages) == 1
    assert sent_messages[0].to == ["bob@example.test"]
    assert "Du wurdest erwähnt" in sent_messages[0].subject
    assert "Invoice 4711" not in sent_messages[0].body


@pytest.mark.django_db
def test_set_document_tags_creates_and_replaces_assignments():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(username="alice")
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()

    SetDocumentTags(
        document=document,
        tag_names=["Dringend", "Rückfrage"],
        actor=user,
    ).execute()
    SetDocumentTags(
        document=document,
        tag_names=["Geprüft"],
        actor=user,
    ).execute()

    assert list(
        document.tag_assignments.order_by("tag__name").values_list(
            "tag__name",
            flat=True,
        )
    ) == ["Geprüft"]
    assert AuditEvent.objects.filter(event_type="document_tags.updated").count() == 2


@pytest.mark.django_db
def test_document_detail_accepts_comment_and_tags(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()

    comment_response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "add_comment",
            "body": "Bitte prüfen.",
        },
    )
    tag_response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "update_tags",
            "tag_names": "Dringend, Rückfrage",
        },
    )

    assert comment_response.status_code == 302
    assert tag_response.status_code == 302
    assert DocumentComment.objects.get(document=document).body == "Bitte prüfen."
    assert set(document.tag_assignments.values_list("tag__name", flat=True)) == {
        "Dringend",
        "Rückfrage",
    }

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    content = response.content.decode()
    assert "Kommentare" in content
    assert "1 vorhanden" in content


@pytest.mark.django_db
def test_document_detail_renders_comment_mention_ui(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    mentioned_user = get_user_model().objects.create_user(username="bob")
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=mentioned_user,
        role=roles["member"],
    )
    client.force_login(user)
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()
    AddDocumentComment(
        document=document,
        body="Bitte @bob prüfen.",
        actor=user,
    ).execute()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "document-mentions.js" in content
    assert 'data-mention-input="document-comment"' in content
    assert 'data-mention-suggestions' in content
    assert "document-comment-mention-users" in content
    assert "document-comment-mention" in content
    assert "@bob" in content


@pytest.mark.django_db
def test_create_document_metadata_field_from_box_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    box = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    user = get_user_model().objects.create_user(
        username="alice",
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
            "documents:settings_metadata_field_create",
            kwargs={"tenant_slug": tenant.slug, "box_id": box.id},
        ),
        {
            "name": "Belegdatum",
            "slug": "belegdatum",
            "field_type": DocumentMetadataField.FieldType.DATE,
            "help_text": "Datum auf dem Dokument",
            "choices_text": "",
            "allow_custom_choices": "",
            "einvoice_source": DocumentMetadataField.EInvoiceSource.INVOICE_DATE,
            "sort_order": "10",
            "is_required": "on",
            "is_active": "on",
        },
    )

    metadata_field = DocumentMetadataField.objects.get(space=box)
    assert response.status_code == 302
    assert metadata_field.name == "Belegdatum"
    assert metadata_field.field_type == DocumentMetadataField.FieldType.DATE
    assert (
        metadata_field.einvoice_source
        == DocumentMetadataField.EInvoiceSource.INVOICE_DATE
    )
    assert metadata_field.allow_custom_choices is False
    assert metadata_field.is_required is True


@pytest.mark.django_db
def test_create_choice_metadata_field_allows_user_added_choices(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    box = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
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
            "documents:settings_metadata_field_create",
            kwargs={"tenant_slug": tenant.slug, "box_id": box.id},
        ),
        {
            "name": "Kategorie",
            "slug": "kategorie",
            "field_type": DocumentMetadataField.FieldType.CHOICE,
            "help_text": "",
            "choices_text": "Rechnung",
            "allow_custom_choices": "on",
            "einvoice_source": "",
            "sort_order": "10",
            "is_active": "on",
        },
    )

    metadata_field = DocumentMetadataField.objects.get(space=box)
    assert response.status_code == 302
    assert metadata_field.allow_custom_choices is True


@pytest.mark.django_db
def test_create_document_from_upload_prefills_metadata_from_einvoice():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    CreateDocumentMetadataField(
        tenant=tenant,
        space=space,
        name="Rechnungsdatum",
        slug="rechnungsdatum",
        field_type=DocumentMetadataField.FieldType.DATE,
        einvoice_source=DocumentMetadataField.EInvoiceSource.INVOICE_DATE,
    ).execute()
    CreateDocumentMetadataField(
        tenant=tenant,
        space=space,
        name="Verkäufer",
        slug="verkaeufer",
        field_type=DocumentMetadataField.FieldType.TEXT,
        einvoice_source=DocumentMetadataField.EInvoiceSource.SELLER_NAME,
    ).execute()
    CreateDocumentMetadataField(
        tenant=tenant,
        space=space,
        name="Netto 7 %",
        slug="netto_7",
        field_type=DocumentMetadataField.FieldType.NUMBER,
        einvoice_source=DocumentMetadataField.EInvoiceSource.TAX_NET_7,
    ).execute()
    CreateDocumentMetadataField(
        tenant=tenant,
        space=space,
        name="Netto 19 %",
        slug="netto_19",
        field_type=DocumentMetadataField.FieldType.NUMBER,
        einvoice_source=DocumentMetadataField.EInvoiceSource.TAX_NET_19,
    ).execute()

    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="",
        space=space,
        file_obj=BytesIO(_zugferd_pdf_bytes()),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()

    document.refresh_from_db()
    assert document.metadata == {
        "rechnungsdatum": "2026-07-07",
        "verkaeufer": "Muster GmbH",
        "netto_7": "100.00",
        "netto_19": "200.00",
    }


@pytest.mark.django_db
def test_document_detail_accepts_box_metadata(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    CreateDocumentMetadataField(
        tenant=tenant,
        space=space,
        name="Kategorie",
        slug="kategorie",
        field_type=DocumentMetadataField.FieldType.CHOICE,
        choices=["Rechnung", "Vertrag"],
        actor=user,
    ).execute()
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "update_metadata",
            "metadata_kategorie": "Rechnung",
        },
    )

    document.refresh_from_db()
    assert response.status_code == 302
    assert document.metadata == {"kategorie": "Rechnung"}
    assert AuditEvent.objects.filter(event_type="document_metadata.updated").exists()


@pytest.mark.django_db
def test_choice_metadata_can_be_extended_from_document_detail(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    metadata_field = CreateDocumentMetadataField(
        tenant=tenant,
        space=space,
        name="Kategorie",
        slug="kategorie",
        field_type=DocumentMetadataField.FieldType.CHOICE,
        choices=["Rechnung"],
        allow_custom_choices=True,
    ).execute()
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "metadata-choice-control" in content
    assert "data-metadata-choice-add" in content
    assert "document-metadata.js" in content

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "update_metadata",
            "metadata_kategorie": "",
            "metadata_kategorie_new_choice": "Gutschrift",
        },
    )

    document.refresh_from_db()
    metadata_field.refresh_from_db()
    assert response.status_code == 302
    assert document.metadata == {"kategorie": "Gutschrift"}
    assert metadata_field.choices == ["Rechnung", "Gutschrift"]
    assert AuditEvent.objects.filter(
        event_type="document_metadata_field.choice_added",
        object_id=str(metadata_field.id),
    ).exists()


@pytest.mark.django_db
def test_child_document_box_inherits_parent_metadata_fields(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    parent_space = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    child_space = CreateDocumentSpace(
        tenant=tenant,
        parent=parent_space,
        name="Eingang",
        slug="eingang",
    ).execute()
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
    CreateDocumentMetadataField(
        tenant=tenant,
        space=parent_space,
        name="Kostenstelle",
        slug="kostenstelle",
        field_type=DocumentMetadataField.FieldType.TEXT,
    ).execute()
    CreateDocumentMetadataField(
        tenant=tenant,
        space=child_space,
        name="Projekt",
        slug="projekt",
        field_type=DocumentMetadataField.FieldType.TEXT,
    ).execute()
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Eingangsrechnung",
        space=child_space,
        file_obj=BytesIO(b"child inherited metadata"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Kostenstelle" in content
    assert "Projekt" in content

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "update_metadata",
            "metadata_kostenstelle": "4711",
            "metadata_projekt": "Umbau",
        },
    )

    document.refresh_from_db()
    assert response.status_code == 302
    assert document.metadata == {
        "kostenstelle": "4711",
        "projekt": "Umbau",
    }


@pytest.mark.django_db
def test_child_document_box_rejects_parent_metadata_slug(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    parent_space = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    child_space = CreateDocumentSpace(
        tenant=tenant,
        parent=parent_space,
        name="Eingang",
        slug="eingang",
    ).execute()
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
    CreateDocumentMetadataField(
        tenant=tenant,
        space=parent_space,
        name="Kostenstelle",
        slug="kostenstelle",
        field_type=DocumentMetadataField.FieldType.TEXT,
    ).execute()
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:settings_metadata_field_create",
            kwargs={"tenant_slug": tenant.slug, "box_id": child_space.id},
        ),
        {
            "name": "Kostenstelle lokal",
            "slug": "kostenstelle",
            "field_type": DocumentMetadataField.FieldType.TEXT,
            "help_text": "",
            "choices_text": "",
            "einvoice_source": "",
            "sort_order": "10",
            "is_active": "on",
        },
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Eltern- oder Kindbox" in content
    assert DocumentMetadataField.objects.filter(space=child_space).count() == 0


@pytest.mark.django_db
def test_document_detail_uses_return_url_and_document_navigation(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    previous_document, _previous_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Rechnung 1",
        space=space,
        file_obj=BytesIO(b"previous"),
        original_filename="previous.pdf",
        content_type="application/pdf",
    ).execute()
    current_document, _current_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Rechnung 2",
        space=space,
        file_obj=BytesIO(b"current"),
        original_filename="current.pdf",
        content_type="application/pdf",
    ).execute()
    next_document, _next_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Rechnung 3",
        space=space,
        file_obj=BytesIO(b"next"),
        original_filename="next.pdf",
        content_type="application/pdf",
    ).execute()
    back_url = reverse("documents:list", kwargs={"tenant_slug": tenant.slug})
    document_nav = f"{previous_document.id},{current_document.id},{next_document.id}"
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": current_document.id},
        ),
        {"back": back_url, "nav": document_nav},
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert f'href="{back_url}"' in content
    assert "Vorheriges" in content
    assert "Nächstes" in content
    assert "Dokument 2 von 3" in content
    assert "Weitere Datei" not in content
    assert (
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": previous_document.id},
        )
        in content
    )
    assert (
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": next_document.id},
        )
        in content
    )


@pytest.mark.django_db
def test_document_core_metadata_edit_prefills_fields(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
        document_date=date(2026, 3, 14),
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:core_metadata_edit",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="title"' in content
    assert 'value="Invoice 4711"' in content
    assert 'name="document_date"' in content
    assert 'value="2026-03-14"' in content
    assert 'name="space"' in content


@pytest.mark.django_db
def test_document_core_metadata_edit_updates_core_metadata(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Alter Titel",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
        document_date=date(2026, 3, 14),
    ).execute()
    document.title_source = Document.TitleSource.OCR
    document.save(update_fields=["title_source"])
    client.force_login(user)
    back_url = (
        reverse("search:documents", kwargs={"tenant_slug": tenant.slug}) + "?q=alt"
    )
    document_nav = str(document.id)

    response = client.post(
        (
            reverse(
                "documents:core_metadata_edit",
                kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
            )
            + f"?back={quote(back_url, safe='')}&nav={document_nav}"
        ),
        {
            "title": "Neuer Titel",
            "document_date": "2026-04-01",
            "space": space.id,
        },
    )

    document.refresh_from_db()
    event = AuditEvent.objects.get(event_type="document_core_metadata.updated")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"?back={quote(back_url, safe='')}&nav={document_nav}"
    )
    assert document.title == "Neuer Titel"
    assert document.title_source == Document.TitleSource.MANUAL
    assert document.document_date == date(2026, 4, 1)
    assert event.data["previous_title"] == "Alter Titel"
    assert event.data["title"] == "Neuer Titel"
    assert event.data["space_changed"] is False


@pytest.mark.django_db
def test_document_core_metadata_edit_moves_document_and_cleans_workflows(
    client,
    django_capture_on_commit_callbacks,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    old_space = CreateDocumentSpace(
        tenant=tenant,
        name="Eingangsrechnungen",
        slug="eingangsrechnungen",
    ).execute()
    new_space = CreateDocumentSpace(
        tenant=tenant,
        name="Geprüfte Rechnungen",
        slug="gepruefte-rechnungen",
    ).execute()
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
    old_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Alte Prüfung",
        slug="alte-pruefung",
        trigger_type=WorkflowTemplate.TriggerType.DOCUMENT_CREATED,
        trigger_document_space=old_space,
    ).execute()
    CreateWorkflowStep(
        template=old_template,
        name="Alt prüfen",
        step_type="task",
    ).execute()
    new_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Neue Prüfung",
        slug="neue-pruefung",
        trigger_type=WorkflowTemplate.TriggerType.DOCUMENT_CREATED,
        trigger_document_space=new_space,
    ).execute()
    CreateWorkflowStep(
        template=new_template,
        name="Neu prüfen",
        step_type="task",
    ).execute()
    with django_capture_on_commit_callbacks(execute=True):
        document, _document_file = CreateDocumentFromUpload(
            tenant=tenant,
            title="Rechnung",
            space=old_space,
            file_obj=BytesIO(b"invoice content to move"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            created_by=user,
        ).execute()
    old_instance = WorkflowInstance.objects.get(
        document=document,
        template=old_template,
    )
    old_task = WorkflowTask.objects.get(instance=old_instance)
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse(
                "documents:core_metadata_edit",
                kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
            ),
            {
                "title": "Rechnung verschoben",
                "document_date": "",
                "space": new_space.id,
            },
        )

    document.refresh_from_db()
    old_instance.refresh_from_db()
    old_task.refresh_from_db()
    new_instance = WorkflowInstance.objects.get(
        document=document,
        template=new_template,
    )
    new_task = WorkflowTask.objects.get(instance=new_instance)
    event = AuditEvent.objects.get(
        event_type="document_core_metadata.updated",
        object_id=str(document.id),
    )

    assert response.status_code == 302
    assert document.space == new_space
    assert old_instance.status == WorkflowInstance.Status.CANCELLED
    assert old_task.status == WorkflowTask.Status.CANCELLED
    assert new_instance.status == WorkflowInstance.Status.RUNNING
    assert new_task.status == WorkflowTask.Status.OPEN
    assert event.data["space_changed"] is True
    assert event.data["previous_space_path"] == old_space.path
    assert event.data["space_path"] == new_space.path
    assert AuditEvent.objects.filter(
        event_type="workflow_instance.cancelled",
        object_id=str(old_instance.id),
    ).exists()


@pytest.mark.django_db
def test_admin_can_soft_delete_document_and_cancel_open_workflows(client):
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
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnungspruefung",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Prüfen",
        step_type="task",
    ).execute()
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Zu löschen",
        space=space,
        file_obj=BytesIO(b"delete me"),
        original_filename="delete-me.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()
    instance = StartWorkflowForDocument(
        template=template,
        document=document,
        actor=user,
    ).execute()
    task = WorkflowTask.objects.get(instance=instance)
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:delete",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert '<select name="reason"' in content
    assert "<option value=\"Testupload\">Testupload</option>" in content
    assert "<textarea" not in content

    response = client.post(
        reverse(
            "documents:delete",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {"reason": "Testupload"},
    )

    document.refresh_from_db()
    instance.refresh_from_db()
    task.refresh_from_db()
    event = AuditEvent.objects.get(
        event_type="document.deleted",
        object_id=str(document.id),
    )
    assert response.status_code == 302
    assert document.status == Document.Status.DELETED
    assert document.deleted_reason == "Testupload"
    assert document.deleted_by == user
    assert document.deleted_at is not None
    assert instance.status == WorkflowInstance.Status.CANCELLED
    assert task.status == WorkflowTask.Status.CANCELLED
    assert event.data["reason"] == "Testupload"
    assert event.data["cancelled_workflow_instance_ids"] == [instance.id]

    response = client.get(
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug})
    )
    assert response.context["documents_count"] == 0
    assert "Zu löschen" not in response.content.decode()

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_member_without_delete_permission_cannot_delete_document(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Nicht löschbar",
        space=space,
        file_obj=BytesIO(b"content"),
        original_filename="document.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()
    client.force_login(user)

    detail_response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    delete_response = client.post(
        reverse(
            "documents:delete",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {"reason": "Soll nicht gehen"},
    )

    document.refresh_from_db()
    assert detail_response.status_code == 200
    assert "Dokument löschen" not in detail_response.content.decode()
    assert delete_response.status_code == 403
    assert document.status == Document.Status.ACTIVE
    assert not AuditEvent.objects.filter(event_type="document.deleted").exists()


@pytest.mark.django_db
def test_update_document_metadata_writes_audit_event():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(username="alice")
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
    ).execute()

    UpdateDocumentMetadata(
        document=document,
        metadata={"vorgang": "Prüfung"},
        actor=user,
    ).execute()

    document.refresh_from_db()
    assert document.metadata == {"vorgang": "Prüfung"}
    assert AuditEvent.objects.filter(event_type="document_metadata.updated").exists()


@pytest.mark.django_db
def test_document_upload_view_rejects_user_without_tenant_membership(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    client.force_login(user)

    response = client.get(
        reverse("documents:upload", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_system_admin_can_access_tenant_without_membership(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    user = get_user_model().objects.create_superuser(
        username="admin",
        password="secret",
    )
    client.force_login(user)

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_dashboard_shows_latest_10_uploads(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    for index in range(12):
        CreateDocumentFromUpload(
            tenant=tenant,
            title=f"Dokument {index}",
            space=space,
            file_obj=BytesIO(f"content {index}".encode()),
            original_filename=f"document-{index}.pdf",
            content_type="application/pdf",
        ).execute()
    client.force_login(user)

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})
    )

    documents = list(response.context["documents"])
    assert response.status_code == 200
    assert len(documents) == 10
    assert documents[0].title == "Dokument 11"
    assert documents[-1].title == "Dokument 2"
    content = response.content.decode()
    assert "Letzte Uploads" in content
    assert "Dateityp" in content
    assert "PDF" in content
    assert "Dateien" not in content
    assert response.context["documents_count"] == 12

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug}),
        {"uploads_page": "2"},
    )

    documents = list(response.context["documents"])
    assert len(documents) == 2
    assert documents[0].title == "Dokument 1"
    assert documents[-1].title == "Dokument 0"


@pytest.mark.django_db
def test_document_list_paginates_documents(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    for index in range(30):
        document, _document_file = CreateDocumentFromUpload(
            tenant=tenant,
            title=f"Dokument {index}",
            space=space,
            file_obj=BytesIO(f"content {index}".encode()),
            original_filename=f"document-{index}.pdf",
            content_type="application/pdf",
        ).execute()
        if index == 29:
            template = CreateWorkflowTemplate(
                tenant=tenant,
                name="Freigabe",
                slug="freigabe",
            ).execute()
            CreateWorkflowStep(
                template=template,
                name="Sachlich prüfen",
                step_type="task",
                assigned_role=roles["viewer"],
            ).execute()
            StartWorkflowForDocument(
                template=template,
                document=document,
            ).execute()
    client.force_login(user)

    response = client.get(
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug})
    )

    documents = list(response.context["documents"])
    assert response.status_code == 200
    assert len(documents) == 25
    assert response.context["documents_count"] == 30
    content = response.content.decode()
    assert "Letzte Uploads" not in content
    assert "Dateityp" in content
    assert "PDF" in content
    assert "Dateien" not in content
    assert "Workflow offen 0/1" in content

    response = client.get(
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug}),
        {"page": "2"},
    )

    documents = list(response.context["documents"])
    assert len(documents) == 5
    assert documents[0].title == "Dokument 4"


@pytest.mark.django_db
def test_document_list_uses_thumbnail_in_document_row(client, monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    monkeypatch.setattr(
        "doksio.documents.thumbnails._render_thumbnail_bytes",
        lambda _document_file: b"thumbnail-bytes",
    )
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Bildbeleg",
        space=space,
        file_obj=BytesIO(b"image content"),
        original_filename="receipt.png",
        content_type="image/png",
        auto_start_ocr=False,
    ).execute()
    thumbnail = DocumentFile.objects.get(
        document=document,
        file_kind=DocumentFile.Kind.THUMBNAIL,
    )
    client.force_login(user)

    response = client.get(
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "document-row-thumbnail" in content
    assert (
        reverse(
            "documents:download",
            kwargs={"tenant_slug": tenant.slug, "file_id": thumbnail.id},
        )
        in content
    )
    assert "Vorschau Bildbeleg" in content


@pytest.mark.django_db
def test_document_detail_uses_converted_pdf_for_tiff(client, monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    monkeypatch.setattr(
        "doksio.documents.thumbnails._render_thumbnail_bytes",
        lambda _document_file: b"thumbnail-bytes",
    )
    document, original_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="TIFF Scan",
        space=space,
        file_obj=BytesIO(_single_page_tiff_bytes()),
        original_filename="scan.tiff",
        content_type="image/tiff",
        auto_start_ocr=False,
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert original_file.content_type == "application/pdf"
    assert original_file.original_filename == "scan.pdf"
    assert "data-pdf-preview" in content
    assert "data-image-preview" not in content
    original_url = reverse(
        "documents:download",
        kwargs={"tenant_slug": tenant.slug, "file_id": original_file.id},
    )
    assert original_url in content


@pytest.mark.django_db
def test_tiff_import_keeps_bilevel_scans_compact(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    monkeypatch.setattr(
        "doksio.documents.thumbnails._render_thumbnail_bytes",
        lambda _document_file: b"thumbnail-bytes",
    )

    _document, original_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="TIFF Scan",
        space=space,
        file_obj=BytesIO(_large_bilevel_tiff_bytes()),
        original_filename="scan.tiff",
        content_type="image/tiff",
        auto_start_ocr=False,
    ).execute()

    with default_storage.open(original_file.storage_key, "rb") as stored_file:
        stored_pdf = stored_file.read()

    assert original_file.content_type == "application/pdf"
    assert original_file.byte_size < 10_000
    assert b"CCITTFaxDecode" in stored_pdf
    assert b"DCTDecode" not in stored_pdf


@pytest.mark.django_db
def test_optimize_document_box_scans_replaces_bloated_scan_pdf(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    monkeypatch.setattr(
        "doksio.documents.thumbnails._render_thumbnail_bytes",
        lambda _document_file: b"thumbnail-bytes",
    )
    _document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Scan PDF",
        space=space,
        file_obj=BytesIO(_bloated_scanned_pdf_bytes()),
        original_filename="scan.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()
    old_storage_key = document_file.storage_key
    old_byte_size = document_file.byte_size

    with django_capture_on_commit_callbacks(execute=True):
        result = OptimizeDocumentBoxScans(
            tenant=tenant,
            document_space=space,
        ).execute()

    document_file.refresh_from_db()
    assert result.candidates == 1
    assert result.optimized == 1
    assert document_file.byte_size < old_byte_size
    assert document_file.storage_key != old_storage_key
    assert not default_storage.exists(old_storage_key)
    assert AuditEvent.objects.filter(
        event_type="document_file.scan_optimized",
        object_id=str(document_file.id),
    ).exists()
    assert AuditEvent.objects.filter(
        event_type="document_box.scan_optimization.completed",
        object_id=str(space.id),
    ).exists()


@pytest.mark.django_db
def test_document_list_shows_einvoice_signal_in_document_row(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
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
    CreateDocumentFromUpload(
        tenant=tenant,
        title="",
        space=space,
        file_obj=BytesIO(_zugferd_pdf_bytes()),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse("documents:list", kwargs={"tenant_slug": tenant.slug})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "document-row-einvoice-indicator" in content
    assert "eRechnungs-Daten vorhanden" in content
    assert "document-row-signal-einvoice" not in content


@pytest.mark.django_db
def test_tenant_admin_can_create_document_box_from_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
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
            "documents:settings_document_box_create",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "name": "Rechnungen",
            "slug": "rechnungen",
            "parent": "",
            "description": "",
        },
    )

    assert response.status_code == 302
    box = DocumentSpace.objects.get(tenant=tenant)
    assert box.path == "/rechnungen"
    assert box.review_assist_enabled is False


@pytest.mark.django_db
def test_tenant_admin_can_start_scan_optimization_from_maintenance(
    client,
    monkeypatch,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["admin"],
    )
    scheduled_jobs = []

    def fake_delay(document_space_id, **kwargs):
        scheduled_jobs.append((document_space_id, kwargs))

    monkeypatch.setattr(
        "doksio.documents.tasks.optimize_document_box_scans.delay",
        fake_delay,
    )
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:settings_maintenance",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "space": str(space.id),
            "include_children": "on",
        },
    )

    assert response.status_code == 302
    assert scheduled_jobs == [
        (
            space.id,
            {
                "include_children": True,
                "actor_id": user.id,
            },
        )
    ]

    get_response = client.get(
        reverse(
            "documents:settings_maintenance",
            kwargs={"tenant_slug": tenant.slug},
        )
    )
    content = get_response.content.decode()
    assert get_response.status_code == 200
    assert "Wartung" in content
    assert "Scan-Speicher" in content
    assert "Scan-Speicher optimieren" in content


@pytest.mark.django_db
def test_tenant_admin_can_update_document_box_from_settings(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    box = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    child = CreateDocumentSpace(
        tenant=tenant,
        parent=box,
        name="Archiv",
        slug="archiv",
    ).execute()
    user = get_user_model().objects.create_user(
        username="alice",
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
            "documents:settings_document_box_edit",
            kwargs={"tenant_slug": tenant.slug, "box_id": box.id},
        ),
        {
            "name": "Buchhaltung",
            "slug": "buchhaltung",
            "parent": "",
            "description": "Finanzdokumente",
            "is_active": "on",
        },
    )

    box.refresh_from_db()
    child.refresh_from_db()
    assert response.status_code == 302
    assert box.path == "/buchhaltung"
    assert box.review_assist_enabled is False
    assert child.path == "/buchhaltung/archiv"


@pytest.mark.django_db
def test_tenant_admin_can_delete_document_box_and_move_documents(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    source = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    child = CreateDocumentSpace(
        tenant=tenant,
        parent=source,
        name="Archiv",
        slug="archiv",
    ).execute()
    target = CreateDocumentSpace(
        tenant=tenant,
        name="Zielbox",
        slug="zielbox",
    ).execute()
    similar_prefix = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen Alt",
        slug="rechnungen-alt",
    ).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["admin"],
    )
    client.force_login(user)
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=child,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
        auto_start_ocr=False,
    ).execute()

    response = client.post(
        reverse(
            "documents:settings_document_box_delete",
            kwargs={"tenant_slug": tenant.slug, "box_id": source.id},
        ),
        {
            "strategy": "move",
            "target_space": str(target.id),
            "delete_reason": "",
        },
    )

    source.refresh_from_db()
    child.refresh_from_db()
    target.refresh_from_db()
    similar_prefix.refresh_from_db()
    document.refresh_from_db()
    assert response.status_code == 302
    assert source.deleted_at is not None
    assert child.deleted_at is not None
    assert source.is_active is False
    assert child.is_active is False
    assert document.space == target
    assert document.status == Document.Status.ACTIVE
    assert target.deleted_at is None
    assert similar_prefix.deleted_at is None
    assert AuditEvent.objects.filter(event_type="document_space.deleted").exists()

    list_response = client.get(
        reverse(
            "documents:settings_document_boxes",
            kwargs={"tenant_slug": tenant.slug},
        )
    )
    content = list_response.content.decode()
    assert "/rechnungen</td>" not in content
    assert "/zielbox" in content


@pytest.mark.django_db
def test_tenant_admin_can_delete_document_box_and_delete_documents(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    source = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["admin"],
    )
    client.force_login(user)
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Invoice 4711",
        space=source,
        file_obj=BytesIO(b"invoice content"),
        original_filename="invoice.pdf",
        content_type="application/pdf",
        created_by=user,
        auto_start_ocr=False,
    ).execute()

    response = client.post(
        reverse(
            "documents:settings_document_box_delete",
            kwargs={"tenant_slug": tenant.slug, "box_id": source.id},
        ),
        {
            "strategy": "delete_documents",
            "target_space": "",
            "delete_reason": "Falsche Dokumentenbox",
        },
    )

    source.refresh_from_db()
    document.refresh_from_db()
    assert response.status_code == 302
    assert source.deleted_at is not None
    assert source.is_active is False
    assert document.status == Document.Status.DELETED
    assert document.deleted_reason == "Falsche Dokumentenbox"
    assert AuditEvent.objects.filter(event_type="document.deleted").exists()
    assert AuditEvent.objects.filter(event_type="document_space.deleted").exists()


@pytest.mark.django_db
def test_tenant_admin_can_hard_empty_document_box(
    client,
    django_capture_on_commit_callbacks,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    source = CreateDocumentSpace(
        tenant=tenant,
        name="Testbox",
        slug="testbox",
    ).execute()
    child = CreateDocumentSpace(
        tenant=tenant,
        parent=source,
        name="Kindbox",
        slug="kindbox",
    ).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["admin"],
    )
    client.force_login(user)
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Testdokument",
        space=source,
        file_obj=BytesIO(b"test content"),
        original_filename="test.pdf",
        content_type="application/pdf",
        created_by=user,
        auto_start_ocr=False,
    ).execute()
    child_document, _child_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Kinddokument",
        space=child,
        file_obj=BytesIO(b"child content"),
        original_filename="child.pdf",
        content_type="application/pdf",
        created_by=user,
        auto_start_ocr=False,
    ).execute()
    storage_key = document_file.storage_key
    OcrJob.objects.create(
        tenant=tenant,
        document_file=document_file,
        status=OcrJob.Status.SUCCEEDED,
        extracted_text="Test OCR",
    )
    DocumentSearchIndex.objects.create(
        tenant=tenant,
        document=document,
        title=document.title,
        combined_text="Test OCR",
    )
    export_run = ExportRun.objects.create(
        tenant=tenant,
        created_by=user,
    )
    ExportRunItem.objects.create(
        tenant=tenant,
        export_run=export_run,
        document=document,
        document_file=document_file,
        status=ExportRunItem.Status.EXPORTED,
    )
    assert default_storage.exists(storage_key)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse(
                "documents:settings_document_box_empty",
                kwargs={"tenant_slug": tenant.slug, "box_id": source.id},
            ),
            {"confirm_name": "Testbox"},
        )

    source.refresh_from_db()
    child.refresh_from_db()
    child_document.refresh_from_db()
    assert response.status_code == 302
    assert source.deleted_at is None
    assert source.is_active is True
    assert child.deleted_at is None
    assert child_document.space == child
    assert not Document.objects.filter(id=document.id).exists()
    assert not DocumentFile.objects.filter(id=document_file.id).exists()
    assert not OcrJob.objects.filter(document_file_id=document_file.id).exists()
    assert not DocumentSearchIndex.objects.filter(document_id=document.id).exists()
    assert not ExportRunItem.objects.filter(document_id=document.id).exists()
    assert not default_storage.exists(storage_key)
    assert AuditEvent.objects.filter(
        event_type="document_space.emptied",
        object_id=str(source.id),
    ).exists()


@pytest.mark.django_db
def test_document_box_hard_empty_requires_exact_box_name(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    source = CreateDocumentSpace(
        tenant=tenant,
        name="Testbox",
        slug="testbox",
    ).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["admin"],
    )
    client.force_login(user)
    document, document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Testdokument",
        space=source,
        file_obj=BytesIO(b"test content"),
        original_filename="test.pdf",
        content_type="application/pdf",
        created_by=user,
        auto_start_ocr=False,
    ).execute()
    storage_key = document_file.storage_key

    response = client.post(
        reverse(
            "documents:settings_document_box_empty",
            kwargs={"tenant_slug": tenant.slug, "box_id": source.id},
        ),
        {"confirm_name": "Falsch"},
    )

    assert response.status_code == 200
    assert Document.objects.filter(id=document.id).exists()
    assert DocumentFile.objects.filter(id=document_file.id).exists()
    assert default_storage.exists(storage_key)
    assert not AuditEvent.objects.filter(event_type="document_space.emptied").exists()


@pytest.mark.django_db
def test_tenant_member_cannot_access_document_box_settings(client):
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
        reverse(
            "documents:settings_document_boxes",
            kwargs={"tenant_slug": tenant.slug},
        )
    )

    assert response.status_code == 403
