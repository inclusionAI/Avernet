# `agentclaw.community.core.bot_config_manifest`

A declarative configuration document that belongs to a **bot** — MCP servers,
workspace resources, local skills, identity files, the imperative startup
script — stored as text and applied by the platform at lifecycle points.

The problem it exists for: business content (skill zips, persona files) lives
on the business own services and self-hosted git, and they want a bot to come
up configured from **their** source of truth instead of being hand-configured
through the UI — identical across instances, reproducible after any rebuild
(#926 is the original instance-consistency demand). The imperative answer
(#935 startup script) cannot reach teclaw and exposes engine directory layout
to users; this module is the declarative one.

## What this module decides, and what it deliberately does not

It owns:

- **the document model and its validation** — schema v1 parsed at `PUT`, every
  rejection naming the offending entry (schema rules + per-engine category
  gates). All-or-nothing: a rejected document writes nothing;
- **the capability resolver** — one pure function over
  `(engine_type, bot_type)`, shared by the read path (`GET …/capabilities`)
  and the write path (`PUT` validation) so they cannot disagree. Unknown
  engines answer *unsupported*;
- **storage fidelity** — the document is persisted as written, byte-exact
  through the round trip: a script body with `$(id)` or `{token}` must come
  back character-identical, because that body is later executed.

It deliberately does not own anything downstream of storage:

- **no apply** — fetch, materialization and lifecycle wiring live in later
  work items (W2+/W4+). This module stores, validates and reports capability.
  Phase 1 therefore answers `engine_config` and `cli_tools` as **unsupported**:
  no materializer exists yet, and a category nothing can apply must not be
  writable (fail closed, not silently ignored on a later apply);
- **no credentials** — `auth` is stored as a *name* only. Tenant credential
  storage/encryption is W3; this schema layer's job is placement — `auth` only
  where a credential is actually presented, never on `from`/`content` entries,
  never carried by value. Transport-side guarantees for `source` URLs
  (https-only, no userinfo, per-hop re-validation) are the guarded fetcher's
  (W2), not the document schema's;

## Phase-1 delivery decisions (2026-08-31)

- Delivery of *any* configuration happens after bot start/activation (user
  decision, tracked in #1508) — nothing in this module assumes pre-boot apply.
- The public routes ship dark behind `BCM_API_ENABLED` (feature flag, pattern
  of `SkillCenterFlags`) until the first materializers land (W5 / M3).

See `docs/bot-config-manifest/design.zh-CN.md` for the full design, and
`work-items.zh-CN.md` W1 (#1469) for acceptance criteria.
