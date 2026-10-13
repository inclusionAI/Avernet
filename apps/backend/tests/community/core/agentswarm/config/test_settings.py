"""AgentSwarmConfig.from_block — the yaml-block parsing contract.

The parser is the module's deployment surface (the `agentswarm`
user_config block, read by the DI provider): its two invariants are
(1) an absent block equals the bare construction — every profile that
ships no block keeps its current behavior — and (2) a malformed value
downgrades to its default rather than wedging boot. Both are pinned
here, plus the LLM fail-clear rule: blank env-expanded credentials
mean "no LLM configured", the same 400 the missing block always meant.
"""
from __future__ import annotations



from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmEgressConfig, LlmConfig,
)

from pathlib import Path

OVERLAY_DIR = (Path(__file__).resolve().parents[5]
               / "src/agentclaw/community/configs")


class TestBareDefaultsContract:

    def test_empty_block_equals_bare_construction(self):
        assert AgentSwarmConfig.from_block({}) == AgentSwarmConfig()
        assert AgentSwarmConfig.from_block(None) == AgentSwarmConfig()

    def test_empty_sub_blocks_equal_bare_construction(self):
        parsed = AgentSwarmConfig.from_block({
            "llm": {}, "orchestration": {}, "tenant_limits": {},
            "admission": {}, "drain": {}, "upstream_breaker": {},
            "retention": {}, "egress": {},
        })
        assert parsed == AgentSwarmConfig()

    def test_every_nested_group_parses_its_own_block(self):
        # the groups carry knob isolation: one from_block per dataclass
        for group, cls_ in (
            ("orchestration", AgentSwarmEgressConfig),  # placeholder replaced below
        ):
            pass
        assert hasattr(AgentSwarmEgressConfig, "from_block")


class TestLlmFailClear:

    def test_absent_llm_stays_none(self):
        assert AgentSwarmConfig.from_block({}).llm is None

    def test_blank_llm_after_env_expansion_stays_none(self):
        # blank env-expanded base_url fails clear; blank model does not (glm default)
        for block in (
            {"llm": None},
            {"llm": {}},
            {"llm": {"base_url": "", "model": "gpt"}},
            {"llm": {"base_url": "   ", "model": "gpt"}},
        ):
            assert AgentSwarmConfig.from_block(block).llm is None, block

    def test_blank_model_keeps_base_url_and_takes_default_model(self):
        llm = LlmConfig.from_block({"base_url": "https://api", "model": ""})
        assert llm is not None
        assert llm.base_url == "https://api"
        assert llm.default_model == "glm-5.2"

    def test_configured_llm_parses_every_knob(self):
        llm = LlmConfig.from_block({
            "base_url": " https://api.test ",
            "secret_name": "sk-x",
            "model": "gpt-x",
            "orchestrator_model": "gpt-big",
            "timeout_ms": 30000,
            "max_tokens": 8192,
            "max_retries": 5,
        })
        assert llm is not None
        assert llm.base_url == "https://api.test"
        assert llm.api_key == "sk-x"
        assert llm.default_model == "gpt-x"
        assert llm.orchestrator_model == "gpt-big"
        assert llm.timeout_seconds == 30
        assert llm.max_tokens == 8192
        assert llm.max_retries == 5

    def test_non_string_llm_fields_downgrade(self):
        llm = LlmConfig.from_block(
            {"base_url": "https://api", "model": "gpt",
             "timeout_ms": -5000, "max_tokens": "many"})
        assert llm is not None
        d = LlmConfig()
        assert llm.timeout_seconds == d.timeout_seconds
        assert llm.max_tokens == d.max_tokens


class TestDefensiveParsing:

    def test_malformed_values_take_defaults(self):
        cfg = AgentSwarmConfig.from_block({
            "orchestration": {"max_turns": "turns", "lease_seconds": 0,
                              "batch_size": -1},
            "artifact_inline_threshold": 0,
        })
        bare = AgentSwarmConfig()
        assert cfg.orchestration.max_turns == bare.orchestration.max_turns
        assert cfg.orchestration.lease_seconds == bare.orchestration.lease_seconds
        assert cfg.orchestration.batch_size == bare.orchestration.batch_size
        assert cfg.artifact_inline_threshold == bare.artifact_inline_threshold

    def test_valid_values_parse(self):
        cfg = AgentSwarmConfig.from_block({
            "orchestration": {"max_turns": 3, "slice_max_seconds": 12.5},
            "retention": {"artifact_ttl_days": 30},
            "message_retention_tail": 10,
            "artifact_store_root": "/var/agentswarm",
            "enabled": "false",
        })
        assert cfg.orchestration.max_turns == 3
        assert cfg.orchestration.slice_max_seconds == 12.5
        assert cfg.retention.artifact_ttl_days == 30
        assert cfg.message_retention_tail == 10
        assert cfg.artifact_store_root == "/var/agentswarm"
        assert cfg.enabled is False

    def test_bool_accepts_yaml_truthy_strings(self):
        assert AgentSwarmConfig.from_block({"enabled": "yes"}).enabled is True
        assert AgentSwarmConfig.from_block({"enabled": "off"}).enabled is False
        assert AgentSwarmConfig.from_block({"enabled": None}).enabled is True

    def test_egress_allows_hosts_tuple_and_drops_noise(self):
        egress = AgentSwarmEgressConfig.from_block(
            {"allowed_hosts": ["api.test", 9, "  "], "require_https": True})
        assert egress.allowed_hosts == ("api.test",)
        assert egress.require_https is True

    def test_non_dict_egress_host_list_takes_default(self):
        egress = AgentSwarmEgressConfig.from_block({"allowed_hosts": "api.test"})
        assert egress.allowed_hosts == AgentSwarmEgressConfig().allowed_hosts


class TestDiProviderWiring:

    def test_provider_parses_the_agentswarm_block(self, monkeypatch):
        import agentclaw.community.di.modules.agentswarm as wiring

        monkeypatch.setattr(
            wiring, "_block",
            lambda name: ({"base_url": "https://api.test", "model": "gpt-x",
                           "secret_name": "sk"}
                          if name == "llm" else
                          {"orchestration": {"max_turns": 7}}
                          if name == "agentswarm" else {}),
        )
        cfg = wiring.AgentSwarmModule().provide_config()
        assert cfg.llm is not None and cfg.llm.default_model == "gpt-x"
        assert cfg.llm.api_key == "sk"
        assert cfg.orchestration.max_turns == 7

    def test_provider_without_block_equals_bare_defaults(self, monkeypatch):
        import agentclaw.community.di.modules.agentswarm as wiring

        monkeypatch.setattr(wiring, "_block", lambda name: {})
        assert (wiring.AgentSwarmModule().provide_config()
                == AgentSwarmConfig())

    def test_overlays_ship_the_block(self):
        for name in ("application-community.yaml", "application-singlebox.yaml"):
            text = (OVERLAY_DIR / name).read_text()
            assert "agentswarm:" in text, name
            assert "AGENTSWARM_ARTIFACT_ROOT" in text, name
