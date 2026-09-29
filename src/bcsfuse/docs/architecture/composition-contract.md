# BCSFuse Application Composition Contract

BCSFuse has one public source owner for shared business behavior: Avernet. A
deployment assembles environment-specific providers outside that shared code
and passes the completed registry into the public application factory.

## Public composition API

The public boundary consists of these Python APIs:

```python
ApplicationContext(
    *,
    mode: str,
    startup_profile: str,
    registry: ProviderRegistry,
)

create_bcsfuse_app(context: ApplicationContext) -> FastAPI
```

`ApplicationContext` is an explicit, already-assembled dependency container.
The factory does not select a deployment, import an internal implementation,
or read internal configuration in order to complete the provider graph.

`create_opensource_app(mode)` remains the compatibility entry point for the
public deployment. It builds the public provider registry and delegates to
`create_bcsfuse_app`.

## Required providers

Before creating the FastAPI application, the factory requires these registry
keys:

| Key | Contract |
| --- | --- |
| `config` | Application configuration provider |
| `secret_provider` | Secret lookup provider |
| `startup_provider` | Async application lifecycle provider |

The public provider builder also registers `context_provider` for request and
runtime context integration. Deployments may replace public implementations,
but must preserve the same provider keys and contracts.

`ProviderRegistry.require(name)` distinguishes a missing key from a registered
falsey value. Missing required providers fail during application construction;
the factory does not silently substitute another implementation.

## Worker and Profile provider contracts

Worker/Profile business behavior is owned by Avernet application services. A
deployment may replace persistence, but its providers must implement the typed,
transport-agnostic Plugin APIs in:

- `src.application.ports.worker_registry_store.WorkerRegistryStore`
- `src.application.ports.worker_profile_content_store.WorkerProfileContentStore`
- `src.domain.services.adapters.worker_runtime_state_store_adapter.WorkerRuntimeStateStoreAdapter`
- `src.domain.services.adapters.worker_profile_binding_store_adapter.WorkerProfileBindingStoreAdapter`
- `src.domain.services.adapters.worker_audit_log_adapter.WorkerAuditLogAdapter`

The registry and profile ports exchange `Worker`, `WorkerConfig`, and
`WorkerProfileContent` domain models; internal providers must not substitute
deployment-specific dictionaries. Public MySQL, SQLite, and in-memory
implementations are tested against the same contracts. Storage write and
cleanup failures propagate to the application service and must not be reported
as success.

During the route migration, the application factory wraps a typed-only Profile
provider with a public delivery compatibility adapter. Providers that already
offer the legacy route methods keep their original registry identity; internal
providers only need to implement the typed Plugin API. Durable Worker/Profile
records are deleted together by the registry provider. Vector data is treated
as rebuildable derived state, so vector cleanup occurs before that atomic
durable delete without deleting Profile records separately. Profile vector
cleanup covers both the legacy exact ID (`worker:profile`) and every indexed
fragment below its prefix (`worker:profile:*`).

The composed application exposes the canonical Worker/Profile lifecycle under
`/v1`: worker creation and lookup, online/offline transitions, profile upsert,
activation and lookup, and worker deletion. Missing workers use HTTP 404 with
`WORKER_NOT_FOUND`; duplicate creation uses HTTP 409 with
`WORKER_ALREADY_EXISTS`; a second delete is the same explicit 404 result. The
legacy `/api/v1/workers/{worker_id}/online|offline` aliases preserve those
status and error-code semantics alongside the canonical routes.

The existing direct `/v1` and `/api/v1` service paths used by Backend and
BCS/BCN are long-lived compatibility contracts. Gateway routes are additive
aliases and do not replace those direct paths. OCB's externally observable
BCSFuse behavior is the internal compatibility baseline; deployment-only
administration and diagnostics may remain conditionally mounted by the
internal composition root rather than becoming public OSS routes.

## Lifecycle and failure behavior

The application lifespan awaits `startup_provider.initialize()` before serving
requests and awaits `startup_provider.shutdown()` when the application stops.
Initialization failures propagate and prevent startup. Business-route mounting
failures also propagate instead of producing a partially usable service.

Background indexing starts only after startup initialization succeeds. During
shutdown, the application waits for an active index build before shutting down
providers so that background work cannot race provider teardown.

Worker registration is a compensating operation across registry, runtime,
binding, audit, and index providers. If a post-create side effect fails, the
service removes any partially written index state and deletes the durable
worker aggregate before returning the error. Retrying the same registration
must not fail with `WORKER_ALREADY_EXISTS` because of the failed attempt.

Protected public routes use `AuthProvider.validate_request(request)` from the
published provider contract. Replacement authentication providers must
implement the complete contract rather than relying on methods available only
on the public default implementation.

The readiness endpoint reports the provider mode and readiness status. When
registry inspection fails, it returns HTTP 503 with the exception type only;
exception messages and provider values are not returned to clients.

## Ownership boundary

Avernet owns the application factory, public provider contracts, public
implementations, shared business routes, and compatibility tests. Internal
repositories own their concrete providers, internal configuration, composition
root, container image, and delivery pipeline. Internal code may depend on this
public composition API; public code must not import internal packages, services,
domains, credentials, or runtime state.
