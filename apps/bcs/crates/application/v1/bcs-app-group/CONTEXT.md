# bcs-group-v1 Context

## Provides

- Normal Group summaries resolve nullable `driver_bot_name` from the driver
  participant after existing Bot-name backfill, without extra registry reads.
  Normal Group details resolve nullable `driver_bot_owner` (`human_<created_by>`)
  and `driver_bot_owner_name` through at most two best-effort registry reads,
  matching legacy display semantics without changing authorization. Missing
  ownership yields nulls; a missing Human name preserves the owner ID. DM
  projections are unchanged. Shared projections also apply to create/update.

- `GroupServiceImpl`, the transport-agnostic implementation of the BCN V1
  Group Service API.
- Principal-aware authorization, V1 projections, and orchestration over the
  existing Group, Session, Friendship, Relation, and Collaboration contracts.
- Optional inline Group Event Subscription provisioning, including hidden
  provisional resources, atomic finalization, compensation, and crash
  reconciliation through injected service contracts.

## Consumes

- `bcs-service-api` application, core, and outbound-port contracts.
- Pure utility crates for asynchronous traits and JSON values.
- Injected Event Subscription provisioning and lifecycle contracts; this
  crate does not depend on Eventing persistence or delivery implementations.

## Allowed dependencies

- `service-api/*`
- Utility crates such as `async-trait` and `serde_json`

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `plugins/*`
- Store or Legacy service implementations outside tests

## Configuration

- The production composition root injects all contract implementations,
  including the optional Group Event Subscription provisioner and state-machine
  runtime.
- This crate must not select implementations or inspect environment variables.

## Runtime ownership

This crate owns the V1 Group use-case facade and Group provisioning recovery
policy. It does not own Group/Event persistence, Webhook delivery, secret
protection, or transport contracts.

## Tests

- `cargo test --package bcs-app-group --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-app-group --all-targets --manifest-path src/bcs/Cargo.toml`
