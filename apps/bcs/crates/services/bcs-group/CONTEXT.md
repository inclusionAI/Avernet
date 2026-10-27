# bcs-group Context

## Provides

- Group session service implementation for BCS.
- Group lifecycle, membership, and session coordination rules.
- Application-facing orchestration for group creation, updates, and state changes.
- `GroupCreateCommand.create_initial_session` controls the initial Session and
  its bootstrap delivery for non-provisional normal Groups, including StateMachine. False
  preserves Group authorization, validation, quota and persistence, with no
  Session call or initialization message. The choice is not persisted.
- `GroupCreateCommand.human_mention_notify_mode` is a required concrete enum
  supplied by callers (HTTP omission resolves to `all`). Normal creation assigns
  it before the first Group upsert, including sessionless and provisional
  creation, without an additional persistence operation. DM creation and reuse
  use the separate, unchanged `DmCreateCommand`.

## Consumes

- `bcs-service-api` contract traits and DTOs.
- `plugin-api/*` contracts when persistence or cache support is needed.
- Pure utility crates for IDs, logging, and serialization.

## Allowed dependencies

- `service-api/*`
- `plugin-api/*`
- Utility crates such as `uuid`, `serde`, and `tracing`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- `external-clients/*` crates not listed in `Allowed dependencies` above

## Configuration

- Bootstrap injects stores, collaborators, and policy knobs explicitly.
- This crate must not choose concrete plugins or inspect env directly.

## Runtime ownership

The crate owns group session business rules and lifecycle decisions. It does not own HTTP/WS handling or implementation selection.

## Tests

- `cargo test --package bcs-group --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-group --all-targets --manifest-path src/bcs/Cargo.toml`

## Provider collaboration scope

Group creation, membership, Human invitations and workbench access enforce the live Provider collaboration boundary across current participants. Group mutation validation precedes optimistic version fencing. No owner fence is persisted after the last restricted participant leaves.

GroupCore.upsert also validates the owner boundary, so direct group creation cannot bypass it. Legacy visibility and member writes share the same policy checks. SQL public writes acquire ordered Provider shared locks and verify current policy within the transaction, preserving Group version fencing; no lock spans delivery.
