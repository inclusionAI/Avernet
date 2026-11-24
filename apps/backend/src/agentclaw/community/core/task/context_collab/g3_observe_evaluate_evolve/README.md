# G3 local telemetry and read projections

Owner: Backend `core/task/context_collab/g3_observe_evaluate_evolve`.
This implements bounded local acquisition and existing read projections, not
full ACCS evaluation, automatic adoption or RSI orchestration.

## Context Boundary

- **Provides:** [protocol values](telemetry.py) `TelemetryContext` and
  `Observation`, [metric/result values](metrics.py) `MetricDefinition`,
  `MetricSample` and `Outcome`, and the transport-free
  [TelemetryServiceProtocol](service_api.py). Existing `observation.py` remains
  the L4 Task/Event read-projection helper, not the Observation write service.
- **Consumes:** injected `TelemetryRepositoryProtocol` and
  `TelemetryAccessProtocol`. [DI](../../../../di/modules/task/telemetry_module.py)
  chooses SQL persistence and the [G1 access adapter](../g1_secure_govern/telemetry_access.py).
  The adapter reads current L4/L6 facts and uses the existing G1 event-visibility
  policy; acquisition never appends a fake Work Event for authorization.
- **Does not own:** Task transitions, admission/selection, artifact access,
  human acceptance, remote identity verification or a new HTTP operation.
  Direct library callers must supply a trusted `ActorContext`; self-declared
  HTTP/JWT data is not an authenticated context.

## Acquisition contract

| Meaning | Generic service | Local commission example |
| --- | --- | --- |
| Step detail | `record_observation` | EVENT with `extensions.detail`, step, note and artifact reference |
| Metric semantics | `define_metric` | Task execution percentage; percent, HIGHER_IS_BETTER, LAST |
| Measured value | `record_metric_sample` | 20, 50, 80 and 100, each referring to the registered definition |
| Execution result | `record_outcome` | SUCCEEDED with result/evidence references and a final metric sample |

A `MetricDefinition` describes a measurement; it does not carry a measured
percentage. Registration returns an explicit versioned `ObjectRef`. Its version
is distinct from `schema_version`, which describes the representation. Exact
repeat registration is a no-op; a different definition at the same ID/version
is rejected. A sample must refer to a definition registered in the same
tenant/environment and its subject type must match `scope`. `window=None`
means an instantaneous measurement, not an invented time window. Definitions
and samples declare semantics; this service does not calculate aggregates,
enforce application-specific units/ranges, or make adoption decisions.

`Observation` has an authenticated observer, observed time, server-owned
recorded time, subject, schema and causal context. Arbitrary step detail belongs
in the producer's versioned extension schema, not a new progress DTO.
Timestamps require a timezone and serialize as UTC; non-JSON payloads and
non-finite numeric samples are rejected. Missing/not-applicable context refs
remain absent; local acquisition does not support Activity refs.

`Outcome` records an execution result, **not** L4 submission, owner acceptance
or proof that a product goal is achieved. Its embedded metric samples must
share its subject and registered metric semantics. They are stored atomically
inside the Outcome, not appended again as standalone samples. An optional local
evaluator ref may identify only the authenticated reporting actor; this is not
an API for impersonating an external evaluator. Empty improvement-objective
refs mean no improvement evaluation is being claimed.

## Security and storage

The local policy is deliberately conservative: Task principal or designated
executors only, plus the pinned participant-scope restriction for nonprincipals
and visibility of the current Task's state evidence. Taskless Collet reports
use its initiator/initial executors. Public Event visibility alone grants no
telemetry access. All supplied actor, Space, Collet, Task, MethodBinding and
pinned ProductProfile associations are checked. These checks do not grant
Resource access or create participation, admission or selection facts.

Telemetry is workspace-visible to those authorized callers. `classification`
is descriptive, **not** an extra access-control implementation. Do not inline
secrets or sensitive payloads; use governed references/digests. Referenced
artifacts/evidence are not fetched or certified by this service. The adapter
does not yet provide redaction, retention jobs, remote observers or delegated
evaluator policies; such policies must be implemented before those use cases.

[SQL storage](persistence.py) is append-only in `ac_context_collab_telemetry`,
isolated by tenant/environment. It stores the protocol JSON, contextual
associations, authenticated recorder and server acquisition timestamp. Global
metric definitions have no Collet, subject or TelemetryContext; those columns
are genuinely NULL. Scoped observations, samples and outcomes require them.
Queries return acceptance order for a subject identity within its Collet;
subject version/issuer is retained in the value, not used as a history filter.
No pagination or cross-call snapshot guarantee is provided by this local API.

Apply the [additive DDL](../sql/2026_11_17_context_collab_telemetry.sql) once before
using the new service. No Task/Event migration or telemetry backfill is needed.
SQLite tests create the ORM schema; they do not validate production DDL execution.

Persistence and authorization failures propagate to callers; no false recording
receipt is returned. Duplicate record IDs conflict instead of overwriting data.
For this commission Profile there is no mandatory telemetry gate: the simulated
Bot logs acquisition failures and continues its business execution. This is a
producer policy, not a service that swallows failed writes. A future Profile
that explicitly requires evidence must enforce its gate separately.

## Compatibility and verification

Acquisition does not mutate Collet/Task revisions or L4 Events, even at 100%
or Outcome SUCCEEDED. It does not feed the older `ProgressSnapshot` projection,
which still derives state and legacy reported progress from L4 Task/Event
facts. Existing signed HTTP `progress` remains a compatibility Work action;
this change neither removes it nor adds a new progress route or L1 envelope.

- [G3 contract/SQLite tests](../../../../../../../tests/community/core/task/context_collab/test_g3_telemetry.py): codecs, immutable definitions, registered samples, durable records, actor/scope/visibility enforcement, tenant/env isolation and write failure propagation.
- [Bot runtime tests](../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_bot_runtime.py): direct calls with Bot identity, nonblocking telemetry failure, unchanged submit fence and no submission on execution failure.
- [Commission journey](../../../../../../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end.py): actual DI/SQLite acquisition, unchanged L4 state during telemetry, then signed HTTP submission and owner acceptance.

### Change impact

Value schemas and metric registration affect direct G3 callers and stored JSON;
access policy changes affect who can report/read local execution details.
Storage changes require matching DDL. Keep service, DI, producer and SQLite
contract tests aligned. Existing L4 Work and progress projections are separate
contracts and must not gain implicit transitions from G3 telemetry.
