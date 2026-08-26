# Spec — Bot Task Modes (`task_claim_mode` / `task_dream_mode`)

> SDD phase 1 — WHAT and WHY. No implementation detail.
> Module: **BCS** (`src/bcs/`, Rust) for the write side — the frontend-facing
> PATCH endpoint and the `bcs_bots` storage live here, and `PhysicalBot` exposes
> the toggles. The roster **read** lives in the **backend task module** core
> (depends on BCS) — see C3. Frontend contract extracted in
> `api-frontend-patch.md`.

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
4. **Query enabled bots**: implement the roster query in the **backend task
   module core** (which depends on BCS for bot data), filtering by
   `task_claim_mode` / `task_dream_mode`. It is **not** a new BCS OpenAPI
   endpoint and **not** implemented inside the BCS module (see C3 for the
   open transport decision). The existing
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

### C3 — Query enabled bots (read lives in backend/task core; depends on BCS)

The roster of enabled bots is queried in the **backend task module's core layer**,
which depends on BCS. BCS itself only persists the toggles and exposes them on its
bot representations; it does **not** host the roster read.

- `POST /bots/query` is **not** changed. It remains explicit-ID hydration only.
- **No new BCS HTTP/OpenAPI read endpoint** for the roster. BCS exposes the toggles
  via existing bot representations (`PhysicalBot` now carries `task_claim_mode` /
  `task_dream_mode`); the roster query/filter is implemented in `backend/.../task`
  core, depending on BCS for the bot data.
- The earlier BCS-internal `list_by_task_modes` (repo `list_control_plane_by_task_modes`
  + `TaskModeMatch`/`BotTaskModesQuery` types) was **reverted** — the read does not
  belong in the BCS module.

> Rationale: the consumer stated the read is "task 内部实现用" and lives in the
> backend task submodule whose core depends on BCS — not in the BCS module. BCS
> keeps table ownership and the PATCH write path; the roster read is a task-module
> concern.
>
> **Open transport decision (blocks the backend read implementation):** `BcnService`
> (backend's only BCS client) is currently **write-only** (onboard/register/switch/
> delete); backend has no existing read path to BCS bots. "No new OpenAPI" + "backend
> core depends on BCS" + "bcs_bots is BCS-owned" therefore need a concrete transport:
> (a) reuse an existing BCS bot-read surface that already returns the toggles and
> filter locally in backend task core; (b) BCS syncs the toggles into a
> backend-readable store and backend reads locally; or (c) another agreed channel.
> This must be confirmed with the consumer/BCS owner before the backend read is built.

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
4. **Roster query**: implemented in the **backend task module core** (depends on
   BCS), **not** in the BCS module and **not** a new BCS OpenAPI endpoint.
   Transport TBD (see C3).
5. **Unset vs explicitly-false**: indistinguishable — unset reads as `false`.
   Existing rows are backfilled to `false` on migration.

## Compatibility notes (carried into the plan phase)

- Contract change to a versioned OpenAPI endpoint (write side only: `PATCH`
  body + `PhysicalBot` response): requires updated `bots.yaml` +
  `domain-models.yaml` + docs + conformance tests (`test_bot_v1_contract.py`,
  Rust route tests, store conformance tests). **No new read path** is added to
  the BCS OpenAPI contract.
- Schema migration ships with the code (MySQL `009_*` + SQLite DDL/versioned
  migration); existing rows backfilled to `false`.
- DB write failures propagate as errors (OCB rule).
- BCS-only storage/endpoints + a small backend task-module client are the two
  touch points. The plan will detail both, plus the migration ordering.