# Permissive Ask-User Answers Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let the BaaS REL20260821 Provider boundary accept and preserve custom and skipped ask-user values.

**Architecture:** Relax only the typed BCN transport model and Engine-neutral normalization guard. Keep the durable interaction pipeline and Engine request construction unchanged so input values flow through the existing projections without option lookup or rewriting.

**Tech Stack:** Python, Pydantic v2, pytest, BaaS BCN downlink service.

---

### Task 1: Add failing BaaS transport and service tests

**Files:**
- Modify: `src/baas/tests/unit/adapters/web/open_api/test_bcn_router.py`
- Modify: `src/baas/tests/unit/core/service/bcn/test_bcn_service.py`
- Modify: `src/baas/tests/unit/core/service/bot_run/test_interaction_protocol.py`
- Modify: `src/baas/tests/unit/core/service/test_bot_interaction_service.py`
- Modify: `src/baas/tests/e2e/asgi/baseline/test_bcn_downlink_extended.py`

**Step 1: Write the failing tests**

Add coverage proving that empty arrays, empty strings, whitespace-only strings,
and custom values are accepted and preserved. Change the sanitized invalid
request fixture to a non-string value.

**Step 2: Run tests to verify RED**

Run:

```bash
cd src/baas
.venv/bin/pytest \
  tests/unit/adapters/web/open_api/test_bcn_router.py \
  tests/unit/core/service/bcn/test_bcn_service.py \
  tests/e2e/asgi/baseline/test_bcn_downlink_extended.py -q
```

Expected: skip acceptance tests fail at Pydantic or normalization.

### Task 2: Relax BaaS value validation

**Files:**
- Modify: `src/baas/src/secbaas/community/api/bot_interaction/_models.py`
- Modify: `src/baas/src/secbaas/community/adapters/web/routers/bcn_downlink/bcn_model.py`
- Modify: `src/baas/src/secbaas/community/core/service/bcn/_bcn_service.py`

**Step 1: Implement the minimum behavior**

- Remove the `values` minimum-length constraint.
- Remove the non-blank string validator while retaining `list[str]` typing.
- Remove the defensive non-empty/non-blank normalization rejection.
- Allow blank string map values, empty `selectedOptions` groups, and blank
  selected option strings in the durable interaction resolution model.
- Preserve all values exactly in existing output projections.

**Step 2: Run tests to verify GREEN**

Run the Task 1 command. Expected: all selected tests pass.

### Task 3: Update BaaS contract documentation

**Files:**
- Modify: `src/baas/docs/2026-08-20-bcn-interaction-resolve-design.md`

Document empty/blank skip values and the absence of option membership checks.

### Task 4: Run focused verification

Run:

```bash
cd src/baas
.venv/bin/pytest \
  tests/unit/adapters/web/open_api/test_bcn_router.py \
  tests/unit/core/service/bcn/test_bcn_service.py \
  tests/e2e/asgi/baseline/test_bcn_downlink_extended.py -q
```

Then run `git diff --check`. Expected: all commands exit 0.
