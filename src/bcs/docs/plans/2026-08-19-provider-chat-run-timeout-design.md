# Configurable Provider Chat Run Timeout Design

- **Date:** 2026-08-19
- **Status:** Approved
- **Scope:** HTTP Provider `chat.send` default run deadline

## Problem

HTTP Provider `chat.send` currently falls back to the hard-coded
`DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS` value of one hour when a delivery frame
does not contain `params.timeout_ms`. The same hard-coded value is used by the
message-flow, system-message, and collaboration-runtime run contexts that
govern callback acceptance and Provider 2.0 SSE reading.

Manager-to-worker task dispatch does not provide an explicit timeout, so a
worker that legitimately runs for two hours reaches the one-hour Provider run
deadline. The manager-worker task ledger TTL is a separate concern and does not
keep the SSE run open.

Operations also needs to change the default deadline through deployment
configuration. Runtime hot reload is not required; a BCS restart may be used
to apply an emergency change.

## Decision

Add the top-level BCS setting:

```toml
provider_chat_run_timeout_ms = 10800000
```

The default is three hours. It is the fallback execution deadline for HTTP
Provider `chat.send` only. Existing frames with an explicit
`params.timeout_ms` retain that value:

```text
effective timeout = explicit frame timeout
                    or provider_chat_run_timeout_ms
```

The configured value is not a cap. An explicit timeout may be shorter or
longer. This preserves entry-point-specific budgets such as the existing A2A
Provider execution timeout.

## Runtime Consistency

The same configured fallback must be applied at every point that owns an
implicit Provider chat deadline:

- `HttpProviderTransport` uses it in the Provider webhook request body when
  `chat.send` has no explicit timeout.
- `BcsMessageFlow` uses it for group-chat and manager-worker run contexts.
- `SystemMessageDispatcherImpl` uses it for system-message run contexts.
- `CollaborationRuntime` uses it when a Bot task node has no explicit node
  deadline.

This keeps the value advertised to the Provider aligned with the BCS callback
and SSE deadline. Constructors retain the public default so isolated tests and
non-bootstrap consumers remain backward compatible; bootstrap injects the
configured value into every production composition profile.

The Provider event service's missing-context retention fallback follows the
new three-hour public default, but it does not create or control an SSE run.

## Interaction with Existing Timeouts

| Path | Effective Provider execution deadline | Other deadline |
| --- | --- | --- |
| Manager assigns worker task | New setting, default 3 h | Task ledger TTL remains unchanged |
| Group/system `chat.send` without explicit timeout | New setting, default 3 h | SSE idle timeout remains 15 min |
| `chat-async` to HTTP Provider | Existing explicit 2 h | Async lifecycle remains 2 h 5 min by default |
| Any `chat.send` with explicit `timeout_ms` | Explicit value | Entry-point lifecycle may still impose its own limit |

`async_chat_run_timeout_ms` is not reused or changed. It owns the outer
`chat-async` lifecycle rather than the default Provider execution budget.

The 15-minute SSE idle timeout is also unchanged. A run may live for up to the
effective run deadline only while the stream continues producing bytes often
enough to avoid the independent idle timeout.

## Configuration Lifecycle

The setting is deserialized with the rest of `BcsConfig` and is immutable for
the process lifetime. Updating the configuration requires restarting BCS. No
environment-variable read or runtime watcher is introduced.

## Compatibility and Risk

The default changes from one hour to three hours for Provider `chat.send`
deliveries without an explicit timeout. Explicit timeouts, non-`chat.send`
Provider methods, interaction resolution, manager-worker task ledger TTL, and
SSE idle/header timeouts do not change.

The main risk is deadline drift between the outbound request and the internal
run context. Tests therefore cover the three-hour public default, config
override parsing, transport fallback and explicit precedence, message-flow and
system-message run contexts, collaboration-runtime fallback, and bootstrap
wiring. An A2A regression test retains its explicit two-hour downstream
timeout.

## Validation

- Focused configuration and Provider transport tests.
- Message-flow, system-message, and collaboration-runtime deadline tests.
- Bootstrap compile/tests for all production composition profiles.
- A2A explicit-timeout regression coverage.
- Affected Cargo package tests, `cargo check -p bcs`, and `git diff --check`.
