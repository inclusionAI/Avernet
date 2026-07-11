# Hermes BCN Connector Design

## Context

Avernet's public BCN onboarding UI currently assumes every local bot is an
OpenClaw bot. Both the landing-page access section and the in-workbench
"Connect Bot" dialog expose the same two OpenClaw-specific instructions:

- a command that installs `openclaw-channel-bcn`; and
- a prompt that asks an OpenClaw bot to install that channel itself.

BCN itself is not OpenClaw-specific. Its documented WebSocket protocol already
supports any engine through `bot.connect`, `bot.status`, `chat.send`,
`chat.inject`, `chat.abort`, `chat.history`, and `chat.event`.

The current upstream default branch is `dev`; the repository has no `main`
branch. This design is based on `origin/dev` commit
`0a327b354652f1589fcf7e2528a24b8d2c978af8`.

## Goal

Add Hermes as a first-class choice in the existing BCN bot onboarding flow so
an already configured local Hermes profile can join BCN without being wrapped
as an OpenClaw bot.

The user-visible result is one additional bot type:

```text
Bot type:       OpenClaw | Hermes
Access method:  Self-service | Bot-assisted
```

Selecting Hermes produces a Hermes-specific command or instruction while
reusing the current six-hour human registration token.

## Non-goals

This MVP does not:

- add an HTTP Provider/Webhook platform;
- add Hermes-specific behavior to the BCN server;
- duplicate model, provider, API-key, Skills, or MCP configuration in Avernet;
- require a BaaS bot or the Avernet Engine service to be running;
- render Hermes tool, approval, clarification, or secret-request events in BCN;
- change the existing OpenClaw onboarding path;
- install a system login service that survives a machine reboot; or
- encode a product-manager prompt in the generic connector.

Role configuration remains part of the selected Hermes profile. The connector
only transports messages.

## Design Decision

Use a small standalone `avernet-hermes-bcn` connector that owns two client
connections:

```text
BCN /ws/bot
    ^
    | BCN protocol v2
    |
avernet-hermes-bcn
    |
    | authenticated JSON-RPC over /api/ws
    v
Hermes Dashboard -> configured Hermes profile -> configured model provider
```

The connector talks directly to Hermes Dashboard's documented TUI Gateway
JSON-RPC protocol. It does not route messages through BaaS or the Community
Engine adapter. This keeps onboarding independent from Avernet's deployment
stack and reuses Hermes' native persistent sessions and streaming events.

The earlier Hermes Community Engine MVP remains useful as protocol evidence,
but it is not a prerequisite for this connector. Pulling that full engine
implementation into the onboarding path would add unrelated session, engine,
and BaaS composition layers.

## Frontend Experience

### Resource contract

Keep the existing OpenClaw resource fields for compatibility and add two
nullable Hermes fields to `Resources`:

```ts
bcnHermesConnectCmdTemplate: string | null;
bcnHermesAutoConnectCmdTemplate: string | null;
```

The open-source extension supplies public defaults containing the existing
`{token}` placeholder. Internal builds may replace either template through the
existing resource extension mechanism. No backend API or token schema changes.

### Connect Bot dialog

`AddBotGuideModal` adds a compact `OpenClaw | Hermes` segmented control above
the existing access-method control. OpenClaw remains the default so current
behavior is unchanged.

The selected engine determines:

- which pair of templates is used;
- whether the copy says OpenClaw or Hermes; and
- which engine icon/label is shown.

An engine is hidden when both of its templates are `null`. The copy button
keeps the current login/token behavior.

### Landing-page access section

`AccessSection` uses the same engine selector and renders the existing two
method cards for the selected engine. This avoids four simultaneous command
cards and keeps the current page density.

The engine/template selection should be expressed by a small shared data
helper, not duplicated conditional trees in both components. Clipboard logic
is outside this change.

## Connector Layout

The MVP is intentionally self-contained under BCN:

```text
src/bcs/connectors/hermes/
  hermes_bcn.py
  tests/
    test_bcs_protocol.py
    test_hermes_gateway.py
    test_bridge.py

src/bcs/docs/install-instructions/
  install-hermes.sh
  install-hermes.md
```

`hermes_bcn.py` contains three small responsibilities without introducing a
general connector framework:

1. `BcsClient`: BCN connection, handshake, heartbeat, reconnect, request ACKs,
   and outbound events.
2. `HermesClient`: authenticated Dashboard JSON-RPC requests, session mapping,
   and prompt event streaming.
3. `HermesBcnBridge`: per-group sequencing and protocol translation.

The file also exposes `register`, `run`, `start`, `stop`, and `status` CLI
commands. Splitting it into a package is deferred until another non-OpenClaw
connector needs the same lifecycle.

## Installation Flow

The self-service command runs `install-hermes.sh` with the current human token.
The installer:

1. Checks `hermes`, `python3`, `curl`, and a valid configured Hermes profile.
2. Accepts `--bot-name`, `--profile`, and `--bcs-endpoint`; omitted values are
   prompted or use documented defaults.
3. Reuses an existing valid `${HERMES_HOME}/bcn/session.json` by default, or,
   after explicit replacement confirmation, calls the existing
   `POST /register?token=...&bot-name=...` endpoint.
4. Immediately persists newly returned BCS credentials with mode `0600` so an
   interrupted installation can resume without registering a second bot.
5. Downloads `hermes_bcn.py` into a user data directory through a temporary
   file and replaces the installed copy only after a Python syntax check.
6. Creates a connector-only virtual environment and installs
   `websockets>=14,<16`.
7. Starts the connector in the background and waits until both sides are
   healthy.

Default locations are:

```text
code:    ${XDG_DATA_HOME:-~/.local/share}/avernet/hermes-bcn/
config:  ${HERMES_HOME}/bcn/session.json
state:   ${HERMES_HOME}/bcn/groups.json
pid:     ${HERMES_HOME}/bcn/connector.pid
log:     ${HERMES_HOME}/bcn/connector.log
```

For a named profile, `HERMES_HOME` resolves to
`~/.hermes/profiles/<profile>`. The installer uses the profile as-is and never
copies API keys into Avernet-owned files.

`USE_CN_MIRROR=1` or `--china-mirror` configures the connector virtual
environment through the repository's existing public PyPI mirror convention.
`PIP_INDEX_URL` takes precedence. `AVERNET_RAW_BASE_URL` may point source
downloads at an organization-controlled mirror; the installer does not
hard-code an unaffiliated GitHub proxy.

The bot-assisted instruction points Hermes to `install-hermes.md`, which
performs the same checks and registration while requiring explicit user
confirmation before replacing an existing `${HERMES_HOME}/bcn/session.json`.

## Hermes Lifecycle

By default the connector starts one private Hermes Dashboard child for the
selected profile. It chooses a free loopback port, generates a cryptographically
random dashboard token, and launches:

```text
HERMES_HOME=<selected profile>
HERMES_DASHBOARD_SESSION_TOKEN=<generated token>
hermes dashboard --host 127.0.0.1 --port <port> --no-open
```

The connector passes the same token to `/api/ws?token=...`. The token is never
printed or placed in process arguments. The Dashboard remains loopback-only.

`start` is idempotent and refuses to replace a live PID. `stop` sends SIGTERM,
waits for the connector and its owned Dashboard child, and only then removes
the PID file. A stale PID file is repaired after verifying the recorded
process is absent. On restart, the connector reuses the recorded Dashboard port
when available; if an unrelated process owns that port, it selects a new free
port and atomically updates the private config.

## Identity And Persistent State

The installer uses human-token registration so the bot is owned by the logged
in user and has the requested display name before its first WebSocket
connection. The connector then reconnects with both `bot_uuid` and `bot_token`
using protocol version 2.

`${HERMES_HOME}/bcn/session.json` stores:

- BCS WebSocket URL;
- BCS bot UUID and bot token;
- display name;
- selected Hermes home and workspace;
- Dashboard port and token; and
- connector version.

`${HERMES_HOME}/bcn/groups.json` maps each BCN group/session scope to a Hermes
persistent `stored_session_id` and any pending silent observations. Writes use
temporary-file plus atomic rename. Both files are owner-readable only.

## Protocol Translation

### Connect and health

- Send `bot.connect` with `bot_id`, `token`, and `protocol_version: 2`.
- Persist a replacement token if BCN rotates it.
- Send `bot.status` every 60 seconds.
- Reconnect with exponential backoff from one to 30 seconds.
- Restart an owned Dashboard child with the same bounded backoff if it exits.

### `chat.send`

1. Generate a bridge `run_id` and ACK immediately.
2. Acquire the lock for the BCN group; different groups may run concurrently.
3. Create a Hermes session with `source: "avernet-bcn"` when no mapping exists,
   or resume the stored session after connector/Dashboard restart.
4. Prepend pending `chat.inject` observations to the current protocol-v2 text.
5. Submit the prompt through `prompt.submit`.
6. Translate `message.delta` into cumulative BCN `chat.event` delta frames.
7. Translate `message.complete` into one final frame, including usage when
   Hermes provides it.
8. Translate a terminal Hermes error into a BCN error frame.

Pending observations are cleared only after Hermes accepts the prompt. A failed
submit keeps them for the next attempt.

### `chat.inject`

ACK immediately and append a compact sender/message record to the group's
pending observations. Do not call `prompt.submit`, so Hermes cannot reply to an
observation-only message.

The buffer is limited to 256 observations and 64 KiB per group. It discards
oldest observations first when either limit is exceeded. BCN protocol v2
already embeds readable group context in later `chat.send` frames, so this
buffer preserves only the turns that otherwise would not enter the Hermes
session.

### Abort and history

- Map `chat.abort` for the active group to `session.interrupt` and emit an
  aborted terminal event when Hermes confirms interruption.
- Map `chat.history` to `session.history`, normalize user/assistant text, and
  apply the requested limit.
- Unknown BCN request methods receive a normal `unknown_method` response; they
  do not terminate the connection.

Tool and approval events are consumed but not forwarded in the MVP. The final
assistant message still contains the result after successful tool use.

## Concurrency Rules

Only one Hermes turn may run per BCN group. A second `chat.send` for the same
group queues behind the active turn; other groups use independent sessions and
locks. The bridge tracks active BCN `run_id` to Hermes live session id so abort
targets the correct turn.

A Dashboard reconnect invalidates live session ids but not stored session ids.
The next operation calls `session.resume` and refreshes the in-memory mapping.

## Failure Behavior

- Invalid or expired human tokens fail installation before local files change.
- Existing BCS credentials are reused by default; replacement requires an
  explicit answer.
- Missing Hermes configuration fails before bot registration when possible.
- A registration success followed by local setup failure leaves the protected
  credential file in place and prints an exact resume command with the bot UUID,
  without printing either token or registering a replacement bot.
- BCS downtime marks the bot offline while the connector keeps retrying.
- Hermes downtime produces a terminal error for the accepted BCN run and then
  triggers Dashboard recovery.
- Malformed frames are logged with secret fields redacted and do not crash the
  reader loop.
- `stop` never kills a Dashboard process it did not start.

## Local Product-manager Rollout

The generic connector does not alter the repository's default six-demo-bot
singlebox stack. For the local target team, rollout is operational and uses an
untracked local configuration:

1. Clone the configured `avernet-default` Hermes profile to a dedicated
   `avernet-product-manager` profile so provider/model settings are preserved.
2. Configure that profile's role as product manager without changing the
   user's general Hermes profile.
3. Connect it to BCN with display name `产品经理`.
4. Verify a real group reply before removing any existing bot.
5. Stop and remove the old OpenClaw `产品经理`, `glm5`, and backend-created
   `developer` entries.
6. Keep the four existing OpenClaw roles: `CEO`, `研发`, `验证`, and `客服`.

The persistent local launch path starts `baas`, `backend`, `bcs`, and
`frontend` explicitly, starts the four OpenClaw profiles from a local dynamic
profile directory, and starts the Hermes connector. It does not use the
singlebox `all` group, because that group intentionally recreates the five
OpenClaw demo bots and the backend `developer` bot.

Expected final BCN roster:

| Display name | Engine | Role |
| --- | --- | --- |
| CEO | OpenClaw | Team lead |
| 产品经理 | Hermes | Product manager |
| 研发 | OpenClaw | Engineering |
| 验证 | OpenClaw | Verification |
| 客服 | OpenClaw | Customer support |

## Verification

### Connector tests

- BCS handshake, token persistence, heartbeat, reconnect, and secret redaction.
- Immediate `chat.send` ACK followed by delta/final translation.
- Silent `chat.inject` buffering and inclusion in the next prompt.
- Persistent Hermes session resume after Dashboard reconnect.
- Per-group serialization and cross-group concurrency.
- Abort, history, malformed-frame, and terminal-error behavior.
- Installer idempotency, existing-credential confirmation, profile validation,
  file modes, mirror selection, and no-secret logging.

An integration test runs fake BCN and Hermes WebSocket servers around the real
bridge loop. It proves the complete frame sequence without a model call.

### Frontend tests

- OpenClaw remains selected by default.
- Hermes selection switches both self-service and bot-assisted templates.
- `{token}` replacement remains correct for both engines.
- Engines with two `null` templates are hidden.
- Landing page and modal expose the same choices and descriptions.

### Live acceptance

1. The workbench's Connect Bot dialog shows `OpenClaw | Hermes`.
2. The Hermes command registers `产品经理` without installing OpenClaw.
3. BCN reports that bot online after connector startup and after a connector
   restart with the same UUID.
4. A real group mention receives a response from the configured Hermes model.
5. A prior observation-only message is visible to Hermes on its next turn.
6. The final visible roster contains exactly the five roles listed above.
7. `glm5`, `developer`, and the old OpenClaw product-manager process are absent.

## Implementation Boundaries

Expected repository changes are limited to:

- two frontend resource fields and their open-source defaults;
- the two existing onboarding components plus focused tests;
- one self-contained Hermes connector and tests;
- Hermes self-service and bot-assisted installation instructions; and
- documentation index updates.

BCN Rust services, BaaS engine dispatch, backend bot schemas, and existing
OpenClaw plugin behavior remain unchanged.
