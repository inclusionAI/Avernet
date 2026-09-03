# `cli_tools` — Platform-Managed Command-Line Tools (W9)

Work item W9 of `docs/bot-config-manifest/work-items.zh-CN.md` §5, issue #1477.
Plan: `plan.md` in this directory.

> **Revision 5 (2026-09-03).** The engine owns the tools directory entirely —
> the backend never knows or addresses a physical path, and every CLI operation
> is by tool **name**. `cli_tools` is always platform-managed, like `mcp`, and
> does not depend on the teclaw switch. The teclaw arm gains what service-bot
> promotion needs: a GET from the engine, staged into OSS, referenced by the
> artifact. Revision history at the end.

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
3. **`TeclawFilePromotion` is the promotion gather this feature must join.** At
   a promotion boundary (draft→verify, verify→publish) the backend "must **read
   the source container's files from the engine**, write each to a
   **stage-scoped OSS key**, and return `{store, path}` refs to embed in the
   composed `BotConfigArtifact` for the new stage". It sweeps two namespaces
   today, `workspace` and `identity`, through `DeviceFileSystem`.
4. **The platform already runs commands in live ARCA containers, as routine
   business.** `BaasService.exec_command_on_bot` drives
   `baas_container_init.py` (bootstrap, engine install, supervisor setup,
   service start, watchdog) and `baas_codefuse_writer.py`, through
   `execute_baas_shell_command(...)` which returns a `CommandResult` with an
   exit code and stderr. The executable bit costs no new channel.
5. **`write_file` cannot set a mode** on any transport: the BaaS device uploads
   through `POST /api/file/upload` with a `target_path` and no mode field, and
   `DeviceFileSystem` exposes no `chmod`.
6. **The table pattern is established.** `ac_bot_startup_script` is the model:
   ORM plus a pydantic record, `env` column, tenant guard, `UniqueConstraint`,
   registered by a side-effect import in `core/schema.py`, protocol and
   implementation split under `core/repository/{protocols,implementations}/bot/`.
7. **`${BOT_ARCH}` already resolves to `amd64`** (`schema/placeholders.py`) —
   W1 shipped it, contrary to the W9 progress table. Only the ELF verification
   half of that row is outstanding.
8. **The schema already parses a `cli_tools` entry** and enforces the mandatory
   `digest`; `fetch/limits.py` carries the 200 MiB width; `APPLY_ORDER` places
   `cli_tools` at `ON_CONTAINER`. The category is refused today only by the
   capability gate.

## The design

**One core component does the work. Everything else calls it. The engine owns
the filesystem.**

```text
      POST/GET/DELETE .../cli-tools          manifest apply / W13 creation
                   │                                      │
                   └──────────────┬───────────────────────┘
                                  ▼
                          CliToolService              ← the only thing that
                        (fetch · verify · record       does the work
                         · delegate · replace)
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
            ac_bot_cli_tool              engine CLI protocol
             (metadata)              install · delete · list · get
                                       · replace_all  — all by NAME
```

- **The backend never knows where a tool lives.** Every operation addresses a
  tool by its `name`. The engine chooses the directory, sets the executable bit,
  and exposes it to the agent. No physical path crosses the boundary in either
  direction, so there is no path for the backend to compute, store, validate or
  leak.
- **Metadata is the platform's.** `ac_bot_cli_tool` holds one row per tool per
  bot: the pinned `digest`, the selected `subpath`, the delivered `md5`, the
  `version`, size and audit stamps. It answers "what does this bot have", and it
  is what makes replacement and removal decidable.
- **The API delegates; it does not implement.** The HTTP routes are a thin
  adapter over `CliToolService`.
- **Manifest apply is just another caller.** The `cli_tools` materialiser calls
  `CliToolService`, not the HTTP endpoints — backend code has no reason to make
  an HTTP request to itself. A manifest apply is a **full override**: the
  declared set becomes the installed set, exactly as every other category
  behaves under §3.2.
- **Always platform-managed, like `mcp`.** `cli_tools` does not depend on the
  `teclaw_platform_managed` switch: the platform is the source of truth for the
  installed set on both families, always.
- **No projection component.** Skills and MCP project because they are platform
  state a runtime must be *told about* and reconciled against. A tool is
  installed or it is not.

### Why the ELF header is checked

ELF — Executable and Linkable Format — is the binary format Linux executables
use. Every ELF file opens with a fixed header whose first two fields are all
this needs: the magic bytes `\x7fELF` at offset 0, and `e_machine` at offset 18
(`0x3E` = x86-64, `0xB7` = arm64). Two field reads, no parsing library.

It is checked **in addition to** the mandatory `sha256` because the two answer
different questions. `digest` answers *"are these the bytes you asked for"* —
the supply chain. The ELF check answers *"can this machine run them"* — the
architecture. An arm64 build, a mislabelled `.zip`, or an HTML error page a CDN
served with a 200 all have perfectly valid digests.

Without it the failure lands at the worst moment: the apply reports success, the
tool sits in the container looking installed, and the model discovers
`cannot execute binary file: Exec format error` mid-task. With it, the apply
report says which architecture was found and which the fleet runs, at install
time, while the owner is still there to fix the URL. It also pairs with
`${BOT_ARCH}`: a manifest may write `…/mycli-linux-${BOT_ARCH}`, and this is
what verifies the URL served what the substitution asked for.

### The teclaw arm and service-bot promotion

teclaw is an external engine and the backend does not know where its tools live
— which is exactly why the protocol needs a **GET**. At a promotion boundary the
backend gathers the tools *from the engine*, writes them to stage-scoped OSS
keys, and composes an artifact whose `cli_tools` refs point at those objects.
This is the same shape `TeclawFilePromotion` already performs for `workspace`
and `identity`, with one difference that follows from the design: those two are
swept through `DeviceFileSystem` by namespace-relative path, whereas tools are
fetched through the engine's CLI GET **by name**, because their directory is not
the backend's to walk.

### Nothing to isolate from the resources API

Because the backend never addresses a tool by path, and the engine keeps tools
outside the workspace it serves to the file APIs, CLI tools simply are not part
of the resources surface — there is nothing to hide and no filter to add. The
resources endpoints are untouched, and a test asserts a bot's installed tool
never appears in its resources listing.

## User Stories

- As a bot owner, I declare a tool with a URL and a `sha256` in my manifest, and
  the model can invoke it in the container.
- As a bot owner, I install, list and delete tools through the CLI-tools API
  without writing a manifest at all.
- As a bot owner, I declare a tool inside a `.tar.gz` by naming its `subpath`,
  and only that one file is delivered.
- As a bot owner, my manifest apply makes the installed set equal the declared
  set: tools I removed from the document are gone from the container.
- As a bot owner, I can ask what tools a bot has and get the platform's record —
  name, version, digest, when it was installed and by whom.
- As a bot owner promoting a service bot from draft to verify to online, the
  tools installed on the source container come with it.
- As a bot owner, I declare a tool built for the wrong architecture and the apply
  report tells me so, instead of the model hitting `exec format error` mid-task.
- As a bot owner, I omit `digest` and the request is refused, because the
  platform will not distribute an unpinned executable on my behalf.
- As a backend engineer, I never see or construct a container path for a tool, so
  I cannot get one wrong.

## Acceptance Criteria

### The metadata table

- [ ] `ac_bot_cli_tool` exists with one row per `(env, bot_id, name)` —
      uniqueness enforced by constraint, so a duplicate command name is
      unwritable rather than merely validated.
- [ ] A row carries the declared `source` and `digest`, the selected `subpath`,
      the platform-computed `md5`, `size_bytes`, `version`, `installed_by`
      (`manifest` or a user id), `modifier` and timestamps.
- [ ] **No column holds a container path.** The engine owns placement; the row
      identifies a tool by `name`.
- [ ] The ORM model registers through the side-effect import in
      `core/schema.py`, carries the `env` column and the tenant guard, and splits
      protocol from implementation under `core/repository/…/bot/`.

### The core service

- [ ] `CliToolService` is the single component that installs, deletes, lists and
      replaces tools. Both the HTTP adapter and the manifest materialiser call
      it; neither reimplements any part of it.
- [ ] `install` fetches under the `cli_tools` width, enforces the declared
      `sha256` over the fetched source object, unpacks an archive under W2's
      guards, selects the one `subpath` member, verifies the ELF header (below),
      computes
      the `md5`, calls the engine, and writes the metadata row — in that order,
      recording nothing for a step that failed.
- [ ] `replace_all` implements full override: the given set becomes the installed
      set, tools not in it are removed, and the outcome is reported per tool.
      Removals are computed **from the table**, so a tool the platform installed
      is removed even if the engine's view has drifted.
- [ ] `list` answers from the platform's table; `drift` compares it against the
      engine's own list so divergence is observable rather than assumed away.
- [ ] Nothing in the service branches on engine type, and nothing in it composes
      a filesystem path.

### The engine protocol

- [ ] Engine-side operations exist for **install one**, **delete one**,
      **list**, **get one**, and a **batch** operation sufficient for full
      override. Every one addresses a tool by `name`.
- [ ] `install` places the file and makes it executable. On the ARCA family that
      is the file write followed by a `chmod` through
      `execute_baas_shell_command`; a failure to set the bit **fails the entry**
      with the command's stderr, rather than leaving a file the model cannot run.
- [ ] `get` returns a tool's bytes by name — what the promotion gather needs from
      an engine whose directory the backend does not know.
- [ ] The engine, not the backend, decides the directory and how the agent
      reaches the tool.
- [ ] The batch operation is what a manifest apply uses, so a full override is
      not N round trips when the engine can take one.

### teclaw promotion

- [ ] At a promotion boundary (draft→verify, verify→publish) the backend gathers
      each installed tool from the engine by name, writes it to a **stage-scoped
      OSS key** under the same layout `TeclawFilePromotion` uses, and the composed
      artifact's `cli_tools` refs point at those objects.
- [ ] Each ref is `{name, store, path, md5, version}` per `cliToolRef`, with
      `md5` and `version` read from the metadata table.
- [ ] Draft and verify snapshots do not share objects, matching the existing
      stage-scoping rule.
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
- [ ] `cli_tools` is **`ON_CONTAINER` on both families and does not depend on the
      `teclaw_platform_managed` switch** — it is always platform-managed, like
      `mcp`. Delivery is a live engine call, so it needs the container.
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

**D-4 — The engine gets install / delete / list / get plus a batch operation.**
`get` exists for the promotion gather; batch exists because full override is the
manifest's semantics for every category and should not cost N round trips.

**D-5 — Nothing is needed to hide tools from the resources API.** Earlier
revisions proposed a `tools/` namespace and, before that, a filter. Neither is
required once the backend never addresses a tool by path and the engine keeps
tools outside the workspace it serves.

**D-6 — Always platform-managed, independent of the teclaw switch**, exactly as
`mcp` is. `ownership.cli_tools` is `platform` on every compose.

**D-7 — Convergence keys on `digest` *and* `subpath`.** `subpath` is a
**source-side** path — which member of the fetched archive is the tool — never a
target path. The same archive with `subpath` moved from `bin/old` to `bin/new`
delivers a different file under the same command name; keying on `digest` alone
would report `unchanged` and leave the old binary answering to it.

**D-8 — The agent finds tools through a default-skillset skill, not `PATH`, in
v1.** Accepted on the owner's call, with the cost stated: the model invokes a
tool by absolute path, so `mycli --help` does not work and every invocation
depends on the skill being read; a script that shells out to a sibling tool will
not find it either. What makes deferring safe is that placement is engine-side —
adding the directory to `PATH` later changes nothing in the manifest schema, the
API, the table or the artifact contract. **`manifest-schema` §3.7's promise that
the platform guarantees the tool is on the agent's `PATH` is therefore no longer
true and must be corrected**, not left to be discovered.

**D-9 — `SCHEMA_VERSION` stays 4.** Settled with the teclaw owner on 2026-08-31
and pinned by a test. `schema_version` no longer tracks this contract's
evolution, so "does this artifact carry `cli_tools`?" is answered by probing.

**D-10 — v1 delivers one self-contained executable per entry.** The flattening
that removed `entrypoints` also removed the shape that could express "executable
but not a command", so an in-package helper cannot be made executable. Rather
than reopen "do we trust an archive's mode bits", v1 states the limit.

## In Scope

- The `ac_bot_cli_tool` table, its record, protocol and implementation.
- `CliToolService`: install, delete, list, `replace_all`, drift.
- The name-addressed engine protocol and the ARCA implementation (file write
  plus `chmod` through the existing helper).
- The teclaw arm: the promotion gather into stage-scoped OSS and the artifact's
  `cli_tools` refs.
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
