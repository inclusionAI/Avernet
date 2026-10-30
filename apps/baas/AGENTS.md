# AGENTS.md

Module guidance for the BaaS app (`apps/baas/`). In the Avernet repo this is
the community edition (`src/secbaas/community/`); the internal OCB monorepo
carries an enterprise overlay (`src/secbaas/enterprise/`) against the same
app root. Product usage lives in `README.md`; this file covers architecture
orientation.

## Read the ADRs before architecture work

- `docs/adr/` is the authority for BaaS architecture **decisions** and their
  consequences. Start at its [`README.md`](docs/adr/README.md) index; the
  current set is ADR-0001 … ADR-0017.
- Orientation set: ADR-0001 (layering and where code belongs), ADR-0003
  (the spi ↔ plugins seam for any new backend), ADR-0007 (how the two
  editions relate). ADR-0005/0004 before touching publish or lifecycle code;
  ADR-0010 and ADR-0016/0017 before writing any repository; ADR-0014 before
  adding scheduled work; ADR-0015 before adding any dependency wiring (one-way
  assembly: `bootstrap/` wires everything, nothing reaches back).
- Legacy notes and the pre-split `docs/ARCHITECTURE.md` diagram are history;
  where an ADR says superseded, the ADR wins.

## When a new ADR is required

Any hard-to-reverse BaaS decision — a new layering rule, protocol or state
machine change, a new external seam, a change to the packaging split — gets
an ADR **before** implementation, following the README recipe: next number is
0018, use the seven required sections, and cite verified `## Evidence` paths
(code wins over prose).

## Two-edition rules

- This repo's `docs/adr/` set is the **platform authority** (it also records
  the two-edition relationship, ADR-0007). The internal OCB monorepo keeps
  its own enterprise-focused ADR set under its `apps/baas/docs/adr/`
  covering the `secbaas.enterprise` overlay. The sets are intentionally
  separate; do not copy one into the other.
- Evidence paths are labeled: `community:` resolves under the Avernet app
  root, `enterprise:` refers to the internal OCB app root (private overlay).
- New infrastructure capability in `core/` must land as an `spi/` contract +
  `plugins/` option selected via `user_config.plugins` in
  `configs/application.yaml` — do not fork a service for one edition.
- Components are assembled **only** by `bootstrap/` (ADR-0015): never import
  `get_container`/`set_container` from `secbaas.community.bootstrap` outside
  `bootstrap/` to self-serve a dependency — constructor injection (or an
  injected zero-arg lazy factory for acyclic graphs) only. New violations
  fail the arch test
  `tests/architecture/test_no_bootstrap_get_container_in_all.py`
  (community tree; enterprise runtime code is under the same principle).
- Contribute shared changes through the Avernet dev branch first; the
  internal monorepo consumes them and bumps its submodule pointer.