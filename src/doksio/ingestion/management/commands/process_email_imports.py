from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from doksio.ingestion.models import ImportSource
from doksio.ingestion.services import (
    ProcessDueEmailImportSources,
    ProcessEmailImportSource,
)


class Command(BaseCommand):
    help = "Verarbeitet konfigurierte E-Mail-Importquellen."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--source-id",
            type=int,
            help="Nur eine bestimmte Importquelle verarbeiten.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Alle aktiven E-Mail-Quellen unabhängig vom Intervall verarbeiten.",
        )

    def handle(self, *args, **options) -> None:
        source_id = options.get("source_id")
        if source_id:
            result = self._process_single_source(source_id)
        elif options.get("all"):
            result = self._process_all_sources()
        else:
            result = ProcessDueEmailImportSources().execute()

        self.stdout.write(
            self.style.SUCCESS(
                "Mail-Import abgeschlossen: "
                f"{result.checked_messages} Mails geprüft, "
                f"{result.matched_attachments} passende Anhänge, "
                f"{result.ignored_attachments} ignorierte Anhänge, "
                f"{result.imported_documents} Dokumente importiert, "
                f"{result.duplicate_documents} Dubletten, "
                f"{result.failed_attachments} Fehler, "
                f"{result.unprocessable_messages} nicht importierbare Mails."
            )
        )
        for error in result.errors:
            self.stderr.write(self.style.ERROR(error))

    def _process_single_source(self, source_id: int):
        try:
            source = ImportSource.objects.select_related(
                "tenant",
                "document_space",
            ).get(
                id=source_id,
                source_type=ImportSource.SourceType.EMAIL,
            )
        except ImportSource.DoesNotExist as exc:
            raise CommandError("E-Mail-Importquelle nicht gefunden.") from exc
        return ProcessEmailImportSource(source=source).execute()

    def _process_all_sources(self):
        return ProcessDueEmailImportSources(force=True).execute()
