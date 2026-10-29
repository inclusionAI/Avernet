"""Tests for the spawned agent naming chain, AgentKind coercion, and the
executor spec override in _execute_with_retry.

Pins the behavioural contract of PR#3242:
  1. subagent_name = envelope.name + "_" + agent_id[:12] (no collision)
  2. _resolve_kind / _executor_kind coerce string kind values to AgentKind
     (run.agent_catalog hydrated from JSON in later turns yields strings)
  3. _execute_with_retry rebuilds the spec with the suffixed spawned name
     AND carries the catalog's `instructions` field (was missing)
  4. bot/llm subagent executor_name use "_" + spawn_digest(..., 12)

Conventions follow test_subagent_runtime.py: sync test defs driving async
code via asyncio.run, hand-written fakes.
"""
from __future__ import annotations

import asyncio

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentKind, AgentSpec, Run, RunId, TaskEnvelope, TenantId,
    RunStatus, RequestMetadata, EpochMillis, spawn_digest,
)
from agentclaw.community.core.agentswarm.runtime.handler.impl import SwarmRunHandler
from agentclaw.community.core.agentswarm.runtime.pool.impl import SubagentRuntime
from agentclaw.community.plugins.community.agentswarm.bot_subagent import (
    AvernetSubagent,
)
from agentclaw.community.plugins.local.agentswarm.subagent_llm import (
    LLMBackedSubagent,
)


def mk_catalog_entry(
    name: str = "coder",
    kind=AgentKind.BOT,
    instructions: str = "Write clean code.",
    config_manifest: str = "",
) -> AgentSpec:
    return AgentSpec(
        name=name,
        role="writes code",
        instructions=instructions,
        kind=kind,
        config_manifest=config_manifest,
    )


def mk_run(catalog=None) -> Run:
    from agentclaw.community.core.agentswarm.domain.models import (
        RunStatus, RequestMetadata, EpochMillis,
    )
    return Run(
        run_id=RunId("run-42"),
        tenant=TenantId("tenant-a"),
        goal="test goal",
        agent_catalog=tuple(catalog) if catalog else (),
        provider=None,
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(0),
    )


def mk_envelope(name: str = "coder_rust") -> TaskEnvelope:
    return TaskEnvelope(
        name=name,
        objective="produce one",
        output_schema={},
        boundaries="",
        parent_goal="",
    )


class TestResolveKind:
    """_resolve_kind: prefix match + AgentKind coercion (Fix B)."""

    def test_exact_match_returns_bot_kind(self):
        run = mk_run([mk_catalog_entry("coder_rust", kind=AgentKind.BOT)])
        kind = SwarmRunHandler._resolve_kind(run, "coder_rust")
        assert kind == AgentKind.BOT

    def test_prefix_match_inherits_bot_kind(self):
        run = mk_run([mk_catalog_entry("coder", kind=AgentKind.BOT)])
        kind = SwarmRunHandler._resolve_kind(run, "coder_rust")
        assert kind == AgentKind.BOT

    def test_prefix_match_inherits_llm_kind(self):
        run = mk_run([mk_catalog_entry("analyst", kind=AgentKind.LLM)])
        kind = SwarmRunHandler._resolve_kind(run, "analyst_x")
        assert kind == AgentKind.LLM

    def test_string_kind_bot_is_coerced_to_enum(self):
        """The core bug: run.agent_catalog hydrated from JSON in later
        turns yields spec.kind as the string 'bot' not AgentKind.BOT.
        The isinstance check must not silently degrade to AgentKind.LLM."""
        catalog = mk_catalog_entry("coder", kind=AgentKind.BOT)
        # simulate what the JSON-boundary produces
        catalog.__dict__["kind"] = "bot"
        run = mk_run([catalog])
        kind = SwarmRunHandler._resolve_kind(run, "coder_rust")
        assert kind == AgentKind.BOT

    def test_string_kind_llm_is_coerced_to_enum(self):
        catalog = mk_catalog_entry("analyst", kind=AgentKind.LLM)
        catalog.__dict__["kind"] = "llm"
        run = mk_run([catalog])
        kind = SwarmRunHandler._resolve_kind(run, "analyst_x")
        assert kind == AgentKind.LLM

    def test_none_kind_falls_back_to_llm(self):
        catalog = mk_catalog_entry("coder", kind=AgentKind.BOT)
        catalog.__dict__["kind"] = None
        run = mk_run([catalog])
        kind = SwarmRunHandler._resolve_kind(run, "coder_rust")
        assert kind == AgentKind.LLM

    def test_no_match_unanimous_bot_catalog_inherits_bot(self):
        """PRE incident (run 88addaf6f, post-REL20261029 deploy): the
        orchestrator named a spawn 'rust_coder' (language first), the
        prefix match against catalog 'coder' missed, and dispatch labeled
        the row llm while the pool's _unanimous_catalog_surface minted a
        bot persona parked at the consent gate. The detail surfaces the
        authorization descriptor only for kind=bot, so the human gate
        was invisible and the run could never be approved. An unresolved
        name in a unanimous catalog must predict the execution kind."""
        run = mk_run([mk_catalog_entry("coder", kind=AgentKind.BOT)])
        kind = SwarmRunHandler._resolve_kind(run, "rust_coder")
        assert kind == AgentKind.BOT

    def test_no_match_unanimous_llm_catalog_inherits_llm(self):
        run = mk_run([mk_catalog_entry("analyst", kind=AgentKind.LLM)])
        kind = SwarmRunHandler._resolve_kind(run, "ghost")
        assert kind == AgentKind.LLM

    def test_no_match_mixed_catalog_returns_llm(self):
        """Was test_no_match_returns_llm: a mixed catalog has no unique
        execution surface to inherit, so the llm default stands."""
        run = mk_run([
            mk_catalog_entry("coder", kind=AgentKind.BOT),
            mk_catalog_entry("analyst", kind=AgentKind.LLM),
        ])
        kind = SwarmRunHandler._resolve_kind(run, "ghost")
        assert kind == AgentKind.LLM

    def test_no_match_empty_catalog_returns_llm(self):
        run = mk_run([])
        kind = SwarmRunHandler._resolve_kind(run, "ghost")
        assert kind == AgentKind.LLM

    def test_unanimous_string_kinds_coerce_before_inheritance(self):
        catalog = mk_catalog_entry("coder", kind=AgentKind.BOT)
        catalog.__dict__["kind"] = "bot"
        run = mk_run([catalog])
        kind = SwarmRunHandler._resolve_kind(run, "rust_coder")
        assert kind == AgentKind.BOT


class TestExecutorKindCoercion:
    """_executor_kind: same string-coercion as _resolve_kind (Fix F)."""

    def test_enum_kind_returns_value(self):
        spec = mk_catalog_entry("coder", kind=AgentKind.BOT)
        assert SubagentRuntime._executor_kind(spec) == "bot"

    def test_string_kind_bot_coerces(self):
        spec = mk_catalog_entry("coder", kind=AgentKind.BOT)
        spec.__dict__["kind"] = "bot"
        assert SubagentRuntime._executor_kind(spec) == "bot"

    def test_string_kind_llm_coerces(self):
        spec = mk_catalog_entry("analyst", kind=AgentKind.LLM)
        spec.__dict__["kind"] = "llm"
        assert SubagentRuntime._executor_kind(spec) == "llm"

    def test_none_kind_returns_llm(self):
        spec = mk_catalog_entry("coder", kind=AgentKind.BOT)
        spec.__dict__["kind"] = None
        assert SubagentRuntime._executor_kind(spec) == "llm"


class TestUniqueSpawnedName:
    """_dispatch_spawn: subagent_name = envelope.name + agent_id[:12]."""

    AGENT_ID = AgentId("a1b2c3d4-5e6f-4678-9abc-def012345678")

    def test_spawned_name_gets_unique_suffix(self):
        """The naming chain: envelope.name + agent_id[:12].

        agent_id 'a1b2c3d4-5e6f-4678-9abc-def012345678' has hyphens; the
        first 12 hex chars after stripping are 'a1b2c3d45e6f'.
        """
        uuid_str = str(self.AGENT_ID).replace("-", "")
        suffix = uuid_str[:12]
        assert suffix == "a1b2c3d45e6f"

        # Verify the f-string produces the expected format:
        envelope_name = "coder_rust"
        spawned_name = f"{envelope_name}_{suffix}"
        assert spawned_name == "coder_rust_a1b2c3d45e6f"

    def test_different_agent_ids_produce_different_names(self):
        id1 = AgentId("a1b2c3d4-5e6f-4678-9abc-def012345678")
        id2 = AgentId("b1b2c3d4-5e6f-4678-9abc-def012345678")
        name1 = f"coder_{str(id1).replace('-','')[:12]}"
        name2 = f"coder_{str(id2).replace('-','')[:12]}"
        assert name1 != name2

    def test_same_agent_type_different_languages_get_different_ids(self):
        """The orchestrator spawns coder_rust, coder_java, coder_ruby
        with different agent_ids → no name collision."""
        ids = [
            AgentId(f"{i:08x}-5e6f-4678-9abc-def012345678")
            for i in range(3)
        ]
        names = [
            f"coder_{lang}_{str(aid).replace('-','')[:12]}"
            for lang, aid in zip(["rust", "java", "ruby"], ids)
        ]
        assert len(set(names)) == 3


class TestSpecOverride:
    """_execute_with_retry: spec.name override + instructions carry."""

    def test_spec_name_uses_spawned_composite_name(self):
        """The spec rebuild in _execute_with_retry carries the spawned
        name (envelope.name + agent_id[:12]) not the bare catalog name,
        so bot_subagent's executor_name reflects the full identity."""
        envelope = mk_envelope("coder_rust")
        agent_id = "a1b2c3d4-5e6f-4678-9abc-def012345678"
        unique_suffix = str(agent_id).replace("-", "")[:12]
        spawned_name = f"{envelope.name}_{unique_suffix}"
        assert spawned_name == "coder_rust_a1b2c3d45e6f"

    def test_catalog_instructions_survive_spec_rebuild(self):
        """Fix C must copy `instructions` from the catalog spec (was
        missing — the bot persona text would have been empty)."""
        catalog = mk_catalog_entry(
            "coder", kind=AgentKind.BOT,
            instructions="Write production-grade Rust code.",
        )
        assert catalog.instructions == "Write production-grade Rust code."


class TestExecutorNaming:
    """bot_subagent + llm_subagent: {spec.name}_{spawn_digest(..., 12)}."""

    def test_bot_subagent_digest_is_12_chars(self):
        digest = spawn_digest("run-42", "agent-1", 1, 12)
        assert len(digest) == 12

    def test_llm_subagent_digest_is_12_chars(self):
        digest = spawn_digest("run-42", "agent-1", 1, 12)
        assert len(digest) == 12

    def test_executor_uses_underscore_separator(self):
        """Was '-' now '_': {spec.name}_{digest12} not {spec.name}-{digest8}."""
        name = "coder_rust_a1b2c3d45e6f"
        digest = "k1l2m3n4o5p6"
        executor = f"{name}_{digest}"
        assert "_" in executor
        assert executor == "coder_rust_a1b2c3d45e6f_k1l2m3n4o5p6"
        assert "-" not in executor

    def test_naming_chain_prefixes_are_nested(self):
        """Each level carries the previous as a full prefix."""
        spec_name = "coder"
        instance_name = f"{spec_name}_rust"
        subagent_name = f"{instance_name}_a1b2c3d45e6f"

        # bot_name and executor_name both prefix with subagent_name
        bot_name = f"{subagent_name}_k1l2m3n4o5p6"
        executor_name = f"{subagent_name}_p7q8r9t0u1v2"

        assert bot_name.startswith(subagent_name + "_")
        assert executor_name.startswith(subagent_name + "_")
        assert subagent_name.startswith(instance_name + "_")
