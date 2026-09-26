# Internal Candidate Search Optional Principals Implementation Plan

**Goal:** Align the internal candidate-search OpenAPI Gateway boundary with the
Gateway configuration while preserving BCS Human and perspective authorization.

**Architecture:** `x-avernet-security` expresses Gateway admission only. The
existing `BotServiceImpl` remains the source of real identity and resource
authorization, so no Rust production change is required.

**Tech Stack:** OpenAPI 3.1 YAML, pytest, deterministic JSON snapshot tooling.

---

### Task 1: Lock the intended boundary with a failing contract test

**Files:**
- Modify: `src/bcs/tests/openapi/test_bot_v1_contract.py`

1. Assert candidate search declares User, App, and Bot as optional.
2. Assert its 401 text covers invalid Gateway identities and its 403 text
   covers the BCS Human and perspective policy.
3. Run the focused test and confirm it fails against the current required
   User/App metadata.

### Task 2: Align the source contract and API documentation

**Files:**
- Modify: `src/bcs/api-contracts/v1/openapi/bots.yaml`
- Modify: `src/bcs/api-contracts/README.md`
- Modify: `src/bcs/docs/plans/2026-08-13-openapi-candidate-search-design.md`

1. Change User and App to optional and add optional Bot metadata.
2. State that Gateway admission is permissive while BCS requires Human and
   validates the selected perspective.
3. Run the focused contract test and OpenAPI validator.

### Task 3: Regenerate and lock the Gateway snapshot

**Files:**
- Modify: `src/gateway/configs/schemas/bcn.internal.openapi.json`
- Modify: `src/gateway/tests/unit/scripts/test_gate_and_publish.py`

1. Add a snapshot assertion for all three optional Principal inputs.
2. Regenerate the deterministic internal JSON from `internal.yaml`.
3. Run the Gateway snapshot test, the BCS OpenAPI suite, `git diff --check`,
   and inspect the final diff.
