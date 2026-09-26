# Preserve Provider SSE messages without timestamp

## Problem

Provider SSE chat messages without `message.timestamp` fail conversion into
the shared `MessageContent` type. A final still closes its run, but loses its
body before group relay and public history persistence.

## Accepted behavior

- Normalize only the Provider SSE input boundary, for every chat state.
- Preserve a supplied `message.timestamp`; when absent, use the event's
  unsigned integer `ts`, then the BCS frame receipt time in milliseconds.
- Normalize a copy; leave the original SSE payload unchanged.
- Keep validation for malformed supplied timestamps, roles and content.
- Keep the shared MessageContent type, callback/WS behavior, terminal handling,
  routing, and history timestamp semantics unchanged.
- Per the user's explicit scope, do not split existing source files.

## Implementation plan

1. Run the existing Provider transport tests as a baseline.
2. Add regressions for timestamp precedence, missing timestamps in all chat
   states, validation, and final-only / delta-plus-final group history.
3. Confirm the missing-timestamp regressions fail with the current parser.
4. Add the small boundary normalization and document the SSE contract.
5. Run Provider transport, protocol, message-flow and relevant bootstrap
   integration tests; review the diff and record validation.

## Compatibility and propagation

This is a backward-compatible clarification of the Provider 2.0 SSE contract.
Providers may omit the message timestamp without losing their response body.
No storage migration, configuration change, new dependency or public Rust API
change is required. Reverting restores the previous strict SSE conversion.

## Validation

- Baseline `cargo test --locked --offline -p bcs-provider-http`: 58 passed.
- Before the fix, three new timestamp tests failed because the message body
  was absent; the malformed-message regression already passed.
- `cargo test --locked --offline -p bcs-provider-http -p bcs-protocol
  -p bcs-message-flow -p bcs-message`: 612 passed, including the four new
  timestamp tests and the strengthened Provider transport contract test.
- `cargo test --locked --offline -p bcs --test provider_downlink_integration`:
  all 9 passed. Both final-only and delta-plus-final without a timestamp relay
  the complete response and return exactly one reply in Human session history.
- The delta-plus-final fixture matches the incident: concatenated deltas and
  final text agree. An exploratory fixture with differing text exposed the
  existing history-buffer behavior that keeps accumulated delta text; resolving
  such disagreement is outside this timestamp fix.
- Independent review found no actionable issues. `git diff --check` passed.
- Full-workspace and Singlebox coverage gates were not run; verification covers
  the changed transport and the downstream protocol, message flow and history.
- Checked source sizes: adapter 3274 lines, transport contract test 1901 lines,
  bootstrap integration test 1354 lines. These existing files were kept intact
  per the user's explicit instruction; no size allowlist or CI gate was changed.
