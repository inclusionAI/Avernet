# Permissive Ask-User Answers Implementation Plan

> **Superseded (2026-08-24):** Do not execute this plan. The approved
> values-classification and `allowOther` behavior is documented in
> [BCN Ask-User Custom Values Implementation Plan](../../../baas/docs/2026-08-24-bcn-ask-user-custom-values-implementation.md).

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let BCS accept custom and skipped ask-user answers while preserving structural validation and exact Provider forwarding.

**Architecture:** Normalize malformed `allowOther` values at the BCS interaction boundary, then keep resolution validation transport-agnostic and permissive about answer content. The stored Provider request remains authoritative for question identity and answer augmentation, but options are no longer an answer allowlist.

**Tech Stack:** Rust, Tokio tests, serde_json, tracing, Provider 2.0 protocol Markdown.

---

### Task 1: Add failing BCS interaction tests

**Files:**
- Modify: `src/bcs/crates/services/bcs-interaction/src/management.rs`

**Step 1: Write the failing tests**

Add tests proving that:

- custom values are accepted when `allowOther` is missing or false;
- `[]`, `[""]`, and `["   "]` are accepted and forwarded unchanged;
- a non-boolean `allowOther` is omitted from the stored and published payload.

**Step 2: Run tests to verify RED**

Run:

```bash
cd src/bcs
cargo test -p bcs-interaction ask_user_accepts -- --nocapture
```

Expected: the custom/skip cases fail with the current membership and non-empty
validation errors, and the malformed field remains in the stored payload.

### Task 2: Relax BCS validation and normalize allowOther

**Files:**
- Modify: `src/bcs/crates/services/bcs-interaction/src/management.rs`

**Step 1: Implement the minimum behavior**

- Sanitize non-boolean `questions[].allowOther` before storing/publishing.
- Warn with run ID, interaction ID, and question index only.
- Require every `values` element to be a string but allow empty/blank strings.
- Permit zero or one value for single-select/free-text questions.
- Remove the options membership check entirely.

**Step 2: Run tests to verify GREEN**

Run:

```bash
cd src/bcs
cargo test -p bcs-interaction
```

Expected: all BCS interaction unit and conformance tests pass.

### Task 3: Update the Provider contract

**Files:**
- Modify: `src/bcs/docs/bcs-provider-2.0-sse-protocol.md`

**Step 1: Document the relaxed semantics**

Specify that `allowOther` is a UI hint, missing/malformed values use the
permissive default, options are not an allowlist, and empty/blank strings are
valid skip representations while all question IDs remain required.

**Step 2: Verify formatting and diff hygiene**

Run:

```bash
git diff --check
```

Expected: exit code 0.

### Task 4: Run focused verification

Run:

```bash
cd src/bcs
cargo test -p bcs-interaction
cargo check -p bcs-interaction
```

Expected: all commands exit 0.
