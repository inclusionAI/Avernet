# Bot runtime

`@avernet/clawweb-shared/server/services/bot-runtime` is the public entrypoint for three operations:

| Operation | Input | Result |
| --- | --- | --- |
| `executeShell` | Bot identity, command, optional timeout and affinity | status, exit code, stdout/stderr, duration |
| `request` | Bot identity, container path, method, optional port/body/multipart-upload/timeout | HTTP status and decoded JSON or text body |
| `sendMessage` | Bot identity, original session key, message, delivery key | accepted/failed/unknown plus actual receipt metadata |

The package has no business module dependency. `BotRuntime` is the business-facing interface. `BotRuntimeClient` validates identity and dispatches to an injected `BotRuntimeProvider` map. `BotTargetResolver` is an auxiliary directory interface; it is not the top-level shared capability. Trusted callers such as Repair may pass their already validated, frozen `ResolvedBotTarget` rather than resolving again. Never deserialize that trusted form directly from a public request.

## Implementations and composition

- `BaasRuntimeProvider`: OpenAPI messages, execute-command, and container HTTP via the BaaS connection API. Hosts supply URLs, credentials and tenant.
- Legacy Arca remains a supported routing key and `BotRuntimeProvider` extension point. The concrete `ArcaRuntimeProvider`, AgentProxy transport and Engine worker belong to OCB's `internal/adapters`; the OCB Host registers them. Public hosts do not register a placeholder Arca provider.

Provider constructors are composition-root APIs. Business modules receive only `BotRuntime` (or a narrower `Pick`). Worker scripts and transports remain private. The shared multipart serializer is an adapter utility; business services use the runtime operations. The narrow `bot-runtime/legacy` entrypoint exists solely for existing Evolve BaaS facade; new consumers use the main entrypoint.

The public host accepts optional `sessionRecovery` injection in `createClawWebBootstrap`. With no configuration the route fails closed. No internal endpoint, MIST identity or Owner-login dependency is required by the public runtime. The existing Singlebox Stage executor is unchanged. No new local Bot runtime is introduced; OCB Owner access connects a local control plane to a remote Bot.

## Semantics

No operation retries automatically. Shell timeout/connection loss is unknown because the remote operation may have executed. HTTP calls preserve the target status, reject redirects, and bound the response body to 16 MiB. Runtime outputs are not persistence-safe by default; each consumer must sanitize logs and stored evidence.

The existing macOS development identity, when configured, is carried across both BaaS HTTP connection negotiation and the returned proxy request. Only the proxy ticket and local IAM cookie enter the second request; the BaaS application API key is not forwarded. Linux does not attach this local cookie. An HTTP 200 alone does not prove that the response came from Engine; consumers must also inspect the response body.

`sendMessage` means channel acceptance, not task success or recovery. The caller supplies `deliveryKey`; BaaS receives it as `message_id`. The internal Arca provider derives a stable owner/Bot/environment/session-scoped ID, keeps a private local receipt journal, and uses Engine idempotency. No distributed database deduplication or changed-message detection is promised. An uncertain outcome must not trigger an automatic replacement send.

The internal Arca provider receipt metadata is confirmed only after locating a user message in the original session transcript. Missing/ambiguous/reset sessions do not produce invented message IDs. BaaS platform acceptance alone remains `locationStatus: unconfirmed`.

## Controlled recovery operations

`BotEngineOperations` validates the declared Engine method/path contract for OC, CC and TE reads and approved writes, over the same `BotRuntime.request`. Read-only file interfaces using POST are classified as reads. Conversation sending supports these three engines; TE Shell is rejected before provider dispatch.

OC/CC recovery transcripts use read-only NAS or controlled Shell file reads. TE session reads use its session API over the BaaS-provided connection. `BotSessionTrajectoryReader` is an injected trace query contract, implemented by the internal host using its existing TCLog queries. It is not a second ingestion service or a product OpenAPI client.

Production connections use the existing BaaS application credentials and proxy token. Non-TE Engine HTTP requests carry the application identity required by Engine; no BaaS API key is sent to the proxy. The optional local development identity remains confined to the existing development path.
