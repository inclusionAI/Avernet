# `cli_tools` — Platform-Managed Command-Line Tools (W9)

Work item W9 of `docs/bot-config-manifest/work-items.zh-CN.md` §5, issue #1477.
Plan: `plan.md` in this directory.

> **Revision 4 (2026-09-03).** CLI tools are **platform-managed**: platform-owned
> metadata in its own table, engine-side protocols for upload / delete / list
> plus a batch operation, and platform APIs that delegate to the engine. Manifest
> apply and creation-time provisioning call the *same core component* those APIs
> call — never the HTTP endpoints. No projection component. Revision history at
> the end.

## Summary

A bot owner declares a command-line tool in the manifest, or manages one
directly through a CLI-tools API; either way the tool ends up on the model's
`PATH` inside the container, and the platform owns the record of what is
installed. The platform does all the dangerous work — fetch, mandatory `sha256`
enforcement, unpack, selection of the one declared file, architecture
verification — so what reaches an engine is **one executable file** plus the
metadata needed to place and replace it.

This is the last unshipped category of the config manifest, and the first one
with a management surface of its own.

## Motivation

`bcs-cli` is the proof the feature is needed: today a binary is placed by hand
by a singlebox script (`scripts/modules/bots.sh:954` prepends its directory to
the openclaw gateway's `PATH`) and taught to the model by a hand-written
`SKILL.md`. That pattern works and cannot be offered to a customer — there is no
declarative way to say "this bot has this tool", so every tool is a bespoke
change to platform scripts.

**Why platform-managed rather than "just files".** A tool is not a workspace
file the user browses and edits. It has metadata the platform must keep to do
its job at all: the `sha256` it was pinned to, which archive member was selected,
the `md5` of the delivered bytes, the version on record. And it needs an
executable bit no file-write path sets. Once the platform owns that record, the
record has to be the only way in — a tool created or deleted through the generic
resources API would leave the platform's metadata describing something that is no
longer there.

§4's investigation (X3, now closed) confirmed there is **no existing CLI
mechanism being duplicated here**: every `bcs-cli` reference in the tree is
singlebox orchestration, and no delivery path anywhere handles the executable
bit.

## What the code allows, checked before writing this

1. **`identity` is the precedent for this whole shape.** It is platform-managed,
   it has its own service (`core/services/identity.py`), its own API, its own
   namespace (`IDENTITY_NS`), and the manifest's `identity` materialiser
   delegates to that service rather than reimplementing it. `cli_tools` is the
   same shape plus a table and an executable bit.
2. **The namespace list is the designed extension point.**
   `core/config_compose/teclaw_paths.py` declares exactly three —
   `WORKSPACE_NS = "workspace"`, `IDENTITY_NS = "identity"`, `CONFIG_NS` — and
   `to_engine_relative` validates against that tuple. A fourth is an addition,
   not a workaround.
3. **The resources surface is structurally confined to `workspace/`.** Every
   resources call goes through `_logical(path)` → `workspace/<rel>`, and
   `build_workspace_mapper` **raises** on any logical path not so prefixed — "a
   non-namespace input is a programming error and fails loudly rather than
   silently passing through" (`resource_addressing.py:50`). `safe_workspace_path`
   rejects every `..` segment outright. So anything outside `workspace/` is
   unreachable from that API by construction.
4. **`write_file` cannot set a mode**, on any transport: the BaaS device uploads
   through `POST /api/file/upload` with a `target_path` and no mode field, and
   `DeviceFileSystem` exposes no `chmod`.
5. **The platform already runs commands in live ARCA containers, as routine
   business.** `BaasService.exec_command_on_bot` is used by
   `baas_container_init.py` (bootstrap, engine install, supervisor and dir setup,
   service start, watchdog) and by `baas_codefuse_writer.py`, through the
   ready-made helper `execute_baas_shell_command(...)` which returns a
   `CommandResult` with an exit code and stderr. The executable bit costs no new
   channel.
6. **The table pattern is established.** `ac_bot_startup_script`
   (`core/bot_startup_script/repository/models.py`) is the model: ORM plus a
   pydantic record, `env` column, tenant guard, `UniqueConstraint`, registered by
   a side-effect import in `core/schema.py`, with protocol and implementation
   split under `core/repository/{protocols,implementations}/bot/`.
7. **`${BOT_ARCH}` already resolves to `amd64`** (`schema/placeholders.py`),
   contrary to the W9 progress table — W1 shipped it. Only the ELF verification
   half of that row is outstanding.
8. **The schema already parses a `cli_tools` entry** and enforces the mandatory
   `digest` (`schema/entries.py`); `fetch/limits.py` already carries the 200 MiB
   width; `APPLY_ORDER` already places `cli_tools` at `ON_CONTAINER`. The
   category is refused today only by the capability gate.

## The design

**One core component does the work. Everything else calls it.**

```text
      POST/GET/DELETE .../cli-tools          manifest apply / W13 creation
                   │                                      │
                   └──────────────┬───────────────────────┘
                                  ▼
                          CliToolService              ← the only thing that
                        (fetch · verify · place        does the work
                         · record · replace)
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
            ac_bot_cli_tool              engine CLI protocol
             (metadata)              upload · delete · list · replace_all
```

- **Metadata is the platform's.** `ac_bot_cli_tool` holds one row per tool per
  bot: the pinned `digest`, the selected `subpath`, the delivered `md5`, the
  `version`, size and audit stamps. It is the answer to "what does this bot
  have", and it is what makes replacement and removal decidable.
- **The engine places bytes and owns the filesystem.** New engine-side
  operations: upload one tool, delete one tool, list tools, and a **batch**
  operation for the full-override semantics the manifest needs.
- **The API delegates; it does not implement.** The HTTP routes are a thin
  adapter over `CliToolService`.
- **Manifest apply is just another caller.** The `cli_tools` materialiser calls
  `CliToolService`, not the HTTP endpoints — backend code has no reason to make
  an HTTP request to itself. A manifest apply is a **full override**: the
  declared set becomes the installed set, exactly as every other category
  behaves under §3.2.
- **No projection component.** Skills and MCP project because they are platform
  state a runtime must be *told about* and reconciled against. A tool is
  installed or it is not; the engine's own list is the truth, and the platform's
  table is the record of what it asked for.

### CLI tools are invisible to the resources API — structurally

The exclusion is **placement, not filtering**: tools live in their own
namespace, a sibling of `workspace/`, so the resources surface cannot address
them. `build_workspace_mapper` raises on a non-`workspace/` logical path and
`safe_workspace_path` refuses `..`, which together mean there is no path a
caller can send to a resources endpoint that reaches a tool.

This is deliberately not a filter. A filter is a rule every present and future
endpoint has to remember, and the codebase already shows how that decays:
`_HIDDEN_DIRNAMES` hides a system directory from the **root listing only**
(`resource_file_service.py:391` guards on `not path`), so naming one explicitly
still lists it today. Structural exclusion has no such gap and needs no
maintenance.

Should any placement ever have to live under `workspace/`, the fallback is the
existing three-part policy — `_HIDDEN_DIRNAMES` for the root listing and the
search/download walks, `is_readonly` / `is_write_forbidden` for create and
delete, and a new guard for the endpoints that take an explicit `path`. The
spec's position is that we should not need it.

## User Stories

- As a bot owner, I declare a tool with a URL and a `sha256` in my manifest, and
  the model can invoke it by name in the container.
- As a bot owner, I upload, list and delete tools through the CLI-tools API
  without writing a manifest at all.
- As a bot owner, I declare a tool inside a `.tar.gz` by naming its `subpath`,
  and only that one file is delivered.
- As a bot owner, my manifest apply makes the installed set equal the declared
  set: tools I removed from the document are gone from the container.
- As a bot owner, I can ask what tools a bot has and get the platform's record —
  name, version, digest, when it was installed and by whom.
- As a bot owner, I declare a tool built for the wrong architecture and the apply
  report tells me so, instead of the model hitting `exec format error` mid-task.
- As a bot owner, I omit `digest` and the `PUT` is refused, because the platform
  will not distribute an unpinned executable on my behalf.
- As a bot owner browsing my workspace files, I never see a CLI tool there, and I
  cannot delete one through the file API and leave the platform's record stale.
- As the teclaw engine owner, the artifact tells me exactly which single file to
  place per tool, and an `md5` that says whether I already have it.

## Acceptance Criteria

### The metadata table

- [ ] `ac_bot_cli_tool` exists with one row per `(env, bot_id, name)` —
      uniqueness enforced by constraint, so a duplicate command name is
      unwritable rather than merely validated.
- [ ] A row carries: the declared `source` and `digest`, the selected `subpath`,
      the platform-computed `md5`, `size_bytes`, `version`, the `modifier`, and
      create/modify timestamps.
- [ ] A row records **who installed it** — `manifest` or a user id — so a
      manifest apply's full override can tell its own tools from an API-installed
      one, and the report can say what it replaced.
- [ ] The ORM model registers through the side-effect import in
      `core/schema.py`, carries the `env` column and the tenant guard, and splits
      protocol from implementation under `core/repository/…/bot/`.

### The core service

- [ ] `CliToolService` is the single component that installs, deletes and lists
      tools. Both the HTTP adapter and the manifest materialiser call it; neither
      reimplements any part of it.
- [ ] `install` fetches under the `cli_tools` width, enforces the declared
      `sha256` over the fetched source object, unpacks an archive under W2's
      guards, selects the one `subpath` member, verifies the ELF header, computes
      the `md5`, delegates placement to the engine, and writes the metadata row —
      in that order, with nothing recorded for a step that failed.
- [ ] `replace_all` implements the full-override semantics: the given set becomes
      the installed set, tools not in it are removed, and the outcome is reported
      per tool. It is one call, so a partial failure is reported as such rather
      than leaving the caller to reconcile.
- [ ] `list` answers from the platform's table, and a **reconcile-style read**
      can compare it against the engine's own list so drift is observable rather
      than assumed away.
- [ ] Nothing in the service branches on engine type; the engine difference lives
      behind the port it calls.

### The engine protocol

- [ ] Engine-side operations exist for **upload one**, **delete one**, **list**,
      and a **batch** operation sufficient for full override (replace the set, or
      delete all).
- [ ] Uploading sets the executable bit. On the ARCA family that is the file
      write followed by a `chmod +x` through `execute_baas_shell_command`; a
      failure to set the bit **fails the entry** with the command's stderr,
      rather than leaving a file the model cannot run.
- [ ] The placed file is exposed on the agent process's `PATH` (see the open
      question) and the user never sees a physical path.
- [ ] On teclaw the artifact carries `cli_tools` refs plus the existing
      `ownership.cli_tools`; the engine places them on receipt, per
      `teclaw-cli-contract.zh-CN.md`.
- [ ] The batch operation is what a manifest apply uses, so a full override is
      not N round trips when the engine can take one.

### The management API

- [ ] `POST` / `GET` / `DELETE` under `/openapi/v1/bots/{bot_id}/cli-tools`
      install, list and remove a tool, each delegating to `CliToolService`.
- [ ] Each route carries its own `ADMISSION` line — the authorization scaffold
      refuses an unregistered route — and is collaborator-scoped the way the
      config-manifest routes are: MEMBER to read, ADMIN to write.
- [ ] The service API contract lives under `api/` and is registered in the
      consistency `_PAIRS`; `core` never imports that layer.
- [ ] A tool installed through the API is visible to a subsequent manifest apply
      as something the override replaces or removes, and the report says which.

### Manifest apply

- [ ] The `cli_tools` materialiser calls `CliToolService.replace_all` and adds no
      fetch, verification or placement logic of its own.
- [ ] `cli_tools` is **`ON_CONTAINER` on ARCA**, where `APPLY_ORDER` already has
      it. On teclaw `TeclawDelivery.phase_of` re-phases every non-script
      construct to `PRE_CONTAINER` under the platform-managed switch, and
      `cli_tools` inherits that generically — no category-specific code, and
      `order.py` is not modified.
- [ ] A `PUT` takes effect immediately on both families, like every other
      category. **No §2.6 exception.**
- [ ] W13 creation provisions tools through the same service call, not a second
      path.
- [ ] Convergence is on the whole delivery-relevant declaration — `digest`
      **and** `subpath` together, read from the metadata row. `version` is
      metadata and never affects it.
- [ ] An empty declared `cli_tools: []` removes every tool; an undeclared
      `cli_tools` is untouched, per §3.2.
- [ ] **Creation cleanup:** when a W13 creation job fails after tools were
      installed but before a bot exists, the rows and the placed files are
      removed with the rest of that bot's manifest state. Otherwise the table
      keeps rows for a `bot_id` that was never created.

### Isolation from the resources surface

- [ ] Tools live in their own namespace outside `workspace/`, registered in
      `teclaw_paths.py` alongside the existing three and accepted by
      `to_engine_relative`.
- [ ] A test asserts the resources API **cannot reach a tool**: every
      resources path is `workspace/`-prefixed, `build_workspace_mapper` raises
      on anything else, and `safe_workspace_path` refuses `..` — so no crafted
      `path` value addresses the tools namespace.
- [ ] `list_resources`, `stat`, `download`, `download-dir`, `preview`, `search`
      and the delete/upload routes are **unchanged**: no filter is added,
      because none is needed.
- [ ] A test asserts a tool never appears in a resources listing for a bot that
      has one installed.

### Admission and capability

- [ ] `cli_tools` is **supported** in the capability resolver for the ARCA family
      and for teclaw, and stays unsupported for desktop bots and unknown engines,
      with the existing reasons.
- [ ] The `cli_tools` rows are removed from the "not yet open" gate tables in
      `manifest-schema` §7 and work-items §5 W1.
- [ ] **Content-dependent `subpath` validation lives in the service, not in W1**:
      after unpack, the selected `subpath` must exist, must be a regular file,
      and must still resolve inside the unpack tree after symlink resolution. W1
      keeps the syntactic half.
- [ ] `digest` remains mandatory for every non-git form, refused at `PUT` as
      today, and equally refused by the API.

### Nothing else moves

- [ ] No deploy-path change: `build_start_command`, `BotDeployContext` and
      `_compose_start_command` are untouched, so every bot's composed start
      command is byte-identical and #935's assertion is unedited.
- [ ] No `core/skill_center/*` change and no runtime-projection change.
- [ ] `engine_config` stays unsupported with its existing reason; no other
      category changes behaviour.
- [ ] Every existing manifest, artifact, resources, creation and deploy test
      passes with assertions untouched.

## Decisions

**D-1 — CLI tools are platform-managed, with platform-owned metadata.** A tool
carries state the platform must keep to do its job — the pin, the selected
member, the delivered hash, the version — so there is a table, and the table is
the record of what was asked for.

**D-2 — One core component; every caller delegates to it.** The HTTP API and
manifest apply are two callers of `CliToolService`. Backend code never calls its
own HTTP endpoints.

**D-3 — No projection component.** Skills and MCP project because they are
platform state a runtime must be told about and reconciled against. A tool is
installed or not. Rejected in review (PR #1870): a `cli_tools` domain on
`EngineRuntimeProjection`.

**D-4 — The engine gets CRUD plus a batch operation.** Full override is the
manifest's semantics for every category, so the protocol has to express "make
the set equal this" without N round trips.

**D-5 — Isolation from the resources API is structural, not a filter.** Tools
live outside the `workspace/` namespace the resources surface is confined to.
`_HIDDEN_DIRNAMES` is the fallback and is explicitly *not* the primary
mechanism: it guards the root listing only, which is the kind of gap a filter
accumulates.

**D-6 — Convergence keys on `digest` *and* `subpath`.** `subpath` is a
**source-side** path — which member of the fetched archive is the tool — never a
target path. The same archive with `subpath` moved from `bin/old` to `bin/new`
delivers a different file under the same command name; keying on `digest` alone
would report `unchanged` and leave the old binary answering to it.

**D-7 — `SCHEMA_VERSION` stays 4.** Settled with the teclaw owner on 2026-08-31
and pinned by a test. The accepted cost: `schema_version` no longer tracks this
contract's evolution, so "does this artifact carry `cli_tools`?" is answered by
probing for the field.

**D-8 — v1 delivers one self-contained executable per entry.** The flattening
that removed `entrypoints` also removed the shape that could express "executable
but not a command", so an in-package helper cannot be made executable. Rather
than reopen "do we trust an archive's mode bits", v1 states the limit.

**D-9 — `PUT` takes effect immediately on both families.** No §2.6 exception for
this category.

## In Scope

- The `ac_bot_cli_tool` table, its record, protocol and implementation.
- `CliToolService`: install, delete, list, `replace_all`, and the drift read.
- The engine-side operations (upload / delete / list / batch) and the ARCA
  implementation: file write plus `chmod +x` through the existing helper.
- The teclaw arm: artifact `cli_tools` refs (`ownership.cli_tools` already
  exists).
- The `/cli-tools` management API and its `api/` contract.
- The `cli_tools` materialiser, delegating to the service.
- The tools namespace and the tests that pin resources-surface isolation.
- The capability unlock and content-dependent `subpath` validation.
- Schema, user manual, teclaw contract reconciliation, work-items updates.

## Out of Scope

- **`bcs-cli` as the first consumer.** The explicit next step: retire the
  singlebox script that hand-places it and onboard it through the manifest.
- **The default-skillset tool-usage skill.** `SkillSetService` already has the
  mechanism; wiring it is follow-up work.
- **Multi-architecture sources.** `linux/amd64` only, one URL per tool (X3).
- **Package-manager installs.** Imperative, and already routed to `script`.
- **A console UI for CLI tools.** The API is the surface this item delivers.
- **`engine_config`**, still out on the X2/T3 decision.

## Open Questions

One, and it is a lookup rather than a design decision:

- **Which directory puts a tool on the agent process's `PATH`, per deploy
  runtime.** `/home/admin/bin` exists in ARCA containers, but its scripts are
  invoked by absolute path (`baas_container_init.py:69,145`), so the tree does
  not say whether it is on the agent's `PATH`. Confirming it — or naming a
  directory and having the engine expose it — is a task, and it blocks nothing
  else in the plan.

## Revision history

| | What changed, and why |
| --- | --- |
| **rev 1** | ARCA arm as a platform-composed start-command prologue; `cli_tools` re-phased to `PRE_CONTAINER`; a §2.6 exception making it effective only at the next provisioning. |
| **rev 2** | Prologue withdrawn as an arrangement rather than a protocol. `cli_tools` as a third domain on `EngineRuntimeProjection`; back to `ON_CONTAINER`; §2.6 exception removed. |
| **rev 3** | Projection withdrawn: a tool is not platform state a runtime must be told about. `cli_tools` as `resources` plus an executable bit, with no platform-side record on ARCA. |
| **rev 4** | **Platform-managed, on the owner's decision.** Metadata gets its own table (D-1); one core service every caller delegates to, including manifest apply (D-2); engine-side CRUD plus a batch operation for full override (D-4); a management API; no projection (D-3). Rev 3's "no platform copy on ARCA" is replaced by the table. Isolation from the resources API is structural (D-5). |
