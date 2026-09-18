# Implementation Plan — task_trajectory → task_context sub-module + task_context_service relay

> Companion to `spec.md` (read first for decisions D1–D4 + propagation). Ordered into phases P0–P8, each
> independently verifiable. Run all tests with `src/backend/.venv/bin/python -m pytest` (repo-mandated venv python).

## P0 — Prep & baseline (no code change)

1. `git -C /Users/jian.jiangj/Git/Avernet rev-parse HEAD` → record (expect `714b851e…`). Confirm working tree clean.
2. Baseline: run the affected suites green so regressions are attributable:
   - `src/backend/.venv/bin/python -m pytest
     src/backend/tests/community/core/task/task_trajectory
     src/backend/tests/community/adapters/http/task/test_trajectory_endpoint.py
     src/backend/tests/community/architecture
     -q`
   - Record pass count (memory: trajectory feature suite ~4101 green). (The 7 `test_bbs_runner` failures are
     pre-existing/out-of-scope; exclude.)
3. Confirm `test_architecture_compliance.py` passes with no trajectory exceptions today (§8 of spec).

## P1 — Physical move + absolute-import rewrite (D1)

> Mechanical. Everything stays under `agentclaw.community.core.task` so the boundary whitelist is unaffected
> (only `api/README.md:166` needs a path update, handled in P6).

1. `git mv src/backend/src/agentclaw/community/core/task/task_trajectory
       src/backend/src/agentclaw/community/core/task/task_context/task_trajectory`
   (preserves history; the empty `task_context/__init__.py` already exists).
2. Absolute import rewrite — replace every occurrence of
   `agentclaw.community.core.task.task_trajectory` → `agentclaw.community.core.task.task_context.task_trajectory`
   across `src/backend` (src + tests). One pass:
   `git grep -l "agentclaw.community.core.task.task_trajectory" -- 'src/backend'` → edit each (Edit/replace_all).
   Known hit list (from grep): `task_trajectory/{analyzer,assembler,payloads,trajectory_service}.py` (self-refs),
   `task_center/{engine,task_service}.py`, `task_dispatch/{rationale,strategies}.py`,
   `task_runner/callback_adapter.py`, `di/modules/task_persistence_module.py`,
   `tests/community/core/task/task_trajectory/test_{analyzer,trajectory_assembler,trajectory_e2e_acceptance,
   trajectory_emitter,trajectory_gates_dispatch,trajectory_gates_submit,trajectory_gates_transition,
   trajectory_intrusion_guard,trajectory_models,trajectory_service}.py`,
   `tests/community/adapters/http/task/test_trajectory_endpoint.py`.
3. **Do NOT yet** touch `api/task/task_trajectory_service.py` (its model import) — handled in P2 with the protocol
   delete. (Or path-update it now and delete in P2 — either is fine; deleting in P2 avoids a transient duplicate.)
4. Verify imports resolve: `src/backend/.venv/bin/python -c "import agentclaw.community.core.task.task_context.task_trajectory.trajectory_service"`
   and the same for `…payloads`, `…models`, `…assembler`, `…analyzer`. Run P0 suites again — must still be green
   (pure move + import rewrite; no behavior change).

## P2 — Internalize trajectory contract + add emit methods (D3 first half)

1. **Relocate `TaskTrajectoryServiceProtocol`** from `api/task/task_trajectory_service.py` into the moved
   `core/task/task_context/task_trajectory/trajectory_service.py` (define it above `TaskTrajectoryService`),
   path-updating its `from …models import TaskTrajectory` and declaring all THREE methods
   (`get_trajectory` async + `emit_trajectory_event` + `emit_submit_trajectory` sync) — since
   `TaskContextService` will depend on this internal protocol for both read and write.
2. Make `class TaskTrajectoryService(TaskTrajectoryServiceProtocol)` inherit it (structural today → explicit
   inheritance; signatures already match for `get_trajectory`; the new emit methods match).
3. Add the two emit methods to `TaskTrajectoryService` (spec §5): alias the free funcs
   (`from .payloads import emit_trajectory_event as _emit_trajectory_event`,
   `from .payloads import emit_submit_trajectory as _emit_submit_trajectory`) and thin-wrap with `self._repo`.
   The free funcs in `payloads.py` stay byte-for-byte (their no-op-if-None + swallow+warn unchanged).
4. **DELETE `api/task/task_trajectory_service.py`** (old protocol-in-api). Its only importers are the 2 routers +
   `task_persistence_module` + the endpoint test — all re-pointed in P4/P5/P7.
5. Verify: `pytest tests/community/core/task/task_trajectory/test_trajectory_service.py -q` green (the impl-test
   still constructs `TaskTrajectoryService` directly; the new emit methods are exercised by P7 additions).

## P3 — New `task_context_service` facade + external contract (D3 second half)

1. Create `core/task/task_context/task_context_service.py`:
   - `class TaskContextServiceProtocol(Protocol)` with the 3 method signatures (spec §5); import `TaskTrajectory`
     for the `get_trajectory` hint from `…task_context.task_trajectory.models`.
   - `class TaskContextService(TaskContextServiceProtocol)`: `@inject __init__(self, trajectory_service:
     TaskTrajectoryServiceProtocol)`; store `self._ts`; relay all three (emit via `self._ts.emit_*(…)`;
     `get_trajectory` via `return await self._ts.get_trajectory(…)`). No repo field.
2. Create `api/task/task_context_service.py`: docstring + `from
   agentclaw.community.core.task.task_context.task_context_service import TaskContextServiceProtocol` (re-export
   only) — matches `api/` convention (one protocol per file, re-export of the owning core module's Protocol).

## P4 — DI wiring (§6 of spec)

1. `di/modules/task_persistence_module.py`:
   - Path-update the moved-module imports (from P1 they're already `…task_context.task_trajectory.*`).
   - Re-point `TaskTrajectoryServiceProtocol` import from `api/task/task_trajectory_service` → the relocated
     internal `…task_context.task_trajectory.trajectory_service`.
   - Keep the existing `task_trajectory_service` provider (`TaskTrajectoryServiceProtocol → TaskTrajectoryService(…)`).
   - Add: `from …task_context.task_context_service import TaskContextService`; `from
     agentclaw.community.api.task.task_context_service import TaskContextServiceProtocol`; add provider
     `def task_context_service(self, injector) -> TaskContextServiceProtocol: return
     TaskContextService(trajectory_service=injector.get(TaskTrajectoryServiceProtocol))` (`@singleton @provider`).
   - Import `TaskContextService` into `core/...` (allowed: api re-exports, core→core for the concrete impl used only
     in the composition root — composition-root-uses-concrete is permitted by Rule 5/14).
2. `di/modules/task_module.py`:
   - Remove `from …core.repository.protocols.task import TaskTrajectoryRepositoryProtocol` (no longer used here).
   - Replace the `trajectory_repo = injector.get(TaskTrajectoryRepositoryProtocol)` try/except (lines ~284-291) with
     a `task_context_svc = injector.get(TaskContextServiceProtocol)` try/except (preserve the INFO-on-unbind log);
     `None` on unbind.
   - Pass `task_context_service=task_context_svc` into the `TaskService(…)` provider (replace `trajectory_repo=…`).
3. Verify DI wiring: `python -c "import agentclaw.community.di.container as c; c.build_injector()"` (or the existing
   DI smoke test) — must construct without error.

## P5 — Consumer reroute (§7 of spec)

1. `adapters/http/task/router.py` + `adapters/http/openapi_v1/task/router.py`:
   `from agentclaw.community.api.task.task_context_service import TaskContextServiceProtocol`; the `Injected(...)`
   default re-points to the new protocol; the `await service.get_trajectory(...)` call is unchanged. Remove the
   old `from …task_trajectory_service import …`. (Keep the two routers in sync — "改其一须同步".)
2. `task_center/engine.py`:
   - ctor `trajectory_repo: "TaskTrajectoryRepositoryProtocol | None" = None` → `task_context_service:
     "TaskContextServiceProtocol | None" = None`; `self._trajectory_repo = …` → `self._task_context_service = …`.
   - Drop `from …trajectory.payloads import emit_trajectory_event`. Keep `from …models import ReasonCatalog` (path
     already updated in P1) + the TYPE_CHECKING models import (P1).
   - `_log_trajectory` (line ~991): wrap `if self._task_context_service is not None:
     self._task_context_service.emit_trajectory_event(task_id, node_id, action_type, action_result=…, …)` (same
     kwargs as today, minus the leading `repo` arg). Update the `_log_trajectory` docstring comment about no-guard.
3. `task_center/task_service.py`:
   - ctor `trajectory_repo` → `task_context_service`; store `self._task_context_service`; forward to
     `TaskLoopCallback(..., task_context_service=self._task_context_service)` (`:134`) and `_build_engine`
     (`trajectory_repo=self._trajectory_repo` → `task_context_service=self._task_context_service` at `:166`).
   - Drop `from …payloads import emit_submit_trajectory`.
   - `execute` (`:349`): `if self._task_context_service is not None:
     self._task_context_service.emit_submit_trajectory(task_id, task_info, submitted_at_ms=int(time.time()*1000))`.
4. `task_runner/callback_adapter.py`:
   - Verify (on edit) how `TaskLoopCallback` (`:359`) and `CallbackAdapter` (`:371`) thread the repo — update BOTH
     the ctor param(s) + any internal forwarding to `task_context_service`. Store `self._task_context_service`.
   - Drop `from …payloads import emit_trajectory_event`; keep `ReasonCatalog`.
   - `_emit_parse_trajectory` (`:548`): the existing `:542` guard `if self._trajectory_repo is None: return` →
     `if self._task_context_service is None: return`; the call → `self._task_context_service.emit_trajectory_event(
     task_id, node_id, action_type, action_result=…, …)` (minus `repo`).
5. Verify the consumers compile + behavior unchanged: run the endpoint tests + the trajectory gate tests + a
   focused engine/callback task test.

## P6 — Doc / context-boundary updates (Rule 22 / §8 of spec)

1. `community/api/README.md`:
   - `internal_dependencies` whitelist line 166: `agentclaw.community.core.task.task_trajectory.models` →
     `agentclaw.community.core.task.task_context.task_trajectory.models`; comment
     `# TaskTrajectory — typed in task_context_service.py Protocol get_trajectory signature`.
   - Any `task_trajectory_service` file reference → `task_context_service` (search the file).
2. `community/core/task/README.md`:
   - Update the `core/task/ 目录树` so `task_context/` lists `task_graph_service.py` + `task_context_service.py` +
     the `task_trajectory/` sub-package (with its existing file rows); remove `task_trajectory/` from the peer
     list under `core/task/`. Add a one-line note: `task_context_service` is the single 对外 entry for trajectory
     read+write; `task_trajectory/` is an internal sub-module.

## P7 — Tests (import re-points + new facade tests)

1. Re-point imports in the ~10 trajectory test files (already done mechanically in P1's path-rewrite — verify each
   resolves; fix any test that imported the deleted `api.task.task_trajectory_service`):
   - `test_trajectory_endpoint.py`: re-target the stub from `TaskTrajectoryServiceProtocol` →
     `TaskContextServiceProtocol` (router dep changed). Rename `_StubTrajectoryService` → `_StubTaskContextService`
     exposing `get_trajectory` (+ optional no-op `emit_*`); the DI binding re-points to
     `TaskContextServiceProtocol`.
   - `test_trajectory_service.py` / `test_trajectory_e2e_acceptance.py`: still construct `TaskTrajectoryService`
     directly (impl moved, not its shape) — imports path-fixed in P1. Add assertions covering the new
     `TaskTrajectoryService.emit_trajectory_event` / `emit_submit_trajectory` (fire-and-forget no-op on `None`
     repo + emits on a fake repo).
2. (Optional, mirroring source) `git mv tests/community/core/task/task_trajectory
   tests/community/core/task/task_context/task_trajectory` so the test tree mirrors the source tree. Imports
   inside resolve regardless of test path (they're absolute). Skip if it complicates test discovery.
3. NEW `tests/community/core/task/task_context/test_task_context_service.py`:
   - relay: `get_trajectory(do_analysis=False/True)` delegates to a fake `TaskTrajectoryService` (assert the
     inner `get_trajectory` was invoked with the same args; analysis errors propagate).
   - relay: `emit_trajectory_event` / `emit_submit_trajectory` delegate (assert inner invoked, args forwarded,
     repo arg dropped).
   - construct `TaskContextService(trajectory_service=fake)` without a repo (facade holds no repo).
   - one happy + one failure path per protocol-contract-tests intent (Rule 25 spirit; Service API is not arch-gated,
     but a real suite is still required by Rule 1/3).
4. NEW `Noop`/`None`-guard behavior: in the engine/task_service/callback_adapter tests that already run with
   `trajectory_repo=None`, confirm emission is still a no-op (now via `task_context_service=None` + the guard).

## P8 — Verification (evidence before claiming done)

1. `git rev-parse HEAD` → confirm still `714b851e…` (no auto-pull disrupt; if changed, re-verify edits survived).
2. Arch gates:
   `src/backend/.venv/bin/python -m pytest
     src/backend/tests/community/architecture/test_module_boundaries.py
     src/backend/tests/community/architecture/test_architecture_compliance.py
     src/backend/tests/community/architecture/test_protocol_contracts.py -q` → green.
3. Affected backend suites via venv python:
   `src/backend/.venv/bin/python -m pytest
     src/backend/tests/community/core/task/task_trajectory
     src/backend/tests/community/core/task/task_context
     src/backend/tests/community/core/task/task_center
     src/backend/tests/community/core/task/task_runner
     src/backend/tests/community/core/task/task_dispatch
     src/backend/tests/community/adapters/http/task/test_trajectory_endpoint.py
     -q` → green, count ≥ P0 baseline (excluding the 7 pre-existing `test_bbs_runner` failures, out-of-scope).
4. Format/lint (the repo's pre-push SAST/lint gate inputs): run the backend SAST/lint the AGENTS.md references for
   changed Python modules; report what was/wasn't run.
5. Report: green suites + counts, the two fork decisions D1/D2 executed (D3/D4 as chosen), unchanged external
   behavior, HEAD preserved. Do **not** commit/push unless asked (branch kept local + the 27 commits preserved).

## File inventory (single source for the diff)

NEW: `core/task/task_context/task_context_service.py`; `api/task/task_context_service.py`;
`tests/community/core/task/task_context/test_task_context_service.py`.
MOVED: `core/task/task_trajectory/` → `core/task/task_context/task_trajectory/` (5 files: `__init__, models, payloads,
assembler, analyzer, trajectory_service`); [+ tests dir optionally].
DELETED: `api/task/task_trajectory_service.py`.
EDIT (imports/DI/wiring/docs): `di/modules/task_persistence_module.py`, `di/modules/task_module.py`,
`adapters/http/task/router.py`, `adapters/http/openapi_v1/task/router.py`, `task_center/{engine,task_service}.py`,
`task_runner/callback_adapter.py`, `api/README.md`, `core/task/README.md`, + ~10 test files (import re-points +
endpoint stub re-target).
