# Meta-evolution

> 中文版：[10-meta-evolution.zh-CN.md](10-meta-evolution.zh-CN.md)

> Status: DRAFT. Service in the [bot evolution architecture](design.md).
> Level 3: improving the improvement mechanism itself, verifying a candidate
> mechanism against the current one, and the bounds no automated loop may cross.

> **Later (level 3). Not in the first iteration.** The first iteration covers
> levels 1–2: bots improve, and every change is verified. This document fixes
> the level-3 design early so that the level-2 contracts (strategy versions,
> runs, candidates, the Experiment Ledger H) are shaped so that they do not
> block level 3 later; §4 lists exactly what level 2 must already provide.
> The only level-3 groundwork built in the first iteration is **recording
> every experiment in H** ([05-experiment-ledger.md](05-experiment-ledger.md)),
> which level 2 needs anyway for its archive and audit trail. Everything else
> here, including every endpoint in §13, is **proposed** and not scheduled for
> the first iteration.

Read [07-verification.md](07-verification.md) first: recursion is only as
trustworthy as its verifier.

## 1. Purpose and scope

Meta-evolution is what makes the platform *recursive* self-improvement rather
than repeated single improvements. Level 2 improves a bot with a fixed
improvement mechanism. Level 3 treats that mechanism as something that can be
improved too: it learns from the record of all level-2 experiments, proposes
a better mechanism, verifies that the new mechanism produces better *verified*
improvements than the current one, and only then lets it run the following
level-2 rounds.

**Owns (proposed):**

- The **mechanism** as an evolvable artifact: mechanism revisions, mechanism
  patches, and per-family mechanism refs (`active`, `previous`, `canary`,
  `candidate/*`) — the same revision/ref/patch model as the Bot Genome.
- The **level-3 loop**: triggering, running a meta-strategy, static checks on
  mechanism patches, adoption, and hand-over to level-2 runs.
- **Mechanism verification**: improvement problems, the comparison protocol
  (M′ vs M1 on held-out improvement problems), and replay screening.
- **Mechanism metrics**: the fitness of a mechanism, derived from H.
- The **bounds of recursion**: the verifier boundary, depth limit, and
  adoption rules.

**Does not own:**

| Concern | Owner |
| --- | --- |
| The Experiment Ledger H itself: its schema, recording, retention, export, and audit | [05-experiment-ledger.md](05-experiment-ledger.md) |
| Strategy registration records, the capability catalog, the strategy port `run(ctx)`, `StrategyContext` | [03-strategy.md](03-strategy.md) |
| The concrete default strategy (ClawEvolve) whose mechanism is the first one level 3 would improve | [04-default-strategies.md](04-default-strategies.md) |
| Running a run: bindings, leases, budgets, kill switches, sandboxing, the Job Protocol | [06-evolution-run.md](06-evolution-run.md) |
| Bot verification (S′ vs S1): suites, splits, graders, comparator, verdicts, verifier integrity | [07-verification.md](07-verification.md) |
| Separation of powers, the gate, risk tiers, promotion of bot genomes | [08-promotion.md](08-promotion.md) |
| Shared API conventions, idempotency keys, SDKs, the `avn` CLI | [09-evolution-api.md](09-evolution-api.md) |
| Genome revisions and the revision/ref/patch machinery level 3 reuses | [01-genome.md](01-genome.md) |

**Where it runs.** In the new module `apps/evolution`, next to the Strategy
Registry, Evolution Run, Verification, and the ledger (module placement is
open decision D-1, see [design.md](design.md)). The meta-loop needs no Backend
code: mechanisms are strategy versions, not bot desired state.

## 2. Domain model

### 2.1 The three levels

The platform is organised around three levels. Each level wraps the one
before it.

| Level | Loop | What changes | Verified by | Where |
| --- | --- | --- | --- | --- |
| **1. Agent** | task → bot S1 ⇄ environment → result | Nothing persistent; the bot finishes the current task | Task outcome | Produces **Experience** ([02-experience.md](02-experience.md)) |
| **2. Single system improvement** | S1 + task feedback → mechanism **M1** proposes change → candidate S′ runs in environment → **verify & accept** → S2 | The **system** (bot genome). Later tasks use S2 | **Bot verification** (S′ vs S1) | A strategy run over the Bot Genome ([06-evolution-run.md](06-evolution-run.md), [07-verification.md](07-verification.md), [08-promotion.md](08-promotion.md)) |
| **3. Recursive self-improvement** | experiment records **H** → improve M1 → **verify & adopt** M2 → M2 runs the following level-2 rounds | The **improvement mechanism** itself. M2 runs the next round | **Mechanism verification** (M2 vs M1) | This document |

![Level 3: improving the improvement mechanism](images/recursion.svg)

No level guarantees improvement. Every change is verified, and a rejected
candidate is a normal, recorded outcome. That outcome is evidence for H.

### 2.2 Types

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `MechanismRevision` | One immutable, content-addressed version of a strategy family's mechanism: the strategy version plus everything that determines its behaviour (§3) | Strategy Registry ([03-strategy.md](03-strategy.md)); level-3 additions here | `draft → candidate → accepted \| rejected → promoted → archived` (mirrors genome revision status) |
| `MechanismRef` | Named, movable pointer to a mechanism revision of one strategy family: `active`, `previous`, `canary`, `candidate/<run>/<n>` | Strategy Registry; moved only by mechanism promotion (and the orchestrator for `candidate/*`) | Moved by compare-and-swap |
| `MechanismPatch` | Typed, itemized change to a mechanism revision; the only thing a meta-strategy may submit | Meta-strategy (proposes) → platform (records) | Immutable once recorded; its candidate id is its content hash |
| Meta-strategy | An ordinary strategy (`StrategyRegistration`) whose target is a mechanism instead of a bot genome | Strategy author | Registered and versioned like any strategy |
| Meta-run | A `Run` of a meta-strategy against one strategy family | Evolution Run ([06-evolution-run.md](06-evolution-run.md)) | Run states: `queued \| running \| completed \| failed \| cancelled \| budget_exhausted` |
| `ImprovementProblem` | A frozen, replayable level-2 task: starting genome, experience snapshot, visible and hidden suites, fixed budget | Mechanism verification (this doc), frozen from H | Immutable once frozen; retired when its verifier version is superseded |
| `MechanismVerificationProfile` | Versioned, human-owned policy for comparing mechanisms: problem split, seeds, significance, tolerances, baseline rules | Verifier (humans) | Changed only by reviewed changes; versioned |
| `MechanismVerification` | One comparison of a candidate mechanism against a baseline mechanism on held-out problems; an `Operation` whose result carries a `Verdict` | Mechanism verification | Operation states: `queued \| running \| succeeded \| failed \| cancelled` |
| `ReplayScreening` | A cheap pre-screen: stored candidates replayed through a new submission filter or verification profile, measuring precision/recall against labelled outcomes | Mechanism verification | Operation; never adoption evidence |
| `MechanismMetrics` | The fitness of a mechanism revision per bot segment and verifier version, derived from H | Derived from the ledger ([05-experiment-ledger.md](05-experiment-ledger.md)); defined here | Recomputed as H grows |
| `LedgerEntry` | One level-2 (or level-3) experiment record | [05-experiment-ledger.md](05-experiment-ledger.md) | Append-only |
| `GateDecision`, `RiskTier`, `ReviewItem`, `Promotion` | Shared promotion types, used here with a mechanism gate profile | [08-promotion.md](08-promotion.md) | As there |

### 2.3 `MechanismRevision`

A mechanism revision is a strategy version treated exactly like a Bot Genome
revision: immutable, content-addressed, with parents and provenance, changed
only by a typed patch. The id is the SHA-256 of the RFC 8785 canonical form
of the mechanism content (registration record, agent definition digests,
default params); the human-readable `version` plays the role the per-bot
`seq` plays for genomes.

```python
from dataclasses import dataclass
from typing import Literal

MechanismStatus = Literal["draft", "candidate", "accepted", "rejected", "promoted", "archived"]

@dataclass(frozen=True)
class MechanismProvenance:
    kind: Literal["human", "meta_run"]   # registered by a person, or proposed by a meta-run
    actor: str                       # user id, or the meta-strategy "id@version"
    run_id: str | None = None        # set only when kind == "meta_run"

@dataclass(frozen=True)
class MechanismRevision:
    id: str                          # "sha256:…" over canonical mechanism content
    family: str                      # strategy id, e.g. "clawevolve/bot-evolution"
    version: str                     # human handle, e.g. "2.0.0"; not an identity
    target_kind: Literal["bot_genome"]  # what this mechanism improves (level 2)
    registration: dict               # the StrategyRegistration record (03-strategy.md)
    agent_definitions: dict[str, str]   # definition name → content digest
    default_params: dict             # thresholds, round limits, per-step models and budgets
    parents: list[str]               # mechanism revision ids
    created_by: MechanismProvenance
    patch_from_parent: str | None    # digest of the MechanismPatch; None for human-registered versions
    status: MechanismStatus
    verifier_version: str | None     # verifier version of the evidence that adopted it; None until verified
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "sha256:5b0e…",
  "family": "clawevolve/bot-evolution",
  "version": "2.0.1",
  "target_kind": "bot_genome",                       // the loop is generic over what it improves
  "agent_definitions": {
    "clawevolve-tune": "sha256:91aa…",               // tune prompt changed: new digest
    "clawevolve-review": "sha256:0c7d…"
  },
  "default_params": {
    "max_rounds": 3,
    "submission_filter": {"min_train_gain_pct": 2, "max_regressed_ratio_pct": 10}
  },
  "parents": ["sha256:3f12…"],                       // 2.0.0
  "created_by": {"kind": "meta_run", "run_id": "run_m12", "actor": "platform/meta-evolve@0.1.0"},
  "patch_from_parent": "sha256:d27f…",
  "status": "candidate",
  "verifier_version": null
}
```

The mechanism itself (what a revision fixes) is described in §3.

### 2.4 `MechanismPatch`

```python
@dataclass(frozen=True)
class MechanismOp:
    op: Literal["param.set", "prompt.edit", "operator.add", "flow.edit", "plugin.replace"]
    target: str                      # param path, agent definition file, operator library, or flow step
    value: object | None = None      # for param.set / plugin.replace
    edits: list[dict] | None = None  # for prompt.edit / operator.add / flow.edit (same edit kinds as genome file.edit)

@dataclass(frozen=True)
class MechanismPatch:
    patch_schema: int
    family: str
    base: str                        # mechanism revision id; must equal the parent (compare-and-swap on record)
    ops: list[MechanismOp]
    rationale: str
    evidence: list[str]              # ledger entry ids the proposal is based on
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch_schema": 1,
  "family": "clawevolve/bot-evolution",
  "base": "sha256:3f12…",
  "ops": [
    {"op": "param.set", "target": "submission_filter.min_train_gain_pct", "value": 2},
    {"op": "prompt.edit", "target": "agents/clawevolve-tune/SKILL.md",
     "edits": [{"kind": "replace_section", "heading": "## Persona edits", "content": "Prefer one itemized change per finding; …"}]}
  ],
  "rationale": "Persona rewrites from the tune step were rejected in 61% of experiments on support bots",
  "evidence": ["ledger:le_5512", "ledger:le_5530", "ledger:le_5601"]
}
```

The candidate id of a mechanism patch is its content hash (for example
`sha256:d27f…`), exactly as for Genome Patches, so a resubmission returns the
same id. A human-registered strategy version has no patch; when it is put up
for adoption, its candidate id is its mechanism revision id.

### 2.5 `ImprovementProblem`

A frozen, replayable level-2 task. Problems are built from H: real past
experiments, frozen with their inputs. Synthetic problems can be added, for
example deliberately degraded genomes with a known fix.

```python
ProblemSplit = Literal["mechanism_train", "mechanism_holdout"]   # §8.2

@dataclass(frozen=True)
class ImprovementProblem:
    id: str                          # "prob_204"
    bot_genome_revision: str         # starting system S
    experience_snapshot: str         # episodes / feedback the mechanism may see
    visible_suites: list[str]        # visible to the mechanism under normal level-2 rules
    hidden_suites: list[str]         # used only to score the outcome
    budget: dict                     # identical for every mechanism compared
    segment: dict                    # engine and bot type, for stratification
    source: Literal["ledger", "synthetic"]
    source_entry: str | None         # ledger entry it was frozen from; None for synthetic problems
    split: ProblemSplit
    verifier_version: str            # the verifier version the problem was frozen under (§9)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "prob_204",
  "bot_genome_revision": "sha256:a90b…",          // starting system S (r41 of a support bot)
  "experience_snapshot": "snapshot:exp_77",       // episodes / feedback the mechanism may see
  "visible_suites": ["train", "validation"],      // visible to the mechanism under normal rules
  "hidden_suites": ["holdout", "regression", "safety"], // used only to score the outcome
  "budget": {"max_usd": 20, "max_rollouts": 400}, // identical for every mechanism compared
  "segment": {"engine": "openclaw", "bot_type": "support"},
  "source": "ledger",
  "source_entry": "ledger:le_5512",
  "split": "mechanism_holdout",
  "verifier_version": "verifier@4"
}
```

### 2.6 `MechanismVerificationProfile`

```python
@dataclass(frozen=True)
class MechanismVerificationProfile:
    id: str                          # e.g. "mechanism-default@1"
    seeds_per_problem: int           # how many times each mechanism runs each problem
    min_holdout_problems: int        # fewer mechanism-holdout problems than this: verification refused
    significance: str                # e.g. "0.05"; strings, no floats in hashed content
    min_yield_gain_pct: int          # lower confidence bound of the yield gain must clear this
    max_regression_rate_increase_pct: int
    max_false_acceptance_increase_pct: int
    cost_tolerance_pct: int
    require_budget_matched_baseline: bool
    canary_share_pct: int            # 0 = no mechanism-level canary
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "mechanism-default@1",
  "seeds_per_problem": 3,
  "min_holdout_problems": 30,
  "significance": "0.05",
  "min_yield_gain_pct": 1,
  "max_regression_rate_increase_pct": 0,
  "max_false_acceptance_increase_pct": 0,
  "cost_tolerance_pct": 10,
  "require_budget_matched_baseline": true,
  "canary_share_pct": 0
}
```

### 2.7 `MechanismVerification` and its verdict

```python
@dataclass(frozen=True)
class ProblemOutcome:
    problem: str
    seed: int
    mechanism: str                   # mechanism revision id
    level2_run: str                  # the sandboxed level-2 run
    accepted_candidate: str | None   # what bot verification would have promoted; None if nothing passed
    hidden_gain_pct: int             # verified gain on the hidden suites (0 if nothing accepted)
    regressed: bool                  # accepted candidate regressed on hidden regression/safety
    cost_usd_cents: int

@dataclass(frozen=True)
class MechanismVerification:
    id: str
    family: str
    candidate: str                   # mechanism revision id under test (M′)
    baseline: str                    # usually the family's active revision (M1)
    profile: str
    verifier_version: str
    operation: str                   # operation id; status by id
    verdict: Literal["pending", "accept", "reject", "inconclusive"]
    comparison: dict | None          # paired statistics per mechanism metric, once finished
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "mv_31",
  "family": "clawevolve/bot-evolution",
  "candidate": "sha256:5b0e…",
  "baseline": "sha256:3f12…",
  "profile": "mechanism-default@1",
  "verifier_version": "verifier@4",
  "operation": "op_19a",
  "verdict": "accept",
  "comparison": {
    "problems": 34, "seeds": 3,
    "yield_gain_pct": {"mean": "3.1", "ci_lower": "1.4", "ci_upper": "4.8"},   // paired per-problem differences
    "regression_rate_pct": {"baseline": "2.9", "candidate": "2.0"},
    "false_acceptance_rate_pct": {"baseline": "5.9", "candidate": "5.9"},
    "cost_per_accepted_usd": {"baseline": "6.40", "candidate": "6.75"},
    "budget_matched_baseline": {"yield_gain_pct": {"mean": "2.2", "ci_lower": "0.6"}}
  }
}
```

### 2.8 `MechanismMetrics`

The fitness of a mechanism, computed per mechanism revision, per bot segment,
and per verifier version (metrics are never compared across verifier
versions, §10). This doc owns the definitions; the Experiment Ledger
([05-experiment-ledger.md](05-experiment-ledger.md)) stores them as a derived
view with the same field names.

| Metric | Definition |
| --- | --- |
| **Verified improvement yield** | Mean verified gain of promoted S2 over S1 on held-out and regression suites, per unit cost |
| **Acceptance rate** | Share of submitted candidates that verification accepted |
| **False-acceptance rate** | Share of promotions later rolled back (gone back from), or regressing online |
| **Regression rate** | Share of candidates that regress on safety and regression suites |
| **Cost per accepted improvement** | Total spend divided by accepted candidates |
| **Descendant productivity** | How much the lineage *after* an accepted candidate keeps improving. The Huxley-Gödel Machine shows this predicts long-run progress better than a single score |

```python
@dataclass(frozen=True)
class MechanismMetrics:
    mechanism: str
    segment: dict
    verifier_version: str
    experiments: int
    verified_yield_pct_per_usd: str
    acceptance_rate_pct: str
    false_acceptance_rate_pct: str
    regression_rate_pct: str
    cost_per_accepted_usd: str
    descendant_productivity: str
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "mechanism": "sha256:3f12…",
  "segment": {"engine": "openclaw", "bot_type": "support"},
  "verifier_version": "verifier@4",
  "experiments": 412,
  "verified_yield_pct_per_usd": "0.31",
  "acceptance_rate_pct": "18.2",
  "false_acceptance_rate_pct": "5.9",
  "regression_rate_pct": "2.9",
  "cost_per_accepted_usd": "6.40",
  "descendant_productivity": "0.44"
}
```

Negative results are first-class inputs to these metrics: rejected
candidates, regressions, going back, and wasted budget are all recorded in H.
That is what lets a meta-strategy learn "operator X keeps failing on bots of
type Y", the way ClawEvolve's operator library would want to.

## 3. What the mechanism is

A mechanism M is a **strategy version** ([03-strategy.md](03-strategy.md))
together with everything that determines its behaviour:

| Mechanism component | Example in ClawEvolve today |
| --- | --- |
| Round structure (loop limits, stop rules) | `bot_evolution` stage sequence, `maxRounds` |
| Proposal prompts and operator library | `_build_tune_prompt`, `references/mutation_operator_library.json` |
| Analysis heuristics | diagnose batch sizes, good/bad ratio, root-cause clustering |
| Submission filter (what is worth submitting) | `test > baseline`, `FULL_OPT_MAX_REGRESSED_RATIO`, paired win-rate thresholds |
| Default binding params | parent choice, window, budget per run |
| Model choices and budgets per step | tune/review/judge models, round budget |

ClawEvolve already improves its mechanism by hand at level 3:
`scripts/calibrate_evolution_gates.py` and `scripts/replay_candidate_gate.py`
replay historical candidates to tune acceptance thresholds, and the tune
prompt carries evolution history. The platform makes that loop explicit,
verified, and pluggable. How ClawEvolve itself becomes a strategy is in
[04-default-strategies.md](04-default-strategies.md).

**Mechanism genome.** One registry, refs, archive, and promotion model serve
both artifact kinds. The loop is generic over what it improves:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"target_kind": "bot_genome"}   // or "mechanism"
```

Agent definitions (prompts, skills, tool configuration) are already shipped
with a strategy version, stored content-addressed at registration, and
recorded by digest with each run ([03-strategy.md](03-strategy.md)). So a
changed tune prompt is already a new strategy version with a new digest, and
results in H are already attributable to the exact prompts used. Level 3 adds
lineage, refs, and patches on top of that.

**What a mechanism patch can change (proposed).** The source names five
kinds of change: edit the flow, replace a plugin version, edit a prompt,
change a parameter, add an operator. They map onto the patch ops of §2.4:

| Op | Changes | Example |
| --- | --- | --- |
| `param.set` | A default param or threshold of the mechanism | `submission_filter.min_train_gain_pct: 2` |
| `prompt.edit` | A file inside an agent definition (same edit kinds as genome `file.edit`) | Rewrite the tune prompt section on persona edits |
| `operator.add` | An entry in the strategy's operator library | Add a "split oversized skill" operator |
| `flow.edit` | Step order or stop rules, where the strategy exposes them as data | Run review before train evaluation |
| `plugin.replace` | A pinned step or plugin version (for a composed strategy, a step version) | `team-x/root-cause-analyzer@1` → `@2` |

For a black-box strategy, only what the strategy exposes as data (params,
agent definitions, operator files, flow description) is patchable by a
meta-strategy. Changes to the strategy's own code are human-authored new
versions, which still go through mechanism verification (§8). This boundary
is open decision O-1.

**Mechanism refs.** Refs are scoped per strategy family.
`clawevolve/bot-evolution` has `active`, `previous`, `canary` and
`candidate/*` refs, which bots and tenants follow or pin through their
evolution policy:

| Ref | Meaning | Who moves it |
| --- | --- | --- |
| `active` | The mechanism new level-2 runs of following bindings use | Mechanism promotion only |
| `previous` | The last `active`, kept for quick going back | Mechanism promotion only |
| `canary` | The mechanism used for a share of level-2 runs during a mechanism canary | Mechanism promotion only |
| `candidate/<run>/<n>` | Candidate mechanisms of a meta-run | Orchestrator |

Going back is the same as for genomes: promoting an earlier mechanism
revision again. There is no separate rollback path.

## 4. What level 2 must already provide

Level 3 is out of the first iteration, but it only works if the level-2
contracts already carry the right fields. These are the hooks; none of them
adds first-iteration scope beyond what level 2 needs for itself.

| Level-2 contract | Requirement for level 3 | Where |
| --- | --- | --- |
| Run freezes its strategy version | Each run records the exact mechanism revision (version plus agent definition digests); runs in flight finish on the mechanism they started with | [06-evolution-run.md](06-evolution-run.md) |
| Strategy versions are immutable and content-addressed | Agent definitions stored by digest at registration; a changed prompt is a new version | [03-strategy.md](03-strategy.md) |
| Experiment Ledger H records every experiment from day one, including rejected candidates and later online outcomes | The mechanism revision, evidence, patch, verdict, cost, adoption, online outcome, and verifier version on every entry | [05-experiment-ledger.md](05-experiment-ledger.md) |
| Every verdict records the verifier version | So mechanism metrics can be compared within one verifier version only | [07-verification.md](07-verification.md) |
| Experience can be addressed as a frozen snapshot | So an improvement problem replays the same episodes for every mechanism compared (proposed requirement) | [02-experience.md](02-experience.md) |
| Revision/ref/patch machinery is generic over `target_kind` | So mechanisms reuse it instead of a second implementation | [01-genome.md](01-genome.md) |
| Bindings name a strategy family and version | So a binding can later follow a family's `active` mechanism ref instead of a pinned version (proposed binding form, open decision O-2) | [06-evolution-run.md](06-evolution-run.md) |
| Separation of powers names level 3 explicitly | Changing the verifier is held by humans only; adopting a mechanism is held by the mechanism gate + human approval | [08-promotion.md](08-promotion.md) |

## 5. The level-3 loop

Same skeleton as level 2. The target is a mechanism, and the evaluator is
mechanism verification.

1. **Trigger**: schedule, enough new experiments in H, a drop in a mechanism
   metric (for example a falling acceptance rate), or manual.
2. **Select parent mechanism**: usually the `active` M1 of a strategy family.
3. **Analyze H**: find patterns in failed or wasted experiments (operators
   with low yield, thresholds that let regressions through, steps that burn
   budget without effect).
4. **Meta-propose**: a **meta-strategy** (itself a strategy, for example a
   coding agent given a filesystem export of H, the Meta-Harness pattern)
   emits a **mechanism patch**: tune a threshold, rewrite the tune prompt
   section on persona edits, add an operator, swap the evaluator mix, reorder
   steps.
5. **Static checks**: the patch must not touch the **verifier boundary**
   (§10), must stay within declared capabilities, and must pass conformance
   (§7).
6. **Mechanism verification**: §8.
7. **Adopt or reject**: M2 becomes the strategy family's `active` mechanism
   only through the gate (human approval by default, §10).
8. **Hand over**: subsequent level-2 runs of bots that follow the family's
   `active` ref use M2. Pinned bots keep M1. Runs already in flight finish on
   the mechanism they started with, because runs freeze their mechanism
   version.

```text
H ──► meta-run (meta-strategy) ──► mechanism patch ──► static checks ──► mechanism verification
                                                                                 │
                       pinned bindings keep M1                                   ▼
level-2 runs ◄── hand over to M2 ◄── promote family `active` ◄── human approval (T3) ◄── verdict accept
     │
     └──► new experiments recorded in H
```

### 5.1 Meta-strategies

A meta-strategy is an ordinary strategy on the same port (`run(ctx)`,
[03-strategy.md](03-strategy.md)) whose target is a mechanism. It differs
from a level-2 strategy in three ways (proposed):

- Its run targets a **strategy family**, not a bot: the meta-run's parent is
  a mechanism revision, and `ctx.parent` is a view of that revision.
- It reads H through a **level-3 capability**, proposed name `ledger.read@1`
  (listed in the capability catalog of [03-strategy.md](03-strategy.md) as
  later, level 3; not granted in the first iteration),
  which gives a filesystem export of the ledger restricted to visible data
  (Meta-Harness found raw history beats summaries). It never includes hidden
  suite cases.
- It submits **mechanism patches** through `ctx.candidates.submit`. The
  candidate's verdict comes from mechanism verification, not bot
  verification.

A meta-strategy may target other strategy families but never itself (§10).

### 5.2 Meta-runs

A meta-run is a `Run` with the same lifecycle, leases, budgets, idempotency,
and crash recovery as a level-2 run
([06-evolution-run.md](06-evolution-run.md)). Level 3 runs on a budget of its
own, separate from any bot's budget, because every mechanism comparison runs
many level-2 runs (§8.3). Kill switches apply unchanged: disabling a
meta-strategy disables it everywhere, and the global switch pauses meta-runs
with everything else.

## 6. Mechanism candidates and adoption

A mechanism candidate becomes `active` only through a dedicated **mechanism
gate profile** ([08-promotion.md](08-promotion.md) holds the gate; this is
its level-3 profile):

1. **Static floor**: schema valid; `base` equals the parent; the patch does
   not touch the verifier boundary (§10); the resulting registration still
   declares only catalog capabilities; agent definitions still validate for
   every declared engine.
2. **Conformance**: the candidate mechanism passes the strategy conformance
   kit ([03-strategy.md](03-strategy.md)) like any new strategy version.
3. **Mechanism verification verdict** `accept` under the family's mechanism
   verification profile (§8).
4. **Risk tier T3 by default**: always human-approved, with the comparison
   report attached. An owner may lower this to T2 for parameter-only patches
   (thresholds, budgets) on their own tenant (open decision O-3 on what "own
   tenant" means for a shared family).
5. **Optional mechanism canary** (§8.4), then promotion of the family's
   `active` ref.

Every step is an audited event in H ([05-experiment-ledger.md](05-experiment-ledger.md)):
the candidate, the verification, the review, and the promotion.

## 7. Static checks on mechanism patches

Run on submission, before any money is spent on verification:

| Check | Rejects |
| --- | --- |
| Verifier boundary | Any op whose target is a suite, split, grader, verification profile, mechanism verification profile, gate floor, significance threshold, or the meta-verification protocol |
| Policy and safety envelope | Any op touching budgets, kill switches, the risk-tier table, locked genes, or bot genome `policy` sections |
| Self-reference | A patch from a meta-run whose family equals the meta-strategy's own family |
| Capabilities | A resulting registration whose `needs` adds capabilities, or names a capability outside the catalog |
| Hidden-data references | Prompt edits that reference hidden suite ids or case content (the same diff audit as for guardrail-like text in bot patches) |
| Schema and base | Invalid ops, or `base` not equal to the parent (compare-and-swap) |

## 8. Mechanism verification

The question is not "is S′ better than S1" but "**does M2 produce better
verified improvements than M1, on problems neither was tuned on?**"

### 8.1 In verification terms

Mechanism verification is the same verifier machinery as bot verification
([07-verification.md](07-verification.md)), one level up:

- The **subject** is a mechanism revision. A **case** is an *improvement
  problem* (frozen bot genome + experience snapshot + suites + budget, §2.5).
- An **execution** is a full level-2 run in sandbox, whose output is itself
  verified by bot verification.
- The **score** is the verified improvement yield on the problem's hidden
  suites, with cost, regression rate, and false-acceptance rate.
- The **comparator** is the same paired-statistics machinery, at problem
  granularity.

### 8.2 Protocol

1. Split improvement problems into **mechanism-train** (the meta-strategy may
   see outcomes) and **mechanism-holdout** (used only here), stratified by
   engine and bot type. Holdout problems should come from bots the
   meta-strategy never saw.
2. Run M1 and M′ on the same holdout problems with the **same budget** and
   multiple seeds. Each run is a full level-2 run in sandbox eval bots, so its
   output is verified by bot verification exactly as in production. No real
   bot's refs move: the level-2 runs work on sandbox copies and their accepted
   candidates are only scored, never promoted.
3. Score each outcome on the problem's hidden suites. Compare the mechanism
   metrics of §2.8 with paired statistics (per-problem paired differences and
   confidence intervals, not raw averages).
4. **Adopt M′ only if** yield improves significantly, regression and
   false-acceptance rates do not rise, and cost stays within tolerance.
   Optionally require M′ to beat a **budget-matched baseline**, that is, M1
   given M′'s extra spend.
5. **Canary at the mechanism level** (optional): after offline adoption, run
   M2 for a fraction of real level-2 runs alongside M1 and compare live yield
   before full handover.

Verdict policy (proposed; thresholds come from the profile):

```text
accept       ⇔ static floor ok ∧ conformance ok
               ∧ CI_lower(Δ yield) ≥ min_yield_gain
               ∧ Δ regression rate ≤ max_regression_rate_increase
               ∧ Δ false-acceptance rate ≤ max_false_acceptance_increase
               ∧ Δ cost within cost_tolerance
               ∧ (beats budget-matched baseline, when required)
reject       ⇔ any floor failure ∨ CI_upper(Δ yield) < min_yield_gain ∨ regression or false-acceptance rate rises
inconclusive ⇔ otherwise  → more problems or seeds, or stop
```

### 8.3 Cost and proxies

This is expensive, since every comparison runs many level-2 runs. Level 3
therefore runs rarely and on a budget of its own.

Cheap proxies are allowed for *pre-screening* candidates. One example is
replaying stored candidates through a new submission filter or verification
profile and measuring precision/recall against labelled outcomes, as
`calibrate_evolution_gates.py` and `replay_candidate_gate.py` already do;
they become the screening tool (`ReplayScreening`). **Proxies are never
allowed for adoption.** Screening is not adoption.

Note the difference between the two kinds of change a replay can screen:

- A change to a strategy's **submission filter** is a mechanism change. It
  is screened by replay and adopted only through §8.2.
- A change to a **verification profile** is a verifier change. Replay
  screening informs the humans who author it; it is adopted only by reviewed
  human change (§10), never by the meta-loop.

### 8.4 Mechanism canary

Optional, per profile (`canary_share_pct`). After offline acceptance and
approval, the family's `canary` ref points at M2, and that share of real
level-2 runs of following bindings start on `canary` instead of `active`.
Live yield is compared from H before `active` moves. Bots that pin a version
are never part of a canary.

## 9. Improvement problems

- **Built from H.** A problem is frozen from a real past experiment: its
  starting genome revision, the experience the mechanism could see (as a
  snapshot), the suites, and a budget. Freezing copies nothing that is
  already content-addressed; it records ids.
- **Synthetic problems** can be added: deliberately degraded genomes with a
  known fix.
- **Split and stratified** into `mechanism_train` and `mechanism_holdout` by
  engine and bot type. Holdout problems come from bots the meta-strategy never
  saw.
- **Tagged with the verifier version.** When a suite changes, problems
  frozen under the old verifier version are not mixed with new ones.
- **A regression test for strategies.** Even without any automated
  meta-strategy, the benchmark is valuable: a human-authored change to a
  strategy (for example a new ClawEvolve tune prompt) can be accepted or
  rejected by comparing verified improvement yield against the current
  mechanism on held-out problems.

## 10. Bounds: what recursion may not touch

Recursion needs a fixed anchor. Without one, the system can "improve" by
weakening its own judge, which is the Darwin Gödel Machine failure (it
disabled its own hallucination checker).

| Never changed by any automated loop | Why |
| --- | --- |
| The **verifier**: suites, splits, graders, platform gate floor, meta-verification protocol, significance thresholds | It defines "better". If the loop can edit it, gains become meaningless |
| Locked genes and `policy` sections of bot genomes | Owner authority |
| Budgets, kill switches, risk-tier table | Safety envelope |
| The meta-strategy's ability to modify itself or its own verifier | Prevents unbounded self-reference |

Rules:

- **Depth is bounded at 2.** M improves S; a meta-strategy improves M. A
  meta-strategy may target other strategy families but never itself. Its own
  changes are human-authored, or go through the same mechanism-verification
  protocol run by a *different* meta-strategy under human approval.
- **Mechanism adoption is risk tier T3 by default**: always human-approved,
  with the comparison report attached. An owner may lower this to T2 for
  parameter-only patches (thresholds, budgets) on their own tenant.
- **The verifier evolves only through human-authored, reviewed changes.** It
  may be *informed* by H (for example "regression suite misses failure class
  Z"), but such a finding is a recommendation to a human, never an automatic
  edit.
- **Verifier changes invalidate comparability.** When a suite changes,
  experiments in H are tagged with the verifier version, and mechanism
  metrics are only compared within a verifier version.
- **Hidden data stays hidden one level up.** Meta-strategy inputs never
  include holdout, regression, or safety cases, nor mechanism-holdout
  problem outcomes. The orchestrator enforces this; prompts do not.

These bounds are enforced structurally: by the static checks of §7, by the
verifier being outside every artifact a loop can patch, and by the separation
of powers in [08-promotion.md](08-promotion.md) (changing the verifier is held
only by humans; adopting a mechanism only by the mechanism gate plus human
approval).

## 11. Phasing

Level 3 depends on a populated H and a trustworthy level-2 verifier. Building
it before those exist would optimise noise.

1. **Record H from day one** (first iteration, with the level-2 work). It is
   cheap and is the prerequisite for everything below.
2. **Offline replay** (P4): port `calibrate_evolution_gates.py` /
   `replay_candidate_gate.py` into a replay tool over H for changes to
   verification profiles and submission filters. Humans adopt.
3. **Improvement-problem benchmark** (P5): freeze problems from H and run the
   mechanism-verification protocol for human-authored mechanism changes. This
   alone is valuable: it is a regression test for strategies (work item
   RSI-23 in [work-items.md](work-items.md)).
4. **Automated meta-strategy** (P6): let a meta-strategy propose mechanism
   patches, adopted only through §8 and human approval, with the bounds of
   §10 enforced by static checks (RSI-24).

## 12. Service interface

All interfaces below are proposed and level 3 only.

```python
from typing import Protocol

class MechanismRegistry(Protocol):
    """Mechanism revisions and refs per strategy family. Implemented by the Strategy
    Registry (03-strategy.md) on the shared revision/ref/patch machinery."""

    async def get_revision(self, family: str, revision: str) -> MechanismRevision: ...
    async def list_revisions(self, family: str, *, status: MechanismStatus | None = None) -> list[MechanismRevision]: ...
    async def refs(self, family: str) -> dict[str, str]:
        """Ref name → mechanism revision id."""
    async def record_candidate(self, patch: MechanismPatch, *, run_id: str) -> str:
        """Static checks (§7), then record a candidate revision. Idempotent:
        returns the patch's content hash as candidate id."""
    async def promote(self, family: str, revision: str, *, expected_active: str,
                      reason: str, actor: str) -> Promotion:
        """Move `active` (and `previous`) by compare-and-swap. Only the mechanism
        gate calls this; going back is promoting an earlier revision."""
    def resolve(self, family: str, ref_or_version: str) -> MechanismRevision:
        """Resolve a binding's `@active` / `@canary` / `@2.0.0` at run start;
        the run freezes the result."""


class ImprovementProblems(Protocol):
    """Frozen improvement problems built from H."""

    async def freeze(self, *, source_entry: str, split: ProblemSplit, budget: dict,
                     idempotency_key: str) -> ImprovementProblem: ...
    async def get(self, problem: str) -> ImprovementProblem: ...
    async def list(self, *, split: ProblemSplit | None = None, segment: dict | None = None,
                   verifier_version: str | None = None) -> list[ImprovementProblem]: ...


class MechanismVerifier(Protocol):
    """Mechanism verification (§8). Verifier-owned, read-only to every loop."""

    async def start_verification(self, *, family: str, candidate: str, baseline: str,
                                 profile: str, idempotency_key: str) -> str:
        """Start M′ vs M1 on mechanism-holdout problems. Returns an operation id at once."""
    async def get_verification(self, verification: str) -> MechanismVerification: ...
    async def start_replay(self, *, family: str, candidate: str, idempotency_key: str) -> str:
        """Replay screening (§8.3). Returns an operation id. Never adoption evidence."""
    async def get_replay(self, replay: str) -> dict: ...


class MechanismMetricsReader(Protocol):
    """Derived from the Experiment Ledger (05-experiment-ledger.md)."""

    async def metrics(self, family: str, *, mechanism: str | None = None,
                      segment: dict | None = None,
                      verifier_version: str) -> list[MechanismMetrics]:
        """verifier_version is required: metrics are never mixed across verifier versions."""


class MechanismGate(Protocol):
    """Level-3 gate profile (08-promotion.md holds the gate)."""

    async def decide(self, family: str, candidate: str) -> GateDecision: ...
    async def approve(self, family: str, candidate: str, *, reason: str, actor: str) -> Promotion: ...
    async def reject(self, family: str, candidate: str, *, reason: str, actor: str) -> GateDecision: ...


class LedgerRead(Protocol):
    """Proposed level-3 capability `ledger.read@1` on StrategyContext, granted only to
    meta-strategies that declare it."""

    async def export(self, *, family: str, since: str | None = None) -> "Workspace":
        """Materialise a filesystem export of visible ledger data into a sandbox
        workspace. Never contains hidden cases or mechanism-holdout outcomes."""
```

## 13. API

All endpoints are **proposed, level 3, not in the first iteration**. They
follow the shared conventions of [09-evolution-api.md](09-evolution-api.md):
prefix `/openapi/v1` (paths below are relative to it), JSON bodies,
`Idempotency-Key` header on every POST that creates something, ETags on
mutable resources, long-running work as operations (`202` + operation id,
status by id, no request held open). Responses below show the `data`
payload of the standard envelope; see
[09-evolution-api.md](09-evolution-api.md) for the envelope, errors,
pagination, and idempotency.

`{id}` is a strategy family id such as `clawevolve/bot-evolution`,
percent-encoded in the path segment (`clawevolve%2Fbot-evolution`), as for
the Strategy Registry endpoints in [03-strategy.md](03-strategy.md).

Callers: human operators and researchers (UI, `avn`), and pipelines/CI that
run the improvement-problem benchmark on a strategy change. Bot callers are
postponed with DR-3 and are not callers of this surface.

### GET /evolution/strategies/{id}/mechanism/revisions

Lists mechanism revisions of a family with lineage and status. Called by the
UI and `avn`.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/mechanism/revisions?status=candidate
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 1,
  "items": [
    {"id": "sha256:5b0e…", "version": "2.0.1", "parents": ["sha256:3f12…"],
     "status": "candidate", "created_by": {"kind": "meta_run", "run_id": "run_m12"}}
  ]
}
```

Errors: `404` unknown family.

### GET /evolution/strategies/{id}/mechanism/revisions/{rev}

Returns one mechanism revision (§2.3). `{rev}` is a revision id or a version.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/mechanism/revisions/2.0.1
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "sha256:5b0e…", "family": "clawevolve/bot-evolution", "version": "2.0.1",
  "target_kind": "bot_genome",
  "agent_definitions": {"clawevolve-tune": "sha256:91aa…", "clawevolve-review": "sha256:0c7d…"},
  "default_params": {"max_rounds": 3, "submission_filter": {"min_train_gain_pct": 2, "max_regressed_ratio_pct": 10}},
  "parents": ["sha256:3f12…"], "patch_from_parent": "sha256:d27f…",
  "status": "candidate", "verifier_version": null
}
```

Errors: `404` unknown family or revision.

### GET /evolution/strategies/{id}/mechanism/refs

Returns the family's mechanism refs. Response carries an `ETag`.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/mechanism/refs
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "active": "sha256:3f12…",
  "previous": "sha256:2e80…",
  "canary": null,
  "candidate/run_m12/1": "sha256:5b0e…"
}
```

### POST /evolution/strategies/{id}/mechanism/promotions

Moves the family's `active` ref to a mechanism revision; going back is
promoting an earlier revision. Called by the mechanism gate after approval,
or by an operator to go back. Compare-and-swap on `expected_active`.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/mechanism/promotions
// Idempotency-Key: promote-clawevolve-2.0.0-after-incident-1182
{
  "revision": "sha256:3f12…",          // going back to 2.0.0
  "expected_active": "sha256:5b0e…",
  "reason": "False-acceptance rate rose on the mechanism canary"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "promotion_id": "mp_07",
  "family": "clawevolve/bot-evolution",
  "active": "sha256:3f12…",
  "previous": "sha256:5b0e…",
  "actor": "user:owner_42",
  "at": "2026-10-09T08:00:00Z"
}
```

Errors: `409` `expected_active` is stale; `403` caller may not promote
mechanisms; `422` the revision has no accepted mechanism verification (forward
promotion only; going back to a previously promoted revision does not need a
new verification).

### GET /evolution/mechanism-candidates/{candidate}

The adoption report for a mechanism candidate: the patch, the static checks,
the mechanism verification comparison, and the gate decision. Called by
reviewers (UI, `avn evolve mechanism review show`).

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/mechanism-candidates/sha256:d27f…
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate": "sha256:d27f…",
  "family": "clawevolve/bot-evolution",
  "revision": "sha256:5b0e…",
  "base": "sha256:3f12…",
  "patch_ops": 2,
  "static_checks": {"verifier_boundary": "ok", "capabilities": "ok", "self_reference": "ok", "conformance": "ok"},
  "verification": {"id": "mv_31", "verdict": "accept", "yield_gain_ci_lower_pct": "1.4"},
  "gate": {"decision": "needs_review", "risk_tier": "T3", "reasons": ["mechanism adoption is T3 by default"]}
}
```

Errors: `404` unknown candidate.

### GET /evolution/mechanism-review-queue

Mechanism candidates waiting for human approval.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/mechanism-review-queue?family=clawevolve%2Fbot-evolution
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 1,
  "items": [
    {"candidate": "sha256:d27f…", "family": "clawevolve/bot-evolution", "risk_tier": "T3",
     "verification": "mv_31", "verdict": "accept", "queued_at": "2026-10-09T06:40:00Z"}
  ]
}
```

### POST /evolution/mechanism-candidates/{candidate}:approve

Human approval. If the profile has no mechanism canary, approval promotes the
family's `active` ref; otherwise it moves `canary` first.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/mechanism-candidates/sha256:d27f…:approve
// Idempotency-Key: approve-sha256-d27f
{"reason": "Yield gain holds on all four segments; cost within tolerance"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"candidate": "sha256:d27f…", "status": "promoted", "promotion_id": "mp_06", "active": "sha256:5b0e…"}
```

Errors: `409` verdict is not `accept`, or `active` moved since the
verification started (re-verify against the new `active`); `403` not a
mechanism approver.

### POST /evolution/mechanism-candidates/{candidate}:reject

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/mechanism-candidates/sha256:d27f…:reject
// Idempotency-Key: reject-sha256-d27f
{"reason": "Gain concentrated in one segment; resubmit with stratified evidence"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"candidate": "sha256:d27f…", "status": "rejected"}
```

### POST /evolution/strategies/{id}/meta-runs

Starts a meta-run: a meta-strategy against this family. Idempotent; returns
`202` with the run id. A meta-run is a `Run` of Evolution Run
([06-evolution-run.md](06-evolution-run.md)); status is looked up by id with
the endpoint below.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/meta-runs
// Idempotency-Key: meta-clawevolve-2026-10-09
{
  "meta_strategy": "platform/meta-evolve@0.1.0",
  "parent": "active",
  "budget": {"max_usd": 200, "max_wall_clock_s": 86400},
  "params": {"ledger_window_days": 90}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_m12", "status": "queued"}
```

Errors: `422` the meta-strategy's own family equals `{id}` (self-reference,
§10); `403` caller may not start meta-runs; `409` an idempotency key reused
with a different body.

### GET /evolution/strategies/{id}/meta-runs/{run}

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/meta-runs/run_m12
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "run_id": "run_m12",
  "status": "completed",
  "meta_strategy": "platform/meta-evolve@0.1.0",
  "parent": "sha256:3f12…",
  "budget": {"max_usd": 200, "spent_usd": "143.20"},
  "candidates": ["sha256:d27f…"]
}
```

### GET /evolution/strategies/{id}/mechanism-metrics

Mechanism metrics per revision and segment, for one verifier version.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/strategies/clawevolve%2Fbot-evolution/mechanism-metrics?verifier_version=verifier@4&engine=openclaw
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "verifier_version": "verifier@4",
  "items": [
    {"mechanism": "sha256:3f12…", "segment": {"engine": "openclaw", "bot_type": "support"},
     "experiments": 412, "verified_yield_pct_per_usd": "0.31", "acceptance_rate_pct": "18.2",
     "false_acceptance_rate_pct": "5.9", "regression_rate_pct": "2.9",
     "cost_per_accepted_usd": "6.40", "descendant_productivity": "0.44"}
  ]
}
```

Errors: `400` `verifier_version` missing (metrics are never mixed across
verifier versions).

### POST /evolution/improvement-problems

Freezes an improvement problem from a ledger entry. Operator-only.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/improvement-problems
// Idempotency-Key: freeze-le_5512
{
  "source_entry": "ledger:le_5512",
  "split": "mechanism_holdout",
  "budget": {"max_usd": 20, "max_rollouts": 400}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"id": "prob_204", "split": "mechanism_holdout", "verifier_version": "verifier@4"}
```

Errors: `422` the entry's experience is no longer retained (cannot be
snapshotted), or its verifier version is superseded.

### GET /evolution/improvement-problems

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/improvement-problems?split=mechanism_holdout&engine=openclaw&verifier_version=verifier@4&page=1&page_size=2
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 41,
  "items": [
    {"id": "prob_204", "segment": {"engine": "openclaw", "bot_type": "support"}, "source": "ledger"},
    {"id": "prob_219", "segment": {"engine": "openclaw", "bot_type": "sales"}, "source": "synthetic"}
  ]
}
```

Mechanism-holdout problems are listed by id and segment only for meta-strategy
callers; their outcomes are never exposed to meta-strategies.

### GET /evolution/improvement-problems/{problem}

Returns one problem (§2.5). Errors: `404`.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/improvement-problems/prob_204
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "prob_204", "bot_genome_revision": "sha256:a90b…", "experience_snapshot": "snapshot:exp_77",
  "visible_suites": ["train", "validation"], "hidden_suites": ["holdout", "regression", "safety"],
  "budget": {"max_usd": 20, "max_rollouts": 400},
  "segment": {"engine": "openclaw", "bot_type": "support"},
  "source": "ledger", "source_entry": "ledger:le_5512",
  "split": "mechanism_holdout", "verifier_version": "verifier@4"
}
```

### POST /evolution/mechanism-verifications

Starts mechanism verification of a candidate against a baseline. Returns `202`
with the operation id. Called by the mechanism gate automatically for meta-run
candidates, and by operators or CI for human-authored strategy versions.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/mechanism-verifications
// Idempotency-Key: mv-clawevolve-2.0.1-vs-2.0.0
{
  "family": "clawevolve/bot-evolution",
  "candidate": "sha256:5b0e…",
  "baseline": "active",
  "profile": "mechanism-default@1"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"verification_id": "mv_31", "operation_id": "op_19a", "status": "queued"}
```

Errors: `422` fewer mechanism-holdout problems than `min_holdout_problems`
for the family's segments; `409` idempotency key reused with a different body.

### GET /evolution/mechanism-verifications/{verification}

Status and, when finished, the comparison (§2.7).

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/mechanism-verifications/mv_31
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "mv_31", "operation": {"id": "op_19a", "status": "succeeded"},
  "candidate": "sha256:5b0e…", "baseline": "sha256:3f12…",
  "profile": "mechanism-default@1", "verifier_version": "verifier@4",
  "verdict": "accept",
  "comparison": {"problems": 34, "seeds": 3,
                 "yield_gain_pct": {"mean": "3.1", "ci_lower": "1.4", "ci_upper": "4.8"}}
}
```

### POST /evolution/replays

Starts replay screening of a candidate submission filter (or, for humans
authoring a verifier change, a candidate verification profile) over stored
candidates in H. Returns `202` with an operation id. Never adoption evidence.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /openapi/v1/evolution/replays
// Idempotency-Key: replay-filter-min-gain-2
{
  "family": "clawevolve/bot-evolution",
  "candidate": "sha256:5b0e…",
  "corpus": {"since": "2026-07-01T00:00:00Z", "labelled_only": true}
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"replay_id": "rp_08", "operation_id": "op_1b2", "status": "queued"}
```

### GET /evolution/replays/{replay}

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// GET /openapi/v1/evolution/replays/rp_08
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "rp_08", "operation": {"id": "op_1b2", "status": "succeeded"},
  "replayed_candidates": 1840,
  "baseline": {"precision_pct": "71.0", "recall_pct": "64.5"},
  "candidate": {"precision_pct": "78.3", "recall_pct": "61.0"},
  "note": "Screening only; adoption requires mechanism verification"
}
```

### Internal: POST /evolution/v1/runs/{run}/ledger:export

Internal API, not under `/openapi/v1`. Meta-strategy workers use the Job Protocol of
[06-evolution-run.md](06-evolution-run.md) unchanged (claim, heartbeat,
workspaces, agents, models, operations, candidates, budget). One endpoint is
added for the proposed `ledger.read@1` capability:

```text
POST /evolution/v1/runs/{run}/ledger:export    ctx.ledger.export → {workspace_id}   (if granted; idempotent per key)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// POST /evolution/v1/runs/run_m12/ledger:export
{"family": "clawevolve/bot-evolution", "since": "2026-07-01T00:00:00Z", "key": "run_m12/ledger"}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"workspace_id": "ws_4410", "entries": 412, "redacted": ["hidden_suite_cases", "mechanism_holdout_outcomes"]}
```

`POST /evolution/v1/runs/{run}/candidates` accepts a `MechanismPatch` body for
meta-runs and returns `{candidate_id}` (the patch content hash); `GET
/evolution/v1/runs/{run}/candidates/{id}` returns the mechanism verdict with
aggregates only. Not granted: `403`; stale fencing token: `409`.

### Internal: verify_mechanism(candidate, baseline, profile)

Internal service call. The mechanism gate makes it on each meta-run candidate (same
contract as `POST /evolution/mechanism-verifications`). Each improvement-problem
execution it schedules is a sandboxed level-2 run submitted to Evolution Run
with idempotency key `<verification_id>/<problem>/<mechanism>/<seed>`, so a
crash and re-dispatch never pays for the same execution twice.

## 14. Examples

### 14.1 A human-authored strategy change through the benchmark (the first use)

The first useful level-3 step needs no meta-strategy: a ClawEvolve maintainer
changes the tune prompt, registers `2.0.1`, and lets mechanism verification
decide.

```python
from avernet_evolution import Client

c = Client.from_env()
family = "clawevolve/bot-evolution"

# 2.0.1 was registered with `avn strategy publish` (03-strategy.md); new tune-prompt digest.
v = c.mechanism_verifications.start(
    family=family, candidate="2.0.1", baseline="active",
    profile="mechanism-default@1",
    idempotency_key="mv-clawevolve-2.0.1-vs-2.0.0",   # same key on every retry
)
result = c.mechanism_verifications.wait(v.verification_id)   # short lookups by id
print(result.verdict, result.comparison["yield_gain_pct"])

if result.verdict == "accept":
    # T3: a human approves from the report; approval promotes `active` (or `canary` first).
    report = c.mechanism_candidates.get(result.candidate)   # human-registered: candidate id = revision id
    c.mechanism_candidates.approve(report.candidate, reason="Benchmark accept; reviewed diff")
```

### 14.2 A meta-strategy (sketch)

```python
class MetaEvolve(EvolutionStrategy):
    """Coding-agent meta-strategy (Meta-Harness pattern). Registered with
    needs: {"ledger.read@1": {}, "agents@1": {"definitions": {"meta-coder": {...}}}}."""

    async def run(self, ctx):
        state = await self.store.load(ctx.run_id)                     # its own storage
        if state is None:
            history = await ctx.ledger.export(family=ctx.params["family"],
                                              since=ctx.params["since"])   # filesystem export of H
            ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/mech")
            op = await ctx.agents.start(
                "meta-coder", workspace=ws,
                prompt="Find operators and thresholds with low verified yield in ./ledger; "
                       "edit only params, agents/ and operators/.",
                idempotency_key=f"{ctx.run_id}/propose")
            await ctx.operations.wait(op)
            patch = ws.to_mechanism_patch()            # static checks run on submit (§7)
            state = State(candidate=await ctx.candidates.submit(patch))
            await self.store.save(ctx.run_id, state)
        verdict = await ctx.candidates.verdict(state.candidate)   # mechanism verification decides
        return RunSummary(candidates=[state.candidate], last_status=verdict.status)
```

### 14.3 Screening a submission-filter change cheaply

```python
rp = c.replays.start(family=family, candidate="2.0.2",
                     corpus={"since": "2026-07-01T00:00:00Z", "labelled_only": True},
                     idempotency_key="replay-filter-min-gain-2")
screen = c.replays.wait(rp.replay_id)
if screen.candidate["precision_pct"] > screen.baseline["precision_pct"]:
    # Worth paying for a real comparison; screening alone never adopts.
    c.mechanism_verifications.start(family=family, candidate="2.0.2", baseline="active",
                                    profile="mechanism-default@1",
                                    idempotency_key="mv-clawevolve-2.0.2-vs-active")
```

### 14.4 Following or pinning a family's mechanism (proposed binding form)

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// One binding in bot_123's evolution policy (06-evolution-run.md); open decision O-2
{
  "strategy": "clawevolve/bot-evolution@active",   // follow the family's active mechanism; "@2.0.0" pins
  "trigger": {"schedule": "0 2 * * *"},
  "parent": "active",
  "allowed_genes": ["persona", "skills"],
  "verification_profile": "default@1",
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "params": {"window_days": 7}
}
```

### 14.5 Going back

```bash
avn evolve mechanism promote clawevolve/bot-evolution --revision 2.0.0 \
  --reason "False-acceptance rate rose after 2.0.1" --yes
```

## 15. Interactions

| Other part | Direction | What flows |
| --- | --- | --- |
| Experiment Ledger H ([05-experiment-ledger.md](05-experiment-ledger.md)) | Ledger → meta-evolution | Experiments (incl. rejected, gone back from, online outcomes) for metrics, problem freezing, and the `ledger.read@1` export |
| Experiment Ledger H | Meta-evolution → ledger | Meta-run candidates, mechanism verifications, approvals, promotions (audited) |
| Strategy Registry ([03-strategy.md](03-strategy.md)) | Both | Mechanism revisions and refs stored there; registration and conformance for candidate mechanisms; `ledger.read@1` catalog entry |
| Default strategies ([04-default-strategies.md](04-default-strategies.md)) | Meta-evolution → strategies | ClawEvolve is the first family whose mechanism is improved; its calibration/replay scripts seed replay screening |
| Evolution Run ([06-evolution-run.md](06-evolution-run.md)) | Both | Meta-runs run as runs; each improvement-problem execution is a sandboxed level-2 run; bindings resolve `@active` to a frozen mechanism; budgets and kill switches |
| Verification ([07-verification.md](07-verification.md)) | Verification → meta-evolution | Bot verification of every problem execution; verifier version; paired-statistics comparator reused at problem granularity |
| Promotion ([08-promotion.md](08-promotion.md)) | Both | Mechanism gate profile, T3 default, review queue items, separation of powers |
| Experience ([02-experience.md](02-experience.md)) | Experience → meta-evolution | Frozen experience snapshots for improvement problems |
| Genome ([01-genome.md](01-genome.md)) | Genome → meta-evolution | Shared revision/ref/patch machinery; starting genomes of problems |
| Evolution API ([09-evolution-api.md](09-evolution-api.md)) | Meta-evolution → API | Endpoints of §13 under the shared conventions; `avn evolve mechanism …` commands |

## 16. Open decisions

| ID | Decision | Notes |
| --- | --- | --- |
| O-1 | What a meta-strategy may patch in a black-box strategy | Proposed: only what the strategy exposes as data (params, agent definitions, operator files, flow description); code changes stay human-authored new versions |
| O-2 | How a binding follows a family's mechanism ref | Proposed `strategy: "<family>@active"`; today a binding always pins an exact version. Must be settled when the binding contract is specified so it is not a breaking change later |
| O-3 | Per-tenant mechanism refs | The source lets an owner lower adoption to T2 "on their own tenant", which implies tenant-scoped refs for a shared family; not designed yet |
| O-4 | Cross-tenant evidence | Holdout problems "from bots the meta-strategy never saw" and learning across bots conflict with the data-handling default that forbids cross-tenant use of experience and learned artefacts ([02-experience.md](02-experience.md)); level 3 may need to run per tenant, or need explicit opt-in |
| O-5 | Version naming for automated mechanism revisions | Proposed: the id is the content hash; the human `version` is assigned at registration of the candidate (for example a patch-level bump) |
| O-6 | Who owns and sizes the level-3 budget | Separate from bot budgets; per family or per platform |
| O-7 | Name and shape of the H capability | Proposed `ledger.read@1` returning a filesystem export; must be added to the catalog through a reviewed platform change |
| O-8 | Mechanism canary allocation | How real level-2 runs are assigned to `canary` vs `active`, and whether owners can opt out |
