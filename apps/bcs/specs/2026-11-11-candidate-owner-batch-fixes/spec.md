# Candidate owner batch review fixes

## Record

- Owner: BCS authority persistence and test support.
- Baseline: PR #3893, `7a9fc8be365c4b8931f49e3a05a1f9d780c27131`.
- Scope approved after review: repeated-ID correctness, indexed MySQL batch
  reads, and the newly oversized authority test-support file.
- Contract: [BotAuthorityRepoPort](../../crates/service-api/bcs-service-api/src/port/repo/bot_authority.rs).

## Decisions and acceptance

1. Deduplicate exact Bot IDs before querying. Restore the original input order
   and multiplicity when projecting results. Do not deduplicate returned owner
   edges: actual corruption must still fail the whole batch.
2. Add an indexable raw identity `IN` prefilter to MySQL batch state, creator,
   and owner queries, retaining the binary residual predicate. This preserves
   case/trailing-space identity on older collations without a schema migration.
   SQLite remains unchanged. At most 128 distinct IDs are read per statement;
   MySQL binds each ID twice (at most 257 parameters including env).
   Removing binary checks outright was rejected because it changes behavior
   on retained schemas with case-insensitive/PAD SPACE identity columns.
3. Move the failing authority hook to a sibling test-support module, retaining
   its existing public import path. Do not refactor unrelated oversized files.
4. Pin repeated-ID behavior in shared SQL/memory contracts and query-count
   tests. Exercise generated SQL on a real isolated MySQL database with the
   full migration chain, verify indexed EXPLAIN plans and exact matching.

## Validation and delivery

Implementation and focused verification completed for the PR #3893 follow-up.
Reusable behavior and costs are recorded in the
[store context](../../crates/services/bcs-edge-permission-store/CONTEXT.md).
There is no response-shape, schema, or configuration change.

- Red: SQLite duplicate-ID regression failed with a false two-owner integrity
  error. The MySQL test on the original implementation failed because Bot-state
  queries used only the environment key rather than the identity key.
- Green: affected crate suites (`bcs-edge-permission-store`,
  `bcs-edge-permission`, `bcs-app-bot`, `bcs-api-http`): 583 passed, 2 opt-in
  MySQL tests ignored in the default run. Memory authority: 14 passed.
- New opt-in MySQL ownership regression: passed on isolated MySQL 9.7.2 after
  applying the full current migration chain, with EXPLAIN JSON formats v1 and
  v2 both exercised. Checks cover 20,000 Bots,
  generated statement plans, legacy creator reads, repeated inputs, and both
  migrated NO PAD and retained case-insensitive/PAD SPACE columns.
- OpenAPI Bot contract suite: 13 passed using isolated `uv --with pytest
  --with pyyaml` dependencies; no dependency files changed.
- `BCS_STATIC_VERSION=1 cargo check --manifest-path apps/bcs/Cargo.toml
  --locked --workspace --all-targets -j 2`: passed with existing warnings.
- Port-purity and forbidden-symbol gates passed from the BCS working directory.
  Changed Rust files are below 1,000 lines (the split main fixture is 930);
  Markdown targets, whitespace and mechanical split fidelity passed.
- Independent diff review reported no actionable findings.

Commands for the focused gates (from repository root):

```sh
BCS_STATIC_VERSION=1 cargo test --manifest-path apps/bcs/Cargo.toml --locked -j 2 \
  -p bcs-edge-permission-store -p bcs-edge-permission -p bcs-app-bot -p bcs-api-http
BCS_STATIC_VERSION=1 cargo test --manifest-path apps/bcs/Cargo.toml --locked -j 2 \
  -p bcs-bot-store --test memory_authority
# BCS_TEST_MYSQL_URL must name a dedicated empty database; never a retained DB.
BCS_STATIC_VERSION=1 cargo test --manifest-path apps/bcs/Cargo.toml --locked -j 2 \
  -p bcs-edge-permission-store --test authority_ownerships_mysql -- --ignored
uv run --no-project --with pytest --with pyyaml python -m pytest \
  apps/bcs/tests/openapi/test_bot_v1_contract.py -q
```

Limits: no full workspace test/coverage run, full Singlebox E2E, original
all-scenario MySQL authority gate, actual MySQL 8/OceanBase run, or production
load test. Migration-chain success in the new test is not a claim that all
MySQL suites pass. Commit/push delivery is recorded by the PR's Git history;
merge and deployment are not established by these checks.
