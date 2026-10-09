# CI Fix Round 1 — PR 2568 (feat/bot-owner-manager-permissions-squash)

Branch: `feat/bot-owner-manager-permissions-squash` (single commit `1b8bc8a5d3`, amended in place).
Covers: Unit Tests run (MySQL chain pin) + BCS e2e (coverage gated) + Singlebox coverage (same 17-failure pattern), plus the E2E coverage-gate symptom (38.98% < 40%).

## Cluster A — `authenticated human lists owned bots through the public API` 500 / `v1 mine returned 500`

**Root cause (single, drives Clusters A+B together):** the legacy owner-delete lane
`DELETE /bots/{id}` → `BotManagementService::leave_bot` (crates/services/bcs-bot/src/application/bot.rs)
called the plain `registry.soft_delete()`, which tombstones the bot row but leaves its
approved owner/manager edges in `edge_grants`. The strict Task 3 read contract
(`PersistentBotRepo::list_controllable_impl` / memory `controllable_union`) fails the WHOLE
union on a dangling role edge ("role edge targets a missing or soft-deleted Bot", typed
`CorruptAuthority`). Story order made this deterministic: `story_user_prepares_agent_network`
registers + `DELETE /bots/{id}`-deletes a temp agent at 12:55:00, BEFORE the legacy `/bots/my`
read at 12:54:59 (green, total=6 owner labels), so every later mine read on that server 500s
(v1 public mine), and every fail-closed lane resolving through
`BotQueryService::list_my_bots` (bcs-http routes/sessions.rs `current_controllable_bot_ids`)
silently fails to an empty union → the Cluster B 403s.

**Callers/identities:** the story's human is mock user `001` (X-Mock-User-Id everywhere;
the v1 lane signs the same subject into a Gateway-Principal JWT). The 5 OpenClaw bots were
onboarded with that identity (owner edges: `created_by` binding + v1 asset on registration),
so a healthy union contains all 5 bots + the `human_001` self row — the CI log's
"/bots/my total=6, owner/manager labels" at 12:54:59 proves both halves.

**Fix (core spec intent preserved — "注册/生命周期/...全部单事务原子提交"):** route
`leave_bot`'s delete through the EXISTING governed single-transaction retirement boundary
`BotRepo::retire_bot_lifecycle` (crates/services/bcs-bot-store/src/ownership_deletion.rs: soft
delete + withdraw every approved owner/manager edge + invalidate PENDING transfers +
`delete/bot/applied` audit in ONE commit), exposed at the core level:
- `bcs-service-api/src/core/registry.rs`: new `BotRegistryCoreService::retire_bot_lifecycle`
  (default fail-closed Err "atomic bot authority retirement is not configured").
- `services/bcs-bot/src/core/bot_core.rs`: delegation to the repo port.
- `services/bcs-bot/src/application/bot.rs` `leave_bot`: builds a
  `BotOperationContext{actor: Human{user_id, effective_actor_id: human_<staff>}}` and calls
  the retirement instead of `soft_delete`. All existing guards (creator check, TC-suffix,
  provider-managed) unchanged. `Ok(false)` (missing/already-retired/Human row) maps to
  `left=false` (Human rows have their own guarded deletion lane). Dialect-independent: both
  store twins (memory critical section / SQL transaction) already implement the boundary;
  the Provider tombstone lanes were already on it (retirement_withdrawal_steps).

**RED-before-fix evidence:**
1. New unit test `leave_bot_retires_authority_edges_so_mine_stays_strict_green`
   (apps/bcs/crates/services/bcs-bot/tests/bot_use_cases.rs), memory twin, drove the real
   trusted-ownership claim (`initialize_existing_ownership`) ×2, legacy-deleted one agent,
   then required the survivor to stay in `list_my_bots`:
   RED: `the strict mine union must still answer after the delete:
        Service(Authority(CorruptAuthority { bot_id: "leave-strict-a", env: "dev",
        detail: "role edge targets a missing or soft-deleted Bot" }))` → GREEN after fix.
2. New durable end-to-end regression
   `legacy_owner_delete_keeps_the_durable_mine_strict_read_green`
   (apps/bcs/crates/bootstrap/bcs/tests/bot_authority_wiring.rs) boots the REAL durable
   SQLite server (config_dir sqlite path + mock auth + Gateway-Principal key), registers via
   the mocked trusted lane, legacy-`DELETE`s the agent, then asserts BOTH mine entrypoints
   still answer. Run with the lane change stashed for RED:
   `v1 mine must answer for a verified principal: 500 Internal Server Error` — the exact CI
   failure; GREEN with the fix.

## Cluster B — 403s only on the final-review cutover lanes

All six failing lanes resolve the human caller through the live mine union and therefore
fail closed to a 403 while it errors on the dangling edge (verified by local reproduction on
the durable SQLite server; every lane GREEN after the single Cluster A fix, without touching
the guards):

| Lane | Resolution path | Already-deleted-agent poisoning |
|---|---|---|
| `POST /groups/{id}/members` | routes/groups/translation.rs `resolve_group_member_caller` → `current_controllable_bot_ids` | empty union → "human caller does not control a coordinator bot" 403; also `authorize_human_owner`→`human_can_manage_bot` would fail |
| `PUT /groups/{id}/label` / `visibility` | same resolver; operations.rs guards via `human_presently_controls_bot` | 403 |
| `POST /groups/{id}/invite-link` | services/bcs-group invite.rs via `BotQueryService::list_my_bots` | 403 |
| `GET /groups/{gid}/sessions?...&collected=true` (mock human) | routes/sessions.rs `resolve_collector_bot()` Human arm → `current_controllable_bot_ids` | "caller does not own bot" 403 (bot-token collect/uncollect lanes stayed 200 in CI because `GroupChatCaller::Bot` resolves without the union) |

**Both-intent preservation:** the guards are NOT relaxed. Legacy story semantics are served
by the identity the stories actually use: the human owner/creator of the driver bots (the
mine union now answers, CEO/PM are `owner`-labeled, so the cutover resolvers find the
coordinator and `human_presently_controls_bot` returns true). New authority semantics stay
fail-closed: no `created_by` fallback, strict typed errors, edges withdrawn only through the
transactional lifecycle.

## Cluster C — `bot_authority.sh` stories "exited without recording an assertion failure"

All five stories were also verified against a locally-booted durable server with the real
routes (script fixes only — no product changes needed):

1. Common harness bug: `skip_case ... || return 77` exits non-zero without recording, so the
   e2e runner counts an honest SKIP as a failure ("exited without recording an assertion
   failure"). Fixed at every skip path in bot_authority.sh: `skip_case` + `TESTS_TOTAL++` +
   `return 0` (same accounting the other suites use in group.sh).
2. Onboard-response parsing: `/bots/onboard` returns `{bot_uuid, onboarded, name...}` at the
   top level (routes/onboard.rs `onboard_result_response`), but every story read
   `data.bot_id`/`bot_id` and the directory fallback only tried `bot_id`/`id`/`uuid` keys
   — while `/bots` items carry `bot_uuid`. New helper `_authority_register_story_bot` parses
   `bot_uuid`/`data.bot_uuid`/`bot_id`/`data.bot_id` and falls back matching
   `bot_uuid|bot_id|id|uuid` by name. This alone caused all three registration stories to
   "SKIP: ...-story-bot not discoverable/registered" in CI.
3. Staff ids with dashes (`authority-manager-002` etc.) are rejected by `/me/ensure-human`
   ("staff_no 格式错误：仅允许 ASCII 字母和数字" → 400) so the manager/transfer grants died
   on "not a live human actor" (400 invalid_subject). Ids changed to ASCII-alnum
   (authoritymgr002 / authorityrecv003 / authoritystranger777 / authorityteammember00N) and
   the team story now materializes the three member users before the credential-gated sync
   (the membership snapshot is fail-closed on non-live humans).
4. `story_ownership_migrate_maintenance_binary_probes`: `bcs-ownership-migrate` is built by the
   coverage `cargo build --workspace` but never exported. e2e_coverage.sh now re-exports
   `BCS_MIGRATE_BIN=$cov_dir/debug/bcs-ownership-migrate` for the e2e.sh child (mirrors the
   existing BCS_CLI_BIN re-export). Verified locally: both usage probes green against the
   built binary.

## MySQL chain pin (bcs-admin)

`migrate_mysql_chain_tests.rs`: the rebase makes the MySQL chain 32 (031 session_registry
from dev + 032 bot_authority ours). Updated pins: fresh chain `applied_versions=32` /
`versions 1..=32`; the v20-prefix resume `applied_versions=11` → `12` (21..=32; the second
no-op pass `applied_versions=0` and history checks unchanged). Audited the rest of the file
and the crate: `migrate_human_input_index_tests.rs:40` already pins `1..=32`; bootstrap
migrations/tests already pin sqlite 33. These are the `#[ignore]`d live-MySQL assertions CI
runs via `cargo test -p bcs-admin full_mysql_migration_chain_applies_and_preserves_history
-- --ignored` (BCS_TEST_MYSQL_URL) — not runnable locally without MySQL; pins arithmetically
consistent with the chain on disk (001..032).

## Also (post-failure diagnostics, CI wrapper)

e2e_coverage.sh: after a failed e2e run, print the last 120 lines of the bcs server log
(`$bcs_log`, the same file the endpoint-coverage pass reads) so CI shows server-side 500/4xx
causes inline. Failure branch only.

## E2E coverage gate 38.98%/<40%

Treated as the symptom of the 17 failures stopping stories early. Not gamed; no threshold
changes. The fixed stories run to completion (canonically: the previously-aborted mine,
add-member, invite, collect stories now exercise their full paths).

## Test matrix (local)

- `cargo check --workspace --all-targets` → clean (0 errors).
- New/changed regression suites: bcs-bot tests 35/35 (incl. the new RED→GREEN test);
  bootstrap `--test bot_authority_wiring` 6/6 (incl. new durable e2e); `--test
  ownership_migration` 11 passed + 1 ignored-MySQL, `--test e2e_bot_authority` 6/6,
  `--test e2e_ownership_transfer` 1/1.
- Full crate suites green: bcs, bcs-http, bcs-api-http, bcs-app-session, bcs-group, bcs-bot,
  bcs-bot-store, bcs-admin, bcs-service-api, bcs-app-register, bcs-app-bot,
  bcs-edge-permission-store, bcs-relation, bcs-relation-store, bcs-session, bcs-session-store,
  bcs-message-flow, bcs-channel (≈290 test binaries, 0 failed).
- Known non-related local failure: bcs-collaboration-store 2 sqlite tests
  (failure_contract::sqlite_failure_action_migration_preserves_legacy_rows_and_replays,
  history_contract::sqlite_history_acceptance_and_terminal_replay_contract) — local-only
  SQLite 3.26 (<3.35) artifact (documented in memory; passes on CI's newer sqlite; the
  commit message already records them as pre-existing on the branch baseline).
- Standalone e2e stories on a REAL durable SQLite server (bcs binary, local config, BCS_AUTH_MOCK,
  signed Gateway-Principal header, built bcs-cli/migrate): drove the register+delete poison
  story then all previously-failing lanes — final run `Passed: 64, Failed: 0`, including:
  add member 200, update label 200, update visibility 200, group invite-link + join 200,
  all 10 赌collect assertions, v1 mine 200 + owner/manager labels, manager grant/revoke story
  (7/7), ownership-transfer story (13/13), maintenance-binary probes (2/2), team-sync story
  4/5 locally — the 5th (member-repair DELETE 409) is a local multi-run idempotency-key
  carryover; a virgin-CI-shaped run with fresh keys verified POST+DELETE both 200.

## Unverified / CI-only items

1. The three live-MySQL `#[ignore]`d assertions I re-pinned (chain=32, prefix-resume=12) —
   unit-tests CI runs them against MySQL 8.4; arithmetically consistent locally, not
   executed locally (no MySQL).
2. The full `bash scripts/e2e_coverage.sh` pass end-to-end (OpenClaw standalone stack +
   instrumented build): not runnable locally in this round; the previously-failing lanes were
   instead reproduced and re-verified against a real bcs server on durable SQLite with the
   same request shapes and identities, and the changed-line coverage gate was not
   re-replicated (the previous CI run already passed it; the new diff is small and mostly
   covered; ~6 residual uncovered lines: the trait-default Err body and the ignored MySQL
   pin assertions).
3. The single CI run ordering nuance to watch: after this fix, `DELETE /bots/{id}` writes a
   `delete/bot/applied` audit row and terminates any PENDING transfer of the deleted bot
   (both are the intended Task-5 boundary semantics, covered by the store-level tests).
4. Residual same-class risk noted but NOT changed (lanes not exercised by CI): the provider
   delete fallback for unbound legacy owner-suffixed bots
   (application/provider.rs, `registry.soft_delete` on the non-binding branch) still takes
   the plain soft delete. The primary provider-tombstone lanes already retire atomically;
   if CI later hits this class there, route them through the same boundary.