"""Rule 25 conformance — MetricsPlugin.

Consumer under test: the agentcompute observability facade
(AgentComputeObservability), a real upper-layer consumer that must count
a swarm's durable events through whatever metrics backend the deploy
profile installs — the exact consumer↔Protocol conformance Rule 25 asks
for. We drive the facade against the deployable impls:

- local NoopMetrics → every operation silent, time_block usable.
- community CommunityMetrics → registry cells accumulate and are
  snapshot-readable; label values are str-coerced, never raising.
- DI-resolved via the world fixtures → test column binds NoopMetrics,
  community column binds CommunityMetrics.
"""
from __future__ import annotations

import time

from agentclaw.community.core.agentcompute.domain import models as dm
from agentclaw.community.core.agentcompute.observability import (
    AgentComputeObservability,
)
from agentclaw.community.plugin_api.metrics import MetricsPlugin
from agentclaw.community.plugins.community.metrics import CommunityMetrics
from agentclaw.community.plugins.local.metrics import NoopMetrics


def _drive_facade_consumer(metrics: MetricsPlugin) -> None:
    """Every facade operation, called the way agentcompute calls it."""
    obs = AgentComputeObservability(metrics)
    obs.run_claim("claim")
    obs.run_claim("reclaim")
    obs.agent_spawn(dm.AgentKind.AVENET)
    obs.llm_call("ok")
    obs.llm_call("transient")
    obs.llm_call("non_retryable")
    obs.llm_call("exhausted")
    obs.llm_call("timeout")
    obs.token_usage(
        dm.TenantId("tenant-1"), dm.LlmCallKind.ORCHESTRATOR,
        dm.TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
    )
    obs.shard_persisted(artifact=True)
    obs.shard_persisted(artifact=False)
    obs.agent_terminal(dm.AgentStatus.COMPLETED)
    obs.stuck_transition()
    obs.queue_depth(7)
    with obs.slice_duration(_run()):
        time.sleep(0.001)


def _run() -> dm.Run:
    return dm.Run(
        run_id=dm.RunId("run-25"),
        tenant=dm.TenantId("tenant-1"),
        goal="conformance",
        agent_catalog=(),
        provider=dm.ProviderId("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(0),
    )


def test_noop_metrics_consumer_records_nothing_and_never_raises():
    _drive_facade_consumer(NoopMetrics())
    with AgentComputeObservability(NoopMetrics()).slice_duration(_run()):
        pass


def test_community_metrics_consumer_accumulates_every_operation():
    m = CommunityMetrics()
    _drive_facade_consumer(m)
    snap = m.snapshot()
    counters = snap["increment"]
    assert counters[("agentcompute_run_claim_total", (("kind", "claim"),))] == 1.0
    assert counters[("agentcompute_run_claim_total", (("kind", "reclaim"),))] == 1.0
    assert counters[("agentcompute_agent_spawn_total", (("kind", "avernet"),))] == 1.0
    assert counters[("agentcompute_llm_call_total", (("outcome", "ok"),))] == 1.0
    assert counters[("agentcompute_llm_call_total", (("outcome", "transient"),))] == 1.0
    assert counters[("agentcompute_llm_call_total", (("outcome", "non_retryable"),))] == 1.0
    assert counters[("agentcompute_llm_call_total", (("outcome", "exhausted"),))] == 1.0
    assert counters[("agentcompute_llm_call_total", (("outcome", "timeout"),))] == 1.0
    assert counters[("agentcompute_shard_persisted_total", (("artifact", "true"),))] == 1.0
    assert counters[("agentcompute_shard_persisted_total", (("artifact", "false"),))] == 1.0
    assert counters[("agentcompute_agent_terminal_total", (("status", "COMPLETED"),))] == 1.0
    assert counters[("agentcompute_stuck_transition_total", ())] == 1.0
    tokens = snap["observe"][("agentcompute_llm_tokens_total",
                              (("kind", "orchestrator"), ("tenant", "tenant-1")))]
    assert tokens == {"count": 1, "sum": 5.0, "min": 5.0, "max": 5.0}
    assert snap["set_gauge"][("agentcompute_queue_depth", ())] == 7.0
    slices = snap["observe"][("agentcompute_slice_duration_seconds",
                              (("tenant", "tenant-1"),))]
    assert slices["count"] == 1


def test_community_metrics_coerces_exotic_label_values():
    m = CommunityMetrics()
    m.increment("odd", labels={"v": {"nested": ["unhashable"]}})
    assert m.snapshot()["increment"][("odd", (("v", "{'nested': ['unhashable']}"),))] == 1.0


def test_world_resolved_metrics_is_the_test_column_noop(world):
    metrics = world.get(MetricsPlugin)
    assert isinstance(metrics, NoopMetrics)
    _drive_facade_consumer(metrics)


def test_community_world_resolves_community_registry(community_world):
    metrics = community_world.get(MetricsPlugin)
    assert isinstance(metrics, CommunityMetrics)