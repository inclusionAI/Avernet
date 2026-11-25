# Bot ownership sync operations

This extends [manager sync operations](flows/bot-manager-sync.md) with the
explicit Backend → BCS Owner channel. See the
[ownership specification](../specs/2026-11-01-bcs-ownership-sync/spec.md) and
[Core boundary](../src/agentclaw/community/core/bot_management/ownership_sync/README.md).
Backend's committed Owner is authoritative; delivery is best-effort, not part of
the handoff transaction or its `SUCCEEDED` condition.

## Configuration and rollout

The block is under `user_config` in the deployment YAML:

```yaml
bot_manager_sync:
  enabled: true
  ownership_enabled: true
  env: prod
  bcs_base_url: "https://bcs.example.com"
```

| `enabled` | `ownership_enabled` | Startup/runtime behavior |
| --- | --- | --- |
| false / omitted | false / omitted | Both off; zero background sync resources |
| true | false / omitted | Managers only, retaining old configuration behavior |
| true | true | Shared runtime admits managers and explicit ownership work |
| false / omitted | true | Invalid; configuration validation fails |

`enabled` is the shared total switch. There is no Owner-only mode. The new field
is strictly boolean and defaults false; old configurations do not silently gain
Owner-writing authority. Unknown keys fail even when disabled. Enabled mode
requires an environment and an HTTP(S) BCS origin. Runtime signing/startup
unavailability closes/disables admission with diagnostics; structural config
errors fail configuration resolution. Owner-off mode creates no Owner HTTP
plugin. Singlebox explicitly enables both channels; TEST/CORP_TEST use recordings.

This is **not** the `owner_handoff.enabled` initiation flag. Do not change handoff
initiation/recovery posture to test delivery. The durable handoff journal is
separate from this memory-only channel; production AAC/device acceptance remains
a separate release prerequisite.

Deploy in this order:

1. Deploy the existing BCS v1 ownership interface and its governance prerequisites.
2. Provision BCS trust for app `backend` in the correct deployment env/tenant with
   **both** `ownership:sync` and `managers:sync`. Do not assume local trust templates
   modify production. Use the existing principal signing key/SecretResolver;
   never print, copy, or introduce another shared key.
3. Confirm stable Bot registration, Human registration, and initialized Bot
   Authority ownership through the existing authorized governance paths.
4. Enable `ownership_enabled` and restart/roll Backend; inspect actual outcomes and
   perform authorized BCS readback before widening rollout.

Delivery is direct to the configured BCS origin, not through Gateway. Each attempt
mints an HS256 `X-Avernet-Principal` (`iss=backend`, `aud=bcs`, `kid=bare`, 60-second
TTL) with only `app_name=backend`, scoped to the work key's tenant. No Bearer,
Human, or request-user credential fallback is permitted. The request is:

```http
PUT /api/v1/collaboration/bots/{bot_id}/ownership
X-Avernet-Principal: <backend service principal>

{"owner_user_id": "<current committed Backend owner_id>"}
```

The entire stable `bot_id:entity_id` is encoded as one path segment. Entity is
immutable resource identity; current Owner is personnel in the body, not the
address. Missing entity must fail closed, never fall back to old/current Owner.
This channel does not register Bots/Humans, initialize version-0 ownership, or
invoke BCS transfer-request/accept workflows.

## Delivery, confirmation, and accepted risks

Only successful completion of the full Owner-change business operation followed
by commit submits an Owner hint. Handoff still independently submits its ordinary
managers hint. Failed persistence remains a business error; notification failure
does not undo success. Querying/retrying an already-terminal handoff is not a
fresh completion and does not redeliver a lost hint.

The shared runtime has one loop, four workers, and 1,024 total slots shared by
Bot entries and pending/expanding Space hints (including running work). Per-key pending kinds
are explicitly `OWNERSHIP` and `MANAGERS`, not inferred from reason. Same-process
same-key work is serialized; when both are claimed, Owner runs first. An Owner
failure does not skip already-claimed managers work. Only confirmed Owner delivery
arranges a subsequent fresh managers read; ordinary manager/Space/repair hints
never silently escalate to ownership writes.

Each kind has at most **three sends within a 15-second read/send/backoff budget**,
with about 1s/2s plus jitter. A combined Owner/managers round can consume two such
budgets. Every attempt re-reads current facts; Owner reads do not depend on
collaborators, manager limits, or Space eligibility. Missing/deleted/cross-scope
Bot, bad identity, or failed read sends nothing. Synchronous SQL already running
cannot be forcibly stopped; a late read result is discarded.

| Observation | Meaning / next step |
| --- | --- |
| `QUEUED` / `COALESCED` | Memory admission only; not a receipt |
| Valid 200, `code=20000`, matching Bot and target Owner, `changed=true` | `CONFIRMED` replacement |
| Same qualified response with `changed=false` | `CONFIRMED` idempotent target; still refresh managers |
| Valid 200, matching Bot, both Owner fields null, `changed=false` | `BOT_MISSING_NOOP`; **not aligned**, no managers follow-up |
| Valid response but Bot or non-null Owner mismatches target | `DRIFT`; inspect mapping/deployment, no follow-up |
| Malformed/contradictory response, timeout, disconnect, 5xx, 408/425/429 | `UNKNOWN`; bounded retry; previous request may have committed |
| 409 ownership not initialized (version 0) | `REJECTED`; use existing authorized initialization, then repair |
| 401/403 | `REJECTED`; check key, issuer/audience, tenant and trusted-app actions |
| Other 4xx, including 404 or unknown Human | `REJECTED`; fix route/registration/governance, no same-round retry |

A bare HTTP 200 is insufficient: the plugin validates envelope, request ID,
field presence/exact types, and consistent change fields. A 404 is not the valid
missing-Bot no-op. Only `NOT_SENT` and `UNKNOWN` are retryable; exhaustion frees
the entry rather than persisting a block. Successful delivery does not perform
an extra GET; authorized readback is an operational/acceptance step.

There is no durable queue, outbox, delivery table/`Bot.ext` state, TaskQueue
compensation, Redis lock, startup sweep, or periodic reconciliation. A crash
between commit and admission, or process exit after admission, may lose work.
No eventual convergence, exactly-once, cross-replica order, or Owner/managers
atomicity is promised. A stale in-flight request can overwrite a newer Owner
late despite fresh-read retries; BCS's local version is not a Backend stale-write
fence. `UNKNOWN` never proves the remote write did not happen.

**Old BCS Owner permissions may persist until a later successful correction.**
This is an authorization risk, not merely display lag. BCS removes the old
Owner's owner/manager edges and strips the new Owner's manager edge according
to its contract; the follow-up projects only current Backend collaborators. It
does not automatically demote the old Owner to manager. Concurrent legitimate
changes can still alter those facts afterward.

## Diagnosis

Use the shared `manager_sync` diagnostic logger and distinguish `kind=ownership`
from `kind=managers`. Logs carry scoped tenant/env/bot_pk, finite reason, attempt,
outcome, elapsed milliseconds, sanitized error code, and available BCS request ID.
Thread-safe counters have bounded event/outcome/work-kind labels: do not turn
Bot keys, request IDs, tenants, or arbitrary reasons into metric labels. There
is no persistent per-key status/blocked registry to inspect or clear.

- `submit_admitted`: queued/coalesced or disabled, never delivery success.
- `submit_rejected`: `queue_full`, `closed`, or `env_mismatch`; closed wins at stop.
- `read_failed`, `read_late`, `projection_failed`: no send from that attempt.
- `send_retryable`, `send_rejected`, `deadline_expired`, `retries_exhausted`,
  `cancelled`: distinguish uncertainty, refusal, budget limits, and shutdown.
- `send_confirmed`, `send_bot_missing_noop`, `send_drift`: do not combine them
  into a generic "synced" result; inspect the request ID and authorized readback.

Never log JWTs, signing materials, credentials, authorization URLs, raw responses,
or full collaborator rosters. Record build/config posture and scoped keys only
in controlled incident records, not as secrets or unbounded metrics.

## Controlled manual repair

First inspect rejection/no-op/drift causes. Correct trust, route, stable mapping,
or complete existing Bot/Human registration and ownership initialization through
authorized governance. Then resubmit **current Backend facts**, inspect terminal
diagnostics, and read back through an authorized BCS identity. Do not bypass
initialization or use an arbitrary Owner override to force alignment.

Resolve `OwnershipSyncRepairService` from the **existing live deployment injector
with its shared lifecycle already started**. It exposes exactly:

```python
request_bot(key: SyncKey) -> SubmissionResult
request_page(*, avernet_tenant: str, env: str, after_pk: int, limit: int) -> RepairPage
```

There is no public repair route. The caller supplies scope/cursor, never an Owner
payload. Pages use ascending PK and exclusive `after_pk`; `limit` is clamped to
0–100. Results have `last_pk`, `queued`, `coalesced`, `rejected`, and exact
`rejected_keys`. Disabled single-Bot repair returns `DISABLED`; disabled page
repair raises `RuntimeError("ownership sync repair unavailable")` rather than
pretending a sweep succeeded. Database errors propagate.

Inside a controlled callback with the live injector and started lifecycle:

```python
from agentclaw.community.core.bot_management.ownership_sync.repair import OwnershipSyncRepairService
from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncKey

repair = injector.get(OwnershipSyncRepairService)
result = repair.request_bot(SyncKey(tenant, env, bot_pk))
if result not in (SubmissionResult.QUEUED, SubmissionResult.COALESCED):
    raise RuntimeError("ownership repair was not admitted")

page = repair.request_page(avernet_tenant=tenant, env=env, after_pk=cursor, limit=100)
# Retain exact rejected_keys; do not advance past unadmitted work silently.
# After the local page drains and outcomes are inspected, continue at page.last_pk.
```

Use the same bounded paging/drain pattern as
[manager maintenance bootstrap](flows/bot-manager-sync.md#5-manual-resync),
but resolve `OwnershipSyncRepairService`, not `ManagerSyncRepairService`.
Do not shut down the application's shared lifecycle from such a callback.
For a **standalone** process, the linked full bootstrap is mandatory: detect and
validate the real deploy profile, register its configuration provider and corp
modules, resolve extra modules, build the injector against the existing database
and signing configuration, then `await lifecycle.startup()` on
`BotManagerSyncLifecycle` before repair. In `finally`, the process that owns that
lifecycle calls `await lifecycle.shutdown()`. Do not bootstrap/recreate schema.
Reject TEST/CORP_TEST; constructing a new injector, enqueuing, and immediately
exiting is not repair. Check both shared-runtime availability and Owner admission.

Drain is only local completion, not remote confirmation. A single Owner round
may require a subsequent managers round; choose a maintenance window accordingly
and inspect both kinds. Retain rejected keys, wait between bounded pages, and
verify BCS before declaring repair complete. No service accepts a caller-specified
replacement Owner, and no repair call grants Owner-change business authority.

## One-shot collaboration repair (the user-facing route)

The section above is **queue admission**, not a repair result. Since the
bot-avatar/collaboration-repair work there is also a real one-shot repair
interface exposed to Bot owners over HTTP:

```http
POST /openapi/v1/bots/{bot_id}/collaboration/repair
```

It repairs one Bot's BCS collaboration metadata — `profile` (name / summary /
avatar), `ownership`, `managers`, and the applicable `caller_config` — from
committed Backend facts, in the caller's request. Only the Bot's current
Backend Owner may call it (`GRANT_CHECKED_OWN_BOT` admission; a non-owner gets
the same masked 404 as a missing Bot). Read the module
[Core boundary](../src/agentclaw/community/core/bot_management/collaboration_repair/README.md)
for the full failure semantics. The two repair surfaces are different tools:

| | Enqueue repair (`request_bot` / `request_page`) | One-shot route (POST) |
| --- | --- | --- |
| Caller | Operator inside a controlled callback against the live injector | The Bot's current Owner over the public API |
| Result | Queue admission (`QUEUED`/`COALESCED`/`DISABLED`) — an admitted hint, **never** a delivery receipt | A synchronous 200 `Envelope<CollaborationRepairReport>` with one item per component (`succeeded`/`failed`/`unknown`/`skipped_dependency`/`not_applicable`) |
| Precondition failures | Admission diagnostics (`queue_full`, `closed`, `env_mismatch`) | Stable errors: 404 `collaboration_bot_not_found` for a missing target, 409 ambiguous/drifted facts or `owner_changed`, 502/503/504 for probe/unavailable/deadline |
| Missing BCS Bot | Background missing-noop classification | Refused before any write; never registers, creates or restores the Bot |
| Retry/background behavior | Admitted hints re-read fresh facts and may send up to the bounded budget | No queue, no retry, no compensation; one bounded send per item, one budget per request (unconfirmed outcomes report `unknown`) |
| Use it for | Maintenance sweeps of delivery state behind the already-best-effort channel | An Owner-driven "fix my Bot's collaboration info now" action with an explicit per-component report |

Do not use queue admission to answer "is it aligned now", and do not treat the
route's HTTP 200 as "everything succeeded" — read `data.status` and the items.
The route requires the deployment's transport to carry BCN provider scope; a
credential-less deployment answers 503 before any plugin is built, and that
refusal creates nothing.


## Stop, rollback, and downgrade

There is no hot reload. Set `ownership_enabled: false` and restart/roll Backend
to return to managers-only delivery. To disable the shared runtime, set **both**
flags false; leaving ownership true with the total flag false fails validation.
Stopping closes admission, bounds drain, cancels remaining async work, and closes
both plugins on their worker loop. Queued work may be lost. Stop does not roll
back BCS writes or cancel requests already in flight remotely.

When downgrading to a Backend version that does not recognize `ownership_enabled`,
**remove that key**, even if false, before starting it. Do not re-enable an old
writer without understanding its trust and ordering behavior. There is no schema
migration or automatic permissions rollback. Restoring Owner requires another
authorized write; assess stale and late-write risk and read back afterward.

## Evidence limits

Unit/consumer contract tests and TEST/CORP_TEST recording plugins prove only their
scoped behavior. Controlled handoff tests may replace Passport/runtime boundaries
while retaining real Core commit and delivery; they do not prove real AAC consent,
credential reissue/retirement, ALC→AEC invalidation, or device update acceptance.
Real BCS readback, complete singlebox gates, and real-device/AAC acceptance are
separate evidence. Record the exact command/build/result; do not borrow older
manager validation evidence as proof of ownership delivery or external rollout.

## 2026-11-02 compatibility rollout and operations admission

The shared TeamClaw reference resolver now supplies **both** ownership and
managers projections. Service Bots use `bot_id:entity_id`, except conversions
with an established `ext.bcn_provider_bot_ref`, which keep that reference.
Personal, desktop and `default` Bots retain their registered `bot_id:owner_id`
address. No provider ref or BCN UUID is rewritten during handoff.

Bot list/detail, collaboration lists and display metadata add `bcn_bot_id`.
`owner_entity_id` / `owner_id` continue to identify the current personnel Owner.
New clients prefer the explicit BCN address; old servers remain supported by
client fallback. Batch display lookups accept entity/current-Owner locators and
exact retained conversion references, then query the resolved Bot's current
record. Ambiguous addresses fail closed. Display lookup does not grant access
to configuration, credentials or runtime operations.

### Administrator handoff for a departed Owner

The existing authenticated `POST /api/bots/owner-handoffs` accepts:

```json
{"bot_pk": 123, "new_owner_id": "target-staff-id", "request_key": "one-operation-uuid"}
```

The current Owner **or an administrator configured in
`user_config.admin.super_admin`** can initiate it. Use the administrator's own
login; never impersonate the departed Owner or supply an `actor_id` in JSON.
The journal records the administrator as `actor_id` and the original Owner as
`old_owner_id`. Old Owner presence/approval is not required. Service-Bot,
Space membership, active approval, execution identity and credential checks
remain in force; administrators cannot bypass those checks. The target identity
must still pass staff validation. Reuse the same `request_key` only when
retrying the same transfer.

### Single-Bot BCN repair admission

Authenticated **super administrators only** may call:

```http
POST /api/bots/owner-handoffs/bcn-repair
Content-Type: application/json

{"bot_pk": 123}
```

The body forbids other fields. Tenant comes from the trusted request context;
environment comes from the configured sync deployment. The operation reads a
live service Bot by PK, validates its established reference, and submits current
ownership through the existing bounded runtime. It never accepts a target Owner,
changes Backend ownership, scans historical Bots, or initializes missing BCN Bots.

HTTP 202 means admission (`queued` or `coalesced`), **not successful repair**:

```json
{"success":true,"data":{"bot_pk":123,"bcn_bot_id":"bot:stable","submission":"queued","delivery_confirmed":false}}
```

Unauthorized callers receive 403, missing/wrong-environment Bots 404,
non-service/default Bots 409, and disabled/unavailable/rejected admission 503.
Admission logs record actor, tenant, environment, PK and outcome, without
credentials. Verify dispatcher `CONFIRMED` and authorized BCN readback before
closing an incident. Ownership confirmation triggers a fresh managers sync.
A retry after lost admission is safe: every send reads current committed facts.
There is no durable delivery or ordering guarantee across replicas.

### Deployment and acceptance sequence

1. Merge Backend/address and both frontend compatibility changes; no database
   migration is required. Deploy BCN/BCS trust before enabling Backend writes.
2. OCB prepub overlay enables `bot_manager_sync.ownership_enabled`; the production
   overlay remains off. The BCS trusted Backend app allows `ownership:sync` and
   `managers:sync`; verify the deployed config and existing principal signing key.
3. On a test service Bot, transfer A→B using the existing operations handoff API.
   Confirm Backend success, unchanged entity/provider ref, B as BCN Owner, and
   the expected collaborators. Exercise old/new list, group/session, friend
   metadata, chat and lifecycle controls. Include a personal/default control
   and a personal-to-service conversion.
4. Deploy the repair admission capability only. **Do not run historical repair**
   as part of deployment. When an incident warrants it, an authorized operator
   submits the affected PK and verifies remote readback. Production enablement
   follows prepub acceptance separately.

Rollback: disable only `ownership_enabled` to stop further Owner sync admission;
retain managers and handoff recovery posture. Rollback does not undo a committed
handoff or a confirmed BCN ownership update.

### Local implementation validation (2026-11-02)

- Backend affected Bot-management/repository/BCN contracts/HTTP/architecture
  selection: **3,465 passed**. Additional final address/legacy-NULL projections
  and reader regressions: **113 passed**; default-locator compatibility: **5 passed**.
  These selections overlap and must not be added together.
- New frontend affected service tests: **55 passed**; TypeScript `--noEmit` passed.
- Old frontend persisted BCN-reference test: **5 passed**, using the available
  Jest runtime and a temporary TypeScript transformer. The full old frontend
  build was not run because this checkout lacks its installed toolchain.
- OCB corporate configuration overlay tests: **34 passed**; TOML trust and
  prepub-on/production-off assertions passed.
- Changed Python blocking lint, added-line secret scans for both repositories,
  and `git diff --check` passed. Existing oversized legacy source files were
  kept to narrow edits; no new source file exceeds 1,000 lines or adds a waiver.
- No real remote BCN/AAC/device acceptance, full product build, full Backend CI,
  or unified Singlebox coverage run was performed. No deployment, historical
  repair, database mutation, commit, or push was performed in this change.
- OCB's pre-existing Avernet gitlink checkout difference was preserved. After
  the public code is committed, release integration must point OCB's gitlink
  at that commit before these local Backend/frontend changes can be deployed.
