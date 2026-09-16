# Optional initial session at group creation

## Accepted behavior

`bcs-cli create-group --no-session` creates a normal Chat or ManagerWorker
Group without creating or reactivating an initial Session, sending its
GroupContext, or starting a bootstrap run. Existing commands keep their
current behavior when the option is absent.

The CLI sends `create_initial_session: false` to `POST /groups`. The HTTP
request defaults this boolean to true and passes it explicitly to the
transport-neutral `GroupCreateCommand`. The option is a request choice, not
persistent Group policy: the caller can later use ordinary Session creation.

Group validation, authorization, quota checks, persistence, and relationships
remain in force. The successful response contains null `session_id`,
`initial_session_id`, and `initial_run`, zero `context_injected`, and a Group
URL without a Session query parameter. The CLI skips its legacy Session-list
fallback when `--no-session` is present and rejects a contradictory server
response that supplies a Session ID, reporting the created Group ID.

This first change supports Chat and ManagerWorker only. Requests combining
`create_initial_session: false` with StateMachine, DM, a collaboration YAML
definition, or inline event subscriptions fail before side effects. Other Group
entrypoints retain initial Session creation. `start_initial_run` retains its
existing semantics.

Deploy the server changes, including read-only Session listing, before clients
use this option. An older server may ignore the field; a response check can
report that incompatibility but cannot undo an already-started run.

## Implementation plan

1. Add CLI and wire-contract tests, run them against the current implementation
   to observe failures, then introduce the defaulted wire option and CLI flag.
2. Add Group use-case tests for sessionless creation, persistence, validation,
   notifications, and unsupported combinations. Propagate the typed boolean
   through constructors; observe failures before adding the orchestration guard.
3. Add HTTP contracts that exercise the real Group and Session services: create
   an empty Group, list repeatedly, then explicitly create a Session. Cover
   parameter forwarding, response fields, and pre-write rejection paths.
4. Verify CLI HTTP payloads, no fallback GET, default behavior, and older-server
   mismatch reporting. Preserve existing public client helper behavior.
5. Update user reference, changelog, and affected context/contract docs. Run
   relevant package tests and inspect the final diff with independent review.

## Validation commands

- `cargo test -p bcs-protocol --offline`
- `cargo test -p bcs-cli --offline`
- `cargo test -p bcs-group --offline`
- `cargo test -p bcs-http --offline`
- `cargo test -p bcs-app-group --offline`
- `cargo test -p bcs-service-api --offline`
- `cargo check -p bcs --tests --offline`

Use focused test targets during implementation, then run affected package
suites. Do not run a global formatter or change unrelated code.

## Validation results

Verified on 2026-09-16 against the implementation based on `08625d671`:

- The combined suites for `bcs-protocol`, `bcs-cli`, `bcs-group`, `bcs-http`,
  `bcs-app-group`, and `bcs-service-api` passed: 1,180 tests passed, zero failed,
  two existing ignored tests (the CLI timeout test and an OAuth registration
  documentation example).
- `cargo check -p bcs --tests --offline` passed, including the bootstrap test
  caller of `GroupCreateCommand`.
- Independent code review found no functional defects. `git diff --check`
  passed. Existing unused-import and dead-code warnings remain.
- CLI subprocess tests ran on the host. Both `NO_PROXY` and `no_proxy` were set
  to `127.0.0.1,localhost,::1`; otherwise the host proxy changed an existing
  localhost transport-error test into an HTTP 500 response.
- The whole workspace and live Singlebox coverage were not run. Validation
  covered the six affected packages and server compilation; the new HTTP
  contracts use real Group/Session application services with memory storage,
  rather than a deployed stack or real Provider runtime.

## Existing file-size debt

All new source files are below 1,000 lines. Six modified files already exceeded
the repository limit on the starting `dev` commit `08625d671`:

| File under `src/bcs/crates/` | Before | After |
| --- | ---: | ---: |
| `adapters/http/bcs-http/src/routes/groups.rs` | 2,686 | 2,705 |
| `application/v1/bcs-app-group/src/lib.rs` | 2,969 | 2,970 |
| `services/bcs-group/src/application/management.rs` | 2,643 | 2,655 |
| `services/bcs-group/tests/management.rs` | 4,158 | 4,163 |
| `tools/bcs-cli/src/client.rs` | 4,281 | 4,306 |
| `tools/bcs-cli/src/main.rs` | 7,152 | 7,170 |

This change adds only the required request/command wiring and session-creation
guard to those files; new service and HTTP tests live in separate files. Splitting
the existing CLI dispatch, client methods, Group orchestration, and shared test
fixture would require a broader refactor than this behavior change. Those
responsibilities should move into dedicated modules in a separate cleanup.
No CI check or allowlist is changed, and the existing files do not meet the
1,000-line limit.
