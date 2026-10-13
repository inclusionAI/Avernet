# AgentPass subbots

`agentpass-subbots` lets a new Provider register multiple Bots under the same
verified AgentPass parent. Existing Providers keep their existing mode. This
feature adds no database migration.

For parent `P` and slug `reviewer`, registration stores
`agent_code=P` and `provider_bot_ref=P:reviewer`. The generated Bot UUID and
Static Token identify this Bot independently. Slugs are unique within the
current environment and Provider; deleted registrations retain their reference.

## Register and run

Create a new Provider with auth mode `agentpass-subbots`. Both scoped
registration and Provider-admin registration accept `bot_slug`. Both validate
identity and reference occupancy during registration, including tombstones;
duplicate subbot references return 409 and the existing unique constraint
handles concurrent requests. There is no separate availability-check API.

For scoped registration, bridge supplies the optional
`expected_auth_mode=agentpass-subbots` precondition. The server checks it before
any mutation and rejects a mismatched Provider mode with 409. This condition
never overrides Provider configuration; existing callers may omit it.

The bridge distribution must install an AgentPass identity resolver. The public
bridge extension contract provides this hook; enterprise SDK verification and
Poolab discovery belong to the internal distribution.

The internal bridge supports:

```sh
bcs-bridge register --provider-auth agentpass-subbots --bot-slug reviewer [other registration arguments]
```

Omitting `--bot-slug` generates a random key. It is saved before any registration
request. An attempt that has not sent the final request may be replaced. An
attempt with an unknown registration outcome retains the same key for retries;
review its private pending file before abandoning it. Reusing an existing bridge
configuration appends a Bot only when Provider, parent, API URL and mode agree.

Parent discovery uses `GET /api/v1/collaboration/agents/me`. It verifies the
AgentPass token and returns a bounded list of undeleted Bot summaries. Zero or
multiple registrations are valid; offline and disabled entries remain visible.
No Bot or AgentPass credentials are returned.

## Request identity

Parent AgentPass requests require `X-BCN-Actor-Bot-Id: <concrete Bot UUID>`, even
when the parent has only one child. A concrete Static Bot Token may omit Actor;
when Actor is supplied it must match. Actor alone never authenticates a Bot.
The server derives Provider mode; no auth-mode request header is needed.

Gateway consumes Actor inside its Bot strategies, and requires a matching Bot
even on routes with optional Bot authentication. Legacy BCN authentication also
keeps searching after a User-only result when Actor is required. Invalid Bot
credentials are terminal. Ordinary AgentPass without Actor requires exactly one
eligible ordinary registration in the current environment; subbots are excluded.

Gateway's signed Principal format is unchanged. BCN keeps the signed Bot UUID
and rechecks its current persisted Bot/Provider state. An ordinary Actor header
does not switch a signed identity. TC subjects without local registration remain
supported.

During each Claude Code or Codex execution, bridge sets `BCN_ACTOR_BOT_ID`,
`BCN_AUTH_MODE=agentpass-subbots` and the BCN API URL in that process's environment.
`bcs-cli` converts the selector to Actor and suppresses shared automatic Bot
tokens in parent mode. An explicit Static Token is still supported. Each Bot has
its own bridge UUID, token, session state and reconnection state.

Provider events enforce the same Actor rules and existing Provider/run/session
relationships. WS uses the Bot's independent Static Token, checks token/UUID/
optional Actor consistency before connection writes, and fixes identity after
the first handshake. `bcs-cli connect` under parent authentication needs that
Bot's Static Token; parent authentication does not mint or rotate WS credentials.

## Operational limits

- Only new Providers support this mode; there is no existing-Provider conversion.
- Mandatory AgentPass proof during registration is a separate enhancement.
  Existing scoped/admin registration tokens remain the registration proof.
- Qwen desktop execution cannot pass per-turn environment and is rejected for
  subbot registration. Claude Code, Codex and internal cfuse-backed variants
  support it.
- The local memory adapter persists a credential-free affiliation marker.
  After restart, an affiliated Bot is refused until Provider metadata is restored;
  it cannot silently become an ordinary implicit AgentPass Bot. Production SQL
  storage retains the authoritative Provider metadata.
- In subbot plugin mode, one Bot's identity failure stops only its client.
  Shared persistence failures still stop the instance.
- Real Poolab proxy injection, security gateway parent-to-parent traffic and
  host capacity must be validated in pre-production. Host-wide engine scheduling
  and strong isolation between siblings are separate concerns.
