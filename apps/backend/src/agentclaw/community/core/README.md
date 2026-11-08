# `agentclaw.community.core`

Backend domain policy and application services. Read the
[Backend development guide](../../../../README.md) for architecture authority,
DI and verification. This is the current domain layout; old `core/routers/`
and flat `core/services/` migration checklists are not instructions for new code.

## Responsibilities and dependencies

Domains own business rules and their Service API Protocols. Most are exposed
through re-exports in [api](../api/README.md); core does not import those exports
back. HTTP/WS routers and request/response schemas live in `adapters/http/`,
not in core. Services receive outbound capability Protocols from `plugin_api/`
and dependencies via constructor injection. New bindings live in `di/modules/`.
Existing `dependencies/`, legacy buckets and named architecture exceptions are
bounded migration debt; do not infer general permission to import a concrete
plugin, web framework or composition root into a new service.

[Repository contracts and ORM implementations](repository/README.md) are grouped
by consumer domain in `core/repository/`; they are not profile plugins.
[Outbound ports](ports/README.md) document consumer-owned narrow seams.

## Domain navigation

Read the affected domain's **Context Boundary** and **Change impact** before
changing it, then follow its declared consumers and dependencies. These are
common starting points, not an exhaustive ownership list:

| Area | Domain entrypoints |
| --- | --- |
| Identity and permissions | [access](access/README.md), [auth](auth/README.md), [caller identity](caller_identity/README.md), [application grants](bot_app_grant/README.md) |
| Bot lifecycle and execution | [bot management](bot_management/README.md), [engine runtime](engine_runtime/README.md), [runtime binding](runtime_binding/README.md), [devices](devices/README.md) |
| Publication | [service bot](service_bot/README.md), [task queue](task_queue/README.md) |
| Skills and configuration | [skill center](skill_center/README.md), [skills pool](skills_pool/README.md), [manifest](bot_config_manifest/README.md), [configuration surface](bot_config_surface/README.md) |
| Conversations and collaboration | [bot chat](bot_chat/README.md), [group chat](group_chat/README.md), [expert chat](expert_chat/README.md), [agentswarm](agentswarm/README.md) |
| Spaces and shared content | [spaces](spaces/README.md), [resources](resources/README.md), [content store](content_store/README.md), [workspace](workspace/README.md) |
| Platform state | [config](config/README.md), [system config](system_config/README.md), [events](events/README.md), [repositories](repository/README.md) |

## Context Boundary maintenance

Use [service_bot](service_bot/README.md) as a real example of `purpose`,
`provides`, `consumes`, `internal_dependencies` and prose `Change impact`.
Copy the format, not its long dependency list. The
[format guide](../../../../../../docs/arch/context-boundary-format.md) defines
which fields are enforced: `internal_dependencies` permits imports made by
this module; `consumes` records intent. Neither waives the layer rules.

For a new significant domain, register the package in
`tests/community/architecture/test_module_boundaries.py` (relative to Backend).
For cross-domain changes, review both sides' contracts, callers, DI bindings and
boundary declarations. Changes to outbound Protocols also need consumer-level
contract tests; changes to Service APIs need their conformance tests. When the
enterprise host participates, check the matching host ref/gitlink and corp
bindings rather than assuming a public test run covers them.

Historical migration proposals under `specs/` explain earlier moves; their old
paths and unchecked TODOs do not supersede this index or current source.
