# The apply engine takes a typed desired state

> Prepares RSI-04 (direct delivery of a compiled revision). Decided with the
> issue owner on 2026-10-23 in the thread that followed the RSI-03 merge
> (#2907): the Manifest and the materialisers are decoupled first; RSI-04
> starts only when this item is done. Issue #3064 (content-store extraction,
> merged as #3072) was its precondition.

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
strings, objects over dicts, typed recursively. Two **producers** emit it,
the Bot Config Manifest (parse, resolve sources, fetch, file the bytes in the
content store, emit) and the Genome Registry (render a revision). The
materialisers take the desired state and nothing else; fetching leaves their
`resolve` stage. The engine gains the two totality rules a complete desired
state needs: a resource the previous delivery wrote and the new state omits
is removed (#2162), and `script: null` removes the script (#2862).

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
- **Producer.** Code that emits a desired state from something else: the
  Manifest producer from a stored Manifest document, the genome producer
  from a revision. A producer owns the grammar of its input and every
  fetch; the engine never sees either.
- **Stored content.** Bytes in the content store, named by digest. Every
  file in a desired state is stored content; directories are one
  `acm-tree-v1` blob. A producer has filed the bytes before it emits.
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
  state built from a revision, so that RSI-04 is a binding and a producer,
  not a Manifest emulation.
- As a Manifest contributor, I want the document grammar, source resolution
  and fetching in one place that emits a typed state, so that a grammar
  change touches the producer and its tests only.
- As a materialiser author, I want typed entries whose content is already
  in the content store, so that `resolve` validates and `write` writes, and
  neither fetches.
- As a bot owner promoting a revision, I want a promotion by a run or a gate
  refused when the bot no longer matches `active`, whoever changed it and
  however, so that an out-of-band change is never silently overwritten.
- As an operator reading an apply report, I want it to say what it applied
  (which producer, which reference) and to keep today's categories,
  entries, removals and git source resolutions.
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
      entries plus the producer's per-entry failures. Enums for every closed
      set (construct, skill origin, MCP transport and endpoint env, unpack
      kind); objects for every entry; no `dict[str, Any]` except
      `engine_config.config`, whose keys are undecided (G-4, #2858).
- [ ] File content is `StoredContent(digest)`; a skill is a local package
      (an `acm-tree-v1` blob), a Skill Center skill (name + pinned
      version) or a shared-repository skill (name + path); a directory
      resource is a path ending in `/` plus a tree blob; an MCP server is a
      code plus a typed override; the script is its body; a CLI tool is a
      name, version, pinned digest, subpath and unpack kind plus stored
      content.
- [ ] The `Materialiser` contract takes typed entries: `resolve(ctx,
      entries: Sequence[E])`. No materialiser imports the Manifest schema,
      its source resolver, its fetchers or its entry delivery.
- [ ] The orchestrator takes a `DesiredState`; the producer's per-entry
      failures abort the construct exactly as a `resolve` failure does
      today (named entries `failed`, neighbours `skipped`, area untouched).

### Producers and the apply run

- [ ] A `DesiredStateProducer` contract (`kind`, `produce(ctx, reference)`)
      and a registry of producers keyed by kind. The apply run is started
      with a `StateRef(producer, reference)`; the worker re-produces the
      state from the reference, never from a serialised copy. The task
      payload still carries identifiers only.
- [ ] The Manifest producer (`core/bot_config_manifest/producer/`) turns a
      stored, validated document into a desired state: it resolves named
      and inline sources, fetches through the existing funnel under the
      existing per-apply budget and git session, files every byte in the
      content store (inline `content:` and script bodies included; the
      inline bytes as produced content), substitutes `${BOT_*}`, builds and
      validates skill packages, selects archive subtrees, and reports per
      entry what it could not produce. It is the only code that reads a
      document's entry keys.
- [ ] The genome producer (`core/bot_genome/`) turns a revision into the
      same model, replacing `compile_spec`/`CompiledManifest`: persona →
      identity, skills by origin, resources, MCP, script body read from the
      store; `memory`, `cli_tools` and `engine_config` not declared. The
      registry's `compile` returns a `DesiredState`.
- [ ] Equivalence: for every `api-contracts/genome/v1/examples/manifest-v1`
      case that has a revision, the Manifest producer on `manifest.yaml`
      (sources served from the example blobs) and the genome producer on
      `revision.json` emit equal desired states, up to the notes the
      Manifest side attaches. The `compiled.yaml` files are retired.
- [ ] CLI tools are pre-fetched by the Manifest producer like every other
      category; `CliToolService` takes its bytes from the content store and
      no longer owns a fetch. The "unchanged entries are not fetched"
      behaviour holds: a pinned digest already filed for the bot is not
      fetched again.
- [ ] The apply report keeps its shape (categories, entries, removals,
      notes, git `sources`) and gains `state: {producer, reference}`.
      Reports persisted before this change read back with
      `producer: manifest`. The HTTP apply surface
      (`POST …/config-manifest/apply`, `GET …/applies/{id}`,
      `GET …/last-apply`, `PUT /config-manifest`, create-with-manifest) is
      unchanged in paths, status codes and payloads other than the added
      `state` object.
- [ ] Dry run still fetches and files (as today) and still writes nothing to
      the bot.
- [ ] Every existing Manifest, creation, delivery-strategy, task, DI and
      HTTP test passes, moved or adapted only for import paths, the typed
      entries, and the producer split (a grammar refusal that a materialiser
      test asserted today is asserted on the producer instead).

### Totality

- [ ] `script: null` in a document is "declared, none": the producer emits
      an empty script declaration and the apply removes the startup script
      (#2862). The validator accepts `null`; the schema docs say so.
- [ ] The engine adapter offers an engine-owned **clear uploads** operation:
      every file the platform uploaded through the file API is recorded by
      the engine (path and origin label) and can be removed in one call,
      optionally by origin. Files the bot created itself are never touched.
      The operation is a declared capability, implemented for `openclaw` and
      `claude_code`, with router tests.
- [ ] The backend's resource road (`DeviceFileSystem`, `ResourceFileService`,
      `ResourceFilePort`, `PlatformResource`) exposes `clear_uploads` and
      labels its uploads with an origin: Manifest apply uploads carry
      `manifest`, console and OpenAPI uploads carry `user`.
- [ ] The resources materialiser converges by **clear then write**: it clears
      its own origin, reports what the clear removed on the plan's
      `removals`, then uploads every declared member (#2162). On a transport
      that cannot clear (today: the teclaw device road), it keeps the
      per-entry behaviour and says so in a note; nothing is silently skipped.

### Genome: drift observed, delivery records dropped

- [ ] `GenomeRegistryProtocol.applied_revision` and
      `GenomePromotionRepositoryProtocol.by_apply_reference` are removed. The
      registry no longer depends on the Manifest apply service.
- [ ] `GenomeRegistryProtocol.drift(bot)` reads the bot's current state the
      way capture does, without recording, and answers `None` when the state
      equals `active` (same revision id under `active`'s policy) or
      `Drift(active, observed)` otherwise. A capture refusal propagates.
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
      `PendingDeliveryApplier` stays bound and still refuses every promotion
      as `not_appliable`. The genome producer is registered under kind
      `genome`; its reference is a promotion id, resolved to the promotion's
      revision. Nothing calls it in production until RSI-04.
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
- The Manifest producer and the removal of grammar and fetch from the
  materialisers; `declared_entries` and the `script: null` fix.
- CLI tools pre-fetch and the `CliToolService` acquire path.
- Engine adapter: upload ledger, `clear uploads` endpoint and capability, for
  both engines; backend resource road and port; resources materialiser
  clear-then-write; `PlatformResource.clear_uploads`.
- Genome: the producer, `compile` on the model, `drift`, the removals, the
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
- Reconciling `ac_resource` rows when a total delivery clears user uploads
  (RSI-04, which is the first caller to clear every origin).
- The Manifest grammar: no new source kinds, no new entry keys. The
  `/config-manifest` endpoints keep their paths and payloads.
- Capture of files the Manifest wrote outside `ac_resource` rows, and
  capture of platform-managed teclaw bots (RSI-03 follow-ups).
- A ledger for uploads made before this change: they are not in the
  engine's record until the next apply re-uploads them, so the first apply
  after deployment removes nothing it did not write itself.

## Decisions taken with the issue owner (2026-10-23)

1. **Resources totality by an engine-owned remove-all**, not by a walk of
   apply-report history. The engine keeps the record; the backend calls one
   operation.
2. **The typed model is the materialisers' input**; producers emit it; the
   materialiser's `Intent` stays the private output of `resolve`.
3. **Drift is observed** (capture compared with `active`), so
   `applied_revision`, `by_apply_reference` and `apply_reference` go, and
   the promotion no longer records a compiled document or its digest.
4. **This refactor lands before RSI-04**, which then binds the applier.

## Decisions taken here, flagged for review

- **Component name and location:** `core/apply_engine/`. The vocabulary
  ("apply", "apply report", "materialiser") is already public through the
  HTTP API and the docs.
- **Clear-uploads scope on a Manifest apply:** only uploads with origin
  `manifest`. A console upload is a user's file; a Manifest apply removing
  it would be a behaviour change #2162 does not ask for. A total delivery
  (RSI-04) clears every origin, because a revision is total and capture
  records user uploads.
- **Transports that cannot clear** (teclaw device road, whose file API is a
  different server) keep today's per-entry convergence with a note, rather
  than failing the category.
- **Genome DDL edited in place** rather than a new numbered file, following
  the #3072 decision that these tables are not deployed.
- **The genome producer's reference is a promotion id.** The report then
  names the promotion, and the worker re-reads the revision from the
  promotion row, so a report can never claim a revision no promotion named.
- **`compiled.yaml` cases are retired** from the genome contract examples
  and replaced by the producer-equivalence test; `manifest-v1-compatibility.md`
  is rewritten around that equivalence. This touches the RSI-02 contract
  folder, which the issue owner owns.

## Open Questions

- Does the user want this as one pull request or three stacked ones
  (engine component + Manifest producer; resources totality including the
  engine adapter; genome drift and removals)? The tasks are grouped so that
  either works; the branch instruction names one branch.
