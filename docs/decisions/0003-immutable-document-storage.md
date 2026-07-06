# ADR 0003: Immutable Document Storage

## Status

Accepted

## Context

DoMaSy is a document management system, not a document editor. Users may attach
metadata, comments, annotations and workflow decisions to documents, but the
stored original files must remain unchanged.

The legally relevant archive for accounting documents can be DATEV Unternehmen
online or another configured archival export target. DoMaSy should still be as
revision-friendly and traceable as possible.

## Decision

DoMaSy stores original document files immutably.

- Original files are never edited, overwritten or rewritten.
- Each stored file receives a checksum.
- A file replacement is modeled as an additional immutable file/version.
- OCR output, previews, thumbnails and extracted text are derivatives and never
  change the original file.
- Metadata, comments, annotations, workflow state and audit history are modeled
  separately from the file.
- Deletion must be audit-aware and should support soft-delete or retention
  restrictions.

## Consequences

- Revisions are easier to reason about.
- Export runs can reference the exact file version that was exported.
- Audit history can explain changes around a document without implying that the
  original file changed.
- Later support for object-lock or WORM-like storage remains possible.
- DoMaSy should not claim certified legal archive behavior unless the storage,
  retention and process requirements are explicitly implemented and verified.
