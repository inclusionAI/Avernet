# agentcompute — Emergent Agent Swarm

LLM orchestrator that dynamically decomposes a goal and spawns ephemeral,
context-isolated subagents at runtime, converging via termination conditions
rather than a pre-authored plan (Kimi Agent Swarm model, D1).

## Layer placement (arch.rules.md §8)

| Layer | Path | Responsibility |
|---|---|---|
| api/ | `community/api/agentcompute/` | transport-agnostic Service Protocol |
| core/ | `community/core/agentcompute/` | runtime + domain + repository |
| adapters/http/ | `community/adapters/http/agentcompute/` | thin router + DTOs (Pydantic) |
| di/modules/ | `community/di/modules/agentcompute_module.py` | composition root |

## Isolation boundary (D17, D35)

agentcompute is feature-isolated (D35): no import of `core/task`, `core/task_queue`, `core/harness`, `plugin_api/object_storage`, or `core/system_config`. Allowed: framework kernel (`core/base.py` Base, DI, DatabasePlugin, schema.py, LifecycleBase) + platform operational infra (metrics, migrations).

## Domain model (D18, D43)

Domain models use `@dataclass` (zero framework dependency, matching `core/task/domain/models.py`). Transport DTOs in `adapters/http/agentcompute/schemas.py` are Pydantic BaseModel (FastAPI boundary). ORM models use SQLAlchemy `Base` from `core/base.py`.

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
