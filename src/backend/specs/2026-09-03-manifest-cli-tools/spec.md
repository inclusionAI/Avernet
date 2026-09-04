# `cli_tools` — Platform-Managed Command-Line Tools (W9)

Work item W9 of `docs/bot-config-manifest/work-items.zh-CN.md` §5, issue #1477.
Plan: `plan.md` in this directory.

> **Revision 8 (2026-09-04).** The delivery port gains a **whole-set** operation
> beside the single-tool ones: `replace_all` means "this is the entire set,
> replace what you have", and it is what a manifest apply calls — one engine
> round trip instead of one per tool, and no intermediate state on the wire.
> The families differ only in how the set is transmitted: teclaw composes it
> into the artifact, ARCA calls a new engine **replacement** endpoint that
> reports per-name status. Because teclaw's port composes *from* the table,
> **platform state is written before the port is called** (D-14), which retires
> the closing redeliver `BotCliToolService` was making and moves the family
> knowledge into the port where it belongs. Rev 7's summary follows.
>
> **Revision 7.** The engine's `install` owns the executable bit —
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
- **Delivery differs by family, and only in how the desired set is
  transmitted.** Both families implement one port with two shapes: `install` /
  `delete` for a single live edit, and `replace_all` for "this is the whole
  set". A one-tool edit must not re-send the rest, and a full override should
  say so in one call rather than N.
  - **ARCA** — one call to the engine, by name for a single edit and to a
    **replacement** endpoint carrying the desired set for an apply. The engine
    owns placement, the executable bit and exposure to the agent; the
    replacement response reports **per name**, so the apply report keeps its
    per-entry shape (D-15). Needs the container, so `ON_CONTAINER`.
  - **teclaw** — the composed artifact *is* the transmission, so all three
    methods do the same thing: compose the bot's artifact from the table and
    deliver it once, exactly as `mcp` is composed and delivered. No separate
    engine upload call, and no per-tool call — a set of any size is one
    artifact.
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

- [x] At a promotion boundary (draft→verify, verify→publish) the tool objects are
      **copied to the new stage-scoped prefix** under the layout
      `TeclawFilePromotion` already builds, and the composed artifact's refs
      point at the new objects.
- [x] **Nothing is downloaded from the engine.** The platform's OSS copy is the
      source, so promotion costs a server-side copy rather than a round trip
      through the container.
- [x] Each ref is `{name, store, path, md5, version}` per `cliToolRef`, with
      `md5` and `version` read from the metadata table.
- [x] Draft and verify snapshots do not share objects.
- [x] A promotion of a bot with no tools composes an artifact byte-identical to
      today's, with `cli_tools` omitted.

### The management API

- [x] `POST` / `GET` / `DELETE` under `/openapi/v1/bots/{bot_id}/cli-tools`
      install, list and remove a tool, each delegating to `CliToolService`.
- [x] Each route carries its own `ADMISSION` line and is collaborator-scoped the
      way the config-manifest routes are: MEMBER to read, ADMIN to write.
- [x] The service API contract lives under `api/` and is registered in the
      consistency `_PAIRS`; `core` never imports that layer.
- [x] No response exposes a container path.
- [x] A tool installed through the API is visible to a subsequent manifest apply
      as something the override replaces or removes, and the report says which.
- [ ] A single install or remove whose port call is refused **rolls its row
      back**, so a non-2xx is never followed by a `GET` that lists the tool
      (D-14).
- [ ] The push to a running teclaw container happens **in the port**;
      `BotCliToolService` no longer asks which family a bot is on.

### Manifest apply

- [x] The `cli_tools` materialiser calls `CliToolService.replace_all` and adds no
      fetch, verification or placement logic of its own.
- [ ] `replace_all` reaches the engine **once**, through the port's whole-set
      operation, whatever the size of the declared set — and not at all when
      every declaration already converges.
- [ ] Platform state is written before the port is called, and every name the
      family did not confirm is rolled back — the refused ones on a per-name
      answer, all of them on a call that did not complete, including the rows
      dropped for removal (D-14).
- [ ] A teclaw apply still makes exactly **one** artifact push, from
      `TeclawDelivery.finish`; the `cli_tools` port does not push a second one
      mid-apply.
- [x] `cli_tools` is **`ON_CONTAINER` on ARCA and `PRE_CONTAINER` on teclaw,
      under either switch position** — a category-specific rule in the teclaw
      strategy's `phase_of`, because the generic re-phasing keys on the switch
      and this category must not. `order.py` still carries the ARCA reading and
      is not modified.
- [x] A test pins that a teclaw creation with declared tools has them in its
      **first** artifact, under both switch positions.
- [x] `ownership.cli_tools` is `platform` on every compose, for the same reason
      `mcp` is.
- [x] A `PUT` takes effect immediately on both families. **No §2.6 exception.**
- [x] W13 creation provisions tools through the same service call.
- [x] Convergence is on `digest` **and** `subpath` together, read from the
      metadata row. `version` is metadata and never affects it.
- [x] An empty declared `cli_tools: []` removes every tool; an undeclared
      `cli_tools` is untouched, per §3.2.
- [x] **Creation cleanup:** when a W13 creation job fails after tools were
      installed but before a bot exists, the rows and the installed tools are
      removed with the rest of that bot's manifest state.

### Admission and capability

- [x] `cli_tools` is **supported** in the capability resolver for the ARCA family
      and for teclaw, and stays unsupported for desktop bots and unknown engines,
      with the existing reasons.
- [x] The `cli_tools` rows are removed from the "not yet open" gate tables in
      `manifest-schema` §7 and work-items §5 W1.
- [x] **Content-dependent `subpath` validation lives in the service**: after
      unpack, the selected member must exist, must be a regular file, and must
      still resolve inside the unpack tree after symlink resolution. W1 keeps the
      syntactic half.
- [x] `digest` remains mandatory for every non-git form, refused at `PUT` and
      equally refused by the API.

### Nothing else moves

- [x] No resources endpoint changes and no filter is added; a test asserts an
      installed tool never appears in a resources listing.
- [x] No deploy-path change beyond the two files the promotion needs
      (`teclaw_file_promotion.py`, and `publish_flow/provider_behavior.py` where its
      refs are merged): `build_start_command`, `BotDeployContext` and
      `_compose_start_command` are untouched, so every bot's composed start
      command is byte-identical and #935's assertion is unedited.
- [x] No `core/skill_center/*` change and no runtime-projection change.
- [x] `engine_config` stays unsupported with its existing reason.
- [~] Every existing test passes. Assertions were edited only where this work
      item *is* the change: the capability and schema cases that pinned
      `cli_tools` as refused now pin it as accepted, `teclaw_off_is_the_pre_w8_shape`
      loses `cli_tools` from ON_CONTAINER, the registry count goes five → six, and
      the surface's pinned operation counts gain three. Construction sites gain the
      new port. `tasks.md` Task 16 lists them.

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

**D-13 — The port carries a whole-set operation beside the single-tool ones.**
`install` and `delete` serve a live owner edit; `replace_all` serves a full
override and says "this is the entire set" in one call. Driving an apply
through the single-tool method instead would mean N engine round trips and N
intermediate states on the wire — and on teclaw those states are *wrong*, not
merely redundant, because `replace_all` removes every undeclared tool before it
installs, so the container would first receive an artifact that has lost tools
and not yet regained them. Both shapes are needed: a one-tool edit through the
batch method would re-transmit every binary the bot has.

**D-14 — Platform state is written before the port is called.** teclaw's port
composes the artifact *from* `ac_bot_cli_tool`, so a port called before the rows
were written would transmit the previous set. This makes the table what its own
repository contract already called it — the record of what the platform *asked
for* — with `drift()` there to compare it against what the engine has.

The consequence is the failure story, and it is the same on every path: **the
platform records what the family confirmed.** Anything it did not is rolled
back — a single install undoes its insert, a single remove puts its row back,
and a whole-set call unwinds every name it touched that the family refused or
never answered for.

*This corrects an earlier draft of this decision*, which had `replace_all`
leave its rows standing on the theory that the desired state would be re-sent
by the next apply. **It would not.** The row already carries the declaration's
`(digest, subpath)`, so the next apply converges on it, reports `unchanged` and
never retries — leaving a row pointing at bytes the engine rejected,
permanently, with `drift()` the only thing that could notice and nothing acting
on it. The argument against rolling back was that unwinding N rows adds a
failure mode; the rows actually unwound are only the ones this apply *touched*
and the family did not confirm, which is typically one or none.

Two specific losses the corrected rule prevents, both found in review of the
first draft:

- A tool whose per-name result says the engine refused it kept the **new** row
  while its **previous** object was collected — destroying the last
  known-good binary of the one tool that failed.
- A whole-call failure (an unreachable engine) never restored the rows already
  **dropped** for removal. "The desired state stands" never covered those:
  they were dropped, not written. The platform would stop tracking a tool the
  container may still be running, and nothing could notice — teclaw cannot
  observe drift by design, and a later apply only removes what the table still
  has.

**D-15 — The engine's replacement response reports per name.** The apply report
is per declared entry, and a single batch call would otherwise collapse every
engine-side refusal into one verdict for the whole set. Platform-side failures —
a digest mismatch, a non-amd64 ELF, a missing `subpath`, an unreachable source —
stay per tool regardless, because they happen before any engine call. This makes
ARCA's replacement endpoint meaningfully more work than the three name-addressed
ones: it accepts a set, applies it, and reports each name's outcome. That is
stated in the engine contract rather than left to be discovered.

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
- The ARCA delivery: name-addressed `install` / `delete` / `list`, plus the
  whole-set `replace` that reports per name — each one call to the engine.
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

Tracked rather than open, because v1 accepts it (rev 8):

- **The whole-set call re-transmits every tool's bytes, not just what changed.**
  An apply that changes one tool of four uploads all four to ARCA. A *no-op*
  apply costs nothing — every declaration converges on `(digest, subpath)` and
  the port is not called at all — so the cost falls only on an apply that
  changes something, which is why it is acceptable for v1. The shape that fixes
  it carries the full desired set as `(name, digest)` and bytes only for what
  changed, which is an engine-contract change; a TODO sits where the payload is
  built.

## Revision history

| | What changed, and why |
| --- | --- |
| **rev 1** | ARCA arm as a platform-composed start-command prologue; `PRE_CONTAINER`; a §2.6 exception. |
| **rev 2** | Prologue withdrawn. `cli_tools` as a third domain on `EngineRuntimeProjection`; back to `ON_CONTAINER`; exception removed. |
| **rev 3** | Projection withdrawn — a tool is not platform state a runtime must be told about. `cli_tools` as `resources` plus an executable bit, no platform record on ARCA. |
| **rev 4** | Platform-managed on the owner's decision: metadata table, one core service every caller delegates to, engine CRUD plus batch, a management API, no projection. |
| **rev 5** | The engine owns the directory and the protocol is **name-addressed** (D-3), so the `tools/` namespace and every isolation mechanism are dropped (D-5). `cli_tools` is always platform-managed, independent of the teclaw switch, like `mcp` (D-6). The teclaw arm gains the **promotion gather** — GET from the engine, stage-scoped OSS, artifact refs — which is why the protocol needs `get`. `PATH` is replaced by a default-skillset skill for v1, with the cost and the §3.7 correction written down (D-8). |
| **rev 6** | **The platform stores the bytes in OSS at install time** (D-4) — rev 5 had no answer for what a teclaw artifact references on a live update or a manifest apply. The artifact becomes teclaw's delivery with no separate engine upload (D-5); promotion becomes a stage-scoped copy rather than a download, so the protocol's `get` is no longer needed; and the phase settles as ARCA `ON_CONTAINER` / teclaw `PRE_CONTAINER` under either switch position (D-6), correcting rev 5's claim that no category-specific code was required. |
| **rev 8** | (Corrected in review: `replace_all` **does** roll back what the family did not confirm — see D-14. The first draft left its rows standing, which the next apply would then converge on and never retry.) The port gains a **whole-set** operation beside the single-tool ones (D-13): a manifest apply calls `replace_all` once instead of `install` per tool, so the engine sees one desired set and no intermediate state — on teclaw those intermediates were wrong, since removals precede installs. The families differ only in transmission: teclaw composes the set into the artifact, ARCA calls a new replacement endpoint reporting **per name** (D-15), which keeps the apply report per entry. teclaw's port composes *from* the table, so **platform state is written before the port is called** (D-14); a single install or remove rolls its row back when the port refuses, an apply does not. The closing redeliver leaves `BotCliToolService` — the family knowledge moves into the port, and `TeclawDelivery.finish` keeps owning the one artifact push a manifest apply makes. |
| **rev 7** | The engine's `install` owns the executable bit (D-5). Rev 6 had the platform do a device write then a `chmod` through the general shell channel; withdrawn in review — once `install` carries the semantics, a platform-side `chmod` is a second implementation of the engine's own job, and it took a user-supplied name through a shell. No `chmod`, no shell command, no quoting. |
