# ADR 0007: URL Scopes and Document Boxes

## Status

Accepted

## Context

DoMaSy is designed as a tenant-aware system that may later run as SaaS. URLs
should make the current scope obvious. Tenant-level features and system-level
features should not share ambiguous paths.

The document model also needs a single clear classification axis. Keeping both
document boxes and document types as required top-level concepts would create
duplicated configuration and unclear ownership of permissions, metadata and
workflows.

## Decision

System-level routes use `/s/...`.

Tenant-level routes use `/t/<tenant-slug>/...`.

The root route `/` may redirect, but business and administration features should
live under either `/s/...` or `/t/<tenant-slug>/...`.

Documents are assigned to exactly one hierarchical document box.
`DocumentSpace` replaces the previous required `document_type` field.

The document box is tenant-scoped and stores a stable path such as:

- `/rechnungen`
- `/rechnungen/eingangsrechnungen`
- `/personalakten`
- `/vertraege`

## Consequences

- URLs clearly show whether the user is in system scope or tenant scope.
- Tenant login lives at `/t/<tenant-slug>/`.
- System login lives at `/s/`.
- Future tenant settings live under `/t/<tenant-slug>/settings/...`.
- Future system settings live under `/s/...`.
- Permissions, metadata rules, workflows, imports and exports can attach to
  document boxes without duplicating a separate required document type model.
- More specific classifications may be added later as optional metadata inside a
  document box.
