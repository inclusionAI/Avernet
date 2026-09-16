# bcs-config-api Context

## Provides

`queued_provider_headers` defines the explicit non-sensitive routing allowlist,
credential-name restrictions and bounded canonical snapshots shared by bootstrap
validation and queued application preparation. It does not acquire credentials.

DeliveryPolicy's environment-level max_context_messages/max_context_bytes use
24/131072 defaults for legacy JSON; validated positive bounded values constrain
only each Send's appended Inject history, not ingress backlog or model tokens.

DeliveryPolicy defines persisted business values, including default policy, partial
Bot overrides and Group/System readiness validation. Management updates must set
`flow_enabled.group` and `flow_enabled.system` to the same boolean; mismatched
values fail with `queue_group_system_switch_mismatch`. Both disabled retains the
legacy transport behavior for new work; already admitted deliveries still drain.
Task is ready under its independent `flow_enabled.task` switch and the same
per-Bot mode/limits. Task-only enforce also requires healthy scheduler supervision.
Direct A2A and State-machine remain unready. These business values are configured
through the durable policy API, not the deprecated TOML policy fields.

The application loader upgrades older durable policies by assigning System the
Group value, committing a version-incrementing CAS with the migration actor
`system:group-system-policy-migration` before publishing it. Startup, master
takeover and periodic reconciliation share this loader. A concurrent edit causes
a reload, never an unconditional overwrite. Migration does not normalize new
management requests, silently ignore database failures, or change other fields.
The removed lock_path field is rejected as unknown; deployment owns scheduler
instance exclusivity.

- Typed configuration contract objects for BCS runtime assembly.
- LogOutputFormat::Raw is a message-only output format; the default
  message-delivery logging output uses it for fixed-column monitoring records.
  Existing Text/Json formats and the default Text deserialization remain unchanged.
- Config-facing value objects and secrets wrappers shared across bootstrap and services.
- A stable schema boundary for configuration-related evolution.
- Fail-fast validation and host-pattern matching semantics for Eventing private
  endpoint allowlist entries.
- Default-off managed-delivery flow switches, Bot limits and code-owned
  readiness validation. This is new-admission policy, not recovery filtering.

## Consumes

- Serialization, secrets, and CIDR parsing primitives only.

## Allowed dependencies

- Value-object, serialization, and secrets crates
- No other workspace role beyond contract-only helpers

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- `services/*`
- `plugin-api/*` and `plugins/*`
- `external-clients/*`

## Configuration

- This crate defines config schema only.
- It must not load files, inspect env, or choose implementations.

## Runtime ownership

The crate owns configuration contract types and their semantics. It does not own runtime wiring or config source discovery.

## Tests

- `cargo test --package bcs-config-api --manifest-path src/bcs/Cargo.toml`
- `cargo check --package bcs-config-api --all-targets --manifest-path src/bcs/Cargo.toml`

## Eventing polling configuration

Optional `fanout_idle_poll_max_interval_ms` and
`delivery_idle_poll_max_interval_ms` ceilings default to their respective base
intervals when absent. Explicit values must be at least the base and at most
60,000 ms. Equal ceilings disable backoff; larger values trade cold-event latency
for fewer idle database calls. Eventing and logging definitions have private
modules with existing root type re-exports preserved.
