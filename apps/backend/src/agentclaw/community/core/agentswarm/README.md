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
`tests/community/architecture/agentswarm/test_isolation.py`, which also
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
| D40 reclaim breaker | `job.reclaims` counts no-progress reclaims; `reclaims > reclaim_max` → terminal `STUCK`; routed-shard growth resets the count (`progress_marker`, progress-aware) |
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
| `ac_agent_swarm_agent_attempt` | `agent_id`, `run_id`, `claim_epoch`, `attempt`, `executor_kind`, `executor_id`, `executor_name`, `executor_state`, `started_at`, `ended_at` — per-attempt executor ledger (D-K): one row per attempt per slice, journaled at spawn through the D28 fence; terminal destroy consumes it, with the pre-change deterministic derivation retained as the legacy belt |
| `ac_agent_swarm_message` | `message_id`, `run_id`, `agent_id`, `seq`, `role`, `content`, `decision`, `token_usage`, `artifact_key` |
| `ac_agent_swarm_result_shard` | `shard_id`, `run_id`, `source_agent_id`, `payload`, `artifact_key`, `routed_at` |
| `ac_agent_swarm_round` | `round_id`, `run_id`, `stage_index`, `orchestrator_steps`, `max_subagent_steps`, `cohort_agent_ids`, `started_at`, `ended_at` |
| `ac_agent_swarm_guard_event` | `event_id`, `run_id`, `agent_id`, `kind`, `details` |
| `ac_agent_swarm_job` | `job_id`, `run_id`, `status`, `run_at`, `claimed_by`, `claim_epoch`, `lease_expires_at`, `attempts`, `reclaims`, `progress_marker`, `deadline_at`, `last_error`, `cancel_requested` |

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
| Avernet `_bot_id` + lifecycle states | `agent.bot_id` + `agent.bot_state` (POC never persisted bot state) | Mapped — `bot_state` now includes `AWAITING_AUTHORIZATION`, the human passport-authorization park: a consent-required create reports the gate instead of generic `PROVISIONING`, and rides an `authorization` descriptor on the run surfaces (see Bot-create human gate below) |
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
| `maintenance.enabled` | true | reaper loop switch (single-knob disable) |
| `maintenance.reaper_interval_seconds` | 300 | reaper sweep cadence, jittered |
| `executor.max_platform_workers` | 4 | platform-bridge thread pool size per pod |
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
- **Orphan reaper** — the worker hosts a jittered maintenance loop (cadence
  `maintenance.reaper_interval_seconds`, default 300s) that pages RUNNING
  runs and probes job claimability. Re-enqueue first: a queue orphan (no job
  row, or a terminal job under a RUNNING run) gets a fresh PENDING job — the
  slice rehydrates and converges normally; a fenced TIMED_OUT is reserved
  for runs past their wall-clock deadline. Every sweep action emits
  `guard_event(kind=stuck, details="orphan")`. Multi-pod sweeps converge:
  re-enqueue is guarded by `uk_job_run`, terminal writes are
  `claim_epoch`-fenced (D28). Disable with `maintenance.enabled=false`.
- **Deadline enforcement** — `deadline_at` joins the `claim_batch`/
  `reclaim_batch` predicates: past-deadline jobs stay claimable precisely so
  the rehydrated slice's wall-clock termination fires TIMED_OUT through the
  existing fenced write path — a claim never bypasses rehydration. The
  reaper's force-TIMED_OUT leg backstops runs whose job row is dead.
- **Progress-aware reclaim breaker** — at claim time the worker mirrors the
  run's routed-shard count into `job.progress_marker` (baseline sentinel
  `-1` = unobserved). On reclaim, growth resets `reclaims` and updates the
  marker — rolling restarts of long bot runs no longer trip D40; an
  unchanged count increments as today, so genuine crash-idle loops still
  STUCK. Mixed versions coexist: old replicas ignore the column, new
  replicas treat `-1`/first observation as baseline, never as progress.
- **Shared artifact root** — multi-replica deployments must point every
  backend pod at the same `AGENTSWARM_ARTIFACT_ROOT` volume
  (`artifact_store_root`): a pod resuming a run dereferences `artifact_key`
  shards spilled by a dead pod. An unshared root surfaces as
  missing-artifact reads exactly across pods.
- **Indexes / perf** — submit-time COUNT scans bind only
  (`tenant`,`status`) today: known gap — counters degrade to fleet-wide
  scans; fix is binding `env` (or status-leading indexes) in the three
  admission counters. SSE cost/pod = subscribers × 4 indexed queries/s —
  switch to one-poller + broadcast when per-run subscribers routinely
  exceed `page_size` (20).
- **Drain** — slice-bound writes are short; verify
  `drain.grace_seconds` (30) ≥ a slice's final write batch before shrinking
    pool/recycle values.

## Rollout

- **Migration first** — apply `sql/` files ascending **before** the release
  that reads them (D38): `progress_marker` is additive and defaulted, so old
  pods keep inserting the old row shape against the migrated table. Never
  DROP back; a reversal is itself a new additive migration.
- **Roll replicas** — restart workers with `drain.grace_seconds` (30)
  covering a slice's final write batch; an unfinished slice either finishes
  within the grace or releases via lease expiry and reschedules — the old
  pod's late writes are rejected by `claim_epoch` fencing.
- **Rollback** — single knob: `maintenance.enabled=false` stops the reaper
  loop; reverted code leaves `progress_marker` inert (extra column ignored,
  never repurposed).

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
  functional `SandboxRuntimeClient` binding for the chat leg, and every
  provisioning call carries the run's authenticated submitter as its
  per-call `owner_id` — no profile-level identity exists.
- **Prompt injection** — subagent output is untrusted: `SliceStore`
  records `prompt_injection` guard events for detected markers while still
  routing the result; the orchestrator wraps results in an
  `<untrusted_result>` fence and instructs the model never to follow
  instructions inside it.

## Service / HTTP / CLI surface

Service Protocol (`service/protocols.py`): `submit_async`, `submit_sync`,
`get_run`, `list_runs`, `run_events`, `cancel`, `purge_run`,
`content_classification` (the last two are service-plane only — data
protection; no route, no CLI). Router prefix `/openapi/v1/agentswarm`; every public
route resolves an authenticated end-user (anonymous → 401; `/health` and `/ready`
stay probe-open), while the tenant scope is an explicit per-request parameter
(body field on submit, query parameter elsewhere) — no shipped profile provides a
tenant through the principal, a blank tenant is rejected at validation, and
cross-tenant access is refused at the data layer as the same 404 as an unknown
run.

```bash
# async submit -> {"run_id": "..."}
curl -sS -X POST http://127.0.0.1:8000/openapi/v1/agentswarm/runs -H 'Content-Type: application/json' -d '{
  "goal": "map the incident to owning services",
  "agents": [{"name": "scout", "role": "research"}],
  "idempotency_key": "inc-42"}'

# sync submit (blocks until terminal or deadline)
curl -sS -X POST 'http://127.0.0.1:8000/openapi/v1/agentswarm/runs?wait=true' -H 'Content-Type: application/json' -d '{"goal": "summarize logs", "agents": [{"name": "s1", "role": "research"}]}'

# get / list / cancel (tenant comes from the authenticated principal)
curl -sS http://127.0.0.1:8000/openapi/v1/agentswarm/runs/<run_id>
curl -sS 'http://127.0.0.1:8000/openapi/v1/agentswarm/runs?page=1&page_size=20&status=COMPLETED'
curl -sS -X POST http://127.0.0.1:8000/openapi/v1/agentswarm/runs/<run_id>/cancel

# SSE stream: status, agent/round/guard deltas, heartbeats, terminal
curl -sS -N http://127.0.0.1:8000/openapi/v1/agentswarm/runs/<run_id>/events

# report (HTML) + health
curl -sS http://127.0.0.1:8000/openapi/v1/agentswarm/runs/<run_id>/report
curl -sS http://127.0.0.1:8000/openapi/v1/agentswarm/health
```

Error mapping: tenant limit exceeded → 429 `tenant_run_limit_exceeded`;
unresolvable tenant (principal without a tenant) → 400; missing LLM
config → 400; unknown run → 404. SSE `run_events` advance row
cursors (`after_agent_id`, `after_round_id`, `after_event`) — each event
emits once per stream.

### Bot-create human gate (`AWAITING_AUTHORIZATION`)

Bot lifecycle states (`ac_agent_swarm_agent.bot_state`) are
`PROVISIONING`, `AWAITING_AUTHORIZATION`, `READY`,
`AUTHORIZATION_REJECTED`, `AUTHORIZATION_EXPIRED`, `CREATE_FAILED`,
`APPLY_FAILED`. While a bot's passport application is **pending human
approval** (the in-window consent window, enforced by the durable creation
job), the swarm reports `AWAITING_AUTHORIZATION` instead of generic
`PROVISIONING`; the park is per-bot — a mixed run keeps each agent's gate
state independent, and auto-issued creations (passport issued inline, no
consent page) never enter the gate (the decision keys off the application
status being pending, never off the presence of consent URLs).

**Run detail** — an agent with a passport application actually PENDING
inside its window carries an additive `authorization` descriptor. Presence
keys off the authorization source — the passport facade's live
application status joined with the durable creation-job payload (no copy
of the facts lives in swarm tables) — never off the agent row's cached
`bot_state` write: parallel cohort agents go PENDING at apply time while
the provisioning pool parks their rows sequentially, so a later agent
carries the gate even before its `bot_state` says
`AWAITING_AUTHORIZATION`, and its window is never hidden behind the
pool's lag:

```json
{
  "agent_id": "agent-42",
  "name": "scout",
  "status": "RUNNING",
  "bot_id": "agent_swarm_9f31c0e2a7b4d6f1",
  "bot_state": "AWAITING_AUTHORIZATION",
  "authorization": {
    "state": "AWAITING_AUTHORIZATION",
    "iframe_url": "https://tcauthmng.example/consent?app=passport",
    "redirect_url": "https://tcauthmng.example/done",
    "deadline": "2026-11-20T10:10:00",
    "application_id": "sim-passport-3d2f0a1b2c04"
  }
}
```

`application_id` names the facade's application so a consumer can
address the decision surface from the run read alone — no direct listing
against the passport facade. Non-pending agents — auto-issued, approved,
rejected, expired, terminal — serialize `authorization: null`, and every
pre-existing field is unchanged. Approvals happen only on the consent
page; the swarm only surfaces the URL, never approves on a human's
behalf.

```bash
# detail view of a mixed run: one agent parked at the gate, one ready
curl -sS 'http://127.0.0.1:8000/openapi/v1/agentswarm/runs/<run_id>/detail?tenant=acme' \
  | jq '.agents[] | {name, bot_state, authorization}'
```

**SSE gate deltas** — `GET /runs/{run_id}/events` rides the existing
`agent` event kind (no new event names) for gate crossings: entering the
park (`bot_state` becomes `AWAITING_AUTHORIZATION`, descriptor attached)
and leaving it (issued → `PROVISIONING`/`READY` resume, human-rejected →
`AUTHORIZATION_REJECTED`, window elapsed → `AUTHORIZATION_EXPIRED` —
descriptor absent from the payload). Deltas are per-stream state, keyed
off the `RunEvents` authorization snapshot, not row cursors: unchanged
status polls emit nothing, the auto-issue fast path (`PROVISIONING →
READY`) emits nothing, and each crossing emits exactly once per stream
(a stream attaching mid-park surfaces the pending gate once):

```
event: agent
data: {"event": "agent", "run_id": "<run_id>", "data": {
  "agent_id": "agent-42", "name": "scout", "status": "RUNNING",
  "kind": "bot", "spawn_round": 0,
  "bot_id": "agent_swarm_9f31c0e2a7b4d6f1",
  "bot_state": "AWAITING_AUTHORIZATION",
  "authorization": {"state": "AWAITING_AUTHORIZATION",
                    "iframe_url": "https://tcauthmng.example/consent?app=passport",
                    "redirect_url": "https://tcauthmng.example/done",
                    "deadline": "2026-11-20T10:10:00",
                    "application_id": "sim-passport-3d2f0a1b2c04"}}}
```

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