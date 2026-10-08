# Bot Evolution Platform (RSI) — design set

> Status: **DRAFT — high-level design, for review.** No code has been written
> against this design. Decisions marked *proposed* need an owner's sign-off
> before the work items that depend on them start.

## What this is

Avernet bots can already be improved by hand (edit persona files, upload
skills, apply a Bot Config Manifest) and, for local OpenClaw bots, by the
AgentEvolve / ClawEvolve pipeline in `apps/evolverun/`. This design set turns
that into a **platform**: a strategy-agnostic way to run recursive
self-improvement (RSI) loops over any Avernet bot, where

1. the **unit of evolution** is a versioned, immutable bot artifact — the
   *Bot Genome* — built on the existing Bot Config Manifest;
2. **how** a bot evolves is a **pluggable strategy** (proposers, evaluators,
   gates, triggers) that other teams can author with an SDK and register
   without changing the platform; ClawEvolve becomes the first, default
   strategy;
3. the loop can be driven by **deterministic code** (API / SDK), by **an
   operator or CI** (CLI), or by **a bot** (the same CLI, scoped to a bot
   principal), all over one contract;
4. **promotion is platform-owned**: no strategy and no bot can write to a live
   bot directly. Everything goes candidate → evaluate → gate → promote, with
   lineage and rollback.

## Reading order

| # | Document | Answers |
| --- | --- | --- |
| 1 | [design.md](design.md) | The architecture: components, loop, ownership, module placement, phasing |
| 2 | [genome.md](genome.md) | What an evolved bot *is*: the Bot Genome, and how it extends the Manifest |
| 3 | [strategy-sdk.md](strategy-sdk.md) | How evolution is pluggable: plugin kinds, strategy manifests, execution bindings |
| 4 | [interfaces.md](interfaces.md) | API vs SDK vs CLI, and how bots drive evolution (advice on point 3) |
| 5 | [default-strategy.md](default-strategy.md) | How ClawEvolve and the other existing pipelines are onboarded as defaults |
| 6 | [governance.md](governance.md) | Gating, risk tiers, anti-reward-hacking, sandboxing, rollout, budgets |
| 7 | [research.md](research.md) | Industry survey and codebase evidence these decisions rest on |
| 8 | [work-items.md](work-items.md) | Numbered work items for follow-up sessions, with dependencies |

Proposed ADRs (drafted in this change, status `proposed`):

- [`docs/adr/0019-bot-genome-is-the-unit-of-evolution.md`](../../adr/0019-bot-genome-is-the-unit-of-evolution.md)
- [`docs/adr/0020-promotion-is-platform-owned.md`](../../adr/0020-promotion-is-platform-owned.md)
- [`docs/adr/0021-bot-principal-for-evolution-surface.md`](../../adr/0021-bot-principal-for-evolution-surface.md)

## Glossary (local to this design until it stabilises)

| Term | Meaning |
| --- | --- |
| **Bot Genome** | The evolvable, declarative definition of one bot: persona files, skills, memory seed, resources, tools, engine config. Immutable once recorded; identified by content hash. |
| **Genome Revision** | One recorded version of a Genome, with parent pointer(s), provenance, and status. |
| **Genome Patch** | A typed, itemized change from one revision to another. The only thing a Proposer may emit. |
| **Phenotype** | The running bot (engine + workspace) produced by applying a revision. Observed, never edited by evolution. |
| **Experience** | Normalized episodes (sessions / trajectories), feedback, and evaluation traces collected from phenotypes. |
| **Strategy** | A versioned composition of plugins (analyzers, proposers, evaluators, gates, selectors, triggers) that defines *how* a bot evolves. |
| **Evolution Run** | One execution of a strategy against one bot (or lineage), made of iterations, candidates, evaluations, and decisions. |
| **Candidate** | A Genome Revision proposed during a run, not yet promoted. |
| **Gate** | The platform-owned decision point that accepts/rejects a candidate, using the strategy's acceptance policy *plus* non-negotiable platform checks. |
| **Promotion** | Moving a bot's `active` ref to a revision and applying it through the existing Manifest / publish chain. |
| **Archive** | Every candidate ever produced, with lineage and scores. Nothing is deleted; it is the selection pool. |

## Non-goals for this design set

- Weight training / fine-tuning. The Experience Store is kept training-ready
  (see [design.md §9](design.md#9-bridge-to-weight-training)), but the
  platform evolves the harness, not the model.
- Evolving team topology and BCS routing. Genome is per-bot in v1; a
  team-level genome is listed as future work.
- Replacing the AgentEvolve UI in this phase.
