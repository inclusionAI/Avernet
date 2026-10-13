# agentswarm — Emergent Agent Swarm

LLM orchestrator that decomposes a goal into per-turn decisions and spawns
ephemeral, context-isolated subagents at runtime, converging via termination
conditions rather than a pre-authored plan (Kimi-agent-swarm model, D1).
Design decisions D1–D43 live in `docs/specs/2026-10-08-agentswarm-integration/design.md`.

This file is the module's single doc — architecture, boundary metadata,
runbook, and wire surface.

## Context Boundary

```yaml
purpose: >
  Emergent multi-agent swarm runs — an LLM orchestrator decides turn by
  turn whether to spawn subagents, wait on their result shards, hand off to
  a catalogue persona, or finish, with durable slice execution over the
  ac_agent_swarm_* tables.
provides:
  - AgentSwarmServiceProtocol
  - AgentSwarmService
  - AgentSwarmWorker
  - SwarmRunHandler
  - SliceStore
  - Orchestrator
  - TerminationEvaluator
  - GuardService
  - SubagentRuntime
  - LLMProvider
  - Subagent / SubagentFactory
  - BotPlatform        # in-process provision seam (BotCreateSpec)
  - ArtifactStore
  - DecisionParser
  - RunEvents / AgentEvent / RoundEvent / GuardEventSummary
consumes:
  - DatabasePlugin
  - LifecycleBase
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.plugin_api.database
  - agentclaw.community.plugin_api.agentswarm  # swarm SPI protocols + seam vocabulary (BotId/IdempotencyKey/BotCreateSpec)
  - agentclaw.community.log
  - agentclaw.community.kernel
  - agentclaw.community.utils
  - agentclaw.community.core.agentswarm
```

## Layer placement (arch.rules.md §8)

| Layer | Path | Responsibility |
|---|---|---|
| api/ | `community/api/agentswarm_service.py` | flat re-export of the Service Protocol + DTOs |
| core/ | `community/core/agentswarm/` | Service Protocol + service, runtime, domain, repository |
| adapters/http/ | `community/adapters/http/agentswarm/` | thin router + DTOs (Pydantic) |
| adapters/cli/ | `community/adapters/cli/agentswarm/` | argparse client over the HTTP surface |
| di/modules/ | `community/di/modules/agentswarm/` | composition root |
| plugin_api/ | `community/plugin_api/agentswarm/` | SPI Protocols: LLMProvider, Subagent/Factory, BotPlatform, ArtifactStore |
| plugins/ | `plugins/local/agentswarm/`, `plugins/community/agentswarm/` | concrete impls (LLM client/stub, artifacts, factory, avernet subagent + host adapter) |

## Isolation (D17/D35)

Feature-isolated: no import of `core/task`, `core/task_queue`,
`core/harness`, `plugin_api/object_storage`, or `core/system_config`; kernel
only (`core.base`, `di`, `plugin_api.database`, `plugin_api.agentswarm`,
`kernel.*`, `log`). Core holds **zero** transport/test-framework imports —
httpx lives only inside `plugins/*/agentswarm/` (the LLM client). The
avernet subagent reaches the host platform through the in-process
`BotPlatform` seam (one `BotCreateSpec` + idempotency key; the host adapter
composes the platform document) — no socket leaves the module. Enforced by
`tests/community/architecture/test_agentswarm_isolation.py`, which also
gate-checks D18 (closed-set StrEnums in the Protocol surface).

## Domain model (D18, D43)

Domain models use `@dataclass` (zero framework dependency). Transport DTOs
in `adapters/http/agentswarm/schemas.py` are Pydantic BaseModel (FastAPI
boundary). ORM models use SQLAlchemy `Base` from `core/base.py`.

## Swarm model

1. **Submit** — `AgentSwarmService.submit_async` creates a `run` row
   (RUNNING) plus a durable `job` row; identical `idempotency_key`s join the
   same run. Tenant admission is capped by
   `tenant_limits.max_concurrent_runs` (429); a missing LLM config fails
   clear (400).
2. **Claim** — a worker claims the job (`claim_epoch` +1 on every
   claim/reclaim — D28 fencing).
3. **Slice** — `SwarmRunHandler.handle_slice` rehydrates `RunState` from
   durable rows, then loops: orchestrator decision
   (spawn/wait/handoff/finish, repair-bounded D20) → `_dispatch_spawn`
   creates `agent` rows + one `round` row per cohort → completions persist
   agent status + `result_shard` (payload XOR artifact_key, D31) + messages
   (exactly-once D24).
4. **Converge** — waiting blocks on shard visibility (`all`/`any` refs);
   slice budget exhaustion reschedules the job (D34 resumable slices).
5. **Terminal** — status + termination reason published through the fenced
   write, per-agent message retention applied (D13), job SUCCEEDED. Guard
   events persist every rejected decision; no-results runs poison-breach
   the reclaim counter into terminal STUCK (D40).

### Worker recovery model

| Decision | Behavior |
|---|---|
| D34 resumable slices | every slice rehydrates from durable rows; any pod resumes |
| D28 claim fencing | state writes carry `claim_epoch`; a fenced UPDATE matches the job row's current epoch or fails (lost slices exit `retry`) |
| D40 reclaim breaker | `job.reclaims` counts no-progress reclaims; `reclaims > reclaim_max` → terminal `STUCK` |
| D24 exactly-once | shard + terminal message persisted before terminal publish; a completed objective never re-runs |
| D23 fresh context | every attempt constructs a fresh subagent; no cross-attempt leakage |
| D42 valid output | jsonschema-validated `output_schema`s; invalid results retried, never routed |
| D13 retention | terminal applies `message_retention_tail` per agent, keeping shard references readable |
| D16 identity retry | interrupted agents respawn under their original `agent_id` + idempotency key |
| D31 shard XOR | exactly one of payload / artifact_key carries a result |

## Tables (`ac_agent_swarm_*`, schema in `repository/<table>/model.py`)

All tables carry `id`, `env`, `tenant`, `is_deleted`, `gmt_create`,
`gmt_modified` from `_AgentSwarmBase`. SQL DDL lives in `sql/`.

| Table | Columns (module-specific) |
|---|---|
| `ac_agent_swarm_run` | `run_id`, `goal`, `agent_catalog`, `provider`, `request_metadata`, `status`, `termination_reason`, `root_agent_id`, `final_answer`, `error`, `active_idempotency_key`, `started_at`, `ended_at` |
| `ac_agent_swarm_agent` | `agent_id`, `run_id`, `parent_agent_id`, `depth`, `name`, `role`, `kind`, `task_spec`, `spawn_round`, `status`, `attempts`, `idempotency_key`, `output`, `error`, `bot_id`, `bot_state`, `started_at`, `ended_at` |
| `ac_agent_swarm_message` | `message_id`, `run_id`, `agent_id`, `seq`, `role`, `content`, `decision`, `token_usage`, `artifact_key` |
| `ac_agent_swarm_result_shard` | `shard_id`, `run_id`, `source_agent_id`, `payload`, `artifact_key`, `routed_at` |
| `ac_agent_swarm_round` | `round_id`, `run_id`, `stage_index`, `orchestrator_steps`, `max_subagent_steps`, `cohort_agent_ids`, `started_at`, `ended_at` |
| `ac_agent_swarm_guard_event` | `event_id`, `run_id`, `agent_id`, `kind`, `details` |
| `ac_agent_swarm_job` | `job_id`, `run_id`, `status`, `run_at`, `claimed_by`, `claim_epoch`, `lease_expires_at`, `attempts`, `reclaims`, `deadline_at`, `last_error`, `cancel_requested` |

## D14 POC field mapping

### Run level
| POC field | Target | Status |
|---|---|---|
| `requests.id` | `run.run_id` (server-generated) | Mapped |
| `requests.goal`/`runs.goal` | `run.goal` (single column) | Mapped |
| `requests.agents`/`runs.agents` | `run.agent_catalog` (JSON) | Mapped |
| `requests.provider` | `run.provider` | Mapped |
| `requests.payload` | `run.request_metadata` (secrets stripped) | Mapped |
| `requests.created_at`/`runs.created_at` | `run.gmt_create` | Mapped |
| `runs.status` | `run.status` (remapped vocabulary) | Mapped |
| `runs.error` | `run.error` | Mapped |
| `runs.finished_at` | `run.ended_at` | Mapped |
| `plans.plan_json` (DAG prompt/edges) | not persisted → `round`; DAG dropped with the materialized-DAG model D1 | Dropped (swarm is edge-less) |

### Agent/lineage
| POC field | Target | Status |
|---|---|---|
| `AgentSpec.name` | `agent.name` | Mapped |
| `AgentSpec.role`/`instructions` | `agent.task_spec` (TaskEnvelope JSON) | Mapped |
| `AgentSpec.metadata.type` (`"avernet"` string) | `agent.kind` (`AgentKind`: `llm`/`avernet`) | Mapped (typed enum) |
| `node_executions.agent` + `DAGNode.id` | `agent.name` + lineage (`agent_id`, `parent_agent_id`, `depth`) | Mapped |
| instance id (`{name}-{uuid}`) | `agent.agent_id` (fresh uuid per spawn) | Mapped |
| `DAGNode.wave` | `agent.spawn_round` | Mapped |
| Avernet `_bot_id` + lifecycle states | `agent.bot_id` + `agent.bot_state` (POC never persisted bot state) | Mapped |
| `AgentContext.upstream` | structural: lineage via `parent_agent_id` + `result_shard`s | Mapped |

### Messages, shards, rounds, guards
- Messages (new concept): sources `node.input.goal` (→ `TaskEnvelope.objective`), `DAGPlan.prompt`, LLM bodies — stored in `ac_agent_swarm_message` with `role`, `content`, `decision`, `token_usage`, `artifact_key`.
- Result shards: `final_output` → `run.final_answer` + terminal-agent shards.
- Rounds: one row per orchestration round. Guard events (new): `serial_collapse`, `spurious_parallelism`, `dedupe_reject`, `depth_exceeded`, `width_exceeded`, `timeout`, `kill`, `stuck`, `prompt_injection`.

### Dropped (with reasons)
| POC item | Dropped reason |
|---|---|
| `DAGPlan.edges` | Swarm is edge-less (D1); structure = lineage + messages |
| `DAGNode.wave`/topological order | Replaced by `spawn_round` + round timeline |
| `NodeStatus.SKIPPED` | No equivalent swarm state |
| `NodeInput.params` | Dead field (never written by the POC planner) |
| `NodeStatus` enum | Replaced by swarm `AgentStatus` |
| `HaltReason` (`done`/`abort`/`drift`) | Replaced by `TerminationReason` |
| Constructor-only thresholds (`max_extensions`, `replan_max_calls`, ...) | Became swarm config (guards, D20/D21) + persisted guard events |
| `driver.max_retries`/`max_workers`/replanner retries | Map to `subagent_max_attempts`, `max_width`, `llm_max_retries` (D23) |

## Configuration

`AgentSwarmConfig` parses the `agentswarm` ``user_config`` block
(`AgentSwarmConfig.from_block`, defensively — a malformed value takes its
default rather than wedging boot). The LLM comes from the **top-level `llm`
yaml block** (the same one the harness reads): `base_url`, `secret_name`
(resolved via SecretResolver), `model`; no agentswarm-specific LLM config
exists. Missing/blank LLM credentials fail a submit with the intended 400.
`AGENTSWARM_ARTIFACT_ROOT` relocates the artifact store (shared volume in
multi-pod deployments).

| Knob | Default | Meaning |
| --- | --- | --- |
| `orchestration.max_turns` | 50 | termination budget |
| `orchestration.max_total_agents` / `max_width` / `max_depth` | 100/8/5 | guards |
| `orchestration.subagent_max_attempts` | 3 | fresh-context retries |
| `orchestration.slice_max_seconds` / `slice_interval` | 300/1 | slice budget/pause |
| `orchestration.reclaim_max` | 5 | D40 circuit breaker |
| `orchestration.lease_seconds` / `poll_interval` / `batch_size` | 60/2/10 | worker |
| `tenant_limits.max_concurrent_runs` | 8 | 429 admission cap |
| `message_retention_tail` | 50 | rows kept per agent at terminal |
| `artifact_inline_threshold` | 16384 | spill blobs to artifacts above |
| `retention.artifact_ttl_days` / `sweep_interval_seconds` | 7/3600 | artifact sweeper |
| `enabled` | true | worker startup switch |

## Operations runbook

- **Schema evolution** — forward-only, additive, backward-readable (D38).
  Policy + verification queries: `sql/2026_10_09_agent_swarm_validation.md`;
  enforcement: `repository/test_agentswarm_schema_contract.py`. A new change
  adds a dated sql file, mirrors the columns in the owning
  `repository/<table>/model.py` in the same change, keeps new columns
  nullable/defaulted, never repurposes an old one.
- **Connection-pool sizing** — the worker is one loop: at most one slice in
  flight per pod; the D23 queue (`maxsize = max_width`) bounds subagent
  writes. Steady need ≈ `2 + workers`, overflow 10 fits `max_width=8` and
  ~50 SSE subscribers/pod; hard ceiling/pod ≈ `2 + workers·max_width + subscribers`.
  Beyond that, add engine kwargs in `plugins/community/database.py` (there
  is no yaml pool knob — it is not a config value by design).
- **Artifact TTL sweeper** — `runtime/retention/` over the `RetentionScanner`
  SPI: dry-run listing, `max_batch` bound, per-key log-and-continue, strict
  `{run_id}/{agent_id}/{result}` conformance, path-escape guard; nothing
  outside `artifact_store_root` is touched. Growth: heavy 100 spills/h ≈
  16.8k files — one hourly O(files) walk is negligible; revisit at ~100×.
- **Indexes / perf** — submit-time COUNT scans bind only
  (`tenant`,`status`) today: known gap — counters degrade to fleet-wide
  scans; fix is binding `env` (or status-leading indexes) in the three
  admission counters. SSE cost/pod = subscribers × 4 indexed queries/s —
  switch to one-poller + broadcast when per-run subscribers routinely
  exceed `page_size` (20).
- **Drain** — slice-bound writes are short; verify
  `drain.grace_seconds` (30) ≥ a slice's final write batch before shrinking
    pool/recycle values.

## Data protection

- **Classification** — `run.goal`, `message.content`, shard payloads may
  carry user text. `content_classification(run_id, tenant)` reports PII
  families per chunk without exposing content; `security.redact_pii`
  redacts exports.
- **Retention / erasure** — message tail applied at terminal (D13);
  artifacts swept by TTL. `purge_run` physically deletes the run's rows
  (tenant-scoped); the append-only `ac_agent_swarm_audit` trail is a
  governance record and is intentionally kept.
- **Egress** — the single outbound surface (the LLM endpoint) validates
  against `config.egress` (http(s)-only, optional https-only, optional host
  allowlist) at client construction — fail clear at boot. The avernet
  subagent calls the host platform through the in-process `BotPlatform`
  seam; the subagent itself opens no socket. Binding preconditions: the
  `task_queue_worker` runs (creations complete as its durable jobs), a
  functional `SandboxRuntimeClient` binding for the chat leg, and
  `agentswarm.host.owner_id` names the account swarm-created bots belong
  to (required — no service-account fallback).
- **Prompt injection** — subagent output is untrusted: `SliceStore`
  records `prompt_injection` guard events for detected markers while still
  routing the result; the orchestrator wraps results in an
  `<untrusted_result>` fence and instructs the model never to follow
  instructions inside it.

## Service / HTTP / CLI surface

Service Protocol (`service/protocols.py`): `submit_async`, `submit_sync`,
`get_run`, `list_runs`, `run_events`, `cancel`, `purge_run`,
`content_classification` (the last two are service-plane only — data
protection; no route, no CLI). Router prefix `/agentswarm/v1`; reads and
per-run routes take the tenant from `X-Tenant-Id`.

```bash
# async submit -> {"run_id": "..."}
curl -sS -X POST http://127.0.0.1:8000/agentswarm/v1/runs -H 'Content-Type: application/json' -d '{
  "tenant": "t1", "goal": "map the incident to owning services",
  "agents": [{"name": "scout", "role": "research"}],
  "idempotency_key": "inc-42"}'

# sync submit (blocks until terminal or deadline)
curl -sS -X POST 'http://127.0.0.1:8000/agentswarm/v1/runs?wait=true' -H 'Content-Type: application/json' -d '{"tenant": "t1", "goal": "summarize logs", "agents": [{"name": "s1", "role": "research"}]}'

# get / list / cancel
curl -sS http://127.0.0.1:8000/agentswarm/v1/runs/<run_id> -H 'X-Tenant-Id: t1'
curl -sS 'http://127.0.0.1:8000/agentswarm/v1/runs?page=1&page_size=20&status=COMPLETED' -H 'X-Tenant-Id: t1'
curl -sS -X POST http://127.0.0.1:8000/agentswarm/v1/runs/<run_id>/cancel -H 'X-Tenant-Id: t1'

# SSE stream: status, agent/round/guard deltas, heartbeats, terminal
curl -sS -N http://127.0.0.1:8000/agentswarm/v1/runs/<run_id>/events -H 'X-Tenant-Id: t1'

# report (HTML) + health
curl -sS http://127.0.0.1:8000/agentswarm/v1/runs/<run_id>/report -H 'X-Tenant-Id: t1'
curl -sS http://127.0.0.1:8000/agentswarm/v1/health
```

Error mapping: tenant limit exceeded → 429 `tenant_run_limit_exceeded`;
missing LLM config → 400; unknown run → 404. SSE `run_events` advance row
cursors (`after_agent_id`, `after_round_id`, `after_event`) — each event
emits once per stream.

CLI (`build_parser`/`main(argv, client)`; no `__main__` guard — invoke
`main` directly):

```bash
cd apps/backend && DEPLOY_PROFILE=test PYTHONPATH=src:. .venv/bin/python -c \
  "from agentclaw.community.adapters.cli.agentswarm import main; raise SystemExit(main(['submit', '--spec', 'spec.json', '--wait']))"
```

`spec.json` carries the submit body above; `--wait` polls until terminal.

## Test suite + coverage gate

```bash
cd apps/backend
DEPLOY_PROFILE=test PYTHONPATH=src:. .venv/bin/python -m pytest tests/community/core/agentswarm/ \
  -q --cov=agentclaw.community.core.agentswarm --cov-branch --cov-fail-under=90
```

Current: 1389 tests, 96.83% branch coverage (gate ≥90%). Architecture
isolation + conformance gates live in `tests/community/architecture/`
(incl. `test_agentswarm_isolation.py`); the single-process E2E is
`tests/community/core/agentswarm/test_e2e.py`.

## Change impact

Contract changes to the Service Protocol ripple into the HTTP router, the
CLI, the DI module, and the conformance pair registered in
`tests/community/architecture/test_service_api_conformance.py`. Changing a
`ac_agent_swarm_*` column is a migration event: the slice loop (D34) and
the reclaim circuit breaker (D40) resume from those durable rows, so
writes must stay backward-readable across releases. Guard/termination
semantics (D20/D21/D28) observably change SSE `guard` events and terminal
statuses.