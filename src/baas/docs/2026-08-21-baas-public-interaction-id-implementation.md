# BaaS Public Interaction ID Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a deterministic BaaS-owned interaction ID that BCN and the frontend use for requested/resolved events and resolve requests without BCN session validation.

**Architecture:** Persist `baas_interaction_id` alongside the existing Engine `session_key` and `interaction_id`. Generate it in the transport-agnostic interaction service from canonical JSON plus SHA-256, resolve rows only through the unique public ID, and rewrite only the SSE delivery copy while retaining raw Engine snapshots and Engine dispatch identities.

**Tech Stack:** Python 3.12, dataclasses and service protocols, SQLAlchemy, MySQL/SQLite schema definitions, pytest, Ruff.

---

### Task 1: Define the deterministic public ID contract

**Files:**
- Modify: `src/baas/src/secbaas/community/api/bot_interaction/_models.py`
- Modify: `src/baas/src/secbaas/community/api/bot_interaction/_protocols.py`
- Modify: `src/baas/src/secbaas/community/api/bot_interaction/__init__.py`
- Modify: `src/baas/src/secbaas/community/core/service/bot_interaction/_service.py`
- Test: `src/baas/tests/unit/core/service/test_bot_interaction_service.py`

**Step 1: Write the failing test**

Add tests asserting that `record_requested` returns the exact deterministic
`BAAS-INTERACTION-` value, redelivery returns the same value, and the same Engine
interaction ID in another session produces a different value.

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/unit/core/service/test_bot_interaction_service.py -k 'baas_interaction_id or duplicate_requested'`

Expected: FAIL because the request result and persisted record do not expose a
BaaS interaction ID.

**Step 3: Write minimal implementation**

Add immutable requested/resolved event result dataclasses. Generate the ID with
canonical JSON and `hashlib.sha256(...).hexdigest()[:32]`, then pass it to the
repository when creating the row.

**Step 4: Run test to verify it passes**

Run the command from Step 2 and expect PASS.

### Task 2: Persist and query the public ID

**Files:**
- Modify: `src/baas/src/secbaas/community/core/repository/bot_run_interaction/_record.py`
- Modify: `src/baas/src/secbaas/community/core/repository/bot_run_interaction/_protocol.py`
- Modify: `src/baas/src/secbaas/community/core/repository/bot_run_interaction/_orm_model.py`
- Modify: `src/baas/src/secbaas/community/core/repository/bot_run_interaction/_orm_repository.py`
- Modify: `src/baas/sqls/migrate_baas_bot_run_interaction.sql`
- Create: `src/baas/sqls/migrate_baas_bot_run_interaction_public_id.sql`
- Test: `src/baas/tests/unit/core/repository/bot_run_interaction/test_orm_repository.py`

**Step 1: Write the failing test**

Cover column round-trip, lookup and state transition by public ID, idempotent
redelivery, and unique-public-ID rejection for a different Engine identity.

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/unit/core/repository/bot_run_interaction/test_orm_repository.py`

Expected: FAIL because the model and repository do not have the new field or
public-ID methods.

**Step 3: Write minimal implementation**

Add the required unique ORM column, repository lookup/transition methods, schema
definition, and an additive migration that backfills existing rows with the
previously public Engine ID before enforcing `NOT NULL` and uniqueness.

**Step 4: Run test to verify it passes**

Run the command from Step 2 and expect PASS.

### Task 3: Resolve only by the BaaS ID

**Files:**
- Modify: `src/baas/src/secbaas/community/core/service/bot_interaction/_service.py`
- Modify: `src/baas/src/secbaas/community/core/service/bcn/_bcn_service.py`
- Modify: `src/baas/src/secbaas/community/adapters/web/routers/open_api/message_router.py`
- Test: `src/baas/tests/unit/core/service/test_bot_interaction_service.py`
- Test: `src/baas/tests/unit/core/service/bcn/test_bcn_service.py`
- Test: `src/baas/tests/unit/adapters/web/open_api/test_message_router.py`

**Step 1: Write the failing test**

Assert that resolve accepts only `baas_interaction_id`, finds the row without a
session argument, preserves idempotency behavior, and BCN does not pass or check
its `session_id`.

**Step 2: Run tests to verify they fail**

Run the three focused test files and expect signature/assertion failures showing
the old composite lookup is still in use.

**Step 3: Write minimal implementation**

Change the service contract and adapters to resolve by public ID. Use the row's
stored Engine pair only for later internal dispatch.

**Step 4: Run tests to verify they pass**

Run the focused test files and expect PASS.

### Task 4: Expose the BaaS ID on requested and resolved SSE events

**Files:**
- Modify: `src/baas/src/secbaas/community/core/service/bot_run/_async_chat_client.py`
- Test: `src/baas/tests/unit/core/service/bot_run/test_async_chat_client_coverage.py`
- Test: `src/baas/tests/unit/core/service/sse/test_default_converter.py`

**Step 1: Write the failing test**

Assert that persisted envelopes retain the Engine ID, delivered requested and
resolved envelopes contain the BaaS ID, and Engine websocket dispatch still uses
the Engine ID.

**Step 2: Run tests to verify they fail**

Run the two focused files and expect delivered payload assertions to show the
Engine ID.

**Step 3: Write minimal implementation**

Copy the Engine envelope for delivery and replace public identity fields without
mutating the persisted snapshot. Return the stored BaaS ID from terminal state
transitions for resolved delivery.

**Step 4: Run tests to verify they pass**

Run the focused test files and expect PASS.

### Task 5: Verify contracts and commit

**Files:**
- Modify: `src/baas/docs/2026-08-19-baas-bcn-interaction-sse-design.md`
- Modify: `src/baas/docs/2026-08-20-bcn-interaction-resolve-design.md`
- Verify: all files changed above

**Step 1: Run focused interaction tests**

Run the complete BaaS interaction, BCN router/service, SSE converter, and ORM
test set. Expected: PASS.

**Step 2: Run architecture and static checks**

Run the BaaS architecture protocol checks plus Ruff format/check on changed
Python files. Expected: PASS with no warnings.

**Step 3: Inspect the diff**

Run `git diff --check`, review the schema migration and identity flow, and verify
that no raw interaction payload logging or unrelated changes were introduced.

**Step 4: Commit**

Commit with title `feat(baas): add public interaction ids` and trailer:

```text
Co-authored-by: yuange.zjy <yuange.zjy@gmail.com>
```
