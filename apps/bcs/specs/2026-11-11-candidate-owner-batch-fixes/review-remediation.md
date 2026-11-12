# PR #3893 review remediation design

## Record

- Owner: BCS authority persistence, Service API, and test support.
- Baseline: PR #3893 head `bbf99abc372610c5409b7d5649d3528dad649ee0`.
- Original contract: [Candidate owner batch review fixes](spec.md) and
  [BotAuthorityRepoPort](../../crates/service-api/bcs-service-api/src/port/repo/bot_authority.rs).
- Architecture decisions: this remediation does not add or supersede an ADR.
- Approved scope: repair the MySQL scenario inventory, Rust Service API
  compatibility metadata, and Memory batch-read snapshot behavior.
- Explicitly deferred by the requester: do not split the four oversized files
  and do not renew or expand their waiver. The file-size finding remains open.

## Decisions and acceptance

### 1. MySQL scenario inventory

The shared `ownerships_for_projects_per_item` report increases the authority
harness from 39 to 40 scenarios and the authority-plus-transfer aggregate from
63 to 64. Update all three owned expectations together:

- `crates/services/bcs-edge-permission-store/tests/authority_mysql.rs`: 40;
- `.github/workflows/unit-tests.yml`: aggregate 64;
- `singlebox/ci/tests/test_bcs_ci_build_reuse.py`: aggregate 64.

Do not change the unrelated OpenAPI operation count of 39. Acceptance requires
static inventory agreement and a real MySQL gate run when a suitable isolated
database is available; lack of such a database must be reported as a gap.

### 2. Rust Service API compatibility

`bcs-service-api` moves from 5.0.0 to 6.0.0. `BotCandidate` gains a Rust struct
field and the three authority traits gain required methods without defaults;
both break external Rust source compatibility and follow the crate's previous
major-version precedent. Update `Cargo.toml`, `Cargo.lock`, and the crate
`CONTEXT.md` together.

Compatibility wording must distinguish two surfaces:

- HTTP JSON: `owner_user_id` is optional and omitted when absent, so existing
  clients can ignore it;
- Rust Service API: struct literals and trait implementors must be updated,
  so this is a breaking 6.0.0 change.

The PR description must classify Service API and breaking, list affected
consumers, implementations, deployment/configuration impact, and migration
strategy. It must also contain the three design-criteria phrases required by
the current PR-1 script. The requester authorized updating the PR description
with `gh pr edit` after local validation, but did not authorize code commit or
push.

### 3. Memory batch-read snapshot

`memory_ownership_projection` retains its immediate empty-input return and its
existing strict error behavior. For non-empty input it must:

1. Acquire the `authority`, `deleted_bot_ids`, and `bots` read guards once in
   the existing lock order.
2. Deduplicate exact Bot IDs while retaining first-input order.
3. Compute each distinct projection once, including legacy fallback and owner
   integrity handling.
4. Expand projections to the original input order and multiplicity without
   re-reading mutable state. Repeated IDs therefore share one result even if a
   soft delete is queued behind the initial registry snapshot.

Add a deterministic regression beside the implementation. The test holds the
`bots` write guard so the batch parks, queues `soft_delete` behind the batch's
registry read, then releases the guard. The old per-item implementation exposes
`Some` followed by `None` for repeated IDs; the fixed implementation returns a
single-snapshot result for every occurrence.

## Validation design

- Prove the new Memory regression fails before the implementation change and
  passes afterwards.
- Run Memory authority/conformance tests, `bcs-service-api` tests with the
  updated lockfile, and SQL authority read/batch-cost tests.
- Re-run the Bot OpenAPI contract suite and CI workflow static checks.
- Run PR-1 with the updated body in a temporary file. Run API-1 if
  `cargo-public-api` is already available; do not install global tooling.
- Check whitespace and line counts for every source file modified by this
  remediation. No global formatter is allowed.

## Delivery and remaining gaps

Record actual results in this document after execution. Leave code changes in
the detached-head working tree without commit or push. Update the GitHub PR
description only after local validation. Remote CI cannot exercise unpushed
code; do not claim it as validation of this remediation.

The four pre-existing oversized modified files remain outside this repair by
explicit requester decision. This document does not assert repository-wide
file-size compliance or provide a waiver for them. The optional MySQL
EXPLAIN/collation test remains opt-in and is not added to CI in this scope.

## Execution evidence (2026-11-11)

Implementation used the approved Native path on the detached `bbf99abc`
workspace. No code was committed or pushed.

- MySQL inventory: the pre-fix GitHub job failed with `left: 40`, `right: 39`.
  Static recount now proves `main=32`, `ownership_sync=8`, `transfer=24`, and
  aggregate `64`; all three expectations were updated. `test_unit_test_workflow_diff.py`
  passed 3 tests and the workflow preservation unittest passed 1 test.
- Memory snapshot: `repeated_ids_share_one_registry_snapshot` failed before the
  fix with `[Some("own"), None]` instead of two owners, then passed after the
  snapshot/deduplication change. Memory authority and shared conformance passed
  24 tests.
- Service API: full `cargo metadata --locked --format-version 1` failed with exit
  101 before the lockfile edit and exited 0 afterwards. `bcs-service-api` passed
  272 tests. PR-1 passed with the remediation body.
- Cross-checks: SQL `authority_reads` and `authority_batch_cost` passed 12 tests;
  the Bot OpenAPI contract passed 13 tests. `git diff --check` passed. Source
  files changed by this remediation are 547, 183, and 189 lines, all below the
  1,000-line limit.
- Plan ruling: `cargo metadata --no-deps` did not validate the lockfile, so the
  full metadata command was used for both red and green lock checks.

API-1 was not executed because `cargo-public-api` is unavailable; no global tool
installation was performed. No dedicated empty MySQL database was provided, so
the real MySQL gate was not run locally. Unpushed changes cannot receive remote
CI validation. Singlebox E2E was not run.

The full workspace suite was attempted after an initial disk-full linker failure
(`ld ... Bus error`). With the CI line-tables-only settings it completed with
**8,236 passed, 9 failed, 73 ignored**. The failures were one
`conformance_provider_a2a_registration` test, five `provider_initial_managers`
tests, and three `mysql_store` tests. A clean archive of the unmodified
`bbf99abc` baseline reproduced the same three targets and all nine failures with
the same error messages; they are baseline issues, not remediation regressions.
No unrelated provider/collaboration modules were changed to pursue them.

The intentionally unresolved oversized-file finding remains unchanged: the four
files were not split and no waiver was requested or expanded. The GitHub PR
description was last updated and verified at 2026-11-12T02:12:47Z with Service API,
breaking, propagation, design criteria, the explicit local/unpushed
validation label, and the baseline-compared full-workspace result. This does not imply code integration, merge, or deployment.

## Final independent review

A fresh read-only reviewer inspected the working-tree diff and the two spec
files. It found no Critical or Important issues and repeated the focused Memory
regression 20 times without failure. Three Minor items are deferred and tracked
in the execution ledger: asserting `soft_delete`'s boolean result in the new
regression, documenting/reconciling the deleted→bots lock order with fixture
helpers, and updating the PR body's old 102-line count for the now 183-line
`memory_ownerships_batch.rs`.

The review also confirmed 40/64 scenario inventory, 6.0.0 manifest/lock
alignment, PR-1 compatibility, and the remote body's explicit local/unpushed
label. Memory's legacy-created_by batch branch remains without a direct
Memory-only test; SQL fallback coverage passes and the implementation branch is
unchanged. No additional fix pass was opened for the deferred Minor items.
