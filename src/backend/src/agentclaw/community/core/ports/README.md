# `core/ports` — outbound ports

## What lives here

A **port** in this package is an *outbound* boundary: the narrow contract a
piece of core states for something it calls **out** to, named by what the
caller needs rather than by what any implementation happens to offer. It is
the consumer's vocabulary, published where both sides can reach it.

That is the distinction against the two neighbouring things it is easy to
confuse it with:

| | Owned by | Shaped by | Example |
|---|---|---|---|
| **Service API Protocol** (`community/api`, `core/<domain>/*_protocol.py`) | the provider | everything the service offers | `DirectActivationServiceProtocol` — six methods **and** `project` |
| **Outbound port** (here) | the consumer | only what the consumer calls | `ActivationPort` — the same six methods, **no** `project` |
| **Repository protocol** (`core/repository`) | the persistence layer | one aggregate's storage | `SkillRepository` |

A port is narrower than the service Protocol on purpose. `ActivationPort`
omits `project` because choosing whether a write projects to a running
container belongs to the manifest delivery strategy, not to the materialisers
that call through the port — so the type they hold must not be able to express
the choice. Narrowing is the design, not an oversight.

## Why a package of its own

A port names a relationship between two modules and belongs to neither. Kept
inside the consumer, it is unreachable by the provider — `core.skill_center`
must not import `core.bot_config_manifest`, so a port living under the latter
can never be declared by a class in the former. Kept inside the provider, it
stops being consumer-owned and starts drifting toward the service's full
surface, which is the thing it exists not to be.

Here, both sides can import it and neither owns it.

## Rules

- **Implementers declare the port in their bases.** `src/backend` runs no
  static type checker, so a structurally-satisfied Protocol is verified by
  nothing — not at import, not at construction, not in CI, and the pairing is
  then discoverable only by walking the DI graph. State it in the class.
  `tests/community/architecture/test_narrow_ports_are_declared.py` enforces it.
- **Members are `@abstractmethod`.** This is what makes the declaration
  load-bearing: a plain `...` stub is inherited in place of a dropped method,
  so the name still resolves and the call silently returns `None`.
- **A port imports nothing from a domain package.** It is reachable from both
  sides only while it stays free of them.

## Contents

- `activation_port.py` — `ActivationPort`: per-bot skill and MCP activation
  writes, without the projection choice.
- `skill_package_upload_port.py` — `SkillPackageUploadPort`: installing one
  local skill package, without the directory-upload route's method.

Remaining ports still live beside their consumers and move here as they are
revisited: `bot_config_manifest/apply/` (`identity_port`, `resource_port`) and
`bot_config_manifest/cli_tools/` (`arca_port`, `delivery_port`).

## Implementations are named for where the write lands

Each port has exactly two, and they split on the one axis every delivery family
splits on — whether the write reaches the bot's **device** or stays in
**platform** state that the composed artifact delivers:

| Port | device (ARCA) | platform (platform-managed teclaw) |
|---|---|---|
| `ActivationPort` | `DeviceActivation` | `PlatformActivation` |
| `SkillPackageUploadPort` | `DeviceSkillPackageUpload` | `PlatformSkillPackageUpload` |

The pairs are not the same shape underneath, and that is expected. The
activation two are wrappers over one `DirectActivationService`, differing only
in the `project` they forward. The upload two share no body at all: one writes
package files onto a container, the other objects into the managed-files store.
Same axis, different depth — name for the axis, because that is what a reader
needs to predict which one a family gets.

## Context Boundary

```yaml
purpose: Outbound ports — the narrow contracts core states for what it calls out to, owned by the caller and published where both caller and implementer can reach them.
provides:
  - ActivationPort
  - SkillPackageUploadPort
consumes: []
consumed_by:
  - "core/bot_config_manifest (apply) — the `mcp` and `skills` materialisers write through these ports; the four implementations are apply/activation_delegates.py, apply/skill_package_upload.py and managed_files/ports.py"
internal_dependencies: []
```
