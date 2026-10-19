# Genome Registry in Backend (RSI-03)

> Work item: `apps/backend/docs/bot-evolution/work-items.md` → RSI-03.
> Builds on the RSI-02 contract in
> [`apps/backend/api-contracts/genome/v1/`](../../api-contracts/genome/v1/README.md),
> which wins over this spec and over `01-genome.md` where they differ.

## Summary

Give every bot an immutable, content-addressed history of its definition:
**Genome Revisions**, recorded either from a whole Bot Config Manifest document
or from a typed **Genome Patch**, pointed at by named **refs** that move only
by compare-and-swap, with a diff between any two revisions and a promotion
operation that moves `active` and hands the promoted revision to the bot's
delivery path. Owners get versioned bots and a way back to any earlier
revision; later evolution work (runs, verification, the gate) gets the store
it records candidates into.

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
- As an **engineer building the evolution service (RSI-08)**, I want an
  in-process registry interface and matching HTTP endpoints that record a
  candidate from a patch, name it `candidate/<run>/<n>`, and answer whether a
  bot has drifted, so that runs can start and record candidates without
  reaching into Backend tables.
- As an **engineer building RSI-04**, I want revisions compiled into a
  Manifest document that points at the content store by digest, so that the
  Manifest change I add has a producer to test against.

## Acceptance Criteria

Recording:
- [ ] A revision can be recorded from a Bot Config Manifest v1 document. The
      document is validated with the Manifest's own validator; every source
      is fetched once and stored in the manifest content store; categories
      the document omits are filled from the parent revision, or from the
      bot's current state for the bot's first revision; the original YAML is
      kept as provenance; the stored revision validates against the contract
      schema and its id is the contract's hash.
- [ ] Every Manifest v1 round-trip case in the contract
      (`examples/manifest-v1/*`) records to exactly its `revision.json`
      when its fixture sources are served, and the two refused cases are
      refused for their stated reasons.
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
- [ ] Anything apply would refuse is refused when recording: the contract's
      §6.4 predicates (persona UTF-8 and engine-legal type, local skill tree a
      valid package, resource admission and path rules, MCP URL/header
      rules, MCP permission and bot-override validity with the recording
      bot's context) run on the resulting revision; every `examples/invalid`
      case that names one of them is refused by it.
- [ ] Recording content that already exists returns the existing revision
      with its original sequence number; no duplicate row and no new `seq`.
- [ ] Each recorded revision reports its risk tier and `rewrite` flags by the
      contract's rules (risk-tiers data, the exact LCS measure, replacement
      detection, policy overrides); the published canonical-JSON and rewrite
      vectors are the oracle.

Refs and lineage:
- [ ] Refs `active`, `previous`, `canary`, `draft` and `candidate/<run>/<n>`
      exist per bot; every move is compare-and-swap on the expected
      revision, a lost race answers a conflict naming the current revision,
      and every move is an append-only event with actor and reason.
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
      version, in the spelling the contract proposes for RSI-04; the
      contract's `compiled.yaml` cases are reproduced; the document's digest
      is recorded with the revision.
- [ ] Promoting a revision moves `active` (and `previous`) in one
      compare-and-swap, marks the revision `promoted`, records a promotion
      with actor, reason, the compiled document digest and the apply
      reference, and starts the apply of the compiled document for a
      personal bot. The promotion is refused before any ref moves when the
      bot's Manifest cannot accept the compiled document (today: until
      RSI-04 adds the content-store and Center sources).
- [ ] The registry answers which revision a bot runs, from its last apply
      joined to the promotions that started applies, and reports drift when
      that differs from `active`; owner promotions on a drifted bot are
      allowed, automated ones are refused.

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

- A Genome Registry core package in Backend with revisions, refs with CAS,
  patch recording and validation, Manifest recording with source pinning and
  first-revision capture, diff, risk tiers and rewrite detection, content
  upload/read, compile, lineage queries, and the drift check.
- A promotion service and endpoint that moves refs, records the promotion
  and hands the compiled document to Manifest apply through one narrow
  applier seam.
- The produced-content write path of the manifest content store
  (`01-genome.md` §9.1 extension 1): produced bytes with genome provenance.
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
