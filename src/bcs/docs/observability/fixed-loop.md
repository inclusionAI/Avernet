# Fixed Loop observations

The Service API `StateMachineLoopInstrumentationHook` is the runtime's metrics
extension point. Bootstrap injects the Prometheus implementation only when the
existing `prometheus-metrics` feature and metrics configuration are enabled.
The hook updates local counters synchronously and must not block, panic, perform
network/storage IO, or affect a business result. No new config, migration,
transaction, durable checkpoint, or public Event type is introduced.

| Counter | Successful observation point | Labels |
| --- | --- | --- |
| `state_machine_loop_iterations_started_total` | Loop entry's Running CAS at attempt 0, including HumanInput | env |
| `state_machine_loop_iterations_completed_total` | Loop result's Completed CAS, once per result regardless of fan-out | env |
| `state_machine_loop_break_total` | The same Completed CAS selects a saved break route | env, outcome |
| `state_machine_loop_exhausted_total` | The same Completed CAS selects a saved exhausted route | env |
| `state_machine_loop_compile_rejected_total` | Each failed invocation of the typed v2 compiler, including validation | env, reason |

`outcome` is restricted to `complete`, `done`, `approved`, `rejected`, `other`.
`reason` is `invalid_definition` or `resource_limit`; the compiler supplies this
classification without changing its public diagnostic code/path/message or
parsing error text. Zero/empty values are invalid definitions; positive values
above the iteration/body/node/byte limits are resource rejections. Invalid YAML
shape, execution gating before compilation, and historical snapshot reads do
not count as typed compiler rejection.

Metrics have no Run, execution/logical Node, Loop, Bot, iteration, attempt, raw
outcome or payload labels. A result that fans out to multiple targets counts
once. Intermediate body nodes and ordinary v1/v2 nodes do not count iterations.

Signals occur immediately after successful local transitions, before follow-up
publication/progression. Retry starts do not count another iteration; stale or
cancelled terminal events and replay of committed results do not count again.
Recovery that first commits an abandoned entry or Judge result counts normally,
using the saved plan. A failed CAS or persistence error emits no success metric.
A crash after commit but before observation may undercount; these process
counters are not a durable audit ledger. No recovery backfill or deduplication
state is maintained for metrics. Use persisted Run/Node/Event records for audit.

Structured `bcs_observation` Loop transition logs include `run_id`, `session_id`,
`loop_id`, `loop_iteration`, `loop_max_iterations`, `definition_node_id`,
`execution_node_id`, `attempt`, `selected_outcome`, and `transition`. The selected
outcome is empty before a result is selected; it retains the actual value on
completion, even when the logical route is exhausted. Retry and ignored Bot
events retain the affected attempt. Logs never infer identity from execution ID
syntax or copy artifacts. Compile rejection logs contain bounded reason,
diagnostic code and authoring path. Logs work with metrics disabled.

Propagation: Service API owns the closed hook contract, collaboration runtime
and compiler emit observations, bootstrap owns Prometheus registration, and
test-support supplies the shared conformance harness/noop implementation. Runtime
tests exercise Bot/Human, retry/stale/cancel, intermediate nodes/fan-out and
recovery after failed writes; bootstrap tests inspect rendered series and live
validation through the configured service. This does not complete the remaining
FL-15/16 recovery stages or enable production v2 execution.
