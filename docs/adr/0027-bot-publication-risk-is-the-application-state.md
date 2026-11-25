---
status: accepted
---

# Use the Bot public risk record for application progress

## Context

A human approval can succeed after a Bot's resources or the applicant's
permissions change. The approval platform determines the human outcome.
TeamClaw determines whether that outcome may be applied to the current Bot.

## Decision

The current risk application, exposed by `risk-checks/current`, owns progress
for risk-linked publication. Its workflow stores the platform decision and its
PUID independently of the local error. The query exposes the decision as an
optional field. The Bot's effective visibility remains in the publication
target's existing visibility record.

On agreement, TeamClaw rechecks the input snapshot and applicant's authority
before publication. Changes yield `CONFIG_CHANGED` or `PERMISSION_CHANGED`.
Readers continue to handle historical `INPUT_OR_PERMISSION_INVALID` records.
Rejection and cancellation close the matching application even if publication
eligibility has changed.

The legacy Bot approval field remains a projection for its matching PUID.
A blocked agreement changes its pending status to `BLOCKED`. Archival marks
any remaining `PROCESSING` entry as `REPLACED`.

## Consequences

A user can see an agreement and a local block together, then submit the
current configuration for a fresh scan. A replacement keeps the old request
in history and prevents it from publishing the replacement's resources. A
legacy field alone would hide the reason for the block.

The owning domain is
[Bot publication](../../apps/backend/src/agentclaw/community/core/bot_public/README.md).
See the [response contract](../../apps/backend/src/agentclaw/community/adapters/http/bot_public/schemas_risk.py)
and [approval guard tests](../../apps/backend/tests/community/core/bot_public/test_risk_approval_guard.py).
