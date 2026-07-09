# tests/_flows/ — business flows are data, not tests

A `FlowCase` is a declarative, executor-agnostic sequence of HTTP steps
(`tests/framework/flow.py`). This package holds them as module-level constants,
one sub-package per module (`skill_center/`, …).

**Two executors consume the same flow:**
- 路 A — `tests/e2e/`, `flow_runner.run_flow` over `TestClient` (in-process, fast, CI). `FsAssert` is skipped.
- 路 B — `tests/acceptance/`, `flow_runner_live.run_flow_live` over `httpx` against a real backend. `FsAssert` is enforced against the host filesystem.

Rules: no pytest import, no assertions, no executor knowledge here. A flow must
read the same whether run in-process or live. Tests live in e2e/ and acceptance/.
