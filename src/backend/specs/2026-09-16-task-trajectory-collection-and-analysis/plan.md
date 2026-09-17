# Task Trajectory Collection & Root-Cause Analysis — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **For agentic workers:** implement in small, reviewable, TDD tasks (red → green → commit per
> item). Keep the forward-driving path and the `task_action_log` contract **zero-change**. Run the
> repository and task regression suites after each milestone. See `tasks.md` for the checklist and
> `spec.md` (this folder) for authoritative behavior/acceptance.

**Goal:** Build an **independent task-trajectory旁路** that reconstructs a task's full lifecycle
(submit→plan→dispatch→execute→verify→reset→transition) into `task_trajectory` +
`task_trajectory_events`, and surfaces root-cause ("why this assignee / why this failure") on
demand via `GET /tasks/{id}/trajectory?do_analysis=true`, which triggers a DI-configured bot to
produce a persisted `TrajectoryAnalysis`. Zero coupling to `task_action_log`; zero intrusion on the
forward-driving path (every gate emission is `try/except`-swallowed + DEBUG log).

**Architecture:** Three independent layers. (1) **Collection** — `_log_trajectory` helper at
`engine.py` lifecycle gates emits a `TrajectoryEvent` and **directly INSERTs** into
`task_trajectory_events` via a new `TaskTrajectoryRepository` (NOT through the in-memory graph
`append_action_event` path — that stays untouched). (2) **Assembly** — `TaskTrajectoryAssembler`
reads events ascending by `gmt_create`, builds `TaskTrajectory{timeline, analysis}`, UPSERTs the
`task_trajectory` head row (preserving backfilled `analysis`). (3) **Analysis** —
`TaskTrajectoryAnalyzer` is a thin multi-executor dispatcher (`rule` / `llm` / `tc_bot`); first
iteration wires only `tc_bot` (bot_id injected from config `task_trajectory_analysis_bot_id`) and
backfills (overwrite) the `analysis` column on `do_analysis=true`. The HTTP entrypoint is the
existing `/tasks/{id}/trajectory` endpoint (internal + OpenAPI mirror) extended with a `do_analysis`
query param; the separate `/tasks/{id}/trajectory/analysis` endpoint is **not** built (folded in,
per confirmed decision #10).

**Tech Stack:** SQLAlchemy 1.x declarative (`core/base.py::Base`); FastAPI + pydantic v2
(`Envelope[T]` generics auto-register OpenAPI); DI via `Injected(Protocol)`; pytest 8.x under
`src/backend/.venv` (uv-managed), in-memory SQLite for repo tests / OceanBase DDL for prod;
`@inject` repositories over `DatabasePlugin.orm_session()` (auto-commit/rollback, `db.flush()`
to surface errors).

**Spec:** `src/backend/specs/2026-09-16-task-trajectory-collection-and-analysis/spec.md`

---

## Spec clarifications discovered during planning (override stale lines in spec.md)

These reconcile the spec against the real code; where they conflict with a spec line, **these win**:

1. **Real file paths** use the `src/backend/src/agentclaw/community/...` prefix (spec's short
   `core/task/...` / `adapters/http/task/...` are shorthand). All paths below use the real prefix.
2. **`_log_trajectory` must NOT delegate to `append_action_event`.** `engine._log_action` (engine.py:713)
   delegates to `self._graph.append_action_event` which mutates the in-memory graph; the spec mandates
   the trajectory path be **independent and direct-INSERT**. So `_log_trajectory` mirrors only the
   *swallow + DEBUG log + no re-raise* pattern, and calls `self._trajectory_repo.insert_event(...)`.
3. **Do not touch the `NodeAction` enum.** It has PLAN/DISPATCH/EXECUTE/VERIFY/RESET/TRANSITION
   (domain/models.py:57) and the spec explicitly forbids changing it. `TrajectoryEvent.action_type`
   is a **separate** `TrajectoryActionType` set (`submit|plan|dispatch|execute|verify|reset|transition`)
   in the new `task_trajectory/models.py`.
4. **SUBMIT gate `engine.py:351-357` is stale/garbled** — that range is the engine constructor, not a
   gate or a `task_info` persist point. Task P6 (SUBMIT) must **locate the real persist point**
   (search `TaskService.execute` ~task_service.py:326 and the engine submit flow for where
   `task_info` is first written) and emit SUBMIT there; do not cite 351-357.
5. **`exec_error_origin` does not exist yet.** `callback_adapter.py` carries only free-text
   `exec_error`. REQ-5's `bot_interface|parse|terminal_invalid|transport` classification is **new
   work**: add an origin-determining helper in `callback_adapter`, surface the origin onto the
   `TaskNodePatch` (an `extend_props` key, e.g. `_exec_error_origin`), and have the engine
   EXECUTE/VERIFY gate read it to set the event `error_type`.

## Global constraints (invariants — must hold throughout)

- **Zero `task_action_log` changes**: never read/modify/enrich it; tests that touch it stay green.
- **Zero intrusion on forward driving**: every trajectory emission (incl. `ext_info` assembly)
  is `try/except Exception: logger.debug(...)` with `# noqa: BLE001`, never re-raised, never blocks
  the gate's main logic. Verified by a gate test that breaks the repo and asserts the gate still completes.
- **Independent entity**: `task_trajectory*` share **no** foreign key / query dependency with
  `task_action_log` or `task_callback`.
- **Append-only events**: `task_trajectory_events` has no unique constraint; duplicate emissions may
  yield duplicate rows (business-accepted; analyzer/diagnose take the last by `id`).
- **`analysis` is single-TEXT, one slot**: every `do_analysis=true` **overwrites** (last-writer-wins);
  no analysis history in first iteration (YAGNI).
- **bot_id is deployment-configured, not per-request**: injected via DI from
  `task_trajectory_analysis_bot_id`; callers cannot choose the bot.
- **`action_input` is not truncated** (execute/verify request原文, submit task_spec_digest,
  plan prompt_digest). Digests elsewhere = SHA-256 over prompt + first 500 chars of response.
- **`submit` is a trajectory action-type, not a `NodeAction`.**

## File structure

```text
src/backend/src/agentclaw/community/
├── core/task/task_trajectory/                 # NEW submodule (采集旁路 + 组装/分析)
│   ├── __init__.py
│   ├── models.py            # TrajectoryEvent, TaskTrajectory, TrajectoryAnalysis,
│   │                        #   ReasonCatalog, DispatchRationale, TrajectoryActionType (REQ-1)
│   ├── payloads.py         # TrajectoryEventBuilder + _log_trajectory emitter helper (REQ-11)
│   ├── assembler.py         # TaskTrajectoryAssembler (REQ-8)
│   └── analyzer.py          # TaskTrajectoryAnalyzer (rule/llm/tc_bot dispatch) (REQ-9)
├── core/task/repository/
│   ├── models.py            # +TaskTrajectoryModel, TaskTrajectoryEventModel (REQ-11)
│   │                        #   +TaskCallbackCorrelationModel (REQ-P1)
│   └── types.py            # +TrajectoryEventRecord, TaskTrajectoryRecord,
│                            #   TaskCallbackCorrelationRecord
├── core/repository/implementations/task/
│   ├── task_trajectory_repository.py        # insert_event / upsert_head /
│   │                                         #   backfill_analysis / list_events_by_task (REQ-11)
│   └── task_callback_correlation_repository.py # write / find_by_event (REQ-P1)
├── core/task/sql/
│   ├── 2026_09_17_task_trajectory.sql        # task_trajectory + task_trajectory_events DDL (REQ-11)
│   └── 2026_09_16_task_callback_correlation.sql                              # (REQ-P1)
├── core/task/task_dispatch/
│   ├── strategies.py        # +DispatchRationale on SearchResult; join_dropped reason micro-classes (REQ-2, REQ-7)
│   └── dispatcher.py        # write rationale into run_info.extend_props["_dispatch_rationale"] (REQ-2)
├── core/task/task_center/engine.py          #挂 _log_trajectory at PLAN/EXECUTE/VERIFY/RESET/DISPATCH/SUBMIT gates (REQ-2..7)
├── core/task/task_center/task_service.py     # (locate) SUBMIT emission point (REQ-6)
├── core/task/task_plan/
│   ├── planner.py          # strategy_name exposed for ext_info (REQ-3)
│   └── strategies.py       # prompt digest source (REQ-3)
├── core/task/task_harness/harness.py         # SLA threshold source: 600/900/180s (REQ-4)
├── core/task/task_runner/
│   ├── callback_adapter.py # +exec_error_origin classification → patch (REQ-5)
│   └── callback_correlation.py # persist to task_callback_correlation on register (REQ-P1)
├── api/task/task_trajectory_service.py      # NEW TaskTrajectoryServiceProtocol (facade) (REQ-8)
├── core/task/task_trajectory/trajectory_service.py # impl: assemble + do_analysis orchestration (REQ-8)
├── adapters/http/task/router.py             # GET /tasks/{id}/trajectory +?do_analysis (internal) (REQ-8)
├── adapters/http/task/schemas.py            # TaskTrajectoryDTO + trajectory_to_dto (REQ-8)
├── adapters/http/openapi_v1/task/router.py # mirror route (PublicAPIRoute) (REQ-8)
└── di/modules/                              # bind TaskTrajectoryServiceProtocol + inject bot_id config

src/backend/tests/community/
├── core/task/task_trajectory/               # NEW: domain/emitter/assembler/analyzer/gate unit tests
│   ├── __init__.py
│   ├── test_trajectory_models.py
│   ├── test_trajectory_emitter.py
│   ├── test_trajectory_assembler.py
│   ├── test_trajectory_analyzer.py
│   └── test_trajectory_gates.py
└── repository/task/                         # existing create_all conftest
    ├── test_task_trajectory_repository.py   # NEW
    └── test_task_callback_correlation_repository.py  # NEW
```

## Phase 0 — Domain models (REQ-1)

1. Add `core/task/task_trajectory/{__init__,models}.py`: plain dataclasses only — `TrajectoryEvent`
   (flat, no nested payload/rationale/phase), `TaskTrajectory{task_id, timeline, analysis, gmt_create, gmt_modify}`,
   `TrajectoryAnalysis{analysis_type, analysis_executor, analysis_input, analysis_output, boost_reason?, failure_reason?, gmt_create}`,
   `ReasonCatalog` enum, `DispatchRationale`, and `TrajectoryActionType` (separate from `NodeAction`).
2. TDD: `tests/community/core/task/task_trajectory/test_trajectory_models.py` asserts each dataclass
   builds from kwargs, `analysis` defaults to None, `ReasonCatalog` covers every spec §overview signal.

## Phase 1 — Storage layer (REQ-11 + REQ-P1)

1. Write additive DDL `core/task/sql/2026_09_17_task_trajectory.sql` (`task_trajectory`,
   `task_trajectory_events` per spec DDL) and `2026_09_16_task_callback_correlation.sql`.
2. Add ORM models to `core/task/repository/models.py` using `AutoIncrementBigInteger`,
   `_binary_string`, `Index(...)` (mirror `TaskCallbackModel`/`TaskActionLogModel` shape); add
   `to_record()` row→dataclass mappers; add record dataclasses to `types.py`.
3. Ensure singlebox SQLite bootstraps the new models (side-effect import already in
   `tests/community/repository/task/conftest.py`; verify `create_all` covers the new tables).
4. Add repository protocols + impls: `TaskTrajectoryRepository` (`insert_event`,
   `upsert_head(task_id, analysis=None)` preserving existing analysis, `backfill_analysis(task_id, analysis_json)`
   UPDATE both tables + `gmt_modify`, `list_events_by_task(task_id) order by gmt_create`);
   `TaskCallbackCorrelationRepository` (`upsert_on_register`, `find_by_event_id`).
   Follow the `orm_session()`/`db.flush()` pattern; **no** explicit `commit()`.
5. TDD: `tests/community/repository/task/test_task_trajectory_repository.py` covers
   insert→append, head UPSERT preserves analysis, backfill updates analysis+gmt_modify, list ascending;
   `test_task_callback_correlation_repository.py` covers register→find idempotent.

## Phase 2 — Emission helper (REQ-11 payloads)

1. Add `core/task/task_trajectory/payloads.py`: `TrajectoryEventBuilder` (builds a `TrajectoryEvent`
   + optional `ext_info: dict` with `schema_v`) and a `_log_trajectory(engine, task_id, node_id,
   action_type, *, action_result, action_input, error_type=None, error_msg=None, ext_info=None,
   status_from=None, status_to=None, attempt=None)` that calls `self._trajectory_repo.insert_event(...)`
   inside `try/except Exception as ex: logger.debug("[task][trajectory] ... 发射失败:%s", ex)` with
   `# noqa: BLE001`, no re-raise.
2. Inject `TaskTrajectoryRepository` into `ExecutionEngine` (constructor / DI).
3. TDD: `test_trajectory_emitter.py` asserts (a) a normal emit produces one `task_trajectory_events`
   row with correct flat fields + `ext_info` JSON; (b) when the repo raises, the emitter swallows it,
   logs DEBUG, returns None, and **does not** re-raise.

## Phase 3 — Collection gates (REQ-2 … REQ-7)

Wire `_log_trajectory` at each engine gate. Each gate = one TDD item: a test drives the gate and
asserts the `task_trajectory_events` row(s) + `ext_info`. Order: DISPATCH (REQ-2/7) → PLAN (REQ-3)
→ RESET (REQ-4) → EXECUTE/VERIFY (REQ-5) → SUBMIT (REQ-6).

1. **REQ-2 DISPATCH**: add `rationale: DispatchRationale | None` to `SearchResult`
   (strategies.py); `DirectDispatchStrategy.apply` / `SearchBasedDispatchStrategy.apply` populate it
   (strategy_name, decision_mode, candidates[], prefetch_tokens, join_filter_applied,
   join_dropped[{bot_id, reason}], skill_prompt_digest, skill_response_digest).
   `TaskDispatcher.dispatch` writes `rationale` (json) into `node.run_info.extend_props
   ["_dispatch_rationale"]` (no contextvar exists — this is the carrier). Engine DISPATCH gates
   fire `_log_trajectory(action_type=dispatch, ...)` with `ext_info["_dispatch_rationale"]` at:
   `_drain` HIT_SINGLE (~engine.py:2963), `_drain` HIT_MULTI (~2870), `on_miss` MISS (~2108).
2. **REQ-7 join reasons**: extend `_apply_claim_join` (strategies.py:359-461) drop reasons from
   `{claim_mode_off}` to `{claim_mode_off, catalog_miss, score_below_threshold, claim_filter_disabled}`;
   carried in `DispatchRationale.join_dropped[].reason`.
3. **REQ-3 PLAN**: in `_plan_with_retry` (engine.py:599; PLAN action emit at ~644), fire one
   trajectory event per attempt: `action_type=plan`, `attempt=n`, `action_input=prompt_digest`
   (SHA-256 over `_compose_planning_prompt` output + first 500 chars of owner-bot response; None for
   workflow strategy); `ext_info={strategy_name, gap_detail, raw_response_digest, has_gap, children[]}`.
4. **REQ-4 RESET**: at the harness RESET gate (~engine.py:2056) fire
   `action_type=reset` with `action_result ∈ {sla_timeout, exec_failed_retry, pending_dispatch_stuck,
   bbs_lease_expired, harness_max}` and `ext_info={trigger, elapsed_ms, sla_threshold_ms,
   attempts_seen}`; `elapsed_ms = now - run_info.start_time`; `sla_threshold_ms` from
   `harness._sla_timeout` / `_pending_timeout` (600/900/180s, overridable via `execution_config`).
5. **REQ-5 EXECUTE/VERIFY**: add `exec_error_origin` classification in `callback_adapter`
   (bot_interface when `TaskNodePatch.exec_error` set from bot callback; parse on
   `ingest_parse_error`; terminal_invalid on non-bool success / failed-no-gaps; transport on
   plan_call_fail / dispatch_exception). Surface origin to `TaskNodePatch.extend_props
   ["_exec_error_origin"]`. At EXECUTE/VERIFY gates (~engine.py:1691/1701/1709) fire
   `action_type=execute|verify` with `error_type` mapped via `ReasonCatalog`
   (`bot_interface→underlying_interface_error`, `parse→parse_error`, `terminal_invalid→
   terminal_invalid`, `transport→transport_error`), `error_msg` truncated,
   `action_input=request_input` original (try/except to collect, None on failure),
   `ext_info={interface_error_code?}`.
6. **REQ-6 SUBMIT**: **locate** the real `task_info` persist point (engine.py:351-357 is stale —
   search `TaskService.execute` ~task_service.py:326 / engine submit flow); fire
   `action_type=submit` with `action_input=task_spec_digest`, `ext_info={source, task_type,
   owner_user_id, owner_bot_id, submitted_at}`. `submit` is a `TrajectoryActionType`, never `NodeAction`.

## Phase 4 — Assembly (REQ-8, read side)

1. Add `core/task/task_trajectory/assembler.py::TaskTrajectoryAssembler.assemble(task_id)`:
   `list_events_by_task` (asc `gmt_create`), map rows → `TrajectoryEvent`, build
   `TaskTrajectory{task_id, timeline, analysis, gmt_create, gmt_modify}` (`analysis` from head row,
   None if absent), `upsert_head(task_id)` (preserves existing `analysis`). No `phases`/`graph_snapshot`.
2. TDD: `test_trajectory_assembler.py` — timeline ordering (SUBMIT first, terminal TRANSITION last),
   empty timeline for pre-trajectory tasks (no action-log fallback), UPSERT preserves a pre-set analysis.

## Phase 5 — Analysis + trigger endpoint (REQ-9 + REQ-8 `do_analysis`)

1. Add `core/task/task_trajectory/analyzer.py::TaskTrajectoryAnalyzer` multi-executor: `.analyze(
   trajectory, ext_info_lookup, *, analysis_type, analysis_executor) -> TrajectoryAnalysis`.
   - `rule` executor: the 7-bullet `failure_reason` derivation + `boost_reason` from last DISPATCH
     `ext_info` (spec REQ-9) — pure function, dormant first-iteration (unit-tested only).
   - `tc_bot` executor: calls the DI-injected bot (bot_id from config `task_trajectory_analysis_bot_id`)
     with the assembled trajectory + `ext_info` summary, returns a `TrajectoryAnalysis`
     (`analysis_type="tc_bot"`, `analysis_executor=bot_id`); synchronous with timeout.
2. Add `core/task/task_trajectory/trajectory_service.py` + `api/task/task_trajectory_service.py`
   (`TaskTrajectoryServiceProtocol`): `get_trajectory(task_id, *, do_analysis: bool = False)
   -> TaskTrajectory` orchestrating assemble → (if `do_analysis`) `.analyze(tc_bot)` →
   `backfill_analysis` (overwrite) → return. On bot failure/timeout: raise a domain error mapped to
   HTTP 504, do **not** backfill.
3. Add `TaskTrajectoryDTO` + `trajectory_to_dto` in `adapters/http/task/schemas.py`; add the
   `do_analysis: bool` query param to the existing internal `GET /api/v1/collaboration/tasks/
   {task_id}/trajectory` handler and the OpenAPI mirror `GET /openapi/v1/collaboration/tasks/
   {task_id}/trajectory` (`PublicAPIRoute`, `principal: PrincipalDep`). **Do not** add a separate
   `/trajectory/analysis` endpoint (decision #10). Register DI bindings + bot_id config.
4. TDD: `test_trajectory_analyzer.py` (per-`ReasonCatalog` fixture → `failure_reason` prefix;
   `tc_bot` with a mock bot client → fills `analysis_type="tc_bot"`; success task →
   `failure_reason=None`); endpoint tests under `tests/community/adapters/http/openapi_v1/` for
   `do_analysis=false` (read, `analysis=null` if never analyzed, no write), `do_analysis=true`
   (backfill + return same `TaskTrajectory` shape), and timeout → 504 no-backfill.

## Phase 6 — Callback↔node correlation across restart (REQ-P1)

1. Persist `CallbackCorrelationRegistry` writes into `task_callback_correlation` on register;
   delete/expire handled by `process_status`/`processed_at`.
2. `TaskTrajectoryAssembler` joins `task_callback_correlation` so a callback arriving after a
   restart can be correlated back to its (node, retry) for the timeline.
3. TDD: correlation repo test + a restart scenario test (register → simulate restart → callback
   arrives → assembler associates to the right node/retry).

## Phase 7 — Validation & rollout

1. Repository suite, core/task suite, di suite, openapi adapter suite all green; `task_action_log`
   tests zero-diff (proves decoupling).
2. E2E four failure types (`success` / `interface_error` / `timeout` / `hung`) each via
   `/trajectory?do_analysis=true`; assert `failure_reason` prefixes & content.
3. Cross-restart trajectory+analysis readability (data in DB after restart).
4. A specifically-written gate-intrusion test: break the trajectory repo, assert each gate still
   completes and drives forward (the swallow guarantee).

## Validation commands

```bash
cd src/backend
.venv/bin/python -m pytest -q tests/community/repository/task
.venv/bin/python -m pytest -q tests/community/core/task/task_trajectory
.venv/bin/python -m pytest -q tests/community/core/task
.venv/bin/python -m pytest -q tests/community/adapters/http/openapi_v1
.venv/bin/python -m pytest -q tests/community/di
# full backend gate (xdist):
DEPLOY_PROFILE=test PYTHONPATH="src/backend/src:src/backend" \
  .venv/bin/python -m pytest tests/community -v -n auto --dist loadfile \
  --continue-on-collection-errors
```

## Compatibility and rollback

- DDL is additive (new tables only); can be deployed before the application change. Old instances
  ignore the new tables and keep driving; trajectory events simply go unbuilt until new code runs.
- Rollback: drop/wither the new tables; old code never read them. Backfilled `analysis` rows are
  harmless historical data. No change to `task_action_log`, `task_callback`, the state machine, or
  the forward-driving API contract — so rollback cannot regress existing behavior.
- `do_analysis=true` overwrites the single `analysis` slot; if the bot is misconfigured, callers
  get a 504 and the prior `analysis` is preserved (no destructive overwrite on failure).
