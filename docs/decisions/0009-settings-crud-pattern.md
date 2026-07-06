# ADR 0009: Settings CRUD Pattern

## Status

Accepted.

## Context

Tenant settings will contain lists that can grow over time, including users,
roles, document boxes, import rules, workflow definitions and export targets.
Inline edit forms inside list pages become hard to scan and awkward to use once
there are many records.

## Decision

Settings CRUD uses separate pages for list, create and edit.

Pattern:

- list page: table, status badges, primary create action and per-row edit action
- create page: focused form for one new object
- edit page: focused form for one existing object
- delete/removal semantics: prefer inactive/archive states for domain objects
  that can be referenced by documents, audit events or workflows

The list page should not multiplex multiple create/update actions through one
large inline form surface. Views should remain explicit, with separate URL names
such as `settings_role_create` and `settings_role_edit`.

## Consequences

- Large settings lists remain scanable.
- Form errors are easier to understand because one page owns one action.
- URL names stay stable and predictable.
- Later HTMX enhancements can improve these flows without changing the domain
  service layer.
