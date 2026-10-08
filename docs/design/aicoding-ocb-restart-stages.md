# Ordinary coding restart: OCB-owned preparation, backup and submission

Base: private OCB `REL20261009` / `39357e3e9b`, Avernet
`4b57661dcbb85c78a2892326c6ddc934dee3ac7a`.

## Problem

The coding worker fenced mutation immediately after backup verification, before
local BaaS restart preparation. A preparation error was therefore treated as an
ambiguous remote submission. The task waited in RESTARTING although no update
had been submitted. Admission and the legacy BaaS path also both wrote PENDING.
An update returning None was interpreted as a missing Bot; the exact reason for
the production database return value has not been established.

## Solution and boundaries

BaaS remains responsible only for its existing device lifecycle. There are NO
changes to BaaS server code, `/status`, frontend, schema, or Repository APIs.
The earlier unconnected BaaS backup-policy prototype has been removed.

The OCB aicoding strategy:

1. Validates admission and persists requested template changes synchronously.
2. Calls the engine-neutral BaaS preparation collaborator before queue admission.
   Configuration resolution, provider registration and workflow-baseline lookup
   fail through the original HTTP boundary. No backup, remote update or provider
   polling task is started by this preflight.
3. Persists the existing coding task/journal and writes Bot PENDING once.
4. Runs backup in that task with a shared 300-second budget (including queue time
   and redeliveries), then invokes the existing restart lifecycle. Direct exec
   calls still use their transport timeout: the budget prevents authorization
   after expiry; it does not forcibly kill an in-flight exec or helper process.
5. Revalidates the current configuration/target on the worker. Prepared requests
   are NOT serialized into the task because they may contain credentials. Local
   preparation may run again; this is intentional, not a second PENDING write.
6. Rechecks the backup receipt and fences exactly at the OCB BaaS client's
   `before_submit` callback, after request-body construction and before POST.
7. Handles known pre-submission errors as terminal failure. Explicit HTTP
   400/401/403/404/422 rejection clears the provider polling intent and persists
   a sanitized failure. Timeouts, connection loss, HTTP 409 and server errors
   after fencing remain ambiguous and observe the existing durable provider
   intent; they do not automatically issue another update.

Failure uses the existing Bot FAILED + ext.start_status/start_message contract.
The operation observer budget is 900 seconds (backup 300 + existing provider
polling 600); task-row retention remains 86400 seconds, not a business deadline.
Existing in-flight handlers stay registered. RESTARTING redelivery attempts to
capture the stored provider handoff without reissuing mutation. Ambiguous legacy
operations without a provable handoff still expire rather than replay mutation.

### Neutral shared-path changes

- Keep the original provider preparation in BotService; expose a default-off
  `prepare_only` boundary before task creation/status writes/remote submission.
  Aicoding alone opts into this preflight via the passed RestartServices callback.
  The previously extracted `restart_preparation.py` and mixin are removed.
- Let strategy policy decide whether admission already wrote PENDING. The default
  returns True and preserves every other engine's original write AND rollback;
  only an owned coding/BaaS task suppresses the duplicate write.
- Add default-no-op submission-policy hooks; no coding engine literals or backup
  decisions in BotService.
- Add optional `before_submit` to the OCB BaaS client and its protocol. This is an
  internal callable, NOT a new HTTP field and NOT logic in the BaaS server.
- Aicoding-specific fences, failure classification and timing remain in its
  strategy modules. Published/Caller pre-backup and noncoding admission are not
  switched to a different execution model.

## Validation / rollout

Regression tests cover the actual BotService lifecycle with mocked transport:
only one PENDING write, synchronous preflight rejection before task creation,
backup failure, preparation failure after backup, intent persistence failure,
HTTP 404 failure, final receipt failure, and ambiguous-response observation.
Real-client tests prove payload -> fence -> POST order and no POST on failure.
Existing restart, published/Caller, status-error and protocol suites are run.
No live deployment or online Bot recovery is performed by this patch.

Large-file debt: BotService and the OCB BaaS client already exceed the repository
1000-line source guideline in the base revision. Preserve existing code placement
and restrict changes to generic policy/phase hooks; the client receives only a
generic callback. Splitting
these whole services is intentionally deferred to a dedicated refactor rather
than mixed into this lifecycle fix. New source modules stay below 1000 lines;
no CI allowlist or gate is weakened. This existing size debt remains a rollout
review item, not a claim that all repository gates passed.

Validated locally on 2026-10-08 with Python 3.12 and the locked Backend
workspace dependencies: **2717 tests passed** (bot_management, service_bot
services, Caller restart backup, service API conformance and protocol-base
ordering). Targeted undefined-name checks and `git diff --check` passed.
The additional isolation regressions assert default-engine PENDING writes and
rollback remain exactly as before and preparation-only calls do not enqueue or
mutate lifecycle status.
Full-repository CI / Singlebox E2E and a live pre-environment restart were not run.
