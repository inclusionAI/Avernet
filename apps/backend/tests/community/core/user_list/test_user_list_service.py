"""Delegation rules for authenticated user-list corrections."""

from __future__ import annotations

from agentclaw.community.core.user_list.service import UserListService
from unittest.mock import Mock


class _Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool, str | None]] = []

    def exists(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        env: str | None = None,
    ) -> bool:
        del entity_id, user_list_type, env
        return False

    def set_membership(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        in_whitelist: bool,
        subject_type: str = "user",
        bot_type: str = "all",
        env: str | None = None,
    ) -> None:
        self.calls.append((entity_id, user_list_type, in_whitelist, env))


def test_correction_allows_any_authenticated_actor():
    repository = _Repository()
    service = UserListService(repository=repository, staff_dept=Mock())

    result = service.correct_membership(
        actor_id="any_authenticated_actor",
        entity_id="target",
        user_list_type="caller_identity",
        in_whitelist=True,
    )

    assert result is True
    assert repository.calls == [("target", "caller_identity", True, None)]


def test_correction_allows_another_authenticated_actor_to_remove():
    repository = _Repository()
    service = UserListService(repository=repository, staff_dept=Mock())

    result = service.correct_membership(
        actor_id="another_authenticated_actor",
        entity_id="target",
        user_list_type="caller_identity",
        in_whitelist=False,
    )

    assert result is False
    assert repository.calls == [("target", "caller_identity", False, None)]


def test_correction_forwards_explicit_environment():
    repository = _Repository()
    service = UserListService(repository=repository, staff_dept=Mock())

    result = service.correct_membership(
        actor_id="authenticated_actor",
        entity_id="target",
        user_list_type="caller_identity",
        in_whitelist=True,
        env="prod",
    )

    assert result is True
    assert repository.calls == [("target", "caller_identity", True, "prod")]


def test_runtime_artifact_list_can_be_corrected_after_boundary_authorization():
    repository = _Repository()
    service = UserListService(repository=repository, staff_dept=Mock())
    for enabled in (True, False):
        result = service.correct_membership(
            actor_id="operator", entity_id="*",
            user_list_type="service_bot_runtime_artifact", in_whitelist=enabled, env="prod",
        )
        assert result is enabled
    assert repository.calls == [
        ("*", "service_bot_runtime_artifact", True, "prod"),
        ("*", "service_bot_runtime_artifact", False, "prod"),
    ]
