# ADR 0004: Generic Workflows and Export Actions

## Status

Accepted

## Context

Invoice approval is an important use case, but DoMaSy should not hard-code the
product around invoices. Workflows should also support other document-centered
processes.

DATEV should be one possible export target and should be usable as a workflow
step.

## Decision

DoMaSy will model workflows generically.

Workflow definitions should support triggers, steps, conditions and actions.
Invoice approval can be delivered as a workflow template.

Exports are modeled as workflow-capable actions. Each export run records the
target, status, involved document file versions, result and errors.

## Consequences

- DATEV export can be added without making DATEV the only export path.
- Other export targets such as SFTP, filesystem, webhook or future accounting
  integrations can follow the same pattern.
- Failed exports can leave a workflow in a recoverable error state.
- Workflow history and export history remain auditable.
