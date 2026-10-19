# Bot WebSocket `session.delete`

`session.delete` requests deletion of exactly one Bot-local Session history.
It does not delete a BCS Group or authorize removal of other Sessions in that
Group. This is a BCS-to-Bot delivery RPC, separate from the public BCS Session
management API and the local OpenClaw gateway's `sessions.delete` RPC.

## Request

```json
{
  "type": "req",
  "id": "delete-1",
  "method": "session.delete",
  "params": {
    "bcs_session_id": "group-id:session-suffix",
    "bcs_group_id": "group-id"
  }
}
```

- `bcs_session_id` is required and must be the canonical Session ID used for
  `chat.send`, with a nonempty Group ID and Session suffix.
- `bcs_group_id` is optional. When supplied, it must match the Group ID in the
  Session ID; it is a consistency check, never a deletion selector.
- A group-only request is rejected without deleting anything. Receivers must
  not guess the latest Session or expand the operation to the whole Group.

The OpenClaw BCN plugin resolves a `direct` route using the current channel
account and canonical Session ID, with BCS Session isolation enforced. It calls
OpenClaw `sessions.delete` for that exact key even when its in-memory mappings
are empty after a restart. Cache cleanup is restricted to the deleted key and
happens only after successful gateway deletion. A gateway failure returns
`DELETE_FAILED` and preserves the mappings.

The Rule Bot cancels only runs for the requested Session and clears only that
Session's history, behavior state, idempotency entries and pending task state.
Other Sessions in the same Group are retained.

## Compatibility and callers

This replaces the old group-only receiver behavior. Old callers must send a
canonical `bcs_session_id`; they must not retry rejection as group-wide cleanup.
There is currently no production BCS `session.delete` sender in this repository;
the receivers are the BCN plugin and Rule Bot. The public BCS Session deletion
API is unchanged, and this change does not add an automatic Bot cleanup call to
it. Any future sender must use this exact-Session contract. Group-wide cleanup
requires explicit enumeration and a separate request for each Session.

Invalid requests use the receiver's existing validation error envelope
(`INVALID_REQUEST` in BCN, `invalid_params` in Rule Bot). Successful deletion is
acknowledged only after the exact local cleanup operation succeeds.

## Conformance checks

- BCN: `test/gateway-protocol.test.ts` covers canonical targeting, sibling
  Session/account isolation, empty mappings after restart, validation, gateway
  scopes and failure propagation.
- Rule Bot: `session_delete_cancels_only_the_requested_session_runs_and_cache`
  and `session_delete_rejects_missing_or_conflicting_session_identity` cover
  exact targeting and rejection without cleanup.
