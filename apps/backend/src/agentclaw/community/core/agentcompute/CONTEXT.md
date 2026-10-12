# agentcompute — Emergent Agent Swarm (module context)

LLM orchestrator that decomposes a goal into per-turn decisions and spawns
ephemeral, context-isolated subagents at runtime, converging via termination
conditions rather than a pre-authored plan (Kimi-agent-swarm model, D1).
Design decisions D1–D43 and the change plan live in
`openspec/changes/migrate-agentcompute-swarm-into-backend/`.

## Context Boundary

```yaml
purpose: >
  Emergent multi-agent compute runs — an LLM orchestrator decides turn by
  turn whether to spawn subagents, wait on their result shards, hand off to
  a catalogue persona, or finish, with durable slice execution over the
  ac_agent_compute_* tables.
provides:
  - AgentComputeServiceProtocol     # submits, polls, lists, cancels, run_events
  - AgentComputeService
  - AgentComputeWorker             # durable job-loop worker (LifecycleBase)
  - SwarmRunHandler                # bounded multi-turn slice execution
  - AgentComputeObservability      # D39 metric facade over MetricsPlugin (12.4)
  - SliceStore                     # completion/shard/round/message persistence
  - Orchestrator                   # decision cycle incl. D20 repair
  - TerminationEvaluator
  - GuardService                   # dedup/depth/width/premature-finish guards
  - SubagentRuntime                # D23 pool+queue, D42 jsonschema validation
  - LLMProvider                    # SPI Protocol (OpenAI-compatible client, stub)
  - Subagent / SubagentFactory     # SPI Protocols
  - ArtifactStore                  # SPI Protocol (filesystem plugin)
  - DecisionParser                 # fenced-JSON decision parsing
  - RunEvents / AgentEvent / RoundEvent / GuardEventSummary   # SSE read model
consumes:
  - DatabasePlugin     # repositories run on orm_session (D19: no await inside)
  - MetricsPlugin      # operational metrics (D39 surface, never raises)
  - LifecycleBase      # kernel lifecycle for the worker
internal_dependencies:
  - agentclaw.community.core.base          # ORM Base
  - agentclaw.community.plugin_api.database # DatabasePlugin
  - agentclaw.community.plugin_api.metrics  # MetricsPlugin seam (D35 ops infra)
  - agentclaw.community.log               # get_logger structured-log seam
  - agentclaw.community.kernel             # lifecycle
  - agentclaw.community.utils              # env resolution in ORM models
  - agentclaw.community.core.agentcompute  # intra-module
```

### Change impact

Contract changes to the Service Protocol ripple into the HTTP router, the
CLI, the DI module, and the conformance pair registered in
`tests/community/architecture/test_service_api_conformance.py`. Changing a
`ac_agent_compute_*` column is a migration event: the slice loop (D34) and
the reclaim circuit breaker (D40) resume from those durable rows, so writes
must stay backward-readable across releases. Guard/termination semantics
(D20/D21/D28) observably change SSE `guard` events and terminal statuses.

## Swarm model

- A submit becomes a `run` row plus a durable `job` row; a worker claims
  the job (`claim_epoch` bumped each claim/reclaim — D28 fencing).
- Each slice: rehydrate state from rows -> orchestrator decides
  (spawn/wait/handoff/finish, repair-bounded D20) -> spawn dispatch creates
  `agent` rows + a `round` row per cohort -> completions persist as agent
  status + `result_shard` (payload XOR artifact key, D31) + messages.
- Waiting blocks on shard visibility (all/any refs); budget exhaustion
  reschedules the job for the next slice (D34 resumable slices).
- Terminal publish sets the run status/termination reason and applies
  per-agent message retention (D13). No-results runs poison-breach the
  reclaim counter into terminal STUCK (D40).

## Tables (`ac_agent_compute_*`)

| Table | Row meaning |
| --- | --- |
| `run` | one submitted goal; status, final answer, idempotency join |
| `job` | durable queue entry: claim epoch, lease, reclaims, cancel flag |
| `agent` | spawned subagent lineage: name, kind, round, status, attempts |
| `message` | per-agent exchanges incl. `decision` role (D18/D30) |
| `result_shard` | one output carrier per completed agent (XOR, D31) |
| `round` | one spawn cohort: stage index + cohort agent ids |
| `guard_event` | persisted guard decisions (dedup/depth/width/stuck/kill) |

## Isolation (D17/D35)

No import of `core/task`, `core/task_queue`, `core/harness`,
`plugin_api/object_storage`, or `core/system_config`; kernel only
(`core.base`, `di`, `plugin_api.database`, `kernel.*`, `log`). Transport
stays at the adapters; httpx appears only inside `plugins/` (LLM client).
The avernet subagent calls the host platform through the in-process
`BotPlatform` seam (create = one `BotCreateSpec` + idempotency key; the
adapter composes the platform document) — no socket leaves the module. Enforced by
`tests/community/architecture/test_agentcompute_isolation.py`.

## Operation knobs (`AgentComputeConfig`)

| Knob | Default | Meaning |
| --- | --- | --- |
| `orchestration.max_turns` | 50 | termination budget |
| `orchestration.max_total_agents` / `max_width` / `max_depth` | 100/8/5 | guards |
| `orchestration.subagent_max_attempts` | 3 | fresh-context retries (4.4) |
| `orchestration.slice_max_seconds` / `slice_interval` | 300/1 | slice budget/pause |
| `orchestration.reclaim_max` | 5 | D40 circuit breaker |
| `orchestration.lease_seconds` / `poll_interval` / `batch_size` | 60/2/10 | worker |
| `tenant_limits.max_concurrent_runs` | 8 | 429 admission cap |
| `message_retention_tail` | 50 | rows kept per agent at terminal |
| `artifact_inline_threshold` | 16384 | spill blobs to artifacts above |
| `enabled` | true | worker startup switch |