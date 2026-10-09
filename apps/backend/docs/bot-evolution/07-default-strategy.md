# Default strategies — onboarding the existing pipelines

> 中文版：[07-default-strategy.zh-CN.md](07-default-strategy.zh-CN.md)

> Status: DRAFT. How ClawEvolve and the other self-improvement code in
> `apps/evolverun/` become the platform's default, replaceable strategies.
> Evidence and file references: [09-research.md §2.2](09-research.md#22-existing-self-improvement-pipelines).

## 1. What exists today

| Pipeline | Where | What it does | Fit |
| --- | --- | --- | --- |
| **ClawEvolve bot/skill evolution** | `apps/evolverun/clawweb` (TS control plane) + `clawweb-skills/clawevolve-skills` (Python stage skills) | Session diagnosis → plan + ClawBench cases → tune/review rounds → bench → accept (`test > baseline`) → pack | **Primary default strategy** |
| **ClawEvolve skill hardening** | same, `skill_hardening` flow | Single-stage hardening of one skill | Second default strategy (skill-scoped) |
| **Workflow-run healing** | ClawWeb `routes/evolve*.ts`, `run-analysis/*` | Failed-run evidence → diagnoses, lessons, suggestions → `suggestion_apply` edits workflow YAML | A separate strategy; targets workflows, not the genome — phase 2 |
| **ClawInsight improvements** | `modules/clawinsight` | Monitoring → improvement items → `plan-source/v2` → plan+optimize | An event trigger for ClawEvolve bindings, plus `plan-source/v2` input through `experience.feedback` |
| **TaskGuard runtime repair** | `apps/evolverun/taskguard` | In-run guardian/repair/retry | Runtime resilience, *not* evolution; its run evidence feeds the Experience Store |
| **Evolvetrace** | `apps/evolverun/evolvetrace` | Run observability; evolution tab is a mock | Possible Archive/Lineage UI later |

The good news: ClawEvolve already has most of the right seams — a stage
contract catalog with JSON Schemas, `preprocess | postprocess | replace`
extensions, versioned custom stage skills, a claim/report step protocol, a
`plan-source/v2` producer-agnostic handoff, train/validation separation with
a review firewall, and offline gate calibration. The onboarding is mostly
**re-pointing those seams at platform contracts**, not a rewrite.

## 2. ClawEvolve as a black-box strategy

ClawEvolve plugs in through the single strategy port
([05-strategy-sdk.md](05-strategy-sdk.md)) as a **black box**: it keeps its
own internals (diagnose logic, tune and review agents, prompts, mutation
operator library, round loop) and only its edges move to the
`StrategyContext`. The code sketch is in
[05-strategy-sdk.md §10](05-strategy-sdk.md#10-examples).

Registration record: `needs` = `experience.sessions@1`,
`agents@1` with the definitions `clawevolve-tune` and `clawevolve-review`
(engine `openclaw`, shipped and uploaded with the strategy, see
[05-strategy-sdk.md §4.2](05-strategy-sdk.md#42-where-agent-definitions-come-from)),
`evaluate.train@1`.

| ClawEvolve piece | In the new model | Change needed |
| --- | --- | --- |
| `acquisition/discovery.py`, `sessions.py`, `service_export.py` | Provider of `experience.sessions` for OpenClaw (platform side) | Move behind the engine session-export contract; normalize to `Episode`; tag with genome revision |
| `clawevolve-diagnose` | Inside the strategy | Read episodes via `ctx.experience.sessions()` instead of disk |
| `clawevolve-plan` (bench cases) | Inside the strategy | Add cases via `ctx.evaluate.add_train_cases()`; the platform assigns splits (removes its own 80/20 split authority) |
| `clawevolve-tune` + `clawevolve-review` | Inside the strategy | **Edit a sandbox from `ctx.workspace.materialise()`, not the live workspace**; run agents via `ctx.agents.run()`; submit `ws.to_patch()` |
| Train bench runs (`bench-full-opt`) | `ctx.evaluate.train()` | ClawBench grading moves into the Verification Service as `platform/clawbench` |
| `action_accept` (`test > baseline`) + advisory gates | Internal filter on what to submit | Acceptance becomes the platform verdict under the binding's verification profile; ClawEvolve's gate thresholds can seed a stricter profile |
| baseline-pack / restore / pack / deploy | — (removed) | Not needed: the strategy never changes the live bot |
| `ce_tasks` / `ce_steps` / claim-report | — (replaced) | Platform Run Orchestrator + Job Protocol |
| `EvolutionFlow` registry (3 closed keys) | Registered strategies | `bot_evolution`, `skill_evolution`, `skill_hardening` become three registered strategies (or one strategy with params) |
| Stage extensions + uploaded stage skills | Strategy versions, or later the composed tier | Swapping one stage becomes a new strategy version, or a composed-tier step once step types exist |
| `skill-decision` human approval + `BotSkillGateway.replaceLocalSkill` (CAS) | Platform review queue + promotion | Human approval generalizes to all T2 patches |

## 3. Decoupling from OpenClaw

Coupling points found and how each is removed:

| Coupling | Replace with |
| --- | --- |
| Hard-coded `/home/admin/.openclaw/workspace` and `~/.openclaw/agents/*/sessions` | `ctx.workspace` (materialised genome) for edits; `ctx.experience.sessions()` for sessions |
| `openclaw agent --local --agent …` to run tune/review/judge/bench | `ctx.agents.run()` (the `agents` capability), OpenClaw provider first; bench execution moves to eval bots in the Verification Service |
| OpenClaw md conventions (SOUL/AGENTS/TOOLS, `skills/skills-local`, `config/mcporter.json`) | Genome genes (`persona`, `skills`, `tools.mcp`); engine projection owns paths |
| `active_engine='openclaw'`, `bot_type='personal'` filters in `singlebox/bot-runtime.ts` | Binding check: the bot's engine must have providers for everything in `needs` |
| Direct SQLite reads of Backend tables (`ac_bots`, …) | Genome Registry / Backend APIs |
| `OPENCLAW_*` env vars in `local-execution.ts` | Config loading only (R: raw env access in config/bootstrap) |

Engines beyond OpenClaw then need only capability providers (session
export, agent runner) and engine projection support, not a ClawEvolve fork.

## 4. Migration plan (strangler, no big bang)

1. **Shadow-record (no behaviour change).** ClawEvolve keeps running as
   today, but each accepted round also records a Genome revision (from its
   pack) via the Genome API. Proves the genome model against real outputs.
2. **Black-box adapter.** Register `clawevolve/bot-evolution@1` as one
   job-worker strategy whose `run(ctx)` calls the existing skill scripts with
   paths pointing at a materialised sandbox, and submits the resulting
   patch. Orchestrated, verified, and promoted by the platform. AgentEvolve UI
   shows platform runs alongside legacy tasks.
3. **Native strategy.** The skills read and write through the strategy SDK
   directly; pack/restore removed from the flow; legacy task types
   deprecated.
4. **Second and third defaults.** `skill_hardening`, ClawInsight trigger,
   workflow-run healing (once workflows are representable as a genome gene
   or as their own artifact — open decision D-4 below).

Each step is independently shippable and reversible.

## 5. A second, non-ClawEvolve default: memory consolidation

To prove pluggability (R19 needs two examples) the platform should ship a
deliberately different strategy early: **`platform/consolidate-memory`**,
modelled on Anthropic Dreams / OpenClaw Dreaming / Hermes Curator.

- **Registration:** `needs` = `experience.feedback@1` only (observations
  from the inbox, ratings, corrections).
- **Binding:** weekly schedule, or an event after N new observations;
  `allowed_genes: ["memory"]`.
- **`run(ctx)`:** cluster recent observations into candidate lessons and
  submit one patch of itemized `memory.add/update/retire` ops (skills unused
  for N days are archived, never deleted).
- **Verification:** a profile with the regression suite and a contradiction
  check; T1 memory items auto-promote, anything affecting persona goes to
  review.

It exercises different genes, a different trigger, and the fast-loop inbox,
which is exactly what the abstraction must handle.

## 6. Open decisions

- **D-2:** Keep the TS ClawWeb control plane as a long-term *host* of the
  default strategy runners, or port runners to the Python strategy SDK?
  Recommendation: keep skills Python (already stdlib-only Python), run them as
  job workers; retire TS orchestration code after step 3.
- **D-3:** Is ClawBench the platform's default grader, or one grader among
  others? Recommendation: platform default (it already supports automated,
  rubric-judge, and hybrid grading), with the grader interface open.
- **D-4:** Workflow YAML (TaskGuard) as a genome gene, or a separate
  artifact? Recommendation: separate artifact with the same revision/ref
  model; decide when workflow-run healing is onboarded.
- **D-5:** The external "ClawMind" `analyze` handler is not in this repo. Its
  contract must be brought in or redefined before workflow-run healing is
  onboarded. The dormant `SingleRunAnalyzer` / `BatchRunAnalyzer` /
  `LessonExpireScheduler` code should be either wired into strategies or removed.
