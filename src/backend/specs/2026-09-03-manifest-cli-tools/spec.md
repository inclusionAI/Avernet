# `cli_tools` — Platform-Managed Command-Line Tools (W9)

Work item W9 of `docs/bot-config-manifest/work-items.zh-CN.md` §5, issue #1477.
Plan: `plan.md` in this directory.

> **Revision 7 (2026-09-03).** The engine's `install` owns the executable bit —
> the platform issues no `chmod` and runs no shell command. Rev 6's summary
> follows, unchanged otherwise.
>
> **Revision 6.** The platform **stores every tool's bytes in OSS
> at install time**. Without that copy there is nothing for a teclaw artifact to
> reference on a live update or a manifest apply — the gap rev 5 missed. That
> makes the artifact teclaw's delivery (no separate engine upload), makes
> promotion a re-key rather than a download, and settles the phase: `cli_tools`
> is `PRE_CONTAINER` on teclaw and `ON_CONTAINER` on ARCA, under either switch
> position. Revision history at the end.

## Summary

A bot owner declares a command-line tool in the manifest, or manages one
directly through a CLI-tools API; either way the tool is installed into the
bot's container and the platform owns the record of what is installed. The
platform does the dangerous work — fetch, mandatory `sha256` enforcement,
unpack, selection of the one declared file, architecture verification — and
hands the engine **one executable file and a name**. Where that file lands is
the engine's business.

## Motivation

`bcs-cli` is the proof the feature is needed: today a binary is placed by hand
by a singlebox script (`scripts/modules/bots.sh:954`) and taught to the model by
a hand-written `SKILL.md`. That pattern works and cannot be offered to a
customer — there is no declarative way to say "this bot has this tool", so every
tool is a bespoke change to platform scripts.

**Why platform-managed.** A tool carries state the platform must keep to do its
job: the `sha256` it was pinned to, which archive member was selected, the `md5`
of the delivered bytes, the version on record. Once the platform owns that
record, the record has to be the only way in — a tool created or deleted behind
the platform's back would leave the metadata describing something that is no
longer there.

## What the code allows, checked before writing this

1. **`identity` is the precedent for this shape.** Platform-managed, own service
   (`core/services/identity.py`), own API, and the manifest's `identity`
   materialiser delegates to that service rather than reimplementing it.
   `cli_tools` is the same plus a table and an engine-side executable bit.
2. **`mcp` is the precedent for "always platform-managed".** The composer marks
   `mcp` as `platform` on **every** occasion, not only under the teclaw switch:
   "the artifact has carried the whole MCP set on every compose since W12, so
   there is no engine state for it to keep" (`config_composer.py`).
   `cli_tools` behaves the same way.
3. **teclaw creation has no on-container phase.** W8's creation sequence with
   the platform-managed switch on is record → pre-container phase → provision →
   `ACTIVE`, with **no phase B**; with the switch off it is an empty phase A →
   create and provision → `ACTIVE` → phase B. Under *either* position the
   pre-container phase runs before the first artifact is composed, so a
   construct teclaw delivers by artifact must be `PRE_CONTAINER` — an
   `ON_CONTAINER` `cli_tools` would simply never run on a teclaw creation.
4. **`TeclawFilePromotion` is the promotion boundary this feature must join.** At
   a promotion boundary (draft→verify, verify→publish) the backend "must **read
   the source container's files from the engine**, write each to a
   **stage-scoped OSS key**, and return `{store, path}` refs to embed in the
   composed `BotConfigArtifact` for the new stage". It sweeps two namespaces
   today, `workspace` and `identity`, through `DeviceFileSystem`.
5. **The executable bit belongs to the engine's `install`, not to the
   platform.** Once ARCA engines expose a dedicated CLI `install` endpoint, that
   call carries the semantics "make this a CLI tool for this bot" — placement,
   the executable bit and exposure to the agent are all inside it. The platform
   issuing a `chmod` of its own would be a second implementation of the engine's
   job, reached through a general shell channel with a user-supplied name to
   quote. (`BaasService.exec_command_on_bot` exists and is used for container
   bootstrap; this feature does not need it.)
6. **The generic file-write path could not have done this anyway**: the BaaS
   device uploads through `POST /api/file/upload` with a `target_path` and no
   mode field, and `DeviceFileSystem` exposes no `chmod`. A CLI tool needs an
   endpoint that knows it is installing a tool — which is what the engine's
   `install` is.
7. **The table pattern is established.** `ac_bot_startup_script` is the model:
   ORM plus a pydantic record, `env` column, tenant guard, `UniqueConstraint`,
   registered by a side-effect import in `core/schema.py`, protocol and
   implementation split under `core/repository/{protocols,implementations}/bot/`.
8. **`${BOT_ARCH}` already resolves to `amd64`** (`schema/placeholders.py`) —
   W1 shipped it, contrary to the W9 progress table. Only the ELF verification
   half of that row is outstanding.
9. **The schema already parses a `cli_tools` entry** and enforces the mandatory
   `digest`; `fetch/limits.py` carries the 200 MiB width; `APPLY_ORDER` places
   `cli_tools` at `ON_CONTAINER`. The category is refused today only by the
   capability gate.

## The design

**One core component does the work. The platform keeps the bytes. The engine
owns the filesystem.**

```text
      POST/GET/DELETE .../cli-tools          manifest apply / W13 creation
                   │                                      │
                   └──────────────┬───────────────────────┘
                                  ▼
                          CliToolService          ← the only thing that
             (fetch · verify · store · record       does the work
                    · deliver · replace)
                                  │
                ┌─────────────────┼─────────────────┐
                ▼                 ▼                 ▼
        ac_bot_cli_tool     OSS tool store    delivery, per family
          (metadata)         (the bytes)      ARCA: install by name
                                              teclaw: artifact refs
```

- **The platform stores every tool's bytes in OSS at install time.** This is the
  gap rev 5 missed: on a live CLI update or a manifest apply, a teclaw artifact
  has to reference the tool *now*, and there is nothing to reference unless the
  platform kept a copy. Gathering from the engine at that moment would be
  circular — the platform is the side that just fetched and verified the bytes.
- **Metadata is the platform's.** `ac_bot_cli_tool` holds one row per tool per
  bot: the pinned `digest`, the selected `subpath`, the delivered `md5`, the
  `version`, the OSS key, size and audit stamps. It answers "what does this bot
  have", and it makes replacement and removal decidable.
- **Delivery differs by family, and only there.**
  - **ARCA** — the tool is installed into the live container by name, in one
    call to the engine's `install` endpoint, which owns placement, the
    executable bit and exposure to the agent. Needs the container, so
    `ON_CONTAINER`.
  - **teclaw** — the artifact *is* the delivery. The platform updates the row,
    writes the bytes to OSS, composes the `cli_tools` refs and delivers, exactly
    as `mcp` is composed and delivered. No separate engine upload call.
- **The backend never knows where a tool lands.** ARCA's install is by name and
  the directory is the port's private constant; teclaw resolves the refs itself.
  No container path crosses the boundary in either direction.
- **The API delegates; it does not implement.** The HTTP routes are a thin
  adapter over `CliToolService`.
- **Manifest apply is just another caller**, and a **full override**: the
  declared set becomes the installed set, per §3.2.
- **Always platform-managed, like `mcp`** — independent of the
  `teclaw_platform_managed` switch.
- **No projection component.**

### The phase, and why it differs by family

`cli_tools` is **`ON_CONTAINER` on ARCA** (a live container write) and
**`PRE_CONTAINER` on teclaw** (the artifact is composed before provisioning),
under *either* switch position. On an existing bot the two phases run back to
back and the distinction is invisible; it only decides anything on the W13
creation path — which is exactly where an `ON_CONTAINER` `cli_tools` would never
run on teclaw, because that sequence has no phase B.

This is a **category-specific rule** in the teclaw strategy's `phase_of`, and
rev 5's claim that the per-family phase needs no category-specific code was
wrong: the generic re-phasing keys on the switch, and this category must not.

### teclaw promotion

- [ ] At a promotion boundary (draft→verify, verify→publish) the tool objects are
      **copied to the new stage-scoped prefix** under the layout
      `TeclawFilePromotion` already builds, and the composed artifact's refs
      point at the new objects.
- [ ] **Nothing is downloaded from the engine.** The platform's OSS copy is the
      source, so promotion costs a server-side copy rather than a round trip
      through the container.
- [ ] Each ref is `{name, store, path, md5, version}` per `cliToolRef`, with
      `md5` and `version` read from the metadata table.
- [ ] Draft and verify snapshots do not share objects.
- [ ] A promotion of a bot with no tools composes an artifact byte-identical to
      today's, with `cli_tools` omitted.

### The management API

- [ ] `POST` / `GET` / `DELETE` under `/openapi/v1/bots/{bot_id}/cli-tools`
      install, list and remove a tool, each delegating to `CliToolService`.
- [ ] Each route carries its own `ADMISSION` line and is collaborator-scoped the
      way the config-manifest routes are: MEMBER to read, ADMIN to write.
- [ ] The service API contract lives under `api/` and is registered in the
      consistency `_PAIRS`; `core` never imports that layer.
- [ ] No response exposes a container path.
- [ ] A tool installed through the API is visible to a subsequent manifest apply
      as something the override replaces or removes, and the report says which.

### Manifest apply

- [ ] The `cli_tools` materialiser calls `CliToolService.replace_all` and adds no
      fetch, verification or placement logic of its own.
- [ ] `cli_tools` is **`ON_CONTAINER` on ARCA and `PRE_CONTAINER` on teclaw,
      under either switch position** — a category-specific rule in the teclaw
      strategy's `phase_of`, because the generic re-phasing keys on the switch
      and this category must not. `order.py` still carries the ARCA reading and
      is not modified.
- [ ] A test pins that a teclaw creation with declared tools has them in its
      **first** artifact, under both switch positions.
- [ ] `ownership.cli_tools` is `platform` on every compose, for the same reason
      `mcp` is.
- [ ] A `PUT` takes effect immediately on both families. **No §2.6 exception.**
- [ ] W13 creation provisions tools through the same service call.
- [ ] Convergence is on `digest` **and** `subpath` together, read from the
      metadata row. `version` is metadata and never affects it.
- [ ] An empty declared `cli_tools: []` removes every tool; an undeclared
      `cli_tools` is untouched, per §3.2.
- [ ] **Creation cleanup:** when a W13 creation job fails after tools were
      installed but before a bot exists, the rows and the installed tools are
      removed with the rest of that bot's manifest state.

### Admission and capability

- [ ] `cli_tools` is **supported** in the capability resolver for the ARCA family
      and for teclaw, and stays unsupported for desktop bots and unknown engines,
      with the existing reasons.
- [ ] The `cli_tools` rows are removed from the "not yet open" gate tables in
      `manifest-schema` §7 and work-items §5 W1.
- [ ] **Content-dependent `subpath` validation lives in the service**: after
      unpack, the selected member must exist, must be a regular file, and must
      still resolve inside the unpack tree after symlink resolution. W1 keeps the
      syntactic half.
- [ ] `digest` remains mandatory for every non-git form, refused at `PUT` and
      equally refused by the API.

### Nothing else moves

- [ ] No resources endpoint changes and no filter is added; a test asserts an
      installed tool never appears in a resources listing.
- [ ] No deploy-path change: `build_start_command`, `BotDeployContext` and
      `_compose_start_command` are untouched, so every bot's composed start
      command is byte-identical and #935's assertion is unedited.
- [ ] No `core/skill_center/*` change and no runtime-projection change.
- [ ] `engine_config` stays unsupported with its existing reason.
- [ ] Every existing manifest, artifact, resources, promotion, creation and
      deploy test passes with assertions untouched.

## Decisions

**D-1 — CLI tools are platform-managed, with platform-owned metadata.** A tool
carries state the platform must keep — the pin, the selected member, the
delivered hash, the version — so there is a table, and the table is the record
of what was asked for.

**D-2 — One core component; every caller delegates to it.** The HTTP API and
manifest apply are two callers of `CliToolService`. Backend code never calls its
own HTTP endpoints.

**D-3 — The engine owns the directory; the protocol is name-addressed.** No
container path crosses the boundary. This is what makes the teclaw arm possible
at all (an external engine's layout is not ours to know) and it removes a whole
class of path-handling from the backend.

**D-4 — The platform stores every tool's bytes in OSS at install time.** Without
it a teclaw artifact composed for a live update or a manifest apply has nothing
to reference. The accepted cost is one extra write per install — and on ARCA,
where the artifact is not the delivery, that write buys re-delivery without
re-fetching a source URL that may have rotated or gone away, which is worth it
on its own.

**D-5 — Delivery is per family, and that is the only thing that differs.** ARCA
installs by name into the live container through the engine's `install`
endpoint, which owns placement, the executable bit and exposure — the platform
issues no `chmod` and runs no shell command. teclaw's artifact *is* the
delivery, so there is no separate engine upload. The protocol needs no `get`,
because the platform never reads bytes back out of a container.

**D-6 — `cli_tools` is `ON_CONTAINER` on ARCA and `PRE_CONTAINER` on teclaw**,
under either switch position, because a teclaw creation has no phase B and its
artifact is composed before provisioning. This is a category-specific rule in
`phase_of`; rev 5's "no category-specific code" claim was wrong.

**D-7 — Nothing is needed to hide tools from the resources API.** Earlier
revisions proposed a `tools/` namespace and, before that, a filter. Neither is
required once the backend never addresses a tool by path and the engine keeps
tools outside the workspace it serves.

**D-8 — Always platform-managed, independent of the teclaw switch**, exactly as
`mcp` is. `ownership.cli_tools` is `platform` on every compose.

**D-9 — Convergence keys on `digest` *and* `subpath`.** `subpath` is a
**source-side** path — which member of the fetched archive is the tool — never a
target path. The same archive with `subpath` moved from `bin/old` to `bin/new`
delivers a different file under the same command name; keying on `digest` alone
would report `unchanged` and leave the old binary answering to it.

**D-10 — The agent finds tools through a default-skillset skill, not `PATH`, in
v1.** Accepted on the owner's call, with the cost stated: the model invokes a
tool by absolute path, so `mycli --help` does not work and every invocation
depends on the skill being read; a script that shells out to a sibling tool will
not find it either. What makes deferring safe is that placement is engine-side —
adding the directory to `PATH` later changes nothing in the manifest schema, the
API, the table or the artifact contract. **`manifest-schema` §3.7's promise that
the platform guarantees the tool is on the agent's `PATH` is therefore no longer
true and must be corrected**, not left to be discovered.

**D-11 — `SCHEMA_VERSION` stays 4.** Settled with the teclaw owner on 2026-08-31
and pinned by a test. `schema_version` no longer tracks this contract's
evolution, so "does this artifact carry `cli_tools`?" is answered by probing.

**D-12 — v1 delivers one self-contained executable per entry.** The flattening
that removed `entrypoints` also removed the shape that could express "executable
but not a command", so an in-package helper cannot be made executable. Rather
than reopen "do we trust an archive's mode bits", v1 states the limit.

## In Scope

- The `ac_bot_cli_tool` table, its record, protocol and implementation.
- `CliToolService`: install, delete, list, `replace_all`, drift.
- The OSS tool store: the bytes the platform keeps per bot.
- The ARCA delivery: name-addressed install / delete / list / batch, each one
  call to the engine.
- The teclaw delivery: `cli_tools` refs on the composed artifact, and promotion
  as a stage-scoped **copy** of the OSS objects.
- The `/cli-tools` management API and its `api/` contract.
- The `cli_tools` materialiser, delegating to the service.
- The capability unlock and content-dependent `subpath` validation.
- Schema, user manual, teclaw contract reconciliation, work-items updates —
  including the §3.7 `PATH` correction (D-8).

## Out of Scope

- **The default-skillset tool-usage skill.** Owner's call: a manual step outside
  this session, to be improved later.
- **Putting the tools directory on the agent's `PATH`.** Engine-side, and
  deferred with D-8's cost recorded.
- **`bcs-cli` as the first consumer.** The explicit next step: retire the
  singlebox script that hand-places it.
- **Multi-architecture sources.** `linux/amd64` only, one URL per tool (X3).
- **Package-manager installs.** Imperative, and already routed to `script`.
- **A console UI for CLI tools.** The API is the surface this item delivers.
- **`engine_config`**, still out on the X2/T3 decision.

## Open Questions

None blocking. One thing to settle with the engine owners as they implement,
which no platform code depends on:

- **The concrete directory name.** The ARCA-side proposal is
  `/home/admin/.openclaw/cli`. Since the backend never names it, this is the
  engine's constant and the default-skillset skill's to describe; the platform
  contract is unaffected by the choice.

## Revision history

| | What changed, and why |
| --- | --- |
| **rev 1** | ARCA arm as a platform-composed start-command prologue; `PRE_CONTAINER`; a §2.6 exception. |
| **rev 2** | Prologue withdrawn. `cli_tools` as a third domain on `EngineRuntimeProjection`; back to `ON_CONTAINER`; exception removed. |
| **rev 3** | Projection withdrawn — a tool is not platform state a runtime must be told about. `cli_tools` as `resources` plus an executable bit, no platform record on ARCA. |
| **rev 4** | Platform-managed on the owner's decision: metadata table, one core service every caller delegates to, engine CRUD plus batch, a management API, no projection. |
| **rev 5** | The engine owns the directory and the protocol is **name-addressed** (D-3), so the `tools/` namespace and every isolation mechanism are dropped (D-5). `cli_tools` is always platform-managed, independent of the teclaw switch, like `mcp` (D-6). The teclaw arm gains the **promotion gather** — GET from the engine, stage-scoped OSS, artifact refs — which is why the protocol needs `get`. `PATH` is replaced by a default-skillset skill for v1, with the cost and the §3.7 correction written down (D-8). |
| **rev 6** | **The platform stores the bytes in OSS at install time** (D-4) — rev 5 had no answer for what a teclaw artifact references on a live update or a manifest apply. The artifact becomes teclaw's delivery with no separate engine upload (D-5); promotion becomes a stage-scoped copy rather than a download, so the protocol's `get` is no longer needed; and the phase settles as ARCA `ON_CONTAINER` / teclaw `PRE_CONTAINER` under either switch position (D-6), correcting rev 5's claim that no category-specific code was required. |
| **rev 7** | The engine's `install` owns the executable bit (D-5). Rev 6 had the platform do a device write then a `chmod` through the general shell channel; withdrawn in review — once `install` carries the semantics, a platform-side `chmod` is a second implementation of the engine's own job, and it took a user-supplied name through a shell. No `chmod`, no shell command, no quoting. |
