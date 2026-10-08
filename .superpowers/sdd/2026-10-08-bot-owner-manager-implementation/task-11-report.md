# Task 11 Report — Session, files, launch and business-audit atomicity

## Status: complete (two focused scopes deferred under Review Notes)

## Commits
- `a46165c588` fix(bcs): make group mutation audit context required at the boundary — Task 10 carry-forward: `GroupMutationCommand.operation` and `CommitGroupEventfulMutation.operation` non-Option, bcs-admin notify-contract migration carries an honest System actor, missing-lock classification ordered before the audit-slot probe.
- (pending second commit) feat(bcs): apply session authority with durable acting identity audit — the full Task 11 change set described below (this commit is being finalized now).

## Implemented

### Two commit classes per spec §12.5 (brief Step 3 model)
- **DB-transaction class** — session auditor `crates/services/bcs-session-store/src/action_audit.rs`
  (new) and session-file auditor `crates/services/bcs-session-file-store/src/action_audit.rs`
  (new) own same-transaction `applied` audit INSERTs for their stores (deterministic
  `session-action:`/`file-action:` audit ids; stable step keys from the controlled
  vocabulary; same-slot content-conflict Fail-closed semantics).
  - Session create / create_with_event: the `create/session/applied` INSERT joins the
    existing INSERT + participants insert + Event plan inside the ONE existing transaction.
  - Collect/uncollect: new signature per brief `collect(&self, session_id, bot_uuid, operation:
    &BotOperationContext)`. The business change is a CONDITIONAL UPDATE
    (collected=0 ↔ collected=1) with `with_transaction_stop_on_no_rows`, so a no-change
    collect ends the transaction before the audit step on EITHER dialect — the
    "幂等无变化不造 applied" invariant holds without reading affected_rows. On change,
    the `collect/session/applied` INSERT joins the same transaction.
  - Eventful lanes (complete/participant add/remove/scope change): the audit INSERT joins
    each lane's existing `[lock, update, event plan]` transaction.
  - File insert/update_handle_and_status/update_status: ONE transaction carries the
    metadata mutation and the `update/session_file/applied` or `create/session_file/applied`
    row; no-change updates stop before the audit step; final `delete()` = metadata DELETE +
    `delete/session_file/completed` in one transaction, missing row = idempotent no-op audit-free.
  - `record_operation_phase(audit)` on the file port: probe-then-insert with
    same-slot idempotency (identical byte content = no-op; different content = Conflict).
  - Memory twins mirror everything stage-first (audit staging precedes any business
    mutation, publish in the same critical section, `arm_action_audit_write_failure`
    and `*_audit_records()` test levers), matching the Task 10 memory-group pattern.
- **External-effect class** — `crates/services/bcs-session-file/src/service.rs`:
  `delete_file` persists `delete/session_file/admitted` BEFORE the backend call; failure
  to write admitted returns an error with ZERO storage calls; on backend success the
  final metadata delete + completed commit together, a failure there retains the row,
  keeps the admitted row and returns the error (no false completion, no claim of
  external rollback); explicit backend errors persist `failed` (reason_code, best-effort).
  `share_mint`/`share_mint_for_history` persist admitted before minting and completed
  after (no metadata change → standalone records). `sweep_expired_pending` records
  honest System phases taking no human name, and `delete_all_for_session` brackets
  per-row admitted/completed with per-resource sub-operations.

### Required-context propagation
- All commands from the brief's list carry the REQUIRED `BotOperationContext`
  (no defaults-from-`created_by`, no Option): `NewSessionParams`, `NewSessionFileParams`,
  `CreateSessionWithEvent`(∃), `AddSessionParticipantWithEvent`,
  `RemoveSessionParticipantWithEvent`, `UpdateSessionParticipantMessageViewScopeWithEvent`,
  `CompleteSessionWithEvent`, `PrepareUploadCommand`, `DeleteFileCommand`,
  `ShareMintCommand`; SessionManagementService and SessionRepoPort collect/uncollect
  signatures exactly per brief; SessionCore/launch paths include `NewSessionParams.operation`.
  `SessionCaller` carries a per-launch operation in `NewSessionParams` with the launch's
  Human user id and effective actor (= creator), so launch/background recovery keeps the
  original Human identity (system only where truly system — runtime run creation,
  timeout scanner, callback recovery, pending sweep, channel WS/CCG/abort lanes).
- `NewSessionParams::default()` retains an explicit System context documented as
  test/system-seeding only, generating a fresh operation id per call so distinct
  (env, operation_id, step_key) slots never collide across independent seeds.

### Identity/authorization
- New `IdentityPolicy::HumanOrAuthorizedBot` + `resolve_authorized_principal(caller, &dyn
  BotAuthorityHook)` in `crates/service-api/bcs-service-api/src/application/v1/authorization.rs`;
  `select_principal` rejects the new policy explicitly (no synchronous bypass).
- The V1 session facade (split into `authorization.rs`/`queries.rs`/`mutations.rs`/`tests.rs`
  in `bcs-app-session`) resolves Human↔Bot view-actor, launch-caller, collect-participant,
  detail-access, ensure_upload_mutation and ensure_collaboration_eligible through live
  `BotAuthorityHook` facts, never `created_by`/`signed owner_id`. The former synchronous
  principal selection in `dto/session.rs` migrated to the facade, `CreateSession.caller`
  is now `AuthenticatedCaller`.
- Launch (`bcs-session/src/launch.rs`) authorizes Human acting as an exact Bot through
  the authority hook.
- Legacy `bcs-http` routes carry honest caller-derived contexts
  (`caller_operation_context`/`legacy_lane_operation_context`/`legacy_collect_operation`),
  group unchanged.

## Tests (evidence)

### Focused Task 11 suites (Step 2) — exact commands and outputs
All commands below run as
`cargo test --manifest-path apps/bcs/Cargo.toml <args>` from the workspace root;
each `test result:` line is that command's actual output.

- `… -p bcs-app-session --test owner_manager_parity`
  → `test result: ok. 2 passed; 0 failed` (1 at original task close; the second test
  was added by the review fix below). Covers: all six Step-1 parity assertions —
  `owner_message_ids == manager_message_ids` (real facade output through the message
  query double), managed-bot-not-in-session collect error, unaffiliated-Human detail
  error, stale signed owner_id + current manager OK vs no-current-role error via
  `resolve_authorized_principal` on live hook facts, other-Human file delete denied +
  owner deletes, manager-driven collect audit rows carrying both identities — plus the
  review-added mixed-identity launch audit test.
- `… -p bcs-session-store --test session_action_audit`
  → `test result: ok. 42 passed; 0 failed`. Of these 42, SIX are the authored audit
  tests (`sqlite_collect_commits_the_applied_audit_with_both_human_identities`,
  `sqlite_same_context_retry_keeps_one_row_and_no_change_writes_nothing`,
  `sqlite_bot_only_operation_records_null_operator_user_id`,
  `sqlite_audit_insert_failure_rolls_the_collect_back_with_zero_residue`,
  `sqlite_create_commits_its_applied_audit_row_in_the_same_transaction`,
  `memory_publishes_collect_state_and_audit_together_or_not_at_all`); the other 36
  are `bootstrap_migrations::*` bring-along tests compiled into the same binary through
  the shared `#[path = "../../../bootstrap/bcs/src/migrations.rs"]` include — the same
  harness pattern the Task 10 group audit suite uses. The audit tests assert through
  real SQLite rows of `bcs_bot_action_audits` (raw SQL SELECT, strict decode), real
  failure injection replacing the audit INSERT in the same transaction, and the memory
  twin's stage-first all-or-nothing critical section.
  (The original report mis-summarized this binary as "42/42 real-SQL audit tests";
  the figure was the binary total, not the authored-audit-test count.)
- `… -p bcs-session-file-store --test file_action_audit`
  → `test result: ok. 42 passed; 0 failed`. Same composition: SIX authored audit
  tests (`sqlite_insert_commits_its_applied_audit_in_the_same_transaction`,
  `sqlite_insert_audit_failure_rolls_metadata_back_with_zero_residue`,
  `sqlite_status_change_commits_applied_and_no_change_writes_nothing`,
  `sqlite_delete_commits_completed_in_one_tx_and_retains_on_failure`,
  `sqlite_record_operation_phase_replays_and_conflicts`,
  `memory_publishes_metadata_and_audit_together_or_not_at_all`) plus the same 36
  `bootstrap_migrations::*` bring-along tests through the migrations include.
- `… -p bcs-session-file --test file_action_audit_lifecycle`
  → `test result: ok. 4 passed; 0 failed` — all four authored
  (`admitted_failure_refuses_to_start_the_external_side_effect`,
  `backend_success_then_metadata_audit_failure_retains_row_and_admits_only`,
  `share_mint_writes_admitted_then_completed_standalone_rows`,
  `pending_sweep_records_honest_system_rows_never_a_forged_human`); this binary has no
  migrations include.

### Regression (Step 4) — whole-crate commands
`cargo test -p <crate>` runs are whole-crate (all binaries), listed per crate:

- `cargo test -p bcs-app-session` — every binary green. Per-binary results:
  lib 8, `group_session_connection` 13, `owner_manager_parity` 2, `session_file_facade`
  7, `v1_session_service` 79.
- `cargo test -p bcs-session` — lib 0 (no inline tests), `conformance_session_services`
  11, `runtime_cleanup_wrapper` 2, `session_activation` 2, `session_launch` 16.
- `cargo test -p bcs-session-file` — lib 60, `file_action_audit_lifecycle` 4.
- `cargo test -p bcs-session-file-store` — lib 11, `conformance` 3, `file_action_audit` 42.
- `cargo test -p bcs-session-store` — lib 15, `conformance_session_repo` 60,
  `session_action_audit` 42.
- `cargo test -p bcs-http` — all 38 binaries green.
- `cargo test -p bcs-api-http -p bcs-group -p bcs-collaboration-runtime -p bcs-message-flow`
  — green (the two `bcs-collaboration-store` failures are the pre-existing ledger-listed
  env problems, verified at HEAD before this plan).
- Group store/group/app-group suites — green after the carry-forward commit
  (`cargo test -p bcs-group-store -p bcs-group -p bcs-app-group`: 336 passed total).

## Notes / TDD honesty
- Fixed step-key maps (SQL: `collect/session/applied` on collect AND follow-up; create
  for membership add; delete for remove; update for mode/scope/completions) are documented
  in-store headers; the Task 1 note "每操作一个逻辑步骤或不同 operation_id" is respected by
  unique operation ids from the launch/application (fresh uuid each command) and per-resource
  sub-operations for retry loops (sweep cleanup). If we ever allow deliveries/finish etc.
  to record in one command, the op id would require "1 operation per distinct step".
- The collect precondition check (a participant absent ⇒ strict `SessionNotFound`)
  compares against the pre-existing flow; the write-side guarantees (conditional update +
  stop-on-no-rows + same-tx audit INSERT) hold identically for the SQLite and MySQL
  flavors, and no-op affected-row differences between dialects are never read.
- Legacy `bcs-http` facade route `/groups/{id}/sessions` etc. obtains audit contexts for
  `SessionCommandService` from `resolve_collector_bot` (Human caller identity preserved
  with the collector as effective actor).

## Self-review findings
- `dto/session.rs` synchronous principal selection migrated; resources with the facade's
  authorized principal only. No new HTTP `operation_id` client fields anywhere.
- No `cargo fmt` executed; UTF-8 truncation remains byte-safe in touched surfaces we
  found (`session_files.rs` and session routes avoid byte-slicing strings we touched).
- MySQL ansi/actual live verification remains env-gated as pre-existing everywhere else
  in the plan (no BCS_TEST_MYSQL_URL server here): NIGHT-GATE-locked: MySQL dialects
  share the exact same SQL statements executed through the same code paths (only
  `self.flavor.now()` timestamps differ), but no live MySQL claims.

## Review notes (honest limits)
1. **RED-list scope**: the SQL dialect requirement "验证于两种方言" is unavoidably limited —
   per the pre-existing ledger constraint ("MySQL live 未验证 (no server)" from Task 2
   onward), the no-op behaviors execute identical SQL on both dialect flavors locally on
   SQLite, with MySQL-shaped SQL routed through exactly the same code paths. The
   plan-consistent MySQL verification remains an env-tier gate as with all earlier
   tasks; the progress ledger should keep the "MySQL unverified statement" caveat.
2. **Deferred: bcs-admin migrate audit** — the group lane audit context is complete per
   plan (System actor, honest) but the bcs-admin binary has no server-audit surfacing
   for the manual notify-migration (tested only as a binary compile-level regression,
   not a real-SQL assertion; the group-store suite already covers the underlying
   eventful lane).
3. **File-split status**: app-session lib was split 1818-line
   (authorization.rs/queries.rs/mutations.rs/tests.rs all <1000). The session-store
   mysql.rs / memory.rs and bcs-session-file/src/service.rs exceed 1000 lines but
   their touched surfaces were kept to the touched-responsibility discipline; full
   mechanical file splits landed where new code was placed (action_audit.rs modules).
   MySQL store files were NOT split when no responsibility boundary changed — flagged
   for final review per the ≤1,000-line plan clause (same situation as noted in the
   ledger Task 5 "modification source" reading note).
4. **HTTP OpenAPI contracts (`sessions.yaml`, `session-files.yaml`, `connections.yaml`)**:
   no wire-visible signature changed (no new required client fields; error codes
   unchanged, only the 403 paths for stale owner_id moved from claim-local to
   authority-fact-local — creating an HTTP-visible behavioral delta captured by the
   parity/facade tests rather than by yaml edits. No contract schema edits made.
5. Background-recovery "keeps the original operator identity" is realized as file store
   retained admitted/completed records and per-row sub-operation persistence; the chat-run/
   delivery-side operation-context work is Task 12's lane per plan (async/sse), and its
   scope here only extends to keeping the session auditor aligned with the existing
   recovery flows, which already merge into the durable commands the ServiceWrapper reads.
6. The 2 pre-existing bcs-collaboration-store suite failures (env) were verified against
   the pre-branch state before this work started (ledger notes this in Task 6); called
   out to avoid any accidental misattribution in review.

---

## Review-fix report (post-review round 1)

### FINDING 1 (Important) — fixed: mixed Human+Bot launch keeps the Human operator in the create-lane audit
- **Defect** (as found): `resolve_launch_caller` mapped an authorized mixed
  Human+Bot caller to `SessionCaller::Bot`, and `prepare_launch` derived the
  `BotOperationContext` from the `SessionCaller` alone, so the
  `create/session/applied` audit row was Bot-only (`operator_user_id` NULL) even
  though a verified Human operated through the managed bot — the exact
  "仅记录被代理 Bot 丢失管理员身份" anti-case (§12.1(6)) the mutations.rs comment
  claimed to prevent.
- **RED-first evidence (old code)**: the new
  `mixed_identity_launch_keeps_the_human_operator_in_the_create_audit` test in
  `owner_manager_parity.rs` was written before the fix and failed on the then-current
  commit:
  `assertion 'left == right' failed: the verified Human operator must survive the
  mixed launch lane: Bot { bot_id: "bot-x" }  left: None  right: Some("bob")` —
  i.e. the real memory-twin audit record carried a Bot-only operator.
- **Fix**: `SessionLaunchRequest` (service-api `application/session_launch.rs`) gains
  `operator_user_id: Option<String>` — the VERIFIED Human operator's user id.
  The V1 facade (`mutations.rs::create`) populates it from
  `command.caller.user` (so pure-Human and authorized mixed callers both carry it;
  Bot-only launches stay `None`). `SessionLaunchApplication::prepare`
  (`launch.rs`) now derives the launch `BotOperationContext` from it:
  `Some(user_id)` → `BotOperationActor::Human { user_id, effective_actor_id: creator }`;
  `None` → the previous behavior (`SessionCaller::Human` keeps `owner_id`;
  `SessionCaller::Bot` stays a Bot actor). The legacy `bcs-http` launch builder
  passes `None` with a comment documenting the fallback; the service-api
  session-launch contract test pins `Some("alice")` on the Human lane.
  No identity is ever derived from `created_by`, and Bot-only launches are
  byte-for-byte unchanged.
- **Covering tests after the fix**:
  - `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-app-session` — every
    binary green: lib 8, `group_session_connection` 13, `owner_manager_parity` 2,
    `session_file_facade` 7, `v1_session_service` 79.
  - `cargo test … -p bcs-session-session -p bcs-session-file -p bcs-session-file-store
    -p bcs-session-store` — all binaries green (11/2/2/16 conformance-cleanup-activation-
    launch; 60/4 file; 11/3/42 file-store; 15/60/42 session-store).
    (full command: `-p bcs-session -p bcs-session-file -p bcs-session-file-store -p bcs-session-store`)
  - `cargo test … -p bcs-http` — 38 binaries, all green (the fix touched the legacy
    launch builder).
  - `cargo test … -p bcs-service-api --test session_launch_contract` → 2 passed.
- **Commit**: `fix(bcs): keep human operator identity in launch session audits`.

### FINDING 2 (Important) — report corrected, no code change
The original Test summary reported "session_action_audit 42/42 … (real SQL)" and
"file_action_audit 42/42 (same)" — those figures were the whole test-BINARY totals
(each of those targets actually contains 6 authored audit tests; the other 36 are
`bootstrap_migrations::*` bring-along tests compiled in through the shared
`migrations.rs` include), and lifecycle contains 4 and parity 1 (now 2 after the
review fix). The Test summary section above now names the exact command for every
claimed figure and quotes that command's real output, including the bring-along
composition.

>Note for future write-ups: this report's canonical path is
>`/home/admin/workspace/Avernet/.superpowers/sdd/2026-10-08-bot-owner-manager-implementation/task-11-report.md`
>(repo-root `.superpowers`), NOT `apps/bcs/.superpowers`, which I mistakenly created
>and which will be removed in favor of this canonical copy.
