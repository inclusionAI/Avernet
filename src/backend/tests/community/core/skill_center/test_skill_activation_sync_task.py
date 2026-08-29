"""Contract tests for the durable Bot-level skill activation sync task.

The handler is a follow-up, so what is under test here is the enqueue contract:
the payload's shape (including the ``action_type`` discriminator), the parse
that a handler will rely on, and the Bot-level dedup key. The dedup itself is
exercised end-to-end against a real ``TaskQueueRepository`` on in-memory SQLite
rather than a mock, because "the second enqueue joins the first" is a property
of the unique index, not of the helper.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Side-effect import: registers TaskQueueModel on Base.metadata so create_all()
# builds ac_task_queue.
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel  # noqa: F401
from agentclaw.community.core.repository.implementations.platform.task_queue import (
    _MAX_IDEMPOTENCY_KEY_LEN,
    TaskQueueRepository,
)
from agentclaw.community.core.skill_center.services.skill_activation_sync_task import (
    SKILL_ACTIVATION_SYNC_DEADLINE_SECONDS,
    SKILL_ACTIVATION_SYNC_TASK,
    SkillActivationSyncAction,
    SkillActivationSyncScope,
    SkillActivationSyncTaskHandler,
    build_skill_activation_sync_idempotency_key,
    build_skill_activation_sync_payload,
    enqueue_skill_activation_sync,
    parse_skill_activation_sync_payload,
)
from agentclaw.community.core.task_queue.services.registry import (
    HandlerRegistry,
    TaskHandler,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.services.wakeup import WorkerWakeup
from agentclaw.community.core.task_queue.types import DEFAULT_APP, Fail, TaskStatus
from agentclaw.community.di.config import TaskQueueConfig

ENV = "dev"
ACTION = SkillActivationSyncAction.PLACEHOLDER


def _scope(env=ENV, entity_id="ent-1", bot_id="bot-1") -> SkillActivationSyncScope:
    return SkillActivationSyncScope(env=env, entity_id=entity_id, bot_id=bot_id)


# ── payload contract ────────────────────────────────────────────────────────


def test_payload_carries_scope_and_action_type():
    payload = build_skill_activation_sync_payload(
        scope=_scope(), action=ACTION, action_args={"set_id": "s-1"}
    )

    assert payload["scope"] == {
        "env": ENV,
        "entity_id": "ent-1",
        "bot_id": "bot-1",
    }
    # The discriminator is persisted as the enum's plain value, not repr().
    assert payload["action_type"] == "placeholder"
    assert payload["action_args"] == {"set_id": "s-1"}


def test_payload_defaults_action_args_to_empty():
    payload = build_skill_activation_sync_payload(scope=_scope(), action=ACTION)

    assert payload["action_args"] == {}


def test_payload_carries_nothing_beyond_the_work_identity():
    """No generated correlation id: the task row's own id is the identity."""
    payload = build_skill_activation_sync_payload(scope=_scope(), action=ACTION)

    assert set(payload) == {"scope", "action_type", "action_args"}


def test_payload_copies_action_args():
    """A later mutation of the caller's dict must not reach the persisted task."""
    args = {"set_id": "s-1"}
    payload = build_skill_activation_sync_payload(
        scope=_scope(), action=ACTION, action_args=args
    )
    args["set_id"] = "s-2"

    assert payload["action_args"] == {"set_id": "s-1"}


def test_payload_does_not_leak_into_the_shared_empty_default():
    """``action_args`` defaults to a shared ``{}``; the copy is what keeps it safe."""
    first = build_skill_activation_sync_payload(scope=_scope(), action=ACTION)
    first["action_args"]["set_id"] = "s-1"

    second = build_skill_activation_sync_payload(scope=_scope(), action=ACTION)

    assert second["action_args"] == {}


def test_parse_round_trips_a_built_payload():
    payload = build_skill_activation_sync_payload(
        scope=_scope(), action=ACTION, action_args={"set_id": "s-1"}
    )

    work = parse_skill_activation_sync_payload(payload)

    assert work.scope == _scope()
    assert work.action is ACTION
    assert work.action_args == {"set_id": "s-1"}


def test_parse_rejects_an_absent_action_args():
    """The builder always writes the key, so a payload missing it is malformed."""
    payload = build_skill_activation_sync_payload(scope=_scope(), action=ACTION)
    del payload["action_args"]

    with pytest.raises(ValueError, match="action_args"):
        parse_skill_activation_sync_payload(payload)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda p: p.update(scope="nope"), "scope must be an object"),
        (lambda p: p["scope"].update(bot_id=""), "scope.bot_id"),
        (lambda p: p["scope"].update(env="   "), "scope.env"),
        (lambda p: p["scope"].pop("entity_id"), "scope.entity_id"),
        (lambda p: p.update(action_type="switch"), "unknown action_type"),
        (lambda p: p.update(action_type=""), "action_type"),
        (lambda p: p.update(action_args=["set_id"]), "action_args"),
        (lambda p: p.update(action_args=None), "action_args"),
    ],
)
def test_parse_rejects_malformed_payloads(mutate, expected):
    payload = build_skill_activation_sync_payload(scope=_scope(), action=ACTION)
    mutate(payload)

    with pytest.raises(ValueError, match=expected):
        parse_skill_activation_sync_payload(payload)


def test_parse_rejects_an_unknown_action_type_rather_than_ignoring_it():
    """An older pod must fail loudly on a newer pod's action, not run it blind."""
    payload = build_skill_activation_sync_payload(scope=_scope(), action=ACTION)
    payload["action_type"] = "activate_from_a_future_release"

    with pytest.raises(ValueError, match="unknown action_type"):
        parse_skill_activation_sync_payload(payload)


# ── idempotency key ─────────────────────────────────────────────────────────


def test_key_is_deterministic():
    assert build_skill_activation_sync_idempotency_key(
        _scope()
    ) == build_skill_activation_sync_idempotency_key(_scope())


@pytest.mark.parametrize(
    "other",
    [
        _scope(env="prod"),
        _scope(entity_id="ent-2"),
        _scope(bot_id="bot-2"),
    ],
)
def test_key_distinguishes_every_scope_component(other):
    assert build_skill_activation_sync_idempotency_key(
        other
    ) != build_skill_activation_sync_idempotency_key(_scope())


def test_key_keeps_env_readable_and_digests_the_rest():
    """Operators filter ``ac_task_queue`` by env first, so it stays literal."""
    prefix, env, digest = build_skill_activation_sync_idempotency_key(
        _scope()
    ).split(":")

    assert prefix == "skill_activation_sync"
    assert env == ENV
    assert len(digest) == 32
    assert all(char in "0123456789abcdef" for char in digest)


def test_key_does_not_reorder_across_the_entity_bot_boundary():
    """``entity_id``/``bot_id`` are digested with a separator, not concatenated."""
    assert build_skill_activation_sync_idempotency_key(
        _scope(entity_id="a", bot_id="bc")
    ) != build_skill_activation_sync_idempotency_key(
        _scope(entity_id="ab", bot_id="c")
    )


def test_key_fits_the_column_even_for_maximum_width_ids():
    """entity_id is varchar(512) and bot_id varchar(128) at their widest."""
    key = build_skill_activation_sync_idempotency_key(
        _scope(env="x" * 20, entity_id="e" * 512, bot_id="b" * 128)
    )

    assert len(key) <= _MAX_IDEMPOTENCY_KEY_LEN
    assert key == key.strip()


@pytest.mark.parametrize(
    "scope, expected",
    [
        (_scope(env=""), "scope.env"),
        (_scope(entity_id="  "), "scope.entity_id"),
        (_scope(bot_id="bot-1 "), "scope.bot_id"),
        (_scope(env=" dev"), "scope.env"),
    ],
)
def test_key_rejects_unrepresentable_scope_components(scope, expected):
    with pytest.raises(ValueError, match=expected):
        build_skill_activation_sync_idempotency_key(scope)


# ── enqueue helper ──────────────────────────────────────────────────────────


def test_enqueue_forwards_the_task_type_key_and_schedule():
    service = MagicMock(spec=TaskQueueService)
    scope = _scope()

    enqueue_skill_activation_sync(
        service, scope=scope, action=ACTION, action_args={"set_id": "s-1"}
    )

    args, kwargs = service.enqueue.call_args
    assert args[0] == SKILL_ACTIVATION_SYNC_TASK
    assert args[1]["action_type"] == ACTION.value
    assert args[1]["action_args"] == {"set_id": "s-1"}
    assert kwargs["deadline_seconds"] == SKILL_ACTIVATION_SYNC_DEADLINE_SECONDS
    assert kwargs["delay_seconds"] == 0
    assert kwargs["idempotency_key"] == (
        build_skill_activation_sync_idempotency_key(scope)
    )


def test_enqueue_returns_the_queue_result_unchanged():
    service = MagicMock(spec=TaskQueueService)
    sentinel = object()
    service.enqueue.return_value = sentinel

    assert (
        enqueue_skill_activation_sync(service, scope=_scope(), action=ACTION)
        is sentinel
    )


# ── handler skeleton ────────────────────────────────────────────────────────


def test_handler_serves_the_task_type():
    assert SkillActivationSyncTaskHandler().task_type == SKILL_ACTIVATION_SYNC_TASK


def test_handler_satisfies_the_task_handler_protocol():
    assert isinstance(SkillActivationSyncTaskHandler(), TaskHandler)


def test_handler_is_registrable():
    """The registry rejects padded or case-colliding types; this one passes."""
    registry = HandlerRegistry()
    handler = SkillActivationSyncTaskHandler()

    registry.register(handler)

    assert registry.get(SKILL_ACTIVATION_SYNC_TASK) is handler


def test_handler_fails_a_malformed_payload_rather_than_retrying():
    """Retry would pin the Bot's dedup key until the deadline for nothing."""
    payload = build_skill_activation_sync_payload(scope=_scope(), action=ACTION)
    del payload["scope"]

    outcome = SkillActivationSyncTaskHandler().handle(payload)

    assert isinstance(outcome, Fail)
    assert "invalid skill activation sync payload" in outcome.error


def test_handler_reports_the_unimplemented_body_as_a_terminal_failure():
    """The body lands later; until then it must not spin on the queue."""
    payload = build_skill_activation_sync_payload(scope=_scope(), action=ACTION)

    outcome = SkillActivationSyncTaskHandler().handle(payload)

    assert isinstance(outcome, Fail)
    assert "no implementation yet" in outcome.error
    assert ACTION.value in outcome.error


# ── dedup against a real queue ──────────────────────────────────────────────


class _InMemorySqliteDB:
    def __init__(self, engine):
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def queue(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        "agentclaw.community.core.task_queue.services.task_queue_service.get_current_env",
        lambda: ENV,
    )
    return TaskQueueService(
        TaskQueueRepository(_InMemorySqliteDB(engine)),
        HandlerRegistry(),
        WorkerWakeup(),
        TaskQueueConfig(),
    )


@pytest.mark.integration
def test_second_operation_on_the_same_bot_joins_the_live_task(queue):
    first, created_first = enqueue_skill_activation_sync(
        queue, scope=_scope(), action=ACTION, action_args={"set_id": "s-1"}
    )
    second, created_second = enqueue_skill_activation_sync(
        queue, scope=_scope(), action=ACTION, action_args={"set_id": "s-2"}
    )

    assert created_first is True
    assert created_second is False
    # The caller is handed the task already in flight — including its payload,
    # which still describes the first operation. A handler that replayed
    # ``action_args`` would therefore drop the second operation's work.
    assert second.id == first.id
    assert second.payload["action_args"] == {"set_id": "s-1"}


@pytest.mark.integration
def test_a_different_bot_is_not_deduped(queue):
    first, created_first = enqueue_skill_activation_sync(
        queue, scope=_scope(), action=ACTION
    )
    second, created_second = enqueue_skill_activation_sync(
        queue, scope=_scope(bot_id="bot-2"), action=ACTION
    )

    assert created_first is True
    assert created_second is True
    assert second.id != first.id


@pytest.mark.integration
def test_a_terminal_task_releases_the_bot_for_the_next_operation(queue, monkeypatch):
    first, _ = enqueue_skill_activation_sync(queue, scope=_scope(), action=ACTION)
    repo = queue._repo
    claimed = repo.claim_batch(
        worker_id="w-1", env=ENV, app=DEFAULT_APP, limit=1, lease_seconds=60
    )
    assert [task.id for task in claimed] == [first.id]
    assert repo.complete(task_id=first.id, worker_id="w-1") is True

    second, created_second = enqueue_skill_activation_sync(
        queue, scope=_scope(), action=ACTION
    )

    assert created_second is True
    assert second.id != first.id
    assert second.status is TaskStatus.PENDING
