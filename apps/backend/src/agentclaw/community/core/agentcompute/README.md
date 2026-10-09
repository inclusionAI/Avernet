# agentcompute — Emergent Agent Swarm

LLM orchestrator that dynamically decomposes a goal and spawns ephemeral,
context-isolated subagents at runtime, converging via termination conditions
rather than a pre-authored plan (Kimi Agent Swarm model, D1).

## Layer placement (arch.rules.md §8)

| Layer | Path | Responsibility |
|---|---|---|
| api/ | `community/api/agentcompute_service.py` | flat re-export of the Service Protocol + DTOs |
| core/ | `community/core/agentcompute/` | Service Protocol + service, runtime, domain, repository |
| adapters/http/ | `community/adapters/http/agentcompute/` | thin router + DTOs (Pydantic) |
| adapters/cli/ | `community/adapters/cli/agentcompute_cli.py` | argparse client over the HTTP surface |
| di/modules/ | `community/di/modules/agentcompute_module.py` | composition root |

## Context Boundary

```yaml
purpose: >
  Emergent multi-agent compute runs — an LLM orchestrator decides turn by
  turn whether to spawn subagents, wait on their result shards, hand off to
  a catalogue persona, or finish, with durable slice execution over the
  ac_agent_compute_* tables.
provides:
  - AgentComputeServiceProtocol
  - AgentComputeService
  - AgentComputeWorker
  - SwarmRunHandler
  - SliceStore
  - Orchestrator
  - TerminationEvaluator
  - GuardService
  - SubagentRuntime
  - LLMProvider
  - Subagent / SubagentFactory
  - ArtifactStore
  - DecisionParser
  - RunEvents / AgentEvent / RoundEvent / GuardEventSummary
consumes:
  - DatabasePlugin
  - LifecycleBase
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.plugin_api.database
  - agentclaw.community.kernel
  - agentclaw.community.utils
  - agentclaw.community.core.agentcompute
```

## Isolation boundary (D17, D35)

agentcompute is feature-isolated (D35): no import of `core/task`, `core/task_queue`, `core/harness`, `plugin_api/object_storage`, or `core/system_config`. Allowed: framework kernel (`core/base.py` Base, DI, DatabasePlugin, schema.py, LifecycleBase) + platform operational infra (metrics, migrations). Enforced by `tests/community/architecture/test_agentcompute_isolation.py`, which also gate-checks D18 (closed-set StrEnums in the Protocol surface; no `fastapi`/`httpx`/`pytest` imports outside `plugins/`).

## Domain model (D18, D43)

Domain models use `@dataclass` (zero framework dependency, matching `core/task/domain/models.py`). Transport DTOs in `adapters/http/agentcompute/schemas.py` are Pydantic BaseModel (FastAPI boundary). ORM models use SQLAlchemy `Base` from `core/base.py`.

## Swarm model

1. **Submit** — `AgentComputeService.submit_async` creates a `run` row (RUNNING) plus a durable `job` row; identical `idempotency_key`s join the same run. Tenant admission is capped by `tenant_limits.max_concurrent_runs` (429) and a missing LLM config fails clear (400).
2. **Claim** — a worker claims the job (`claim_epoch` +1 on every claim/reclaim — D28 fencing).
3. **Slice** — `SwarmRunHandler.handle_slice` rehydrates `RunState` from durable rows, then loops: orchestrator decision (spawn / wait / handoff / finish, repair-bounded D20) → `_dispatch_spawn` creates `agent` rows + one `round` row per cohort → completions persist agent status + `result_shard` (payload XOR artifact_key, D31) + messages (exactly-once D24).
4. **Converge** — waiting blocks on shard visibility (`all`/`any` refs); slice budget exhaustion reschedules the job (D34 resumable slices).
5. **Terminal** — run status + termination reason published through the fenced write, per-agent message retention applied (D13), job SUCCEEDED. Guard events persist every rejected decision.

## Worker recovery model

| Decision | Behavior |
|---|---|
| D34 resumable slices | every slice rehydrates from `run`/`agent`/`shard`/`message` rows; any pod resumes |
| D28 claim fencing | state writes carry `claim_epoch`; a fenced UPDATE matches the job row's current epoch or fails (lost slices exit `retry`) |
| D40 reclaim breaker | `job.reclaims` counts no-progress reclaims; `reclaims > reclaim_max` → terminal `STUCK` |
| D24 exactly-once | shard + terminal message persisted before terminal publish; a completed objective never re-runs |
| D23 fresh context | every subagent attempt constructs a fresh subagent; no cross-attempt leakage |
| D42 valid output | jsonschema-validated `output_schema`s; invalid results retried, never routed |
| D13 retention | terminal applies `message_retention_tail` per agent, keeping shard references readable |
| D16 identity retry | interrupted agents respawn under their original `agent_id` + idempotency key |
| D31 shard XOR | exactly one of payload / artifact_key carries a result |

## Tables (`ac_agent_compute_*`, schema in `repository/models.py`)

All tables carry `id`, `env`, `tenant`, `is_deleted`, `gmt_create`, `gmt_modified` from `_AgentComputeBase`.

| Table | Columns (module-specific) |
|---|---|
| `ac_agent_compute_run` | `run_id`, `goal`, `agent_catalog`, `provider`, `request_metadata`, `status`, `termination_reason`, `root_agent_id`, `final_answer`, `error`, `active_idempotency_key`, `started_at`, `ended_at` |
| `ac_agent_compute_agent` | `agent_id`, `run_id`, `parent_agent_id`, `depth`, `name`, `role`, `kind`, `task_spec`, `spawn_round`, `status`, `attempts`, `idempotency_key`, `output`, `error`, `bot_id`, `bot_state`, `started_at`, `ended_at` |
| `ac_agent_compute_message` | `message_id`, `run_id`, `agent_id`, `seq`, `role`, `content`, `decision`, `token_usage`, `artifact_key` |
| `ac_agent_compute_result_shard` | `shard_id`, `run_id`, `source_agent_id`, `payload`, `artifact_key`, `routed_at` |
| `ac_agent_compute_round` | `round_id`, `run_id`, `stage_index`, `orchestrator_steps`, `max_subagent_steps`, `cohort_agent_ids`, `started_at`, `ended_at` |
| `ac_agent_compute_guard_event` | `event_id`, `run_id`, `agent_id`, `kind`, `details` |
| `ac_agent_compute_job` | `job_id`, `run_id`, `status`, `run_at`, `claimed_by`, `claim_epoch`, `lease_expires_at`, `attempts`, `reclaims`, `deadline_at`, `last_error`, `cancel_requested` |

SQL DDL lives in `sql/`.

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
| `plans.plan_json` (DAG prompt/edges) | ~~not persisted~~ → `round`; DAG dropped with the materialized-DAG model D1 | Dropped (reason: swarm is edge-less) |

### Agent/lineage
| POC field | Target | Status |
|---|---|---|
| `AgentSpec.name` | `agent.name` | Mapped |
| `AgentSpec.role`/`instructions` | `agent.task_spec` (TaskEnvelope JSON) | Mapped |
| `AgentSpec.metadata.type` (`"avernet"` string) | `agent.kind` (`AgentKind` enum: `llm`/`avernet`) | Mapped (typed enum) |
| `node_executions.agent` + `DAGNode.id` | `agent.name` + lineage (`agent_id`, `parent_agent_id`, `depth`) | Mapped |
| instance id (`{name}-{uuid}`) | `agent.agent_id` (fresh minted uuid per spawn) | Mapped |
| `DAGNode.wave` | `agent.spawn_round` | Mapped |
| Avernet `_bot_id` + lifecycle states | `agent.bot_id` + `agent.bot_state` (new: POC never persisted bot state) | Mapped |
| `AgentContext.upstream` | structural: lineage via `parent_agent_id` + `result_shard`s | Mapped |

### Messages (new concept — POC had none)
Sources: `node.input.goal` (→ `TaskEnvelope.objective`), `DAGPlan.prompt`, LLM request/response bodies. Stored in `ac_agent_compute_message` with `role`, `content`, `decision` (JSON), `token_usage` (JSON), `artifact_key`.

### Result shards (`node_executions.result`/`RunLog.results`/`final_output`)
`final_output` → `run.final_answer` + terminal-agent `result_shard`s.

### Rounds (`DAGNode.wave`/`plan_round`/`extension_history`)
One row per orchestration round.

### Guard events (new — POC never persisted)
Kinds: `serial_collapse`, `spurious_parallelism`, `dedupe_reject`, `depth_exceeded`, `width_exceeded`, `timeout`, `kill`, `stuck`.

### Dropped (with reasons)
| POC item | Dropped reason |
|---|---|
| `DAGPlan.edges` (dependency pairs) | Swarm is edge-less (D1); structure = lineage + messages |
| `DAGNode.wave`/topological order | Replaced by `spawn_round` + round timeline |
| `NodeStatus.SKIPPED` | No equivalent swarm state (task D14 review) |
| `NodeInput.params` | Dead field (never written by POC planner) |
| `app.workers` config | Unused in POC |
| `NodeStatus` enum (`PENDING/RUNNING/SUCCEEDED/FAILED/SKIPPED`) | Replaced by swarm `AgentStatus` |
| `HaltReason` (`done`/`abort`/`drift`) | Replaced by `TerminationReason` |
| Constructor-only thresholds (`max_extensions`, `replan_max_calls`, `max_parse_retries`, ...) | Not POC-persisted; became swarm config (guards, D20/D21) + persisted guard events |
| POC `driver.max_retries`/`max_workers`/replanner retry knobs | Not POC-persisted; map to `subagent_max_attempts`, `max_width`, `llm_max_retries` (D23) |

## Service / HTTP / CLI surface

Service Protocol (`service_protocol.py`): `submit_async`, `submit_sync`,
`get_run`, `list_runs`, `run_events`, `cancel`. The HTTP router prefix is
`/agentcompute/v1`; reads and per-run routes take the tenant from the
`X-Tenant-Id` header.

```bash
# async submit -> {"run_id": "..."}
curl -sS -X POST http://127.0.0.1:8000/agentcompute/v1/jobs -H 'Content-Type: application/json' -d '{
  "tenant": "t1", "goal": "map the incident to owning services",
  "agents": [{"name": "scout", "role": "research"}],
  "idempotency_key": "inc-42"}'

# sync submit (blocks until terminal or deadline)
curl -sS -X POST http://127.0.0.1:8000/agentcompute/v1/run -H 'Content-Type: application/json' -d '{"tenant": "t1", "goal": "summarize logs", "agents": [{"name": "s1", "role": "research"}]}'

# get / list / cancel
curl -sS http://127.0.0.1:8000/agentcompute/v1/jobs/<run_id> -H 'X-Tenant-Id: t1'
curl -sS 'http://127.0.0.1:8000/agentcompute/v1/jobs?page=1&page_size=20&status=COMPLETED' -H 'X-Tenant-Id: t1'
curl -sS -X POST http://127.0.0.1:8000/agentcompute/v1/jobs/<run_id>/cancel -H 'X-Tenant-Id: t1'

# SSE stream: status, then agent/round/guard deltas, progress heartbeats, terminal
curl -sS -N http://127.0.0.1:8000/agentcompute/v1/jobs/<run_id>/stream -H 'X-Tenant-Id: t1'

# report (HTML) + health
curl -sS http://127.0.0.1:8000/agentcompute/v1/jobs/<run_id>/report -H 'X-Tenant-Id: t1'
curl -sS http://127.0.0.1:8000/agentcompute/v1/health
```

Error mapping: tenant limit exceeded → 429 `tenant_run_limit_exceeded`;
missing LLM config → 400; unknown run → 404. SSE `run_events` advance row
cursors (`after_agent_id`, `after_round_id`, `after_event`), so each event
is emitted once per stream.

CLI (`build_parser`/`main(argv, client)`; the module has no `__main__`
guard, so invoke `main` directly):

```bash
cd apps/backend && DEPLOY_PROFILE=test PYTHONPATH=src:. .venv/bin/python -c \
  "from agentclaw.community.adapters.cli.agentcompute_cli import main; raise SystemExit(main(['submit', '--spec', 'spec.json', '--wait']))"
```

`spec.json` carries the submit body above; `--wait` polls until terminal.

## Test suite + coverage gate

```bash
cd apps/backend
DEPLOY_PROFILE=test PYTHONPATH=src:. .venv/bin/python -m pytest tests/community/core/agentcompute/ \
  -q --cov=agentclaw.community.core.agentcompute --cov-branch --cov-fail-under=90
```

Current: 285 tests, 97.30% branch coverage (gate ≥90%). Architecture
isolation + conformance gates live in `tests/community/architecture/`
(incl. `test_agentcompute_isolation.py`); the single-process E2E is
`tests/community/core/agentcompute/test_e2e.py`.
