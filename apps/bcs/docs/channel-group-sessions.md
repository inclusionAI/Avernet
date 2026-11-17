# Channel group-chat sessions

## Chat session membership

When a channel binding targets a Group, a new chat session inherits all of that
Group's Bot and Human participants, as Web-created sessions do. Inherited roles,
participation modes and message-view scopes are preserved. The accepted inbound
sender is added as a present Human consultant only if not already a session
member; repeated messages do not replace an inherited member's settings.

This applies to both direct and group-chat conversations. Human group members
can therefore find the new session in their participant-filtered Web session
list even if they have not sent a channel message. Membership remains a snapshot:
reusing an existing session does not backfill other group members. State-machine
session creation and HumanInput admission keep their separate rules.

The `inbound_chat_sessions_inherit_group_humans_and_preserve_membership` test in
`bcs-channel` covers Chat and ManagerWorker groups, direct and group chats,
existing and new senders, inherited Human settings and session reuse.

## New session per group-chat message

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

### Behavior

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

### Contract and compatibility

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
