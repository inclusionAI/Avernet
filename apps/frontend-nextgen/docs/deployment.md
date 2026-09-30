# Frontend Nextgen deployment

## Scope

This directory supplies a portable OCI image and same-origin Nginx routing. It does not create Alibaba Cloud resources, issue certificates, register domains, or contain OAuth/private keys.

The public `@tc-chat/ui@2.0.0` package and its `@ant-design/x` dependency require `antd:^6.1.1`. `antd@6.6.1` is pinned as an approved SDK-only direct exception: do not import it from application code, and re-audit/remove it when upgrading tc-chat.

## Required upstreams

The container refuses to start until all variables are provided:

| Variable                           | Existing frontend route responsibility                                       |
| ---------------------------------- | ---------------------------------------------------------------------------- |
| `BCS_AUTH_UPSTREAM`                | BCS `/auth/*` OAuth routes                                                   |
| `TASK_ENGINE_UPSTREAM`             | `/openapi/v1/collaboration/tasks/*` task execute/dashboard/list/grant/revoke |
| `TEAMCLAW_GATEWAY_UPSTREAM`        | `/openapi/*` and non-task `/api/v1/collaboration/*`                          |
| `TEAMCLAW_ADMIN_UPSTREAM`          | Reserved for the separately deployed admin service; see note below           |
| `PRIVATE_CHAT_MANAGEMENT_UPSTREAM` | `/api/*` private-chat management                                             |
| `PRIVATE_CHAT_SESSION_UPSTREAM`    | `/proxypass/*` and WebSocket                                                 |
| `CLAWWEB_UPSTREAM`                 | `/api/workflows*`                                                            |
| `AIXCORE_UPSTREAM`                 | `/aixcore/*`                                                                 |
| `BCS_ENDPOINT_PRE`                 | Bot registration command endpoint for pre-production                         |
| `BCS_ENDPOINT_PROD`                | Bot registration command endpoint for production                             |

Values must be absolute upstream origins understood by Nginx, for example an internal service-discovery URL. Repository examples deliberately do not provide real domains or credentials.

The current frontend development proxy proves that space, work-order and work-order-notification paths use the separate admin upstream. The Nginx template therefore places those three specific locations before the broad `/openapi/` Gateway location.

## Build

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
