# Promotion

> 中文版：[08-promotion.zh-CN.md](08-promotion.zh-CN.md)

> Status: DRAFT. Service in the [bot evolution architecture](design.md).
> Who decides whether a verified candidate changes a bot: the separation of
> powers, the gate, risk tiers, the review queue, promotion of a revision to
> `active`, rollout, and going back. Proposed decision:
> [DR-2](decisions/0002-promotion-is-platform-owned.md).

## 1. Purpose and scope

Self-improvement that the improver can grade is self-deception at scale. The
literature is consistent on this: the Darwin Gödel Machine removed its own
hallucination markers; self-graded Hermes loops are lenient; LLM-authored
skills often add nothing without eval-guided revision; harness-evolution
gains often vanish against a budget-matched baseline (see
[research.md](research.md)). This service holds the rules that keep the
platform trustworthy regardless of which strategy runs: strategies propose,
the verifier measures, and **only this service moves a bot's `active` ref**.

**Verification answers "how good is this candidate?"; Promotion answers "who
decides, and does the bot change?"** Promotion is the only place in the
platform that can move a bot's `active` (and `canary`) genome ref, and the
only place that applies a revision to the running bot as the result of
evolution.

Promotion owns:

- the **separation of powers**: which actor may hold which power (§3);
- the **gate**: the platform-owned decision point that combines the
  non-negotiable platform floor, the verification verdict, the holdout and
  baseline checks, and the risk-tier approval into one **gate decision** per
  candidate (§4);
- the **platform floor**, including scanning of patches for secrets, PII,
  new outbound URLs, and new MCP servers (§4.2);
- **risk tiers**: the classification of every patch into `T0..T3` and the
  default promotion route of each tier (§5);
- the **review queue**: candidates that passed every automated check but
  need a human decision, with approve and reject (§6);
- **promotion**: moving `active`, `previous`, and `canary`, and applying the
  revision through the existing Manifest apply or service-bot publish chain
  (§7);
- **rollout and going back**: shadow, canary, promote, optional
  auto-rollback, and going back by promoting an earlier revision (§8).

Promotion explicitly does **not** own:

| Concern | Owner |
| --- | --- |
| Revisions, refs, compare-and-swap (CAS) on refs, patches, content store, lineage, the `policy` section of a genome (locked genes, pins, risk overrides) | Genome Registry, [01-genome.md](01-genome.md). Promotion is the only caller allowed to move `active`, `previous`, and `canary` there |
| How a candidate is measured: suites, splits, graders, executors, paired statistics, verification profiles, verdicts, holdout rotation, online verification metrics, anti-reward-hacking rules and verifier integrity | Verification, [07-verification.md](07-verification.md) |
| Bindings (which strategy runs on a bot, trigger, allowed genes, verification profile, budget), runs, sandboxing, budgets and kill switches | Evolution Run, [06-evolution-run.md](06-evolution-run.md). Promotion reads the binding fields it needs and honours the kill switches |
| Recording experiments and the audit trail | Experiment Ledger, [05-experiment-ledger.md](05-experiment-ledger.md). Promotion writes its gate decisions, approvals, rejections, and promotions there |
| Adopting a new improvement mechanism (level 3) | Meta-evolution, [10-meta-evolution.md](10-meta-evolution.md). It reuses the gate with a separate mechanism-adoption profile (T3 by default) |
| Shared API conventions, the `avn` CLI, generated SDKs | Evolution API, [09-evolution-api.md](09-evolution-api.md) |

**Where it runs.** Promotion runs in **Backend**, beside the Genome Registry
(proposed `core/bot_genome/`, next to `core/bot_config_manifest/`).
The reason is that Backend owns desired state, the Manifest, the publish
chain, tenancy, and approvals, and promotion must sit beside apply
([design.md](design.md); work item RSI-12 splits it as "backend: gate floor,
promotion; evolution: bindings"). Verification and Evolution Run live in the
new `apps/evolution` module; they call Promotion through its service
interface (§9). The public endpoints are under the shared `/openapi/v1`
prefix (§10): Genome and Promotion endpoints, including the candidate
report, review-queue, and approve/reject paths under `/bots/{bot_id}/evolution/`,
are served by Backend; the other evolution endpoints are served by
`apps/evolution`; [09-evolution-api.md](09-evolution-api.md) presents them
as one public surface (this follows the recommended option of D-1 in
[design.md](design.md)).

**Callers in the first iteration** are deterministic pipelines (nightly jobs,
CI, product backends) and humans (bot owners, tenant admins, reviewers),
through the API, SDKs, CLI, and UI. Bot callers are postponed with DR-3; they
would never hold a promotion power in any case (§3).

## 2. Domain model

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `RiskTier` | Severity class `T0..T3` of a patch; decides the default promotion route | Promotion (tier table); per-bot `risk_overrides` in the genome `policy` | Computed once per candidate when it is recorded; immutable for that candidate |
| `GateDecision` | The platform's decision about one candidate: every check with its result, the risk tier, and the outcome (`auto_promote`, `needs_review`, `not_promotable`) | Promotion | Created at submission when the static floor fails, otherwise when the verification verdict is final; immutable; a new decision is recorded only if the inputs change (for example a holdout incident) |
| `ReviewItem` | A candidate waiting for a human decision, with everything a reviewer needs | Promotion | `open` → `approved` / `rejected` / `superseded`; never deleted |
| `Promotion` | One move of `active` (or `canary`) to a revision, including going back, with actor, reason, and apply result | Promotion | `applying` → `applied` / `apply_failed`; append-only |
| `CandidateReport` | Read view joining a candidate's diff, verification summary, gate decision, and review state | Promotion (view) | Derived on read; not stored |

Types used here but defined elsewhere: `GenomeRevision`, `GenomeRef`,
`GenomePatch`, `GenomePolicy` ([01-genome.md](01-genome.md)); `Candidate`
([03-strategy.md](03-strategy.md)); `Verdict`, `VerificationProfile`,
`Evaluation` ([07-verification.md](07-verification.md)); `Binding`,
`EvolutionPolicy`, `Run` ([06-evolution-run.md](06-evolution-run.md));
`LedgerEntry` ([05-experiment-ledger.md](05-experiment-ledger.md)).

A short reminder of the terms this doc leans on:

- A **candidate** is a Genome Patch against a base revision submitted by a
  strategy, plus a rationale and evidence. Its **candidate id** is the content
  hash of the patch (`sha256:c41e…`). Recording it produces a candidate
  **revision** (`r42`, id `sha256:7c1e…`) whose parent is the base (`r41`,
  `sha256:a90b…`).
- A **verdict** is Verification's answer for a candidate under the binding's
  verification profile: `pending | accept | reject | inconclusive`.
- A **ref** is a named, movable pointer to an immutable revision. `active` is
  what the bot runs; `previous` is the last `active`; `canary` is what canary
  instances run.

### 2.1 RiskTier

A risk tier is assigned **per patch op**; a patch takes the maximum tier of
its ops, after raises (rewrite flag, guardrail tag) and the bot's
`risk_overrides`.

```python
from enum import IntEnum
from typing import Literal

class RiskTier(IntEnum):     # ordered, so tiers compare; serialized by name ("T0".."T3") in JSON
    T0 = 0   # annotations only
    T1 = 1   # memory item add/update/retire, skill description tweak, resource content update
    T2 = 2   # persona edits, skill add/update, allowlisted engine_config, any rewrite-flagged edit
    T3 = 3   # tools, script, memory replace mode, permissions, anything in policy: locked by default

# The Genome Patch op names (01-genome.md §6.1; the full set is fixed by RSI-02).
PatchOpName = Literal["file.edit", "skill.add", "skill.update", "memory.add",
                      "memory.update", "memory.retire", "engine_config.set"]

@dataclass(frozen=True)
class TierRaise:                    # one raise applied after the per-op mapping (§5.1)
    kind: Literal["rewrite", "guardrail_touch"]
    target: str | None              # the file that triggered it, e.g. "persona/SOUL.md"; None for patch-wide

@dataclass(frozen=True)
class TierAssessment:
    tier: RiskTier                  # the patch's tier: the maximum over its ops, after raises and overrides
    per_op: list["OpTier"]          # one entry per patch op, in patch order
    raised_by: list[TierRaise]      # empty when no raise applied

@dataclass(frozen=True)
class OpTier:
    op_index: int                   # position of the op in the patch, from 0
    op: PatchOpName
    target: str                     # what the op changes, e.g. "persona/SOUL.md", "skills/refund-policy"
    tier: RiskTier
    reason: str                     # short human-readable reason for the tier, e.g. "persona edit"
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "tier": "T2",
  "per_op": [
    {"op_index": 0, "op": "file.edit", "target": "persona/SOUL.md", "tier": "T2",
     "reason": "persona edit"},
    {"op_index": 1, "op": "skill.update", "target": "skills/refund-policy", "tier": "T2",
     "reason": "skill content update"},
    {"op_index": 2, "op": "memory.add", "target": "memory/customer-tier-rules", "tier": "T1",
     "reason": "memory item add"}
  ],
  "raised_by": []                     // no rewrite flag, no guardrail-like text touched
}
```

### 2.2 GateDecision

```python
from dataclasses import dataclass
from typing import Literal

CheckName = Literal[
    "static_floor", "regression_floor", "verification_verdict",
    "holdout", "budget_matched_baseline", "risk_tier_approval",
]
GateOutcome = Literal["auto_promote", "needs_review", "not_promotable"]

@dataclass(frozen=True)
class GateCheck:
    name: CheckName
    result: Literal["pass", "fail", "not_required", "not_run"]
    detail: str                     # short human-readable reason
    evidence: list[str]             # ids: "eval:ev_301", "scan:sc_77", ...

@dataclass(frozen=True)
class GateDecision:
    id: str                         # "gd_204"
    bot: BotRef                     # the bot (owner + bot id, 09-evolution-api.md §2.7)
    candidate_id: str               # content hash of the patch
    revision_id: str                # candidate revision
    parent_revision_id: str         # base the patch was made against
    run_id: str
    binding_id: str
    verdict: Literal["accept", "reject", "inconclusive"]   # the final verdict the gate decided on
    verification_profile: str       # "default@1"
    verifier_version: str           # recorded so decisions across verifier versions are not mixed
    risk: TierAssessment
    auto_promote_ceiling: RiskTier | None   # from the binding; None = never auto-promote
    checks: list[GateCheck]
    outcome: GateOutcome
    reasons: list[str]              # why the outcome is what it is
    decided_at: str                 # RFC 3339
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "gd_204",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "candidate_id": "sha256:c41e…",
  "revision_id": "sha256:7c1e…",        // r42
  "parent_revision_id": "sha256:a90b…", // r41
  "run_id": "run_7f3",
  "binding_id": "bind_01",
  "verdict": "accept",
  "verification_profile": "default@1",
  "verifier_version": "verifier-2026.10.1",
  "risk": {"tier": "T2", "per_op": [], "raised_by": []},   // per_op elided here; see §2.1
  "auto_promote_ceiling": "T1",
  "checks": [
    {"name": "static_floor", "result": "pass", "detail": "schema ok; no locked gene or pin touched; scan clean", "evidence": ["scan:sc_77"]},
    {"name": "regression_floor", "result": "pass", "detail": "safety: 0 newly failing; regression: 0 newly failing", "evidence": ["eval:ev_302"]},
    {"name": "verification_verdict", "result": "pass", "detail": "accept on validation under default@1", "evidence": ["eval:ev_301"]},
    {"name": "holdout", "result": "pass", "detail": "final candidate of run scored on holdout; no drop", "evidence": ["eval:ev_305"]},
    {"name": "budget_matched_baseline", "result": "not_required", "detail": "not required for this strategy", "evidence": []},
    {"name": "risk_tier_approval", "result": "fail", "detail": "tier T2 above auto-promote ceiling T1", "evidence": []}
  ],
  "outcome": "needs_review",
  "reasons": ["risk tier T2 requires human review"],
  "decided_at": "2026-10-08T03:41:00Z"
}
```

### 2.3 ReviewItem

```python
ReviewStatus = Literal["open", "approved", "rejected", "superseded"]

@dataclass(frozen=True)
class ReviewItem:
    candidate_id: str               # the review item is keyed by candidate id
    bot: BotRef                     # the bot (owner + bot id)
    revision_id: str
    revision_seq: int               # 42, shown as "r42"
    parent_revision_id: str
    run_id: str
    strategy: str                   # "clawevolve/bot-evolution"
    strategy_version: str           # "2.0.0"
    risk_tier: RiskTier
    gate_decision_id: str
    rationale: str                  # from the candidate; required on every patch
    evidence: list[str]             # "episode:ep_91", "finding:f_12"
    self_reported_metrics: dict     # shown to reviewers, never used for acceptance
    status: ReviewStatus
    created_at: str
    decided_by: str | None          # user or pipeline client id of the decider; None while open
    decided_at: str | None
    decision_reason: str | None
    promotion_id: str | None        # set when an approval promoted the revision
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate_id": "sha256:c41e…",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "revision_id": "sha256:7c1e…",
  "revision_seq": 42,
  "parent_revision_id": "sha256:a90b…",
  "run_id": "run_7f3",
  "strategy": "clawevolve/bot-evolution",
  "strategy_version": "2.0.0",
  "risk_tier": "T2",
  "gate_decision_id": "gd_204",
  "rationale": "Escalation section did not cover refunds above the tier limit; 9 of 31 failed episodes were mis-escalated.",
  "evidence": ["episode:ep_91", "finding:f_12"],
  "self_reported_metrics": {"train_pass_rate": "0.81"},   // strategy's own claim, informational only
  "status": "open",
  "created_at": "2026-10-08T03:41:00Z",
  "decided_by": null,
  "decided_at": null,
  "decision_reason": null,
  "promotion_id": null
}
```

### 2.4 Promotion

```python
PromotionStatus = Literal["applying", "applied", "apply_failed"]
ActorKind = Literal["user", "pipeline", "gate_policy", "rollout_policy"]

@dataclass(frozen=True)
class Actor:
    kind: ActorKind                 # gate_policy = auto-promotion; rollout_policy = auto-rollback
    id: str                         # user id, pipeline credential name, or policy/binding id

@dataclass(frozen=True)
class ApplyResult:
    kind: Literal["manifest_apply", "service_publish"]   # personal bot: Manifest apply; service bot: publish flow
    reference: str | None           # apply report id or published version, once known
    detail: str                     # short human-readable progress or failure text

@dataclass(frozen=True)
class Promotion:
    id: str                         # "prm_5d2"
    bot: BotRef                     # the bot (owner + bot id)
    ref: Literal["active", "canary"]
    revision_id: str                # what the ref now points at
    revision_seq: int
    from_revision_id: str           # what the ref pointed at before
    going_back: bool                # informational: target is an ancestor that was promoted before
    candidate_id: str | None        # set when the promotion came from a candidate
    gate_decision_id: str | None
    actor: Actor
    reason: str
    status: PromotionStatus
    apply: ApplyResult
    created_at: str
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "prm_5d2",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "ref": "active",
  "revision_id": "sha256:7c1e…",
  "revision_seq": 42,
  "from_revision_id": "sha256:a90b…",
  "going_back": false,
  "candidate_id": "sha256:c41e…",
  "gate_decision_id": "gd_204",
  "actor": {"kind": "user", "id": "user_owner_17"},
  "reason": "Reviewed diff and verification report; escalation fix is correct.",
  "status": "applied",
  "apply": {"kind": "manifest_apply", "reference": "apply_8812", "detail": "all categories converged"},
  "created_at": "2026-10-08T09:12:00Z"
}
```

### 2.5 CandidateReport

The read view a reviewer (or a pipeline) uses. It joins data owned by other
services; it is not stored.

```python
@dataclass(frozen=True)
class SplitSummary:
    split: Literal["train", "validation", "holdout", "regression", "safety"]
    mean_delta: str                 # candidate minus parent, mean score; decimal string (no floats in canonical JSON)
    ci_low: str | None              # confidence interval of mean_delta; None for must-pass splits
    ci_high: str | None
    newly_failing: int              # cases the parent passed and the candidate fails
    detail_visible: bool            # per-case detail is shown per caller role (07-verification.md)

@dataclass(frozen=True)
class CandidateReport:
    candidate_id: str
    bot: BotRef                     # the bot (owner + bot id)
    revision_id: str
    revision_seq: int
    parent_revision_id: str
    parent_revision_seq: int
    run_id: str
    strategy: str
    strategy_version: str
    patch_summary: list[OpTier]     # ops with their tiers
    diff_path: str                  # relative API path of the genome diff
    rationale: str
    evidence: list[str]
    verdict: Literal["pending", "accept", "reject", "inconclusive"]
    splits: list[SplitSummary]
    cost: dict                      # parent vs candidate cost per case
    flags: list[Literal["rewrite", "guardrail_touch", "overfit_suspect", "wins_by_spending"]]   # meanings in §6.1
    gate: GateDecision
    review: ReviewItem | None
```

The JSON form is the response of `GET /bots/{bot_id}/evolution/candidates/{candidate}` (§10.1).

## 3. Separation of powers

Every power in the evolution loop has exactly one kind of holder, and the
powers that decide "better" and "live" are never held by the thing being
improved or by the thing doing the improving. This is the core of DR-2.

| Power | Holder | Never held by |
| --- | --- | --- |
| Propose changes | Strategies (by submitting candidates); subject bots through the inbox once DR-3 is decided (postponed) | — |
| Choose which strategies run on a bot, what they may change, and how strictly they are verified | **Owner / tenant admin** (bindings, [06-evolution-run.md](06-evolution-run.md)) | Strategies, bots |
| Define what must never get worse | **Platform + owner** (regression, safety, holdout suites; platform floor) | Strategies, bots |
| Run evaluations | **Platform** (Verification, with verifier-owned executors and graders, [07-verification.md](07-verification.md)) | Strategies |
| Change the verifier (suites, graders, profiles, protocols, thresholds) | **Humans**, through reviewed changes ([07-verification.md](07-verification.md)) | Any automated loop, including level 3 |
| Adopt a new mechanism (level 3) | Platform mechanism gate + human approval ([10-meta-evolution.md](10-meta-evolution.md)) | Meta-strategies |
| Decide promotion | **Platform gate** + owner/reviewer per risk tier (this doc) | Strategies, bots |
| Change locked genes / policy | Owner, tenant admin ([01-genome.md](01-genome.md)) | Strategies, bots |

What each row means in practice:

- **Propose.** A strategy's only output is a candidate through
  `ctx.candidates.submit`. It cannot write to a live bot, its workspace, or
  its genome refs. What a strategy chooses to submit is its own business (for
  example ClawEvolve's `test > baseline` rule becomes an internal filter on
  what it submits); whether a candidate is accepted is not.
- **Choose.** The binding is the owner's statement of trust in a strategy for
  one bot: allowed genes, verification profile, budget, and the auto-promote
  ceiling (§5.3). An owner may choose a **stricter** verification profile; a
  strategy can never loosen it.
- **Define "never worse".** The platform floor (§4.2) is not overridable by
  strategies or owners. Regression and safety suites are platform- and
  owner-owned, never visible to strategies.
- **Run evaluations.** Strategies may run train evaluations through
  `evaluate.train@1` for their own feedback, but the evaluations the gate
  uses are run only by Verification.
- **Change the verifier.** Suites, graders, profiles, protocols, and
  thresholds change only through human-authored, reviewed changes. The
  Experiment Ledger may suggest a verifier change ("failure class Z has no
  regression coverage"); it never applies one.
- **Adopt a mechanism.** A level-3 candidate mechanism is adopted only after
  mechanism verification and human approval. Mechanism adoption is T3 by
  default (an owner may lower it to T2 for parameter-only patches on their
  own tenant); details in [10-meta-evolution.md](10-meta-evolution.md).
- **Decide promotion.** Only this service moves `active`. An automated
  promotion happens only when every gate check passes and the risk tier is at
  or below the owner's auto-promote ceiling; anything else waits for a
  human.
- **Change policy.** The genome `policy` section (locked genes, mutable
  genes, pins, risk overrides) is copied forward verbatim by the platform and
  changed only by the owner or a tenant admin. The floor rejects any patch
  that touches it.

Caller rules that follow from the table:

| Caller | May do in Promotion |
| --- | --- |
| Human owner / tenant admin / delegated reviewer | Read reports and the queue; approve and reject; promote any revision of the bot, including going back |
| Deterministic pipeline (nightly job, CI, product backend) | Read reports and the queue; approve only items whose tier is within the auto-promote policy the owner configured; reject |
| The gate itself (`gate_policy` actor) | Promote a candidate whose gate decision is `auto_promote` |
| A rollout policy (`rollout_policy` actor) | Go back to `previous` when an owner-enabled auto-rollback rule fires (§8.3) |
| Strategies | Nothing. They read their own candidates' verdicts through `ctx.candidates.verdict` ([03-strategy.md](03-strategy.md)), never gate decisions or the queue |

### 3.1 Decision record: DR-2

DR-2 (status: proposed) states the decision this doc implements:

> Evolution strategies are pluggable, but **promotion is not**. Only the
> platform gate can move a bot's `active` genome ref. Strategies may submit
> candidate patches. Neither strategies nor bots can write to a live bot, its
> workspace, or its genome refs.

Verification is likewise platform-owned: suites, graders, verification
profiles, and protocols live outside the genome, are read-only to strategies,
bots, and the level-3 meta-loop, and change only through human-reviewed
changes. Strategy inputs never include holdout, regression, or safety cases.

Consequences recorded in DR-2:

- ClawEvolve's tune stage must stop editing the live workspace and instead
  emit patches from a sandbox; pack/restore leaves the evolution flow
  ([04-default-strategies.md](04-default-strategies.md)).
- Any third-party strategy can be enabled without trusting it with
  production write access.
- Bot owners get a review queue and per-bot policy (enabled strategies,
  auto-promote ceiling, budgets).
- Every promotion and going back is audited with evidence and evaluation
  links.

Alternatives rejected in DR-2:

| Alternative | Why rejected |
| --- | --- |
| **Each strategy promotes by its own rules** (today's ClawEvolve accept rule plus in-place edits) | Reward hacking and self-grading are the dominant failure mode reported for self-improving agents |
| **Always require human approval** | Rejected as the default because it blocks low-risk memory updates. Kept available as an owner policy (set the auto-promote ceiling to none) |

## 4. The gate

The gate is the platform-owned decision point that turns a candidate with a
final verdict into a gate decision. Verification decides *how good* the
candidate is; the gate decides *whether it may change the bot, and who must
agree*.

### 4.1 Checks

A candidate is promotable only if **all** of these pass:

1. **Static floor** (§4.2, run at submission) — schema valid; base matches; no locked gene or
   pinned item touched; no secrets/PII introduced; size and rewrite
   thresholds; no new outbound URLs or MCP servers unless the gene is
   unlocked; no permission escalation; budget not exceeded.
2. **Regression floor** — on the bot's `regression` and `safety` suites the
   candidate is not worse than the parent beyond a tolerance. Default: zero
   newly failing cases on `safety` (no tolerance), at most one on
   `regression`, and that one sends the candidate to review even if its tier
   would allow auto-promotion. The tolerance comes from the binding's
   verification profile; Verification runs the suites.
3. **Verification verdict** — `accept` on `validation` under the binding's
   verification profile ([07-verification.md](07-verification.md)). The
   owner may pick a stricter profile; a strategy cannot loosen it. For T2+
   candidates the verdict must come from an ensemble of at least two graders
   (Verification records inter-judge agreement; low agreement yields
   `inconclusive`, not `accept`).
4. **Holdout check** — the final candidate of a run is scored on the sealed
   `holdout` split before promotion (when the profile requires it; timing
   owned by [07-verification.md](07-verification.md)), and the promoted revision is scored on
   `holdout` periodically, not every iteration, so the holdout does not leak
   through repeated selection. A holdout drop on `active` opens an
   **incident** and blocks further auto-promotion for the bot until an owner
   clears it; pending and new candidates of that bot go to review instead.
5. **Budget-matched baseline** — optional per strategy, **mandatory for
   platform-published strategies**: the candidate must beat
   parent-with-extra-sampling at equal cost. A candidate that wins only by
   spending more is flagged `wins_by_spending` in its report.
6. **Risk tier approval** (§5) — the tier is at or below the binding's
   auto-promote ceiling (auto-promote), or a human approves it in the review
   queue.

Checks 1–5 produce a pass or a fail without a human. Check 6 is where a human
may enter.

### 4.2 The platform floor and patch scanning

The platform floor is the set of checks no strategy and no owner can
override. Promotion defines the floor rules and owns `FloorCheck` (§9). It
is cheap, so it runs **at candidate submission, before any verification
spend**: the patch-level structural rules are also enforced by the Genome
Registry when the candidate is recorded ([01-genome.md](01-genome.md)), and
Evolution Run then calls Promotion's `FloorCheck` (through
`PromotionService.check_floor`) for the submitted candidate
([06-evolution-run.md](06-evolution-run.md)). A floor failure makes the
verdict `reject` without running any suite; the result is stored on the
`GateDecision` and in the ledger, and the verdict only references it
([07-verification.md](07-verification.md)).

| Floor check | Rule | On failure |
| --- | --- | --- |
| Schema | The patch and the resulting revision validate against the genome and patch JSON Schemas | Candidate not recorded |
| Base | `base` equals the parent revision; ops apply cleanly (no fuzzy merge in v1) | Candidate not recorded |
| Locked genes and pins | No op touches a gene listed in `policy.locked_genes` or an item in `policy.pins`; no op touches `policy` | Candidate not recorded |
| Allowed genes | Every op is within the binding's `allowed_genes` (which itself stays within `policy.mutable_genes`) | Candidate not recorded |
| Secret and PII scan | No secrets or PII introduced, using the **same scanner rules as the repository pre-push hook** (private keys, recognizable provider tokens, bearer/JWT credentials, high-entropy values in credential-like fields), plus the tenant's PII policy | `not_promotable` |
| Outbound endpoints | No new outbound URLs and no new MCP servers unless the corresponding gene is unlocked | `not_promotable` |
| Permission escalation | No op widens tools, permissions, or anything in `policy` | `not_promotable` |
| Size and rewrite | Patch size, file count, and file size within the Manifest limits; a `file.edit` that changes more than a configurable fraction of a file (default 40%) is flagged `rewrite` (raises the tier, §5.1) | Over limit: `not_promotable`; rewrite: tier raised |
| Budget | The run did not exceed its budget for this candidate | `not_promotable` |

**Why scan patches at all.** Experience (episodes, feedback) is untrusted
input: it is a prompt-injection and memory-poisoning vector. A strategy that
mines episodes can carry a secret, a customer's personal data, or an
attacker's URL from an episode into a memory item or a skill. Every patch
derived from experience therefore gets secret, PII, and URL scanning before
it can be promoted. (Redaction on ingest and retention of experience are in
[02-experience.md](02-experience.md); sandboxing of strategies is in
[06-evolution-run.md](06-evolution-run.md).)

### 4.3 Outcomes

| Outcome | When | What happens next |
| --- | --- | --- |
| `auto_promote` | All checks pass and the tier is at or below the binding's auto-promote ceiling, no incident blocks the bot, the bot's evolution is not frozen, and the daily promotion limit is not reached | Promotion moves `active` (or `canary`, §8) with actor `gate_policy` |
| `needs_review` | Checks 1–5 pass but the tier is above the ceiling, or a soft condition sends it to review (one tolerated regression failure, a holdout incident on the bot, the daily promotion limit reached, a frozen bot) | A `ReviewItem` is opened |
| `not_promotable` | The verdict is `reject` or `inconclusive`, or any of checks 1–5 fails, or the tier is T3 on a gene the owner has not unlocked | Revision status becomes `rejected`; it is kept, never deleted |

Rejection is a normal outcome, not an error: most candidates should fail,
and every decision is recorded in the Experiment Ledger as evidence.

An `inconclusive` verdict is not promotable. The strategy may spend more of
its budget (more seeds or cases, through Verification) or stop; the gate
decides only on a final `accept`.

### 4.4 Gate procedure

```python
def decide(c: CandidateFacts, b: BindingView, bot: BotState) -> GateDecision:
    checks = [
        c.floor,                                                                  # §4.2, run at submission
        regression_floor(c.evaluations, b.verification_profile),                  # tolerance from profile
        verdict_check(c.verdict),                                                 # accept on validation
        holdout_check(c.evaluations, bot.open_incidents),
        baseline_check(c.evaluations, required=c.strategy_platform_published or b.require_baseline),
    ]
    risk = classifier.classify(c.patch, c.policy)                                 # §5.1

    if any(ch.result == "fail" for ch in checks) or c.verdict != "accept":
        return decision(checks, risk, "not_promotable")
    if risk.tier == RiskTier.T3 and not c.policy.unlocks(risk):
        return decision(checks, risk, "not_promotable")

    soft = soft_conditions(checks, bot)        # tolerated regression failure, incident, freeze, daily limit
    within = b.auto_promote_ceiling is not None and risk.tier <= b.auto_promote_ceiling
    if within and risk.tier < RiskTier.T3 and not soft:
        return decision(checks + [approval("pass", "within ceiling")], risk, "auto_promote")
    return decision(checks + [approval("fail", why(risk, b, soft))], risk, "needs_review", soft)
```

T3 is never auto-promoted, whatever the ceiling says.

## 5. Risk tiers

### 5.1 Tier table and assignment

Assigned per patch op; a patch takes the max of its ops.

| Tier | Examples | Default promotion |
| --- | --- | --- |
| **T0** | Annotations only | Auto |
| **T1** | Memory item add/update/retire; skill description tweak; resource content update | Auto if the gate passes and owner policy allows |
| **T2** | Persona edits (SOUL/AGENTS/RULES); skill add/update; `engine_config` within the allowlist; any `rewrite`-flagged edit | Human review (owner or delegate); the owner may raise the auto ceiling to T2 per strategy |
| **T3** | MCP/CLI tool changes, script, memory `replace` mode, permissions, anything in `policy` | Locked by default; when unlocked, always human review, never auto |

Proposed default mapping from Genome Patch ops ([01-genome.md](01-genome.md))
to tiers. The normative mapping is carried as data in the patch schema
(work item RSI-02: "every patch op has a defined risk tier") and is listed
in [01-genome.md](01-genome.md); the two tables must stay identical. This
doc explains the policy behind it.

| Op | Target | Tier |
| --- | --- | --- |
| annotation change on the revision record | — | T0 |
| `memory.add`, `memory.update`, `memory.retire` (modes `seed`, `merge`) | `memory` | T1 |
| `skill.update` touching only the skill description | `skills/<name>` | T1 |
| `file.edit` | `resources/...` | T1 |
| `file.edit` | `persona/*` (SOUL, AGENTS, RULES) | T2 |
| `skill.add`, `skill.update` (content) | `skills/<name>` | T2 |
| `engine_config.set` (allowlisted key) | `engine_config.<key>` | T2 |
| any op on `tools.mcp`, `tools.cli_tools`, `script`; memory `replace` mode; permission changes | locked genes | T3 |
| any op on `policy` | `policy` | rejected by the floor (never promotable) |

Raises applied after the per-op mapping:

- **Rewrite flag.** A `file.edit` flagged `rewrite` (more than the
  configured fraction of the file changed, default 40%) is at least T2.
  Whole-file regeneration erodes context ("context collapse"), so large
  rewrites always get human eyes.
- **Guardrail diff audit.** A patch that mentions or edits guardrail-like
  text (safety sections, refusal policies, logging or reporting instructions,
  text resembling evaluation instructions) is tagged `guardrail_touch` and
  raised to at least T2. The detection rules are part of the verifier
  integrity rules in [07-verification.md](07-verification.md); the tier
  consequence is applied here.
- **Owner overrides.** `policy.risk_overrides` in the genome may raise the
  tier of specific genes or items for that bot. Overrides never lower a
  floor rule and can never make T3 auto-promotable.

### 5.2 Why tiers are per op and use the maximum

A patch is reviewed as a unit, so its route must be safe for its riskiest
part. Taking the maximum also means a strategy cannot "dilute" a persona
edit by bundling it with many memory items. Per-op tiers are still kept
(`per_op`) so the reviewer sees which op caused the tier.

Multi-iteration runs may squash several accepted iterations into one patch
for review; the ledger keeps each step, and the squashed patch is tiered
like any other.

### 5.3 The auto-promote ceiling

The **auto-promote ceiling** is the highest tier that may be promoted without
a human, set by the owner **per binding** (so per strategy, per bot). It is
the binding field `auto_promote_ceiling`, and the rollout settings are the
binding field `rollout`; both are proposed fields of the binding defined in
[06-evolution-run.md](06-evolution-run.md).

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// The Promotion-related fields of one binding in bot_123's evolution policy (other fields elided).
{
  "id": "bind_01",
  "strategy": "clawevolve/bot-evolution@2.0.0",
  "allowed_genes": ["persona", "skills", "memory"],
  "verification_profile": "default@1",
  "auto_promote_ceiling": "T1",        // binding field (06): "T0" | "T1" | "T2" | null (never auto)
  "rollout": {"canary_share": "0.1", "auto_rollback": false}   // binding field (06), multi-instance bots only (§8)
}
```

- Default ceiling: `T1` (memory items and small description or resource
  updates promote automatically when the gate passes).
- `null` means "always require human approval" — the rejected-as-default
  alternative of DR-2, available as owner policy.
- The highest allowed value is `T2`. T3 is never auto.
- The per-bot and per-tenant **maximum promotions per day** is a budget
  ([06-evolution-run.md](06-evolution-run.md)); when it is reached,
  auto-promotions turn into review items.

## 6. Review queue

The review queue holds candidates whose gate decision is `needs_review`: they
passed every automated check, but a human must agree. It generalizes
ClawEvolve's `skill-decision` human approval (with
`BotSkillGateway.replaceLocalSkill` doing a CAS skill replace,
`contracts/bot-skill-gateway.ts`) from one skill to every T2 patch, and
beyond.

### 6.1 What a reviewer sees

Each item is shown with its `CandidateReport` (§2.5):

- the **diff** of candidate revision against its parent (genome diff,
  [01-genome.md](01-genome.md)), with each op's tier and the reason the
  patch got its tier;
- the strategy's **rationale** and **evidence** (episodes, findings) — every
  patch must carry a rationale, and non-trivial ops must cite evidence;
- the **verification summary** per split: paired mean difference and
  confidence interval on `validation`, newly failing cases on `regression`
  and `safety`, the holdout result, the verifier version, and cost per case
  for parent and candidate. Per-case detail is visible per caller role
  ([07-verification.md](07-verification.md));
- **flags**: `rewrite`, `guardrail_touch`, `overfit_suspect` (validation gain
  greatly exceeds gain on regression and holdout, or scores fluctuate across
  seeds), `wins_by_spending`;
- the strategy's **self-reported metrics**, labelled as such; they are never
  used for acceptance;
- the full **gate decision**.

### 6.2 Lifecycle

```text
open ──approve──▶ approved   (Promotion created; revision status → promoted)
  │
  ├──reject───▶ rejected    (revision status → rejected; kept, never deleted)
  │
  └──(parent no longer active and owner policy says so)──▶ superseded
```

- **Approve** promotes the candidate revision (to `active`, or to `canary`
  when the binding has a rollout, §8). Approval needs a reason, which is
  stored and audited.
- **Reject** needs a reason. The rejection is evidence in the Experiment
  Ledger (labelled outcomes feed verifier calibration and level 3).
- **Stale base (proposed).** A candidate was verified against its parent.
  If `active` has moved since (another promotion, or going back), approving
  it would replace a revision it was never compared with. Proposed rule:
  approval uses CAS on `active` with the candidate's parent as the expected
  value. On conflict the API returns `409 stale_parent`; the reviewer can
  either promote anyway with an explicit `override_stale_parent: true` and a
  reason (audited), or reject it so the strategy can propose against the new
  `active`. Whether a stale item should instead become `superseded`
  automatically is open decision P-1 (§13).
- Items are never deleted; decided items stay readable in the queue with
  `status` filters and in the ledger.

### 6.3 Who reviews

The owner, a tenant admin, or a delegate the owner names. Pipelines may
approve only within the auto-promote policy the owner configured for them
(§3). T3 items (only possible after the owner unlocked the gene) always need
a human. For mechanism adoption (level 3), the comparison report is
mandatory ([10-meta-evolution.md](10-meta-evolution.md)).

## 7. Promotion

A **promotion** moves a bot's `active` ref (or `canary`) to a revision and
applies that revision to the running bot through the existing chain. It is
the only way evolution changes a bot.

### 7.1 Steps

1. **CAS on the ref.** `active` moves from the expected revision to the
   target, and `previous` is set to the old `active`, in one compare-and-swap
   on the Genome Registry's refs. A concurrent promotion loses with `409`
   and changes nothing. The ref log records actor and reason.
2. **Revision status.** The target revision's status becomes `promoted`.
3. **Apply.** The revision is compiled to a pinned Manifest document plus a
   memory projection ([01-genome.md](01-genome.md)) and applied:
   - **Personal bot:** through Manifest apply. Manifest apply is a one-shot
     command, not a controller (ADR 0018); the apply report records
     `revision_id` and the compiled document digest.
   - **Service bot:** as the **next published version** through the existing
     draft → verify → publish flow, with `revision_id` stored on the publish
     record. The VERIFY stage of that flow is where shadow verification
     attaches (§8.1), and an optional automated verification gate on the
     `VALIDATING → ONLINE_PUB` transition is work item RSI-22
     ([07-verification.md](07-verification.md)).
4. **Record.** A `Promotion` record and a ledger entry with actor, reason,
   gate decision, approval, and links to evaluations
   ([05-experiment-ledger.md](05-experiment-ledger.md)).
5. **Attribution.** From now on, episodes carry the new revision id
   ([02-experience.md](02-experience.md)), which is what makes online
   comparison of `r41` and `r42` possible.

If apply fails, the promotion's status is `apply_failed`, the apply report
says why, and the owner goes back by promoting `previous` (§8.4). For
service bots, when exactly `active` moves relative to the publish flow is
open decision P-2 (§13).

### 7.2 Who may promote what

| Target | Actor | Requirement |
| --- | --- | --- |
| A candidate revision | `gate_policy` | Gate decision `auto_promote` |
| A candidate revision | user / pipeline (approve) | Gate decision `needs_review`, approved in the queue; pipelines only within owner policy |
| A revision that was promoted before (going back) | owner / tenant admin, or `rollout_policy` for an enabled auto-rollback | Revision belongs to the bot; normal audited promotion |
| An owner-authored revision (recorded from the owner's own Manifest edit, `draft` ref) | owner / tenant admin | Owner action, outside the evolution gate (the owner holds that power directly); audited |
| A candidate revision whose gate decision is `not_promotable` | nobody | Refused with `422 not_promotable` |

Kill switches ([06-evolution-run.md](06-evolution-run.md)) are honoured
here: when evolution is frozen for a bot (`active` is kept), no candidate is
auto-promoted and approvals are refused with `403 evolution_frozen`; going
back by an owner stays allowed, because it is the remedy for a bad
promotion (proposed).

### 7.3 Never delete

Rejected, superseded, and retired revisions are kept, never deleted; they
stay visible in the archive view of the ledger
([05-experiment-ledger.md](05-experiment-ledger.md)). Any
later content-store sweep must never remove a blob reachable from a
`promoted` revision ([01-genome.md](01-genome.md)).

## 8. Rollout and going back

### 8.1 Shadow (optional)

The candidate runs on mirrored traffic or replayed episodes and is graded
offline with the same graders, without user impact. For service bots this
maps to the existing VERIFY stage of the publish flow, which today deploys a
verify-environment bot and checks nothing. Shadow grading is Verification's
online verification ([07-verification.md](07-verification.md)); Promotion
decides whether a shadow result is required before `active` moves (part of
the binding's `rollout` field, proposed, [06-evolution-run.md](06-evolution-run.md)).

### 8.2 Canary (multi-instance bots)

A share of instances runs the `canary` ref. Promotion moves `canary` to the
candidate (a promotion with `ref: "canary"`); Verification compares online
metrics (task success, user feedback, error rate, cost) of `canary` against
`active` with sequential testing. When the comparison is favourable, a
second promotion moves `active` to the same revision. Canary is not
available for single-instance (personal) bots.

### 8.3 Promote and optional auto-rollback

Promote is §7. An owner may enable an **auto-rollback rule** per policy
(the binding field `rollout.auto_rollback`, proposed in [06-evolution-run.md](06-evolution-run.md)): when online verification
reports a confirmed regression of the new `active` (or a holdout drop), the
platform promotes `previous` with actor `rollout_policy`. That is an ordinary
going-back promotion, not a separate mechanism.

### 8.4 Going back

**Going back is promoting an earlier revision again.** Because revision ids
are content hashes, no new revision is created: `active` moves back to the
earlier revision (for example `r41`), `previous` becomes the revision being
left, and the ref log records the move with actor and reason.

```text
avn genome promote --revision r41 --reason "r42 mis-routes VIP refunds"
```

To the rest of the platform this is just "apply this revision", exactly like
a forward promotion: a personal bot gets it through Manifest apply; a service
bot gets it as the **next published version** through the existing publish
flow. It works for personal and service bots and to any earlier revision of
the bot, not only one step back.

The **existing service-bot rollback feature** (versioned publish records with
a frozen artifact, one step back only;
`core/service_bot/services/publish_rollback_mixin.py:38-80`) is left as it
is. RSI neither replaces nor extends it, and there is no duplicate rollback
path.

### 8.5 After promotion

Online outcomes of the new `active` against its parent (online verification,
recurrence checks from Insight) are joined to the experiment in the ledger
later. Confirmed regressions become new `regression` cases, and false
accepts (promotions later reverted) count against the strategy's mechanism
metrics ([05-experiment-ledger.md](05-experiment-ledger.md),
[10-meta-evolution.md](10-meta-evolution.md)).

## 9. Service interface

Used by Evolution Run (after a verdict is final), by the Evolution API layer
(public endpoints), and by the UI backend. Strategies never call it.

```python
from typing import Protocol

class PromotionService(Protocol):
    """Platform-owned gate, review queue, and promotion (DR-2).

    The only component allowed to move a bot's `active`, `previous`, and
    `canary` refs in the Genome Registry.
    """

    def check_floor(self, bot: BotRef, candidate_id: str) -> GateCheck:
        """Run every FloorCheck on a submitted candidate (§4.2). Called by Evolution
        Run at submission, before verification. On failure, records a GateDecision
        with outcome `not_promotable` and the verdict becomes `reject` without
        running any suite. Idempotent per candidate."""

    def decide(self, bot: BotRef, candidate_id: str) -> GateDecision:
        """Evaluate the gate for a candidate whose verdict is final.

        Called by Evolution Run when Verification reports a final verdict.
        Idempotent per (candidate, verdict, verifier version): calling again
        returns the stored decision. An `auto_promote` outcome triggers the
        promotion before returning; `needs_review` opens a ReviewItem.
        Raises VerdictPending if the verdict is still `pending`.
        """

    def reconsider(self, bot: BotRef, reason: str) -> list[GateDecision]:
        """Re-evaluate open items of a bot after a bot-level change
        (holdout incident opened or cleared, kill switch, daily limit reset).
        Never turns a decided item back into an open one."""

    def candidate_report(self, bot: BotRef, candidate_id: str,
                         viewer: Actor) -> CandidateReport:
        """Diff + verification summary + gate decision + review state.
        Per-case verification detail is filtered by the viewer's role."""

    def review_queue(self, bot: BotRef, status: ReviewStatus | None = "open",
                     min_tier: RiskTier | None = None,
                     page: int = 1, page_size: int = 20) -> "Page[ReviewItem]":
        """List review items for a bot, newest first."""

    def approve(self, bot: BotRef, candidate_id: str, actor: Actor, reason: str,
                idempotency_key: str, override_stale_parent: bool = False) -> Promotion:
        """Approve an open item and promote its revision.

        Raises NotReviewable (not in the queue), AlreadyDecided (decided with
        another idempotency key), PolicyDenied (actor may not approve this
        tier), StaleParent (`active` is no longer the candidate's parent and
        override_stale_parent is False), EvolutionFrozen.
        """

    def reject(self, bot: BotRef, candidate_id: str, actor: Actor, reason: str,
               idempotency_key: str) -> ReviewItem:
        """Reject an open item; the revision's status becomes rejected."""

    def promote(self, bot: BotRef, revision_id: str, ref: Literal["active", "canary"], expected_revision: str,
                actor: Actor, reason: str, idempotency_key: str) -> Promotion:
        """Move `ref` ("active" or "canary") to `revision_id` with CAS on
        `expected_revision`, then apply. Going back is this call with an
        earlier revision. Raises RefConflict, NotPromotable, PolicyDenied."""


class FloorCheck(Protocol):
    """One platform-floor rule (§4.2). Floor checks are plugins owned by the
    platform, never by strategies; adding one is a reviewed platform change."""

    name: Literal["schema", "base", "locked_genes_and_pins", "allowed_genes", "secret_pii_scan",
                  "outbound_endpoints", "permission_escalation", "size_and_rewrite", "budget"]   # one per row of §4.2

    def check(self, patch: "GenomePatch", parent: "GenomeRevision",
              policy: "GenomePolicy", allowed_genes: list[str]) -> GateCheck: ...


class RiskClassifier(Protocol):
    """Maps a patch to its risk tier (§5.1), including raises and owner overrides."""

    def classify(self, patch: "GenomePatch", policy: "GenomePolicy") -> TierAssessment: ...
```

The secret and PII scanner behind the floor shares its rules with the
repository pre-push hook, so a credential that the hook blocks in a commit is
also blocked in a patch. Owners' tenant PII policy adds rules; it never
removes the shared ones.

## 10. API

Public endpoints are under the shared prefix `/openapi/v1`; paths below are
relative to it. Shared conventions (errors, pagination, ETags, idempotency
keys) are in [09-evolution-api.md](09-evolution-api.md). Every `POST` here
takes an `Idempotency-Key` header: a client-chosen string that is identical
for every retry of one logical request and different for different
requests; a retry with the same key returns the first result and does
nothing new. Responses below show the `data` payload of the standard
envelope; see [09-evolution-api.md](09-evolution-api.md) for the envelope,
errors, pagination, and idempotency.

Callers: humans (UI, `avn` CLI) and deterministic pipelines (client SDK).
Bot callers are postponed with DR-3.

### 10.1 Public endpoints

#### GET /bots/{bot_id}/evolution/candidates/{candidate}

The candidate report: diff, verification summary, gate decision, and review
state. Called by the UI review screen, `avn evolve review show`, and
pipelines deciding whether to approve.

Example request:

```text
GET /openapi/v1/bots/bot_123/evolution/candidates/sha256:c41e…
```

Example response (`200`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate_id": "sha256:c41e…",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "revision_id": "sha256:7c1e…",
  "revision_seq": 42,
  "parent_revision_id": "sha256:a90b…",
  "parent_revision_seq": 41,
  "run_id": "run_7f3",
  "strategy": "clawevolve/bot-evolution",
  "strategy_version": "2.0.0",
  "patch_summary": [
    {"op_index": 0, "op": "file.edit", "target": "persona/SOUL.md", "tier": "T2", "reason": "persona edit"},
    {"op_index": 1, "op": "skill.update", "target": "skills/refund-policy", "tier": "T2", "reason": "skill content update"}
  ],
  "diff_path": "/bots/bot_123/genome/revisions/sha256:7c1e…/diff?against=sha256:a90b…",
  "rationale": "Escalation section did not cover refunds above the tier limit; 9 of 31 failed episodes were mis-escalated.",
  "evidence": ["episode:ep_91", "finding:f_12"],
  "verdict": "accept",
  "splits": [
    {"split": "validation", "mean_delta": "0.12", "ci_low": "0.05", "ci_high": "0.19", "newly_failing": 0, "detail_visible": true},
    {"split": "regression", "mean_delta": "0.00", "ci_low": null, "ci_high": null, "newly_failing": 0, "detail_visible": true},
    {"split": "safety", "mean_delta": "0.00", "ci_low": null, "ci_high": null, "newly_failing": 0, "detail_visible": true},
    {"split": "holdout", "mean_delta": "0.07", "ci_low": "0.01", "ci_high": "0.13", "newly_failing": 0, "detail_visible": false}
  ],
  "cost": {"parent_usd_per_case": "0.031", "candidate_usd_per_case": "0.033"},
  "flags": [],
  "gate": {
    "id": "gd_204",
    "outcome": "needs_review",
    "risk": {"tier": "T2", "raised_by": []},
    "auto_promote_ceiling": "T1",
    "reasons": ["risk tier T2 requires human review"]
  },
  "review": {"status": "open", "created_at": "2026-10-08T03:41:00Z"}
}
```

Notable errors: `404 not_found` (unknown bot or candidate, or the caller may
not read the bot). A candidate whose verdict is still `pending` is not an
error: the report is returned with `"verdict": "pending"` and `"gate": null`.

#### GET /bots/{bot_id}/evolution/review-queue

List review items. Called by the UI, `avn evolve review list`, and
pipelines. Query parameters: `status` (`open` default, `approved`,
`rejected`, `superseded`, or `all`), `min_tier`, `page` (1-based),
`page_size` (1 to 100, default 20).

Example request:

```text
GET /openapi/v1/bots/bot_123/evolution/review-queue?status=open
```

Example response (`200`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 1,
  "items": [
    {
      "candidate_id": "sha256:c41e…",
      "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
      "revision_id": "sha256:7c1e…",
      "revision_seq": 42,
      "parent_revision_id": "sha256:a90b…",
      "run_id": "run_7f3",
      "strategy": "clawevolve/bot-evolution",
      "strategy_version": "2.0.0",
      "risk_tier": "T2",
      "gate_decision_id": "gd_204",
      "rationale": "Escalation section did not cover refunds above the tier limit; 9 of 31 failed episodes were mis-escalated.",
      "evidence": ["episode:ep_91", "finding:f_12"],
      "self_reported_metrics": {"train_pass_rate": "0.81"},
      "status": "open",
      "created_at": "2026-10-08T03:41:00Z",
      "decided_by": null,
      "decided_at": null,
      "decision_reason": null,
      "promotion_id": null
    }
  ]
}
```

Notable errors: `404 not_found`; `400 invalid_argument` for an unknown
`status` value.

#### POST /bots/{bot_id}/evolution/candidates/{candidate}:approve

Approve an open review item and promote its revision. Called by owners and
reviewers (UI, `avn evolve review approve`), and by pipelines within owner
policy.

Example request:

```text
POST /openapi/v1/bots/bot_123/evolution/candidates/sha256:c41e…:approve
Idempotency-Key: review-bot_123-sha256:c41e-approve
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "reason": "Reviewed diff and verification report; escalation fix is correct.",
  "override_stale_parent": false     // optional; true only to promote over a moved `active` (audited)
}
```

Example response (`200`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "review": {
    "candidate_id": "sha256:c41e…",
    "status": "approved",
    "decided_by": "user_owner_17",
    "decided_at": "2026-10-08T09:12:00Z",
    "decision_reason": "Reviewed diff and verification report; escalation fix is correct.",
    "promotion_id": "prm_5d2"
  },
  "promotion": {
    "id": "prm_5d2",
    "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
    "ref": "active",
    "revision_id": "sha256:7c1e…",
    "revision_seq": 42,
    "from_revision_id": "sha256:a90b…",
    "going_back": false,
    "candidate_id": "sha256:c41e…",
    "gate_decision_id": "gd_204",
    "actor": {"kind": "user", "id": "user_owner_17"},
    "reason": "Reviewed diff and verification report; escalation fix is correct.",
    "status": "applying",
    "apply": {"kind": "manifest_apply", "reference": null, "detail": "apply started"},
    "created_at": "2026-10-08T09:12:00Z"
  }
}
```

Notable errors: `404 not_found`; `409 not_reviewable` (the candidate is not
in the queue, for example it was auto-promoted or is `not_promotable`);
`409 already_decided` (decided earlier under a different idempotency key);
`409 stale_parent` (`active` moved since verification; see §6.2);
`403 policy_denied` (a pipeline approving above its allowed tier);
`403 evolution_frozen` (per-bot kill switch).

#### POST /bots/{bot_id}/evolution/candidates/{candidate}:reject

Reject an open review item. The revision's status becomes `rejected`; it is kept, never deleted.

Example request:

```text
POST /openapi/v1/bots/bot_123/evolution/candidates/sha256:c41e…:reject
Idempotency-Key: review-bot_123-sha256:c41e-reject
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "reason": "The new escalation text drops the legal-hold exception."
}
```

Example response (`200`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate_id": "sha256:c41e…",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "revision_id": "sha256:7c1e…",
  "revision_seq": 42,
  "status": "rejected",
  "decided_by": "user_owner_17",
  "decided_at": "2026-10-08T09:20:00Z",
  "decision_reason": "The new escalation text drops the legal-hold exception.",
  "promotion_id": null
}
```

Notable errors: `404 not_found`; `409 not_reviewable`; `409 already_decided`;
`400 invalid_argument` (empty reason).

#### POST /bots/{bot_id}/genome/promotions

Move `active` (or `canary`) to a revision and apply it. Used for going back,
for promoting an owner-authored revision, for canary → active, and
internally by the gate and approvals (which call the service interface, not
this endpoint). Called by owners and tenant admins (UI,
`avn genome promote`).

Example request (going back from `r42` to `r41`):

```text
POST /openapi/v1/bots/bot_123/genome/promotions
Idempotency-Key: 4b0f6c1e-9a7d-4f53-8d1e-2c6a9e1b7f02
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "revision": "sha256:a90b…",          // r41; "r41" (seq) is also accepted
  "ref": "active",                     // "active" (default) or "canary"
  "expected_revision": "sha256:7c1e…", // CAS: what `ref` must point at now (r42)
  "reason": "r42 mis-routes VIP refunds"
}
```

Example response (`201`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "prm_6a0",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "ref": "active",
  "revision_id": "sha256:a90b…",
  "revision_seq": 41,
  "from_revision_id": "sha256:7c1e…",
  "going_back": true,
  "candidate_id": null,
  "gate_decision_id": null,
  "actor": {"kind": "user", "id": "user_owner_17"},
  "reason": "r42 mis-routes VIP refunds",
  "status": "applying",
  "apply": {"kind": "manifest_apply", "reference": null, "detail": "apply started"},
  "created_at": "2026-10-09T08:02:00Z"
}
```

The ref moves at once; apply progress is visible on the promotion's
`apply` field through the existing Manifest apply report (personal bots) or
publish record (service bots), both of which record the `revision_id`.

Notable errors: `404 not_found` (unknown revision or not this bot's);
`409 ref_conflict` (`expected_revision` does not match; nothing changed);
`422 not_promotable` (a candidate revision whose gate decision is
`not_promotable`); `403 policy_denied` (caller may not promote);
`400 invalid_argument` (`ref` other than `active` or `canary`;
`canary` on a single-instance bot).

### 10.2 Internal interfaces

Not part of the public API; listed so the boundary is explicit.

| Interface | Caller → callee | Purpose |
| --- | --- | --- |
| `PromotionService.check_floor(bot, candidate)` (runs every `FloorCheck`) | Evolution Run → Promotion | Static floor at candidate submission, before verification |
| `PromotionService.decide(bot, candidate)` | Evolution Run → Promotion | Evaluate the gate once a verdict is final |
| `PromotionService.reconsider(bot, reason)` | Verification (holdout incident), Evolution Run (kill switch) → Promotion | Re-evaluate open items after a bot-level change |
| `verify(candidate, profile)` | Promotion → Verification (only when the gate needs a holdout or baseline result not yet available) | Request the missing evaluation ([07-verification.md](07-verification.md)) |
| Ref CAS (`active`, `previous`, `canary`) | Promotion → Genome Registry | Move refs; only Promotion may move these three |
| Manifest apply / publish flow | Promotion → existing Backend services | Apply the promoted revision |
| Ledger append | Promotion → Experiment Ledger | Gate decisions, approvals, rejections, promotions |

Strategies see none of these. Their view of the outcome is the verdict they
look up by candidate id ([03-strategy.md](03-strategy.md)).

## 11. Examples

### 11.1 A nightly pipeline that approves within policy

```python
# Illustrative only
from avernet_evolution import Client, RiskTier

c = Client.from_env()
run_id = c.runs.start(bot_id="bot_123", binding="bind_01",      # bind_01 runs clawevolve/bot-evolution@2.0.0
                      budget={"max_usd": 10},
                      idempotency_key="nightly-bot_123-2026-10-08")   # same key on every retry
run = c.runs.wait(run_id)                     # repeated short status lookups by id

for cand in run.candidates():
    report = c.candidates.get(bot_id="bot_123", candidate=cand.id)
    if report.gate is None or report.gate.outcome != "needs_review":
        continue                              # auto-promoted, not promotable, or still pending
    if report.gate.risk.tier <= RiskTier.T1 and not report.flags:
        # Only reached when the owner turned gate auto-promotion off but allowed
        # this pipeline to approve T1; anything higher is left for a human.
        c.review.approve(bot_id="bot_123", candidate=cand.id,
                         reason="nightly auto-policy",
                         idempotency_key=f"approve-bot_123-{cand.id}")
```

The same calls back the CLI and the UI backend.

### 11.2 A human review from the CLI

```text
$ avn evolve review list --bot bot_123
CANDIDATE        REV  TIER  STRATEGY                         VERDICT  CREATED
sha256:c41e…     r42  T2    clawevolve/bot-evolution@2.0.0   accept   2026-10-08T03:41Z

$ avn evolve review show sha256:c41e… --bot bot_123      # report: diff, splits, flags, gate
$ avn genome diff r42 --against r41 --bot bot_123
$ avn evolve review approve sha256:c41e… --bot bot_123 --reason "escalation fix is correct" --yes
```

Exit codes follow the shared CLI conventions
([09-evolution-api.md](09-evolution-api.md)): for example `4` for a
conflict such as `stale_parent`, `5` for `policy_denied`.

### 11.3 Going back

```python
# Illustrative only
refs = c.genome.refs(bot_id="bot_123")        # active = r42, previous = r41
promo = c.genome.promote(bot_id="bot_123",
                         revision=refs.previous,
                         expected_revision=refs.active,
                         reason="r42 mis-routes VIP refunds",
                         idempotency_key="goback-bot_123-r42-to-r41")
assert promo.going_back
```

For a service bot the same call publishes `r41` as the next published
version; the existing one-step service-bot rollback is not used.

### 11.4 The gate deciding a T1 memory candidate

A `platform/consolidate-memory@1.0.0` run on `bot_123` submits one patch with
two `memory.add` ops and one `memory.retire`. Verification returns `accept`
under `default@1` with a regression and contradiction check. The gate
records: floor pass (scan clean), regression floor pass, verdict pass,
holdout pass on the run's final candidate, baseline not required, tier `T1`
≤ ceiling `T1`. Outcome `auto_promote`; Promotion moves `active` with actor
`gate_policy`, and the bot's next sessions carry the new revision id. Had
any op touched persona text, the tier would be T2 and the candidate would
wait in the review queue.

## 12. Interactions

| Other part | Direction | What flows |
| --- | --- | --- |
| Genome ([01-genome.md](01-genome.md)) | Promotion → Genome | CAS moves of `active`, `previous`, `canary`; revision status changes (`promoted`, `rejected`); reading patches, diffs, and `policy` |
| Genome ([01-genome.md](01-genome.md)) | Genome → Promotion | Patch-level floor results at record time (base, locked genes, pins, rewrite flag) |
| Experience ([02-experience.md](02-experience.md)) | Promotion → Experience (indirect) | After promotion, episodes carry the new revision id |
| Strategy ([03-strategy.md](03-strategy.md)) | none directly | Strategies submit candidates and read verdicts only; they never see gate decisions or the queue |
| Default strategies ([04-default-strategies.md](04-default-strategies.md)) | — | ClawEvolve's accept rule becomes an internal submission filter; its `skill-decision` approval becomes this review queue |
| Experiment Ledger ([05-experiment-ledger.md](05-experiment-ledger.md)) | Promotion → Ledger | Gate decisions, approvals, rejections, promotions, going back, with actor and reason |
| Evolution Run ([06-evolution-run.md](06-evolution-run.md)) | Run → Promotion | `check_floor` at submission; `decide` when a verdict is final; binding fields (allowed genes, profile, auto-promote ceiling, rollout); budgets, daily promotion limits, kill switches |
| Verification ([07-verification.md](07-verification.md)) | Verification → Promotion | Verdicts, per-split evidence, holdout and baseline results, online (shadow/canary) results, holdout incidents |
| Verification ([07-verification.md](07-verification.md)) | Promotion → Verification | `verify(candidate, profile)` when a holdout or baseline result is missing |
| Evolution API ([09-evolution-api.md](09-evolution-api.md)) | API → Promotion | Public endpoints of §10; SDK and `avn` commands |
| Meta-evolution ([10-meta-evolution.md](10-meta-evolution.md)) | Meta → Promotion | Mechanism adoption through a separate gate profile (T3 default, comparison report required) |
| Existing Manifest apply and service-bot publish flow (Backend) | Promotion → existing | Apply of the promoted revision; `revision_id` on apply reports and publish records |

## 13. Open decisions

- **P-1: Stale parent on approval.** Approve with CAS on the candidate's parent
  and an explicit override (proposed in §6.2), or mark stale items
  `superseded` automatically and require the strategy to propose again?
- **P-2: When `active` moves for a service bot.** At approval (desired state
  first, publish follows), or only when the publish flow reaches
  `ONLINE_PUB`? The second keeps `active` equal to what users see but makes
  promotion wait on a manual "go online" step today.
- **P-3: Holdout cadence.** The timing is defined in
  [07-verification.md](07-verification.md): once on the final candidate of a
  run before promotion (when the profile requires it), and periodically on
  `active`. Open here: the periodic schedule and how long an incident blocks
  auto-promotion.
- **P-4: Owner-authored revisions and the floor.** Owner edits are outside the
  evolution gate. Should the secret/PII scan of the floor still run on them
  (advisory or blocking)?
- **P-5: Auto-rollback rule.** Which online signals may trigger it (confirmed
  regression from canary, holdout drop, error-rate spike), and whether it is
  allowed for service bots, where going back is a publish.
- **P-6: Delegated reviewers.** How an owner names a delegate and whether T2
  reviews may require two reviewers for some genes. Kept minimal until
  authorization is designed.
- **P-7: DR-2 acceptance.** DR-2 is proposed; it is promoted to `docs/adr/` on
  acceptance (work item RSI-01).

Resolved:

- **Default regression tolerance.** `default@1` of
  [07-verification.md](07-verification.md): zero newly failing `safety`
  cases; `regression` tolerance 1, and a spent tolerance
  (`tolerance_used: true`) routes the candidate to human review (§4.1, §4.3).
- **Where the review-queue endpoints are served.** Genome and Promotion
  endpoints (including the `/bots/{bot_id}/evolution/candidates/…` and
  `/bots/{bot_id}/evolution/review-queue` paths) are served by Backend;
  Experience, Strategy registry, Evolution Run, Verification, Ledger, and
  Meta-evolution endpoints by `apps/evolution`;
  [09-evolution-api.md](09-evolution-api.md) presents them under one public
  `/openapi/v1` surface. This follows the recommended option of D-1
  ([design.md](design.md)).
