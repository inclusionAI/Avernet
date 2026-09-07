# Channel group-chat sessions

Set `config.group_chat_new_session_per_message` to `true` on a channel binding
to create an independent session for every accepted group-chat message. The
field is an optional boolean, defaulting to `false`; other types, including
`null`, are rejected on binding creation and config update.

```json
{
  "group_chat_new_session_per_message": true
}
```

Add this field to the existing provider configuration when creating a binding
with `POST /openapi/v1/collaboration/channels/bindings`, or updating it with
`PATCH /openapi/v1/collaboration/channels/bindings/{id}` using the `config`
property. Config updates replace the config, so retain all required provider
settings and credentials. Set the field to `false` or remove it to resume
reuse of the current session.

## Behavior

- Applies only to channel group chats (`conversation_type = "2"`), such as
  DingTalk groups. The existing requirement to mention the bot still applies.
- Supports both Bot and Group binding targets and both `per_sender` and
  `conversation_shared` scopes. Scope still controls sender attribution and
  conversation mapping; it no longer causes separate messages to share a session.
- Direct chats (`conversation_type = "1"`) continue to reuse their sessions.
- Each distinct accepted message creates a fresh chat session or starts a new
  state-machine session. Concurrent messages receive different session IDs.
- Commands such as `/new` retain their command behavior. Replies consumed by
  a pending HumanInput request continue that request instead of starting a run.
- Existing inbound deduplication still suppresses repeated message IDs.
- Starting another session does not cancel or archive the previous run. Its
  replies retain their original channel route through session metadata.

## Contract and compatibility

This is an additive Channel Service API configuration extension implemented by
`bcs-channel`, exposed through existing binding create/update APIs. Provider
configuration remains opaque JSON; no Plugin API signatures or database schema
change. Existing bindings require no migration and retain session reuse.
Deployments opt in per binding; disabling the setting restores reuse of the
latest mapped session. Provider plugins must preserve this service-owned field
when persisting or redacting configuration.

The `group_new_session_per_message_*` tests in `bcs-channel` exercise the public
application service for configuration validation, both target/scope combinations,
DM compatibility, duplicate and concurrent delivery, old-session outbound routing,
and independent workflow starts.
