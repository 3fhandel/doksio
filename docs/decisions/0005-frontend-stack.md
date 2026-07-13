# ADR 0005: Frontend Stack

## Status

Accepted

## Context

The primary users are non-accounting clerks and occasional approvers. The UI
should be modern, consistent and efficient, but the application does not require
a heavy single-page frontend at the start.

Doksio needs components such as document lists, search dropdowns, filters, tabs,
badges, modals, comments, forms, icons and PDF preview controls.

The project should not require Node.js for the frontend asset pipeline.

## Decision

The frontend will use Django templates with HTMX, Alpine.js, Bootstrap and
lucide SVG icons.

- Django templates render pages and reusable fragments.
- HTMX provides server-driven partial updates.
- Alpine.js handles local client-side UI state.
- Bootstrap provides a consistent component system without a JavaScript build
  step.
- lucide SVG icons provide the default icon set for buttons, navigation and
  document actions.
- PDF.js provides document preview.

## Consequences

- The frontend remains close to Django and easy to iterate.
- The project avoids SPA and Node.js build complexity while still supporting
  modern interaction.
- Bootstrap reduces the amount of custom component styling needed.
- More specialized JavaScript components can be added later if search dropdowns
  or comboboxes become too complex for HTMX and Alpine.js alone.
