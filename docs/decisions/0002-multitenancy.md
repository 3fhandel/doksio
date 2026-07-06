# ADR 0002: Tenant-Aware Design

## Status

Accepted

## Context

DoMaSy may be used by multiple companies or organizational units. Even if the
first deployment is on-premise for one company, later SaaS-capable operation
should remain possible.

## Decision

DoMaSy will be tenant-aware from the beginning.

Core business entities should be scoped to a tenant or organization, including
documents, users, groups, roles, import sources, workflow definitions and export
targets.

The initial implementation does not need separate databases per tenant. A shared
database with tenant-scoped rows is acceptable for the first architecture.

## Consequences

- All queries touching business data must be tenant-scoped.
- Search and permissions must enforce tenant boundaries.
- Export targets and import policies can differ per tenant.
- The design keeps a path open for SaaS deployments or multi-organization
  installations.
