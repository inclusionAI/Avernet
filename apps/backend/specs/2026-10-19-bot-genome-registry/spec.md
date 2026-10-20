# Genome Registry in Backend (RSI-03)

> Work item: `apps/backend/docs/bot-evolution/work-items.md` → RSI-03.
> Builds on the RSI-02 contract in
> [`apps/backend/api-contracts/genome/v1/`](../../api-contracts/genome/v1/README.md)
> for the format of a revision and a patch. Where this spec changes how a
> revision is *recorded* (from the bot, not from a Manifest document), the
> contract and `01-genome.md` are updated by this work item.

## Summary

Give every bot an immutable, content-addressed history of its definition:
**Genome Revisions**. A revision is recorded in one of two ways: by
**capturing** the bot as it is now (its persona files, skills, resources,
MCP servers and startup script), or by applying a typed **Genome Patch** to
an existing revision, the patch's **base**, which becomes the new revision's
parent. Named **refs** (`active`, `draft`, …) point at revisions and move
only by compare-and-swap. The registry can diff any two revisions, and a
**promotion** moves `active` and delivers the promoted revision to the bot.

Delivery is a separate item. This item records promotions and hands the
compiled revision to one narrow delivery seam; RSI-04 (redefined as
**Stage 2**) builds the components that deliver a compiled revision to the
bot runtime directly, through the existing apply engine, with no Manifest
document in between. Until RSI-04 lands a promotion is refused before any
ref moves. A revision is not tied to the Manifest: the Manifest's own
`/config-manifest` endpoints keep working as today and are not used by the
registry.

### Terms

- **Capture.** Reading what the bot has now and recording it as a
  revision. Always total: every category present. The parent is the bot's
  `active` revision, or none for the bot's first revision.
- **Base / parent.** Every revision except a bot's first has one parent. A
  patch names it as `base` (required); a capture's parent is `active`. Ids
  are content hashes, so recording the same content again returns the
  existing revision and never changes its parent.
- **Compare-and-swap (CAS).** A ref move carries the revision the caller
  believes the ref points at now; it happens only if that is still true,
  otherwise it is refused with the current value.
- **Ref event.** Every ref move adds one row to an append-only log (ref
  name, from, to, who, why, when). Rows are never changed or deleted.
- **Run, candidate.** An evolution run (RSI-08) is one execution of a
  strategy for one bot; it may submit several candidate patches. Each is a
  revision named `candidate/<run id>/<n>`, `n` counting from 1 in
  submission order.
- **Policy.** The owner's rules in every revision (`mutable_genes`, `pins`,
  `risk_overrides`). It is not bot state, so it is not captured: a capture
  copies the policy of `active` (the contract default for a first
  revision), and the owner changes it with its own operation, which records
  a new revision with the same content and the new policy.
- **Drift.** The bot runs something other than what `active` says, usually
  because someone changed the bot directly after the last promotion.
  Detected, not prevented; the owner reconciles by capturing and promoting.
- **Produced content.** Bytes the registry stores itself rather than the
  Manifest fetch pipeline: captured files, files a patch writes, uploads.
  The content store today only accepts fetched bytes with a source URL, so
  it gains a write path for these.

## Motivation

RSI-02 fixed the format of a revision and a patch but shipped no runtime
code. Nothing in Backend can record a revision, check a patch against a
policy, keep a bot's content by digest for good, or say which revision a bot
runs. RSI-03 is the next step on the critical path
RSI-01 → RSI-02 → RSI-03 → RSI-04 (versioned bots that can go back to any
earlier revision), and RSI-04 and RSI-08 build on its service interface and
endpoints.

## User Stories

- As a **bot owner**, I want to capture my bot as a revision at any time
  and see its history, so that I can go back to any earlier revision.
- As a **bot owner**, I want to edit my bot as a small, itemized patch
  against a named base revision, so that two editors cannot silently
  overwrite each other.
- As a **bot owner or tenant admin**, I want the policy recorded with every
  revision to be enforced when a patch is recorded, so that nothing
  automated can touch a pinned file or a gene I have not opened up.
- As a **reviewer**, I want a per-gene diff between any two revisions, with
  text files as unified diffs and a risk tier, so that I can judge a change
  without reading whole files.
- As an **engineer building the evolution service (RSI-08)**, I want a
  registry I can call from Backend code and the same operations as HTTP
  endpoints, so that a run can record each candidate patch as
  `candidate/<run id>/<n>` and ask whether the bot has drifted.
- As an **engineer building RSI-04 (Stage 2)**, I want promotion to go
  through one narrow delivery seam that hands me the compiled revision, so
  that direct delivery plugs in without touching the registry.

## Acceptance Criteria

Recording:
- [ ] A revision can be captured from a bot: persona files, skills (local
      skills as stored file trees, Center skills as pinned versions,
      shared-repository skills by repository path), resources (files and
      directory trees), MCP servers with the bot's overrides, and the
      startup script. Every captured byte lands in the content store by
      digest; the stored revision validates against the contract schema and
      its id is the contract's hash.
- [ ] Shared-repository skills (`git://…` memberships on the platform-synced
      skills repository) are recorded as `origin: repo` with `repo_path`,
      an additive extension of the RSI-02 schema (no version bump); they are
      unpinned until the repository sync exposes a commit.
- [ ] Capture applies the contract's normalisation (the platform-written
      AGENTS.md reference block is removed and trailing whitespace trimmed)
      and refuses, naming the item, what cannot be recorded: a resource
      whose path the Manifest path rule rejects, and a platform-managed
      teclaw bot.
- [ ] A revision can be recorded from a patch against a base revision. All
      twelve ops apply as the contract defines; the contract's example
      patches applied to their example base give the example candidate
      revision, and the stored patch digest is the candidate id.
- [ ] The patch's own `base` names the revision it is applied to. An
      unknown base (not a revision of this bot) is `not_found`; a patch is
      refused with a reason code when its ops do not apply exactly to that
      base (`does_not_apply`), touch a gene outside `mutable_genes` or a
      run's `allowed_genes` (`gene_not_mutable`, `gene_not_allowed`),
      overlap a pin (`pinned_item`), lack a rationale or required evidence
      (`missing_rationale`), fail the schema (`schema`), or exceed the
      Manifest-mirrored limits (`limit_exceeded`).
- [ ] A recorded revision is always one that apply would accept: the checks
      Manifest apply runs before writing (persona UTF-8 and engine-legal
      type, local skill files form a valid package, resource admission and
      path rules, MCP URL/header rules, MCP permission and bot-override
      validity in the recording bot's context) run on the new revision's
      content; every `examples/invalid` case that names one of them is
      refused by it.
- [ ] Recording content that already exists returns the existing revision
      unchanged (same id, sequence number and parent). Going back is the
      same idea for refs: in A → B → C → D, promoting B again records
      nothing and leaves B's parent as A; only `active` and `previous`
      move.
- [ ] Each recorded patch reports its risk tier and `rewrite` flags by the
      contract's rules; the published canonical-JSON and rewrite vectors
      are the oracle.
- [ ] An owner can set a bot's policy; that records a new revision with the
      same content and the new policy, parent `active`.

Refs and lineage:
- [ ] Refs `active`, `previous`, `canary`, `draft` and `candidate/<run>/<n>`
      exist per bot; every move is compare-and-swap, a lost race answers a
      conflict naming the current revision, and every move adds a ref event.
- [ ] Only the designated mover may move each ref: owners move `draft`;
      `active`, `previous` and `canary` move only through promotion;
      candidate refs are created when a run candidate is recorded and never
      moved.
- [ ] Revisions list per bot newest first, filtered by status and parent; a
      revision is retrievable by id, by `r<seq>`, or by ref name.
- [ ] A diff between any two revisions of the same bot lists each changed
      gene as added, removed or modified, carries a unified diff for
      modified text files, and reports a risk tier and rewrite flags.

Content:
- [ ] Bytes can be uploaded for a bot and read back by digest, only when
      the digest is reachable from that bot's revisions or uploads; a read
      re-verifies the hash.

Promotion and drift:
- [ ] A revision compiles to a complete, Manifest-shaped desired-state
      document whose file entries point at the content store by digest and
      whose Center and repo skills are pinned by version or path. Compiling
      each contract example's `revision.json` reproduces its
      `compiled.yaml`. The document's digest is the digest of its canonical
      JSON.
- [ ] Promoting a revision moves `active` and `previous` in one
      compare-and-swap, marks the revision `promoted`, records a promotion
      with actor, reason, the compiled document and its digest, and hands
      the compiled revision to the delivery seam.
- [ ] Until RSI-04 provides direct delivery, the seam's preflight refuses
      every promotion as `not_appliable` before any ref moves, naming
      RSI-04; service bots are refused as unsupported the same way.
- [ ] The registry answers which revision a bot runs by joining the bot's
      last apply to the promotion that started it, and reports drift when
      the last apply was not started by a promotion or its revision differs
      from `active`. Owner promotions on a drifted bot are allowed;
      automated ones are refused.

Surface and tests:
- [ ] The HTTP endpoints exist under `/openapi/v1/bots/{bot_id}/genome/`
      with the standard envelope, authorization rows, admission modes and
      documented error statuses: list/get/diff revisions, capture, record a
      patch, set policy, refs, content, promotions.
- [ ] Unit tests cover every module; the contract test keeps the vectors
      and examples as the oracle for canonicalisation, the rewrite measure,
      patch application and compile; repository, DI wiring and HTTP bars
      are tested like the Manifest's.
- [ ] The singlebox story "capture → patch → diff → move draft → promote"
      runs end to end up to the delivery hand-off, where promotion is
      refused as `not_appliable`; the "deliver → go back two revisions" leg
      completes with RSI-04.

## In Scope

- A Genome Registry core package in Backend: capture, patch recording and
  validation, policy change, diff, risk tiers and rewrite detection, refs
  with CAS, content upload/read, compile, lineage queries, the drift check.
- A promotion service and endpoint that moves refs, records the promotion
  with the compiled document, and hands the compiled revision to one narrow
  delivery seam, whose only implementation here refuses until RSI-04.
- The additive contract extension `origin: repo` for shared-repository
  skills.
- The produced-content write path of the manifest content store.
- Tables, repositories, DI wiring, HTTP routes, authorization and admission
  rows, error mapping, tests, and the contract and design-doc updates that
  record capture as the way revisions are recorded.
- Moving the test-only canonical-JSON and rewrite reference implementations
  into the core package.

## Out of Scope

- **Delivery (RSI-04, redefined as Stage 2):** components that take a
  compiled revision into the existing apply engine directly, reading bytes
  from the content store and resolving Center and repo skills, plus the
  applied-document digest and the totality fixes (#2162, #2862). Until then
  a promotion is refused before moving refs. `memory` delivery (RSI-05) and
  `engine_config` (#2858) stay where they are.
- **Recording a revision from a Manifest document.** Revisions come from
  the bot or from patches; an owner who manages a bot through Manifest
  documents applies the document as today and captures the result.
- **Service-bot promotion through the publish flow.** Refused as
  unsupported behind the same seam until RSI-04 / RSI-16.
- **The gate, review queue, approvals, canary rollout, kill switches**
  (RSI-12, RSI-16); **Experience, evaluations, the ledger** (RSI-10,
  RSI-11, RSI-21); **a shared `Idempotency-Key` store** (RSI-07); **bot
  callers and `inbox/*` refs** (DR-3, RSI-14).
- Capturing platform-managed teclaw bots (refused and named, see above),
  and pinning a shared-repository skill to a commit (the repository sync
  exposes none yet).

## Open Questions

- None. Decisions made during review and recorded above: capture replaces
  recording from a Manifest document; a promotion hands the compiled
  revision to a delivery seam and writes no Manifest; a patch's own `base`
  is its only base; shared-repository skills are recorded by path; policy
  changes are an operation of their own; RSI-04 is redefined as direct
  delivery (Stage 2) and precedes any further Manifest work.
