# `agentclaw.community`

The public Backend namespace. Start with the [Backend development guide](../../../README.md)
for architecture authority, dependencies, development commands and integration boundaries.
This package is the shared implementation; an enterprise host adds `agentclaw.corp`
through the PEP 420 `agentclaw` namespace and its own composition root.

| Layer | Read when changing |
| --- | --- |
| [api](api/README.md) | Service API Protocol ownership and exports |
| [core](core/README.md) | domain policy, persistence and cross-domain dependencies |
| [plugin_api](plugin_api/README.md) | outbound capability contracts and compatibility |
| [plugins](plugins/README.md) | profile-selected capability implementations |
| `adapters/http/` | HTTP/WS routes, schemas and delivery translation |
| `di/` | bindings, profile selection and bootstrap |
| `kernel/` | cross-layer pure contracts/data |
| `configs/` | base and profile configuration overlays |

Boot and worker assembly are in `main.py`, `adapters/http/app.py`,
`adapters/http/boot.py` and `di/container.py`. Read `di/profile.py` before changing
profile behavior. Domain code receives dependencies rather than selecting a
runtime profile or concrete infrastructure implementation.
