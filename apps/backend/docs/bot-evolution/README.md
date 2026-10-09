# Bot Evolution Platform (RSI) — design set

> 中文版：[README.zh-CN.md](README.zh-CN.md)

> Status: **DRAFT — design, for review.** No code has been written against
> this design. Decisions marked *proposed* need an owner's sign-off before the
> work items that depend on them start.

## What this is

Avernet bots can already be improved by hand (edit persona files, upload
skills, apply a Bot Config Manifest) and, for local OpenClaw bots, by the
AgentEvolve / ClawEvolve pipeline in `apps/evolverun/`. This design set turns
that into a **platform**: a strategy-agnostic way to run self-improvement
loops over any Avernet bot, where

1. the **unit of evolution** is a versioned, immutable bot artifact, the
   *Bot Genome*, built on the existing Bot Config Manifest;
2. **how** a bot evolves is a **pluggable strategy**: one `run(ctx)` port
   that other teams implement with an SDK and register without changing the
   platform. Each bot chooses its strategies through bindings; ClawEvolve
   becomes the first default strategy;
3. the loop is driven through **one contract** with three surfaces: REST
   API, SDKs, and a CLI for humans and CI (bots as callers are postponed);
4. **verification is platform-owned**: no strategy can write to a live bot.
   Everything goes candidate → verify → gate → promote, with lineage, and
   going back is promoting an earlier revision;
5. **the mechanism can improve too** (level 3): the experiment history is
   used to propose a better mechanism, adopted only after it is verified to
   produce better verified improvements. The verifier itself stays fixed and
   human-owned.

**First iteration:** levels 1–2 (improving bots, with verification). Level 3
(item 5) is designed so nothing blocks it, but it comes later.

## Reading order

Start with [design.md](design.md): it explains the problem, goals, and
architecture, and summarizes the governance rules. The numbered docs then
cover one part each: first the **components** (things that exist and are
owned), then the **services** (things that run). Each has the same shape:
domain model, service interface, API with examples, and code examples.

These files are one design, not separate work sessions. Follow-up sessions
are the work items RSI-01…RSI-24 in [work-items.md](work-items.md), each of
which names the documents to read first.

| Doc | Kind | Answers |
| --- | --- | --- |
| [design.md](design.md) | Overview | Problem, goals, three levels, architecture, guarantees (governance summary), placement, phasing, risks |
| [01-genome.md](01-genome.md) | Component | What an evolvable bot *is*: revisions, refs, patches, memory, storage, Genome Registry API |
| [02-experience.md](02-experience.md) | Component | What strategies learn from: episodes, feedback, session export, data handling |
| [03-strategy.md](03-strategy.md) | Component | How evolution is pluggable: the strategy port, capability catalog, context, agent definitions, SDK, conformance |
| [04-default-strategies.md](04-default-strategies.md) | Component | ClawEvolve and the other first strategies, and the existing code they reuse |
| [05-experiment-ledger.md](05-experiment-ledger.md) | Component | The record of every experiment: lineage, archive, audit |
| [06-evolution-run.md](06-evolution-run.md) | Service | Bindings, runs, leases, long-running operations, Job Protocol, sandboxing, budgets |
| [07-verification.md](07-verification.md) | Service | How we know a change is good: suites, graders, paired runs, profiles, verdicts |
| [08-promotion.md](08-promotion.md) | Service | Who decides: separation of powers, gate, risk tiers, review, promotion, rollout, going back |
| [09-evolution-api.md](09-evolution-api.md) | Service | The public access layer: API conventions, endpoint index, SDKs, `avn` CLI |
| [10-meta-evolution.md](10-meta-evolution.md) | Service (later) | Level 3: improving the mechanism, mechanism verification, bounds |
| [research.md](research.md) | Appendix | Industry survey and codebase evidence |
| [work-items.md](work-items.md) | Appendix | Work items for follow-up sessions, with dependencies |

Draft decision records (status `proposed`). They live here while under
discussion; once accepted, each is promoted to `docs/adr/` with the next free
ADR number:

- [DR-1 — Bot Genome is the unit of evolution](decisions/0001-bot-genome-is-the-unit-of-evolution.md)
- [DR-2 — Promotion is platform-owned](decisions/0002-promotion-is-platform-owned.md)
- [DR-3 — Bot principal for the evolution surface](decisions/0003-bot-principal-for-evolution-surface.md) (**postponed**: how bots talk to the platform is decided first)

## Glossary

| Term | Meaning |
| --- | --- |
| **Bot Genome** | The evolvable, declarative definition of one bot: persona files, skills, memory, resources, tools, engine config. Immutable once recorded; identified by content hash. |
| **Genome Revision** | One recorded version of a Genome, with parent pointer(s), provenance, and status. |
| **Genome Patch** | A typed, itemized change from one revision to another. The only thing a strategy may submit. |
| **Phenotype** | The running bot (engine + workspace) produced by applying a revision. Observed, never edited by evolution. |
| **Experience** | Normalized episodes (sessions or trajectories) and feedback collected from running bots, each tagged with the revision that produced it. |
| **Strategy** | Versioned code with one method, `run(ctx)`, that proposes candidates for a bot; registered with the catalog capabilities it `needs`. |
| **Capability** | A platform-owned, versioned field of the strategy context (for example `experience.sessions@1`), with providers per engine. |
| **Binding** | One entry in a bot's evolution policy: which strategy, trigger, parent, allowed genes, verification profile, budget, params. |
| **Evolution Run** | One execution of a binding against one bot, with its inputs frozen at start and identified by a run id; it submits candidates and looks up their verdicts by id. |
| **Operation** | Long-running work started inside a run (an agent session, a train evaluation): its start returns an id, and its status is looked up by that id. |
| **Candidate** | A Genome Revision proposed during a run, not yet promoted. |
| **Verdict** | The result of verifying a candidate: `pending`, `accept`, `reject`, or `inconclusive`. |
| **Gate** | The platform-owned decision point that accepts or rejects a candidate, using the verdict under the binding's verification profile *plus* non-negotiable platform checks. |
| **Promotion** | Moving a bot's `active` ref to a revision and applying it through the existing Manifest / publish chain. |
| **Experiment Ledger (H)** | Every improvement experiment ever run (mechanism, parent, candidate, evidence, verdict, cost, online outcome). Nothing is deleted; it is the level-2 selection pool, the audit trail, and the level-3 evidence base. |
| **Mechanism (M)** | The improvement mechanism: a strategy version plus its prompts, agent definitions, parameters, and models. Versioned, so it can itself be improved (level 3). |
| **Verifier** | Suites, graders, the gate floor, and verification protocols. Human-owned; never modified by any automated loop. |

## Non-goals

- Weight training or fine-tuning. Experience is kept training-ready (see
  [design.md](design.md)), but the platform evolves the harness, not the
  model.
- Evolving team topology and BCS routing. The genome is per-bot in v1; a
  team-level genome is future work.
- Bots calling the platform (postponed with DR-3).
- Replacing the AgentEvolve UI in this phase.
