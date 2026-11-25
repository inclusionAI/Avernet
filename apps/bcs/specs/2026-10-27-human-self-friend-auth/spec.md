# Legacy friend routes: Human actor-self authorization

## Scope and authority

BCS HTTP adapter owns this regression fix. The approved requirement is to let
an authenticated Human access their own `human_<staff_no>` actor through legacy
friend routes without granting access to other Humans or weakening Bot control.
The existing caller-self behavior in `routes/friend_connections.rs` is the
reference. Architecture rules remain in `docs/arch/arch.rules.md`; no ADR,
Service API, Plugin API, wire schema, configuration or migration changes apply.

## Design

After extracting a nonempty verified staff number and loading the requested
actor, distinguish its persisted `actor_kind`. For a Human, require the exact
canonical actor ID `human_<staff_no>`; reject all other Humans independently of
Bot authority. For a Bot, preserve the live `bot_authority_hook.can_manage`
check and propagation of authority failures as HTTP 500. Keep token precedence,
missing-identity errors and missing-actor errors unchanged.

Use the shared `check_actor_ownership` function so list/create requests,
accept/reject requests and list-friends all preserve this distinction. Do not
restore `created_by` authorization or create synthetic Human ownership edges.

## Validation and access cost

Add routed HTTP tests covering the reported received-inbox request, all shared
legacy entrypoints, other-Human denial, no identity, missing actors, Bot IDs
resembling Human IDs, live Bot owner/manager revocation, and authority failures.
Assert application caller IDs and that rejected requests never reach the
application service. A failing authority hook must not affect Human self access.

The existing actor lookup and its cache/freshness behavior are retained:
`PersistentBotRepo::try_get` uses the process map when warm and loads one actor
from the DB when cold. Human requests skip the former scalar authority call:
`DbBotAuthorityStore::role` previously queried `bot_ownership_state` once and
found no row because it requires `actor_kind = 'bot'`; the memory authority
implementation rejects the non-Bot without DB access. Thus the DB-backed Human
path removes one bounded SELECT per request (including denials), and the memory
path removes no DB queries. Bot lookups, authority queries and downstream
FriendService work are unchanged. No new scans, writes, caches, retries,
transactions or concurrent work are introduced. No load benchmark was run.

Run the focused new suite, the existing `authority_cutover` suite, and the
`bcs-http` package tests. Record actual results in the final change report;
production-environment verification is not available locally.


## Validation evidence (2026-10-27)

- Before the fix, the new routed suite reproduced HTTP 403 on the exact
  `human_327325` received-inbox request, plus HTTP 500 when the unrelated Bot
  authority store was unavailable: 5 passed, 3 failed as expected.
- Existing `authority_cutover` baseline: 9 passed.
- After the fix, `cargo test --locked --offline --manifest-path apps/bcs/Cargo.toml
  -p bcs-http`: 587 passed, 0 failed across 45 test targets including
  doc-tests. Includes 9 new authorization tests and the 9 existing authority
  cutover tests. The new cases also verify exact (not prefix) identity matching.
- `git diff --check`: passed. Source sizes: `friends.rs` 571 lines, new
  `friends_actor_auth_contract.rs` 405 lines (both below 1,000).
- `cargo clippy --locked --offline --manifest-path apps/bcs/Cargo.toml -p bcs-http
  --test friends_actor_auth_contract --no-deps`: blocked by 13 existing denied
  `unwrap_used`/`expect_used` findings in unchanged library files, including
  `gateway_trace.rs`, `oauth/mod.rs`, `routes/admin_invocations.rs`,
  `routes/collaboration_drafts.rs`, `routes/groups/projections.rs`,
  `routes/sessions.rs`, and `state.rs`. These files match HEAD; lint policy was
  not weakened and unrelated code was not edited.
- No live deployment test, workspace-wide Cargo tests, or Singlebox E2E run.
  This is a focused HTTP-adapter fix with no schema or cross-module contract
  changes. Changes are tracked on `fix/bcs-human-self-friend-auth`; the requested PR targets `dev`.
