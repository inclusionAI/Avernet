# Bot manager sync — operations runbook (backfill, compensation, controlled recovery)

> **Revision (2026-10-28, direct delivery — THE sanctioned form):** the
> syncer delivers by PUTting **directly to the BCS origin** under a
> self-signed `X-Avernet-Principal` that names this Backend component's own
> service identity (`iss=backend`, `aud=bcs`, one `backend` app principal
> scoped to the Backend tenant of the snapshot). There is no gateway hop and
> no per-tenant bearer. The prerequisites below that used to name the
> gateway's trusted-App credential registry now read: the shared principal
> signing key resolves under the existing gateway_principal conventions
> (§2.1), and BCS registers the `backend` issuer + the
> `trusted_apps(app=backend, tenant=<backend tenant>,
> actions=["managers:sync"])` rule (§2.2).

Backend→BCS bot manager set sync: what an approved operator runs for the
**first backfill**, for **targeted compensation** of dropped work, and for
**controlled recovery** of stuck markers — and the proofs each step must leave
behind. The maintenance surface is
`ManagerSyncRepairService` (`agentclaw.community.core.bot_collaborator.manager_sync.repair`)
bound in the DI composition root (`di/modules/bot_manager_sync_module.py`).

Reading this runbook is not authority. Every step below assumes the operator
already holds approved maintenance authority for the deployment and its
incident/change ticket. The service itself cannot prove evidence or
authorization: it demands an explicit `operator_id` and `evidence` on the
destructive step (§5) and refuses empty ones — but the *truth* of that
evidence is the operator's responsibility.

Ground rules that hold throughout:

- **Repair is never a public endpoint.** There is no HTTP route, and none may
  be added. All operations below run through the injector (§1).
- **Submission is never confirmation.** `request_bot` /
  `request_page` return admission verdicts only (`SubmissionResult`:
  QUEUED / COALESCED / REJECTED / DISABLED — refused keys are preserved
  precisely in `RepairPage.rejected_keys`). Whether a sync actually ran, and
  what it confirmed on the BCS side, is answered by the delivery round's
  `SyncOutcome` — a queued/all-zero sweep alone is never acceptance (§6).
- **Repair never bypasses blocking.** Repair requests go through the same
  bounded admission as business notifications: env gate, queue capacity,
  closed admission and coordination markers all apply unchanged. A marker
  that blocks a sender blocks a repair send too — clearing it is §5's explicit,
  audited step, never a side effect.
- **No secrets, no rosters in outputs.** Repair outputs and the page logs
  carry counts and ids of *keys* only (tenant, env, bot_pk, attempt ids,
  operator id, evidence). App credentials must never be printed, and manager
  id lists never appear in maintenance output.

## 1. Controlled entry: the maintenance injector, not a new endpoint

`ManagerSyncRepairService` is resolved from the deployment's **existing
controlled Python / maintenance environment** — a deployment shell or
maintenance job runner that already has access to the backend checkout, the
deployment's configuration and the DI composition root. No public endpoint
is (or may ever be) created for it.

Concrete steps:

1. Open the controlled maintenance environment on the deployment host (the
   deployment's own virtualenv and configuration — never a copy with
   hand-edited config).
2. Build the deployment's injector and resolve the service:

   ```python
   from agentclaw.community.di import build_injector, DeployProfile
   from agentclaw.community.core.bot_collaborator.manager_sync.repair import (
       DisabledManagerSyncRepair,
       ManagerSyncRepairService,
   )

   injector = build_injector(profile=DeployProfile.<deploy profile>)
   repair = injector.get(ManagerSyncRepairService)
   ```

3. Check what came back. If the feature is off or its stack is unavailable,
   the container binds the zero-resource `DisabledManagerSyncRepair` twin:
   `request_bot` answers `DISABLED`, and `request_page` /
   `clear_confirmed_attempt` raise `RuntimeError` with a stable reason word
   (`disabled`, `shared-redis-required`, `principal-signing-key-required`).
   That is
   the honest answer — there is no coordinator to CAS against in that
   posture, and no verdict may be fabricated. Fix the posture (config /
   signing key / Redis) before continuing; do not work around the twin.
4. Only then run §2–§6. Nothing in the maintenance environment exposes the
   service over HTTP, and the shared principal signing key used to mint
   the delivery's service principal stays inside the deployment's secret
   store.

## 2. Verify the actors before the first send

Run this checklist before the **first backfill** of a new deployment and
after any credential / scope / registration change. Every item needs
**server-side evidence**, not "the config file says so":

1. **Shared principal signing key** — `SecretNamesConfig.gateway_principal_signing_key`
   resolves through the SecretResolver and is non-empty. This is the SAME
   named secret the `/openapi/v1` verifier init reads, and the delivery path
   mints its self-signed `backend` service principal from the value it
   resolves; a deployment where it misses keeps the whole feature
   unavailable (`principal-signing-key-required`). Verify presence/rotation
   state; never print the value, and remember rotation requires a restart of
   every side of the contract (gateway, backend, BCS).
2. **BCS issuer + app + tenant scope** — the deployed BCS is configured to
   trust the `backend` issuer for `aud=bcs` (its gateway_principal issuers
   list contains `backend`), and its `[[bot_authority.trusted_apps]]` rule
   names exactly the scope the deployment targets: `app = "backend"` (byte-exact),
   the tenant matching `avernet_tenant` of the keys being
   synced, and `actions = ["managers:sync"]`. A rule scoped to another
   tenant can never authorize a snapshot outside it — BCS denies by default.
3. **Bot registration and version** — the Bot is registered on BCS with a
   known version. Registration evidence comes from the BCS registry itself,
   not from the Backend's own tables.
4. **Stable identity** — the Backend Bot carries its stable `entity_id`
   (the id that survives an owner handoff). A Bot without a stable identity
   fails every projection with `SnapshotUnavailable` — fix the data, do not
   sync around it.
5. **Human registration** — every projected manager id is a registered Human
   on the BCS side. Ids that are not registered humans must be resolved before
   the sweep, or the round will refuse.
6. **Owner alignment** — the Backend's owner of the Bot and the BCS-side
   owner record **agree, evidenced from both sides**. Owner alignment is a
   precondition for the owner-check marker recovery in §5.

> An ordinary App credential is **not assumed to be able to `GET managers`**.
> A 403/404 (or an empty answer) from an ordinary App is *not* proof that the
> roster is empty — only the evidence in §4/§6 counts. Do not treat a
> permission-denied on an ordinary App as a data answer.

## 3. First-time Redis namespace generation initialization

The coordination namespace is guarded by a **generation sentinel** — the Redis
key `mgrsync:v1:{sha256(<target_id>)}:generation`. Every coordination script
verifies it before touching a lease or marker; a missing or mismatched
sentinel makes **every sender refuse with `CoordinationUnavailable`**. The
sentinel is never auto-created by workers at runtime.

Rules:

1. **Initialize only with all senders stopped.** Before writing the
   sentinel for the first time, stop every Backend instance that could send
   manager syncs for the target (roll the deployment, or keep
   `bot_manager_sync.enabled=false` on every replica during the init). The
   initializer must be the only writer of the namespace.
2. In the controlled maintenance environment, with the same `target_id` the
   deployment is configured with, write the sentinel with an explicit,
   operator-chosen generation string (e.g. `ops-init-<date>`). The key is
   `mgrsync:v1:{sha256(<target_id>)}:generation` — compute it with the
   deployment's own helpers rather than hand-hashing:

   ```python
   from agentclaw.community.plugins.community.manager_sync_coordination import (
       coordination_keys,
   )
   key = coordination_keys(target_id="<target_id>", key=<any SyncKey>).generation
   # redis: SET <key> "ops-init-<date>"   (plain SET, no TTL)
   ```

   Never re-use an old generation string after a loss.
3. **Pin the generation in the deployment config** — set the same string as
   `coordination_generation` in the deployment's `bot_manager_sync` block —
   and deploy it **before** enabling sending. A deployment whose configured
   generation does not match the sentinel refuses to send.
4. Only then enable sending (`bot_manager_sync.enabled=true`) and start the
   Backend instances.
5. **Cache loss / eviction / FLUSHDB of the namespace cannot self-heal.**
   If the sentinel disappears or no longer matches, every sender stops, and
   no worker will ever re-create it. Re-initialization is this section, redone
   by an operator: stop senders → write a *new* generation → pin it → re-enable.
   The shared Redis used here must provide the non-eviction guarantee; if it
   does not, keep the feature off.

## 4. Unknown delivery: stop, drain, prove — then (and only then) clear

An UNKNOWN outcome (in-flight marker written, delivery answer unknown —
crash mid-send, timeout, network cut) leaves a **TTL-less in-flight record**
that blocks every later send for that key. Recovery is deliberately slow:

1. **Stop the old senders and drain.** Stop (or close admission on) every
   process that could still emit the round in question, then let the queues
   drain bounded. The point is to make *no further submissions* possible
   while the evidence is collected.
2. **Prove from the BCS server side that the request is no longer being
   submitted**: the delivery's `request_id` (returned in the
   `ManagerSetChange` confirmation), the BCS audit log of applied /
   manager-set requests, and BCS-side server evidence together decide
   whether that attempt was applied, never applied, or is still open.
   Do not proceed while the answer is "might still be in flight".
3. A **fixed wait time is NOT proof**, and **`GET roster` is NOT proof**:
   waiting N seconds proves nothing about a retrying/restarted sender, and a
   fetched roster cannot distinguish "applied my write" from "never received
   it" — a roster that looks right does not license clearing a marker whose
   write status is unknown. Only request_id / audit / server evidence does.
4. Once §4.2 shows the attempt was **never applied** (or was conclusively
   applied and acknowledged — see §6 for the empty-roster caveat), continue
   with §5. Records that stay unresolved go to engineering, not to a forced
   clear.

## 5. Marker clearing: exact attempt_id CAS, with operator + evidence

Clearing a marker is the single destructive step in this runbook, and it is
strictly CAS: `clear_confirmed_attempt` deletes **exactly** the marker whose
id matches the `attempt_id` — the coordinator's compare-and-delete — and
returns its verdict verbatim.

1. Read the blocked state in the maintenance environment. Build the
   coordinator the deployment runs (`RedisManagerSyncCoordinator` over the
   shared Redis URL, with the deployment's `target_id` and
   `coordination_generation`) and call `coordinator.read_blocked(key)`: it
   returns the in-flight attempt id if one is present, else the owner-hold
   marker id, else `None`. That exact id is the `attempt_id`; **a guessed or
   stale id simply refuses** (the service returns `False`, the marker stands,
   nothing is sent).
2. Call the clear with the confirmed id and a real audit record:

   ```python
   cleared = repair.clear_confirmed_attempt(
       key,                                  # the SyncKey of the blocked Bot
       attempt_id="<exact id from read_blocked / evidence>",  # never guessed
       operator_id="<the operating human's id>",
       evidence="<ticket + what §4 proved + from which server evidence>",
   )
   ```

   Empty/whitespace `operator_id` or `evidence` is rejected before the
   coordinator is ever touched. The service logs `[MANAGER-SYNC-REPAIR]
   marker_cleared ... attempt_id=... operator_id=... evidence=...` (or the
   matching refusal at WARNING) — that audit line *is* the required
   operator/evidence record.
3. **Owner-check markers need more**: an owner-hold marker (planted for a
   live owner verification) may only be cleared with **both-sides
   owner-consistency evidence** — Backend and BCS agree on the current owner,
   evidenced from both sides, in addition to §4's not-submitted proof.
   Clearing it with only a partial owner story defeats its purpose.
4. After a successful clear, **re-request explicitly**: `repair.request_bot(key)`.
   The re-request is never automatic — no send is ever hidden behind a clear.
5. A refused CAS (`False`) is a stop, not a retry loop: re-check the
   attempt id and the evidence; a repeated refusal means the marker is
   not (or no longer) the one you think it is.

## 6. First empty roster: acceptance needs existence proof

A first backfill over a fresh BCS registration may legitimately produce an
empty manager set, and a delivery round will answer
`ACKNOWLEDGED_EMPTY` (total 0 against an empty push). **Queued counters and
all-zero pages alone are not acceptance.**

1. `RepairPage` counts and `QUEUED` results prove only *submission*, and
   `ACKNOWLEDGED_EMPTY` proves only that BCS acknowledged the request —
   neither proves the target existed.
2. For the first empty-roster acceptance, additionally prove **the Bot
   exists** on the BCS side: registration evidence (registry entry, version)
   plus the stable identity from §2.4, exactly like the preconditions of §2.
3. Acceptance of the first (empty) roster therefore requires all of:
   every key of the sweep accounted for (`queued + coalesced + rejected`
   equals the paged keys, `rejected_keys` empty), every delivered round's
   `ACKNOWLEDGED_EMPTY` backed by that round's Bot-existence proof, and no
   lingering blocked keys (`read_blocked` answers `None`). Anything less is
   not acceptance — it is an unfinished sweep.

## 7. Disabling the feature

Setting `bot_manager_sync.enabled=false`:

- **stops future projections only.** No further notifications are admitted
  (`DISABLED`), no rounds run, no lifecycle loop starts.
- **never undoes already-written managers.** BCS manager sets stay exactly as
  they are; Redis coordination keys (leases, markers, generation sentinel)
  stay in place and can be recovered later via this runbook.
- **the business Backend keeps running normally.** Collaborators, owner
  handoffs, spaces and every other backend flow are unaffected — their sync
  notifications just draw `DISABLED`.

To re-enable, §3's posture must hold again (generation pinned and matching);
otherwise the senders stay refused.

## 8. Release evidence: where each acceptance fact lives

Every release that ships the manager-sync capability must be able to show,
from its own run, the following distinct evidence — never one gate standing
in for another (see `apps/backend/specs/2026-10-27-bcs-manager-sync/spec.md`
for the gate table and the NOT-RUN discipline):

1. **Backend unchanged where it must be:** the Backend's existing module
   suites, architecture tests and changed-line coverage gates, plus the SAST
   branch-diff scan — green on the release candidate.
2. **The feature itself:** the external live acceptance suite
   (`tests/community/acceptance/bcs_manager_sync_external/`) green against
   the controlled release Backend + the **deployed** dev BCS — with the
   Backend build SHA, the BCS deployed version and env/tenant recorded by
   the `live_manager_case` fixture, base matrix first, then the destructive
   drills in the isolated fault-capable posture. This suite is the *only*
   live evidence for the feature: the release singlebox builds the
   release-tree BCS and proves nothing here, and no singlebox result — pass
   or fail — may be cited as feature acceptance.
3. **BCS contract authority:** the dev-source Rust authority tests
   (`bcs-api-http --test bot_authority_routes`) at a recorded, fixed dev SHA.
   They prove the contract the Backend codes against; they cannot prove which
   version an endpoint actually runs — deployment verification is separate
   and uses the §2 checklist.
4. **Deployment postures:** §2's actor checklist verified (shared signing
   key distribution, BCS `backend` issuer + trusted_apps rule, tenant
   mapping, registration, stable identity, Human registration, owner
   alignment), §3's generation init + non-eviction guarantee confirmed, and
   the first-backfill sweep's per-key outcomes (§6) captured with the empty
   and non-empty rosters' existence proofs.
5. **Drill records:** at least one full §4/§5 unknown drill (stop → prove
   from BCS server-side evidence → CAS clear with operator + evidence →
   re-request → convergence) and the owner-check drill (owner summary
   unchanged after a Backend handoff; marker left to §5.3 reconciliation)
   performed against the isolated posture, with the drill's evidence text
   kept with the release ticket.

A release missing any of these keeps `bot_manager_sync.enabled=false` on
every replica; the async de-permission window (§7 of the spec) is acceptable
only for a live-verified deployment, and a disabled feature simply keeps the
pre-existing Backend behavior.

One more operational fact about the live suite itself: its owner-check drill
is deliberately the **last** test in the file and ends with the case target
intentionally blocked (roster un-reconciled, marker present). After a full
live run, re-provisioning the case Bot and completing the §5.3 both-sides
reconciliation are prerequisites before the suite can run again against the
same target.

## 9. Singlebox/dev downgrade posture (memory coordination — dev only)

Singlebox runs the manager sync in its **single-replica downgrade form**:
`coordination_mode: memory` in the `bot_manager_sync` block
(`application-singlebox.yaml`):

- **Coordination**: the in-process `MemoryManagerSyncCoordinator` bound to
  the configured `coordination_generation` — the SAME coordinator contract
  the conformance suites pin. All whole-stack invariants (claim / begin /
  finish / hold-owner-check / generation sentinel / attempt-id CAS) are
  enforced inside one process. There is **no cross-process protection**: the
  downgrade is valid *only* because singlebox has exactly one Backend
  process and always whole-stack-restarts — that is the single-writer
  guarantee for this dev stack.
- **Delivery stays the real production path unchanged**: the self-signed
  HTTP plugin PUTs **directly to the local BCS origin** (`bcs_base_url`,
  http://127.0.0.1:21000 — direct mode is the sanctioned form on every
  stack), carrying an `X-Avernet-Principal` the Backend minted for its own
  `backend` service identity, and the dev BCS applies its
  `[bot_authority] trusted_apps` scope rule (`managers:sync`, byte-exact
  app + tenant). The signing-key availability check is identical to the
  redis mode — a missing key keeps the feature unavailable with a
  diagnostic (`principal-signing-key-required`), never an unsigned send.
- **Credentials**: the shared gateway-principal signing key, in its
  clearly-marked dev form (`AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE`,
  dev literal `avernet-dev-signing-key-NOT-FOR-PROD`, exported by
  `singlebox/modules/backend.sh`) — the same key the local gateway signs
  with and the dev BCS verifies with, overridable together from
  `singlebox/.env.local`. The earlier gateway-seeded
  `singlebox-manager-sync` app and its dev bearer were removed with the
  direct-delivery revision; the gateway's `local_dev_apps` seed machinery
  stays (config-gated, currently empty — it seeds nothing anywhere).
- **No Redis anywhere** in this posture: no `redis_url`, no generation
  sentinel bootstrap (§3), no eviction concern — the coordination namespace
  lives and dies with the one Backend process.

**Production is unchanged.** Every non-dev deployment stays on the default
`coordination_mode: redis` (shared, non-evicting Redis + the ops-initialized
generation sentinel, §3), keeps `enabled: false` unless it has completed the
§2/§3/§6 checklists, and must never set `memory` — two sender processes
sharing one BCS would race undetected. A production config without shared
Redis keeps the feature unavailable (`shared-redis-required`), exactly as
before; singlebox evidence never substitutes for §8's release evidence.

---

### Concrete repair driving procedures (reference)

**First backfill sweep** (after §2, §3):

```python
cursor = 0
while True:
    page = repair.request_page(
        avernet_tenant="<tenant>", env="<deployment env>",
        after_pk=cursor, limit=100,
    )
    print(page.last_pk, page.queued, page.coalesced, page.rejected)  # counts only
    if page.rejected_keys:
        # Do NOT blind-advance past these: each is a key the notifier
        # refused (REJECTED/DISABLED). Record them, investigate capacity /
        # admission / posture, then re-request each explicitly:
        for key in page.rejected_keys:
            ...  # evidence + decision per key
            repair.request_bot(key)
    if page.queued + page.coalesced + page.rejected == 0:
        break  # empty page: every sync-eligible Bot has been paged
    cursor = page.last_pk
```

**Targeted compensation sources** — what to compensate, and where the evidence
of a drop comes from:

- `RepairPage.rejected_keys` — refused by admission mid-sweep (handled above).
- `dispatcher.stats.rejected_space_keys` — space fan-outs that hit the full
  queue; re-run the affected keys through `request_bot`.
- Defer-window drops — the dispatcher logs
  `defer_window_expired` when an entry exhausts its bounded defer budget and
  is dropped "for repair"; that log line gives tenant/env/bot_pk.
- `latched_owner_keys` in `dispatcher.stats` — owner-gated keys whose hold
  failed; these follow §5's owner-consistency path, never a plain re-send.