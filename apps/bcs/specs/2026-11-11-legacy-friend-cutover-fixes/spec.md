# Legacy friend cutover compatibility fixes

## Superseded cutover decisions

The requester subsequently chose to invalidate legacy requests instead of
migrating them, confirmed the original field widths suffice, and explicitly
authorized removing the unexecuted MySQL 046 / SQLite 044 from this PR.
[The edge-owned cutover](../2026-11-11-edge-friend-cutover/spec.md) supersedes
this document's history-preserving migration and legacy-list decisions.
The original evidence below is retained as history, not current rollout advice;
error propagation, kind/receiver authorization and collaboration writeback
repairs remain applicable.

## Record

Owner: BCS. Baseline: PR #3846, `b5dabaab2cc9f0628eec23152cc3869e385d337d`.
The user approved repairing all four review findings, in the current checkout
on the PR branch, without creating a worktree. No deployment is implied.

## Scope and decisions

- Keep the legacy HTTP shapes and friend-name read path. Mirror Human→Bot
  decisions into the relation graph used by that reader, including revocation.
  Human mirror failures must propagate; idempotent retries repair the mirror
  only while the authoritative grant remains active (never undo revocation).
- Add new SQLite/MySQL migrations preserving legacy request IDs, environment,
  direction, timestamps and status (`accepted` → `approved`). Never rewrite
  numbered migrations. Existing connect decisions win on an existing ID.
  Require historical request coverage before the durable server accepts traffic.
  Stop legacy writers before migration; do not run old writers after cutover.
  MySQL 046 widens request env/author/decider columns before copying legacy
  values (32/256/256 characters); the active migration history is unchanged.
- Make permission-request inbox/outbox reads fallible. SQL errors and malformed
  rows propagate through ConnectService and the legacy/V1 facade, including
  pending checks during mutations. No silent partial or empty success.
- Both decision paths resolve the actual request and require Connect kind before
  any mutation. Explicit receiver hints must not bypass persisted authorization.

## Contracts and boundaries

See [friend domain](../../crates/services/bcs-friend/CONTEXT.md),
[edge store](../../crates/services/bcs-edge-permission-store/CONTEXT.md),
[repo contract](../../crates/service-api/bcs-service-api/src/port/repo/permission_request.rs),
[writeback contract](../../crates/service-api/bcs-service-api/src/port/friendship_writeback.rs),
and [migration lifecycle](../../migrations/README.md).

Only repository list result types change (all implementations/consumers and
conformance drivers are updated). No wire fields or route paths change.
Human relation persistence is required while its legacy reader remains active;
existing Bot↔Bot writeback error policy is not broadened in this repair.

## Acceptance and validation

Failing tests precede implementation: Human approve/auto-approve/revoke listing,
old pending request ID after upgrade, database read failures, and non-Connect
accept/reject with/without a receiver hint. Include migration idempotency,
environment isolation and preservation of newer decisions. Run affected Cargo
suites, workspace compilation, architecture gates and source-size checks.
Real MySQL and full-stack E2E evidence must be reported separately from SQLite.

## Delivery

Implementation was completed locally on the existing PR branch. At the initial
repair handoff it was not committed or pushed. The user subsequently authorized
a commit and an explicit `git push --no-verify`; this does not imply merge or
deployment. Owning knowledge is updated in the friend/store contexts and
migration README linked above.

### Verification (2026-11-11)

- Original regressions were observed failing before implementation. The four
  fixes now pass, including migrated-ID approval through the compatibility
  facade, all three list directions, non-Connect types with/without receiver
  hints, Human auto/manual approval, revocation and delayed approval retries.
- `cargo test --locked -j 2 -p bcs-edge-permission -p bcs-edge-permission-store
  -p bcs-app-invitation -p bcs-friend -p bcs-db-migrations`: 307 passed,
  one real-MySQL authority conformance test ignored.
- `cargo test --locked -j 2 -p bcs-api-http -p bcs-http -p bcs-admin`:
  1,016 passed. Environment-dependent MySQL tests are ignored in this default
  run; the new cutover-specific test was separately executed as described below.
- `cargo test --locked -j 2 -p bcs --test integration_friend_connect_store
  --test bot_authority_migration --test bootstrap_auth_boundaries`: 7 passed.
- `cargo check --locked --workspace --all-targets -j 2`: passed, with existing
  unused/dead-code warnings. No global formatter was run.
- `bcs-admin db migrate --dialect mysql --check-files`: passed, versions 001–046.
- Real MySQL 9.7.2 (temporary loopback instance, strict SQL mode):
  `friend_cutover_mysql_upgrade_preserves_ids_widths_and_history` passed.
  It applies 001–045, seeds legacy 255-character actor IDs and a 32-character
  environment, applies 046, verifies the full values and readiness gate,
  and checks that rerunning preserves history and newer decisions.
- The broader ignored `full_mysql_migration_chain_applies_and_preserves_history`
  was attempted, but failed at the existing history-query EXPLAIN assertion
  (`column 'key' is missing or NULL` in `assert_history_lookup_plans`). This is
  not reported as a passing full MySQL regression; the check was not weakened.
- Port-purity, forbidden-symbol and store-boundary checks passed. Dependency,
  import-rule and trait-naming checks fail with the same reported failures on
  the original PR snapshot; no additional failing gate entry was found.
  `cargo-machete` is not installed. These baseline failures remain unresolved.
- `git diff --check`, changed-source size (maximum 999 lines), and changed
  Markdown relative links passed. Historical numbered SQL files are untouched.

### Remaining validation limits

No full Singlebox E2E or production-scale migration/lock test was run; this
local repair used focused module and bootstrap verification, not a release or
push gate. The bulk backfill requires the documented maintenance window.
MySQL 8/OceanBase-specific behavior is not established by MySQL 9.7.2 evidence.
