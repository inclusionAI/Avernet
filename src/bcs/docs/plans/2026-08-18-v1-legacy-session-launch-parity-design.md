# V1 and Legacy Session Launch Parity Design

**Date:** 2026-08-18
**Status:** Approved

## Problem

BCS currently implements `POST /groups/{group_id}/sessions` and
`POST /openapi/v1/collaboration/groups/{group_id}/sessions` through different
application paths.

The legacy HTTP handler owns most of the launch workflow itself: caller
authorization, creator selection, participant construction, Session creation
or reactivation, StateMachine startup, and initial SessionContext delivery.
The V1 Session facade implements a smaller, Human-only Chat creation path. As a
result, the two routes differ in supported principals, Session kinds, input and
metadata handling, collaboration startup, and public-Group behavior.

The duplicated orchestration also puts domain policy in an HTTP delivery
adapter and makes later fixes likely to drift between protocols.

## Goals

- Keep the legacy create/reactivate API behavior compatible with its current
  clients.
- Give V1 create-session the same launch capabilities as legacy, except that
  V1 does not expose reactivation.
- Keep V1 request and response shapes native to the V1 OpenAPI contract and
  standard Envelope.
- Authenticate and extract protocol-specific identity in each HTTP adapter,
  then run one transport-neutral authorization and launch workflow.
- Preserve raw Session input and metadata without application-layer schema
  remapping.
- Preserve the existing Group-strategy behavior for Chat, ManagerWorker, and
  StateMachine Groups.

## Non-goals

- Adding a V1 Session reactivation endpoint.
- Removing `session_id` from the legacy request or splitting the legacy route.
- Splitting Chat and service-invocation creation into separate V1 URLs.
- Adding runtime enforcement for `StateMachineDefinition.input_schema`.
- Introducing `payload`, `extensions`, or a new metadata format.
- Inferring or validating `meta.channel.source` from `binding_id`.
- Changing Session persistence or database schemas.
- Refactoring unrelated Session list, detail, participant, collection, file,
  history, completion, or deletion operations.

## Alternatives Considered

### Shared transport-neutral Session launch application service (selected)

Both HTTP adapters translate their authenticated caller and DTO into one
`SessionLaunchService` command. The service owns creator authorization, Group
access, participant construction, persistence orchestration, and collaboration
startup. Each adapter retains its own wire contract and response projection.

This removes policy from the legacy adapter, prevents V1/legacy workflow drift,
and keeps protocol-specific identity claims and envelopes out of core logic.

### Separate V1 and legacy facades with shared helpers

This would reduce some duplication but would leave authorization ordering,
default selection, side effects, and error behavior independently implemented.
The two routes could drift again even if they share low-level helper functions.

### Translate legacy requests into the V1 application contract

The V1 contract intentionally does not represent legacy reactivation and does
not accept all legacy JSON shapes. Making it the common internal model would
leak V1 protocol constraints into legacy behavior and make compatibility
dependent on a versioned delivery contract.

## Architecture

The new boundary is a protocol-neutral application service declared in
`bcs-service-api` and implemented alongside the Session services in
`bcs-session`.

```text
Gateway Principal verifier                 Bot token / Human cookie resolver
             |                                          |
             v                                          v
      V1 HTTP adapter                             legacy HTTP adapter
      - select caller                             - resolve caller
      - parse V1 DTO                              - parse legacy DTO
      - map V1 fields                             - map legacy fields
             |                                          |
             +------------ SessionLaunchCommand --------+
                                      |
                                      v
                         SessionLaunchService
                         - resource authorization
                         - creator authorization
                         - participant construction
                         - create or reactivate
                         - StateMachine startup or
                           SessionContext delivery
                                      |
                    +-----------------+------------------+
                    v                 v                  v
          SessionManagement   CollaborationRuntime   SystemMessage
```

`SessionManagementService` remains the lower-level persistence and lifecycle
service. StateMachine and SessionContext side effects do not move into
`SessionManagementService`, because callers of that lower-level service do not
all represent an HTTP Session launch.

One `SessionLaunchService` instance is constructed in bootstrap and injected
into both the legacy `Services` container and the V1 API state. No HTTP adapter
depends on the concrete implementation.

## Identity Boundary

Authentication remains protocol-specific:

- V1 verifies the Gateway-signed `AuthenticatedCaller` and selects a Human or
  Bot under the `human_or_owned_bot` identity policy.
- Legacy resolves either a Bot token or a Human cookie/query identity with its
  existing resolver.

Both adapters then produce the same neutral caller:

```rust
pub enum SessionCaller {
    Human {
        actor_id: String,
        owner_id: String,
        display_name: Option<String>,
    },
    Bot {
        bot_uuid: String,
    },
}
```

`owner_id` is the persisted Bot ownership key for the authenticated Human:
the legacy `staff_no` or the V1 Gateway User ID. Raw cookies, tokens, headers,
Gateway claim structs, tenant data, and HTTP types do not cross into the shared
service.

Identity extraction does not grant resource authority. The shared application
service performs all database-backed checks:

- private-Group membership or ownership access;
- public-Group access and non-member role restrictions;
- whether a Human owns the requested creator Bot;
- whether a Bot is attempting to act as anything other than itself;
- whether the effective creator may access the Group.

## Shared Application Contract

The shared create command is transport-neutral:

```rust
pub struct CreateSessionLaunch {
    pub caller: SessionCaller,
    pub group_id: String,
    pub requested_creator: Option<String>,
    pub title: Option<String>,
    pub kind: Option<SessionKind>,
    pub input: Option<serde_json::Value>,
    pub meta: Option<serde_json::Value>,
    pub public_creator_role: Option<RequestedSessionRole>,
    pub context_delivery: Option<DeliveryType>,
}
```

An omitted `requested_creator` means the authenticated caller. Keeping it
optional also preserves the legacy distinction between an inferred Human
creator and an explicitly supplied Human `created_by`, which affects whether
the Human is added to the new Session roster.

Reactivation is a separate application method and command containing the
target `session_id`. It shares authorization, defaulting, runtime-start, and
error helpers with creation, but only the legacy adapter calls it.

```rust
#[async_trait]
pub trait SessionLaunchService: Send + Sync {
    async fn create(
        &self,
        command: CreateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError>;

    async fn reactivate(
        &self,
        command: ReactivateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError>;
}
```

`RequestedSessionRole` retains unknown legacy strings until the shared service
can apply the historical rule: public Groups reject them, while private Groups
ignore `caller_role`. V1 produces only known non-Driver roles.

The outcome contains the domain Session, the lower-level `created` flag used by
the legacy status-code projection, and an optional StateMachine run view.
Adapters are responsible for projecting that outcome into their own response
schema; V1 always returns its create-specific 201 Envelope.

## Launch Behavior

### Group and creator authorization

The shared service preserves the legacy authorization order and semantics:

1. Load the Group and fail if it does not exist.
2. Admit every authenticated caller to a public Group.
3. For a private Group, require the Bot caller to participate, or require the
   Human caller to participate directly or own a participating Bot.
4. Resolve the effective creator from `requested_creator`, defaulting to the
   caller.
5. A Bot caller may create only as itself.
6. A Human may create as itself or as a Bot whose persisted `created_by`
   matches the Human ownership key.
7. For a private Group, independently verify that the effective creator has
   Group access.

The V1 `acting_bot_id` maps to `requested_creator`. Legacy `created_by` maps to
the same field. Neither adapter performs the ownership lookup.

### Session kind

Both protocols use the same internal `SessionKind`:

- explicit `chat` becomes `SessionKind::Chat`;
- explicit `service_invocation` becomes
  `SessionKind::ServiceInvocation`;
- when omitted, a StateMachine Group defaults to `service_invocation`;
- when omitted for other Group strategies, it defaults to `chat`.

Chat and service invocation remain two modes on one endpoint. Service
invocation is valid for Chat, ManagerWorker, and StateMachine Groups; only the
StateMachine plus service-invocation combination starts a StateMachine run.

### Participants

New Sessions inherit the complete Group participant roster and fill missing
participant modes with the existing defaults.

- The Group lead remains authoritative: Driver for Chat and StateMachine
  fallback Chat, Manager for ManagerWorker.
- A public-Group creator not already in the roster is added using the requested
  public creator role, defaulting to Consultant. Driver is not permitted for a
  non-member creator.
- A Human invoking a StateMachine service is present as an Observer with
  `Present` mode so Human nodes can route input correctly.
- An explicitly supplied Human `created_by` is added to a new private Session
  as a Human Driver when no earlier rule added that Human.
- An inferred Human creator is not automatically added, preserving the legacy
  proxy behavior.

Participant construction occurs before the create write so a required
participant persistence failure cannot be silently ignored.

### Input

The application contract remains `Option<serde_json::Value>` and does not
perform field-level schema mapping.

- A string remains `Value::String`.
- An object remains `Value::Object` with every field preserved.
- Omitted input remains `None`; V1 no longer wraps Group context into a
  synthetic `{"query": ...}` input.
- Reactivation replaces the whole stored input only when legacy supplies a new
  input, following the existing repository behavior.

For Chat and non-StateMachine service invocation, the full value is delivered
through `SessionContext`: strings render directly and other JSON renders as
formatted JSON. ManagerWorker shows the task input to the Manager, while
Workers receive coordination instructions without the task body.

For StateMachine service invocation, the full value becomes the run input and
is available to the runtime and Judge without application-layer schema
validation.

### Metadata

`meta` is stored and returned without renaming, derivation, merging, or source
inference. The application treats it as raw JSON. Known current consumers are:

- `callback_target.user_id` and
  `callback_target.open_conversation_id` for AntDing callbacks;
- `callback_target.baas_session_id` for BaaS callbacks;
- `channel.binding_id`, `channel.conversation_id`,
  `channel.conversation_type`, `channel.session_scope`, and
  `channel.im_user_id` for channel conversation reconstruction;
- `channel.context_projection`, with top-level `context_projection` as the
  existing fallback.

`channel.source` remains caller-supplied for HTTP requests. It is not inferred
from `binding_id` and is not consistency-checked. Internal ChannelService
callers may continue generating it from their inbound message context.

### Context delivery

The common `context_delivery` value controls only the initial SessionContext
delivery and is accepted for both kinds:

- Chat and ManagerWorker Sessions receive SessionContext.
- Chat Group service invocation receives SessionContext.
- ManagerWorker service invocation receives SessionContext.
- StateMachine Group Chat receives the free-chat fallback SessionContext.
- StateMachine service invocation starts the run and does not emit
  SessionContext, so the value has no effect.
- ManagerWorker always sends to the Manager even when `inject` is requested.
- Other participants receive injected context.

SessionContext notification remains best-effort and asynchronous, matching the
legacy route. It is emitted only for a newly created Session and only when no
StateMachine run was started.

### Reactivation

The legacy adapter keeps accepting `session_id` in the create-session body.
When present it calls `SessionLaunchService::reactivate`; all current status,
callback-state, input replacement, and Group-membership checks remain intact.

V1 has no `session_id` request field and no reactivation route. The shared
application capability is not evidence of a public V1 operation.

## V1 HTTP Contract

The V1 route remains:

```http
POST /openapi/v1/collaboration/groups/{group_id}/sessions
```

Its request uses V1 field names:

```json
{
  "title": "optional title",
  "kind": "chat",
  "acting_bot_id": "optional-owned-bot",
  "creator_role": "consultant",
  "input": "a string or an object",
  "meta": {
    "callback_target": {},
    "channel": {}
  },
  "context_delivery": "send"
}
```

`creator_role` maps to legacy `caller_role` and is relevant only when a public
Group creator is not already a Group participant.

`input` uses an explicit OpenAPI `oneOf`:

```yaml
oneOf:
  - type: string
  - type: object
    additionalProperties: true
    properties:
      query:
        type: string
```

This preserves the existing `{ "query": "..." }` V1 shape while allowing
arbitrary object input. Arrays, numbers, booleans, and null are rejected by the
V1 adapter; legacy keeps accepting its existing arbitrary JSON input.

`meta` is an object with documented known properties and
`additionalProperties: true`. Its legacy field names remain unchanged, and no
`payload` or `extensions` field is introduced.

The operation opts into V1 `human_or_owned_bot` identity selection and keeps
Gateway authentication declarations in OpenAPI. It returns HTTP 201 and the
standard V1 created Envelope. The V1 Session detail explicitly projects the
raw `input`, `meta`, resolved `kind`, and optional StateMachine run information
without exposing legacy aliases such as `id` alongside `session_id`.

The request never accepts `session_id`; unknown fields remain rejected.

## Legacy HTTP Compatibility

The legacy request and response remain stable:

- endpoint path is unchanged;
- `session_id`, `session_title`, `session_kind`, `created_by`, `caller_role`,
  `group_context_delivery`, `input`, and `meta` retain their names;
- Bot-token and Human-cookie resolution remain unchanged;
- `session_id` continues selecting reactivation;
- omitted kind retains Group-strategy inference;
- legacy continues accepting arbitrary JSON for `input` and `meta`;
- success status remains 201 for create and 200 for reactivation;
- response JSON retains both `id` and `session_id` and the current
  StateMachine run fields;
- legacy error JSON and status mapping remain in the legacy adapter.

Compatibility is protected by keeping the existing legacy HTTP contract tests
and adding shared-service parity tests before slimming the handler.

## Error Handling

The shared service returns transport-neutral errors for:

- unauthenticated/invalid neutral caller construction (normally rejected in
  the adapter before invocation);
- Group or Session not found;
- forbidden Group access or creator selection;
- invalid public creator role;
- invalid Session parameters;
- running or callback-pending reactivation conflict;
- StateMachine runtime start failure;
- persistence and dependency failures.

V1 maps them to its stable error codes and Envelope. Legacy maps them to its
existing status codes and JSON error shapes.

StateMachine startup currently occurs after Session persistence. If startup
fails, the request returns an error and the created/reactivated Session remains
persisted; this behavior is preserved rather than adding an unrelated rollback
mechanism.

## Testing Strategy

### Shared application tests

Cover the full behavior matrix through the transport-neutral service:

| Group strategy | kind | expected startup |
| --- | --- | --- |
| Chat | chat | SessionContext |
| Chat | service_invocation | SessionContext + service lifecycle |
| ManagerWorker | chat | Manager-focused SessionContext |
| ManagerWorker | service_invocation | Manager-focused SessionContext + service lifecycle |
| StateMachine | chat | free-chat SessionContext |
| StateMachine | service_invocation | StateMachine run, no SessionContext |

Also cover:

- Human self creation and owned-Bot creation;
- Bot self creation and rejection of another creator;
- private Group access and public non-member insertion;
- public role validation;
- explicit versus inferred Human creator behavior;
- raw string and object input preservation;
- raw metadata preservation;
- omitted input preservation;
- context delivery forwarding;
- StateMachine Human Observer insertion;
- create versus reactivation behavior and errors.

### Legacy adapter regression tests

- Existing request DTO and `session_create_contract` tests remain green.
- Add route-level assertions for unchanged field mapping, create/reactivate
  status, legacy JSON projection, and identity-to-neutral-caller mapping.
- Confirm legacy arbitrary JSON input and metadata remain accepted.

### V1 adapter and application tests

- Human and Bot Gateway callers map to the neutral caller.
- Human `acting_bot_id` ownership and Bot self-only behavior are enforced.
- String/object input round-trips exactly.
- `kind`, `meta`, `creator_role`, and `context_delivery` map correctly.
- Unknown fields, input arrays/scalars other than string, and a V1
  `session_id` field are rejected.
- StateMachine service invocation returns run information in a V1 Envelope.
- V1 never calls reactivation.

### Contract and architecture tests

- Update and run `tests/openapi/test_session_v1_contract.py`.
- Regenerate and verify the checked-in Gateway OpenAPI artifact if the
  repository contract workflow requires it.
- Run focused `bcs-service-api`, `bcs-session`, `bcs-app-session`, `bcs-http`,
  `bcs-api-http`, and bootstrap tests.
- Run BCS architecture and formatting gates for the touched boundaries.

## Compatibility Summary

After the change, the two HTTP protocols differ only where intended:

| Concern | Legacy | V1 |
| --- | --- | --- |
| Authentication transport | Bot token / Human cookie | Gateway Principal |
| Request field names | legacy names | V1 names |
| Input admission | arbitrary JSON | string or object |
| Reactivation | supported through `session_id` | not exposed |
| Success/error projection | legacy JSON | V1 Envelope |
| Authorization and launch workflow | shared | shared |

No persistence migration is required.
