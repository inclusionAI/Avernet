# BCS Changelog

All notable BCS changes are documented here. Items follow
[Keep a Changelog](https://keepachangelog.com/) and use
[Semantic Versioning](https://semver.org/) semantics for the public API.

## [Unreleased]

### Added

- **Explicit Bot owner/manager authority model.** Bots now carry an explicit
  ownership state (`ownership_version`) with an approved owner edge plus
  manager sources (`direct`/`manual`, `team/<team_id>`,
  `ownership_transfer/<transfer_id>`). New Human-only APIs:
  `GET /openapi/v1/collaboration/bots/mine` and the legacy `/bots/my` return
  the owner∪manager union with `access_relation: owner|manager`;
  `GET/PUT/DELETE /openapi/v1/collaboration/bots/{bot_id}/managers/{user_id}`
  (manager list/grant/revoke), the ownership view and confirmation-based
  transfer family under `/openapi/v1/collaboration/bots/{bot_id}/ownership*`
  (pending-transfer creation, party-scoped listings, accept/reject/cancel),
  and the credential-gated trusted-platform team slice
  `PUT /api/v1/bots/{bot_id}/manager-sources/teams/{team_id}` with its
  member-repair lane. The team slice mounts ONLY when the new
  `[team_manager_sync]` section resolves real HMAC key material (process env
  or secret backend); enabled-without-material is a startup configuration
  error and the lane is never mounted anonymously. A `bcs-ownership-migrate`
  maintenance binary governs the one-shot historical backfill
  (`--maintenance inspect/apply`, bounded batches, batch-id replay recovery,
  governed conflict report); see `docs/runbooks/bot-authority-cutover.md`.
- **Composition root wiring of the authority lanes.** The bootstrap
  constructor assembles the authority store as the FIRST datasource consumer
  over the selected database; an unmatched datasource now fails the startup
  with a configuration error instead of panicking. The manager, transfer and
  team facades, the Session connection authorization, the Workbench protected
  delivery gate and the friend lane all consume the one authority lane.

### Changed

- Registration is now honest about ownership initialization: a successful
  register/onboard implies the first owner edge committed (the legacy
  swallow-on-error behavior is gone), while a failed initialization returns
  an error and leaves the pre-existing runtime Bot untouched.
- A Provider switch no longer rewrites `created_by`; re-onboarding,
  ensure-human repairs and reconnects can never re-claim or overwrite the
  current owner/manager facts (`created_by` remains historical origin only).
- Workbench protected frames are continuously re-authorized at both queue
  positions (enqueue AND dequeue; replay counts as a new dispatch). A
  revoked binding drops its protected backlog with no further authority
  reads; `SkipMessage` skips only the frame; connection and binding stay.
  An assembly that never wires the authorization service answers
  `InvalidateBinding` for every protected frame (fail-closed default).

- **Bot WebSocket V3 canonical uplink events.** V3 Bot connections now use
  the Provider Run Event envelope for `agent` and `chat` events, with required
  `runId`, `sessionId`, `seq`, and `ts`. BCS validates the event against its
  server-owned run context and rejects unknown runs, bot/session mismatches,
  terminal or expired runs, and duplicate or regressed sequence numbers.
  `bot.connect` returns explicit capability flags. Only V3 connections with
  `client_kind=native_mcp` may turn an exactly mapped, start/result-paired MCP
  tool result into task intent; V1/V2 events cannot opt into that path. The
  bundled OpenClaw and DeepSeek Harness clients now negotiate V3 and emit the
  canonical `agent`, `chat`, thinking, final, and session identity fields. The
  OpenClaw channel package version is bumped to `1.0.24` for this upgrade.

- `bcs-cli create-group --no-session` creates a Chat or ManagerWorker group
  without an initial Session, GroupContext delivery, or bootstrap run.
  `bcs-cli collaboration create --no-session` also supports StateMachine groups,
  preserving YAML and participant bindings without starting an initial run.
  `POST /groups` accepts `create_initial_session` (default `true`); `false`
  returns null initial Session/run IDs and leaves later explicit Session
  creation available. DM and non-empty inline event-subscription requests
  reject this option before provisioning. Upgrade the server before using it;
  the CLI reports a contradictory Session ID without deleting created resources.
  `start_initial_run` applies only when an initial Session is created.

### Breaking

- **Legacy creator facts no longer authorize current control.** `created_by`,
  the legacy `is_creator` edge, Bot-id suffixes and signed Gateway owner
  claims are no longer permission answers: every "may this Human manage this
  Bot" decision resolves through the live Bot authority store (owner edge /
  manager sources) via the centralized authority hook or the owner∪manager
  mine projection, on every HTTP entry (v1 and legacy) — including the group
  invite minting, member removal, group-visibility and event-subscription
  scope lanes — the Session connection/launch lanes, the session-file
  membership/identity lanes and the Workbench protected delivery. Deployments
  with historical bots must run the
  `bcs-ownership-migrate` cutover (see the runbook) before relying on manager
  abilities; version-0 (uninitialized) Bots answer
  `ownership_not_initialized` on the ownership-dependent Human surfaces.
  Legacy 500 bodies no longer carry internal authority/store diagnostics:
  the full cause is logged server-side and the client sees a fixed generic
  error text.

- Rename `collaboration.experimental_fixed_loop_execution` to
  `collaboration.loop_execution_enabled`. Update existing configuration files;
  the old key is rejected rather than accepted as an alias. The default remains
  `false`, and the switch controls both Loop execution and Loop recovery.

- Remove `collaboration.experimental_progression_recovery`; delete this key
  from existing configuration files. Workflow recovery now starts with the
  service and retains leader election and bounded scanning. Ordinary workflow
  recovery runs without an opt-in; Loop recovery requires `loop_execution_enabled`.

- **Session listing no longer creates a legacy session for an empty group.**
  `GET /groups/{id}/sessions` (including `bcs session list`) now returns
  `200 OK` with `items: []` for sessionless groups without creating a
  `{group_id}:00000000` session or delivering initial GroupContext messages.
  Callers that need a session must explicitly create one. Existing legacy
  sessions and the initial session created during group creation are unchanged.

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
