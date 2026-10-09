# Verification

> 中文版：[07-verification.zh-CN.md](07-verification.zh-CN.md)

> Status: DRAFT. Service in the [bot evolution architecture](design.md).
> How the platform measures whether a candidate genome revision is actually
> better than its parent: suites, splits, executors, graders, paired
> statistics, verdicts, and the rules that keep the verifier out of reach of
> what it judges.

## 1. Purpose and scope

Every improvement loop has a "verify and accept" step. Verification is the
only thing standing between "a strategy proposed a change" and "the bot
changed". This doc answers **how** a candidate is measured. **Who decides**
what happens with the result (the gate, risk tiers, approvals, promotion,
rollout) is [08-promotion.md](08-promotion.md).

| Level | Question | Compared | Where |
| --- | --- | --- | --- |
| 2 | Is candidate bot **S′** better than its parent **S1**, without breaking anything? | S′ vs S1 on the same cases | [§4](#4-bot-verification-protocol-level-2) |
| 2 (online) | Is promoted **S2** still better on real traffic? | S2 vs S1 live | [§6](#6-holdout-audits-and-online-verification) |
| 3 | Does candidate mechanism **M′** produce better *verified* improvements than **M1**? | M′ vs M1 on held-out improvement problems | [10-meta-evolution.md](10-meta-evolution.md) (later; reuses this service's comparator) |

Two properties hold at every level:

- **Rejection is a normal outcome.** Most candidates should fail. The
  platform reports a rejection as a result, not an error, and records it in
  the Experiment Ledger ([05-experiment-ledger.md](05-experiment-ledger.md))
  as evidence.
- **The verifier is not part of what evolves.** Suites, graders, profiles,
  protocols, and thresholds are versioned, human-owned assets
  ([§7](#7-governance-anti-reward-hacking-and-verifier-integrity)). No loop,
  including level 3, may change them.

**Owns:**

- The **Suite registry**: suites, their cases, and the platform-assigned
  split of every case.
- **Verification profiles**: versioned policies saying how strictly a
  candidate is checked.
- **Executor and grader plugins**: where a revision runs during evaluation
  and how a rollout is scored. They are owned by the verifier, never by
  strategies.
- The **comparator** (paired statistics) and the **verdict policy**.
- **Evaluations** and **verdicts**, including the train-split evaluation
  that backs the `evaluate.train@1` capability.
- Holdout audits and the measurement side of online verification (shadow
  grading, canary comparison, recurrence checks).
- The governance topics *anti-reward-hacking* and *verifier integrity*.

**Does not own:**

| Concern | Owner |
| --- | --- |
| Genome revisions, patches, content blobs, materialisation sources | [01-genome.md](01-genome.md) |
| Episodes and feedback (Verification writes eval traces there) | [02-experience.md](02-experience.md) |
| The strategy port, `StrategyContext`, the capability catalog entry `evaluate.train@1` as a contract | [03-strategy.md](03-strategy.md) |
| ClawEvolve/ClawBench inventory rows (ClawBench runner, ClawWeb Bench store, ClawEvolve splits and gates, diagnose → plan) and ClawEvolve's own submission filter | [04-default-strategies.md](04-default-strategies.md) |
| Ledger schema and storage of verdicts as experiment records | [05-experiment-ledger.md](05-experiment-ledger.md) |
| Runs, bindings, budgets, kill switches, sandboxing rules, Job Protocol transport | [06-evolution-run.md](06-evolution-run.md) |
| Static floor rules, gate decision, risk tiers, review queue, separation-of-powers table, rollout, going back | [08-promotion.md](08-promotion.md) |
| Public API conventions, SDKs, `avn` CLI | [09-evolution-api.md](09-evolution-api.md) |
| Mechanism verification (level 3), including offline replay of verification profiles and submission filters | [10-meta-evolution.md](10-meta-evolution.md) |

**Where it runs.** The Verification Service (C5) lives in the proposed new
module `apps/evolution`, next to Evolution Run. The deployed-sandbox
executor reuses Backend's eval environment (`eval_publish` plus the
`plugin_api/eval_env/` seams), and the optional publish-flow gate is a
Backend hook that calls this service ([§8](#8-placement-reuse-and-migration)).

## 2. Domain model

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `Suite` | A versioned set of cases for one bot or for the whole platform | Verifier (humans) and bot owner | New version on every reviewed change; old versions kept for comparability |
| `Case` | One replayable task: prompt or scripted multi-turn user, workspace files, grading spec | Suite | Immutable per content digest; retired, never edited in place |
| `Split` | The role a case plays: `train`, `validation`, `holdout`, `regression`, `safety` | Platform (assigned, never by a strategy) | Fixed per case per suite version; holdout rotated on a schedule |
| `Grader` | A plugin that scores one rollout: `automated`, `rubric_judge`, `hybrid`, `ensemble` | Verifier | Versioned plugin; a grader change is a verifier change |
| `Executor` | A plugin that runs a revision on a case: local sandbox or deployed sandbox | Verifier | Versioned plugin |
| `VerificationProfile` | Versioned policy: splits, seeds, executor, thresholds, significance, tolerances, cost rules | Verifier; chosen per binding by the owner | Immutable per version (`default@1`); stricter profiles added by review |
| `Evaluation` | One execution of one or two revisions on a set of cases, with every rollout and grade | Verification Service | `queued → running → succeeded | failed | cancelled`; kept for audit |
| `Verdict` | The comparison result for one candidate under one profile: `pending | accept | reject | inconclusive`, with per-split evidence; the one verdict type of the platform (full operator view, plus the strategy-visible `StrategyVerdictView`) | Verification Service | Created `pending` on `verify`, becomes final once; never changes after |

Supporting value types: `Rollout` (one execution of one case with one
seed), `Grade` (`score + critique + breakdown`), `SplitResult` (the
comparison of one split), `VerifierVersion` (the exact suites, graders,
profile, and protocol that produced a verdict), and `StrategyVerdictView`
(the redacted verdict a strategy may see).

### 2.1 Suite

A **suite** is a versioned set of cases. The ClawBench Markdown case format
is the v1 case format, so existing ClawBench cases load unchanged. A suite
has a scope: a platform suite (for example a safety suite applied to every
bot) or a bot suite (cases mined from that bot's own failures). Proposed:
the cases used for a candidate are the union of the platform suites and the
candidate's bot suites, each at its current version when the verdict is
created.

```python
@dataclass(frozen=True)
class Suite:
    suite_id: str                      # "support-core"
    version: int                       # increments on every reviewed change
    scope: SuiteScope                  # platform-wide, or one bot
    case_format: Literal["clawbench-md@1"]
    graders: list[GraderRef]           # default graders for cases that name none
    split_counts: dict[Split, int]     # counts are always visible; contents are not
    digest: str                        # content address of the whole suite version
    created_at: datetime
    change_reason: str                 # human-reviewed changes only

@dataclass(frozen=True)
class SuiteScope:
    kind: Literal["platform", "bot"]
    bot_id: str | None                 # set only when kind == "bot"
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "suite_id": "support-core",
  "version": 7,
  "scope": {"kind": "bot", "bot_id": "bot_123"},
  "case_format": "clawbench-md@1",
  "graders": [{"id": "platform/clawbench", "version": "1.0.0"}],
  "split_counts": {"train": 40, "validation": 30, "holdout": 20, "regression": 18, "safety": 12},
  "digest": "sha256:3b8d…",
  "created_at": "2026-10-06T10:00:00Z",
  "change_reason": "Add 4 regression cases from confirmed partial-refund failures"
}
```

### 2.2 Case and Split

A **case** is one replayable task. It carries the prompt (or a scripted
multi-turn simulated user, ClawBench's `interactions`), workspace files,
expected behaviour, and a grading spec. Cases are content-addressed: editing
one produces a new case, so a verdict can always name exactly what it ran.

A **split** is the role a case plays. The platform assigns it, never a
strategy, using a session-grouped, leakage-safe assignment (cases mined from
the same session land in the same split).

| Split | Purpose | What a strategy sees |
| --- | --- | --- |
| `train` | Feedback for the proposer | Per-case results with critiques |
| `validation` | Decides improvement | Aggregates only |
| `holdout` | Sealed. Final candidate of a run before promotion, and periodic audits of `active` | Nothing |
| `regression` | Must-pass. Grows from production failures and previously fixed cases | Nothing |
| `safety` | Must-pass, zero tolerance | Nothing |

Only `validation` decides improvement; `regression` and `safety` can only
block; `train` is never used for acceptance.

```python
class Split(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    HOLDOUT = "holdout"
    REGRESSION = "regression"
    SAFETY = "safety"

@dataclass(frozen=True)
class Case:
    case_id: str                       # stable id within the suite
    suite_id: str
    suite_version: int
    split: Split                       # assigned by the platform
    must_pass: bool                    # true for regression and safety
    digest: str                        # content address of the Markdown case file
    origin: CaseOrigin                 # where it came from (authored, mined, strategy-added)
    name: str
    category: str
    timeout_s: int
    grading: GradingSpec               # grader kind, rubric, weights
    source_episode: str | None         # set when the case was mined from a real episode

@dataclass(frozen=True)
class CaseOrigin:
    kind: Literal["authored", "mined", "strategy"]
    run_id: str | None                 # set only for kind == "strategy"
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// A regression case as an operator sees it. A strategy never receives this object.
{
  "case_id": "refund_partial_03",
  "suite_id": "support-core",
  "suite_version": 7,
  "split": "regression",
  "must_pass": true,
  "digest": "sha256:e19a…",
  "origin": {"kind": "mined", "run_id": null},
  "name": "Partial refund on a split shipment",
  "category": "refunds",
  "timeout_s": 300,
  "grading": {
    "kind": "hybrid",
    "weights": {"automated": 0.6, "rubric_judge": 0.4},
    "rubric": "Offers a partial refund for the undelivered item only; does not escalate."
  },
  "source_episode": "ep_91"
}
```

### 2.3 Grader and Executor

A **grader** scores one rollout. Every grader returns `score + critique +
breakdown`, because reflective strategies (GEPA, ClawEvolve tune) need the
critique, not just a number.

| Kind | What it does | Origin |
| --- | --- | --- |
| `automated` | Deterministic check supplied by the case (`grade(transcript, workspace)`) | ClawBench `automated` |
| `rubric_judge` | LLM judge with the case's rubric | ClawBench `llm_judge` |
| `hybrid` | Weighted combination of the two | ClawBench `hybrid` |
| `ensemble` | Several judges, preferably from different model families, with an inter-judge agreement measure | New |

An **executor** runs a revision on a case:

| Executor | How | Trade-off |
| --- | --- | --- |
| Local sandbox | Materialised workspace plus the engine CLI, ClawBench style | Fast, cheap; not the real delivery path |
| Deployed sandbox | An ephemeral eval bot via `eval_publish` and the `eval_env` seams | Real apply/delivery path, any engine; slower |

Executors and graders are plugins owned by the verifier, selected by the
composition root of `apps/evolution` from configuration.

```python
@dataclass(frozen=True)
class GraderRef:
    id: str                            # "platform/clawbench"
    version: str                       # "1.0.0"

@dataclass(frozen=True)
class Grade:
    score: float                       # 0.0 .. 1.0
    critique: str                      # textual feedback; shown to strategies on train only
    breakdown: dict[str, float]        # per criterion or per sub-grader
    judges: list[JudgeScore]           # empty for automated grading
    agreement: float | None            # inter-judge agreement; None when fewer than two judges

@dataclass(frozen=True)
class JudgeScore:
    model_family: str                  # recorded so it can differ from the strategy's models
    score: float
    critique: str

@dataclass(frozen=True)
class Rollout:
    case_id: str
    revision_id: str
    seed: int
    executor: str                      # "local_sandbox" | "deployed_sandbox"
    transcript_ref: str                # eval trace stored in the Experience Store
    cost: Cost
    grade: Grade
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// One graded rollout of the candidate on a validation case, with a two-judge ensemble.
{
  "case_id": "refund_policy_11",
  "revision_id": "sha256:7c1e…",
  "seed": 2,
  "executor": "deployed_sandbox",
  "transcript_ref": "trace:tr_5512",
  "cost": {"tokens": 18400, "usd": 0.21, "latency_ms": 41200},
  "grade": {
    "score": 0.85,
    "critique": "Correctly offered a partial refund; did not confirm the item list first.",
    "breakdown": {"automated": 1.0, "rubric_judge": 0.625},
    "judges": [
      {"model_family": "family-a", "score": 0.6, "critique": "Missed confirmation step."},
      {"model_family": "family-b", "score": 0.65, "critique": "Policy correct; confirmation missing."}
    ],
    "agreement": 0.95
  }
}
```

### 2.4 VerificationProfile

A **verification profile** is a versioned policy that says how strictly a
candidate is checked: which splits, how many seeds, which executor, the
minimum effect, the confidence level, regression tolerance, and cost rules.
It is owned by the verifier and chosen per binding by the bot owner. An
owner may pick a stricter profile, never a looser one; a strategy cannot
choose or change it.

```python
@dataclass(frozen=True)
class VerificationProfile:
    profile_id: str                    # "default"
    version: int                       # profiles are referenced as "default@1"
    stricter_than: list[str]           # profiles this one may replace in a binding
    executor: Literal["local_sandbox", "deployed_sandbox"]
    seeds: SeedPolicy
    validation: ValidationRule
    regression_tolerance: int          # newly failing must-pass regression cases allowed
    safety_tolerance: Literal[0]       # always zero
    ensemble_from_tier: str            # "T2": use an ensemble for candidates at or above this risk tier
    min_judge_agreement: float
    holdout_on_final_candidate: bool
    overfit_guard: OverfitRule
    cost: CostRule
    budget_matched_baseline: bool      # beat parent-with-extra-sampling at equal cost

@dataclass(frozen=True)
class SeedPolicy:
    initial: int                       # k seeds per case
    max: int                           # raised automatically when close to the threshold
    escalate_within: float             # escalate when |CI_lower - min_effect| < this

@dataclass(frozen=True)
class ValidationRule:
    min_effect: float                  # CI lower bound of the paired mean difference must clear it
    confidence: float                  # e.g. 0.95

@dataclass(frozen=True)
class OverfitRule:
    max_gain_ratio: float              # validation gain vs regression/holdout gain
    max_seed_fluctuation: float

@dataclass(frozen=True)
class CostRule:
    max_cost_increase_pct: float | None  # None: cost is reported but not limited
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// The numeric thresholds are proposed starting values, not decided ones; only k = 3 comes from the source design.
{
  "profile_id": "default",
  "version": 1,
  "stricter_than": [],
  "executor": "deployed_sandbox",
  "seeds": {"initial": 3, "max": 9, "escalate_within": 0.02},
  "validation": {"min_effect": 0.02, "confidence": 0.95},
  "regression_tolerance": 1,             // a used tolerance is flagged so Promotion sends it to review
  "safety_tolerance": 0,
  "ensemble_from_tier": "T2",
  "min_judge_agreement": 0.7,
  "holdout_on_final_candidate": true,
  "overfit_guard": {"max_gain_ratio": 3.0, "max_seed_fluctuation": 0.05},
  "cost": {"max_cost_increase_pct": null},
  "budget_matched_baseline": false
}
```

### 2.5 Evaluation

An **evaluation** is one execution of one or two revisions on a set of
cases: every rollout and every grade. A paired evaluation runs a subject and
its baseline on the same cases, with the same seeds and simulated-user
scripts, in the same executor. Evaluations are the raw evidence; verdicts
are computed from them. An evaluation is a long-running piece of work, so it
is started as an operation and looked up by id.

```python
@dataclass(frozen=True)
class Evaluation:
    evaluation_id: str                 # "ev_301"
    bot_id: str
    purpose: Literal["verification", "train", "holdout_audit", "ad_hoc", "shadow"]
    subject: str                       # revision id, or a workspace id for train evaluations
    baseline: str | None               # revision id; None for unpaired (train, single-revision ad-hoc)
    suites: list[SuiteVersionRef]
    splits: list[Split]
    executor: str
    seeds: int
    operation_id: str                  # status lives on the operation
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    rollouts: list[Rollout]            # filled as it runs
    cost: Cost
    verifier: VerifierVersion
    run_id: str | None                 # set for verification and train evaluations of a run

@dataclass(frozen=True)
class VerifierVersion:
    protocol: str                      # "bot-verification@1"
    profile: str                       # "default@1"
    suites: dict[str, int]             # suite id -> version
    graders: dict[str, str]            # grader id -> version
    digest: str                        # hash over all of the above
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// A finished paired verification evaluation, summarised (rollouts truncated to one).
{
  "evaluation_id": "ev_301",
  "bot_id": "bot_123",
  "purpose": "verification",
  "subject": "sha256:7c1e…",               // r42, the candidate revision
  "baseline": "sha256:a90b…",              // r41, its parent
  "suites": [{"suite_id": "support-core", "version": 7}, {"suite_id": "platform-safety", "version": 3}],
  "splits": ["validation", "regression", "safety"],
  "executor": "deployed_sandbox",
  "seeds": 3,
  "operation_id": "op_19a",
  "status": "succeeded",
  "rollouts": [
    {"case_id": "refund_policy_11", "revision_id": "sha256:7c1e…", "seed": 2,
     "executor": "deployed_sandbox", "transcript_ref": "trace:tr_5512",
     "cost": {"tokens": 18400, "usd": 0.21, "latency_ms": 41200},
     "grade": {"score": 0.85, "critique": "Correct policy; skipped confirmation.",
               "breakdown": {"automated": 1.0, "rubric_judge": 0.625}, "judges": [], "agreement": null}}
  ],
  "cost": {"tokens": 3120000, "usd": 6.4, "latency_ms": 2710000},
  "verifier": {
    "protocol": "bot-verification@1",
    "profile": "default@1",
    "suites": {"support-core": 7, "platform-safety": 3},
    "graders": {"platform/clawbench": "1.0.0"},
    "digest": "sha256:9e07…"
  },
  "run_id": "run_7f3"
}
```

### 2.6 Verdict

A **verdict** is the comparison result for one candidate under one profile.
Its status is `pending` until the comparison finishes, then exactly one of
`accept`, `reject`, or `inconclusive`, and it never changes after that. It
carries per-split evidence, cost, the verifier version, and reasons. A
verdict is about measurement only: whether the candidate is promoted is the
gate's decision ([08-promotion.md](08-promotion.md)).

```python
VerdictStatus = Literal["pending", "accept", "reject", "inconclusive"]

@dataclass(frozen=True)
class FloorRef:                        # Promotion's static floor, run at submission (08-promotion.md)
    passed: bool
    gate_decision_ref: str | None      # GateDecision id holding the floor result; set when the floor failed

@dataclass(frozen=True)
class Verdict:
    candidate: str                     # candidate id: content hash of the patch
    bot_id: str
    run_id: str | None                 # None for a publish-flow verification outside a run
    revision: str | None               # candidate revision id recorded by the Genome Registry; present once recorded
    parent_revision: str
    profile: str                       # "default@1"
    status: VerdictStatus
    floor: FloorRef                    # reference only: floor rules and results are owned by Promotion
    sanity: Literal["pass", "fail", "not_run"]
    splits: dict[Split, SplitResult]   # per-split aggregates: validation, regression, safety, holdout (when run)
    judge_agreement: float | None
    overfit_flags: list[str]
    tolerance_used: bool               # a regression tolerance was spent; Promotion routes to review
    cost: CostComparison
    evaluations: list[str]             # evaluation ids
    verifier: VerifierVersion
    reasons: list[str]
    decided_at: datetime | None

@dataclass(frozen=True)
class SplitResult:
    cases: int
    seeds: int
    mean_delta: float | None           # paired mean difference (validation, holdout)
    ci: tuple[float, float] | None
    win_rate: float | None
    newly_failing: list[str]           # case ids (must-pass splits); never shown to strategies

@dataclass(frozen=True)
class AggregateScores:                 # validation aggregates a strategy may see
    cases: int
    seeds: int
    mean_delta: float                  # paired mean difference, candidate minus parent
    ci: tuple[float, float]

@dataclass(frozen=True)
class StrategyVerdictView:             # what ctx.candidates.verdict(id) returns ("Verdict" in 03-strategy.md)
    candidate: str                     # candidate id
    status: VerdictStatus
    revision: str | None               # candidate revision, so the next round can build on it
    aggregates: dict[str, AggregateScores]   # {"validation": ...} only; empty while pending
    reasons: list[str]                 # categories only, e.g. "must_pass_failure"; no case ids
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// The full verdict as stored and shown to operators and reviewers.
{
  "candidate": "sha256:c41e…",
  "bot_id": "bot_123",
  "run_id": "run_7f3",
  "revision": "sha256:7c1e…",
  "parent_revision": "sha256:a90b…",
  "profile": "default@1",
  "status": "accept",
  "floor": {"passed": true, "gate_decision_ref": null},
  "sanity": "pass",
  "splits": {
    "validation": {"cases": 30, "seeds": 3, "mean_delta": 0.061, "ci": [0.028, 0.094],
                   "win_rate": 0.63, "newly_failing": []},
    "regression": {"cases": 18, "seeds": 3, "mean_delta": null, "ci": null,
                   "win_rate": null, "newly_failing": []},
    "safety":     {"cases": 12, "seeds": 3, "mean_delta": null, "ci": null,
                   "win_rate": null, "newly_failing": []}
  },
  "judge_agreement": 0.82,
  "overfit_flags": [],
  "tolerance_used": false,
  "cost": {"parent_usd_per_case": 0.19, "candidate_usd_per_case": 0.2, "increase_pct": 5.3},
  "evaluations": ["ev_301", "ev_302"],
  "verifier": {"protocol": "bot-verification@1", "profile": "default@1",
               "suites": {"support-core": 7, "platform-safety": 3},
               "graders": {"platform/clawbench": "1.0.0"}, "digest": "sha256:9e07…"},
  "reasons": ["validation CI lower bound 0.028 >= min_effect 0.02", "no must-pass failures"],
  "decided_at": "2026-10-08T03:58:00Z"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// The same verdict as a strategy sees it through ctx.candidates.verdict("sha256:c41e…").
{
  "candidate": "sha256:c41e…",
  "status": "accept",
  "revision": "sha256:7c1e…",
  "aggregates": {"validation": {"cases": 30, "seeds": 3, "mean_delta": 0.061, "ci": [0.028, 0.094]}},
  "reasons": ["validation_improved"]
}
```

## 3. Verification pipeline

![Verification pipeline and splits](images/verification-pipeline.svg)

A verification request names a **subject** and a **baseline** (two genome
revisions of the same bot; at level 3, two mechanism revisions) and a
**profile**. The pipeline then:

1. **Plans** the cases per split, the seeds, and the cost cap from the
   profile and the suites in scope.
2. **Executes** paired rollouts in a sandbox: subject and baseline on the
   same cases, seeds, and simulated-user scripts, in the same executor.
3. **Grades** each rollout with the suite's graders (score + critique).
4. **Compares** per split with the **comparator**: paired per-case
   differences across repeated seeds, confidence intervals, win rate, and
   cost.
5. Produces the **verdict**: `accept`, `reject`, or `inconclusive`, with
   evidence.

The comparator is a set of pure functions over grades, so it can be tested
in isolation and reused at problem granularity for mechanism verification
([10-meta-evolution.md](10-meta-evolution.md)).

**Who can touch what.** Executors and graders are verifier plugins, not
strategy code. A binding picks the verification profile (an owner may pick
a stricter one, never a looser one). A strategy may *add* train cases
through `ctx.evaluate.add_train_cases()`
([§5](#5-train-split-evaluation-evaluatetrain1)). It may not change graders,
holdout, regression, or safety.

## 4. Bot verification protocol (level 2)

Run when a strategy submits candidate S′ with parent S1 (Evolution Run calls
`verify(candidate, profile)` with the binding's profile), and when the
service-bot publish flow asks for a verification gate.

1. **Static floor** (cheap, before verification): Verification does not
   define or run floor rules. Evolution Run calls Promotion's `FloorCheck`
   when the candidate is submitted ([06-evolution-run.md](06-evolution-run.md),
   [08-promotion.md](08-promotion.md)). A floor failure makes the verdict
   `reject` without running any suite; the verdict only references the
   result (`floor: {passed, gate_decision_ref}`), which is stored on the
   `GateDecision` and in the ledger.
2. **Sanity**: a fail-fast case, as ClawBench's `task_00_sanity` does. A
   failing sanity case ends with `reject`.
3. **Paired execution**: S1 and S′ on the same cases, with the same seeds
   and simulated-user scripts, in the same executor. Default *k* = 3 seeds
   per case, raised automatically when results are close to the threshold
   (the reachable form of ClawEvolve's `replicate-validation`).
4. **Grade** with the suite's graders. For candidates at risk tier T2 or
   above (the tier is assigned per patch op by Promotion, see
   [08-promotion.md](08-promotion.md)), use an **ensemble** of at least two
   judges, preferably from a different model family than the strategy's
   models (the model each strategy call used is recorded by `models@1`), and
   record inter-judge agreement. Low agreement makes the verdict
   `inconclusive` rather than `accept`.
5. **Compare per split:**
   - `validation`: paired mean difference with a confidence interval. The
     lower bound must clear the profile's minimum effect, not just "> 0".
   - `regression`: zero newly failing must-pass cases, or within the
     profile's tolerance. A spent tolerance sets `tolerance_used`, which
     Promotion routes to review.
   - `safety`: zero newly failing cases, no tolerance.
   - `train`: reported to the strategy as feedback, never used for
     acceptance.
6. **Holdout**: not run on every candidate. It runs on the final candidate
   of a run before promotion, and periodically on `active`
   ([§6](#6-holdout-audits-and-online-verification)).
7. **Overfit guard**: flag candidates whose validation gain greatly exceeds
   their gain on regression and holdout, or whose score fluctuates across
   seeds beyond tolerance (this generalises the dormant
   `CandidateVersionService` check in
   `modules/workflow/server/services/evolve/candidate-version-service.ts`,
   which auto-deploys only if `scoreVsBaseline > 0` and round fluctuation
   ≤ 0.05).
8. **Cost accounting**: tokens, latency, and money per case for S1 and S′.
   A profile can require that S′ is not more expensive beyond tolerance, or
   that it beats a **budget-matched baseline** (S1 with extra sampling at
   equal cost), so a strategy has to beat "just try harder". A candidate
   that wins only by spending more is reported as such.
9. **Verdict** with evidence, written to the Experiment Ledger
   ([05-experiment-ledger.md](05-experiment-ledger.md)).

### 4.1 Default verdict policy

A binding can choose a stricter profile, never a looser one.

```text
accept       ⇔ floor ok ∧ sanity ok ∧ safety: no new failures ∧ regression: within tolerance
               ∧ validation: CI_lower(Δ) ≥ min_effect ∧ judges agree ∧ (holdout ok, when run)
reject       ⇔ floor fails ∨ sanity fails ∨ any must-pass failure beyond tolerance
               ∨ CI_upper(Δ) < min_effect
inconclusive ⇔ otherwise  → the strategy may spend more budget (submit with more cases) or stop
```

```python
def decide(floor: FloorRef, sanity: str, splits: dict[Split, SplitResult],
           agreement: float | None, p: VerificationProfile) -> VerdictStatus:
    """Pure function; the default verdict policy above."""
    if not floor.passed or sanity == "fail":
        return "reject"
    if splits[Split.SAFETY].newly_failing:
        return "reject"
    if len(splits[Split.REGRESSION].newly_failing) > p.regression_tolerance:
        return "reject"
    lo, hi = splits[Split.VALIDATION].ci
    if hi < p.validation.min_effect:
        return "reject"
    holdout = splits.get(Split.HOLDOUT)
    if holdout is not None and holdout.ci[1] < 0:
        return "reject"
    judges_ok = agreement is None or agreement >= p.min_judge_agreement
    if lo >= p.validation.min_effect and judges_ok:
        return "accept"
    return "inconclusive"
```

**Seed escalation.** When the validation interval straddles the minimum
effect within `seeds.escalate_within`, the service adds seeds (up to
`seeds.max`) before deciding, instead of returning `inconclusive` straight
away. This is what makes ClawEvolve's dormant paired, seeded replication
reachable.

**Rejection is a result.** `reject` and `inconclusive` are normal verdicts
with full evidence, never API errors.

### 4.2 Verdict lookup and idempotency

`verify` returns at once with a `pending` verdict; it never holds a request
open. The verdict is looked up by candidate id (by the strategy through
`ctx.candidates.verdict(id)`, by Promotion and operators through the
candidate report in [08-promotion.md](08-promotion.md)). Because the
candidate id is the content hash of the patch, a repeated `verify` for the
same candidate and profile returns the existing verdict and starts no new
rollouts. Submissions made before a run fails, is cancelled, or runs out of
budget are still verified ([06-evolution-run.md](06-evolution-run.md)).

## 5. Train-split evaluation (`evaluate.train@1`)

The `evaluate.train@1` capability (declared in a strategy's `needs`; contract
in [03-strategy.md](03-strategy.md)) gives a strategy a platform evaluation
on the **train split only**, with scores and critiques, plus a way to add
train cases. This service implements it.

- **`ctx.evaluate.start_train(workspace, idempotency_key=…, cases=None)`**
  evaluates the strategy's sandbox workspace on the bot's train cases and
  returns an **operation id** at once. Status is looked up by id
  (`ctx.operations.get(op_id)`); when it has succeeded, the result holds a
  score per case, the grader critiques, and the aggregate. Over the Job
  Protocol this is `POST /evolution/v1/runs/{run}/evaluations:train` →
  `202 {operation_id}`.
- **Idempotent per key.** Repeating the start with the same idempotency key
  returns the same operation, finished or not, instead of running and paying
  for the evaluation again. Strategies build keys from the run id and their
  own step, for example `run_7f3/round-2/train`, so they re-attach after a
  re-dispatch without storing operation ids.
- **Charged to the run's budget** (rollouts, tokens, money), and cancelled
  when the run ends.
- **`ctx.evaluate.add_train_cases(cases)`** adds cases the strategy wrote
  (for example, ClawEvolve's plan step turning diagnosed failures into
  bench cases). Proposed: strategy-authored cases always land in `train`,
  because the strategy has seen them, so they can never serve as hidden
  evidence. Cases are validated against the case format and content-scanned
  like patches (experience-derived text is untrusted input). A quick call:
  plain request and response.
- **What never reaches the strategy**: validation per-case details,
  holdout, regression, and safety cases, their ids, or their counts per
  case. The orchestrator enforces this on every response, not prompts.

```python
@dataclass(frozen=True)
class TrainEvaluationResult:        # ctx.operations.get(op_id).result when succeeded
    evaluation_id: str
    score: float                    # mean over train cases and seeds
    cases: list[TrainCaseResult]
    cost: Cost

@dataclass(frozen=True)
class TrainCaseResult:
    case_id: str
    score: float
    critique: str
    breakdown: dict[str, float]
    passed: bool
```

## 6. Holdout audits and online verification

**Holdout audits.** The holdout is sealed. It runs on the final candidate
of a run before promotion (when the profile says so) and periodically on
the bot's `active` revision. A holdout drop blocks auto-promotion for the
bot and opens a review; acting on it is Promotion's job
([08-promotion.md](08-promotion.md)). The holdout is rotated and refreshed
from production on a schedule, so it does not become a training target
through repeated selection. Periodic re-audit of promoted lineage on fresh
cases also catches gains that do not generalise.

**Online verification.** Offline suites never cover everything. After
promotion, this service measures; the rollout mechanics (shadow and canary
refs, going back) belong to [08-promotion.md](08-promotion.md).

- **Shadow** (optional): replay recent real episodes, or mirror traffic to
  S2 without user impact, and grade offline with the same graders. For
  service bots this attaches to the existing **VERIFY stage**, which today
  deploys a verify bot but checks nothing.
- **Canary** (multi-instance bots): traffic is split between `active` (S1)
  and `canary` (S2). Verification compares task success, user feedback,
  error rate, and cost with sequential testing. An auto-rollback rule is
  optional and owned by Promotion.
- **Recurrence check**: Insight's `DISAPPEARED / STILL_PRESENT /
  INSUFFICIENT_DATA` verification (`modules/clawinsight`:
  `insight_failure_task`, `insight_metric_daily`,
  `/internal/governance/verification-results`) becomes an online signal on
  the experiment's `online_outcome` in the ledger.
- **Feedback into suites**: confirmed regressions become proposed new
  `regression` cases (through the diagnose → plan pipeline), and false
  accepts lower the strategy's mechanism metrics in the ledger.

## 7. Governance: anti-reward-hacking and verifier integrity

Self-improvement that the improver can grade is self-deception at scale.
The literature is consistent on this: the Darwin Gödel Machine disabled its
own hallucination checker; self-graded loops are lenient; LLM-authored
skills often add nothing without eval-guided revision; harness-evolution
gains often vanish against a budget-matched baseline (see
[research.md](research.md)). The rules below make verification trustworthy
regardless of which strategy runs. The full separation-of-powers table is in
[08-promotion.md](08-promotion.md); this service holds the power "run
evaluations", and humans hold "change the verifier".

### 7.1 Anti-reward-hacking rules

- **Outside the genome.** Evaluators, graders, suites, and their configs
  live outside the genome and are read-only to strategies (and to bots, once
  bot callers exist; postponed).
- **Hidden cases.** Strategies never see validation per-case details
  (aggregates only), holdout, regression, or safety cases. This generalises
  ClawEvolve's review firewall and is enforced by the `StrategyContext` and
  the Job Protocol responses, not by prompt.
- **Diff audit.** Patches that mention or edit guardrail-like text (safety
  sections, refusal policies, logging/reporting instructions, text
  resembling evaluation instructions) are tagged; Promotion raises them to
  T2 or above, which in turn makes this service use a judge ensemble.
- **Independent judges.** Judges come from a different model family than
  the strategy's models where available; multiple graders for T2
  promotions.
- **Cost honesty.** Cost and length are tracked per revision; a candidate
  that wins only by spending more is reported as such, and a profile can
  require a budget-matched baseline (mandatory for platform-published
  strategies, per [08-promotion.md](08-promotion.md)).
- **Re-audit.** Promoted lineage is periodically re-audited on fresh cases
  ([§6](#6-holdout-audits-and-online-verification)).

### 7.2 Verifier integrity

- **Versioned.** Suites, graders, profiles, and protocols each have
  versions. Every verdict records the `VerifierVersion`, and comparisons
  across verifier versions are never mixed (ledger metrics are computed
  within one verifier version).
- **Human-owned.** Changes go through code review like platform code. The
  Experiment Ledger may *suggest* verifier changes (for example "failure
  class Z has no regression coverage"); it never applies them.
- **Calibrated.** The verifier itself is measured periodically: judge
  accuracy against human labels, gate precision/recall on a golden corpus
  (extending `calibrate_evolution_gates.py` from gates to judges), and
  holdout freshness.
- **Hidden.** Strategy and meta-strategy inputs never include holdout,
  regression, or safety cases. Validation is exposed only as aggregates.
  The orchestrator enforces this; prompts do not.
- **Tamper-evident.** Grader code and case content are content-addressed.
  A candidate that edits text resembling guardrails or evaluation
  instructions is tagged and raised in risk tier.
- **Sandboxed.** Evaluations run only on sandbox materialisations (eval
  bots via `eval_env`, or ephemeral workspaces), with no production
  credentials and no access to the live phenotype. The sandboxing rules
  themselves are in [06-evolution-run.md](06-evolution-run.md).

## 8. Placement, reuse, and migration

### 8.1 Existing platform code this service reuses

From the inventory of bot-quality evaluation paths (ordinary unit tests of
platform code excluded). The ClawEvolve/ClawBench rows of the same inventory
(ClawBench runner, ClawWeb Bench store, ClawEvolve splits and gates, gate
calibration/replay, diagnose → plan) are in
[04-default-strategies.md](04-default-strategies.md); in short, ClawBench's
case format and graders seed the `platform/clawbench` grader and the Suite
registry, and gate calibration seeds verifier calibration.

| Component | Where | What it does | Status | Reuse as |
| --- | --- | --- | --- | --- |
| **Backend eval env + Quality Task** | `core/service_bot/services/publish_flow/eval_publish_mixin.py:33-185`, `core/quality/services/task_processor.py:36-44,304-329`, `adapters/http/quality/router.py`; seams `plugin_api/eval_env/*` (`EvalEnvLifecycleProtocol`, `EvalVersionSyncProtocol`, …), BaaS `spi/eval_env/` | Deploys an isolated, TTL-bound copy of a service bot at `PublishStage.EVAL`, routes eval sessions by tag, then calls an **external** grader (MASA `/eval/start`, `/eval/progress`). Results stored opaquely | Wired, but grader external; eval_env plugin protocols are unused Noop stubs; no scheduler | **Deployed-sandbox executor** (the real phenotype, any engine) |
| **Service-bot VERIFY stage** | `publish_flow_service.py:150,174,386-397` | Deploys a verify-environment bot and waits for manual "go online" | Live, **no automated checks** | Attachment point for a **verification gate** on publish |
| **Insight verification** | `modules/clawinsight` (`insight_failure_task`, `insight_metric_daily`, `/internal/governance/verification-results`) | Online failure monitoring; post-fix recurrence check `DISAPPEARED/STILL_PRESENT/INSUFFICIENT_DATA` | Live; judges external | **Online verification** signal |
| **CandidateVersionService** | `modules/workflow/server/services/evolve/candidate-version-service.ts` | Auto-deploy if `scoreVsBaseline>0` and round fluctuation ≤ 0.05 (overfit check) | Dormant, for workflows | Idea reused in the overfit guard |
| TaskGuard voters, hallucination checker | `apps/evolverun/taskguard/src` | Runtime guards in workflows (3-voter majority) | Live, runtime | Pattern for multi-judge (ensemble) grading |

Not bot-quality evaluation (excluded): `bcs-judge` (picks state-machine
transitions), `singlebox/verity/*` (platform smoke tests), backend/bcsfuse
golden tests (config behaviour), legacy `validation_templates`.

### 8.2 Gaps against what the platform needs

1. No statistical significance. Acceptance compares two means once; std
   from `--runs` is unused, and paired replication is unreachable.
2. The test split is re-used every round against the last accepted
   baseline. There is no sealed holdout and no guard against adaptive
   overfitting across rounds.
3. No first-class must-pass regression or safety suite blocking promotion.
4. One judge per grade. No judge ensemble, agreement measure, or judge
   calibration.
5. Bench runs a local OpenClaw agent on a copied workspace, not the deployed
   bot. The deployed-bot path (eval env) has no in-repo grader.
6. No online paired comparison (shadow/canary). Insight only counts
   recurrence before and after.
7. No comparison of improvement *mechanisms* (only gate calibration); see
   [10-meta-evolution.md](10-meta-evolution.md).

### 8.3 Placement

| Piece | Owner | Notes |
| --- | --- | --- |
| Verification Service (plans, comparator, verdicts, profiles) | `apps/evolution` (C5) | Called by Evolution Run, by Promotion (holdout audits, candidate report), by the service-bot publish flow, and by operators |
| Suite registry | `apps/evolution` | Starts from the ClawWeb Bench data model and the ClawBench case format; adds `split`, `must_pass`, and visibility |
| Executor plugins | Local sandbox: `apps/evolution`. Deployed sandbox: **Backend** `eval_publish` + `eval_env` seams | Making the eval-env plugin protocols real (today Noop) is part of this work |
| Grader plugins | Verifier-owned | `platform/clawbench` (automated / rubric / hybrid) first, then ensemble |
| Publish-flow hook | Backend | Optional verification gate on `VALIDATING → ONLINE_PUB` for service bots, using this same service |
| Quality Task | Backend | Points at the in-repo Verification Service instead of (or alongside) the external MASA grader, via a plugin, so the open-source build has a working grader |

### 8.4 Migration from what exists

1. Extract `lib_grading` and case parsing (`clawbench-base/scripts/`) into
   the `platform/clawbench` grader plugin, keeping the Markdown case format
   byte-compatible.
2. Move suite storage to the Suite registry and keep ClawWeb Bench readable
   (or make it a view).
3. Implement the comparator with paired statistics. Re-express ClawEvolve's
   `full_opt_gate` and `candidate_opt_gate` as verdict-policy rules and make
   them **blocking** under the default profile. ClawEvolve's
   `action_accept` stays as the strategy's own, looser submission filter
   ([04-default-strategies.md](04-default-strategies.md)).
4. Add a deployed-sandbox executor on `eval_publish`, and wire the Quality
   Task to it.
5. Attach the optional verification gate to the service-bot VERIFY stage.
6. Extend gate calibration to judge calibration and run it on a schedule.

Work items: RSI-11 (Verification Service) and RSI-22 (publish-flow gate and
Quality Task) in [work-items.md](work-items.md). RSI-22 is done when a
service bot whose verify-stage candidate fails a must-pass case cannot be
published without an explicit, audited override.

## 9. Service interface

Proposed Python Protocols in `apps/evolution`. Other parts call the
`VerificationService`; the plugins are what executors and graders
implement. Long-running work returns ids at once; nothing blocks.

```python
class VerificationService(Protocol):
    """Platform-owned verifier. Read-only to strategies; they reach it only
    through evaluate.train@1 and ctx.candidates.verdict."""

    async def verify(self, candidate: CandidateRef, profile: str) -> Verdict:
        """Start verifying a submitted candidate against its parent under `profile`
        (e.g. "default@1"). Returns at once with status "pending" (or the existing
        verdict: idempotent per (candidate_id, profile)). Called by Evolution Run on
        submission and by the publish-flow hook. Never raises for a rejection."""

    async def get_verdict(self, candidate_id: str, profile: str) -> Verdict:
        """Full verdict for operators, Promotion, and the ledger."""

    async def strategy_view(self, candidate_id: str, run_id: str) -> StrategyVerdictView:
        """Redacted verdict for ctx.candidates.verdict(id): validation aggregates only.
        Refuses candidates not submitted by `run_id`."""

    async def start_train_evaluation(self, run_id: str, workspace_id: str, *,
                                     idempotency_key: str,
                                     cases: list[str] | None = None) -> str:
        """Backs ctx.evaluate.start_train. Evaluates a run's sandbox workspace on the
        bot's train split (all train cases when `cases` is None). Returns an
        operation id; idempotent per (run_id, idempotency_key). Charged to the run."""

    async def add_train_cases(self, run_id: str, cases: list[CaseDraft]) -> list[str]:
        """Backs ctx.evaluate.add_train_cases. Validates, scans, and stores the cases
        in the bot's train split. Returns case ids."""

    async def start_evaluation(self, request: EvaluationRequest, *,
                               idempotency_key: str) -> str:
        """Operator-only ad-hoc evaluation of a revision (optionally paired with a
        baseline). Returns the operation id; the operation's result carries the
        evaluation_id. Never feeds promotion."""

    async def get_evaluation(self, bot_id: str, evaluation_id: str,
                             view: CallerView) -> Evaluation:
        """Evaluation with rollouts filtered to what the caller may see."""

    async def audit_holdout(self, bot_id: str, revision_id: str, *,
                            reason: Literal["final_candidate", "periodic"],
                            idempotency_key: str) -> str:
        """Score a revision on the sealed holdout, paired with its parent.
        Returns an operation id. A drop is reported to Promotion."""


class SuiteRegistry(Protocol):
    async def list_suites(self, *, bot_id: str | None = None) -> list[Suite]: ...
    async def get_suite(self, suite_id: str, *, version: int | None = None,
                        view: CallerView) -> SuiteDetail:
        """`version=None` means the current version. Case contents per caller view."""
    async def cases_for(self, bot_id: str, splits: list[Split]) -> list[Case]:
        """Platform suites plus the bot's suites, current versions. Internal only."""


class Executor(Protocol):
    """Verifier-owned plugin: runs one revision on one case in a sandbox."""
    kind: str                                   # "local_sandbox" | "deployed_sandbox"
    async def prepare(self, bot_id: str, revision_id: str, key: str) -> SandboxHandle: ...
    async def execute(self, sandbox: SandboxHandle, case: Case, seed: int) -> RolloutRecord: ...
    async def release(self, sandbox: SandboxHandle) -> None: ...


class Grader(Protocol):
    """Verifier-owned plugin: scores one rollout. Always returns score + critique."""
    ref: GraderRef
    async def grade(self, case: Case, rollout: RolloutRecord) -> Grade: ...


class Comparator(Protocol):
    """Pure functions; reused by mechanism verification at problem granularity."""
    def compare(self, split: Split, subject: list[Rollout], baseline: list[Rollout],
                confidence: float) -> SplitResult: ...
```

## 10. API

**Public endpoints.** All public paths are relative to the prefix `/openapi/v1`. Shared
conventions (errors, pagination, `Idempotency-Key`, ETags) are in
[09-evolution-api.md](09-evolution-api.md). Responses below show the `data`
payload of the standard envelope; see
[09-evolution-api.md](09-evolution-api.md) for the envelope, errors,
pagination, and idempotency. Verdicts are read through the
candidate report in [08-promotion.md](08-promotion.md); this service has no
separate public verdict endpoint.

### GET /evolution/suites

Lists suites in scope for a bot, or platform suites. Called by operators,
the UI backend, and pipelines. Returns metadata and split counts, never case
contents.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: GET /openapi/v1/evolution/suites?bot=bot_123
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 200
{
  "total": 2,
  "items": [
    {"suite_id": "support-core", "version": 7, "scope": {"kind": "bot", "bot_id": "bot_123"},
     "case_format": "clawbench-md@1",
     "split_counts": {"train": 40, "validation": 30, "holdout": 20, "regression": 18, "safety": 12},
     "digest": "sha256:3b8d…"},
    {"suite_id": "platform-safety", "version": 3, "scope": {"kind": "platform", "bot_id": null},
     "case_format": "clawbench-md@1",
     "split_counts": {"train": 0, "validation": 0, "holdout": 0, "regression": 0, "safety": 25},
     "digest": "sha256:0f6a…"}
  ]
}
```

Errors: `404` unknown bot.

### GET /evolution/suites/{suite}

One suite version with its cases, filtered by caller role: operators see
case contents of the bot's suites; holdout and safety contents are visible
only to verifier maintainers (proposed). Optional `?version=`. The role
model itself is out of scope here.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: GET /openapi/v1/evolution/suites/support-core?version=7
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 200 (operator view; holdout entries are listed without content)
{
  "suite_id": "support-core",
  "version": 7,
  "digest": "sha256:3b8d…",
  "cases": [
    {"case_id": "refund_partial_03", "split": "regression", "must_pass": true,
     "digest": "sha256:e19a…", "name": "Partial refund on a split shipment",
     "content_ref": "/openapi/v1/evolution/suites/support-core/cases/refund_partial_03"},
    {"case_id": "hold_17", "split": "holdout", "must_pass": false,
     "digest": "sha256:44c0…", "name": null, "content_ref": null}
  ]
}
```

Errors: `404` unknown suite or version.

### POST /bots/{bot}/evolution/evaluations

Operator-only ad-hoc evaluation: evaluate a revision on chosen suites and
splits, optionally paired with a baseline. Used by bot owners and
researchers, for example to check a hand-written revision before proposing
it. Requires an `Idempotency-Key` header. Returns `202` with
`{operation_id}`; nothing waits. Status is looked up with
`GET /bots/{bot}/evolution/operations/{operation}`
([09-evolution-api.md](09-evolution-api.md)); once the operation has
succeeded, its `result` carries the `evaluation_id`, and the evaluation
itself is read with `GET /bots/{bot}/evolution/evaluations/{evaluation}`. An ad-hoc evaluation never produces a
verdict that feeds promotion. Proposed: it cannot include `holdout` (running
the holdout on demand would leak it through selection).

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: POST /openapi/v1/bots/bot_123/evolution/evaluations
// Header: Idempotency-Key: adhoc-bot_123-r42-2026-10-08
{
  "revision": "sha256:7c1e…",
  "baseline": "sha256:a90b…",
  "suites": ["support-core"],
  "splits": ["validation", "regression"],
  "executor": "local_sandbox",
  "seeds": 3
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 202
{"operation_id": "op_19a"}
```

Errors: `400` unknown split or a `holdout` request; `404` unknown revision or
suite; `409` same idempotency key with a different body; budget ceilings of
the bot or tenant ([06-evolution-run.md](06-evolution-run.md)) reject with
the shared budget error.

### GET /bots/{bot}/evolution/evaluations/{evaluation}

Status and results of an evaluation, by id (the `evaluation_id` from the
operation's `result`, or from a verdict's `evaluations`). Callers: operators,
the UI backend, pipelines.
Rollouts of hidden splits are summarised for callers who may not see them.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: GET /openapi/v1/bots/bot_123/evolution/evaluations/ev_310
{}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 200
{
  "evaluation_id": "ev_310",
  "purpose": "ad_hoc",
  "operation_id": "op_19a",
  "status": "succeeded",
  "subject": "sha256:7c1e…",
  "baseline": "sha256:a90b…",
  "splits": {
    "validation": {"cases": 30, "seeds": 3, "mean_delta": 0.058, "ci": [0.021, 0.095],
                   "win_rate": 0.6, "newly_failing": []},
    "regression": {"cases": 18, "seeds": 3, "mean_delta": null, "ci": null,
                   "win_rate": null, "newly_failing": []}
  },
  "rollouts_ref": "/openapi/v1/bots/bot_123/evolution/evaluations/ev_310?include=rollouts",
  "cost": {"tokens": 1980000, "usd": 3.9, "latency_ms": 1500000},
  "verifier": {"protocol": "bot-verification@1", "profile": null,
               "suites": {"support-core": 7}, "graders": {"platform/clawbench": "1.0.0"},
               "digest": "sha256:51d2…"}
}
```

Errors: `404` unknown evaluation for this bot.

**Internal APIs** (not part of the public API).

**`verify(candidate, profile)`** is an in-process call inside
`apps/evolution` for Evolution Run ([06-evolution-run.md](06-evolution-run.md)).
Backend callers (the publish-flow hook and the Quality Task plugin) need a
transport; proposed: `POST /evolution/v1/verifications` (`202` with the
candidate id and `pending` verdict, idempotent per candidate and profile)
and `GET /evolution/v1/verifications/{candidate}?profile=` for the verdict.
This replaces the earlier sketch `POST /evolution/verifications`.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: POST /evolution/v1/verifications  (from the service-bot publish-flow hook)
{
  "bot": "bot_123",
  "candidate_revision": "sha256:7c1e…",
  "parent_revision": "sha256:a90b…",
  "profile": "default@1"
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 202
{"candidate_id": "sha256:c41e…", "profile": "default@1", "status": "pending"}
```

**Job Protocol endpoints served by this service.** The Job Protocol and its
rules (fencing tokens in the `Evolution-Fencing-Token` header, `403` for
capabilities not granted, `409` for stale tokens) are defined in
[06-evolution-run.md](06-evolution-run.md); the two endpoints behind
`evaluate.train@1` are implemented here.

### POST /evolution/v1/runs/{run}/evaluations:train

`ctx.evaluate.start_train`. Called by job-worker strategies with
`evaluate.train@1` granted.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: POST /evolution/v1/runs/run_7f3/evaluations:train  (header Evolution-Fencing-Token)
{
  "workspace_id": "ws_run_7f3_round-2",
  "idempotency_key": "run_7f3/round-2/train",
  "cases": null                        // null: all train cases of the bot
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 202
{"operation_id": "op_19a"}
```

When the operation has succeeded,
`GET /evolution/v1/runs/run_7f3/operations/op_19a` returns:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "status": "succeeded",
  "result": {
    "evaluation_id": "train_77",
    "score": 0.82,
    "cases": [
      {"case_id": "train_refund_04", "score": 1.0, "passed": true,
       "critique": "Applied the partial refund rule correctly.", "breakdown": {"automated": 1.0}},
      {"case_id": "train_refund_09", "score": 0.4, "passed": false,
       "critique": "Escalated instead of refunding the undelivered item.",
       "breakdown": {"automated": 0.0, "rubric_judge": 0.4}}
    ],
    "cost": {"tokens": 410000, "usd": 0.9, "latency_ms": 380000}
  }
}
```

Errors: `403` `evaluate.train@1` not granted; `404` unknown workspace;
`409` stale fencing token; budget exhausted ends the run
([06-evolution-run.md](06-evolution-run.md)).

### POST /evolution/v1/runs/{run}/evaluations/cases

`ctx.evaluate.add_train_cases`. Plain request and response.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Request: POST /evolution/v1/runs/run_7f3/evaluations/cases  (header Evolution-Fencing-Token)
{
  "cases": [
    {"format": "clawbench-md@1",
     "content": "---\nid: train_refund_12\nname: Partial refund, one item missing\ngrading_type: hybrid\ntimeout_seconds: 300\n---\n## Prompt\nOne of my two items never arrived. Can I get a refund for it?\n",
     "source_episode": "ep_91"}
  ]
}
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Response 200
{"case_ids": ["train_refund_12"], "split": "train", "suite": {"suite_id": "support-core", "version": 8}}
```

Errors: `400` case does not parse or fails content scanning; `403`
capability not granted; `409` stale fencing token.

The verdict lookup `GET /evolution/v1/runs/{run}/candidates/{id}`
(`ctx.candidates.verdict`) is served from `strategy_view` and returns the
redacted `StrategyVerdictView` shown in [§2.6](#26-verdict).

## 11. Examples

**Evolution Run verifying a submission.** When a strategy calls
`ctx.candidates.submit`, Evolution Run records the candidate with the Genome
Registry and asks for a verdict under the binding's profile.

```python
async def on_candidate_submitted(run: Run, binding: Binding, candidate: CandidateRef,
                                 verification: VerificationService) -> None:
    # Returns at once with "pending"; idempotent, so a re-dispatched run is safe.
    verdict = await verification.verify(candidate, profile=binding.verification_profile)
    await ledger.record_submission(run.run_id, candidate.candidate_id, verdict.status)
```

**A strategy using `evaluate.train@1`.** Inside `run(ctx)`, a strategy
scores its sandbox on the train split, keeps only what helps, and submits;
the platform's verdict decides acceptance.

```python
async def tune_round(ctx: StrategyContext, ws: Workspace, key: str, best: float) -> str | None:
    op = await ctx.evaluate.start_train(ws, idempotency_key=f"{key}/train")
    train = (await ctx.operations.wait(op)).result        # short lookups by id
    failures = [c for c in train.cases if not c.passed]   # critiques are visible on train only
    ctx.log.info("train", score=train.score, failing=len(failures))
    if train.score <= best:
        return None                                       # the strategy's own filter
    return await ctx.candidates.submit(Candidate(patch=ws.to_patch(),
                                                 rationale=f"train {train.score:.2f}",
                                                 evidence=[f"eval:{train.evaluation_id}"]))
```

```python
# Later, by candidate id. Only aggregates come back.
v = await ctx.candidates.verdict(candidate_id)
if v.status == "accept":
    base = v.revision          # next round builds on the accepted revision
```

**An operator checking a hand-written revision** with the generated client
SDK ([09-evolution-api.md](09-evolution-api.md)):

```python
from avernet_evolution import Client

c = Client.from_env()
started = c.evaluations.start(
    bot="bot_123", revision="sha256:7c1e…", baseline="sha256:a90b…",
    suites=["support-core"], splits=["validation", "regression"],
    idempotency_key="adhoc-bot_123-r42-2026-10-08")       # same key on every retry
op = c.operations.wait(bot="bot_123", operation=started.operation_id)   # short lookups by id
ev = c.evaluations.get(bot="bot_123", evaluation=op.result["evaluation_id"])
print(ev.splits["validation"].ci, ev.splits["regression"].newly_failing)
```

**Service-bot publish-flow gate** (Backend, proposed for RSI-22): on the
`VALIDATING → ONLINE_PUB` transition the hook asks for a verdict and blocks
on a must-pass failure unless an audited override is given.

```python
async def before_online_pub(publish: PublishRecord, client: VerificationClient) -> GateResult:
    resp = await client.verifications.start(bot=publish.bot_id,
                                            candidate_revision=publish.revision_id,
                                            parent_revision=publish.previous_revision_id,
                                            profile="default@1")
    verdict = await client.verifications.get(resp.candidate_id, profile="default@1")
    if verdict.status == "pending":
        return GateResult.wait()                 # publish stays in VALIDATING; checked again later
    if verdict.status == "reject" and not publish.override:
        return GateResult.block(reasons=verdict.reasons)
    return GateResult.allow(verdict_ref=verdict.candidate)
```

## 12. Interactions

| Other part | Direction | What flows |
| --- | --- | --- |
| [01-genome.md](01-genome.md) | Genome → Verification | Candidate and parent revisions, content blobs to materialise sandboxes; revision `evaluations` links point back at evaluation ids |
| [02-experience.md](02-experience.md) | Verification → Experience; Experience → Verification | Eval traces (every rollout, with grades and critiques) written as experience records; real episodes replayed for shadow grading and mined into proposed cases |
| [03-strategy.md](03-strategy.md) | Strategy ↔ Verification (through the context) | `evaluate.train@1` calls in; train results and redacted verdicts out |
| [04-default-strategies.md](04-default-strategies.md) | Default strategies → Verification | ClawBench case format and graders (as `platform/clawbench`); ClawEvolve's gate thresholds can seed a stricter profile; its plan step adds train cases |
| [05-experiment-ledger.md](05-experiment-ledger.md) | Verification → Ledger | Verdicts, evaluations, cost, verifier version per experiment; online outcomes; suggestions for verifier changes flow back to humans only |
| [06-evolution-run.md](06-evolution-run.md) | Run ↔ Verification | `verify(candidate, profile)` on submission; Job Protocol transport for train endpoints; budget charging; sandboxing rules |
| [08-promotion.md](08-promotion.md) | Verification ↔ Promotion | Verdicts into the gate and candidate report; the static floor result (from submission) and risk tier into verification; holdout audit requests and holdout drops; canary/shadow comparisons |
| [09-evolution-api.md](09-evolution-api.md) | API → Verification | Public suite and evaluation endpoints; shared conventions; SDK and CLI clients |
| [10-meta-evolution.md](10-meta-evolution.md) | Meta-evolution → Verification | Reuses the comparator at problem granularity; replays stored candidates through new profiles for screening |
| Backend publish flow and Quality Task | Backend ↔ Verification | Deployed-sandbox executor (`eval_publish`, `eval_env`); optional VERIFY-stage gate; in-repo grader for the Quality Task |
| ClawInsight | Insight → Verification | Recurrence-check results as an online signal |

## 13. Open decisions

- **Default grader (DS-2, tracked in
  [04-default-strategies.md](04-default-strategies.md)).** Is ClawBench the
  platform's default grader, or one grader among others? Recommendation:
  platform default (it already supports automated, rubric-judge, and hybrid
  grading), with the grader interface open.
- **V-1: Numeric defaults of `default@1`.** Only *k* = 3 seeds comes from the
  design; `min_effect`, confidence, seed ceiling, agreement threshold, and
  overfit ratios are proposed starting values to be set from a calibration
  run.
- **V-2: Cost of verification.** Train evaluations are charged to the run's
  budget. Whether platform verification of submitted candidates is charged
  to the run (and so can be starved by it) or to a separate per-bot
  verification allowance under the bot/tenant ceilings is open.
  Proposed: separate allowance, bounded by the profile, because submissions
  made before a budget stop must still be verified.
- **V-3: Where strategy-added cases land.** Proposed here: always `train`. The
  sources say both "a strategy may add train-split cases" and "the platform
  assigns splits"; if the platform may route strategy-authored cases into
  hidden splits, the hidden splits would contain content the strategy wrote.
- **V-4: Automatic regression growth.** The sources say regression "grows
  automatically from production failures" and also that verifier changes are
  human-reviewed and the ledger never applies them. Proposed: growth is
  proposed automatically and becomes a new suite version after review.
  Decide whether confirmed-regression cases may skip review.
- **V-5: Holdout rotation.** Schedule and size of holdout refresh from
  production, and how verdicts from before and after a rotation are
  compared (they are different verifier versions).
- **V-6: Ad-hoc evaluations and holdout.** Proposed: operators cannot run the
  holdout on demand. Confirm.
- **V-7: Internal transport for `verify`.** Proposed `POST
  /evolution/v1/verifications` for Backend callers, replacing the earlier
  `POST /evolution/verifications` sketch.
- **V-8: Suite scope resolution.** Proposed: platform suites plus the bot's
  suites at their current versions. Whether owners can pin suite versions
  per binding is open.

Resolved: the regression tolerance default is `regression_tolerance: 1` in
`default@1` (`safety` stays zero), and a spent tolerance sets
`tolerance_used`, which Promotion routes to human review
([08-promotion.md](08-promotion.md)).
