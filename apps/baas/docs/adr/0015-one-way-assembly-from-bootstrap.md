# ADR-0015: One-way assembly — components are wired by bootstrap, never the reverse

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

`bootstrap/` is the composition root: it constructs every component and
resolves all dependencies via constructor injection and config-driven
selectors (ADR-0001, ADR-0003). `community.bootstrap.get_container()`
exposes the live `ApplicationContainer` singleton, so it is mechanically
possible for any module to fetch the container and self-serve dependencies —

that is service location, and it reverses the assembly direction: hidden
coupling, components that are only testable through the global container,
implicit "container already initialized" ordering assumptions, and wiring that
no longer lives in one reviewable place. A few lazy imports of this kind
existed historically (callback handling, `arca_utils`); issue #852 removed
the final sites, and an architecture unit test now ratchets the count at
zero.

## Decision

Assembly is strictly one-directional: `bootstrap/` wires all components; no
production component uses bootstrap in reverse.

- Outside `bootstrap/`, production code shall not import
  `get_container`/`set_container` from `secbaas.community.bootstrap`; new
  violations fail
  `community: tests/architecture/test_no_bootstrap_get_container_in_all.py`
  (empty debt baseline — keep it empty; a new grandfather entry needs an
  explicit, time-bounded plan like any other architecture waiver).
- Components receive dependencies exclusively via **constructor injection**.
  Where the object graph needs to stay acyclic, inject a zero-argument lazy
  factory resolved at call time — the `DeviceCallbackHandler` pattern —
  rather than reaching for the container.
- Layer import bans stay in force
  (`community: tests/architecture/test_layer_rules.py`): `api/`, `spi/`, and
  `core/` must not import `bootstrap` at all; `adapters/` may import
  `bootstrap` only to drive startup; within `bootstrap/` itself (including
  enterprise wiring) there are no bans.
- The only sanctioned outside-in flow is the process entrypoint calling
  bootstrap's public lifecycle helpers (`init_container_config`,
  `initialize_services`, `shutdown_services`, `set_container`): entrypoints
  *run* the composition root, they must not become a channel for other code
  to self-serve from it.
- The enterprise overlay joins by registering plugin options (ADR-0003)
  inside its own `bootstrap/_register.py` wiring — never from runtime
  component code.

## Consequences

**Positive:** the object graph is acyclic by construction; every dependency is
explicit and reviewable in one place; components are unit-testable with plain
constructor arguments; swapping backends is configuration-only; regressions
fail a test instead of surfacing as hidden coupling.

**Negative:** genuinely lazy/cyclic needs require the explicit lazy-factory
ceremony; bootstrap wiring signatures grow as components are added.

**Risks:** a future "service registry"-style helper could recreate reverse
access under a different name — apply the principle (only the composition
root selects implementations), not just the symbol; the AST scan does not
flag `TYPE_CHECKING` imports of container types, so keep container types out
of component signatures entirely.

## Evidence

- community: `src/secbaas/community/bootstrap/__init__.py` — `get_container`/`set_container` definitions and lifecycle exports
- community: `tests/architecture/test_no_bootstrap_get_container_in_all.py` — the ratchet (Rules 6/7/14 derivation; empty `_KNOWN_DEBT` since #852)
- community: `tests/architecture/test_layer_rules.py` — layer import bans ("bootstrap → everything; api/spi/core must not import bootstrap")
- community: `src/secbaas/community/core/service/paas/_callback_handler.py` — sanctioned lazy-factory injection pattern
- community: `src/secbaas/community/bootstrap/_container.py` — container factory delegates live inside bootstrap
- community: `src/secbaas/community/bootstrap/plugins/_plugin_core.py` — in-bootstrap wiring example
- enterprise: `src/secbaas/enterprise/bootstrap/_register.py` — overlay joins inside enterprise bootstrap wiring
- enterprise: `docs/micro-kernel-refactor.md` — Rule 14: "only Composition Root selects implementations"