# ADR 0008: Tenant RBAC and AccessControl

## Status

Accepted.

## Context

DoMaSy must distinguish system administration from tenant administration. Django
already provides users, superusers, groups and global permissions, but tenant
roles need to be scoped to one tenant. A user can be an admin in one tenant and a
viewer in another tenant.

## Decision

DoMaSy uses Django users as the platform identity and adds tenant-scoped access
control in the `accounts` app.

Core concepts:

- `TenantPermission`: stable permission code such as `documents.upload`.
- `TenantRole`: tenant-owned bundle of permissions.
- `TenantMembership`: link between a user, a tenant and one tenant role.
- `AccessControl`: central permission checker for tenant-aware decisions.

Views should use policy helpers such as `can_manage_roles()` instead of checking
role slugs directly. Application services receive validated domain objects, for
example a `TenantRole`, and guard against cross-tenant role assignment.

System administrators remain Django superusers and are allowed through the same
policy layer.

## Consequences

- Role management can be exposed to tenant admins without giving them system
  admin access.
- Authorization remains explicit and testable.
- Default roles can be bootstrapped per tenant, but existing role permissions are
  not overwritten by later bootstrap runs.
- Django's built-in auth stays useful for identity and system-level admin, while
  tenant permissions remain modeled in the domain.
