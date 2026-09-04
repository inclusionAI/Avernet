# Dynamic Planner & Driver — Design Proposal (v2)

**Status:** APPROVED — Oracle-reviewed, all 9 blockers resolved
**Date:** 2026-09-03

## Oracle Review Summary

9 BLOCKERS → all resolved. 14 CONCERNS → addressed. 5 SUGGESTIONS → adopted S1 (split SPI), S2 (RunEvent callback), S5 (v1 simplification).

Key structural change from v1: **split `Planner` and `Replanner` into separate SPIs** (Oracle S1). This resolves B9 (StaticPlanner contract leak), C2 (composition coupling), and partially B1 (goal/agents access) in one stroke.

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │            community/spi/                 │
                    │  Planner (ABC)  │  Replanner (ABC)        │
                    │  Driver (ABC)   │  RunEvent / PlanExtension│
                    └────┬──────────┬──────────┬────────────────┘
                         │          │          │
          ┌──────────────┼──────────┼──────────┼──────────────┐
          ▼              ▼          ▼          ▼              ▼
   plugins/planner/  plugins/    plugins/   plugins/      plugins/
   static.py         replanner/  driver/    driver/       replanner/
   StaticPlanner     dynamic.py  static.py  dynamic.py    (future)
                     Dynamic-    Static-    Dynamic-
                     Replanner   Driver     Driver
```

## SPI Contracts

### `community/spi/planner.py` (NEW)

```python
class PlanError(RuntimeError):
    """Raised when a planner cannot produce a valid plan."""

class Planner(ABC):
    """Static planner SPI: produce a DAGPlan from a goal + agents.

    Dynamic replanning is handled by the separate Replanner SPI.
    This interface is intentionally minimal — no prior/results/errors.
    """
    @abstractmethod
    def plan(self, goal: str, agents: list[AgentSpec]) -> DAGPlan: ...
```

### `community/spi/replanner.py` (NEW — Oracle S1)

```python
class HaltReason(StrEnum):
    DONE = "done"       # goal achieved early; skip remaining → succeeded=True
    ABORT = "abort"     # goal cannot be achieved → succeeded=False
    DRIFT = "drift"     # goal drifted too far → succeeded=False

@dataclass
class PlanExtension:
    """Structured delta from the replanner LLM."""
    extensions: list[DAGNode] = field(default_factory=list)
    new_edges: list[tuple[str, str]] = field(default_factory=list)
    halt_reason: HaltReason | None = None   # None = continue (Oracle B4 fix)
    rationale: str = ""

class Replanner(ABC):
    """Dynamic replanner SPI: extend an in-flight plan based on results.

    Returns a PlanExtension delta (NOT a full plan). The driver merges it.
    """
    @abstractmethod
    def extend(
        self,
        goal: str,
        agents: list[AgentSpec],
        prior: DAGPlan,
        results: dict[str, Any],
        errors: dict[str, str],
    ) -> PlanExtension: ...
```

### `community/spi/driver.py` (NEW — Oracle B5/S2 fix)

```python
@dataclass
class RunLog:
    statuses: dict[str, NodeStatus] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)   # read-only view of plan.nodes[n].result
    errors: dict[str, str] = field(default_factory=dict)

@dataclass
class RunResult:
    plan: DAGPlan
    log: RunLog
    succeeded: bool
    final_output: Any = None

@dataclass
class RunEvent:
    """Structured event for non-node occurrences (replans, halts, etc)."""
    kind: str   # "replan_called", "replan_applied", "replan_rejected", "halt"
    extension_count: int = 0
    nodes_added: list[str] = field(default_factory=list)
    rationale: str = ""
    halt_reason: str | None = None

class Driver(ABC):
    """Driver SPI: execute a DAGPlan and return results."""
    @abstractmethod
    def run(
        self,
        plan: DAGPlan,
        *,
        on_event: Callable[[RunEvent], None] | None = None,
    ) -> RunResult: ...
```

## DAGPlan Changes (Oracle B1 fix)

Add `goal` and `available_agents` to `DAGPlan` so the dynamic driver can
re-invoke the replanner without needing `goal`/`agents` passed to `run()`:

```python
@dataclass
class DAGPlan:
    nodes: dict[str, DAGNode] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    prompt: str = ""
    goal: str = ""                                    # NEW (B1 fix)
    available_agents: list[str] = field(default_factory=list)  # NEW (B1 fix)
```

`StaticPlanner.plan()` populates `plan.goal` and `plan.available_agents`
from its args. `RunResult` has them available via `result.plan`.

## StaticDriver Template Method Refactor (Oracle C1 fix)

Before:
```python
class StaticDriver:
    def run(self, plan) -> RunResult:
        # monolithic wave loop
```

After:
```python
class StaticDriver:
    def run(self, plan, *, on_event=None) -> RunResult:
        self._on_event = on_event
        self._before_run(plan)
        remaining = list(plan.topological_order())
        succeeded = True
        while remaining:
            wave, rest = self._take_ready(plan, remaining)
            if not wave:
                self._skip_remaining(plan, remaining)
                succeeded = False
                break
            remaining = rest
            self._run_wave(plan, wave)
            succeeded = self._after_wave(plan, wave, succeeded)
        return self._after_run(plan, succeeded)

    def _before_run(self, plan): ...
    def _after_wave(self, plan, wave, succeeded) -> bool: return succeeded
    def _after_run(self, plan, succeeded) -> RunResult: ...
```

`DynamicDriver` overrides `_after_wave` to inject the replan hook —
no `run()` duplication.

## DynamicReplanner — Super Parameters (v1, Oracle S5 simplified)

| Parameter | Type | Default | Effect |
|-----------|------|---------|--------|
| `max_extensions` | int | `10` | hard cap on replan calls (B2: increments on EVERY call) |
| `max_total_nodes` | int | `100` | cap on plan size → `PlanError` if exceeded |
| `replan_timeout_seconds` | float | `30.0` | per-call LLM timeout (Oracle B3 fix) |
| `max_total_injected_chars` | int | `8000` | cap combined prompt size (Oracle C4 fix) |
| `inject_completed_results` | bool | `True` | pass completed results to replanner |
| `inject_errors` | bool | `True` | pass failed-node errors |
| `custom_extension_prompt` | str \| None | `None` | override default extension prompt |
| `dedup_strategy` | `"off"` \| `"skip_dup_goals"` | `"skip_dup_goals"` | prevent re-adding completed tasks |

**Dropped from v1** (Oracle S5): `extension_mode` (only append), `allow_edge_removal`, `manual_replan_signal`, `dedup_strategy: "skip_dup_subtrees"`, `replan_on_failure_to_skip`, `emit_replan_events`, `extend_remaining` (always True), `allow_new_agents` (always False).

## DynamicDriver — Super Parameters (v1, Oracle S5 simplified)

| Parameter | Type | Default | Effect |
|-----------|------|---------|--------|
| `replan_strategy` | `"after_wave"` \| `"on_failure"` \| `"both"` | `"after_wave"` | when to invoke replanner |
| `replan_max_calls` | int | `10` | hard cap (B2: increments on every call incl. empty) |
| `replan_cooldown_waves` | int | `1` | min waves between replans (cooldown resets on each replan) |
| `halt_on_replan_error` | bool | `True` | if replanner raises, fail the run |
| `persist_replans` | bool | `True` | call `repository.save_plan` with `to_full_dict()` after each merge (B8 fix) |

**Dropped from v1**: `manual_replan_signal`, `replan_strategy: "manual"`, `emit_replan_events` (replaced by `on_event` callback), `extend_remaining` (always True), `replan_on_failure_to_skip`.

## DynamicDriver._after_wave() — Replan Hook

```python
class DynamicDriver(StaticDriver):
    def __init__(self, replanner, *, replan_strategy="after_wave",
                 replan_max_calls=10, replan_cooldown_waves=1,
                 halt_on_replan_error=True, persist_replans=True,
                 on_progress=None, max_workers=4, max_retries=3,
                 repository=None):
        super().__init__(on_progress, max_workers, max_retries)
        self._replanner = replanner
        self._replan_strategy = replan_strategy
        self._replan_max_calls = replan_max_calls
        self._replan_cooldown = replan_cooldown_waves
        self._halt_on_replan_error = halt_on_replan_error
        self._persist_replans = persist_replans
        self._repository = repository
        self._replan_count = 0
        self._waves_since_replan = 0
        self._run_id = None

    def _after_wave(self, plan, wave, succeeded) -> bool:
        self._waves_since_replan += 1
        if not self._should_replan(succeeded):
            return succeeded
        try:
            extension = self._replanner.extend(
                goal=plan.goal,
                agents=[AgentSpec(name=n) for n in plan.available_agents],
                prior=plan,
                results=dict(self._log.results),
                errors=dict(self._log.errors),
            )
            self._replan_count += 1       # B2: increment on EVERY call
            self._waves_since_replan = 0
            return self._apply_extension(plan, extension, succeeded)
        except PlanError as e:
            if self._halt_on_replan_error:
                raise
            logger.warning("replan failed, continuing: %s", e)
            return succeeded

    def _should_replan(self, succeeded) -> bool:
        if self._replan_count >= self._replan_max_calls:
            return False
        if self._waves_since_replan < self._replan_cooldown:
            return False
        if self._replan_strategy == "after_wave":
            return True
        if self._replan_strategy == "on_failure":
            return not succeeded
        if self._replan_strategy == "both":
            return True
        return False

    def _apply_extension(self, plan, extension, succeeded) -> bool:
        # B4: handle halt_reason
        if extension.halt_reason is not None:
            if extension.halt_reason == HaltReason.DONE:
                self._skip_remaining(plan, [n for n in plan.nodes
                    if plan.nodes[n].status == NodeStatus.PENDING])
                return True     # succeeded=True
            else:  # ABORT or DRIFT
                self._skip_remaining(plan, [n for n in plan.nodes
                    if plan.nodes[n].status == NodeStatus.PENDING])
                return False    # succeeded=False

        # B6: transactional merge — validate on shadow, swap if clean
        if extension.extensions or extension.new_edges:
            self._merge_plan_transactional(plan, extension)

        # Emit event (B5 fix — via on_event, not on_progress)
        if self._on_event:
            self._on_event(RunEvent(
                kind="replan_applied",
                extension_count=self._replan_count,
                nodes_added=[n.id for n in extension.extensions],
                rationale=extension.rationale,
            ))

        # B8: persist with full state
        if self._persist_replans and self._repository and self._run_id:
            self._repository.save_plan(self._run_id, plan)

        return succeeded

    def _merge_plan_transactional(self, plan, extension):
        """B6 fix: build candidate in shadow, validate, swap if clean."""
        import copy
        shadow = copy.deepcopy(plan)
        for node in extension.extensions:
            shadow.add_node(node)
        for src, dst in extension.new_edges:
            shadow.add_edge(src, dst)
        shadow.validate()   # raises DAGCycleError / DAGDanglingEdgeError
        # Validation passed — apply to real plan
        for node in extension.extensions:
            plan.add_node(node)
        for src, dst in extension.new_edges:
            plan.add_edge(src, dst)
```

## Termination Safety (Oracle B2, B4 fixes)

1. **`_replan_count` increments on EVERY `extend()` call** regardless of
   whether extensions are empty or merge is applied (B2 fix).
2. **`halt_reason` replaces `should_continue`** (B4 fix):
   - `None` → continue normally
   - `"done"` → skip remaining PENDING nodes, `succeeded=True`
   - `"abort"` → skip remaining, `succeeded=False`
   - `"drift"` → skip remaining, `succeeded=False`
3. **`replan_timeout_seconds`** bounds each LLM call (B3 fix).
4. **`max_extensions` + `max_total_nodes`** hard-cap plan growth.
5. **`replan_cooldown_waves`** prevents every-wave replanning if set > 1.
6. **Transactional merge** — failed validation leaves plan unchanged (B6 fix).

## File Changes

| Step | File | Action |
|------|------|--------|
| 1 | `community/spi/planner.py` | NEW: `Planner` ABC + `PlanError` |
| 2 | `community/spi/replanner.py` | NEW: `Replanner` ABC + `PlanExtension` + `HaltReason` |
| 3 | `community/spi/driver.py` | NEW: `Driver` ABC + `RunResult` + `RunLog` + `RunEvent` |
| 4 | `community/core/_dag.py` | ADD: `goal: str` + `available_agents: list[str]` fields to `DAGPlan` |
| 5 | `plugins/planner/static.py` | NEW: `StaticPlanner` (from `core/_planner.py`) |
| 6 | `plugins/driver/static.py` | NEW: `StaticDriver` refactored to template method (from `core/_driver.py`) |
| 7 | `plugins/replanner/dynamic.py` | NEW: `DynamicReplanner` with all super params |
| 8 | `plugins/driver/dynamic.py` | NEW: `DynamicDriver` with replan hook |
| 9 | `plugins/_register.py` | ADD: register planner/replanner/driver options |
| 10 | `bootstrap/__init__.py` | ADD: Config fields + PluginAccessor accessors |
| 11 | `cli.py` + `adapters/http/_app.py` | CHANGE: swap to container.plugins() |
| 12 | `community/compat.py` | NEW: `Planner = StaticPlanner`, `Driver = StaticDriver` aliases |
| 13 | `tests/test_dynamic_replanner.py` | NEW |
| 14 | `tests/test_dynamic_driver.py` | NEW |

## State Consistency (Oracle C5 fix)

**`plan.nodes[n].result` is the single source of truth.**
`RunLog.results` is a read-only view populated from `plan.nodes[n].result`
during execution. No divergence invariant — they're the same data accessed
through different references.

## Persistence (Oracle B8 fix)

`persist_replans=True` → after each successful merge, call
`repository.save_plan(run_id, plan)` using `plan.to_full_dict()` (NOT
`to_json()` which is lightweight). This writes full state including
node status/results/timestamps, enabling crash recovery from the last
extension boundary.

## Backward Compatibility

- `from agentcompute.community.core import Planner, Driver` still works
  via aliases in `community/compat.py` (Oracle C10 fix — separate module,
  not `core/__init__.py`, to avoid import cycles).
- Existing tests construct `Planner()` and `Driver()` directly — pass
  unchanged.
- `Config()` without `planner`/`driver`/`replanner` fields defaults to
  `"static"` / `"static"` / `None`, preserving current behavior.