# The apply engine takes a typed desired state

> Prepares RSI-04 (direct delivery of a compiled revision). Decided with the
> issue owner on 2026-10-23 in the thread that followed the RSI-03 merge
> (#2907): the Manifest and the materialisers are decoupled first; RSI-04
> starts only when this item is done. Issue #3064 (content-store extraction,
> merged as #3072) was its precondition. Review round 1 on #3083 folded in
> (compiler naming, resources root, one PR).

## Summary

Today the apply engine's materialisers read raw Manifest entries
(`Sequence[dict[str, Any]]` straight out of the parsed YAML), interpret the
document grammar themselves (`from`, `subpath`, `unpack`, `content`, the
trailing slash that tells a file from a directory), and fetch sources inside
their `resolve` stage. The Genome Registry compiles a revision into a dict
shaped like a Manifest document, with source spellings the Manifest parser
does not accept, so that one day it can be fed to the same materialisers.

This item turns that around. The apply engine becomes its own core
component with a **typed desired state** as its only input: enums over
strings, objects over dicts, typed recursively. Two **compilers** emit it:
the Bot Config Manifest compiles a stored document (parse, resolve sources,
fetch, file the bytes in the content store, emit) and the Genome Registry
compiles a revision. The materialisers take the desired state and nothing
else; fetching leaves their `resolve` stage. The engine gains the two
totality rules a complete desired state needs: the bot's **resources root**
holds exactly the declared resources after an apply (#2162), and
`script: null` removes the script (#2862).

On the genome side, "which revision does this bot run" stops being a join
through apply ids. Drift is **observed**: the registry reads the bot's
current state, the way capture does, and compares it with `active`. The
compiled document, its digest and the apply reference disappear from
promotions. The delivery seam stays unbound; binding it is RSI-04.

### Terms

- **Apply engine.** The component that converges one bot to a desired
  state: the orchestrator, the materialisers, their ports, the delivery
  strategies, the apply run (lock, task, report).
- **Desired state.** The typed, complete description of what a bot should
  hold, per construct: identity files, resources, skills, MCP servers,
  CLI tools, the startup script, engine config. A construct is either
  *declared* (with zero or more entries) or *not declared* (untouched, not
  reported), exactly the distinction `declared_entries` draws today.
- **Compiler.** Code that emits a desired state from something else: the
  **Manifest compiler** from the bot's stored Manifest document, the
  **genome compiler** from a revision. A compiler owns the grammar of its
  input and every fetch; the engine never sees either. Nothing produces a
  Manifest document; the name says what goes in, not what comes out.
- **State reference.** What an apply run is asked to converge to, typed:
  `ManifestStateRef()` (the bot's stored document; a bot has one, so it
  carries nothing) or `GenomeStateRef(promotion_id)` (the revision the named
  promotion delivers). The run service picks the compiler by the
  reference's type and re-compiles from it in the worker; nothing
  serialised travels.
- **Stored content.** Bytes in the content store, named by digest. Every
  file in a desired state is stored content; directories are one
  `acm-tree-v1` blob. A compiler has filed the bytes before it emits.
- **Resources root.** The bot's workspace root, as the resources Open API
  defines it (every path workspace-relative, no path meaning the root).
  Declared resource paths are workspace-relative and may not name a
  **protected entry**: the identity files at the root, `skills/`,
  `skills-pool/` and `skills-local/`, the engine's `state/`, `conf/` and `<engine>_conf/`, and
  dotfiles at any depth — one reviewed list in
  `core/workspace/protected_entries.py`, read by the Open API's read-only
  policy, the clear and both compilers. Converging resources means the
  workspace holds exactly the declared entries afterwards, protected entries
  aside; whatever else was there, including files the bot wrote itself, is
  gone, because the engine cannot tell a platform upload from a bot-written
  file and does not try. (Review round 2, 2026-10-26: `data/` was the
  legacy console upload's directory, not a contract; the Open API never had
  one.)
- **Materialiser.** Unchanged in role (`resolve` → `plan` → `write`, per
  construct). `resolve` now takes typed entries and keeps only the checks
  that belong to the engine or its ports: engine capability, permission,
  package validation, admission, reserved names.
- **Drift.** The bot's current state differs from `active`. Observed by
  reading the bot, never inferred from delivery records.

## Motivation

- RSI-04 must deliver a revision with no Manifest document in between.
  With raw dicts as the materialiser input there is no way to do that
  without inventing Manifest grammar the parser refuses (`protocol:
  content_store`, `center`, `repo`), which is what `compile.py` does now.
- The materialisers hold two unrelated jobs: interpreting a document and
  converging a bot. Grammar changes (an entry key, a source kind) reach six
  files, and a fetch failure is reported from inside the component that
  should only write.
- Drift tracked through apply ids and document digests answers only one
  question: was the last apply a promotion. It cannot see a console edit, a
  session that wrote a file, or an OpenAPI change. Observing the bot
  answers the real question and removes three columns and a join.
- Two known gaps make a desired state non-total today: a resource the
  previous document declared stays on disk when omitted, and `script: null`
  is read as "absent".

## User Stories

- As the Genome Registry, I want to hand the apply engine a typed desired
  state compiled from a revision, so that RSI-04 is a binding and a
  compiler, not a Manifest emulation.
- As a Manifest contributor, I want the document grammar, source resolution
  and fetching in one place that emits a typed state, so that a grammar
  change touches the compiler and its tests only.
- As a materialiser author, I want typed entries whose content is already
  in the content store, so that `resolve` validates and `write` writes, and
  neither fetches.
- As a bot owner, I want a promotion made by an evolution run or by an
  automatic rule refused when the bot no longer matches `active`, so that
  an out-of-band change is never silently overwritten; and I want my own
  promotions never refused for drift, because an owner promotion is how
  the bot is brought back in line: capture the bot as it is now and promote
  that (keep the change), or promote `active` again (discard it).
- As an operator reading an apply report, I want it to say what it
  converged to (the bot's Manifest document, or the revision a named
  promotion delivers) and to keep today's categories, entries, removals and
  git source resolutions.
- As a Manifest user removing a resource entry from my document, I want the
  file gone from the bot after the next apply (#2162).
- As a Manifest user setting `script: null`, I want the startup script
  removed (#2862).

## Acceptance Criteria

### Component and model

- [ ] A new core component `core/apply_engine/` owns the desired-state
      model, the materialiser contract, the orchestrator, the six
      materialisers, their narrow ports and delegates, the delivery
      strategies and the apply run (service, task, lock, report codec,
      repository models). It declares a `## Context Boundary` and imports
      nothing from `core.bot_config_manifest` at runtime; the architecture
      tests enforce both.
- [ ] `DesiredState` is a frozen, recursively typed model: one declaration
      per construct (`None` when not declared), each a tuple of typed
      entries plus the compiler's per-entry failures. Enums for every closed
      set (construct, skill origin, MCP transport and endpoint env, unpack
      kind); objects for every entry; no `dict[str, Any]` except
      `engine_config.config`, whose keys are undecided (G-4, #2858).
- [ ] File content is `ContentRef(digest)`; a skill is a local package
      (an `acm-tree-v1` blob), a Skill Center skill (name + pinned
      version) or a shared-repository skill (name + path); a directory
      resource is a path ending in `/` plus a tree blob; an MCP server is a
      code plus a typed override; the script is its body; a CLI tool is a
      name, version, pinned digest, subpath and unpack kind plus stored
      content.
- [ ] The `Materialiser` contract takes typed entries: `resolve(ctx,
      entries: Sequence[E])`. No materialiser imports the Manifest schema,
      its source resolver, its fetchers or its entry delivery.
- [ ] The orchestrator takes a `DesiredState`; the compiler's per-entry
      failures abort the construct exactly as a `resolve` failure does
      today (named entries `failed`, neighbours `skipped`, area untouched).

### Compilers and the apply run

- [ ] A `DesiredStateCompiler` contract (`compile(ctx, ref) -> Compiled`)
      and a registry of compilers keyed by the state reference type. The
      apply run is started with a `StateRef`; the worker re-compiles from
      it, never from a serialised copy. The task payload carries the
      reference's identifiers only.
- [ ] The Manifest compiler (`core/bot_config_manifest/compiler/`) turns the
      bot's stored, validated document into a desired state: it resolves
      named and inline sources, fetches through the existing funnel under
      the existing per-apply budget and git session, files every byte in
      the content store (inline `content:` and script bodies included; the
      inline bytes as produced content), substitutes `${BOT_*}`, builds and
      validates skill packages, selects archive subtrees, and reports per
      entry what it could not compile. It is the only code that reads a
      document's entry keys.
- [ ] The genome compiler (`core/bot_genome/`) turns a revision into the
      same model, replacing `compile_spec`/`CompiledManifest`: persona →
      identity, skills by origin, resources, MCP, script body read from the
      store; `memory`, `cli_tools` and `engine_config` not declared. The
      registry's `compile(bot, rev)` returns a `DesiredState`.
- [ ] Equivalence: for every `api-contracts/genome/v1/examples/manifest-v1`
      case that has a revision, the Manifest compiler on `manifest.yaml`
      (sources served from the example blobs) and the genome compiler on
      `revision.json` emit equal desired states, up to the notes the
      Manifest side attaches. The `compiled.yaml` files are retired.
- [ ] CLI tools are pre-fetched by the Manifest compiler like every other
      category; `CliToolService` takes its bytes from the content store and
      no longer owns a fetch. The "unchanged entries are not fetched"
      behaviour holds: a pinned digest already filed for the bot is not
      fetched again.
- [ ] The apply report keeps its shape (categories, entries, removals,
      notes, git `sources`) and gains a `state` object **inside the report
      JSON** that is already stored in the `report` column: `{"kind":
      "manifest"}` or `{"kind": "genome", "promotion_id": "…"}`. No new
      column. Reports persisted before this change read back as
      `manifest`. The HTTP apply surface (`POST …/config-manifest/apply`,
      `GET …/applies/{id}`, `GET …/last-apply`, `PUT /config-manifest`,
      create-with-manifest) is unchanged in paths, status codes and
      payloads other than the added `state` object.
- [ ] Dry run still fetches and files (as today) and still writes nothing to
      the bot.
- [ ] Every existing Manifest, creation, delivery-strategy, task, DI and
      HTTP test passes, moved or adapted only for import paths, the typed
      entries, and the compiler split (a grammar refusal that a materialiser
      test asserted today is asserted on the compiler instead).

### Totality

- [ ] `script: null` in a document is "declared, none": the compiler emits
      an empty script declaration and the apply removes the startup script
      (#2862). The validator accepts `null`; the schema docs say so.
- [ ] The resources root is the workspace root. The Manifest validator
      refuses a `resources` entry whose `path` names a protected entry
      (`resources_protected_entry`), the Manifest compiler refuses the same
      at apply time for a stored document, and the genome's spec checks
      refuse it for a revision; the protected list is
      `core/workspace/protected_entries.py` and the schema states the rule.
- [ ] The resources materialiser converges by **clear then write**: it
      removes the resources root with the device road's existing tree
      delete (`DeviceFileSystem.delete_tree`, which every transport already
      implements), then uploads every declared member. `plan` lists the
      root once and reports as `removals` every path that will not be
      written again; there are no per-member `exists` probes. A failed
      clear fails the category before any write (#2162). No new engine
      capability or endpoint is introduced; what each engine's file API
      must guarantee for this to hold is written down for the teclaw and
      AICoding teams (a separate document, in Chinese, per the review).
- [ ] The platform's resource rows (`ac_resource`) follow the root: on
      clear, the bot's file rows under the root are deleted; every file the
      materialiser writes gets a row with source `manifest`, so capture sees
      Manifest-written resources (closing the RSI-03 note in
      `capture/bot_state.py`) and never meets a row whose file is gone.
- [ ] `PlatformResource` (the platform-managed store road) clears by
      deleting its resources category listing.

### Genome: drift observed, delivery records dropped

- [ ] `GenomeRegistryProtocol.applied_revision` and
      `GenomePromotionRepositoryProtocol.by_apply_reference` are removed. The
      registry no longer depends on the Manifest apply service.
- [ ] `GenomeRegistryProtocol.drift(bot)` reads the bot's current state the
      way capture does, without recording, and answers `None` when the state
      equals `active` (same revision id under `active`'s policy, both specs
      first put in one representation: a directory resource is one tree in a
      revision but its member files, one row each, on the applied bot) or
      `Drift(active, observed)` otherwise. When the bot cannot be read at
      all (the same cases in which capture refuses: a platform-managed
      teclaw bot with no device, a resource row whose file cannot be read),
      the drift check does not guess "no drift"; it raises that
      `CaptureRefused`, and a promotion that needed the check is refused
      with it (422), naming what could not be read.
- [ ] Promotion refuses with `409 drifted` for a non-user actor when `drift`
      answers; the error carries `{active, observed}`. An owner promotion is
      not checked.
- [ ] `ac_genome_promotion` loses `document_digest`, `document_json`,
      `apply_reference` and `idx_genome_promotion_bot_apply`; the ORM, the
      row model, the `Promotion` dataclass and `PromotionPayload` lose the
      same fields and `document`. The DDL is edited in place (the table is
      not deployed; same decision as #3072). The DDL/ORM contract test still
      pins them together.
- [ ] `RevisionApplier.preflight/start` take a `DesiredState`;
      `PendingDeliveryApplier` stays bound. It exists because RSI-03 shipped
      promotion before anything could deliver: it refuses every promotion
      as `not_appliable` before any ref moves, so `active` never points at
      a revision the bot does not run. RSI-04 replaces it with the direct
      applier (compile → start an apply run with `GenomeStateRef`). The
      genome compiler is registered now; nothing calls it in production
      until then.
- [ ] Docs say what the code does: `01-genome.md` §2.7, §5.5, §8, §11,
      §13.3, §14; `08-promotion.md` §2.4, §7.1, §10; `06-evolution-run.md`
      drift; `work-items.md` RSI-03/04; the contract README §7 and
      `manifest-v1-compatibility.md`; the `bot_genome`, `bot_config_manifest`
      and new `apply_engine` READMEs; `CONTEXT-MAP.md`; the zh-CN mirrors.
      One ADR records the two system decisions (typed desired state as the
      engine's input; drift observed, not tracked).

### Hygiene

- [ ] No file over 1,000 lines without an allowlist entry; no formatting of
      lines the change does not touch; every new Python line ≤ 120 columns;
      `T | None` only for domain-valid absence.

## In Scope

- The new component, the model, the contract change, the orchestrator and
  the six materialisers; the move of the apply run and the delivery
  strategies; DI rewiring; test moves.
- The Manifest compiler and the removal of grammar and fetch from the
  materialisers; `declared_entries` and the `script: null` fix.
- CLI tools pre-fetch and the `CliToolService` acquire path.
- The resources root: validator rule, compiler refusal, clear-then-write
  in the materialiser, `ac_resource` row reconciliation, the platform-store
  road; the file-API expectations document for the engine teams.
- Genome: the compiler, `compile` on the model, `drift`, the removals, the
  DDL edit, HTTP schema, repository, tests, docs, ADR.

## Out of Scope

- Binding the direct applier (promotion → apply run), Center and
  shared-repository skill materialisation, service-bot publish, the
  singlebox done-when: **RSI-04**. The model carries the Center and repo
  variants; the skills materialiser refuses them with a readable reason
  until RSI-04 delivers them.
- `engine_config` application (#2858, G-4) and the `memory` category
  (RSI-05). The model carries `engine_config` as a declaration with an
  untyped mapping and no materialiser, as today.
- The Manifest grammar beyond the resources-root rule: no new source kinds,
  no new entry keys. The `/config-manifest` endpoints keep their paths and
  payloads.
- Capture of platform-managed teclaw bots (RSI-03 follow-up).
- The deprecated `/api/resources/file` upload (it writes under
  `workspace/data` and records rows relative to that directory, unlike the
  Open API and the current internal file routes): capture reads such a row's
  file where that layout put it; the route itself is left alone.

## Decisions taken with the issue owner (2026-10-23, incl. review round 1)

1. **Resources totality by clearing the resources root before writing**,
   not by a walk of apply-report history and not by an upload ledger. The
   engine cannot tell a platform upload from a file the bot wrote under the
   root, so both go; the root is what makes that acceptable.
2. **The typed model is the materialisers' input**; compilers emit it; the
   materialiser's `Intent` stays the private output of `resolve`.
3. **Drift is observed** (capture compared with `active`), so
   `applied_revision`, `by_apply_reference` and `apply_reference` go, and
   the promotion no longer records a compiled document or its digest.
4. **This refactor lands before RSI-04**, which then binds the applier.
5. **One pull request.**

6. **The resources root is the workspace root, with one protected list**
   (review round 2, 2026-10-26). The resources Open API already treats the
   workspace as the root and refuses the identity files and dotfiles; the
   apply engine follows the same contract and the same list, extended to the
   system directories the Open API only hides (`skills/`, `skills-pool/`,
   `skills-local/`, `state/`, `conf/`, `<engine>_conf/`). `data/` is dropped as a root; it is
   an ordinary sub-directory where the deprecated console upload happened to
   write. An engine-owned resources root (the engine excluding its own system
   files) stays a possible later contract change, not part of this item.

## Decisions taken here, flagged for review

- **Component name and location:** `core/apply_engine/`. The vocabulary
  ("apply", "apply report", "materialiser") is already public through the
  HTTP API and the docs.
- **"Compiler", not "producer".** The registry already says `compile`; a
  Manifest compiler compiles a document, a genome compiler compiles a
  revision, both into a desired state.
- **The state reference is a typed union**, not a `(kind, string)` pair,
  so the Manifest case carries nothing and the genome case carries a
  promotion id by name. The report names the promotion, and the worker
  re-reads the revision from the promotion row, so a report can never claim
  a revision no promotion named.
- **Genome DDL edited in place** rather than a new numbered file, following
  the #3072 decision that these tables are not deployed.
- **`compiled.yaml` cases are retired** from the genome contract examples
  and replaced by the compiler-equivalence test; `manifest-v1-compatibility.md`
  is rewritten around that equivalence. This touches the RSI-02 contract
  folder, which the issue owner owns.

## Open Questions

- ~~**Resources root confirmed as `data/`?**~~ Resolved the other way:
  decision 6.
- **Bind the direct applier in this item?** Everything it needs exists
  after the apply run takes a `StateRef`; the binding itself is a provider
  and a thin applier. Left to RSI-04 as agreed, unless the owner prefers
  not to carry the pending applier through one more item.
