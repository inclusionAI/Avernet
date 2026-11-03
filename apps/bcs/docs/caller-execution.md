# Request-scoped caller execution

Channel providers may opt into caller execution with `requires_caller_execution` and validate the binding target with `validate_binding_config`. The default implementations preserve existing providers. The channel service additionally enforces a single Bot target. Group bindings, state-machine routing, WebSocket delivery and fan-out are not supported.

An authenticated ingress may supply an `InboundMessage.execution_token`. This request-only value is not serializable and its Debug output is redacted. Local commands are processed normally. A caller-enabled binding requires a nonempty credential and sender identity before dispatch; an ordinary binding discards the credential. The normalized `WebSendCommand.caller_execution` carries the bound Bot, binding, original message and user identity, not a generic arbitrary extension map exposed to clients.

Composition supplies `BcsMessageFlow.execution_cache` using the existing cache plugin. Before admission the service writes an insert-only record with a random reference and a five-minute TTL. The record binds the message, user, binding, Bot, Group and Session. Failure to write prevents admission. A queued projection contains only the reference and non-secret scope, never the token. Canonical message content, event streams, history, run correlation and transport snapshots do not contain credentials.

At delivery preparation, only the original HTTP provider `chat.send` can resolve the reference. Scope mismatch, missing cache, failed reads or expiry fail closed. A final expiry check before queued transport I/O uses the non-secret deadline in transport context without rereading or extending the cache record. Direct nonqueued sends use the same mapping. HTTP callback and SSE use the same top-level extensions:

```json
{
  "caller_context": {"subject": {"kind": "human", "ref": "<user>"}},
  "caller_execution_token": {"type": "iam-token", "token": "<credential>"}
}
```

The HTTP adapter preserves other extensions and provider authentication but redacts the credential extension in request logs. Delivery command Debug omits request payloads. Task results, timeout notices, bot relays and future messages do not inherit caller execution. Secrets expire naturally; retries never extend their lifetime.

Deploy all consumers before enabling bindings. Old consumers reject the new projection through its strict decoder; draining caller jobs is required before a binary rollback. There is no database schema migration. Additional cache cost is one conditional write per new caller request and one read per preparation attempt, plus the existing registry routing lookup for target validation. No background scan is added.
