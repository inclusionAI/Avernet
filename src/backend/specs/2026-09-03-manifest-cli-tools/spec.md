# `cli_tools` — Declarative Command-Line Tools for Both Engine Families (W9)

Work item W9 of `docs/bot-config-manifest/work-items.zh-CN.md` §5, issue #1477.
Plan: `plan.md` in this directory.

## Summary

A bot owner declares a command-line tool in the manifest; the tool ends up on
the model's `PATH` inside the container, on both engine families. The platform
does all the dangerous work — fetch, mandatory `sha256` enforcement, unpack,
selection of the one declared file, architecture verification — so what reaches
an engine is **one executable file** plus a change-detection hash.

This is the last unshipped category of the config manifest. Everything it needs
already exists: W1 parses the vocabulary and resolves `${BOT_ARCH}`, W2 fetches
under guard, W3 authorises private sources, W8 built the managed-files store and
the ownership map, and the artifact contract's `cliToolRef` shape landed early
with W12 (#1734). What is missing is the materialiser, the delivery on each
family, and the capability unlock.

## Motivation

`bcs-cli` is the proof the feature is needed: today a binary is placed by hand
by a singlebox script (`scripts/modules/bots.sh:954` prepends its directory to
the openclaw gateway's `PATH`) and taught to the model by a hand-written
`SKILL.md`. That pattern works and cannot be offered to a customer — there is no
declarative way to say "this bot has this tool", so every tool is a bespoke
change to platform scripts.

§4's investigation (X3, now closed) confirmed there is **no existing CLI
mechanism being duplicated here**: every `bcs-cli` reference in the tree is
singlebox orchestration, and no delivery path anywhere handles the executable
bit. This is new machinery, not a second implementation.

## What the code allows, checked before writing this

1. **Nothing in any delivery path can set an executable bit.** `DeviceFileSystem`
   offers `read_file` / `write_file` / `delete_tree` / `delete_file` and no
   `chmod`; there is no exec channel on the device protocol, and no `PATH`
   injection anywhere in `service_bot/` or `devices/`. So placement, the
   executable bit and `PATH` cannot be properties of the platform *writing a
   file*. They belong to whoever owns the container's filesystem: the engine.
2. **A cross-engine projection seam already exists, and `skills` and `mcp` use
   it.** `EngineRuntimeProjection` (`runtime_projection_contract.py:266`)
   defines `validate_plan` (refuse desired state a runtime has no contract for,
   before any request is emitted) and `apply` (converge this bot's runtime,
   returning an observed outcome). `EngineRuntimeProjectionRegistry` resolves
   one per engine off `ac_bots.active_engine`, with the per-domain contract as
   the **default** — so an ordinary new engine needs no entry at all.
3. **Its two implementations are already the two families' shapes.**
   `PerDomainRuntimeProjection` writes each domain to its own runtime endpoint;
   `WholeArtifactRuntimeProjection` recomposes the whole artifact and discards
   the arguments. `cli_tools` fits both without inventing a third shape.
4. **The result vocabulary already covers an engine that cannot do it yet.**
   `RuntimeProjectionStatus` is `CONVERGED` / `PENDING` / `DEGRADED` /
   `SKIPPED`, and `RuntimeProjectionIssue` carries a code, a reason, a
   `retryable` flag and a suggested action. Nothing new is needed to report
   "this engine has no tools endpoint yet".
5. **`ActivationPort` is the precedent for reaching that seam from a
   materialiser.** The `mcp` and `skills` materialisers already call live
   activation through a narrow port that a delivery strategy binds — the real
   `DirectActivationService` on ARCA, `RecordOnlyActivation` on the
   platform-managed teclaw path. A `cli_tools` port is the same move.
6. **The managed-files store from W8 is the right home for the bytes.**
   `ManagedFilesStore` already keeps the platform's own copy of a bot's
   manifest-delivered files by category and namespace, and
   `ManagedFilesComposeReader` is what the teclaw composer reads. It becomes
   the shared content plane both families' projections reference.
7. **`${BOT_ARCH}` already resolves to `amd64`** (`schema/placeholders.py`,
   `BOT_ARCH_VALUE`), contrary to the W9 progress table — W1 shipped it. Only
   the ELF verification half of that row is outstanding.
8. **The schema already parses a `cli_tools` entry** and already enforces the
   mandatory `digest` (`schema/entries.py`), because W1 parses the whole
   vocabulary. `fetch/limits.py` already carries the 200 MiB `cli_tools` width,
   and `APPLY_ORDER` already places `cli_tools` at `ON_CONTAINER`. The category
   is refused today only by the capability gate.

## The delivery decision

**One declarative contract, projected onto whichever engine the bot runs, live.**

The platform never places a file or edits a `PATH` itself. It converges a
desired state and hands each engine the same per-tool ref — `{name, store,
path, md5, version}`, the shape `cliToolRef` already defines — through the
seam that already exists for exactly this: `EngineRuntimeProjection`
(`runtime_projection_contract.py:266`), which `skills` and `mcp` already
project through, resolved per engine by `EngineRuntimeProjectionRegistry`.

That seam is what makes this a protocol rather than an arrangement. Its two
implementations are already the two families' native shapes:

- `PerDomainRuntimeProjection` — engines with separate runtime endpoints per
  domain. `cli_tools` becomes a third domain beside skills and MCP: the
  platform calls the engine's tools endpoint with the declared set, and the
  engine places the files, sets the executable bit, exposes them on `PATH`,
  and removes what the set no longer names.
- `WholeArtifactRuntimeProjection` — teclaw. Its projection *is* recomposing
  the artifact, so `cli_tools` refs plus the existing `ownership.cli_tools`
  are the whole of its side.

A new engine therefore supports `cli_tools` the way it supports skills and
MCP: it implements the projection, or it inherits the registry's default. It
never needs a bespoke arrangement in platform code, and nothing about tool
delivery lives in a start command.

Because the projection targets a **live** runtime, `cli_tools` is an
`ON_CONTAINER` construct — unchanged from where `APPLY_ORDER` already has it
— and a `PUT` takes effect immediately on both families, with **no §2.6
exception for this category**.

### The cost, stated plainly

This asks the ARCA-family engines for a new runtime endpoint, which is a
change to `engine-requirements.zh-CN.md:16`'s "zero changes" line. That is
the deliberate trade: a start-command arrangement would have cost the engines
nothing now and cost every future engine a bespoke integration, and the tool
would only have become effective at the next provisioning. The protocol costs
one endpoint per engine and generalises.

Until an engine implements it, the projection answers with the vocabulary the
contract already has — `SKIPPED` or `DEGRADED` with a `RuntimeProjectionIssue`
naming the engine and the missing endpoint — so a bot on an engine that is not
ready reports honestly in the apply report instead of silently doing nothing.

## User Stories

- As a bot owner, I declare a tool with a URL and a `sha256`, and the model can
  invoke it by name in the container.
- As a bot owner, I declare a tool that lives inside a `.tar.gz` by naming its
  `subpath`, and only that one file is delivered.
- As a bot owner, I `PUT` a manifest that adds a tool and the running bot has it
  on `PATH` when the apply finishes — no restart, like every other category.
- As a bot owner, I change a tool's version and the apply replaces it in place;
  I remove it from the manifest and it is gone from the container.
- As a bot owner, I declare a tool built for the wrong architecture and the
  apply report tells me so, instead of the model hitting `exec format error`
  mid-task.
- As a bot owner, I omit `digest` and the `PUT` is refused, because the platform
  will not distribute an unpinned executable on my behalf.
- As the teclaw engine owner, the artifact tells me exactly which single file to
  place per tool, and an `md5` that says whether I already have it.
- As an ARCA engine owner, `cli_tools` reaches me the same way skills and MCP
  do — one more domain on the projection I already implement.
- As a bot owner on an engine whose tools endpoint does not exist yet, the apply
  report tells me the category was skipped and why, rather than reporting
  success and delivering nothing.

## Acceptance Criteria

### Admission and capability

- [ ] `cli_tools` is **supported** in the capability resolver for the ARCA family
      and for teclaw, and stays unsupported for desktop bots and unknown
      engines, with the existing reasons.
- [ ] The unsupported-reason constant for `cli_tools` is removed, and the
      gate tables in `manifest-schema.zh-CN.md` §7 and work-items §5 W1 lose
      their `cli_tools` row — the rule that this surface never accepts what it
      cannot apply now holds by the category being applicable, not by refusal.
- [ ] **Content-dependent `subpath` validation lives here, not in W1**: after
      unpack, the selected `subpath` must exist, must be a **regular file**, and
      must still resolve inside the unpack tree after symlink resolution. W1
      keeps only the syntactic half (absolute path, `..` segments, duplicate
      `name`), because at `PUT` time there is no tree to inspect.
- [ ] `digest` remains mandatory for every non-git form, refused at `PUT` as
      today.

### Fetch, verify, select

- [ ] For a single-binary entry, the platform fetches the source under the
      `cli_tools` width (200 MiB), enforces the declared `sha256` over the
      fetched bytes, and takes those bytes as the tool.
- [ ] For an archive entry, the `sha256` is enforced over **the whole archive**,
      the archive is unpacked under W2's guards, `subpath` selects the one file,
      and **no other member is delivered**.
- [ ] The platform computes the **`md5`** of the finally selected file and
      records it. It is computed by us, never read from the store's ETag.
- [ ] The selected file's **ELF header is verified** to be `linux/amd64`; a
      mismatch fails that entry in the apply report with a message naming the
      architecture found and the one expected. A non-ELF file fails the same
      way.
- [ ] `${BOT_ARCH}` in a `source` URL resolves to `amd64` before fetch and
      before credential prefix authorisation, as W1 already implements.

### Convergence

- [ ] The convergence criterion is **the whole delivery-relevant declaration —
      `digest` *and* `subpath` together** — not `digest` alone. Same archive,
      `subpath` changed from `bin/old` to `bin/new`, is a real change: judging
      on `digest` alone would report `unchanged`, deliver nothing, and leave the
      old file in place while the declaration names the new one.
- [ ] `version` is metadata: it appears in the apply report and the artifact and
      never affects convergence.
- [ ] The category is all-or-nothing per §3.2: a declared `cli_tools` is
      overwritten to equal the declaration; tools the previous document declared
      and this one does not are removed; an undeclared `cli_tools` is untouched.
- [ ] An empty declared `cli_tools: []` means "this bot has no platform-delivered
      tools" and removes all of them.

### Platform state (both families)

- [ ] The selected file is written to the managed-files store under a new
      `cli_tools` category, keyed per bot and tool name, alongside the `md5` and
      `version`. That store record is the desired state both arms read.
- [ ] Convergence is observed from the store — an unchanged tool writes nothing
      and plans `unchanged` — matching how the identity and resource
      materialisers behave.
- [ ] `cli_tools` stays an **`ON_CONTAINER`** construct in both delivery
      strategies — where `APPLY_ORDER` already has it — because the projection
      targets a live runtime. On the W13 creation path it therefore lands in
      phase B, with `identity`, `resources`, `skills` and `mcp`.
- [ ] **Creation cleanup:** when a W13 creation job fails after the store was
      written but before a bot exists (provisioning failed, the container never
      reached `ACTIVE`), the tool objects are deleted along with the rest of
      that bot's manifest state — the manifest row and the script row the job
      already deletes. Without this the store keeps bytes for a `bot_id` that
      was never created and nothing will ever collect them.

### teclaw arm

- [ ] The composer emits `cli_tools` on every artifact it composes for a bot
      with the platform-managed switch on, each entry `{name, store, path, md5,
      version}` naming the single executable file, per `cliToolRef`.
- [ ] `ownership.cli_tools` follows the same per-operation rule W8 established:
      `platform` on a manifest apply's artifact and on the first artifact of a
      bot carrying a manifest; `engine` on any other compose and while the
      switch is off.
- [ ] `SCHEMA_VERSION` **stays 4**. This is settled, not pending: `cli_tools`
      entered v4 as an additive field under the ignore-unknown-fields rule (A5),
      agreed with the teclaw owner on 2026-08-31. The existing test that pins
      the version against drift keeps passing.
- [ ] With the switch off, or on a bot with no declared tools, `to_dict` still
      omits `cli_tools` entirely and the artifact is byte-identical to today's.
- [ ] `teclaw-cli-contract.zh-CN.md` is reconciled with what ships: the
      `schema_version` 4 → 5 statement and the "engine receives an unpacked
      directory" / `entrypoints` language are stale against the flattened
      one-entry-one-file shape and the no-bump decision.

### ARCA arm — the engine projection

- [ ] A `cli_tools` domain is added to `PerDomainRuntimeProjection`: given the
      declared set of refs, it calls the engine's tools endpoint, and the
      engine owns placement, the executable bit, `PATH` exposure and removal of
      what the set no longer names.
- [ ] The platform sends the **same ref shape both families get** —
      `{name, store, path, md5, version}` per `cliToolRef` — so there is one
      protocol, not an ARCA dialect. The engine fetches the bytes from the
      store address it is given.
- [ ] `validate_plan` refuses a `cli_tools` plan an engine has no contract for
      **before any runtime request is emitted**, per the seam's own guarantee.
- [ ] An engine without a tools endpoint yields `SKIPPED` (or `DEGRADED` when
      other domains converged) with a `RuntimeProjectionIssue` naming the
      engine and the missing endpoint, `retryable` set honestly, and the apply
      report carries it. It never reports success having delivered nothing.
- [ ] `ProjectionScope` gains the `cli_tools` half, so a mutation that touched
      only tools does not force a skills or MCP rewrite — the same round-trip
      economy the existing halves buy.
- [ ] The whole-artifact projection needs no `cli_tools` branch: recomposing
      the artifact already carries the refs (teclaw arm above).
- [ ] **No engine image is modified by this work item.** The endpoint is the
      engine side's to implement; this item delivers the platform half, the
      protocol document, and the honest `SKIPPED` until they ship.

### `PUT` takes effect immediately, on both families

- [ ] A `PUT` that changes `cli_tools` stores the document, triggers the apply,
      and the materialiser converges the store and projects onto the live
      runtime. The effect is immediate on **both** families, exactly like every
      other category — **no §2.6 exception for `cli_tools`**, and no delivery
      note about a later provisioning.
- [ ] No restart, republish or payload rebuild is issued. A test pins that the
      `cli_tools` path names none of them.
- [ ] On a bot that is not `ACTIVE`, `cli_tools` behaves like the other
      `ON_CONTAINER` categories: recorded as failed with the existing warning
      naming the apply call to make once the bot is up. No new behaviour.

### Documentation

- [ ] `manifest-schema.zh-CN.md` §3.7 states the v1 limit plainly: **a delivered
      tool is one self-contained executable file**. A tool needing an in-package
      helper or a sibling `lib/` is out of scope and must be built as a static
      binary. An unwritten limit is the first packaged wrapper's bug report.
- [ ] The user manual gains a `cli_tools` section: the two source forms, the
      mandatory `digest`, the per-family timing rule above, and the fact that
      teaching the model to *use* the tool is the owner's job (identity
      `TOOLS.md` or a companion skill), not this category's.
- [ ] The W9 entry in both `work-items.md` and `work-items.zh-CN.md` is updated
      to what shipped, and the stale rows are corrected (`${BOT_ARCH}` already
      landed with W1; the artifact field already landed with #1734).

### Nothing else moves

- [ ] No other category, materialiser, or apply point changes behaviour.
- [ ] `engine_config` stays unsupported, with its existing reason.
- [ ] The orchestrator and the order table stay engine-agnostic; the engine
      difference lives in the projection registry, where it already lives for
      `skills` and `mcp`, and the materialiser names no engine.
- [ ] **No deploy-path change.** `build_start_command`, `BotDeployContext` and
      `_compose_start_command` are untouched, so every bot's composed start
      command is byte-identical to today's and #935's assertion is unedited.
- [ ] Every existing manifest, artifact, creation and deploy test passes with
      assertions untouched.

## Decisions

**D-1 — One declarative contract, projected live through the seam `skills` and
`mcp` already use.** The platform converges desired state and hands every
engine the same `cliToolRef`; the engine owns placement, the executable bit and
`PATH`. Rejected: an engine-side *command* protocol (platform issues
`chmod`/`mv`/`PATH` instructions) — it contradicts `manifest-schema` §6's
"physical placement is always the engine's decision", makes `cliToolRef.md5`
meaningless because the platform would have to diff against container state it
does not have, and opens a remote-exec channel into customer containers.
Rejected in review (PR #1870): a platform-composed **start-command prologue**
— it is an arrangement, not a protocol, so every future engine would need a
bespoke integration, and it made the category effective only at the next
provisioning.

**D-2 — `cli_tools` is an `ON_CONTAINER` construct**, unchanged from
`APPLY_ORDER`. It projects onto a live runtime, so it needs the container, and
on the W13 creation path it lands in phase B with the other four categories.

**D-3 — `PUT` takes effect immediately on both families. No §2.6 exception.**
This is what D-1 buys over the rejected prologue, and it is why the protocol is
worth its cost.

**D-4 — The managed-files store is the single content plane for both arms.**
teclaw's composer reads it into artifact refs; the ARCA projection hands the
engine the same store address. One materialiser, one convergence rule, one ref
shape, two transports.

**D-5 — Convergence keys on `digest` *and* `subpath`.** `subpath` is a
**source-side** path — which member of the fetched archive is the tool — never
a target path, since placement is the engine's. So the same archive with
`subpath` moved from `bin/old` to `bin/new` delivers a *different file* under
the same command name; keying on `digest` alone would report `unchanged`, send
nothing, and leave the old binary answering to a name whose declaration now
means the new one.

**D-6 — `SCHEMA_VERSION` stays 4.** Settled with the teclaw owner on
2026-08-31 and already pinned by a test. The accepted cost: `schema_version` no
longer tracks this contract's evolution, so "does this artifact carry
`cli_tools`?" is answered by probing for the field.

**D-7 — v1 delivers one self-contained executable per entry.** The flattening
that removed `entrypoints` also removed the shape that could express
"executable but not a command", so an in-package helper cannot be made
executable. Rather than reopen "do we trust an archive's mode bits", v1 states
the limit.

**D-8 — An engine without the endpoint reports `SKIPPED`, not success.** The
projection contract's existing status vocabulary carries it, so shipping the
platform half ahead of the engines is honest rather than silently inert.

## In Scope

- The capability unlock and the content-dependent `subpath` validation.
- The `cli_tools` materialiser: fetch, `sha256` enforcement, unpack, `subpath`
  selection, ELF verification, `md5`, store write, replacement semantics.
- The `cli_tools` category in the managed-files store.
- The teclaw arm: composer `cli_tools` refs (`ownership.cli_tools` already exists).
- The ARCA arm's **platform half**: the `cli_tools` domain on the per-domain
  projection, its `ProjectionScope` half, `validate_plan`, and the honest
  `SKIPPED` for an engine without the endpoint.
- The engine-facing protocol document for the ARCA family.
- The apply-report entries.
- Schema, user manual, teclaw contract reconciliation, and work-items updates.

## Out of Scope

- **The ARCA engines' own tools endpoints.** Each engine implements the
  protocol on its side; this item ships the platform half and the document.
  Until an engine does, its bots report `SKIPPED`.
- **`bcs-cli` as the first consumer.** The explicit next step: retire the
  singlebox script that hand-places it and onboard it through the manifest.
- **The default-skillset tool-usage skill.** `SkillSetService` already has the
  mechanism; wiring it is follow-up work.
- **Multi-architecture sources.** `linux/amd64` only, one URL per tool (X3).
  `${BOT_ARCH}` exists so a future mixed fleet changes where the value comes
  from, not the schema.
- **Package-manager installs.** Imperative, and already routed to `script`.
- **`engine_config`**, still out on the X2/T3 decision.

## Open Questions

None blocking the platform half. Two are with the engine owners and gate only
when a given engine's bots stop reporting `SKIPPED`:

- The tools endpoint's concrete shape per ARCA engine — the successor to A2,
  which asked where a platform tools directory joins `PATH`. Under D-1 that
  question is the engine's to answer internally, and A2 is closed by the
  protocol rather than by a platform-side answer.
- Whether any engine needs a sandbox policy for user-supplied binaries beyond
  what it already applies to skills.

## Revision history

| | What changed, and why |
| --- | --- |
| **rev 1** | Platform-composed start-command prologue for the ARCA arm; `cli_tools` re-phased to `PRE_CONTAINER`; a §2.6 exception making it effective only at the next provisioning. |
| **rev 2** | Review of inclusionAI/Avernet#1870. The prologue is withdrawn: it is an arrangement, not a protocol, so every future engine would need a bespoke integration. `cli_tools` projects live through `EngineRuntimeProjection` — the seam `skills` and `mcp` already use — so it stays `ON_CONTAINER` (D-2), `PUT` is immediate on both families and the §2.6 exception is gone (D-3), and the deploy path is untouched. The cost moves onto the engines: one endpoint each, with an honest `SKIPPED` until they ship (D-8). |
