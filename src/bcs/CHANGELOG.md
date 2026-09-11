# BCS Changelog

All notable BCS changes are documented here. Items follow
[Keep a Changelog](https://keepachangelog.com/) and use
[Semantic Versioning](https://semver.org/) semantics for the public API.

## [Unreleased]

### Breaking

- **Removed `POST /bot/events/coordination` HTTP Provider coordination
  callback.** The `ProviderCoordinationEventRequest` /
  `ProviderCoordinationEventKindDto` / `ProviderCoordinationIntentDto`
  wire DTOs, the `submit_coordination` service trait method, and the
  associated `ProviderBotCoordinationCommand` / `Outcome` /
  `ProviderCoordinationEventKind` / `ProviderCoordinationIntent`
  application types have been deleted. The route now returns `404 Not
  Found`.
  - **Migration:** External Providers must surface coordination results
    as a BCS coordination echo inside the run stream (`agent` /
    `tool_call_end` SSE event) instead of posting to
    `/bot/events/coordination`. The canonical WS echo path
    (`maybe_handle_coordination_echo` → `CoordinationCall::from_stdout`
    → `task.*`) is unchanged and is the single source of truth for
    coordination intake.
  - **Shared symbols preserved:** `CoordinationMode`,
    `ProviderCoordinationConfig`, `ProviderCoordinationConfigDto`,
    `ProviderCoordinationModeDto`, `CoordinationCall`, and
    `CoordinationCall::from_stdout` are unchanged; the sibling
    `POST /bot/events` route and `credential_from_headers` shared
    helper are also unchanged.
  - **Telemetry:** the `gateway_trace` `Span::none()` suppression for
    `/bot/events/coordination` was removed together with the route (the
    route no longer exists, so the special-case span suppression is no
    longer reachable).
  - **Operational follow-up:** monitor `404` counts for
    `/bot/events/coordination` after rollout; revert the feature branch
    (`git revert`) if an external Provider still relies on it.

### Removed

- `post_coordination_event` route handler and `coordination_kind_from_wire`
  helper in `bcs-http/src/routes/bot_events.rs`.
- `submit_coordination` impl and the coordination-only helpers
  (`coordination_call_from_command`, `coordination_intent_to_call`,
  `dispatch_coordination_call`, `coordination_argument_str`,
  `authenticate_coordination`) inside `bcs-bot` `ProviderBotEvents`.
- Six coordination HTTP contract tests in
  `bcs-http/tests/bot_events_contract.rs`; the dual-intake
  (`reference_coordination` module) contract test in
  `bcs-message-flow/tests/contract_bot_event.rs`.
- `submit_coordination` noop in `bcs-test-support`.