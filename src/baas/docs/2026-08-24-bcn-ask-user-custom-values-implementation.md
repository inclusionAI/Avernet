# BCN Ask-User Custom Values Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Preserve explicitly marked custom ask-user answers from BCN through BCS and BaaS while producing Engine-compatible resolution frames.

**Architecture:** BCN adds optional per-answer `customValues` alongside canonical `values`. BCS validates and forwards both fields with stored question metadata; BaaS normalizes them into existing durable resolution fields and emits Engine's synthetic `other` selection marker whenever custom input is present.

**Tech Stack:** Rust, serde_json, Python 3.12, Pydantic v2, pytest

---

### Task 1: Evolve the BCS Provider contract

**Files:**
- Modify: `src/bcs/crates/services/bcs-interaction/src/management.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs`
- Modify: `src/bcs/docs/bcs-provider-2.0-sse-protocol.md`

1. Add failing tests showing that a multi-select answer may contain canonical
   `values` and non-empty `customValues` when `allowOther=true`.
2. Add a failing test showing that custom input is rejected when
   `allowOther=false`.
3. Run the focused `bcs-interaction` tests and confirm the expected failures.
4. Update ask-user validation so at least one of the two arrays is populated,
   canonical values are always offered values, custom values are explicit and
   allowed only by `allowOther`, and single-select cardinality spans both arrays.
5. Preserve `customValues` while augmenting the answer with stored
   `question/header`.
6. Update the Provider 2.0 protocol examples and compatibility rules.
7. Run focused BCS service and Provider transport tests.

### Task 2: Accept custom values at the BaaS boundary

**Files:**
- Modify: `src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_model.py`
- Modify: `src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_router.py`
- Modify: `src/baas/src/secbaas/community/api/bcn/_models.py`
- Modify: `src/baas/tests/unit/adapters/web/open_api/test_bcn_router.py`
- Modify: `src/baas/tests/e2e/asgi/baseline/test_bcn_downlink_extended.py`

1. Add failing boundary tests for absent, custom-only, and mixed
   `customValues`.
2. Run the focused tests and confirm they fail because the field is not in the
   model/domain input.
3. Add the optional wire field, validate its members as non-empty strings, and
   map it to the transport-independent tuple.
4. Re-run the focused boundary tests.

### Task 3: Normalize into Engine-compatible resolution fields

**Files:**
- Modify: `src/baas/src/secbaas/community/core/service/bcn/_bcn_service.py`
- Modify: `src/baas/tests/unit/core/service/bcn/test_bcn_service.py`
- Modify: `src/baas/tests/unit/core/service/bot_run/test_interaction_protocol.py`

1. Add failing normalization tests for custom-only and mixed multi-select
   answers.
2. Confirm the old implementation incorrectly places free text in
   `selectedOptions`.
3. Render custom values with the `自定义输入: ` prefix and use `("other",)` as
   the Engine selection row whenever custom input exists.
4. Verify the exact serialized Engine request and ensure ordinary answers are
   unchanged.
5. Run all affected BaaS unit and ASGI contract tests.

### Task 4: Final verification

**Files:**
- Review all changed files only; do not run global formatters.

1. Run focused BCS and BaaS tests.
2. Run `git diff --check` and inspect the complete diff for unrelated changes.
3. Document any broader test suite not run and why.
