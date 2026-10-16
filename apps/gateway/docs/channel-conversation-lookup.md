# Resolve channel conversations from a BCS Session

Relay can resolve an external IM conversation after receiving a canonical BCS
Session ID, without carrying IM identifiers through every runtime request.

```http
GET /openapi/v1/collaboration/channels/conversations/by-session?bcs_session_id=<canonical-session-id>&channel_type=dingtalk
x-avernet-bot-token: <participating-bot-token>
```

Use the Gateway's configured Bot token header (the default is
`x-avernet-bot-token`) or its existing authenticated Human credentials. Gateway
verifies the credentials and signs a Principal for BCS. Do not construct an
`X-Avernet-Principal` header in Relay. An application credential alone does not
grant Session access. If both Human and Bot identities are present, the Bot must
belong to that Human and be authorized for the Session.

The caller must be allowed to read the Session: a participating Bot, a Human
participant, or a Human who owns a participating Bot. Use the canonical BCS
Session ID, not a Group ID or TeamClaw/engine Session ID. Encode query values
with an HTTP client; `channel_type` is optional. Blank values are rejected.

```json
{
  "code": 20000,
  "message": "OK",
  "data": {
    "items": [{
      "binding_id": "binding-1",
      "conversation_id": "external-conversation-1",
      "conversation_type": "2",
      "session_scope": "conversation",
      "bcs_session_id": "session-1",
      "last_active_at": 123
    }]
  },
  "request_id": "request-1"
}
```

`items` can contain zero or multiple entries. Select the relevant binding;
do not assume a single conversation or a one-to-one relationship between an IM
conversation and BCS Sessions. Live mappings take precedence. If no live
mapping exists, ChannelService resolves historical Session metadata, including
after `/new`. Filtering existing live mappings to zero results does not trigger
historical fallback. IM user IDs, binding configuration and credentials are
excluded.

Missing/invalid Principal returns 401; unauthorized Session access or an
app-only caller returns 403; missing Session/Group returns 404; invalid query
returns 400; lookup failures return 500 rather than a successful empty list.

Gateway's existing collaboration domain forwards the path and query to BCS.
Its generated `configs/schemas/bcn.openapi.json` publishes the operation in the
served OpenAPI document. Deploy the BCS route and Gateway schema together.

This is a read-only lookup. Authorization uses the existing Session detail
application service (Session/Group reads, ownership resolution when needed,
and participant-name projection), followed by the existing Channel mapping
query. Mapping filtering may read each binding; historical fallback reads the
Session when no live mapping exists. No new SQL, cache, writes or migration is
introduced. Relay should resolve associations when needed rather than query
for every streamed event; scope any retained association by Bot and BCS Session
and handle multiple bindings and Session rollover explicitly.
