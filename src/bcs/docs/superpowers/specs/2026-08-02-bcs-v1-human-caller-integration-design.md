# BCS V1 Human Caller Integration Design

- **Date:** 2026-08-02
- **Status:** Approved (brainstorm)
- **Scope:** Principal propagation and Human Actor selection for all 32 implemented BCS V1 HTTP operations

## Context

BCS V1 now contains 32 implemented HTTP operations: the original 27 Group,
Session, Invitation, and Friendship operations plus five Bot control-plane
operations. The V1 HTTP adapter still injects one preselected
`Principal::{Human, Bot}` into request extensions and every Application command
still carries that single Principal.

The Gateway Principal verifier added in the preceding batch has a different
and intentionally broader output. It verifies the signed
`X-Avernet-Principal` JWT and returns a complete, secret-free
`AuthenticatedCaller` that may contain User, Bot, App, and AccessKey identities
at the same time.

The current public product surface does not need Bot callers. All 32 operations
in this batch are Human APIs. A later feature may introduce Bot-facing HTTP
operations with similar business capabilities, but that feature will make its
own HTTP contract and Application-reuse decisions rather than adding an
implicit Human/Bot priority rule now.

## Decision summary

1. All 32 current V1 operations require a usable User identity and execute as
   a Human Actor.
2. Gateway verification returns the complete `AuthenticatedCaller`; it never
   selects a business Actor.
3. HTTP middleware stores the complete caller in request extensions, and
   routes pass it unchanged into Application commands.
4. Each Application use case explicitly selects the Human identity before
   applying resource authorization.
5. A Caller that contains User plus Bot, App, or AccessKey is valid. The User
   is selected; the other identities do not alter authorization.
6. A valid Caller without User is forbidden. There is no fallback to Bot,
   `bot.owner_id`, App owner metadata, or AccessKey metadata.
7. A `bot_uuid` in a path or request body identifies a managed resource. It
   does not turn the Human caller into that Bot Actor.
8. The three read operations that expose an Actor-relative collaboration view
   use one explicit View Actor rule: omit `view_bot_id` for the authenticated
   User's Human Actor, pass that same Human Actor ID explicitly, or pass the
   UUID of a Bot whose `created_by` equals the authenticated User ID.
9. Selecting a View Actor scopes returned data. It does not change the caller,
   impersonate the selected Bot, or grant Group/Session management authority.
10. Bot-facing APIs, `act_as`, Human-first/Bot-first policies, and duplicated
   Human/Bot HTTP surfaces are out of scope.

## Goals

- Connect the verified Gateway Caller contract to every implemented V1 use
  case without losing App or AccessKey calling context.
- Make the Human-only admission rule explicit in the OpenAPI contract and in
  Application code.
- Preserve the existing BCS Human Actor convention and resource-level
  authorization behavior.
- Distinguish authentication failures from authenticated callers that are not
  admitted to a Human API.
- Keep JWT, headers, and Gateway protocol details out of Application and Core.
- Avoid speculative abstractions for Bot-facing operations that do not yet
  exist.

## Non-goals

- Do not add a Bot-facing V1 operation.
- Do not add `act_as`, Actor-selection headers, or request fields.
- Do not let a Human impersonate an owned Bot. Selecting an owned Bot as a
  read-only View Actor is data scoping, not caller substitution.
- Do not infer Human identity from `AuthenticatedBotIdentity.owner_id`.
- Do not make App or AccessKey a BCS business Actor.
- Do not add scopes that Gateway does not provide.
- Do not change Legacy HTTP authentication or Legacy CLI behavior.
- Do not mount the V1 router in production or add bootstrap trust wiring in
  this batch.
- Do not migrate Human Actor IDs, `created_by`, relationships, or stored
  Group/Session data.

## Architecture and data flow

```text
Gateway-authenticated request
        |
        | X-Avernet-Principal: <compact signed JWT>
        v
bcs-api-http Gateway verifier
        | verify signature, iss, aud, kid, time, tenants, identity shapes
        v
AuthenticatedCaller { tenant, user?, bot?, app?, access_key? }
        |
        | request extension and Application command
        v
V1 Application use case
        | require User; project Human Principal; apply resource authorization
        v
existing Application/Core ports and persistence
```

The trust and policy boundaries remain separate:

- Gateway answers which identities were authenticated.
- The HTTP verifier proves that the Caller description came from the trusted
  Gateway and is valid for BCS.
- The current HTTP contract declares that the operation is a Human API.
- Application selects Human and decides whether that Human may perform the
  requested action on the target BCS resource.
- Core and repositories remain unaware of JWT and HTTP concepts.

## HTTP verifier and middleware contract

The injectable HTTP `PrincipalVerifier` changes from returning a single
`Principal` to returning the full Caller:

```rust
#[async_trait]
pub trait PrincipalVerifier: Send + Sync {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError>;
}
```

Its production implementation extracts exactly one non-empty raw compact JWT
from `X-Avernet-Principal` and delegates cryptographic and wire validation to
the existing `GatewayPrincipalTokenVerifier`. The middleware inserts
`AuthenticatedCaller` and `RequestId` into request extensions. It does not
require User, inspect path parameters, select an Actor, or perform ownership
checks.

Header absence, malformed header values, invalid JWTs, invalid signatures,
expired tokens, wrong audience or issuer, and invalid Principal sets all fail
at this boundary with the same unauthenticated response.

## Application command contract

Every current V1 command or query that carries:

```rust
pub principal: Principal
```

changes to carry:

```rust
pub caller: AuthenticatedCaller
```

Routes do not discard identities by constructing a Human Principal. This
preserves the authenticated calling context and prevents transport policy from
leaking into commands as a pre-authorized Actor.

Application owns one transport-neutral Human projection helper. Each public
use-case method calls it explicitly before resource authorization:

```rust
fn require_human(caller: &AuthenticatedCaller) -> Result<Principal, ApplicationError> {
    let user = caller.user.as_ref().ok_or_else(|| {
        ApplicationError::forbidden("This operation requires a Human caller")
    })?;

    Ok(Principal::human(
        AuthenticatedUser {
            id: user.id.clone(),
            username: user.username.clone(),
            display_name: user.display_name.clone(),
            full_name: user.full_name.clone(),
        },
        caller.tenant.clone(),
        BTreeSet::new(),
    ))
}
```

The exact helper name and ownership may follow local style, but these
invariants are binding:

- It consumes `AuthenticatedCaller`, not headers or JWT claims.
- It selects only `caller.user`.
- It uses the already-normalized `caller.tenant`.
- It does not infer or synthesize scopes; Gateway currently supplies none.
- It does not consult `caller.bot.owner_id`.
- It returns Forbidden when User is absent.
- Its result preserves the existing Human Actor mapping:
  `subject.id = staff-1` becomes `actor_id = human_staff-1` only through the
  existing Application `Principal::actor_id()` convention.

The full Caller remains available on the command for future audit or policy
requirements. App and AccessKey are not evaluated in this batch.

## Operation semantics

All 32 OpenAPI operations declare the Gateway-recognized extension
`x-avernet-security.user: required`. This is the transport contract requiring
a usable User identity; BCS still receives and retains the complete
`AuthenticatedCaller`.

| Operation group | Count | Human semantics |
| --- | ---: | --- |
| Bot control plane | 5 | Human queries or mutates Bot registry/control-plane records; ownership uses raw User ID against `created_by`. |
| Bot-scoped Friendship | 6 | Human manages the path, request, or resolved receiver Bot only when its `created_by` equals the authenticated User ID. The Bot remains a target resource, not the caller Actor. |
| Group | 8 | Human is an independent Group Actor using `human_<subject.id>` for participant, originator, driver, and manager comparisons. Group listing may use the authenticated Human or an owned Bot as its View Actor. |
| Session | 10 | Human is an independent Session Actor. Session listing and message history use the authenticated Human by default and accept an explicitly authorized View Actor. |
| Invitation | 3 | Human must be authorized to create invitations and is the joining Actor when accepting one. |

### Bot control-plane operations

The five newly merged operations already require Human semantically:

- `list_my_bots`
- `query_bots`
- `get_bot`
- `update_bot`
- `list_bot_candidates`

Their Application service continues to compare `caller.user.id` with the
stored `created_by` value where ownership is required. The other authenticated
identity kinds neither grant nor deny access.

### Bot-scoped Friendship operations

Paths such as:

```text
/openapi/v1/bots/collaboration/{bot_uuid}/friendships
```

remain Human APIs for this batch. The Application service checks that the
named Bot's `created_by` equals `caller.user.id`. Creator relation edges, a
legacy Bot UUID suffix, missing-`created_by` auto-claim, and
`caller.bot.bot_uuid` do not grant ownership. Application must not select the
Bot identity or fall back to Bot after a Human authorization failure.

For friend-request acceptance and rejection, Application first loads the
request, resolves the receiver Bot, then applies the same Human-to-managed-Bot
authorization. For friendship deletion, the current rule allowing management
of either endpoint remains, but both checks use the selected Human Principal.

### Group and Session operations

For generic Group and Session resources, the Human is the business Actor in
its own right. Existing comparisons against Group participants, originator,
driver, ManagerWorker manager, Session participants, and Session creator use
`human_<subject.id>`.

Human ownership of a Bot does not imply permission to act as that Bot or to
manage the Bot's Group or Session. If a Bot is the driver or manager but the
Human is not an independently authorized Human Actor, management requests
remain forbidden. The read-only View Actor selection below is the only
exception and scopes data rather than changing the business caller.

### Unified View Actor contract

These Human HTTP operations use the same optional `view_bot_id` query
parameter:

```text
GET /openapi/v1/groups
GET /openapi/v1/groups/{group_id}/sessions
GET /openapi/v1/sessions/{session_id}/messages
```

The first path replaces the Bot-scoped
`GET /openapi/v1/bots/collaboration/{bot_uuid}/groups` list operation. The
operation count remains 32 and its operation ID becomes `list_groups`.

Application resolves one effective View Actor before querying data:

| Request value | Effective View Actor | Required authorization |
| --- | --- | --- |
| `view_bot_id` omitted | `human_<caller.user.id>` | A usable authenticated User is already required. |
| `view_bot_id=human_<staff_no>` | The supplied Human Actor | It must equal `human_<caller.user.id>`. |
| `view_bot_id=<bot_uuid>` | The supplied Bot | The Bot must exist and `bot.created_by == caller.user.id`. |

The parameter name remains `view_bot_id` for compatibility even though it may
contain the caller's Human Actor ID. A different Human Actor, an unknown Bot,
a Bot without `created_by`, or a Bot created by another User is rejected with
the same generic `403 forbidden`. The check never uses a creator relation edge
and never auto-claims a legacy Bot. Extra Bot/App/AccessKey identities in the
authenticated Caller do not change the result.

The selected Actor affects only these read operations:

- `list_groups` applies the existing `membership=all|direct|session_only`,
  search, kind, and strategy filters to the selected Actor's Group relations.
  Omitting `view_bot_id` therefore lists the authenticated Human Actor's
  Groups, not Groups belonging to all Bots that the Human created.
- `list_sessions` returns only Sessions under `group_id` where the selected
  Actor is a Session Participant. Group membership, ownership, creator, or
  manager status does not broaden the result set.
- `list_session_messages` requires the selected Actor to be a Participant of
  the requested Session and applies that Participant's history cutoff and
  message-owner visibility rules.

Explicit and default View Actor branches are mutually exclusive. A failed
explicit View Actor check never falls back to the Human Actor's direct
permissions. For both list operations, an authorized View Actor with no
matching relation returns `200` with an empty page. Both `items` and `total`
must be filtered by the selected Actor before `offset`/`limit` pagination.

For message history, omitting `view_bot_id` is exactly equivalent to passing
the authenticated Human Actor ID. The Human must be a Session Participant;
being Group/Session creator, driver, or manager does not create an implicit
manager view. Likewise, an explicitly selected owned Bot must be a Session
Participant. A selected Actor that is not a Session Participant is rejected
with the same generic `403 forbidden`.

View Actor selection does not apply to `get_group`, `get_session`, or any
Group/Session mutation. In particular, neither detail operation accepts a
`view_bot_id` query parameter.

Detail reads instead use an implicit read relation derived from the
authenticated User. `get_group` is readable when the Group participants
contain either the User's `human_<caller.user.id>` Actor or at least one Bot
whose `created_by` equals `caller.user.id`. `get_session` applies the same rule
to Session participants. This owned-Bot intersection grants only the detail
read: the caller remains Human, no Bot message perspective is selected, and
no Group or Session management authority is granted. Bot ownership is still
determined only by exact `created_by`; creator relation edges, UUID suffixes,
and auto-claim do not count.

All Group and Session mutations continue to evaluate the authenticated Human
Actor and their operation-specific direct management relations.

## Error semantics

The response boundary distinguishes authentication from admission and
authorization:

| Condition | Result |
| --- | --- |
| `X-Avernet-Principal` missing, malformed, forged, expired, or scoped to another audience | `401 unauthenticated` |
| JWT valid but `AuthenticatedCaller.user` absent | `403 forbidden` |
| User present with additional Bot/App/AccessKey identities | Continue as Human |
| Human does not own or manage a target Bot | `403 forbidden` |
| Explicit View Actor is another Human, unknown, unowned, or not a required Session Participant | `403 forbidden` |
| Authorized View Actor has no matching Group or Session relation in a list operation | `200` with an empty page |
| Neither the Human Actor nor any Bot created by that Human is a participant for a Group/Session detail read | `403 forbidden` |
| Human lacks a required Group/Session mutation relation or role | `403 forbidden` |
| Resource is absent or deliberately hidden by an existing use case | Existing `404` behavior |
| Request shape or state is invalid | Existing `400`/`409` behavior |

No error response reveals whether a signature, claim, or Principal-set detail
caused authentication failure. Application-level Forbidden responses may use
the existing stable error envelope but must not expose credentials or JWT
contents.

## OpenAPI contract changes

Gateway derives route requirements from this extension:

```yaml
x-avernet-security:
  user: required
```

Contract tests must assert that every one of the 32 operations uses exactly
`{user: required}`. The older `{principal: required}` and locally invented
`{principal: human}` shapes are not recognized by Gateway and must not appear.
Invalid Gateway authentication remains `401`; a valid Caller that reaches
Application without User remains `403` as a defense-in-depth rule.

The Group list operation also moves from:

```text
GET /openapi/v1/bots/collaboration/{bot_uuid}/groups
```

to:

```text
GET /openapi/v1/groups?view_bot_id=<optional-view-actor>
```

The existing `POST /openapi/v1/groups` operation remains on the same path. The
Group list retains `offset`, `limit`, `q`, `membership`, `kind`, and `strategy`.
The Session list adds the same optional `view_bot_id` parameter; Session
message history changes the default from an implicit manager view to the
authenticated Human Actor's Participant view. All three parameter descriptions
must document that an explicit Human value may only identify the caller and an
explicit Bot value requires `created_by` ownership.

`GET /openapi/v1/groups/{group_id}` and
`GET /openapi/v1/sessions/{session_id}` deliberately do not add
`view_bot_id`. Their `403` contract documents the implicit participant
intersection across the caller's Human Actor and Bots created by that User.

## Compatibility and risk

This is a deliberate narrowing and path revision of the unmounted V1 contract.
The current branch's test verifier can inject Bot Principals into the original
27 routes and the current Group list path embeds a Bot UUID, but production
bootstrap does not mount `bcs-api-http`; therefore the change does not remove a
deployed Bot API. Generated V1 clients must update from `list_bot_groups` to
`list_groups` before this V1 surface is mounted.

Legacy HTTP routes, bot-token authentication, WebSocket behavior, and
`bcs-cli` remain unchanged. Bot callers continue to use those Legacy surfaces
until a separately designed V1 Bot API exists.

No persistence schema or data migration is required. The stable Human
compatibility rules remain:

- Gateway User ID maps to raw `created_by` for ownership checks.
- BCS maps the same User ID to `human_<id>` for Actor relationships.
- Tenant remains authenticated context and is not added to existing storage
  keys in this batch.

The principal implementation risks are accidental privilege fallback,
discarding the full Caller too early, and treating a path Bot as the caller.
Focused contract and regression tests must pin all three boundaries.

## Test strategy

### Identity and boundary tests

- `PrincipalVerifier` returns `AuthenticatedCaller` and cannot return a
  preselected `Principal`.
- Middleware inserts the full Caller and the Route passes it unchanged into
  the command.
- Application and Core remain free of HTTP, JWT, and Gateway wire types.
- User-only and User+Bot+App+AccessKey Callers both select the same Human Actor.
- Bot-only, App-only, and AccessKey-only Callers reach Application only after
  successful authentication and are rejected with Forbidden.
- No code derives Human from `AuthenticatedBotIdentity.owner_id`.

### Operation-family tests

- All five Bot control-plane operations require Human.
- All six Bot-scoped Friendship operations authorize only when the target or
  resolved Bot's `created_by` equals the authenticated User ID.
- Group operations use the Human Actor ID for participant and manager rules.
- Session operations use the Human Actor ID for read, manage, completion,
  participant, and message-visibility rules.
- Group and Session detail reads succeed when either the Human Actor or an
  exact-`created_by` owned Bot is a participant, but that implicit relation
  grants neither management access nor a message perspective.
- Omitting `view_bot_id` on all three View Actor operations selects the
  authenticated Human Actor and never expands to owned Bots.
- Passing the caller's Human Actor is equivalent to omission; passing an owned
  Bot scopes to that Bot; every other explicit value is Forbidden without
  fallback.
- Group and Session list `items` and `total` are both scoped before pagination.
- Session history requires the selected Human or Bot to be a Session
  Participant, including when the Human is a creator or manager.
- Invitation creation uses Human Group/Session authorization and invitation
  acceptance joins as Human.
- A User+Bot Caller never changes outcome merely because the extra Bot
  identity matches a target or resource participant.

### Contract and regression tests

- OpenAPI validation counts 32 operations and requires
  `x-avernet-security.user: required` on every operation.
- The operation inventory contains `GET /openapi/v1/groups` with
  `operationId: list_groups` and excludes the old Bot-scoped Group list path.
- Group list, Session list, and Session message history expose the documented
  optional `view_bot_id` parameter.
- Group and Session detail expose no `view_bot_id` parameter and document the
  Human-or-created-Bot participant read rule.
- HTTP route tests distinguish invalid authentication (`401`) from valid
  non-Human callers (`403`).
- Existing Human behavior and error mappings remain covered.
- Legacy route and CLI tests remain unchanged and passing.
- Workspace boundary and architecture tests continue to pass.

## Deferred Bot-facing design

When a concrete Bot-facing use case is requested, it will define:

- distinct Bot HTTP paths or operations;
- Gateway route-security requirements;
- how the Application selects and validates `caller.bot`;
- whether and how Human and Bot entrypoints share Application orchestration;
- Bot-specific authorization, audit attribution, and error behavior.

That future design must not change the meaning of these Human endpoints or add
automatic Human/Bot fallback to them.
