# Plan: avernet-goal-cli v1 implementation

## Scope

Implement the approved `design.md` contract end to end:

- Backend single-target Intake supports execution mode and optional A2A proxy Bot.
- A2A Intake reuses the existing `benchmark_execute` BCS chat-async path and waits for the target Bot result.
- Existing `/start {session_id}` remains one Session to one Task and returns `TaskOpResult`.
- Rust CLI supports single Goal, repeated Bot targets, resume/get, Batch schema/example/validate/submit, text/JSON output, and stable exit codes.
- Group targets and user-controlled runtime strategies remain unsupported in v1.

## Architecture

```text
avernet-goal-cli
  ├── parse + validate
  ├── expand Goal × target
  ├── Gateway HTTP client
  └── interactive session controller
          │
          v
POST /api/v1/collaboration/tasks/intake
  ├── direct target-Bot Intake
  └── A2A proxy Bot -> BCS chat-async -> target Bot
          │
          v
TaskIntakeResult(session_id, target, execution_mode, TaskSpec)
          │ confirm
          v
POST /api/v1/collaboration/tasks/start {session_id}
          │
          v
TaskService.execute -> TaskOpResult -> existing Relay/Centralized runtime
```

## Backend changes

1. Extend intake domain/DTO/persistence with `TaskExecutionMode`, `requested_mode`, and `requested_a2a_proxy_bot_ref`.
2. Preserve backward decoding for rows without `execution_mode`.
3. Add an A2A Intake adapter that reuses `BcsClientPort`, `BcsBotTokenProvider`, and BCS Chat Run status polling.
4. Resolve and authorize the Human-owned proxy Bot; target Bot remains the Intake and task owner.
5. Build `TaskInfoRequest` from the frozen execution mode instead of hardcoding Relay.
6. Keep Group rejected for the CLI path and preserve one Session -> one Task idempotency.

## CLI changes

Create an independent Rust package in the CLI organization's standalone `avernet-goal-cli` repository, with no dependency on Avernet or BCS business crates. Avernet retains only the Backend contracts and cross-repository design documentation.

- Root command submits one Goal.
- Repeated `--to bot:<ref>` expands into independent sessions.
- `resume` reconnects to the same interactive controller.
- Batch commands expose a generated JSON Schema, examples, local validation, and submission.
- Gateway origin and authentication are injected through the Ant CLI runtime; no Backend URL flag or custom token store.
- Tests use an injectable mock HTTP transport and local fixtures.

## Reliability

- Persist the Intake Session before remote recognition.
- Persist the A2A run handle as internal control state, never the bearer token.
- Resume reconciles an unfinished A2A run instead of resending it.
- `/start` keeps existing `UNIQUE(session_id)` behavior.
- Batch has best-effort independent execution semantics.

## Verification

- Backend domain, repository, HTTP adapter, A2A adapter, Relay/Centralized regression tests.
- Rust unit/contract tests and `cargo clippy`.
- Focused Backend Ruff and pytest suites.
- `git diff --check` and modified source-file line-count checks.
