# B6–B7 implementation and joint acceptance

## Commits and deployment units

B6 is the independent Legacy resource-compatibility commit. B7 adds the Backend
Bot-management handoff subcomponent, journal/worker, scoped write guards and
credential cutover contracts. OCB supplies the corporate Passport RPC adapter;
tcauthmng supplies the facade/credential retirement implementation. No Frontend
or SecBaaS changes are included, and no deployment enables initiation by default.

OCB's starting dev lacked B5 Passport addressing. Its implementation branch
therefore first ports the already-existing B5 Backend contract changes, retaining
current-dev tests and leaving the Avernet submodule pointer unchanged. Before
OCB deployment, publish the public Backend commit, then pin OCB to that available
commit using the repository's normal contribution workflow. Local cross-tree
tests explicitly use the new Backend source; they are not evidence that the
unchanged submodule pin already contains it.

## Command contract

Authenticated internal endpoints, under `/api/bots/owner-handoffs`:

| Method/path | Input | Result |
| --- | --- | --- |
| POST collection | bot_pk, new_owner_id, request_key | Idempotent durable operation; no immediate success claim |
| GET /{operation_id} | Server-authenticated actor | Non-secret phase/audit facts |
| POST /{operation_id}/retry | Server-authenticated actor | Enqueue recovery of the existing operation; never reset reissue intent |
| POST /{operation_id}/cancel | Original actor/admin | Only QUEUED and unclaimed operations can cancel |
| GET /{operation_id}/authorization | Actual execution identity | Fresh AAC status/consent URLs only, never the token |

Bot PK is the command identity. All resource and AgentPass addressing retains
server-scoped env + entity + bot_id. Neither env, tenant nor actor is accepted
from the command body. List/detail Legacy contracts remain unchanged by B7.
Disabled command/status/authorization paths return 404 before querying the new
table. Existing authenticated Bot reads remain available.

## Durable stages

| Phase | Work | Failure/restart behavior |
| --- | --- | --- |
| QUEUED | Check current passport; persist reissue intent and old generation | Cancellation remains possible only before intent |
| REISSUING | Initial caller issues once; later workers reconcile remote operation marker | Unknown result stays frozen; no blind second issuance |
| WAITING_AUTHORIZATION | Require selected generation and ISSUED AAC with token | Pending is not success; only executor may retrieve consent metadata |
| UPDATING_RUNTIME | Inject selected token via existing draft/online/verify/Caller fan-out | Any error, empty binding set or empty device acknowledgement blocks retirement |
| RETIRING_OLD_CREDENTIALS | Explicit RPC retires only previous local credential caches; AgentPass handles old remote credential expiry | Must acknowledge local writes; never revoke the shared Agent; partial outcomes retry forward |
| COMMITTING_OWNER | Recheck Bot/marker/work orders/executor; update responsibility and permissions atomically | DB failure preserves old Owner and maintenance marker; replay final transaction |
| SUCCEEDED | New Owner committed; old Owner collaborator row removed; lock released | Repeated request/status returns original operation |

Bot/entity/creator and resource locator columns never move. Whitelist/App Grant
remain attached to the Bot. Other collaborators and immutable historical actors
remain unchanged. A configured independent execution binding remains independent,
including STAFF; without one, the new Owner becomes executor and no binding is
created. Reissuing in both cases also replaces credential administration; do not
run ordinary admin-sync/updatePassport afterward, which could mint another
generation.

Pending Bot collaborator orders block transfer. Friend order IDs are opaque;
conservatively block pending friend approvals assigned to the previous Owner,
and processing friend approvals without a reliably scoped approver. Existing
orders and notifications are not reassigned. New Bot approval admission and
execution-identity changes check the aggregate maintenance marker under a Bot
row lock. Direct Bot update/space/ext/delete writes also lock and check before
mutating. Shared internal/OpenAPI write gates reject marked Bots; reads retain
their existing behavior.

## Rollout sequence

1. Review/apply `src/agentclaw/community/core/bot_management/owner_handoff/sql/2026_10_25_bot_owner_handoff.sql`
   in Backend. Retain the B5 execution-binding table and current TaskQueue schema.
   This DDL does not backfill or rename any existing owner column.
2. Publish/deploy tcauthmng facade **1.0.6.20261025** and corresponding server.
   Validate protocol-v1 capability and correlated query, then deploy the OCB
   adapter and matching public Backend pin. Old reissue callers omit new fields.
3. Deploy B6/B7 with `owner_handoff.enabled=false` and
   `owner_handoff.recovery_enabled=false`. Verify existing business first.
4. In an isolated acceptance environment with an enabled TaskQueue worker,
   enable recovery and initiation together for controlled backend-only testing.
   Use a non-default service Bot with known runtime targets. Confirm real AAC
   can issue for the intended executor; manual confirmation, if required, must
   be performed by that executor.
5. Complete the real acceptance matrix below, then implement/accept Frontend and
   SecBaaS consumers in B8. Production initiation remains disabled until then.

Example acceptance-only flags (not a production config change):

```yaml
owner_handoff:
  enabled: true
  recovery_enabled: true
task_queue_worker:
  enabled: true
```

To stop new transfers while draining existing work, use enabled=false and
recovery_enabled=true. Reverting code/config cannot restore revoked credentials;
reconcile outstanding operations forward. Do not remove the journal or force
clear a maintenance marker after remote intent.

## Joint acceptance matrix

Local automated tests use real ORM transactions and explicit external contract
fixtures. They cover A→B stable Legacy paths, collaborator retention/removal,
independent/default execution, ambiguity/default exclusions inherited from B4/B6,
idempotency, timeout-after-issuance, unknown outcomes, pending AAC, generation
drift, failed/empty runtime acknowledgements, failed revocation, final transaction
rollback, enqueue-gap recovery, worker leases, tenant isolation and closed-entry
HTTP behavior. These fixtures are not production mocks or real AAC evidence.

Required environment evidence before enabling production:

- Selected AAC executor/admins are correct; SKIP_CONFIRM_IF_ALLOWED behaves as
  expected, and manual consent still works when required.
- Every actual draft/online/verify/Caller device uses the selected generation;
  test mixed provider support and partial/unreachable devices. No-device Bots
  currently remain blocked rather than reporting a fabricated runtime success.
- Retrying provider revocation is safe after a lost response; old ALC/AAC/AEC
  and cached tokens stop executing, while the selected generation remains valid.
- Existing long-lived proxy/operator sessions have an agreed invalidation or
  expiry strategy; this implementation does not invent a provider capability.
- Background publication/projection and all other direct mutators are quiesced
  or accepted against the maintenance fence; shared HTTP gating alone is not
  proof that every external writer honors it.
- Kill/restart a real worker at each durable stage and reconcile persisted
  remote markers; inspect journal/queue storage for absence of raw credentials.
- Frontend/SecBaaS and real external consumers pass their deferred compatibility
  matrix before opening the product entry.

The staff directory confirms existence, not employment-active status. If active
employment must be enforced automatically, an authoritative status capability is
still required; it cannot be inferred from display names.


## B8 consumer work

Frontend should use returned entity_id for stable resource location while
keeping owner_id for responsible-person display. Add a service-Bot-only transfer
entry (hide personal/default), staff selection, per-action idempotency key,
operation progress/retry/cancel and executor-only pending-consent handling.
Refresh Bot ownership/permission/navigation state after success; preserve the
existing operation on uncertainty instead of creating another key and blindly
reissuing. Backend remains authoritative for all permission/maintenance checks.

Verify the recipient's access under existing Space membership and external
MCP/App authorization rules; transfer does not automatically grant Space-level
or unrelated personal permissions. Validate those combinations in the real
acceptance environment before opening the entry.

SecBaaS can remain the last independently coordinated consumer. Trace its actual
Backend API calls first: where Backend already normalizes publish/resource
locators, validate that API compatibility instead of assuming SecBaaS directly
reads ac_bot_publish or requiring an unsupported client change. Any confirmed
caller contract change belongs in that consumer's later review and acceptance.

## 2026-10-27 provider clarification and publication compatibility

AgentPass confirmed `revokeIdentityCredential` deletes the shared agentCode's
Agent and cascades to its credentials. Owner handoff must not invoke that API.
The compatible `retireAgentCredentials` RPC now only retires old local caches;
remote credentials follow AgentPass's automatic retirement/expiry. This does not
promise immediate remote invalidation. Agents already deleted by the old path
need provider recovery/re-registration before runtime acceptance.

Legacy publication requests still accept `publish_id` or owner-shaped locators.
Both read and write permission interceptors resolve the Bot's current Owner;
only writes are blocked by the handoff maintenance fence. Build, promotion,
restart, retry, restore, rollback and scaling load the Bot through stable
address lookup. The publish record, resource paths and runtime legacy locator
keep entity identity. New approvals use the current Owner and leave history
unchanged. Deletion checks current ownership and protection rules before any
version teardown, then cleans service-Bot resources under entity. Legacy scale
and direct status mutations now require the same ADMIN/Owner authorization as
other publication writes. Frontend payloads, schema and personal/default deletion
policies are unchanged.
