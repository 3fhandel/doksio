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
- Frontend: Django templates, HTMX, Alpine.js, Tailwind CSS, daisyUI and lucide icons
- Document preview: PDF.js
- Deployment: Docker Compose first, Kubernetes-compatible later if needed

## High-Level Shape

DoMaSy should start as a modular Django monolith. The codebase should keep clear
domain boundaries, but avoid premature microservices.

Suggested Django apps:

- `tenancy`: tenants, organizations and tenant context
- `accounts`: users, groups, roles and OIDC claim mapping
- `documents`: documents, document files, versions, metadata and annotations
- `storage`: object storage integration, checksums and immutable file handling
- `ingestion`: upload, API import, mail import and watched folder import
- `ocr`: OCR jobs, text extraction and derived OCR artifacts
- `search`: full text indexing and permission-aware search
- `workflows`: workflow definitions, instances, tasks and actions
- `exports`: export targets, export runs and export adapters
- `audit`: append-only audit events

## Document Model Principles

Documents are logical containers. Files are immutable artifacts.

- `Document` represents the business object.
- `DocumentFile` represents one immutable stored file.
- Original files are never edited or overwritten.
- OCR output, previews, thumbnails and extracted text are derivatives.
- Metadata, comments, annotations, workflow state and audit events live around the
  immutable file.
- Replacing a file means adding a new `DocumentFile`, not modifying an existing
  one.

## Workflow Model

Workflows should be generic and configurable. Invoice approval is one workflow
template, not the hard-coded center of the product.

Workflow building blocks:

- Triggers:
  - document imported
  - document type detected
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
  - document type
  - amount threshold
  - metadata presence
  - assigned document space
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
- Documents and document spaces
- Workflows
- Administration

The central document view should combine:

- document list or search results
- document/PDF preview
- metadata
- workflow state
- comments and annotations
- available actions

The frontend stack should avoid a full SPA at the beginning:

- Django templates render pages and fragments.
- HTMX handles partial updates and server-driven interactions.
- Alpine.js handles local UI state.
- Tailwind CSS provides utility styling.
- daisyUI provides consistent components.
- lucide icons provide the default icon set.

## MVP Scope

The first usable version should include:

- tenant-aware document storage
- local development login and OIDC-ready structure
- user groups, roles and permissions
- manual upload and/or API import
- immutable object storage
- local OCR
- full text search
- document spaces or folders
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
