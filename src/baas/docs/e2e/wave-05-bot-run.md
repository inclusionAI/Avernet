# Wave 5: Bot Run Engine

**Priority**: 🔴 Critical. Heart of the system — 13 files averaging 22% coverage.
**Target files**: 13. **New groups**: 2 (`bot_run_lifecycle`, `bot_run_concurrency`).
**Prerequisite**: None — E2E tests use stub plugins (stub bot_service, stub engine adapters), not real aiohttp. The bot_run engine is testable as-is through HTTP API + stubs.
**Estimated phases**: 7.

---

## File-to-Group Mapping

| File | Group | Rationale |
|------|-------|-----------|
| `_async_chat_client.py` | `bot_run_lifecycle` | Chat lifecycle |
| `_async_session_client.py` | `bot_run_lifecycle` | Session lifecycle |
| `_async_chat_client_pool.py` | `bot_run_lifecycle` | Pool management |
| `_baas_service.py` | `bot_run_lifecycle` | Entry point for bot runs |
| `_bot_websocket_client.py` | `bot_run_lifecycle` | WebSocket connection lifecycle |
| `_claw_service.py` | `bot_run_lifecycle` | Claw bot lifecycle |
| `_runner.py` | `bot_run_lifecycle` | Run orchestration |
| `_executor.py` | `bot_run_concurrency` | Task execution |
| `_worker.py` | `bot_run_concurrency` | Worker pool |
| `_task_concurrency_pool.py` | `bot_run_concurrency` | Concurrency pool |
| `_task_message_dispatcher.py` | `bot_run_concurrency` | Task dispatch |
| `_queue_task_message_dispatcher.py` | `bot_run_concurrency` | Queue dispatch |
| `_bot_concurrency.py` | `bot_run_concurrency` | Bot-level concurrency |

---

## Phase 4.0: Infrastructure Setup — Two New Test Groups + Stub Enhancement

### Step A: Create `bot_run_lifecycle` test group

1. Create `tests/e2e/bot_run_lifecycle/__init__.py`

2. Register marker in `pyproject.toml` under `[tool.pytest.ini_options.markers]`:
```toml
bot_run_lifecycle: marks e2e tests for bot run lifecycle (start → run → stop)
```

3. Add to `test-stages.sh`:
```bash
run_e2e_bot_run_lifecycle() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E Bot run lifecycle tests"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    export SESSION_LABEL="bot-run-lifecycle"
    _start_app "$overlay"
    _run_pytest uv run pytest tests/e2e/bot_run_lifecycle/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "bot_run_lifecycle" \
        --junitxml="$REPORT_DIR/e2e-bot-run-lifecycle.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}
```

### Step B: Create `bot_run_concurrency` test group

1. Create `tests/e2e/bot_run_concurrency/__init__.py`

2. Register marker:
```toml
bot_run_concurrency: marks e2e tests for bot run concurrency patterns (worker, pool, dispatch)
```

3. Add to `test-stages.sh`:
```bash
run_e2e_bot_run_concurrency() {
    local mode="${1:-${_BAAS_MODE:-bare}}"
    local overlay="${2:-${_BAAS_OVERLAY:-e2e-sqlite}}"
    log_sub "E2E Bot run concurrency tests"
    mkdir -p "$REPORT_DIR"
    export _BAAS_MODE="$mode"
    export SESSION_LABEL="bot-run-concurrency"
    _start_app "$overlay"
    _run_pytest uv run pytest tests/e2e/bot_run_concurrency/ -v --durations=0 \
        --log-cli-level=INFO --tb=short \
        -m "bot_run_concurrency" \
        --junitxml="$REPORT_DIR/e2e-bot-run-concurrency.xml" --color=yes
    local rc=$?
    _stop_app
    return $rc
}
```

### Step C: Wire both into `run_e2e_tests()` chain

After `run_e2e_paas_operations`:
```bash
run_e2e_bot_run_lifecycle "$mode" "$overlay" || ec=$((ec + $?))
run_e2e_bot_run_concurrency "$mode" "$overlay" || ec=$((ec + $?))
```

### Step D: Enhance StubBotServicePlugin

In `src/secbaas/community/plugins/bot_service/stub/_stub_plugin.py`:

Add env-var-controlled behavior:
- `BAAS_STUB_BOT_BINDING_PAYLOAD` — JSON string for `get_binding()` return value
- `BAAS_STUB_BOT_BINDING_ERROR` — simulate binding resolution failure
- `BAAS_STUB_BOT_BINDING_NOT_FOUND` — return None for unknown bot

### Step E: Enhance engine adapter stubs

In each `plugins/bot/engine_adapter/{aicoding,hermes,claude_code}/stub/`:

Add env-var-controlled behavior:
- `BAAS_STUB_ENGINE_SESSION_ERROR` — simulate session creation failure
- `BAAS_STUB_ENGINE_SESSION_SLOW` — add delay to session creation (timeout tests)

### Step F: Enhance StubCachePlugin

In `src/secbaas/community/plugins/cache/stub/_stub_cache.py`:

Add stateful behavior:
- In-memory dict for all cache operations (already mostly there if dict-based)
- `get()` returns `None` for unset keys
- `set()` stores with optional TTL support

**Verification**:
1. `just test-e2e-full` — both new groups appear, 0 tests each
2. Check: coverage dumps created at `tmp/coverage/baas-e2e/{bot-run-lifecycle,bot-run-concurrency}/`

---

## Phase 4.1: Bot Run Lifecycle — Start/Create Paths

**Goal**: Exercise `_baas_service.py` (17.5% → 40%+) and `_claw_service.py` (23.7% → 50%+)

**Files to create/modify**:
- `tests/e2e/bot_run_lifecycle/test_bot_start.py` (new)

**What to test**:
- Start bot run with valid params via API
- Start bot run with invalid bot UUID
- Start bot run when bot is already running
- Start bot run with different engine types (aicoding, hermes, claude_code)
- Start bot run with user message included
- Start Claw bot run

**Verification**:
1. `just test-e2e-full`
2. Check: `_baas_service.py` ≥ 40%
3. Check: `_claw_service.py` ≥ 50%
4. Check: no test failures

---

## Phase 4.2: Bot Run Lifecycle — Session + Chat Client

**Goal**: Exercise `_async_session_client.py` (35.4% → 70%+) and `_async_chat_client.py` (14.1% → 40%+)

**Files to create/modify**:
- `tests/e2e/bot_run_lifecycle/test_bot_session.py` (new)

**What to test**:
- Create session client for bot run
- Session client with different session keys
- Session client error handling
- Chat client pool: acquire, release, exhaust (when pool is full)
- Chat client creation with config

**Verification**:
1. `just test-e2e-full`
2. Check: `_async_session_client.py` ≥ 70%
3. Check: `_async_chat_client.py` ≥ 40%
4. Check: no test failures

---

## Phase 4.3: Bot Run Lifecycle — WebSocket + Run Orchestration

**Goal**: Exercise `_bot_websocket_client.py` (16.5% → 50%+) and `_runner.py` (23.0% → 50%+)

**Files to create/modify**:
- `tests/e2e/bot_run_lifecycle/test_bot_runner.py` (new)

**What to test**:
- Create WebSocket client connection
- WebSocket client disconnect/reconnect
- Runner: start, monitor, stop lifecycle
- Runner with engine adapter errors
- Runner status transitions

**Verification**:
1. `just test-e2e-full`
2. Check: `_bot_websocket_client.py` ≥ 50%
3. Check: `_runner.py` ≥ 50%
4. Check: no test failures

---

## Phase 4.4: Bot Run Lifecycle — Error + Edge Paths

**Goal**: Push lifecycle files closer to 90%

**Files to create/modify**:
- `tests/e2e/bot_run_lifecycle/test_bot_run_errors.py` (new)

**What to test**:
- Bot run with binding resolution failure (stub env var)
- Engine adapter session creation failure (stub env var)
- Engine adapter session timeout (stub env var delay)
- Pool exhaustion → error
- Invalid session key → error
- WebSocket client connection failure → retry

**Verification**:
1. `just test-e2e-full`
2. Check: all lifecycle files ≥ 70% target (interim)
3. Check: no test failures

---

## Phase 4.5: Bot Run Concurrency — Worker + Executor

**Goal**: Exercise `_worker.py` (31.1% → 70%+) and `_executor.py` (21.7% → 60%+)

**Files to create/modify**:
- `tests/e2e/bot_run_concurrency/test_worker.py` (new)

**What to test**:
- Worker: pick up task from queue, execute, complete
- Worker: handle task failure, retry
- Worker: graceful shutdown
- Executor: execute bot command
- Executor: handle command timeout

**Verification**:
1. `just test-e2e-full`
2. Check: `_worker.py` ≥ 70%
3. Check: `_executor.py` ≥ 60%
4. Check: no test failures

---

## Phase 4.6: Bot Run Concurrency — Pool + Dispatchers

**Goal**: Exercise `_task_concurrency_pool.py` (32.6% → 70%+), `_task_message_dispatcher.py` (25.0% → 60%+), `_queue_task_message_dispatcher.py` (20.9% → 60%+), `_bot_concurrency.py` (45.8% → 80%+)

**Files to create/modify**:
- `tests/e2e/bot_run_concurrency/test_concurrency.py` (new)

**What to test**:
- Task concurrency pool: acquire, release, semaphore limits
- Bot concurrency: check limits, respect limits
- Task message dispatcher: dispatch to correct worker
- Queue task dispatcher: enqueue, dequeue, priority ordering

**Verification**:
1. `just test-e2e-full`
2. Check: `_task_concurrency_pool.py` ≥ 70%
3. Check: `_task_message_dispatcher.py` ≥ 60%
4. Check: `_queue_task_message_dispatcher.py` ≥ 60%
5. Check: `_bot_concurrency.py` ≥ 80%
6. Check: no test failures

---

## Phase 4.7: Bot Run — Final Push to 90%

**Goal**: Close remaining gaps across all 13 files

**Files to create/modify**:
- `tests/e2e/bot_run_lifecycle/test_bot_run_final.py` (new)
- `tests/e2e/bot_run_concurrency/test_concurrency_final.py` (new)
- Extend existing test files with additional edge cases found during Phase 4.1-4.6

**Verification**:
1. `just test-e2e-full`
2. Check: all 13 bot_run files ≥ 90%
3. Check: no test failures

---

## Wave 4 Completion Check

After all phases complete:
1. `just test-e2e-full` — all tests pass
2. All 13 `core/service/bot_run/*.py` files ≥ 90%
3. Both new test groups produce coverage in `tmp/coverage/baas-e2e/`