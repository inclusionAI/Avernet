# Preserve Legacy Human Review Template Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove PR #514's accidental changes to the shipped Legacy Human review template and its dedicated runtime placeholder while retaining generic V1 channel-aware startup behavior.

**Architecture:** Restore the seed as the authoritative Legacy contract, then delete only the runtime mechanism that exists to support the accidental `$authenticated_human` seed value. Keep generic channel detection in `CollaborationRuntimeService` and the V1 Group facade because it applies to explicitly channel-enabled definitions independently of this template.

**Tech Stack:** Rust, Tokio tests, Serde YAML/JSON, Cargo workspace tests

---

### Task 1: Lock the Legacy seed contract

**Files:**
- Modify: `src/bcs/crates/services/bcs-collaboration-template/src/lib.rs`
- Modify: `src/bcs/crates/tools/bcs-admin/src/seed_loader.rs`

**Step 1: Change the template service test**

Update `returns_bot_human_bot_template_with_human_input_node` to assert:

```rust
assert!(
    detail.definition["runtime"]["state_machine"]
        .get("human_input_channel")
        .is_none()
);
assert!(
    detail.definition["runtime"]["state_machine"]["nodes"]["human_review"]
        .get("assignee")
        .is_none()
);
assert!(
    detail.definition["runtime"]["state_machine"]["nodes"]["human_review"]
        .get("notification")
        .is_none()
);
```

**Step 2: Change the admin seed-loader test**

Apply the same three assertions to the projected `human_review` definition in
`loads_seed_catalog_with_projected_fields`.

**Step 3: Run the focused tests and verify RED**

Run:

```bash
cargo test -p bcs-collaboration-template returns_bot_human_bot_template_with_human_input_node
cargo test -p bcs-admin loads_seed_catalog_with_projected_fields
```

Expected: both tests fail because the current seed still contains
`human_input_channel`, `assignee`, and `notification`.

### Task 2: Restore both Legacy seed files

**Files:**
- Modify: `src/bcs/seeds/collaboration-templates/en-US/bot-human-bot-review.yaml`
- Modify: `src/bcs/seeds/collaboration-templates/zh-CN/bot-human-bot-review.yaml`

**Step 1: Restore original Human review semantics**

Remove `human_input_channel`, `assignee`, and `notification` and restore the
comments describing the current group Human as the responder.

**Step 2: Run the focused tests and verify GREEN**

Run:

```bash
cargo test -p bcs-collaboration-template returns_bot_human_bot_template_with_human_input_node
cargo test -p bcs-admin loads_seed_catalog_with_projected_fields
```

Expected: both tests pass.

### Task 3: Remove the dedicated runtime placeholder

**Files:**
- Modify: `src/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs`
- Modify: `src/bcs/crates/services/bcs-collaboration-runtime/tests/runtime_progression.rs`

**Step 1: Remove placeholder-specific tests**

Delete the test that starts a run by replacing a fixed actor with
`$authenticated_human` and its persisted-placeholder assertions.

**Step 2: Remove production placeholder resolution**

Delete:

```rust
const AUTHENTICATED_HUMAN_ASSIGNEE: &str = "$authenticated_human";
```

Remove the pre-validation call that resolves the placeholder and delete
`resolve_authenticated_human_assignees`.

**Step 3: Verify original unassigned Human behavior**

Run:

```bash
cargo test -p bcs-collaboration-runtime \
  --test runtime_progression \
  human_input_can_start_without_authenticated_human_when_session_has_present_human
```

Expected: pass.

### Task 4: Verify generic V1 channel behavior remains

**Files:**
- No production changes expected.

**Step 1: Run runtime channel configuration regression**

Run:

```bash
cargo test -p bcs-collaboration-runtime \
  --test runtime_progression \
  configure_im_definition_defers_channel_validation_until_run_start
```

Expected: pass.

**Step 2: Run V1 deferred-start regression**

Run:

```bash
cargo test -p bcs-group-v1 \
  --test v1_group_service \
  state_machine_create_defers_initial_run_until_required_channel_is_bound
```

Expected: pass.

### Task 5: Final verification and delivery

**Files:**
- Verify all modified files.

**Step 1: Run affected crate suites**

Run:

```bash
cargo test -p bcs-collaboration-template \
  -p bcs-admin \
  -p bcs-collaboration-runtime \
  -p bcs-group-v1
```

Expected: all tests pass.

**Step 2: Check scope**

Run:

```bash
rg -n '\$authenticated_human|AUTHENTICATED_HUMAN_ASSIGNEE|resolve_authenticated_human_assignees' \
  src/bcs/crates src/bcs/seeds
git diff --check
git status --short
```

Expected: no placeholder occurrences in production, test, or seed paths; no
whitespace errors; and only intended files are modified.

**Step 3: Commit and push**

```bash
git add <intended files>
git commit -m "fix(bcs): preserve legacy human review template"
git push --no-verify
```
