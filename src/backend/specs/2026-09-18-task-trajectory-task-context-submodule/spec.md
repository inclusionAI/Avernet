# Task Trajectory as a `task_context` Sub-module + `task_context_service` Relay Facade

> Refactor design for the existing **task_trajectory** feature (`specs/2026-09-16-task-trajectory-collection-and-analysis/`)
> so it is owned by `task_context` and **all** external trajectory read/write goes through a new
> `task_context_service` that relays to `trajectory_service` (impl stays).
>
> Branch: `task_iteration_0917_dev` (27 unpushed feature commits `dff21801e…714b851e` — preserved as-is; this refactor
> adds on top, no push without user request). Today: 2026-09-18.

## 1. Background — what exists today

`task_trajectory` is currently a **peer** package `core/task/task_trajectory/` (sibling of `task_context/`,
`task_center/`, `task_dispatch/`, `task_runner/` under `core/task/`). Its external contract
`TaskTrajectoryServiceProtocol` lives in `api/task/task_trajectory_service.py` — the **old** "Protocol in `api/`"
pattern the repo migrated **away** from (see `api/README.md`: defining Protocols in `api/` forced core→api
exceptions in `test_architecture_compliance.py`; the preferred pattern is *Protocol in its owning core module,
`api/` re-exports, concrete service inherits*).

Two external access channels exist today:

- **Read/analysis** — `TaskTrajectoryService.get_trajectory(task_id, *, do_analysis=False) -> TaskTrajectory`,
  called by 2 HTTP routers, each `Injected(TaskTrajectoryServiceProtocol)`:
  - `adapters/http/task/router.py` (`GET /api/v1/collaboration/tasks/trajectory`)
  - `adapters/http/openapi_v1/task/router.py` (`GET /openapi/v1/collaboration/tasks/trajectory`, clone — "改其一须同步")
  - `do_analysis=False` = pure read; `True` = read + bot-analysis backfill (a write; 503 if bot unconfigured, 504 on bot failure).
- **Write/emit** — fire-and-forget event emission (decision #14): 3 holders each hold a **raw
  `TaskTrajectoryRepositoryProtocol`** (plumbed via `task_module.py` try/except, `… | None = None`) and call
  **free functions** in `task_trajectory/payloads.py` that no-op when the repo is `None`:
  - `task_center/engine.py` — `_log_trajectory` → `emit_trajectory_event` (PLAN/EXECUTE/VERIFY/RESET/TRANSITION gates)
  - `task_center/task_service.py` — `execute` → `emit_submit_trajectory` (SUBMIT gate)
  - `task_runner/callback_adapter.py` — `_emit_parse_trajectory` → `emit_trajectory_event` (parse_error); also
    `TaskLoopCallback` (same file, `:359`) receives `trajectory_repo` in its ctor and forwards it (verify-on-edit).

`task_context/` today contains only `task_graph_service.py` (`TaskGraphService` — the "任务图谱 SSOT") + an empty
`__init__.py`. The user wants `task_trajectory` to become a **sub-module of `task_context`**, with a new
**`task_context_service`** as the single entry for **all** external trajectory read/write, **relaying to
`trajectory_service`** (impl + repo + emit helpers stay, become internal).

## 2. Goals

1. Make `task_trajectory` a **physical** sub-package of `task_context` (D1).
2. Add `task_context_service` (facade) + its external Service API contract; **all** external read+write of trajectory
   data go through it (D2, includes the emit path), relaying to `trajectory_service`.
3. `trajectory_service` (impl) + repo + `payloads.emit_*` stay as **internal** collaborators; add `emit_*` methods to
   `TaskTrajectoryService` so the facade can relay writes through it (centralizes emission in the trajectory module).
4. Preserve behavior exactly: HTTP responses, decision #14 fire-and-forget no-op + INFO-on-unbind, 503/504 analysis
   mapping, the full green feature suite.
5. Align with `docs/arch/arch.rules.md` (Rules 3/5/8/16/19/22) + the repo's preferred "Protocol in core / api/
   re-export / impl inherits" pattern (`api/README.md`).

## 3. Decisions (made on the user's behalf pending confirmation — alternatives in §9)

- **D1 — module layout = physical MOVE.** `task_trajectory/` → `task_context/task_trajectory/`. The user said
  "task_trajectory 是 task_context 的一个子模块功能…在 task_context 中" → nesting is the literal reading.
  Cost: rewrite every absolute import `agentclaw.community.core.task.task_trajectory.X` →
  `…task_context.task_trajectory.X` across the moved files, DI, and ~10 test files (mechanical).
- **D2 — write path IN scope.** The user said "读写" (read AND write). Route the emit path
  (engine/task_service/callback_adapter) through `task_context_service`, not just the 2 routers. The raw
  `TaskTrajectoryRepositoryProtocol` handle stops being held outside the trajectory module.
- **D3 — contract location = Protocol-in-core + api/ re-export + inheritance** (repo preferred pattern).
  - NEW `TaskContextServiceProtocol` **defined in** `core/task/task_context/task_context_service.py`,
    **re-exported by** `api/task/task_context_service.py`; `class TaskContextService(TaskContextServiceProtocol)`
    inherits it (real signatures, not duck-typed).
  - **DELETE `api/task/task_trajectory_service.py`** (old protocol-in-`api/` anti-pattern). **Relocate
    `TaskTrajectoryServiceProtocol` INTO the moved package** (`core/task/task_context/task_trajectory/`, co-located
    with the impl); `TaskTrajectoryService` inherits it; `TaskContextService` depends on
    `TaskTrajectoryServiceProtocol` (internal, DI) — Rule 5 compliant (no core→concrete dependency).
- **D4 — optional capability / `None` (decision #14) = keep `| None` + per-site guards** (minimal, matches the
  local style already in `engine.py`/`task_service.py`/`callback_adapter.py`). Consumers keep
  `task_context_service: TaskContextServiceProtocol | None = None`; emit sites `if svc is not None: svc.emit_*(…)`
  (`None` is the intentional "capability off" state — AGENTS.md allows `T | None` for intentional valid None).
  Routers inject the non-optional `TaskContextServiceProtocol` (DI always binds in router injectors). Preserves
  the decision-#14 no-op + the INFO log on unbind in `task_module`. Alternative: a `NoopTaskContextService` fallback
  (non-None end-to-end) — structurally cleaner, rejected for minimal diff (documented in §9).
- **Domain-TYPE imports stay direct.** `ReasonCatalog` (engine, callback_adapter), `DispatchRationale`
  (task_dispatch/rationale.py, strategies.py) are type/enum usage, **not** data read/write — only path-updated, NOT
  routed through the service (routing enums through a service would be wrong; Rule 19).

## 4. New structure

```text
core/task/task_context/
├── __init__.py
├── task_graph_service.py            # existing, unchanged
├── task_context_service.py         # NEW: TaskContextServiceProtocol + TaskContextService (facade, relays)
└── task_trajectory/                # MOVED here (was sibling core/task/task_trajectory/)
    ├── __init__.py
    ├── models.py                   # domain models (path-update inbound imports)
    ├── payloads.py                 # emit_trajectory_event / emit_submit_trajectory free funcs (kept as internal helpers)
    ├── analyzer.py
    ├── assembler.py
    └── trajectory_service.py       # TaskTrajectoryServiceProtocol (relocated, internal) + TaskTrajectoryService (gains emit_* methods)

api/task/task_context_service.py    # NEW: re-export TaskContextServiceProtocol only
api/task/task_trajectory_service.py # DELETED (old protocol-in-api)
```

## 5. Service surface

`TaskContextServiceProtocol` (external, defined in core/, re-exported from api/) — 3 methods:

```python
class TaskContextServiceProtocol(Protocol):
    async def get_trajectory(self, task_id: str, *, do_analysis: bool = False) -> TaskTrajectory: ...
    def emit_trajectory_event(self, task_id, node_id, action_type, *, action_result, action_input=None,
                              error_type=None, error_msg=None, ext_info=None, status_from=None,
                              status_to=None, attempt=0, now_ms=None) -> None: ...
    def emit_submit_trajectory(self, task_id, task_info, *, submitted_at_ms, node_id=None) -> None: ...
```

- `get_trajectory` stays **async** (relays `await self._ts.get_trajectory(…)`). Sync analysis semantics + 503/504
  preserved (relayed unchanged; the `TrajectoryAnalysis*` errors propagate to the router as today).
- `emit_trajectory_event` / `emit_submit_trajectory` are **sync** (the free funcs are sync; `repo.insert_event` is
  sync) and **fire-and-forget** (decision #14 swallow+warn + no-op-if-unavailable preserved inside the trajectory
  module). The method signatures are the free-func signatures **minus the leading `repo` arg**.

`TaskTrajectoryService` gains two methods (thin wrappers over `payloads.*`, passing `self._repo`; aliases the free
funcs to avoid shadowing: `from .payloads import emit_trajectory_event as _emit_trajectory_event`):
```python
def emit_trajectory_event(self, task_id, node_id, action_type, *, action_result, ...) -> None:
    _emit_trajectory_event(self._repo, task_id, node_id, action_type, action_result=action_result, ...)
def emit_submit_trajectory(self, task_id, task_info, *, submitted_at_ms, node_id=None) -> None:
    _emit_submit_trajectory(self._repo, task_id, task_info, submitted_at_ms=submitted_at_ms, node_id=node_id)
```
The free funcs in `payloads.py` are kept as-is (their `if repo is None: return` no-op + swallow+warn unchanged), so
the tested emission logic is untouched.

`TaskContextService(TaskContextServiceProtocol)` holds `trajectory_service: TaskTrajectoryServiceProtocol` and
relays all three (no repo field — pure relay, per the user's "中转到 trajectory_service").

## 6. DI wiring changes

`di/modules/task_persistence_module.py`:
- Path-update all `agentclaw.community.core.task.task_trajectory.*` imports → `…task_context.task_trajectory.*`.
- Update the `TaskTrajectoryServiceProtocol` import to the **relocated internal** path; keep its provider binding
  `TaskTrajectoryServiceProtocol → TaskTrajectoryService(assembler, repo, analyzer, config)` (the repo/assembler/
  analyzer/config bindings are unchanged, path-updated as needed).
- **Add a new provider** binding the NEW external contract:
  `TaskContextServiceProtocol → TaskContextService(trajectory_service=injector.get(TaskTrajectoryServiceProtocol))`.
- New imports: `from agentclaw.community.core.task.task_context.task_context_service import TaskContextService`;
  `from agentclaw.community.api.task.task_context_service import TaskContextServiceProtocol`.

`di/modules/task_module.py`:
- Replace the `trajectory_repo = injector.get(TaskTrajectoryRepositoryProtocol)` try/except
  (lines ~284-291, INFO log on unbind) with `task_context_svc = injector.get(TaskContextServiceProtocol)` try/except
  → `None` on unbind (preserve the INFO log).
- Pass `task_context_service=task_context_svc` into the `TaskService(...)` provider instead of `trajectory_repo=…`.
- Drop the now-unused `TaskTrajectoryRepositoryProtocol` import from `task_module` (the repo is consumed only
  internally by `TaskTrajectoryService` via `task_persistence_module`).

## 7. Consumer changes

| File | Change |
|---|---|
| `adapters/http/task/router.py` | import `TaskContextServiceProtocol` from `api/task/task_context_service`; `Injected(TaskContextServiceProtocol)`; call `await service.get_trajectory(...)` (unchanged behavior). |
| `adapters/http/openapi_v1/task/router.py` | same (clone — keep in sync). |
| `core/task/task_center/engine.py` | ctor param `trajectory_repo: ... \| None = None` → `task_context_service: TaskContextServiceProtocol \| None = None`; store `self._task_context_service`. `_log_trajectory` calls `self._task_context_service.emit_trajectory_event(...)` (drop `repo` arg) guarded `if self._task_context_service is not None:` (preserves the existing "no guard needed because emitter no-ops" intent — now explicit). **Drop** the `from ...payloads import emit_trajectory_event` import. **Keep** the `ReasonCatalog`/`models` type imports (path-updated). |
| `core/task/task_center/task_service.py` | ctor param `trajectory_repo` → `task_context_service`; forward `task_context_service=self._task_context_service` to `TaskLoopCallback` (`:134`) and `_build_engine` → `engine` (`:166`); `execute` calls `self._task_context_service.emit_submit_trajectory(task_id, task_info, submitted_at_ms=...)` guarded. Drop the `emit_submit_trajectory` import. |
| `core/task/task_runner/callback_adapter.py` | `TaskLoopCallback` (`:359`) + `CallbackAdapter` (`:359/371` — same file) ctor params `trajectory_repo=None` → `task_context_service`; `_emit_parse_trajectory` calls `self._task_context_service.emit_trajectory_event(...)` (the existing `:542` `if self._trajectory_repo is None: return` guard becomes `if self._task_context_service is None: return`). Drop the `emit_trajectory_event` import; keep `ReasonCatalog` (path-updated). Verify both classes' forwarding on edit. |

## 8. Propagation analysis (`arch.rules.md` Rule 16)

- **Affected consumers:** 2 HTTP routers (read); 3 emit holders (`engine.py`, `task_service.py`,
  `callback_adapter.py`) + the `TaskService`/`TaskLoopCallback`/`_build_engine` plumbing chain.
- **Affected implementations:** `TaskTrajectoryService` (gains 2 emit methods + inherits relocated protocol);
  NEW `TaskContextService`.
- **Affected contracts:** DELETE external `TaskTrajectoryServiceProtocol` (api/); ADD external
  `TaskContextServiceProtocol` (api/ re-export, defined in core/); RELOCATE `TaskTrajectoryServiceProtocol` internal
  to the moved package.
- **Affected config/DI:** `task_persistence_module` (new provider + path updates + import re-points);
  `task_module` (repo→service plumbing).
- **Affected boundaries/docs:** `api/README.md` — update `internal_dependencies` whitelist line 166
  (`task_trajectory.models` → `task_context.task_trajectory.models`, comment → `task_context_service` protocol) +
  any `task_trajectory_service` file reference; `core/task/README.md` — update the `core/task/` dir tree so
  `task_context/` now houses `task_context_service.py` + the `task_trajectory/` sub-package, and remove
  `task_trajectory/` from the peer list (Rule 22 context boundary).
- **Compatibility:** EXTERNAL HTTP behavior **unchanged** (same endpoints, response shapes, 503/504 mapping,
  decision-#14 no-op). **Internal** contract change only. **No schema/migration** (tables untouched).
- **Migration:** mechanical — (a) directory move, (b) absolute import path find/replace, (c) new facade + protocol
  + re-export, (d) DI rebind, (e) emit-site reroute + ctor rename, (f) doc updates, (g) test import re-points +
  endpoint-test stub re-target. No data migration.

## 9. Alternatives rejected (re-confirm on review)

- **D1 alt (keep sibling):** lower churn, no import-path rewrite — but does not physically express sub-module
  ownership and contradicts the user's "在 task_context 中". Chose move; revisit if reviewer wants lower risk.
- **D2 alt (read-only relay):** routes only the 2 routers; leaves the emit write-path on the raw repo handle —
  contradicts "读写" and leaves the exact leak the user wants closed.
- **D4 alt (`NoopTaskContextService` fallback):** structurally cleaner (non-None service end-to-end, no per-site
  guards, aligns with AGENTS.md "non-optional end to end"). Rejected for minimal diff + match-local-style. Document
  here; cheap to switch if reviewer prefers it.
- **D3 alt (keep `TaskTrajectoryServiceProtocol` in api/):** lower churn but perpetuates the old anti-pattern +
  leaves an external contract surface that future callers could import directly (the leak the user wants to
  prevent). Chose the clean delete + relocate.

## 10. Non-goals

- Not re-homing `DispatchRationale` (consumed by `task_dispatch`) — separate refactor.
- Not touching `TaskGraphService` / `task_graph` — user scoped to trajectory data; `task_graph_service.py` unchanged.
- Not changing HTTP endpoints / response shapes / analysis bot semantics / DDL / tables.
- Not rerouting domain-type imports (`ReasonCatalog`, `DispatchRationale`) — they are type usage, not read/write.

## 11. Risks & care

- Branch `task_iteration_0917_dev` has 27 unpushed commits and **auto-pulls mid-session** (per repo memory). Verify
  HEAD before/after; **never** reset/checkout/stash/rebase/pull/push/branch. No push without explicit request.
- Keep arch tests green: `tests/community/architecture/test_module_boundaries.py` (context boundary; everything stays
  under the `agentclaw.community.core.task` prefix — whitelist unaffected apart from `api/README.md` line 166),
  `test_architecture_compliance.py` (no trajectory exceptions today — confirm after the api/ protocol deletion),
  `test_protocol_contracts.py` (Plugin-only; **out of scope** — `TaskContextServiceProtocol` is a Service API).
- 1 000-line cap: new files small; moved files unchanged in size.
- Tests run via `src/backend/.venv/bin/python -m pytest …` (stale starlette fail-closed guard — use the venv python,
  not system `python3`, per repo memory).
