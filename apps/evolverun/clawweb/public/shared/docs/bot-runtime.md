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

`BotEngineOperations` validates the Agent-facing adapter method/path contract for OC, CC and TE reads and approved writes, over the same `BotRuntime.request`. Read-only file interfaces using POST are classified as reads. Conversation sending supports these three engines; TE Shell is rejected before provider dispatch.

Engine operations accept only declared paths. Optional structured `query.limit` (1–1000) is restricted to Cron run-history reads; `query.force` is restricted to immediate Cron execution. Queries are serialized by the runtime, not embedded in caller-provided paths. Cron HTTP writes use a schedule expression string and command, not the internal schedule/payload objects. TE Cron enable/disable use their dedicated POST endpoints. OC/CC MCP start/stop/restart are not advertised or dispatched because the current plugins return 501. The Agent-facing contract is shared by Repair and session recovery: MCP and user Skill reads remain available; mutations and whole-Bot configuration application are not exposed. Owner performs those changes through the product workflow. Generic file writes reject explicit managed MCP/Skill/Cron paths. The general BotRuntime.request transport and Backend/adapter APIs are unchanged. Cron management uses declared adapter operations, never Shell, native engine CLI/RPC or scheduling-file edits.

OC/CC recovery transcripts use read-only NAS or controlled Shell file reads. TE recovery uses uploaded trajectories first; its session API over the BaaS-provided connection is optional supplemental evidence. `BotSessionTrajectoryReader` is an injected trace query contract, implemented by the internal host using its existing TCLog queries. It is not a second ingestion service or a product OpenAPI client. A missing session or trace does not establish that the original task did not execute.

Callers may provide an exact `messageMarker` and `notBeforeMs` for delivery verification. A reader may return `delivery_confirmed: true` only after matching the marker in an actual user input of the scoped owner/Bot/session trace at or after that time; previews, assistant quotations and tool output cannot confirm delivery. Callers that omit these optional fields retain the existing read behavior.

Production connections use the existing BaaS application credentials and proxy token. Non-TE Engine HTTP requests carry the application identity required by Engine; no BaaS API key is sent to the proxy. The optional local development identity remains confined to the existing development path.

Historical recovery may call `BotSessionTrajectoryReader` with the logical `BotTarget`, without resolving a current container. Readers that enforce historical scope declare `supportsHistoricalBoundary: true`; older readers remain available online but are not enabled for historical simulation. Optional `until` binds a millisecond cutoff, a trace/observation anchor and, when known, the original physical session ID. Implementations must check owner/Bot/session on every list and direct detail/content request, return no records when the anchor is unavailable, and exclude later events and completion fields (including previews and full output) whose timestamps do not establish visibility at the cutoff. Pagination cannot widen that scope. Omitting `until` preserves online behavior. The public contract owns these semantics; each host supplies its existing source reader.
