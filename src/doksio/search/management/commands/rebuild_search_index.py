from __future__ import annotations

from django.core.management.base import BaseCommand, CommandParser

from doksio.documents.models import Document
from doksio.search.services import RebuildDocumentSearchIndex
from doksio.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Rebuild denormalized document search indexes."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", dest="tenant_slug", help="Tenant slug")
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options) -> None:
        batch_size = max(int(options["batch_size"]), 1)
        tenant_slug = options.get("tenant_slug")
        documents = Document.objects.select_related("tenant").order_by("id")
        if tenant_slug:
            tenant = Tenant.objects.get(slug=tenant_slug)
            documents = documents.filter(tenant=tenant)

        total = documents.count()
        processed = 0
        self.stdout.write(f"Rebuilding search index for {total} document(s).")
        for document in documents.iterator(chunk_size=batch_size):
            RebuildDocumentSearchIndex(document=document).execute()
            processed += 1
            if processed % batch_size == 0:
                self.stdout.write(f"{processed}/{total}")

        self.stdout.write(self.style.SUCCESS(f"Rebuilt {processed} search indexes."))

