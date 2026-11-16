# OpenClaw Gateway Connection Recovery

`OpenClawGatewayClient` retains the RPC, event subscription and chat-stream API.
`GatewayConnection` owns the socket, handshake and connection lifecycle.

- Connect and disconnect are serialized per client, not across the entire pool.
- A stale connected flag without a socket must initiate a new handshake.
- Failed or cancelled handshakes clean up sockets, receive tasks and stale state.
- Old receive loops cannot invalidate a replacement socket.
- Reconnection preserves event listeners, but fails requests still awaiting a
  response on the old connection. It does not resend any application request,
  including `chat.send` or `cron.run`.
- The pool returns only a connected client; failure of one slot can fall through
  to another slot.

OpenClaw cron queries distinguish absence from upstream failure: a successfully
decoded empty `cron.list` result can produce `404 Task not found`. Transport,
RPC and malformed-list failures propagate through the existing HTTP error path
(500), never as an empty result or a false 404. Other engine implementations and
successful response schemas are unchanged.

## Child run completion

Each chat stream registers accepted `sessions_spawn` results by exact child
run ID and session key. Matching child `chat` final/error/aborted frames update
that child once before parent-stream filtering; they are not parent replies.
Parent continuation never decrements the child count. This supports several
children completing in one parent run or one subsequent parent continuation.

Child completion alone never forwards a deferred parent final. A subsequent
parent continuation and final, or correlated v1 announce finals, must complete
the response. Announce IDs must match a registered child's full identity;
duplicate and foreign frames cannot settle another child. Direct children
covered by a subsequent parent final are not awaited again in mixed announce
fan-in. Bounded early terminal buffering handles fast children and ACK races.

These observations prove child execution completion and a later parent result,
not that the model consumed every queued result. After an observed child
completion, an unknown same-parent continuation is associated by session/time;
the Gateway protocol does not expose a causal child-delivery identity for that
path. Missing frames retain the existing explicit timeout behavior.
