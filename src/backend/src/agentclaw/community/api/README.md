# `agentclaw.community.api`

**Service API Protocols.** Post-R8 this layer is delivery-adapter
agnostic: a flat collection of `@runtime_checkable Protocol` classes,
one per public service, that any consumer (HTTP, CLI, in-process
embedder) can call against. FastAPI routers live next door under
[`adapters/http/`](../adapters/http/README.md); they Inject a Protocol
from this layer rather than the concrete service class in `core/`.

Layout:

```
api/
├── README.md
├── __init__.py
├── <service>_service.py   # one Protocol per file, no imports from fastapi
├── <factory>_factory.py   # factory Protocols (SkillServiceFactoryProtocol, …)
└── …
```

Conformance is **structural**: concrete services under
`core/<module>/services/` do *not* inherit from the Protocol (that
would force a `core → api` import, which the layering rule forbids).
Instead `tests/architecture/test_service_api_conformance.py` parametrizes
over every `(Protocol, ConcreteService)` pair and asserts
`issubclass(ConcreteService, Protocol)` against the `@runtime_checkable`
Protocol — so a missing or renamed method on the concrete class fails
CI rather than only showing up as a router-time `AttributeError`.

Two enforcement gates live under `tests/architecture/`:

- `test_api_layer_is_protocols_only.py` — every file under `api/`
  defines a Protocol, no subdirectories, no router code.
- `test_http_adapter_layer_is_http_only.py` — every router under
  `adapters/http/` Injects an `<X>Protocol` from `api/`, never a
  concrete service class from `core/<m>/services/`.

DI wires Protocol → concrete via a per-module `@singleton @provider
@inject` alias in `di/modules/<m>_module.py`, so both
`Injected(Protocol)` and `Injected(Concrete)` resolve to the same
singleton.

## Context Boundary

```yaml
purpose: "Service API Protocols — transport-agnostic contracts between adapters and core services."
provides:
  - "One Protocol per public service / factory"
  - "Structural conformance gate via tests/architecture/test_service_api_conformance.py"
consumes:
  - "No service impls at import time — Protocols only declare shape, they don't depend on concrete services"
  - "A small number of core dataclass / schema types used to type Protocol method signatures (see internal_dependencies)"
internal_dependencies:
  - agentclaw.community.core.access.repository       # UserInfoRecord — typed in user_service.py
  - agentclaw.community.core.bot_chat.schemas        # ConversationDetail, HealthCheckData — typed in bot_chat_service.py
  - agentclaw.community.core.channel.services.repositories  # ChannelRecord — typed in channel_service.py
  - agentclaw.community.core.quality.repositories    # QualityTaskRecord — typed in quality_service.py and task_processor_service.py
  - agentclaw.community.core.service_bot.services.baas_service  # BotWsConnectionInfoResponse / HttpConnectionInfo — typed in baas_service.py (BaasService is a plain core service)
  - agentclaw.community.core.service_bot.types       # PublishStage enum — typed in baas_service.py
  - agentclaw.community.kernel.device_dto            # OutBoundOperationRule — typed in baas_service.py Protocol (B6)
```

### Change impact

Adding a Protocol method is a contract change. Every concrete service
that backs that Protocol must implement it; the conformance test will
fail otherwise. Removing or renaming a method is a breaking change for
every consumer (adapter / CLI / RPC) — coordinate with downstream.
Field shape in adapter-owned types (e.g. `AuthenticatedUser`) lives
under `adapters/http/`, not here.
