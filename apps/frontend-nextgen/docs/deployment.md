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

## Collab form（协作群极简站）

Build: `docker build --build-arg FRONTEND_FORM=collab -f apps/frontend-nextgen/Dockerfile .` (from the repository root, as the Dockerfile's build-context comment requires). Local equivalents: `npm run build:collab` / `npm run dev:collab`.

This builds the collab form on the complete public source. The independent
internal site uses the OCB nextgen `export:collab` output and its dedicated
Dockerfile; the contract is specified in
`specs/2026-11-03-collab-form/export-plan.md`. The public source — and the
default Open product Singlebox serves — are not pruned by the export, and the
legacy `apps/frontend` is not involved.

The image shape matches the default form (same nginx template); only the page
assembly differs: 协作群 (collaboration groups), 发现 (public Bots/groups only)
and 外部 Bot (external bots) as the three top-level entries. The discovery
assembly disables Backend enhancement and Human single-chat actions even if
`PUBLIC_BOT_BACKEND_ENABLED` is enabled in runtime configuration. The required
upstream environment variables are unchanged. After the collab form passes runtime network acceptance, the
following upstreams may point at valid, resolvable local placeholders (e.g.
`http://127.0.0.1:9`): `TASK_ENGINE_UPSTREAM`, `CLAWWEB_UPSTREAM`,
`PRIVATE_CHAT_*_UPSTREAM`, `AIXCORE_UPSTREAM`, `TEAMCLAW_ADMIN_UPSTREAM`
(collab disables space initialization through the capability layer and removes
execute-based group creation through null slots; this is not a request-failure
degradation). Do not use arbitrary unresolvable hostnames as placeholders —
nginx may fail to start.

### Collab-form validation boundaries (recorded 2026-11-04, export-plan Task 20)

Which boundary is verified where, and who owns the remainder. "This repo" is
Avernet; the internal export deliverable lives in OCB's
`apps/frontend-nextgen`.

| Boundary | Status / Evidence |
| --- | --- |
| Full public CI (Open form) | **VERIFIED in this repo** — `.github/workflows/frontend-nextgen.yml` gate set re-run at `53d9ea5f57`: `check:open-core`, `npm run ci` (typecheck, lint, jest incl. `check:collab-closure`, Open build), `build:collab`, a2a package job — all green; run record and numbers in `specs/2026-11-03-collab-form/spec.md` (Status). |
| Full public Singlebox startup + smoke | **VERIFIED live** — `FRONTEND_VARIANT=nextgen FRONTEND_FORM=open singlebox.sh start all` rc=0; real login-cookie flow, group create/list over BCS, external-bot listing, open-only routes answered (`/bot-workshop`, BBS task list). Details: root `docs/singlebox-nextgen-local.md` (2026-11-04 record). |
| Legacy independence | **VERIFIED** in a disposable checkout without `apps/frontend` — nextgen startup green there, legacy start fails loudly, both singlebox verity suites pass. |
| Export fixtures (OCB `test:collab-export`) | **VERIFIED on this machine** — 28/28 graph/emit fixtures. |
| Isolated install/typecheck/test/build in the exported tree | **VERIFIED via OCB `scripts/ci-collab-export.sh`** (export + output-tree-only `npm ci`/`typecheck`/`test`/`build` + failure-atomicity probes); asserted by the same gate on private CI. |
| Docker image build/startup from the output root | **UNVERIFIED locally — no Docker CLI on the dev machine.** Owner: private deployment environment; exact steps in OCB `apps/frontend-nextgen/deploy/collab/README.md` (`docker build -f apps/frontend-nextgen/deploy/collab/Dockerfile .`). |
| Image runtime smoke: health, deep-link fallback, private auth, retained collaboration/external-Bot flows, zero task/admin requests | **UNVERIFIED — requires the private stack.** Owner: private deployment environment against configured upstreams; checklist in OCB `deploy/collab/README.md`. Until it passes, the upstream-placeholder guidance above stays conditional. |
| Collab-form browser smoke (dev server or image: exact three-entry sidebar, no search/bell, dialogs, WS push, criterion-8 request trace) | **UNVERIFIED — no browser automation in this run.** Owned by the same private deployment acceptance. |

The exported output is an internal deliverable; none of the internal evidence
above is produced by or published from this repository.


### Discovery export update (2026-11-11)

The public collab routes/closure now include public Bots and public groups; the
expert and task routes remain excluded. Private overlays must inherit the public
collab capabilities without re-enabling `getPublicBotBackendEnabled` or
`getDiscoveryExpertsEnabled`, and artifact tests must expect three sidebar entries
and two discovery tabs. Re-run the isolated export/typecheck/build gate and target
stack browser smoke after updating the Avernet revision. Historical export
validation above does not validate this expanded discovery artifact.
