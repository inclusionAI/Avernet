# Backend development guide

### App knowledge framework

- [App knowledge standard](docs/agents/knowledge-standard.md) and [templates](docs/agents/templates/README.md).
- [App architecture](docs/arch/README.md) and [app decisions](docs/adr/README.md).
- [Existing knowledge catalog](docs/agents/existing-knowledge.md): existing docs, domain context and historical designs.
- [Flows](docs/flows/README.md), [diagnostics](docs/diagnostics/README.md), and [change specs](specs/README.md).


The public `agentclaw.community` backend provides bot lifecycle, sessions, chat,
and workspace management. It builds and tests without `agentclaw.corp`.
This guide applies to the `apps/backend/` layout, checked on 2026-10-26.
Use the files in the target checkout; old `src/backend/` migration paths are historical.

## Responsibilities and interfaces

Backend owns product-facing Bot, access, configuration and conversation
orchestration. Provider lifecycle and runtime execution remain behind their
contracts; a successful metadata write does not imply runtime readiness.

| Boundary | Entry point |
| --- | --- |
| HTTP/WS delivery and DTOs | `src/agentclaw/community/adapters/http/` |
| Inbound business contracts | [Service API](src/agentclaw/community/api/README.md) |
| Outbound capabilities | [Plugin API](src/agentclaw/community/plugin_api/README.md) |
| Business domains and state ownership | [Core index](src/agentclaw/community/core/README.md), then the owning domain |

### Authority and reading order

| Concern | Source |
| --- | --- |
| Structural invariants | [Architecture constitution](../../docs/arch/arch.rules.md) |
| Required enforcement | [CI constraints](../../docs/arch/ci.enforce.md) and current `tests/community/architecture/` gates |
| Service contracts | [Service API](src/agentclaw/community/api/README.md), owning core Protocols and schema/lifecycle definitions |
| Outbound capability contracts | [Plugin API](src/agentclaw/community/plugin_api/README.md) |
| Domain responsibilities | [Core index](src/agentclaw/community/core/README.md), then the affected domain's Context Boundary |
| Boundary and contract-test format | [Context Boundary](../../docs/arch/context-boundary-format.md), [Protocol tests](../../docs/arch/protocol-contract-tests.md) |

Formal contracts govern interface meaning; the constitution governs structure.
This guide supplies current navigation and implementation conventions. Domain
READMEs refine their own scope and cannot relax structural rules. Tests and
configuration establish what this checkout enforces, not permission to redefine a
contract. A named exception in a gate applies only to its named subject. If
implementation and contract disagree, report and resolve the mismatch rather
than treating an old migration proposal or a passing test as a new contract.

## Layout and dependency direction

```text
apps/backend/
├── src/agentclaw/community/
│   ├── api/            Service API Protocol definitions/re-exports; no HTTP routers
│   ├── adapters/http/  HTTP/WS delivery, schemas, auth and composition entrypoints
│   ├── core/           domain policy, services and persistence contracts/bodies
│   ├── plugin_api/     outbound capability Protocols
│   ├── plugins/        local/community capability implementations
│   ├── kernel/         low-level cross-layer contracts and data
│   ├── di/             configuration-driven assembly and bindings
│   ├── configs/        base and profile YAML overlays
│   └── main.py         public entrypoint
├── tests/community/    public unit, contract, architecture and integration suites
├── pyproject.toml      dependency groups and tool settings
└── scripts/ci_test.sh  Backend CI entrypoint
```

- Delivery adapters inject a Service API Protocol from `api/`, translating
  transport data/errors without owning domain policy or choosing plugins.
- Most Service API Protocols are owned by their core domain and re-exported
  from flat `api/` modules. Read the API README for retained definitions and
  named exceptions. Core must not gain a dependency on `api/` or delivery code.
- Core consumes outbound capability contracts. Concrete plugins are selected by
  composition roots, not imported or chosen by new business code.
- Profile-independent repositories live in `core/repository/`, with domain
  `protocols/` and `implementations/`; database selection is injected beneath
  them. Do not recreate local/prod ORM repository copies under `plugins/`.
- Kernel cannot import higher `agentclaw.*` layers. Keep framework/transport
  concerns out of core, raw environment reads in approved configuration/boot
  code, and implementation-specific imports out of contracts.
- Legacy `core/*/dependencies/` and named gate exceptions are existing debt,
  not the template for new modules. New bindings belong in `di/`.

The detailed import allowlists and compatibility exceptions remain in the
architecture gates; this guide does not expand them.

## Composition, configuration and enterprise integration

Follow `main.py` → `adapters/http/app.py` / `boot.py` → `di/container.py`.
The worker runtime builds the injector and attaches it to the application.
`di/modules/` binds services and Protocol aliases; `di/profile_modules.py`
selects the infrastructure column. Prefer constructor injection for services
and `Injected(Protocol)` at HTTP boundaries; do not add service-locator calls.

`DEPLOY_PROFILE` is mandatory and selects assembly; `SERVER_ENV` selects runtime
environment. Supported values and validation live in `di/profile.py`.
`di/config_bootstrap.py` and `modules_bootstrap.py` manage profile registration;
YAML files live under `src/agentclaw/community/configs/`, not a module-root
`configs/` directory. Do not resurrect mode selection in domain services.

`agentclaw` is a PEP 420 namespace. An enterprise superproject supplies a separate
`agentclaw.corp` source root and registers implementations at its composition
root. Public code must remain importable without that package or private SDKs.
For enterprise changes, inspect the matching superproject checkout, recorded
Avernet gitlink, consumers, bindings, configuration, build and tests. Public
branch merge is not proof of enterprise integration or deployment. Record the
exact refs and any missing enterprise evidence in the private work ledger.

## Flows and diagnostics

| Task or symptom | Read next |
| --- | --- |
| Trace a request to its runtime target | [Backend runtime targeting](../../docs/flows/backend-runtime-targeting.md) |
| Missing runtime/resource, wrong-instance result or timeout | [Runtime target diagnostics](docs/diagnostics/runtime-targeting.md) |
| Backend manager/Owner projection into BCS | [Manager sync](docs/flows/bot-manager-sync.md), [Owner sync](docs/bot-ownership-sync-operations.md) |
| Bot capability configuration and apply | [Manifest guide](docs/bot-config-manifest/README.zh-CN.md), [apply-engine boundary](src/agentclaw/community/core/apply_engine/README.md) |

The [repository map](../../CONTEXT-MAP.md) selects the app; this README is its
knowledge entry point. Detailed contracts, domain READMEs and existing runbooks
remain the authoritative topic sources.

### Existing documentation and change specifications

| Need | Existing source |
| --- | --- |
| API behavior | [OpenAPI v1 guide](docs/openapi-v1/README.md), [session-lock errors](docs/session-lock-api.md), and the owning contract/schema. |
| Subsystem knowledge | [Manifest](docs/bot-config-manifest/README.zh-CN.md), [Bot evolution](docs/bot-evolution/README.md), and the relevant core boundary. |
| Operations and failure handling | The flow/diagnostic links above and their referenced runbooks. |
| A change's intent, tradeoffs or verification history | The relevant topic under `specs/`, selected by issue or source history. |

Specs retain their own baselines and evidence; they are not the default guide to
today's architecture. An explicitly adopted contract inside `specs/` remains
authoritative until deliberately replaced. At delivery, update the owning reusable documentation and
link it from the spec/report using the [promotion rules](../../docs/agents/knowledge-standard.md#existing-docs-and-change-specs).
For example, the [manager-sync spec](specs/2026-10-27-bcs-manager-sync/spec.md)
records change history while the linked runbook describes operation.

## Develop and verify

Run these from **`apps/backend/`** using the Python version in `pyproject.toml`:

```bash
uv sync --frozen

# Narrow checks: choose relevant unit/contract/DI/compatibility tests as well
DEPLOY_PROFILE=test uv run --no-sync pytest tests/community/architecture -q

# Full Backend CI, including its coverage gate and reports
bash scripts/ci_test.sh
```

For a changed HTTP interface, run the existing fast gate before review/PR:

```bash
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/architecture \
  tests/community/framework/test_coverage_gate.py \
  tests/community/adapters/http/openapi_v1/test_explicit_user_id.py \
  tests/community/adapters/http/openapi_v1/test_schema_docs.py \
  tests/community/core/bot_config_surface/test_no_handler_only_checks.py
```

Run focused checks first, then independent Standards/Spec review and fix serious
findings. Open/update the PR to start remote CI; run the local full CI once in
parallel, fixing failures attributable to the change. Coverage thresholds,
worker options and report paths are owned by `scripts/ci_test.sh`, not copied
here. Test discovery and default acceptance exclusions are in `pytest.ini`;
external-service opt-ins are defined by the test fixtures. A default suite
passing does not demonstrate a live external service path.

### Test conventions

Service unit tests mock external Protocol dependencies rather than the tested
class's private methods. Keep cases focused on observable behavior and share
fixtures through the appropriate `conftest.py`, without importing another
module's conftest. Router tests inject test service bindings through the test
application/injector. Persistence integration tests exercise the actual local
database with isolated state; do not replace the database with a mock when
claiming storage behavior. Select the suite's existing external-service markers
for tests that need remote infrastructure.

For a public standalone stack, use the repository's [singlebox commands](../../CLAUDE.md#singlebox--the-only-local-entry-point).
For enterprise functional or cross-service validation, follow the matching
superproject's simulator instructions, build the intended public and enterprise
sources, verify the running image, then assert the relevant business outcome.
Unit/contract gates, simulator business evidence and full CI answer different
questions; a healthy process alone is not acceptance evidence. Documentation
changes should check links, commands and agreement with source.

## New modules and cross-domain changes

1. Read this guide, the constitution, and each touched domain's Context Boundary
   and Change impact. Trace providers and consumers before proposing a new seam.
2. Place HTTP schemas/routers in adapters, policy in core, public Service API
   exports in `api/`, replaceable outbound capabilities in `plugin_api/`, and
   bindings in `di/`. Reuse existing contracts with matching semantics.
3. Update contracts, consumers, selected implementations and meaningful
   compatibility/consumer tests together. Preserve existing legacy behavior
   unless removal and migration are explicitly in scope.
4. Give a new significant domain a Context Boundary and register it in
   `tests/community/architecture/test_module_boundaries.py`. A Plugin Protocol
   needs consumer-level contract evidence and the applicable pairing gates.
5. Verify affected scopes, including enterprise consumers when relevant, and
   report PR HEAD, checks, review and integration status separately. Keep public
   docs and PRs free of private work-item links, credentials and diagnostic data.

## Department membership rules

Department rules (`subject_type=department`) accept either a complete name
chain such as `Example-Engineering-Research` or a legacy ID path such as
`0001/0020/0030`. A rule for `Example-Engineering` includes its descendants;
`Research` alone does not match that full chain. Names use `-` as the hierarchy
separator, and ID paths use `/`. Empty levels, wildcards and values longer than
1024 characters are invalid candidates. Single-level names remain valid.

The directory's `dept_name` and `dept_path` are evaluated independently, so a
missing or invalid value does not prevent the other from matching. Comparisons
retain the database column's string collation, including case-insensitivity
where configured. Existing user-first checks and `env`, `user_list_type` and
`bot_type` scopes remain in effect. No schema or existing rule migration is
required. The `user_list.match_decision` log separates `department_names` from
`department_paths` to explain the candidates used in a decision.
