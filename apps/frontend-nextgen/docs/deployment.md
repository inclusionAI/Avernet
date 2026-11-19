# Frontend Nextgen deployment

## Scope

This directory supplies a portable OCI image and same-origin Nginx routing. It does not create Alibaba Cloud resources, issue certificates, register domains, or contain OAuth/private keys.

The public `@tc-chat/ui@2.0.0` package and its `@ant-design/x` dependency require `antd:^6.1.1`. `antd@6.6.1` is pinned as an approved SDK-only direct exception: do not import it from application code, and re-audit/remove it when upgrading tc-chat.

## Required upstreams

The container refuses to start until all variables are provided:

| Variable                           | Existing frontend route responsibility                                       |
| ---------------------------------- | ---------------------------------------------------------------------------- |
| `BCS_AUTH_UPSTREAM`                | BCS `/auth/*` OAuth routes                                                   |
| `TASK_ENGINE_UPSTREAM`             | Non-BBS `/openapi/v1/collaboration/tasks/*` task execute/dashboard/list/grant/revoke |
| `TEAMCLAW_GATEWAY_UPSTREAM`        | BBS `/openapi/v1/collaboration/tasks/bbs/*`, bot-scoped BBS routes, other `/openapi/*`, and non-task `/api/v1/collaboration/*` |
| `TEAMCLAW_ADMIN_UPSTREAM`          | Reserved for the separately deployed admin service; see note below           |
| `PRIVATE_CHAT_MANAGEMENT_UPSTREAM` | `/api/*` private-chat management                                             |
| `PRIVATE_CHAT_SESSION_UPSTREAM`    | `/proxypass/*` and WebSocket                                                 |
| `CLAWWEB_UPSTREAM`                 | `/api/workflows*`                                                            |
| `AIXCORE_UPSTREAM`                 | `/aixcore/*`                                                                 |
| `BCS_ENDPOINT_PRE`                 | Bot registration command endpoint for pre-production                         |
| `BCS_ENDPOINT_PROD`                | Bot registration command endpoint for production                             |

Values must be absolute upstream origins understood by Nginx, for example an internal service-discovery URL. Repository examples deliberately do not provide real domains or credentials.

The current frontend development proxy proves that space, work-order and work-order-notification paths use the separate admin upstream. The Nginx template therefore places those three specific locations before the broad `/openapi/` Gateway location.

For local Task Engine diagnosis only, the development build accepts
`TEAMCLAW_TASK_API_BASE=/api/v1/collaboration/tasks`. Pair it with
`TASK_ENGINE_UPSTREAM=<task-engine-origin>` to send the complete task path directly to the
Task Engine. Without this override, the public default remains
`/openapi/v1/collaboration/tasks`. This is a development proxy option and does not add a
production Nginx route for the internal `/api/v1` surface.

## Build

Deployment overlays can disable the OpenClaw onboarding tab by overriding
`getOpenClawRegistrationEnabled` with `{ status: 'available', value: false }`.
It defaults to enabled. This controls external-bot onboarding only; existing bots
and bot-engine creation options are unaffected. Re-enable the capability to restore
the tab and its installer instructions.
If no onboarding methods are enabled, the dialog displays an explicit unavailable
state instead of an empty type selector or installer.

`DSH_REGISTRATION_GUIDE_URL` optionally supplies the DeepSeek Harness guide through
runtime configuration. Set it to an HTTP(S) guide for the plugin version deployed
with this release, using an immutable revision when hosting documentation in Git.
When absent, the optional guide link is hidden; all inline onboarding steps remain
available. Internal overlays can override the guide capability independently.

```bash
docker build -t <ACR_REGISTRY>/<NAMESPACE>/avernet-frontend-nextgen:<VERSION> .
docker push <ACR_REGISTRY>/<NAMESPACE>/avernet-frontend-nextgen:<VERSION>
```

`<...>` values are operator inputs, not literal defaults.

## ECS deployment

1. Install a container runtime on the ECS instance.
2. Authenticate to the selected Alibaba Cloud Container Registry instance.
3. Pull the immutable image tag or digest.
4. Provide all required upstream variables through a protected environment file outside the repository.
5. Bind container port `8080` only to the load balancer or host reverse proxy.
6. Terminate TLS at ALB/Nginx and expose one public origin for `/`, `/auth/*`, and API paths.
7. Configure BCS `auth.oauth.base_url` to that exact HTTPS public origin so `/auth/callback/alipay` matches the registered callback.
8. Validate `/healthz`, `/auth/url`, `/auth/user`, one OpenAPI read, and a WebSocket connection before adding traffic.

## ACK deployment

1. Push the immutable image to ACR.
2. Create a Deployment with the seven required variables sourced from ConfigMaps or Secrets as appropriate.
3. Add readiness and liveness probes on `GET /healthz` port `8080`.
4. Add a ClusterIP Service targeting port `8080`.
5. Configure ALB Ingress for a single HTTPS host and preserve `/auth/*` callback paths.
6. Keep OAuth private keys and `jwt_secret` in the BCS workload Secret; they are not frontend variables.
7. Roll out with a new immutable tag/digest and keep the prior ReplicaSet for rollback.

## OAuth behavior

- The browser receives an HttpOnly `bcs_session` cookie from BCS.
- JavaScript must not read or persist the JWT.
- Same-origin routing avoids cross-site Cookie and CORS ambiguity.
- Current compatibility routes are `/auth/url`, `/auth/user`, `/auth/refresh`, and `/auth/logout`.
- When Gateway exposes OAuth through OpenAPI, update the centralized auth endpoint configuration and Nginx routing only after the real contract is available.

## Production information still required

- Public domain and TLS certificate ownership.
- ACR instance, namespace, image retention and vulnerability policy.
- ECS or ACK selection and resource sizing.
- Concrete upstream service-discovery origins, including the task engine origin for `TASK_ENGINE_UPSTREAM` (it may equal the Gateway when the Gateway owns the task routes).
- Confirmation of whether admin space/work-order routes remain separate after the future Gateway consolidation.
- Alipay App ID, callback registration, RSA keys and BCS JWT secret delivery.
- Logging, metrics, alerting, rollout and rollback policy.

## Independent Evolution / RSI (opt-in)

`RSI_ENABLED` defaults to `false`. Set exactly `true` to expose Bot chat `+ → RSI任务`.
The flag controls visibility only; it adds no authentication or user identity.
Both Nginx and the development proxy keep RSI on the existing `/openapi` Gateway
route. There is no direct browser-to-Evolution proxy or `EVOLUTION_UPSTREAM`
setting. Existing login redirects, same-origin credentials and the shared
Backend request client remain the authority for system identity.

Gateway authenticates, Backend applies its existing principal/Bot gates, then
forwards only the nine explicit control operations to the independently deployed
Evolution service with a short-lived internal authorization receipt. Browser
cookies and Authorization headers are not forwarded to Evolution. Backend
configuration and service-to-service trust are deployment prerequisites; see
[Backend control ingress](../../backend/docs/flows/bot-evolution-control.md).
The frontend does not configure IAM tokens, staff IDs, names or a second login.

Internal Worker routes must not be exposed through the browser origin. Their
legacy query fence credentials require log redaction on all internal proxies/APM;
restrict them to the execution network. Never expose the local fixture profile
through a public frontend.

The UI configures full Binding JSON, saves with ETag, starts idempotently, polls
Job/Worker/evidence/candidates and cancels a Run. Scope is Bot+owning entity, not
chat-session. Shared system permissions still distinguish Space chat/read from
explicit Bot operations permission; no RSI-specific role is added. `completed`
does not mean evaluated or promoted.

The production Platform/service delegation and a real Agent Swarm child-operation
executor still require environment validation. See [Evolution service](../../evolution/README.md)
and [migration report](../../../docs/specs/2026-11-17-evolution-service-migration/report.md)
for actual validation. A visibility switch is not real environment acceptance.

### Entry location and unavailable-service diagnosis

The RSI entry belongs to the selected, owned Bot's chat workspace (`/workspace/chat`,
composer `+` menu), not its workshop edit page. The runtime configuration script
must load before the application entry; after replacing a static deployment,
reload the page and verify both the deployed image and `/runtime-config.js`.
A visible menu proves neither platform authorization nor executor readiness.

On HTTP503 the panel reports that the independent service is disconnected or
unavailable without exposing arbitrary upstream error messages. Configuration
must load successfully before polling, editing, or submitting. A submission with
an unknown outcome retains its idempotency key: reconcile server state instead
of opening another tab or generating a new key to submit again.

A deployment overlay may deliberately expose the menu for connection diagnostics
while returning explicit503 for RSI APIs. Such an overlay must not fall back to
Backend's old Evolution authority or expose internal Worker APIs through the
browser origin. `RSI_ENABLED=true` does not require a separate frontend upstream;
an unconfigured Backend returns503. Configure the Backend, not a frontend login bypass.

Real01–06 acceptance additionally needs a trusted Platform adapter and deployed
isolated workers. Missing07–09 evaluation is not the blocker for that acceptance.
