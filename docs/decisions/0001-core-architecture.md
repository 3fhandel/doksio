# ADR 0001: Core Architecture

## Status

Accepted

## Context

DoMaSy is a new document management system for small and medium-sized
businesses. It should be dockerized, web-based and initially focused on document
storage, search and workflow use cases. Invoice handling is important, but should
not force the whole architecture into a hard-coded invoice system.

## Decision

DoMaSy will start as a modular Django monolith using PostgreSQL, object storage,
local OCR, asynchronous background jobs and server-rendered frontend technology.

The initial stack is:

- Python and Django
- PostgreSQL
- Celery for asynchronous document processing
- cron or Celery Beat for periodic triggers
- Redis or RabbitMQ as broker, with Redis preferred for the initial setup
- S3-compatible object storage, with MinIO for local development
- PostgreSQL full text search initially
- Django templates, HTMX, Alpine.js, Tailwind CSS and daisyUI
- PDF.js for document preview

## Consequences

- The system remains simple to deploy and reason about.
- Domain modules can be kept separate without introducing distributed-system
  complexity.
- Long-running work such as OCR and exports does not block web requests.
- The frontend remains server-driven and avoids SPA complexity.
- The architecture can evolve toward stronger search, storage or deployment
  options later.
