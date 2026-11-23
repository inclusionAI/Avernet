# Slice Architecture Rules Context

> Generated for the Todo template/stage lifecycle implementation on 2026-11-20.

## Arch-scaffold status

- `ensure_project_scaffolded(project_root=".")`: **degraded**. The vendored backend could not detect a supported language from the monorepo root because language manifests and source roots live below app directories.
- Explicit Python initialization also refused the root for the same reason.
- `show_rules_context(root)`: returned no configured rule matrix.
- `check_before_code(...)`: degraded because the vendored bridge and backend disagree on the `interface` call signature.
- Fallback required by the implementation phase is active: repository architecture constitution, CI gates, Context Boundary rules, and the skill built-in layer/test rules are the governing anchors.

## Governing source-of-truth rules

- `docs/arch/arch.rules.md`: contracts are authoritative; core stays transport agnostic; adapters do not own policy; concrete selection belongs to composition roots; database and external failures propagate.
- `docs/arch/ci.enforce.md`: dependency, environment, configuration, contract, schema-documentation, and red-flag gates must remain enabled.
- `docs/arch/context-boundary-format.md`: new Service API and repository seams must be declared in their owning Context Boundary metadata.
- `docs/arch/protocol-contract-tests.md`: protocol additions require conformance/compatibility evidence.
- Root and Backend `AGENTS.md`: HTTP adapters depend only on Service API Protocols; repositories are split into Protocol and implementation; tenant/env isolation is explicit; files remain under 1,000 lines; source changes include tests.

## Slice 2 rule anchor

`slice-2-publish-and-read-immutable-template-version`

- Files/layers:
  - `core/task/todos/template_*`: domain/application contract and policy; no FastAPI/SQLAlchemy imports.
  - `core/repository/protocols/task/todo_templates.py`: repository port; type-only domain imports under `TYPE_CHECKING` where possible.
  - `core/repository/implementations/task/todos/template_models.py` and `todo_template_repository.py`: persistence adapter; explicit tenant/env filters and transactions.
  - `api/task/todo_template_service.py`: Service API re-export only.
  - `adapters/http/openapi_v1/task/todos/template_*`: delivery DTO/router; imports Service API Protocol, not concrete service/repository.
  - `di/modules/task/todo_bindings.py`: composition root selects implementations.
- Applicable architecture rules: contract authority, dependency direction, transport-agnostic core, repository separation, composition-root binding, explicit isolation, write-error propagation, contract/documentation/test pairing.
- Fix hints:
  - move HTTP/DTO concerns from core to adapters;
  - move persistence details behind repository Protocols;
  - move concrete construction into DI;
  - add tenant/env predicates to every query;
  - update Context Boundary and conformance/inventory tests with every new contract/route.
- Tests: codec unit tests; real repository integration tests; service authorization/state tests; HTTP contract/inventory/schema tests.

## Slice 3-8 default anchor

The same dependency and contract rules remain active. Before each later slice, extend this document with its touched files, layer assignment, contract changes, and focused test plan. Run the incremental architecture guard after Green; because arch-scaffold is degraded at the monorepo root, also run the repository architecture tests as the authoritative fallback.
