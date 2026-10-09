# CI Fix Round 2 — PR 2568 (feat/bot-owner-manager-permissions-squash)

Branch: `feat/bot-owner-manager-permissions-squash`, amended into the single commit `29b9e4a77a` (new SHA at the end of this report).
Failure classes fixed: (1) Unit-tests live-MySQL `ERROR 22001 (1406): Data too long for column 'audit_id'` inside the bcs-admin full-mysql-chain test (session-insert phase), (2) E2E + Singlebox: 3 failed assertions of `story_team_manager_sources_platform_sync` (the verified snapshot-sync PUT and both member-repair POST/DELETE returned 403).

## Failure 1 — audit_id (and sibling id) VARCHAR overflow

### Root cause (writer × column audit)

The 032 columns were sized before the id formats were finalized: several lanes
compose `audit_id` from raw strings that are individually legal at their own
column widths but jointly exceed 64 chars. The exact CI failure:

- `bcs-admin` chain test phase 2 inserts a session on the migrated MySQL chain
  with an operation id shaped `migrate-group-notify-<group id>` (~25 chars);
  the session audit lane composes
  `session-action:{env}:{operation_id}:{step_key}` = 15 + 1 + 5 + 1 + 25 + 1 + 21 ≈ 69 > VARCHAR(64)
  → MySQL rejects the insert (`ERROR 22001 (1406) Data too long for column 'audit_id'`),
  which the server maps to `Internal error: session insert: ...` and the chain
  test fails. SQLite/memory twins are TEXT/unbounded, so nothing local failed —
  the overflow was MySQL-real-width-only (exactly the class the task called out).

Full writer audit (every `audit_id` writer vs its 032 column):

| Column | Writer(s) | Worst-case composition | Old | New |
|---|---|---|---|---|
| `bcs_bot_action_audits.audit_id` | `delivery_action_audit_id` (message-store), `session_action_audit_id` (session-store), `group_action_audit_id` (group-store), `chat_run_action_audit_id`, `file_action_audit_id` (session-file-store + session-file), `business_action_audit_id` (edge-permission-store), `action_audit_id` (bot-store) — all `{lane}:{env}:{operation_id}:{step_key}` with lane prefixes 10–16 chars (longest `chat-run-action`) | 16 + 1 + env + 1 + operation_id + 1 + step_key; with op ids embedding uuids (36) / `run_id`-column-width ids this is routinely > 64 | 64 | **512** |
| `bot_manager_changes.audit_id` | SQL expr `audit_id_expr`: SQLite `? \|\| '-' \|\| id`, MySQL `CONCAT(?, '-', id)` — `{operation_id}-{edge_id}` | 256 + 1 + 19 digits = 276 | 64 | **512** |
| `bot_ownership_initializations.audit_id` | `{operation_id}-init-{bot_id}` (ownership_initialization.rs / memory twin) | 256 + 6 + bot uuid shape (≈40) | 64 | **512** |
| `operation_id` ×7 columns (`bcs_bot_action_audits`, `bot_manager_changes`, `bot_manager_sync_operations`, `bot_team_manager_sources.last_operation_id`, `bot_ownership_initializations`, `bcs_message_deliveries`, `bcs_chat_runs`) | the shared `BotOperationContext.operation_id`; worst shapes embed resource ids at their own column caps: `bot-event-delivery:{delivery_id≤128}:{uuid 36}` ≈ 190; `direct-chat-create:{run_id≤128}` ≈ 147; `session-file-pending-sweep:{row_file_id}`; per-resource sub-records `{parent}:{resource_id}` | ≈ 190 | 64 | **256** |
| `bot_manager_sync_operations.service_id` | credential `sub` claim; the verifier's own bound is `MAX_CLAIM_LEN = 256` (`team_manager_credential.rs`) — a 65..256-char claim verifies but 500s on insert | 256 | 64 | **256** |
| `step_key` | `stable_step_key(action, resource, phase)` controlled vocabulary, ≤ `delete/session_file/completed` = 29 chars | 29 ≤ column | 96 | 96 unchanged |
| `terminal_reason`, `status`, `action`, phase/resource/operator vocabularies | fixed CHECK vocabularies | — | — | unchanged |

Client-supplied ids (`team_id`, `new_team_id`, `idempotency_key`,
`client_request_id`, migration `batch_id`) are unbounded inputs whose columns
are VARCHAR(64) inside wide composite unique keys that cannot be widened
(`uk_manager_sync_scope` and `uk_bot_transfer_client_request` are already at
2560/2816 of MySQL's 3072-byte budget), so the fix for them is fail-closed
application validation, not widening:

- `validate_team_sync_command` (service-api types): team_id / new_team_id /
  idempotency_key length ≤ `bcs_domain::AUTHORITY_CLIENT_ID_MAX` (64) → typed
  400, never MySQL 1406 mid-transaction.
- `OwnershipTransferServiceImpl::validate_bounded_client_id` (bcs-app-bot):
  client_request_id ≤ 64.
- `validate_migration_batch` (service-api): batch_id ≤ 64 (it is also composed
  into the init operation id).

### Fix policy (single, consistent)

Widen the unshipped 032/033 columns to each writer's proven maximum with
headroom; enforce input bounds for client-supplied ids. Widths are single-sourced
in `bcs_domain` (`bcs-domain/src/bot_authority.rs`):

- `AUTHORITY_AUDIT_ID_VARCHAR_WIDTH = 512` — the derivation is by construction:
  `prefix(16) + ':' + env(≤64) + ':' + operation_id(≤256) + ':' + step_key(≤96)`
  = 435 ≤ 512, and manager-change/init lanes are smaller.
- `AUTHORITY_OPERATION_ID_VARCHAR_WIDTH = 256` (worst embedding shape ≈ 190).
- `AUTHORITY_SERVICE_ID_VARCHAR_WIDTH = 256` (verifier claim bound).
- `AUTHORITY_CLIENT_ID_MAX = 64` (schema-backed input bound).

MySQL byte budgets recomputed in the 032 header comment:

- `uk_bot_action_audit_id` / `uk_manager_changes_audit_id` / `uk_ownership_init_audit_id`: 512 × 4 = 2048 B (single-column keys) ✓
- `uk_bot_action_audit_slot` = 256 + 1024 + 384 = 1664 B ✓
- `uk_manager_sync_scope` = 256 + 1024 + 1024 + 256 + 256 = 2816 B ✓ (all < InnoDB 3072)

SQLite twin (033): columns stay TEXT (the file's whole style); the widths are
documented in per-table comments mirroring the MySQL digits — **and the
comments carry no semicolons**, an important constraint (the Rust sqlite
migration runner splits statements on `;`; my first version had a `;` inside
one comment and broke the entire chain locally: `execute sqlite statement: not
an error`. Caught by the migration test suite going RED; fixed and re-verified).
The server boots the chain to `current_version=33 target_version=33 applied_versions=33`.

### Regression tests added

- `sql_files_pin_the_authority_id_width_budgets` (crates/bootstrap/bcs/tests/bot_authority_migration.rs):
  parses the MySQL file's column declarations (including `ADD COLUMN ` ALTERs)
  and pins every audit_id / operation_id / last_operation_id / service_id width
  to the shared constants; asserts the sqlite doc-comments agree digit for digit.
- `authority_ledger_accepts_maximum_width_ids` (same file): real INSERTs of
  audit rows at every id's column cap (512 audit_id / 256 operation_id /
  longest step-key vocab value) into the full migrated SQLITE chain, asserting
  they commit and are readable — the sqlite twin accepts exactly what the
  MySQL real width accepts.
- Per-lane conformance tests at every writer (`audit_id_fits_the_durable_column_budget_at_worst_inputs`)
  in bcs-message-store, bcs-session-store, bcs-group-store, bcs-chat-run-store,
  bcs-session-file-store, bcs-session-file, bcs-edge-permission-store,
  bcs-bot-store: worst legal inputs (env at the longest resolve-env vocab,
  op id at its 256 cap, longest step-key vocab) → assert ≤ 512.
- `bot_manager_changes` SQL-expression budget proof (edge-permission-store):
  256 + 1 + 19 (widest i64) ≤ 512; memory twin + ownership-init pen tests in
  bcs-bot-store.
- Input-bound tests: `width_bound_tests` (team sync ids),
  `batch_id_bound` (migration batches), `client_request_id_bound` (transfer keys).

## Failure 2 — team-story 403s (env-claim mismatch in the singlebox runtime)

### Root cause (followed the evidence, reproduced locally first)

The signature verifies (401 was the uncredentialed lane probe only), scope
claims are empty/unrestricted, so the 403 must be `authorize_sync`'s env
comparison (`team_manager_sync.rs`: `self.env != store_env → Forbidden`).
The CI e2e bcs.log showed the server env is **local** (`ensure_human_actor ...
env=local`), while the OLD mint helper (`bot_authority.sh`) derives the claim
from ambient env vars only: SERVER_ENV/REAL_SERVER_ENV/ALIPAY_APP_ENV/BCS_SERVER_ENV → none exported
in the e2e.sh child → `""→"dev"`. Claim `dev` vs server `local` → 403.

Why the server is "local" while the story shell sees nothing: singlebox
deliberately does NOT export SERVER_ENV (`singlebox/singlebox.sh:808` comment),
but its bcs module starts the server process INLINE:
`SERVER_ENV="${BCS_SERVER_ENV}" ... singlebox/modules/bcs.sh:950` where
`$BCS_SERVER_ENV` resolves to "local" in the standalone mode
(`env/utils.sh resolve_bcs_server_env` → LOCAL_MODE=true → local) — exactly the
asymmetry the task suspected.

Local RED reproduction (round-1 real-server harness, extended): booted the real
bcs binary (durable SQLite config, BCS_AUTH_MOCK, `[team_manager_sync] enabled`
with the local-only key) with `SERVER_ENV=local` inline (singlebox shape), then
replayed the story's exact request sequence with the PRE-FIX mint logic:

```
SUMMARY[RED]: sync=F post=F delete=F tamper-P
403 body: {"code":40300,"message":"forbidden: team-manager credential 'e2e-story-platform'
           is bound to env 'dev' and may not run in env 'local'",
           "data":{"error_code":"invalid_manager_sync_source"}}
```

— byte-identical to the CI 403 class, and the failure is the env comparison, not
service_id and not the repair endpoints' verifier path.

### Fix — read the server's assembled env instead of guessing

- `/health` now reports the authoritative value
  (`BootstrapHealthPort::health`, `crates/bootstrap/bcs/src/http_adapter.rs`):
  `"env": bcs_config::resolve_env_str()` — the SAME function the verifier env,
  authority store envs, and audit env derive from, so the reported env and the
  verifier's are one source of truth at runtime.
- `bot_authority.sh`: new `_authority_server_reported_env` (curl /health → env
  field); `_authority_team_credential` mints the env claim FIRST from that
  report, keeping the env-var chain only as the unreachable-server fallback.
- Diagnostics improvement (the "why no WARN in bcs.log" question): the reason
  round-1's bcs.log tail showed only http_access lines is that NOTHING logged
  these rejections — no tracing call existed in the 403 conversion path, not a
  log-filter issue (the standalone config's default filter passes WARN). Added
  `WARN team-manager sync rejected: {code}: {message}` in
  `team_manager_sources.rs` at the credential-verify and all three business-call
  map sites (no credential/key ever logged). Verified in the harness server log:

```
WARN bcs_api_http::v1::internal::routes::team_manager_sources: team-manager sync rejected:
  invalid_manager_sync_source: forbidden: team-manager credential 'e2e-story-platform' is
  bound to env 'dev' and may not run in env 'local' request_id=...
```

  (Confirms not just the fix but the future-diagnosis goal: at default INFO
  level the reason is now on the console/bcs.log.)

### Local GREEN replay (final, all four story assertions)

Same harness, fixed script mint (the replay literally sources the updated
`bot_authority.sh` and calls the real `_authority_team_credential`):

```
== /health: (env reported)
== minted credential env claim: local
== the verified snapshot sync commits -> 200
== member-repair POST commits -> 200
== member-repair DELETE commits -> 200
== tampered same-key replay -> 409 (expect non-2xx)
SUMMARY[GREEN]: sync=P post=P delete=P tamper-P
```

The lane probe (uncredentialed) stays 401, and no rejection WARN lines remain in
the server log. Runtime artifacts: /tmp/harness-2568 (config dir, WS bot-connect
client, replay driver; database recreated per run).

## Verification matrix (local)

- `cargo check --workspace --all-targets` → clean.
- bcs-domain, bcs-service-api (30 suites), bcs-message-store, bcs-session-store,
  bcs-group-store, bcs-chat-run-store, bcs-session-file-store, bcs-session-file,
  bcs-bot-store (17), bcs-edge-permission-store (incl. `tests/team_manager_sync`
  46 passed), bcs-app-bot (13, incl. new client_request_id bound tests),
  bcs-jwt, bcs-admin (64 passed, 5 ignored = live-MySQL), bcs-api-http (22,
  incl. team_manager_routes where the new WARN path is covered by the
  unknown-credential/scope-rejection route tests) — all `ok`, 0 failed.
- bootstrap bcs: `--test bot_authority_migration` 13/13 (incl. the two new
  migration-width tests), `--test bot_authority_wiring` 6/6, `--test
  ownership_migration` 11+1-ignored, `--test e2e_bot_authority` 1/1,
  `--test e2e_ownership_transfer` 1/1, `--test ensure_mine_unit` 29/29.
- Real-server e2e slice: RED repro (3 lanes 403, exact CI shape) → GREEN final
  replay 4/4 assertions.
- Disk discipline: /shared at the 15G boundary → `cargo sweep --time 1` (no
  stale artifacts to clean; add-of-target artifacts were overlapping).

## Residual CI-only risks

1. The three live-MySQL `#[ignore]`d assertions (`full_mysql_migration_chain_applies_and_preserves_history`,
   plus the bot_authority/transfer MySQL companions) could not be executed
   locally (no MySQL). The 1406 fix is arithmetic (worst compositions ≤ new
   widths) and the MySQL file's index byte budgets were recomputed (< 3072);
   the bcs-admin chain pins (applied_versions=32 etc.) are unchanged since no
   migration count changed.
2. The full `bash scripts/e2e_coverage.sh` (OpenClaw stack + instrumented
   build) is not runnable locally; the team story was instead replayed against
   the real bcs binary on durable SQLite with the exact request shapes and the
   updated mint helper. The singlebox shape difference that caused the bug
   (SERVER_ENV passed inline, never exported) is now handled by construction
   since the claim env is read from the server's own /health.
3. The singlebox-stack lanes beyond the team story were not re-replayed this
   round; round-1's 64-test replay plus this round's RED→GREEN slice cover the
   changed lines. The new WARN lines light up under any future rejection.