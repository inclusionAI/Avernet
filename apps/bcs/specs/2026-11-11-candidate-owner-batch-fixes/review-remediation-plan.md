# PR #3893 Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the MySQL scenario inventory, declare the Rust Service API breaking version correctly, and make Memory owner batch reads deduplicated and snapshot-consistent without addressing the explicitly deferred oversized files.

**Architecture:** Keep all public method signatures and response semantics unchanged. Update existing scenario expectations and crate metadata in place; refactor only `memory_ownership_projection` to compute distinct projections over read guards acquired once, then restore input multiplicity.

**Tech Stack:** Rust, Tokio RwLock, Cargo, Python unittest/pytest, GitHub Actions and `gh`.

**Spec:** [PR #3893 review remediation design](review-remediation.md)

## Global Constraints

- Start from PR head `bbf99abc372610c5409b7d5649d3528dad649ee0`.
- Do not split the four oversized files and do not renew or expand their waiver; report that finding as unresolved.
- Do not run global `cargo fmt`; do not change unrelated logic.
- Do not install global tools or services. Use an actual MySQL gate only when an existing dedicated empty test database is available.
- Do not commit or push code. The authorized remote write is limited to updating PR #3893's description after local validation.
- Preserve the signatures `memory_ownership_projection(&self, bot_ids: &[String]) -> ServiceResult<Vec<Option<BotOwnershipView>>>` and `ownerships_for(&self, bot_ids: &[String])`.
- Preserve empty-input, legacy fallback, environment isolation, and whole-batch integrity-error behavior.

## Review Focus

- Repeated IDs racing a queued soft delete must share one snapshot result; Task 2 adds the deterministic lock-order regression.
- Deduplicating requested IDs must not hide genuinely duplicate approved owner edges; Task 2 runs the existing shared corruption scenario.
- Empty input and version-0 legacy fallback must retain their established results; Task 2 runs the shared Memory conformance suite.
- A manifest version bump without its lockfile must fail closed rather than silently selecting another version; Task 3 checks the red lock mismatch and the corrected `--locked` metadata.
- Scenario inventory must be 40/64 without changing the unrelated OpenAPI route count of 39; Task 1 runs the inventory/static checks.

---

### Task 1: Align the MySQL scenario inventory

**Files:**
- Modify: `apps/bcs/crates/services/bcs-edge-permission-store/tests/authority_mysql.rs:538`
- Modify: `.github/workflows/unit-tests.yml:234`
- Modify: `singlebox/ci/tests/test_bcs_ci_build_reuse.py:179`

**Interfaces:**
- Consumes: the existing `ownerships_for_projects_per_item` report in `run_bot_authority_contract_with_scenarios` and 24 transfer scenarios.
- Produces: authority inventory `40` and authority-plus-transfer inventory `64` used by the existing MySQL CI gate.

- [x] **Step 1: Record the existing failing assertion**

Use the existing GitHub failure from job `130532094597`: `authority_mysql_conformance` reports `left: 40`, `right: 39`. No replacement regression is needed; the stale assertion is already red. Confirm that `tests/openapi/test_dump_openapi.py:122` remains 39 because it counts routes, not scenarios.

- [x] **Step 2: Update exactly three expectations**

Change only the authority assertion `39 → 40` and the two aggregate strings `scenarios=63 → scenarios=64`. Do not change the transfer count of 24 or other authority gates.

- [x] **Step 3: Verify static inventory agreement**

Run a read-only Python count over `run_bot_authority_contract_with_scenarios` and `run_ownership_sync_contract`: expected `main=32`, `ownership_sync=8`, `authority=40`, `aggregate_with_transfer=64`. Grep the three changed locations to prove they contain 40/64 and no longer 39/63.

- [x] **Step 4: Run CI configuration checks**

Run:

```bash
python3 singlebox/ci/tests/test_unit_test_workflow_diff.py
python3 -m unittest -v \
  singlebox.ci.tests.test_bcs_ci_build_reuse.BcsBuildReuseTest.test_workflow_preserves_contracts_and_coverage_thresholds
```

Expected: PASS. If the unittest is skipped because its declared Cargo tool prerequisites are unavailable, record the skip rather than claiming it passed.

### Task 2: Make Memory owner batch reads snapshot-consistent

**Files:**
- Modify: `apps/bcs/crates/services/bcs-bot-store/src/memory_ownerships_batch.rs`

**Interfaces:**
- Consumes: `MemoryBotRepo::{authority, deleted_bot_ids, bots}`, `MemoryAuthorityState`, `BotOwnershipView`, and `BotRepoPort::soft_delete` in the regression.
- Produces: the unchanged `memory_ownership_projection(&self, &[String]) -> ServiceResult<Vec<Option<BotOwnershipView>>>` contract, with distinct IDs computed once and results expanded in input order.

- [x] **Step 1: Add the failing deterministic regression**

Add `#[cfg(test)] mod tests` beside the implementation with test `repeated_ids_share_one_registry_snapshot`. Seed one initialized Bot with owner `own`; request the same Bot twice. Hold `repo.bots.write()`, spawn the batch, let it park on `bots.read()`, spawn `soft_delete`, let it mark the tombstone and queue on `bots.write()`, then release the initial guard. Extract each result's `owner_user_id` and assert:

```rust
assert_eq!(owners, vec![Some("own".to_string()), Some("own".to_string())]);
```

Await both tasks to avoid leaked work. Use the existing cooperative `yield_now` pattern; do not add sleeps or production test hooks.

- [x] **Step 2: Run the regression before changing implementation**

Run:

```bash
BCS_STATIC_VERSION=1 cargo test --manifest-path apps/bcs/Cargo.toml --locked \
  -p bcs-bot-store repeated_ids_share_one_registry_snapshot -- --nocapture
```

Expected: FAIL on the old implementation, with repeated positions split between `Some("own")` and `None`. If scheduling does not produce that exact red evidence, fix the test choreography rather than accepting a probabilistic test.

- [x] **Step 3: Implement one-snapshot distinct projection**

Keep the early empty return. Acquire `authority.read()`, `deleted_bot_ids.read()`, and `bots.read()` once in that order. Use a `HashSet` for first-occurrence deduplication and a map from each distinct Bot ID to its computed `Option<BotOwnershipView>`.

Replace per-item lock acquisition with lookups through those guards. Preserve all current version, owner-count, Human-owner, missing/deleted, and legacy `created_by` branches. Finish by cloning the computed options in original `bot_ids` order; every distinct ID must have a map entry, while an integrity error returns before result assembly.

- [x] **Step 4: Verify the regression turns green**

Re-run the Step 2 command. Expected: PASS, with both repeated positions carrying `own`, and the queued delete completing afterward.

- [x] **Step 5: Run Memory authority and shared conformance**

Run:

```bash
BCS_STATIC_VERSION=1 cargo test --manifest-path apps/bcs/Cargo.toml --locked -j 2 \
  -p bcs-bot-store --test memory_authority --test conformance_bot_authority
```

Expected: PASS, including empty input, 257 repeated IDs, missing/deleted/version-0 behavior, and duplicate-owner whole-batch errors.

### Task 3: Declare the Rust Service API breaking version

**Files:**
- Modify: `apps/bcs/crates/service-api/bcs-service-api/Cargo.toml:4`
- Modify: `apps/bcs/Cargo.lock` (`bcs-service-api` package version)
- Modify: `apps/bcs/crates/service-api/bcs-service-api/CONTEXT.md`
- Create outside the repository: `/tmp/opencode/pr3893-body-remediation.md`

**Interfaces:**
- Consumes: existing major-version precedent in the crate context and the public `BotCandidate`/authority trait changes already in the PR head.
- Produces: `bcs-service-api` 6.0.0 metadata and a PR body accepted by PR-1/API-1.

- [x] **Step 1: Update the manifest and prove the lock requirement**

Change only `bcs-service-api` to `6.0.0`. Before updating the lockfile, run `cargo metadata --manifest-path apps/bcs/Cargo.toml --locked --format-version 1`; expected: FAIL because the lockfile needs updating. Then change the matching package version in `Cargo.lock` to `6.0.0` without unrelated dependency updates.

- [x] **Step 2: Document the two compatibility surfaces**

Add a `Version 6.0.0` section to the crate context: optional/omitted HTTP `owner_user_id` remains wire-compatible, while the Rust struct field and three required trait methods are breaking. Workspace path consumers rebuild together; there is no Plugin API, schema, route, or configuration migration.

- [x] **Step 3: Build the PR body and verify PR-1**

Append an architecture self-check to the current PR body without deleting its Problem/Solution/Validation sections. Mark Service API and breaking; include exact headings `影响的 consumer`, `影响的 implementation`, `影响的部署 / 配置`, and `迁移 / deprecation 方案`. Include `De-domain test`, `Infrastructure swap test`, and `Business reuse test` with explanations from the spec. Run from `apps/bcs`:

```bash
PR_BODY_FILE=/tmp/opencode/pr3893-body-remediation.md \
BCS_BASE_REF=dev bash scripts/ci/check-pr-gate.sh
```

Expected: PASS with no missing classification, propagation, or design-criteria error.

- [x] **Step 4: Verify locked metadata and Service API tests**

Run:

```bash
cargo metadata --manifest-path apps/bcs/Cargo.toml --locked --format-version 1
BCS_STATIC_VERSION=1 cargo test --manifest-path apps/bcs/Cargo.toml --locked -p bcs-service-api
```

Expected: both PASS. If `cargo public-api --version` is available, also run `check-public-api.sh` with the same body and base environment; otherwise record API-1 as not executed.

### Task 4: Cross-check, record evidence, and update the PR description

**Files:**
- Modify: `apps/bcs/specs/2026-11-11-candidate-owner-batch-fixes/review-remediation.md`
- Remotely modify after validation: PR #3893 description only.

**Interfaces:**
- Consumes: Tasks 1–3 and their actual outputs.
- Produces: an evidence-bearing remediation record and a PR-1-compatible remote description, with local code left uncommitted and unpushed.

- [x] **Step 1: Run affected SQL and HTTP contract tests**

Run:

```bash
BCS_STATIC_VERSION=1 cargo test --manifest-path apps/bcs/Cargo.toml --locked -j 2 \
  -p bcs-edge-permission-store --test authority_reads --test authority_batch_cost
python3 -m pytest apps/bcs/tests/openapi/test_bot_v1_contract.py -q
```

Expected: PASS. Do not relabel the five already reproduced merge-base OpenAPI failures as regressions from this PR.

- [x] **Step 2: Run or explicitly defer the real MySQL gate**

Only if an existing dedicated empty test database is explicitly available, run `authority_mysql_conformance` against it and expect 40 authority + 24 transfer scenarios and the aggregate 64. Otherwise state that live MySQL was not run locally and that unpushed changes cannot receive remote CI validation.

- [x] **Step 3: Check the final diff**

Run `git diff --check`, inspect `git diff --stat` and `git status --short`, and verify every source file modified by this remediation remains at or below 1,000 lines. Do not include the four deferred pre-existing oversized files in this claim.

- [x] **Step 4: Record actual evidence and update the PR body**

Append executed commands, pass/fail/skip results, and remaining gaps to `review-remediation.md`. Preserve the explicit unresolved file-size finding. Once local checks are complete, update PR #3893 with `gh pr edit --body-file /tmp/opencode/pr3893-body-remediation.md`; do not commit or push code.

- [x] **Step 5: Review the delivered state**

Confirm the final PR body on GitHub contains Service API, breaking, propagation, and all three design-criteria phrases. Report local changes, verification evidence, the unpushed/uncommitted state, live MySQL status, and the intentionally unresolved oversized-file issue separately.
