# The apply engine takes one typed desired state, and drift is observed, not tracked

> 中文版：[0022-apply-engine-desired-state-and-observed-drift.zh-CN.md](0022-apply-engine-desired-state-and-observed-drift.zh-CN.md)

Status: accepted (2026-10-23; the refactor before RSI-04, decided with the issue owner on PR #3083). Owner: @totalfrank, signed off.

## Decision

**One input.** The Backend's apply engine (`apps/backend/src/agentclaw/community/core/apply_engine/`)
is its own component, and its only input is a typed **desired state**
(`DesiredState`): one `Declaration` per construct — `script`, `identity`,
`resources`, `skills`, `mcp`, `engine_config`, `cli_tools` — each either
undeclared (`None`: the apply leaves the bot's state for it alone) or
declared with a tuple of typed entries, an empty tuple declaring "none",
which the engine converges to by removing what is there. Every entry names
bytes already in the content store by digest (`ContentRef`), or a Skill
Center version, or a shared-repository path. The engine never sees a
Manifest document and never fetches.

**Two compilers, no translation.** What produces a desired state is a
*compiler*, looked up by the apply run from the state reference an apply
carries: `StateRef("manifest")` → the Manifest compiler
(`core/bot_config_manifest/compiler/`, which validates, fetches, unpacks
and files), `StateRef("genome", promotion_id)` → the genome compiler
(`core/bot_genome/compile.py`, which reads a stored revision while the
promotion is current). A revision is not translated into a Manifest
document, and a Manifest document is not translated into a revision. The
contract's `compiled.yaml` oracles are replaced by an equivalence test that
compiles every example case both ways and compares the states
(`api-contracts/genome/v1/manifest-v1-compatibility.md`).

**Drift is observed.** Whether a bot holds its `active` revision is found
by reading the bot — `GenomeRegistry.drift` captures its state as
`capture` would, computes the revision id it would record under `active`'s
policy and compares — never by recording what was delivered. Apply reports
carry no document digest; the promotion row carries no compiled document,
no digest and no apply id; `applied_revision` and `by_apply_reference` are
gone. The 409 a drifted bot answers to an automated promotion names
`active` and the observed revision id.

**The resources root.** The engine owns `data/` whole: a resources
declaration clears the root and writes the declaration, so a path the
previous document declared and this one does not, and a file the bot wrote
there itself, are removed (#2162). Both compilers refuse a resource path
outside the root. `script: null` declares no script and removes one the bot
has; an absent `script` leaves it alone (#2862).

Where [ADR 0019](0019-bot-genome-is-the-unit-of-evolution.md) and
[ADR 0020](0020-promotion-is-platform-owned.md) describe the compiled
document's digest and its mapping back to a revision, this decision
supersedes that mechanism; their decisions on what a revision is and who
owns promotion stand.

Design: [`apps/backend/specs/2026-10-23-apply-engine-desired-state/`](../../apps/backend/specs/2026-10-23-apply-engine-desired-state/)
and [`apps/backend/docs/bot-evolution/01-genome.md` §2.7, §5.5](../../apps/backend/docs/bot-evolution/01-genome.md).

## Consequences

- RSI-04 shrinks to binding: a `RevisionApplier` that starts an apply run
  with `StateRef("genome", promotion_id)`, materialisers that resolve a Center
  version and a repository path, the service-bot publish path, singlebox.
- A new source of desired state (a template, a future format) is a new
  compiler and a new state reference, never a new materialiser or a change
  to the engine.
- Drift costs a capture (one read of the bot) whenever a caller asks;
  nothing is written. A bot whose state cannot be read cannot be said to be
  in step either (`CaptureRefused` propagates).
- An apply that declares `resources` replaces everything under `data/`,
  including files the bot wrote there; the root is the documented
  ownership boundary, and nothing outside it is touched.
- The genome's promotion table changed shape before the feature went live
  (columns removed in the committed DDL rather than by a new migration).

## Alternatives

- **Compile a revision to a Manifest document and apply that.** Rejected:
  the document needed source spellings that are not Manifest grammar, a
  digest to be carried through apply reports and promotion rows, and a join
  from apply ids to promotions — three records of one fact the bot itself
  can answer.
- **Track drift by recording the delivered digest.** Rejected: a record
  says what was sent, not what the bot holds; a hand edit on the device, a
  partial apply, or a bot restored from elsewhere all leave the record
  wrong and the bot unobserved.
- **A per-file ownership ledger for resources.** Rejected: the root is a
  simpler boundary the schema already states, and the ledger would have to
  be kept in step with every write path, the console upload included.
