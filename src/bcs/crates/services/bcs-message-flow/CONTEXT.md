# bcs-message-flow Context

## Provides

- `MessageFlowService` implementation for Workbench/Web group send, bot event relay, chat abort, and master-slave task flow.
- `A2aChatService` implementation for direct bot chat and async chat run APIs.

## Consumes

- `bcs-service-api` service traits and delivery ports.
- `bcs-protocol` wire frames and payload DTOs.

## Allowed dependencies

- `service-api/*`
- Pure utility crates such as `serde_json`, `uuid`, and `tracing`

## Forbidden dependencies

- Composition-root runtime crate
- `adapters/*`
- Concrete `services/bcs-routing`
- `external-clients/*` crates not listed in `Allowed dependencies` above
- websocket runtime sender handles and adapter framework types
- DingTalk runtime crates

## Configuration

- Policy knobs and dependency ports are provided by constructors and bootstrap wiring.
- This crate must not inspect env or choose concrete adapters/plugins directly.

## Runtime ownership

The service owns request-time message lifecycle decisions. It does not own
transport connection state and does not directly send websocket frames.

## Tests

- `cargo test --package bcs-message-flow --manifest-path src/bcs/Cargo.toml`
- Structure check: scan this crate for adapter/runtime transport symbols before merging.
