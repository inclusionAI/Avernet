# Spec — Bot Task Modes (`task_claim_mode` / `task_dream_mode`)

> SDD phase 1 — WHAT and WHY. No implementation detail.
> Module: **BCS** (`src/bcs/`, Rust). The frontend-facing PATCH endpoint and the
> `bcs_bots` storage live here; the BCS core service exposes an internal
> (non-OpenAPI) read capability for the roster (see C3). Frontend contract
> extracted in `api-frontend-patch.md`.

## Overview

Add two independent boolean toggles per bot, persisted on the BCS-owned
`bcs_bots` table:

- `task_claim_mode` — the bot may claim tasks.
- `task_dream_mode` — the bot may propose / "dream" tasks.

A frontend renders two switch buttons per bot to turn each on or off
independently, via the existing BCS bot update endpoint. When a toggle has
never been set, reads return `false`. The task consumer needs to retrieve the
roster of bots that have either toggle enabled, so it can scope task
discovery/dispatch to opted-in bots.

## Why

- Task claiming and task dreaming are upcoming bot capabilities that must be
  **opt-in per bot**. Operators need a simple on/off control per bot, owned by
  the same update flow they already use.
- The task consumer needs an **authoritative, up-to-date roster** of enabled bots
  rather than scanning every bot client-side. The toggles and the roster read
  belong with BCS, which owns bot relationships/routing/coordination (OCB entity
  ownership); the consumer reads the roster through the BCS core service
  (in-process, not a published contract — see C3).

## Goals

1. **Persist** `task_claim_mode` and `task_dream_mode` per bot as queryable
   flags on `bcs_bots`, each defaulting to `false` (off) when unset.
2. **Set** either toggle independently through the existing
   `PATCH /openapi/v1/collaboration/bots/{bot_id}` endpoint, under the same
   owner-only authorization as today's patch. (Frontend contract in
   `api-frontend-patch.md`.)
3. **Read** both toggles in BCS bot representations returned to clients
   (single GET, PATCH response, candidates/mine, etc.).
4. **Query enabled bots**: provide a **BCS-internal core-service read**
   (`list_by_task_modes`) that returns physical bots filtered (optionally) by
   `task_claim_mode` / `task_dream_mode` with OR (`Any`) / AND (`All`) semantics.
   It is **not exposed over OpenAPI** and is consumed in-process by the task
   consumer (see C3 for the consumer-boundary caveat). The existing
   `POST /openapi/v1/collaboration/bots/query` is **not modified**.

## Non-goals

- Defining task-claim or task-dream execution, scheduling, or dispatch logic.
  This change only adds the toggles and the roster read.
- Frontend implementation (tracked separately; backend/BCS-only here).
- Changes to the gateway's read-only `bcs_bots` mirror (it resolves bots by
  session token using 6 columns; it does not need these toggles).
- Changes to `POST /bots/query` (left untouched — see C3).
- Audit history or an event stream for toggle changes (not requested).

## Capabilities

### C1 — Set a task-mode toggle (BCS PATCH)

A caller who owns the bot (the bot's creator — same authorization as today's
PATCH) may set either toggle independently:

- `task_claim_mode: true | false`
- `task_dream_mode: true | false`

Requirements:
- Toggles are **top-level fields** on the PATCH request body (not inside
  `descriptor`).
- Omitting a toggle leaves it unchanged; setting one does not disturb the other.
- Both may be set in one PATCH.
- A PATCH carrying neither toggle and no other field remains invalid (today's
  "patch must contain at least one mutable field" rule is preserved).
- Setting a toggle to its current value is a successful no-op.
- Toggles are **physical-bot-only** (`kind = bot`). Patching them on a Human row
  is rejected with `invalid_bot_kind` (same bot-only semantics as `descriptor`).
- Persistence failures surface as errors (never silently return success).

### C2 — Read toggles (BCS bot representations)

Every **physical bot** representation returned by BCS OpenAPI bot endpoints
includes `task_claim_mode` and `task_dream_mode` as boolean fields, **always
present**, defaulting to `false` when never set. Human rows do not expose these
fields (they have no task-behavior semantics), consistent with Human rows
omitting `descriptor` / `reachability` / `provider` / `agent_code`.

### C3 — Query enabled bots (internal BCS core-service read; not exposed over OpenAPI)

The roster of enabled bots is a **BCS-internal core-service capability** — read
at the `BotControlPlaneCoreService` / `BotControlPlaneRepoPort` layer only:

- `POST /bots/query` is **not** changed. It remains explicit-ID hydration only.
- **No new HTTP/OpenAPI read endpoint** is exposed. The core service gains
  `list_by_task_modes` (repo `list_control_plane_by_task_modes`) with optional
  filters on `task_claim_mode` / `task_dream_mode` and OR (`Any`) / AND (`All`)
  semantics, scoped to physical bots and excluding soft-deleted rows.
- The consumer calls the BCS core service directly (in-process, BCS-internal).
  No backend Python client, no backend HTTP route, no read-side OpenAPI contract.

> Rationale for the internal shape: the consumer stated the read is "task内部实现用"
> / "只是core service调用" — an internal capability, not a published API. Keeping
> the read at the core layer avoids widening the OpenAPI surface and the
> cross-module contract burden. The toggles stay set through BCS and persisted on
> `bcs_bots` (BCS-owned); BCS keeps table ownership.
>
> **Open consumer-boundary question**: this shape assumes the consumer is
> BCS-internal (in-process). If the consumer is actually the backend Python task
> module (separate process, no `bcs_bots` access), an in-process core call is
> unreachable and a transport (OpenAPI/IPC) would have to be reintroduced — that
> conflicts with "no OpenAPI" and must be resolved with the consumer owner. The
> implementation delivers exactly the requested internal read; the question only
> governs whether a follow-up transport task is needed.

## Decisions (locked)

1. **Storage**: two top-level boolean columns on `bcs_bots`
   (`task_claim_mode`, `task_dream_mode`), defaulting to `0`/false — mirroring
   `is_deleted`. They are filterable columns, **not** fields inside the
   `bot_info` JSON blob. (The repo's queryable flags — `is_deleted`,
   `visibility`, `status`, `actor_kind` — are all top-level columns; `bot_info`
   holds descriptor payload read by ID, never filtered.)
2. **Write path**: extend `PATCH /openapi/v1/collaboration/bots/{bot_id}`;
   toggles ride the same patch path as `visibility` / `status`.
3. **Read exposure**: physical bots only; Human rows do not expose or accept
   the toggles (`invalid_bot_kind` on PATCH).
4. **Roster query**: internal BCS core-service read (`list_by_task_modes`),
   **not** `/bots/query` and **not** a new OpenAPI endpoint. Consumer is
   BCS-internal (see open question in C3).
5. **Unset vs explicitly-false**: indistinguishable — unset reads as `false`.
   Existing rows are backfilled to `false` on migration.

## Compatibility notes (carried into the plan phase)

- Contract change to a versioned OpenAPI endpoint (write side only: `PATCH`
  body + `PhysicalBot` response): requires updated `bots.yaml` +
  `domain-models.yaml` + docs + conformance tests (`test_bot_v1_contract.py`,
  Rust route tests, store conformance tests). **No new read path** is added to
  the OpenAPI contract.
- Schema migration ships with the code (MySQL `009_*` + SQLite DDL/versioned
  migration); existing rows backfilled to `false`.
- DB write failures propagate as errors (OCB rule).
- BCS-only storage/endpoints + a small backend task-module client are the two
  touch points. The plan will detail both, plus the migration ordering.