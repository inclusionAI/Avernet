# A2A Task / Run Binding Storage v1

Updated: 2026-10-31. Implements the plan's normalized, many-to-one association
model. This is an internal repository/storage contract, not an A2A extension.

## Ownership and schema

- `bcs_a2a_tasks`: unique `(env, task_key)` identifies a remote Task. `task_key`
  hashes Provider, provider_bot_ref, interface URL, tenant, deployment route
  fingerprint and remote_task_id. `scope_key` prevents adopting that identity
  from another local Bot/Session. The row contains explicit remote_task_id,
  context_id, shared pending_input_json, and an independent version.
- `bcs_a2a_run_bindings`: unique `(env, run_id)`; nullable `(env, task_key)` FK
  references tasks. The FK is NOT unique: many runs can bind one Task. Before
  the first remote response the run has no Task row. Explicit scope columns:
  bot_id, session_id (unbounded TEXT), provider_id, provider_bot_ref,
  interface_url, tenant, route_fingerprint; plus indexed scope_key.
  Explicit immutable request columns: group_id, protocol_version, binding,
  update_mode, credential_kind, message_id, request_digest, request_context_id.
  extensions_json is the negotiated URI array. Mutable run columns: state,
  version, deadline_ms, updated_at_ms and six nullable budget columns:
  budget_remaining_ms, budget_total_deadline_ms, budget_input_wait_ms,
  budget_phase, budget_phase_started_ms, budget_phase_deadline_ms.
- `bcs_a2a_contexts`: unique `(env, scope_key)`, with the seven explicit scope
  fields and context_id. This is the current cross-task continuation mapping,
  not task history. No scope_json or duplicate updated_at_ms.
- MySQL opaque run/message/task/context identifiers use binary collation;
  identifiers differing only in case must not compare equal.
- All three tables have auto-increment id, gmt_create, gmt_modified. Only the
  extension array and protocol-defined input snapshot remain JSON. No secret
  values, text deltas, full AgentCards or chat histories are stored here.

Run identity/request fields remain per run because they exist before a remote
Task is known and later requests may have their own frozen negotiation. Task
input is shared; local run state and timeout budget are not shared. Task
identity/input storage does not claim to be an authoritative remote state
machine: a local failure/terminal must not become a fabricated remote terminal.

## Repository behavior

`A2aRunRepoPort::get/active` assemble `A2aRunRecord` from explicit binding
columns and the shared Task row. `task_version` is absent before binding.

`create` reserves an unbound submitting run, version 1. `compare_and_set`
returns `Some(committed_record)` on success, including the committed Task
version; `None` on run or Task revision conflict, and an error on durable
write failure/invalid identity. It atomically:

1. Creates/adopts or revision-fences the shared Task input snapshot.
2. On first association only, establishes the context mapping; the same
   context ID causes no context row write or audit timestamp change.
3. CAS-updates the run binding. Existing task identity cannot be replaced or
   detached. A bound writer must supply the current task_version.
4. Reads the committed joined projection inside the same transaction.

Adoption must match scope and context and preserves existing input receipts;
an unversioned new binding cannot overwrite a prior shared input snapshot.
Task CAS prevents a stale run from overwriting receipts accepted by another
run. All intermediate task/context writes roll back if the binding CAS fails.
Completing a local run does not complete other bindings.

`has_other_active_task_run` is a bounded indexed existence query. A run-scoped
abort and failed-tracking cleanup reject before calling CancelTask if it would
cancel another active run's Task. The check uses the authenticated remote Task
identity even when the first binding write failed, not just a nullable local FK. Scope abort still requires one unambiguous active run. No broad Task
cancellation endpoint, multi-instance owner, automatic text-clarification
routing or parallel execution policy is introduced. Existing serial queue
admission and exec replies on the original run remain. A new ordinary message
does not infer an old task from Session alone.

## Access cost and limits

Counts below are SQL statements, not network round trips. MySQL executes
transaction steps sequentially on one connection; BEGIN/COMMIT add protocol
operations. SQLite uses a local connection. No transaction spans remote IO.

| Path | Reads/writes in this store |
| --- | --- |
| get run | 1 SELECT, indexed binding lookup + one indexed LEFT JOIN |
| active scope | 1 SELECT, at most 2 returned bindings, joined to tasks |
| context | 1 SELECT by unique scope, verifies the explicit scope fields |
| create | 1 INSERT, one binding row |
| first remote identity / adoption | 1 Task lookup, then transaction: 1 Task INSERT/CAS, 1 conditional context INSERT/upsert, 1 run CAS, 1 joined SELECT |
| later state/input update | transaction: 1 Task CAS, 1 run CAS, 1 joined SELECT; no context operation |
| unbound failure/unknown update | transaction: 1 run CAS, 1 joined SELECT |
| run-scoped cancel safety | 1 additional existence SELECT with LIMIT 1, using the Task binding index |
| text delta | no association write |

Indexes: unique env/run, unique env/task key, unique env/context scope;
active `(env, scope_key, state)` and Task bindings `(env, task_key, state, run_id)`.
Active lookup does not request ordering: it only needs up to two matching
bindings to detect ambiguity, avoiding a sort. No throughput guarantee is claimed. JSON snapshots
are not queried for identity. Writes touch at most one Task, one binding and
one context per operation; no per-binding fan-out. Shared-Task revision conflicts
fail visibly, not in an unbounded retry loop. Callers retain the existing serial
admission and bounded retry policies. No retention cleanup or load benchmark
is added; historical bindings still need a future retention policy.

## Development migration and validation

First publication uses the consolidated MySQL/SQLite 039_bot_a2a migration after
upstream version 038. It creates normalized tables and minimal Card fields directly;
no experimental association tables are created or dropped. All committed upstream
migrations remain unchanged. Recreate disposable databases from earlier A2A drafts;
this is not an upgrade/downgrade plan for retained draft data.

Validation results are recorded in the companion task-bindings-validation.json.
Conformance includes many-to-one identity, shared receipts, stale run/Task CAS,
immutable bindings, independent local state, scope isolation and transaction
rollback. SQLite and real MySQL execute the same shared repository contract.
