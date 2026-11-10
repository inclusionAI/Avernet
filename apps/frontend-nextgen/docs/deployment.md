# Frontend Nextgen deployment

## Scope

This directory supplies a portable OCI image and same-origin Nginx routing. It does not create Alibaba Cloud resources, issue certificates, register domains, or contain OAuth/private keys.

The public `@tc-chat/ui@2.0.0` package and its `@ant-design/x` dependency require `antd:^6.1.1`. `antd@6.6.1` is pinned as an approved SDK-only direct exception: do not import it from application code, and re-audit/remove it when upgrading tc-chat.

## Deployment profiles

`FRONTEND_API_PROFILE` selects the API surface this container serves. The
validation script runs as `10-teamclaw-api-profile.sh`, i.e. before the official
Nginx `20-envsubst-on-templates.sh`, so every template swaps in before the
standard `${...}` substitution pass.

### `full` (default)

Keeps the existing multi-service topology. All ten variables in the table
below are required, the image default `default.conf.template` stays in place,
and the generated `runtime-config.js` sets the optional Public Bot backend
integration flag `PUBLIC_BOT_BACKEND_ENABLED: true` (JS boolean). Operators may
override it with `PUBLIC_BOT_BACKEND_ENABLED=false` (or `true`); any other value
fails startup with `PUBLIC_BOT_BACKEND_ENABLED must be true or false`.

### `bcs-only`

Serves standalone BCS friend discovery (public Bot list, name search, smart
search, requests, approvals, same-origin group chat) without Backend, Gateway,
Admin or Task Engine:

- Only `BCS_UPSTREAM` is required (non-empty). It must be an `http://` or
  `https://` origin: trailing slashes are stripped and a path part (for example
  `http://bcs:21000/api`) is rejected, because a prefix-location `proxy_pass`
  carrying a URI rewrites the matched path and a regex-location one makes Nginx
  refuse to start.
- `BCS_ENDPOINT_PRE` / `BCS_ENDPOINT_PROD` are optional. When unset they are
  emitted as empty strings, and the frontend then falls back to the browser
  same origin (`window.location.origin`) for the Bot registration command, never
  to the compile-time `http://127.0.0.1:21000` placeholder. The compile-time
  default is only used when the runtime keys are absent (local `npm run dev`).
- The entrypoint copies `/etc/nginx/template-profiles/default.bcs-only.conf.template`
  (a read-only profile config directory, deliberately not the envsubst
  templates directory) into `/etc/nginx/templates/default.conf.template`. It
  also writes the normalized `BCS_UPSTREAM` into that template, because the
  official Nginx `20-envsubst-on-templates.sh` reads the original environment
  from its own process and would otherwise see the unnormalized value.
- Same-origin routes: `/auth/*` (OAuth redirects and the
  `/auth/callback/<provider>` browser callback), `/openapi/v1/auth/*`, and the
  complete collaboration subtree `/openapi/v1/collaboration/*`
  (`bots/mine`, `bots/{actor}/candidates`, `friend-connections/**`,
  `tasks/*`, plus the group-chat WebSockets `/openapi/v1/collaboration/messages/ws`
  and `/openapi/v1/collaboration/group/ws`) proxy to `BCS_UPSTREAM`. BCS is the
  only business API in this profile, so the whole subtree is forwarded; the
  collaboration location sets `proxy_http_version 1.1` and the Upgrade /
  Connection headers (the same style as the full template) so WebSocket
  handshakes succeed. When `$http_upgrade` is empty Nginx omits the Upgrade
  header, so plain HTTP requests are unaffected.
- The internal smart search
  `^/api/v1/collaboration/bots/<actor>/candidates/search` is the only
  `/api/` route and proxies to `BCS_UPSTREAM`; it is a regex location, so it
  wins over the `/api/` prefix regardless of file order.
- Unmatched `/api/` and `/openapi/` locations return an explicit 404. They must
  never fall through to the SPA `index.html`, which would masquerade as a
  working endpoint. Routes owned by Admin or the Gateway in the full profile
  (`/openapi/v1/bots`, `/openapi/v1/bots/spaces`, ...) are therefore not
  forwarded to BCS.
- Everything else stays static SPA serving with `try_files ... /index.html` for
  discovery deep links, plus `GET /healthz`.
- `runtime-config.js` sets `PUBLIC_BOT_BACKEND_ENABLED: false` (JS boolean,
  never the string `'false'`), which disables optional Backend metadata
  enrichment, personal-space initialization and notification polling
  (see `src/capabilities/defaultCapabilities.ts`). This profile ignores the
  `PUBLIC_BOT_BACKEND_ENABLED` override: it stays `false`.

An unknown profile value fails fast with `Unknown FRONTEND_API_PROFILE` before
any upstream-variable validation.

The dev server mirrors the same profile: `PRESET=<preset>
FRONTEND_API_PROFILE=bcs-only BCS_UPSTREAM=<bcs-origin> npm run dev`. It compiles
`TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED=false` and sends the whole
`/openapi/v1/collaboration` subtree to `BCS_UPSTREAM` with `ws: true`, matching
the production profile. The internal candidate search route is inserted before
the generic `/api` wildcard key because the umi proxy matches keys in insertion
order. No other preset or profile behavior changes.

## Required upstreams

With the default `full` profile the container refuses to start until all
variables are provided:

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
4. Provide all required upstream variables through a protected environment file outside the repository (`BCS_UPSTREAM` only for `FRONTEND_API_PROFILE=bcs-only`).
5. Bind container port `8080` only to the load balancer or host reverse proxy.
6. Terminate TLS at ALB/Nginx and expose one public origin for `/`, `/auth/*`, and API paths.
7. Configure BCS `auth.oauth.base_url` to that exact HTTPS public origin so `/auth/callback/alipay` matches the registered callback.
8. Validate `/healthz`, `/auth/url`, `/auth/user`, one OpenAPI read, and a WebSocket connection before adding traffic.

## ACK deployment

1. Push the immutable image to ACR.
2. Create a Deployment with the required variables sourced from ConfigMaps or Secrets as appropriate (`BCS_UPSTREAM` only for `FRONTEND_API_PROFILE=bcs-only`).
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
