# Bot Evolution Platform (RSI) — design set

> 中文版：[README.zh-CN.md](README.zh-CN.md)

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
4. **verification is platform-owned**: no strategy and no bot can write to a
   live bot directly. Everything goes candidate → verify → gate → promote,
   with lineage and rollback;
5. **the mechanism improves too** (true RSI): the experiment history H is
   used to propose a better mechanism M2, which is adopted only after it is
   verified to produce better verified improvements than M1. The verifier
   itself stays fixed and human-owned.

## Reading order

These files are one design, split by topic, and meant to be read together in
this order. They are not separate work sessions: follow-up sessions are the
work items RSI-01…RSI-24 in [10-work-items.md](10-work-items.md), each of which
names the documents to read first.

| # | Document | Answers |
| --- | --- | --- |
| 1 | [01-design.md](01-design.md) | The architecture: three levels, components, loop, ownership, module placement, phasing |
| 2 | [02-genome.md](02-genome.md) | What an evolved bot *is*: the Bot Genome, and how it extends the Manifest |
| 3 | [03-verification.md](03-verification.md) | How we know a change is good: bot verification (S′ vs S) and mechanism verification (M′ vs M), and what eval code already exists |
| 4 | [04-recursion.md](04-recursion.md) | Level 3: improving the improvement mechanism from the Experiment Ledger H, safely |
| 5 | [05-strategy-sdk.md](05-strategy-sdk.md) | How evolution is pluggable: plugin kinds, strategy manifests, execution bindings |
| 6 | [06-interfaces.md](06-interfaces.md) | API vs SDK vs CLI, and how bots drive evolution |
| 7 | [07-default-strategy.md](07-default-strategy.md) | How ClawEvolve and the other existing pipelines are onboarded as defaults |
| 8 | [08-governance.md](08-governance.md) | Gating, risk tiers, anti-reward-hacking, sandboxing, rollout, budgets |
| 9 | [09-research.md](09-research.md) | Industry survey and codebase evidence these decisions rest on |
| 10 | [10-work-items.md](10-work-items.md) | Numbered work items for follow-up sessions, with dependencies |

Draft decision records (status `proposed`). They live here while under
discussion; once accepted, each is promoted to `docs/adr/` with the next free
ADR number:

- [DR-1 — Bot Genome is the unit of evolution](decisions/0001-bot-genome-is-the-unit-of-evolution.md)
- [DR-2 — Promotion is platform-owned](decisions/0002-promotion-is-platform-owned.md)
- [DR-3 — Bot principal for the evolution surface](decisions/0003-bot-principal-for-evolution-surface.md)

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
| **Archive / Experiment Ledger (H)** | Every improvement experiment ever run (mechanism, parent, candidate, evidence, verdict, cost, online outcome). Nothing is deleted; it is the level-2 selection pool and the level-3 evidence base. |
| **Mechanism (M)** | The improvement mechanism: a strategy version plus its prompts, operators, parameters, and models. Versioned like a genome, so it can itself be improved (level 3). |
| **Bot verification** | Deciding whether a candidate bot S′ is better than its parent S, on platform-owned suites. |
| **Mechanism verification** | Deciding whether a candidate mechanism M′ produces better *verified* improvements than M on held-out improvement problems. |
| **Verifier** | Suites, graders, gate floor, and verification protocols. Human-owned; never modified by any automated loop. |

## Non-goals for this design set

- Weight training / fine-tuning. The Experience Store is kept training-ready
  (see [01-design.md §9](01-design.md#9-bridge-to-weight-training)), but the
  platform evolves the harness, not the model.
- Evolving team topology and BCS routing. Genome is per-bot in v1; a
  team-level genome is listed as future work.
- Replacing the AgentEvolve UI in this phase.
