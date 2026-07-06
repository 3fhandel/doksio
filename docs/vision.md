# DoMaSy Vision

DoMaSy is a slim document management system for small and medium-sized
businesses. It is not a private document archive and not a document editor.

The product manages immutable business documents, metadata, annotations, search,
permissions, workflows, imports and exports. The first strong use case is invoice
handling, but the core product should remain a generic document and workflow
system.

## Product Positioning

DoMaSy is the operational document and workflow system.

For accounting documents, the legally authoritative archival system can be DATEV
Unternehmen online or another configured archival export target. DoMaSy should be
as traceable and revision-friendly as possible, but it does not initially claim
to be a certified legal long-term archive.

## Core Goals

- Accept documents from multiple sources such as uploads, HTTP endpoints, mail
  inboxes and watched folders.
- Import documents according to configurable import policies.
- Store original files immutably.
- Run local OCR and make documents searchable.
- Manage business metadata, annotations and audit history.
- Provide a generic workflow system for document-centered processes.
- Support invoice approval as a workflow use case.
- Support secure document boxes such as personnel files.
- Support configurable export targets, including DATEV-related exports.
- Provide tenant-aware access control, RBAC and OIDC-compatible authentication.
- Run as a dockerized web application.

## Non-Goals

- DoMaSy is not a document editor.
- DoMaSy should not modify, overwrite or rewrite original document files.
- DoMaSy is not initially positioned as the legally authoritative archive for
  accounting records when DATEV Unternehmen online is configured as the archive.
- DoMaSy is not primarily a bookkeeping system.
- DoMaSy is not initially a broad enterprise content management suite.

## Initial User Focus

The main UI should be built for non-accounting clerks and occasional approvers.
The application should feel like a calm work tool: task-oriented, searchable and
clear, with accounting details present where needed but not dominating the
experience.
