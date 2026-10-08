# agentcompute Swarm — Detailed Design

- **Date:** 2026-10-08
- **Status:** Draft (design contract; implementation not started)
- **Change:** `openspec/changes/migrate-agentcompute-swarm-into-backend/`
- **Scope:** Backend module `apps/backend/src/agentclaw/community/core/agentcompute/` + `api/agentcompute/`, `adapters/http/agentcompute/`, `di/modules/agentcompute_module.py`.
- **Authority:** This document is the detailed design companion to the OpenSpec change. Where it and the OpenSpec artifacts disagree, the OpenSpec `specs/**` requirements govern behavior; this document governs structure and interfaces.

---

## 1. Context and motivation

The backend already ships a mature **goal-driven task framework** at `apps/backend/src/agentclaw/community/core/task` (~17.8k LOC) on shared `task_*` storage: a decomposition-tree, goal+acceptance-convergence engine with planner / dispatcher / runner, a harness, and callback-driven reporting.

A standalone **agentcompute** POC (branch `poc/agentcompute`, `src/agentcompute`) explores multi-agent orchestration with ephemeral per-node agents. Its current implementation **materializes a DAG** and executes it with a static/dynamic driver. It lives outside the backend, persists to its own four SQLite tables via a raw `sqlite3` plugin, and exposes a sync-first FastAPI surface.

Research (Kimi K2.5/K2.6 Agent Swarm, Anthropic multi-agent research system, OpenAI Swarm/Agents SDK, AutoGen Swarm, LangGraph swarm) establishes that the intended target is a **different class of system**:

| Characteristic | POC (DAG engine) | Intended target (swarm) |
|---|---|---|
| Structure | Materialized DAG, authored + replanned | Emergent; produced at runtime |
| Coordination primitive | DAG edge / topological schedule | Orchestrator decision (`spawn`/`wait`/`finish`/`handoff`) |
| Who decides next step | Engine/scheduler | The orchestrator LLM |
| Planning | Plan-then-execute + mid-run replanner | Continuous; no separate plan artifact |
| Convergence | Sink-node completion | Termination conditions |
| Subagents | Ephemeral bot per node | Ephemeral, context-isolated subagents |
| Context | Node-scoped inputs | Sharded; only results route back |

This design specifies a **true emergent swarm runtime** integrated into the backend as an isolated subsystem.

### 1.1 Decisions locked with the requester

- agentcompute is a **true emergent swarm**, not a second DAG engine. No materialized plan; the orchestrator is an LLM in a structured-decision loop.
- agentcompute is a **fully isolated feature**: it MUST NOT reuse any other feature module — including `core/task`, `core/task_queue`, the harness LLM, the object-storage plugin, and the system-config service.
- agentcompute uses its **own `ac_agent_compute_*` tables** (fully isolated).
- agentcompute owns its **durable execution** (its own queue table + worker), its **LLM client**, its **artifact store**, and its **config**.
- The orchestrator emits **structured JSON decisions** (`spawn_agent` / `wait` / `finish` / `handoff`).
- The HTTP surface is **distinct** from the task API.

### 1.2 Isolation boundary — feature-isolation, not ops-isolation

**agentcompute is isolated from other *feature* modules; it MAY use platform *operational* infrastructure.** The distinction:

| Allowed | Blocked (feature modules) |
|---|---|
| Framework kernel: ORM `Base`, DI container, `DatabasePlugin` seam, `schema.py` registration, `LifecycleBase` | `core/task` (goal-driven framework), `core/task_queue`, `core/harness` LLM, `plugin_api/object_storage`, `core/system_config` |
| **Platform operational infra**: metrics/tracing (e.g. the platform's OTel/metrics seam), schema-migration tooling, connection-pool config, logging, the gateway-principal/auth seam | any other feature's `core/*` domain logic or its `di/modules/*` |

**agentcompute builds its own domain machinery** (orchestrator, subagent runtime, swarm storage, LLM client, artifact store, config) but MUST NOT hand-roll operational infrastructure the platform already provides (metrics, migrations, drain hooks). "Isolated" means *no feature-domain coupling*, not *no operations*.

---

## 2. Goals / Non-goals

**Goals**

- Land an emergent swarm runtime inside the backend 4-layer architecture, transport-agnostic and contract-governed.
- Model each run's structure as an emergent **spawn-lineage tree** persisted in `ac_agent_compute_*`, not a static plan.
- Run each swarm run as a **durable, exactly-once-claimed** unit of background work that survives restarts.
- Keep subagents **ephemeral and context-isolated**; route only results back.
- Enforce convergence via **composable termination conditions**; guard against **serial collapse** and **spurious parallelism**.
- Reuse **only** the framework kernel (`Base`, DI container, `DatabasePlugin` seam, schema registration, `LifecycleBase`); build agentcompute's own durable execution, LLM client, artifact store, and config — reusing no other feature module.
- Preserve the POC's observable behaviors where they still apply (run status, per-agent status, report), exposed through a distinct API.

**Non-goals**

- Rebuilding or modifying the goal-driven framework or `task_*` storage.
- Persisting a static DAG or requiring a planner that produces a full plan up front.
- Building a general-purpose workflow engine or a broker beyond agentcompute's own resumable-slice queue.
- Multi-region active-active or multi-writer replication (single-region horizontal fleet is the target).
- Cross-tenant model/KV-cache isolation for a shared model endpoint (per-tenant credentials mitigate; deep isolation is a separate hardening track).
- Frontend work.

---

## 3. Architecture overview

```
                 HTTP (distinct surface)                        CLI
   ┌──────────────────────────────────────┐              ┌────────────────┐
   │ adapters/http/agentcompute/           │              │ agentcompute   │
   │  POST /run   (sync)                    │              │  CLI entry     │
   │  POST /jobs  (async)                   │              └───────┬────────┘
   │  GET  /jobs/{id}                       │                      │
   │  GET  /jobs/{id}/stream  (SSE)         │                      │
   │  GET  /jobs/{id}/report  (HTML)        │                      │
   │  POST /jobs/{id}/cancel                │                      │
   └───────────────────┬────────────────────┘                      │
                       │  AgentComputeServiceProtocol (transport-agnostic)
                       ▼                                             ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │  api/agentcompute/  — Service Protocol                                  │
   └───────────────────────────────┬───────────────────────────────────────┘
                                   ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │  core/agentcompute/                                                     │
   │                                                                         │
   │   ┌───────────────┐    decisions    ┌───────────────────────────────┐   │
   │   │ Orchestrator   │ ─────────────▶ │ Subagent Runtime               │   │
   │   │ (LLM loop)     │ ◀───────────── │  bounded queue + worker pool   │   │
   │   │  spawn/wait/   │  results only  │  completion queue              │   │
   │   │  finish/handoff│                │  ┌──────────┐  ┌────────────┐  │   │
   │   └──────┬─────────┘                │  │ LLM agent│  │ Avernet    │  │   │
   │          │                          │  │          │  │ bot agent  │  │   │
   │   ┌──────▼─────────┐                │  └────┬─────┘  └─────┬──────┘  │   │
   │   │ Termination &   │               └───────┼──────────────┼─────────┘   │
   │   │ Guardrails      │                       │              │             │
   │   └──────┬─────────┘                        │              │  HTTPS      │
   │          │                                  │              ▼             │
   │   ┌──────▼──────────────────────────────────▼───────────────────────┐    │
   │   │ Repository layer (DatabasePlugin.orm_session)                    │    │
   │   └──────┬───────────────────────────────────────────┬──────────────┘    │
   └──────────┼───────────────────────────────────────────┼───────────────────┘
              ▼                                            ▼
     ac_agent_compute_* tables                     agentcompute's own
     (run/agent/message/result_shard/             ArtifactStore (filesystem)
      round/guard_event/job)                        (large artifacts, key refs)

   Durable substrate: ac_agent_compute_job (agentcompute's own queue table)
    → agentcompute worker claims ONCE → resident loop (held + heartbeated) → terminal
```

### 3.1 Layer placement (arch.rules.md §8)

| Layer | Path | Responsibility |
|---|---|---|
| api/ | `community/api/agentcompute/` | transport-agnostic service Protocol |
| core/ | `community/core/agentcompute/` | runtime + domain + repository (no transport imports) |
| adapters/http/ | `community/adapters/http/agentcompute/` | thin router + schemas + translation |
| di/modules/ | `community/di/modules/agentcompute_module.py` | composition root |

### 3.2 Module tree

```
core/agentcompute/
├── README.md                       # swarm model, storage mapping, boundary vs core/task
├── domain/
│   ├── models.py                   # Run, Agent, Message, ResultShard, Round, GuardEvent, enums
│   └── errors.py                   # AgentComputeError hierarchy
├── spi/
│   ├── llm_provider.py             # LLMProvider SPI
│   ├── subagent.py                 # Subagent SPI (Protocol: setup/run_task/teardown/halt)
│   ├── subagent_factory.py          # SubagentFactory SPI (create(agent) -> Subagent)
│   ├── artifact_store.py           # ArtifactStore SPI
│   ├── decision_parser.py          # DecisionParser SPI (parse -> DecisionParse)
│   └── bot_api_client.py           # BotApiClient SPI (Avernet backend bot API, D41)
├── orchestration/
│   ├── orchestrator.py             # decision loop
│   ├── decision.py                 # decision schema + parse/validate
│   ├── termination.py              # composable termination conditions
│   └── guards.py                   # serial-collapse / spurious-parallelism / budget
├── runtime/
│   ├── subagent_runtime.py         # spawn, bounded queue + worker pool, completion queue, timeouts
│   ├── completion.py               # completion channel + wait(all|any)
│   └── swarm_run.py                # one round of a run + round state
├── repository/
│   ├── models.py                   # ORM models (ac_agent_compute_*)
│   ├── types.py                    # record dataclasses
│   └── serializers.py              # enum/json round-trips
├── plugins/
│   ├── llm/                        # LLM client (OpenAI-compatible + stub)
│   ├── agents/                     # llm_subagent.py, avernet_subagent.py
│   ├── artifact_store/             # filesystem ArtifactStore impl
│   ├── bot_api/                    # BotApiClient impl (Avernet backend-bot API)
│   └── decision_parser/            # DecisionParser impl (parse/validate/repair)
└── sql/
    └── 2026_10_08_agent_compute.sql  # OceanBase DDL
```

---

## 4. Domain model

**Typing principle.** Every parameter, API field, SPI argument, and persisted value uses a **typed model** — never a bare `str` for a value that has a closed set or a distinct meaning. Opaque identifiers use `NewType` wrappers (so a `RunId` can't be passed where an `AgentId` is expected); closed sets use `StrEnum`; structured payloads use frozen dataclasses (domain) or Pydantic DTOs (transport). The only raw `str` allowed is free-form human text (a goal, an objective, an error message) and JSON-schema/MCP payload dicts whose shape is caller-defined.

All domain types are plain dataclasses / StrEnums / NewTypes with **zero transport or framework dependency**. Transport DTOs live in `adapters/http/` and are Pydantic models mirroring the domain. `DAGPlan`-era loose dicts are replaced by typed records.

### 4.1 Identifiers (NewType)

```python
from typing import NewType

RunId = NewType("RunId", str)
AgentId = NewType("AgentId", str)
TenantId = NewType("TenantId", str)
JobId = NewType("JobId", str)
MessageId = NewType("MessageId", str)
ShardId = NewType("ShardId", str)
RoundId = NewType("RoundId", str)
EventId = NewType("EventId", str)
ArtifactKey = NewType("ArtifactKey", str)     # object-store key (caller-owned layout)

# provider/model identity — distinct types so a model id can't be passed as a provider
ProviderId = NewType("ProviderId", str)
ModelId = NewType("ModelId", str)
```

### 4.2 Enums (every closed set is an enum — no string literals in code or signatures)

```python
class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"            # orchestrator finished normally
    FAILED = "FAILED"                  # unrecoverable error
    CANCELLED = "CANCELLED"            # external cancel
    TIMED_OUT = "TIMED_OUT"
    GUARD_KILLED = "GUARD_KILLED"
    STUCK = "STUCK"                    # reclaim circuit breaker tripped (no progress)

class AgentStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    KILLED = "KILLED"

class AgentKind(StrEnum):
    LLM = "llm"
    AVENET = "avernet"

class BotState(StrEnum):               # replaces free-form bot_state strings
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    CREATE_FAILED = "CREATE_FAILED"
    APPLY_FAILED = "APPLY_FAILED"

class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    DECISION = "decision"

class GuardKind(StrEnum):
    TIMEOUT = "timeout"
    SERIAL_COLLAPSE = "serial_collapse"
    SPURIOUS_PARALLELISM = "spurious_parallelism"
    DEDUPE_REJECT = "dedupe_reject"
    DEPTH_EXCEEDED = "depth_exceeded"
    WIDTH_EXCEEDED = "width_exceeded"
    KILL = "kill"

class TerminationReason(StrEnum):
    FINISH = "finish"
    NO_DECISION = "no_decision"
    NO_PROGRESS = "no_progress"        # M1: repeated identical spawns/decisions
    TURN_BUDGET = "turn_budget"
    MAX_AGENTS = "max_agents"            # M1: hard cap on total spawned agents
    WALL_CLOCK = "wall_clock"
    CANCELLED = "cancelled"
    STUCK = "stuck"                     # reclaim circuit breaker
    ERROR = "error"
    GUARD = "guard"

class DecisionKind(StrEnum):           # orchestrator decision discriminator
    SPAWN_AGENT = "spawn_agent"
    WAIT = "wait"
    HANDOFF = "handoff"
    FINISH = "finish"

class WaitMode(StrEnum):
    ALL = "all"
    ANY = "any"

class LlmCallKind(StrEnum):            # what a completion is for (drives prompt + model)
    ORCHESTRATOR = "orchestrator"
    SUBAGENT = "subagent"

class WorkerOutcomeKind(StrEnum):      # agentcompute's own queue outcome (analogue of Complete/Reschedule/Retry/Fail)
    COMPLETE = "complete"
    RESCHEDULE = "reschedule"
    RETRY = "retry"
    FAIL = "fail"

class TaskQueueStatus(StrEnum):        # ac_agent_compute_job.status
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
```

### 4.3 Value objects (frozen dataclasses)

```python
@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

@dataclass(frozen=True)
class AcceptanceGap:                  # a structured gap (never a loose dict)
    id: str
    detail: str

@dataclass(frozen=True)
class TaskEnvelope:                   # the unit of work handed to a subagent
    name: str                         # spawn-local handle used by wait refs (e.g. "a1"); resolved to an AgentId
    objective: str                    # free-form human text
    output_schema: dict[str, Any]     # caller-defined JSON schema
    boundaries: str                   # free-form scope text
    parent_goal: str                  # free-form context text

@dataclass(frozen=True)
class SubagentResult:                 # a subagent's final result (returned to the runtime)
    output: StructuredOutput
    token_usage: TokenUsage | None = None

```

> **Tool calling is out of scope for this change.** Subagents are LLM-completion-only (text generation) or Avernet-bot-backed (chat completion via the backend's own API). The swarm is a **text-generation swarm**, not a tool-augmented one. Tool execution (web search, code execution, API calls) may be added in a follow-up; it is deliberately deferred here.

**Name → identity resolution (B4):** `TaskEnvelope.name` is a **spawn-local handle** the orchestrator uses in `WaitDecision.refs`. On dispatch the system mints a fresh `AgentId` for each spawned envelope and records an `(name → AgentId)` alias for the current round; `WaitDecision.refs` (a tuple of those names) is resolved through that alias map to `AgentId`s before `runtime.wait(...)`. Aliases are scoped to the run (a name reused in a later round resolves to the latest agent with that name, or the system rejects the reuse — one rule, stated once). The `Agent` record's `name` (§4.4) is the envelope name; its `agent_id` is the minted id.

### 4.4a SPI definitions (Protocols — the contracts the swarm core depends on)

```python
@runtime_checkable
class Subagent(Protocol):
    """A single ephemeral subagent executor. Lifecycle: setup -> run_task -> teardown."""
    name: str
    async def setup(self) -> None: ...
    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult: ...
    async def teardown(self) -> None: ...
    async def halt(self) -> None: ...

@runtime_checkable
class SubagentFactory(Protocol):
    """Creates a Subagent instance for a given agent spec."""
    def create(self, agent_spec: AgentSpecRaw, run: Run) -> Subagent: ...

@runtime_checkable
class ArtifactStore(Protocol):
    """Stores large outputs by key; persist only the key reference in the shard."""
    def put(self, key: str, content: bytes | str) -> bool: ...
    def put_file(self, key: str, local_path: str) -> bool: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> bool: ...
    def sign_url(self, key: str, expires: int = 7200) -> str: ...
```

### 4.4 Records (mutable domain dataclasses — field names match the tables)

```python
@dataclass
class Run:
    run_id: RunId
    tenant: TenantId
    goal: str                                  # free-form human text
    agent_catalog: tuple[AgentSpecRaw, ...]    # typed catalog (see §5.1)
    provider: ProviderId
    request_metadata: RequestMetadata          # typed, not dict[str, Any]
    status: RunStatus
    termination_reason: TerminationReason | None
    root_agent_id: AgentId | None
    final_answer: FinalAnswer | None           # typed (text | structured mapping)
    error: str | None                          # free-form message text
    started_at: EpochMillis
    ended_at: EpochMillis | None

@dataclass
class Agent:
    agent_id: AgentId
    run_id: RunId
    parent_agent_id: AgentId | None
    depth: int
    name: str
    role: str
    kind: AgentKind
    task_spec: TaskEnvelope
    spawn_round: int
    status: AgentStatus
    attempts: int
    idempotency_key: IdempotencyKey             # H4: stable per logical subagent; reused on re-spawn
    output: StructuredOutput
    error: str | None
    bot_id: BotId | None
    bot_state: BotState | None
    started_at: EpochMillis | None
    ended_at: EpochMillis | None

@dataclass
class Message:
    id: MessageId
    run_id: RunId
    agent_id: AgentId
    seq: int
    role: MessageRole
    content: MessageContent                    # typed union: TextContent | JsonContent
    decision: Decision | None                  # typed Decision when role=DECISION (not Any)
    token_usage: TokenUsage | None
    artifact_key: ArtifactKey | None
    created_at: EpochMillis

@dataclass
class ResultShard:
    id: ShardId
    run_id: RunId
    source_agent_id: AgentId
    payload: StructuredOutput
    artifact_key: ArtifactKey | None
    routed_at: EpochMillis

@dataclass
class Round:
    id: RoundId
    run_id: RunId
    stage_index: int
    orchestrator_steps: int
    max_subagent_steps: int
    cohort_agent_ids: tuple[AgentId, ...]
    started_at: EpochMillis
    ended_at: EpochMillis | None

@dataclass
class GuardEvent:
    id: EventId
    run_id: RunId
    agent_id: AgentId | None
    kind: GuardKind
    details: GuardDetails                      # typed per-kind payload, not dict[str, Any]
    created_at: EpochMillis
```

Supporting typed aliases / wrappers (declared where used):

```python
EpochMillis = NewType("EpochMillis", int)      # never a bare int
BotId = NewType("BotId", str)
IdempotencyKey = NewType("IdempotencyKey", str)   # H4: stable per logical subagent
SecretRef = NewType("SecretRef", str)             # M5: name of a secret; resolved by the DI module

@dataclass(frozen=True)
class RequestMetadata:                         # replaces opaque dict[str, Any]
    title: str = ""
    instruction: str = ""
    extend: dict[str, str] = field(default_factory=dict)   # flat string map; secrets stripped

@dataclass(frozen=True)
class AgentSpecRaw:                            # caller-offered candidate agent
    name: str
    role: str
    instructions: str = ""
    kind: AgentKind = AgentKind.LLM
    model: ModelId | None = None               # per-agent model override (None -> config default)
    metadata: dict[str, str] = field(default_factory=dict)

StructuredOutput = dict[str, Any]              # caller-defined JSON shape (documented)
FinalAnswer = str | StructuredOutput           # text or structured result
MessageContent = str | StructuredOutput        # text or JSON
GuardDetails = StructuredOutput                # per-kind documented shape
```

**Catalog persistence schema (M4).** `run.agent_catalog` is stored as JSON text and round-trips a `list[AgentSpecRaw]` under a documented schema: `[{"name", "role", "instructions", "kind" (the `AgentKind` value), "metadata" (flat string map)}]`. The serializer maps `kind` to/from the enum value; unknown keys are rejected on load. This is the loader/serializer contract for the caller-provided catalog.

**SecretRef resolution (M5).** `SecretRef` is the *name* of a secret. It is resolved **only** in agentcompute's DI module / composition boundary — the module reads it from the deployment's secret source (env/config at boot) and hands the client a resolved token (or a frozen `SecretValue`); `core/agentcompute` never reads the secret or the environment itself (consistent with §12).

---

## 5. Orchestrator design

### 5.1 The decision contract

The orchestrator emits **one typed decision per turn** — a discriminated union on `DecisionKind`. On the wire it is JSON; in code it is a frozen dataclass. Parsing produces a typed `Decision | None`; a `kind` outside `DecisionKind`, a missing required field, or an empty `agents` on a spawn yields `None` (treated as terminate).

**Wire shapes (JSON):**

```jsonc
// kind = "spawn_agent"
{ "kind": "spawn_agent",
  "agents": [
    { "name": "a1",
      "objective": "…",
      "output_schema": { "type": "object", "properties": { "…": {} } },
            "boundaries": "…" }
  ] }

// kind = "wait"
{ "kind": "wait", "refs": ["a1","a2"], "mode": "all" }   // mode ∈ WaitMode

// kind = "handoff"
{ "kind": "handoff", "target": "reviewer", "reason": "…" }

// kind = "finish"
{ "kind": "finish", "answer": "…" }        // or a structured object
```

**Typed decision models (domain):**

```python
@dataclass(frozen=True)
class SpawnDecision:
    kind: DecisionKind = DecisionKind.SPAWN_AGENT     # fixed discriminator (documentation)
    agents: tuple[TaskEnvelope, ...]                  # each carries name/objective/schema/boundaries

@dataclass(frozen=True)
class WaitDecision:
    kind: DecisionKind = DecisionKind.WAIT
    refs: tuple[str, ...]                             # spawn-local names → resolved to AgentId
    mode: WaitMode = WaitMode.ALL

@dataclass(frozen=True)
class HandoffDecision:
    kind: DecisionKind = DecisionKind.HANDOFF
    target: str                                       # agent role/name to hand control to
    reason: str = ""

@dataclass(frozen=True)
class FinishDecision:
    kind: DecisionKind = DecisionKind.FINISH
    answer: FinalAnswer = ""

Decision = SpawnDecision | WaitDecision | HandoffDecision | FinishDecision

@dataclass(frozen=True)
class DecisionParse:
    decision: Decision | None
    error: str | None                                 # validation error for the repair loop (D20)

class DecisionParser(Protocol):
    def parse(self, raw: str) -> DecisionParse: ...    # decision=None + error set → caller repairs (D20)
```

> **Typing note (M2):** `kind` is a plain `DecisionKind` with a fixed default, **not** `Literal[DecisionKind.X]` — `Literal` on a `StrEnum` member is redundant and not cleanly typed. Dispatch uses structural `match` on the concrete decision class (§5.2), so `kind` is documentation/round-trip, not the discriminator mechanism.

**Parsing and repair (D20):**

- Strip code fences; parse the first balanced JSON object.
- Validate against the typed models; unknown `kind`, missing required fields, or empty `agents` on `SpawnDecision` yields `decision=None` **with an `error`**.
- **On `decision=None`: run a bounded repair loop (`repair_max`, default 2): re-request the decision from the LLM, feeding the validation `error` back, up to the bound.** Only **after** the repair budget is exhausted is the run terminated (`TerminationReason.NO_DECISION`). Terminating on the first bad decision is forbidden.
- `output_schema` stays a caller-defined JSON-schema dict (`dict[str, Any]`), the one intentionally-open payload.
- The orchestrator's own completion request is issued with a constrained structural output where the provider supports it (strict JSON schema / tool-use); the repair loop is the fallback.

### 5.2 Orchestrator control loop (one round)

```python
async def run_round(self, run: Run, round_state: RoundState) -> RoundOutcome:
    # 1. Build the orchestrator prompt: goal + catalog + recent result shards only.
    # 2. Invoke the LLM; parse; on failure run the bounded repair loop (D20).
    parse = await self._decide_with_repair(run, round_state)   # ≤ repair_max re-asks, error fed back
    if parse.decision is None:
        return RoundOutcome.terminate(TerminationReason.NO_DECISION)   # only after repair exhausted
    decision = self._guards.review(run, round_state, parse.decision)   # may veto/reject

    # 4. Dispatch on the typed union (structural match).
    match decision:
        case FinishDecision(answer=a):      return RoundOutcome.finish(a)
        case WaitDecision(refs=names, mode=m):
            ids = round_state.resolve_names(names)                 # name → AgentId (B4)
            await self._runtime.wait(ids, m); return RoundOutcome.continue_()
        case HandoffDecision(target=t):
            round_state.active = self._resolve_persona(t)   # D30: resolve via catalog; unresolvable → repair
            return RoundOutcome.continue_()
        case SpawnDecision(agents=agents):
            for envelope in agents:
                self._guards.check_spawn(run, round_state, envelope)   # depth/width/dedup
                self._runtime.spawn(run, envelope, round_state)        # non-blocking; mints AgentId + alias
            return RoundOutcome.continue_()

    # 5. Termination conditions evaluated over the turn delta (see §5.3).
```

**Non-gating invariant & async model (D34 / H3).** The orchestrator does not barrier on running subagents unless it issued an explicit `wait`. Within a **slice**, the resident loop selects over `{orchestrator_decision_future} ∪ {subagent_completion_future}` via `asyncio.wait(FIRST_COMPLETED)`. A `wait` parks the orchestrator until the named cohort is satisfied. At the **slice boundary**, the loop persists continuation state and returns — releasing the claim so any pod may re-enter. In-flight subagents that do not finish within the slice are **parked** (agent row left `RUNNING` with no committed shard, re-spawned next slice by `idempotency_key`); they are not kept alive as live `asyncio.Task`s. A crash tears the slice down and recovery (§9.2) rehydrates from lineage/shards.

### 5.3 Run loop (across rounds)

The durable-queue handler drives this as **bounded slices** (D34): the worker claims the job for one slice, runs the resident loop until the slice budget elapses or the run is terminal, persists continuation state, and returns — releasing the claim so **any** worker can run the next slice. Outcomes are a **typed `WorkerOutcome`**.

```python
# Module-level terminal set (H2) — NOT an invented enum method.
TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset({
    RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
    RunStatus.TIMED_OUT, RunStatus.GUARD_KILLED, RunStatus.STUCK,
})

async def handle_slice(self, claim: Claim) -> WorkerOutcome:      # D34
    run, state = self._load_run_state(claim.run_id)               # recovery-aware (D16)
    if run.status in TERMINAL_RUN_STATUSES:
        return WorkerOutcome.complete()
    
    try:
        outcome = await self._run_resident_slice(run, state, claim.epoch, self._slice_budget())
    except TransientRunError as exc:                              # transient → requeue (not a completion)
        return WorkerOutcome.retry(error=str(exc))

    term = self._termination.evaluate(run, state, outcome)        # incl. reclaim circuit breaker
    if term is not None:
        self._finalize(run, term); return WorkerOutcome.complete()
    if outcome.fatal:
        self._fail(run, outcome.error); return WorkerOutcome.fail(outcome.error)

    try:
        self._persist_continuation(run, state)                    # round index, in-flight continuations
    except TransientRunError as exc:
        return WorkerOutcome.retry(error=str(exc))               # boundary-write failure → requeue, not STUCK
    return WorkerOutcome.reschedule(seconds=self._config.slice_interval)   # park; any worker resumes
```

**Slice model (D34).** Unlike the prior one-long-claim model, a slice is bounded: at most `slice_max_seconds` (or one round), after which the handler persists continuation state and returns. The resident loop **checks `claim.epoch` on every write** and self-aborts if its slice was reclaimed (fencing). In-flight subagents that cannot finish within the slice are **parked as continuations** (re-created on the next slice from their `idempotency_key`), so they are not orphaned — the only state that must survive is the agent row (RUNNING, no shard) + the shard set, not a live `asyncio.Task`.

**Turn cadence (H1).** After a non-`wait` decision the loop awaits `asyncio.wait({orchestrator_turn_timer, completion}, FIRST_COMPLETED)` bounded by `orchestrator_turn_interval` — no unthrottled decision spin.

### 5.2a Handoff (D30)

A `handoff` decision changes the orchestrator's **persona** for subsequent turns: `target` resolves through the run's agent catalog to a spec whose `role`/`instructions` become the orchestrator persona (`round_state.active`, **read by the prompt builder** and persisted in the slice continuation). The handoff is recorded as a `Message(role=DECISION)`. An unresolvable `target` is an invalid decision → repair loop (D20).

### 5.4 Context sharding and result routing

- Subagent initial context = `TaskEnvelope` only (`objective`, `output_schema`, `boundaries`, `parent_goal`).
- On completion the runtime writes a `ResultShard` (structured `payload`, or `artifact_key` for large output) and enqueues it to the completion channel.
- The orchestrator's prompt includes only recent **result shards**, never a subagent's message trace.

**Result-schema validation (D42).** Before a subagent result is routed back as a shard, the system SHALL validate it against the TaskEnvelope's `output_schema` (if provided). A result that fails validation is treated as a subagent failure (retry under `subagent_max_attempts`) rather than routing an invalid result to the orchestrator. This prevents malformed outputs from corrupting the orchestrator's context.

---

## 6. Subagent runtime design

### 6.1 Concurrency and backpressure

Bounded concurrency uses a **bounded work queue + a fixed pool of worker tasks** (D23) — **not** a raw `asyncio.Semaphore` (a cancelled task can leak a semaphore token because it never runs its `finally`). Queue capacity provides backpressure; worker tasks are long-lived for the run; task handles are retained in an owner set; `CancelledError` is re-raised after cleanup.

```python
class SubagentRuntime:
    def __init__(self, *, max_width: int, per_task_timeout: float, max_attempts: int, ...):
        self._work: asyncio.Queue[WorkItem] = asyncio.Queue(maxsize=max_width)  # backpressure
        self._completions: asyncio.Queue[ResultShard] = asyncio.Queue()          # completion channel
        self._workers: list[asyncio.Task] = []
        self._running: set[asyncio.Task] = set()          # retained handles (no fire-and-forget)

    async def _worker(self) -> None:
        while True:
            item = await self._work.get()                 # blocks when idle; capacity bounds batch
            try:
                await self._run_with_retry(item)
            except asyncio.CancelledError:
                raise                                     # never swallow; propagates teardown
            finally:
                self._work.task_done()

    async def _run_with_retry(self, item: WorkItem) -> None:
        for attempt in range(1, self._max_attempts + 1):
            item.agent.attempts = attempt
            try:
                result = await asyncio.wait_for(
                    self._subagent_for(item.agent).run_task(item.envelope),  # fresh unit each attempt
                    timeout=self._per_task_timeout,
                )
                shard = await self._route(item.run, item.agent, result)      # artifact-aware
                await self._completions.put(shard)                           # completion channel
                return
            except asyncio.TimeoutError:
                item.agent.error = "timeout"              # retryable like a failure (B2)
                if attempt < self._max_attempts:
                    continue                              # do NOT kill a retryable agent mid-loop
                self._kill(item.agent, GuardKind.TIMEOUT) # final attempt → kill + record
            except Exception as exc:
                item.agent.error = str(exc)               # retry with fresh context
        self._fail(item.agent)                            # attempts exhausted → surface failure
```

- **`spawn(...)` enqueues a `WorkItem`**; if the queue is full it **awaits space** (backpressure), so the spawn does not exceed `max_width`.
- Excess spawns sit `QUEUED` (persisted as such) until a worker picks them up.
- Each subagent runs as an `asyncio.Task` owned by a pool worker; **never** a thread (GIL + SQLAlchemy session hazards).
- Each retry attempt builds a **fresh execution unit and fresh context** — a failed attempt's state is never reused (§6.4).
- **Timeout is retryable** like any failure; the agent is killed only on the final attempt. Kill and retry are mutually exclusive.
- Persistence per agent uses a **short-lived `orm_session` per write**; no session is held across an `await` (§8.5).

### 6.2 Wait semantics

```python
async def wait(self, refs: tuple[AgentId, ...], mode: WaitMode) -> None:
    match mode:
        case WaitMode.ANY: await self._first_completion(refs)
        case WaitMode.ALL: await self._all_completions(refs)
```

`refs` are **already-resolved `AgentId`s** (the caller mapped spawn-local names via `round_state.resolve_names`, B4). `any` = first of the cohort; `all` = wait until every ref has a persisted terminal result shard — **already-complete refs are satisfied immediately** (checked against persisted shards, not only the in-memory channel). The channel is a latency optimization; persisted shards are the source of truth (§5.4).

### 6.3 Subagent kinds

| Kind | Setup | Execute | Teardown |
|---|---|---|---|
| `LLM` | build prompt from envelope | one LLM completion → parse structured result | — |
| `AVERNET` | create bot via the **backend's own bot API** (no external gateway), poll to `READY` | stream chat completion | delete bot; on failure record `bot_state` |

**Avernet subagent calls the backend's own API directly (D41).** The POC created bots via an external Avernet gateway (`gateway_base_url` + `principal_token` + HTTP paths). Now that agentcompute lives inside the backend, the Avernet subagent calls the **backend's own bot-management API** (e.g., `/openapi/v1/bots/...`) directly over localhost HTTP — no external gateway is needed. Config: `avernet.backend_base_url` (the backend's own API root) replaces the POC's `gateway_base_url`/`principal_token`. Bot endpoints: `bot_create_path`, `status_path`, `delete_path`. Chat completion via the backend's WebSocket session API (see §6.3). A deterministic client-supplied identity (`run_id:agent_id`) is passed for idempotent bot creation. The agentcompute API itself is **exposed to the gateway**: `/agentcompute/v1/...` is the surface the gateway routes external requests to (§17.6).

Avernet lifecycle states: `READY` and the terminal failures `AUTHORIZATION_REJECTED`, `AUTHORIZATION_EXPIRED`, `CREATE_FAILED`, `APPLY_FAILED` are **from the POC**; `PROVISIONING` is **new**. Selection is by the agent spec's `kind` (`AgentKind.AVERNET` vs `AgentKind.LLM`, §4.4).

> **Streaming note (C):** the POC *streamed* LLM/bot output but immediately **buffered it into one assembled string** before use. The new `LLMProvider.complete` returns one `CompletionResponse` (§10.1), so **observable result semantics are unchanged**. SSE streams **run/agent progress**, not raw model tokens.

### 6.4 Async model and retry layers

**Async.** The whole runtime is async by construction: `POST /jobs` enqueues a durable run and returns immediately; the worker runs the resident run loop on an event loop; subagents are executed by a **fixed worker pool** draining a **bounded queue**, with an `asyncio.Queue` completion channel. The synchronous `POST /run` is a thin `await`-wrapper over the same async engine (no separate sync implementation). LLM and Avernet HTTP calls are non-blocking.

**Retry layers (bounded, per layer):**

| Layer | Mechanism | Bound | Recorded on |
|---|---|---|---|
| **LLM call** | client retries transient failures with exponential backoff + jitter; non-retryable errors fail fast | `llm_max_retries` | client logs |
| **Subagent attempt** | a failed subagent run is retried with a **fresh execution unit + fresh context** (no poisoned reuse) | `subagent_max_attempts` (POC default 3) | `agent.attempts` / agent status |
| **Run round (job)** | transient round failure → requeue the job within the deadline; unrecoverable → fail the run | run deadline | `ac_agent_compute_job.attempts` / `last_error` |
| **Guard re-spawn** | an agent killed by a recoverable guard is re-spawned with fresh context | `respawn_max` | `guard_event` |

Cross-cutting rules:
- Every retry uses a **fresh context** — retries never resume a poisoned message list.
- Retries are **bounded** and never unblocked by wall-clock alone; the **run deadline** is the outer bound (a job cannot outlive `deadline_at`).
- When retries are exhausted, the failure is **surfaced to the orchestrator** (which may re-plan) or ends the run — never silently swallowed or fabricated as success.
- Idempotency: each logical subagent carries an `idempotency_key` (H4); re-claiming a job after a lost lease, or re-spawning after a crash, is safe because side-effectful executors are idempotent/check-then-create and the run reconstructs from persisted lineage/shards (D16/D24). LLM-only subagents are naturally idempotent; the key is still recorded for lineage consistency.

---

## 7. Termination and guardrails

### 7.1 Composable termination conditions

Evaluated **per turn over the delta**, first-match-wins:

| Condition | Signal | Reason |
|---|---|---|
| Finish | `finish` decision | `finish` |
| No decision (post-repair) | empty/invalid decision after `repair_max` re-asks | `no_decision` |
| No-progress | N consecutive identical spawn objectives/decisions | `no_progress` |
| Turn/message cap | turn count ≥ limit | `turn_budget` |
| Max total agents | total spawned agents ≥ `max_total_agents` | `max_agents` |
| Wall-clock | elapsed ≥ deadline | `wall_clock` |
| External cancel | cancel signal set | `cancelled` |
| Depth cap | spawn depth > max | `error` (spawn rejected) |
| Width cap | concurrent ≥ max | backpressure (queue full), not termination |

### 7.2 Guards

- **Serial collapse:** track orchestrator's own work vs spawn count over a rolling window; if orchestrator calls dominate and spawns ≈ 0 → `GuardEvent(SERIAL_COLLAPSE)` (and the prompt is nudged to decompose).
- **Spurious parallelism:** dedup spawn objectives by normalized text/embedding similarity against existing agents; reject near-duplicates → `GuardEvent(DEDUPE_REJECT)`. A round spawning many agents with non-distinct objectives → `GuardEvent(SPURIOUS_PARALLELISM)`.
- **Premature-finish guard:** a `finish` with zero routed shards and no prior spawns is treated as no-progress (not valid completion); the run records a guard event instead.
- **Kill with reason:** `kill(agent, reason)` sets `AgentStatus.KILLED` and persists the reason; run cancellation cascades to all running agents.
- **Guard-driven re-spawn:** an agent killed by a *recoverable* guard (health/budget breaker, not a hard failure or cancellation) is re-spawned with a **fresh context**, recording the reason; re-spawns are bounded (`respawn_max`) — at the bound the agent is marked failed and the failure surfaces to the orchestrator.
- **Progress-aware:** `max_turns` + `max_total_agents` + no-progress detection prevent a run from extending indefinitely.

---

## 8. Persistence design

### 8.1 Tables (`ac_agent_compute_*`)

**Mandatory columns — present on EVERY table below** (listed once here, omitted from the per-table rows to reduce noise):

| column | type | null | notes |
|---|---|---|---|
| `id` | BigInteger AUTO_INCREMENT | no | PK (SQLite: `INTEGER` via `with_variant`) |
| `gmt_create` | timestamp | no | `DEFAULT CURRENT_TIMESTAMP` |
| `gmt_modified` | timestamp | no | `DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` |
| `env` | varchar(20) | no | deployment environment |
| `tenant` | varchar(64) | no | tenant discriminator — taken from the API request and propagated to every row for the run |
| `is_deleted` | tinyint(1) / Boolean | no | logical-delete flag, default 0 |

Every per-table index set below includes an `env`+`tenant` scoping prefix, and all default reads filter `is_deleted = 0`.

**`ac_agent_compute_run`**

| column | type | null | notes |
|---|---|---|---|
| `id` | BigInteger AUTO_INCREMENT | no | PK |
| `run_id` | varchar(128) utf8mb4_bin | no | unique `uk_run_id` |
| `goal` | text | no | |
| `agent_catalog` | text (JSON) | yes | caller-provided catalog (schema §4.4) |
| `provider` | varchar(64) | yes | diagnostic label |
| `request_metadata` | text (JSON) | yes | opaque, secrets stripped |
| `status` | varchar(32) | no | `RunStatus` |
| `termination_reason` | varchar(32) | yes | `TerminationReason` |
| `root_agent_id` | varchar(128) | yes | |
| `final_answer` | text (JSON) | yes | |
| `error` | text | yes | |
| `ended_at` | bigint | yes | |
| _+6 mandatory_ | | | `gmt_create`/`gmt_modified`/`env`/`tenant`/`is_deleted` (+ `id`) |

Indexes: `uk_run_id (env, tenant, run_id)`, `uk_run_idem (env, tenant, active_idempotency_key)`, `idx_scope_status (env, tenant, status, gmt_modified)`.

**`ac_agent_compute_agent`**

| column | type | null | notes |
|---|---|---|---|
| `id` | BigInteger AUTO_INCREMENT | no | PK |
| `agent_id` | varchar(128) utf8mb4_bin | no | |
| `run_id` | varchar(128) utf8mb4_bin | no | |
| `parent_agent_id` | varchar(128) | yes | lineage |
| `depth` | int | no | 0 = root |
| `name` | varchar(128) | no | |
| `role` | varchar(256) | yes | |
| `kind` | varchar(16) | no | `AgentKind` |
| `task_spec` | text (JSON) | yes | envelope |
| `spawn_round` | int | no | |
| `status` | varchar(32) | no | `AgentStatus` |
| `attempts` | Integer | no | default 0; subagent retry attempt count |
| `idempotency_key` | varchar(190) utf8mb4_bin | no | H4: stable per logical subagent; reused on re-spawn |
| `output` | text (JSON) | yes | |
| `error` | text | yes | |
| `bot_id` | varchar(128) | yes | avernet |
| `bot_state` | varchar(32) | yes | avernet |
| `started_at` / `ended_at` | bigint | yes | |
| _+6 mandatory_ | | | `gmt_create`/`gmt_modified`/`env`/`tenant`/`is_deleted` (+ `id`) |

Indexes: `uk_agent (env, tenant, run_id, agent_id)`, `uk_agent_idem (env, tenant, run_id, idempotency_key)`, `idx_run_status (env, tenant, run_id, status)`, `idx_parent (env, tenant, run_id, parent_agent_id)`.

**`ac_agent_compute_message`**

| column | type | null | notes |
|---|---|---|---|
| `id` | BigInteger AUTO_INCREMENT | no | PK |
| `message_id` | varchar(128) utf8mb4_bin | no | |
| `run_id` | varchar(128) | no | |
| `agent_id` | varchar(128) | no | |
| `seq` | int | no | per-agent monotonic |
| `role` | varchar(16) | no | `MessageRole` |
| `content` | text (JSON) | yes | |
| `decision` | text (JSON) | yes | structured decision/tool payload |
| `token_usage` | text (JSON) | yes | |
| `artifact_key` | varchar(512) | yes | externalized content |
| _+6 mandatory_ | | | `gmt_create`/`gmt_modified`/`env`/`tenant`/`is_deleted` (+ `id`) |

Indexes: `uk_message (env, tenant, agent_id, seq)`, `idx_run_agent (env, tenant, run_id, agent_id)`.

**`ac_agent_compute_result_shard`**

| column | type | null | notes |
|---|---|---|---|
| `id` | BigInteger AUTO_INCREMENT | no | PK |
| `shard_id` | varchar(128) utf8mb4_bin | no | |
| `run_id` | varchar(128) | no | |
| `source_agent_id` | varchar(128) | no | |
| `payload` | text (JSON) | yes | structured result |
| `artifact_key` | varchar(512) | yes | large result |
| `routed_at` | bigint | yes | epoch ms |
| _+6 mandatory_ | | | `gmt_create`/`gmt_modified`/`env`/`tenant`/`is_deleted` (+ `id`) |

Indexes: `uk_shard (env, tenant, shard_id)`, `idx_run (env, tenant, run_id)`.

**`ac_agent_compute_round`**

| column | type | null | notes |
|---|---|---|---|
| `id` | BigInteger AUTO_INCREMENT | no | PK |
| `round_id` | varchar(128) utf8mb4_bin | no | |
| `run_id` | varchar(128) | no | |
| `stage_index` | int | no | unique per run |
| `orchestrator_steps` | int | no | |
| `max_subagent_steps` | int | no | |
| `cohort_agent_ids` | text (JSON) | yes | |
| `started_at` / `ended_at` | bigint | yes | |
| _+6 mandatory_ | | | `gmt_create`/`gmt_modified`/`env`/`tenant`/`is_deleted` (+ `id`) |

Indexes: `uk_round (env, tenant, run_id, stage_index)`.

**`ac_agent_compute_guard_event`** (new-only; see D15)

| column | type | null | notes |
|---|---|---|---|
| `id` | BigInteger AUTO_INCREMENT | no | PK |
| `event_id` | varchar(128) utf8mb4_bin | no | |
| `run_id` | varchar(128) | no | |
| `agent_id` | varchar(128) | yes | |
| `kind` | varchar(32) | no | `GuardKind` |
| `details` | text (JSON) | yes | |
| _+6 mandatory_ | | | `gmt_create`/`gmt_modified`/`env`/`tenant`/`is_deleted` (+ `id`) |

Indexes: `uk_event (env, tenant, event_id)`, `idx_run_kind (env, tenant, run_id, kind)`.

**`ac_agent_compute_job`** — agentcompute's own durable queue (see §9)

| column | type | null | notes |
|---|---|---|---|
| `id` | BigInteger AUTO_INCREMENT | no | PK |
| `job_id` | varchar(128) utf8mb4_bin | no | |
| `run_id` | varchar(128) utf8mb4_bin | no | unique `uk_job_run` |
| `status` | varchar(20) | no | PENDING/RUNNING/SUCCEEDED/FAILED/TIMED_OUT |
| `run_at` | DateTime | no | next-eligible time (DB clock) |
| `claimed_by` | varchar(128) | yes | single owner |
| `claim_epoch` | BigInteger | no | default 0; monotonic, incremented per claim (fencing, D28) |
| `lease_expires_at` | DateTime | yes | claim lease (DB clock) |
| `attempts` | Integer | no | default 0; diagnostic only (deadline is the give-up rule) |
| `deadline_at` | DateTime | no | run-level give-up time |
| `last_error` | text | yes | |
| `cancel_requested` | Boolean | no | default 0; durable cancel flag polled by the resident loop |
| `app` | varchar(32) | no | app scoping |
| _+6 mandatory_ | | | `gmt_create`/`gmt_modified`/`env`/`tenant`/`is_deleted` (+ `id`) |

Indexes: `uk_job_run (env, tenant, run_id)`, `idx_scope_status_run_at (env, tenant, status, run_at)`, `idx_scope_lease (env, tenant, lease_expires_at)`.

### 8.2 ORM conventions

**Storage follows the project's `DatabasePlugin`.** Every repository is written against the injected `DatabasePlugin` (`orm_session` / `transactional_orm_session`) and runs **unchanged** on both profiles — SQLite (`SqliteDB`, `DEPLOY_PROFILE=test`) and community MySQL/OceanBase. agentcompute opens no raw DB connection and carries no sqlite-specific schema bootstrap. Tests exercise the repositories against a real `SqliteDB` instance (not a mock) plus a MySQL/OceanBase dialect smoke.

- `Base` from `core/base.py`; `AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")`.
- `_binary_string(n)` (`utf8mb4_bin` on MySQL) for all identifier columns in unique indexes (so OceanBase PAD-SPACE/case-fold cannot merge distinct ids).
- **Mandatory columns on every table:** `gmt_create`/`gmt_modified` = `Column(DateTime, default=func.now(), onupdate=func.now())`; `env` = `Column(String(20), nullable=False, default=get_current_env)`; `tenant` = `Column(String(64), nullable=False)`; `is_deleted` = `Column(Boolean, nullable=False, default=False, server_default="0")` (DDL `tinyint(1) NOT NULL DEFAULT 0`). Every unique index is scoped `(env, tenant, …)`.
- **Logical delete:** repositories set `is_deleted` instead of physically deleting; all default reads filter `is_deleted = 0`.
- **No `await` inside a session** (§8.5); blocking ORM offloaded via `asyncio.to_thread`.
- OceanBase-only modifiers (`BLOCK_SIZE`/`LOCAL`/`GLOBAL`) live only in `sql/2026_10_08_agent_compute.sql`, never in the ORM.
- Register all models in `core/schema.py::import_all_models()` so SQLite `create_all` builds them.

### 8.3 POC field mapping (D14)

**Run level** (`requests` + `runs` → `ac_agent_compute_run`):

| POC field | Target |
|---|---|
| `requests.id` | `run.run_id` (server-generated on submit) |
| `requests.goal` / `runs.goal` | `run.goal` (single column) |
| `requests.agents` / `runs.agents` | `run.agent_catalog` |
| `requests.provider` | `run.provider` |
| `requests.payload` | `run.request_metadata` (secrets stripped) |
| `requests.created_at` / `runs.created_at` | `run.gmt_create` |
| `runs.status` | `run.status` (remapped vocabulary) |
| `runs.error` | `run.error` |
| `runs.finished_at` | `run.ended_at` |
| `plans.plan_json` | redistributed to `round`/`result_shard`/`run.final_answer`; raw DAG JSON dropped with the DAG model |

Run status remap: POC `planning/running/completed/failed/failed_planning` → `RUNNING` / terminal `COMPLETED`/`FAILED`/`CANCELLED`/`TIMED_OUT`/`GUARD_KILLED`; `failed_planning` → `FAILED` with `termination_reason=error`.

**Agent / lineage** (`node_executions.agent` + `AgentSpec` → `ac_agent_compute_agent`):

| POC field | Target |
|---|---|
| `AgentSpec.name` | `agent.name` |
| `AgentSpec.role` / `instructions` | `agent.task_spec` (envelope) |
| `AgentSpec.metadata.type` | `agent.kind` |
| `node_executions.agent` + `DAGNode.id` | `agent.name` + lineage (`agent_id`, `parent_agent_id`, `depth`) |
| instance id (`{name}-{uuid}`) | `agent.agent_id` |
| `DAGNode.wave` | `agent.spawn_round` |
| Avernet `_bot_id` + states | `agent.bot_id` + `agent.bot_state` (new) |
| `AgentContext.upstream` | lineage via `parent_agent_id` + result shards |

**Messages** (`ac_agent_compute_message`): new concept (POC had none). Sources: `node.input.goal`, `DAGPlan.prompt`, LLM request/response bodies.

**Result shards** (`node_executions.result` / `RunLog.results` / `final_output`): `final_output` → `run.final_answer` + terminal agents' result shards.

**Rounds** (`DAGNode.wave` + `DAGNode.plan_round` + `extension_history`): one row per orchestration round; the two POC counters collapse into `agent.spawn_round` + the round's step counts.

**Guard events** (`RunEvent`/`HaltReason`): the POC emitted these to callbacks only and **never persisted them** — this table is new-only data (D15).

**Explicit drops (with reason):**

| POC item | Reason |
|---|---|
| `DAGPlan.edges` (dependency pairs) | Swarm is edge-less by design; structure is lineage + messages |
| `DAGNode.wave` / topological order | Replaced by `spawn_round` + round timeline |
| `NodeStatus.SKIPPED` | No swarm equivalent; terminal states are explicit |
| `NodeInput.params` | Never written by the POC planner (dead field) |
| `app.workers` config | Declared but unused in the POC |
| `NodeStatus` enum (PENDING/RUNNING/SUCCEEDED/FAILED/SKIPPED) | Replaced by swarm `AgentStatus` |
| `HaltReason` (`done`/`abort`/`drift`) | Replaced by `termination_reason` |
| Constructor-only thresholds (`max_extensions`, `replan_max_calls`, `max_parse_retries`, …) | Not POC-persisted; become swarm config + persisted guard events |
| POC `driver.max_retries` / `max_workers` / replanner retry knobs | Not POC-persisted; map to swarm config (`subagent_max_attempts`, `max_width`, `llm_max_retries`) — see §6.4 |

### 8.4 Message retention (D13)

- During an active run: full transcript per agent.
- On run terminal: keep the last N messages per agent (configurable, default 50) plus any message referenced by a result shard; replace large content with artifact keys; remove the rest. Applied by the message repository; N from agentcompute's own config.

### 8.5 Session concurrency invariant (no `await` inside a session)

The `DatabasePlugin` has **no cross-profile concurrency guarantee**, so agentcompute publishes a hard invariant:

- Every write is a **short, synchronous `orm_session` block with no `await` inside**. A session lifetime never spans an event-loop suspension.
- `SqliteDB` wraps the whole session in a process-wide **blocking `threading.RLock`** (single StaticPool connection) — an `await` inside the block would stall the entire event loop. `CommunityDatabase` has **no lock** and a real pool, so serialization is *not* a correctness guarantee there (backend contention instead). The invariant makes both profiles safe.
- Blocking ORM work driven from the async side is offloaded (e.g. `asyncio.to_thread`); the session block itself stays synchronous.
- One row per agent per event; `transactional_orm_session` only for genuine multi-statement units (still no `await` inside).
- **Enforced by a test** asserting no `await` appears inside an `orm_session`/`transactional_orm_session` block in agentcompute repositories.

---

## 9. Durable execution (D10, D16)

### 9.1 Queue modeling — resumable slices across a fleet (D34)

A run is executed as a sequence of **bounded slices**, each owned by one worker for the slice's duration only. This is the production execution model (supersedes the earlier "one long claim" D28); it removes the per-run worker-slot ceiling and makes rolling deploys drainable.

- **One durable job row per run** in `ac_agent_compute_job`; the job is **rescheduled between slices** (`run_at = now + backoff`), NOT held for the whole run.
- **Slice** = a bounded unit of work on the resident loop (one orchestration round, or `slice_max_seconds`, whichever first). At the end of a slice the handler **persists continuation state** (round index, in-flight agent continuations, orchestrator context refs) and returns, releasing the claim.
- **Any worker** may claim the next slice of the run (horizontal scale, cross-pod). The claim is per-slice; a run is not pinned to a pod.
- **Fencing (per slice).** The job carries a monotonic `claim_epoch` incremented on **every** claim. **State-mutating writes** (run row, result shard commit, round row, agent status, finalize) are fenced via a job-row subquery: the repository's `_fenced_write` runs in one `orm_session` and predicates on `(SELECT claim_epoch FROM ac_agent_compute_job WHERE run_id=?) = :mine` — a stale epoch (0 rows affected) means the slice was reclaimed and the write is discarded. **Append-only audit rows** (messages, guard events — keyed by fresh id, can't clobber) are **not fenced**: they're safe even from a stale worker and harmless on rehydrate. The heartbeat `renew` is also epoch-guarded (job row).
- **Stale-pod cooperative cancel (B3).** `renew` returning 0 rows sets an `asyncio.Event(slice_lost)`; the resident loop checks it at every decision boundary + the subagent runtime checks between attempts; the slice tears down via `CancelledError`, cancels siblings, and returns without persisting.
- **Durable cancel.** Cancel writes a durable `cancel_requested` flag; the running slice observes it on a bounded poll (every `lease_seconds/3`); if no slice is running, the next claim honors it.
- **Reclaim circuit breaker.** A run reclaimed `reclaim_max` times with no progress (`round_index` unchanged) is failed with `STUCK` (terminal, `termination_reason=stuck`) — preventing a poison-pill reclaim loop. `attempts` remains diagnostic; the deadline remains the normal give-up rule.
- Enqueue: `deadline_at = now + <run budget>` (DB clock), unique `run_id`, optional client idempotency key (D29).
- The worker is agentcompute's own `LifecycleBase` participant; it starts in `startup()`, stops claiming on `shutdown()`, and **drains in-flight slices** within the grace window (D34/§9.3). In-module wakeup cuts idle poll latency.

**Slice continuation state** (persisted between slices; the recovery snapshot §9.2): `round_index`; for each in-flight agent its `agent_id`, `attempts`, `idempotency_key`; the orchestrator context = the set of committed `result_shard`s (not the raw transcript); and the current persona (handoff, D30). A **parked continuation** is an agent row left `RUNNING` with no committed shard — it is re-spawned next slice under its `idempotency_key`; there is no intra-subagent checkpoint.

### 9.2 Recovery (D16, D34)

Recovery snapshot (persisted at every slice boundary + on every committed write) = round timeline (max `stage_index`) + agent lineage with per-agent terminal status + latest result shard per terminal agent + slice continuation state (§9.1).

On restart / reclaimed expired lease, the next slice (by **any** worker):
1. Load run + agents + rounds + shards + continuation.
2. If terminal → no-op.
3. Rebuild orchestrator context from completed result shards.
4. **Reconcile each agent against its committed result (H4):**
   - `COMPLETED` with a committed shard → **leave as-is** (never re-run).
   - `FAILED` → leave failed (the orchestrator may re-plan it).
   - `RUNNING`/`QUEUED` (no committed shard) → **re-spawn under the agent's existing `idempotency_key`**: a side-effectful executor (Avernet bot create) is idempotent or check-then-create, so the effect is not duplicated; an LLM-only executor is naturally idempotent.
   - **Finished-but-not-committed** (the LLM/side effect succeeded but the crash happened before the shard was committed): no committed shard ⇒ treated as `RUNNING` and re-spawned under the same key; the single committed shard on retry is the source of truth.
5. Continue with the next slice.
6. **Reclaim circuit breaker (Oracle gap 2):** if `reclaim_count` for the run (with unchanged `round_index`) reaches `reclaim_max`, transition the run to `STUCK` (terminal, `termination_reason=stuck`) instead of looping. This prevents a poison-pill run from burning the fleet. A transient DB blip at the continuation-write boundary returns `WorkerOutcome.retry` (§5.3), so a single DB failure does NOT trip the breaker — only genuinely no-progress reclaims do.

### 9.3 Drain & rolling deploy (production)

- **Graceful drain.** On `shutdown()`, the worker **stops claiming new slices** and lets the in-flight slice finish within the shutdown grace window. Because slices are bounded (`slice_max_seconds`), a slice fits the grace window by construction — unlike a whole-run claim. If the grace window is exceeded, the worker persists the slice continuation and releases the claim explicitly rather than relying on lease expiry (cleaner recovery, no double-recovery window).
- **Rainbow / surge-with-lead deploy.** New pods take new slices; old pods drain their current slice (≤ `slice_max_seconds`) then terminate. Deploy lead time MUST be ≥ `slice_max_seconds` + lease interval so no slice is force-killed mid-write.
- **Cross-pod resume.** A run's next slice may be claimed by any pod (horizontal scale). `claim_epoch` fences a pod whose slice was reclaimed; a pod that missed SIGTERM and keeps writing is fenced out (its writes no-op).
- **In-flight run impact SLO:** with the slice model, a rolling deploy costs at most a re-execution of one slice's non-terminal subagents (idempotency-keyed), not a whole run.

### 9.4 Fleet backpressure & admission control (production)

- `POST /jobs` / `POST /run` SHALL be **admission-controlled**: reject (429) when global or per-tenant concurrency is saturated, or when the run queue depth exceeds a threshold. Accepted runs are never silently dropped.
- The fleet exposes a **queue-depth + concurrency signal** (metrics, §16) for autoscaling and alerting.
- Per-tenant submit **rate limit** (QPS) in addition to per-tenant concurrency/spend caps (D25/D32).

---

## 10. LLM integration

### 10.1 SPI (fully typed)

No bare `str` parameters, no string-defaulted enums. The SPI takes a typed request object and returns a typed response object.

```python
@dataclass(frozen=True)
class CompletionRequest:
    kind: LlmCallKind                          # ORCHESTRATOR | SUBAGENT (drives prompt + default model)
    model: ModelId | None                      # None → config default for the kind
    system_prompt: str = ""                    # free-form text
    user_prompt: str = ""                      # free-form text
    max_tokens: int | None = None
    temperature: float = 0.0

@dataclass(frozen=True)
class CompletionResponse:
    text: str                                  # raw model text (parsed by the decision/result parser)
    model: ModelId
    token_usage: TokenUsage
    finish_reason: FinishReason                # typed enum

class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"

class LLMProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
```

The caller (`orchestration/decision.py`, the LLM subagent) converts `CompletionResponse.text` into a typed `Decision` / `StructuredOutput`; the client itself never parses domain shapes.

### 10.2 agentcompute's own client

- agentcompute implements its **own** async OpenAI-compatible chat-completions client behind the SPI (retry + backoff + timeouts). It does **not** use `core/harness/services/llm.py`.
- Config: agentcompute's own frozen dataclass `AgentComputeLlmConfig`: `base_url: str`, `token_source: SecretRef  # LLM provider only; Avernet uses backend_base_url (D41)`, `default_model: ModelId`, `orchestrator_model: ModelId | None`, `timeout_seconds: float`, `max_tokens: int`, `max_retries: int`, injected by agentcompute's DI module.
- The decision parser lives in `orchestration/decision.py`, **not** inside the client.
- A `StubLLMProvider` returns scripted `CompletionResponse`s for deterministic tests.

---

## 11. HTTP API

Prefix: `/agentcompute/v1` (internal, gateway-fronted via §17.6).

| Method | Path | Behavior |
|---|---|---|
| POST | `/agentcompute/v1/run` | sync: submit (requires `tenant`), await terminal, return summary |
| POST | `/agentcompute/v1/jobs` | async: submit (requires `tenant`), return `{run_id}`, run durably |
| GET | `/agentcompute/v1/jobs/{run_id}` | status + lineage + rounds + final answer (tenant-scoped) |
| GET | `/agentcompute/v1/jobs/{run_id}/stream` | SSE: agent spawn/state, round, terminal |
| GET | `/agentcompute/v1/jobs/{run_id}/report` | HTML: lineage tree + per-agent detail |
| POST | `/agentcompute/v1/jobs/{run_id}/cancel` | signal cancel; cascade to running agents |
| GET | `/agentcompute/v1/jobs` | list runs (tenant + status filter, paginated) |

**Transport DTOs (Pydantic, `adapters/http/agentcompute/schemas.py`).** Every request/response field is typed — enums for closed sets, typed wrappers for identifiers; no bare `str` for a value with a closed set.

```python
class AgentSpecDTO(BaseModel):
    name: str
    role: str
    instructions: str = ""
    kind: AgentKind = AgentKind.LLM
    model: ModelId | None = None               # per-agent model override
    metadata: dict[str, str] = {}

class SubmitRunDTO(BaseModel):
    tenant: TenantId                           # REQUIRED
    goal: str
    agents: list[AgentSpecDTO] = Field(min_length=1)
    provider: ProviderId | None = None
    model: ModelId | None = None
    limits: RunLimitsDTO | None = None         # typed overrides (width/depth/budgets/timeouts)
    idempotency_key: IdempotencyKey | None = None   # D29: duplicate submit joins the live run

class RunSummaryDTO(BaseModel):
    run_id: RunId
    status: RunStatus                          # enum
    termination_reason: TerminationReason | None
    final_answer: FinalAnswerDTO | None
    lineage: list[AgentDTO]
    rounds: list[RoundDTO]
    error: str | None

class SubmitRunResponseDTO(BaseModel):
    run_id: RunId

class StreamEventDTO(BaseModel):               # SSE payload, discriminated by `event`
    event: StreamEventKind                     # enum: agent_spawn | agent_state | round | terminal
    run_id: RunId
    data: AgentDTO | RoundDTO | RunSummaryDTO

class CancelResponseDTO(BaseModel):
    run_id: RunId
    status: RunStatus
```

**Service protocol (`api/agentcompute/`, transport-agnostic — typed, no raw strings):**

```python
@runtime_checkable
class AgentComputeServiceProtocol(Protocol):
    async def submit_sync(self, req: SubmitRunRequest) -> RunSummary: ...
    async def submit_async(self, req: SubmitRunRequest) -> RunId: ...
    def get_run(self, run_id: RunId, tenant: TenantId) -> RunSummary | None: ...
    def get_report(self, run_id: RunId, tenant: TenantId) -> ReportDocument: ...
    async def cancel(self, run_id: RunId, tenant: TenantId) -> RunStatus: ...
    def stream(self, run_id: RunId, tenant: TenantId) -> AsyncIterator[StreamEvent]: ...
    def list_runs(self, tenant: TenantId, status: RunStatus | None = None,
                  *, page: int = 1, page_size: int = 20) -> RunPage: ...
```

```python
@dataclass(frozen=True)
class RunPage:
    items: tuple[RunSummary, ...]
    total: int
    page: int
    page_size: int
```

`SubmitRunRequest` (domain) carries `tenant: TenantId`, typed `agent_catalog: tuple[AgentSpecRaw, ...]`, `provider: ProviderId | None`, `model: ModelId | None`, and `limits: RunLimits | None`. The HTTP DTOs convert to/from these.

- The adapter is thin: DTO ↔ domain translation + routing, delegating to the service protocol. No orchestration logic.
- **Tenant** (sync `POST /run` and async `POST /jobs`) is a **required typed field**; missing/blank → validation error, no run created. The service stamps `tenant` on the run row and propagates it to every child row, and scopes all reads by tenant.
- SSE uses FastAPI `StreamingResponse` with an async generator emitting typed `StreamEvent` payloads (stdlib/FastAPI pattern; no cross-feature dependency).

---

## 12. DI and configuration

- `di/modules/agentcompute_module.py` binds: `AgentComputeService`, orchestrator, its own worker, subagent runtime/factory, repositories, agentcompute's own `LLMProvider` client, and its own artifact store — using only the framework kernel seams.
- Add the module to `di/container.py::build_injector` (and/or `modules_for(profile)`).
- The worker is a `LifecycleBase` (kernel) participant, auto-discovered by `discover_lifecycle_participants`; it starts its loop in `startup()` and drains in `shutdown()`.
- Config: agentcompute's own dataclasses — LLM (`base_url`, `token_source: SecretRef  # LLM provider only; Avernet uses backend_base_url (D41)`, `default_model`, `orchestrator_model`, `timeout_seconds`, `max_tokens`, `max_retries`); orchestration limits (`max_width`, `max_depth`, `max_total_agents`, turn/wall-clock caps, `subagent_max_attempts`, `per_subagent_timeout`, `respawn_max`, `repair_max`, retention tail); tenant limits (`tenant_max_concurrent_runs`, `tenant_max_width`, ``); Avernet (backend_base_url + bot endpoint paths, no external gateway — D41); artifact-store root/threshold; enablement flag.
- **No raw env access** in `core/agentcompute`; the DI module resolves `SecretRef` and injects a resolved token (§4.4 M5).

---

## 13. Risks / trade-offs

| Risk | Mitigation |
|---|---|
| Orchestrator quality drives everything | Structured decision schema, few-shot prompt, dedup/depth/width guards, stub provider for tests; prompt in a versioned constant |
| Reimplementing durable execution (isolation) | agentcompute owns its queue table + worker; copy proven claim/heartbeat/reschedule/deadline semantics, keep the surface minimal, unit-test claim/recovery directly |
| Retry storms / runaway retries | Bounded per layer (LLM / subagent / job / re-spawn); single run deadline is the outer bound; backoff + jitter on the LLM layer; exhausted retries surface failure instead of looping |
| Runaway cost | Token/turn/width/depth + critical-path budgets, guards |
| Concurrent DB writes from many subagents | One row per agent per event, short-lived `orm_session`, no session across `await` |
| Lineage persistence volume | Artifact references for large outputs; message retention policy |
| Restart recovery completeness | Snapshot = rounds + lineage + per-agent shards; resume from snapshot (rehydrate, do not re-run committed agents); re-spawn only non-terminal agents under their idempotency key |
| Two queue/LLM impls in the repo | Explicit isolation constraint; keep agentcompute's implementations module-private and documented; do not promote them to shared infra |

---

## 14. Migration plan

1. **Scaffold + storage** — `core/agentcompute/` + `ac_agent_compute_*` ORM (+ `ac_agent_compute_job`) + `sql/*.sql`; register in `core/schema.py`; repositories over `DatabasePlugin`; SQLite tests.
2. **LLM client + orchestrator loop** — agentcompute's own `LLMProvider` SPI + async client (with bounded retry/backoff); structured-decision loop, termination conditions, guards; stub + real tests.
3. **Subagent runtime + artifact store** — ephemeral subagents (bounded queue + fixed worker pool, completion queue, timeouts/cancel, bounded fresh-context retry); LLM subagent; Avernet bot subagent; agentcompute's own artifact store.
4. **Durable execution** — agentcompute's own queue table + worker (claim/heartbeat/reschedule between slices/complete/fail, resumable slices across pods) as a `LifecycleBase` participant; async submit + resident slice loop; restart recovery.
5. **Service + HTTP + CLI** — `AgentComputeService` (sync/async/status/report/cancel); HTTP adapter; DI wiring; sync `/run` wrapper over the async engine.
6. **Retire the standalone package** once in-tree tests pass.

**Rollback:** additive until step 6. Before step 6, reverting removes only the new module/route. After step 6, revert the merge commit and drop `ac_agent_compute_*` (no other module's DDL changed).

---

## 15. Open items (non-blocking, decide during implementation)

- **Message summary strategy** for retention — naive truncation vs an LLM summary of dropped messages. Default: truncate; revisit if recovery fidelity needs it.
- **Critical-path budget defaults** — tune after first real runs.
- **Avernet bot reuse vs per-subagent** — this design uses per-subagent ephemeral bots (POC semantics); revisit if provisioning cost dominates.
- **Distributed subagents** — escalate to a broker only if subagents must outlive a worker or need untrusted isolation (explicit non-goal now).
- **Durable-execution engine** — this design hand-rolls a durable queue table + worker (isolation constraint). If the isolation constraint is ever relaxed, a durable-execution engine (Temporal/DBOS-style) could replace it.
- **Full OpenTelemetry export** — deferred; this change records per-agent cost + a decision trace, not OTel spans.

---

## 16. Production hardening (review D19–D27)

Added after an evidence-based review (MAST arXiv 2503.13657; Anthropic multi-agent; LangGraph durability; CPython asyncio cancellation).

### D19 — Session invariant: no `await` inside a session (§8.5)
Hard invariant across both `DatabasePlugin` profiles. Enforced by test.

### D20 — Constrained output + bounded repair loop
The orchestrator requests a constrained structured decision (provider strict schema / tool-use where available). On parse/validation failure, a **bounded repair loop (≤2 attempts)** feeds the validation error back; only after exhaustion is the decision treated as absent → terminate. **Terminating on the first bad decision is forbidden.** Rationale: prompt-only JSON fails 15–20%; JSON mode guarantees validity, not schema compliance.

### D21 — Hard caps independent of the model
agentcompute — not the LLM — owns the stop conditions: max turns, max total tokens, run wall-clock deadline, max total spawned agents, and **no-progress detection** (N consecutive identical spawn objectives/decisions). Rationale: MAST ranks step repetition (15.7%) and non-recognition of completion (12.4%) as the top system-design failures.

### D22 -- REMOVED: budget counters rescoped

Per-run token/cost budgets, the locked ledger, spent_tokens, and spent_turns are **removed**. token_budget/critical_path_budget termination reasons are removed. Per-agent token_usage is recorded (D26) but not enforced. Runaway cost is gated only by non-token caps and per-tenant LLM credentials/quotas at the gateway (D25).

### D23 — Bounded queue + worker pool (not a raw semaphore)
Subagent concurrency uses a **bounded queue + fixed worker pool** (queue `maxsize` = backpressure). Task handles are retained (no fire-and-forget); first fatal failure cancels siblings; `CancelledError` is re-raised after cleanup (never captured by a broad `except`). Rationale: with a bare `asyncio.Semaphore`, a task cancelled at an `await` may not reach its post-acquire `finally` in the expected order, so a token can leak and the effective width silently shrinks; a bounded queue + pool sidesteps the token-hand-out race entirely and gives clean backpressure and teardown. (The hazard is *swallowing* `CancelledError` in a broad `except`/bare `return`, converting cancellation to completion — not that `finally` never runs.)

### D24 — Crash-recovery idempotency
Each subagent attempt carries an **idempotency key**; side-effectful executors (Avernet bot create, external calls) are idempotent or check-then-create; **per-subagent result rows are committed as they complete** so resume never re-runs a completed subagent; interrupted agents re-spawn under the same key. Rationale: naive replay re-runs completed subagents and re-fires side effects.

### D25 — Tenant-scoped runtime isolation
Tenant identity comes from the **authenticated principal** (never the prompt/agent input). agentcompute enforces tenant-scoped runtime limits (max concurrent runs, max fan-out width, max tokens/spend per tenant) on top of the stored `tenant` column, and uses per-tenant LLM credentials/budget where the deployment provides them. One tenant's cap blocks only that tenant. Rationale: a stored column gives query isolation, not blast-radius isolation; a runaway swarm is a cross-tenant DoS.

### D26 — Cost observability and decision trace
Record **per-agent token/cost usage** aggregating to run level (child usage never excluded) and persist a **decision/spawn trace** (each decision, its spawns, the terminal reason). Full OTel deferred.

### D27 — Scope: migrate + evolve
Not a 1:1 port. Port the POC's LLM/Avernet executors + hexagonal split as reference; build swarm semantics fresh (POC had no tool-calling, no message store, no subagent isolation).

### D28 — Fencing, cancel, budget durability, cadence (under the slice model)

> **Superseded by D34** for the execution shape: D28's original "one long claim held for the run" is replaced by **resumable slices**. The four mechanisms below are retained but now operate **per slice / per run** as D34 requires.

- **Fencing (split-brain).** `ac_agent_compute_job` carries a monotonic `claim_epoch` incremented on **every** claim. The heartbeat renew AND **every write path** (run/agent/message/shard/round/guard/budget) carry `WHERE claimed_by=? AND claim_epoch=?`. A worker whose renew affects 0 rows has lost its slice and **stops and self-aborts** — so a stale (partitioned) worker cannot write after reclaim.
- **Cancel delivery.** Cancel writes a durable `cancel_requested` flag; the running slice polls it every `lease_seconds/3` (≤ one poll interval), and a run with no live slice honors it on the next claim (or via deadline/reclaim).
- **Budget enforcement removed (scope decision).** Per-run token/cost budgets and the locked ledger (former D22) are **removed**. Runaway cost is now gated only by non-token caps (`max_turns`, `max_total_agents`, `max_width`/`max_depth`, `wall_clock` deadline) and per-tenant LLM credentials/quotas at the gateway (D25). Per-agent `token_usage` is still recorded (D26 observability) for cost attribution but not enforced. This simplifies fencing (no budget-counter writes to fence) and removes the  columns.
- **Turn cadence (H1).** After a non-`wait` decision the loop awaits a completion or a bounded `orchestrator_turn_interval` — no unthrottled decision spin.

- **Avernet idempotency (D-c):** the ephemeral bot creation passes a **deterministic client-supplied identity** (derived from `run_id` + `agent_id`, via an idempotency header / manifest id), so a re-spawn or retry after a crash-window creates the bot at most once.

**Note:** the former trade-off ("a long run holds one worker slot") **no longer applies** — slices release the worker between boundaries (D34), removing the ceiling.

### D29 — Submit idempotency at the HTTP boundary
`SubmitRunDTO` carries an optional client `idempotency_key`; a unique `(env, tenant, active_idempotency_key)` index (active while non-terminal) makes a retried `POST /jobs`/`POST /run` **join the live run**. Without a key, each submit creates a new run. Rationale: `run_id` is server-generated, so the prior "duplicate join" behavior was unreachable from HTTP.

### D30 — Handoff is a persona change
A `handoff` decision resolves `target` through the run's agent catalog and applies that spec's role/instructions as the orchestrator persona for subsequent turns; recorded as a decision message; `round_state.active` is read by the prompt builder. Unresolvable target → invalid (repair).

### D31 — Result shard is exactly one of payload or artifact
A `result_shard` carries exactly one of `payload` or `artifact_key` — never both, never neither (repository guard + test). Rationale: both-nullable allowed an empty "source of truth" shard.

### D32 — Tenant limit accounting
Tenant concurrency = `COUNT(*) WHERE tenant=? AND status='RUNNING'` at submit/spawn; tenant spend = sum of persisted usage, recorded for attribution (not enforced). Gives D25 a concrete accounting source.

### D33 — Coordination time vs audit time
Job coordination columns (`run_at`/`lease_expires_at`/`deadline_at`) are `DateTime` **DB-clock** and never enter the domain; all domain/audit timestamps are `EpochMillis` (`bigint`). Conversion happens only at the serializer boundary.

### D34 — Resumable-slice execution, cross-pod (supersedes D28's one-long-claim)
A run executes as bounded slices; each slice is owned by one worker for the slice's duration then released. The job reschedules between slices; any pod resumes. Per-slice `claim_epoch` fences **state-mutating writes** via job-row subquery; append-only rows are unfenced. Stale-pod uses cooperative cancel. Slice continuation state is persisted at each boundary. This removes the per-run worker-slot ceiling, makes rolling deploys drainable, and enables fleet backpressure. (Detail: §9.1/§9.3/§9.4.)

### D35 — Feature-isolation, not ops-isolation
agentcompute is isolated from other feature *domains* (no `core/task`, `core/task_queue`, harness LLM, object_storage, system_config) but MAY use platform *operational* infra (metrics/tracing, migration tooling, pool config, logging, auth/principal seam). It builds domain machinery itself but not the ops layer the platform provides. (§1.2)

### D36 — Horizontal multi-pod scale target
Multiple pods share runs; any pod may run a run's next slice. Single-region horizontal fleet is the target; multi-region active-active is a non-goal. Requires D34 + per-slice fencing.

### D37 — Full multi-tenant isolation layer
Authz (submit/cancel/read from the authenticated principal), per-tenant quotas + submit rate limits, append-only audit log, hard-erasure path, PII classification, per-tenant LLM credentials. Deep cross-tenant model/KV isolation is a documented non-goal for v1. (§17.5)

### D38 — Schema migration & data lifecycle
`ac_agent_compute_*` changes go through versioned forward migrations (never drop-table rollback once data exists). Artifacts carry TTL; soft-delete is complemented by a hard-erasure path. (§17.2)

### D39 — Observability, SLOs, degradation
Emit metrics (queue depth, claim/reclaim, slice/run latency p50/p99, LLM error rate, tokens/$ per tenant, admission-reject, STUCK) + tracing (run→slice→decision→subagent) + structured logs. Define SLOs + alerts. Upstream (LLM/gateway) circuit breaker + admission shedding; connection-pool sizing + exhaustion behaviour. (§17.3/§17.4)

### D40 — Reclaim circuit breaker & full fencing
State-mutating writes are epoch-guarded (§9.1). A run reclaimed `reclaim_max` times with no progress is marked `STUCK` (terminal) to prevent a poison-pill loop. (§9.2/§17.1)

### D41 — Avernet subagent calls the backend's own API (no gateway)

The Avernet subagent no longer calls an external Avernet gateway. It calls the **backend's own bot-management API** (e.g., `/openapi/v1/bots/...`) via localhost HTTP — the backend already has bot creation/streaming/deletion endpoints, and the POC's gateway indirection is no longer needed now that the code lives in the backend. Config: `avernet.backend_base_url` (defaults to `localhost:<port>`) replaces `gateway_base_url`/`principal_token`. The agentcompute API (`/agentcompute/v1/...`) is the surface **exposed to the gateway** — the gateway routes external requests to this internal surface.

### D42 — Result-schema validation before routing

Before a subagent result is routed back as a shard, the system validates it against the TaskEnvelope's `output_schema` (if provided). A result that fails validation is treated as a subagent failure (retry), not routed to the orchestrator. Prevents malformed outputs from corrupting the orchestrator context.

### Round-2 residual risks (accepted)
- **Hostile partition still possible without a second fencing channel** — the `claim_epoch` guard closes the common case (reclaim + stale writes); a fully Byzantine worker is out of scope.
- **Long-held claim consumes a worker slot** for the run's duration (D28 trade-off, above).

### Post-review residual risks (accepted)

- **Hand-rolled durability** (D3/D10/D24) is the biggest correctness surface; mitigated by copying proven claim/heartbeat semantics + idempotency keys, and flagged as an open item if isolation is relaxed.
- **Summarization loss** in retention (D13) follows the documented "compaction cliff"; the design keeps result shards verbatim and only compacts messages — a verbatim-constraints retention rule can be added if needed.
- **Cross-tenant prompt/KV-cache leakage** (OWASP LLM06) is partially mitigated by per-tenant credentials (D25); a shared-model deployment would additionally need namespace isolation. Flagged, not solved in this change.

---

## 17. Production operations (goal = production-ready)

This section is additive to reach a **production bar**, with **horizontal multi-pod scale** as the target. Each item below is a launch gate unless marked otherwise.

### 17.1 Execution & orchestration operations
- **Resumable slices, cross-pod** (D34, §9.1/§9.3): any pod runs a run's next slice; per-slice fencing; drainable.
- **Fleet backpressure / admission control** (§9.4): 429 when global or per-tenant saturation; accept-or-reject, never silent drop; queue-depth signal for autoscaling.
- **Rolling deploy / rainbow** (§9.3): deploy lead ≥ `slice_max_seconds` + lease; explicit checkpoint-on-drain; in-flight impact ≤ one slice.
- **Reclaim circuit breaker** (§9.2): `STUCK` after `reclaim_max` no-progress reclaims (poison-pill guard).

### 17.2 Schema & data lifecycle
- **Migration framework**: `ac_agent_compute_*` schema changes go through the platform's migration tooling (versioned, forward-only); **never** "drop tables" as the rollback once data exists. Rollback = forward-fix or additive-migration + backfill.
- **Artifact TTL / retention**: artifacts in the store carry a TTL; a sweep reclaims expired artifacts. Messages follow D13 retention.
- **Erasure path**: soft-delete (`is_deleted`) is not erasure — a hard-delete/anonymize path per tenant/run is required for privacy compliance.
- **PII classification**: run inputs/outputs may contain PII; classify and avoid persisting sensitive content beyond retention (secrets already stripped from payloads).


**Migration-order gate:** `create_all(checkfirst=True)` adds missing *tables*, not missing *columns*. New columns (`claim_epoch`, `cancel_requested`, `active_idempotency_key`) MUST be applied via `ALTER TABLE ADD COLUMN` on **all profiles** (including community SQLite files) before the release's code boots. The migration ships and is applied in a strictly *earlier* release than the code that reads those columns — this is a correctness requirement, matching the proven `task_queue` pattern. The §14 rollback must also be updated: after data exists, rollback is a forward-fix, not a drop-table.

### 17.3 Observability & SLOs
- **Metrics** (via the platform metrics seam): queue depth, claim/reclaim count, slice latency p50/p99, run latency p50/p99, LLM error/timeout rate, tokens/`$` per tenant, admission-reject rate, STUCK rate.
- **Tracing**: span a run → slice → orchestrator decision → subagent (the decision/spawn trace, D26) so a stuck run is debuggable end-to-end.
- **SLOs** (initial targets, tune at launch): run scheduling latency p99 < 5s; slice success rate > 99%; reclaim rate < 1%; API availability > 99.9%. Alerts on SLO burn + queue-depth saturation.
- **Structured logging** contract: run_id, slice epoch, tenant, node, phase on every log line.

### 17.4 Failure isolation & degradation
- **Bulkhead**: a pathological run cannot starve the fleet — per-run slice budget, per-tenant caps, and the reclaim breaker.
- **Upstream circuit breaker**: when the LLM/gateway is unhealthy, fail-fast/shed admission instead of stalling every run to its deadline; surface a degraded status.
- **Connection-pool sizing**: `CommunityDatabase` has no lock and a real pool — size the pool ≥ max concurrent subagent writes across runs; define pool-exhaustion behaviour (bounded wait + error, not unbounded).
- **Fencing coverage**: **every** write path (run/agent/message/shard/round/guard/budget) is epoch-guarded, not just run/shard/finalize (Oracle gap 1).

### 17.5 Security & multi-tenant isolation
- **Authz model**: who may submit / cancel / read a run — derived from the authenticated principal (D25); cross-tenant read is denied by default and enforced at the repository (tenant predicate injected by the storage client, not agent-supplied).
- **Per-tenant quotas & rate limits**: per-tenant concurrency, fan-out width, tokens/spend (D25/D32) **plus** submit QPS (D32/§9.4); a tenant's cap affects only that tenant.
- **Audit log**: append-only record of submit/cancel/terminal transitions with principal + tenant.
- **Egress allowlist**: subagent/LLM/Avernet egress restricted to known endpoints.
- **Prompt-injection / tool-abuse controls**: subagent tools gated; tool calls referencing out-of-scope resources rejected before execution.
- **Per-tenant LLM credentials/budget** at the gateway (D25). Deep cross-tenant model/KV isolation is a documented non-goal for v1 (per-tenant credentials mitigate).

### 17.6 API
- **Versioned surface**: `/agentcompute/v1/...` with a compatibility/deprecation policy (the current `/agentcompute` prefix is the v1 surface).

### 17.6a Testing & coverage gate

- **Full unit + integration tests** for the module under `tests/community/core/agentcompute/`: unit tests with stub `LLMProvider`/`ArtifactStore` (no I/O); integration tests against a **real `SqliteDB` `DatabasePlugin`** and real repositories; one single-process end-to-end run test (submit → spawn → results route → finish, asserting persisted lineage/shards/rounds/terminal state).
- **Storage tests use the project `DatabasePlugin`** (real `SqliteDB`, `DEPLOY_PROFILE=test`) plus a MySQL/OceanBase dialect smoke — no mocked repositories.
- **Coverage gate: ≥90% line coverage on `core/agentcompute/`**, enforced in CI with the repo tooling (`pytest-cov` → XML → `report_check.py --min-line-coverage 90` scoped to the module), stricter than the repo's 75% default and the 80% changed-line default.

### 17.7 Launch gates (must-have before production)
1. Resumable-slice cross-pod execution (D34) — **the single highest-leverage change**.
2. Admission control + fleet backpressure (§9.4).
3. Graceful drain + rainbow deploy runbook (§9.3).
4. Schema migration framework (17.2).
5. Metrics + tracing + SLOs + alerts (17.3).
6. Reclaim circuit breaker + fencing on every write (17.1/17.4).
7. Upstream circuit breaker + degradation policy (17.4).
8. Connection-pool sizing + exhaustion handling (17.4).
9. Authz + audit + per-tenant quotas/rate limits (17.5).
10. **Full unit + integration tests with ≥90% line coverage on `core/agentcompute/`** (17.6a), including storage tests on a real `SqliteDB` `DatabasePlugin`.

**Should-have (fast-follow):** artifact TTL, erasure path, PII classification, egress allowlist, injection controls, index/perf review.
**Can-follow:** full cost→billing attribution, advanced fairness/preemption, multi-region.

---

## Appendix A — Framework kernel (the ONLY allowed shared surface)

| Kernel item | Path | Reuse for |
|---|---|---|
| `Base` | `core/base.py` | ORM declarative base |
| DI container | `di/container.py::build_injector` | module registration |
| `DatabasePlugin` seam | `plugin_api/database.py` | `orm_session` / `transactional_orm_session` persistence |
| Model registration | `core/schema.py::import_all_models()` | `create_all` table bootstrap |
| `LifecycleBase` | `kernel/lifecycle.py` | agentcompute worker boot/shutdown |

**Blocked feature modules (MUST NOT import):** `core/task`, `core/task_queue`, `core/harness/services/llm.py`, `plugin_api/object_storage.py`, `core/system_config`, and every other feature's `core/*` / `di/modules/*`.

## Appendix B — agentcompute-owned components (built in this change)

| Component | Location | Replaces |
|---|---|---|
| Durable job table + worker | `core/agentcompute/repository`, `runtime/worker.py` | `core/task_queue` |
| LLM client | `core/agentcompute/plugins/llm/` | `core/harness/services/llm.py` |
| Artifact store | `core/agentcompute/spi/artifact_store.py` + filesystem impl | `plugin_api/object_storage.py` |
| Config dataclasses | `core/agentcompute/config.py` + DI module | `core/system_config` / `di/config.py` |

## Appendix C — Reference: research basis

- Kimi K2.5/K2.6 Agent Swarm — emergent decomposition, context sharding, PARL reward, critical steps.
- Anthropic multi-agent research system — orchestrator-workers, artifact bypass, duplicate-work pitfall.
- OpenAI Swarm / Agents SDK — handoff loop, statelessness.
- AutoGen Swarm team + event-driven handoff — routing by handoff message, composable termination conditions.
- LangGraph swarm — handoff-as-tool, persistent active agent.