# ADR 0012: Central Document Title Discovery

## Status

Accepted

## Context

Automatic document title settings were initially stored in each import source.
That caused identical document boxes to behave differently depending on whether
a file arrived through upload, API, folder or email. A manually restarted OCR
job also had no reliable source-specific configuration.

Title discovery is document behavior and must follow the destination document
box, not the transport used to import the file.

## Decision

Doksio stores automatic title discovery in tenant-scoped
`DocumentTitleRule` records.

Resolution follows a fixed order:

1. An exact rule for the document's destination box.
2. The tenant-wide default rule.
3. The built-in automatic OCR strategy.

The available strategies are:

- automatic title extraction from OCR full text
- title formatting from structured eInvoice data
- regular-expression search and replacement on OCR full text
- disabled automatic title discovery

The eInvoice strategy uses a validated format string with a fixed set of
placeholders. A precision such as `{seller_name:.12}` truncates a value to the
specified number of characters. Because not every document contains structured
invoice data, every eInvoice rule also defines an OCR fallback: automatic,
regular expression or disabled. The fallback is used when no eInvoice data is
available or when a required placeholder has no value.

OCR jobs persist the resolved policy in their metadata when they are created.
This makes processing reproducible even if an administrator changes the rule
while a job is waiting.

Tenant administrators can explicitly recalculate all titles in a document box
through a resumable maintenance job. This operation intentionally replaces
manual titles. The job snapshots the effective rule for every included box at
startup, processes only the document ID range that existed then, updates the
search index in batches and falls back to the original filename when no title
can be derived.

All upload and import paths use the same resolver. Import sources no longer own
title settings. A title entered during manual upload or later edited in the
document core data is marked as manual and is never overwritten by OCR.

Existing import-source title settings are migrated to rules for their fixed,
fallback and routing-target document boxes. When multiple sources configured
the same box differently, the most recently updated source wins.

## Consequences

- A document box behaves consistently across upload, API, folder, email and
  batch import.
- Manual OCR retries use the same rule as the original import.
- Administrators manage title behavior in one tenant-level settings area.
- Adding a new import adapter does not require title-discovery configuration.
- OCR jobs remain auditable and deterministic after they have been queued.
