# AgentPass subbots: server registration and authentication

This change supplies the public server foundation (A1) for multiple Bots under
one AgentPass parent. It includes registration, identity lookup, parent discovery,
BCN HTTP/WS enforcement and the community Gateway authentication pipeline.

Bridge/CLI registration, automatic Actor injection and engine execution support
belong to A2. Enterprise AgentPass verification and Gateway routing configuration
belong to OCB O1; the Poolab bridge resolver belongs to OCB O2. Do not enable a
new-mode Provider in a deployment until its enterprise authentication integration
is installed. A1 is independently testable with injected identity verifiers,
local storage and explicit HTTP credentials; it imports no private SDK.

## Registration and stored identity

Only newly created Providers use `agentpass-subbots`; existing Providers are not
converted. There is no database migration or new identity table.

For parent `P` and slug `reviewer`, both Provider-admin and scoped registration
store `agent_code=P` and `provider_bot_ref=P:reviewer`, and issue an independent
Bot UUID and Static Token. Clients supply the complete `provider_bot_ref`.
The server splits at the last `:`, validates the parent against
`[A-Za-z0-9_.:-]+` and the slug against `[a-z0-9][a-z0-9_-]{0,31}`, and preserves
the full ref unchanged. The full ref must be at most 128 bytes. A parent may
itself contain colons: `poolab:region:P:reviewer` resolves to parent
`poolab:region:P`. Other modes preserve the entire ref without parsing a suffix.

Bridge generates a random suffix for each subbot registration invocation and
composes the ref in memory before requesting registration; neither registration request accepts a
separate `bot_slug` or `expected_auth_mode` parameter. Response `bot_slug` remains
derived metadata, not a database column. Removed/unknown request fields do not
change the supplied ref; an incomplete ref still fails registration.

Both registration paths validate identity, endpoint readiness and reference
occupancy during registration. Duplicate references, including deleted Bot
references, return 409. The existing environment/Provider/reference unique
constraint protects concurrent creation. There is no registration-check API.
Ordinary capability updates retain the stored parent and cannot replace it.

Registration derives its authentication mode from the Provider's stored
configuration. Newly issued v2 registration Tokens also carry that mode in the
signed `auth_mode` claim; redemption requires a present claim to match the
current configuration before any Bot is created. Historical v2 Tokens without
the claim remain accepted. The v2 encoding, signature algorithm and public
request parameters are unchanged. Clients supply the complete Provider Bot reference.
Existing scoped/admin registration tokens remain the registration proof. Mandatory
live AgentPass proof during registration is a separate enhancement. SQL Bot
creation and gateway projection are atomic; subsequent owner-edge failures still
require reconciliation, so a failed POST
must not be assumed to mean that no Bot was created.

## Request identity

Clients may send `X-BCN-Actor-Bot-Id: <Bot UUID>` to select the Bot they intend to
act as. Actor is a selector, never a credential. The server derives Provider
mode from storage; it does not require an auth-mode request header.

| Credential | Actor | Result |
| --- | --- | --- |
| Parent AgentPass for subbots | Required, even with one child | Resolve the exact parent/UUID pair |
| Concrete Bot Static Token | Optional | Resolve its Bot; a supplied Actor must match |
| Ordinary AgentPass | Optional | Without Actor, require one eligible ordinary registration in the environment |
| Actor alone | Present | Reject without a matching authenticated Bot |

Runtime reads check current Provider/affiliation state and exclude deleted Bots.
Subbots are never candidates for implicit ordinary AgentPass selection. Multiple
ordinary candidates are rejected rather than selecting the first row.

Legacy authentication continues past a User-only result when Actor requires a
Bot. Invalid Bot credentials and lookup failures remain terminal through HTTP
adapters. Gateway applies Actor inside Bot strategies and requires the resulting
Bot even on optional-Bot routes. Credential conflicts are rejected. Identity
verification remains supplied by the deployment; TC direct subjects remain
compatible with the existing Principal shape and optional Actor equality checks
in their enterprise strategy.

Gateway's signed Principal format is unchanged. BCN uses its signed Bot UUID,
checks current persisted state, and never switches identity using an unsigned
Actor header. The independent session-token route uses the same state check.

Provider event authentication enforces the same parent/Actor and Static Token
rules plus existing Provider/run/session relationships. WS uses the Bot's own
Static Token, validates caller/token/body UUID consistency before connection
writes, and fixes identity after the first handshake.

## Parent discovery

`GET /api/v1/collaboration/agents/me` verifies the AgentPass parent independently
of Bot registration. It returns `agent_code`, undeleted Bot summaries and an
optional continuation cursor. Zero or multiple Bots are valid. Offline Bots and
entries associated with disabled Providers/bindings remain visible for discovery,
without granting runtime access. No credentials are returned.

The page size is 1-100 (one extra row detects the next page), optionally scoped
to a Provider. Existing `bots/me` retains a single-Bot result: explicit Actor
selects that Bot; Static Token selects its own Bot; an implicit parent lookup
obeys the runtime identity rules above.

## Storage and validation boundaries

SQL Actor and Static Token lookup use one joined read returning at most one row.
Implicit selection reads at most two ordinary candidates to detect ambiguity,
with one additional existence query when no ordinary candidate is found. Parent
discovery returns at most 101 rows per page. Queries are environment-scoped and
read Provider/binding metadata together rather than issuing per-row metadata
queries. LIMIT bounds returned rows, not all database work; production MySQL
query plans and large datasets still need deployment validation.

Memory storage persists a credential-free Provider affiliation marker. After a
restart without restored Provider metadata, affiliated Bots fail closed instead
of becoming ordinary implicit AgentPass Bots.

Server acceptance covers sibling registration, duplicate/tombstone conflicts,
Provider-configured identity parsing, parent immutability, environment isolation,
ambiguous ordinary identity, Actor mismatch, credential conflicts, terminal
errors, bounded discovery, signed-state checks, Provider events and WS identity.
Real enterprise SDK/Poolab traffic is validated in the OCB integration PRs.
Strong isolation between siblings sharing the parent credential and host-wide
engine scheduling are outside this server change.

## Bridge mode selection and retry semantics

New bridge versions select the identity resolver from the registration Token's
`auth_mode`; `--provider-auth` and `--bot-slug` are no longer CLI options. The
client reads unverified selection metadata; only server-side signature and
Provider checks grant registration authority. An unsupported mode fails locally,
without falling back to another identity. Old bridge versions ignore the added
Token field. New bridge versions require it and ask users to obtain a new Token
when given an older Token. Existing saved runtime identities remain usable.

For `agentpass-subbots`, the bridge generates a 32-character random hexadecimal
suffix per invocation. The display name is independent of identity. Repeating
registration with the same name creates another Bot. Only successful registration
appends credentials and the complete ref to the formal bridge configuration.
There is no pending registration file or retry identity; historical `.pending.json`
files are ignored and left untouched. Failures preserve the existing configuration.

The bridge never automatically retries a creation request. A lost response or
server-side failure may leave a remote Bot even when no local credentials were
saved. Inspect `agents/me` and reconcile before deciding to register again; a new
invocation uses a new ref and can create an additional Bot. Registration does not
provide credential recovery or exactly-once creation across invocations.
