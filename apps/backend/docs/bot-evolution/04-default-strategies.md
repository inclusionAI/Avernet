# Default strategies

> 中文版：[04-default-strategies.zh-CN.md](04-default-strategies.zh-CN.md)

> Status: DRAFT. Component in the [bot evolution architecture](design.md).
> The strategies the platform ships by default — ClawEvolve
> (`clawevolve/bot-evolution`), memory consolidation
> (`platform/consolidate-memory`), and the reference strategy
> `platform/manual-patch` — and how today's pipelines in `apps/evolverun/`
> become them.

## 1. Purpose and scope

A **strategy** is code that implements the single strategy port `run(ctx)`
and proposes changes to a bot by submitting candidates; the platform then
verifies, gates, and promotes them. The port, the capability catalog, the
registration record, and the strategy SDK are defined in
[03-strategy.md](03-strategy.md). This document is about three concrete
**implementations** of that port that the platform ships:

| Strategy | Version in examples | Role | Phase / work item |
| --- | --- | --- | --- |
| `platform/manual-patch` | `1.0.0` | Trivial reference strategy: submits a patch supplied in its params. Exercises the whole loop end to end with deterministic checks | P2, RSI-08 |
| `clawevolve/bot-evolution` | `2.0.0` | **Primary default.** Today's ClawEvolve bot evolution (diagnose → plan → tune/review rounds → bench) as a black-box strategy | P3, RSI-13 |
| `platform/consolidate-memory` | `1.0.0` | Second, deliberately different default: consolidates feedback and episodes into curated memory items ("dream" job). Proves pluggability with a different capability set (R19 needs two examples) | P5, RSI-15 |

This document owns:

- the registration record, params schema, and `run(ctx)` behaviour of each
  default strategy;
- the mapping from ClawEvolve's current pieces to the platform model, the
  removal of its OpenClaw coupling, and the strangler migration plan;
- the inventory of ClawEvolve and ClawBench assets that the platform reuses
  (the rows moved here from the verification inventory), and of the other
  self-improvement pipelines in `apps/evolverun/`;
- the strategy-owned progress record of each strategy (the platform has no
  checkpoint API, so each strategy persists its own progress).

It does **not** own:

| Topic | Owner |
| --- | --- |
| The port, `StrategyContext`, capability catalog, `Candidate`/`Verdict` semantics, conformance kit, Strategy Registry | [03-strategy.md](03-strategy.md) |
| Bindings, runs, leases and re-dispatch, budgets, the Job Protocol definition, sandboxing | [06-evolution-run.md](06-evolution-run.md) |
| Episodes, feedback, the session-export provider for OpenClaw (moved out of ClawEvolve) | [02-experience.md](02-experience.md) |
| Suites, splits, graders (incl. the extracted `platform/clawbench` grader), verdict policy, verification profiles | [07-verification.md](07-verification.md) |
| Gate, risk tiers, review queue, promotion | [08-promotion.md](08-promotion.md) |
| Genome revisions, Genome Patch ops, memory gene | [01-genome.md](01-genome.md) |
| Experiment Ledger H (where each run and submission is recorded) | [05-experiment-ledger.md](05-experiment-ledger.md) |
| ClawEvolve's mechanism components (prompts, operator library, gate thresholds) as the target of level-3 evolution | [10-meta-evolution.md](10-meta-evolution.md) |

**Where they run.**

| Strategy | Code lives in | Runtime (`runtime.kind`) |
| --- | --- | --- |
| `clawevolve/bot-evolution` | `apps/evolverun/clawweb-skills/clawevolve-skills/` (Python stage skills, stdlib only) plus a thin strategy entry point; strategy authors = the ClawEvolve team ("Strategy implementations: strategy authors, incl. `apps/evolverun` for defaults") | `job_worker` (container image). Long-term host is open decision DS-1 (was D-2) |
| `platform/consolidate-memory` | Shipped by the platform with `apps/evolution` *(proposed placement)* | `in_process` *(proposed)* |
| `platform/manual-patch` | Shipped by the platform with `apps/evolution` | `in_process` *(proposed)*; it is the strategy RSI-08 uses in the singlebox local profile |

All three are ordinary strategies: they are registered, bound, run, and
verified exactly like a strategy from another team. Being "default" only
means the platform ships them and recommends bindings for them; they get no
extra access.

## 2. Domain model

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `StrategyRegistration` (one per default) | The registration record of each default strategy version (§4.2, §7.2, §11) | Strategy author; stored by the Strategy Registry ([03-strategy.md](03-strategy.md)) | New record per strategy version |
| `ManualPatchParams` | Params of `platform/manual-patch`: the Genome Patch to submit | Caller who starts the run | Frozen at run start |
| `ClawEvolveParams` | Params of `clawevolve/bot-evolution`: window, rounds, polling interval | Bot owner (binding) | Frozen at run start |
| `Finding` | One diagnosed problem from real sessions (good/bad case, root cause, linked episodes) | ClawEvolve | Created by diagnose on a run's first attempt; kept in ClawEvolve's state |
| `TrainCaseProposal` | A ClawBench case that ClawEvolve's plan step proposes to the platform via `ctx.evaluate.add_train_cases` | ClawEvolve proposes; the Verification Service assigns the split and owns the case afterwards | Once added, versioned in the suite registry |
| `ClawEvolveState` | ClawEvolve's own per-run progress: findings, current base, round number, pending candidate, history | ClawEvolve, in **its own store** keyed by run id (today's `ce_tasks` / `ce_steps` can serve) | Created on first attempt; updated after every step; reloaded on re-dispatch |
| `ConsolidateMemoryParams` | Params of `platform/consolidate-memory` | Bot owner (binding) | Frozen at run start |
| `LessonCluster` | A group of related feedback items summarized into one candidate lesson | consolidate-memory | Per run |
| `ConsolidationPlan` | The memory ops a consolidate-memory run decided to submit, persisted before submission so a re-dispatch resubmits the same patch | consolidate-memory, in its own store keyed by run id | Created once per run |
| `RunSummary` | What `run(ctx)` returns; each strategy fills it with its own counters | Strategy; shape in [03-strategy.md](03-strategy.md) | Once per run |

Platform types used here but defined elsewhere: `StrategyContext`,
`Candidate`, `Verdict`, `Operation`, `Capability`, `AgentDefinition`
([03-strategy.md](03-strategy.md)); `Episode`, `Feedback`
([02-experience.md](02-experience.md)); `GenomePatch`, `GenomeRevision`
([01-genome.md](01-genome.md)); `Binding`, `Run`, `Budget`
([06-evolution-run.md](06-evolution-run.md)).

### 2.1 `ManualPatchParams`

```python
@dataclass(frozen=True)
class ManualPatchParams:
    patch: GenomePatch            # base must equal the run's parent revision
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch": {
    "patch_schema": 1,
    "base": "sha256:a90b…",                       // r41, the binding's parent ("active")
    "ops": [
      {"op": "file.edit", "target": "skills/refund-policy/SKILL.md",
       "edits": [{"kind": "replace_section", "heading": "## When to use",
                  "content": "Use for full and partial refunds of paid orders."}]}
    ],
    "rationale": "Partial refunds are allowed by policy; the skill did not trigger on them",
    "evidence": ["episode:ep_91"]
  }
}
```

### 2.2 `ClawEvolveParams`

```python
@dataclass(frozen=True)
class ClawEvolveParams:
    window_days: int = 7          # how far back diagnose reads sessions
    max_rounds: int = 3           # tune/review/bench rounds per run
    poll_s: int = 60              # interval between verdict lookups
    max_sessions: int = 500       # proposed: cap on episodes read (sessions() default limit)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"window_days": 7, "max_rounds": 3, "poll_s": 60, "max_sessions": 500}
```

`window_days` and `max_rounds` come from the binding example in the
sources, `poll_s` from the ClawEvolve code sketch; `max_sessions` is
proposed here.

### 2.3 `Finding`

A finding is what ClawEvolve's diagnose step (`clawevolve-diagnose`, an LLM
session judge) mines from real sessions: a good or bad case, its likely root
cause, and the episodes that show it. Findings are inputs to the plan step
(bench cases) and to the tune prompt.

```python
@dataclass(frozen=True)
class Finding:
    finding_id: str
    kind: Literal["bad_case", "good_case"]
    summary: str
    root_cause: str
    episodes: list[str]           # episode ids, evidence for the candidate
    severity: Literal["low", "medium", "high"]
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "finding_id": "f_12",
  "kind": "bad_case",
  "summary": "Bot refuses partial refunds although the policy allows them",
  "root_cause": "skill refund-policy does not trigger on partial refunds",
  "episodes": ["ep_91", "ep_97"],
  "severity": "high"
}
```

### 2.4 `TrainCaseProposal`

ClawBench cases are Markdown files with YAML front matter (`lib_tasks.py`).
The case format stays byte-compatible ([07-verification.md](07-verification.md));
over the JSON API a case travels as a string inside a JSON envelope.

```python
@dataclass(frozen=True)
class TrainCaseProposal:
    case_key: str                 # strategy-chosen, stable per run: "<run>/<finding>/<n>"
    format: str                   # proposed: "clawbench-md/1"
    content: str                  # the Markdown case, unchanged
    derived_from: list[str]       # finding / episode ids
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "case_key": "run_7f3/f_12/1",
  "format": "clawbench-md/1",
  "content": "---\nid: partial_refund_01\ngrading_type: hybrid\n---\n## Prompt\nCan I get a refund for half of my order A17?\n## Expected\n…",
  "derived_from": ["finding:f_12", "episode:ep_91"]
}
```

### 2.5 `ClawEvolveState`

The strategy's own progress record. The platform never reads it and has no
API for it; its shape is ClawEvolve's business. Shown here because it is
what makes ClawEvolve survive a crash and re-dispatch (§4.4).

```python
@dataclass
class RoundRecord:
    round: int
    candidate: str | None         # candidate id submitted in this round, if any
    train_score_pct: int
    verdict: str | None           # pending | accept | reject | inconclusive

@dataclass
class ClawEvolveState:
    run_id: str
    findings: list[Finding]
    base: str                     # revision the next round edits (parent, then last accepted)
    next_round: int
    best_train_pct: int           # best train score so far; ClawEvolve's own submission filter
    pending: str | None           # candidate id whose verdict is still being looked up
    history: list[RoundRecord]
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "run_id": "run_7f3",
  "findings": [{"finding_id": "f_12", "kind": "bad_case", "summary": "Bot refuses partial refunds",
                "root_cause": "skill refund-policy trigger", "episodes": ["ep_91"], "severity": "high"}],
  "base": "sha256:a90b…",
  "next_round": 1,
  "best_train_pct": 78,
  "pending": "sha256:c41e…",
  "history": [{"round": 0, "candidate": "sha256:c41e…", "train_score_pct": 78, "verdict": "pending"}]
}
```

### 2.6 `ConsolidateMemoryParams`, `LessonCluster`, `ConsolidationPlan`

```python
@dataclass(frozen=True)
class ConsolidateMemoryParams:
    window_days: int = 7          # feedback considered
    min_feedback: int = 5         # below this, the run submits nothing
    max_new_items: int = 10       # cap on memory.add ops per run
    retire_unused_days: int = 30  # memory items with no supporting use for this long are retired

@dataclass(frozen=True)
class LessonCluster:
    lesson_key: str               # becomes the memory item key
    text: str
    tags: list[str]
    sources: list[str]            # feedback / episode ids
    action: Literal["add", "update", "retire"]

@dataclass(frozen=True)
class ConsolidationPlan:
    run_id: str
    base: str
    ops: list[dict]               # Genome Patch ops (memory.add / memory.update / memory.retire)
    rationale: str
    evidence: list[str]
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// A ConsolidationPlan as persisted before submission
{
  "run_id": "run_8c2",
  "base": "sha256:a90b…",
  "ops": [
    {"op": "memory.add",
     "item": {"key": "partial-refunds-allowed", "text": "Partial refunds are allowed for paid orders.",
              "tags": ["billing"], "source": ["feedback:fb_301", "episode:ep_91"]}},
    {"op": "memory.retire", "key": "old-shipping-sla"}
  ],
  "rationale": "3 corrections in 7 days state partial refunds are allowed; old-shipping-sla unused for 45 days",
  "evidence": ["feedback:fb_301", "feedback:fb_305", "feedback:fb_322"]
}
```

## 3. What exists today

All of the repo's self-improvement code is under `apps/evolverun/`; there is
nothing equivalent elsewhere in the repo.

| Pipeline | Where | What it does | Fit |
| --- | --- | --- | --- |
| **ClawEvolve bot/skill evolution** | `apps/evolverun/clawweb` (TS control plane) + `clawweb-skills/clawevolve-skills` (Python stage skills) | Session diagnosis → plan + ClawBench cases → tune/review rounds → bench → accept (`test > baseline`) → pack | **Primary default strategy** (§4) |
| **ClawEvolve skill hardening** | same, `skill_hardening` flow | Single-stage hardening of one skill | Second ClawEvolve-based default (skill-scoped), §9 |
| **Workflow-run healing** | ClawWeb `routes/evolve*.ts`, `run-analysis/*` | Failed-run evidence → diagnoses, lessons, suggestions → `suggestion_apply` edits workflow YAML | A separate strategy; targets workflows, not the genome — phase 2 of onboarding (§9) |
| **ClawInsight improvements** | `modules/clawinsight` | Monitoring → improvement items → `plan-source/v2` → plan+optimize | An event trigger for ClawEvolve bindings, plus `plan-source/v2` input through `experience.feedback` (§9) |
| **TaskGuard runtime repair** | `apps/evolverun/taskguard` | In-run guardian/repair/retry | Runtime resilience, *not* evolution; its run evidence feeds the Experience Store |
| **Evolvetrace** | `apps/evolverun/evolvetrace` | Run observability; evolution tab is a mock | Possible UI for the archive view of the ledger and lineage later |

ClawEvolve already has most of the right seams: a stage contract catalog with
JSON Schemas, `preprocess | postprocess | replace` extensions, versioned
custom stage skills, a claim/report step protocol, a `plan-source/v2`
producer-agnostic handoff, train/validation separation with a review
firewall, and offline gate calibration. Onboarding is mostly **re-pointing
those seams at platform contracts**, not a rewrite.

### 3.1 Codebase evidence

File references (from the research inventory, [research.md](research.md)):

- **ClawEvolve control plane** (TS): `clawweb/public/modules/clawevolve/server/`
  — `services/evolve/evolution-flow.ts` (three closed flow keys:
  `bot_evolution`, `skill_evolution`, `skill_hardening`),
  `stage-catalog.ts` + `resources/evolve/official-stage-catalog.json`
  (stage JSON Schemas; `preprocess|postprocess|replace`),
  `task-registry.ts`, `routes/evolve.ts`, `routes/internal/evolve.ts`
  (claim/report step protocol), `services/evolve/skill-application.ts` +
  `contracts/bot-skill-gateway.ts` (human-approved skill replace with CAS),
  `create-module.ts:60-200` (host DI options).
- **Stage skills** (Python, stdlib): `clawweb-skills/clawevolve-skills/` —
  diagnose (`acquisition/discovery.py:41-96`, `sessions.py`,
  `service_export.py` which declares `session-export/v1`,
  `integration/plan_source.py:25-97` with an 80/20 train/validation split),
  plan, tune, review, bench (`clawbench-base/scripts/lib_grading.py:51-108`),
  pack, deploy. The optimize loop is in
  `clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py`
  (sequence `:9491-9513`; accept rule `:6256-6378` = test score strictly
  greater than baseline; env-tunable gates `:1890-1905`; hard-coded workspace
  `:221`, `FIXED_WORKSPACE = /home/admin/.openclaw/workspace`; tune via
  `openclaw agent --local` `:7938`).
- **Tune edits the live workspace directly**; rejected rounds restore a
  pre-round pack. This is the single most important behaviour to remove.
- **plan-source/v2** (`clawinsight/server/services/evolve/plan-source-contract.ts:6-40`)
  is the producer-agnostic findings handoff.
- **Workflow-run healing**: run evidence ingest, analysis runs, suggestions,
  lessons; the analyzer handler ("ClawMind") is not in the repo; batch
  analyzers and lesson expiry are only instantiated in tests.
- **ClawInsight**: improvement items → plan-source → plan+optimize; admin
  review; rule-trust evolution after 3 verified successes.
- **TaskGuard**: in-run guardian/repair/retry; no persistent learning.
- **Evolvetrace**: observability; the evolution tab is mocked
  (`src/components/workflow-workspace/evolution-mock.ts`).

## 4. ClawEvolve as the primary default strategy

### 4.1 Black box

ClawEvolve plugs in through the single strategy port as a **black box**: it
keeps its own internals (diagnose logic, tune and review agents, prompts,
mutation operator library, round loop), and only its edges move to the
`StrategyContext`. It is the reference case for the black-box tier, which
is the only tier in the first iteration ([03-strategy.md](03-strategy.md)).

What changes for ClawEvolve, in one list:

1. It reads sessions through `ctx.experience.sessions()` instead of the
   OpenClaw session directory.
2. It edits a **sandbox** from `ctx.workspace.materialise()`, never the live
   workspace, and submits `ws.to_patch()` as a candidate.
3. It runs its tune and review agents through `ctx.agents.start()` as
   long-running operations, and its train bench through
   `ctx.evaluate.start_train()`.
4. Its `test > baseline` acceptance becomes an **internal filter** on what it
   submits; acceptance itself is the platform verdict.
5. It no longer packs, restores, or deploys: it never changes the live bot.
6. It persists its own round state keyed by run id, so a re-dispatched run
   continues (§4.4).

### 4.2 Registration record

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "runtime": {"kind": "job_worker", "image": "registry.example/clawevolve@sha256:…"},
  "needs": {
    "experience.sessions@1": {},
    "agents@1": {"definitions": {                  // shipped with the strategy, uploaded at registration
      "clawevolve-tune":   {"engine": "openclaw", "path": "agents/clawevolve-tune"},
      "clawevolve-review": {"engine": "openclaw", "path": "agents/clawevolve-review"}
    }},
    "evaluate.train@1": {}
  },
  "params_schema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "window_days":  {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
      "max_rounds":   {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
      "poll_s":       {"type": "integer", "minimum": 10, "default": 60},
      "max_sessions": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500}
    }
  }
}
```

- `candidates@1` and `models@1` are always granted and are not declared.
- `params_schema` (a proposed, optional field of the registration record,
  [03-strategy.md](03-strategy.md)) lets the binding check reject bad params
  when the policy is written (§11, Params schemas).
- The agent definitions are today's `clawevolve-tune` and
  `clawevolve-review` skills (`SKILL.md`, `agents/`, `references/`). Today
  they are installed into the OpenClaw runtime ClawEvolve drives, so naming
  them is enough. On the platform they are **shipped with the strategy**,
  uploaded and validated by the `agents@1` provider for `openclaw` at
  registration, stored content-addressed, and loaded by digest into the
  sandbox for each call ([03-strategy.md](03-strategy.md)). Changing the
  tune prompt therefore means registering a new strategy version.
- Engine compatibility is derived: the only `engine` among the definitions
  is `openclaw`, so the binding check accepts this strategy only for
  OpenClaw bots until another engine's definitions are added (§5).

### 4.3 Mapping ClawEvolve's pieces

| ClawEvolve piece | In the new model | Change needed |
| --- | --- | --- |
| `acquisition/discovery.py`, `sessions.py`, `service_export.py` | Provider of `experience.sessions` for OpenClaw (platform side, [02-experience.md](02-experience.md)) | Move behind the engine session-export contract; normalize to `Episode`; tag with genome revision |
| `clawevolve-diagnose` | Inside the strategy | Read episodes via `ctx.experience.sessions()` instead of disk |
| `clawevolve-plan` (bench cases) | Inside the strategy | Add cases via `ctx.evaluate.add_train_cases()`; the platform assigns splits (removes its own 80/20 split authority) |
| `clawevolve-tune` + `clawevolve-review` | Inside the strategy | **Edit a sandbox from `ctx.workspace.materialise()`, not the live workspace**; run agents via `ctx.agents.start()` as long-running operations looked up by id; submit `ws.to_patch()` |
| Train bench runs (`bench-full-opt`) | `ctx.evaluate.start_train()` (an operation looked up by id) | ClawBench grading moves into the Verification Service as `platform/clawbench` |
| `action_accept` (`test > baseline`) + advisory gates | Internal filter on what to submit | Acceptance becomes the platform verdict under the binding's verification profile; ClawEvolve's gate thresholds can seed a stricter profile |
| baseline-pack / restore / pack / deploy | — (removed) | Not needed: the strategy never changes the live bot |
| `ce_tasks` / `ce_steps` / claim-report | — (replaced) as orchestration; reused as storage | Platform run orchestration + Job Protocol replace dispatch. The tables can serve as ClawEvolve's own progress store keyed by run id (§4.4) |
| `EvolutionFlow` registry (3 closed keys) | Registered strategies | `bot_evolution`, `skill_evolution`, `skill_hardening` become three registered strategies (or one strategy with params; open decision, §14) |
| Stage extensions + uploaded stage skills | Strategy versions, or later the composed tier | Swapping one stage becomes a new strategy version, or a composed-tier step once step types exist |
| `skill-decision` human approval + `BotSkillGateway.replaceLocalSkill` (CAS) | Platform review queue + promotion ([08-promotion.md](08-promotion.md)) | Human approval generalizes to all T2 patches |
| Validation ids redacted from tune/review prompts (review firewall) | Structural: the strategy never receives validation cases, only verdict aggregates | Nothing to redact; the context does not contain them |

### 4.4 The round loop

One run of `clawevolve/bot-evolution`:

1. **First attempt only — diagnose and plan.** Read episodes of the parent
   revision for `window_days`; mine findings; propose train cases with
   `ctx.evaluate.add_train_cases` (the platform assigns splits; holdout,
   regression, and safety stay hidden). Save state.
2. **Each round** (up to `max_rounds`), with key `<run_id>/round-<n>`:
   1. materialise a sandbox from the current `base` with that key;
   2. start the tune agent (`<key>/tune`), wait for it by id;
   3. start the review agent (`<key>/review`), wait for it by id;
   4. start a train evaluation of the sandbox (`<key>/train`), wait for it;
   5. **submission filter** (ClawEvolve's own heuristic, the successor of
      `action_accept`): submit `ws.to_patch()` only if the train score beats
      the best so far; save the pending candidate id;
   6. look the verdict up by candidate id every `poll_s` until it is not
      `pending`; if `accept`, the next round builds on the accepted
      revision; save state.
3. Return a `RunSummary` with rounds run and candidates submitted.

**Why the filter uses the train score.** Today `action_accept` compares
*test* (validation) scores. On the platform, ClawEvolve no longer sees
validation per case — only verdict aggregates — so its own filter can only
use the train split; the validation comparison happens in the platform
verdict, which is the actual acceptance. ClawEvolve's `full_opt_gate` and
`candidate_opt_gate` are re-expressed as verdict-policy rules and made
blocking in the verifier ([07-verification.md](07-verification.md)); they no
longer run inside the strategy.

**Waiting for a verdict.** The run stays `running` while it looks verdicts
up, and that time counts against the binding's `max_wall_clock_s`. There is
no callback and no waiting state.

**Crash and re-dispatch.** The platform re-dispatches a run whose lease
expired with the same run id and `ctx.attempt + 1`. ClawEvolve reloads
`ClawEvolveState` from its store (today's `ce_tasks` / `ce_steps` can serve),
and because every workspace and operation key is derived from the run id
and the round, repeating `materialise`, `agents.start`, and
`evaluate.start_train` returns the same sandbox and the same operations —
including agent edits already made — instead of paying again. A candidate
resubmitted after a crash gets the same candidate id, because the id is the
content hash of the patch.

**Idempotency keys used by ClawEvolve.** Following the shared rule
`<run_id>/<own step>`:

| Call | Key |
| --- | --- |
| `workspace.materialise` | `run_7f3/round-2` |
| `agents.start("clawevolve-tune")` | `run_7f3/round-2/tune` |
| `agents.start("clawevolve-review")` | `run_7f3/round-2/review` |
| `evaluate.start_train` | `run_7f3/round-2/train` |

Never a send-time timestamp: a key must be identical across retries.

### 4.5 ClawEvolve evaluation assets the platform reuses

These rows of the bot-quality evaluation inventory are ClawEvolve and
ClawBench assets. They become either parts of this strategy or building
blocks that the verifier extracts from it; the verifier side of the reuse is
specified in [07-verification.md](07-verification.md).

| Component | Where | What it does | Status | Reuse as |
| --- | --- | --- | --- | --- |
| **ClawBench runner** | `apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-base/scripts/` | Markdown + YAML cases (`lib_tasks.py`). Graders: `automated` (case-supplied `grade(transcript, workspace)`), `llm_judge` (rubric), `hybrid` (weighted) in `lib_grading.py`. Scripted multi-turn user (`interactions`). `--runs N` mean/std | **Live**, OpenClaw-only (runs `openclaw agent --local` on a copied workspace) | **Case format + graders** of the platform verifier (`platform/clawbench`) |
| **ClawWeb Bench store** | `apps/evolverun/clawweb/public/shared/server/schema.ts` (`cm_bench_domains/templates/template_versions/runs/task_results/artifacts`), `routes/bench.ts` | Versioned suites (domain = suite), published templates with `source_hash`, run and per-case results with breakdown and transcripts | **Live** (also in OSS edition) | Data model seed for the **Suite registry** and **Verification results** |
| **ClawEvolve splits** | `clawevolve-plan/clawevolve_plan/bench/split.py`, `clawevolve_bench_plan_run.py:185-211`, `clawevolve_optimize_run.py:1140-1330` | Train/test domains, session-grouped leakage-safe split, validation ids redacted from tune/review prompts, cases and graders frozen | **Live** | **Split assignment + visibility rules**, now platform-owned |
| **ClawEvolve gates** | `clawevolve_optimize_run.py`: `action_accept` `:6256`, `candidate_static_gate` `:2666`, `candidate_opt_gate` `:5675-5958`, `full_opt_gate` `:5862-5947`, `replicate-validation` `:6380-6520`, evaluation identity `:3604-3745` | Accept iff test score > baseline. Regression budget, protected signals, and paired seeded replication exist but are **advisory or unreachable** | Accept live; rest dormant | **Comparator + verdict policy** building blocks (pure functions) in the verifier; `action_accept` stays as the strategy's own looser filter |
| **Gate calibration / replay** | `clawevolve-skills/scripts/calibrate_evolution_gates.py`, `replay_candidate_gate.py` | Golden corpus of labelled historical decisions + adversarial scenarios. Reports precision/recall of gates. Offline replay of gates on stored rounds | Live tooling | Seed of **verifier calibration** and of **mechanism verification** for changes to verification profiles and submission filters ([10-meta-evolution.md](10-meta-evolution.md)) |
| **Diagnose → plan** | `clawevolve-diagnose/clawevolve_diagnose/judge/*`, `clawevolve-plan/bench/case_contract.py`, `template_builder.py` | LLM session judge mines good/bad cases from real sessions and turns them into bench cases | Live | Inside this strategy (findings, train cases), and **regression-suite growth** from production failures on the verifier side |

Migration of these assets (the ClawEvolve/ClawBench steps of the verifier
migration):

1. Extract `lib_grading` + case parsing into the `platform/clawbench` grader
   plugin, keeping the Markdown case format byte-compatible.
2. Move suite storage to the Suite registry and keep ClawWeb Bench readable
   (or make it a view).
3. Re-express ClawEvolve's `full_opt_gate` and `candidate_opt_gate` as
   verdict-policy rules and make them **blocking** under the default
   profile. Keep `action_accept` as the strategy's own (looser) policy
   layered on top.
4. Extend gate calibration (`calibrate_evolution_gates.py`) to judge
   calibration and run it on a schedule.

Whether ClawBench is *the* platform default grader or one among several is
open decision DS-2 (§14).

## 5. Decoupling ClawEvolve from OpenClaw

Coupling points found in the code, and what replaces each:

| Coupling | Replace with |
| --- | --- |
| Hard-coded `/home/admin/.openclaw/workspace` and `~/.openclaw/agents/*/sessions` | `ctx.workspace` (materialised genome) for edits; `ctx.experience.sessions()` for sessions |
| `openclaw agent --local --agent …` to run tune/review/judge/bench | `ctx.agents.start()` (the `agents` capability; an operation looked up by id), OpenClaw provider first; bench execution moves to eval bots in the Verification Service |
| OpenClaw md conventions (SOUL/AGENTS/TOOLS, `skills/skills-local`, `config/mcporter.json`) | Genome genes (`persona`, `skills`, `tools.mcp`); engine projection owns paths |
| `active_engine='openclaw'`, `bot_type='personal'` filters in `singlebox/bot-runtime.ts` | Binding check: the bot's engine must have providers for everything in `needs`, and be among the `engine` values of the `agents@1` definitions |
| Direct SQLite reads of Backend tables (`ac_bots`, …) | Genome Registry / Backend APIs |
| `OPENCLAW_*` env vars in `local-execution.ts` | Configuration loading only (raw environment access belongs in config/bootstrap) |

Engines beyond OpenClaw then need only capability providers (session export,
agent runner) and engine projection support — plus ClawEvolve agent
definitions for that engine in a new strategy version — not a ClawEvolve
fork.

## 6. Migration plan (strangler, no big bang)

1. **Shadow-record (no behaviour change).** ClawEvolve keeps running as
   today, but each accepted round also records a Genome revision (from its
   pack) via the Genome API. Proves the genome model against real outputs.
2. **Black-box adapter.** Register ClawEvolve as one job-worker strategy
   whose `run(ctx)` calls the existing skill scripts with paths pointing at a
   materialised sandbox, and submits the resulting patch. Orchestrated,
   verified, and promoted by the platform. The AgentEvolve UI shows platform
   runs alongside legacy tasks. (The source plan calls this
   `clawevolve/bot-evolution@1`; this document reads it as the 1.x line and
   the native strategy below as `2.0.0`.)
3. **Native strategy.** The skills read and write through the strategy SDK
   directly; pack/restore are removed from the flow; legacy task types are
   deprecated. This is `clawevolve/bot-evolution@2.0.0` as registered in §4.2.
4. **Second and third defaults.** `skill_hardening`, the ClawInsight trigger,
   and workflow-run healing (once workflows are representable as a genome
   gene or as their own artifact — open decision DS-3).

Each step is independently shippable and reversible. Work item RSI-13
covers steps 1–3 and is done when `clawevolve/bot-evolution` produces the
same or better results as legacy AgentEvolve on its own bench, through the
platform, without touching the live workspace.

## 7. `platform/consolidate-memory`

### 7.1 Why a second default

The abstraction is proven only by a second, deliberately different strategy
(R19: abstract after two examples). Memory consolidation differs from
ClawEvolve in every dimension the port must handle: different genes
(`memory` instead of persona and skills), a different trigger (weekly or
after N new feedback items instead of nightly), a different input
(feedback first, with the episodes it cites, instead of a diagnosis over
all sessions), no agents and no train evaluation, and one submission per
run instead of a multi-round loop.

It is modelled on background consolidators in industry practice (see
[research.md](research.md)):

| Reference | What we adopt |
| --- | --- |
| Anthropic Dreams | Writes a **new** version from sessions, input untouched, adopt or discard → the run proposes a patch; promotion decides |
| OpenClaw Dreaming | Opt-in, nightly, scored promotion into memory → bindings are opt-in; only gated items reach memory |
| Hermes Curator | Archive, never delete; pinned items write-protected; dry-run → items are retired (kept in history), pins are honoured by the platform floor |
| ACE Curator | Itemized delta updates avoid brevity bias and context collapse → one op per memory item, never a rewritten blob |
| Letta sleep-time agents | The acting agent does not edit memory; a background process does → the subject bot never writes its own memory |

### 7.2 Registration record

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "platform/consolidate-memory",
  "version": "1.0.0",
  "runtime": {"kind": "in_process", "entrypoint": "avernet_evolution.strategies.consolidate_memory:ConsolidateMemoryStrategy"},
  "needs": {
    "experience.feedback@1": {},                 // ratings, corrections, outcomes, findings
    "experience.sessions@1": {}                  // episodes cited by feedback (RSI-15); models@1 is always granted
  },
  "params_schema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "window_days":        {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
      "min_feedback":       {"type": "integer", "minimum": 1, "default": 5},
      "max_new_items":      {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
      "retire_unused_days": {"type": "integer", "minimum": 7, "default": 30}
    }
  }
}
```

Declaring `experience.sessions@1` means owners are shown that the strategy
reads conversation history ([02-experience.md](02-experience.md)).

The `entrypoint` field for in-process strategies is proposed; the normative
`runtime` shape is in [03-strategy.md](03-strategy.md).

### 7.3 Binding

- **Trigger:** weekly schedule, or an event after N new feedback items.
- **`allowed_genes: ["memory"]`.**
- **Verification profile:** one with the regression suite and a
  **contradiction check** (new items must not contradict persona, pinned
  items, or each other). The source proposes this profile; its name in the
  examples below, `memory@1`, is illustrative, and its definition belongs to
  [07-verification.md](07-verification.md).
- **Budget:** small (the source example uses `max_usd: 5`,
  `max_wall_clock_s: 1800`).

### 7.4 `run(ctx)`

1. Read feedback for `window_days` with `ctx.experience.feedback()`. Below
   `min_feedback`, return without submitting. Read the episodes of the same
   window with `ctx.experience.sessions()` and keep those the feedback cites,
   as supporting evidence for each lesson.
2. Load the parent's current memory items (`ctx.parent` →
   `spec.memory.items_digest` → content by digest).
3. Cluster feedback into candidate lessons with `ctx.models.complete()`
   (plain model calls: no tools, no steps), and decide per lesson:
   `memory.add`, `memory.update` (an existing key), or `memory.retire`
   (an item contradicted by feedback, or unused for `retire_unused_days`).
   Items are retired, never deleted; pinned items are left alone.
4. **Persist the `ConsolidationPlan`** in its own store keyed by run id,
   then submit **one** patch of itemized memory ops. A re-dispatched run
   finds the saved plan and resubmits the same patch, so it gets the same
   candidate id instead of a second, different candidate from new model
   output.
5. Return. Verification and promotion continue without the run.

**Promotion outcome.** Memory item add/update/retire is risk tier T1, so under
owner policy it auto-promotes when the gate passes; anything affecting
persona goes to review (T2). The strategy never uses memory `replace` mode
(T3).

**Interim until the engine memory projection contract lands (RSI-05).**
Until engines can project curated memory items, memory evolution is limited
to a platform-managed persona file (for example `LESSONS.md`). In that
interim the strategy emits `file.edit` ops on that file instead of
`memory.*` ops. Those are persona edits, so they are T2 and go to review,
and the binding must allow them (see open decision DS-5, §14). The strategy
uses feedback and episodes (RSI-15); observations recorded by subject bots
are postponed with bot callers (DR-3).

## 8. `platform/manual-patch` (reference strategy)

The smallest possible strategy. It exists so the platform loop —
orchestration, candidate recording, verification, gate, promotion, going
back — can be built and tested before any real strategy is onboarded, and
so the conformance kit has a trivial passing example.

- **Registration:** no declared capabilities (it uses only the always-granted
  `candidates@1` and `parent`).
- **Params:** the Genome Patch to submit (`ManualPatchParams`).
- **`run(ctx)`:** check that `patch.base` equals `ctx.parent.id` (fail the
  run with a non-retryable reason otherwise), submit the patch as a
  candidate, return. It does not wait for the verdict: submissions are
  verified whether or not the run is still running.
- **Verification:** deterministic checks only (automated graders, no
  judges), so the singlebox story is reproducible offline. The profile name
  `deterministic@1` in the examples is illustrative.
- **Idempotency:** no state needed. The same params give the same patch, so
  a re-dispatched run resubmits and gets the same candidate id.

The RSI-08 singlebox story uses it: start a run with this strategy →
candidate recorded → gate → promoted → bot updated → go back by promoting
the earlier revision; repeating the start with the same idempotency key
returns the same run id.

## 9. Other pipelines in `apps/evolverun/`

| Pipeline | Plan |
| --- | --- |
| `skill_evolution` flow | Same stages as bot evolution, scoped to one target skill. Either its own registered strategy or a param of `clawevolve/bot-evolution` (DS-6, §14). Migration step 4 |
| `skill_hardening` flow | Single hardening stage on one skill. Registered strategy, skill-scoped (`allowed_genes: ["skills"]`). Migration step 4 |
| ClawInsight improvements | Not a strategy: an **event trigger** for ClawEvolve bindings (for example `{"event": "failure_rate_alert"}`), and its `plan-source/v2` findings become input through `experience.feedback`. Consuming that input requires ClawEvolve to declare `experience.feedback@1`, i.e. a new strategy version |
| Workflow-run healing | A separate strategy targeting workflow YAML, not the genome. Waits for DS-3 (where workflows live) and DS-4 (the missing analyzer contract) |
| TaskGuard | Runtime resilience inside a run, not evolution. Its run evidence feeds the Experience Store as feedback ([02-experience.md](02-experience.md)) |
| Evolvetrace | Observability. Possible UI for the archive view of the ledger and lineage later |

## 10. Service interface

### 10.1 Strategy classes

All three implement the port from [03-strategy.md](03-strategy.md):

```python
class EvolutionStrategy(Protocol):
    async def run(self, ctx: StrategyContext) -> RunSummary: ...
```

Each strategy's own storage is injected by its host (the job-worker entry
point, or the `apps/evolution` composition root for in-process strategies).
It is the strategy's storage, not a platform API.

```python
class RunStateStore(Protocol[S]):
    """Strategy-owned progress, keyed by run id. The platform never reads it."""
    async def load(self, run_id: str) -> S | None: ...
    async def save(self, run_id: str, state: S) -> None: ...   # raises on write failure; never silently drops


class ManualPatchStrategy(EvolutionStrategy):
    """platform/manual-patch@1.0.0 — submits params.patch as one candidate.

    Needs no declared capability. Fails the run (retryable=False) if the
    patch base is not the run's parent revision.
    """
    async def run(self, ctx: StrategyContext) -> RunSummary: ...


class ClawEvolveStrategy(EvolutionStrategy):
    """clawevolve/bot-evolution@2.0.0 — diagnose, plan, then tune/review/train rounds.

    Needs experience.sessions@1, agents@1 {clawevolve-tune, clawevolve-review},
    evaluate.train@1. Keeps ClawEvolveState in `store`; all workspace and
    operation keys are derived from the run id and round, so a re-dispatch
    re-attaches instead of repeating paid work.
    """
    def __init__(self, store: RunStateStore[ClawEvolveState]) -> None: ...
    async def run(self, ctx: StrategyContext) -> RunSummary: ...

    # Internals kept from today's skills (not part of any platform contract):
    #   diagnose(episodes) -> list[Finding]                 clawevolve-diagnose
    #   plan_bench(findings) -> list[TrainCaseProposal]     clawevolve-plan
    #   build_tune_prompt(findings, history) -> str         clawevolve-tune (_build_tune_prompt)
    #   build_review_prompt(findings, train) -> str         clawevolve-review


class ConsolidateMemoryStrategy(EvolutionStrategy):
    """platform/consolidate-memory@1.0.0 — one itemized memory patch per run.

    Needs experience.feedback@1 and experience.sessions@1 (models@1 is always granted). Persists the
    ConsolidationPlan before submitting so a re-dispatch resubmits the same
    patch.
    """
    def __init__(self, store: RunStateStore[ConsolidationPlan]) -> None: ...
    async def run(self, ctx: StrategyContext) -> RunSummary: ...
```

### 10.2 What each strategy returns

`RunSummary` is defined in [03-strategy.md](03-strategy.md); the fields each
default strategy fills:

| Strategy | Summary contents |
| --- | --- |
| `platform/manual-patch` | `candidates: [<id>]` |
| `clawevolve/bot-evolution` | `rounds`, `candidates` (ids per round), `findings` count |
| `platform/consolidate-memory` | `candidates` (zero or one id), `ops` counts by kind, or a `skipped` reason (for example "not enough feedback") |

### 10.3 Capability calls per strategy

| Call | manual-patch | ClawEvolve | consolidate-memory |
| --- | --- | --- | --- |
| `ctx.parent` | read id | read id | read id and memory items |
| `ctx.experience.sessions` | — | yes | yes (episodes cited by feedback) |
| `ctx.experience.feedback` | — | — (later, for ClawInsight input) | yes |
| `ctx.evaluate.add_train_cases` | — | first attempt | — |
| `ctx.workspace.materialise` / `ws.to_patch` | — | each round | — |
| `ctx.agents.start` (operation) | — | tune, review | — |
| `ctx.evaluate.start_train` (operation) | — | each round | — |
| `ctx.operations.get` / `wait` | — | yes | — |
| `ctx.models.complete` | — | optional | yes |
| `ctx.candidates.submit` | once | per round, filtered | at most once |
| `ctx.candidates.verdict` | — | yes, between rounds | — |

## 11. API

Public endpoints are under the prefix `/openapi/v1`; headings below are
relative to it. The internal Job Protocol is under `/evolution/v1` and is
listed separately at the end of this section. Everything is JSON. Responses
below show the `data` payload of the standard envelope; see
[09-evolution-api.md](09-evolution-api.md) for the envelope, errors,
pagination, and idempotency. This document defines no new
endpoints: it shows how the default strategies use the endpoints defined in
[03-strategy.md](03-strategy.md) (registration),
[06-evolution-run.md](06-evolution-run.md) (policy, runs, Job Protocol), and
[09-evolution-api.md](09-evolution-api.md) (conventions, idempotency keys).

**Registration (public).**

### `POST /evolution/strategies`

Registers a version of a default strategy. Called by the strategy's
publisher (`avn strategy publish`; for platform-shipped strategies, the
platform's release pipeline). Agent definitions are uploaded with the
request and validated by the engine's `agents@1` provider. Normative
definition in [03-strategy.md](03-strategy.md).

Request (ClawEvolve, as in §4.2; consolidate-memory's record is in §7.2
and manual-patch's is below):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "runtime": {"kind": "job_worker", "image": "registry.example/clawevolve@sha256:…"},
  "needs": {
    "experience.sessions@1": {},
    "agents@1": {"definitions": {
      "clawevolve-tune":   {"engine": "openclaw", "path": "agents/clawevolve-tune"},
      "clawevolve-review": {"engine": "openclaw", "path": "agents/clawevolve-review"}
    }},
    "evaluate.train@1": {}
  },
  "params_schema": {"type": "object", "additionalProperties": false, "properties": {"window_days": {"type": "integer", "minimum": 1, "maximum": 90, "default": 7}}}   // abridged; full schema in §4.2
}
```

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "needs": {
    "experience.sessions@1": {},
    "agents@1": {"definitions": {
      "clawevolve-tune":   {"engine": "openclaw", "digest": "sha256:5d1f…"},   // stored content-addressed
      "clawevolve-review": {"engine": "openclaw", "digest": "sha256:9e07…"}
    }},
    "evaluate.train@1": {}
  },
  "conformance": "pending"                        // must pass the kit before binding outside development
}
```

Request (manual-patch):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "platform/manual-patch",
  "version": "1.0.0",
  "runtime": {"kind": "in_process", "entrypoint": "avernet_evolution.strategies.manual_patch:ManualPatchStrategy"},
  "needs": {},
  "params_schema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {"patch": {"$ref": "genome-patch.schema.json"}}
  }
}
```

Notable errors: `400` when a `needs` entry is not in the catalog; `422`
when an agent definition fails validation or names an engine with no
`agents@1` provider; `409` when the version already exists with different
content.

### `GET /evolution/strategies/{id}/versions/{version}`

Reads one registration record. Called by the UI, the binding check, and
operators.

Request: `GET /openapi/v1/evolution/strategies/platform%2Fconsolidate-memory/versions/1.0.0`

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "platform/consolidate-memory",
  "version": "1.0.0",
  "runtime": {"kind": "in_process", "entrypoint": "avernet_evolution.strategies.consolidate_memory:ConsolidateMemoryStrategy"},
  "needs": {"experience.feedback@1": {}, "experience.sessions@1": {}},
  "conformance": "passed"
}
```

Notable errors: `404` for an unknown id or version.

### Params schemas

Each strategy validates its params at run start (params are validated by
the strategy). Each default strategy also declares the schema below as
`params_schema` in its registration record (§4.2, §7.2, and the
manual-patch record above; a proposed, optional field defined in
[03-strategy.md](03-strategy.md)), so the binding check rejects bad params
when the policy is written ([06-evolution-run.md](06-evolution-run.md)).

`platform/manual-patch`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "patch": {"$ref": "genome-patch.schema.json"}   // the Genome Patch schema (01-genome.md)
  }
}
```

`patch` is not `required` in the schema: the binding (`bind_03`) carries
empty params, and each run supplies the patch in the run submission's
`params`. A run without a patch fails at start with a non-retryable reason.

`clawevolve/bot-evolution`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "window_days":  {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
    "max_rounds":   {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
    "poll_s":       {"type": "integer", "minimum": 10, "default": 60},
    "max_sessions": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500}
  }
}
```

`platform/consolidate-memory`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "window_days":        {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
    "min_feedback":       {"type": "integer", "minimum": 1, "default": 5},
    "max_new_items":      {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    "retire_unused_days": {"type": "integer", "minimum": 7, "default": 30}
  }
}
```

Schemas use JSON Schema draft 2020-12. Limits (minimum, maximum) are
proposed.

**Binding and starting the defaults (public).**

### `PUT /bots/{bot}/evolution/policy`

Sets the bot's list of bindings. Called by the bot owner or tenant admin
(UI, `avn evolve policy set`). Defined in
[06-evolution-run.md](06-evolution-run.md); shown here with the recommended
bindings for the defaults.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bindings": [
    {
      "id": "bind_01",
      "strategy": "clawevolve/bot-evolution@2.0.0",
      "trigger": {"schedule": "0 2 * * *"},
      "parent": "active",
      "allowed_genes": ["persona", "skills"],
      "verification_profile": "default@1",
      "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
      "params": {"window_days": 7, "max_rounds": 3}
    },
    {
      "id": "bind_02",
      "strategy": "platform/consolidate-memory@1.0.0",
      "trigger": {"schedule": "0 4 * * 0"},
      "parent": "active",
      "allowed_genes": ["memory"],
      "verification_profile": "memory@1",
      "budget": {"max_usd": 5, "max_wall_clock_s": 1800},
      "params": {}
    },
    {
      "id": "bind_03",
      "strategy": "platform/manual-patch@1.0.0",
      "trigger": {"manual": true},
      "parent": "active",
      "allowed_genes": ["persona", "skills", "memory", "resources"],
      "verification_profile": "deterministic@1",
      "budget": {"max_usd": 0, "max_wall_clock_s": 600},
      "params": {}
    }
  ]
}
```

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"bot": "bot_123", "bindings": ["bind_01", "bind_02", "bind_03"], "etag": "W/\"policy-7\""}
```

Notable errors (binding check, at configuration time rather than partway
through a paid run): `422` when the bot's engine has no provider for a
capability in `needs` (for example ClawEvolve on a non-OpenClaw bot,
because its agent definitions are OpenClaw only); `422` when
`allowed_genes` leaves the bot's genome `policy` (locked genes stay
locked); `412` on a stale `If-Match`.

### `POST /bots/{bot}/evolution/runs`

Starts a run. Defined in [06-evolution-run.md](06-evolution-run.md). The
manual-patch reference strategy is always started this way, with the patch
in `params`.

Request (with header `Idempotency-Key: 3f6c2a9e-8d1b-4c7e-9a0f-2b5d6e7f8a91`);
the body is `{binding, params?, budget?}`, here running `bind_03`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "binding": "bind_03",
  "params": {
    "patch": {
      "patch_schema": 1,
      "base": "sha256:a90b…",
      "ops": [{"op": "file.edit", "target": "skills/refund-policy/SKILL.md",
               "edits": [{"kind": "replace_section", "heading": "## When to use",
                          "content": "Use for full and partial refunds of paid orders."}]}],
      "rationale": "Partial refunds are allowed by policy",
      "evidence": ["episode:ep_91"]
    }
  }
}
```

Response (`202`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "queued"}
```

Notable errors: a repeat with the same `Idempotency-Key` returns the same
`run_id` (not an error); `422` when params fail the registration's
`params_schema`; a run whose patch base is not the parent fails at start
with a non-retryable reason.

### Internal: Job Protocol calls made by the default strategies

`clawevolve/bot-evolution` runs as a job worker and makes these HTTP calls.
The in-process strategies make the same calls as direct Python method calls
on `ctx`, with the same payloads. The endpoint list and semantics are
defined in [06-evolution-run.md](06-evolution-run.md); every request returns
promptly, work that can outlast a short request is an operation (`202` +
operation id), endpoints of capabilities that were not granted return
`403`, and calls with a stale fencing token return `409`. After the claim,
every call carries the fencing token in the `Evolution-Fencing-Token`
header, as defined there.

### `POST /evolution/v1/jobs:claim`

The ClawEvolve worker claims a job for its strategy id.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"worker_id": "clawevolve-worker-3", "strategy_ids": ["clawevolve/bot-evolution"]}
```

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "job_id": "job_7f3",
  "run_id": "run_7f3",
  "attempt": 1,
  "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60, "max_sessions": 500},
  "parent": "sha256:a90b…",
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "granted": ["candidates@1", "models@1", "experience.sessions@1", "agents@1", "evaluate.train@1"],
  "fencing_token": "ft_000231"
}
```

Notable errors: `204` (no content) when no job is available is proposed.

### `POST /evolution/v1/jobs/{id}/heartbeat`

Renews the lease while `run(ctx)` works. An expired lease re-queues the job
with the same run id and `attempt + 1`.

Request: `POST /evolution/v1/jobs/job_7f3/heartbeat` with header
`Evolution-Fencing-Token: ft_000231`, body `{}`.

Response (`cancelled: true` is how a cancellation reaches the worker; the
SDK turns it into `ctx.cancelled`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"lease_expires_at": "2026-10-09T02:06:00Z", "cancelled": false}
```

Notable errors: `409` after the lease was lost (the worker must stop).

### `GET /evolution/v1/runs/{run}/parent`

`ctx.parent`. Used by all three strategies.

Request: `GET /evolution/v1/runs/run_7f3/parent`

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "sha256:a90b…",
  "seq": 41,
  "spec": {
    "persona": [{"type": "SOUL.md", "digest": "sha256:11aa…"}],
    "skills": [{"name": "refund-policy", "digest": "sha256:22bb…", "origin": {"kind": "local"}}],
    "memory": {"mode": "merge", "items_digest": "sha256:33cc…"}
  },
  "lineage": ["sha256:a90b…", "sha256:8f20…"]
}
```

### `GET /evolution/v1/runs/{run}/content/{digest}`

File bytes of the parent or a workspace. consolidate-memory reads the
memory item set through it; the ClawEvolve black-box adapter reads files to
lay them out for the legacy scripts.

Request: `GET /evolution/v1/runs/run_8c2/content/sha256:33cc…`

Response (`application/json` for an item set; other files are returned as
raw bytes):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"items": [{"key": "old-shipping-sla", "text": "Standard shipping takes 5 days.", "tags": ["shipping"],
            "source": ["episode:ep_12"]}]}
```

### `GET /evolution/v1/runs/{run}/experience/sessions`

`ctx.experience.sessions(days, limit, revision)`. ClawEvolve's diagnose
input. Returns redacted, normalized episodes ([02-experience.md](02-experience.md)).

Request: `GET /evolution/v1/runs/run_7f3/experience/sessions?days=7&limit=500`

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "episodes": [
    {
      "episode_id": "ep_91",
      "revision_id": "sha256:a90b…",
      "started_at": "2026-10-07T09:12:00Z",
      "turns": [
        {"role": "user", "text": "Can I get a refund for half of my order?"},
        {"role": "assistant", "text": "…", "tool_calls": [{"name": "order_lookup", "args": {"id": "A17"}}]},
        {"role": "tool", "name": "order_lookup", "result": "…"}
      ],
      "outcome": {"status": "user_corrected", "feedback": "partial refunds are allowed"},
      "redactions": ["email", "phone"]
    }
  ]
}
```

Notable errors: `403` if `experience.sessions@1` was not granted.

### `GET /evolution/v1/runs/{run}/experience/feedback`

`ctx.experience.feedback(days, kinds, limit)`. consolidate-memory's main input.
The `Feedback` shape is normative in [02-experience.md](02-experience.md);
the example is illustrative.

Request: `GET /evolution/v1/runs/run_8c2/experience/feedback?days=7`

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "feedback": [
    {"feedback_id": "fb_301", "kind": "correction", "episode_id": "ep_91",
     "revision_id": "sha256:a90b…", "text": "partial refunds are allowed",
     "created_at": "2026-10-07T09:15:00Z"},
    {"feedback_id": "fb_305", "kind": "rating", "episode_id": "ep_97",
     "revision_id": "sha256:a90b…", "rating": -1, "created_at": "2026-10-07T14:02:00Z"}
  ]
}
```

Notable errors: `403` if `experience.feedback@1` was not granted (the case
for ClawEvolve 2.0.0).

### `POST /evolution/v1/runs/{run}/evaluations/cases`

`ctx.evaluate.add_train_cases`. ClawEvolve's plan step proposes cases; the
platform assigns splits. *Proposed reading:* the response reveals only the
cases assigned to `train`; cases the platform put into hidden splits are
counted but not identified.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "cases": [
    {"case_key": "run_7f3/f_12/1", "format": "clawbench-md/1",
     "content": "---\nid: partial_refund_01\ngrading_type: hybrid\n---\n## Prompt\nCan I get a refund for half of my order A17?\n",
     "derived_from": ["finding:f_12", "episode:ep_91"]}
  ]
}
```

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"received": 12, "train_case_ids": ["case_501", "case_503", "case_507"], "duplicates": 0}
```

Notable errors: `422` for a case that does not parse as a ClawBench case;
`403` if `evaluate.train@1` was not granted.

### `POST /evolution/v1/runs/{run}/workspaces`

`ctx.workspace.materialise(revision, key)`. Idempotent per key: a
re-dispatched run gets the same sandbox back, with agent edits already made.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:a90b…", "key": "run_7f3/round-2"}
```

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"workspace_id": "ws_42", "revision": "sha256:a90b…", "created": false}   // false: an existing sandbox was returned
```

### `POST /evolution/v1/runs/{run}/agents:start`

`ctx.agents.start(definition, workspace, prompt, idempotency_key)`. Starts
the tune or review agent inside the sandbox as a long-running operation.
The definition is loaded by digest, read-only; only the workspace is
writable.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "definition": "clawevolve-tune",
  "workspace_id": "ws_42",
  "prompt": "Findings: f_12 partial refunds refused. History: round 1 rejected (regression on case_88). Edit skills/refund-policy …",
  "idempotency_key": "run_7f3/round-2/tune",
  "timeout_s": 1800
}
```

Response (`202`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"operation_id": "op_19a", "status": "queued"}
```

Notable errors: `403` for a definition the strategy did not register or if
`agents@1` was not granted; a repeat with the same key returns `202` with
the same `operation_id`.

### `POST /evolution/v1/runs/{run}/evaluations:train`

`ctx.evaluate.start_train(workspace, idempotency_key)`. Replaces
ClawEvolve's own train bench (`bench-full-opt`). Train split only.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"workspace_id": "ws_42", "idempotency_key": "run_7f3/round-2/train"}
```

Response (`202`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"operation_id": "op_19b", "status": "queued"}
```

### `GET /evolution/v1/runs/{run}/operations/{id}`

`ctx.operations.get(op_id)`; the SDK's `operations.wait` repeats this short
lookup. Status values: `queued | running | succeeded | failed | cancelled`.

Request: `GET /evolution/v1/runs/run_7f3/operations/op_19b`

Response (a succeeded train evaluation; scores and critiques are the
feedback reflective strategies need):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "op_19b",
  "status": "succeeded",
  "result": {
    "score": 0.82,
    "cases": [
      {"case_id": "case_501", "score": 1.0, "critique": "Offered a partial refund correctly"},
      {"case_id": "case_503", "score": 0.4, "critique": "Did not ask for the order id before refunding"}
    ],
    "cost": {"usd": "1.20", "rollouts": 36}
  }
}
```

A succeeded agent operation returns an `AgentResult` instead, for example
`{"exit_status": "ok", "transcript_artifact": "art_77"}`; files it changed
stay in the workspace until the strategy turns them into a patch.

### `POST /evolution/v1/runs/{run}/models:complete`

`ctx.models.complete(messages, model, max_tokens)`. consolidate-memory uses
it to cluster feedback into lessons. Charged to the budget; the model used
is recorded in the ledger. When `model` is omitted, the platform default is
used.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "messages": [
    {"role": "system", "content": "Group the feedback into lessons. Answer as JSON: [{key, text, tags, sources, action}]."},
    {"role": "user", "content": "fb_301 correction: partial refunds are allowed\nfb_305 rating -1 on ep_97\n…"}
  ],
  "max_tokens": 2048
}
```

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "text": "[{\"key\": \"partial-refunds-allowed\", \"text\": \"Partial refunds are allowed for paid orders.\", \"tags\": [\"billing\"], \"sources\": [\"fb_301\", \"fb_305\"], \"action\": \"add\"}]",
  "model": "platform-default",
  "usage": {"input_tokens": 912, "output_tokens": 64},
  "charged_usd": "0.01"
}
```

Notable errors: when the run's budget is exhausted the call is refused and
the SDK raises `BudgetExhausted` (the status code is defined in
[06-evolution-run.md](06-evolution-run.md)).

### `POST /evolution/v1/runs/{run}/candidates`

`ctx.candidates.submit(candidate)`. Idempotent: the candidate id is the
content hash of the patch, so a resubmission returns the same id.

Request (from ClawEvolve round 2):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch": {"patch_schema": 1, "base": "sha256:a90b…",
            "ops": [{"op": "skill.update", "name": "refund-policy",
                     "file_ops": [{"kind": "unified_diff", "path": "SKILL.md", "diff": "@@ -3,1 +3,1 @@ …"}]},
                    {"op": "file.edit", "target": "persona/SOUL.md",
                     "edits": [{"kind": "replace_section", "heading": "## Escalation", "content": "…"}]}]},
  "rationale": "Round 2: train 82% vs best 78%; fixes trigger misses on partial refunds",
  "evidence": ["finding:f_12", "episode:ep_91"],
  "self_metrics": {"train_score_pct": 82}       // shown to reviewers, never used for acceptance
}
```

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"candidate_id": "sha256:c41e…", "created": true}
```

Notable errors: `422` when the patch fails the schema, its base is not
current, or an op touches a gene outside the binding's `allowed_genes` or a
locked gene.

### `GET /evolution/v1/runs/{run}/candidates/{id}`

`ctx.candidates.verdict(candidate_id)`. Status `pending | accept | reject |
inconclusive`, with validation **aggregates only** (the `StrategyVerdictView`
of [07-verification.md](07-verification.md)). ClawEvolve reads
`revision` to build its next round on an accepted candidate.

Request: `GET /evolution/v1/runs/run_7f3/candidates/sha256:c41e…`

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate": "sha256:c41e…",
  "status": "accept",
  "revision": "sha256:d7a2…",                   // r42, the candidate revision recorded by the Genome Registry
  "aggregates": {"validation": {"cases": 30, "seeds": 3, "mean_delta": 0.061, "ci": [0.028, 0.094]}},
  "reasons": ["validation_improved"]
}
```

### `POST /evolution/v1/jobs/{id}/complete`

Ends the job successfully with the strategy's `RunSummary`.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"rounds": 3, "candidates": ["sha256:c41e…", "sha256:e903…"], "findings": 4}
```

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "completed"}
```

### `POST /evolution/v1/jobs/{id}/fail`

Ends the job with a failure. A retryable failure may be re-dispatched under
the same run id (up to the run's attempt limit); a non-retryable one ends
the run as `failed`. manual-patch uses it for a patch whose base is not the
parent.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"reason": "diagnose found no usable sessions in window", "retryable": false}
```

Response:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "failed"}
```

## 12. Examples

### 12.1 `platform/manual-patch`

```python
class ManualPatchStrategy(EvolutionStrategy):
    async def run(self, ctx: StrategyContext) -> RunSummary:
        params = ManualPatchParams.parse(ctx.params)          # validates against the params schema
        if params.patch.base != ctx.parent.id:
            raise NonRetryableRunError("patch base is not the run's parent revision")
        candidate_id = await ctx.candidates.submit(Candidate(
            patch=params.patch,
            rationale=params.patch.rationale,
            evidence=params.patch.evidence,
        ))                                                    # same patch → same id after a re-dispatch
        ctx.log.info("submitted", candidate=candidate_id)
        return RunSummary(candidates=[candidate_id])
```

A pipeline using it end to end (client SDK, [09-evolution-api.md](09-evolution-api.md)):

```python
from avernet_evolution import Client

c = Client.from_env()
run_id = c.runs.start(bot="bot_123", binding="bind_03",            # binding runs platform/manual-patch@1.0.0
                      params={"patch": patch}, idempotency_key="fix-refund-trigger-2026-10-09")
run = c.runs.get(run_id)                       # repeat until terminal
cand = run.candidates()[0]
report = cand.report()                         # diff + verification + gate decision (08-promotion.md)
```

### 12.2 `clawevolve/bot-evolution` (native strategy)

Its internals stay (diagnose logic, tune prompt, mutation operator library,
round loop); only the edges move to the context. Compared with the earlier
sketch, this version also runs the review agent and fills the run summary.

```python
class ClawEvolveStrategy(EvolutionStrategy):
    def __init__(self, store: RunStateStore[ClawEvolveState]) -> None:
        self.store = store                                            # its own storage, not the platform's

    async def run(self, ctx: StrategyContext) -> RunSummary:
        p = ClawEvolveParams(**ctx.params)
        state = await self.store.load(ctx.run_id)
        if state is None:                                             # first attempt
            episodes = await ctx.experience.sessions(days=p.window_days, limit=p.max_sessions)
            findings = diagnose(episodes)                             # clawevolve-diagnose, unchanged logic
            await ctx.evaluate.add_train_cases(plan_bench(findings))  # platform assigns splits
            state = ClawEvolveState(run_id=ctx.run_id, findings=findings, base=ctx.parent.id,
                                    next_round=0, best_train_pct=0, pending=None, history=[])
            await self.store.save(ctx.run_id, state)

        while state.next_round < p.max_rounds:
            if ctx.cancelled.is_set():
                break
            key = f"{ctx.run_id}/round-{state.next_round}"           # same keys after a re-dispatch
            if state.pending is None:
                ws = await ctx.workspace.materialise(state.base, key=key)   # sandbox, not the live bot
                tune = await ctx.agents.start("clawevolve-tune", workspace=ws,
                                              prompt=build_tune_prompt(state.findings, state.history),
                                              idempotency_key=f"{key}/tune")
                await ctx.operations.wait(tune)                       # short status lookups by id
                train_op = await ctx.evaluate.start_train(ws, idempotency_key=f"{key}/train")
                train = (await ctx.operations.wait(train_op)).result  # replaces its own bench step
                review = await ctx.agents.start("clawevolve-review", workspace=ws,
                                                prompt=build_review_prompt(state.findings, train),
                                                idempotency_key=f"{key}/review")
                await ctx.operations.wait(review)
                pct = round(train.score * 100)                        # TrainResult.score is 0..1
                if pct > state.best_train_pct:                        # its own submission filter
                    state.pending = await ctx.candidates.submit(Candidate(
                        patch=ws.to_patch(),
                        rationale=f"Round {state.next_round}: train {pct}%",
                        evidence=[f"finding:{f.finding_id}" for f in state.findings],
                        self_metrics={"train_score_pct": pct}))
                    state.best_train_pct = pct
                    state.history.append(RoundRecord(state.next_round, state.pending,
                                                     pct, "pending"))
                    await self.store.save(ctx.run_id, state)          # survives a crash from here on
            if state.pending is not None:
                verdict = await ctx.candidates.verdict(state.pending) # the platform decides
                if verdict.status == "pending":
                    await asyncio.sleep(p.poll_s)                     # counts against max_wall_clock_s
                    continue
                if verdict.status == "accept":
                    state.base = verdict.revision                     # next round builds on it
                state.history[-1].verdict = verdict.status
                state.pending = None
            state.next_round += 1
            await self.store.save(ctx.run_id, state)

        return RunSummary(rounds=state.next_round, findings=len(state.findings),
                          candidates=[r.candidate for r in state.history if r.candidate])
```

(Ordering note: in this sketch review runs after the train evaluation,
reviewing the edited sandbox with its train critiques. The review agent may
edit the sandbox further in today's flow; if so, the train evaluation must
run after review. The exact stage order stays ClawEvolve's choice.)

### 12.3 ClawEvolve black-box adapter (migration step 2)

Before the skills are ported, the adapter calls the existing scripts on a
local copy of the sandbox. Sketch only:

```python
class ClawEvolveAdapter(EvolutionStrategy):
    async def run(self, ctx: StrategyContext) -> RunSummary:
        ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/adapter")
        local = await ws.checkout(tmp_dir(ctx.run_id))               # files by digest via content/{digest}
        await export_sessions_for_legacy(ctx, local.sessions_dir)    # ctx.experience.sessions → session-export/v1 files
        run_legacy_optimize(workspace=local.root, sessions=local.sessions_dir,
                            no_pack=True, no_deploy=True)            # existing skill scripts, paths re-pointed
        await ws.sync_from(local)                                    # write edits back into the sandbox
        cid = await ctx.candidates.submit(Candidate(patch=ws.to_patch(), rationale="legacy adapter run"))
        return RunSummary(candidates=[cid])
```

`checkout`, `sync_from`, and `export_sessions_for_legacy` are adapter
helpers, not SDK contracts; the legacy scripts still invoke `openclaw agent
--local` inside the worker during this step, which is why step 3 replaces
them with `ctx.agents.start`.

### 12.4 `platform/consolidate-memory`

```python
class ConsolidateMemoryStrategy(EvolutionStrategy):
    def __init__(self, store: RunStateStore[ConsolidationPlan]) -> None:
        self.store = store

    async def run(self, ctx: StrategyContext) -> RunSummary:
        p = ConsolidateMemoryParams(**ctx.params)
        plan = await self.store.load(ctx.run_id)                     # set by an earlier attempt?
        if plan is None:
            feedback = await ctx.experience.feedback(days=p.window_days)
            if len(feedback) < p.min_feedback:
                return RunSummary(candidates=[], skipped="not enough feedback")
            cited = {f.episode_id for f in feedback if f.episode_id}
            episodes = [e for e in await ctx.experience.sessions(days=p.window_days)
                        if e.episode_id in cited]                    # supporting evidence per lesson
            current = await load_memory_items(ctx.parent)            # items_digest → content by digest
            lessons = await cluster_lessons(ctx.models, feedback, episodes, current)   # models.complete, JSON answer parsed
            ops = to_memory_ops(lessons, current,
                                max_new=p.max_new_items,
                                retire_unused_days=p.retire_unused_days,
                                pins=ctx.parent.policy.pins)         # pinned items are never touched
            if not ops:
                return RunSummary(candidates=[], skipped="no new lessons")
            plan = ConsolidationPlan(run_id=ctx.run_id, base=ctx.parent.id, ops=ops,
                                     rationale=summarize(lessons),
                                     evidence=[s for l in lessons for s in l.sources])
            await self.store.save(ctx.run_id, plan)                  # before submit: re-dispatch resubmits this
        cid = await ctx.candidates.submit(Candidate(
            patch=GenomePatch(patch_schema=1, base=plan.base, ops=plan.ops,
                              rationale=plan.rationale, evidence=plan.evidence),
            rationale=plan.rationale, evidence=plan.evidence))
        return RunSummary(candidates=[cid], ops=Counter(op["op"] for op in plan.ops))
```

### 12.5 A nightly ClawEvolve run, end to end

1. Binding `bind_01` fires at 02:00 on `bot_123`. The platform submits the
   run with idempotency key `bind_01/2026-10-09T02:00:00Z` and gets
   `run_7f3`; strategy version `2.0.0`, params, budget (`max_usd: 20`), and
   parent (`active` = `r41`, `sha256:a90b…`) are frozen.
2. The ClawEvolve worker claims the job (§11, Job Protocol) with the granted
   capabilities `experience.sessions@1`, `agents@1`, `evaluate.train@1`.
3. Diagnose reads the last 7 days of episodes of `r41`, finds `f_12`, and
   proposes 12 train cases; the platform keeps some of them hidden.
4. Round 0: the tune agent edits sandbox `ws_42`, train scores 78%, the
   strategy submits `sha256:c41e…`.
5. Verification (`platform/clawbench` graders, paired with `r41`, with
   regression and safety suites) returns `accept`; the gate assigns T2
   (persona + skill), so the candidate goes to the review queue
   ([08-promotion.md](08-promotion.md)). The candidate revision `r42`
   becomes the base of round 1.
6. The worker is killed during round 1. The lease expires; the run is
   dispatched again with `attempt = 2`. ClawEvolve reloads its state,
   repeats `materialise` and `agents.start` with the same keys, and gets back
   `ws_43` and the already-running tune operation.
7. After three rounds the job completes. The owner approves `r42`;
   promotion moves `active` to `r42`. Every submission, accepted or not, is
   in the Experiment Ledger with the strategy version and agent definition
   digests ([05-experiment-ledger.md](05-experiment-ledger.md)).

## 13. Interactions

| Other component / service | Direction | What flows |
| --- | --- | --- |
| Strategy Registry ([03-strategy.md](03-strategy.md)) | defaults → registry | Registration records, ClawEvolve agent definitions (uploaded, stored by digest), conformance results |
| Evolution Run ([06-evolution-run.md](06-evolution-run.md)) | both | Jobs and leases, frozen params/parent/budget, every `ctx` call (Job Protocol for ClawEvolve, in-process for the platform strategies), `RunSummary` |
| Experience ([02-experience.md](02-experience.md)) | experience → defaults | Episodes (ClawEvolve), feedback and cited episodes (consolidate-memory); ClawEvolve's session export code moves there as the OpenClaw provider |
| Verification ([07-verification.md](07-verification.md)) | both | Train cases and train evaluations (from ClawEvolve); verdict aggregates (to all); ClawBench graders, Bench store model, split rules, and gate functions extracted from ClawEvolve |
| Promotion ([08-promotion.md](08-promotion.md)) | candidates → gate | Candidates reach the gate and review queue; ClawEvolve's `skill-decision` approval is replaced by the review queue |
| Genome ([01-genome.md](01-genome.md)) | both | Parent revision and content by digest (in); Genome Patches (out); shadow-recorded revisions in migration step 1 |
| Experiment Ledger ([05-experiment-ledger.md](05-experiment-ledger.md)) | run → ledger | Each submission with strategy version, agent definition digests, models used, cost |
| Meta-evolution ([10-meta-evolution.md](10-meta-evolution.md)) | later | ClawEvolve's prompts, operator library, and submission-filter thresholds are the first mechanism to evolve; gate calibration/replay tools seed mechanism verification |
| Evolution API ([09-evolution-api.md](09-evolution-api.md)) | callers → defaults | Starting manual-patch runs; binding the defaults; `avn strategy publish` for ClawEvolve |
| Engine adapter (OpenClaw) | providers for defaults | `experience.sessions` provider (session export), `agents@1` provider (runs ClawEvolve's agents in a sandbox), memory projection (RSI-05) for consolidate-memory |
| ClawWeb / AgentEvolve UI | interim | Shows platform runs alongside legacy tasks during migration (step 2); may host the default strategy runner until DS-1 is decided |
| ClawInsight | → ClawEvolve bindings | Event triggers; `plan-source/v2` findings as feedback input (later version) |

## 14. Open decisions

- **DS-1 (was D-2): Long-term host of the default strategy runners.** Keep the TS
  ClawWeb control plane as a long-term *host* of the default strategy
  runners, or port runners to the Python strategy SDK? Recommendation: keep
  skills Python (already stdlib-only Python), run them as job workers;
  retire TS orchestration code after migration step 3. Decided in RSI-13.
- **DS-2 (was D-3): ClawBench as the platform default grader.** Is ClawBench the
  platform's default grader, or one grader among others? Recommendation:
  platform default (it already supports automated, rubric-judge, and hybrid
  grading), with the grader interface open. Decided in RSI-11.
- **DS-3 (was D-4): Workflow YAML (TaskGuard) as a genome gene, or a separate
  artifact?** Recommendation: separate artifact with the same revision/ref
  model; decide when workflow-run healing is onboarded.
- **DS-4 (was D-5): The ClawMind analyzer.** The external "ClawMind" `analyze` handler
  is not in this repo. Its contract must be brought in or redefined before
  workflow-run healing is onboarded. The dormant `SingleRunAnalyzer` /
  `BatchRunAnalyzer` / `LessonExpireScheduler` code should be either wired
  into strategies or removed.
- **DS-5: Interim memory binding.** Before RSI-05, consolidate-memory
  writes a persona file (`LESSONS.md`), but its binding allows only
  `memory`. Options: allow `persona` for the interim binding (wider than
  needed), or let `allowed_genes` name one item (for example
  `persona/LESSONS.md`) if the genome policy supports item-level targets.
  Recommendation: item-level target, if [01-genome.md](01-genome.md) can
  express it.
- **DS-6: One ClawEvolve strategy or three.** `bot_evolution`,
  `skill_evolution`, and `skill_hardening` as three registered strategies,
  or one strategy with a flow param? The sources leave both open.
  Recommendation: `skill_hardening` as its own strategy (different stages,
  different `allowed_genes`); `skill_evolution` as a param of
  `clawevolve/bot-evolution` (same stages, narrower target).

Resolved: params schemas are declared as `params_schema` in each
registration record and checked by the binding check (§11, Params schemas;
[03-strategy.md](03-strategy.md)); consolidate-memory declares both
`experience.feedback@1` and `experience.sessions@1` (§7.2).
