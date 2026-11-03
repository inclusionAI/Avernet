"""Department names match complete ancestor chains alongside legacy IDs."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.repository.implementations.identity.user_list import UserListRepository
from agentclaw.community.core.user_list.service import UserListService
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.staff_dept import StaffDeptInfo
from tests.community.core.user_list.test_department_matching import add, match


@pytest.fixture
def name_case(world):
    directory = Mock()
    directory.get_dept_by_work_no.return_value = StaffDeptInfo(
        dept_name="示例集团-技术部-研究组", dept_path="0001/0020/0030",
    )
    repository = UserListRepository(world.get(DatabasePlugin))
    return SimpleNamespace(
        repository=repository, directory=directory,
        service=UserListService(repository=repository, staff_dept=directory),
    )


@pytest.mark.parametrize("rule,expected", [
    ("示例集团", True), ("示例集团-技术部", True),
    ("示例集团-技术部-研究组", True), ("技术部-研究组", False),
    ("研究组", False), ("技术部", False), ("示例集团-技术", False),
    ("示例集团-技术部-研究组二", False), ("其他集团-技术部-研究组", False),
    ("0001/0020", True), ("0001/0020/0030", True),
])
def test_complete_name_prefixes_and_legacy_ids(name_case, rule, expected):
    add(name_case, entity_id=rule)
    assert match(name_case) is expected
    name_case.directory.get_dept_by_work_no.assert_called_once_with(work_no="staff")


@pytest.mark.parametrize("path", [None, "", " ", "/0001", "0001//0020", "0001/*", "a" * 1025])
def test_valid_name_matches_with_missing_or_invalid_id_path(name_case, path):
    add(name_case, entity_id="示例集团-技术部")
    name_case.directory.get_dept_by_work_no.return_value = StaffDeptInfo(
        dept_name="示例集团-技术部-研究组", dept_path=path,
    )
    assert match(name_case)


@pytest.mark.parametrize("name", [None, "", " ", "-示例集团", "示例集团-", "示例集团--技术部", "示例集团- -技术部", "示例集团-*", "a" * 1025])
def test_valid_id_path_matches_with_missing_or_invalid_name(name_case, name):
    add(name_case, entity_id="0001/0020")
    name_case.directory.get_dept_by_work_no.return_value = StaffDeptInfo(
        dept_name=name, dept_path="0001/0020/0030",
    )
    assert match(name_case)


@pytest.mark.parametrize("name,path,reason", [
    (None, None, "no_dept"), ("", "", "no_dept"),
    ("示例集团--技术部", "/0001", "invalid_department_data"),
    (" ", " ", "invalid_department_data"),
])
def test_no_valid_department_data_does_not_grant(name_case, name, path, reason, caplog):
    caplog.set_level("INFO")
    add(name_case, entity_id="示例集团")
    name_case.directory.get_dept_by_work_no.return_value = StaffDeptInfo(dept_name=name, dept_path=path)
    assert not match(name_case)
    assert f"reason={reason}" in caplog.text


@pytest.mark.parametrize("name", [" Research ", " Research-Platform "])
def test_single_name_and_outer_whitespace(name_case, name):
    add(name_case, entity_id="Research")
    name_case.directory.get_dept_by_work_no.return_value = StaffDeptInfo(dept_name=name)
    assert match(name_case)


@pytest.mark.parametrize("env,list_type,rule_bot,actual,expected", [
    ("pre", "feature", "all", "personal", True),
    ("pre", "feature", "all", "service", True),
    ("pre", "feature", "personal", "personal", True),
    ("pre", "feature", "service", "service", True),
    ("pre", "feature", "service", "personal", False),
    ("prod", "feature", "all", "personal", False),
    ("pre", "other", "all", "personal", False),
])
def test_name_rules_preserve_scope(name_case, env, list_type, rule_bot, actual, expected):
    add(name_case, entity_id="示例集团-技术部", env=env, user_list_type=list_type, bot_type=rule_bot)
    assert match(name_case, bot_type=actual) is expected
    if not expected:
        name_case.directory.get_dept_by_work_no.assert_not_called()


def test_name_does_not_use_user_subject_rules(name_case):
    add(name_case, entity_id="示例集团-技术部", subject_type="user")
    assert not match(name_case)
    name_case.directory.get_dept_by_work_no.assert_not_called()


def test_name_rule_preserves_user_short_circuit(name_case):
    add(name_case, entity_id="示例集团-技术部")
    add(name_case, entity_id="staff", subject_type="user")
    assert match(name_case)
    name_case.directory.get_dept_by_work_no.assert_not_called()


def test_logs_distinguish_name_and_id_candidates(name_case, caplog):
    caplog.set_level("INFO")
    add(name_case, entity_id="示例集团-技术部")
    assert match(name_case)
    assert "department_names=('示例集团', '示例集团-技术部', '示例集团-技术部-研究组')" in caplog.text
    assert "department_paths=('0001', '0001/0020', '0001/0020/0030')" in caplog.text
    assert "matched_source=department reason=matched" in caplog.text


def test_combined_candidates_are_deduplicated(name_case, monkeypatch):
    add(name_case, entity_id="Research")
    name_case.directory.get_dept_by_work_no.return_value = StaffDeptInfo(dept_name="Research", dept_path="Research")
    original = name_case.repository.exists_any
    seen = []

    def capture(**kwargs):
        if kwargs["subject_type"] == "department":
            seen.append(kwargs["entity_ids"])
        return original(**kwargs)

    monkeypatch.setattr(name_case.repository, "exists_any", capture)
    assert match(name_case)
    assert seen == [("Research",)]
