"""Unit tests for ConfigComposerInputCollector (Task 15a).

Verifies the concrete collector adapts each source service into the composer's
container-view inputs: skill scope/name derivation, the MCP collect+merge loop,
resource/identity mapping, and the engine_overrides default.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.channel.services.engine_overrides_reader import (
    ChannelEngineOverridesReader,
)
from agentclaw.community.core.config_compose.models import ComposeRequest
from agentclaw.community.core.config_compose.services.collector import (
    ConfigComposerInputCollector,
)


def _req() -> ComposeRequest:
    return ComposeRequest(
        entity_id="staff_u1", bot_id="bot1", user_id="u1", engine_type="openclaw"
    )


def _reader_over(channel_repo) -> ChannelEngineOverridesReader:
    """Wrap a (mock) channel_repo in a REAL reader so the collector's
    engine_overrides tests exercise the collector→reader delegation end to end —
    i.e. they double as the byte-identical draft regression."""
    if channel_repo is None:
        channel_repo = MagicMock()
        channel_repo.get_by_type_and_identity_ids.return_value = []
    return ChannelEngineOverridesReader(channel_repo=channel_repo)


def _collector(*, skill_set_service=None, mcp_config_service=None,
               resource_repository=None,
               identity_service=None, channel_repo=None):
    return ConfigComposerInputCollector(
        skill_set_service_factory=_factory_returning(skill_set_service or MagicMock()),
        mcp_config_service=mcp_config_service or MagicMock(),
        resource_repository=resource_repository or MagicMock(),
        bot_repo=MagicMock(),
        path_factory=MagicMock(),
        identity_service=identity_service or MagicMock(),
        overrides_reader=_reader_over(channel_repo),
    )


def _factory_returning(svc):
    f = MagicMock()
    f.create.return_value = svc
    return f


@pytest.mark.unit
def test_skills_emit_only_shared_local_skipped():
    """git:// (shared) skills are emitted; local:// (user upload) skills are
    engine-owned and intentionally NOT emitted (promotion packs their bytes)."""
    svc = MagicMock()
    svc.get_active_skills.return_value = [
        {"git_path": "git://team/weather", "name": "weather"},
        {"git_path": "local:///aidesktop/aidesktop_prod/bolt_data/staff_u1/bot7/openclaw/workspace/skills/skills-local/my-skill",
         "name": "my-skill"},
        {"git_path": "local://skills-local/teclaw-skill", "name": "teclaw-skill"},
    ]
    skills = _collector(skill_set_service=svc).skills(_req())
    assert [(s.name, s.scope) for s in skills] == [
        ("weather", "shared"),  # git:// → shared; both local:// skipped
    ]
    # shared: skill-repo store, repo-relative key straight from git_path (env-free)
    assert skills[0].store == "skill-repo"
    assert skills[0].path == "team/weather"


@pytest.mark.unit
def test_local_skill_not_emitted_engine_owned():
    """A user (skills-local) skill is engine-owned: the collector does NOT emit a
    ref for it (the running container auto-discovers it; the publish-time gather
    snapshots its bytes). Only the shared (git://) skill is emitted."""
    from agentclaw.community.core.config_compose.models import ComposeRequest

    svc = MagicMock()
    svc.get_active_skills.return_value = [
        {"git_path": "git://team/weather", "name": "weather"},
        {"git_path": "local:///aidesktop/aidesktop_prod/bolt_data/staff_u1/bot7/openclaw/workspace/skills/skills-local/my-skill",
         "name": "my-skill"},
    ]
    collector = ConfigComposerInputCollector(
        skill_set_service_factory=_factory_returning(svc),
        mcp_config_service=MagicMock(),
        resource_repository=MagicMock(),
        bot_repo=MagicMock(),
        path_factory=MagicMock(),
        identity_service=MagicMock(),
        overrides_reader=_reader_over(None),
    )
    skills = collector.skills(
        ComposeRequest(entity_id="staff_u1", bot_id="bot7", user_id="u1", engine_type="openclaw")
    )
    assert len(skills) == 1
    assert skills[0].scope == "shared"
    assert skills[0].store == "skill-repo"
    assert skills[0].path == "team/weather"


@pytest.mark.unit
def test_mcps_run_collect_then_per_server_merge():
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [
        {"server_code": "a"}, {"server_code": "b"}
    ]
    # Center has no extra detail for these — leaves the bare dicts unchanged.
    svc.mcp_center.get_mcp_detail.return_value = None
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = ("kee", {"h": "v"}, "PROD", "http")
    inputs = _collector(skill_set_service=svc, mcp_config_service=mcp_cfg).mcps(_req())
    assert [i.mcp_data["server_code"] for i in inputs] == ["a", "b"]
    assert inputs[0].api_key == "kee"
    assert inputs[0].headers == {"h": "v"}
    assert inputs[0].endpoint_env == "PROD"
    assert inputs[0].transport_protocol == "http"
    assert mcp_cfg.build_mcp_sync_payload.call_count == 2


@pytest.mark.unit
def test_mcps_enrich_bare_association_with_center_endpoints():
    # collect_bot_active_mcps returns only the skill-set association fields (no
    # endpoints). The collector must fetch MCP Center detail per server and merge
    # it in, so the composer downstream has endpoints to select from. Regression:
    # teclaw whole-artifact compose previously saw endpoints=[] → "no usable
    # endpoint".
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [{"server_code": "dima"}]
    svc.mcp_center.get_mcp_detail.return_value = {
        "serverCode": "dima",
        "runMode": "REMOTE",
        "endpoints": [
            {"env": "PROD", "networkType": "INTRANET",
             "transportProtocol": "STREAMABLE_HTTP",
             "url": "https://dima.example/mcp"},
        ],
    }
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)
    inputs = _collector(skill_set_service=svc, mcp_config_service=mcp_cfg).mcps(_req())
    svc.mcp_center.get_mcp_detail.assert_called_once_with("dima")
    assert inputs[0].mcp_data["endpoints"] == [
        {"env": "PROD", "networkType": "INTRANET",
         "transportProtocol": "STREAMABLE_HTTP",
         "url": "https://dima.example/mcp"},
    ]


@pytest.mark.unit
def test_mcps_skip_center_lookup_when_no_server_code():
    # A malformed association without a server_code is left untouched and never
    # hits MCP Center (nothing to look up by).
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [{"name": "orphan"}]
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)
    inputs = _collector(skill_set_service=svc, mcp_config_service=mcp_cfg).mcps(_req())
    svc.mcp_center.get_mcp_detail.assert_not_called()
    assert inputs[0].mcp_data == {"name": "orphan"}


@pytest.mark.unit
def test_mcps_center_error_leaves_bare_dict_unchanged():
    # A Center fetch failure is best-effort: the bare dict flows through unchanged
    # (the composer surfaces the "no usable endpoint" error, same as before).
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [{"server_code": "boom"}]
    svc.mcp_center.get_mcp_detail.side_effect = RuntimeError("center down")
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)
    inputs = _collector(skill_set_service=svc, mcp_config_service=mcp_cfg).mcps(_req())
    assert inputs[0].mcp_data == {"server_code": "boom"}


@pytest.mark.unit
def test_mcps_preserve_local_fields_when_center_lacks_them():
    # Center detail merges over the bare dict but locally-set fields absent from
    # Center (e.g. a default MCP's headers) survive the merge.
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [
        {"server_code": "d", "headers": {"x-ling-auth": "tok"}}
    ]
    svc.mcp_center.get_mcp_detail.return_value = {
        "serverCode": "d", "runMode": "REMOTE", "endpoints": []
    }
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)
    inputs = _collector(skill_set_service=svc, mcp_config_service=mcp_cfg).mcps(_req())
    assert inputs[0].mcp_data["headers"] == {"x-ling-auth": "tok"}
    assert inputs[0].mcp_data["runMode"] == "REMOTE"


@pytest.mark.unit
def test_resources_emit_absolute_container_path(monkeypatch):
    # ResourceService is constructed inside resources(); stub the class. Each file
    # resource is emitted as data_dir + its relative path (a resolvable container
    # path), NOT its origin label r.source ('upload'/'manual'). Non-file resources
    # (no path — URL/link/node) are dropped.
    import agentclaw.community.core.config_compose.services.collector as collector_mod
    import agentclaw.community.core.resources.services.resource_service as rs_mod

    monkeypatch.setattr(collector_mod, "get_bolt_base_dir", lambda: Path("/bolt"))

    r1 = MagicMock(source="upload")
    r1.name = "r1"
    r1.path = "docs/a.md"
    r2 = MagicMock(source="manual")  # URL/link resource — no file path
    r2.name = "r2"
    r2.path = None
    fake_svc = MagicMock()
    fake_svc.data_dir = Path("/bolt/staff_u1/bot1/openclaw/workspace/data")
    fake_svc.list_resources.return_value = [r1, r2]
    monkeypatch.setattr(rs_mod, "ResourceService", lambda **kw: fake_svc)
    files = _collector().resources(_req())
    # emitted as bot-data store + bolt_data-relative key (data_dir+path minus /bolt root)
    assert [(f.name, f.store, f.path) for f in files] == [
        ("r1", "bot-data", "staff_u1/bot1/openclaw/workspace/data/docs/a.md"),
    ]


@pytest.mark.unit
def test_bot_files_always_empty():
    # ac_file is fully retired: teclaw owns its files in the container (gathered
    # at promotion), and no other engine populated ac_file. bot_files is now a
    # protocol-required no-op.
    assert _collector().bot_files(_req()) == []


@pytest.mark.unit
def test_identity_files_use_sync_existence_check(tmp_path, monkeypatch):
    import agentclaw.community.core.config_compose.services.collector as collector_mod

    monkeypatch.setattr(collector_mod, "get_bolt_base_dir", lambda: tmp_path)

    existing = tmp_path / "AGENTS.md"
    existing.write_text("x")
    ident = MagicMock()
    # exists() only for AGENTS.md; everything else missing.
    ident.get_bot_file_path.side_effect = lambda et, eid, bid, ft: (
        existing if ft == "AGENTS.md" else tmp_path / f"{ft}.missing"
    )
    files = _collector(identity_service=ident).identity_files(_req())
    assert [f.name for f in files] == ["AGENTS.md"]
    # bot-data store + bolt_data-relative key (file path minus the bolt_data root)
    assert files[0].store == "bot-data"
    assert files[0].path == "AGENTS.md"


@pytest.mark.unit
def test_engine_overrides_default_empty():
    assert _collector().engine_overrides(_req()) == {}


# ── engine_overrides: DingTalk channels (Piece A) ───────────────────────────


def _channel_record(**kw):
    """Build a ChannelRecord for the collector's engine_overrides read.

    Defaults to an active (status='1'), no-stage dingding row whose JSON
    ``config`` carries the stored snake_case fields the frontend persists.
    """
    from agentclaw.community.core.channel.services.repositories import ChannelRecord

    config = kw.pop("config", None)
    if config is None:
        config = {
            "client_id": "cid-1",
            "client_secret": "sec-1",
            "dm_policy": "open",
            "card_template_id": "tpl-1",
            "card_template_key": "key-1",
        }
    base = dict(
        id=1, type="dingding", description=None, identity_id="u1",
        bind_bot_id="bot1", config=config, status="1", deleted=0,
        gmt_create=None, gmt_modified=None, env="prod", stage=None,
    )
    base.update(kw)
    return ChannelRecord(**base)


def _repo_returning(records):
    repo = MagicMock()
    repo.get_by_type_and_identity_ids.return_value = records
    return repo


@pytest.mark.unit
def test_engine_overrides_active_channel_maps_neutral_account():
    """An active, no-stage dingding row → one engine-neutral snake_case account."""
    repo = _repo_returning([_channel_record()])
    out = _collector(channel_repo=repo).engine_overrides(_req())

    # Lookup keyed on (type, [user_id, aideskdingding], bind_bot_id).
    repo.get_by_type_and_identity_ids.assert_called_once_with(
        type="dingding", identity_ids=["u1", "aideskdingding"], bind_bot_id="bot1"
    )
    dingding = out["channels"]["dingding"]
    assert dingding["enabled"] is True
    (acct,) = dingding["accounts"]
    assert acct == {
        "client_id": "cid-1",
        "client_secret": "sec-1",
        "robot_code": "cid-1",          # always == client_id (openclaw parity)
        "dm_policy": "open",
        "group_policy": "open",
        "message_type": "markdown",     # enable_streaming_cards defaults False
        "enable_streaming_cards": False,
        "card_template_id": "tpl-1",
        "card_template_key": "key-1",
    }


@pytest.mark.unit
def test_engine_overrides_streaming_cards_robot_code_and_omitted_nulls():
    """message_type defaults to 'card' when streaming cards on; robot_code is
    always client_id (a stored robot_code is ignored, matching openclaw); and
    missing client_secret / empty card-template fields are omitted, never null."""
    rec = _channel_record(config={
        "client_id": "cid-1",  # no client_secret stored
        "robot_code": "robot-9", "enable_streaming_cards": True,
    })
    out = _collector(channel_repo=_repo_returning([rec])).engine_overrides(_req())
    acct = out["channels"]["dingding"]["accounts"][0]
    assert acct["message_type"] == "card"
    assert acct["robot_code"] == "cid-1"   # stored robot_code ignored (openclaw parity)
    assert "client_secret" not in acct     # missing secret omitted, not null
    assert "card_template_id" not in acct and "card_template_key" not in acct


@pytest.mark.unit
@pytest.mark.parametrize("status,stage,included", [
    ("1", None, True),
    ("1", "", True),
    ("1", "draft", True),
    ("0", None, False),       # inactive
    ("1", "verify", False),   # external-deployment stage
    ("1", "online", False),   # external-deployment stage
])
def test_engine_overrides_status_stage_filter(status, stage, included):
    rec = _channel_record(status=status, stage=stage)
    out = _collector(channel_repo=_repo_returning([rec])).engine_overrides(_req())
    assert (out != {}) is included


@pytest.mark.unit
def test_engine_overrides_multiple_accounts_and_dedup():
    """Distinct client_ids → multiple accounts; a duplicate client_id is deduped."""
    recs = [
        _channel_record(id=1, config={"client_id": "a", "client_secret": "sa"}),
        _channel_record(id=2, config={"client_id": "b", "client_secret": "sb"}),
        _channel_record(id=3, config={"client_id": "a", "client_secret": "sa2"}),
    ]
    out = _collector(channel_repo=_repo_returning(recs)).engine_overrides(_req())
    accounts = out["channels"]["dingding"]["accounts"]
    assert [a["client_id"] for a in accounts] == ["a", "b"]


@pytest.mark.unit
def test_engine_overrides_no_active_channels_returns_empty():
    """Only inactive/non-live rows → {} (no channels key); artifact keeps default."""
    recs = [
        _channel_record(status="0"),
        _channel_record(status="1", stage="online"),
    ]
    out = _collector(channel_repo=_repo_returning(recs)).engine_overrides(_req())
    assert out == {}


# NOTE: the former ``test_bot_data_ref_key_equals_teclaw_write_key`` lived here but
# was tautological — it set ``store_base = bolt_base`` and ``write_key =
# host.lstrip('/')``, neither of which is the real teclaw key, so it passed while
# the write key actually diverged from the artifact ref (the regression this SDD
# fixes). The real write==ref invariant now lives in
# ``test_config_composer.py::test_store_key_for_equals_artifact_ref_full_key``,
# asserted against the real ``teclaw/{env}/bolt_data`` store base.


# ── teclaw: files owned by the running container (no ac_file / no probe) ──

def _teclaw_req() -> ComposeRequest:
    return ComposeRequest(
        entity_id="staff_u1", bot_id="bot1", user_id="u1", engine_type="teclaw"
    )


@pytest.mark.unit
def test_resources_empty_for_teclaw():
    # ac_resource files live under /workspace, captured by the engine gather at
    # promotion — so teclaw compose emits no resource refs.
    collector = _collector()
    assert collector.resources(_teclaw_req()) == []


@pytest.mark.unit
def test_identity_files_empty_for_teclaw_without_probing():
    identity_service = MagicMock()
    collector = _collector(identity_service=identity_service)
    assert collector.identity_files(_teclaw_req()) == []
    identity_service.get_bot_file_path.assert_not_called()
