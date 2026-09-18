# Tasks — task_trajectory → task_context sub-module + task_context_service relay

> Execution checklist for `spec.md` / `plan.md`. All phases DONE 2026-09-18.

- [x] **P0** — HEAD verified (`714b851e`), venv python confirmed; affected baseline 548 green.
- [x] **P1** — `git mv task_trajectory/ → task_context/task_trajectory/`; absolute-import rewrite
      (`agentclaw.community.core.task.task_trajectory` → `...task_context.task_trajectory`) across 22 files.
- [x] **P1-fix** — widened `api/README.md` `internal_dependencies` to broad `task_context` prefix →
      `test_module_boundaries` green (the only whitelist that listed the specific old path).
- [x] **P2** — internalized `TaskTrajectoryServiceProtocol` into the moved `trajectory_service.py`
      (declares get_trajectory + emit_*); `TaskTrajectoryService` inherits it; added `emit_trajectory_event`
      / `emit_submit_trajectory` methods (wrap `payloads.*` with `self._repo`, preserve decision-#14 no-op+swallow).
- [x] **P3** — created `core/task/task_context/task_context_service.py` (`TaskContextServiceProtocol` +
      `TaskContextService` pure-relay facade, holds no repo) + `api/task/task_context_service.py` re-export.
- [x] **P4** — DI: `task_persistence_module` binds internal `TaskTrajectoryServiceProtocol` + new
      `task_context_service` provider (`TaskContextServiceProtocol → TaskContextService`); `task_module`
      repo-try/except → `TaskContextServiceProtocol` try/except (None + INFO on unbind), passed into `TaskService`.
- [x] **P5** — rerouted consumers: 2 HTTP routers `Injected(TaskContextServiceProtocol)`;
      `engine.py` / `task_service.py` / `callback_adapter.py` ctors `trajectory_repo` → `task_context_service`
      (`| None`), emit sites `if svc is not None: svc.emit_*(…)` (decision-#14 no-op preserved); dropped the
      `payloads.emit_*` imports (kept `ReasonCatalog` type imports).
- [x] **P6** — `core/task/README.md` dir tree updated (`task_context/` now houses `task_context_service.py`
      + the `task_trajectory/` sub-package; removed peer `task_trajectory/` entry). `api/README.md` whitelist done in P1.
- [x] **P7** — tests: added the `_task_context_support.py` `_tcs(repo)` helper (wraps a repo as a
      `task_context_service`, relays via the same `payloads.*` free funcs; `None→None` for the unbound guard);
      the 7 gate-test subclasses pass `task_context_service=_tcs(trajectory_repo)` to `super()` (call-sites
      unchanged); renamed the direct `_CaseTaskService` / `TaskLoopCallback` constructions; re-pointed the
      endpoint test stub to `TaskContextServiceProtocol`; added `test_task_context_service.py` (relay
      read/analysis + 503/504 propagation + emit forwarding + protocol conformance); deleted
      `api/task/task_trajectory_service.py` (orphaned, no remaining importers).
- [x] **P8** — verification: 1092 passed / 5 skipped / 0 failed across `task_trajectory` + `task_context` +
      `task_center` + `task_runner` + `task_dispatch` + endpoint + **all architecture gates**
      (`module_boundaries`, `architecture_compliance`, `protocol_contracts`); HEAD still `714b851e`;
      ruff found zero new issues (only pre-existing F401/E402 the repo tolerates; my imports are all used).
- [ ] **commit/push** — NOT done (user keeps branch local + the 27 prior commits as-is; push/PR to `dev` on request).
