# Optional Exec Interaction Command Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow Provider 2.0 `exec` interaction requests to omit `command` without losing the approval event.

**Architecture:** Keep normalization at the BaaS delivery adapter and payload validation in the BCS interaction service. BaaS omits an unusable optional value; BCS accepts absence but rejects a present malformed value. Existing interaction identity, options, sequencing, storage, and publication behavior stays unchanged.

**Tech Stack:** Python 3, pytest, Rust, Tokio, Cargo.

---

## Task 1: Make BaaS conversion tolerant of an absent command

**Files:**

- Modify: `src/baas/tests/unit/core/service/sse/test_default_converter.py`
- Modify: `src/baas/src/secbaas/community/core/service/sse/_default_converter.py`
- Modify: `src/baas/docs/2026-08-19-baas-bcn-interaction-sse-design.md`

1. Change the converter unit test to require missing, null, non-string, empty, and whitespace-only commands to produce an interaction without a `command` field.
2. Run the focused test and confirm it fails against the required-command implementation.
3. Change `_transform_exec_requested` to copy only a non-empty string command and otherwise continue conversion silently.
4. Run the focused test and the complete default-converter test file.
5. Update the BaaS contract/design document to mark command optional and describe omission behavior.
6. Run `git diff --check`, review the branch diff, and commit the BaaS change.

## Task 2: Make BCS validation optional-but-strict-when-present

**Files:**

- Modify: `src/bcs/crates/services/bcs-interaction/src/management.rs`
- Modify: `src/bcs/crates/services/bcs-interaction/tests/conformance_interaction.rs`
- Modify: `src/bcs/docs/bcs-provider-2.0-sse-protocol.md`

1. Add service/conformance coverage proving an exec request without command is accepted, stored, and published, while an explicitly malformed command remains rejected.
2. Run the narrow tests and confirm the command-less case fails against current validation.
3. Change exec requested-payload validation to call the non-empty-string validator only when the `command` key is present.
4. Run the narrow tests and the complete `bcs-interaction` package tests.
5. Update the Provider 2.0 protocol document with the optional field and present-value constraint.
6. Run `git diff --check`, review the branch diff, and commit the BCS change.

## Task 3: Verify both release branches

1. Re-run the complete BaaS converter test file on the `REL20260821`-based branch.
2. Re-run `cargo test -p bcs-interaction` on the `dev`-based branch.
3. Confirm both worktrees are clean and report branch names, base commits, change commits, and deployment ordering.
