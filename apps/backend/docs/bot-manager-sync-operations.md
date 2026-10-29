# Bot manager sync operations

This runbook covers enabling, observing, and manually resubmitting the Backend → BCS
manager projection. The feature is genuinely best-effort: it has no durable queue,
distributed ordering, in-flight marker, or convergence guarantee.

## 1. Operational contract

- Backend business writes are authoritative and complete independently of BCS.
- A successful notification means only that local memory admitted work.
- Different replicas may send the same Bot concurrently or out of order.
- A request that times out locally may still complete later in BCS.
- A process restart can lose queued or dirty work.
- Stale managers, including revoked permissions, may remain until a later successful
  sync or a manual resync.
- Disabling the feature stops future sends; it does not clear existing BCS managers.

Do not interpret this feature as an outbox, distributed lock, or eventual-consistency
system. Existing editor locks reduce common overlap but do not serialize every trigger.

## 2. Enablement checklist

The only Backend configuration is:

```yaml
bot_manager_sync:
  enabled: true
  env: prod
  bcs_base_url: "https://bcs.example.com"
```

The block is absent or `enabled: false` when disabled. There is no Redis, memory mode,
coordination generation, target ID, or per-tenant bearer setting.

When upgrading the earlier PR implementation, remove `target_id`,
`coordination_generation`, and `coordination_mode` from deployment configuration
before starting this version. Unknown keys are rejected even when disabled. Old
manager-sync Redis keys are neither read nor automatically deleted; any cleanup
must wait until no old-version sender uses that namespace. No SQL migration is
needed.

Release wiring: PR #3208 targets `REL20261029` and includes the local BCS trust
rule. Production BCS must still provision the corresponding scoped permission
and signing key; the local template is not production configuration.

Before enabling, verify:

1. `bcs_base_url` is the BCS origin, not a gateway URL.
2. The existing gateway-principal signing key resolves for Backend. Never print it.
3. BCS verifies HS256 principals with `iss=backend`, `aud=bcs`, and `kid=bare`.
4. BCS has an explicit `trusted_apps` rule for `app="backend"`, the Backend tenant,
   and action `managers:sync`.
5. The Bot has its stable Backend `entity_id` and is registered under the expected BCS
   identity with initialized Bot Authority ownership. An owner read returning
   `bot_ownership_not_initialized` is a deployment/governance precondition to
   resolve with the existing BCS maintenance process, not by bypassing auth.
6. Projected user IDs are valid BCS Humans. The syncer does not create Humans.

Backend sends no Bearer token and does not route manager PUTs through the gateway.
Every request carries a 60-second self-signed `X-Avernet-Principal` containing only
the Backend app identity scoped to the snapshot tenant.

Singlebox may keep an explicit `enabled: true` block when it runs the real local BCS,
the shared local signing key is installed, and local BCS trusts the `backend` app for
that tenant. It reuses the base manager-sync provider and real HTTP plugin; there is no
singlebox-specific manager-sync module or separate memory mode. Test doubles are for
TEST/CORP_TEST profiles, not the singlebox runtime.

## 3. What success and failure mean

A worker reads current facts, projects the complete roster, and sends:

```http
PUT /api/v1/collaboration/bots/{bot_id}/managers
X-Avernet-Principal: <backend service principal>

{"manager_user_ids": ["user-a", "user-b"]}
```

The roster contains eligible explicit `admin` and `member` collaborators, excluding
the current Backend Owner. The 1,000-ID cap is checked **after** Owner exclusion
and deduplication in both reader and projector. Passport and device credentials remain admin-only.

One round starts at most three sends in a 15-second budget. It retries `NOT_SENT` and
`UNKNOWN` failures, including timeout and HTTP 5xx, with approximately 1s/2s plus
jitter. It does not retry a permanent `REJECTED` response. Every attempt re-reads
current Backend facts.

Because `UNKNOWN` is retried without a distributed barrier, both the old and new
requests may eventually apply. A completed round therefore describes only its local
observation; it does not establish global ordering or durable convergence.

A read or projection failure sends nothing. In particular, it never clears BCS with
`[]`. An empty replacement is valid only after a successful authoritative read that
really projects an empty roster.

## 4. Observability and triage

Use the structured manager-sync logs and existing service/BCS request logs. Do not log
or copy manager rosters, JWTs, signing keys, or response bodies into incident notes.

Useful distinctions:

- `submit_admitted`: queued/coalesced locally; `reason` identifies collaborator,
  Owner handoff, Space membership, Bot-space change, or repair. This is not a
  delivery receipt and does not identify the origin of every coalesced send.
- `submit_rejected`: local admission was full, closed, or received the wrong env.
  Closed admission takes priority during shutdown.
- Diagnostic counters use finite event/outcome labels and thread-safe snapshots;
  trigger reasons appear in logs, never as arbitrary/high-cardinality labels.
- `read_failed` / `projection_failed`: no BCS request was sent.
- `send_rejected`: BCS permanently refused the request; check identity, tenant,
  `trusted_apps`, Bot/Human registration, and request shape.
- `send_retryable`: a transient/uncertain delivery failed; another bounded attempt
  may be sent. An uncertain previous attempt can still complete late.
- `retries_exhausted` / final `FAILED`: no more sends occur in that round. A later
  notification or manual repair is still allowed.
- `send_drift`: the confirmed BCS total differs from the submitted roster size.

There is no blocked-key registry to inspect or clear. Do not wait for or manipulate
Redis keys; none are part of this feature.

## 5. Manual resync

Repair is a controlled maintenance surface resolved from the deployment injector. It
is not a public HTTP endpoint and must not become one.

Use the already-running application's injector when the maintenance environment
provides it. **Constructing a fresh injector and immediately exiting after enqueue
is not a resync:** it has not started the background worker.

For a standalone maintenance process, use the deployment's actual configuration,
existing business database and signing key (never TEST/CORP_TEST recording plugins),
then start and stop the sync lifecycle owned by that process. Do not bootstrap or
recreate the business schema. For example, a single-Bot resubmission is:

```python
import asyncio
import os

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import BotManagerSyncDispatcher
from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import BotManagerSyncLifecycle
from agentclaw.community.core.bot_collaborator.manager_sync.repair import ManagerSyncRepairService
from agentclaw.community.di import DeployProfile, build_injector, validate_deploy_environment
from agentclaw.community.di.config_bootstrap import register_config_provider
from agentclaw.community.di.modules_bootstrap import (
    register_corp_modules,
    resolve_extra_modules,
)
from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncKey

profile = DeployProfile.detect()
if profile in (DeployProfile.TEST, DeployProfile.CORP_TEST):
    raise RuntimeError("maintenance resync requires real deployment delivery")
validate_deploy_environment()
register_config_provider(profile)
register_corp_modules(profile)
injector = build_injector(
    profile=profile, extra_modules=resolve_extra_modules(profile)
)
key = SyncKey(os.environ["MANAGER_SYNC_TENANT"], os.environ["MANAGER_SYNC_ENV"],
              int(os.environ["MANAGER_SYNC_BOT_PK"]))

async def main():
    lifecycle = injector.get(BotManagerSyncLifecycle)
    await lifecycle.startup()
    try:
        repair = injector.get(ManagerSyncRepairService)
        entry = injector.get(BotManagerSyncProtocol)
        if not isinstance(entry, BotManagerSyncDispatcher):
            raise RuntimeError("manager sync is disabled or unavailable")
        dispatcher = entry
        result = repair.request_bot(key)
        print(result.value)  # admission only
        if result not in (SubmissionResult.QUEUED, SubmissionResult.COALESCED):
            raise RuntimeError("resync was not admitted; check configuration/capacity")
        if not await dispatcher.drain(timeout_seconds=30):
            raise RuntimeError("local round did not finish in the maintenance window")
    finally:
        # This process owns this lifecycle; do not shut down an application's
        # shared lifecycle from a maintenance callback inside that application.
        await lifecycle.shutdown()

asyncio.run(main())
```

Local drain means the submitted round ended, not that BCS applied it. Inspect its
outcome logs and verify the roster through an authorized BCS read.

For a tenant/env sweep, replace the single-Bot submission inside the same lifecycle
scope with bounded pages. Wait between pages instead of queuing an entire tenant:

```python
cursor = 0
while True:
    page = repair.request_page(avernet_tenant=key.avernet_tenant, env=key.env,
                               after_pk=cursor, limit=100)
    print(page.last_pk, page.queued, page.coalesced, page.rejected)
    if page.rejected_keys:
        # Maintenance output only; retain these keys for resubmission.
        for rejected_key in page.rejected_keys:
            print("not admitted", rejected_key)
        raise RuntimeError(f"page has {page.rejected} unadmitted keys; do not skip them")
    if page.queued + page.coalesced == 0:
        break
    if not await dispatcher.drain(timeout_seconds=600):
        raise RuntimeError("page did not drain; inspect before advancing")
    cursor = page.last_pk
```

`limit` is clamped to 100. `rejected_keys` is the exact set not admitted on that page.
Queued/coalesced counts are not delivery confirmation. Repair sends no stored payload:
the worker re-reads current Backend facts when it executes.

There are no marker-read, marker-clear, CAS, owner-hold, or drain-proof repair methods.
Owner handoff compensation is the same `request_bot` call as any other refresh; it
does not transfer BCS ownership or prove owners match.

## 6. Common incidents

### Removed collaborator still has BCS permission

1. Confirm the Backend collaborator/Space state is correct.
2. Check for `submit_rejected`, read/projection failures, rejected BCS responses, or
   retry exhaustion around the change.
3. Correct identity, tenant, registration, or capacity problems.
4. Run `request_bot` for the Bot.
5. Verify through an authorized BCS read/audit path. Do not treat admission as proof.

The stale permission is an accepted best-effort risk until step 5 succeeds.

### BCS timeout or 5xx

The round may retry, and an earlier request may still apply late. Do not attempt to
“clear” an in-flight record; no such record exists. Inspect BCS logs when ordering
matters, then submit a fresh `request_bot` after the service is healthy. A later fresh
projection is the only normal correction mechanism.

### BCS 401/403

Verify the shared signing key, `backend` issuer, `bcs` audience, `bare` key ID, tenant,
and BCS `trusted_apps(app=backend, actions=[managers:sync])` rule. Do not add a gateway
Bearer credential as a workaround.

### Read/projection failure

Fix the missing Bot identity, unavailable Team Space facts, invalid collaborator row,
or database error. Then request a fresh sync. Never manually replace the failure with
an empty manager list.

## 7. Rollback

Set `bot_manager_sync.enabled: false` and roll the Backend deployment. This stops new
admission and removes the worker/pool on restart. It does not undo manager sets already
written to BCS and does not guarantee queued work from a stopping process completed.

If rollback must also revoke permissions, use an approved BCS administrative process
or restore the Backend delivery path and run explicit fresh repairs. No automatic
rollback or schema migration exists.

## 8. Evidence discipline

For a release or incident, record the Backend build, BCS build, tenant/env, relevant
configuration posture, test command, and actual result. Do not copy old Redis/marker
review evidence into this contract. The earlier ordering/barrier design is historical
only. The 2026-10-28 local 1,178-test selection is recorded in the feature spec; remote
CI, unified singlebox, and external live acceptance remain separate evidence.

The singlebox CI collaborator suite also exercises Backend HTTP changes followed
by an exact BCS Owner-read projection assertion (`test_manager_sync_bcs_projection.py`).
A passing test proves that isolated stack's real direct-delivery path, not external
rollout or eventual consistency. TEST and CORP_TEST bind recording plugins and do
not provide that evidence.
