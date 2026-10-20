# Genome Registry in Backend (RSI-03)

> Work item: `apps/backend/docs/bot-evolution/work-items.md` → RSI-03.
> Builds on the RSI-02 contract in
> [`apps/backend/api-contracts/genome/v1/`](../../api-contracts/genome/v1/README.md),
> which wins over this spec and over `01-genome.md` where they differ.

## Summary

Give every bot an immutable, content-addressed history of its definition:
**Genome Revisions**. A revision is recorded in one of two ways: from a whole
Bot Config Manifest document, or by applying a typed **Genome Patch** (a list
of small edits) to an existing revision, the patch's **base**, which becomes
the new revision's parent. Named **refs** (`active`, `draft`, …) point at
revisions and move only by compare-and-swap (explained under *Terms* below).
The registry can diff any two revisions, and a promotion operation moves
`active` and hands the promoted revision to the bot's delivery path. Owners
get versioned bots and a way back to any earlier revision; later evolution
work (runs, verification, the gate) gets the store it records candidates
into.

### Terms used in this spec

- **Base / parent.** Every revision except a bot's first has one parent: the
  revision it was made from. For a patch that is the patch's `base`, and it
  is required. For a Manifest document the document is the source of truth
  for every category it declares; `base` matters only for what it does not
  say: the categories it leaves out are copied from the base, and the base
  becomes the parent so the history stays one tree. When recording a
  document, `base` is optional: omitted, it defaults to the bot's `active`
  revision, and for a bot with no revisions yet to "no parent", with
  omitted categories captured from the bot's current state. A caller
  editing an unpromoted draft names the draft as `base` so the history
  records what the edit started from. Ids are content hashes, so recording
  the same content again returns the existing revision and does not change
  its parent.
- **When a whole document is recorded.** Recording a Manifest document
  means "this is the complete definition I want for the bot; record it as
  its next revision, descending from `base`". A patch cannot do everything,
  so this path exists for: the bot's first revision (a bot that already has
  a Manifest, or was set up in the console, needs a baseline before any
  patch can target it); owner changes no patch op can express, which by
  the contract are the MCP servers, the startup script, a Center skill's
  pinned version and the policy (§6.1: those change "only when an owner
  records a whole Manifest document"); reconciling drift by recording what
  the bot runs now and promoting it; and owners who keep their bot's
  Manifest in git and want history without writing patches.
- **Compare-and-swap (CAS).** A ref move carries the revision the caller
  believes the ref points at now; the move happens only if that is still
  true, otherwise it is refused with the current value. This is what stops
  two editors silently overwriting each other.
- **Ref event.** Every ref move is also written to an append-only log (the
  ref's name, from, to, who, why, when). Rows are only ever added, never
  changed or deleted, so the history of every move is kept.
- **Run, candidate.** An evolution run (RSI-08) is one execution of a
  strategy for one bot; it may submit several candidate patches. Each is
  recorded as a revision named `candidate/<run id>/<n>`, where `n` counts
  the run's candidates from 1 in submission order.
- **Drift.** A bot has drifted when what it actually runs is not what its
  `active` ref says, usually because someone wrote the Manifest directly
  (`PUT /config-manifest`, which records no revision) after the last
  promotion. Drift is detected, not prevented; the owner reconciles by
  promoting again.
- **In-process interface.** A Python service interface other Backend code
  calls directly (the way promotion calls the registry), as opposed to the
  HTTP endpoints external callers use.
- **Fixture.** A file checked in for tests to use in place of something
  real. Here: the example Manifests in the contract name git repositories
  and OSS objects that do not exist, so RSI-02 shipped the files they
  would contain, and the tests hand those files to the recorder instead of
  fetching anything.
- **Produced content.** Bytes that did not come from fetching a git or OSS
  source: a file a patch edits or adds, a file uploaded for a patch, or a
  file captured from the bot's current state. The manifest content store
  today only accepts fetched bytes with a source URL, so a write path for
  produced bytes is needed.

## Motivation

RSI-02 fixed the format of a revision and a patch but shipped no runtime
code. Nothing in Backend can yet record a revision, check a patch against a
policy, store a bot's content by digest for good, or tell which revision a
bot runs. RSI-03 is the next step on the critical path
RSI-01 → RSI-02 → RSI-03 → RSI-04 ("versioned bots that can go back to any
earlier revision"), and RSI-04 (Manifest content-store and Center sources,
the applied-document digest) and RSI-08 (the evolution service) both build
on the registry's service interface and its HTTP endpoints.

## User Stories

- As a **bot owner**, I want each edit I make through the genome API to be
  recorded as a revision with who, when and from what, so that I can see my
  bot's history and go back to any earlier revision.
- As a **bot owner**, I want to edit my bot as a small, itemized patch
  against a named base revision, so that two people (or a person and an
  automated actor) editing at the same time cannot silently overwrite each
  other.
- As a **bot owner or tenant admin**, I want the policy recorded with every
  revision to be enforced when a patch is recorded, so that nothing
  automated can touch a pinned file or a gene I have not opened up.
- As a **reviewer**, I want a per-gene diff between any two revisions, with
  text files shown as unified diffs and a risk tier for the change, so that
  I can judge a change without reading whole files.
- As an **engineer building the evolution service (RSI-08)**, I want a
  registry I can call from Backend code and the same operations as HTTP
  endpoints, so that a run can record each candidate patch as a revision
  named `candidate/<run id>/<n>`, and can ask whether the bot has drifted
  before it starts, without reaching into Backend tables.
- As an **engineer building RSI-04**, I want revisions compiled into a
  Manifest document that points at the content store by digest, so that the
  Manifest change I add has a producer to test against.

## Acceptance Criteria

Recording:
- [ ] A revision can be recorded from a Bot Config Manifest v1 document. The
      document is validated with the Manifest's own validator; every source
      is fetched once and stored in the manifest content store; the original
      YAML is kept as provenance; the stored revision validates against the
      contract schema and its id is the contract's hash.
- [ ] A Manifest document may leave categories out (to the Manifest, an
      omitted category means "leave the bot as it is"), but a revision must
      be total: every category present. So an omitted category is filled
      from the `base` revision (the one the caller names, or `active` when
      they name none; see *Terms*). A document that declares every category
      is recorded as it is, and `base` then only sets the parent. For a
      bot's first revision there is no base yet, so omitted categories are captured from
      what the bot has now (its identity files, skills, resources, MCP
      servers and script), which also covers bots that were set up through
      the console and never had a Manifest.
- [ ] Test, using the RSI-02 examples: for each example Manifest document in
      `api-contracts/genome/v1/examples/manifest-v1/`, recording it produces
      exactly the revision the example says it should (`revision.json`),
      and the two examples marked as refused are refused for the stated
      reasons. The examples name git repositories and OSS objects that do
      not exist; the test serves the files RSI-02 shipped for them instead
      of fetching anything over the network.
- [ ] A revision can be recorded from a patch against a base revision. All
      twelve ops apply as the contract defines; the contract's example
      patches, applied to their example base, give the example candidate
      revision (`02-support-bot-candidate.json`) and the stored patch digest
      is the candidate id.
- [ ] Recording a patch refuses, with the contract's reason codes
      (`base_mismatch`, `gene_not_mutable`, `pinned_item`,
      `gene_not_allowed`, `missing_rationale`, `schema`, `limit_exceeded`),
      a patch whose base is not the parent, whose ops do not apply exactly,
      that touches a gene outside `mutable_genes` or a run's `allowed_genes`,
      that overlaps a pin, that lacks a rationale or required evidence, or
      that exceeds the Manifest-mirrored limits.
- [ ] Anything apply would refuse is refused when recording, so a recorded
      revision is always one that could be applied. Concretely, the same
      checks Manifest apply runs before writing to a bot are run on the
      content of the new revision at record time: a persona file is valid
      UTF-8 and of a type the bot's engine accepts; a local skill's files
      form a valid skill package; every resource file has an allowed
      extension and size and the resource paths do not collide; an MCP
      entry's URL and headers are well formed, the tenant may enable that
      server, and its bot-level config is valid for the bot's engine. The
      contract README §6.4 lists them; every `examples/invalid` case that
      names one of them is refused by it.
- [ ] Recording content that already exists returns the existing revision
      unchanged: same id, same sequence number, same parent; no duplicate
      row. Going back is the same idea at the ref level: in the history
      A → B → C → D, promoting B again creates nothing and changes nothing
      on B (its parent stays A); only the `active` ref moves to B and
      `previous` to D, and the ref event log records that move.
- [ ] Each recorded revision reports its risk tier and `rewrite` flags by the
      contract's rules (risk-tiers data, the exact LCS measure, replacement
      detection, policy overrides); the published canonical-JSON and rewrite
      vectors are the oracle.

Refs and lineage:
- [ ] Refs `active`, `previous`, `canary`, `draft` and `candidate/<run>/<n>`
      exist per bot; every move is compare-and-swap on the expected
      revision, a lost race answers a conflict naming the current revision,
      and every move adds one row to the append-only ref event log with the
      actor and reason.
- [ ] Only the designated mover may move each ref kind: owners move `draft`;
      `active`, `previous` and `canary` move only through promotion;
      candidate refs are created by recording a run candidate and never
      moved afterwards.
- [ ] Revisions can be listed per bot newest first, filtered by status and by
      parent; a revision is retrievable by id, by `r<seq>`, or by ref name.
- [ ] A diff between any two revisions of the same bot lists each changed
      gene as added, removed or modified, carries a unified diff for modified
      text files, and reports the change's risk tier and rewrite flags.

Content:
- [ ] Bytes can be uploaded for a bot and are returned by digest, and bytes
      can be read by digest only when the digest is reachable from that bot's
      revisions or uploads; a read re-verifies the hash.

Promotion and drift:
- [ ] A revision compiles to a Manifest document whose file entries point at
      the content store by digest and whose Center skills are pinned by
      version, in the spelling the contract proposes for RSI-04. Each
      contract case under `examples/manifest-v1/` ships the expected result
      of that compilation as `compiled.yaml` (the document M′ of the
      round-trip definition); compiling the case's revision reproduces it.
      The document's digest is recorded with the revision.
- [ ] Promoting a revision moves `active` (and `previous`) in one
      compare-and-swap, marks the revision `promoted`, records a promotion
      with actor, reason, the compiled document digest and the apply
      reference, and starts the apply of the compiled document for a
      personal bot. The promotion is refused before any ref moves when the
      bot's Manifest cannot accept the compiled document (today: until
      RSI-04 adds the content-store and Center sources).
- [ ] The registry answers which revision a bot runs: it looks up the bot's
      last Manifest apply and the promotion that started that apply, if any.
      If the last apply was not started by a promotion (someone wrote the
      Manifest directly, so the promoted content has been overwritten), or
      its revision differs from `active`, the bot has drifted. Owner
      promotions on a drifted bot are allowed, because promoting is how the
      owner reconciles; automated promotions are refused.

Surface and tests:
- [ ] The HTTP endpoints of `01-genome.md` §12 and the promotion endpoint of
      `08-promotion.md` §10.1 exist under `/openapi/v1/bots/{bot_id}/genome/`
      with the standard envelope, authorization rows and admission modes,
      and the documented error statuses.
- [ ] Unit tests cover every module; the contract test keeps the vectors and
      examples as the oracle for the registry's canonicalisation, rewrite
      measure, recording and compile; the repository, DI wiring and HTTP
      bars are tested like the Manifest's.
- [ ] The singlebox story "edit → revision → diff → move draft → promote"
      runs end to end up to the apply hand-off; the "apply → go back two
      revisions" leg is exercised once RSI-04 lets the Manifest accept a
      compiled document (see Out of Scope).

## In Scope

- A Genome Registry core package in Backend with revisions, refs moved by
  compare-and-swap, patch recording and validation, Manifest recording with
  source pinning and first-revision capture, diff, risk tiers and rewrite
  detection, content upload/read, compile, lineage queries, and the drift
  check.
- A promotion service and endpoint that moves refs, records the promotion
  and hands the compiled document to Manifest apply through one narrow
  applier seam.
- A write path in the manifest content store for produced content (bytes a
  patch wrote, an upload, or a capture of the bot's current state; see
  *Terms*), recorded with genome provenance instead of a source URL
  (`01-genome.md` §9.1 extension 1). Fetched sources need nothing new: the
  fetch pipeline already stores their bytes under the bot.
- Tables, repositories, DI wiring, HTTP routes, authorization and admission
  rows, error mapping, docs and tests for all of the above.
- Moving the test-only canonical-JSON and rewrite reference implementations
  into the core package, with the contract test pointed at them.

## Out of Scope

- **Manifest changes (RSI-04):** the content-store and Center-skill sources,
  the applied-document digest on apply reports and publish records,
  resource removal (#2162), `script: null` (#2862), `memory` delivery
  (RSI-05), `engine_config` (#2858). Until RSI-04 lands, a compiled document
  is not accepted by the Manifest, so promotion is refused before moving
  refs; the applier seam needs no change afterwards.
- **Service-bot promotion through the publish flow.** The seam carries the
  bot kind; the publish implementation lands with RSI-04/RSI-16, and a
  service-bot promotion is refused as unsupported until then.
- **The gate, review queue, approvals, canary rollout, kill switches**
  (RSI-12, RSI-16). Promotion here is the owner/tenant-admin path and the
  internal move RSI-12 will call.
- **Experience, evaluations, the ledger** (RSI-10, RSI-11, RSI-21): the
  revision metadata carries `evidence` and `evaluations` ids but nothing
  here writes evaluations.
- **A shared `Idempotency-Key` store** (RSI-07): the header is accepted;
  revisions are idempotent by content and promotions by compare-and-swap.
- **Bot callers, `inbox/*` refs** (DR-3, RSI-14): postponed.
- **Capturing current state of platform-managed teclaw bots** and of skills
  supplied from the shared skill repository (`git://`), which genome v1
  cannot represent: capture refuses and names them.
- A git export of a bot's history (`01-genome.md` §9.2, proposed).

## Open Questions

- None blocking. Settled during planning and flagged at the review gate:
  the promotion-before-RSI-04 behaviour, service-bot promotion deferred,
  the join used for "which revision runs" until the applied digest exists
  (apply id recorded on the promotion), and repo-skill capture refused.
