# bcs-protocol Context

当前位置说明：该 crate 当前暂留在 `crates/service-api/` 下以降低迁移扰动；按 `src/bcs/docs/arch/refactor-arch-proposal.md` 的目标目录，它应最终归入 `crates/contracts/bcs-protocol`。在迁移完成前，仍应把它视为 protocol-contract crate，而不是 service-api 角色的一部分。

## Provides

- External wire DTOs, protocol frames, and compatibility-facing payload models for BCS.
- Stable request and response shapes shared by adapters and external callers.
- Versionable protocol contract types.

## Consumes

- Serialization crates only.

## Allowed dependencies

- Serialization and wire-format helper crates
- No concrete runtime or transport clients

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- `plugin-api/*` and `plugins/*`
- Runtime HTTP or WebSocket client logic

## Configuration

- This crate has no runtime config source.
- Version and compatibility decisions are made by adapters and bootstrap, not inside protocol types.

## Runtime ownership

The crate owns stable wire contract objects only. It does not own application commands, core business models, or runtime clients.

## Tests

- `cargo test --package bcs-protocol --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-protocol --all-targets --manifest-path src/bcs/Cargo.toml`
