# Carolynli review follow-up (2026-11-09)

Scope: PR #3708 comment 6736147439, F1/F2/F3. Implementation inline; a bounded read-only review checks the F1/F3 patch.

## Plan

- [x] F3: reproduce the list/initialization lock inversion with a deterministically
  polled list future while authority is write-locked. Acquire authority before
  bots in the candidate list. Run Memory tests.
- [x] F1: reproduce registration failure leaving a mine-migratable version-0 Bot.
  Extend the existing Provider-admin ownership composite to ordinary fresh Bot
  registration. Commit initial authority with the Bot before any candidate-list
  visibility; preserve replay and existing-Bot attachment semantics. Do not repair
  initialized Bots by regranting managers. Test failure/retry and concurrent mine.
- [x] F2: reproduce retained trailing-space identity failure on native MySQL.
  Correct the specifically reviewed 043 migration to NO PAD binary comparison,
  archive its exact old bytes, strictly recognize only that historical checksum,
  and add an additive follow-up for databases that already recorded 043. Never
  rewrite migration history records. Verify fresh and retained full chains.
- [x] Update contracts/context/deployment notes, run affected tests and source
  size/whitespace checks, commit and push with --no-verify as explicitly requested.
  Reply to the review comment with actual results and remaining limitations.

## Migration correction scope

The request to fix F2 explicitly targets PR #3708's committed migration 043.
This is a scoped historical correction, not permission to edit other migrations.
The original name/version/checksum remains recognized only for its exact archived
body; a new numbered migration upgrades previously applied installations. MySQL
must support NO PAD utf8mb4_0900_bin (MySQL 8+); unsupported compatible engines
must fail rather than silently fold identities. No stored identity is trimmed.

## Current contract and compatibility

Only a **nonempty normalized manager set** selects atomic ordinary admission.
Single-owner clients (including duplicate-only lists) retain their legacy path.
Fresh Gateway/Plugin/Subbot multi-user admissions commit identity and authority
before returning from Core. A2A remains atomic. Replays with initialized authority
never reapply a snapshot. Multi-user requests targeting an existing version-0 Bot
fail with Conflict before capability/token/binding writes, rather than claiming
success around owner-only migration. Operators must explicitly reconcile partial
registrations left by older binaries; initialized older states cannot be safely
backfilled from a new registration request because revocations are authoritative.

No HTTP fields, permissions, manager limits or runtime authorization policy change.
The new additive Core and repo entries have fail-closed defaults. Service API 4.0
records this PR's source-breaking `OwnershipInitialization` manager field; all
workspace consumers rebuild together and Human-token callers pass an empty list.

## Deployment

Stop writers, use a MySQL-compatible engine supporting `utf8mb4_0900_bin`, and run
through 044 before multi-user admission. Corrected 043 preserves NO PAD legacy
trailing-space keys; 044 repeats those ALTERs for previously applied original 043.
The original 043 file is archived verbatim; the runner pins old AND corrected
checksums, name, version and dialect. It never updates historical checksum/date
records, and unknown or future-edited revisions still fail. Interrupted original
043 ALTERs can be resumed with the corrected idempotent MODIFY statements. No
identity trimming, deduplication or schema downgrade is permitted on rollback.


The atomic multi-user path uses the same owner/manager/profile authority lifecycle
as trusted Human/A2A registration; it does not append retired creator-relation
writes after commit. Single-owner compatibility ensures remain unchanged. The
Memory composite locks binding projections before their reference index, and
rejects collisions between the pending physical Bot and its initial Humans before
publishing either. Both protections have controlled red/green regression tests.

## Executed validation

- Red/green: the original ordinary registration left a version-0 Bot after a
  manager failure; the new test now proves no Bot, binding or migratable candidate
  survives. Core/application interleaving tests verify the mine sweep cannot
  steal the snapshot. Gateway and Plugin revocation/transfer replays still pass.
- Red/green controlled futures reproduce both lock inversions (mine versus
  authority, and binding versus reference index). Both lock-order tests now pass.
- Red/green: pending physical Bot IDs matching owner/manager Human IDs previously
  passed/overwrote staged identities; both are rejected without publication now.
- Red/green on isolated native MySQL 9.7.2: retained `human_retained` and
  `human_retained ` rows under NO PAD legacy collation caused original 043 to
  fail with error 1062. Corrected 043 preserves both registry and grant rows.
- Seven affected packages (`bcs-bot`, `bcs-bot-store`, `bcs-app-a2a-bot`,
  `bcs-service-api`, `bcs-app-register`, `bcs-admin`, `bcs-db-migrations`),
  `SERVER_ENV=dev cargo test --tests --no-fail-fast`: **1,045 passed,
  0 failed, 12 ignored** in 114 suites. After the final application adjustment,
  `cargo test -p bcs-bot --tests`: **282 passed, 0 failed**. These counts overlap.
- Final narrow registration/store-library rerun: **116 passed, 0 failed,
  4 ignored**. Includes SQLite rollback/retry, both controlled lock-order tests,
  initial identity collision checks and no post-atomic retired relation writes.
- Native MySQL full-chain manager test: **1 passed**; covers 001–044, retained
  trailing-space identities, ordinary/A2A atomic rollback, retry, case/accent
  identities, 100 managers, runtime-profile and membership separation.
- Native `bcs-admin` full-chain test: **1 passed**; includes applying archived
  original 043, recording its real historical checksum, upgrading via 044 and
  verifying that all prior checksum/timestamp rows remain unchanged. Strict
  checksum/name/version/dialect rejection also passes in the normal test suite.
- MySQL runs used only a disposable local instance; its EXPLAIN format was set to
  TRADITIONAL for the existing plan assertions. No existing database was used.
- `git diff --check` and changed-source size checks pass (19 Rust files,
  maximum 983 lines). No formatter, CI threshold or production lint was weakened.
- `cargo clippy -p bcs-bot -p bcs-bot-store --lib --no-deps` **does not pass**:
  33 denied expect/unwrap diagnostics are in eight files unchanged from the
  review head (verified by Git diff). No repository-wide lint pass is claimed.
- Full workspace coverage, full Singlebox/HTTP concurrency E2E and production-scale
  migration lock-duration testing were **not rerun** for this follow-up. The
  deterministic concurrency and real store/migration tests above are the evidence.
