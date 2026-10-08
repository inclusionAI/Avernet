# Default strategies — onboarding the existing pipelines

> 中文版：[default-strategy.zh-CN.md](default-strategy.zh-CN.md)

> Status: DRAFT. How ClawEvolve and the other self-improvement code in
> `apps/evolverun/` become the platform's default, replaceable strategies.
> Evidence and file references: [research.md §2.2](research.md#22-existing-self-improvement-pipelines).

## 1. What exists today

| Pipeline | Where | What it does | Fit |
| --- | --- | --- | --- |
| **ClawEvolve bot/skill evolution** | `apps/evolverun/clawweb` (TS control plane) + `clawweb-skills/clawevolve-skills` (Python stage skills) | Session diagnosis → plan + ClawBench cases → tune/review rounds → bench → accept (`test > baseline`) → pack | **Primary default strategy** |
| **ClawEvolve skill hardening** | same, `skill_hardening` flow | Single-stage hardening of one skill | Second default strategy (skill-scoped) |
| **Workflow-run healing** | ClawWeb `routes/evolve*.ts`, `run-analysis/*` | Failed-run evidence → diagnoses, lessons, suggestions → `suggestion_apply` edits workflow YAML | Analyzer + Proposer pair; targets workflows, not genome — phase 2 |
| **ClawInsight improvements** | `modules/clawinsight` | Monitoring → improvement items → `plan-source/v2` → plan+optimize | A **Trigger** + **Analyzer** feeding the primary strategy |
| **TaskGuard runtime repair** | `apps/evolverun/taskguard` | In-run guardian/repair/retry | Runtime resilience, *not* evolution; its run evidence is an **ExperienceSource** |
| **Evolvetrace** | `apps/evolverun/evolvetrace` | Run observability; evolution tab is a mock | Possible Archive/Lineage UI later |

The good news: ClawEvolve already has most of the right seams — a stage
contract catalog with JSON Schemas, `preprocess | postprocess | replace`
extensions, versioned custom stage skills, a claim/report step protocol, a
`plan-source/v2` producer-agnostic handoff, train/validation separation with
a review firewall, and offline gate calibration. The onboarding is mostly
**re-pointing those seams at platform contracts**, not a rewrite.

## 2. Mapping ClawEvolve onto plugin kinds

| ClawEvolve piece | Plugin kind | Change needed |
| --- | --- | --- |
| `acquisition/discovery.py`, `sessions.py`, `service_export.py` | **ExperienceSource** (OpenClaw) | Move behind the engine session-export contract; normalize to `Episode`; tag with genome revision |
| `clawevolve-diagnose` (judge, cluster, rewrite to replayable query) | **Analyzer** | Read episodes from C2 instead of disk; emit `plan-source/v2` (already its output) |
| `clawevolve-plan` (bench templates, objective, spec) | **SuiteBuilder** (+ produces strategy-private spec) | Output cases to C5; platform assigns splits (removes its own 80/20 split authority) |
| `clawevolve-tune` | **Proposer** | **Edit a sandbox workspace, not the live one**; return `GenomePatch` via `GenomeWorkspace.to_patch()` instead of `diff.patch` + `changed_files.txt` |
| `clawevolve-review` (firewall, hypotheses) | Part of Proposer (two-agent proposer) | Keep firewall; it maps directly onto Job Protocol input filtering |
| `clawbench-base` grading | **Evaluator** (platform-provided default) | Wrap as `platform/clawbench`; executor runs in eval bot via `eval_env` instead of `openclaw agent --local` |
| `action_accept` + env-tunable advisory gates | **AcceptancePolicy** `clawevolve/acceptance` | Lift thresholds from env vars into policy params; platform floor added on top |
| baseline-pack / restore / pack / deploy | — (removed) | Replaced by Genome revisions, refs, and platform promotion |
| `ce_tasks` / `ce_steps` / claim-report | — (replaced) | Platform Run Orchestrator + Job Protocol |
| `EvolutionFlow` registry (3 closed keys) | Strategy manifests | `bot_evolution`, `skill_evolution`, `skill_hardening` become three registered strategies |
| Stage extensions + uploaded stage skills | Strategy versions reusing plugins | `replace` = swap one step's plugin; pre/post = extra steps |
| `skill-decision` human approval + `BotSkillGateway.replaceLocalSkill` (CAS) | Platform review queue + promotion | Human approval generalizes to all T2 patches |

The resulting default strategy is the manifest example in
[strategy-sdk.md §4](strategy-sdk.md#4-strategy-manifest).

## 3. Decoupling from OpenClaw

Coupling points found and how each is removed:

| Coupling | Replace with |
| --- | --- |
| Hard-coded `/home/admin/.openclaw/workspace` and `~/.openclaw/agents/*/sessions` | `GenomeWorkspace` (materialised genome) for edits; ExperienceSource for sessions |
| `openclaw agent --local --agent …` to run tune/review/judge/bench | **AgentRunner** abstraction inside the plugin SDK, with an OpenClaw implementation first; bench execution moves to eval bots |
| OpenClaw md conventions (SOUL/AGENTS/TOOLS, `skills/skills-local`, `config/mcporter.json`) | Genome genes (`persona`, `skills`, `tools.mcp`); engine projection owns paths |
| `active_engine='openclaw'`, `bot_type='personal'` filters in `singlebox/bot-runtime.ts` | Strategy `applies_to.engines` checked against engine capabilities |
| Direct SQLite reads of Backend tables (`ac_bots`, …) | Genome Registry / Backend APIs |
| `OPENCLAW_*` env vars in `local-execution.ts` | Config loading only (R: raw env access in config/bootstrap) |

Engines beyond OpenClaw then need only an ExperienceSource and engine
projection support, not a ClawEvolve fork.

## 4. Migration plan (strangler, no big bang)

1. **Shadow-record (no behaviour change).** ClawEvolve keeps running as
   today, but each accepted round also records a Genome revision (from its
   pack) via the Genome API. Proves the genome model against real outputs.
2. **Adapter strategy.** Register `clawevolve/bot-evolution@1` whose steps
   are thin job workers that call the existing skill scripts with paths
   pointing at a `GenomeWorkspace`. Orchestrated by the platform; promotion by
   the platform. AgentEvolve UI shows platform runs alongside legacy tasks.
3. **Native strategy.** Skills read/write through the plugin SDK directly;
   pack/restore removed from the flow; legacy task types deprecated.
4. **Second and third defaults.** `skill_hardening`, ClawInsight trigger,
   workflow-run healing (once workflows are representable as a genome gene
   or as their own artifact — open decision D-4 below).

Each step is independently shippable and reversible.

## 5. A second, non-ClawEvolve default: memory consolidation

To prove pluggability (R19 needs two examples) the platform should ship a
deliberately different strategy early: **`platform/consolidate-memory`**,
modelled on Anthropic Dreams / OpenClaw Dreaming / Hermes Curator.

- Trigger: schedule + idle; or N new observations in the inbox.
- Analyzer: cluster recent observations and episodes into candidate lessons.
- Proposer: itemized `memory.add/update/retire` ops and `skill.update` for
  skills unused for N days → archive (never delete).
- Evaluator: regression suite only (memory changes should not regress) +
  a contradiction check.
- AcceptancePolicy: no-regression; T1 items auto-promote, persona-affecting
  items go to review.

It exercises different genes, a different trigger, and the fast-loop inbox,
which is exactly what the abstraction must handle.

## 6. Open decisions

- **D-2:** Keep the TS ClawWeb control plane as a long-term *host* of the
  default strategy runners, or port runners to the Python plugin SDK?
  Recommendation: keep skills Python (already stdlib-only Python), run them as
  job workers; retire TS orchestration code after step 3.
- **D-3:** Is ClawBench the platform's default Evaluator, or one evaluator
  among others? Recommendation: platform default (it already supports
  automated, rubric-judge, and hybrid grading), with the Evaluator kind open.
- **D-4:** Workflow YAML (TaskGuard) as a genome gene, or a separate
  artifact? Recommendation: separate artifact with the same revision/ref
  model; decide when workflow-run healing is onboarded.
- **D-5:** The external "ClawMind" `analyze` handler is not in this repo. Its
  contract must be brought in or redefined before workflow-run healing is
  onboarded. The dormant `SingleRunAnalyzer` / `BatchRunAnalyzer` /
  `LessonExpireScheduler` code should be either wired as plugins or removed.
