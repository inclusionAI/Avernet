from unittest.mock import Mock
from agentclaw.community.plugin_api.dws_identity import DwsRolloutIdentity

import pytest

from agentclaw.community.core.system_config.dws_rollout import (
    is_allowed,
    is_stop,
    is_dws_category,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ({}, False),
        ({"mode": "bad"}, False),
        ({"mode": "all", "extra": True}, False),
        ({"mode": "allowlist", "staff_ids": [165137]}, False),
        ({"mode": "disabled", "staff_ids": ["165137"]}, False),
        ({"mode": "allowlist", "staff_ids": ["165137"]}, True),
        ({"mode": "allowlist", "staff_ids": ["other"]}, False),
        ({"mode": "all"}, True),
    ],
)
def test_policy(value, expected):
    store = Mock()
    store.get_config.return_value = value
    assert is_allowed(store, "165137", "pre") is expected
    store.get_config.assert_called_once_with(
        category="dws_hosting", config_key="rollout", env="pre"
    )
    assert not is_allowed(store, "", "pre")


def test_failure_closed_and_environment_separation():
    store = Mock()
    store.get_config.side_effect = lambda **kw: (
        {"mode": "all"} if kw["env"] == "pre" else None
    )
    assert is_allowed(store, "165137", "pre")
    assert not is_allowed(store, "165137", "prod")
    store.get_config.side_effect = RuntimeError("database unavailable")
    assert not is_allowed(store, "165137", "pre")


def test_default_policy_preserves_opaque_ids_and_zeroes():
    store = Mock()
    store.get_config.return_value = {"mode": "allowlist", "staff_ids": ["004232", "UserA"]}
    assert is_allowed(store, "004232", "pre")
    assert not is_allowed(store, "4232", "pre")
    assert not is_allowed(store, "usera", "pre")
    assert is_allowed(store, "UserA", "pre")


def test_comparison_policy_is_called_without_mutating_stored_values():
    store = Mock()
    value = {"mode": "allowlist", "staff_ids": ["alias"]}
    store.get_config.return_value = value
    policy = Mock(spec=DwsRolloutIdentity)
    policy.key.side_effect = lambda v: "canonical" if v in {"actor", "alias"} else ""
    assert is_allowed(store, "actor", "pre", policy)
    assert policy.key.call_count == 2
    assert value == {"mode": "allowlist", "staff_ids": ["alias"]}
    assert not is_allowed(store, "unknown", "pre", policy)
    policy.key.side_effect = ValueError("invalid identity")
    assert not is_allowed(store, "actor", "pre", policy)


@pytest.mark.parametrize(
    "category",
    ["dws_hosting", "DWS_HOSTING", "dws_hosting ", "dws_hósting", "ｄｗｓ_hosting"],
)
def test_category_collation_variants(category):
    assert is_dws_category(category)
    assert not is_dws_category("device")


def test_only_safe_stop_actions_bypass_rollout():
    assert is_stop("enabled", {"enabled": False})
    assert is_stop("takeover", {"taken_over": True})
    assert not is_stop("enabled", {"enabled": True})
    assert not is_stop("enabled", {"enabled": 0})
    assert not is_stop("takeover", {"taken_over": False})
    assert not is_stop("settings.save", {})


def test_pre_seed_is_idempotent_and_preserves_existing_policy():
    import json
    import sqlite3
    from pathlib import Path
    from agentclaw.community.core.system_config import dws_rollout

    sql = Path(dws_rollout.__file__).with_name("dws_rollout_pre.sql").read_text()
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            CREATE TABLE ac_config_category(id INTEGER PRIMARY KEY, category TEXT, category_name TEXT, env TEXT, creator TEXT, modifier TEXT, gmt_create TEXT, gmt_modified TEXT);
            CREATE TABLE ac_config_item(id INTEGER PRIMARY KEY, parent_id INTEGER, config_key TEXT, config_value TEXT, description TEXT, creator TEXT, modifier TEXT, gmt_create TEXT, gmt_modified TEXT);
        """)
        db.executescript(sql)
        db.executescript(sql)
        assert db.execute("SELECT env FROM ac_config_category").fetchall() == [("pre",)]
        values = db.execute("SELECT config_value FROM ac_config_item").fetchall()
        assert len(values) == 1
        assert json.loads(values[0][0]) == {
            "mode": "allowlist",
            "staff_ids": ["165137"],
        }
        db.execute(
            "UPDATE ac_config_item SET config_value = ?", ('{"mode":"disabled"}',)
        )
        db.executescript(sql)
        assert json.loads(
            db.execute("SELECT config_value FROM ac_config_item").fetchone()[0]
        ) == {"mode": "disabled"}
