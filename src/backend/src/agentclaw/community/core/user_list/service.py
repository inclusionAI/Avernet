"""Application service for frontend-only user-list eligibility checks."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.identity import UserListRepositoryProtocol
from agentclaw.community.log import get_logger


logger = get_logger()


class UserListService:
    """Expose only boolean membership, never the underlying list data."""

    @inject
    def __init__(self, repository: UserListRepositoryProtocol) -> None:
        self._repository = repository

    def is_in_user_list(
        self,
        *,
        entity_id: str,
        user_list_type: str,
        env: str | None = None,
    ) -> bool:
        return self._repository.exists(
            entity_id=entity_id,
            user_list_type=user_list_type,
            env=env,
        )

    def correct_membership(
        self,
        *,
        actor_id: str,
        entity_id: str,
        user_list_type: str,
        in_whitelist: bool,
        env: str | None = None,
    ) -> bool:
        self._repository.set_membership(
            entity_id=entity_id,
            user_list_type=user_list_type,
            in_whitelist=in_whitelist,
            env=env,
        )
        logger.info(
            "user_list_membership_corrected actor_id=%s entity_id=%s "
            "user_list_type=%s env=%s in_whitelist=%s",
            actor_id,
            entity_id,
            user_list_type,
            env or "runtime",
            in_whitelist,
        )
        return in_whitelist


__all__ = ["UserListService"]
