# PR #2568 review fix wave (6 findings) — report

Branch: `feat/bot-owner-manager-permissions-squash`. Per the controller's
mid-task instruction, the original feature squash commit **65f0ff5c0d is
left untouched**; this wave is a **new separate commit on top** (no amend,
no push). Spec:
`apps/bcs/docs/superpowers/specs/2026-09-18-bot-manage-permission-design.md`.

RED-first evidence was captured for every finding by path-scoped `git stash`
of only that fix's source files, running the new suite against the pre-fix
tree (RED), then restoring (GREEN).

---

## F1 [P1] — leave/status/visibility authorize current control, not `created_by`

**Files**
- `apps/bcs/crates/services/bcs-bot/src/application/bot.rs`
- `apps/bcs/crates/services/bcs-bot/src/application/runtime.rs`
- `apps/bcs/crates/bootstrap/bcs/src/server.rs` (wiring at all four `Bot::new_with_friend` sites)
- `apps/bcs/crates/services/bcs-bot/tests/bot_use_cases.rs`

**Root cause confirmed.** `leave_bot` (~line 573) authorized via
`authorize_human_creator_required` (the `created_by == staff_no` creator
fact), so a former creator without any live role could delete the Bot while
the CURRENT owner got `Forbidden`. The sweep also found the STATUS and
VISIBILITY lanes (`update_status` at line ~508, `set_visibility` at ~541)
and the runtime status lane (`runtime.rs` `update_runtime_status`) all
routing through `authorize_bot_management`, whose only non-self allow was
the same legacy `created_by` comparison.

**Fix.** `Bot` gained `authority: Option<Arc<dyn BotAuthorityHook>>` +
`with_authority(...)`. New fail-closed helpers mirror the Task-9
PATCH/candidate cutover's shape:
- bot-self lane unchanged (caller == bot.bot_uuid → allow);
- human callers resolve `authority.can_manage(staff_no, bot_id)` live —
  `true` allows, `false` → `Forbidden("caller 'human_<id>' holds no current
  owner/manager role for bot '<bot>'")`, unwired hook → fail-closed
  `Forbidden`, typed authority errors propagate as
  `BotUseCaseError::Service(..)` (uninit/corrupt never flatten to a deny);
- non-human non-self callers: Forbidden.
`leave_bot` keeps the TC-bot / provider-managed business conditions FIRST
(they keep their pinned messages), then requires the live can_manage; both
legacy helpers were deleted so no caller can regress to the creator fact.
Bootstrap wires `with_authority(authority_management.hook)` at all four
construction scopes (`build_use_case_bundle`, both memory servers, the
durable-path runtime service).

**Spec cite (deletion level).** Spec §1.3 (line 42): "manager 能否执行破坏性
业务操作？| 与 owner 在同 Bot、同资源角色下平权，仍受删除等业务条件限制" and
§9/§10.2 (line 554): "若对应删除规则允许 manager 删除，它仍可能合法删除,
不得误报为 ownership 漏洞。" I found **no clause making Bot deletion
owner-only**, so the delete lane runs at the `can_manage` level (owner or
manager), with the TC/provider-managed business conditions retained. §12.2
("创建来源不能继续授权") drives the creator denial.

**RED evidence** (pre-fix tree, tests seen failing):
- `leave_bot_authorizes_current_owner_not_the_former_creator` —
  `the former creator must be denied: Ok(BotLeaveResult { left: true, bot_uuid: "transfer-bot" })`
- `leave_bot_allows_live_manager_to_delete` —
  `Forbidden("User alice is not the creator of bot managed-bot")`
- `status_and_visibility_lanes_authorize_live_roles` —
  `the former creator must not update status: Ok(BotStatusUpdateResult { updated: true, ... })`
All three GREEN post-fix; five legacy dev tests that pinned creator-shaped
behavior were reworked (`leave_bot_allows_owner_soft_delete_for_unmanaged_bot`,
`update_status_rejects_caller_mismatch`, `set_visibility_*×3`), plus
`bcs-http` `route_contract::leave_bot_route_soft_deletes_owner_bot` re-seeded
with a live owner edge and wired through a new
`services_builder_with_bot_use_cases_and_authority` (RepoAuthorityHook).

---

## F2 [P1] — authority SQL identity predicates pinned to binary collation

**Files** — `apps/bcs/crates/services/bcs-edge-permission-store/src/authority/`:
`transfer_query.rs` (new shared `binary_identity(flavor, col)` helper),
`reads.rs`, `audit.rs`, `manager.rs`, `team_sync_sql.rs`,
`transfer_create.rs`, `transfer_decide.rs`, plus the new
`tests/authority_case_identity.rs`.

**Root cause confirmed.** Live MySQL keeps `edge_grants`'s LEGACY
case-insensitive column collation (migration 032 comment: "All subject IDs
compare by exact case-sensitive identity: new tables use COLLATE
utf8mb4_bin; **edge_grants keeps its existing column collation and the edge
store's existing dialect encapsulation** (spec §5.3)" — but the authority
lane used plain `from_id = ?` / `to_id = ?` bindings). On live MySQL,
`USER-A` matches `user-a`'s approved Owner/Manager rows and passes
can_manage / require_owner.

**Fix.** Reused the legacy-friend-lane binary-identity comparison wrapper
(what the older grant queries in edge_grant.rs already do — `CAST(col AS
BINARY)` on MySQL, `col COLLATE BINARY` on SQLite) as the shared
`super::transfer_query::binary_identity` and pinned **every** authority-lane
identity predicate: `reads.rs` (ownership owner-slot, `role`, `roles_for`
pair conjunctions), `audit.rs` `mutation_guards` (now flavor-parameterized),
`manager.rs` (validation aggregate ×6 subqueries, revoke/insert/restore
statements, team sources read, list_managers page + batched source read +
owner exclusion), `team_sync_sql.rs` (aggregate, owner slot, member/revoked
reads, subject guard, lane revoke/restore/insert, audit select, receipt
guard), `transfer_create.rs` (owner slot, cleanup mismatch, pending-insert
guards), `transfer_decide.rs` (snapshot guard incl. `SUBSTR(binary(o.from_id),7)`,
invalidate probe, accept plan, owner revoke/restore/insert, manager grant,
non-team revoke, accepted-receipt guard, decide-validation owner read).
Bound parameters are never wrapped; the `?` counts are unchanged everywhere.

**RED evidence** — new suite
`bcs-edge-permission-store --test authority_case_identity` rebuilds the
migrated `edge_grants` with NOCASE identity columns (the faithful local
replica of live MySQL's collation; SQLite's default comparison is already
binary so the plain production schema cannot express this). On the pre-fix
tree 4/5 failed exactly as the reviewer predicted:
- `role_reads_never_fold_a_case_variant_onto_foreign_rows` —
  `role(user-a) matched a foreign-case owner edge: Some(Owner)`
- `ownership_reads_never_fold_a_case_variant_bot_onto_foreign_rows` —
  `ownership('bot-a')` resolved through the case-variant BOT-A edge:
  `Ok(OwnershipState { owner_user_id: "user-a", ownership_version: 1 })`
- `manager_write_guards_never_fold_a_case_variant_onto_foreign_slots` —
  user-a acted as owner through the case-variant User-A edge:
  `Ok(ManagerMutationResult { changed: true, .. })`
- `revoke_never_touches_a_case_variant_subject_row` — revoking user-b
  folded onto the foreign User-B approved row (`changed=true`).
(`list_managers_keeps_case_variants_distinct` also passes pre-fix — the
Rust-side decode is binary by construction there; it pins the behavior.)
All GREEN post-fix; the full `bcs-edge-permission-store` suite (conformance,
manager mutation, ownership transfer, team sync) is green.

---

## F3 [P1] — WS protected group subscription refuses the session-run downgrade

**Files**
- `apps/bcs/crates/adapters/ws/bcs-ws/src/web/dispatcher/chat.rs`
- `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs`

**Root cause confirmed.** With a PROTECTED Group subscription, a
`chat.send` carrying `session_id` made `run_session_key` = the session id
while the connection's protected binding lives under the GROUP key, so
`channel_binding_of(&run_session_key, conn_id)` missed →
`protected_run_anchor = None` → the run registered on the legacy
`PublicControl` path (sender_human_view even `None`, since the exact
session subscription was absent) → frames sent with no enqueue/pre-send
authorization; a revoked user kept receiving run messages.

**Fix.** Refuse the silent downgrade (chosen semantics per the dispatch's
"fail the message" option — keeps §14 intact: protecting frames requires
a subscription+binding for the exact run scope; messages never deliver
without it). Right after the subscription resolution, the handler walks the
connection's subscribed keys: if any key other than the run key carries a
live protected binding **and** no protected binding exists for the run key,
the send is refused with `protected_subscription_scope_mismatch` ("The
connection's protected subscription does not cover the referenced Session;
subscribe to the Session before sending") — no publish, no run channel, no
`active_run_ids`, connection not closed (subscribe-then-retry works). The
check is general: it also covers the mirror direction (a session-scoped
protected connection sending a group-level chat), which is guarded for the
GREEN lane but not covered by the RED fixture.

**RED evidence** (pre-fix tree):
`protected_group_subscription_refuses_session_scoped_run_downgrade` —
`the session-referenced chat must be refused: Some(Object {"runId":
"run-web-1", "status": "accepted"})` (message flowed, run registered).
GREEN post-fix. The test's second half registers a SESSION-protected
subscription on the same connection: chat.send with session_id now
succeeds, `send_visible_event` delivers for the still-authorized view, and
after `hook.revoke(...)` mid-flight the very same dispatch answers `false`
and the connection receives zero further run frames — the deterministic
grant/revoke pattern mirrors `tests/protected_delivery.rs` (recording
`DeliveryAuthorizationService`, no sleeps). Legacy/unprotected connections
(existing web_frame_compat + group_session_ws fixtures) keep the old
legacy-lane behavior — full `bcs-ws` suite green.

---

## F4 [P1] — V1 Group CRUD selects the principal through the live authority

**Files**
- `apps/bcs/crates/application/v1/bcs-app-group/src/service.rs` (create ~204, update ~406, delete ~518)
- `apps/bcs/crates/application/v1/bcs-app-group/src/authorization.rs` (`load_group_detail_for_caller`, used by `get`)
- `apps/bcs/crates/application/v1/bcs-app-group/src/lib.rs` (import cutover)
- new `apps/bcs/crates/application/v1/bcs-app-group/tests/mixed_identity_authority.rs`
- `apps/bcs/crates/application/v1/bcs-app-group/tests/v1_group_service.rs` (one pinned claim-shaped test reworked)

**Root cause confirmed.** All four remaining `select_principal(…,
IdentityPolicy::HumanOrOwnedBot)` sites trusted the Gateway-signed
`owner_id` claim: a mixed caller with a stale claim (claim predates an
ownership transfer) was rejected even when the User is the CURRENT
owner/manager, while a former creator whose claim still matched was
accepted.

**Fix.** All four sites now call
`resolve_authorized_principal(&command.caller, self.authority.as_ref()).await`
(the Task-11 infrastructure in
`apps/bcs/crates/service-api/bcs-service-api/src/application/v1/authorization.rs`).
`human_can_sponsor`/group authorization stays unchanged — only the
effective-Principal selection was cut over; `GroupServiceImpl` already
held `authority: Arc<dyn BotAuthorityHook>` and every trait method was
already async, so no trait or bootstrap change was needed (the openapi
group routes carry no identity pre-gate, so no HTTP change either).

**RED evidence** — `tests/mixed_identity_authority.rs` seeds a PUBLIC bot
whose creator is `staff-a` and whose CURRENT owner is `staff-b`
(`created_by` retained, live owner edge transferred). On the pre-fix tree
all 4 tests failed:
- current owner + stale claim:
  `Forbidden("The authenticated Bot is not owned by the authenticated User")`
  (create, and the update/get/delete suite);
- former creator + match claim: `Ok(Collaboration(... originator_actor_id:
  "bot-p" ... ))` — the retired creator created the group through the Bot.
GREEN post-fix: current-owner-with-stale-claim creates/updates/reads/deletes;
former-creator's acting-Bot lane is refused with "may not act as the
authenticated Bot". One legacy test
(`mismatched_human_and_bot_caller_is_rejected_before_provisioning`) pinned
the old `Forbidden` shape; its refusal-before-provisioning intent is kept,
its assertion now documents the fail-closed live-authority branch
(`Forbidden | Internal`), consistent with the shared Task-11 semantics
(also used by bcs-app-session).

---

## F5 [P2] — session-file routes no longer pre-gate with the sync claim check

**Files**
- `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/internal/routes/session_file.rs`
- `apps/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs`

**Root cause confirmed.** Every protected file handler ran
`authorize_identity` → sync `select_principal(HumanOrOwnedBot)` FIRST, so
mixed-identity requests were 403'd by the adapter before the application
layer (`bcs-app-session/src/file.rs` `load_member`, which already resolves
through `resolve_authorized_principal`) could apply the live facts.

**Fix.** Removed the `authorize_identity` helper and all its call sites;
the `RouteIdentityPolicy` extractor params are gone from the handlers.
Policy audit: all ten route-methods declared `IdentityPolicy::HumanOrOwnedBot`
— there was **no Direct/BotOnly policy to keep**; the route annotations now
declare `IdentityPolicy::HumanOrAuthorizedBot`, documenting the actual
runtime contract (the application resolves; the adapter only checks
presence). The semaphore: `ApiState`/auth middleware still requires an
authenticated caller; every denied case still 403s — decided in the app
layer.

**RED evidence** — new `mixed_identity_*` tests in `session_routes.rs` with
a `HookedSessionFileService` (resolve-then-answer over a live-authority
hook, mirroring Task-11 `load_member`). Pre-fix:
`mixed_identity_file_routes_pass_the_stale_claim_to_the_live_authority` —
`the current manager's list passes through despite the stale claim: left: 403, right: 200`.
Post-fix: list/prepare/upload/download for the current manager (with a
stale claim naming the former creator) all get 200/201/202; the
former-creator caller gets 403 decided by the application resolution. The
old pinned test
(`session_file_routes_admit_bot_and_reject_mismatched_or_app_only_callers`)
was reworked into
`session_file_routes_admit_bot_and_reject_unauthorized_mixed_or_app_only_callers`
(bot-only still 200; unauthorized mixed pair, app-only → 403 from the live
resolution).

---

## F6 [P2] — DM lane drops the creator short-circuit

**Files**
- `apps/bcs/crates/services/bcs-group/src/application/management/guards.rs`
- `apps/bcs/crates/services/bcs-group/tests/management.rs`

**Root cause confirmed.** `ensure_human_can_dm_bot` granted via
`target.created_by == staff_no || relation_has_creator_edge(...)` BEFORE
the live authority and friendship arms — after transfer + manager-revoke
the former creator could still open/reuse a DM with the protected Bot.

**Fix.** The creator arm and its `relation_has_creator_edge` helper (now
dead) were removed. Lane order after the fix: public visibility → LIVE
`can_manage` (owner/manager, unchanged) → friendship/relation arms
(unchanged positions, verified). Spec: §12.2 (创建来源不能继续授权 — the
former creator facts are history/audit only); §8.2 gives owners *and*
managers parity for collaboration reachability; **no DM compat exception
exists in the spec** (the word "DM" does not appear in it), so no display
role was retained in the permission path.

**RED evidence** (pre-fix tree):
`create_dm_denies_the_former_creator_without_friendship` got a full
`DmCreateResult { … group_id: bcs_grp_dm_…, created: true }` for the
former creator; post-fix both the create and the reuse probe get
`GroupUseCaseError::Forbidden`. The counterpart test
`create_dm_keeps_owner_manager_and_friend_lanes_open` pins the live
manager arm and the friendship arm still granting. No dev-side test pinned
the creator-DM behavior anywhere else (full `bcs-group` suite green
unmodified otherwise).

---

## Verification (tiered; disk before each step, /shared held 11–12G free throughout)

1. `cargo check --workspace --all-targets` — **clean** (no errors;
   no new warnings from this wave's files).
2. New RED→GREEN suites: all six findings demonstrated RED on the
   pre-fix tree (excerpts above) and GREEN post-fix.
3. Touched-crate regressions (all green, 0 failures):
   - `-p bcs-bot` (19 binaries) + `-p bcs-bot-store` + `-p bcs-edge-permission`
     + `-p bcs-edge-permission-store` (incl. new authority_case_identity: 42
     passed; conformance/manager/transfer/team-sync suites green)
   - `-p bcs-group` / `-p bcs-group-store` (93 + others, incl. 2 new DM
     tests) / `-p bcs-app-group` (89 + 4 new mixed-identity tests; full
     5 test binaries green)
   - `-p bcs-ws` (13 binaries incl. protected_delivery, web_frame_compat
     with the new F3 test)
   - `-p bcs-api-http` (39 binaries, incl. session_routes with the two new
     F5 tests) + `-p bcs-http` (39 binaries incl. current_authority
     legacy-cutover suite and the reworked route_contract delete test)
   - integration/e2e: `-p bcs --tests` — **719 passed; 0 failed; 32
     ignored** (the `#[ignore]` live-MySQL suites) across 65 test binaries,
     incl. bootstrap wiring, integration_discover_fuse, ownership
     migration, protected-delivery e2e.
4. Pre-existing failures/known flake: none observed in this wave. The
   `bcs-collaboration-store` sqlite ×2 and clippy deny-lint wall were not
   re-run (out-of-scope, verified pre-existing on the clean base per the
   dispatch); the ownership_deletion race flake did not appear.
5. MySQL live lanes: the 9 `#[ignore](BCS_TEST_MYSQL_URL)` suites remain
   unverified locally (as in the original commit); the F2 pin is mirrored
   by the SQLite NOCASE-rebuild harness, which reproduces the exact
   case-folding semantics of the live table collation.

## Concerns / notes for the reviewer

- **F1 / uninitialized legacy bots:** bots whose ownership was never
  initialized now fail closed on delete/status/visibility-with-human-callers
  with the typed `ownership_not_initialized` branch (spec §12.4) instead of
  a creator-fact decision. New registrations initialize ownership; the
  cutover runbook (`docs/runbooks/bot-authority-cutover.md`) +
  `bcs-ownership-migrate` govern pre-cutover rows. One legacy bcs-http
  contract test needed its fixture upgraded to a live owner edge (documented
  above).
- **F2 index note:** the binary identity comparison (CAST/COLLATE) bypasses
  the plain `edge_grants` index for these control-plane predicates, mirrored
  from the legacy friend-lane queries; per-op statement counts are unchanged
  and all budget assertions still pass.
- **F2 duplicate-key behavior on live MySQL:** a case-variant identity pair
  cannot physically coexist in the direct/manual slot (the legacy unique key
  folds case), so after this fix a grant of a case-folding duplicate now
  surfaces a duplicate-key storage error (fail-closed) rather than silently
  treating the foreign row as the subject's slot. Not reachable through the
  production identity chains (ids come from one casing authority); flagged
  for the runbook.
- **F3 semantics:** the chosen "refuse the send" keeps zero-silent-downgrade;
  it is deliberately strict — a protected client that referenced an
  un-subscribed session before this wave silently received unguarded frames,
  and will now get an explicit recoverable error instead.
- `bcs_bots.bot_uuid` comparisons in the authority lane keep the table's
  collation (out of the cited finding scope: `from_id`/`to_id` on
  `edge_grants` only); flagged for a follow-up sweep if desired.

## Commit

New commit on top of the untouched feature squash:

```
a095a0f8dd fix(bcs): close authority review findings on creator grants and delivery authz
```

(24 files changed, +2155/−285; 65f0ff5c0d remains pristine in history;
never pushed.)
