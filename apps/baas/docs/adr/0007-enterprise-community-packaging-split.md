# ADR-0007: Enterprise / Community packaging split via PEP 420 namespace

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

BaaS must be simultaneously an open-source, reproducible-from-public-deps
product and an enterprise service integrated with company middleware. Forking
per-customer was rejected history: the secbaas monolith predates the split
(`split-plan.md`), and the plugin seam (ADR-0003) is what makes a package
split viable. Company-only services, credentials, and registries must never
land in the OSS tree.

## Decision

Ship two editions of one app under the implicit PEP 420 `secbaas` namespace:

| Edition | Package | Repo | Contents |
| ------- | ------- | ---- | -------- |
| Community | `secbaas.community` | Avernet `apps/baas/src/` | The full platform: `api/`, `adapters/`, `core/`, `bootstrap/`, `spi/`, `plugins/` with stub/self-hosted backends |
| Enterprise | `secbaas.enterprise` | internal OCB monorepo `apps/baas/src/` | Overlay only: enterprise `plugins/` options (real ZDAS/ZCache/SM4 secret, buservice auth, mesh sandboxes), `routers/`, `bootstrap/` wiring |

Rules:

- the platform and every contract live in `secbaas.community`; the enterprise
  package contains **no** business logic and **no** duplicated services — only
  plugin implementations and their wiring;
- enterprise joins via the sanctioned seam: `plugin_registry.register_plugin_option`
  deferred factories (ADR-0003);
- each repo owns its ADR documentation: this set records the platform
  (including the two-edition relationship); the internal monorepo keeps a
  separate enterprise-focused set for the `secbaas.enterprise` overlay
  architecture (see `docs/adr/README.md` scope rules);
- contribution protocol: the Avernet repo is authoritative on GitHub
  (`inclusionAI/Avernet`); internal mirrors are fetch-only; enterprise
  changes flow through the same public review, then the consuming monorepo
  bumps its submodule pointer.

## Consequences

**Positive:** OSS tree is clean of company secrets by construction; community
features are enterprise features; review of shared logic happens once,
publicly; enterprise cannot silently fork.

**Negative:** two-repo mechanics (pointer bumps, upstream PRs) for every
platform change; the namespace trick means imports resolve only when both
 package roots are present in enterprise builds.

**Risks:** an enterprise "quick fix" bypassing upstream review would fork the
platform silently — mitigated by the pointer-bump protocol and by this ADR
being the reference point for reviewers.

## Evidence

- community: `src/secbaas/community/` — platform tree (`api`, `adapters`, `core`, `bootstrap`, `spi`, `plugins`)
- enterprise: `src/secbaas/enterprise/` — overlay tree (`plugins`, `routers`, `bootstrap`; no domain core)
- community: `src/secbaas/community/plugin_registry.py` — the join seam for enterprise options
- enterprise: `docs/split-plan.md`, `docs/micro-kernel-plugins.md` — split history
- community: `pyproject.toml` — package wiring