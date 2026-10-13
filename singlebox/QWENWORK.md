# QwenWork minimal local validation stack

Run from the Avernet repository root:

```bash
./singlebox/qwenwork.sh setup
./singlebox/qwenwork.sh start
```

This starts **Backend, BCS, Gateway and the next-generation frontend** only.
It does not start BaaS, BCSFuse, OpenClaw bots or a model mock server. QwenWork
and the bridge run separately on the user's Mac. Use the normal public
Singlebox prerequisites (Node 22+, uv-managed Python 3.12 and the Rust build
prerequisites); no model key is required for this stack.

Ports come from `singlebox/.env.local`, with the normal defaults: BCS 21000,
Backend 8888, Gateway 8889 and frontend 8000. The script prints the workbench
and local login URLs. It clears inherited HTTP/SOCKS proxy variables only for
its local child services. Port conflicts follow the existing Singlebox ownership
checks; unrelated processes are not killed.

## Provider provisioning and authorization

After BCS is healthy, the script queries `local` by slug and creates it only if
absent: protocol 2.0, static bearer authentication, no HTTP downlink endpoint.
A disabled or incompatible existing Provider causes an error; the script never
renames, deletes or silently changes it. Provisioning uses a distinct local
bootstrap identity, not the user's identity. Returned Provider credentials are
not persisted or printed by this script.

The Provider ID is saved privately in the ignored
`singlebox/.dependencies/standalone/qwenwork-provider.json`. On every BCS
configuration generation in this profile, that ID is added to
`openapi_v1.registration_self_service_provider_ids`, preserving other authorized
IDs and settings. The first startup restarts BCS if needed to load authorization.
Subsequent startups reuse the Provider, and recover discovery after a local DB
reset. The normal Singlebox profile's authorization policy is unchanged.

Before enabling the frontend, the script verifies Provider discovery and v2
plugin token issuance through Gateway using two ordinary local users. This
exercises Gateway → BCS authorization rather than using Provider admin tokens.
The verification tokens are discarded. Only after this succeeds is the frontend
started with `QWENWORK_PROVIDER_SLUG=local`. Generic Open Core builds leave this
setting empty and mark QwenWork onboarding unsupported. Other deployments can
explicitly configure the slug after equivalent provisioning and authorization.

The normal local dev-cookie authentication is strictly for local validation.
This script is not production Provider provisioning or production authentication.

## Connect the desktop

Open the printed local login link, then `/bot-workshop` and the external Bot
QwenWork tab. Download a bridge release containing `init`, register against the
local BCS endpoint, run the printed initialization command, fill its WebSocket
URL/Bot ID/channel Secret in QwenWork's WeCom Bot channel, and start the bridge.
Provider authorization belongs to this script; bridge initialization only owns
local desktop settings and Hooks.

The recommended `0.1.4-beta.2` distribution includes `init`. Its prerelease, both
macOS archives and installer have been verified. This script does not publish
or replace releases.

```bash
./singlebox/qwenwork.sh verify   # recheck ordinary-user discovery/token issuance
./singlebox/qwenwork.sh status
./singlebox/qwenwork.sh restart
./singlebox/qwenwork.sh stop
```

Stopping retains Provider data and configuration for reuse. The existing
Singlebox status/log locations remain authoritative. This verification proves
the deployment and registration prerequisite chain; it does not claim a real
desktop chat/tool round trip until a bridge and QwenWork are connected.
