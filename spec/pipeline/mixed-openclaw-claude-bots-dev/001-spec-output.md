# Singlebox Mixed OpenClaw and Claude Code Bots

## Goal

`./scripts/singlebox.sh --standalone --claude-bots-config <local-json> restart all`
starts exactly five existing OpenClaw profiles and three BaaS-managed Claude Code bots.  The Claude bots use independent relay processes and are registered as local BCS Provider bots.

## Design

- The option is opt-in. Without it, the existing singlebox start order and default demo bot are unchanged.
- The local JSON is ignored by Git. It contains three bot records: `planner`, `developer`, and `reviewer`; each record supplies an isolated Claude config directory, workspace, and relay port.
- Three relay processes run from Avernet's vendored Claude gateway. BaaS starts one Engine adapter per Claude bot and receives the record-specific relay URL through non-secret bot template configuration.
- When a role-specific Claude config directory has no `settings.json`, its relay
  may read the local default Claude `settings.json` as a model-provider source.
  Only the required Anthropic-compatible model environment keys and timeout are
  injected into the Claude child process; credentials are neither copied to the
  role directories nor logged. A role-specific `settings.json` always takes
  precedence, preserving independent profile configuration.
- A local BCS Provider bridge translates BCS downlink requests to BaaS streaming downlink requests and converts the response stream back to BCS SSE.
- BCS authenticates this webhook with its Provider-level
  `bcs_to_provider_token`, not a per-bot runtime token.  The bridge validates
  that credential and independently restricts `provider_bot_ref` to the three
  locally registered roles before forwarding to BaaS.
- The Backend bot owner from `entity_id` remains in each `provider_bot_ref` for
  BaaS binding lookup.  The BCS Provider bot itself is owned by the active
  local BCS user (`BCS_MOCK_USER_ID`, normally `001`) and is published as
  `public`; this makes the three Claude bots appear beside the five OpenClaw
  bots in the frontend and selectable in a mixed group.
- A restart must remove the three BCS Provider bots that belong to its prior
  local Provider before it discards that Provider's runtime credentials.
  Otherwise BCS retains visually identical, unauthenticated stale bot cards
  and a user can select a bot whose downlink token no longer exists.  The
  active run uses an explicit `（当前）` BCS display-name suffix so historical
  cards from an older implementation remain distinguishable without deleting
  user groups or unrelated BCS data.
- Relay startup must verify the selected Claude executable with the
  no-side-effect `--version` command before it is passed to the SDK.  An
  explicit `CLAUDE_CODE_PATH` is authoritative and fails closed when unusable;
  automatic discovery skips a broken native installation and may select a
  working existing `claude` executable on `PATH`, `/opt/homebrew/bin`, or
  `/usr/local/bin`.  The selected path is never logged as a credential and no
  model request is used for this preflight.
- Runtime diagnostics include role, bot identity, port, and run ID only. They never emit credentials or complete prompts.

## Acceptance

1. Default `restart all` behaviour stays unchanged.
2. Mixed mode validates exactly three unique roles and ports, starts three isolated relays, and suppresses the default demo bot.
3. BaaS receives each Claude bot's own relay URL when it allocates the adapter.
4. The Provider bridge preserves run IDs and maps send, inject, history, and error traffic.
5. Automated tests cover configuration, lifecycle ordering, env propagation, and bridge protocol translation. A real local smoke test uses no-side-effect prompts only.
6. Repeated mixed `restart all` removes the previous run's three Provider bots
   with the Provider admin credential, and the BCS picker exposes exactly one
   current planner/developer/reviewer trio with the `（当前）` suffix.
7. When the default Claude executable exits abnormally during `--version`, a
   healthy discovered fallback is injected through `CLAUDE_CODE_PATH`; a real
   BCS group message then reaches a Claude `final` event rather than only
   relay health.
8. A human sender identity is request metadata only. The frontend transport
   must map it to the BCS Workbench `bot_id` sender field and must never pass
   it through the legacy `bot_uuid` field. A message with no selected `@`
   mention must therefore retain an empty target and be routed by BCS to the
   group's Driver bot.
9. `start frontend` must leave the Umi/Tailwind process alive after
   `singlebox.sh` exits. Its stdin keeper must be in the same no-hangup process
   group as npm; readiness is valid only while the port remains owned by that
   process group.
10. `status all` must initialize BCSFuse's standalone runtime paths before it
    reads its PID file, so a healthy BCSFuse listener is reported with a health
    result instead of a false `pid_file: stale` fallback.
11. A newly created normal Chat group retains the BCS default `SessionContext`
    delivery: the Driver receives `chat.send` and other participants receive
    `chat.inject`. This applies equally when the group contains a
    Provider-downlink bot; only an explicit `driver_delivery` override and the
    existing ManagerWorker semantics alter the Driver delivery.
12. In the local mixed singlebox UI, the exact legacy local role names
    `Claude Planner`, `Claude Developer`, and `Claude Reviewer` are hidden
    from tabs and the collaboration picker whenever their `（当前）` counterparts
    are present. Existing groups are not mutated; users create a fresh group
    to replace members that belong to retired Provider registrations.
13. `restart all` must not perform an unbounded BCN plugin dependency install
    while starting the five OpenClaw bots. When the source plugin has a usable
    `dist/esm/index.js`, runtime startup uses that artifact even if source files
    are newer. It records a metadata-only rebuild hint; an absent artifact fails
    fast and directs the operator to the explicit BCS setup step.

## Implemented safety and protocol decisions

- The Provider bridge retains a single loopback webhook URL while accepting h2c
  prior-knowledge SSE for `chat.send` and HTTP/1 callbacks for `chat.inject`
  and `chat.history`. The two protocol-native listeners are only reachable
  through that one loopback bridge port. The TCP classifier buffers a split
  h2c preface before selecting a listener.
- The local bridge explicitly rejects `chat.abort`: the BCS Provider webhook
  payload preserves the newly-created abort request ID, but does not carry a
  safe target stream ID. It must not guess and cancel an unrelated BaaS run.
  An HTTP/SSE client disconnect still aborts that request's local upstream
  fetch through its own `AbortController`.
- Provider runtime credentials are exact token-to-`provider_bot_ref` bindings;
  legacy unbound token records are rejected. Claude config directories,
  workspaces, and bot names are unique after path normalization.
- Backend accepts only canonical relay URLs for the declared role:
  `ws://127.0.0.1` on its fixed role port, with no user info, query, fragment,
  or non-root path. BaaS logs only whether a relay URL was supplied.
- The local `singlebox_claude` relay override is accepted only for
  `claude_code` Bots with a missing, blank, or `normalCC` template type.
  Other Claude Code template types keep their existing generic provisioning.
- Per user direction, this worktree leaves BCS Provider diagnostics unchanged
  from the base implementation. The new loopback bridge's own diagnostics do
  not emit request bodies, bridge credentials, sensitive webhook URL
  components, or chat text.
- The singlebox guard suite must not assert a BCS logging change or the
  reverted Provider-downlink SessionContext override. Those are outside the
  two retained BCS business changes, so the guard checks lifecycle behavior
  without creating a failing requirement against the unchanged base code.
- BaaS's local SQLite bootstrap supplies the configured internal BCN API-key
  identity.  The downlink service uses that record solely to construct an
  internal Bot chat context after the bridge has authenticated the request;
  it is not a replacement for either the bridge-to-BaaS bearer credential or
  BCS's Provider credential.  Missing this record must fail with a diagnostic
  error rather than looking like a Claude model timeout. The database plugin
  seeds it through the SPI metadata table, without importing a Core ORM
  implementation into the plugin layer.
- Per user direction, BCS Bot WebSocket and token-store diagnostic behavior is
  unchanged from the base implementation.
- Claude relay diagnostics similarly report whether model-provider credentials
  are present, never their value or a prefix of it.
- Mixed `start all` and `restart all` perform an ownership-aware port preflight
  before stopping anything. Every relay plus the Engine, BaaS, Backend, BCS,
  BCSFuse, Provider bridge, and Frontend listener must be free or owned by this
  worktree; an external checkout blocks the operation without changing either
  stack.
- `restart all` dispatches only to the `all` group lifecycle; it cannot fall
  through to individual-service restarts. Its only success condition is the
  full topology readiness check, so a Umi frontend listener alone must never
  be presented as an all-stack success.
- The mixed-only generated BCS runtime config permits the loopback Provider
  callback needed by the local bridge, while retaining private-network blocking
  for all non-loopback destinations. Source and default BCS configs remain
  strict.
- The frontend's user-collaboration sender passes only explicit mention IDs as
  a target. Its human actor identity is derived in `useGroupChat` and copied
  by the session transport into the BCS `bot_id` sender field, never
  `bot_uuid`; this satisfies Workbench sender authorization without making an
  unmentioned message target a human actor instead of the Claude Driver.
- The frontend launcher owns both the npm dev server and its long-lived stdin
  keeper under one `nohup` shell. This prevents a background pipeline's keeper
  from receiving a shell-exit hangup and making Tailwind terminate after an
  otherwise successful readiness probe.
- Frontend send diagnostics report content length, identifiers' presence, and
  counts only. They must never pass the assembled send params (which include
  the user message) to the browser console. BCSFuse status loads its runtime
  environment before inspecting the PID file, including in standalone mode.
- A Provider-downlink group context does not override BCS's normal Driver
  semantics: the Driver receives the ordinary `chat.send` result and
  non-Drivers receive `chat.inject`. This preserves the existing visible
  initialization contract for every normal Chat group.

## Automated evidence

- `scripts/test_singlebox_mixed_claude_bots.sh`: strict 5+3 topology,
  lifecycle rollback/preflight, and isolation validation.
- `scripts/test_singlebox_service_guards.sh`: verifies a mixed stack rejects an
  external listener during preflight, before its restart path can stop a
  service, and checks BaaS readiness against its loopback health endpoint.
- `scripts/test_bcn_plugin_source.sh`: verifies the five-bot runtime start
  reuses a present BCN `dist` artifact without invoking npm, including when a
  source TypeScript file is newer than that artifact.
- `scripts/test_claude_relays.sh`: all three isolated gateway processes reach
  health and do not create a workspace `CLAUDE.md`.
- `scripts/test_bcs_baas_provider_bridge.mjs`: real h2c streaming and HTTP/1
  callback contract tests for delta/final/error, inject/history, reference
  authorization, and explicit abort rejection.
- BCS, Backend, BaaS, and vendored gateway focused unit suites cover abort
  serialization, per-bot adapter environment forwarding, downlink credential
  handling, and role prompt/data-directory isolation.

## Local runtime evidence

On 2026-08-08, the mixed topology completed in mock-model mode: all three
relays, BaaS, Backend, BCS, BCSFuse, five OpenClaw bots, three normalCC bots,
the Provider bridge, and Frontend started; BCS registered one Provider and
three Provider bots. A h2c Developer probe traversed BCS Provider bridge,
BaaS, the normalCC adapter, and the Developer relay, and the relay resolved
the configured default model-provider settings. In the Codex-launched local
process environment, the final Claude CLI child was then terminated with
`SIGKILL`; this is distinct from authentication and must be rechecked from the
user's own terminal before treating it as a model-provider failure.

The final acceptance was repeated from a real macOS Terminal after the native
CLI probe fallback selected the working existing CLI. A fresh BCS group routed
a no-side-effect user message to the current Developer Provider bot. The
browser rendered the Developer final reply, while the BCS, bridge, BaaS, and
relay diagnostics correlated the same run without recording the message body
or credentials. The frontend was also restarted from that terminal and still
owned port 8000 after `singlebox.sh` returned.

## Claude inject session-store invariant (2026-08-09)

- A relay's `chat.inject` entry and its subsequent SDK `resume` must use the
  same role-specific Claude projects root.  For a relay configured with
  `RELAY_CLAUDE_CONFIG_DIR=<role-config>`, that root is
  `<role-config>/projects`; it must never fall back to the shared
  `~/.claude/projects` directory.
- The JSONL existence probe follows the identical root-selection rule.  This
  prevents an inject from being persisted to a detached transcript while the
  next `chat.send` resumes the real role-specific transcript.
- Regression acceptance: a Developer inject with a unique marker is written
  under `~/.claude-developer/projects`, the old shared projects directory is
  untouched, and the session probe resolves the role-specific transcript.

### Live acceptance probe

- Create an isolated three-bot group with CEO as the driver, with no Developer
  SDK session established before sending any marker.
- Send a no-side-effect marker request to Reviewer. Its delivery to Developer
  must be recorded in the relay's in-memory history before the first Developer
  send, then in `~/.claude-developer/projects/<encoded-workspace>/<sdk-session>.jsonl`
  after that send creates the session.
- The first explicit Developer request must contain the marker in its final
  response. The probe output may contain only boolean/count metadata, never
  marker text, conversation text, credentials, or full IDs.

## Cold-start inject replay invariant (2026-08-09)

- In a new BCS group, `chat.inject` can precede a Claude bot's first
  `chat.send`. Until that first send creates an SDK session, the relay has no
  native JSONL transcript to append to; those injects therefore exist only in
  the relay session store.
- For that cold-start sequence, the first model request must include every
  stored inject entry that has neither been written to a native transcript nor
  explicitly replayed in its user prompt before the current request. It must
  not rely solely on `appendSystemPrompt`, because an
  Anthropic-compatible relay/provider may not preserve that appended context
  as a model-visible conversation turn.
- The original current user request remains the final portion of the model
  prompt. The relay's own stored chat history continues to keep the original
  request, not the expanded prompt. A successful explicit replay is persisted
  as metadata-only state so it occurs at most once. Once an SDK
  session/transcript exists, normal role-scoped JSONL inject persistence
  remains authoritative and must not be duplicated. This also repairs a
  pre-fix session on its next successful Developer turn.
- Diagnostics may record only cold-inject count, replayed character count and
  current request length; they must never log injected text, prompts, tokens,
  or credentials.

### Cold-start regression acceptance

- A gateway test sends two `chat.inject` requests to a fresh session, then a
  first `chat.send`.
- Its fake model runner must receive both inject markers and the original
  current request in `params.message`; neither marker may be present only in
  `params.systemPrompt`.
- The test must fail under the old system-prompt-only behavior and pass after
  the replay implementation.
