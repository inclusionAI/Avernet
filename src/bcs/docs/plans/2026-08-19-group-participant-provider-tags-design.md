# Group Participant Provider Tags Design

- **Date:** 2026-08-19
- **Status:** Implemented
- **Scope:** BCS group creation, participant persistence, Session snapshots, and Provider `chat.send` delivery

## Problem

BCS Provider downlink already supports routing tags for direct A2A chat. A
`chat.send` frame may contain `params.tags`, and `bcs-provider-http` projects
those values to `ProviderWebhookRequest.to_bot.tags`. Group chat does not
currently populate the field: group creation has no per-participant tags, and
the shared group `build_chat_send_frame` always writes an empty tag list.

Group execution is Session-scoped. `Group.participants` seeds a Session, while
`Session.participants` is the runtime participant snapshot used for routing and
delivery. Persisting tags only on the Group would therefore be insufficient:
the group message flow replaces the Group participant view with the current
Session participants before selecting and delivering to a target.

## Decision

Add tags as per-participant, group-scoped metadata:

```json
{
  "driver_bot": "bot-driver",
  "participants": [
    {
      "bot_uuid": "bot-reviewer",
      "role": "consultant",
      "tags": ["tenant-a", "review"]
    }
  ]
}
```

Tags belong to the Group membership rather than the global Bot or
`ProviderBotBinding`. Different groups may assign different tags to the same
Bot, and different participants in one group may have different tags.

The canonical flow is:

```text
create-group participants[].tags
    -> Group.participants[].tags
    -> Session creation copies the complete Participant
    -> Session.participants[].tags
    -> resolve the target Participant in the current Session
    -> chat.send.params.tags
    -> ProviderWebhookRequest.to_bot.tags
```

`Group.participants` is the template for future Sessions.
`Session.participants` is the authority for an active Session. Once a Session
has been created, later Group membership changes must not implicitly mutate its
participant tags.

## Contract and Application Changes

The legacy create-group contract and OpenAPI V1 `CreateParticipant` add an
optional `tags: string[]` field. Participant detail responses expose `tags` so
callers can verify the stored membership metadata. An omitted field is
equivalent to an empty list.

The inbound HTTP adapters only deserialize and map the field. The group
application normalizes every tag by trimming surrounding whitespace and
discarding empty values, matching the existing direct A2A tag behavior. Order
is preserved. The application carries the normalized list through
`GroupCreateParticipantCommand` into the shared domain `Participant`.

`Participant` gains `tags: Vec<String>` with a Serde default. Both Group and
Session use this type. Group creation stores the tagged participants and
copies the complete values into the automatically created initial Session.
Every later Session creation also seeds its participant snapshot from the
tagged Group participants.

Phase one supports setting tags only during group creation. Adding a member to
an existing group assigns an empty list, and participant update does not expose
a tag mutation operation. DM creation remains unchanged because callers do not
supply its participants directly.

## Persistence

`bcs_group_participants` adds a nullable `tags_json` text column. Group Store
writes a JSON string array using the existing parameter-bound database APIs and
decodes a missing or `NULL` value as an empty list. The MySQL migration,
SQLite versioned migration, and baseline schemas are updated together.

Session Store needs no independent tag column. Its `participants` JSON already
serializes the complete `Participant` values. All Session load paths must
deserialize that complete JSON without reconstructing a reduced Participant
that drops tags. Historical Session JSON has no `tags` field and loads it as an
empty list through the Serde default.

No user-supplied tag value is interpolated into SQL. Tags are routing
identifiers, not a credential channel, and callers must not place secrets in
them.

## Provider Delivery

The shared group `build_chat_send_frame` accepts the target participant's tags
instead of unconditionally writing `[]`. Each group-scoped active-delivery path
resolves the target from the current Session participant snapshot:

- ordinary user or Bot group-message routing;
- Bot callback forwarding;
- group system messages that use `chat.send`; and
- state-machine node dispatch.

Tags are populated only when the resolved delivery target is
`BotDeliveryTarget::HttpProvider`. WebSocket targets continue to receive the
existing empty group tag list. `chat.inject`, abort, history, and direct A2A
behavior do not change.

The Provider HTTP adapter requires no new transformation. Its existing
`provider_tags_from_params` path continues to convert
`chat.send.params.tags` into `to_bot.tags`. Contract coverage is extended to
prove that tags originating at group creation reach the Provider webhook.

## Compatibility and Risk

The change is additive at the HTTP boundary. Existing create-group requests,
Group rows, and Session JSON deserialize to empty tags and preserve current
delivery behavior.

The main correctness risk is reading tags from `Group.participants` during an
active Session. That would violate Session snapshot semantics and could deliver
the wrong routing identity. Message-flow tests must deliberately use different
Group and Session tag values and assert that the Session value wins.

Another risk is cross-target leakage. Delivery tests use two participants with
different tags and verify that each Provider request contains only the target
participant's list.

No frontend configuration UI or new CLI flag is included. Frontend transport
types may accept and display the field without adding a user-facing editor.

## Validation

- Group application coverage verifies create-time normalization and participant
  detail projection.
- Group repository conformance coverage verifies tags survive upsert and
  participant addition.
- Session application coverage verifies a newly created Session inherits tags
  from the parent Group participant snapshot.
- Message-flow coverage deliberately assigns different Group and Session tags
  and verifies Provider `chat.send.params.tags` uses the Session value.
- Protocol coverage verifies the shared `chat.send` builder preserves non-empty
  tags; the existing Provider transport contract covers projection from
  `params.tags` to `to_bot.tags`.
- SQLite migration tests cover migration 009 planning, application, and
  idempotency. The MySQL migration is additive and uses `IF NOT EXISTS` so it
  is safe with the updated baseline schema.
- The complete Rust workspace compiles with all test targets, and the bundled
  OpenAPI contract validates successfully. Global `cargo fmt` is intentionally
  not run per repository instructions.
