# Durable Teclaw Publish Status Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Teclaw's process-local publish-status reconciler with a DB-backed `TaskQueueService` task that survives Backend and worker restarts.

**Architecture:** `TeclawProvisionService` persists the binding, then enqueues `teclaw.create.publish_poll`. A bot-management-owned handler validates the current binding on every at-least-once delivery, polls BaaS once, persists terminal state to both bot and binding, and returns `Reschedule`, `Retry`, or `Complete` to the shared worker. A profile-independent lifecycle registers the handler before the task worker starts.

**Tech Stack:** Python 3.12, Injector, pytest, SQLAlchemy-backed task queue, existing `LifecycleBase` bootstrap discovery.

## Global Constraints

- Preserve the existing 600-second behavior: a publish that is still non-terminal after one final poll remains `PENDING` for compatibility.
- On durable task enqueue failure, mark both the bot and binding `FAILED`, matching the regular BaaS create lifecycle.
- A terminal transition completes only after both bot and binding writes succeed; persistence failures retry and are never reported as success.
- Duplicate delivery, lease reclaim, a missing binding, a terminal binding, a non-Teclaw binding, and a mismatched `publish_id` must not mutate current state.
- Corp, singlebox, and test use the same handler and lifecycle; only shared task-queue infrastructure configuration differs.
- Remove `TeclawStatusReconciler`, its daemon scheduler, and every profile-specific no-op reconciler provider.
- Do not destroy the remote BaaS bot when task enqueue fails.
- Do not change the regular BaaS create or restart lifecycle.
- Use required, non-optional constructor dependencies for `TaskQueueService`, `BotRepository`, and `DeviceBindingRepository`.

---

### Task 1: Add the durable Teclaw publish task handler

**Files:**
- Create: `src/backend/src/agentclaw/community/core/bot_management/services/teclaw_publish_task_handler.py`
- Create: `src/backend/tests/community/core/bot_management/services/test_teclaw_publish_task_handler.py`
- Modify: `src/backend/tests/community/core/task_queue/test_task_worker.py`

**Interfaces:**
- Consumes: `BaasService.get_publish_progress(publish_id: int)`, `BotRepository.update_by_owner(bot_id: str, owner_id: str, update_data: dict)`, `DeviceBindingRepository.get_by_id(binding_id: int)` and `update_status(binding_id: int, status: str)`, `HandlerRegistry.register(handler)`.
- Produces: `TECLAW_CREATE_PUBLISH_POLL_TASK`, `TECLAW_PUBLISH_TASK_DEADLINE_SECONDS`, `build_teclaw_publish_poll_payload(binding_id, bot_id, owner_id, publish_id, started_at_epoch_s)`, `map_publish_status(publish_status)`, `TeclawPublishTaskHandler`, and `TeclawPublishTaskLifecycle`.

- [ ] **Step 1: Write failing handler contract tests**

Create the new test module with a real `DeviceBindingRecord` fixture and tests for payload construction, stale guards, status mapping, retry/reschedule/timeout decisions, dual persistence, partial-write convergence, and lifecycle registration. The core setup and assertions are:

```python
from datetime import datetime
from unittest.mock import MagicMock

import asyncio
import pytest

from agentclaw.community.core.bot_management.services.teclaw_publish_task_handler import (
    TECLAW_CREATE_PUBLISH_POLL_TASK,
    TeclawPublishTaskHandler,
    TeclawPublishTaskLifecycle,
    build_teclaw_publish_poll_payload,
    map_publish_status,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, Retry


def _binding(
    *,
    status: str = "PENDING",
    provider: str = "teclaw",
    publish_id: int | str = 9,
) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=77,
        entity_id="staff-1",
        entity_type="staff",
        device_id="BOT-x",
        device_provider=provider,
        env="dev",
        device_props={"publish_id": publish_id},
        status=status,
        apply_reason=None,
        applied_by="u1",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )


def _payload(**updates) -> dict:
    payload = build_teclaw_publish_poll_payload(
        binding_id=77,
        bot_id="b1",
        owner_id="u1",
        publish_id=9,
        started_at_epoch_s=100.0,
    )
    payload.update(updates)
    return payload


def _handler(*, clock=lambda: 200.0):
    baas = MagicMock()
    bot_repo = MagicMock()
    bot_repo.update_by_owner.return_value = {"status": "PENDING"}
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = _binding()
    handler = TeclawPublishTaskHandler(
        baas_service=baas,
        bot_repository=bot_repo,
        device_binding_repo=binding_repo,
        poll_delay_seconds=10.0,
        clock=clock,
    )
    return handler, baas, bot_repo, binding_repo


@pytest.mark.parametrize(
    "publish_status,expected",
    [
        ("SUCCESS", "ACTIVE"),
        ("FAILED", "FAILED"),
        ("REJECTED", "FAILED"),
        ("REVOKED", "FAILED"),
        ("PENDING", "PENDING"),
        (None, "PENDING"),
    ],
)
def test_map_publish_status(publish_status, expected):
    assert map_publish_status(publish_status) == expected


def test_pending_publish_reschedules_before_timeout():
    handler, baas, bot_repo, binding_repo = _handler(clock=lambda: 699.0)
    baas.get_publish_progress.return_value = {"status": "PENDING"}

    assert handler.handle(_payload()) == Reschedule(10.0)
    bot_repo.update_by_owner.assert_not_called()
    binding_repo.update_status.assert_not_called()


def test_timeout_polls_once_then_preserves_pending():
    handler, baas, bot_repo, binding_repo = _handler(clock=lambda: 700.0)
    baas.get_publish_progress.return_value = {"status": "PENDING"}

    assert handler.handle(_payload()) == Complete()
    baas.get_publish_progress.assert_called_once_with(9)
    bot_repo.update_by_owner.assert_not_called()
    binding_repo.update_status.assert_not_called()


@pytest.mark.parametrize(
    "publish_status,stored_status",
    [
        ("SUCCESS", "ACTIVE"),
        ("FAILED", "FAILED"),
        ("REJECTED", "FAILED"),
        ("REVOKED", "FAILED"),
    ],
)
def test_terminal_publish_persists_bot_then_binding(publish_status, stored_status):
    handler, baas, bot_repo, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": publish_status}

    assert handler.handle(_payload()) == Complete()
    bot_repo.update_by_owner.assert_called_once_with(
        "b1", "u1", {"status": stored_status}
    )
    binding_repo.update_status.assert_called_once_with(
        binding_id=77, status=stored_status
    )


def test_terminal_publish_still_converges_after_business_timeout():
    handler, baas, bot_repo, binding_repo = _handler(clock=lambda: 900.0)
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}

    assert handler.handle(_payload()) == Complete()
    bot_repo.update_by_owner.assert_called_once_with(
        "b1", "u1", {"status": "ACTIVE"}
    )
    binding_repo.update_status.assert_called_once_with(
        binding_id=77, status="ACTIVE"
    )


def test_bot_write_failure_retries_before_binding_write():
    handler, baas, bot_repo, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    bot_repo.update_by_owner.side_effect = RuntimeError("bot db down")

    outcome = handler.handle(_payload())

    assert isinstance(outcome, Retry)
    binding_repo.update_status.assert_not_called()


def test_partial_terminal_write_retries_until_binding_converges():
    handler, baas, bot_repo, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    binding_repo.update_status.side_effect = [RuntimeError("db down"), None]

    first = handler.handle(_payload())
    second = handler.handle(_payload())

    assert isinstance(first, Retry)
    assert second == Complete()
    assert bot_repo.update_by_owner.call_count == 2
    assert binding_repo.update_status.call_count == 2


def test_transient_publish_query_returns_retry():
    handler, baas, bot_repo, binding_repo = _handler()
    baas.get_publish_progress.side_effect = RuntimeError("baas down")

    outcome = handler.handle(_payload())

    assert isinstance(outcome, Retry)
    bot_repo.update_by_owner.assert_not_called()
    binding_repo.update_status.assert_not_called()


def test_lifecycle_registers_handler():
    registry = HandlerRegistry()
    lifecycle = TeclawPublishTaskLifecycle(
        registry=registry,
        baas_service=MagicMock(),
        bot_repository=MagicMock(),
        device_binding_repo=MagicMock(),
    )

    asyncio.run(lifecycle.bootstrap())

    assert isinstance(
        registry.get(TECLAW_CREATE_PUBLISH_POLL_TASK),
        TeclawPublishTaskHandler,
    )
```

Add the stale-delivery and invalid-payload cases with explicit mutations:

```python
@pytest.mark.parametrize(
    "binding",
    [
        None,
        _binding(status="ACTIVE"),
        _binding(status="FAILED"),
        _binding(status="RELEASED"),
        _binding(provider="baas"),
        _binding(publish_id=10),
    ],
)
def test_stale_or_terminal_binding_completes_without_polling(binding):
    handler, baas, bot_repo, binding_repo = _handler()
    binding_repo.get_by_id.return_value = binding

    assert handler.handle(_payload()) == Complete()
    baas.get_publish_progress.assert_not_called()
    bot_repo.update_by_owner.assert_not_called()
    binding_repo.update_status.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        _payload(binding_id=True),
        _payload(bot_id=7),
        _payload(owner_id=7),
        _payload(publish_id="9"),
        _payload(started_at_epoch_s="100"),
    ],
)
def test_invalid_payload_fails_before_repository_access(payload):
    handler, baas, _, binding_repo = _handler()

    outcome = handler.handle(payload)

    assert isinstance(outcome, Fail)
    assert outcome.error.startswith("invalid payload:")
    binding_repo.get_by_id.assert_not_called()
    baas.get_publish_progress.assert_not_called()


def test_binding_read_failure_returns_retry():
    handler, baas, _, binding_repo = _handler()
    binding_repo.get_by_id.side_effect = RuntimeError("binding db down")

    outcome = handler.handle(_payload())

    assert isinstance(outcome, Retry)
    assert "binding db down" in outcome.error
    baas.get_publish_progress.assert_not_called()
```

Extend the real-SQLite worker integration suite with a reclaimed Teclaw task.
The first worker claims the row with an immediately expired lease and
disappears; a newly constructed worker must reclaim the same persisted row and
run the Teclaw handler:

```python
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.services.teclaw_publish_task_handler import (
    TECLAW_CREATE_PUBLISH_POLL_TASK,
    TeclawPublishTaskHandler,
    build_teclaw_publish_poll_payload,
)


def test_teclaw_publish_task_is_reclaimed_after_worker_restart():
    w = _world(lease_seconds=0)
    baas = MagicMock()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    bot_repo = MagicMock()
    bot_repo.update_by_owner.return_value = {"status": "ACTIVE"}
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = SimpleNamespace(
        status="PENDING",
        device_provider="teclaw",
        device_props={"publish_id": 9},
    )
    w.registry.register(
        TeclawPublishTaskHandler(
            baas_service=baas,
            bot_repository=bot_repo,
            device_binding_repo=binding_repo,
        )
    )
    record = w.enqueue(
        TECLAW_CREATE_PUBLISH_POLL_TASK,
        build_teclaw_publish_poll_payload(
            binding_id=77,
            bot_id="b1",
            owner_id="u1",
            publish_id=9,
            started_at_epoch_s=time.time(),
        ),
    )
    abandoned = w.repo.claim_batch(
        worker_id="dead-worker",
        env=ENV,
        limit=1,
        lease_seconds=0,
    )
    assert [task.id for task in abandoned] == [record.id]

    restarted_worker = TaskWorker(w.repo, w.registry, w.config)
    asyncio.run(restarted_worker.run_once())

    assert w.status_of(record.id) == TaskStatus.SUCCEEDED
    bot_repo.update_by_owner.assert_called_once_with(
        "b1", "u1", {"status": "ACTIVE"}
    )
    binding_repo.update_status.assert_called_once_with(
        binding_id=77, status="ACTIVE"
    )
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
cd src/backend
uv run pytest -q \
  tests/community/core/bot_management/services/test_teclaw_publish_task_handler.py \
  tests/community/core/task_queue/test_task_worker.py
```

Expected: collection fails with `ModuleNotFoundError` for `teclaw_publish_task_handler`.

- [ ] **Step 3: Implement the minimal task contract and handler**

Create `teclaw_publish_task_handler.py` with these public constants and shapes:

```python
TECLAW_CREATE_PUBLISH_POLL_TASK = "teclaw.create.publish_poll"
TECLAW_PUBLISH_TASK_DEADLINE_SECONDS = 86400
_PUBLISH_POLL_TIMEOUT_SECONDS = 600.0
_PUBLISH_PROGRESS_TRANSIENT_ERROR = "get_publish_progress transient error"


def build_teclaw_publish_poll_payload(
    *,
    binding_id: int,
    bot_id: str,
    owner_id: str,
    publish_id: int,
    started_at_epoch_s: float,
) -> dict:
    return {
        "binding_id": binding_id,
        "bot_id": bot_id,
        "owner_id": owner_id,
        "publish_id": publish_id,
        "started_at_epoch_s": started_at_epoch_s,
    }


def map_publish_status(publish_status: str | None) -> str:
    normalized = (publish_status or "").strip().upper()
    if normalized == "SUCCESS":
        return DeviceBindingStatus.ACTIVE.value
    if normalized in {"FAILED", "REJECTED", "REVOKED"}:
        return DeviceBindingStatus.FAILED.value
    return DeviceBindingStatus.PENDING.value
```

Implement strict `_require_int`, `_require_str`, and `_require_number` parsers matching the BaaS handler's bool rejection. Implement `TeclawPublishTaskHandler.handle` in this order:

```python
def handle(self, payload: dict | None) -> TaskOutcome:
    try:
        binding_id = _require_int(payload, "binding_id")
        bot_id = _require_str(payload, "bot_id")
        owner_id = _require_str(payload, "owner_id")
        publish_id = _require_int(payload, "publish_id")
        started_at_epoch_s = _require_number(payload, "started_at_epoch_s")
    except ValueError as exc:
        return Fail(f"invalid payload: {exc}")

    try:
        binding = self._device_binding_repo.get_by_id(binding_id)
    except Exception as exc:
        return Retry(f"load Teclaw binding failed: {exc}")

    if binding is None:
        return Complete()
    if binding.status in {
        DeviceBindingStatus.ACTIVE.value,
        DeviceBindingStatus.FAILED.value,
        DeviceBindingStatus.RELEASED.value,
    }:
        return Complete()
    if binding.device_provider != TECLAW_DEVICE_PROVIDER:
        return Complete()
    current_publish_id = (binding.device_props or {}).get("publish_id")
    if current_publish_id is None or str(current_publish_id) != str(publish_id):
        return Complete()

    try:
        progress = self._baas.get_publish_progress(publish_id)
    except Exception as exc:
        logger.warning(
            "[TeclawPublishTaskHandler] publish query failed: "
            "publish_id=%s error=%s",
            publish_id,
            exc,
        )
        return Retry(_PUBLISH_PROGRESS_TRANSIENT_ERROR)

    status = map_publish_status((progress or {}).get("status"))
    if status in {
        DeviceBindingStatus.ACTIVE.value,
        DeviceBindingStatus.FAILED.value,
    }:
        return self._persist_terminal(
            bot_id=bot_id,
            owner_id=owner_id,
            binding_id=binding_id,
            status=status,
        )
    if (self._clock() - started_at_epoch_s) >= _PUBLISH_POLL_TIMEOUT_SECONDS:
        return Complete()
    return Reschedule(self._poll_delay_seconds)
```

`_persist_terminal` must update the bot first, reject `None` from `update_by_owner`, update the binding second, return `Retry` on either write exception, and return `Complete` only after both calls finish.

Implement `TeclawPublishTaskLifecycle(LifecycleBase)` with required constructor arguments `registry`, `baas_service`, `bot_repository`, and `device_binding_repo`; its `bootstrap()` registers one `TeclawPublishTaskHandler`.

- [ ] **Step 4: Run the handler tests and verify GREEN**

Run:

```bash
cd src/backend
uv run pytest -q \
  tests/community/core/bot_management/services/test_teclaw_publish_task_handler.py \
  tests/community/core/task_queue/test_task_worker.py
```

Expected: all handler tests pass.

- [ ] **Step 5: Commit the durable handler**

```bash
git add src/backend/src/agentclaw/community/core/bot_management/services/teclaw_publish_task_handler.py src/backend/tests/community/core/bot_management/services/test_teclaw_publish_task_handler.py src/backend/tests/community/core/task_queue/test_task_worker.py
git commit -m "feat(teclaw): add durable publish polling task" -m "Poll Teclaw publish status through TaskQueueService-compatible handler outcomes and stale-task guards. Preserve the existing 600-second behavior: non-terminal publishes remain PENDING for forward compatibility."
```

---

### Task 2: Enqueue after binding persistence and fail both records on enqueue errors

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_management/services/teclaw_provision_service.py`
- Modify: `src/backend/tests/community/core/bot_management/services/test_teclaw_provision_service.py`

**Interfaces:**
- Consumes: Task 1's task type, payload builder, deadline constant, plus `TaskQueueService.enqueue(task_type, payload, deadline_seconds)`.
- Produces: `TeclawProvisionService` with required `task_queue_service` and `bot_repository` dependencies; provisioning returns `PENDING` after enqueue success or `FAILED` after a successfully persisted enqueue-failure transition.

- [ ] **Step 1: Replace reconciler assertions with failing queue assertions**

Change `_make_service` to create and inject `task_queue_service` and `bot_repository`, setting `bot_repository.update_by_owner.return_value` to a bot dict. In the happy-path test, freeze the module clock and assert exact durable work:

```python
with patch(
    "agentclaw.community.core.bot_management.services.teclaw_provision_service.time.time",
    return_value=123.0,
):
    result = svc.provision(
        bot=_BOT,
        owner_id="u1",
        agent_pass_token="passport-token",
    )

task_queue_service.enqueue.assert_called_once_with(
    "teclaw.create.publish_poll",
    {
        "binding_id": 77,
        "bot_id": "b1",
        "owner_id": "u1",
        "publish_id": 9,
        "started_at_epoch_s": 123.0,
    },
    deadline_seconds=86400,
)
assert result.status == "PENDING"
```

Add an ordering assertion by making `insert_binding` verify `task_queue_service.enqueue` has not yet been called, then checking enqueue occurred after the insert.

Add enqueue-failure tests:

```python
def test_enqueue_failure_marks_bot_and_binding_failed_without_destroying_remote():
    svc, baas, _, binding_repo, task_queue_service, bot_repo = _make_service()
    task_queue_service.enqueue.side_effect = RuntimeError("queue down")

    result = svc.provision(
        bot=_BOT,
        owner_id="u1",
        agent_pass_token="passport-token",
    )

    assert result.status == "FAILED"
    bot_repo.update_by_owner.assert_called_once_with(
        "b1", "u1", {"status": "FAILED"}
    )
    binding_repo.update_status.assert_called_once_with(
        binding_id=77, status="FAILED"
    )
    baas.destroy_bot.assert_not_called()


def test_enqueue_failure_propagates_bot_write_failure():
    svc, _, _, binding_repo, task_queue_service, bot_repo = _make_service()
    task_queue_service.enqueue.side_effect = RuntimeError("queue down")
    bot_repo.update_by_owner.side_effect = RuntimeError("bot db down")

    with pytest.raises(RuntimeError, match="bot db down"):
        svc.provision(bot=_BOT, owner_id="u1")

    binding_repo.update_status.assert_not_called()


def test_enqueue_failure_propagates_binding_write_failure():
    svc, _, _, binding_repo, task_queue_service, _ = _make_service()
    task_queue_service.enqueue.side_effect = RuntimeError("queue down")
    binding_repo.update_status.side_effect = RuntimeError("binding db down")

    with pytest.raises(RuntimeError, match="binding db down"):
        svc.provision(bot=_BOT, owner_id="u1")
```

- [ ] **Step 2: Run provision tests and verify RED**

Run:

```bash
cd src/backend
uv run pytest -q tests/community/core/bot_management/services/test_teclaw_provision_service.py
```

Expected: constructor and queue assertions fail because the service still requires `status_reconciler` and never enqueues.

- [ ] **Step 3: Implement enqueue and failure persistence**

Replace the reconciler dependency with required `TaskQueueService` and `BotRepository` dependencies. Immediately after `insert_binding`, call:

```python
try:
    self._task_queue_service.enqueue(
        TECLAW_CREATE_PUBLISH_POLL_TASK,
        build_teclaw_publish_poll_payload(
            binding_id=binding_id,
            bot_id=bot_id,
            owner_id=owner_id,
            publish_id=int(publish_id),
            started_at_epoch_s=time.time(),
        ),
        deadline_seconds=TECLAW_PUBLISH_TASK_DEADLINE_SECONDS,
    )
except Exception as exc:
    logger.exception(
        "[TeclawProvisionService.provision] enqueue publish poll failed: "
        "bot_id=%s binding_id=%s publish_id=%s error=%s",
        bot_id,
        binding_id,
        publish_id,
        exc,
    )
    self._mark_enqueue_failed(
        bot_id=bot_id,
        owner_id=owner_id,
        binding_id=binding_id,
    )
    return TeclawProvisionResult(
        binding_id=binding_id,
        device_id=bot_uuid,
        status=DeviceBindingStatus.FAILED.value,
        config_artifact=config_artifact,
    )
```

`_mark_enqueue_failed` updates the bot first and raises if `update_by_owner` returns `None`, then updates the binding. Do not call `_best_effort_destroy` in this branch. Move the best-effort outbound-rule update after successful enqueue so the durable follow-up is the first post-binding action.

Update the module docstring to describe durable task polling and remove every `TeclawStatusReconciler` import, field, and call.

- [ ] **Step 4: Run provision and handler tests and verify GREEN**

Run:

```bash
cd src/backend
uv run pytest -q tests/community/core/bot_management/services/test_teclaw_provision_service.py tests/community/core/bot_management/services/test_teclaw_publish_task_handler.py
```

Expected: both test modules pass.

- [ ] **Step 5: Commit the provisioning behavior**

```bash
git add src/backend/src/agentclaw/community/core/bot_management/services/teclaw_provision_service.py src/backend/tests/community/core/bot_management/services/test_teclaw_provision_service.py
git commit -m "fix(teclaw): fail provisioning when task enqueue fails" -m "Mark both the bot and binding FAILED when durable polling cannot be enqueued, matching the regular BaaS create lifecycle. Do not destroy the already-created remote bot on this failure path."
```

---

### Task 3: Register one lifecycle in every profile and remove the process-local reconciler

**Files:**
- Modify: `src/backend/src/agentclaw/community/di/modules/bot_management_module.py`
- Modify: `src/backend/src/agentclaw/community/di/modules/infrastructure/singlebox/devices.py`
- Modify: `src/backend/src/agentclaw/community/di/modules/infrastructure/test/devices.py`
- Modify: `src/backend/src/agentclaw/community/di/modules/testing_devices_module.py`
- Modify: `src/backend/tests/community/architecture/test_lifecycle_discovery.py`
- Modify: `src/backend/tests/community/di/test_profile_and_modules_for.py`
- Delete: `src/backend/src/agentclaw/community/core/bot_management/services/teclaw_status_reconciler.py`
- Delete: `src/backend/tests/community/core/bot_management/services/test_teclaw_status_reconciler.py`

**Interfaces:**
- Consumes: Task 1's `TeclawPublishTaskLifecycle`, shared `HandlerRegistry`, real `BaasService`, `BotRepository`, and `DeviceBindingRepository` bindings.
- Produces: one profile-neutral lifecycle discoverable before `TaskWorker.startup`; no reconciler binding in any profile.

- [ ] **Step 1: Write failing lifecycle and profile assertions**

Add `TeclawPublishTaskLifecycle` to `_EXPECTED_PARTICIPANTS` in `test_lifecycle_discovery.py`.

Replace `test_singlebox_reconciler_uses_real_dependencies_with_noop_scheduler` with:

```python
@pytest.mark.parametrize("profile", [DeployProfile.TEST, DeployProfile.SINGLEBOX])
def test_teclaw_publish_lifecycle_uses_real_dependencies_in_every_local_profile(profile):
    injector = build_injector(profile=profile)

    lifecycle = injector.get(TeclawPublishTaskLifecycle)

    assert lifecycle._baas is injector.get(BaasService)
    assert lifecycle._bot_repository is injector.get(BotRepository)
    assert lifecycle._device_binding_repo is injector.get(DeviceBindingRepository)
    assert "TeclawPublishTaskLifecycle" in {
        type(participant).__name__
        for participant in discover_lifecycle_participants(injector)
    }
```

Update imports from `TeclawStatusReconciler` to `TeclawPublishTaskLifecycle`.

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run:

```bash
cd src/backend
uv run pytest -q tests/community/architecture/test_lifecycle_discovery.py tests/community/di/test_profile_and_modules_for.py
```

Expected: the new lifecycle is missing from Injector discovery.

- [ ] **Step 3: Wire the neutral lifecycle and remove overrides**

In `BotManagementModule`, remove the reconciler import/provider. Import `HandlerRegistry`, `TeclawPublishTaskLifecycle`, and add:

```python
@singleton
@provider
@inject
def teclaw_publish_task_lifecycle(
    self,
    registry: HandlerRegistry,
    baas_service: BaasService,
    bot_repository: BotRepository,
    device_binding_repo: DeviceBindingRepository,
) -> TeclawPublishTaskLifecycle:
    return TeclawPublishTaskLifecycle(
        registry=registry,
        baas_service=baas_service,
        bot_repository=bot_repository,
        device_binding_repo=device_binding_repo,
    )
```

Update the `teclaw_provision_service` provider to pass `task_queue_service` and `bot_repository` instead of `teclaw_status_reconciler`.

Delete each profile-specific `teclaw_status_reconciler` provider and its import from singlebox/test device modules. Delete the old reconciler implementation and test module.

- [ ] **Step 4: Run focused DI and Teclaw tests and verify GREEN**

Run:

```bash
cd src/backend
uv run pytest -q \
  tests/community/architecture/test_lifecycle_discovery.py \
  tests/community/di/test_profile_and_modules_for.py \
  tests/community/core/bot_management/services/test_teclaw_publish_task_handler.py \
  tests/community/core/bot_management/services/test_teclaw_provision_service.py
```

Expected: all selected tests pass and no background scheduler thread is constructed.

- [ ] **Step 5: Confirm all runtime references are gone**

Run:

```bash
rg -n "TeclawStatusReconciler|teclaw_status_reconciler|status_reconciler" \
  src/backend/src src/backend/tests
```

Expected: no matches.

- [ ] **Step 6: Commit the runtime wiring cleanup**

```bash
git add src/backend/src/agentclaw/community/di/modules src/backend/tests/community/architecture/test_lifecycle_discovery.py src/backend/tests/community/di/test_profile_and_modules_for.py src/backend/src/agentclaw/community/core/bot_management/services/teclaw_status_reconciler.py src/backend/tests/community/core/bot_management/services/test_teclaw_status_reconciler.py
git commit -m "refactor(teclaw): replace process reconciler lifecycle" -m "Register the durable publish handler before TaskWorker startup in every profile and remove the in-memory scheduler plus profile-specific no-op bindings."
```

---

### Task 4: Update boundary documentation and run final verification

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_management/README.md`
- Modify: `src/backend/src/agentclaw/community/core/task_queue/README.md`
- Modify: `src/backend/src/agentclaw/community/core/bot_management/services/bot_service.py`
- Modify: `src/backend/src/agentclaw/community/core/devices/services/device_service.py`
- Modify: Teclaw-related test comments found by the reference scan.

**Interfaces:**
- Consumes: completed durable lifecycle behavior from Tasks 1-3.
- Produces: architecture boundary metadata and comments that describe DB-backed task reconciliation rather than the deleted daemon scheduler.

- [ ] **Step 1: Update architecture and status documentation**

In `bot_management/README.md`, add `TeclawProvisionService + TeclawPublishTaskLifecycle` to `provides` and `TaskQueueService + HandlerRegistry` to `consumes`.

In `task_queue/README.md`, replace the stale statement that no production handler is registered with:

```markdown
## Status

The BaaS and Teclaw lifecycle components register production handlers during
`bootstrap()` in every deployment profile. The worker processes them only when
`task_queue_worker.enabled=true`. The production `ac_task_queue` table must be
provisioned before enabling the worker; local and test SQLite schema bootstrap
creates it from the shared ORM metadata.
```

Replace remaining runtime/test comments that claim `TeclawStatusReconciler` owns convergence with `TeclawPublishTaskHandler` or “the durable Teclaw publish task”. Do not rewrite the historical problem statement in the approved design document.

- [ ] **Step 2: Run formatting and static checks for touched Python files**

Run:

```bash
cd src/backend
uv run ruff check \
  src/agentclaw/community/core/bot_management/services/teclaw_publish_task_handler.py \
  src/agentclaw/community/core/bot_management/services/teclaw_provision_service.py \
  src/agentclaw/community/di/modules/bot_management_module.py \
  src/agentclaw/community/di/modules/infrastructure/singlebox/devices.py \
  src/agentclaw/community/di/modules/infrastructure/test/devices.py \
  src/agentclaw/community/di/modules/testing_devices_module.py \
  tests/community/core/bot_management/services/test_teclaw_publish_task_handler.py \
  tests/community/core/bot_management/services/test_teclaw_provision_service.py
```

Expected: exit code 0.

- [ ] **Step 3: Run the focused regression suite**

Run:

```bash
cd src/backend
uv run pytest -q \
  tests/community/core/bot_management/services/test_teclaw_publish_task_handler.py \
  tests/community/core/bot_management/services/test_teclaw_provision_service.py \
  tests/community/core/task_queue/test_task_worker.py \
  tests/community/plugins/test_task_queue_repository.py \
  tests/community/architecture/test_lifecycle_discovery.py \
  tests/community/di/test_profile_and_modules_for.py
```

Expected: all tests pass, including the existing real-SQLite task-worker and lease-reclaim coverage.

- [ ] **Step 4: Run Backend architecture enforcement**

Run:

```bash
cd src/backend
uv run pytest -q tests/community/architecture
```

Expected: all applicable architecture checks pass. If a documented command is unavailable locally, record the exact command and error instead of substituting a weaker check.

- [ ] **Step 5: Run the full Backend unit suite once**

Run:

```bash
cd src/backend
uv run pytest -q tests/community
```

Expected: all community Backend tests pass. Report any unrelated pre-existing failure separately with its exact test name and traceback.

- [ ] **Step 6: Review the final diff and commit documentation**

Run:

```bash
git diff --check
rg -n "TeclawStatusReconciler|teclaw_status_reconciler|status_reconciler" \
  src/backend/src src/backend/tests
git status --short
```

Expected: `git diff --check` succeeds, the reference scan has no matches, and only Issue #116 files are changed.

Commit:

```bash
git add src/backend/src/agentclaw/community/core/bot_management/README.md src/backend/src/agentclaw/community/core/task_queue/README.md src/backend/src/agentclaw/community/core/bot_management/services/bot_service.py src/backend/src/agentclaw/community/core/devices/services/device_service.py src/backend/tests
git commit -m "docs(teclaw): describe durable publish convergence"
```

- [ ] **Step 7: Perform code review, push, open the PR, and request review**

Use the repository's code-review flow to inspect correctness, architecture boundaries, and test evidence. After resolving findings, push `codex/issue-116-teclaw-task-queue`, open a PR against `dev` referencing `#116`, summarize the preserved timeout compatibility and enqueue-failure behavior, and request `@totalfrank` as reviewer.
