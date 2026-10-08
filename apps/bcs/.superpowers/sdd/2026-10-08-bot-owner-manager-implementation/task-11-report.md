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

### Focused Task 11 suites (Step 2, all GREEN)
- `bcs-app-session --test owner_manager_parity` — 1 passed. All six Step-1
  parity assertions: `owner_message_ids == manager_message_ids` (REAL facade output
  through the message query double), managed-bot-not-in-session collect error,
  unaffiliated Human detail error, stale signed owner_id + current manager OK vs
  no current role error via `resolve_authorized_principal` (actual hook facts),
  other Human file delete denied + owner deletes successfully, plus manager-column
  audit rows carrying `operator_user_id=Some("bob")` / `effective_actor_id="bot-x"` /
  Applied on the real memory audit records.
- `bcs-session-store --test session_action_audit` — 42 passed (STEP SQL probes).
  RED list pinned: dual-identity applied row for Human a/bot-x; same-context internal
  retry keeps one row; already-collected no-change writes nothing; Bot-only uncollect row
  has operator_user_id NULL (raw SQL decode); armed SELECT-failing audit-step rolls the
  collect back with zero residue and works after disarm; create audit in the same insert
  transaction; memory twin published together, stage-armed flip discarded, real
  collected-state integrity.
- `bcs-session-file-store --test file_action_audit` — 42 passed. Insert+applied same
  transaction; injected insert-audit failure zero residue; status-change applied row with
  dual identity and no-change no-op audit-free on either dialect; delete+completed same
  transaction; delete-audit-failure retains the row with admitted kept and no completed;
  `record_operation_phase` identical replay idempotent and different content Conflict.
- `bcs-session-file --test file_action_audit_lifecycle` — 4 passed. Admitted-failure →
  0 backend calls; backend success then final-transaction failure → row retained, admitted
  kept, no completed, error surface; share writes admitted+completed (no metadata change);
  pending sweep marks Failed with honest System rows never pretending the originator Human.

### Regression (Step 4)
- `bcs-app-session` all binaries: 108 passed (79 v1 + 16 + component suites) — including
  existing facade suites updated for authority-fact seeding (no created_by fallbacks).
- `bcs-session` (launch, application conformance, runtime-cleanup, activation): all green.
- `bcs-session-file`, `bcs-session-file-store`, `bcs-session-store` incl. conformance:
  all green (memory unit tests extend `unit_test_operation` and per-call fresh ids).
- `bcs-http` legacy suite: 38 binaries all green (session member/status routes, file
  paths incl. session-file test-app with fail-closed authority context).
- `bcs-api-http`, `bcs-group`, `bcs-collaboration-runtime`, `bcs-message-flow`: green
  (pre-existing `bcs-collaboration-store` suite failures remain as recorded in the ledger,
  verified unrelated to this plan).
- Group store/group/app-group suites: green after the carry-forward commit.

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