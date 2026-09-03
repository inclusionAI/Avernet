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
   injection anywhere in `service_bot/` or `devices/`. So the executable bit and
   `PATH` cannot be a property of *writing the file* on either family — they
   have to be someone's explicit act after the bytes land.
2. **On ARCA the platform composes the container's boot line.**
   `DeployConfigComposer.build_start_command` is platform code per deploy
   runtime, and it chains image-baked scripts (`ack_composer` invokes
   `start_service.sh`; `managed_composer` chains bootstrap → install engine →
   sandbox service → watchdog with `&&`). This is a platform-owned seam, not a
   cross-team API.
3. **The boot line is recomposed on every device provisioning.**
   `_build_create_bot_payload` is reached from `create_bot` and `upgrade_bot`,
   and `restart_bot` releases the device and allocates a new one. So a prologue
   added there runs on create, restart and republish alike.
4. **`after_create_cmd_hook` is already elided from BaaS logs**
   (`baas_service.py:176-188`), which is what makes it safe to embed a
   short-lived signed URL in it.
5. **`ObjectStoragePlugin.sign_url(key, expires)` exists**, so a container can
   pull an object without holding any store credential of its own.
6. **The managed-files store from W8 is the right home for the bytes.**
   `ManagedFilesStore` already keeps the platform's own copy of a bot's
   manifest-delivered files by category and namespace, and
   `ManagedFilesComposeReader` is what the teclaw composer reads. Adding a
   category is the shape it was built for.
7. **`${BOT_ARCH}` already resolves to `amd64`** (`schema/placeholders.py`,
   `BOT_ARCH_VALUE`), contrary to the W9 progress table — W1 shipped it. Only
   the ELF verification half of that row is outstanding.
8. **The schema already parses a `cli_tools` entry** and already enforces the
   mandatory `digest` (`schema/entries.py`), because W1 parses the whole
   vocabulary. `fetch/limits.py` already carries the 200 MiB `cli_tools` width.
   The category is refused today only by the capability gate.

## The delivery decision

**One contract, two implementers — and the platform ships ARCA's
implementation of it.**

The already-merged `cliToolRef` says *"physical placement and PATH exposure
decided by the engine owner"*, and its `md5` is *"the engine's change test, not
an integrity gate"*. That is a declarative desired-state protocol, and it is
what teclaw implements per `teclaw-cli-contract.zh-CN.md` §3.4: placement,
`md5`-based skip, executable bit, `PATH`, full-replacement semantics.

ARCA-family engines were promised **zero changes**
(`engine-requirements.zh-CN.md:16`), and A2 was scoped as "confirm your PATH
injection point", not "implement a protocol". So rather than asking five images
to implement the contract, the platform emits a **boot-chain prologue** ahead of
the engine's own boot script that implements the same four behaviours once, in
shell, for every ARCA image.

The consequence that makes this coherent: because the prologue pulls the tools
itself at boot, **`cli_tools` needs no bound device on either family**. It is a
pre-container construct everywhere, so a bot created with tools has them in its
*first* container — the business case — rather than one restart later.

An ARCA engine that later wants its own tools directory or sandbox policy
implements the contract itself and tells the platform to skip the prologue.
That is option 1's end state, reached without a contract change.

## User Stories

- As a bot owner, I declare a tool with a URL and a `sha256`, and the model can
  invoke it by name in the container.
- As a bot owner, I declare a tool that lives inside a `.tar.gz` by naming its
  `subpath`, and only that one file is delivered.
- As a bot owner, I create a bot whose manifest declares tools, and its **first**
  container already has them on `PATH`.
- As a bot owner, I change a tool's version and the next provisioning replaces
  it; I remove it from the manifest and it is gone from the container.
- As a bot owner, I declare a tool built for the wrong architecture and the
  apply report tells me so, instead of the model hitting `exec format error`
  mid-task.
- As a bot owner, I omit `digest` and the `PUT` is refused, because the platform
  will not distribute an unpinned executable on my behalf.
- As the teclaw engine owner, the artifact tells me exactly which single file to
  place per tool, and an `md5` that says whether I already have it.
- As an operator, the ARCA prologue is inert for every bot that declares no
  tools — the composed start command is byte-identical to today's.

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
- [ ] `cli_tools` is a **`PRE_CONTAINER`** construct in both delivery
      strategies, since nothing about it requires a bound device.
- [ ] A bot's tool objects are deleted when a W13 creation ends without a bot,
      with the rest of that bot's manifest state.

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

### ARCA arm

- [ ] `build_start_command` gains a platform-generated prologue, ahead of the
      engine's own boot chain, that for each declared tool: pulls the object via
      a short-lived signed URL, verifies the recorded `md5`, places it in a
      platform-defined tools directory on the bot's NAS, sets the executable
      bit, and prepends that directory to `PATH` for everything the chain starts.
- [ ] The prologue implements the same four contract behaviours as teclaw:
      placement, `md5` change test (a matching `md5` skips the download and the
      replace), executable bit, and **full replacement** — a tool in the
      directory that the desired state no longer names is removed.
- [ ] **A bot with no declared tools composes a byte-identical start command to
      today's.** The prologue is emitted only when the bot has a non-empty
      `cli_tools` desired state, and #935's existing start-command assertion is
      kept unedited.
- [ ] The prologue's failure does not silently strand the bot: a failed pull or
      an `md5` mismatch is visible in the boot log with the tool's name, and the
      chain's exit status behaves as the runtime's existing contract requires.
- [ ] Signed URLs expire on the order of the boot window, not days, and the hook
      carrying them stays elided in BaaS logs.
- [ ] The prologue is emitted per deploy runtime through the composer that
      already owns that runtime's chain; no engine image is modified.

### `PUT` timing, stated rather than discovered

- [ ] On **teclaw**, a `PUT` that changes `cli_tools` takes effect immediately —
      the apply materialises to the store and the closing redeliver carries the
      new artifact, per §2.6.
- [ ] On **ARCA**, a `PUT` that changes `cli_tools` is *delivered* now (the
      store is converged and the apply report says so) and becomes *effective*
      at the next device provisioning — create, restart or republish — exactly
      like `script`. The apply response carries a delivery note saying so in
      those terms.
- [ ] This is an explicit, documented **§2.6 exception for this one category on
      one family**, recorded in the user manual and the work-items entry rather
      than left for a user to discover.

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
- [ ] The orchestrator and the order table stay engine-agnostic; the ARCA
      prologue lives in the deploy composer, not in the materialiser.
- [ ] Every existing manifest, artifact, creation and deploy test passes with
      assertions untouched.

## Decisions

**D-1 — One declarative contract; the platform implements ARCA's side of it.**
Rejected: an engine-side *command* protocol, where the platform issues
`chmod`/`mv`/`PATH` instructions. It contradicts `manifest-schema` §6's
commitment that physical placement is always the engine's decision; it makes
`cliToolRef.md5` meaningless (the platform would have to compute the diff, which
needs container state it does not have); full replacement and idempotent
redelivery stop being free; and a general remote-exec channel into customer
containers is the largest security surface this feature could add, in a feature
whose whole posture is supply-chain narrowing. Also rejected: asking five ARCA
images to implement the contract now, which breaks the zero-changes promise for
no benefit this iteration.

**D-2 — `cli_tools` is a pre-container construct on both families.** It follows
from D-1: the ARCA prologue pulls at boot, so nothing needs a bound device. This
is what makes the first container of a newly created bot already carry its
tools.

**D-3 — Effectiveness on ARCA is deferred to the next provisioning.** The cost
of D-1, stated rather than discovered. Same lifecycle as `script`, and the only
§2.6 exception in the feature.

**D-4 — The managed-files store is the single desired-state record for both
arms.** teclaw's composer reads it, the ARCA prologue pulls from it. One
materialiser, one convergence rule, two deliveries.

**D-5 — Convergence keys on `digest` *and* `subpath`.** Stated in the work item;
restated here because judging on `digest` alone is the silent-wrong-file bug.

**D-6 — `SCHEMA_VERSION` stays 4.** Settled with the teclaw owner on 2026-08-31
and already pinned by a test. The accepted cost, written down: `schema_version`
no longer tracks this contract's evolution, so "does this artifact carry
`cli_tools`?" is answered by probing for the field.

**D-7 — v1 delivers one self-contained executable per entry.** The flattening
that removed `entrypoints` also removed the shape that could express "executable
but not a command", so an in-package helper cannot be made executable. Rather
than reopen "do we trust an archive's mode bits", v1 states the limit.

## In Scope

- The capability unlock and the content-dependent `subpath` validation.
- The `cli_tools` materialiser: fetch, `sha256` enforcement, unpack, `subpath`
  selection, ELF verification, `md5`, store write, replacement semantics.
- The `cli_tools` category in the managed-files store.
- The teclaw arm: composer `cli_tools` refs and `ownership.cli_tools`.
- The ARCA arm: the boot-chain prologue and its emission per deploy runtime.
- The apply-report entries and the ARCA delivery note.
- Schema, user manual, teclaw contract reconciliation, and work-items updates.

## Out of Scope

- **`bcs-cli` as the first consumer.** The explicit next step: retire the
  singlebox script that hand-places it and onboard it through the manifest.
  Deliberately deferred so the mechanism lands first.
- **The default-skillset tool-usage skill.** The per-engine default skill set
  entry that teaches the model which tools exist and how to call them.
  `SkillSetService` already has the mechanism; wiring it is follow-up work.
- **An ARCA engine owning its own placement.** The opt-out that lets an image
  implement the contract itself and skip the prologue. Designed for by D-1, not
  built.
- **Multi-architecture sources.** `linux/amd64` only, one URL per tool (X3).
  `${BOT_ARCH}` exists so that a future mixed fleet changes where the value
  comes from, not the schema.
- **Package-manager installs.** Imperative, and already routed to `script`.
- **`engine_config`**, still out on the X2/T3 decision.

## Open Questions

None blocking. Two items to confirm as the plan lands, neither of which changes
the design:

- The exact NAS tools directory per deploy runtime, and confirmation that the
  runtime's boot chain starts the engine as a user that reads it — the concrete
  form of A2, now one verification per image rather than one design per engine.
- Whether the signed-URL expiry needs to exceed a slow first boot; the fallback
  is a longer expiry, not a different mechanism.
