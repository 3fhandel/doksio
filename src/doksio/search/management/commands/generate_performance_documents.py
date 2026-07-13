from __future__ import annotations

from itertools import cycle

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from doksio.documents.models import Document, DocumentSpace
from doksio.search.models import DocumentSearchIndex
from doksio.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Generate synthetic tenant documents for local performance checks."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", dest="tenant_slug", default="demo")
        parser.add_argument("--count", type=int, default=50000)
        parser.add_argument("--batch-size", type=int, default=1000)

    def handle(self, *args, **options) -> None:
        tenant = Tenant.objects.get(slug=options["tenant_slug"])
        count = max(int(options["count"]), 1)
        batch_size = max(int(options["batch_size"]), 1)
        spaces = list(DocumentSpace.objects.filter(tenant=tenant, is_active=True))
        if not spaces:
            raise SystemExit("Tenant has no active document boxes.")

        now = timezone.now()
        space_cycle = cycle(spaces)
        created = 0
        self.stdout.write(
            f"Generating {count} synthetic document(s) for tenant {tenant.slug}."
        )
        while created < count:
            size = min(batch_size, count - created)
            documents = []
            for offset in range(size):
                number = created + offset + 1
                space = next(space_cycle)
                documents.append(
                    Document(
                        tenant=tenant,
                        space=space,
                        title=f"Lasttest Dokument {number:06d}",
                        title_source=Document.TitleSource.MANUAL,
                        document_date=now.date(),
                        metadata={
                            "lasttest": True,
                            "nummer": str(number),
                            "box": space.name,
                        },
                        created_at=now,
                        updated_at=now,
                    )
                )
            with transaction.atomic():
                created_documents = Document.objects.bulk_create(
                    documents,
                    batch_size=batch_size,
                )
                DocumentSearchIndex.objects.bulk_create(
                    [
                        DocumentSearchIndex(
                            tenant=tenant,
                            document=document,
                            title=document.title,
                            metadata_text=(
                                f"lasttest {document.metadata['nummer']} "
                                f"{document.metadata['box']}"
                            ),
                            combined_text=(
                                f"{document.title}\nlasttest "
                                f"{document.metadata['nummer']} "
                                f"{document.metadata['box']}"
                            ),
                            updated_at=now,
                        )
                        for document in created_documents
                    ],
                    batch_size=batch_size,
                )
            created += size
            self.stdout.write(f"{created}/{count}")

        self.stdout.write(
            self.style.SUCCESS(f"Generated {created} synthetic documents.")
        )
