# ding-logger Context

当前位置说明：该 crate 当前暂留在 `crates/auxiliary/` 下作为启动期或独立运行的诊断能力；按 `src/bcs/docs/arch/refactor-arch-proposal.md`，若它继续作为独立运维/诊断工具存在，目标目录应为 `crates/tools/ding-logger`。

## Provides

- Auxiliary DingTalk logging and diagnostics capability for BCS-related workflows.
- A startup-only or standalone logger for observing group message traffic.
- Passive logging utilities that do not participate in core BCS routing decisions.

## Consumes

- DingTalk HTTP and WebSocket endpoints.
- Logging, serialization, and async network client crates.
- Entry-point supplied credentials and runtime switches.

## Allowed dependencies

- HTTP/WebSocket client, logging, and serialization crates
- No dependency on BCS service or adapter runtime crates

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- `plugin-api/*` and `plugins/*`
- Ownership of BCS delivery or routing semantics

## Configuration

- Bootstrap or a standalone entrypoint explicitly enables this auxiliary capability.
- Credentials and endpoints must be supplied at startup, not discovered through business services.

## Runtime ownership

The crate owns passive logging and diagnostics only. It does not own BCS business logic, delivery state, or plugin selection.

## Tests

- `cargo test --package ding-logger --manifest-path src/bcs/Cargo.toml`
- `cargo check --package ding-logger --all-targets --manifest-path src/bcs/Cargo.toml`
