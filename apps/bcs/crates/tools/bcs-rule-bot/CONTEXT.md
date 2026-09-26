# bcs-rule-bot Context

## Provides

- A deterministic, non-LLM bot runtime for BCS development and end-to-end tests.
- One host process for all `runtime.type=rule` entries in a profile manifest.
- Fixed, random reply, echo, random number, task worker, and supervisor behaviors.
- Native BCS WebSocket sessions, heartbeats, history, abort, and session cleanup.

## Consumes

- `bcs-protocol` WebSocket DTOs.
- Version 2 `bots.json` profile manifests.
- Per-profile `.bcs/session.json` identity files.
- Existing BCS `task.dispatch` and `task.complete` methods.

## Allowed dependencies

- `bcs-protocol`
- Async runtime, WebSocket client, CLI, serialization, random, and logging crates

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- `plugins/*`
- LLM clients and model configuration

## Runtime ownership

This crate owns Rule Bot behavior and client-side session state. It does not
change BCS routing policy, server application services, or persistence.

## Tests

- `cargo test --package bcs-rule-bot --manifest-path src/bcs/Cargo.toml`
- `cargo clippy --package bcs-rule-bot --all-targets --no-deps --manifest-path src/bcs/Cargo.toml -- -D warnings`
- `bash scripts/test_singlebox_rule_bots.sh`
