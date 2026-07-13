from __future__ import annotations

from time import perf_counter

from django.core.management.base import BaseCommand, CommandParser

from doksio.search.services import SearchDocuments
from doksio.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Run a small local benchmark for tenant document search."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("query", nargs="?", default="lasttest")
        parser.add_argument("--tenant", dest="tenant_slug", default="demo")
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument(
            "--explain",
            action="store_true",
            help="Print the database query plan for the search queryset.",
        )

    def handle(self, *args, **options) -> None:
        tenant = Tenant.objects.get(slug=options["tenant_slug"])
        query = options["query"]
        limit = max(int(options["limit"]), 1)
        queryset = SearchDocuments(
            tenant=tenant,
            filters={"q": query, "sort": "relevance"},
        ).execute()

        if options["explain"]:
            self.stdout.write(queryset.explain())

        started_at = perf_counter()
        count = queryset.count()
        count_seconds = perf_counter() - started_at

        started_at = perf_counter()
        results = list(queryset[:limit])
        page_seconds = perf_counter() - started_at

        self.stdout.write(f"Tenant: {tenant.slug}")
        self.stdout.write(f"Query: {query}")
        self.stdout.write(f"Count: {count} in {count_seconds:.3f}s")
        self.stdout.write(f"First {len(results)} rows in {page_seconds:.3f}s")
        if results:
            self.stdout.write("First result: " + results[0].title)

