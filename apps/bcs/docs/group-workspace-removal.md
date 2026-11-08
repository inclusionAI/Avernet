# Deprecated group Workspace removal

The legacy group Workspace feature has been removed. It previously held
`decisions`, `tasks`, `notes`, and `audit_log` in process memory. SQL-backed
groups did not persist this state, so a restart, cache eviction or routing to
another instance could lose or change the observed content.

## Contract changes

- `GET /groups/{id}` no longer returns `workspace`.
- `GET /groups/{id}/workspace` and `PUT /groups/{id}/workspace` are no longer
  registered and return HTTP 404.
- The `Workspace` type, its dedicated task/audit DTOs, group field, application
  commands/results and core/repository mutation methods are removed. Rust
  consumers must remove references to those deleted APIs when rebuilding.
- Legacy serialized group JSON containing `workspace` remains readable; the
  removed field is ignored and is not included when the group is serialized.

There is no database migration or replacement storage for this feature. The
change removes the old cache-only mutation path and adds no database round trips
to group detail reads.

Manager-worker task dispatch and recovery, GroupContext, state-machine judge
decisions, collaboration event history, and session file sharing are separate
capabilities and retain their existing contracts. Clients should remove their
legacy Workspace field assumptions rather than redirect those fields to an
unrelated task or context API.

## Verification

Domain tests cover absent Workspace serialization and reading legacy group JSON.
HTTP contract tests cover successful group detail reads without the field and
404 responses to both retired routes. The shared-group E2E story verifies the
retired routes while retaining membership, metadata and routing-policy checks.
