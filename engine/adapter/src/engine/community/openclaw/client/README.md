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
