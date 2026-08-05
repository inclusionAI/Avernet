# bcs-cli Context

## Provides

- CLI and admin entrypoints for testing and operating BCS.
- Operator-facing flows such as debugging, admin commands, and protocol-level requests.
- `collaborate permission` and `collaborate run` for server-authorized,
  one-shot state-machine execution in the current BCS session.
- A tool boundary separate from server runtime assembly.

## Consumes

- `bcs-protocol` wire DTOs.
- HTTP, OAuth, filesystem, and CLI framework crates.
- User-supplied flags, config file paths, and tokens.

## Allowed dependencies

- `bcs-protocol` wire contract crate, currently located at `service-api/bcs-protocol`
- CLI, HTTP client, auth SDK, and serialization crates

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- `plugins/*`
- Server runtime ownership logic

## Configuration

- CLI args, optional config files, and tokens are read at tool entrypoints only.
- BCS API URL precedence is `--url`, `BCS_API_BASE_URL`, `MOLTIS_BCS_URL`,
  `$BOT_DATA_DIR/.bcs/session.json`, a runtime-selected compiled distribution
  default, then the local default.
- Distributions may compile both `BCS_CLI_DEFAULT_PRE_URL` and
  `BCS_CLI_DEFAULT_PROD_URL` into one binary. The CLI selects pre for
  `pre`/`prepub` and production for every other runtime environment. Public
  builds omit both values and retain the local default.
- This crate must not choose server-side concrete implementations.

## Runtime ownership

The crate owns operator workflows and diagnostics. It does not own BCS server runtime or request-time business rules.

## Tests

- `cargo test --package bcs-cli --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-cli --all-targets --manifest-path src/bcs/Cargo.toml`
