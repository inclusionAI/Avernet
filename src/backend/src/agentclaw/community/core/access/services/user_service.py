"""User management service — CRUD operations on ac_user_info via PolicyRepository."""
from injector import inject

from agentclaw.community.core.access.errors import UserNotFoundError
from agentclaw.community.core.access.repository import PolicyRepository
from agentclaw.community.core.access.models import UserInfoRecord
from agentclaw.community.log import get_logger

logger = get_logger()


class UserService:
    """Thin service encapsulating user CRUD previously inlined in the router."""

    @inject
    def __init__(self, repository: PolicyRepository) -> None:
        self._repo = repository

    def list_users(self, *, user_type: str | None = None) -> list[UserInfoRecord]:
        """Return all users, optionally filtered by *user_type*."""
        return self._repo.list_users(user_type=user_type)

    def get_user(self, *, user_id: str, user_type: str) -> UserInfoRecord:
        """Return a single user or raise :class:`UserNotFoundError`."""
        record = self._repo.get_user_info(user_id=user_id, user_type=user_type)
        if record is None:
            raise UserNotFoundError(user_id, user_type)
        return record

    def upsert_user(self, *, user_id: str, user_type: str, status: str) -> None:
        """Insert or update a user record (ON DUPLICATE KEY UPDATE status)."""
        self._repo.upsert_user_info(user_id=user_id, user_type=user_type, status=status)
