# Run the evolution control plane as a new `apps/evolution` service

> 中文版：[0021-evolution-control-plane-in-apps-evolution.zh-CN.md](0021-evolution-control-plane-in-apps-evolution.zh-CN.md)

Status: accepted (2026-10-16; formerly bot-evolution open decision D-1, option A). Owner: @totalfrank, signed off.

## Decision

The evolution control plane (Evolution Run orchestration, Strategy Registry,
Experience, Verification, Experiment Ledger, and Meta-evolution) lives in a
new Python service, `apps/evolution`, built with the Backend DI and plugin
pattern (`plugin_api`, composition roots, conformance tests) required by
[`docs/arch/arch.rules.md`](../arch/arch.rules.md).

Genome and Promotion stay in Backend, because they are desired state and
promotion must sit beside Manifest apply and publish
([ADR 0019](0019-bot-genome-is-the-unit-of-evolution.md),
[ADR 0020](0020-promotion-is-platform-owned.md)). The engine adapter owns
physical projection, memory import/export, and session export. ClawWeb stays
the UI and hosts the ClawEvolve strategy during migration.

Design: [`apps/backend/docs/bot-evolution/design.md` §6](../../apps/backend/docs/bot-evolution/design.md#6-ownership-and-module-placement).

## Consequences

- A new deployable that needs singlebox wiring, a local profile (SQLite,
  in-process strategies), and its own CI and pre-push gates (work item RSI-08).
- Long-running, model-heavy, bursty work scales and fails independently of
  Backend request serving.
- `apps/evolution` reaches Genome and Promotion only through Backend's
  contracts, never through Backend internals; the public `/openapi/v1`
  surface routes each evolution path to its serving service.

## Alternatives

- **Inside Backend as `core/evolution/`.** Rejected: puts long-running model
  jobs in the request-serving backend, which is already very large.
- **Promote ClawWeb's TS control plane.** Rejected: outside the
  constitution's DI, plugin, and conformance tooling; Node only; mixed with
  the UI.
