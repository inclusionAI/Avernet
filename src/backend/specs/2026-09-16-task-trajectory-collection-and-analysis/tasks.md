# Task Trajectory Collection & Root-Cause Analysis — Tasks

> Status legend: `[x]` implemented + covered by tests, `[~]` implemented but gated / pending
> environment validation, `[ ]` not yet done.
> TDD discipline: for every implementation item, write the named failing test first, run it to
> confirm it fails, implement the minimal code to pass, run it green, then commit. Spec is
> `spec.md`; sequencing & clarifications are in `plan.md` (**read the "Spec clarifications" block
> before starting — it overrides 5 stale spec lines**).

## P0 — Domain models (REQ-1)

- [x] Add `core/task/task_trajectory/__init__.py` + `models.py` with plain dataclasses:
      `TrajectoryEvent` (flat: no `payload`/`rationale`/`phase`), `TaskTrajectory`, `TrajectoryAnalysis`,
      `ReasonCatalog` enum, `DispatchRationale`, `TrajectoryActionType` (separate from `NodeAction`).
- [x] Test `tests/community/core/task/task_trajectory/__init__.py` + `test_trajectory_models.py`:
      each dataclass builds from kwargs; `analysis`/`gmt_modified` default to `gmt_create`/None;
      `ReasonCatalog` covers every §overview signal + REQ-5 origins; `TrajectoryActionType` ≠ `NodeAction`.

## P1 — Storage (REQ-11 + REQ-P1)

- [x] Write additive DDL `core/task/sql/2026_09_17_task_trajectory.sql` (`task_trajectory`,
      `task_trajectory_events` per spec DDL; `task_trajectory` unique on `task_id`);
      write `core/task/sql/2026_09_16_task_callback_correlation.sql`.
- [x] Add ORM models `TaskTrajectoryModel`, `TaskTrajectoryEventModel`,
      `TaskCallbackCorrelationModel` into `core/task/repository/models.py` (use
      `AutoIncrementBigInteger`, `_binary_string`, `Index(...)`; mirror `TaskCallbackModel`);
      add `.to_record()` mappers; add record dataclasses to `core/task/repository/types.py`.
- [x] Verify singlebox `Base.metadata.create_all` creates the new tables (conftest in
      `tests/community/repository/task/` imports `core/task/repository/models`).
- [x] Implement `TaskTrajectoryRepository`: `insert_event`, `upsert_head(task_id, analysis=None)`
      (preserve existing `analysis`/`gmt_modified` when head exists), `backfill_analysis(task_id,
      analysis_json)` UPDATE both tables `analysis`+`gmt_modified`, `list_events_by_task(task_id)`
      ascending by `gmt_create`. Add protocol. `orm_session()`/`db.flush()`, no explicit commit.
- [x] Implement `TaskCallbackCorrelationRepository`: `upsert_on_register(event_id, main_session_id,
      task_id, node_id, retry)`, `find_by_event_id(event_id)`. Add protocol.
- [x] Test `tests/community/repository/task/test_task_trajectory_repository.py`: insert→append
      (append-only, duplicate rows allowed), head UPSERT preserves analysis, backfill updates
      analysis+`gmt_modified`, list ascending by `gmt_create`.
- [x] Test `tests/community/repository/task/test_task_callback_correlation_repository.py`:
      register→find by event_id idempotent.

## P2 — Emission helper (REQ-11)

- [x] Add `core/task/task_trajectory/payloads.py`: `build_trajectory_event_record(...)` (builds a
      `TrajectoryEventRecord` persisted projection with `datetime` gmt + `ext_info` JSON envelope
      `{"schema_v":1, **ext_info}` + domain enum→`.value` string mapping) and
      `emit_trajectory_event(repo, task_id, node_id, action_type, *, action_result, action_input=None,
      error_type=None, error_msg=None, ext_info=None, status_from=None, status_to=None, attempt=0,
      now_ms=None)` → `repo.insert_event(record)` inside `try/except Exception: logger.warning(...)`
      `# noqa: BLE001`, no re-raise. Does NOT call `append_action_event`. ``repo is None`` → no-op.
- [x] Inject `TaskTrajectoryRepositoryProtocol` into `ExecutionEngine` as optional dependency
      (`trajectory_repo: ... | None = None`); add engine method `_log_trajectory(...)` delegating to
      `emit_trajectory_event(self._trajectory_repo, ...)`. Threaded through `TaskService.__init__` +
      `_build_engine`; DI wired in `task_module.py` (try/except resolve → prod gets real repo,
      lightweight tests get None → emitter no-ops).
- [x] Test `test_trajectory_emitter.py` (9 cases): builder enum→value / ext_info envelope / None
      defaults / plain-string acceptance; normal emit → exactly one record with correct flat fields
      + `ext_info` JSON carries `schema_v`+payload + gmt_* from now_ms + enums→strings; repo None →
      no-op; repo raises → swallowed, WARNING logged (caplog), no re-raise.

## P3 — Collection gates (REQ-2 … REQ-7)

Each gate item: write a `test_trajectory_gates.py` case that drives the gate (mock repo capture or
real sqlite) and asserts the emitted row's `action_type`/`action_result`/`action_input`/`ext_info`.

### REQ-2 — DISPATCH rationale
- [x] Add `rationale: DispatchRationale | None` to `SearchResult` (strategies.py);
      `DirectDispatchStrategy.apply` / `SearchBasedDispatchStrategy.apply` populate strategy_name,
      decision_mode, candidates[], prefetch_tokens, join_filter_applied, join_dropped[],
      skill_prompt_digest, skill_response_digest.
- [x] `TaskDispatcher.dispatch` writes rationale JSON into
      `node.run_info.extend_props["_dispatch_rationale"]` (no contextvar available — this is the carrier).
- [x] Engine DISPATCH gates fire `_log_trajectory(action_type=dispatch, ext_info=
      {"_dispatch_rationale": ...})` at `_drain` HIT_SINGLE (~engine.py:2963), `_drain` HIT_MULTI
      (~2870), `on_miss` MISS (~2108). Gate test asserts `ext_info` carries full `DispatchRationale`.

### REQ-7 — join-dropped reason micro-classes
- [x] Extend `_apply_claim_join` (strategies.py:359-461) drop reasons to
      `{claim_mode_off, catalog_miss, score_below_threshold, claim_filter_disabled}`; flowed into
      `DispatchRationale.join_dropped[].reason`. Test: each reason restorable from a DISPATCH
      event's `ext_info`.

### REQ-3 — PLAN per-attempt events
- [ ] In `_plan_with_retry` (engine.py:599; PLAN action emit ~644), fire one trajectory event per
      attempt: `action_type=plan`, `attempt=n`, `action_input=prompt_digest` (SHA-256 of
      `_compose_planning_prompt` output + first 500 chars of owner-bot response; None for workflow
      strategy), `ext_info={strategy_name, gap_detail, raw_response_digest, has_gap, children[]}`.
- [ ] Test: `MAX_HARNESS=2` retry → ≥2 `plan` rows, `attempt` increasing, mid-row `error_*` set,
      success row `ext_info.children` set; workflow strategy → `prompt_digest=None`.

### REQ-4 — RESET elapsed/SLA
- [ ] At harness RESET gate (~engine.py:2056) fire `action_type=reset` with `action_result ∈
      {sla_timeout, exec_failed_retry, pending_dispatch_stuck, bbs_lease_expired, harness_max}` and
      `ext_info={trigger, elapsed_ms, sla_threshold_ms, attempts_seen}`; `elapsed_ms=now-start_time`;
      `sla_threshold_ms` from `harness._sla_timeout`/`_pending_timeout` (600/900/180s).
- [ ] Test: an SLA-timeout RESET row yields `failure_reason`-derivable `elapsed`/`threshold`;
      non-timeout trigger → `sla_threshold_ms=None`.

### REQ-5 — EXECUTE/VERIFY error origin
- [ ] Add `exec_error_origin` classification helper in `callback_adapter.py`:
      `bot_interface` (bot-set `exec_error`), `parse` (`ingest_parse_error` callback_adapter.py:412),
      `terminal_invalid` (non-bool success / failed-no-gaps ~:194), `transport` (plan_call_fail /
      dispatch_exception). Surface origin to `TaskNodePatch.extend_props["_exec_error_origin"]`.
- [ ] At EXECUTE/VERIFY gates (~engine.py:1691/1701/1709) fire `action_type=execute|verify` with
      `error_type` via `ReasonCatalog` map (`bot_interface→underlying_interface_error`,
      `parse→parse_error`, `terminal_invalid→terminal_invalid`, `transport→transport_error`),
      `error_msg` truncated, `action_input=request_input` original (try/except collect, None on
      fail), `ext_info={interface_error_code?}`.
- [ ] Test: a bot-`exec_error` callback → event `error_type=underlying_interface_error` + interface
      msg; parse failure → `parse_error`; `action_input` carries full request.

### REQ-6 — SUBMIT
- [x] **Locate the real `task_info` persist point** (spec's engine.py:351-357 is stale — search
      `TaskService.execute` ~task_service.py:326 / engine submit flow) and fire
      `action_type=submit` with `action_input=task_spec_digest`, `ext_info={source, task_type,
      owner_user_id, owner_bot_id, submitted_at}`. `submit` is a `TrajectoryActionType`, never `NodeAction`.
      Located fire point: `task_service.py:TaskService.execute` — the unique `self._task_info_repo.insert(record)`
      call (status=PENDING) is the real persist; the SUBMIT gate sits right after that block, before
      `initialize_graph`, covering all branches. Helper `emit_submit_trajectory` in
      `core/task/task_trajectory/payloads.py` (outside `task_service.py` to respect its 999/1000-line CI
      limit); `task_service.py` addition is a single call site + 1 import line (net 0 lines — moved
      `_split_owner_bot_id` to `task_service_support.split_owner_bot_id` to buy headroom).
- [x] Test: any task's `timeline[0].action_type == "submit"`; workflow/yaml/bbs/external branches covered.
      `tests/community/core/task/task_trajectory/test_trajectory_gates_submit.py` (16 cases): emission,
      ext_info fields, digest == SHA-256(json.dumps(spec.to_dict(), sort_keys=True)), branch coverage
      (dynamic/workflow/yaml/bbs parametrized), fire-point placement (None vs present task_info_repo,
      persist IntegrityError short-circuits), zero-intrusion (broken repo → execute succeeds + WARNING),
      action_log untouched, `submit` is `TrajectoryActionType` not `NodeAction`.

### Intrusion guard (cross-cutting)
- [ ] Test: with the trajectory repo forced to raise, every gated path (PLAN/DISPATCH/EXECUTE/
      VERIFY/RESET/SUBMIT) still completes and drives forward — proves the swallow guarantee.

## P4 — Assembly (REQ-8 read side)

- [ ] Add `core/task/task_trajectory/assembler.py::TaskTrajectoryAssembler.assemble(task_id)`:
      `list_events_by_task` (asc `gmt_create`) → `TrajectoryEvent`s → `TaskTrajectory{timeline,
      analysis, gmt_create, gmt_modified}`; `upsert_head(task_id)` preserving existing `analysis`. No
      `phases`/`graph_snapshot`; never touches `task_action_log`.
- [ ] Test `test_trajectory_assembler.py`: timeline ascending (SUBMIT first, terminal TRANSITION
      last); pre-trajectory old task → empty timeline (no action-log fallback); UPSERT preserves a
      pre-set analysis.

## P5 — Analysis + trigger endpoint (REQ-9 + REQ-8 `do_analysis`)

### Analyzer
- [ ] Add `core/task/task_trajectory/analyzer.py::TaskTrajectoryAnalyzer.analyze(trajectory,
      ext_info_lookup, *, analysis_type, analysis_executor) -> TrajectoryAnalysis`: dispatch
      `rule`/`llm`/`tc_bot`; `boost_reason`/`failure_reason` flattened strings; `analysis` JSON
      serializes with no event list (no recursion).
- [ ] `rule` executor = the 7-bullet `failure_reason` derivation (REQ-9) + `boost_reason` from last
      DISPATCH `ext_info`. Unit-tested; **not wired to live trigger first iteration** (decision #11).
- [ ] `tc_bot` executor = calls the DI-injected bot (bot_id from `task_trajectory_analysis_bot_id`)
      with trajectory + ext_info summary; synchronous with timeout; returns `TrajectoryAnalysis{
      analysis_type="tc_bot", analysis_executor=bot_id, …}`.

### Service facade + endpoint
- [ ] Add `api/task/task_trajectory_service.py` (`TaskTrajectoryServiceProtocol.get_trajectory(
      task_id, *, do_analysis: bool = False) -> TaskTrajectory`) + impl in `core/task/task_trajectory/
      trajectory_service.py`: assemble → (if `do_analysis`) `.analyze(tc_bot)` → `backfill_analysis`
      (overwrite) → return same-shape `TaskTrajectory`. Bot failure/timeout → domain error → HTTP 504,
      no backfill.
- [ ] Add `TaskTrajectoryDTO` + `trajectory_to_dto` in `adapters/http/task/schemas.py`.
- [ ] Add `do_analysis: Annotated[bool, Query(description="是否触发 bot 总体分析(默认关闭)")] = False`
      to the existing internal `GET /api/v1/collaboration/tasks/{task_id}/trajectory` handler and the
      OpenAPI mirror `GET /openapi/v1/collaboration/tasks/{task_id}/trajectory` (`PublicAPIRoute`,
      `principal: PrincipalDep`). Register DI bindings + `task_trajectory_analysis_bot_id` config.
- [ ] Test `test_trajectory_analyzer.py`: per-`ReasonCatalog` fixture → `failure_reason` prefix;
      `tc_bot` with a mock bot client → `analysis_type="tc_bot"`/`analysis_executor=bot_id`;
      success task → `failure_reason=None`.
- [ ] Endpoint tests under `tests/community/adapters/http/openapi_v1/`: `do_analysis=false`
      (read, `analysis=null` if never analyzed, no DB write) + `do_analysis=true` (backfill, same
      `TaskTrajectory` shape returned, re-call overwrites) + timeout → 504 no-backfill. NO separate
      `/trajectory/analysis` endpoint created (decision #10).

## P6 — Callback↔node correlation across restart (REQ-P1)

- [ ] Persist `CallbackCorrelationRegistry` writes into `task_callback_correlation` on register
      (callback_correlation.py → repo). `task_callback_correlation` DDL in place (P1).
- [ ] `TaskTrajectoryAssembler` joins `task_callback_correlation` so a post-restart callback
      correlates back to its (node, retry).
- [ ] Test: register → simulate restart → callback arrives → assembler associates to the right
      (node, retry).

## P7 — Validation & rollout

- [ ] Repository / core/task / di / openapi adapter suites green; `task_action_log` tests zero-diff
      (decoupling proof).
- [ ] E2E four types (`success`/`interface_error`/`timeout`/`hung`) via
      `/trajectory?do_analysis=true`; assert `failure_reason` prefixes & content.
- [ ] Cross-restart trajectory+analysis readable (data persists in DB after restart).
- [ ] Gate-intrusion test (P3 cross-cutting) green; `dashboard?include_action_log=true` untouched.
