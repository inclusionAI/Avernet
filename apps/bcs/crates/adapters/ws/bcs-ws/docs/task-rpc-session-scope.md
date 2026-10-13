# Task RPC Session scope

The Bot WebSocket delivery adapter accepts the following scope for
`task.dispatch`, `task.message` and `task.complete` on both V2 and V3 connections:

- `group_id` is required.
- An optional `session_id` identifies the originating Session. `bcs_session_id`
  and `sessionId` are aliases for this same field. Supplying more than one alias
  is a duplicate-field parse error.
- An explicit Session takes precedence over a Session encoded in `group_id`.
  The adapter supplies the application with the real Group ID and the normalized
  Session as `payload.bcs_session_id`.
- An explicit empty or whitespace-only Session is invalid. A Session with a
  `<group>:<suffix>` shape must match the requested Group; it cannot redirect
  the task to a different Group. The application still validates stored Session
  existence, Group membership and current participant permissions.

V3 callers may send separate scope fields:

```json
{"group_id":"group-1","bcs_session_id":"group-1:session-1"}
```

V2 callers may continue sending the existing session-scoped Group ID:

```json
{"group_id":"group-1:session-1"}
```

The historical combination `group_id=group-1:session-1` with
`session_id=group-1` continues to resolve to `group-1:session-1`. An absent or
null explicit Session preserves legacy scope recovery. Bare Group-only calls
retain their existing application behavior; the adapter does not invent a
Session or create a database row.

These control RPCs do not change V2 event framing or V3 unified Run Events. No
database schema change or extra repository lookup is required by normalization.
Conformance cases live in `tests/support/task_rpc_scope.rs` and the existing
legacy frame cases in `tests/frame_compat.rs`.
