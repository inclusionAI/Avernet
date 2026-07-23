"""Application service for frontend-only user-list eligibility checks."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.user_list.repository import UserListRepositoryProtocol
from agentclaw.community.log import get_logger


_CORRECTION_OPERATOR_IDS = frozenset({"330429", "61256"})
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
        # COSEC: correction rights are the explicit product allow-list and
        # remain enforced in the application service for non-HTTP callers.
        if actor_id not in _CORRECTION_OPERATOR_IDS:
            logger.warning("user_list_correction_forbidden actor_id=%s", actor_id)
            raise Forbidden("USER_LIST_CORRECTION_FORBIDDEN")

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
