"""Tests for core/access/services/user_service.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.access.errors import UserNotFoundError
from agentclaw.community.core.access.models import UserInfoRecord
from agentclaw.community.core.access.services.user_service import UserService


def _make_record(user_id="u1", user_type="COMPETE", status="ACCESS") -> UserInfoRecord:
    return UserInfoRecord(id=1, user_id=user_id, user_type=user_type, status=status)


def _make_service(repo=None) -> UserService:
    if repo is None:
        repo = MagicMock()
    return UserService(repository=repo)


class TestListUsers:
    def test_returns_all_users_when_no_filter(self):
        repo = MagicMock()
        repo.list_users.return_value = [_make_record("u1"), _make_record("u2")]
        svc = _make_service(repo)
        result = svc.list_users()
        assert len(result) == 2
        repo.list_users.assert_called_once_with(user_type=None)

    def test_passes_user_type_filter(self):
        repo = MagicMock()
        repo.list_users.return_value = [_make_record()]
        svc = _make_service(repo)
        svc.list_users(user_type="COMPETE")
        repo.list_users.assert_called_once_with(user_type="COMPETE")

    def test_returns_empty_list_when_no_users(self):
        repo = MagicMock()
        repo.list_users.return_value = []
        svc = _make_service(repo)
        assert svc.list_users() == []


class TestGetUser:
    def test_returns_record_when_found(self):
        repo = MagicMock()
        record = _make_record()
        repo.get_user_info.return_value = record
        svc = _make_service(repo)
        result = svc.get_user(user_id="u1", user_type="COMPETE")
        assert result is record

    def test_raises_user_not_found_when_none(self):
        repo = MagicMock()
        repo.get_user_info.return_value = None
        svc = _make_service(repo)
        with pytest.raises(UserNotFoundError) as exc_info:
            svc.get_user(user_id="missing", user_type="COMPETE")
        assert exc_info.value.user_id == "missing"
        assert exc_info.value.user_type == "COMPETE"

    def test_passes_kwargs_to_repo(self):
        repo = MagicMock()
        repo.get_user_info.return_value = _make_record()
        svc = _make_service(repo)
        svc.get_user(user_id="u99", user_type="NORMAL")
        repo.get_user_info.assert_called_once_with(user_id="u99", user_type="NORMAL")


class TestUpsertUser:
    def test_delegates_to_repo(self):
        repo = MagicMock()
        svc = _make_service(repo)
        svc.upsert_user(user_id="u1", user_type="COMPETE", status="ACCESS")
        repo.upsert_user_info.assert_called_once_with(
            user_id="u1", user_type="COMPETE", status="ACCESS"
        )
