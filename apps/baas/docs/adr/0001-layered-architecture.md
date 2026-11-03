# ADR-0001: Layered architecture with inward dependencies

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

BaaS began as a single `secbaas` service with an `servers/ application/ domain/
infra/` monolith layout. That layout is now history: the code was reorganized
(the publish enums, for example, were "extracted from domain/model/publish.py
as part of api/domain → api/{domain}/ refactoring") and then split into two
editions (ADR-0007). The undocumented layer rules were the main reason new
contributors could not tell where a change belonged.

Today the community package is the full platform:
`api/` (route definitions, request/response models, domain enums — no service
logic), `adapters/` (the FastAPI web app — the only transport-aware layer),
`core/` (business services in `service/`, persistence in `repository/`),
`bootstrap/` (DI composition root, startup), plus `spi/` and `plugins/`
(ADR-0003). The enterprise overlay adds only backend plugin implementations,
`routers/`, and its own `bootstrap/`. The legacy layout diagram still lives in
`enterprise: docs/ARCHITECTURE.md` and is superseded by this ADR.

## Decision

Use a layered architecture with inward-only dependencies in the community
platform:

- `adapters/` → `api/` → `core/`: adapters own transport concerns (FastAPI
  app wiring, middleware, request dispatch) and contain no business policy;
- `api/` owns route surface and data shapes only;
- `core/service/` owns business logic and is transport-agnostic (no FastAPI
  imports);
- `core/repository/` owns persistence per aggregate (ADR-0013);
- `bootstrap/` is the only composition root that selects implementations —
  raw environment/config/dependency wiring belongs there;
- `spi/` is the only seam through which `core/` reaches infrastructure, and
  `plugins/` supply configurable implementations.

The architecture constitution of the monorepo (contracts as authority,
transport-agnostic cores, composition roots as the approved selection point)
applies verbatim; this ADR is the layer map that realizes it for the BaaS app.

## Consequences

**Positive:** each layer is testable in isolation; business rules do not drift
into transport code; the enterprise overlay can replace whole backends without
touching layers above; the target of any new file is unambiguous.

**Negative:** more directories and indirection than the old monolith; adding
a feature still means touching several layers.

**Risks:** the legacy diagram in `docs/ARCHITECTURE.md` can mislead until it
is archived — keeping it is deliberate (history), but this ADR supersedes it.

## Evidence

- community: `src/secbaas/community/api/` — route modules holding models/enums, no service logic
- community: `src/secbaas/community/adapters/` — web adapter root
- community: `src/secbaas/community/core/service/` and `core/repository/`
- community: `src/secbaas/community/bootstrap/` — composition root
- community: `src/secbaas/community/api/publish_manage/_enums.py` — docstring records the api/domain → api/{domain} refactoring
- enterprise: `src/secbaas/enterprise/{routers,bootstrap,plugins}/` — overlay contains no domain core
- enterprise: `docs/ARCHITECTURE.md` — superseded pre-split layout (history)