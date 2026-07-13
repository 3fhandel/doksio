# ADR 0011: Generic Workflow Core

## Status

Accepted

## Context

Doksio needs workflow capabilities, especially for invoice review. The product
should not hard-code invoice approval as the only workflow shape, because the
same engine should later support personnel files, document checks, exports and
other tenant-specific processes.

## Decision

Workflows are modeled generically.

Core entities:

- `WorkflowTemplate`: reusable tenant-scoped definition
- `WorkflowStep`: ordered step with type, role assignment and instructions
- `WorkflowInstance`: one workflow run for one document
- `WorkflowTask`: actionable task created from a step

The first implementation supports manual workflow starts, automatic starts after
document creation/import for configured document boxes, role-assigned tasks,
task completion, required completion comments and automatic advancement to the
next step. Workflow management is available in tenant settings.

## Consequences

- Invoice approval can be configured as a workflow template instead of being
  hard-coded.
- Future import triggers, export actions and condition checks can reuse the same
  instance/task model.
- The first engine is intentionally simple and linear; branching and graphical
  modeling remain later extensions.
