# ADR-0018: Session runtime observation through the Bot boundary

## Status

accepted

## Date

2026-11-19

## Applies to

all

## Context

Message-result queries read persisted request state, not live Engine activity.
A running Bot can emit tool events without changing the visible message cursor.
Task Harness must not depend on Engine addresses or recovery tickets.

## Decision

Expose a read-only session runtime status Open API through the existing BotRunner,
BotService and session HTTP client boundary. Reuse Bot authorization, lifecycle
selection and normalized session affinity for service Bots. Engine reads the same
process-local registry as chat.status after an upstream history-access check.
No query sends a message, attaches to a stream, or creates a session.

Expose run identity, start time and latest event sequence, not event contents or
recovery credentials. Compare sequences only within one run generation. Missing,
ambiguous, unsupported or inaccessible observations are not evidence of stopped
execution. Preserve Relay reports and task state transitions.

## Consequences

**Positive:** Harness observes tool/model stream activity through its Bot port.
Both editions reuse the same routing and transport boundary.

**Negative:** The Engine registry is process-local and bounded. Affinity is not
a durable run-to-instance binding; membership changes or restarts can lose visibility.

**Risks:** A quiet long-running call can emit no events. Sequence stagnation is
only silence, not proof of failure. Query failure must not trigger business replay.

## Evidence

- community: `src/secbaas/community/adapters/web/routers/open_api/session_router.py`
- community: `src/secbaas/community/core/service/bot_run/_runner.py`
- community: `src/secbaas/community/core/service/bot_run/_baas_service.py`
- community: `src/secbaas/community/core/service/bot_run/_async_session_client.py`
