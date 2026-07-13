# ADR 0006: Modular Architecture and Code Quality

## Status

Accepted

## Context

Doksio is intended to become a clean, maintainable business application. The
project should not trade long-term architecture quality for quick-and-dirty
implementation speed.

The application will include document storage, tenant scoping, permissions,
audit logging, OCR, search, workflows and exports. These concerns can easily
become tangled if business logic is spread across views, models, forms, signals
and background tasks.

## Decision

Doksio will optimize for a clean, modular architecture.

Core principles:

- Use a modular Django monolith with clear domain apps.
- Put important business actions into named application services or use-case
  classes.
- Keep views thin.
- Keep Celery tasks thin.
- Keep templates focused on presentation.
- Avoid large "god models".
- Avoid hidden business flows in Django signals.
- Enforce tenant scoping consistently.
- Treat permissions, audit logging and immutable document storage as first-class
  architectural concerns.
- Add focused tests for central domain rules from the beginning.

Examples of named application services:

- `ImportDocument`
- `StoreImmutableFile`
- `RunOcrForDocumentFile`
- `StartWorkflowForDocument`
- `CompleteWorkflowTask`
- `ExportDocumentFile`
- `AddDocumentComment`
- `UpdateDocumentMetadata`

## Consequences

- The codebase should remain easier to understand and extend.
- Business processes become explicit and testable.
- Background workers and web views can share the same application logic.
- The project may move slightly slower at the beginning, but should avoid costly
  rewrites caused by tangled responsibilities.
