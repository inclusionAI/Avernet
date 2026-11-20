"""Real coverage for ``LocalSkillUploadDstAssetMixin.ensure_dst_local_skill_asset``.

The ``from: from_bot`` member registration runs through this mixin method,
which the larger ``LocalSkillUploadService`` mixes in ahead of its protocol
stub. The service constructor wires the seven dependencies the mixin reaches
for (``_skill_repo``/``_edit_guard``/``_skill_service_factory`` directly,
``_authorize``/``_scope_for``/``_same_name_matches``/``_is_teclaw`` as helper
methods on the service itself). This file instantiates the real service and
mocks only those seven seams, so every line of the mixin method itself runs
against the real implementation.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from agentclaw.community.core.skill_center.errors import (
    LocalSkillDuplicateError,
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillEditPausedError,
    LocalSkillLayoutRollbackError,
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_service import (
    LocalSkillUploadService,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditPausedError,
    SkillsPoolEditRollbackError,
)


class _Guard:
    """A no-op edit guard that records acquire/release and can raise on acquire."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.acquired = 0
        self.released = 0

    async def acquire_for_edit_wait(self, **_kwargs):
        self.acquired += 1
        if self._error is not None:
            raise self._error
        return object()

    def release(self, _lease) -> None:
        self.released += 1


class _Repo:
    """Skill repository stub: only the calls ``ensure_dst_local_skill_asset`` makes.

    ``list_bot_local_by_name`` returns the configured ``src_rows`` (the reverse
    lookup against the source bot). ``create`` and ``replace_bot_local_skill``
    record their calls and return either a canned row or, for ``replace``, the
    sentinel ``None`` when the caller asked for the storage-failure branch.
    """

    def __init__(
        self,
        *,
        src_rows: list[dict[str, Any]] | None = None,
        create_error: Exception | None = None,
        replace_result: dict[str, Any] | None | None = "_DEFAULT",
    ) -> None:
        self.src_rows = src_rows or []
        self.create_error = create_error
        # ``"_DEFAULT"`` is the canned-success board; ``None`` is the explicit
        # storage failure branch the mixin raises ``LocalSkillStorageError`` on.
        self._replace_result = replace_result
        self.created: list[dict[str, Any]] = []
        self.replaced: list[dict[str, Any]] = []

    def list_bot_local_by_name(self, **_kwargs) -> list[dict[str, Any]]:
        return list(self.src_rows)

    def create(self, row: dict[str, Any]) -> dict[str, Any]:
        if self.create_error is not None:
            raise self.create_error
        out = {
            "id": "9",
            "active": False,
            "gmt_created": None,
            "gmt_modified": None,
            **row,
        }
        self.created.append(out)
        return dict(out)

    def replace_bot_local_skill(self, **kwargs):
        self.replaced.append(kwargs)
        if self._replace_result == "_DEFAULT":
            return {
                "id": kwargs["skill_id"],
                "name": "qc",
                "description": kwargs["description"],
                "git_path": f"local://{kwargs['new_locator']}",
                "user_id": kwargs["owner_id"],
                "bolt_id": kwargs["bot_id"],
                "active": False,
            }
        return self._replace_result


def _make_service(*, repo=None, guard=None, factory=None) -> LocalSkillUploadService:
    """Build a real ``LocalSkillUploadService`` with every unused dep mocked.

    Only the three constructor-injected dependencies the mixin reaches for are
    real: ``_skill_repo``, ``_edit_guard``, ``_skill_service_factory``. The
    helper methods ``_authorize``/``_scope_for``/``_same_name_matches``/
    ``_is_teclaw`` are set per-test on the instance (Python resolves the
    instance attribute ahead of the class method, so the override is local
    to that test).
    """
    service = LocalSkillUploadService(
        repo or _Repo(),
        MagicMock(name="bot_repo"),
        MagicMock(name="collaborators"),
        factory or MagicMock(name="factory"),
        guard or _Guard(),
        MagicMock(name="device_resolver_provider"),
        MagicMock(name="runtime_reconciler"),
        MagicMock(name="package_validator"),
        MagicMock(name="package_runtime"),
        MagicMock(name="security_observer_provider"),
        False,
    )
    return service


def _bot(*, entity_id: str = "dst-entity", bot_type: str = "personal") -> dict[str, Any]:
    return {
        "id": 1,
        "bot_id": "dst-bot",
        "entity_id": entity_id,
        "owner_id": "dst-owner",
        "active_engine": "moltis",
        "bot_type": bot_type,
        "entity_type": "staff",
        "env": "dev",
        "status": "ACTIVE",
    }


def _default_location_mock() -> MagicMock:
    factory = MagicMock(name="factory")
    factory.local_skill_package_location.return_value = SimpleNamespace(
        directory="skills-local/qc", layout="LEGACY",
    )
    return factory


def _authorize_ready(service, *, bot=None) -> None:
    """Wire ``_authorize``/``_scope_for``/``_same_name_matches``/``_is_teclaw`` to benign fakes."""
    service._authorize = MagicMock(return_value=bot or _bot())
    service._scope_for = MagicMock(return_value="scope-1")
    service._is_teclaw = MagicMock(return_value=False)


# ── happy paths ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_creates_dst_row_when_no_existing_matches():
    repo = _Repo(src_rows=[{"name": "qc", "description": "useful", "user_id": "src-owner"}])
    factory = _default_location_mock()
    service = _make_service(repo=repo, factory=factory)
    _authorize_ready(service)
    service._same_name_matches = MagicMock(return_value=[])

    result = await service.ensure_dst_local_skill_asset(
        bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
        source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
    )

    assert result["operation"] == "created"
    assert result["actor_id"] == "dst-owner"
    assert result["skill"]["name"] == "qc"
    assert result["skill"]["description"] == "useful"
    assert result["skill"]["user_id"] == "dst-entity"  # stable_resource_owner_id == entity_id
    assert result["skill"]["git_path"] == "local://skills-local/qc"
    assert result["skill"]["active"] is False
    # Repo create ran with the description propagated from the source row and the
    # layout-owned locator the factory returned.
    assert len(repo.created) == 1
    row = repo.created[0]
    assert row["name"] == "qc"
    assert row["description"] == "useful"
    assert row["git_path"] == "local://skills-local/qc"
    assert row["user_id"] == "dst-entity"
    assert row["bolt_id"] == "dst-bot"
    assert row["source_type"] == "upload"
    factory.local_skill_package_location.assert_called_once()


@pytest.mark.asyncio
async def test_updates_dst_row_when_same_name_owned_match_exists():
    existing = {
        "id": "9", "name": "qc", "description": "stale",
        "git_path": "local:///private/skills-local/qc",
        "user_id": "dst-entity", "bolt_id": "dst-bot", "active": False,
    }
    repo = _Repo(src_rows=[{"name": "qc", "description": "fresh", "user_id": "src-owner"}])
    factory = _default_location_mock()
    service = _make_service(repo=repo, factory=factory)
    _authorize_ready(service)
    service._same_name_matches = MagicMock(return_value=[existing])

    result = await service.ensure_dst_local_skill_asset(
        bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
        source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
    )

    # The replace stays at the historical locator: ``operation="updated"``, the
    # row keeps its existing id and git_path, the description is refreshed.
    assert result["operation"] == "updated"
    assert result["actor_id"] == "dst-owner"
    assert result["skill"]["id"] == "9"
    assert result["skill"]["git_path"] == existing["git_path"]
    assert result["skill"]["description"] == "fresh"
    assert result["skill"]["user_id"] == "dst-entity"
    # No create path entered.
    assert repo.created == []
    # replace ran with old==new locator, preserving the recorded git_path.
    assert len(repo.replaced) == 1
    call = repo.replaced[0]
    assert call["skill_id"] == "9"
    assert call["owner_id"] == "dst-entity"
    assert call["bot_id"] == "dst-bot"
    assert call["old_locator"] == "/private/skills-local/qc"
    assert call["new_locator"] == "/private/skills-local/qc"
    assert call["description"] == "fresh"
    # The factory is consulted for ``dst_git_path`` (the create branch's
    # locator) on every path — the location is computed up front, before the
    # ``matches`` branch decides which locator the row keeps.
    factory.local_skill_package_location.assert_called_once()


# ── reverse-lookup and upgrade error branches ──────────────────────────────


@pytest.mark.asyncio
async def test_raises_local_skill_not_found_when_source_bot_has_no_match():
    repo = _Repo(src_rows=[])  # source bot had no row owned by source_owner_id
    service = _make_service(repo=repo)
    _authorize_ready(service)
    service._same_name_matches = MagicMock(return_value=[])

    with pytest.raises(LocalSkillNotFoundError):
        await service.ensure_dst_local_skill_asset(
            bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
            source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
        )


@pytest.mark.asyncio
async def test_raises_local_skill_not_found_when_multiple_source_rows_match():
    repo = _Repo(src_rows=[
        {"name": "qc", "description": "a", "user_id": "src-owner"},
        {"name": "qc", "description": "b", "user_id": "src-owner"},
    ])
    service = _make_service(repo=repo)
    _authorize_ready(service)
    service._same_name_matches = MagicMock(return_value=[])

    with pytest.raises(LocalSkillNotFoundError):
        await service.ensure_dst_local_skill_asset(
            bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
            source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
        )


@pytest.mark.asyncio
async def test_raises_duplicate_when_multiple_same_name_owned_matches():
    repo = _Repo(src_rows=[{"name": "qc", "description": "x", "user_id": "src-owner"}])
    service = _make_service(repo=repo)
    _authorize_ready(service)
    # Two same-name rows already owned on the dst bot — ambiguous replace.
    service._same_name_matches = MagicMock(return_value=[
        {"id": "1", "git_path": "local://a", "user_id": "dst-entity"},
        {"id": "2", "git_path": "local://b", "user_id": "dst-entity"},
    ])

    with pytest.raises(LocalSkillDuplicateError):
        await service.ensure_dst_local_skill_asset(
            bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
            source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
        )


# ── scope / readiness interlocks under the lock ────────────────────────────


@pytest.mark.asyncio
async def test_raises_not_found_when_scope_changed_under_lock():
    repo = _Repo(src_rows=[{"name": "qc", "description": "x", "user_id": "src-owner"}])
    service = _make_service(repo=repo)
    bot = _bot()
    # Two ``_authorize`` calls; both return the same bot. ``_scope_for`` flips
    # between calls so the post-lock re-read disagrees with the pre-read.
    service._authorize = MagicMock(return_value=bot)
    service._scope_for = MagicMock(side_effect=["scope-1", "scope-2"])
    service._is_teclaw = MagicMock(return_value=False)
    service._same_name_matches = MagicMock(return_value=[])

    with pytest.raises(LocalSkillNotFoundError):
        await service.ensure_dst_local_skill_asset(
            bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
            source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
        )


@pytest.mark.asyncio
async def test_raises_not_ready_when_bot_not_ready_under_lock():
    repo = _Repo(src_rows=[{"name": "qc", "description": "x", "user_id": "src-owner"}])
    service = _make_service(repo=repo)
    bot = _bot()
    bot["status"] = "STOPPED"  # is_bot_ready requires ACTIVE
    service._authorize = MagicMock(return_value=bot)
    service._scope_for = MagicMock(return_value="scope-1")
    service._is_teclaw = MagicMock(return_value=False)
    service._same_name_matches = MagicMock(return_value=[])

    with pytest.raises(LocalSkillNotReadyError):
        await service.ensure_dst_local_skill_asset(
            bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
            source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
        )


# ── storage failures ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_failure_is_wrapped_into_storage_error():
    repo = _Repo(
        src_rows=[{"name": "qc", "description": "x", "user_id": "src-owner"}],
        create_error=RuntimeError("db down"),
    )
    factory = _default_location_mock()
    service = _make_service(repo=repo, factory=factory)
    _authorize_ready(service)
    service._same_name_matches = MagicMock(return_value=[])

    with pytest.raises(LocalSkillStorageError):
        await service.ensure_dst_local_skill_asset(
            bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
            source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
        )
    # create was attempted once and surfaces in the recorded call.
    assert len(repo.created) == 0  # the error prevented the row append


@pytest.mark.asyncio
async def test_replace_returning_none_is_raised_as_storage_error():
    existing = {
        "id": "9", "name": "qc", "description": "stale",
        "git_path": "local:///private/skills-local/qc",
        "user_id": "dst-entity", "bolt_id": "dst-bot", "active": False,
    }
    repo = _Repo(
        src_rows=[{"name": "qc", "description": "fresh", "user_id": "src-owner"}],
        replace_result=None,  # the storage-failure branch
    )
    factory = _default_location_mock()
    service = _make_service(repo=repo, factory=factory)
    _authorize_ready(service)
    service._same_name_matches = MagicMock(return_value=[existing])

    with pytest.raises(LocalSkillStorageError):
        await service.ensure_dst_local_skill_asset(
            bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
            source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
        )


# ── edit-guard acquire-error mapping ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "guard_error, mapped",
    [
        (SkillsPoolEditBusyError(), LocalSkillEditBusyError),
        (SkillsPoolEditRollbackError(), LocalSkillLayoutRollbackError),
        (SkillsPoolEditLockUnavailableError(), LocalSkillEditLockUnavailableError),
        (SkillsPoolEditPausedError(), LocalSkillEditPausedError),
    ],
    ids=["busy", "rollback", "lock-unavailable", "paused"],
)
async def test_edit_guard_acquire_errors_are_remapped(guard_error, mapped):
    repo = _Repo(src_rows=[{"name": "qc", "description": "x", "user_id": "src-owner"}])
    guard = _Guard(error=guard_error)
    service = _make_service(repo=repo, guard=guard)
    _authorize_ready(service)
    service._same_name_matches = MagicMock(return_value=[])

    with pytest.raises(mapped):
        await service.ensure_dst_local_skill_asset(
            bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
            source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
        )
    # Acquire was attempted and never released: the lease only enters the
    # try/finally body when acquisition succeeds, so the release counter stays 0.
    assert guard.acquired == 1
    assert guard.released == 0


# ── lease release on every exit path ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_lock_is_released_after_successful_create():
    repo = _Repo(src_rows=[{"name": "qc", "description": "x", "user_id": "src-owner"}])
    guard = _Guard()
    factory = _default_location_mock()
    service = _make_service(repo=repo, guard=guard, factory=factory)
    _authorize_ready(service)
    service._same_name_matches = MagicMock(return_value=[])

    await service.ensure_dst_local_skill_asset(
        bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
        source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
    )

    assert guard.acquired == 1
    assert guard.released == 1


@pytest.mark.asyncio
async def test_lock_is_released_after_failure_path():
    repo = _Repo(src_rows=[])  # source bot has no row → NotFound before persistence
    guard = _Guard()
    service = _make_service(repo=repo, guard=guard)
    _authorize_ready(service)
    service._same_name_matches = MagicMock(return_value=[])

    with pytest.raises(LocalSkillNotFoundError):
        await service.ensure_dst_local_skill_asset(
            bot_id="dst-bot", owner_id="dst-owner", actor_id="dst-owner",
            source_bot_id="src-bot", source_owner_id="src-owner", name="qc",
        )

    # Acquire succeeded but the body raised — the finally still releases the lease.
    assert guard.acquired == 1
    assert guard.released == 1
