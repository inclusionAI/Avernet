"""Application service for frontend-only user-list eligibility checks."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.user_list.repository import UserListRepositoryProtocol
from agentclaw.community.log import get_logger


logger = get_logger()


class UserListService:
    """Expose only boolean membership, never the underlying list data."""

    @inject
    def __init__(self, repository: UserListRepositoryProtocol) -> None:
        self._repository = repository

    def is_in_user_list(self, *, entity_id: str, user_list_type: str) -> bool:
        return self._repository.exists(
            entity_id=entity_id,
            user_list_type=user_list_type,
        )

    def correct_membership(
        self,
        *,
        actor_id: str,
        entity_id: str,
        user_list_type: str,
        in_whitelist: bool,
    ) -> bool:
        self._repository.set_membership(
            entity_id=entity_id,
            user_list_type=user_list_type,
            in_whitelist=in_whitelist,
        )
        logger.info(
            "user_list_membership_corrected actor_id=%s entity_id=%s "
            "user_list_type=%s in_whitelist=%s",
            actor_id,
            entity_id,
            user_list_type,
            in_whitelist,
        )
        return in_whitelist


__all__ = ["UserListService"]
