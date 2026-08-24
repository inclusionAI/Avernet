# BCN Ask-User Custom Values Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Classify values-only Frontend ask-user answers in BCS and preserve explicit custom values through BaaS while producing Engine-compatible resolution frames.

**Architecture:** Frontend continues to send only `values`. BCS compares them with stored requested `options[].value`, forwards declared selections in `values` and off-list input in `customValues`, and augments both with stored question metadata. Missing `allowOther` means true; explicit false rejects off-list input with a correlated warning and a clear Frontend error. BaaS normalizes the explicit split into existing durable resolution fields and emits Engine's synthetic `other` selection marker whenever custom input is present.

**Tech Stack:** Rust, serde_json, Python 3.12, Pydantic v2, pytest

---

### Task 1: Evolve the BCS Provider contract

**Files:**
- Modify: `src/bcs/crates/services/bcs-interaction/src/management.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs`
- Modify: `src/bcs/docs/bcs-provider-2.0-sse-protocol.md`

1. Add failing tests showing that BCS partitions a values-only multi-select
   answer into canonical `values` and non-empty `customValues`.
2. Add failing tests showing that missing `allowOther` means true and custom
   input is rejected only when `allowOther=false`.
3. Run the focused `bcs-interaction` tests and confirm the expected failures.
4. Update ask-user validation so Frontend supplies only non-empty `values`,
   single-select cardinality is enforced, and off-list values are rejected only
   by an explicit `allowOther=false`.
5. Partition values by exact requested option value, generate `customValues`,
   and augment the answer with stored `question/header`.
6. Log rejected resolutions with run/session/group/bot/interaction/resolver
   context and preserve the validation message in the Frontend
   `invalid_request` response.
7. Update the Provider 2.0 protocol examples and compatibility rules.
8. Run focused BCS service, WebSocket, and Provider transport tests.

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
