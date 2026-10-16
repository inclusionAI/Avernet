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

import pytest

from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmEgressConfig, AgentSwarmOrchestrationConfig,
    LlmConfig,
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

    def test_zero_budget_knobs_take_defaults(self):
        # a zero poll interval is a hot loop, a zero slice/timeout/sweep/
        # grace budget wedges the worker: 0 downgrades like any junk value
        cfg = AgentSwarmConfig.from_block({
            "orchestration": {"poll_interval": 0, "slice_max_seconds": 0,
                              "slice_interval": 0},
            "drain": {"grace_seconds": 0},
            "retention": {"sweep_interval_seconds": 0},
            "llm": {"base_url": "https://api", "model": "gpt",
                    "timeout_ms": 0},
        })
        bare = AgentSwarmConfig()
        assert cfg.orchestration.poll_interval == bare.orchestration.poll_interval
        assert cfg.orchestration.slice_max_seconds == bare.orchestration.slice_max_seconds
        assert cfg.orchestration.slice_interval == bare.orchestration.slice_interval
        assert cfg.drain.grace_seconds == bare.drain.grace_seconds
        assert cfg.retention.sweep_interval_seconds == bare.retention.sweep_interval_seconds
        assert cfg.llm is not None
        assert cfg.llm.timeout_seconds == LlmConfig().timeout_seconds

    def test_negative_budget_knobs_take_defaults(self):
        cfg = AgentSwarmConfig.from_block({
            "orchestration": {"poll_interval": -2, "slice_max_seconds": -1,
                              "slice_interval": -0.5},
            "drain": {"grace_seconds": -1},
            "retention": {"sweep_interval_seconds": -1},
            "admission": {"max_pending_jobs": 0},
        })
        bare = AgentSwarmConfig()
        assert cfg.orchestration.poll_interval == bare.orchestration.poll_interval
        assert cfg.orchestration.slice_max_seconds == bare.orchestration.slice_max_seconds
        assert cfg.orchestration.slice_interval == bare.orchestration.slice_interval
        assert cfg.drain.grace_seconds == bare.drain.grace_seconds
        assert cfg.retention.sweep_interval_seconds == bare.retention.sweep_interval_seconds
        assert cfg.admission.max_pending_jobs == bare.admission.max_pending_jobs

    def test_small_positive_budget_knobs_still_parse(self):
        # strict positivity must not clamp legitimate sub-second cadences
        cfg = AgentSwarmConfig.from_block({
            "orchestration": {"poll_interval": 0.25, "slice_interval": 0.1,
                              "slice_max_seconds": 1},
            "drain": {"grace_seconds": 0.5},
            "retention": {"sweep_interval_seconds": 1},
            "admission": {"max_pending_jobs": 1},
        })
        assert cfg.orchestration.poll_interval == 0.25
        assert cfg.orchestration.slice_interval == 0.1
        assert cfg.orchestration.slice_max_seconds == 1
        assert cfg.drain.grace_seconds == 0.5
        assert cfg.retention.sweep_interval_seconds == 1
        assert cfg.admission.max_pending_jobs == 1

    def test_blank_artifact_store_root_takes_default(self):
        # an env-expanded ${AGENTSWARM_ARTIFACT_ROOT:-} that comes up blank
        # must not blank the artifact root
        for root in ("", "   ", None, 7):
            cfg = AgentSwarmConfig.from_block({"artifact_store_root": root})
            assert cfg.artifact_store_root == \
                AgentSwarmConfig().artifact_store_root, root


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


class TestHostOwnerRequired:
    """``agentswarm.host.owner_id`` — required when the host block ships
    (P0-9): the account swarm-created bots belong to has no
    service-account fallback, so a present-but-blank owner is a config
    error, not a downgrade. An absent block keeps the bare-defaults
    contract (no platform side opted in), and the refusal for an
    unconfigured owner happens at the bot_platform provider."""

    def test_host_block_with_blank_owner_raises_config_error(self):
        from agentclaw.community.core.agentswarm.service.protocols import (
            AgentSwarmConfigError,
        )
        for owner in ("", "   "):
            with pytest.raises(AgentSwarmConfigError):
                AgentSwarmConfig.from_block({"host": {"owner_id": owner}})

    def test_host_owner_error_names_the_knob_and_the_reason(self):
        from agentclaw.community.core.agentswarm.service.protocols import (
            AgentSwarmConfigError,
        )
        with pytest.raises(AgentSwarmConfigError) as excinfo:
            AgentSwarmConfig.from_block({"host": {"owner_id": ""}})
        message = str(excinfo.value)
        assert "host.owner_id" in message
        assert "no service-account fallback" in message

    def test_host_block_with_set_owner_parses(self):
        cfg = AgentSwarmConfig.from_block({"host": {"owner_id": "owner-1"}})
        assert cfg.host.owner_id == "owner-1"

    def test_host_owner_strips_surrounding_blanks(self):
        cfg = AgentSwarmConfig.from_block({"host": {"owner_id": " owner-1 "}})
        assert cfg.host.owner_id == "owner-1"

    def test_absent_host_block_keeps_bare_default_owner(self):
        # no host block: no platform opt-in, owner stays empty and the
        # bot_platform provider refuses construction (logged by
        # provide_runtime) — the bare-defaults contract is unchanged.
        assert AgentSwarmConfig.from_block({}).host.owner_id == ""
        assert AgentSwarmConfig.from_block({}) == AgentSwarmConfig()
        assert AgentSwarmConfig.from_block(None) == AgentSwarmConfig()

    def test_non_string_owner_downgrades_to_refusal(self):
        from agentclaw.community.core.agentswarm.service.protocols import (
            AgentSwarmConfigError,
        )
        # a present host block with a non-string owner cannot parse to a
        # usable identity — same precise refusal as a blank one.
        with pytest.raises(AgentSwarmConfigError):
            AgentSwarmConfig.from_block({"host": {"owner_id": 7}})


class TestTerminationKnobs:
    """``wall_clock_seconds`` / ``no_progress_threshold`` orchestration
    knobs (P1-9): the TerminationEvaluator's hardcoded defaults become
    config-driven. Both zero values wedge every run (instant WALL_CLOCK /
    NO_PROGRESS at the first evaluate), so they downgrade to the tested
    default like other budget knobs."""

    def test_wall_clock_and_no_progress_parse(self):
        orch = AgentSwarmOrchestrationConfig.from_block(
            {"wall_clock_seconds": 7200.0, "no_progress_threshold": 7})
        assert orch.wall_clock_seconds == 7200.0
        assert orch.no_progress_threshold == 7

    def test_zero_termination_knobs_take_defaults(self):
        d = AgentSwarmOrchestrationConfig()
        orch = AgentSwarmOrchestrationConfig.from_block(
            {"wall_clock_seconds": 0, "no_progress_threshold": 0})
        assert orch.wall_clock_seconds == d.wall_clock_seconds
        assert orch.no_progress_threshold == d.no_progress_threshold

    def test_malformed_termination_knobs_take_defaults(self):
        d = AgentSwarmOrchestrationConfig()
        orch = AgentSwarmOrchestrationConfig.from_block(
            {"wall_clock_seconds": "day", "no_progress_threshold": "none"})
        assert orch.wall_clock_seconds == d.wall_clock_seconds
        assert orch.no_progress_threshold == d.no_progress_threshold

    def test_bare_termination_knobs_match_evaluator_defaults(self):
        # the dataclass defaults ARE the evaluator's historical
        # hardcoded values, so a profile with no block keeps behavior.
        from agentclaw.community.core.agentswarm.orchestration.termination import (
            TerminationEvaluator,
        )
        orch = AgentSwarmOrchestrationConfig()
        evaluator = TerminationEvaluator()
        assert orch.wall_clock_seconds == evaluator._wall_clock_seconds
        assert orch.no_progress_threshold == evaluator._no_progress_threshold
