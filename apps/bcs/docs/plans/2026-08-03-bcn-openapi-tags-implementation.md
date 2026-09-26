# BCN OpenAPI Tag Grouping Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Group all BCN operations into five collaboration-scoped sections in Gateway's Swagger UI.

**Architecture:** Add operation tags at the authoritative BCS YAML fragments, publish them in the deterministic BCN artifact, and teach Gateway's existing OpenAPI aggregation function to retain top-level tag metadata. Keep route behavior, schemas, and security requirements unchanged.

**Tech Stack:** OpenAPI 3.1 YAML/JSON, Python 3.12, pytest, unittest.

---

### Task 1: Specify exported BCN tag coverage

**Files:**
- Modify: `src/bcs/tests/openapi/test_dump_openapi.py`
- Modify: `src/bcs/api-contracts/v1/openapi.yaml`
- Modify: `src/bcs/api-contracts/v1/openapi/*.yaml`

1. Add a failing exporter assertion that every operation has exactly one
   approved tag and that the root contract declares the five tags in order.
2. Run the focused exporter test and confirm it fails because tags are absent.
3. Add the five root declarations and operation-level tags, assigning both
   connection operations to `Collaboration / Sessions`.
4. Re-run the exporter test and contract validation.

### Task 2: Preserve tag metadata in Gateway aggregation

**Files:**
- Modify: `src/gateway/tests/test_served_openapi.py`
- Modify: `src/gateway/src/gateway/community/core/forwarding/_openapi.py`

1. Add a failing test for ordered, name-based de-duplication of top-level tags.
2. Run the focused Gateway test and confirm the served document omits tags.
3. Merge tag definitions while iterating domain descriptions and include them
   in the served document when non-empty.
4. Re-run the focused Gateway test.

### Task 3: Publish and verify the generated artifact

**Files:**
- Modify: `src/gateway/configs/schemas/bcn.openapi.json`
- Modify: `src/gateway/tests/test_dump_and_publish_script.py`

1. Require the dry-run artifact to contain the five tag declarations and
   verify the connection operations use the Session tag.
2. Run the test and confirm it fails against the current contract.
3. Generate and publish the deterministic BCN artifact through the existing
   dump-and-publish path.
4. Run focused BCS and Gateway tests plus `git diff --check`.
