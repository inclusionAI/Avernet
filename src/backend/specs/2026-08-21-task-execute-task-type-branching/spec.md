# `execute` task_type Branching (dynamic / workflow / yaml) — Design Spec

- **Date:** 2026-08-21
- **Scope:** Branch `TaskService.execute` on `execution_config.task_type`
  (`dynamic` | `workflow` | `yaml`); wire `task_runner` for the `workflow`
  (single-bot) and `yaml` (BCN coop-group) paths; record the obtained
  `session_id` to `task_node_run_info`. `dynamic` is unchanged.
- **Status:** Draft, pending user review → then implementation plan.
- **Builds on:** `2026-08-20-task-execute-request-persistence` (the
  `TaskInfoRequest` + `task_info` persistence this extends) and the
  `task_*` persistence layer (`TaskNodeRepository`, `TaskNodeRunInfoRepository`).

## 1. Goal

`execute` reads `task_type` from `execution_config` and dispatches:
- **`dynamic`** — current engine flow (fire-and-forget `on_execute`: plan →
  dispatch → start_run).
- **`workflow`** — trigger a single bot: send `/{workflow_id} 参数列表` to
  `owner_bot_id` via `task_runner`; obtain the conversation `session_id`; persist.
- **`yaml`** — create a BCN cooperation group via `task_runner`; obtain the
  initial `session_id`; persist.

The `session_id` (workflow + yaml) is recorded on a `task_node_run_info` row
for the root node (`node_id == task_id`, `retry=0`).

## 2. Locked decisions

| # | Decision | Resolution |
|---|---|---|
| D1 | workflow `session_id` | **Surface a real session_id.** `OpenApiBotPort.send_message` returns `BotSendResult{run_id, session_id}` (the `SingleboxEngineAdapter` surfaces the WS session it already creates; `OpenApiBotAdapter` best-effort). The dynamic single-bot path reads `.run_id`. |
| D2 | yaml `session_id` | **One engine method.** `ExecutionEngine.start_coop_group(gf) -> CoopGroupStart{group_id, session_id}` does `form_coop_group(gf)` then fetches the session by default. A runner helper `TaskRunner.get_group_session(group_id)` reads the stashed `_group_meta[…]["session_id"]`; if `None` (e.g. state_machine), calls `bcs.create_session(group_id)`. |
| D3 | Persistence scope | Persist **root `task_node` (status=RUNNING) + `task_node_run_info` (retry=0, session_id)**. Leave `task_info.status = PENDING` (consistent with `dynamic` — nothing drives `task_info.status` today). |
| D4 | `collab_mode` (yaml) | `"state_machine"` if `execution_config["yaml"]` is present, else `"manager_worker"` (the BCS-recognized value). |
| D5 | Branch point | After the existing `task_info` persist + `initialize_graph` (which creates the in-memory root node — the callback target), read `task_type`. |
| D6 | Await semantics | `workflow`/`yaml` are **awaited inline** in `execute` (the action must produce the `session_id` before persisting). `dynamic` stays fire-and-forget (background `on_execute`). |
| D7 | task_runner seam | `execute` reaches `task_runner` via **new `ExecutionEngine` methods** (`trigger_single_bot_workflow`, `start_coop_group`); the engine delegates to its runner/ports. `task_runner` stays the implementation (identity resolution, poller/session). |
| D8 | DI | Inject `TaskNodeRepositoryProtocol` + `TaskNodeRunInfoRepositoryProtocol` into `TaskService` (via `task_module` provider, like `task_info_repo`). |

**Assumed (object unless raised):**
- `args` key carries 参数列表: message = `/{workflow_id} ` + `" ".join(args)`,
  `args = execution_config.get("args", [])`; `workflow_id = execution_config["workflow_id"]`.
- yaml group bots: `bot_ids = [owner_bot_id] + execution_config.get("participant_bot_ids", [])`;
  `group_name = execution_config.get("group_name", f"task-{task_id}")`;
  `extend_props["definition_yaml"] = execution_config["yaml"]`.
- Profiles with `bot`/`bcs` = `None` (community stub) degrade gracefully (new
  engine methods return stub results), mirroring the existing `on_execute` degrade.
- Callbacks unchanged: the bot/BCS reports back via the existing `on_report`
  flow keyed by `loop_task_id = {task_id}::{task_id}`; the in-memory root node
  is the target.

## 3. Architecture & data flow

```
execute(request: TaskInfoRequest)
  task_id = provider(); task_info = request.to_task_info(task_id)
  persist task_info (PENDING)                                   # existing
  graph = initialize_graph(task_info)                           # existing — in-mem root (PENDING), callback target
  task_type = request.execution_config["task_type"]
  ┌─ dynamic  → harness.register + asyncio.create_task(engine.on_execute(task_id))   # existing, fire-and-forget
  ├─ workflow → await engine.trigger_single_bot_workflow(task_id, owner_bot_id, msg) # new
  │             → BotSendResult{run_id, session_id}
  └─ yaml     → gf = GroupFormation(...); await engine.start_coop_group(gf)          # new
                → CoopGroupStart{group_id, session_id}
  # workflow/yaml: set in-mem root (run_mode/assignee/RUNNING) + persist task_node + task_node_run_info(session_id)
  return TaskOpResult(task_id, success, run_id=graph.run_id)
```

`workflow`/`yaml` reuse the in-memory graph from `initialize_graph` (the root
node is the callback target for later `on_report`). They additionally persist
the session_id to the DB. This is the scope-B overlap: in-memory graph remains
the engine/dashboard/callback SSOT; DB `task_node_run_info` is the session
record. Persisting node lifecycle to DB on callbacks stays a later scope.

## 4. Port change — `BotSendResult` (D1)

`core/task/task_runner/integration/ports.py`:
```python
@dataclass(frozen=True)
class BotSendResult:
    run_id: str
    session_id: str | None = None

class OpenApiBotPort(Protocol):
    async def send_message(self, *, bot_id: str, message: str,
                           metadata: dict[str, Any] | None = None) -> BotSendResult: ...
```
- `SingleboxEngineAdapter.send_message` (and the double adapter): surface the
  internal WS session id as `session_id` (it already creates one in
  `_create_session`).
- `OpenApiBotAdapter.send_message`: `session_id=None` (or the message-derived
  id if the upstream returns one).
- `TaskExecutor._dispatch_single_bot` (dynamic path): read `.run_id` from the
  result; extend `SingleBotHandle` with an optional `session_id` (carried but
  not required by the dynamic path).

`core/repository/README.md` `internal_dependencies` and any boundary guards:
adding `BotSendResult` to `ports.py` is additive; verify
`test_module_boundaries` stays green.

## 5. Runner additions

`core/task/task_runner/runner.py` (facade `TaskRunner`):
```python
async def trigger_workflow(self, *, bot_id: str, message: str,
                           metadata: dict | None = None) -> BotSendResult:
    if self._execution_backend is not None:
        return await self._execution_backend.trigger_workflow(bot_id=bot_id, message=message, metadata=metadata)
    # stub: no backend
    return BotSendResult(run_id=f"stub_{uuid.uuid4().hex[:8]}", session_id=None)

async def get_group_session(self, group_id: str) -> str | None:
    if self._execution_backend is not None:
        return await self._execution_backend.get_group_session(group_id)
    return None  # stub: no backend
```
`core/task/task_runner/integration/task_executor.py` (`TaskExecutor`):
```python
async def trigger_workflow(self, *, bot_id, message, metadata=None) -> BotSendResult:
    res = await self._bot.send_message(bot_id=bot_id, message=message, metadata=metadata or {})
    biz_task_id = (metadata or {}).get("biz_task_id", "")
    self._poller.register(SingleBotHandle(
        loop_task_id=f"{biz_task_id}::{biz_task_id}",   # root node_id == task_id
        run_id=res.run_id, bot_id=bot_id, session_id=res.session_id,
        registered_at=time.monotonic(),
    ))
    return res

async def get_group_session(self, group_id: str) -> str | None:
    meta = self._group_meta.get(group_id)
    sid = meta.get("session_id") if meta else None
    if sid is None and self._bcs is not None:
        sid = await self._bcs.create_session(group_id)   # initial session for state_machine / absent
    return sid
```
(`TaskExecutor` is the `execution_backend`; it already holds `_bot`, `_bcs`,
`_poller`, `_group_meta`.)

## 6. Engine additions (D7) — `core/task/task_center/engine.py`

```python
@dataclass(frozen=True)
class CoopGroupStart:
    group_id: str
    session_id: str | None

class ExecutionEngine:
    async def trigger_single_bot_workflow(self, *, task_id: str, bot_id: str,
                                          message: str) -> BotSendResult:
        return await self._runner.trigger_workflow(bot_id=bot_id, message=message,
                                                   metadata={"biz_task_id": task_id})

    async def start_coop_group(self, gf: GroupFormation) -> CoopGroupStart:
        group_id = await self._runner.form_coop_group(gf)
        session_id = await self._runner.get_group_session(group_id)
        return CoopGroupStart(group_id=group_id, session_id=session_id)
```
`CoopGroupStart` lives in the engine module (or `task_runner`); `BotSendResult`
imported from `ports.py`. The engine already holds `self._runner`.

## 7. `execute` branching — `core/task/task_center/task_service.py`

After `initialize_graph` (line ~108) and before the dynamic `on_execute`
scheduling, branch on `task_type`. New helpers `_run_workflow(...)` and
`_run_yaml(...)`:
- Both set the in-memory root node: `run_mode`, `assignee`, `status=RUNNING`
  via `self._graph.update_task_node_info(TaskNodePatch(task_id=task_id,
  node_id=task_id, status=Status.RUNNING, run_mode=..., assignee=...))`.
- Both persist:
  - `self._task_node_repo.insert(TaskNodeRecord(id=0, task_id=task_id,
    node_id=task_id, task_spec=task_info.task_spec.to_dict(), status=Status.RUNNING))`.
  - `self._run_info_repo.insert(TaskNodeRunInfoRecord(id=0, node_id=task_id,
    task_id=task_id, run_mode=..., assignee=..., session_id=..., retry=0,
    start_time=<now_ms>, update_time=<now_ms>, output=None,
    acceptance_result=None, extend_props=None, end_time=None))`.

### `_run_workflow`
```python
wf_id = request.execution_config.get("workflow_id")
args = request.execution_config.get("args", [])
message = f"/{wf_id} " + " ".join(args)
res = await self._engine.trigger_single_bot_workflow(task_id=task_id,
                                                      bot_id=request.owner_bot_id,
                                                      message=message)
# in-mem root: run_mode="single_bot", assignee=owner_bot_id, RUNNING
# persist task_node + run_info(session_id=res.session_id)
```

### `_run_yaml`
```python
from agentclaw.community.core.task_dispatch.strategies import GroupFormation
ec = request.execution_config
has_yaml = bool(ec.get("yaml"))
gf = GroupFormation(
    bot_ids=[request.owner_bot_id, *ec.get("participant_bot_ids", [])],
    collab_mode="state_machine" if has_yaml else "manager_worker",
    group_name=ec.get("group_name", f"task-{task_id}"),
    members_info=[], extend_props={"definition_yaml": ec.get("yaml")},
)
start = await self._engine.start_coop_group(gf)
# in-mem root: run_mode="coop_group", assignee=start.group_id, RUNNING
# persist task_node + run_info(session_id=start.session_id)
```

### dynamic (unchanged)
```python
if self._harness is not None: self._harness.register(task_id)
bg = asyncio.create_task(self._engine.on_execute(task_id))
self._bg_tasks.add(bg); bg.add_done_callback(self._on_bg_done)
```

## 8. DI wiring — `di/modules/task_module.py`

The `task_service` provider `@inject`s two more ports and forwards them:
`task_node_repo: TaskNodeRepositoryProtocol`, `task_node_run_info_repo:
TaskNodeRunInfoRepositoryProtocol` (both bound by `TaskPersistenceModule`).
`TaskService.__init__` stores them as `self._task_node_repo` /
`self._run_info_repo`.

## 9. Boundary / layering

- `core/task/task_center` imports: `core.repository.protocols.task` (two more
  protocols), `core/task/repository/types` (`TaskNodeRecord`,
  `TaskNodeRunInfoRecord`), `core/task_dispatch.strategies` (`GroupFormation`),
  `core/task_runner/integration/ports` (`BotSendResult`). If
  `test_module_boundaries` trips, add the entries to `core/task`'s
  `internal_dependencies` in its `README.md` (mirror the prior `plugins/local`
  / `api/README` fixes).
- The engine already depends on `task_runner`/`task_dispatch`; the new methods
  stay inside that existing dependency.

## 10. Tests

- **Port/runner unit**: `trigger_workflow` returns `BotSendResult{run_id,
  session_id}` (stub backend + a fake bot port); `get_group_session` returns
  `_group_meta` session, and calls `create_session` when absent (fake bcs).
- **execute dynamic**: unchanged — existing `test_task_service` / `test_e2e`
  still pass (they construct `TaskService` with `task_info_repo`/provider;
  now also need `task_node_repo`/`run_info_repo` — inject fakes or real SQLite).
- **execute workflow**: `task_type=workflow`, a fake engine returning a
  `BotSendResult{session_id="ws-x"}`; assert an in-memory root flip to
  RUNNING/single_bot/owner_bot_id AND a `task_node_run_info` row with
  `session_id="ws-x"` (real SQLite repo) AND a root `task_node` row.
- **execute yaml**: `task_type=yaml` with a yaml; fake engine returning
  `CoopGroupStart{group_id, session_id}`; assert run_info row
  (`run_mode=coop_group`, `assignee=group_id`, `session_id`) + `task_node`;
  `collab_mode=state_machine` when yaml present, `manager_worker` otherwise
  (assert the `GroupFormation` passed to the fake engine).
- The existing test facade (`_CaseTaskService`) overrides `_build_engine`; the
  new engine methods must be stubbable — tests inject a fake engine or override
  the two new methods.

## 11. Out of scope

- Persisting node lifecycle to DB on callbacks (workflow/yaml result reports)
  — still scope B.
- Driving `task_info.status` (stays PENDING for all task_types).
- Changes to the callback/PUSH flow (reused as-is).
- `bbs` run_mode (not a task_type here).

## 12. Validation

- New + updated tests green; `tests/community/core/task` no regression
  (dynamic path unchanged).
- Full `tests/community` green (incl. `test_module_boundaries`,
  `test_repository_contracts`).
- flake8/antflake on touched files clean.

## 13. Compatibility and risk

- **`OpenApiBotPort.send_message` return type changes** (`str` →
  `BotSendResult`) — breaks any caller reading it as `str`. Known callers:
  `TaskExecutor._dispatch_single_bot` (updated) + the singlebox/double/openapi
  adapters (updated). Grep to confirm no others.
- `execute` now blocks on the bot/BCS call for `workflow`/`yaml` (await inline)
  — different latency profile than `dynamic`'s fire-and-forget; acceptable
  since the `session_id` is needed before return.
- Two new required DI params on `TaskService` (`task_node_repo`,
  `task_node_run_info_repo`) — all test constructors of `TaskService`/
  `_CaseTaskService` must pass them (fakes or real).
- `collab_mode` `manager_worker` (not `master_worker`) — confirmed against
  `task_dispatch/strategies.py`; flag if the upstream BCS expects otherwise.
- In-memory/DB dual-write for workflow/yaml (in-mem graph = SSOT for
  callbacks/dashboard; DB = session record) — acknowledged scope-B overlap.