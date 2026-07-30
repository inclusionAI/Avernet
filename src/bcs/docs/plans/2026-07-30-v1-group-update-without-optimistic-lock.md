# V1 Group Update Without Optimistic Lock Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Remove the version-based optimistic locking introduced for V1 Group
updates while preserving field-scoped persistence, storage-error propagation,
persisted-response projection, and visibility-guarded participant insertion.

**Architecture:** Route V1 updates through the existing
`patch_mutable_fields` operation and reload the Group through `try_get`.
Remove the versioned patch contract from the application/core/repository
layers. Keep `Group.version` as an existing domain field, but do not change it
from V1 field patches or visibility-guarded participant insertion.

**Tech Stack:** Rust, async-trait, Tokio, Cargo workspace tests.

---

### Task 1: Pin the no-version-change behavior

**Files:**

- Modify:
  `src/bcs/crates/services/bcs-group-v1/tests/v1_group_service.rs`
- Modify:
  `src/bcs/crates/services/bcs-group-store/src/lib.rs`

**Step 1: Write the failing V1 service assertion**

Capture the seeded Group version in
`update_preserves_hidden_legacy_routing_fields` and assert that both the
persisted Group and returned detail retain that version after the update.

**Step 2: Write the failing SQL assertion**

Change the visibility-guarded participant SQL test to assert that the Group
update statement does not contain `version = version + 1`, while retaining the
visibility and duplicate-participant guards.

**Step 3: Run tests to verify RED**

Run:

```bash
cargo test -p bcs-group-v1 --test v1_group_service update_preserves_hidden_legacy_routing_fields -- --exact
cargo test -p bcs-group-store guarded_participant_insert_preserves_group_version
```

Expected: both tests fail because the current implementation increments the
Group version.

### Task 2: Remove the versioned patch contract and implementation

**Files:**

- Modify:
  `src/bcs/crates/service-api/bcs-service-api/src/port/repo/group.rs`
- Modify:
  `src/bcs/crates/service-api/bcs-service-api/src/core/group.rs`
- Modify:
  `src/bcs/crates/services/bcs-group/src/core/group_core.rs`
- Modify:
  `src/bcs/crates/services/bcs-group-store/src/lib.rs`
- Modify:
  `src/bcs/crates/services/bcs-group-store/src/memory.rs`
- Modify:
  `src/bcs/crates/services/bcs-group-v1/src/lib.rs`

**Step 1: Remove the versioned method**

Delete `patch_mutable_fields_if_version` from both service contracts and from
the core, database, and memory implementations.

**Step 2: Route V1 update through the existing patch**

Call `patch_mutable_fields`, then reload with `try_get`. Map both operations
through the existing V1 storage error mapping and return `GroupNotFound` when
the reload has no row.

**Step 3: Stop participant insertion from changing Group version**

Remove `version = version + 1` from the database visibility-guard update and
remove the corresponding in-memory increment. Keep timestamp updates and the
existing visibility/duplicate checks.

**Step 4: Run the focused tests to verify GREEN**

Re-run both commands from Task 1 and expect PASS.

### Task 3: Remove obsolete optimistic-lock tests

**Files:**

- Modify:
  `src/bcs/crates/services/bcs-group-store/src/lib.rs`
- Modify:
  `src/bcs/crates/services/bcs-group-store/tests/conformance_group_repo.rs`

**Step 1: Remove obsolete CAS coverage**

Delete the SQL compare-and-swap test and the repository test that expects a
stale version conflict.

**Step 2: Preserve relevant repository coverage**

Keep coverage showing that a public Group rejects a protected Bot through
`add_participant_with_visibility_guard`, without asserting a version change.

**Step 3: Verify no new optimistic-lock symbols remain**

Run:

```bash
rg -n 'patch_mutable_fields_if_version|expected_version|version = version \+ 1' \
  src/bcs/crates/service-api/bcs-service-api/src/{core,port/repo}/group.rs \
  src/bcs/crates/services/bcs-group \
  src/bcs/crates/services/bcs-group-store \
  src/bcs/crates/services/bcs-group-v1
```

Expected: no matches.

### Task 4: Regression verification

**Files:**

- Verify all files changed above.

**Step 1: Run focused crate suites**

Run:

```bash
cargo test -p bcs-group-store
cargo test -p bcs-group-v1
cargo test -p bcs-group
cargo test -p bcs-service-api
```

Expected: all tests pass.

**Step 2: Run static checks**

Run:

```bash
cargo check -p bcs-group-v1 -p bcs-group-store -p bcs-group -p bcs-service-api
git diff --check
```

Expected: all checks pass and the diff has no whitespace errors.

**Step 3: Review and commit**

Inspect the complete diff against the confirmed design, commit only the
intended files, and report the commit and push status.
