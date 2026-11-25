# OpenAPI IAM session context compatibility

## Requirement

`POST /openapi/v1/bots/{bot_id}/iam-token` must carry the current chat session
when refreshing Caller identity for a TeClaw runtime. Draft Bot debugging and
ordinary online conversations use `session_key`, matching the legacy IAM
contract. `sessionKey` and the OpenAPI route's earlier `session_id` spelling
remain compatible aliases.

Friend conversations continue to send an owned `session_id` together with
`f_user_id`. The delivery adapter must verify friendship and session ownership
before forwarding that value as the internal `session_key`; client-supplied
`session_key` or `sessionKey` values cannot override the verified friend
session.

## Compatibility

The change is additive. Existing clients that omit session context or use
`session_id` retain their current behavior. The legacy `/api/v1/token/iam`
contract and non-TeClaw Caller behavior are unchanged.
