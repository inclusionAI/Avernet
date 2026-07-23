"""Delegation rules for authenticated user-list corrections."""

from __future__ import annotations

from agentclaw.community.core.user_list.service import UserListService


class _Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def exists(self, *, entity_id: str, user_list_type: str) -> bool:
        del entity_id, user_list_type
        return False

    def set_membership(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        in_whitelist: bool,
    ) -> None:
        self.calls.append((entity_id, user_list_type, in_whitelist))


def test_correction_allows_any_authenticated_actor():
    repository = _Repository()
    service = UserListService(repository=repository)

    result = service.correct_membership(
        actor_id="any_authenticated_actor",
        entity_id="target",
        user_list_type="caller_identity",
        in_whitelist=True,
    )

    assert result is True
    assert repository.calls == [("target", "caller_identity", True)]


def test_correction_allows_another_authenticated_actor_to_remove():
    repository = _Repository()
    service = UserListService(repository=repository)

    result = service.correct_membership(
        actor_id="another_authenticated_actor",
        entity_id="target",
        user_list_type="caller_identity",
        in_whitelist=False,
    )

    assert result is False
    assert repository.calls == [("target", "caller_identity", False)]
