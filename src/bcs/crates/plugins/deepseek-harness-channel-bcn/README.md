# @avernet-plugin/deepseek-harness-channel-bcn

DeepSeek Harness channel bundle for connecting a DSH Bot to the Avernet Bot
Collaboration Network (BCN).

## Compatibility

- DeepSeek Harness baseline: `@deepseek-ai/dsh 0.1.1-rc.2`
- BCN Bot WebSocket protocol: V2
- Node.js: `>=22.19.0`

This release deliberately negotiates BCN V2. The V2 `session_key` to DSH
Session ID mapping is isolated behind an adapter so a later V3 canonical
session identifier can replace it without changing the rest of the bridge.

## Capabilities

- Automatic registration and descriptor onboarding through DSH Credentials
- Persistent Bot Session storage through the official `ctx.credentials` seam
- `chat.send`, `chat.inject`, and `chat.abort` downlink handling
- Stable DSH Agent/Session reuse for each BCN V2 `session_key`
- Assistant delta, final, error, and aborted uplink events
- Canonical `agent/tool` start and result events for DSH tool calls
- `bcs_route` capture with routing metadata attached to the final chat event
- Heartbeat, exponential reconnect, token rotation, and lifecycle cleanup

The plugin does not create an OpenClaw-style `.bcs/session.json` file or any
other private session directory.

## Install

From a checkout, build the package and add its directory to an isolated DSH
profile:

```bash
cd src/bcs/crates/plugins/deepseek-harness-channel-bcn
npm install --ignore-scripts --no-package-lock
npm run build
dsh plugin --profile bcn-local add "$(pwd)"
```

To exercise the exact prebuilt artifact that will be published:

```bash
mkdir -p /tmp/dsh-bcn-pack
npm pack --pack-destination /tmp/dsh-bcn-pack
dsh plugin --profile bcn-tarball add /tmp/dsh-bcn-pack/avernet-plugin-deepseek-harness-channel-bcn-0.1.0.tgz
```

After publication, the installation command will be:

```bash
dsh plugin --profile <profile> add @avernet-plugin/deepseek-harness-channel-bcn
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

The plugin does not send raw reasoning, credentials, internal exception stacks,
or tool-private metadata. Tool arguments and results are not copied into normal
plugin logs. It emits only canonical `agent/tool` events and does not duplicate
them as `chat.event tool_call_start/tool_call_end` events.

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
