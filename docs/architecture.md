# DoMaSy Architecture Notes

This document records the current architecture direction for DoMaSy.

## Stack

- Backend: Python and Django
- Database: PostgreSQL
- Background processing: Celery
- Periodic triggers: cron or Celery Beat
- Broker/cache: Redis or RabbitMQ, with Redis preferred for a small start
- Object storage: S3-compatible storage, with MinIO for local/dockerized setups
- OCR: local-only OCR, initially Tesseract or OCRmyPDF-based
- Search: PostgreSQL full text search initially
- Auth: OIDC-compatible authentication, without hard dependency on a specific IdP
- Frontend: Django templates, HTMX, Alpine.js, Bootstrap and lucide SVG icons
- Document preview: PDF.js
- Deployment: Docker Compose first, Kubernetes-compatible later if needed

## High-Level Shape

DoMaSy should start as a modular Django monolith. The codebase should keep clear
domain boundaries, but avoid premature microservices.

Suggested Django apps:

- `tenancy`: tenants, organizations and tenant context
- `accounts`: users, groups, roles and OIDC claim mapping
- `documents`: hierarchical document boxes, documents, document files,
  versions, metadata and annotations
- `storage`: object storage integration, checksums and immutable file handling
- `ingestion`: upload, API import, mail import and watched folder import
- `ocr`: OCR jobs, text extraction and derived OCR artifacts
- `search`: full text indexing and permission-aware search
- `workflows`: workflow definitions, instances, tasks and actions
- `exports`: export targets, export runs and export adapters
- `audit`: append-only audit events

## Code Organization Principles

DoMaSy optimizes for clean, modular architecture over short-term speed.

Business logic should live in explicit application services or use-case classes,
not in views, templates, forms, Celery tasks or Django signals.

Examples of named application services:

- `ImportDocument`
- `StoreImmutableFile`
- `RunOcrForDocumentFile`
- `StartWorkflowForDocument`
- `CompleteWorkflowTask`
- `ExportDocumentFile`
- `AddDocumentComment`
- `UpdateDocumentMetadata`

Views should stay thin: validate the request, call an application service and
render a response. Celery tasks should also stay thin: load the required context
and call an application service.

Django signals should be used sparingly. Important business processes should be
explicitly started so that the control flow remains visible and testable.

Tenant scoping, permissions, audit logging and immutable file handling are core
architecture concerns and must not be added as afterthoughts.

## Tenant Access Control

DoMaSy separates system administration from tenant administration.

- System administrators are Django superusers and operate under `/s/...`.
- Tenant users operate under `/t/<tenant-slug>/...`.
- Tenant users are regular Django users without system-admin access unless they
  are explicitly superusers.
- Tenant memberships connect one Django user to one tenant.
- Tenant memberships can have multiple tenant roles.
- Tenant roles are tenant-scoped, editable permission bundles and can model
  departments or functional groups.
- Tenant roles can explicitly grant tenant-wide document box access or be
  limited to one or more document boxes. If neither tenant-wide access nor boxes
  are selected, the role grants no document box access.
- Multiple roles behave additively: permissions and allowed document boxes are
  combined across all active roles.
- Views and services should use the central `AccessControl` layer or policy
  helpers instead of checking role names directly.

The initial default tenant roles are:

- `admin`: tenant administration and document access
- `member`: regular document usage including upload
- `viewer`: read-only document access

## Document Model Principles

Documents are logical containers. Files are immutable artifacts.

- `Document` represents the business object.
- `Document.title_source` records whether the current title was entered
  manually, derived from the filename or prefilled from OCR.
- `Document.document_date` is the global Belegdatum. It is useful across
  document boxes and can be prefilled from OCR.
- `DocumentSpace` represents the tenant-scoped, hierarchical document box for a
  document.
- `DocumentFile` represents one immutable stored file.
- A `Document` belongs to exactly one `DocumentSpace`.
- `DocumentSpace` is the primary classification axis; there is no separate
  required document type axis.
- Original files are never edited or overwritten.
- OCR output, previews, thumbnails and extracted text are derivatives.
- OCR may prefill global document metadata such as Belegdatum, but must not
  overwrite values already set by a user or import rule.
- OCR may replace filename-derived document titles, but must not overwrite
  manually entered titles.
- Tags, metadata, comments, annotations, workflow state and audit events live
  around the immutable file.
- Generic comments and tags are available for all documents.
- Structured metadata is configured per document box through field definitions.
  Generic document fields such as amount or vendor must not be hard-coded
  globally.
- Document metadata values are stored on `Document.metadata` by field slug; field
  definitions provide label, type, required state, choice values and ordering.
- Replacing a file means adding a new `DocumentFile`, not modifying an existing
  one.

## Workflow Model

Workflows should be generic and configurable. Invoice approval is one workflow
template, not the hard-coded center of the product.

The first workflow slice contains:

- `WorkflowTemplate`: tenant-owned reusable workflow definition
- `WorkflowStep`: ordered step with type, instructions and optional assigned
  tenant role
- `WorkflowInstance`: one running or completed workflow attached to a document
- `WorkflowTask`: actionable task created from a step
- tenant permissions for using and managing workflows
- manual workflow start from the document detail view
- automatic workflow start after document creation/import, optionally limited
  to one document box and its child boxes
- task completion with optional required comment
- audit events for template changes, workflow start and task completion

Workflow building blocks:

- Triggers:
  - document imported
  - document space matched
  - metadata condition matched
  - manual start
- Steps:
  - create task
  - request approval
  - verify metadata
  - wait for OCR
  - run export
  - set status
- Conditions:
  - document space
  - amount threshold
  - metadata presence
- Actions:
  - assign user or group
  - require comment
  - start export
  - call webhook
  - finalize or lock workflow state

## Exports

Exports should be modeled as workflow-capable actions, not as one-off download
buttons.

DATEV is one possible export target. The system should support multiple export
targets over time.

Important export entities:

- `ExportTarget`: tenant-scoped target configuration
- `ExportRun`: one execution against a target
- `ExportItem`: one exported document file and its result

An export must reference the exact immutable `DocumentFile` version that was
exported.

## Frontend Direction

The UI should be task-oriented and optimized for document decisions.

Primary areas:

- My Tasks
- Inbox
- Search
- Documents and document boxes
- Workflows
- Administration

The central document view should combine:

- document list or search results
- document/PDF preview
- metadata
- workflow state
- comments and annotations
- available actions

The frontend stack should avoid a full SPA and a Node.js build pipeline at the
beginning:

- Django templates render pages and fragments.
- HTMX handles partial updates and server-driven interactions.
- Alpine.js handles local UI state.
- Bootstrap provides consistent no-build components.
- lucide SVG icons provide the default icon set.

## MVP Scope

The first usable version should include:

- tenant-aware document storage
- local development login and OIDC-ready structure
- user groups, roles and permissions
- manual upload and/or API import
- immutable object storage
- local OCR
- full text search
- document boxes or folders
- comments or annotations
- audit trail
- simple generic workflows with tasks and status transitions

Deferred from the first MVP:

- mail import
- watched folder import
- DATEV export implementation
- graphical workflow builder
- complex workflow condition language
- certified legal archive behavior
- automatic invoice data extraction

## OCR Model

OCR is modeled as an explicit, tenant-scoped background job around immutable
document files.

- `OcrJob` stores status, engine, language, extracted text and failure details.
- OCR reads an immutable `DocumentFile`; it never modifies or overwrites the
  stored original file.
- Document creation starts OCR automatically for supported content types.
- Web views can still start OCR through an application service, for example to
  retry a failed job.
- Celery tasks only load the job and delegate to the application service.
- Local extraction uses text extraction for text PDFs first and falls back to
  local OCR tooling for scanned PDFs and images.
- OCR failures are stored on the job and recorded in the audit trail instead of
  failing silently.

## Search Model

The first search slice is a tenant-scoped universal document search.

- Search lives under `/t/<tenant-slug>/search/`.
- Results are restricted by tenant and document-view permission.
- Free-text search currently matches document title, original file names,
  SHA-256 values, OCR text, tag names and comments.
- Filters include tags, Belegdatum from/to, document box, optional child boxes
  and OCR status.
- Sorting supports created date, Belegdatum and title.
- PostgreSQL full text search and ranking can replace or augment the current
  portable queryset implementation once the search surface is stable.
