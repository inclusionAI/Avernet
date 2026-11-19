# Request-scoped caller execution

For a channel binding that targets one Bot, trusted adapters can pass an
authenticated Human in `InboundMessage.caller_ingress`. BCS checks that the
identity matches the normalized channel sender before dispatch. The target
Bot's Caller configuration controls managed Caller admission; providing
identity does not itself enable Caller mode. Adapters must leave the field
unset unless they have authenticated the sender at their ingress boundary.

Trusted channel submissions can skip the group speaking lease when a live
single-Bot binding and its per-sender conversation mapping prove the Session
has exactly the mapped Human and the bound Bot, and that Bot is the sole send
target. The Caller identity checks still apply. Workbench and other ingress
keep their existing lease checks, as do shared or unverified channel Sessions.

The binding-level credential forwarding below is a separate path.

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

## Deployment-owned request headers

A hosting composition may install a `ProviderRequestHeaderPolicy` through
`bcs_provider_http::install_provider_request_header_policy` once before serving.
Public BCS installs none. The policy receives method, Provider identifiers and
resolved runtime extensions, then returns secret header values. It must not
perform I/O or persist credentials. The adapter invokes it only at HTTP request
construction, after URL validation, for callback and SSE delivery. It does not
change JSON extensions, admission, jobs, history or Provider authentication.
Reserved authentication/protocol headers and invalid HTTP names or values fail
closed before sending; generated values are marked sensitive. Policies also
receive non-chat methods and must explicitly select their supported requests.
The protected caller send port uses its separate wire contract and does not
invoke this ordinary Provider policy.

## Credential type ownership

The credential type is an opaque nonempty label supplied by the trusted channel
plugin. Admission requires its declared label to match `ExecutionToken`'s label;
the cache and HTTP projection preserve it exactly. Public coordination does not
interpret a label as an authentication scheme. The receiving Provider validates
its supported types and owns credential exchange. Deploy compatible consumers
before a channel starts emitting a new label; older queue consumers may reject
that label, so drain its jobs before rolling back. Cache TTL, scope validation
and credential non-persistence are unchanged.
