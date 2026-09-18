# BCS-Native Bot Publication

## Problem

`POST /openapi/v1/collaboration/bots/{bot_uuid}/public` currently fails with
`404000 "Not found"` for any bot that exists only in BCS. The chain is:

1. The gateway verbatim-forwards the request to the Backend
   (`community/adapters/http/openapi_v1/collaboration_bots/router.py`).
2. `BotPublicService.public_bcs_bot` splits `bot_uid` on `:` and requires an
   `ac_bots` row matching `(bot_id, owner_id, is_delete=0, env)`
   (`core/bot_public/services/bot_public_service.py:584-587`). BCS-native bots
   were onboarded by another provider and have no such row, so the service
   raises `BotNotFoundError`.

Five further defects compound the problem:

- The publication write path (`_persist_public_approval`,
  `_apply_callback_decision`, `_apply_visibility_direct`) talks to BCS through
  the **Provider Admin** channel (`GET/PATCH
  /providers/{provider_id}/bots/{bot_uuid}/attributes`) using AC's provider
  credential. BCS documents that route as requiring an active Provider↔Bot
  binding (`src/bcs/.../bcs-http/CONTEXT.md`), which a BCS-native bot does not
  have. The calls succeed today only because the binding check is documented
  but not enforced; if BCS ever enforces it, every one of these calls turns
  into a 403.
- The antprocess callback endpoint (`POST /api/v1/antprocess/callback`) has
  **no authentication**, and the `public_scope` branch drops `puid` without
  validating it against the pending approval block. Any party that can reach
  the route can flip an AC bot's visibility with a form POST.
- The operation is admitted to **application callers** on the Backend
  (`ADMISSION` entry `AdmissionMode.OPEN`,
  `adapters/http/openapi_v1/admission.py:613`) while the gateway already
  declares `user: required` for the same path
  (`src/gateway/configs/application.yaml:314`). The two hops disagree, and the
  new write path cannot serve a caller with no Human identity at all.
- `visibility=private` "direct apply" shares the same provider-admin misuse.
- A failure while persisting the pending block is swallowed
  (`bot_public_service.py:648-662`, and the inline apply at `:668-680`), which
  under the state machine below would strand a real approval ticket with no
  block to match it.

## Contract

Identity model: **Gateway Principal + `created_by`**. Every BCS read and write
in the publication flow runs under the bot owner's user principal against the
existing BCS V1 endpoints — `GET/PATCH /openapi/v1/collaboration/bots/{bot_id}`.
No BCS code changes; no new BCS endpoint; no Provider Admin channel anywhere in
this flow.

### Admission is Human-only

The operation must be reachable only by a caller carrying a real User
identity:

- Backend: change the `ADMISSION` entry from `OPEN` to `REFUSED`. `REFUSED` is
  the mode that refuses a machine caller while still admitting a Human
  (`ADMITTING_MODES` excludes it, `admission.py:944-950`), and it is the label
  the edge already expects for this path.
- Gateway: `POST /openapi/v1/collaboration/bots/{bot_uuid}/public` already
  declares `user: required`, and the gateway's `_runner` raises `AuthError`
  when a required identity has no credential — so app-only callers are stopped
  at the edge today. The Backend change makes its independent table agree;
  both sides are pinned by their inventory tests and must move together.

Rationale: `resign_principal_token` preserves the inbound identity set and
cannot add a User that was never there, and BCS V1 requires a Human
(`authorization.rs:27-33`, "Bot/App/AccessKey identities never act as a
fallback"). An app-only caller would otherwise fail mid-flow — after a ticket
was created — instead of being refused up front.

### Principals

Two ways to obtain a usable principal, exactly one of which may mint:

1. **Synchronous path (request in flight) — re-address only.**
   `resign_principal_token` (`core/gateway_principal/signer.py`) re-signs the
   inbound, already-verified `aud=backend` token into `aud=bcs`, preserving
   identities and `exp`/`iat`. No minting happens on this path.
2. **Asynchronous antprocess callback (no inbound token) — constrained
   minting.** A new `core/gateway_principal/minter.py` builds the minimal
   identity set for the ticket's `owner_id` and signs it. This is the **only**
   minting site in the codebase; it accepts `owner_id` and nothing else, is
   reachable only from the approval-decision path, and logs a key fingerprint,
   never a token.

The minted token must satisfy the same wire contract BCS verifies, and must
reuse the existing principal config rather than restating constants:

```json
{
  "principals": [
    {
      "type": "user",
      "subject": {
        "id": "<owner_id>",
        "username": "<owner_id>"
      }
    }
  ],
  "iss": "<backend issuer>",
  "aud": "<bcs audience>",
  "iat": 1234567890,
  "exp": 1234567950
}
```

- JOSE header: `kid="bare"` (`_BCN_KEY_ID` in
  `utils/gateway_principal_config.py`), `typ="JWT"`.
- `iat`/`exp` are integer seconds; `exp` is `iat + 60`.
- `iss`, `aud`, `kid` and the HS256 key come from `PrincipalSignerConfig`
  (`gateway_principal_config.py`) — the minter defines no constants of its own.
- `subject.username` is required by the User principal model
  (`gateway_principal/models.py`), so it is set to `owner_id`.
- `principals` is never empty.

### Read fallback (publish entry)

`public_bcs_bot` first queries `ac_bots` as today. On a miss it re-addresses
the inbound principal and calls `GET /openapi/v1/collaboration/bots/{bot_uid}`:

- The GET contract requires a Human caller but applies **no ownership or
  visibility filter** (`api-contracts/v1/openapi/bots.yaml:407-415`) and may
  return either a physical Bot or a Human row. The GET is therefore a
  hydration, not an authorization.
- BCS 404 → `BotNotFoundError`; the `404000 "Not found"` wire contract is
  unchanged (`tests/.../test_openapi_public_bcs.py` keeps pinning it).
- The Backend itself checks the resource: `kind == "bot"` and
  `created_by == caller user_id`. A Human row, a missing `created_by`, or a
  mismatch is answered **404**, matching both the `ac_bots` owner-mismatch
  behavior and the platform's existence-non-disclosure rule
  (`principal.py` USER_ID_DESCRIPTION).
- Otherwise the service synthesizes the bot dict it would have read from
  `ac_bots`: `bot_id`, `owner_id`, `bot_name` from the BCS `name`,
  `entity_id` empty. The skills/MCPs sections of the approval context then
  fall into their existing tolerated-failure branches and render as empty —
  acceptable copy degradation, no flow change.
- The PATCH that follows is authorized independently by BCS on
  `record.created_by == caller` (`bcs-app-bot/lib.rs:563`); the Backend check
  is the early, non-disclosing one, BCS is the backstop.

### Synchronous writes

`_persist_public_approval` and `_apply_visibility_direct` stop calling
`BcnService.get_attributes/patch_attributes`. They re-address the inbound
principal and use `GET → merge → PATCH` on the V1 bot route. `friend_ext` is a
wholesale top-level replace on both the provider route and the V1 route — both
funnel into the same `control_plane.patch` — so merge behavior is preserved.

The OpenAPI handler gains a `PrincipalTokenDep` exposing the raw
`X-Avernet-Principal` header (already verified by `require_principal`) so the
service has a token to re-address.

### Asynchronous callback (corp)

`POST /api/v1/antprocess/callback` handling gains two guards **before** any
BCS write, then applies the decision through the minted principal:

1. **Corroboration.** The decision is only applied after
   `ApprovalWorkflowPlugin.query_approval_status(puid)` confirms it. This
   requires evolving the Plugin API from an untyped `dict` into a defined
   contract (see below).
2. **Pending-block state machine**, evaluated against BCS through the minted
   principal: the scope block (`public_user_approval` /
   `public_agent_approval`) must exist, its `status` must be `PROCESSING`, and
   its `puid` must equal the callback's `global_unique_id`. A terminal block
   replays idempotently.
3. Only then: merge the status block, and on `AGREE` flip the scope visibility
   field (`user → user_visibility`, `agent → visibility`, value from the
   block's stored `visibility`, default `protected`) and promote
   `view_friend_deps` to the scope's top-level `friend_ext` key
   (`view_scope_user_friend_deps` for `user`, `view_scope_agent_friend_deps`
   for `agent`) — one PATCH carrying `friend_ext` plus the visibility field.
4. The BCSFuse worker-state sync on `AGREE`+`user` is unchanged.

The community/local no-workflow fall-through and the inline-`COMPLETED` path
run the **same** state machine, but with the re-addressed request principal
instead of a minted one. On that path no real ticket exists, so the block's
`puid` and the callback's `puid` are both `None` and the equality check holds
trivially; real antprocess tickets always carry a non-empty puid on both sides.

### Corroboration contract

`query_approval_status` currently promises only an arbitrary `dict`
(`plugin_api/approval_workflow.py:60-67`) and the public query response model
carries no decision field (`antprocess/schemas.py:30-37`). The security gate
above cannot rest on that. The Plugin API is evolved to a typed result:

```python
{
    "success": True,          # query itself succeeded
    "puid": "<global_unique_id>",
    "state": "COMPLETED",     # platform state, echoed
    "decision": "AGREE",      # AGREE | DISAGREE | CANCEL | None
}
```

with defined semantics: the authoritative field is `decision`; `decision is
None` means no decision has been recorded yet and the callback is rejected; a
`decision` that differs from the callback's claim is rejected and logged at
error level; transport failure or `success=False` is reported so antprocess
retries; a `puid` mismatch between query and callback is rejected. Per
`docs/arch/protocol-contract-tests.md`, this Plugin API behavior change ships
with updated contract documentation and conformance tests, not only a unit
test.

### Orphan prevention

The pending block is written **before** the approval ticket is created is not
possible (the ticket puid is not known earlier), so the order stays
ticket-first and the write stops being best-effort:

- If persisting the pending block fails, the Backend cancels the ticket
  (best effort) and **propagates the failure** to the caller. Today's
  warning-only swallow contradicts the repository rule that persistence write
  failures are never silently returned as success.
- If the inline apply fails after a successful persist (community path), the
  failure is propagated; the block remains `PROCESSING` and a republish
  rewrites it cleanly.
- A ticket whose block cannot be written is therefore cancelled rather than
  left to be permanently rejected by the state machine. Durable outbox or
  reservation-first designs were considered and rejected as heavier than the
  failure mode justifies.

### Concurrency

`friend_ext` is replaced wholesale and the V1 PATCH has no precondition
(revision/ETag/CAS), so the design does **not** claim atomic
first-decision-wins:

- Conflicting decisions for one ticket are not possible from antprocess (one
  decision per ticket) and, for forged or duplicated callbacks, corroboration
  makes the applied decision always the platform-confirmed one. Same-decision
  replays are idempotent.
- Cross-scope or cross-flow writers can still race: two publishes (user and
  agent) on one bot, or a callback racing a publish, both read `friend_ext`,
  merge, and write, losing one another's blocks. To make this self-healing,
  every `friend_ext` write is followed by a **verify-and-rewrite loop**: GET
  the bot again, confirm the intended block (and status) survived, and if it
  did not, re-merge onto the latest value and retry with backoff; on
  exhaustion the write is treated as failed, which routes into the orphan
  prevention above.
- Upgrade path if hard atomicity is ever required: a revision precondition on
  the V1 PATCH (the event-subscription surface already uses optimistic
  revisions, so the pattern exists) or a dedicated atomic decision operation.
  Both are BCS contract changes and are deliberately out of scope.

## Data flow

Synchronous publish (BCS-native bot):

```
POST .../bots/bot_x/public?user_id=U        (gateway principal, aud=backend)
→ Human-only admission (REFUSED for app-only)
→ ac_bots miss
→ resign(token→aud=bcs) → GET v1 /bots/bot_x
     404 · kind≠bot · created_by≠U → 404 (not found)
     else → synthesize bot dict
→ start approval ticket
→ persist: resign → GET v1 → merge PROCESSING block → PATCH v1
     failure → cancel ticket, propagate error
     verify-and-rewrite until the block survives
→ community: inline COMPLETED → callback state machine (resigned principal)
```

Corp callback:

```
POST /api/v1/antprocess/callback            (form; no user principal)
→ query_approval_status(puid) corroborates the decision — else reject
→ mint(owner_id) → GET v1 /bots/bot_uid
→ state machine: block exists ∧ PROCESSING ∧ puid match ∧ not terminal
→ merge → PATCH v1 {friend_ext, [visibility field]} → verify-and-rewrite
     (BCS backstop: created_by == minted owner, else 403 logged)
→ AGREE ∧ user → BCSFuse worker sync (unchanged)
```

## Error semantics

| Condition | Result |
| --- | --- |
| App-only caller reaches the route | 401 at entry (Backend `REFUSED`); no ticket created |
| Bot absent in both `ac_bots` and BCS | 404 `404000 "Not found"` (unchanged) |
| BCS row is a Human row, or `created_by` ≠ caller | 404 (existence non-disclosure) |
| Corroboration: no decision yet | Decision not applied; callback reports failure so antprocess retries |
| Corroboration: decision differs from the claim | Decision not applied; error logged; failure reported |
| Callback puid ≠ pending block puid | Decision not applied; error logged |
| Block not `PROCESSING` / already terminal | Idempotent success on the same decision; conflicting decision rejected and logged |
| Pending-block persist failure | Ticket cancelled (best effort); error propagated to the caller |
| V1 PATCH 403 (BCS ownership backstop) | Decision not applied; error logged with request id |
| Verify-and-rewrite exhausted | Write treated as failed; routed through orphan prevention |

## Security analysis

- The mint is the single place the Backend can create a user credential. Its
  blast radius is bounded by three independent gates: antprocess
  corroboration, the pending-block state machine (a valid `PROCESSING` ticket
  for that exact bot and puid must already exist — a ticket that itself
  required the owner's real principal at publish time), and the BCS
  `created_by` check.
- A minted token lives 60 seconds and authorizes exactly the Human it names;
  it carries `iss=<backend>`, so BCS audit logs attribute these writes to a
  backend-issued credential rather than to gateway-authenticated user traffic.
- Human-only admission removes the class of caller for whom re-addressing can
  never produce an admissible credential, and it closes the Backend/edge
  disagreement the two-hop agreement exists to prevent.
- There is no CAS on the block writes. The mitigating mechanism is
  corroboration (for decision correctness) plus verify-and-rewrite (for block
  durability); the residual is a bounded window in which one writer's block
  can be temporarily absent, not a silently lost decision.
- Reusing `update_bot` instead of adding a dedicated BCS decision endpoint is
  a deliberate call: under the current principal contract a minted token could
  invoke any user-scoped route anyway, so a dedicated endpoint would add no
  containment. Its only real advantage would be atomic check-and-set, which
  the upgrade path above preserves as a future option.
- Known residual: the callback route itself remains network-reachable only
  over the intranet path antprocess uses; adding route-level authentication
  for antprocess is tracked as a follow-up, not silently absorbed here.

## Compatibility and rollout

- The `404000` wire contract for unknown bots is unchanged.
- **Human-only admission** is a behavior change for any non-gateway caller
  that previously reached this route with an app credential. Through the
  gateway such callers are already refused (`user: required`); the Backend
  change makes the refusal uniform and earlier. The Backend route-inventory
  test and the gateway `route_security` test are the two pins that must move
  together.
- `visibility=private` direct-apply now enforces ownership through BCS
  `created_by` instead of the unenforced provider binding — same success
  response shape, stricter admission.
- **Community/local behavior change**: publication writes become real V1
  mutations against the local BCS instead of the previous `{"skipped": true}`
  no-ops from the provider-admin channel. This makes the community flow a full
  closed loop and is what singlebox exercises end to end.
- **Singlebox signing key**: `singlebox_coverage.sh:12` arms the Backend with
  `singlebox-gateway-principal-key-not-for-production` while gateway and BCS
  default to `avernet-dev-signing-key-NOT-FOR-PROD`
  (`scripts/modules/gateway.sh:55`, `scripts/modules/bcs.sh:908`). The
  mismatch is invisible today because local publication skipped BCS writes; a
  Backend-signed token would be rejected with 401. Singlebox must inject one
  value into `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE` and
  `AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE`, and the coverage run must
  include a real Backend→BCS verification round-trip.
- Deployment check (no code): the BCS Gateway-Principal verifier `issuers`
  must include the Backend issuer. The friend-approval re-addressing flow
  already depends on the same configuration, so production satisfies it;
  verification is a rollout checklist item.
- Rollback: revert restores the provider-admin path and the `OPEN` admission
  entry; no data migration because the `friend_ext` block shape is untouched.

## Testing

- Read fallback: BCS 404; `kind=human`; `created_by` missing or mismatched;
  synthesis success — the last two both answer 404.
- Admission: an app-only caller is refused at entry and creates no ticket.
- Minter: claim shape (`principals`, `subject.id`/`username`, `iat`/`exp`
  integers, `kid`, `typ`) and that a minted token verifies against the real
  BCS verifier, not a mock.
- Corroboration: no decision yet; decision differs from the claim; unknown
  `last_operate`; query transport failure; puid mismatch between query and
  callback.
- State machine: idempotent replay; terminal block with the same and a
  conflicting decision; missing block; non-`PROCESSING` block.
- Durability: pending-block persist failure cancels the ticket and surfaces an
  error; verify-and-rewrite recovers a clobbered block; two scopes updating
  `friend_ext` concurrently; two opposing callbacks arriving concurrently.
- Existing anchors keep passing: `test_openapi_public_bcs.py` (404 contract),
  `test_bcs_publication_sync.py`, `test_bot_public_service.py`.
- Plugin API contract: conformance tests for the evolved
  `query_approval_status` result.
- Singlebox: one signing key across Backend, Gateway and BCS; a real
  Backend→BCS verification round-trip; a BCS-native bot completing publish →
  inline COMPLETED → visibility flip → the pending-approval block visible to
  the frontend reader (`friendApprovalAttributes.ts`).
