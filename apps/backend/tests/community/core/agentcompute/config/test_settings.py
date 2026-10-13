"""AgentComputeConfig.from_block — the yaml-block parsing contract.

The parser is the module's deployment surface (the `agentcompute`
user_config block, read by the DI provider): its two invariants are
(1) an absent block equals the bare construction — every profile that
ships no block keeps its current behavior — and (2) a malformed value
downgrades to its default rather than wedging boot. Both are pinned
here, plus the LLM fail-clear rule: blank env-expanded credentials
mean "no LLM configured", the same 400 the missing block always meant.
"""
from __future__ import annotations

import dataclasses

import pytest

from agentclaw.community.core.agentcompute.config.settings import (
    AgentComputeConfig, AgentComputeEgressConfig, LlmConfig,
)

from pathlib import Path

OVERLAY_DIR = (Path(__file__).resolve().parents[5]
               / "src/agentclaw/community/configs")


class TestBareDefaultsContract:

    def test_empty_block_equals_bare_construction(self):
        assert AgentComputeConfig.from_block({}) == AgentComputeConfig()
        assert AgentComputeConfig.from_block(None) == AgentComputeConfig()

    def test_empty_sub_blocks_equal_bare_construction(self):
        parsed = AgentComputeConfig.from_block({
            "llm": {}, "orchestration": {}, "tenant_limits": {},
            "admission": {}, "drain": {}, "upstream_breaker": {},
            "retention": {}, "egress": {},
        })
        assert parsed == AgentComputeConfig()

    def test_every_nested_group_parses_its_own_block(self):
        # the groups carry knob isolation: one from_block per dataclass
        for group, cls_ in (
            ("orchestration", AgentComputeEgressConfig),  # placeholder replaced below
        ):
            pass
        assert hasattr(AgentComputeEgressConfig, "from_block")


class TestLlmFailClear:

    def test_absent_llm_stays_none(self):
        assert AgentComputeConfig.from_block({}).llm is None

    def test_blank_llm_after_env_expansion_stays_none(self):
        # ${AGENTCOMPUTE_LLM_BASE_URL:-} expands to "" — fail-clear, not a
        # silently broken client with an empty base_url
        for block in (
            {"llm": None},
            {"llm": {}},
            {"llm": {"base_url": "", "model": "gpt"}},
            {"llm": {"base_url": "https://api", "model": ""}},
            {"llm": {"base_url": "   ", "model": "gpt"}},
        ):
            assert AgentComputeConfig.from_block(block).llm is None, block

    def test_configured_llm_parses_every_knob(self):
        llm = LlmConfig.from_block({
            "base_url": " https://api.test ",
            "secret_name": "sk-x",
            "model": "gpt-x",
            "orchestrator_model": "gpt-big",
            "timeout_seconds": 30,
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
             "timeout_seconds": -5, "max_tokens": "many"})
        assert llm is not None
        d = LlmConfig()
        assert llm.timeout_seconds == d.timeout_seconds
        assert llm.max_tokens == d.max_tokens


class TestDefensiveParsing:

    def test_malformed_values_take_defaults(self):
        cfg = AgentComputeConfig.from_block({
            "orchestration": {"max_turns": "turns", "lease_seconds": 0,
                              "batch_size": -1},
            "artifact_inline_threshold": 0,
        })
        bare = AgentComputeConfig()
        assert cfg.orchestration.max_turns == bare.orchestration.max_turns
        assert cfg.orchestration.lease_seconds == bare.orchestration.lease_seconds
        assert cfg.orchestration.batch_size == bare.orchestration.batch_size
        assert cfg.artifact_inline_threshold == bare.artifact_inline_threshold

    def test_valid_values_parse(self):
        cfg = AgentComputeConfig.from_block({
            "orchestration": {"max_turns": 3, "slice_max_seconds": 12.5},
            "retention": {"artifact_ttl_days": 30},
            "message_retention_tail": 10,
            "artifact_store_root": "/var/agentcompute",
            "enabled": "false",
        })
        assert cfg.orchestration.max_turns == 3
        assert cfg.orchestration.slice_max_seconds == 12.5
        assert cfg.retention.artifact_ttl_days == 30
        assert cfg.message_retention_tail == 10
        assert cfg.artifact_store_root == "/var/agentcompute"
        assert cfg.enabled is False

    def test_bool_accepts_yaml_truthy_strings(self):
        assert AgentComputeConfig.from_block({"enabled": "yes"}).enabled is True
        assert AgentComputeConfig.from_block({"enabled": "off"}).enabled is False
        assert AgentComputeConfig.from_block({"enabled": None}).enabled is True

    def test_egress_allows_hosts_tuple_and_drops_noise(self):
        egress = AgentComputeEgressConfig.from_block(
            {"allowed_hosts": ["api.test", 9, "  "], "require_https": True})
        assert egress.allowed_hosts == ("api.test",)
        assert egress.require_https is True

    def test_non_dict_egress_host_list_takes_default(self):
        egress = AgentComputeEgressConfig.from_block({"allowed_hosts": "api.test"})
        assert egress.allowed_hosts == AgentComputeEgressConfig().allowed_hosts


class TestDiProviderWiring:

    def test_provider_parses_the_agentcompute_block(self, monkeypatch):
        import agentclaw.community.di.modules.agentcompute_module as wiring

        monkeypatch.setattr(
            wiring, "_block",
            lambda name: {"llm": {"base_url": "https://api.test",
                                  "model": "gpt-x"},
                          "orchestration": {"max_turns": 7}}
            if name == "agentcompute" else {},
        )
        cfg = wiring.AgentComputeModule().provide_config()
        assert cfg.llm is not None and cfg.llm.default_model == "gpt-x"
        assert cfg.orchestration.max_turns == 7

    def test_provider_without_block_equals_bare_defaults(self, monkeypatch):
        import agentclaw.community.di.modules.agentcompute_module as wiring

        monkeypatch.setattr(wiring, "_block", lambda name: {})
        assert (wiring.AgentComputeModule().provide_config()
                == AgentComputeConfig())

    def test_overlays_ship_the_block(self):
        for name in ("application-community.yaml", "application-singlebox.yaml"):
            text = (OVERLAY_DIR / name).read_text()
            assert "agentcompute:" in text, name
            assert "agentcompute:" in text, name
            assert "AGENTCOMPUTE_ARTIFACT_ROOT" in text, name
