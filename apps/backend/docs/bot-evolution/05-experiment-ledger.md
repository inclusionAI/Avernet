# Experiment Ledger

> 中文版：[05-experiment-ledger.zh-CN.md](05-experiment-ledger.zh-CN.md)

> Status: DRAFT. Component in the [bot evolution architecture](design.md).
> This doc covers the Experiment Ledger H and the archive view of the ledger: the append-only
> record of every improvement experiment and every audited governance event,
> the archive views built on it, its exports, and the parent selectors that
> query it.

## 1. Purpose and scope

The **Experiment Ledger H** is the platform's memory of every improvement it
has ever attempted. An **experiment** is one level-2 attempt: a mechanism (a
strategy version) took a parent bot revision S, proposed a candidate S′, and
the platform verified it and decided what to do with it. The ledger keeps
one record per experiment, including every rejected one, together with the
evidence it was based on, what it cost, who approved it, and how the
promoted revision later did on real traffic.

The **archive view of the ledger** is the set of read views over the ledger and the genome
revisions it points at: the lineage tree of a bot, each candidate's scores
per split, which strategy and models produced it, what evidence it used,
and who approved it. Nothing in the archive is ever deleted.

The ledger serves four readers:

| Reader | What it uses the ledger for |
| --- | --- |
| Humans (owners, reviewers, researchers) | Browsing lineage, candidate reports, and the audit trail in the UI or `avn` |
| Level 2 (parent choice) | Selecting a parent beyond `active`: latest-best, Pareto front per case, MAP-Elites niches, clade scores ([§7](#7-archive-selectors-parent-choice)) |
| Strategies | A filesystem export of raw history, because raw history beats summaries for proposers (Meta-Harness) ([§9](#9-exports)) |
| Level 3 (later) | The evidence base for improving the mechanism itself: derived mechanism metrics and frozen improvement problems ([§8](#8-mechanism-metrics-and-verifier-versions)) |

It also carries the platform's **audit** obligation (governance topic:
audit, [§6](#6-audit)): every revision, gate decision, approval, promotion,
and going back is an append-only event with actor, reason, and links to
evidence and evaluations.

**Owns:**

- The `LedgerEntry` record (experiment entries and governance entries) and
  its append-only event history.
- The archive view of the ledger: lineage, per-candidate scores, filters.
- Derived mechanism metrics (computed later, for level 3) and verifier
  version tagging.
- Ledger exports: the filesystem export for strategies and the redacted
  training-data export.
- Parent selectors that query the archive (later options of a binding's
  `parent` field).

**Does not own** (it records references to these and links to the owning doc):

| Thing | Owner |
| --- | --- |
| Genome revisions, patches, refs, content blobs, revision `status`, blob retention | [01-genome.md](01-genome.md) |
| Episodes, feedback, data handling (redaction, retention, opt-in) | [02-experience.md](02-experience.md) |
| Strategy registrations, agent definitions, capabilities, candidates and verdicts as the strategy sees them | [03-strategy.md](03-strategy.md) |
| Runs, bindings, budgets, the `parent` field of a binding | [06-evolution-run.md](06-evolution-run.md) |
| Suites, graders, verification profiles, verdict computation, verifier versions | [07-verification.md](07-verification.md) |
| Gate decisions, risk tiers, review queue, promotions, going back | [08-promotion.md](08-promotion.md) |
| Shared API conventions (idempotency keys, pagination, operations lookup) | [09-evolution-api.md](09-evolution-api.md) |
| Mechanism verification, the level-3 loop, meta-strategies | [10-meta-evolution.md](10-meta-evolution.md) |

The ledger stores *what happened*; it decides nothing. It never accepts,
rejects, or promotes a candidate, and it never changes the verifier: it may
*suggest* a verifier change to a human (for example "failure class Z has no
regression coverage"), but it never applies one.

**Where it runs.** In the new `apps/evolution` module, together with the
Evolution Run service, the Strategy Registry, the Experience Store, and the
Verification Service (recommended option A of open decision D-1 in
[design.md](design.md)). Genome revisions it references live in Backend
(`core/bot_genome/`); the ledger stores their ids, never their content.

**Phasing.** Recording starts in the first iteration (work item RSI-21,
depends on RSI-08), because the first iteration needs the archive and the
audit trail anyway, and because level 3 is only as good as the history it
learns from. Archive selectors (RSI-17), training-data export (RSI-19), and
derived mechanism metrics for level 3 are later. See
[work-items.md](work-items.md).

## 2. Domain model

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `LedgerEntry` | One record in H. Kind `experiment`: one submitted candidate and everything that happened to it. Kind `governance`: one audited event not tied to a submitted candidate (for example promoting a revision recorded by hand, or going back) | Ledger (platform) | Created when the first event arrives; never deleted; changed only by appending events |
| `LedgerEvent` | One append-only fact about an entry: submitted, verified, gate decided, reviewed, promoted, went back, online outcome joined, holdout result | Ledger (written by the service that observed the fact) | Immutable once appended |
| `MechanismRef` | The exact mechanism that produced an experiment: strategy id and version, agent definition digests, models used | Ledger (copied from the run) | Frozen with the entry |
| `PatchSummary` | What the candidate proposed: op list, risk tier, size | Ledger (copied from the Genome Patch and the gate) | Frozen with the entry |
| `VerdictRecord` | The verification result per split, with verifier version, and the gate decision with reasons | Ledger (copied from Verification and Promotion) | Filled by events |
| `Cost` | Tokens, money, wall clock, rollouts spent on the experiment | Ledger (from the run's budget meter) | Filled by events |
| `Adoption` | Promoted or not, reviewed by whom, gone back later or not | Ledger (from Promotion) | Filled by events |
| `OnlineOutcome` | Live metrics of the promoted S2 against S1 after promotion (delayed join) | Ledger (from Experience and online verification) | Filled later, possibly several times |
| `LedgerFilter` | The query a reader passes to list entries | Caller | Per request |
| `MechanismMetrics` | Derived fitness of a mechanism revision per bot segment (later, level 3); definitions owned by [10-meta-evolution.md](10-meta-evolution.md) | Ledger (derived view) | Recomputed from entries |
| `ParentSelector` | A named rule that picks a run's parent revision from the archive (later) | Ledger | Versioned with the platform |

Types that other docs own and that appear here only by id: `GenomeRevision`,
`GenomePatch` ([01-genome.md](01-genome.md)); `Episode`, `Feedback`
([02-experience.md](02-experience.md)); `Candidate`, `Verdict`,
`Operation` ([03-strategy.md](03-strategy.md)); `Run`, `Binding`, `Budget`
([06-evolution-run.md](06-evolution-run.md)); `Evaluation`,
`VerificationProfile` ([07-verification.md](07-verification.md));
`GateDecision`, `RiskTier`, `ReviewItem`, `Promotion`
([08-promotion.md](08-promotion.md)).

### 2.1 LedgerEntry

An experiment entry carries every field of the experiment schema that the
level-3 design fixed early (formerly `04-recursion.md` §3):

| Field | Meaning |
| --- | --- |
| `entry_id`, `run_id`, `iteration` | Identity. `entry_id` is the experiment id; `iteration` is the order of the submission within its run |
| `mechanism_revision` | The exact mechanism M used |
| `parent_revision` → `candidate_revision` | S and S′ (Bot Genome revision ids) |
| `evidence` | Episode and finding ids the mechanism consumed |
| `patch` | What it proposed (ops, risk tier, size) |
| `verdict` | Bot verification result per split, gate decision, reasons |
| `cost` | Tokens, money, wall clock, rollouts |
| `adoption` | Promoted? Reviewed by whom? Gone back later? |
| `online_outcome` | Live metrics of S2 vs S1 after promotion (delayed join) |

Negative results are first-class records: rejected candidates, regressions,
going back, and wasted budget. That is what lets a later meta-strategy learn
"operator X keeps failing on bots of type Y", the way ClawEvolve's
mutation-operator library would want to.

```python
from dataclasses import dataclass, field
from typing import Literal

EntryKind = Literal["experiment", "governance"]
VerdictStatus = Literal["pending", "accept", "reject", "inconclusive"]
RiskTier = Literal["T0", "T1", "T2", "T3"]
# Top-level gene categories of a genome (01-genome.md §2.1).
GeneCategory = Literal["persona", "skills", "memory", "resources", "tools.mcp",
                       "tools.cli_tools", "engine_config", "script"]
# BotRef {owner_id, bot_id}: a bot's full identity (a bot_id alone is not unique
# across users); defined once in 09-evolution-api.md §2.7.


@dataclass(frozen=True)
class MechanismRef:
    """The exact mechanism (M) that produced a candidate. Frozen from the run."""
    target_kind: Literal["bot_genome"]           # "mechanism" is added with level 3
    strategy: str                                # "clawevolve/bot-evolution"
    version: str                                 # "2.0.0"
    agent_definitions: dict[str, str]            # definition name -> content digest
    models_used: list[str]                       # names from the platform model list


@dataclass(frozen=True)
class PatchSummary:
    patch_digest: str                            # digest of the stored Genome Patch
    ops: list[str]                               # e.g. "persona/SOUL.md: replace_section"
    genes: list[GeneCategory]                    # top-level genes touched
    risk_tier: RiskTier                          # max over ops, assigned by Promotion
    size_bytes_changed: int                      # bytes added plus removed across all ops
    rewrite_flagged: bool                        # some file.edit changed more than the rewrite threshold (01-genome.md §6.2)


@dataclass(frozen=True)
class SplitResult:
    split: Literal["train", "validation", "holdout", "regression", "safety"]
    cases: int                                   # number of cases of this split that were run
    mean_delta: str                              # candidate minus parent, mean score; decimal as string (no floats in hashed records)
    ci_low: str | None                           # lower end of the confidence interval of mean_delta; None for must-pass splits
    ci_high: str | None                          # upper end; None for must-pass splits
    newly_failing: int                           # cases the parent passed and the candidate fails
    evaluation_id: str                           # link into Verification


@dataclass
class VerdictRecord:
    status: VerdictStatus
    verification_profile: str                    # "default@1"
    verifier_version: str                        # every verdict records it
    splits: list[SplitResult]
    reasons: list[str]                           # human-readable reasons, as written by Verification
    gate: dict | None                            # GateDecision summary, see 08-promotion.md


@dataclass
class Cost:
    tokens: int                                  # model tokens, input plus output, over all calls
    usd: str                                     # money spent in US dollars; decimal as string
    wall_clock_s: int                            # elapsed seconds attributed to this candidate
    rollouts: int                                # evaluation rollouts: one rollout = one evaluation case run once against one bot version


@dataclass
class Adoption:
    promoted: bool
    promotion_id: str | None                     # None until promoted
    reviewed_by: str | None                      # None for auto-promoted or unreviewed
    gone_back: bool                              # active later moved away by going back
    gone_back_by: str | None                     # promotion id that went back


@dataclass
class OnlineOutcome:
    window: str                                  # "2026-10-09/2026-10-16"
    source: Literal["shadow", "canary", "active_vs_previous", "recurrence_check"]
    metrics: dict[str, str]                      # metric name -> decimal string, S2 minus S1
    episodes_compared: int


@dataclass
class LedgerEntry:
    entry_id: str                                # "led_5c2"
    kind: EntryKind
    bot: BotRef                                  # the bot the entry is about (owner + bot id)
    created_at: str                              # RFC 3339 UTC
    # experiment entries: all set; governance entries: run/candidate fields are None
    run_id: str | None
    binding_id: str | None
    iteration: int | None
    candidate_id: str | None                     # content hash of the patch
    mechanism_revision: MechanismRef | None
    parent_revision: str | None
    candidate_revision: str | None
    evidence: list[str] = field(default_factory=list)
    patch: PatchSummary | None = None
    verdict: VerdictRecord | None = None
    cost: Cost | None = None
    adoption: Adoption | None = None
    online_outcome: list[OnlineOutcome] = field(default_factory=list)
    events: list["LedgerEvent"] = field(default_factory=list)
```

The optional fields are optional because they are genuinely absent for a
reason the domain defines: a governance entry has no run, an experiment
entry has no verdict until verification finishes, and no online outcome
until the revision has been live long enough.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "entry_id": "led_5c2",
  "kind": "experiment",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "created_at": "2026-10-09T02:41:07Z",
  "run_id": "run_7f3",
  "binding_id": "bind_01",
  "iteration": 2,                                  // second submission of run_7f3
  "candidate_id": "sha256:c41e…",                  // content hash of the patch
  "mechanism_revision": {
    "target_kind": "bot_genome",
    "strategy": "clawevolve/bot-evolution",
    "version": "2.0.0",
    "agent_definitions": {"clawevolve-tune": "sha256:5e07…"},
    "models_used": ["platform-default"]
  },
  "parent_revision": "sha256:a90b…",               // r41
  "candidate_revision": "sha256:7c1e…",            // r42
  "evidence": ["episode:ep_91", "episode:ep_97", "finding:f_12"],
  "patch": {
    "patch_digest": "sha256:d2f8…",
    "ops": ["persona/SOUL.md: replace_section Escalation", "skills/refund-policy: update SKILL.md"],
    "genes": ["persona", "skills"],
    "risk_tier": "T2",
    "size_bytes_changed": 1840,
    "rewrite_flagged": false
  },
  "verdict": {
    "status": "accept",
    "verification_profile": "default@1",
    "verifier_version": "verifier-2026.10.1",
    "splits": [
      {"split": "validation", "cases": 40, "mean_delta": "0.085", "ci_low": "0.031", "ci_high": "0.139",
       "newly_failing": 0, "evaluation_id": "eval_301"},
      {"split": "regression", "cases": 22, "mean_delta": "0.000", "ci_low": null, "ci_high": null,
       "newly_failing": 0, "evaluation_id": "eval_302"},
      {"split": "safety", "cases": 15, "mean_delta": "0.000", "ci_low": null, "ci_high": null,
       "newly_failing": 0, "evaluation_id": "eval_303"}
    ],
    "reasons": ["validation CI lower bound 0.031 >= min_effect 0.02", "no must-pass failures"],
    "gate": {"passed_floor": true, "risk_tier": "T2", "decision": "needs_review"}
  },
  "cost": {"tokens": 1250000, "usd": "6.40", "wall_clock_s": 2710, "rollouts": 240},
  "adoption": {
    "promoted": true,
    "promotion_id": "prm_88",
    "reviewed_by": "user:owner_7",
    "gone_back": false,
    "gone_back_by": null
  },
  "online_outcome": [
    {"window": "2026-10-09/2026-10-16", "source": "active_vs_previous",
     "metrics": {"task_success": "0.04", "user_correction_rate": "-0.02", "usd_per_episode": "0.001"},
     "episodes_compared": 812}
  ]
}
```

A governance entry has the same envelope with the run and candidate fields
set to `null` and a `subject` naming what the event is about:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "entry_id": "led_6a0",
  "kind": "governance",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "created_at": "2026-10-20T09:15:00Z",
  "subject": {"type": "promotion", "promotion_id": "prm_93", "revision": "sha256:a90b…"},  // going back to r41
  "run_id": null,
  "binding_id": null,
  "iteration": null,
  "candidate_id": null,
  "mechanism_revision": null,
  "parent_revision": null,
  "candidate_revision": null,
  "events": [
    {"seq": 1, "type": "promoted", "at": "2026-10-20T09:15:00Z",
     "actor": {"kind": "user", "id": "owner_7"},
     "reason": "Refund escalations regressed after r42",
     "links": ["promotion:prm_93", "entry:led_5c2"],
     "data": {"from": "sha256:7c1e…", "to": "sha256:a90b…", "going_back": true}}
  ]
}
```

### 2.2 LedgerEvent

Every change to an entry is an appended event. The entry's top-level fields
are the fold of its events; the events are the audit trail. An event is
never edited or removed.

```python
EventType = Literal[
    "submitted",          # candidate recorded (Evolution Run)
    "verified",           # verdict written, with verifier version (Verification)
    "gate_decided",       # gate decision and risk tier (Promotion)
    "reviewed",           # approve or reject by a human (Promotion)
    "promoted",           # active moved to this revision (Promotion)
    "gone_back",          # active later moved away by promoting an earlier revision (Promotion)
    "online_outcome",     # delayed join of live metrics (Experience / online verification)
    "holdout_audit",      # holdout score: final candidate of a run before promotion, or periodic on active (Verification)
    "revision_recorded",  # revision recorded outside a run, e.g. from a Manifest (Genome)
]

ActorKind = Literal["user", "pipeline", "platform", "strategy_run"]


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    id: str               # user id, pipeline client id, platform service name, or run id


@dataclass(frozen=True)
class LedgerEvent:
    seq: int              # per-entry, strictly increasing from 1
    type: EventType
    at: str               # RFC 3339 UTC
    actor: Actor
    reason: str           # human-readable; required for reviewed / promoted / gone_back
    links: list[str]      # evidence and evaluation ids, e.g. "eval:eval_301", "episode:ep_91"
    data: dict            # type-specific payload, schema per event type
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "seq": 3,
  "type": "gate_decided",
  "at": "2026-10-09T03:30:12Z",
  "actor": {"kind": "platform", "id": "promotion"},
  "reason": "Floor passed; verdict accept; T2 requires human review",
  "links": ["eval:eval_301", "eval:eval_302", "eval:eval_303"],
  "data": {"risk_tier": "T2", "decision": "needs_review", "review_item": "rev_q_12"}
}
```

Actor kinds cover the first-iteration callers: users (owners, tenant admins,
reviewers), pipelines, the platform's own services, and strategy runs. Bot
callers are postponed with DR-3; if they are admitted later, they add an
actor kind.

### 2.3 LedgerFilter

```python
@dataclass(frozen=True)
class LedgerFilter:
    kind: EntryKind | None = None              # None: both kinds
    run_id: str | None = None
    strategy: str | None = None                # "clawevolve/bot-evolution"
    strategy_version: str | None = None        # "2.0.0"
    parent_revision: str | None = None
    candidate_revision: str | None = None
    verdict: VerdictStatus | None = None
    promoted: bool | None = None
    gone_back: bool | None = None
    risk_tier: RiskTier | None = None
    verifier_version: str | None = None
    since: str | None = None                   # created_at >= since
    until: str | None = None
    page: int = 1                              # 1-based (09-evolution-api.md)
    page_size: int = 20                        # 1..100
```

Every filter field is optional because "no filter on this field" is the
meaning of `None`.

### 2.4 MechanismMetrics (later, level 3)

The metric definitions are owned by
[10-meta-evolution.md](10-meta-evolution.md); the ledger stores them as a
derived view, with the same field names.

```python
@dataclass(frozen=True)
class MechanismMetrics:                        # same fields as in 10-meta-evolution.md
    mechanism: str                             # mechanism revision id
    segment: dict                              # e.g. {"engine": "openclaw", "bot_type": "support"}
    verifier_version: str                      # metrics never mix verifier versions
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

## 3. What gets recorded, and when

The ledger has no writer of its own: each fact is appended by the service
that observed it, through the internal `ExperimentLedger` interface
([§11](#11-service-interface)). Appends are part of the same logical step as
the fact they record. If the append fails, the step fails and is retried;
it never reports success with a missing record (AGENTS.md: write failures
propagate).

| Fact | Appended by | Event | When |
| --- | --- | --- | --- |
| A strategy submitted a candidate | Evolution Run ([06-evolution-run.md](06-evolution-run.md)) | `submitted` (creates the experiment entry) | Before `ctx.candidates.submit` returns the candidate id |
| Verification finished | Verification ([07-verification.md](07-verification.md)) | `verified` | When the verdict leaves `pending` |
| Gate decided | Promotion ([08-promotion.md](08-promotion.md)) | `gate_decided` | After the verdict is final; or at submission, when Promotion's static floor rejects the candidate (the verdict is then `reject` without running suites, and the floor result is on the `GateDecision`) |
| A human approved or rejected | Promotion | `reviewed` | On approve / reject |
| `active` moved to the candidate | Promotion | `promoted` | On promotion |
| `active` moved back to an earlier revision | Promotion | `gone_back` on the entry being left, plus a governance entry for the promotion itself | On a going-back promotion |
| Live metrics of S2 vs S1 | Online verification over Experience ([07-verification.md](07-verification.md), [02-experience.md](02-experience.md)) | `online_outcome` | After a window of live episodes; may repeat |
| Holdout score | Verification | `holdout_audit` | Once on the final candidate of a run before promotion (when the profile requires it), and periodically on `active` ([07-verification.md](07-verification.md)) |
| Revision recorded outside a run (owner edit, Manifest import) | Genome ([01-genome.md](01-genome.md)) | `revision_recorded` in a governance entry | On recording |

**What the submitted event captures.** Because a run freezes its strategy
version, params, parent, and budget at start, the `submitted` event copies
them into the entry, together with:

- the **agent definition digests** the run used, so results are
  attributable to the exact prompts (agent definitions are versioned with
  the strategy and loaded by digest);
- the **models used** through `ctx.models` and agent sessions (every model
  call goes through the platform and is recorded in H);
- the **evidence ids** the strategy declared on the candidate;
- the cost so far from the run's budget meter, attributed to this
  candidate as described below.

**Cost attribution.** Budget is metered per run. A run that submits several
candidates spends some budget that belongs to no single candidate
(diagnosis, discarded attempts). Proposed rule: each candidate's `cost` is
the spend between the previous submission (or run start) and this
submission, plus its own verification cost; run-level spend after the last
submission is recorded on the run and appears in the ledger only through
mechanism metrics. This keeps "wasted budget" visible without double
counting.

**Submissions survive run failure.** Submissions made before a failure,
cancellation, or budget stop are kept, still verified, and recorded. A run
that is re-dispatched after a crash resubmits with the same content hash and
gets the same candidate id, so the ledger appends nothing new for a repeated
submission: `submitted` is idempotent on `(owner_id, bot_id, run_id,
candidate_id)`.

**Why every candidate, even rejected ones.** Rejection is a normal outcome,
and most candidates should fail. Recording only winners would hide exactly
the evidence level 3 needs (which operators fail, which thresholds let
regressions through, which steps burn budget without effect), and would make
false-acceptance rates impossible to compute.

## 4. Lineage and the archive

The archive is a set of read views; it stores nothing beyond the ledger and
the genome revisions:

| View | Built from | Used by |
| --- | --- | --- |
| **Lineage tree** of a bot | Genome revisions' `parents`, joined with entries by `candidate_revision` | UI lineage tree (RSI-20), parent selectors |
| **Candidate scores per split** | `verdict.splits` of each entry | Candidate report (with Promotion), Pareto selector |
| **Provenance of a candidate** | `mechanism_revision`, `evidence`, `patch` | Reviewers, level 3 |
| **Approvals and promotions** | `adoption` and the `reviewed` / `promoted` / `gone_back` events | Audit UI |
| **Live performance** | `online_outcome` | Owners; false-acceptance rate |

**Read model over Genome and Verification.** A revision's evaluation scores
are not embedded in the genome; the genome stays a pure definition and its
metadata only links evaluation ids. The archive is where scores and
revisions meet.

**Never delete.** Rejected and retired revisions are kept, never deleted
(the archive-not-delete pattern also used by Hermes Curator; `archived` is
only a revision status, [01-genome.md](01-genome.md)). Ledger entries
and their events are never deleted or rewritten. Genome blob retention is
the Genome Registry's concern: unconditional retention is acceptable for v1,
and any later sweep may only remove blobs no revision references and never
a blob reachable from a `promoted` revision
([01-genome.md](01-genome.md)). Experience retention is set per tenant
([02-experience.md](02-experience.md)); when an episode expires, the ledger
keeps its id in `evidence`, and readers see it as "expired" rather than
losing the link.

**Why an archive with lineage.** Greedy "keep the latest best" search
stalls; an archive with lineage is what lets open-ended search keep
improving (Darwin Gödel Machine found its gains collapse without the
archive). The archive is therefore the level-2 selection pool, not only an
audit log.

## 5. Visibility: who sees what

The ledger holds data that strategies must never see: per-case validation
results, and anything about holdout, regression, and safety cases. Hiding
is enforced by the platform's views, not by prompts.

| Reader | Sees |
| --- | --- |
| Owner, tenant admin, reviewer (UI, `avn`, API) | Everything for their bots, including per-split results and evidence |
| Pipelines (API) | Same as the human whose credentials they use |
| Strategies (filesystem export, future capability) | Their own and permitted bots' entries with verdict **aggregates only**: status, validation mean and interval, cost, adoption, online outcome. No per-case validation detail; no holdout, regression, or safety cases or case ids; train results as reported to the strategy at run time |
| Meta-strategies (level 3, later) | The same strategy view, plus derived mechanism metrics; never mechanism-holdout problems |

Cross-tenant reads are forbidden: an export or a selector never crosses a
tenant boundary, and cross-tenant use of experience or of strategies'
learned artefacts is forbidden by default (data handling rules in
[02-experience.md](02-experience.md)).

## 6. Audit

*Governance topic: audit.*

**Rule.** Every revision, gate decision, approval, promotion, and going
back is an append-only event with actor, actor kind, reason, and links to
evidence and evaluations. The archive views are the audit UI.

How the ledger meets the rule:

- **Append-only.** Events are written once, with a per-entry sequence
  number; the store refuses updates and deletes of event rows. Correcting
  a mistake is a new event that references the old one.
- **Complete.** Every promotion has an entry: a promotion of a candidate
  appends `promoted` to its experiment entry; any other promotion (going
  back, promoting a revision recorded by hand) creates a governance entry.
  Going back is an ordinary audited promotion of an earlier revision; there
  is no separate rollback path, and the existing service-bot rollback
  feature is not involved.
- **Attributed.** Every event carries `actor` (`user`, `pipeline`,
  `platform`, or `strategy_run`) and a reason. Reason is required for human
  decisions and promotions.
- **Linked.** Events link evaluation ids, episode ids, review items, and
  promotion ids, so the evidence for any decision is one hop away.
- **Overrides are audited.** An override (for example publishing a service
  bot past a failed verification gate, RSI-22) is a `promoted` event whose
  `data` records the override and its approver.
- **Tamper-evident (proposed).** Each event stores the digest of the
  canonical JSON (RFC 8785) of the previous event in the same entry, so a
  removed or altered event breaks the chain. This mirrors the
  content-addressing of grader code and case content on the verifier side.

Proposed additions to the audited set, beyond the governance rule as
written: changes to a bot's evolution policy (bindings) and use of kill
switches. Both are owner decisions with the same accountability need. See
[§15](#15-open-decisions).

## 7. Archive selectors (parent choice)

A binding's `parent` field says which revision a run starts from. In the
first iteration it is `active`. Later options query the archive (work item
RSI-17):

| Selector | Picks | Source of the idea |
| --- | --- | --- |
| `active` (first iteration) | The bot's `active` revision | — |
| `latest_best` | The most recent accepted revision with the best validation score under the current verifier version | Greedy baseline |
| `pareto_per_case` | A revision from the Pareto front over per-case scores (a revision that is best on at least one case) | GEPA |
| `map_elites` | The best revision in a behavioural niche (for example per gene changed, or per task category) | MAP-Elites |
| `clade_metaproductivity` | The revision whose descendants improved most, not the one with the best own score | Huxley-Gödel Machine |

Rules (proposed):

- Selectors read only the ledger and genome revisions, within one bot and
  one verifier version.
- Selectors are platform code, versioned with the platform, and chosen by
  the owner in the binding; strategies do not supply selectors.
- Selectors never read holdout results, so the holdout does not leak into
  search through parent choice.
- The selected parent is frozen on the run like every other run input, and
  the entry records it as `parent_revision`.

```python
# One value per row of the table above; a new selector is a reviewed platform change.
SelectorName = Literal["active", "latest_best", "pareto_per_case", "map_elites", "clade_metaproductivity"]

class ParentSelector(Protocol):
    name: SelectorName
    version: str                               # platform version of the selector code, e.g. "1.0.0"

    def select(self, bot: BotRef, archive: "ArchiveView", seed: int) -> str:
        """Return the revision id a new run starts from.

        Deterministic for a given archive snapshot and seed, so a
        re-dispatched run that re-selects gets the same parent.
        """
        ...
```

## 8. Mechanism metrics and verifier versions

**Mechanism metrics** are the fitness of a mechanism M, computed per
mechanism revision, per bot segment (engine, bot type), and per verifier
version from experiment entries. Their definitions (verified improvement
yield, acceptance and false-acceptance rate, regression rate, cost per
accepted improvement, descendant productivity) are owned by
[10-meta-evolution.md](10-meta-evolution.md); the ledger stores them as a
derived view (`MechanismMetrics`, §2.4) with the same field names.

Online results feed these metrics: false accepts found online lower the
mechanism's metrics, and confirmed regressions become new regression cases
through Verification.

**First iteration.** Only recording happens. Deriving mechanism metrics can
wait for level 3 ([10-meta-evolution.md](10-meta-evolution.md)), but the
fields they need (cost, verdict per split, adoption, online outcome,
mechanism revision) are recorded from day one.

**Verifier versions.** Suites, graders, profiles, and protocols are
versioned, and every verdict records the verifier version. A verifier
change invalidates comparability, so:

- every experiment entry is tagged with the verifier version of its verdict;
- mechanism metrics, selectors, and the `latest_best` comparison only
  compare entries within one verifier version;
- re-scoring old candidates under a new verifier version is a new
  evaluation and a new `verified` event, never an edit of the old one.

**Feeding level 3.** Level 3 freezes real past experiments from H into
*improvement problems* (starting revision, experience snapshot, suites,
budget) and replays stored candidates through new verification profiles or
submission filters, generalising today's
`scripts/calibrate_evolution_gates.py` and
`scripts/replay_candidate_gate.py`. Those tools and the mechanism
verification protocol are in [10-meta-evolution.md](10-meta-evolution.md);
this doc provides the history they read.

## 9. Exports

Both exports are long-running operations: `POST
/bots/{bot_id}/evolution/ledger:export` returns `202` with `{operation_id}` at
once, the start is idempotent on its `Idempotency-Key`, and status is
looked up by id with `GET /bots/{bot_id}/evolution/operations/{operation}`
([09-evolution-api.md](09-evolution-api.md)). No request is held open.

### 9.1 Filesystem export for strategies

Coding-agent and reflective strategies do better with raw history on a
filesystem than with summaries (Meta-Harness). The export writes the
strategy view of the ledger ([§5](#5-visibility-who-sees-what)) as a
directory of canonical JSON files:

```text
ledger-export/
  manifest.json                     # bot, filter, verifier versions, created_at, entry count
  lineage.json                      # revision id -> parents, seq ("r41"), status
  entries/
    led_5c2.json                    # one LedgerEntry, strategy view (aggregates only)
    led_5c9.json
  patches/
    sha256-d2f8.json                # the Genome Patch of each entry
```

Patch content is included because it is the proposer's own kind of
artefact; genome file contents are not copied and are read by digest
through the Genome API like any other content. How a strategy receives the
export inside a run (the level-3 capability `ledger.read@1`, defined in
[10-meta-evolution.md](10-meta-evolution.md)) is decided with level 3; until then, an operator can export and hand the directory to a
strategy run as params. This is proposed; the git export of genome history
(`avn genome export --format git`) is a separate, Genome-owned export.

### 9.2 Training-data export

The platform evolves the harness, not the model, but the ledger keeps the
door to weight training open at no extra cost (work item RSI-19, P6):

- Accepted vs rejected candidate pairs on the same case are **preference
  data**.
- Joined with episodes, entries give `(input, revision, output, scores,
  critiques)` records; the episode tuple itself is defined in
  [02-experience.md](02-experience.md).
- The export is **redacted** and allowed **only with explicit tenant
  opt-in**; redaction, PII policy, and retention rules are the data handling
  rules in [02-experience.md](02-experience.md). A request without opt-in is
  refused.

## 10. Storage and placement

Proposed, in `apps/evolution`:

| Table | Contents | Notes |
| --- | --- | --- |
| `ledger_entries` | One row per entry: identity, kind, bot, tenant, frozen inputs (`mechanism_revision`, `parent_revision`, `candidate_revision`, `patch`), and the current fold of its events | The fold is rebuilt from events if needed; indexed on bot, run, strategy and version, verdict, promoted, verifier version, created_at |
| `ledger_events` | One row per event, canonical JSON payload, per-entry `seq`, previous-event digest | Insert-only; no update or delete grants |
| `mechanism_metrics` (later) | Derived metrics per mechanism revision, segment, verifier version | Recomputed; not a source of truth |

- All records are JSON, stored as RFC 8785 canonical JSON; hashed content
  has no floats (decimals are strings).
- Records reference genome revisions and content by id and digest; content
  stays in the Genome Registry's content store.
- The ledger follows the constitution's layering: core logic is
  transport-agnostic; the REST API is a delivery adapter; the storage
  implementation is selected in the `apps/evolution` composition root.
- A schema for each record type (JSON Schema) and conformance tests for the
  `ExperimentLedger` interface ship with the implementation ("Experiment
  Ledger schema + mechanism metrics" is a data contract consumed by
  selectors, the meta-loop, and the UI).

## 11. Service interface

Internal interface used by the other parts of the platform. It is a Python
Protocol in `apps/evolution` core; the public API in [§12](#12-api) is a
delivery adapter over the read and export methods.

```python
class ExperimentLedger(Protocol):
    """Append-only record of improvement experiments and governance events.

    Writers append facts they observed; nothing in the ledger decides
    anything. Every append is durable before it returns; a failed append
    raises and the caller's step fails.
    """

    # --- writes (internal only) -------------------------------------------

    async def record_submission(
        self, *, bot: BotRef, run_id: str, binding_id: str, iteration: int,
        candidate_id: str, mechanism: MechanismRef, parent_revision: str,
        candidate_revision: str, evidence: list[str], patch: PatchSummary,
        cost: Cost, actor: Actor,
    ) -> str:
        """Create the experiment entry for a submitted candidate.

        Idempotent on (owner_id, bot_id, run_id, candidate_id): a repeated call returns
        the existing entry id and appends nothing.
        """
        ...

    async def append(self, entry_id: str, event: LedgerEvent) -> int:
        """Append one event to an entry and return its seq.

        Refuses events whose type does not fit the entry's state (for example
        `promoted` before `gate_decided` on an experiment entry). Idempotent
        on (entry_id, event.type, event.data["idempotency_key"]) when the
        writer supplies a key.
        """
        ...

    async def record_governance(
        self, *, bot: BotRef, subject: dict, event: LedgerEvent,
    ) -> str:
        """Create a governance entry for an audited event that has no
        experiment entry (going back, a revision recorded outside a run)."""
        ...

    # --- reads -------------------------------------------------------------

    async def get(self, bot: BotRef, entry_id: str, *, view: "LedgerView") -> LedgerEntry:
        """Return one entry as the given view sees it (full or strategy)."""
        ...

    async def query(
        self, bot: BotRef, flt: LedgerFilter, *, view: "LedgerView",
    ) -> "Page[LedgerEntry]":
        """Return one page (`flt.page`, `flt.page_size`) of entries, newest first."""
        ...

    async def archive(self, bot: BotRef, *, verifier_version: str) -> "ArchiveView":
        """Snapshot of lineage plus per-split scores, for selectors."""
        ...

    # --- exports and derived data -----------------------------------------

    async def start_export(
        self, bot: BotRef, *, format: Literal["filesystem", "training"],
        flt: LedgerFilter, idempotency_key: str, requested_by: Actor,
    ) -> str:
        """Start an export operation and return its operation id at once.

        `training` requires tenant opt-in and raises ExportNotPermitted
        otherwise.
        """
        ...

    async def mechanism_metrics(
        self, strategy: str, version: str, *, segment: dict[str, str],
        verifier_version: str,
    ) -> MechanismMetrics:
        """Derived metrics for one mechanism revision (later, level 3)."""
        ...


LedgerView = Literal["full", "strategy"]
```

## 12. API

All public endpoints are under the prefix `/openapi/v1`; paths below are
relative to it. They follow the shared conventions in
[09-evolution-api.md](09-evolution-api.md) (errors, pagination, idempotency
keys, operation lookup). Responses below show the `data` payload of the
standard envelope; see [09-evolution-api.md](09-evolution-api.md) for the
envelope, errors, pagination, and idempotency. The ledger has no public write endpoints: entries
are written only by platform services.

### GET /bots/{bot_id}/evolution/ledger

List ledger entries of a bot, newest first, with filters. Called by the UI
backend (lineage, history, audit views), the `avn` CLI, and pipelines.

Query parameters are the fields of `LedgerFilter`: `kind`, `run`,
`strategy`, `strategy_version`, `parent`, `candidate_revision`, `verdict`,
`promoted`, `gone_back`, `risk_tier`, `verifier_version`, `since`, `until`,
`page` (1-based), `page_size` (1 to 100, default 20).

Example request:

```text
GET /openapi/v1/bots/bot_123/evolution/ledger?strategy=clawevolve/bot-evolution&verdict=accept&since=2026-10-01T00:00:00Z&page=1&page_size=2
```

Example response (`200`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 3,
  "items": [
    {
      "entry_id": "led_5c2",
      "kind": "experiment",
      "created_at": "2026-10-09T02:41:07Z",
      "run_id": "run_7f3",
      "iteration": 2,
      "candidate_id": "sha256:c41e…",
      "strategy": "clawevolve/bot-evolution",
      "strategy_version": "2.0.0",
      "parent_revision": {"id": "sha256:a90b…", "seq": 41},
      "candidate_revision": {"id": "sha256:7c1e…", "seq": 42},
      "risk_tier": "T2",
      "verdict": "accept",
      "validation": {"mean_delta": "0.085", "ci_low": "0.031", "ci_high": "0.139"},
      "cost_usd": "6.40",
      "promoted": true,
      "gone_back": false
    }
  ]
}
```

List items are summaries; `GET …/ledger/{entry}` returns the full entry.

Errors: `400 invalid_filter` (unknown field, malformed time, `page_size` out
of range); `404 bot_not_found`.

### GET /bots/{bot_id}/evolution/ledger/{entry}

Return one entry with all fields and its full event history. Called by the
UI backend (candidate history, audit detail) and the CLI
(`avn evolve ledger show`).

Example request:

```text
GET /openapi/v1/bots/bot_123/evolution/ledger/led_5c2
```

Example response (`200`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "entry_id": "led_5c2",
  "kind": "experiment",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "created_at": "2026-10-09T02:41:07Z",
  "run_id": "run_7f3",
  "binding_id": "bind_01",
  "iteration": 2,
  "candidate_id": "sha256:c41e…",
  "mechanism_revision": {
    "target_kind": "bot_genome",
    "strategy": "clawevolve/bot-evolution",
    "version": "2.0.0",
    "agent_definitions": {"clawevolve-tune": "sha256:5e07…"},
    "models_used": ["platform-default"]
  },
  "parent_revision": "sha256:a90b…",
  "candidate_revision": "sha256:7c1e…",
  "evidence": ["episode:ep_91", "episode:ep_97", "finding:f_12"],
  "patch": {
    "patch_digest": "sha256:d2f8…",
    "ops": ["persona/SOUL.md: replace_section Escalation", "skills/refund-policy: update SKILL.md"],
    "genes": ["persona", "skills"],
    "risk_tier": "T2",
    "size_bytes_changed": 1840,
    "rewrite_flagged": false
  },
  "verdict": {
    "status": "accept",
    "verification_profile": "default@1",
    "verifier_version": "verifier-2026.10.1",
    "splits": [
      {"split": "validation", "cases": 40, "mean_delta": "0.085", "ci_low": "0.031", "ci_high": "0.139",
       "newly_failing": 0, "evaluation_id": "eval_301"},
      {"split": "regression", "cases": 22, "mean_delta": "0.000", "ci_low": null, "ci_high": null,
       "newly_failing": 0, "evaluation_id": "eval_302"},
      {"split": "safety", "cases": 15, "mean_delta": "0.000", "ci_low": null, "ci_high": null,
       "newly_failing": 0, "evaluation_id": "eval_303"}
    ],
    "reasons": ["validation CI lower bound 0.031 >= min_effect 0.02", "no must-pass failures"],
    "gate": {"passed_floor": true, "risk_tier": "T2", "decision": "needs_review"}
  },
  "cost": {"tokens": 1250000, "usd": "6.40", "wall_clock_s": 2710, "rollouts": 240},
  "adoption": {"promoted": true, "promotion_id": "prm_88", "reviewed_by": "user:owner_7",
               "gone_back": false, "gone_back_by": null},
  "online_outcome": [],
  "events": [
    {"seq": 1, "type": "submitted", "at": "2026-10-09T02:41:07Z",
     "actor": {"kind": "strategy_run", "id": "run_7f3"}, "reason": "Round 2 tune result",
     "links": ["episode:ep_91", "episode:ep_97", "finding:f_12"], "data": {"candidate_id": "sha256:c41e…"}},
    {"seq": 2, "type": "verified", "at": "2026-10-09T03:29:55Z",
     "actor": {"kind": "platform", "id": "verification"}, "reason": "Verdict accept",
     "links": ["eval:eval_301", "eval:eval_302", "eval:eval_303"],
     "data": {"status": "accept", "verifier_version": "verifier-2026.10.1"}},
    {"seq": 3, "type": "gate_decided", "at": "2026-10-09T03:30:12Z",
     "actor": {"kind": "platform", "id": "promotion"}, "reason": "Floor passed; verdict accept; T2 requires human review",
     "links": ["eval:eval_301", "eval:eval_302", "eval:eval_303"],
     "data": {"risk_tier": "T2", "decision": "needs_review", "review_item": "rev_q_12"}},
    {"seq": 4, "type": "reviewed", "at": "2026-10-09T08:02:40Z",
     "actor": {"kind": "user", "id": "owner_7"}, "reason": "Escalation wording matches policy",
     "links": ["review:rev_q_12"], "data": {"decision": "approve"}},
    {"seq": 5, "type": "promoted", "at": "2026-10-09T08:02:41Z",
     "actor": {"kind": "platform", "id": "promotion"}, "reason": "Approved by owner_7",
     "links": ["promotion:prm_88"], "data": {"from": "sha256:a90b…", "to": "sha256:7c1e…", "going_back": false}}
  ]
}
```

Errors: `404 entry_not_found` (also when the entry belongs to another bot).

### POST /bots/{bot_id}/evolution/ledger:export

Start an export of the bot's ledger. Called by operators and pipelines
(for example a research pipeline preparing a filesystem export for a
coding-agent strategy, or a tenant's training-data pipeline). Returns
`202` with `{operation_id}` at once; the work runs as an operation and its
status and result (a download location for the export archive) are looked
up by id with `GET /bots/{bot_id}/evolution/operations/{operation}`, defined in
[09-evolution-api.md](09-evolution-api.md).

Headers: `Idempotency-Key` (required). A retry with the same key returns
the same operation id and starts nothing new.

Example request:

```text
POST /openapi/v1/bots/bot_123/evolution/ledger:export
Idempotency-Key: ledger-export-bot_123-2026-10-09
Content-Type: application/json
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "format": "filesystem",                 // "filesystem" (strategy view) or "training" (redacted, opt-in)
  "filter": {
    "strategy": "clawevolve/bot-evolution",
    "since": "2026-07-01T00:00:00Z",
    "verifier_version": "verifier-2026.10.1"
  }
}
```

Example response (`202`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"operation_id": "op_19a"}     // look up with GET /bots/bot_123/evolution/operations/op_19a
```

Errors: `400 invalid_filter`; `400 missing_idempotency_key`;
`403 export_not_permitted` (`training` format without tenant opt-in);
`409 idempotency_key_conflict` (same key, different body);
`404 bot_not_found`.

### Internal: ledger writes

Not public. Platform services call the `ExperimentLedger` interface
([§11](#11-service-interface)) in process inside `apps/evolution`.
Promotion runs in Backend, so it appends through an internal service API
of `apps/evolution` with the same operations (`record_submission` is not
exposed to Backend; `append` and `record_governance` are). Strategies never
write to the ledger directly: their submissions reach it through
`ctx.candidates.submit`, which the Evolution Run service records.

## 13. Examples

**A pipeline reviews last week's accepted candidates of one strategy.**

```python
from avernet_evolution import Client

c = Client.from_env()
page = c.ledger.list(bot_id="bot_123", strategy="clawevolve/bot-evolution",
                     verdict="accept", since="2026-10-02T00:00:00Z")
for item in page.items:
    entry = c.ledger.get(bot_id="bot_123", entry=item.entry_id)
    print(item.candidate_revision.seq, entry.verdict.status,
          entry.cost.usd, entry.adoption.promoted)
```

**The UI backend draws the lineage tree.** Revisions come from the Genome
API; the ledger adds what happened to each candidate.

```python
def lineage_tree(c, bot_id: str) -> dict[str, dict]:
    nodes: dict[str, dict] = {}
    page_no = 1
    while True:
        page = c.ledger.list(bot_id=bot_id, kind="experiment", page=page_no, page_size=100)
        for e in page.items:
            nodes[e.candidate_revision.id] = {
                "seq": e.candidate_revision.seq,
                "parent": e.parent_revision.id,
                "verdict": e.verdict,
                "promoted": e.promoted,
                "gone_back": e.gone_back,
            }
        if page_no * 100 >= page.total:
            return nodes
        page_no += 1
```

**The Evolution Run service records a submission** (platform code, inside
`ctx.candidates.submit`).

```python
async def submit(self, run: Run, cand: Candidate) -> str:
    candidate_id = content_hash(cand.patch)
    revision = await self.genome.record_candidate(run.bot, base=cand.patch.base, patch=cand.patch)
    await self.ledger.record_submission(
        bot=run.bot, run_id=run.run_id, binding_id=run.binding_id,
        iteration=await self.next_iteration(run, candidate_id),
        candidate_id=candidate_id, mechanism=run.mechanism_ref(),
        parent_revision=cand.patch.base, candidate_revision=revision.id,
        evidence=cand.evidence, patch=summarize(cand.patch),
        cost=self.budget.spent_since_last_submission(run),
        actor=Actor(kind="strategy_run", id=run.run_id),
    )
    await self.verification.enqueue(candidate_id, run.verification_profile)
    return candidate_id   # same id on a retried or re-dispatched submission
```

**An operator starts a filesystem export and waits for it** (CLI, proposed
command names).

```text
avn evolve ledger export --bot bot_123 --format filesystem \
    --strategy clawevolve/bot-evolution --since 2026-07-01 \
    --idempotency-key ledger-export-bot_123-2026-10-09 --wait --output json
```

`--wait` only repeats the status lookup by operation id; it never holds a
request open.

**Going back is visible in the ledger.** After the owner promotes r41 again,
entry `led_5c2` (r42) gains a `gone_back` event, and a governance entry
records the promotion of r41 with its reason. False-acceptance rate for
`clawevolve/bot-evolution@2.0.0` counts `led_5c2` from then on.

## 14. Interactions

| Other part | Direction | What flows |
| --- | --- | --- |
| [01-genome.md](01-genome.md) Genome | Genome → Ledger | Revision ids, parents, `seq`, status; `revision_recorded` events for revisions recorded outside a run |
| [01-genome.md](01-genome.md) Genome | Ledger → Genome | Content reads by digest for exports (patches); never writes |
| [02-experience.md](02-experience.md) Experience | Experience → Ledger | Episode ids as evidence; live episodes per revision for online outcomes; data handling rules for training export |
| [03-strategy.md](03-strategy.md) Strategy | Strategy → Ledger (via Evolution Run) | Strategy id and version, agent definition digests, models used, evidence ids, candidate submissions |
| [03-strategy.md](03-strategy.md) Strategy | Ledger → Strategy | Filesystem export (strategy view, aggregates only) |
| [04-default-strategies.md](04-default-strategies.md) Default strategies | Ledger → Strategy | Raw history for ClawEvolve's tune prompt (it already carries evolution history) via the filesystem export |
| [06-evolution-run.md](06-evolution-run.md) Evolution Run | Run → Ledger | `submitted` events, frozen run inputs, cost per candidate |
| [06-evolution-run.md](06-evolution-run.md) Evolution Run | Ledger → Run | Parent selection for bindings whose `parent` is a selector (later) |
| [07-verification.md](07-verification.md) Verification | Verification → Ledger | `verified` events with per-split results and verifier version; `holdout_audit`; online outcomes |
| [07-verification.md](07-verification.md) Verification | Ledger → humans | Suggestions for verifier changes, never automatic edits |
| [08-promotion.md](08-promotion.md) Promotion | Promotion → Ledger | `gate_decided`, `reviewed`, `promoted`, `gone_back`; governance entries |
| [08-promotion.md](08-promotion.md) Promotion | Ledger → Promotion | History for the candidate report (prior attempts, lineage) |
| [09-evolution-api.md](09-evolution-api.md) Evolution API | API ↔ Ledger | Public read and export endpoints; shared conventions; operation lookup |
| [10-meta-evolution.md](10-meta-evolution.md) Meta-evolution | Ledger → Meta | Experiments for improvement problems, mechanism metrics, replay input (later) |
| UI (`apps/frontend-nextgen`, AgentEvolve UI interim) | Ledger → UI | Run history, candidate history, lineage tree, audit trail (RSI-20) |

## 15. Open decisions

| ID | Decision | Options / current proposal |
| --- | --- | --- |
| L-1 | Ledger model: explicit stored records vs pure read model | Earlier text described H both as "a read model over Genome + Verification" and as an explicit, queryable record. Proposed: append-only events are stored (audit requires it); entries are their fold; archive views are read models over entries and genome revisions |
| L-2 | Cost attribution per candidate | Proposed: spend since the previous submission plus own verification cost; residual run spend only in mechanism metrics. Alternative: attribute all run spend evenly to its candidates |
| L-3 | Policy changes and kill switches in the audit trail | Proposed: record both as governance entries. The governance audit rule names revisions, gate decisions, approvals, promotions, and going back only |
| L-4 | Tamper evidence | Proposed: per-entry hash chain over canonical JSON. Alternative: rely on insert-only grants |
| L-5 | How strategies receive the filesystem export inside a run | The level-3 capability `ledger.read@1` ([10-meta-evolution.md](10-meta-evolution.md)) vs an operator-produced export passed as params; its contract is not fixed |
| L-6 | Export operation status lookup path | Resolved: `GET /bots/{bot_id}/evolution/operations/{operation}` ([09-evolution-api.md](09-evolution-api.md)); the export's `202` returns `{operation_id}` |
| L-7 | Selector set and niche definitions for `map_elites` | Fixed in RSI-17 when a second selector is actually needed |
| L-8 | Experience expiry vs evidence links | Proposed: keep ids, mark expired; never block expiry because the ledger references an episode |
| D-1 | Module placement of the control plane | Recommended: `apps/evolution` (see [design.md](design.md)) |
