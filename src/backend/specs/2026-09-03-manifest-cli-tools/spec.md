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

1. **A CLI tool is a file plus an executable bit.** Nothing else about it is
   platform state: no activation row, no allow-list, no per-tool record a
   runtime has to be told about. So the machinery it needs is the machinery
   `resources` already has, not the machinery `skills` and `mcp` have.
2. **The one write chain already delivers files to both families.**
   `ResourceFileService`'s dispatcher fans out per transport — arca/baas via
   device sync, teclaw via the managed-files store — and the `resources`
   materialiser never branches on engine. `cli_tools` rides the same chain.
3. **`write_file` cannot set a mode**, on any transport: the BaaS device
   uploads through `POST /api/file/upload` with a `target_path` and no mode
   field, and `DeviceFileSystem` exposes no `chmod`.
4. **But the platform already runs commands in live ARCA containers, as
   routine business.** `BaasService.exec_command_on_bot` is used by
   `baas_container_init.py` (bootstrap, engine install, supervisor and dir
   setup, service start, watchdog), by `baas_codefuse_writer.py` to write a
   token, and through the ready-made helper
   `execute_baas_shell_command(baas_service, device, shell_cmd, timeout_seconds)`
   which returns a `CommandResult` with an exit code. A `chmod +x` after an
   upload is therefore an existing channel used for its existing purpose —
   **not a new capability, and nothing an engine team has to build.**
5. **`/home/admin/bin` is an established in-container directory** holding the
   platform's own scripts, and the NAS mount at `/home/admin` persists across
   restarts (`deploy_models.py:167`, `managed_composer.py:428`).
6. **On ARCA the platform holds no copy of a delivered file, by design**, and
   `resources` handles convergence accordingly: "**replace, don't diff** —
   every apply rewrites every member", because a drifted tree would survive a
   source-side comparison. `cli_tools` can take the same rule on ARCA and needs
   no platform-side record there.
7. **teclaw is the family that needs a record**, because its artifact carries
   `{store, path, md5}` refs — which is what the W8 managed-files store is
   for, and what `ManagedFilesComposeReader` already reads.
8. **`${BOT_ARCH}` already resolves to `amd64`** (`schema/placeholders.py`),
   contrary to the W9 progress table — W1 shipped it. Only the ELF
   verification half of that row is outstanding.
9. **The schema already parses a `cli_tools` entry** and enforces the mandatory
   `digest` (`schema/entries.py`); `fetch/limits.py` already carries the
   200 MiB width; `APPLY_ORDER` already places `cli_tools` at `ON_CONTAINER`.
   The category is refused today only by the capability gate.

## The delivery decision

**A CLI tool is a file with an executable bit. It is delivered the way files
are delivered.**

`cli_tools` rides the same write chain `resources` uses, through a port bound
per delivery strategy — the shape W6 already established:

| | ARCA family | teclaw |
| --- | --- | --- |
| Bytes go to | the live container, via device sync | the managed-files store |
| Executable bit | `chmod +x` through `execute_baas_shell_command` | the engine's, on placement |
| The engine learns of it | nothing to learn — the file is simply there, on `PATH` | artifact `cli_tools` refs + `ownership.cli_tools` |
| Convergence | replace, don't diff — every apply rewrites (as `resources` does) | the store's tool index |

Nothing new is asked of any engine on the ARCA side: the file write is the
existing chain, and the `chmod` is a channel the platform already uses for
container bootstrap, engine install and token writes. teclaw's side is the
artifact refs its contract already defines.

The manifest is the desired state and the apply overrides what is in the
container with what the document says — the same override semantics
`resources` has under §3.2.

Because the ARCA arm writes into a live container, `cli_tools` is
`ON_CONTAINER` **on ARCA** — where `APPLY_ORDER` already has it. On teclaw the
delivery strategy re-phases every non-script construct to `PRE_CONTAINER` when
the platform-managed switch is on (`TeclawDelivery.phase_of`), and `cli_tools`
inherits that generically: nothing category-specific to write. A `PUT` takes
effect immediately on both families, with **no §2.6 exception**.

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
- As an ARCA operator, nothing in any engine image changes: the tool is a file
  written by the chain that already writes files, made executable by the
  channel that already runs container commands.
- As a bot owner, if the executable bit cannot be set the apply report says so
  with the command's error, rather than leaving a file the model cannot run.

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

### Delivery state, per family

- [ ] **ARCA:** the selected file is written through the same chain
      `resources` uses, into a platform tools directory in the container, and
      then made executable via `execute_baas_shell_command`. The platform keeps
      **no copy** — matching how `resources` behaves on ARCA today.
- [ ] **ARCA convergence is replace-don't-diff**, the rule `resources` already
      states: every apply rewrites every declared tool, because a tool replaced
      by hand in the container would survive a source-side comparison. The
      category is therefore never `is_noop` on ARCA.
- [ ] **teclaw:** the selected file is written to the managed-files store under
      a new `cli_tools` category, with its `md5`, `version` and the
      `(digest, subpath)` convergence key recorded in a per-bot tool index.
      That index is what the composer reads.
- [ ] `cli_tools` is **`ON_CONTAINER`** — where `APPLY_ORDER` already has it —
      which is the ARCA reading. On teclaw, `TeclawDelivery.phase_of` re-phases
      every non-script construct to `PRE_CONTAINER` under the platform-managed
      switch, and `cli_tools` inherits that with **no category-specific code**.
      `order.py` is not modified.
- [ ] **Creation cleanup:** when a W13 creation job fails after the store was
      written but before a bot exists (provisioning failed, the container never
      reached `ACTIVE`), the teclaw tool objects are deleted along with the rest
      of that bot's manifest state — the manifest row and the script row the job
      already deletes. Without this the store keeps bytes for a `bot_id` that
      was never created and nothing will ever collect them. (ARCA holds no copy,
      so there is nothing to clean there.)

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

### ARCA arm — file write plus an executable bit

- [ ] The tool is written through `ResourceFileService`'s chain, so the
      materialiser branches on no engine and the transport dispatch is the
      existing one.
- [ ] After the write, the file is made executable through
      `execute_baas_shell_command` — the helper `baas_container_init` and
      `baas_codefuse_writer` already use. **No new channel, no engine change.**
- [ ] A non-zero exit from the `chmod` fails that entry in the apply report with
      the command's stderr, rather than leaving a non-executable file that the
      model would hit as "permission denied" mid-task.
- [ ] Tools land in a platform-defined directory that is on the agent process's
      `PATH`, so the user never sees a physical path (see Open Questions for
      which directory).
- [ ] Full replacement: a tool in that directory which the declaration no
      longer names is removed, per §3.2's category semantics.
- [ ] **No deploy-path change.** `build_start_command`, `BotDeployContext` and
      `_compose_start_command` are untouched, so every bot's composed start
      command is byte-identical and #935's assertion stands unedited.
- [ ] **No engine image, endpoint or protocol is added.**

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
      difference lives in the port a delivery strategy binds, exactly as it does
      for `resources`, and the materialiser names no engine.
- [ ] `skills`, `mcp` and the runtime projection are **not touched**: no new
      projection domain, no `ProjectionScope` change.
- [ ] **No deploy-path change.** `build_start_command`, `BotDeployContext` and
      `_compose_start_command` are untouched, so every bot's composed start
      command is byte-identical to today's and #935's assertion is unedited.
- [ ] Every existing manifest, artifact, creation and deploy test passes with
      assertions untouched.

## Decisions

**D-1 — A CLI tool is a file with an executable bit, delivered by the file
chain.** `cli_tools` rides `ResourceFileService`'s chain through a port bound
per strategy — the W6 shape — and the executable bit is set by
`execute_baas_shell_command`, the channel the platform already uses for
container bootstrap and token writes. Nothing is asked of any ARCA engine.

Two earlier designs are withdrawn, both in review of PR #1870:

- *rev 1, a platform-composed start-command prologue* — an arrangement in
  platform code, not a protocol, so every future engine would need a bespoke
  integration; and it made the category effective only at the next
  provisioning.
- *rev 2, a `cli_tools` domain on `EngineRuntimeProjection`* — that seam exists
  for `skills` and `mcp`, which are **platform state a runtime must be told
  about**. A tool is not: there is no activation row, no allow-list, nothing to
  reconcile. Modelling it there would have bought a protocol nobody needed and
  charged five engine teams an endpoint for it.

**D-2 — `cli_tools` is `ON_CONTAINER`, which is the ARCA reading.** On teclaw
the delivery strategy already re-phases every non-script construct to
`PRE_CONTAINER` under the platform-managed switch, so the phase differs by
family through existing generic code and `order.py` needs no change.

**D-3 — `PUT` takes effect immediately on both families. No §2.6 exception.**
Apply writes the file and sets the bit; the effect is immediate.

**D-4 — ARCA converges by replacing, not diffing.** The rule `resources`
already states, for the reason it states it: the platform holds no copy on
ARCA, and a hand-replaced binary would survive a source-side comparison. Only
teclaw keeps a record, because only its artifact needs `md5`.

**D-5 — Convergence keys on `digest` *and* `subpath` where a record exists.**
`subpath` is a **source-side** path — which member of the fetched archive is
the tool — never a target path. The same archive with `subpath` moved from
`bin/old` to `bin/new` delivers a *different file* under the same command name;
keying on `digest` alone would report `unchanged`, send nothing, and leave the
old binary answering to a name whose declaration now means the new one.

**D-6 — `SCHEMA_VERSION` stays 4.** Settled with the teclaw owner on
2026-08-31 and already pinned by a test. `schema_version` no longer tracks this
contract's evolution, so "does this artifact carry `cli_tools`?" is answered by
probing for the field.

**D-7 — v1 delivers one self-contained executable per entry.** The flattening
that removed `entrypoints` also removed the shape that could express
"executable but not a command", so an in-package helper cannot be made
executable. Rather than reopen "do we trust an archive's mode bits", v1 states
the limit.

## In Scope

- The capability unlock and the content-dependent `subpath` validation.
- The `cli_tools` materialiser: fetch, `sha256` enforcement, unpack, `subpath`
  selection, ELF verification, `md5`, and the replacement semantics.
- The tool port, bound to the device chain on ARCA and the store on teclaw.
- The executable bit on ARCA, through the existing exec helper.
- The `cli_tools` category and tool index in the managed-files store (teclaw).
- The teclaw arm: composer `cli_tools` refs (`ownership.cli_tools` exists).
- The apply-report entries, including a failed `chmod`.
- Schema, user manual, teclaw contract reconciliation, and work-items updates.

## Out of Scope

- **`bcs-cli` as the first consumer.** The explicit next step: retire the
  singlebox script that hand-places it and onboard it through the manifest.
- **The default-skillset tool-usage skill.** `SkillSetService` already has the
  mechanism; wiring it is follow-up work.
- **Any engine-side change, endpoint or protocol.** Under D-1 there is none.
- **Multi-architecture sources.** `linux/amd64` only, one URL per tool (X3).
- **Package-manager installs.** Imperative, and already routed to `script`.
- **`engine_config`**, still out on the X2/T3 decision.

## Open Questions

One, and it is the last surviving piece of A2:

- **Which directory do tools land in, such that it is on the agent process's
  `PATH`?** `/home/admin/bin` exists in ARCA containers and holds the
  platform's own scripts, but they are invoked by absolute path, so its
  presence on the agent's `PATH` is unconfirmed. Two candidate answers — place
  tools in a directory the engine's `PATH` already includes, or have the
  container-init step add one — and the choice needs one confirmation per
  deploy runtime rather than a design decision. It does not block the
  materialiser, the store, the teclaw arm, or admission.

## Revision history

| | What changed, and why |
| --- | --- |
| **rev 1** | Platform-composed start-command prologue for the ARCA arm; `cli_tools` re-phased to `PRE_CONTAINER`; a §2.6 exception making it effective only at the next provisioning. |
| **rev 2** | Review of #1870: the prologue withdrawn as an arrangement rather than a protocol. `cli_tools` modelled as a third domain on `EngineRuntimeProjection`, the seam `skills` and `mcp` use; `ON_CONTAINER` restored; §2.6 exception dropped. |
| **rev 3** | Review of #1870, second round. The projection domain is withdrawn too: that seam carries **platform state a runtime must be told about**, and a tool is a file with an executable bit — no activation row, nothing to reconcile. `cli_tools` now rides the `resources` write chain through a per-strategy port, with `chmod +x` via `execute_baas_shell_command` (D-1). Correcting rev 2's premise: the platform **already** runs commands in live ARCA containers (`baas_container_init`, `baas_codefuse_writer`), so the executable bit needs no new channel and no engine change — the engine-endpoint cost rev 2 accepted is gone. ARCA converges by replacing rather than diffing, as `resources` does (D-4), and only teclaw keeps a record. The phase is per family through existing generic code (D-2). |
