# Wave 3: Publish Manage

**Priority**: 🔴 Critical. `_publish_service.py` has 349 untested lines on 1367 total (74.5%).
**Target files**: 5. **New groups**: None (extends baseline).
**Estimated phases**: 5.

---

## Phase 2.1: Publish Approval/Denial Flows

**Goal**: Exercise publish approval logic in `_publish_service.py` (74.5% → 80%+)

**Files to create/modify**:
- `tests/e2e/baseline/test_publish_approval.py` (new)

**What to test**:
- Auto-approve publish when threshold met (already partially covered — extend)
- Manual approve flow (admin endpoint)
- Manual deny flow
- Approve with invalid publish ID
- Deny with invalid publish ID
- Approve already-approved publish (idempotency)
- Deny already-denied publish (idempotency)

**Verification**:
1. `just test-e2e-full`
2. Check: `_publish_service.py` ≥ 80%
3. Check: no test failures

---

## Phase 2.2: Publish with No Devices / Zero Scale

**Goal**: Exercise edge cases where publish has 0 target devices

**Files to create/modify**:
- `tests/e2e/baseline/test_publish_no_devices.py` (already exists — extend)

**What to test**:
- Publish to bot with no active devices
- Publish with scale=0
- Publish with all devices in failed state
- Publish to bot with template mismatch

**Verification**:
1. `just test-e2e-full`
2. Check: `_publish_service.py` ≥ 83%
3. Check: no test failures

---

## Phase 2.3: Batch Publish Edge Cases

**Goal**: Exercise `_publish_batch/_orm_repository.py` (82.5% → 90%+) and batch publish code paths

**Files to create/modify**:
- `tests/e2e/baseline/test_publish_batch.py` (new)

**What to test**:
- Batch publish with multiple bots
- Batch publish with partial success (some bots fail)
- Batch publish record queries (list, filter by status, paginate)
- Batch publish with empty bot list (validation)

**Verification**:
1. `just test-e2e-full`
2. Check: `_publish_batch/_orm_repository.py` ≥ 90%
3. Check: `_publish_service.py` ≥ 85%
4. Check: no test failures

---

## Phase 2.4: Publish Record Repository

**Goal**: Exercise `publish_record/_orm_repository.py` query paths (72.1% → 90%+)

**Files to create/modify**:
- `tests/e2e/baseline/test_publish_records.py` (new)

**What to test**:
- List publish records with filters (bot_id, status, time range)
- Paginate publish records (offset, limit)
- Query publish record by ID
- Publish record status transitions
- Upload fail count tracking

**Verification**:
1. `just test-e2e-full`
2. Check: `publish_record/_orm_repository.py` ≥ 90%
3. Check: no test failures

---

## Phase 2.5: Admin Service + Publish Repository

**Goal**: Exercise `_admin_service.py` (82.4% → 90%+) and `publish/_orm_repository.py` (81.3% → 90%+)

**Files to create/modify**:
- `tests/e2e/baseline/test_publish_admin.py` (new)

**What to test**:
- Admin force-cancel publish
- Admin force-retry publish
- List publishes with admin filters
- Publish repository: find by ID, list by status, count

**Verification**:
1. `just test-e2e-full`
2. Check: `_admin_service.py` ≥ 90%
3. Check: `publish/_orm_repository.py` ≥ 90%
4. Check: `_publish_service.py` ≥ 90%
5. Check: no test failures

---

## Wave 2 Completion Check

After all 5 phases complete:
1. `just test-e2e-full` — all tests pass
2. `_publish_service.py` ≥ 90%
3. `_admin_service.py` ≥ 90%
4. `publish_record/_orm_repository.py` ≥ 90%
5. `publish/_orm_repository.py` ≥ 90%
6. `publish_batch/_orm_repository.py` ≥ 90%