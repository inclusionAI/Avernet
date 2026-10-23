# @avernet-plugin/deepseek-harness-channel-bcn

DeepSeek Harness channel bundle for connecting a DSH Bot to the Avernet Bot
Collaboration Network (BCN).

## 0.2.4

- Reject Provider onboarding tokens without explicit `static_bearer` auth before registration, avoiding unusable Bot registrations.
- Install the matching 0.2.4 plugin release by default.

## 0.2.3

Validate DeepSeek Harness `0.2.1-rc.2` compatibility, retaining the existing
`0.2.0-rc.2` lower bound and legacy Bot Session support.

## 0.2.2

- Support Provider-scoped v2 onboarding Tokens with `static_bearer` authentication and `plugin` mode.
- Persist the local Provider reference before registration and retain Provider membership across restarts and Token rotation.
- Keep existing legacy Bot Sessions working without automatic re-registration.

## 0.2.1

Fix approval and question routing for BCN-owned Agents when the DSH Web UI is
also enabled. BCN interaction handlers now run before the Web UI fallback, so
pending requests reach BCN instead of waiting only in the native Web UI.
Other Agents retain their existing interaction handlers.

## Compatibility

- DeepSeek Harness declared compatibility: `>=0.2.0-rc.2 <=0.2.1-rc.2`
- DeepSeek Harness development/test baseline: `@deepseek-ai/dsh 0.2.1-rc.2`
- BCN Bot WebSocket protocol: V3
- Node.js: `>=22.19.0`

This release requires BCN V3. Every `chat.send` and `chat.inject` must carry the canonical
`bcs_session_id`; uplink events use the shared Run Event envelope with
`runId`, `sessionId`, `seq`, and `ts`. The adapter still keeps the historical
session-key mapping helper for persisted sessions created by older releases.

## Capabilities

First-time setup exchanges a legacy human Token or a Provider-scoped v2 Token through `POST /register`. That
endpoint creates the Bot and establishes its display name and ownership. On
restart, the plugin reuses the saved Bot Session and connects directly to
`/ws/bot`; it does not re-register or require a separate `/bots/onboard` call.

Provider-scoped onboarding uses `mode=plugin` and a generated local
`provider_bot_ref`. Only `static_bearer` Providers are supported by this client;
AgentPass registration is not inferred from a generated local identity. The server
authenticates the Token, checks the human/Provider scope, and persists membership.
The plugin verifies the returned Provider/ref/mode, then saves that metadata with
the Bot Session. Provider-admin credentials are never needed on the local machine.

Before the request, the plugin saves a non-secret pending identity in the managed
`${botSessionRef}_REGISTRATION` credential. Retries retain the same reference;
changing the endpoint, Provider, owner or Bot name while registration is pending
requires a separate profile. A lost successful response or failed Session save
can leave a registered Bot without local credentials: the server returns 409 for
duplicate references and never replays credentials. The plugin stops rather than
silently creating another Bot; an operator must recover that registration.

Existing Bot Sessions without Provider metadata remain valid and are not
automatically re-registered or migrated. Provider membership for an existing Bot
must be attached through the server-side administration process. A Token is only
needed for first registration; restarts and runtime Token rotation preserve membership.

Current registration only publishes the Bot name and ownership. The configured
`summary`, `domains`, `skills`, and `scopes` are not synchronized by this plugin;
do not rely on these settings for skill-based discovery. Descriptor synchronization
and propagation of server-side onboarding persistence failures require a separate
registration contract update. A successful `/register` response currently cannot
prove that the server persisted its optional descriptor.

- Automatic registration and persistent Bot credentials through DSH Credentials
- Persistent Bot Session storage through the official `ctx.credentials` seam
- `chat.send`, `chat.inject`, and exact-run `chat.abort` downlink handling
- Isolated DSH Agent/Session reuse for each BCN conversation, including
  multiple sessions that share one group-level `session_key`
- DSH Agent preset composition matching Web sessions; new BCN sessions use the
  configured default preset and resumed sessions restore their recorded preset
- Assistant delta, final, error, and aborted uplink events
- Canonical `agent/tool` start and result events for DSH tool calls
- BCN coordination tools selected from the authenticated group type and
  recipient role carried by each downlink
- `bcs_route` capture with routing metadata attached to the final chat event
- Manager-worker `task.dispatch`, `task.message`, and `task.complete` support
- Heartbeat, exponential reconnect, token rotation, and lifecycle cleanup
- Agent-scoped execution approvals and structured questions through BCN
  `interaction` events and authenticated `interaction.resolve` requests

The plugin does not create an OpenClaw-style `.bcs/session.json` file or any
other private session directory.

This release does not implement `chat.history`.
Unsupported BCN requests receive the WebSocket client's standard `NOT_FOUND`
response, and unsolicited unsupported events are ignored.

`chat.abort` requires `session_key` (the canonical BCN session ID) and
`run_id`. It cancels only that run: pending agent creation cannot deliver after
the abort, queued input is removed by message ID, and active turns use
`cancel({ kind: 'user' }, { keepInbox: true })`. Other queued runs survive.
Repeated aborts acknowledge an empty `aborted_run_ids`; unknown runs and
cross-session requests fail. Terminal events are emitted once.

DSH 0.2 uses scoped live events and V4 tool-result messages. This adapter
subscribes in each owned Agent's setup, reads tool identity from the message's
top-level `toolCallId`, and never subscribes to replayed history. Assistant
text is forwarded as deltas after each durable `assistant/message` commit,
including interrupted prefixes. Failed/retried `assistant/attempt` output and
raw reasoning are excluded; this deliberately buffers text until a step
commits because BCN's append-only delta stream cannot retract a failed attempt.
The scoped `agent/assistant-stream` feed retains only the current attempt's
visible text so an explicit abort can preserve its partial answer.
The historical `bcn-v2-*` DSH session IDs remain unchanged. DSH's own storage
migration handles old session formats; the plugin never rewrites session logs.

Execution approvals offer only `allow_once` and `deny`. Question options use
their DSH labels as wire values; free-text answers return as `custom`.
Cancellation and unload settle pending waits without granting approval.
Timed questions attach to DSH's official foreground wait and respect its
remaining duration. Late answers after that foreground wait closes are not
accepted by this adapter. Mode-switch interactions are not implemented.
These hooks are installed only on BCN-owned Agents and do not intercept Web
sessions or subagents.

BCN messages run in a dedicated in-process DSH Agent created through
`ctx.agents.create` or restored through `ctx.agents.resume`; they do not reuse a
browser tab's live Agent. The plugin mounts that session's DSH Agent preset
before publishing the Agent, then adds only the BCN tools allowed for that
session:

| BCN session | BCN tools added by this plugin |
| --- | --- |
| Ordinary structured-routing group | `bcs_route` |
| Manager in a `manager_worker` group | `bcs_assign_task`, `bcs_task_complete` |
| Worker in a `manager_worker` group | `bcs_send_task_message` |
| Mention-routing group, or manager-worker session without a valid recipient role | None |

The manager/worker decision uses only the server-delivered
`session_context.recipient_role`; it does not infer authority from Bot names,
participants, environment variables, or model input. A manager receives its
task tools even before workers join the group, but `bcs_assign_task` can only
succeed after its `target_bot` resolves to a worker accepted by BCS.
Task RPCs carry the canonical `group:session` identity in their existing
`group_id` wire field, which BCS unwraps into group and conversation scope.
They do not fall back to a shared group's default conversation.

With DSH's default `standard` preset the base tool set includes the Bash,
filesystem, search, Skills, planning, subagent, and workflow capabilities
selected by DSH. Actual command and file access remains governed by the host's
DSH sandbox and permission preset.

## Install

### One-command setup

After this package is published, install, configure, and start a DSH profile
with the repository installer:

```bash
curl -fsSL https://raw.githubusercontent.com/inclusionAI/Avernet/dev/apps/bcs/crates/plugins/deepseek-harness-channel-bcn/install-dsh.sh | \
  BCN_ONBOARDING_TOKEN='<registration-token>' bash -s -- \
    --endpoint http://127.0.0.1:21000/ \
    --profile web \
    --bot-name 'DeepSeek Harness Bot'
```

The installer first checks whether the `dsh` executable is available. If it is
missing, it exits without consuming the registration Token and prints the
matching CLI installation command:

```bash
npm install --global @deepseek-ai/dsh@0.2.1-rc.2
```

Run the generated onboarding command again after installing the CLI. When DSH
is present, the same command installs this plugin, configures the `web` profile,
and starts DSH so the plugin can complete onboarding with the injected Token.
The DSH plugin manager checks the peer compatibility range declared in this
package (`>=0.2.0-rc.2 <=0.2.1-rc.2`) before activation. Existing
`0.1.1-rc.2` installations must be upgraded first. Development dependencies
remain pinned to `0.2.1-rc.2` so builds and unit tests remain reproducible.

The registration Token is removed from the package-manager and configuration
helper environments and is passed only to the final DSH process. The installer
does not print or persist it. If the command is generated by a trusted BCN
portal, avoid copying it into shared shell history or logs because the command
itself contains the short-lived Token.

Pass `--no-start` to install and configure without launching DSH. In that mode
the Token is deliberately discarded and must be supplied again on the first
start. `--package <directory-or-tarball>` replaces the npm package spec for
local and release-artifact testing.

The installer delegates profile changes to the packaged
`dsh-bcn-configure` command. That helper preserves unrelated patch rows and
`!!js` expressions, refuses symlinked profile configuration, writes atomically,
and rolls back if `dsh --dump-config` rejects the composed profile.

### Manual and release-artifact setup

From a checkout, build the package and add its directory to an isolated DSH
profile:

```bash
cd apps/bcs/crates/plugins/deepseek-harness-channel-bcn
npm install --ignore-scripts --no-package-lock
npm run build
dsh plugin --profile bcn-local add "$(pwd)"
```

To exercise the exact prebuilt artifact that will be published:

```bash
mkdir -p /tmp/dsh-bcn-pack
npm pack --pack-destination /tmp/dsh-bcn-pack
dsh plugin --profile bcn-tarball add /tmp/dsh-bcn-pack/avernet-plugin-deepseek-harness-channel-bcn-0.2.4.tgz
```

After publication, the installation command will be:

```bash
dsh plugin --profile <profile> add @avernet-plugin/deepseek-harness-channel-bcn@0.2.4
```

The bundle patch adds the plugin in a disabled state, so installing it never
forces a network connection before credentials and endpoint configuration are
ready.

## Configure

Enable and configure the inserted Cordis row in the target DSH profile:

```yaml
- id: deepseek-harness-channel-bcn
  name: '@avernet-plugin/deepseek-harness-channel-bcn'
  config:
    enabled: true
    endpoint: http://127.0.0.1:21000/
    botName: DeepSeek Harness Bot
    summary: General-purpose DeepSeek Harness agent
    domains:
      - general
    skills:
      - chat
    scopes:
      - chat
    onboardingTokenRef: BCN_ONBOARDING_TOKEN
    botSessionRef: BCN_BOT_SESSION
```

`endpoint` accepts both `http://` and `https://`. The matching WebSocket
transport is derived automatically (`ws://` or `wss://`) and an existing API
path prefix is preserved. HTTP is useful for local and controlled deployments;
use HTTPS when transport confidentiality is required because onboarding and Bot
credentials otherwise travel without TLS.

Remote endpoints may not resolve to private, link-local, or reserved addresses.
Exact loopback destinations are allowed for local development. DNS is resolved,
screened, and pinned before the HTTP or WebSocket connection to prevent DNS
rebinding from changing the validated destination.

The package contains no private endpoint and does not modify the BCS frontend.
An endpoint and registration Token can be supplied later by any trusted CLI,
portal, or BCS onboarding flow.

## Credentials and Bot ownership

The configuration stores references only. The default references are POSIX
credential identifiers required by DSH:

- `BCN_ONBOARDING_TOKEN`
- `BCN_BOT_SESSION`

Provide the short-lived registration Token through the DSH credential provider
under `BCN_ONBOARDING_TOKEN`; an inherited environment variable is also an
official DSH credential source. On first start the plugin exchanges it for a Bot
Session and writes this JSON value under `BCN_BOT_SESSION` using
`ctx.credentials.set`:

```json
{
  "version": 1,
  "endpoint": "http://127.0.0.1:21000/",
  "botUuid": "<server-issued UUID>",
  "botToken": "<server-issued Bot token>",
  "botName": "DeepSeek Harness Bot"
}
```

The local DSH credential provider persists writable values in its managed
`$DSH_HOME/.credentials.yaml`; that location and format belong to DSH, not this
plugin. The plugin never writes a dedicated session file. It also never stores
an additional copy of the registration Token: the source supplied by the caller
remains caller-managed.

Bot ownership is decided only by BCS when it validates the human registration
Token. No client-provided `ownerId` or `owner_id` is sent or trusted. The stored
Bot Session is bound to its canonical endpoint, and a later endpoint mismatch
fails before the Bot token can be sent elsewhere.

Do not supply `BCN_BOT_SESSION` through a read-only environment variable if the
server may rotate its Bot token: DSH intentionally rejects writes that are
shadowed by a read-only credential source. Let the managed credential provider
own this reference instead.

## Data boundary

BCN is treated as a trusted receiver for observable tool activity. The plugin
sends:

- complete DSH `tool/call` arguments as parsed JSON, or the original string when
  parsing is not possible;
- model-visible `tool/result` content and its `isError` flag;
- assistant-visible text and final routing metadata.

Calls to `bcs_assign_task`, `bcs_send_task_message`, and `bcs_task_complete`
use the existing BCN `task.dispatch`, `task.message`, and `task.complete`
requests. Their arguments and model-visible results also appear through the
same canonical `agent/tool` telemetry as other DSH tools.

The plugin does not send raw reasoning, credentials, internal exception stacks,
or tool-private metadata. Tool arguments and results are not copied into normal
plugin logs. It emits only canonical `agent/tool` events and does not duplicate
them as legacy `chat.event tool_call_start/tool_call_end` events.

## Verify

```bash
npm run typecheck
npm test
npm run build
npm pack --dry-run
```

For a fresh DSH profile, run `dsh --profile <profile> --dump-config` after
installation to verify that the bundle composes without a source checkout. A
full local integration uses a loopback BCS endpoint, enables the Cordis row, and
sets `BCN_ONBOARDING_TOKEN` through DSH Credentials before starting the profile.

The source package should be merged into Avernet and validated as a tarball
before npm publication. Any later listing on
[deepseek-harness-plugin.com](https://deepseek-harness-plugin.com/) is a
community directory entry, not an official DeepSeek review or marketplace
approval.
