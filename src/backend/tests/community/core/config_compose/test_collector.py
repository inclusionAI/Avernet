"""Unit tests for ConfigComposerInputCollector (Task 15a).

Verifies the concrete collector adapts each source service into the composer's
container-view inputs: skill scope/name derivation, the MCP collect+merge loop,
resource/identity mapping, and the engine_overrides default.
"""
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from agentclaw.community.core.channel.services.engine_overrides_reader import (
    ChannelEngineOverridesReader,
)
from agentclaw.community.core.config_compose.models import ComposeRequest, StdioLaunch
from agentclaw.community.core.config_compose.services.collector import (
    _MCP_DETAIL_WORKERS,
    ConfigComposerInputCollector,
    McpDetailUnavailableError,
)
from agentclaw.community.core.config_compose.services.mcporter_composer import (
    McporterComposeError,
)
from agentclaw.community.core.mcp.services.local_mcp_registry import LocalMCPRegistry
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)
from tests.community.core.bot_config_manifest.cli_tools._fakes import (
    FakeCliToolRepo,
)


def _req(engine_type: str = "openclaw") -> ComposeRequest:
    return ComposeRequest(
        entity_id="staff_u1", bot_id="bot1", user_id="u1", engine_type=engine_type
    )


def _reader_over(channel_repo) -> ChannelEngineOverridesReader:
    """Wrap a (mock) channel_repo in a REAL reader so the collector's
    engine_overrides tests exercise the collector→reader delegation end to end —
    i.e. they double as the byte-identical draft regression."""
    if channel_repo is None:
        channel_repo = MagicMock()
        channel_repo.get_by_type_and_identity_ids.return_value = []
    return ChannelEngineOverridesReader(channel_repo=channel_repo)


def _ready_center_store():
    store = MagicMock()
    store.verify_version.return_value = True
    return store


def _collector(*, skill_set_service=None, mcp_config_service=None,
               resource_repository=None,
               identity_service=None, channel_repo=None,
               local_mcp_registry=None, managed_files_reader=None,
               cli_tool_repository=None):
    center_store = _ready_center_store()
    return ConfigComposerInputCollector(
        skill_set_service_factory=_factory_returning(skill_set_service or MagicMock()),
        mcp_config_service=mcp_config_service or MagicMock(),
        resource_repository=resource_repository or MagicMock(),
        bot_repo=MagicMock(),
        path_factory=MagicMock(),
        identity_service=identity_service or MagicMock(),
        overrides_reader=_reader_over(channel_repo),
        center_store=center_store,
        cli_tool_repository=cli_tool_repository or FakeCliToolRepo(),
        # Default to an EMPTY registry rather than the production default, which
        # would read the repo's bundled local-mcp-servers.yaml off disk and make
        # every test here depend on that file's contents.
        local_mcp_registry=local_mcp_registry or _registry_over({}),
        managed_files_reader=managed_files_reader,
    )


def _registry_over(servers: dict) -> LocalMCPRegistry:
    """A REAL registry over a temp catalog, so these tests exercise its actual
    normalization (flat ``command``/``args``/``env`` → ``stdioConfigs`` with
    ``arguments``/``envVariables``) rather than a mock's idea of it."""
    path = Path(tempfile.mkdtemp()) / "local-mcp-servers.yaml"
    path.write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")
    return LocalMCPRegistry(path)


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
def test_skills_consume_the_delegated_get_active_skills_dict_contract():
    """``get_active_skills`` now delegates to the capability reader and emits
    exactly these keys (``id``, ``name``, ``git_path``, ``skill_uuid``,
    ``sc_version_number``); the collector must need nothing beyond them."""
    svc = MagicMock()
    svc.get_active_skills.return_value = [
        {
            "id": "7",
            "name": "weather",
            "git_path": "git://team/weather",
            "skill_uuid": None,
            "sc_version_number": None,
        },
        {
            "id": "9",
            "name": "my-skill",
            "git_path": "local://my-skill",
            "skill_uuid": None,
            "sc_version_number": None,
        },
    ]
    skills = _collector(skill_set_service=svc).skills(_req())
    assert [(s.name, s.scope, s.store, s.path) for s in skills] == [
        ("weather", "shared", "skill-repo", "team/weather"),
    ]


@pytest.mark.unit
def test_center_skill_requires_and_emits_exact_store_identity():
    svc = MagicMock()
    svc.get_active_skills.return_value = [
        {
            "id": "10",
            "name": "center-weather",
            "git_path": "center://public-weather",
            "skill_uuid": "00000000-0000-4000-8000-000000000010",
            "sc_version_number": "1.0.0",
        }
    ]

    skills = _collector(skill_set_service=svc).skills(_req("teclaw"))

    assert [(s.name, s.scope, s.store, s.path) for s in skills] == [
        (
            "center-weather",
            "shared",
            "skill-center",
            "00000000-0000-4000-8000-000000000010/1.0.0",
        )
    ]


@pytest.mark.unit
def test_center_skill_without_exact_version_fails_closed():
    svc = MagicMock()
    svc.get_active_skills.return_value = [
        {
            "id": "10",
            "name": "center-weather",
            "git_path": "center://public-weather",
            "skill_uuid": "00000000-0000-4000-8000-000000000010",
            "sc_version_number": None,
        }
    ]

    with pytest.raises(ValueError, match="exact"):
        _collector(skill_set_service=svc).skills(_req("teclaw"))


@pytest.mark.unit
def test_center_skill_with_missing_exact_store_version_fails_closed():
    svc = MagicMock()
    svc.get_active_skills.return_value = [
        {
            "id": "10",
            "name": "center-weather",
            "git_path": "center://public-weather",
            "skill_uuid": "00000000-0000-4000-8000-000000000010",
            "sc_version_number": "1.0.0",
        }
    ]
    collector = _collector(skill_set_service=svc)
    collector._center_store.verify_version.return_value = False

    with pytest.raises(ValueError, match="Store Version is unavailable"):
        collector.skills(_req("teclaw"))


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
        center_store=_ready_center_store(),
        cli_tool_repository=FakeCliToolRepo(),
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
    # Center carries both codes. A resolvable record is now a precondition for a
    # remote server: an empty lookup fails the compose (see the raise cases below).
    svc.mcp_center.get_mcp_detail.return_value = {"runMode": "REMOTE", "endpoints": []}
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
def test_mcps_require_strict_policy_context_for_a_complete_artifact():
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = []

    assert _collector(skill_set_service=svc).mcps(_req()) == []

    svc.collect_bot_active_mcps.assert_called_once_with(
        entity_id="staff_u1",
        bot_id="bot1",
        user_id="u1",
        entity_type="staff",
        engine_type="openclaw",
        strict_policy_context=True,
    )


@pytest.mark.unit
def test_one_compose_builds_one_skill_set_service():
    """``skills`` and ``mcps`` of one compose share a single service.

    Building it is not free — the factory re-resolves the bot's workspace
    paths, re-reads the bot row, and mints a ``SkillService`` whose
    construction mkdirs against the shared ``/aidesktop`` mount. The request's
    identifiers fully determine the result, so a compose that built it twice
    paid for the same object twice.
    """
    svc = MagicMock()
    svc.get_active_skills.return_value = []
    svc.collect_bot_active_mcps.return_value = []
    factory = _factory_returning(svc)
    collector = ConfigComposerInputCollector(
        skill_set_service_factory=factory,
        mcp_config_service=MagicMock(),
        resource_repository=MagicMock(),
        bot_repo=MagicMock(),
        path_factory=MagicMock(),
        identity_service=MagicMock(),
        overrides_reader=_reader_over(None),
        center_store=_ready_center_store(),
        cli_tool_repository=FakeCliToolRepo(),
        local_mcp_registry=_registry_over({}),
    )

    req = _req()
    collector.skills(req)
    collector.mcps(req)

    assert factory.create.call_count == 1


@pytest.mark.unit
def test_a_memoized_service_never_crosses_from_one_bot_to_another():
    """The memo is per-request, so bot B's compose cannot observe bot A's service.

    The collector is a process-wide singleton and compose runs on a thread
    pool, so a memo held on the collector would eventually hand one bot's
    per-bot service — its workspace paths, its bot row — to another bot's
    compose. Two requests, two services, however many calls each makes.
    """
    factory = MagicMock()
    services = [MagicMock(), MagicMock()]
    for svc in services:
        svc.get_active_skills.return_value = []
        svc.collect_bot_active_mcps.return_value = []
    factory.create.side_effect = services
    collector = ConfigComposerInputCollector(
        skill_set_service_factory=factory,
        mcp_config_service=MagicMock(),
        resource_repository=MagicMock(),
        bot_repo=MagicMock(),
        path_factory=MagicMock(),
        identity_service=MagicMock(),
        overrides_reader=_reader_over(None),
        center_store=_ready_center_store(),
        cli_tool_repository=FakeCliToolRepo(),
        local_mcp_registry=_registry_over({}),
    )

    bot_a = ComposeRequest(
        entity_id="staff_u1", bot_id="botA", user_id="u1", engine_type="openclaw"
    )
    bot_b = ComposeRequest(
        entity_id="staff_u2", bot_id="botB", user_id="u2", engine_type="openclaw"
    )
    collector.skills(bot_a)
    collector.skills(bot_b)
    collector.mcps(bot_b)

    assert factory.create.call_count == 2
    assert [
        call.kwargs["bot_id"] for call in factory.create.call_args_list
    ] == ["botA", "botB"]
    # Bot B's second call reused bot B's service, not bot A's.
    services[0].collect_bot_active_mcps.assert_not_called()
    services[1].collect_bot_active_mcps.assert_called_once()


@pytest.mark.unit
def test_mcps_reuse_the_set_the_request_already_carries():
    """A resolved set on the request replaces the collector's own read.

    Whole-artifact delivery resolves the effective MCP set during plan
    resolution and threads it here; re-collecting would put the identical
    query against the identical database microseconds later. The threaded
    entries are the bare associations ``collect_bot_active_mcps`` returns, so
    everything downstream — Center enrichment, the per-server merge — still
    runs over them.
    """
    svc = MagicMock()
    svc.mcp_center.get_mcp_detail.return_value = {
        "runMode": "REMOTE", "endpoints": []
    }
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = ("kee", {}, "PROD", "http")

    inputs = _collector(skill_set_service=svc, mcp_config_service=mcp_cfg).mcps(
        ComposeRequest(
            entity_id="staff_u1", bot_id="bot1", user_id="u1",
            engine_type="teclaw",
            effective_mcps=({"server_code": "a"}, {"server_code": "b"}),
        )
    )

    svc.collect_bot_active_mcps.assert_not_called()
    assert [i.mcp_data["server_code"] for i in inputs] == ["a", "b"]
    assert mcp_cfg.build_mcp_sync_payload.call_count == 2


@pytest.mark.unit
def test_an_empty_carried_set_is_not_a_missing_one():
    """``()`` means "this bot has no MCPs", not "nobody resolved them".

    A falsy check here would re-read the database for exactly the bots the
    reuse is cheapest for, and — worse — could compose a set the projection
    that called it never declared.
    """
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [{"server_code": "ghost"}]

    inputs = _collector(skill_set_service=svc).mcps(
        ComposeRequest(
            entity_id="staff_u1", bot_id="bot1", user_id="u1",
            engine_type="teclaw", effective_mcps=(),
        )
    )

    assert inputs == []
    svc.collect_bot_active_mcps.assert_not_called()


_HITL_CATALOG = {
    "hitl": {
        "command": "python3",
        "args": ["/home/admin/hitl/hitl_mcp_server.py"],
        "env": {"MCP_TRANSPORT": "stdio"},
    }
}


def _mcps_with(servers, *, center_detail, registry_catalog):
    """Run ``mcps()`` over one collected server code, with the given Center reply."""
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = servers
    svc.mcp_center.get_mcp_detail.return_value = center_detail
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)
    return _collector(
        skill_set_service=svc,
        mcp_config_service=mcp_cfg,
        local_mcp_registry=_registry_over(registry_catalog),
    ).mcps(_req())


@pytest.mark.unit
def test_mcps_resolve_stdio_launch_from_registry():
    """A server in the local registry arrives at the composer as the local form.

    Also pins the key-name translation: the registry normalizes to
    ``arguments``/``envVariables``, the compose input speaks ``args``/``env``.
    """
    inputs = _mcps_with(
        [{"server_code": "hitl"}], center_detail=None, registry_catalog=_HITL_CATALOG
    )

    assert inputs[0].stdio == StdioLaunch(
        command="python3",
        args=["/home/admin/hitl/hitl_mcp_server.py"],
        env={"MCP_TRANSPORT": "stdio"},
    )


@pytest.mark.unit
def test_mcps_resolve_stdio_launch_when_center_lookup_fails():
    """The registry is consulted even when enrichment gives us nothing.

    This is the whole reason classification lives here rather than reading
    ``run_mode`` downstream: MCP Center is best-effort, and a local server has no
    endpoint, so a Center outage must not turn it into a remote entry that then
    fails compose outright.
    """
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [{"server_code": "hitl"}]
    svc.mcp_center.get_mcp_detail.side_effect = RuntimeError("center down")
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)

    inputs = _collector(
        skill_set_service=svc,
        mcp_config_service=mcp_cfg,
        local_mcp_registry=_registry_over(_HITL_CATALOG),
    ).mcps(_req())

    assert inputs[0].stdio is not None
    assert inputs[0].stdio.command == "python3"


_PER_ENGINE_HITL_CATALOG = {
    "hitl": {
        "stdioConfigs": [
            {"engineType": "teclaw", "command": "python3",
             "arguments": ["/usr/local/bin/teclaw_hitl_mcp_server.py"]},
            {"command": "python3",
             "arguments": ["/home/admin/hitl/hitl_mcp_server.py"]},
        ]
    }
}


@pytest.mark.unit
@pytest.mark.parametrize(
    "engine_type,expected_arg",
    [
        ("teclaw", "/usr/local/bin/teclaw_hitl_mcp_server.py"),
        ("openclaw", "/home/admin/hitl/hitl_mcp_server.py"),
        ("claude_code", "/home/admin/hitl/hitl_mcp_server.py"),
    ],
)
def test_mcps_pick_the_launch_instruction_for_this_engine(engine_type, expected_arg):
    """A launch instruction is a path into a specific image, so the same
    ``server_code`` resolves differently per engine — ``hitl`` ships under
    ``/usr/local/bin`` on teclaw and under ``/home/admin`` everywhere else. An
    entry naming no engine is the default for the rest."""
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [{"server_code": "hitl"}]
    svc.mcp_center.get_mcp_detail.return_value = None
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)

    inputs = _collector(
        skill_set_service=svc,
        mcp_config_service=mcp_cfg,
        local_mcp_registry=_registry_over(_PER_ENGINE_HITL_CATALOG),
    ).mcps(_req(engine_type=engine_type))

    assert inputs[0].stdio == StdioLaunch(command="python3", args=[expected_arg], env={})


@pytest.mark.unit
def test_mcps_never_borrow_another_engines_launch_instruction():
    """With only a teclaw-specific entry and no engine-agnostic default, a
    different engine gets nothing rather than teclaw's binary — launching another
    image's path is worse than not launching."""
    catalog = {
        "hitl": {
            "stdioConfigs": [
                {"engineType": "teclaw", "command": "python3",
                 "arguments": ["/usr/local/bin/teclaw_hitl_mcp_server.py"]},
            ]
        }
    }
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [{"server_code": "hitl"}]
    svc.mcp_center.get_mcp_detail.return_value = None
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)

    collector = _collector(
        skill_set_service=svc,
        mcp_config_service=mcp_cfg,
        local_mcp_registry=_registry_over(catalog),
    )

    # teclaw resolves it…
    assert collector.mcps(_req(engine_type="teclaw"))[0].stdio is not None
    # …openclaw does not, and since Center has no record either, it fails loudly.
    with pytest.raises(McporterComposeError, match="not a local server"):
        collector.mcps(_req(engine_type="openclaw"))


@pytest.mark.unit
def test_mcps_resolved_local_definition_wins_over_bundled_catalog():
    """A deployment's own local server keeps its own command.

    The bundled catalog ships ``hitl``; an operator may register a local server
    under that same code with a different binary. Preferring the catalog would
    swap in a ``/home/admin/...`` path their image need not contain.
    """
    inputs = _mcps_with(
        [{"server_code": "hitl"}],
        center_detail={
            "serverCode": "hitl",
            "runMode": "LOCAL",
            "stdioConfigs": [
                {"command": "/opt/acme/bin/hitl", "arguments": ["--serve"]}
            ],
        },
        registry_catalog=_HITL_CATALOG,
    )

    assert inputs[0].stdio == StdioLaunch(
        command="/opt/acme/bin/hitl", args=["--serve"], env={}
    )


@pytest.mark.unit
def test_mcps_center_remote_wins_over_colliding_registry_name():
    """A caller's own REMOTE server is not hijacked by a same-named local entry.

    The bundled catalog ships ``hitl``/``clawmind``; a deployment may legitimately
    register a remote server under one of those codes. Center positively reporting
    REMOTE settles it — otherwise the entry would be rewritten into a launch
    command for a binary that deployment's image need not even contain.
    """
    inputs = _mcps_with(
        [{"server_code": "hitl"}],
        center_detail={"serverCode": "hitl", "runMode": "REMOTE",
                       "endpoints": [{"env": "PROD", "networkType": "INTERNET",
                                      "transportProtocol": "STREAMABLE_HTTP",
                                      "url": "https://byo.example/hitl"}]},
        registry_catalog=_HITL_CATALOG,
    )

    assert inputs[0].stdio is None
    assert inputs[0].mcp_data["endpoints"][0]["url"] == "https://byo.example/hitl"


@pytest.mark.unit
def test_mcps_remote_server_absent_from_registry_has_no_stdio():
    inputs = _mcps_with(
        [{"server_code": "dima"}],
        center_detail={"serverCode": "dima", "runMode": "REMOTE", "endpoints": []},
        registry_catalog=_HITL_CATALOG,
    )

    assert inputs[0].stdio is None


@pytest.mark.unit
def test_mcps_registry_entry_without_command_yields_no_launch():
    """An unlaunchable catalog entry must not produce a half-formed local entry.

    With Center also holding no record, the server resolves as neither form and
    the compose fails — the correct outcome, and better than publishing a stdio
    entry the engine cannot act on.
    """
    with pytest.raises(McporterComposeError, match="not a local server"):
        _mcps_with(
            [{"server_code": "broken"}],
            center_detail=None,
            registry_catalog={"broken": {"args": ["--x"]}},
        )


@pytest.mark.unit
def test_mcps_registry_entry_without_command_defers_to_center_when_it_has_one():
    """Same unlaunchable catalog entry, but Center knows the code: it composes as
    a remote server. The broken catalog row is simply not a launch instruction."""
    inputs = _mcps_with(
        [{"server_code": "broken"}],
        center_detail={"serverCode": "broken", "runMode": "REMOTE", "endpoints": []},
        registry_catalog={"broken": {"args": ["--x"]}},
    )

    assert inputs[0].stdio is None


@pytest.mark.unit
def test_mcps_fall_back_to_collected_stdio_configs_when_registry_empty():
    """With no registry entry, a ``stdioConfigs`` carried on the collected dict
    still resolves — the registry is the preferred source, not the only one."""
    inputs = _mcps_with(
        [{"server_code": "fs"}],
        center_detail={"serverCode": "fs", "runMode": "LOCAL",
                       "stdioConfigs": [{"command": "node", "arguments": ["/srv.js"]}]},
        registry_catalog={},
    )

    assert inputs[0].stdio == StdioLaunch(command="node", args=["/srv.js"], env={})


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
def test_mcps_center_error_raises_here_with_the_cause_chained():
    """A Center failure on a remote server fails the compose, keeping the cause.

    Swallowing it produced an entry with no endpoints, and the composer three
    layers down then reported "no usable endpoint" — which reads as a
    misconfigured server and sends the reader auditing network coverage for a
    record that was never fetched. The original error must survive as
    ``__cause__`` so the traceback names what actually broke.
    """
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [{"server_code": "boom"}]
    svc.mcp_center.get_mcp_detail.side_effect = RuntimeError("center down")
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)

    with pytest.raises(McporterComposeError, match="could not resolve") as excinfo:
        _collector(skill_set_service=svc, mcp_config_service=mcp_cfg).mcps(_req())

    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert str(excinfo.value.__cause__) == "center down"


@pytest.mark.unit
def test_mcps_center_empty_record_raises_here_too():
    """A lookup that succeeds but returns nothing is equally fatal for a remote
    server — the code is simply unknown to Center — and is reported as its own
    cause rather than as a transport error."""
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [{"server_code": "ghost"}]
    svc.mcp_center.get_mcp_detail.return_value = None
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)

    with pytest.raises(McporterComposeError, match="could not resolve") as excinfo:
        _collector(skill_set_service=svc, mcp_config_service=mcp_cfg).mcps(_req())

    assert isinstance(excinfo.value.__cause__, McpDetailUnavailableError)


@pytest.mark.unit
def test_mcps_local_server_survives_center_having_no_record():
    """The converse, and the reason the failure is decided by the caller: Center
    legitimately has no record of a stdio server, so an empty lookup must NOT be
    fatal once the registry has supplied a launch instruction."""
    inputs = _mcps_with(
        [{"server_code": "hitl"}], center_detail=None, registry_catalog=_HITL_CATALOG
    )

    assert inputs[0].stdio is not None
    assert inputs[0].stdio.command == "python3"


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


# ── concurrent Center enrichment ────────────────────────────────────────────
#
# ``get_mcp_detail`` is one blocking round trip per server (~90 ms in the traced
# request), so enriching in sequence made the whole compose scale linearly with
# the bot's MCP count. These pin the fan-out and, more importantly, the four
# properties the sequential loop gave the caller for free.


def _remote(server_code: str) -> dict:
    """A Center reply for a remote server — enough for compose to accept it."""
    return {"serverCode": server_code, "runMode": "REMOTE", "endpoints": []}


def _mcps_over(codes, lookup, *, registry_catalog=None):
    """Run ``mcps()`` over ``codes``, answering Center with ``lookup(code)``."""
    svc = MagicMock()
    svc.collect_bot_active_mcps.return_value = [{"server_code": c} for c in codes]
    svc.mcp_center.get_mcp_detail.side_effect = lookup
    mcp_cfg = MagicMock()
    mcp_cfg.build_mcp_sync_payload.return_value = (None, {}, "PROD", None)
    return _collector(
        skill_set_service=svc,
        mcp_config_service=mcp_cfg,
        local_mcp_registry=_registry_over(registry_catalog or {}),
    ).mcps(_req())


@pytest.mark.unit
def test_mcps_fetch_center_detail_concurrently():
    """The lookups overlap — a serial loop cannot get past this barrier.

    Every lookup blocks until all five have arrived, so the call only completes
    if the five are genuinely in flight at once. One-at-a-time enrichment leaves
    the first waiter to time out, which surfaces as a failed compose rather than
    as a quietly slower one.
    """
    codes = ["a", "b", "c", "d", "e"]
    gate = threading.Barrier(len(codes), timeout=10)

    def lookup(server_code):
        gate.wait()
        return _remote(server_code)

    inputs = _mcps_over(codes, lookup)

    assert [i.mcp_data["server_code"] for i in inputs] == codes


@pytest.mark.unit
def test_mcps_keep_raw_order_when_lookups_finish_out_of_order():
    """Output follows ``raw``, not completion.

    The composer writes ``McpComposeInput`` entries in list order, so reading
    futures as they complete would reorder the artifact for no reason other than
    which Center reply landed first. Here the replies land in exactly reverse
    order.

    Completion order is *forced* with a chain of events rather than staggered
    sleeps: a thread descheduled longer than a sleep gap would otherwise invert
    the inversion and fail the test for scheduler timing that has nothing to do
    with the behaviour under test. Each lookup waits for its turn, so the
    replies land in reverse order deterministically or the test times out
    saying so. (The chain needs all entries in flight at once, which holds:
    the pool is ``min(len(codes), _MCP_DETAIL_WORKERS)`` wide.)
    """
    codes = ["first", "second", "third", "fourth"]
    completion_order = list(reversed(codes))
    turns = {code: threading.Event() for code in codes}
    turns[completion_order[0]].set()
    finished: list[str] = []
    lock = threading.Lock()

    def lookup(server_code):
        assert turns[server_code].wait(timeout=10), f"never got a turn: {server_code}"
        with lock:
            finished.append(server_code)
            done = len(finished)
        if done < len(completion_order):
            turns[completion_order[done]].set()
        return _remote(server_code)

    inputs = _mcps_over(codes, lookup)

    assert finished == completion_order  # completion order really did invert
    assert [i.mcp_data["server_code"] for i in inputs] == codes


@pytest.mark.unit
def test_mcps_bound_the_fan_out_at_mcp_center():
    """A bot with many MCPs must not open one request per server at once.

    Removing the sequential cost is the point; replacing it with an unbounded
    burst at MCP Center is not. More servers than workers, so the ceiling has to
    actually hold some of them back.

    Every worker is held until the pool is provably saturated, so the ceiling is
    *observed* rather than raced for: peak lands on exactly
    ``_MCP_DETAIL_WORKERS`` — never above it (that is the bound) and never
    below (nothing may exit until that many are in flight at once). A
    sleep-based version would only show "some overlap happened", and would say
    it on the strength of CI scheduling.
    """
    codes = [f"s{i}" for i in range(_MCP_DETAIL_WORKERS * 2 + 3)]
    lock = threading.Lock()
    in_flight = 0
    peak = 0
    saturated = threading.Event()

    def lookup(server_code):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
            if in_flight >= _MCP_DETAIL_WORKERS:
                saturated.set()
        assert saturated.wait(timeout=10), "pool never reached its worker ceiling"
        with lock:
            in_flight -= 1
        return _remote(server_code)

    inputs = _mcps_over(codes, lookup)

    assert [i.mcp_data["server_code"] for i in inputs] == codes
    assert peak == _MCP_DETAIL_WORKERS


@pytest.mark.unit
def test_mcps_bound_the_fan_out_across_concurrent_composes():
    """The ceiling is process-wide, not per compose.

    A pool built per call would cap each compose at ``_MCP_DETAIL_WORKERS`` and
    cap nothing between them — and composes *do* run concurrently, since
    ``project_skills`` dispatches each projection through ``asyncio.to_thread``.
    Two at once would then reach ``2 x _MCP_DETAIL_WORKERS`` simultaneous Center
    lookups while the constant still claimed eight.

    The breach is detected directly rather than inferred from a peak count: the
    lookups rendezvous on a barrier needing ``_MCP_DETAIL_WORKERS + 1`` parties,
    one more than the ceiling allows. Under a shared pool that barrier can never
    fill — at most ``_MCP_DETAIL_WORKERS`` lookups are ever resident — so it
    times out and every waiter leaves by the broken-barrier path. Under a
    per-call pool the two composes bring twice that many workers, the barrier
    fills, and ``breached`` is set.

    The mix deliberately includes **single-entry composes**. Running a lone
    lookup inline would be cheaper but would open a side door around the pool:
    one saturating compose plus N one-MCP composes would put
    ``_MCP_DETAIL_WORKERS + N`` lookups in flight while the constant still
    claimed ``_MCP_DETAIL_WORKERS``. A test built only from fat composes cannot
    see that, since every one of their entries goes through the pool.

    (Counting a peak instead would prove nothing here: whatever releases the
    workers has to fire at the very threshold the test is trying to exceed, so
    the count stops at the ceiling under both implementations.)
    """
    sizes = [_MCP_DETAIL_WORKERS, 1, 1, 1]
    start = threading.Barrier(len(sizes), timeout=10)
    over_ceiling = threading.Barrier(_MCP_DETAIL_WORKERS + 1, timeout=2)
    breached = threading.Event()
    lock = threading.Lock()
    resident = 0
    peak = 0

    def lookup(server_code):
        nonlocal resident, peak
        with lock:
            resident += 1
            peak = max(peak, resident)
        try:
            over_ceiling.wait()
        except threading.BrokenBarrierError:
            pass  # never enough in flight to fill it — the ceiling held
        else:
            breached.set()  # one more than the ceiling was resident at once
        with lock:
            resident -= 1
        return _remote(server_code)

    results: list[list] = []

    def compose(tag, size):
        codes = [f"{tag}{i}" for i in range(size)]
        start.wait()  # every compose submits together, so they really do overlap
        results.append(_mcps_over(codes, lookup))

    threads = [
        threading.Thread(target=compose, args=(tag, size))
        for tag, size in zip("abcd", sizes)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "a compose never finished"
    assert sorted(len(r) for r in results) == sorted(sizes)
    assert not breached.is_set(), (
        f"{_MCP_DETAIL_WORKERS + 1} lookups were in flight at once — the fan-out "
        "ceiling is not process-wide (per-compose pool, or a path around it)"
    )
    assert peak <= _MCP_DETAIL_WORKERS


@pytest.mark.unit
def test_mcps_carry_each_entrys_own_failure_through_the_fan_out():
    """One server's failure stays that server's, with its own cause chained.

    ``_enrich_mcp_detail`` returns the cause instead of raising precisely so the
    caller can judge each entry separately. Fanning out must not collapse the
    per-entry causes into whichever one happened to surface first — the raise
    still has to name the server that failed and chain *its* exception.
    """
    boom = RuntimeError("center down for c")

    def lookup(server_code):
        if server_code == "c":
            raise boom
        return _remote(server_code)

    with pytest.raises(McporterComposeError, match="MCP c:") as excinfo:
        _mcps_over(["a", "b", "c", "d"], lookup)

    assert excinfo.value.__cause__ is boom


@pytest.mark.unit
def test_mcps_local_server_still_survives_an_empty_lookup_beside_remote_peers():
    """The converse under concurrency: a local server with no Center record
    composes, while its remote siblings resolve normally in the same fan-out."""
    def lookup(server_code):
        return None if server_code == "hitl" else _remote(server_code)

    inputs = _mcps_over(
        ["a", "hitl", "b"], lookup, registry_catalog=_HITL_CATALOG
    )

    assert [i.mcp_data["server_code"] for i in inputs] == ["a", "hitl", "b"]
    assert inputs[1].stdio is not None
    assert inputs[1].stdio.command == "python3"
    assert inputs[0].stdio is None and inputs[2].stdio is None


@pytest.mark.unit
def test_mcps_lookups_run_under_the_requests_tenant():
    """Pool workers inherit no context vars, so the tenant is copied onto each
    task. Without that, a Center lookup for a registered external tenant would
    run under the default one — reading another tenant's catalog."""
    seen: list[str] = []
    lock = threading.Lock()

    def lookup(server_code):
        with lock:
            seen.append(get_current_avernet_tenant())
        return _remote(server_code)

    with avernet_tenant_scope("acme"):
        _mcps_over(["a", "b", "c"], lookup)

    assert seen == ["acme"] * 3


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
    from agentclaw.community.core.channel.models import ChannelRecord

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


# ── teclaw: files owned by the running container (no backend mirror / no probe) ──

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
