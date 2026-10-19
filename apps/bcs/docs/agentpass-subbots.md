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
Bot UUID and Static Token. The supplied `provider_bot_ref` is the parent before
composition. The server requires `bot_slug` in this mode; it must match
`[a-z0-9][a-z0-9_-]{0,31}`, with a composed reference no longer than 128 bytes.
Other modes ignore `bot_slug`.

Both registration paths validate identity, endpoint readiness and reference
occupancy during registration. Duplicate references, including deleted Bot
references, return 409. The existing environment/Provider/reference unique
constraint protects concurrent creation. There is no registration-check API.
Ordinary capability updates retain the stored parent and cannot replace it.

Scoped registration additionally accepts optional
`expected_auth_mode=agentpass-subbots`. A mismatch with the Provider's stored
mode fails before any mutation; the parameter cannot change Provider settings.
Old callers may omit it. Existing scoped/admin registration tokens remain the
registration proof. Mandatory live AgentPass proof during registration is a
separate enhancement. SQL Bot creation and gateway projection are atomic;
subsequent owner-edge failures still require reconciliation, so a failed POST
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
mode mismatch without writes, parent immutability, environment isolation,
ambiguous ordinary identity, Actor mismatch, credential conflicts, terminal
errors, bounded discovery, signed-state checks, Provider events and WS identity.
Real enterprise SDK/Poolab traffic is validated in the OCB integration PRs.
Strong isolation between siblings sharing the parent credential and host-wide
engine scheduling are outside this server change.
