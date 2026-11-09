# bcn-runtime-api

Application/core Service APIs and outbound business ports.

See [BCN runtime contracts v1](../../../../../docs/architecture/bcn-runtime.md)
for lifecycle, limits, compatibility, isolation and composition boundaries.

Run focused tests with `cargo test -p bcn-runtime-api` from `apps/bcs`.

## Repository recovery contract correction (pre-release v1)

`TaskQueryStore` reads admission-owned metadata and snapshots, including accepted
nonterminal tasks. Result adapters delegate task queries rather than invent
principal/session/target metadata from a ResultIntent. `SessionStore::load_spec`
and `scan_active` discover admitted sessions before first activation;
`deactivate` removes wake only under current ownership and idle-state checks.

`CommandBus::publish` now requires current OwnershipFence and CommittedEffectRef,
returning the immutable committed Command. Callers derive stable IDs through
`effect_command_id`; no caller-provided effect body is publication authority.
Receive is cursor-bearing and bounded; consumers advance even empty eligible
pages and cycle at end. Connector commands must have a persisted dispatch before
ACK. `DispatchStore::claim` takes a committed CommandId; read/scan_active expose
DispatchContext (command/session/task/node) alongside exact immutable operation,
so ACK does not erase restart routing. Terminal records reject later mutations;
exact terminal receipt retries remain idempotent.

`ConnectorProjection` replaces the reset's caller-constructed TaskView. It is
node-scoped output with expected task cursor, correlated through DispatchContext.
The store aggregates peers and allocates task-global epochs independently of
per-dispatch fences. Delta's supplied cursor is an expected current cursor.
Atomic projection/snapshot/Reset publication eliminates partial reset state.

InboxPort is a trusted internal capability. Only Worker may forward the actual
ResultStore return as ResultSaved, after a matching immutable intent was committed.
The store verifies full payload/session association and atomically confirms the
saved receipt with inbox/wake; protocol clients cannot submit arbitrary inputs.
These signature/record changes require corresponding new-runtime consumer
updates and explicit offline migration/discard of pre-release records missing
required context. The legacy runtime is unchanged. See the runtime-store README
for persistence semantics, bounds, cost accounting and production limitations.
