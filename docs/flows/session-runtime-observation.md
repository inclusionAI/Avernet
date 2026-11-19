# Session runtime observation

## Scope and ownership

Task Harness observes live single-Bot activity without reading event bodies or
opening an execution/recovery channel. Manager-worker collaboration sessions use
the existing BCS message and delivery observation APIs described below. Backend owns the silence policy; SecBaaS owns Bot
authorization, lifecycle and instance routing; Engine owns the run observation.
Relay reports, task transitions and task-loop are unchanged.

## Ordered flow and contracts

1. Backend's [Bot adapter](../../apps/backend/src/agentclaw/community/core/task/task_runner/client/open_api_bot_adapter.py)
   sends an authenticated `GET /openapi/v1/sessions/runtime/status` through SecBaaS.
   Query: required `session_id`, selected `bot_id`, and `lifecycle_stage`
   (`draft`, `online`, `verify`, `all`; default `online`). Bot API keys cannot
   override their own identity; other keys require existing Bot policy grants.
2. SecBaaS [session router](../../apps/baas/src/secbaas/community/adapters/web/routers/open_api/session_router.py)
   delegates to BotRunner and the existing BotService routing seam. Service Bots
   reuse normalized session affinity and authenticated device proxy routing.
   Draft and online resolve their respective existing lifecycle bindings. This
   does not create a binding or pin a run across instance membership changes.
3. The [session HTTP client](../../apps/baas/src/secbaas/community/core/service/bot_run/_async_session_client.py)
   calls Engine `GET /api/sessions/runtime/status?session_id=...`. Engine checks
   upstream history access and reads the same registry as WS `chat.status`.
   It never attaches to a stream, resumes, sends or creates a session.
4. SecBaaS returns `code: 0`, `message: "success"`, `data` containing
   `session_id`, `found`, `run_id`, `state`, `started_at`, `last_event_seq`, `reason`.
   Engine uses its existing `success/data` envelope. `started_at` is Unix ms;
   `run_id` is the Engine run identity, not a BaaS message/result ID.
   A found observation requires run identity, start time, state and nonnegative
   sequence; state is `starting|running|final|error|aborted`. A missing observation
   has `found: false`, reason `NO_ACTIVE_RUN|AMBIGUOUS_RUN|UNSUPPORTED_ENGINE`,
   and optional fields omitted or null. No ticket, event body or tool parameters
   are returned.
5. Backend [probe](../../apps/backend/src/agentclaw/community/core/task/task_harness/runtime_probe.py)
   compares sequences only within the same `(run_id, started_at)` generation.
   An increase resets runtime silence; first observation, generation change or
   sequence rollback only resets the baseline. Terminal Engine states use the
   existing Harness terminal observation policy, not automatic task completion.

## Failure boundaries and limits

- [Harness](../../apps/backend/src/agentclaw/community/core/task/task_harness/relay_runtime_monitor.py)
  treats absent, ambiguous, unsupported, denied, malformed or unavailable activity
  observations as unobservable: retain the cursor, clear accumulated silence and
  do not resume or mark the node failed merely because the query failed. The next
  usable observation starts a fresh silence window. Repeated failures are logged.
- The sole unfinished run is selected; otherwise use the newest retained terminal
  run. Terminal records retain the existing 180-second TTL. Process restart,
  cache expiry or affinity changes can make activity unobservable.
- Event sequence includes forwarded tool/model events, not just visible text.
  Activity is not evidence of business progress. A single long-running call can
  remain silent; this API does not add a tool-duration heartbeat or cancellation.
- Non-session legacy run probes and state-machine group probes retain their
  existing behavior. No cross-ARCA recovery is introduced.

## Manager-worker collaboration sessions

Backend reuses the current participant Bot's Bearer token for BCS history reads
(with pending inputs) and the existing
`POST /openapi/v1/collaboration/sessions/{session_id}/message-deliveries/query`.
Only the coordinator's `send` delivery can establish execution failure; group
lifecycle, worker failures and `STREAM_ERROR` text alone cannot establish it.

The probe associates source input messages with deliveries and orders history by
BCS timestamps rather than UUIDs. It can walk up to eight 100-message pages to
locate a trimmed source, then persists the association for subsequent polls.
Ambiguous/missing sources, newer unresolved inputs, newer runs, queued admissions
and unavailable/denied queries fence old failures instead of triggering recovery.

Harness requires the same node generation, run, delivery and activity cursor to
remain failed across two consecutive polling intervals before resuming the
original session. A new execution or activity restarts confirmation. After the
async probe it rechecks that the node is still running in the same generation.
A valid coordinator `queue_admission` is accepted even when `delivered=false`;
its source message and run are retained so pending recovery is not sent again.
Runtime completion never substitutes for the task's accepted Relay reports.

Existing recovery budgets, quiet windows and per-process serialization remain.
There is no distributed recovery claim or cross-process exactly-once guarantee;
this change does not add a lock or change the Relay public protocol.

## Rollout and verification

Deploy Engine, then SecBaaS, then Backend. Keep existing proxy authentication and
session-affinity routing in place. If the new read path is absent, Harness does
not infer execution failure; runtime silence recovery is unavailable for that Bot
session until observations work again.

Local contract tests cover real Engine event publication and HTTP responses,
SecBaaS authorization/routing/HTTP parsing, Backend cursor classification and
Harness decisions. See Engine
[test_runtime_status_http.py](../../engine/adapter/src/engine/community/api/tests/transport/test_runtime_status_http.py),
SecBaaS [router tests](../../apps/baas/tests/unit/adapters/web/open_api/test_session_router.py),
and Backend [probe tests](../../apps/backend/tests/community/core/task/task_harness/test_runtime_probe.py).
These do not establish deployed multi-instance behavior. Preproduction acceptance
must verify draft/online queries and increasing tool-event sequences while the
visible message cursor is unchanged. See [SecBaaS ADR-0018](../../apps/baas/docs/adr/0018-session-runtime-observation.md).
