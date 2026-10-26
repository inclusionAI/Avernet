# AGENTS.md

Module guidance for the BaaS app (`apps/baas/`). In the Avernet repo this is
the community edition (`src/secbaas/community/`); the internal OCB monorepo
carries an enterprise overlay (`src/secbaas/enterprise/`) against the same
app root. Product usage lives in `README.md`; this file covers architecture
orientation.

## Read the ADRs before architecture work

- `docs/adr/` is the authority for BaaS architecture **decisions** and their
  consequences. Start at its [`README.md`](docs/adr/README.md) index; the
  current set is ADR-0001 … ADR-0014.
- Orientation set: ADR-0001 (layering and where code belongs), ADR-0003
  (the spi ↔ plugins seam for any new backend), ADR-0007 (how the two
  editions relate). ADR-0005/0004 before touching publish or lifecycle code;
  ADR-0010 before writing any repository; ADR-0014 before adding scheduled
  work.
- Legacy notes and the pre-split `docs/ARCHITECTURE.md` diagram are history;
  where an ADR says superseded, the ADR wins.

## When a new ADR is required

Any hard-to-reverse BaaS decision — a new layering rule, protocol or state
machine change, a new external seam, a change to the packaging split — gets
an ADR **before** implementation, following the README recipe: next number is
0015, use the seven required sections, and cite verified `## Evidence` paths
(code wins over prose).

## Two-edition rules

- The ADR set and this file are **byte-identical** in both repos' `apps/baas/`.
  Change both copies or neither.
- Evidence paths are labeled: `community:` resolves under the Avernet app
  root, `enterprise:` under the internal OCB app root; unlabeled paths must
  resolve under both.
- New infrastructure capability in `core/` must land as an `spi/` contract +
  `plugins/` option selected via `user_config.plugins` in
  `configs/application.yaml` — do not fork a service for one edition.
- Contribute shared changes through the Avernet dev branch first; the
  internal monorepo consumes them and bumps its submodule pointer.