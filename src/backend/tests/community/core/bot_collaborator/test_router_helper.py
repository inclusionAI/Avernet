"""Tests for helper functions in bot_collaborator router."""
from __future__ import annotations

from unittest.mock import MagicMock


class TestLockInfoWithHolderName:
    """Tests for _lock_info helper function with holder_name."""

    def test_lock_info_without_holder_name(self):
        """LockInfo can be created without holder_name."""
        from agentclaw.community.adapters.http.bot_collaborator.router import _lock_info
        from datetime import datetime

        lock = MagicMock()
        lock.id = 1
        lock.lock_key = "bot_1:u_owner"
        lock.holder_user_id = "u_user"
        lock.gmt_create = datetime(2024, 1, 1, 12, 0, 0)

        result = _lock_info(lock)

        assert result.id == 1
        assert result.lock_key == "bot_1:u_owner"
        assert result.holder_user_id == "u_user"
        assert result.holder_name is None
        assert result.gmt_create == datetime(2024, 1, 1, 12, 0, 0)

    def test_lock_info_with_holder_name(self):
        """LockInfo can be created with holder_name."""
        from agentclaw.community.adapters.http.bot_collaborator.router import _lock_info
        from datetime import datetime

        lock = MagicMock()
        lock.id = 1
        lock.lock_key = "bot_1:u_owner"
        lock.holder_user_id = "u_user"
        lock.gmt_create = datetime(2024, 1, 1, 12, 0, 0)

        result = _lock_info(lock, holder_name="User Name")

        assert result.id == 1
        assert result.holder_user_id == "u_user"
        assert result.holder_name == "User Name"