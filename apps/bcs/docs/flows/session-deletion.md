# Bot-local session deletion

## Scope and path

The `session.delete` WebSocket request is a BCS-to-Bot delivery RPC for one Bot-local Session, distinct from the public BCS Session management API. The receiver requires a canonical `bcs_session_id`; an optional `bcs_group_id` must match the embedded Group ID. A group-only request is rejected rather than expanded to a group-wide delete.

The BCN receiver resolves the exact route and asks the local gateway to delete that Session. It clears only the corresponding cached mapping after a successful deletion; gateway failure returns `DELETE_FAILED` and preserves mappings. Rule Bot cancellation and state cleanup are likewise limited to the requested Session.

## Evidence and limitations

[Session deletion protocol](../session-delete-protocol.md) is the source for request format, compatibility and named conformance tests. It states that this repository currently has no production BCS sender for this RPC; do not infer that the separate public deletion API automatically invokes it. Verify the relevant sender, receiver and deployed versions before diagnosing a runtime event.
