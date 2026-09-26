"""Unit tests for SystemConfigService — the two-table config CRUD logic.

The service is pure mapping/serialization over ``ConfigRepositoryProtocol``, so
a lightweight in-memory fake repo exercises every branch (value parse/serialize,
category-missing short circuits, datetime isoformat vs. None) without a DB.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from agentclaw.community.core.system_config.models import (
    ConfigCategoryRecord,
    ConfigItemRecord,
)
from agentclaw.community.core.system_config.service import SystemConfigService


def _category(
    *,
    id: int = 1,
    category: str = "device",
    category_name: str = "设备配置",
    description: str | None = "desc",
    env: str = "dev",
    with_times: bool = True,
) -> ConfigCategoryRecord:
    t = datetime(2024, 1, 2, 3, 4, 5) if with_times else None
    return ConfigCategoryRecord(
        id=id,
        category=category,
        category_name=category_name,
        description=description,
        env=env,
        creator="alice",
        modifier="bob",
        gmt_create=t,
        gmt_modified=t,
    )


def _item(
    *,
    id: int = 10,
    parent_id: int = 1,
    config_key: str = "k",
    config_value: str = '{"a": 1}',
    with_times: bool = True,
) -> ConfigItemRecord:
    t = datetime(2024, 1, 2, 3, 4, 5) if with_times else None
    return ConfigItemRecord(
        id=id,
        parent_id=parent_id,
        config_key=config_key,
        config_value=config_value,
        description="d",
        creator="alice",
        modifier="bob",
        gmt_create=t,
        gmt_modified=t,
    )


class FakeConfigRepo:
    """In-memory ConfigRepositoryProtocol fake — records calls, returns set data."""

    def __init__(self) -> None:
        self.categories: list[ConfigCategoryRecord] = []
        self.category_by_key: dict[tuple[str, str], ConfigCategoryRecord] = {}
        self.category_by_id: dict[int, ConfigCategoryRecord] = {}
        self.config_by_key: dict[tuple[int, str], ConfigItemRecord] = {}
        self.configs_by_parent: dict[int, list[ConfigItemRecord]] = {}
        self.all_configs: list[dict] = []
        self.upsert_category_calls: list[dict] = []
        self.upsert_config_calls: list[dict] = []
        self.deleted_categories: list[int] = []
        self.deleted_configs: list[int] = []

    # categories
    def get_category_by_id(self, *, category_id: int):
        return self.category_by_id.get(category_id)

    def get_category(self, *, category: str, env: str):
        return self.category_by_key.get((category, env))

    def list_categories(self, *, env: str):
        return [c for c in self.categories if c.env == env]

    def upsert_category(self, *, category, category_name, env, description=None, operator=None):
        self.upsert_category_calls.append(
            {
                "category": category,
                "category_name": category_name,
                "env": env,
                "description": description,
                "operator": operator,
            }
        )
        return 42

    def delete_category(self, *, category_id: int) -> bool:
        self.deleted_categories.append(category_id)
        return True

    # config items
    def get_config(self, *, config_id: int):
        return None

    def get_config_by_key(self, *, parent_id: int, config_key: str):
        return self.config_by_key.get((parent_id, config_key))

    def upsert_config(self, *, parent_id, config_key, config_value, description=None, operator=None):
        self.upsert_config_calls.append(
            {
                "parent_id": parent_id,
                "config_key": config_key,
                "config_value": config_value,
                "description": description,
                "operator": operator,
            }
        )
        return 99

    def delete_config(self, *, config_id: int) -> bool:
        self.deleted_configs.append(config_id)
        return True

    def list_configs(self, *, parent_id: int):
        return self.configs_by_parent.get(parent_id, [])

    def list_all_configs(self, *, env: str):
        return self.all_configs


def _svc():
    repo = FakeConfigRepo()
    return SystemConfigService(repo), repo


# ── value parse / serialize ───────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ('{"a": 1}', {"a": 1}),
        ("[1, 2, 3]", [1, 2, 3]),
        ("42", 42),
        ("plain string", "plain string"),  # non-JSON → returned as-is
        ("", ""),
    ],
)
def test_parse_value(raw, expected) -> None:
    svc, _ = _svc()
    assert svc._parse_value(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "value,expected",
    [
        ("already a string", "already a string"),
        ({"a": 1}, '{"a": 1}'),
        ([1, 2], "[1, 2]"),
        (42, "42"),
        ("中文", "中文"),  # ensure_ascii=False keeps unicode
    ],
)
def test_serialize_value(value, expected) -> None:
    svc, _ = _svc()
    assert svc._serialize_value(value) == expected


# ── categories ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_list_categories_maps_records_and_filters_env() -> None:
    svc, repo = _svc()
    repo.categories = [
        _category(id=1, category="device", env="dev"),
        _category(id=2, category="system", env="dev", with_times=False),
        _category(id=3, category="other", env="prod"),
    ]

    out = svc.list_categories(env="dev")

    assert [c["id"] for c in out] == [1, 2]
    assert out[0]["category"] == "device"
    assert out[0]["gmt_create"] == "2024-01-02T03:04:05"
    assert out[1]["gmt_create"] is None  # None datetime path


@pytest.mark.unit
def test_get_category_by_id_found_and_missing() -> None:
    svc, repo = _svc()
    repo.category_by_id = {7: _category(id=7, category="security")}

    found = svc.get_category_by_id(category_id=7)
    assert found is not None and found["category"] == "security"

    assert svc.get_category_by_id(category_id=999) is None


@pytest.mark.unit
def test_create_category_delegates_to_upsert() -> None:
    svc, repo = _svc()

    cid = svc.create_category(
        category="device", category_name="设备", description="d", env="dev", operator="op"
    )

    assert cid == 42
    assert repo.upsert_category_calls == [
        {"category": "device", "category_name": "设备", "env": "dev", "description": "d", "operator": "op"}
    ]


@pytest.mark.unit
def test_delete_category_delegates() -> None:
    svc, repo = _svc()
    assert svc.delete_category(category_id=5) is True
    assert repo.deleted_categories == [5]


# ── get / set config ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_config_returns_parsed_value() -> None:
    svc, repo = _svc()
    repo.category_by_key = {("device", "dev"): _category(id=1)}
    repo.config_by_key = {(1, "k"): _item(config_value='{"x": true}')}

    assert svc.get_config(category="device", config_key="k", env="dev") == {"x": True}


@pytest.mark.unit
def test_get_config_none_when_category_missing() -> None:
    svc, _ = _svc()
    assert svc.get_config(category="nope", config_key="k", env="dev") is None


@pytest.mark.unit
def test_get_config_none_when_config_missing() -> None:
    svc, repo = _svc()
    repo.category_by_key = {("device", "dev"): _category(id=1)}
    assert svc.get_config(category="device", config_key="missing", env="dev") is None


@pytest.mark.unit
def test_set_config_serializes_and_upserts() -> None:
    svc, repo = _svc()
    repo.category_by_key = {("device", "dev"): _category(id=3)}

    cid = svc.set_config(
        category="device", config_key="k", config_value={"a": 1}, env="dev",
        description="d", operator="op",
    )

    assert cid == 99
    call = repo.upsert_config_calls[0]
    assert call["parent_id"] == 3
    assert call["config_value"] == '{"a": 1}'  # serialized


@pytest.mark.unit
def test_set_config_returns_zero_when_category_missing() -> None:
    svc, repo = _svc()
    cid = svc.set_config(category="nope", config_key="k", config_value="v", env="dev")
    assert cid == 0
    assert repo.upsert_config_calls == []


# ── delete config ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_delete_config_delegates() -> None:
    svc, repo = _svc()
    assert svc.delete_config(config_id=11) is True
    assert repo.deleted_configs == [11]


@pytest.mark.unit
def test_delete_config_by_key_happy_path() -> None:
    svc, repo = _svc()
    repo.category_by_key = {("device", "dev"): _category(id=1)}
    repo.config_by_key = {(1, "k"): _item(id=55)}

    assert svc.delete_config_by_key(category="device", config_key="k", env="dev") is True
    assert repo.deleted_configs == [55]


@pytest.mark.unit
def test_delete_config_by_key_false_when_category_missing() -> None:
    svc, repo = _svc()
    assert svc.delete_config_by_key(category="nope", config_key="k", env="dev") is False
    assert repo.deleted_configs == []


@pytest.mark.unit
def test_delete_config_by_key_false_when_config_missing() -> None:
    svc, repo = _svc()
    repo.category_by_key = {("device", "dev"): _category(id=1)}
    assert svc.delete_config_by_key(category="device", config_key="x", env="dev") is False
    assert repo.deleted_configs == []


# ── list configs ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_list_configs_maps_and_parses() -> None:
    svc, repo = _svc()
    repo.category_by_key = {("device", "dev"): _category(id=1)}
    repo.configs_by_parent = {
        1: [
            _item(id=10, config_key="a", config_value='{"v": 1}'),
            _item(id=11, config_key="b", config_value="raw", with_times=False),
        ]
    }

    out = svc.list_configs(category="device", env="dev")

    assert [c["id"] for c in out] == [10, 11]
    assert out[0]["config_value"] == {"v": 1}  # parsed JSON
    assert out[1]["config_value"] == "raw"  # non-JSON kept
    assert out[1]["gmt_modified"] is None


@pytest.mark.unit
def test_list_configs_empty_when_category_missing() -> None:
    svc, _ = _svc()
    assert svc.list_configs(category="nope", env="dev") == []


@pytest.mark.unit
def test_list_all_configs_delegates() -> None:
    svc, repo = _svc()
    repo.all_configs = [{"id": 1, "config_key": "k"}]
    assert svc.list_all_configs(env="dev") == [{"id": 1, "config_key": "k"}]
