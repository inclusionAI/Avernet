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

## Lifecycle and failure behavior

The application lifespan awaits `startup_provider.initialize()` before serving
requests and awaits `startup_provider.shutdown()` when the application stops.
Initialization failures propagate and prevent startup. Business-route mounting
failures also propagate instead of producing a partially usable service.

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
