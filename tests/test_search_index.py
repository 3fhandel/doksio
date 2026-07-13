from __future__ import annotations

from io import BytesIO

import pytest
from django.core.management import call_command

from doksio.documents.models import Document
from doksio.documents.services import CreateDocumentFromUpload, CreateDocumentSpace
from doksio.search.models import DocumentSearchIndex
from doksio.tenancy.models import Tenant


@pytest.mark.django_db
def test_rebuild_search_index_command_recreates_missing_index():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Index Rechnung",
        space=space,
        file_obj=BytesIO(b"invoice content"),
        original_filename="rechnung.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()
    DocumentSearchIndex.objects.filter(document=document).delete()

    call_command("rebuild_search_index", "--tenant", tenant.slug, verbosity=0)

    search_index = DocumentSearchIndex.objects.get(document=document)
    assert search_index.tenant == tenant
    assert "Index Rechnung" in search_index.combined_text
    assert "rechnung.pdf" in search_index.combined_text


@pytest.mark.django_db
def test_generate_performance_documents_creates_documents_and_indexes():
    tenant = Tenant.objects.create(name="Demo GmbH", slug="demo")
    CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()

    call_command(
        "generate_performance_documents",
        "--tenant",
        tenant.slug,
        "--count",
        "12",
        "--batch-size",
        "5",
        verbosity=0,
    )

    assert Document.objects.filter(tenant=tenant).count() == 12
    assert DocumentSearchIndex.objects.filter(tenant=tenant).count() == 12
    assert DocumentSearchIndex.objects.filter(
        tenant=tenant,
        combined_text__icontains="lasttest",
    ).count() == 12


@pytest.mark.django_db
def test_benchmark_search_command_reports_count(capsys):
    tenant = Tenant.objects.create(name="Demo GmbH", slug="demo")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    CreateDocumentFromUpload(
        tenant=tenant,
        title="Benchmark Rechnung",
        space=space,
        file_obj=BytesIO(b"benchmark"),
        original_filename="benchmark.pdf",
        content_type="application/pdf",
        auto_start_ocr=False,
    ).execute()

    call_command(
        "benchmark_search",
        "Benchmark",
        "--tenant",
        tenant.slug,
        "--limit",
        "5",
    )

    output = capsys.readouterr().out
    assert "Tenant: demo" in output
    assert "Count: 1" in output
    assert "First result: Benchmark Rechnung" in output
