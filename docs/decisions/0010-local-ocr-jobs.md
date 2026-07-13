# ADR 0010: Local OCR Jobs

## Status

Accepted

## Context

Doksio should process document text locally. External OCR services are not part
of the intended architecture, because tenant documents may contain invoices,
personnel files and other business-sensitive data.

Document files are immutable. OCR must therefore read from stored files and
write derived text into separate state without modifying the original artifact.

## Decision

OCR is implemented as a tenant-scoped `OcrJob`.

The first implementation supports:

- pending, running, succeeded and failed job states
- local extraction for plain text files
- PDF text extraction through `pdftotext`
- scanned PDF OCR through `ocrmypdf`
- image OCR through `tesseract`
- automatic OCR start after upload/import-style document creation for supported
  content types
- automatic Belegdatum prefill from OCR text when the document does not already
  have one
- automatic document title prefill from OCR text when the upload title was left
  empty and the current title is only filename-derived
- audit events for job creation, success and failure
- inline execution for local development and tests
- Celery execution for normal background processing

Document creation schedules OCR after the database commit, so background workers
only see committed documents and files. The web layer may also start OCR through
an application service for retries. The Celery task stays thin and delegates
execution to the same service.

## Consequences

- OCR processing is explicit, testable and suitable for background execution.
- Original document files remain unchanged.
- Failed OCR attempts remain visible to users and administrators.
- Later search indexing can consume successful OCR jobs without coupling search
  directly to file storage or upload views.
