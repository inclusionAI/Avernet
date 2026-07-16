# Wave 2: Device Hooks Service

**Priority**: 🔴 Critical. Hooks fire on every device lifecycle operation.
**Target files**: 4. **New groups**: None (extends baseline).
**Estimated phases**: 4.

---

## Phase 2.1: Start Hook Validation Paths

**Goal**: Exercise uncovered paths in `_start_hook_dispatcher.py` (87.5% → 90%+)

**Files to create/modify**:
- `tests/e2e/baseline/test_device_start_hooks.py` (new)

**What to test**:
- Start hook dispatching with null/empty hook configs
- Start hook with retryable errors
- Start hook with non-retryable errors
- Start hook timeout scenarios beyond what's in existing `test_async_device_hooks_timeout.py`

**Verification**:
1. `just test-e2e-full`
2. Check: `_start_hook_dispatcher.py` ≥ 90% in `tmp/coverage/baas-e2e/baseline/htmlcov/index.html`
3. Check: no test failures

---

## Phase 1.2: Deploy Config Edge Cases

**Goal**: Exercise `api/device_manage/_deploy_config.py` validation paths (83.5% → 90%+)

**Files to create/modify**:
- `tests/e2e/baseline/test_deploy_config.py` (new)

**What to test**:
- Deploy config with missing required fields (validation error paths)
- Deploy config with invalid field types
- Deploy config serialization/deserialization
- Deploy config merge with overrides

**Verification**:
1. `just test-e2e-full`
2. Check: `_deploy_config.py` ≥ 90%
3. Check: no test failures

---

## Phase 1.3: Device Facade Config Edge Cases

**Goal**: Exercise `api/device_manage/_device_facade_config.py` (84.9% → 90%+)

**Files to create/modify**:
- `tests/e2e/baseline/test_device_facade_config.py` (new)

**What to test**:
- Facade config with different device types (Arca, K8s, Desktop, Poolab)
- Facade config with partial platform settings
- Facade config validation for unsupported platforms
- Facade config with null optional fields

**Verification**:
1. `just test-e2e-full`
2. Check: `_device_facade_config.py` ≥ 90%
3. Check: no test failures

---

## Phase 1.4: Device Service Error Paths

**Goal**: Exercise uncovered error paths in `_device_service.py` (49.7% → 65%+)

**Files to create/modify**:
- `tests/e2e/baseline/test_device_service_errors.py` (new)

**What to test**:
- Create device with invalid template UUID
- Create device when quota exceeded
- Update device with invalid parameters
- Destroy device that is already destroyed (idempotency)
- Destroy device that is in use
- Device listing with edge-case filters
- Device TTL extension with invalid TTL values

**Verification**:
1. `just test-e2e-full`
2. Check: `_device_service.py` ≥ 65%
3. Check: no test failures

---

## Wave 1 Completion Check

After all 4 phases complete:
1. `just test-e2e-full` — all tests pass
2. `_start_hook_dispatcher.py` ≥ 90%
3. `_deploy_config.py` ≥ 90%
4. `_device_facade_config.py` ≥ 90%
5. `_device_service.py` ≥ 65%