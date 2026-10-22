"""Shared-table eligibility, scoped deletion and directory short circuiting."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.user_list.service import UserListService
from agentclaw.community.core.repository.implementations.identity.user_list import UserListRepository
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.staff_dept import DeptLookupError, StaffDeptInfo


@pytest.fixture
def eligibility(world):
    repository = UserListRepository(world.get(DatabasePlugin))
    directory = Mock()
    directory.get_dept_by_work_no.return_value = StaffDeptInfo(
        dept_no="42", dept_name="Research", dept_path="Group/Platform/Research",
    )
    service = UserListService(repository=repository, staff_dept=directory)
    return SimpleNamespace(repository=repository, directory=directory, service=service)


def add(case, *, entity_id="Group/Platform", subject_type="department", bot_type="all", env="pre", user_list_type="feature"):
    return case.service.correct_membership(
        actor_id="operator", entity_id=entity_id, subject_type=subject_type,
        bot_type=bot_type, user_list_type=user_list_type, env=env, in_whitelist=True,
    )


def match(case, *, bot_type="personal", env="pre", user_list_type="feature"):
    return case.service.matches_user(
        entity_id="staff", user_list_type=user_list_type, env=env, bot_type=bot_type,
    )


@pytest.mark.parametrize("subject_type", ["user", "department"])
@pytest.mark.parametrize("rule_type,actual,expected", [
    ("all", "personal", True), ("all", "service", True),
    ("personal", "personal", True), ("personal", "service", False),
    ("service", "personal", False), ("service", "service", True),
])
def test_rule_bot_scope(eligibility, subject_type, rule_type, actual, expected):
    add(eligibility, entity_id="staff" if subject_type == "user" else "Group/Platform", subject_type=subject_type, bot_type=rule_type)
    assert match(eligibility, bot_type=actual) is expected
    if subject_type == "user" or not expected:
        eligibility.directory.get_dept_by_work_no.assert_not_called()


@pytest.mark.parametrize("path,expected", [
    ("Group/Platform", True), ("Group/Platform/Research", True),
    ("Group/PlatformExtra", False), ("Group/platform/Research", False),
    (" Group/Platform/ ", True), (None, False), ("", False),
    ("/Group/Platform", False), ("Group//Platform", False), ("Group/*", False),
    ("a" * 1025, False),
])
def test_department_path_boundaries(eligibility, path, expected):
    add(eligibility)
    eligibility.directory.get_dept_by_work_no.return_value = StaffDeptInfo(dept_path=path)
    assert match(eligibility) is expected


def test_user_match_short_circuits_directory(eligibility):
    add(eligibility, entity_id="staff", subject_type="user")
    add(eligibility)
    eligibility.directory.get_dept_by_work_no.side_effect = AssertionError("must not query")
    assert match(eligibility)


def test_scope_isolation_and_no_rules_skip_directory(eligibility):
    assert not match(eligibility)
    add(eligibility, env="prod")
    add(eligibility, user_list_type="other")
    assert not match(eligibility)
    eligibility.directory.get_dept_by_work_no.assert_not_called()


def test_directory_failure_does_not_grant_and_logs_reason(eligibility, caplog):
    caplog.set_level("INFO")
    add(eligibility)
    eligibility.directory.get_dept_by_work_no.side_effect = DeptLookupError("CREDENTIAL_SENTINEL")
    assert not match(eligibility)
    assert "directory_error" in caplog.text
    assert "CREDENTIAL_SENTINEL" not in caplog.text


@pytest.mark.parametrize("actual", ["all", "desktop", "", None])
def test_invalid_actual_bot_type_rejected(eligibility, actual):
    add(eligibility, entity_id="staff", subject_type="user")
    with pytest.raises(ValueError):
        match(eligibility, bot_type=actual)


@pytest.mark.parametrize("subject,bot,path", [
    ("bad", "all", "staff"), ("user", "bad", "staff"),
    ("department", "all", "/Group"), ("department", "all", "Group//Platform"),
    ("department", "all", "*"), ("department", "all", "a" * 1025),
])
def test_invalid_correction_never_persists(eligibility, subject, bot, path):
    with pytest.raises(ValueError):
        add(eligibility, subject_type=subject, bot_type=bot, entity_id=path)
    assert not match(eligibility)


def test_all_and_specific_records_coexist_and_delete_precisely(eligibility):
    for subject in ("user", "department"):
        for bot_type in ("all", "service", "personal"):
            for _ in range(2):
                add(eligibility, entity_id="staff", subject_type=subject, bot_type=bot_type)
    eligibility.service.correct_membership(
        actor_id="operator", entity_id="staff", user_list_type="feature", env="pre", in_whitelist=False,
    )
    assert not eligibility.service.is_in_user_list(entity_id="staff", user_list_type="feature", env="pre")
    assert match(eligibility, bot_type="service")
    assert match(eligibility, bot_type="personal")
    from agentclaw.community.core.user_list.models import EntityUserListModel
    with eligibility.repository._db.orm_session() as session:
        assert session.query(EntityUserListModel).count() == 5


def test_removing_specific_record_does_not_override_all(eligibility):
    add(eligibility)
    add(eligibility, bot_type="personal")
    eligibility.service.correct_membership(
        actor_id="operator", entity_id="Group/Platform", subject_type="department", bot_type="personal",
        user_list_type="feature", env="pre", in_whitelist=False,
    )
    assert match(eligibility)
    eligibility.service.correct_membership(
        actor_id="operator", entity_id="Group/Platform", subject_type="department",
        user_list_type="feature", env="pre", in_whitelist=False,
    )
    assert not match(eligibility)


def test_real_di_provider(world):
    service = world.get(UserListService)
    service.correct_membership(actor_id="operator", entity_id="staff", user_list_type="feature", env="pre", in_whitelist=True)
    assert service.matches_user(entity_id="staff", user_list_type="feature", env="pre", bot_type="personal")


def test_sqlite_parallel_independent_sessions(eligibility):
    def write(index):
        add(eligibility, entity_id=f"staff_{index}", subject_type="user", bot_type="personal")
        return eligibility.service.matches_user(entity_id=f"staff_{index}", user_list_type="feature", env="pre", bot_type="personal")
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(executor.map(write, range(12)))


def test_database_failure_is_logged_and_never_allowed(eligibility, monkeypatch, caplog):
    caplog.set_level("INFO")
    def fail(**kwargs):
        raise RuntimeError("DB_CREDENTIAL_SENTINEL")
    monkeypatch.setattr(eligibility.repository, "exists_any", fail)
    with pytest.raises(RuntimeError):
        match(eligibility)
    assert "reason=db_error" in caplog.text
    assert "DB_CREDENTIAL_SENTINEL" not in caplog.text
    eligibility.directory.get_dept_by_work_no.assert_not_called()


def test_staff_error_reexport_preserves_exception_identity():
    from agentclaw.community.adapters.http.openapi_v1.errors import DeptLookupError as LegacyError
    assert LegacyError is DeptLookupError


def test_personal_repository_value_error_is_not_swallowed(eligibility, monkeypatch, caplog):
    caplog.set_level("INFO")
    def fail(**kwargs):
        raise ValueError("REPOSITORY_FAILURE_SENTINEL")
    monkeypatch.setattr(eligibility.repository, "exists_any", fail)
    with pytest.raises(ValueError, match="REPOSITORY_FAILURE_SENTINEL"):
        match(eligibility)
    assert "reason=db_error" in caplog.text
    assert "REPOSITORY_FAILURE_SENTINEL" not in caplog.text
