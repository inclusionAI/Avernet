---
status: accepted
---

# Persist per-domain lifecycle runtime reprojection

A device becoming `ACTIVE` proves only that its current binding has reported basic liveness; it does not prove that every runtime capability can accept delivery. Lifecycle-triggered reprojection for per-domain runtimes will therefore run through durable, binding-fenced tasks, split into independent Skill and MCP work, while mutation-triggered projection retains its existing synchronous result and rollback contracts. Teclaw remains on its existing whole-artifact path: one composed `BotConfigArtifact`, one `/api/v1/bot/apply`, no per-domain readiness probe, and no retry change in this decision.

## Considered Options

- Expanding global device readiness to require every capability was rejected because one unavailable capability would incorrectly make otherwise usable runtime functions unavailable.
- Retrying the whole projection in process was rejected because Backend restart can lose the signal and a failed MCP projection would repeatedly deliver an already-converged Skill projection.
- Reusing `GET /api/mcp` as readiness was rejected because relay-backed engines may currently turn an unsuccessful `mcp.config.list` response into an empty successful list.
- Extending the Skills Pool reconciliation task was rejected because filesystem layout convergence and MCP configuration readiness have different evidence and change reasons, despite sharing the same durable task infrastructure.

## Consequences

Per-domain lifecycle signals enqueue one live task per `{environment, binding, projection component, runtime generation}` and each attempt revalidates the current device and sandbox before reading the latest desired state. A generation digest prevents a live task for an old sandbox or restart publish from swallowing a newer Runtime signal. MCP readiness is an explicit, fail-closed Engine contract: OpenClaw and Hermes validate their local MCP configuration plane, while AICoding and Claude Code require a successful Relay MCP round trip; the Backend reaches it through the existing device adapter transport. Desktop Skills Pool remains the single writer after transition begins; the lifecycle Skill component wakes that durable task and rechecks authority after any Legacy write. Retryable outcomes use the existing task queue backoff and a ten-minute deadline, after which desired state is preserved and the task becomes `TIMED_OUT`; a new lifecycle or explicit reconciliation signal may enqueue fresh work. Implementation must update the relevant Service API or Plugin API contracts, context-boundary metadata, conformance tests, OCB Corp implementations, `ocb-public` gitlink, runtime image, and deployment evidence together.
