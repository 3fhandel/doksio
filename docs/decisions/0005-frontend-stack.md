# ADR 0005: Frontend Stack

## Status

Accepted

## Context

The primary users are non-accounting clerks and occasional approvers. The UI
should be modern, consistent and efficient, but the application does not require
a heavy single-page frontend at the start.

DoMaSy needs components such as document lists, search dropdowns, filters, tabs,
badges, modals, comments, forms, icons and PDF preview controls.

## Decision

The frontend will use Django templates with HTMX, Alpine.js, Tailwind CSS,
daisyUI and lucide icons.

- Django templates render pages and reusable fragments.
- HTMX provides server-driven partial updates.
- Alpine.js handles local client-side UI state.
- Tailwind CSS provides modern utility styling.
- daisyUI provides a consistent component system on top of Tailwind.
- lucide icons provide the default icon set for buttons, navigation and document
  actions.
- PDF.js provides document preview.

## Consequences

- The frontend remains close to Django and easy to iterate.
- The project avoids SPA complexity while still supporting modern interaction.
- daisyUI reduces the amount of custom component styling needed.
- More specialized JavaScript components can be added later if search dropdowns
  or comboboxes become too complex for HTMX and Alpine.js alone.
