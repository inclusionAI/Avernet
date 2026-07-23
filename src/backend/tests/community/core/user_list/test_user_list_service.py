"""Authorization and delegation rules for user-list corrections."""

from __future__ import annotations

import pytest

from agentclaw.community.core.errors import Forbidden
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


@pytest.mark.parametrize("actor_id", ["330429", "61256"])
def test_correction_allows_only_the_two_named_operators(actor_id):
    repository = _Repository()
    service = UserListService(repository=repository)

    result = service.correct_membership(
        actor_id=actor_id,
        entity_id="target",
        user_list_type="caller_identity",
        in_whitelist=True,
    )

    assert result is True
    assert repository.calls == [("target", "caller_identity", True)]


def test_correction_rejects_other_authenticated_users_before_writing():
    repository = _Repository()
    service = UserListService(repository=repository)

    with pytest.raises(Forbidden):
        service.correct_membership(
            actor_id="not-authorized",
            entity_id="target",
            user_list_type="caller_identity",
            in_whitelist=False,
        )

    assert repository.calls == []
