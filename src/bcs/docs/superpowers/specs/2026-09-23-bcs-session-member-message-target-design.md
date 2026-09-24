# BCS Session Member Message Target Routing

- **Date:** 2026-09-23
- **Status:** Design approved for implementation planning
- **Scope:** OpenClaw outbound target resolution + BCN plugin
- **Related incident:** `message(action=send, channel=bcs, target=human_378611)` returned `Unknown target "human_378611" for BCS.`

## Problem

The OpenClaw `message` tool treats `target` as an external channel destination. The
BCS channel currently advertises group-only delivery and does not provide a
channel target resolver or directory adapter. Consequently, a BCS Human Actor ID
such as `human_378611` is sent through the generic group-target lookup path and is
rejected before the BCS WebSocket is used.

The intended behavior is different:

```text
message(action=send, channel=bcs, target=<session member actor id>)
  -> validate the actor against the current BCS Session
  -> route to the current BCS Session
  -> prepend a BCS-compatible @mention
  -> send using the current BCS run/session context
```

This is not a direct-message feature. It is a convenience target syntax for
sending one directed @mention into the current BCS Session.

## Decision Summary

Extend the OpenClaw target-resolution contract so a channel target resolver can
receive the host-owned `currentSessionKey`. The BCS plugin will use that key to
read its existing `sessionKey -> BCS Session` and `sessionKey -> participants`
mappings.

When the input target exactly matches a Human or Bot participant in the current
BCS Session, the resolver returns the current OpenClaw session key as the
transport target and attaches provider metadata identifying the participant to
mention. The BCS plugin then prefixes the outbound text with `@<actor_id>`.

The host must preserve this provider metadata through the message-send path. It
is opaque to generic channel code and is consumed only by the BCS plugin's
payload preparation hook.

No global actor search, cross-Session routing, new BCS HTTP endpoint, or direct
Human-to-Human BCS conversation is introduced.

## Goals

1. Allow `target=human_<id>` for a Human participant in the current BCS Session.
2. Allow the same behavior for a Bot actor ID in the current BCS Session.
3. Preserve the existing BCS `run_id` and `bcs_session_id` delivery path.
4. Convert the target into a normal BCS text @mention, so existing BCS routing,
   persistence, Human Mention Notify, and frontend rendering remain authoritative.
5. Reject targets that are not members of the current Session.
6. Keep concurrent BCS Sessions isolated even when they contain the same actor ID.
7. Preserve existing behavior for all non-BCS channels and existing BCS targets.
8. Avoid adding a new BCS transport protocol solely for this convenience feature.

## Non-goals

- Sending a private BCS message directly to a Human Actor.
- Searching all BCS Groups or Sessions for an actor.
- Resolving a target against a previous, sibling, or arbitrary Session.
- Changing BCS routing semantics for ordinary text @mentions.
- Changing `route.resolve`; that operation remains Bot-focused and is not used
  for Human target resolution.
- Changing Human Mention Notify provider contracts.
- Adding persistence, migrations, or new BCS database tables.

## Terminology

- **Actor ID:** A BCS participant identifier, for example `human_378611` or a
  Bot UUID.
- **BCS Session ID:** The canonical BCS collaboration Session identifier,
  normally supplied as `bcs_session_id` and represented by the BCS Session key
  format used by the plugin.
- **OpenClaw session key:** The host-owned key passed to the active agent turn.
  It is not interchangeable with a BCS Session ID.
- **Session-member target:** A `message` target that exactly matches one actor in
  the current BCS Session participant list.
- **Provider metadata:** Opaque target-resolution data preserved by OpenClaw and
  consumed by the selected channel plugin.

## Existing Evidence

### BCS already receives and caches the required context

`src/bcs/crates/plugins/openclaw-channel-bcn/src/inbound-handler.ts` already
receives both:

- `params.bcs_session_id`;
- `params.session_context.session_id` and `participants`.

`rememberTaskToolSession()` stores:

- `sessionKey -> BCS group ID`;
- `sessionKey -> BCS Session ID`;
- `sessionKey -> session participant IDs`;
- `sessionKey -> BCS WebSocket client`.

The plugin already exposes:

```ts
resolveBcsSessionIdFromSessionKey(sessionKey)
```

The missing piece is that OpenClaw's generic target-resolution call does not pass
`input.sessionKey` into the plugin resolver.

### Current BCS outbound delivery requires current context

The BCS plugin's `outbound.sendText` resolves the active run and BCS Session
from its `to` value. It rejects delivery if the current run or Session context
is absent. The new member-target route must therefore normalize `to` to the
current OpenClaw session key rather than leaving it as `human_<id>`.

### Existing BCS routing already understands Human mentions

BCS routing validates @mentions against the Session participant list. Human
mentions are retained as actor IDs for persistence and Human Mention Notify, but
are not treated as Bot response targets. The feature should reuse this behavior
by sending text such as:

```text
@human_378611 请处理这个问题
```

## Proposed Architecture

### 1. Host target-resolution context

Extend the OpenClaw target-resolution call chain with an optional,
host-owned `currentSessionKey`:

```ts
resolveChannelTarget({
  cfg,
  channel,
  input,
  accountId,
  preferredKind,
  currentSessionKey,
})
```

The plugin contract becomes:

```ts
targetResolver.resolveTarget({
  cfg,
  accountId,
  input,
  normalized,
  preferredKind,
  currentSessionKey,
})
```

Required propagation points:

1. `runMessageAction(input)` retains the existing `input.sessionKey`.
2. `resolveActionTarget()` passes it to `resolveResolvedTargetOrThrow()`.
3. `resolveResolvedTargetOrThrow()` passes it to `resolveChannelTarget()`.
4. `resolveChannelTarget()` passes it to the plugin's `targetResolver`.

The value is host context, not model-controlled input. A plugin must not infer
or override it from `target`, message text, or user-provided fields.

### 2. Opaque provider metadata

Extend the resolved target shape with an optional provider-owned metadata field:

```ts
providerMetadata?: Record<string, unknown>
```

The host treats this field as opaque and only preserves it through the send path.
It must not make routing or authorization decisions based on provider-specific
keys.

The metadata must be carried to the channel-owned send preparation hook through
`ChannelMessagePreparedSendPayloadContext` / the equivalent outbound context:

```ts
resolvedTarget?: ResolvedMessagingTarget
```

The host must preserve metadata for both local and gateway/plugin-owned send
paths. If a path cannot preserve it, the BCS resolver must not return a
session-member target through that path; it must fail closed instead.

### 3. BCS target resolver behavior

The BCS plugin registers a `messaging.targetResolver` implementation. It handles
a target as a session-member target only when all of the following hold:

1. `currentSessionKey` is present;
2. the plugin has a cached BCS Session ID for that key;
3. the plugin has a cached participant list for that key;
4. `input` exactly matches a cached participant actor ID;
5. the actor is a supported Human or Bot participant;
6. the target is not the current sender actor, unless the existing BCS contract
   explicitly permits self-mentions for the specific send path.

For a match, it returns:

```ts
{
  to: currentSessionKey,
  kind: "group",
  display: participantDisplayName ?? actorId,
  source: "normalized",
  providerMetadata: {
    route: "bcs-session-member-mention",
    bcsSessionId,
    actorId,
    actorKind: "human" | "bot",
  },
}
```

`to` is intentionally the OpenClaw session key. This lets the existing BCS
`resolveActiveRunId(to)` and `resolveBcsSessionIdFromSessionKey(to)` logic
continue to work without a second delivery path.

The resolver must not use a mutable global "last target" slot. The returned
metadata travels with the current send operation, which keeps concurrent Sessions
isolated.

### 4. BCS outbound message preparation

The BCS plugin consumes `providerMetadata.route ===
"bcs-session-member-mention"` in its existing send payload preparation hook.
It transforms the text payload as follows:

```text
input:  请处理这个问题
output: @human_378611 请处理这个问题
```

For a Bot target:

```text
input:  请检查数据库
output: @bot_uuid 请检查数据库
```

Preparation requirements:

- preserve all attachments and non-text payload fields;
- prefix exactly once, including on retries or mirrored sends;
- do not add a second prefix if the text already begins with the same target
  mention;
- use the actor ID, not a display name, as the canonical routing token;
- keep the original actor ID in persisted structured mention metadata through
  the normal BCS routing path;
- do not expose provider metadata in the user-visible response.

The BCS transport then sends the prepared text to the current Session using the
existing `run_id` and Session event format.

### 5. Normal target behavior

The special route applies only to exact current-Session participant IDs. The
following remain unchanged:

- ordinary BCS group/session targets;
- targets for other channels;
- BCS names that are not exact actor IDs;
- BCS `bcs_route` tool behavior;
- existing automatic replies through the current Session.

If a target looks like an actor ID but is not in the current Session, the plugin
must return a target-specific unknown error. It must not search another Session
or downgrade to a global directory lookup.

## Data Flow

```text
message(action=send,
  channel=bcs,
  target=human_378611,
  message=请处理这个问题)
        │
        ▼
OpenClaw runMessageAction
  input.sessionKey = current OpenClaw BCS session key
        │
        ▼
resolveActionTarget
  passes currentSessionKey to BCS targetResolver
        │
        ▼
BCS targetResolver
  sessionKey -> bcsSessionId
  sessionKey -> participants
  exact membership check
        │
        ├─ no match -> fail closed
        │
        ▼
Resolved target
  to = currentSessionKey
  metadata.actorId = human_378611
        │
        ▼
BCS payload preparation
  @human_378611 请处理这个问题
        │
        ▼
Existing BCS outbound.sendText
  resolveActiveRunId(currentSessionKey)
  resolveBcsSessionIdFromSessionKey(currentSessionKey)
        │
        ▼
BCS chat event -> current Session
        │
        ▼
Existing BCS mention routing + Human Mention Notify
```

## Error Semantics

Errors must be deterministic and must not leak a fallback route:

| Condition | Result |
| --- | --- |
| `currentSessionKey` absent | Existing BCS unknown-target behavior; no cross-Session lookup |
| Session mapping absent | `BCS_CURRENT_SESSION_CONTEXT_UNAVAILABLE` |
| Participant cache absent | `BCS_CURRENT_SESSION_CONTEXT_UNAVAILABLE` |
| Target not a current participant | `BCS_UNKNOWN_SESSION_MEMBER` |
| Target is a current participant but current run is absent at send time | Existing `BCS V3 outbound reply has no active run/session context` |
| Current BCS Session ID is stale or missing | `BCS_CURRENT_SESSION_CONTEXT_UNAVAILABLE` |
| BCS WebSocket unavailable | Existing `BCS WebSocket not connected` |

The message action must not report success if the target conversion or final BCS
send failed.

## Security and Isolation

1. The current Session key is supplied by the host runtime, never by the model.
2. Actor membership is checked against the current Session snapshot only.
3. No global actor directory is queried.
4. A target from Session A cannot be routed through Session B's mapping.
5. Provider metadata must not be accepted from arbitrary message parameters.
6. The plugin must use exact actor ID comparison; display-name substring matches
   are not allowed for this feature.
7. The feature must respect existing BCS participant visibility and routing
   behavior. It does not create membership or bypass authorization.
8. Provider metadata must not contain credentials, tokens, or private URLs.

## Compatibility and Rollout

This is an additive, opt-in behavior:

- OpenClaw target resolvers that ignore `currentSessionKey` remain valid.
- Plugins that do not return provider metadata retain existing behavior.
- BCS target behavior changes only when the target exactly matches a participant
  in the active BCS Session.
- No database migration is required.
- No BCS wire-contract change is required.
- The BCS plugin package version must be bumped when the host/plugin contract is
  released together.
- Host and plugin versions must be deployed compatibly: an older host may ignore
  the new context, in which case session-member target routing fails closed rather
  than routing globally.

A temporary compatibility diagnostic should log the route decision with:

```text
channel=bcs
route=bcs-session-member-mention
session_key=<redacted-or-hashed-as-required>
bcs_session_id=<safe identifier>
actor_id=<actor id>
result=<resolved|not_member|context_unavailable|send_failed>
```

Do not log message contents, tokens, or invitation URLs.

## Testing Strategy

### OpenClaw host contract tests

Add tests that verify:

1. `input.sessionKey` reaches `targetResolver.resolveTarget`.
2. Existing resolver callers without a session key still work.
3. `providerMetadata` survives target resolution and reaches the send
   preparation/outbound context.
4. Metadata is not synthesized from model-controlled parameters.
5. Two concurrent sends preserve their independent resolved metadata.
6. A failed resolver does not fall through to an unrelated global target.

### BCN plugin unit tests

Add tests for:

1. `human_<id>` resolves when it is in the current Session.
2. A Bot actor ID resolves when it is in the current Session.
3. A Human actor in another Session is rejected.
4. An unknown actor is rejected.
5. Missing `currentSessionKey` fails closed.
6. Missing Session ID mapping fails closed.
7. Missing participant snapshot fails closed.
8. The resolved `to` is the current OpenClaw session key.
9. The prepared text receives exactly one `@<actor_id>` prefix.
10. Existing @ prefix is not duplicated.
11. Human and Bot targets use the same current-session transport path.
12. Two active Sessions containing the same actor remain isolated.

### BCS integration / protocol tests

Using the existing mock BCS WebSocket support, verify that:

1. `message(target=human_1)` emits a chat event for Session A with text
   beginning `@human_1`.
2. `message(target=bot_1)` emits a chat event for the same Session with
   `@bot_1`.
3. The emitted event uses Session A's active run ID and canonical BCS Session ID.
4. A target not present in Session A does not emit a chat event.
5. The existing BCS route and Human Mention Notify behavior observes the actor
   ID after the transformed message is routed.
6. A Session B send cannot reuse Session A's actor metadata or run mapping.

### Regression coverage

Run the closest OpenClaw plugin/target-resolution tests and the BCN plugin tests.
Run the relevant BCS WebSocket and message-flow contract tests. The implementation
plan must list the exact commands and state any cross-repository test that cannot
run in the Avernet checkout.

## Implementation Boundaries

### OpenClaw repository

Expected host changes are limited to the target-resolution and outbound-send
contracts needed to carry:

- `currentSessionKey` into target resolvers;
- the resolved target metadata into the send preparation path.

Do not add BCS-specific logic to generic OpenClaw core beyond these transport-
neutral extension points.

### Avernet / BCS repository

Expected plugin changes are limited to:

- BCS target resolver registration;
- current Session membership lookup using existing plugin state;
- BCS provider metadata construction;
- BCS payload preparation for the canonical @mention prefix;
- plugin and protocol tests;
- user-facing BCS plugin documentation if the message target syntax is exposed.

Do not change BCS core routing, persistence, Human Notify APIs, or database schema
for this feature.

## Rejected Alternatives

### Global actor directory lookup

Rejected because an actor ID does not identify a unique Session. It would risk
cross-Session delivery and would make a convenience target bypass current
conversation isolation.

### Mutable pending-target map keyed only by Session

Rejected because concurrent sends in one Session can overwrite one another. The
actor target must travel as per-operation metadata.

### Extending `route.resolve` to resolve Humans

Rejected for this feature because the plugin already has the current Session
participant snapshot, and `route.resolve` is a Bot routing protocol. Expanding it
would add wire and server complexity without improving correctness.

### Treating `human_<id>` as a direct BCS channel target

Rejected because BCS has no direct Human transport in this path. The correct
semantics are a directed @mention in the current Session.

## Acceptance Criteria

The design is implemented successfully when all of the following are true:

- The original screenshot request no longer returns generic `Unknown target` when
  `human_378611` is a participant in the current BCS Session.
- The resulting BCS message is visibly a Session message with an @mention.
- The target Human receives the existing Human Mention Notify behavior where the
  Group policy and Provider configuration allow it.
- A Human or Bot outside the current Session cannot be reached through this target
  syntax.
- Concurrent BCS Sessions do not cross-route.
- Existing BCS replies, Bot routing, and non-BCS message actions remain green.
