# Code report

- Public worktree: asc-caller-refresh-openapi-20261112; target REL20261112.
- Changed: existing scheduled refresh Protocol/Noop; existing Caller OpenAPI router/schema; OWNER authorization and addressed-grant admission rows; owner/source navigation documentation; behavior and HTTP framework tests; necessary route inventory counts.
- Validation: 18 Caller adapter tests passed locally. Orchestrator combined handler/framework/coverage/user-id checks: 58 passed; final framework no-mock/real HTTP/coverage checks: 39 passed. Ruff I/F on implementation and adapter tests passed after import ordering. Actual PR checks remain a separate gate.
- Logs: openapi_caller_refresh_requested / succeeded / failed, with system, operation, method/route, actor, Bot locator, trace, aggregate status, safe per-binding results, duration/error type. Raw token and SDK messages are never logged or returned. Known domain errors map through existing envelopes; unsupported=501, invalid capability=400, unknown upstream=502.
- The public layer depends only on the existing Service API. Existing community Noop explicitly refuses this independent operation. No enterprise dependency, scheduler, transport, schema migration, coverage exclusion or Singlebox modification.
