# Evolution Run

> 中文版：[06-evolution-run.zh-CN.md](06-evolution-run.zh-CN.md)

> Status: DRAFT. Service in the [bot evolution architecture](design.md).
> How a bot's evolution policy turns into runs, and how the platform executes
> a run: triggers, idempotent submission, leased jobs and re-dispatch,
> long-running operations, runtimes and the Job Protocol, sandboxing, and
> budgets and kill switches.

## 1. Purpose and scope

The Evolution Run service (component C4, the *Run Orchestrator*, in earlier
drafts) is the platform part that executes strategies against bots. A
**strategy** is versioned code with one method, `run(ctx)`, that proposes
changes to a bot ([03-strategy.md](03-strategy.md)). A **run** is one
execution of one strategy against one bot. The service is a durable state
machine for runs: it fires runs when their triggers say so, freezes their
inputs, hands the strategy a context with exactly the capabilities it was
granted, keeps the run alive across crashes, charges every cost to a budget,
and records what the strategy submitted.

It owns:

| Owns | Summary | Section |
| --- | --- | --- |
| Evolution policy and bindings | Per bot: which strategies run, when, from which parent, what they may change, how strictly they are verified, with what budget and params | [§3](#3-evolution-policy-and-bindings) |
| Triggers | Schedule, manual, and event triggers that create runs | [§4](#4-triggers) |
| Run lifecycle | `queued → running → completed \| failed \| cancelled \| budget_exhausted`; start, during, end, cancellation | [§5](#5-run-lifecycle) |
| Idempotent run submission | A submission returns a run id; a repeat with the same idempotency key returns the same id | [§6](#6-idempotent-submission-and-status-by-id) |
| Leases and re-dispatch | Every attempt of a run is a leased job; a dead worker's run is dispatched again, as a new job, with the same run id | [§7](#7-leases-re-dispatch-and-crash-recovery) |
| Long-running operations (platform side) | Agent sessions and train evaluations are persisted operations with ids, run by the platform | [§8](#8-long-running-operations-platform-side) |
| Runtimes and the Job Protocol | In-process strategies, and job-worker containers that reach `ctx` over HTTP | [§9](#9-runtimes-and-the-job-protocol) |
| Sandboxing | Strategies work only on sandbox materialisations, with no credentials, egress, or model keys of their own | [§10](#10-sandboxing) |
| Budgets and kill switches | Per-run budgets enforced by the platform; per-bot and per-tenant ceilings; kill switches per strategy, per bot, and global | [§11](#11-budgets-and-kill-switches) |

It does **not** own:

| Not owned here | Owner |
| --- | --- |
| Genome revisions, refs, patches, content store. The service creates candidate revisions and moves `candidate/<run>/<n>` refs through the Genome API | [01-genome.md](01-genome.md) |
| Episodes and feedback, the session export providers behind `experience.*` | [02-experience.md](02-experience.md) |
| The strategy port, `StrategyContext` as seen by the strategy, the capability catalog, registration records, agent definitions, the Strategy Registry, the strategy SDK and conformance kit | [03-strategy.md](03-strategy.md) |
| ClawEvolve and `platform/consolidate-memory` as strategies | [04-default-strategies.md](04-default-strategies.md) |
| The Experiment Ledger H, where runs, candidates, costs, and models used are recorded | [05-experiment-ledger.md](05-experiment-ledger.md) |
| Suites, graders, verification profiles, verdicts (`verify(candidate, profile)`) | [07-verification.md](07-verification.md) |
| The gate, risk tiers, review queue, promotion and going back, patch scanning | [08-promotion.md](08-promotion.md) |
| Shared API conventions (error envelope, ETags, idempotency-key rules), SDKs, the `avn` CLI | [09-evolution-api.md](09-evolution-api.md) |
| Level 3 (improving the mechanism itself) | [10-meta-evolution.md](10-meta-evolution.md) |

Two principles from the strategy design shape this service:

- **One door out.** A strategy reaches the platform only through its
  context. Isolation and budgets are therefore enforced here, in one place,
  whatever the strategy is.
- **Strategies propose; the platform decides.** A run submits candidates.
  Recording, verification, the gate, and promotion stay platform-owned
  ([DR-2](decisions/0002-promotion-is-platform-owned.md)). A run can never
  move a bot's `active` ref.

**Placement.** The service lives in the new module `apps/evolution`
(recommended option A of open decision D-1, see [design.md](design.md)),
next to the Strategy Registry, the Experience Store, the Verification
Service, and the Experiment Ledger. Runs are long-running, LLM-heavy, bursty
work that should scale and fail independently of Backend request serving.
The service generalizes ClawEvolve's `ce_tasks` / `ce_steps` tables and
claim-report endpoints (`routes/internal/evolve.ts` in the ClawWeb control
plane), which it replaces for the default strategy
([04-default-strategies.md](04-default-strategies.md)). For singlebox it has
a local profile: SQLite storage and in-process strategies (work item RSI-08
in [work-items.md](work-items.md)).

## 2. Domain model

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `EvolutionPolicy` | A bot's list of bindings | Bot owner / tenant admin (stored here) | Replaced as a whole by `PUT …/policy`, versioned by ETag |
| `Binding` | One entry in the policy: strategy version, trigger, parent, allowed genes, verification profile, budget, params, and (proposed) auto-promote ceiling and rollout | Bot owner / tenant admin | Created, changed, or removed with the policy; checked against the bot on every change |
| `Trigger` | What makes a binding create a run: a schedule, manual only, or a platform event | Part of a binding | As the binding |
| `Budget` | Per-run spending limits: USD, wall clock, rollouts, tokens | Part of a binding; frozen on the run | Frozen at run start; spent amounts never reset |
| `Run` | One execution of a binding, with strategy version, params, parent, and budget frozen at start; the run id is its only handle | Platform (this service) | `queued → running → completed \| failed \| cancelled \| budget_exhausted`; never deleted |
| `Job` | One dispatch attempt of a run, executed by a worker under a lease: holder, lease expiry, attempt, fencing token | Platform (this service) | One per attempt; a re-dispatch creates a new job for the same run; closed when its attempt ends |
| `Operation` | A long-running capability call (agent session, train evaluation) started by a run, persisted and executed by the platform | Platform (this service) | `queued → running → succeeded \| failed \| cancelled`; unfinished ones are cancelled when the run ends |
| `Workspace` | A sandbox materialisation of a revision, created for a run under a key | Platform (this service) | Created idempotently per `(run, key)`; discarded after the run ends |
| `RunSummary` | What `run(ctx)` returns when it finishes normally | Strategy | Stored on the run |
| `Candidate`, `Verdict` | A Genome Patch the run submits, and the platform's verification result for it | Defined in [03-strategy.md](03-strategy.md); verdicts produced by [07-verification.md](07-verification.md) | Candidate id = content hash of the patch; kept even if the run fails |
| `StrategyContext` | The run's only door to the platform | Defined in [03-strategy.md](03-strategy.md); built by this service | Built per attempt |

### 2.1 Budget

A **budget** is the per-run spending limit. Every model call, agent session,
and evaluation is charged to it, and the run stops when any dimension runs
out.

A **rollout** is one execution of one evaluation case against one bot
version. For example, running the test case "partial refund" once against
the sandbox candidate is one rollout; running it with 3 seeds (3 repeats,
to average out model randomness) is 3 rollouts, and running it against both
the parent and the candidate for a comparison counts both. Train
evaluations a strategy starts are counted in rollouts; verification of
submitted candidates is not charged to the run (§11.1).

```python
@dataclass(frozen=True)
class Budget:
    max_usd: float                      # US dollars the run may spend in total: model calls, agent
                                        # sessions, and train evaluations. At the limit the next charged
                                        # call fails and the run ends as budget_exhausted
    max_wall_clock_s: int               # seconds from the run's first start to its end, including time
                                        # spent waiting and between attempts (§7.3); at the limit the run ends
    max_rollouts: int | None = None     # how many evaluation rollouts (see above) the run may use;
                                        # None = no limit on this dimension
    max_tokens: int | None = None       # model tokens (input + output, all calls) the run may use;
                                        # None = no limit on this dimension

@dataclass(frozen=True)
class BudgetUsage:                      # what has been spent so far, in the same units as Budget
    usd: float                          # US dollars charged so far
    wall_clock_s: int                   # seconds since the run first started
    rollouts: int                       # evaluation rollouts charged so far
    tokens: int                         # model tokens charged so far
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  // Limits: at most $20, 2 hours, and 400 rollouts (for example 20 train cases x 2 versions x 10 rounds);
  // no token limit because max_tokens is omitted.
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200, "max_rollouts": 400},
  // Spent so far: $6.42, about 30 minutes, 96 rollouts, 412,300 tokens.
  "budget_used": {"usd": 6.42, "wall_clock_s": 1830, "rollouts": 96, "tokens": 412300}
}
```

### 2.2 Trigger and Binding

```python
@dataclass(frozen=True)
class ScheduleTrigger:
    schedule: str                       # cron expression, evaluated in UTC

@dataclass(frozen=True)
class ManualTrigger:
    manual: Literal[True]               # no automatic firing; runs only when submitted

# Platform events a binding can be triggered by (proposed list, from §4). A new
# event is added by a reviewed platform change, together with its producer.
EventName = Literal[
    "failure_rate_alert",               # the bot's failure rate crossed its alert threshold
    "clawinsight_improvement_item",     # ClawInsight recorded an improvement item for the bot
    "feedback_threshold_reached",       # N new feedback items arrived since the last run (memory consolidation)
]

@dataclass(frozen=True)
class EventTrigger:
    event: EventName                    # fire a run when this platform event arrives for the bot

Trigger = ScheduleTrigger | ManualTrigger | EventTrigger

@dataclass(frozen=True)
class Rollout:                          # proposed; read by Promotion (08-promotion.md). How a promoted
                                        # revision reaches a multi-instance bot; not the evaluation
                                        # "rollouts" counted in Budget
    canary_share: str                   # share of instances on `canary`, decimal string, e.g. "0.1"
    auto_rollback: bool                 # owner-enabled auto-rollback rule

@dataclass(frozen=True)
class Binding:
    id: str                             # chosen by the owner, unique within the bot, e.g. "bind_01"
    strategy: str                       # "<strategy id>@<version>", a registered, conformant version
    trigger: Trigger
    parent: "SelectorName"              # which revision runs start from (05-experiment-ledger.md §7);
                                        # only "active" is accepted in the first iteration
    allowed_genes: list[str]            # what this strategy may change on THIS bot; within the bot's policy
    verification_profile: str           # e.g. "default@1"; owners may pick a stricter one, never a looser one
    budget: Budget
    params: dict                        # validated against the registration's params_schema, if any, and by the strategy
    auto_promote_ceiling: Literal["T0", "T1", "T2"] | None = "T1"   # proposed; None = never auto-promote
    rollout: Rollout | None = None      # proposed; multi-instance bots only; None = promote `active` directly
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "bind_01",
  "strategy": "clawevolve/bot-evolution@2.0.0",
  "trigger": {"schedule": "0 2 * * *"},             // or {"manual": true}, {"event": "failure_rate_alert"}
  "parent": "active",
  "allowed_genes": ["persona", "skills"],
  "verification_profile": "default@1",
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60},
  "auto_promote_ceiling": "T1",                      // proposed: "T0" | "T1" | "T2" | null (never auto)
  "rollout": {"canary_share": "0.1", "auto_rollback": false}   // proposed: multi-instance bots only
}
```

### 2.3 EvolutionPolicy

Each bot has exactly **one** evolution policy, and the policy is a list of
bindings. Each binding attaches one strategy to the bot with its own
trigger, allowed genes, verification profile, budget, and params. Several
bindings mean several strategies (or the same strategy with different
settings) improving different aspects of the bot on different schedules:
for example, `bind_01` runs ClawEvolve nightly on `persona` and `skills`,
and `bind_02` runs `platform/consolidate-memory` weekly on `memory`, as in
the example below. Bindings run independently: each binding has at most one
active run at a time (§4), but runs of different bindings can overlap.
When two bindings' candidates are built on the same parent and one of them
is promoted first, `active` has moved by the time the other is approved;
Promotion then refuses the second with `409 stale_parent` unless the
reviewer explicitly overrides
(§6.2 of [08-promotion.md](08-promotion.md)).

```python
@dataclass(frozen=True)
class EvolutionPolicy:
    bot: BotRef                         # the bot this policy belongs to (owner + bot id, 09-evolution-api.md §2.7)
    bindings: list[Binding]             # one entry per strategy attached to the bot
    etag: str                           # changes on every successful PUT; used with If-Match
    updated_at: datetime
    updated_by: str                     # user id of whoever wrote this version
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Evolution policy of bot_123 (OpenClaw support bot)
{
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "bindings": [
    {
      "id": "bind_01",
      "strategy": "clawevolve/bot-evolution@2.0.0",
      "trigger": {"schedule": "0 2 * * *"},
      "parent": "active",
      "allowed_genes": ["persona", "skills"],
      "verification_profile": "default@1",
      "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
      "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60}
    },
    {
      "id": "bind_02",
      "strategy": "platform/consolidate-memory@1.0.0",
      "trigger": {"schedule": "0 4 * * 0"},
      "parent": "active",
      "allowed_genes": ["memory"],
      "verification_profile": "default@1",
      "budget": {"max_usd": 5, "max_wall_clock_s": 1800},
      "params": {}
    }
  ],
  "etag": "\"pol-v12\"",
  "updated_at": "2026-10-06T08:00:00Z",
  "updated_by": "user_owner_5"
}
```

Per-bot ceilings and the per-bot freeze switch (§11) are proposed as
further fields of this document; see [§17](#17-open-decisions).

### 2.4 Run

A **run** is one execution of one binding for one bot: the platform starts
the binding's strategy once, against a parent revision frozen at start,
under the binding's budget. It is the durable business record of that
execution (what was frozen, what it spent, what it submitted, how it
ended) and the **only handle callers use**: they start it, look it up,
list its candidates, and cancel it by its run id. A run never disappears
and never restarts under a new id, even when its worker crashes.

```python
RunStatus = Literal["queued", "running", "completed", "failed", "cancelled", "budget_exhausted"]
# Why a run ended, when the status alone does not say (proposed; extended only by a reviewed change).
EndReason = Literal[
    "worker_failed",                    # the strategy reported a non-retryable failure
    "max_attempts",                     # re-dispatched max_attempts times without finishing (§7.2)
    "cancelled_by_owner",               # a caller cancelled it (POST …/runs/{run}:cancel)
    "kill_switch",                      # a strategy, bot, or global kill switch stopped it (§11.3)
    "consecutive_rejections",           # the escalation rule stopped it (§11.2)
]

@dataclass(frozen=True)
class TriggerRecord:
    kind: Literal["schedule", "manual", "event"]
    fire_time: datetime | None          # set for schedule triggers
    event_id: str | None                # set for event triggers
    requested_by: str | None            # set for manual submissions: the caller's user or pipeline client id

@dataclass
class Run:
    id: str                             # "run_7f3"; globally unique; the only handle
    bot: BotRef                         # the bot it runs against (owner + bot id)
    binding_id: str
    idempotency_key: str                # (owner_id, bot_id, key) -> run id
    trigger: TriggerRecord
    # frozen at submission
    strategy: str                       # "clawevolve/bot-evolution"
    strategy_version: str               # "2.0.0"
    parent_ref: "SelectorName"          # the binding's `parent` at submission, e.g. "active"
    parent_revision: str                # resolved revision id, e.g. "sha256:a90b…"
    allowed_genes: list[str]
    verification_profile: str
    params: dict
    budget: Budget
    granted: list[str]                  # capabilities in the context, always-granted ones included
    agent_definitions: dict[str, str]   # definition name -> digest, recorded for attribution
    # changing
    status: RunStatus
    attempt: int                        # 1 on first dispatch, +1 on each re-dispatch
    max_attempts: int
    budget_used: BudgetUsage
    candidate_count: int
    created_at: datetime
    started_at: datetime | None         # first transition to running
    ended_at: datetime | None
    end_reason: EndReason | None        # None while running, and for completed or budget_exhausted runs
    summary: dict | None                # RunSummary from a completed run
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "run_7f3",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "binding_id": "bind_01",
  "idempotency_key": "bind_01/2026-10-08T02:00:00Z",
  "trigger": {"kind": "schedule", "fire_time": "2026-10-08T02:00:00Z", "event_id": null, "requested_by": null},
  "strategy": "clawevolve/bot-evolution",
  "strategy_version": "2.0.0",
  "parent_ref": "active",
  "parent_revision": "sha256:a90b…",                 // r41
  "allowed_genes": ["persona", "skills"],
  "verification_profile": "default@1",
  "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60},
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "granted": ["parent", "workspace", "operations", "budget", "log", "artifacts", "cancelled",
              "candidates@1", "models@1", "experience.sessions@1", "agents@1", "evaluate.train@1"],
  "agent_definitions": {"clawevolve-tune": "sha256:5d0e…", "clawevolve-review": "sha256:8b17…"},
  "status": "running",
  "attempt": 2,                                       // re-dispatched once after a worker crash
  "max_attempts": 3,
  "budget_used": {"usd": 6.42, "wall_clock_s": 1830, "rollouts": 96, "tokens": 412300},
  "candidate_count": 1,
  "created_at": "2026-10-08T02:00:03Z",
  "started_at": "2026-10-08T02:00:09Z",
  "ended_at": null,
  "end_reason": null,
  "summary": null
}
```

### 2.5 Job

A **job** is one dispatch attempt of a run: the unit a worker claims and
executes under a **lease**, a time-limited claim the worker must keep
renewing. A run has one job per attempt: when a worker crashes and its
lease expires, the platform re-dispatches the run as a new job (attempt
`n + 1`) for the **same** run. A **fencing token** is a value issued with
each lease; calls that carry an older token are refused, so a worker that
lost its lease cannot interfere with the new holder. Callers never see
jobs; workers only see jobs.

| | Run | Job |
| --- | --- | --- |
| What it represents | One execution of a binding for one bot: the business record (frozen inputs, spend, candidates, outcome) | One attempt to execute that run on a worker, under a lease |
| Lifetime | From submission to a terminal status; kept forever | From dispatch until the attempt completes, fails, or its lease expires |
| How many | One per submission (per idempotency key) | One per attempt of the run: 1, or more after crashes |
| Who sees it | Callers (API, SDK, CLI, UI) by run id; the strategy as `ctx.run_id` | Workers and the Job Protocol only, by job id |

```python
@dataclass
class Job:
    id: str                             # "job_7f3_2" (run_7f3, attempt 2); one job per attempt of the run
    run_id: str                         # the run this attempt executes
    attempt: int                        # which attempt of the run this job is (1, 2, …)
    worker_id: str | None               # current holder; None while queued
    lease_expires_at: datetime | None
    fencing_token: str | None           # new value on every claim
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "job_7f3_2",
  "run_id": "run_7f3",
  "attempt": 2,
  "worker_id": "worker-clawevolve-02",
  "lease_expires_at": "2026-10-08T02:31:10Z",
  "fencing_token": "ft_7f3_2_b81c"
}
```

### 2.6 Operation

An **operation** is a capability call whose work can outlast a short
request: an agent session (`agents.start`) or a train evaluation
(`evaluate.start_train`). Starting it returns an operation id at once; its
status is looked up by id.

```python
OperationStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

@dataclass
class Operation:
    id: str                             # "op_19a"
    run_id: str
    kind: Literal["agent_session", "train_evaluation"]   # same values as Operation.kind in 03-strategy.md
    idempotency_key: str                # "<run>/<own step>", e.g. "run_7f3/round-1/tune"
    status: OperationStatus
    cost: BudgetUsage                   # charged to the run's budget as it accrues
    created_at: datetime
    finished_at: datetime | None
    result: dict | None                 # AgentResult or TrainResult once succeeded
    error: dict | None                  # set when failed
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "op_19a",
  "run_id": "run_7f3",
  "kind": "agent_session",
  "idempotency_key": "run_7f3/round-1/tune",
  "status": "succeeded",
  "cost": {"usd": 1.12, "wall_clock_s": 640, "rollouts": 0, "tokens": 88100},
  "created_at": "2026-10-08T02:12:40Z",
  "finished_at": "2026-10-08T02:23:20Z",
  "result": {
    "definition": "clawevolve-tune",
    "exit_status": "completed",
    "transcript_artifact": "art_tune_r1",
    "changed_files": ["persona/SOUL.md", "skills/refund-policy/SKILL.md"]
  },
  "error": null
}
```

### 2.7 Workspace

A **workspace** is a sandbox materialisation of a genome revision: the
revision's files laid out for an agent to edit, isolated from the live bot.
Materialising is idempotent per `(run, key)`, so a re-dispatched run gets
the same sandbox back, including edits an agent operation already made.

```python
@dataclass(frozen=True)
class Workspace:
    id: str                             # "ws_7f3_r1"
    run_id: str
    key: str                            # "run_7f3/round-1"
    revision: str                       # revision it was materialised from
    created_at: datetime
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"id": "ws_7f3_r1", "run_id": "run_7f3", "key": "run_7f3/round-1",
 "revision": "sha256:a90b…", "created_at": "2026-10-08T02:12:31Z"}
```

### 2.8 RunSummary

```python
@dataclass(frozen=True)
class RunSummary:
    rounds: int | None = None           # strategy-defined; None if the strategy has no rounds
    notes: str = ""
    extra: dict = field(default_factory=dict)   # strategy-specific, shown to owners
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"rounds": 3, "notes": "two candidates submitted; round 2 accepted", "extra": {"findings": 5}}
```

## 3. Evolution policy and bindings

A bot's **evolution policy** is a list of **bindings**. Different bots use
different strategies, and one bot may use several (for example ClawEvolve
nightly on persona and skills, and memory consolidation weekly on memory);
what several bindings mean is explained in §2.3.

Information about a strategy is split by who it is true of:

- **Facts about code are registered.** "ClawEvolve 2.0.0 drives OpenClaw
  agents and reads conversation history" is true no matter which bot uses
  it, so it is recorded once in the strategy's registration record
  ([03-strategy.md](03-strategy.md)).
- **Choices about bots are configured.** "bot_123 runs ClawEvolve nightly,
  may change persona and skills, and has a $20 budget" is a decision about
  one bot, so it lives in the bot's binding, owned by the bot owner, and can
  change at any time without touching the strategy.

Triggers, parent choice, allowed genes, and verification strictness are
binding fields owned by the bot owner. They are not strategy code.

### 3.1 Binding fields

| Field | Meaning | Rules |
| --- | --- | --- |
| `id` | Stable id of the binding within the bot | Chosen by the owner; unique within the bot; runs refer to it |
| `strategy` | `<id>@<version>` of a registered strategy version | Must be registered and have passed the conformance kit ([03-strategy.md](03-strategy.md)); not disabled by a kill switch |
| `trigger` | When runs are created ([§4](#4-triggers)) | One of schedule, manual, event |
| `parent` | Which revision a run starts from | `active` in the first iteration. Other selectors (latest-best, Pareto front, MAP-Elites niches, clade scores) are later options that query the ledger ([05-experiment-ledger.md](05-experiment-ledger.md)) |
| `allowed_genes` | What this strategy may change on this bot | Must stay within the bot's genome `policy`: locked genes stay locked ([01-genome.md](01-genome.md)) |
| `verification_profile` | Which verification profile judges candidates | Owners may pick a stricter profile than the platform default, never a looser one ([07-verification.md](07-verification.md)) |
| `budget` | Per-run limits | Must fit within per-bot and per-tenant ceilings ([§11](#11-budgets-and-kill-switches)) |
| `params` | Strategy parameters | Validated against the registration's `params_schema` when present (check 8, §3.2), and by the strategy |
| `auto_promote_ceiling` *(proposed)* | Highest risk tier Promotion may promote without a human | `T0`, `T1` (default), `T2`, or `null` (always review); never `T3`. Read by Promotion ([08-promotion.md](08-promotion.md)) |
| `rollout` *(proposed)* | Canary share and the auto-rollback rule | Multi-instance bots only; `null` promotes `active` directly. Read by Promotion ([08-promotion.md](08-promotion.md)) |

### 3.2 Binding checks

When the policy is written, every new or changed binding is checked against
the bot. A mismatch is rejected at configuration time, not partway through a
paid run:

1. The strategy version is registered, conformant, and not disabled.
2. Every capability in the strategy's `needs` has a provider for the bot's
   engine (for example, the session export provides `experience.sessions`
   for OpenClaw).
3. If the strategy declares `agents@1`, the bot's engine is among the
   `engine` values of its agent definitions. There is no separate
   `supports_engines` list; engine compatibility is derived from `needs`.
4. `allowed_genes` is within the bot's genome `policy`: no locked gene, no
   pinned item.
5. `verification_profile` exists and is not looser than the platform
   default.
6. `budget` fits within the per-bot and per-tenant ceilings.
7. `trigger` is well formed: a valid cron expression, or an `EventName`
   (§2.2).
8. If the strategy's registration record has a `params_schema` (proposed,
   [03-strategy.md](03-strategy.md)), `params` validates against it.

The same checks run again when a run is submitted, because the bot (its
engine, its genome `policy`) or the strategy (a kill switch) may have
changed since the policy was written. ClawEvolve's
`active_engine='openclaw'` and `bot_type='personal'` filters in
`singlebox/bot-runtime.ts` become check 2.

## 4. Triggers

A **trigger** is what makes a binding create a run. Each firing is an
ordinary idempotent run submission ([§6](#6-idempotent-submission-and-status-by-id)),
made by the platform instead of a caller.

| Trigger | JSON | Fires when | Idempotency key |
| --- | --- | --- | --- |
| Schedule | `{"schedule": "0 2 * * *"}` | The cron expression matches (UTC) | `<binding_id>/<scheduled_fire_time>`, e.g. `bind_01/2026-10-08T02:00:00Z` |
| Manual | `{"manual": true}` | Never automatically; only when a caller submits a run | Chosen by the caller |
| Event | `{"event": "failure_rate_alert"}` | A platform event with that name arrives for the bot | `<binding_id>/<event_id>` (proposed) |

Examples of event triggers from the default strategies: a failure-rate
alert (`failure_rate_alert`), a ClawInsight improvement item
(`clawinsight_improvement_item`, an event trigger for ClawEvolve bindings),
and "N new feedback items" for memory consolidation
(`feedback_threshold_reached`)
([04-default-strategies.md](04-default-strategies.md)). These three are the
proposed `EventName` values (§2.2).

Rules:

- **A firing is safe to repeat.** The scheduler may fire the same schedule
  slot twice (for example after its own restart); the key
  `<binding_id>/<scheduled_fire_time>` makes the second firing return the
  existing run.
- **Any binding can also be submitted manually.** `{"manual": true}` means
  only that nothing fires automatically (proposed).
- **Missed firings are skipped, not backfilled** (proposed). If the
  service, the bot, or the strategy was paused when a slot passed, the slot
  is recorded as skipped and the next slot fires normally.
- **One active run per binding** (proposed). If a binding's previous run is
  still `queued` or `running` when the trigger fires, the firing is recorded
  as skipped. Manual submissions for the same binding are refused with
  `409` while one is active. See [§17](#17-open-decisions).

The fast-loop path (a subject bot requesting a run of itself) is postponed
with bot callers (DR-3,
[decisions/0003](decisions/0003-bot-principal-for-evolution-surface.md)).

## 5. Run lifecycle

```text
queued → running → completed | failed | cancelled | budget_exhausted
```

There is no "waiting" state. A strategy that is waiting for a verdict or an
operation is `running`, and that time counts against its wall clock.

| Status | Meaning |
| --- | --- |
| `queued` | Recorded with frozen inputs; waiting for a worker to claim it (first time, or after a lost lease) |
| `running` | A worker holds the lease and `run(ctx)` is executing |
| `completed` | `run(ctx)` returned a `RunSummary` |
| `failed` | The strategy reported a non-retryable failure, or `max_attempts` was reached |
| `cancelled` | A caller, a kill switch, or an escalation rule stopped it |
| `budget_exhausted` | A budget dimension ran out |

### 5.1 Start

At submission the service:

1. Runs the binding checks again ([§3.2](#32-binding-checks)) and the
   kill-switch checks ([§11.3](#113-kill-switches)).
2. **Freezes** the strategy version, params, parent, allowed genes,
   verification profile, and budget. The parent ref (`active`) is resolved
   to a revision id now, so a promotion during the run does not change what
   the run started from.
3. Records the agent definition digests of the strategy version, so results
   are attributable to the exact prompts used.
4. **Reserves** the budget against per-bot and per-tenant ceilings.
5. Records the run as `queued`, with its first job, and returns the run id.

On each dispatch (claim) the service builds the `StrategyContext` with
exactly the granted capabilities: the always-granted parts (`parent`,
`workspace`, `operations`, `budget`, `log`, `artifacts`, `cancelled`), the
always-granted capabilities `candidates@1` and `models@1`, and the
capabilities declared in the strategy's `needs`. Access to anything else
raises `CapabilityNotGranted` in process, or returns `403` over the Job
Protocol. The context also carries `run_id`, `params`, and `attempt`.

### 5.2 During

- Every model call, agent session, and evaluation is charged to the budget
  (§11.1).
- Every candidate the strategy submits is validated and recorded before
  `candidates.submit` returns (§5.4).
- The platform guarantees that nothing hidden reaches the strategy: no
  holdout, regression, or safety cases, and validation results only as
  aggregates. This is enforced by what the context and the Job Protocol
  return, not by prompts.

### 5.3 End

- **Normal end.** `run(ctx)` returns a `RunSummary` (`POST
  …/jobs/{id}/complete`); the run is `completed`.
- **Failure.** The strategy raises or calls `POST …/jobs/{id}/fail`. A
  retryable failure re-queues the run like a lost lease (§7); a
  non-retryable one, or the last allowed attempt, ends it as `failed`.
- **Cancellation.** A caller calls `POST …/runs/{run}:cancel`, or a kill
  switch or escalation rule fires. The context's `cancelled` token is set;
  the strategy is expected to stop promptly (the conformance kit tests
  this). After a grace period the lease is revoked. The run ends as
  `cancelled`.
- **Budget.** When any budget dimension runs out, the next charged call
  raises `BudgetExhausted`; the run ends as `budget_exhausted`.

In every case:

- Submissions made before a failure, cancellation, or budget stop **are
  kept and still verified**.
- **Unfinished operations are cancelled** (§8).
- Every submission, accepted or rejected, is recorded in the Experiment
  Ledger H with the strategy version
  ([05-experiment-ledger.md](05-experiment-ledger.md)).

### 5.4 Candidates submitted by a run

`ctx.candidates.submit(candidate)` (Job Protocol `POST
…/runs/{run}/candidates`) is a short call. Before it returns, the service:

1. Validates the patch against the patch schema, checks that its `base` is
   a revision of this bot reachable from the run, and checks that every op
   stays within the run's frozen `allowed_genes`. A violation returns `422`.
2. Computes the **candidate id**: the content hash of the patch. A repeated
   submission of the same patch (including one after a re-dispatch) returns
   the same id and creates nothing new.
3. Records the candidate revision in the Genome Registry and points the ref
   `candidate/<run>/<n>` at it ([01-genome.md](01-genome.md)).
4. Calls Promotion's static floor (`FloorCheck`, through
   `PromotionService.check_floor`, [08-promotion.md](08-promotion.md)) on the
   recorded candidate, before verification spends anything. A floor failure
   makes the verdict `reject` without running any suite; the result is
   stored on the `GateDecision` and in the ledger.
5. Persists the candidate on the run and in the ledger, then returns the id.

If the floor passed, verification then runs asynchronously: the service calls
`verify(candidate, profile)` with the run's frozen verification profile
([07-verification.md](07-verification.md)), and hands the verdict to the
gate ([08-promotion.md](08-promotion.md)). The strategy looks the verdict up
by candidate id (`ctx.candidates.verdict(id)`); there is no callback and no
blocking call. It sees status (`pending | accept | reject | inconclusive`)
and validation aggregates only, never per-case hidden data. What a strategy
submits is its own choice; whether a candidate is accepted is the
platform's.

## 6. Idempotent submission and status by id

Starting a run (a trigger firing, or a caller through the API) returns a
**run id**. Submission is idempotent, and the platform guarantees it:

- The submitter sends an **idempotency key**: a string chosen by the
  submitter, identical for every retry of one logical request and different
  for different requests.
- The platform stores `(owner_id, bot_id, key) → run id`. A repeated submission with the
  same key returns the same run id and starts nothing new, whatever the
  first run's status is now.
- From then on the run id is the only handle. Callers look up status,
  candidates, and verdicts by it. No callback channel is needed; the CLI's
  `--wait` only repeats the lookup.

Where keys come from:

| Submitter | Key | Example |
| --- | --- | --- |
| Pipeline / CI | A UUID created once before the first attempt, or a deterministic name | `nightly-bot_123-2026-10-08` |
| Platform trigger | `<binding_id>/<scheduled_fire_time>` | `bind_01/2026-10-08T02:00:00Z` |
| Strategy (operations, workspaces) | `<run_id>/<own step>` | `run_7f3/round-2/tune` |

A key must never contain something that changes between retries, such as a
send-time timestamp. Reusing a key with a different request body is
refused with `409` (proposed; the shared rules are in
[09-evolution-api.md](09-evolution-api.md)).

The same pattern holds one level down: `ctx.candidates.submit` returns a
candidate id (the content hash of the patch), and verdicts are looked up by
that id; operation starts take an idempotency key and return an operation
id (§8).

## 7. Leases, re-dispatch, and crash recovery

A run must survive its process dying: a worker crash, hardware failure, or
reboot. The work is split between the platform and the strategy:

| Concern | Owner | How |
| --- | --- | --- |
| The run record, its frozen inputs, budget spent, and candidates submitted | Platform | Persisted before run submission or `candidates.submit` returns |
| Noticing that a run's process died | Platform | Every attempt of a run is a **leased job**. The worker (or the in-process host) renews the lease; when it expires, the run goes back to `queued` and is dispatched again as a new job with the same run id and `ctx.attempt + 1`. A fencing token rejects calls from the old holder. After `max_attempts` the run ends as `failed` |
| Agent sessions and train evaluations already started | Platform | They are operations (§8): persisted and run by the platform, independent of the strategy's process. They keep running across a re-dispatch; the strategy re-attaches by repeating the start with the same idempotency key |
| The strategy's own progress (round number, search state, history) | Strategy | The strategy persists whatever it needs in **its own storage**, keyed by run id, and on re-dispatch reloads it and continues. The platform has **no checkpoint API** and never reads this state; its shape differs from strategy to strategy |

### 7.1 Leases

- A worker claims a job (`POST /evolution/v1/jobs:claim`) and receives the
  run's frozen inputs, its `attempt`, and a fresh **fencing token**.
- The worker renews the lease with `POST …/jobs/{id}/heartbeat`. Proposed
  defaults: lease 60 s, heartbeat every 20 s.
- The in-process runtime renews the lease from its host loop the same way,
  so both runtimes recover identically.
- Every run-scoped Job Protocol call carries the fencing token. A call with
  a stale token returns `409`; the old holder must stop.

### 7.2 Re-dispatch

When a lease expires, the service:

1. Increments `attempt` and sets the run back to `queued` (if attempts
   remain; otherwise ends it as `failed` with `end_reason: "max_attempts"`).
   Proposed default `max_attempts`: 3.
2. Leaves operations running, workspaces in place, and candidates recorded.
3. Issues a new fencing token on the next claim, invalidating the old one.

The re-dispatched strategy reloads its own state by run id, re-materialises
its workspaces with the same keys (getting the same sandboxes back),
repeats operation starts with the same idempotency keys (getting the same
operations back, finished or not), and resubmits any candidate it is unsure
about (getting the same candidate id back). The conformance kit tests
exactly this: killing a strategy mid-run and dispatching the same run id
again neither duplicates candidates or operations nor exceeds the budget
([03-strategy.md](03-strategy.md)).

### 7.3 Budget across attempts

A re-dispatched run uses the same frozen inputs and the same budget: what
earlier attempts spent stays spent. Wall clock is measured from the run's
first start, including time spent `queued` between attempts (proposed), so
a crash loop cannot extend a run beyond `max_wall_clock_s`.

## 8. Long-running operations (platform side)

Some capability calls do work that takes minutes or longer: an agent
session (`agents.start`) or a train evaluation over many rollouts
(`evaluate.start_train`). They never hold a request open while the work
runs. How a strategy uses them is in [03-strategy.md](03-strategy.md); this
section is what the platform guarantees.

- **Start returns an id at once.** The start call records an operation,
  hands the work to the platform, and returns its operation id. Over the
  Job Protocol this is `202` with the id.
- **Status is looked up by id.** `ctx.operations.get(op_id)` returns the
  status (`queued`, `running`, `succeeded`, `failed`, or `cancelled`) and,
  once it has succeeded, the result. Every lookup is a short request. The
  SDK helper `ctx.operations.wait(op_id)` repeats short lookups; it never
  holds one request open.
- **Start is idempotent.** It takes an idempotency key. The platform stores
  `(run, key) → operation id`; repeating a start with the same key returns
  the same operation, finished or not, instead of starting and paying for
  the work again.
- **Operations belong to the platform and the run.** They are persisted and
  executed by the platform, independent of the strategy's process, so a
  strategy crash does not stop them. Their cost is charged to the run's
  budget as it accrues. When the run ends, its unfinished operations are
  cancelled.
- **Workspaces follow the same rule.** `ctx.workspace.materialise(revision,
  key)` is idempotent per key, so a re-dispatched run gets back the same
  sandbox, including the edits an agent operation already made.

Calls that are always quick stay plain request and response: reading
experience, adding train cases, `candidates.submit`, `candidates.verdict`,
`models.complete` (bounded by `max_tokens`), and budget calls. The catalog
contract of each capability states which of its calls are operations; any
call whose work can outlast a short request must be one.

**Agent operations.** `agents.start(definition, workspace=…, prompt=…,
idempotency_key=…, timeout_s=…)` makes the engine's `agents@1` provider
load the named agent definition by digest into the sandbox next to the
workspace. The definition is read-only to the agent; only the workspace is
writable. A definition the strategy did not register is refused. When the
operation succeeds, its result holds the transcript and exit status; the
files the agent changed stay in the workspace until the strategy turns them
into a patch. Where definitions come from and how they are validated is in
[03-strategy.md](03-strategy.md).

**Train-evaluation operations.** `evaluate.start_train(workspace,
idempotency_key=…)` runs the Verification Service on the **train split
only**, with scores and critiques ([07-verification.md](07-verification.md)).
Validation, holdout, regression, and safety stay hidden.

## 9. Runtimes and the Job Protocol

A strategy version's registration record names its runtime:

| `runtime.kind` | How it runs | How `ctx` reaches it |
| --- | --- | --- |
| `in_process` | Python package loaded by the `apps/evolution` composition root, selected by configuration (R5/R14) | Direct Python objects |
| `job_worker` | Container image (any language) | The **Job Protocol**: each `ctx` call maps to one HTTP endpoint |

Both runtimes see the same run lifecycle, leases, idempotency, and budget
rules; the conformance kit runs against both `StrategyContext`
implementations (in-process and Job-Protocol) shipped in the strategy SDK.

### 9.1 The Job Protocol

The Job Protocol is an **internal** Plugin API (wire form) between the
service and job-worker strategies. Its endpoints live under
`/evolution/v1`, not under the public `/openapi/v1`. Each is listed with an
example in [§14.2](#142-internal-api-job-protocol).

| Endpoint | `ctx` call |
| --- | --- |
| `POST /evolution/v1/jobs:claim` | (worker loop) claim a job: `{worker_id, strategy_ids[]}` → `{job_id, run_id, attempt, params, parent, budget, granted, fencing_token}` |
| `POST /evolution/v1/jobs/{id}/heartbeat` | lease renewal; an expired lease re-queues the run as a new job |
| `GET /evolution/v1/runs/{run}/parent` | `ctx.parent` |
| `GET /evolution/v1/runs/{run}/content/{digest}` | file bytes of the parent / workspace |
| `GET /evolution/v1/runs/{run}/experience/sessions` | `ctx.experience.sessions` (if granted) |
| `GET /evolution/v1/runs/{run}/experience/feedback` | `ctx.experience.feedback` (if granted) |
| `POST /evolution/v1/runs/{run}/workspaces` | `ctx.workspace.materialise {revision, key} → {workspace_id}` (idempotent) |
| `GET /evolution/v1/runs/{run}/workspaces/{workspace}/files` | list a workspace's files → `[{path, digest}]` (file bytes through `content/{digest}`) |
| `PUT /evolution/v1/runs/{run}/workspaces/{workspace}/files/{path}` | `ws.write(path, bytes) → {digest}` |
| `POST /evolution/v1/runs/{run}/workspaces/{workspace}:patch` | `ws.to_patch() → {patch}` (the Genome Patch against the workspace's base) |
| `POST /evolution/v1/runs/{run}/log` | `ctx.log`: structured log lines |
| `PUT /evolution/v1/runs/{run}/artifacts/{name}` | `ctx.artifacts`: upload an artifact (bytes) |
| `POST /evolution/v1/runs/{run}/agents:start` | `ctx.agents.start → 202 {operation_id}` (if granted; idempotent) |
| `POST /evolution/v1/runs/{run}/evaluations:train` | `ctx.evaluate.start_train → 202 {operation_id}` (if granted; idempotent) |
| `POST /evolution/v1/runs/{run}/evaluations/cases` | `ctx.evaluate.add_train_cases` (if granted) |
| `GET /evolution/v1/runs/{run}/operations/{id}` | `ctx.operations.get → {status, result?}` |
| `POST /evolution/v1/runs/{run}/operations/{id}:cancel` | `ctx.operations.cancel` |
| `POST /evolution/v1/runs/{run}/models:complete` | `ctx.models.complete → completion` |
| `POST /evolution/v1/runs/{run}/candidates` | `ctx.candidates.submit → {candidate_id}` (idempotent) |
| `GET /evolution/v1/runs/{run}/candidates/{id}` | `ctx.candidates.verdict → {status, aggregates}` |
| `POST /evolution/v1/runs/{run}/budget:charge` | `ctx.budget.charge` |
| `POST /evolution/v1/jobs/{id}/complete` | `run(ctx)` returned a `RunSummary` |
| `POST /evolution/v1/jobs/{id}/fail` | `run(ctx)` failed: `{reason, retryable}` |

Rules for every endpoint:

- **Every request returns promptly.** Work that can outlast a short request
  is an operation: its start returns `202` with an operation id, and the
  worker looks up its status by id. No request is held open while an agent
  session or evaluation runs.
- **JSON with JSON Schemas.** All payloads are JSON (except the raw bytes
  of `content/{digest}`), each with a published JSON Schema.
- **No bot credentials.** A worker gets no credentials to the bot.
- **`403`** for an endpoint of a capability that was not granted.
- **`409`** for a call with a stale fencing token.
- **Fencing token transport** (proposed): every call after the claim sends
  the header `Evolution-Fencing-Token: <token>`.
- **Cancellation reaches the worker through the heartbeat** (proposed): the
  heartbeat response carries `cancelled: true`, which the SDK turns into
  `ctx.cancelled`.

Workers are platform-run containers. Bots acting as workers ("runner bots")
are postponed together with bot callers (DR-3,
[decisions/0003](decisions/0003-bot-principal-for-evolution-surface.md)).

## 10. Sandboxing

Strategies are untrusted code proposing changes from untrusted data. The
service is where isolation is enforced, whatever the strategy is.

- **Sandbox materialisations only.** Strategies and evaluators work only on
  sandbox materialisations: ephemeral workspaces from
  `ctx.workspace.materialise`, or eval bots via `eval_env` used by the
  Verification Service ([07-verification.md](07-verification.md)). No
  production credentials and no access to the live phenotype (the running
  bot). A strategy never changes the live bot, so ClawEvolve's
  baseline-pack / restore / pack / deploy steps leave the flow.
- **Exactly the granted capabilities.** Each strategy declares the catalog
  capabilities it `needs` and its runtime isolation (R13); its context
  grants nothing else.
- **No egress, no model keys, no own agent runtime.** Strategies have no
  network egress or model keys of their own. Model calls go through
  `ctx.models` and agent sessions through `ctx.agents`, both charged to the
  run's budget and recorded. This is a deliberate limitation: a strategy
  can use only what the capability catalog provides. Its own computation
  (parsing, search, ranking) is unrestricted. A new need is met by adding a
  catalog entry once a second strategy needs it, not by an exception for
  one strategy.
- **Agent definitions are read-only.** An agent operation's definition is
  loaded by digest next to the workspace and is read-only; only the
  workspace is writable.
- **Hidden data stays hidden.** Holdout, regression, and safety cases never
  reach a strategy; validation is exposed only as aggregates. The context
  and the Job Protocol enforce this, not prompts.
- **Experience is untrusted input.** Episode text is a prompt-injection and
  memory-poisoning vector. Strategies must treat it as data. Patches derived
  from it are scanned for secrets, PII, and new URLs before they can be
  promoted ([08-promotion.md](08-promotion.md)). Rate limits for inbox
  submissions from subject bots are postponed with bot callers.
- **Job-worker isolation** (proposed): a job-worker container runs with no
  network route except to the Job Protocol endpoint, no mounted secrets,
  and a read-only root filesystem apart from its scratch directory. The
  strategy's own progress store is the one storage it is allowed to reach;
  how it reaches it is open (ER-10, [§17](#17-open-decisions)).

## 11. Budgets and kill switches

Budgets are enforced by the service, not by the strategy.

### 11.1 Per-run budget

- **Dimensions:** USD, wall clock, evaluation rollouts, and tokens
  (`Budget`, §2.1). Usage is reported on the run (`budget_used`).
- **Metering:** the platform charges every call it serves: `models.complete`
  (tokens and USD, with the model used recorded), agent operations, and
  train evaluations (rollouts and USD). `ctx.budget.charge` records an
  explicit charge the strategy reports (proposed semantics: costs incurred
  through the platform that the platform cannot meter itself; it can only
  add to spend, never refund).
- **Reservation:** the whole budget is reserved against per-bot and
  per-tenant ceilings at submission and released (unspent part) at the end.
- **Exhaustion:** when a dimension runs out, the next charged call fails
  with `BudgetExhausted` (Job Protocol: `402`, `error.code:
  "budget_exhausted"`, proposed), unfinished operations are cancelled, and
  the run ends as `budget_exhausted`. Verification of candidates already
  submitted is not charged to the run and still happens.
- **Waiting counts.** Time a strategy spends looking up verdicts or
  operations counts against `max_wall_clock_s`.

### 11.2 Per-bot and per-tenant ceilings

- Daily and monthly spend ceilings per bot and per tenant. A run whose
  budget does not fit in the remaining ceiling is refused at submission
  (`409`, `error.code: "ceiling_exceeded"`, proposed) and a trigger firing
  is recorded as skipped.
- Max promotions per day per bot is enforced by Promotion
  ([08-promotion.md](08-promotion.md)).
- **Escalation:** N consecutive rejected candidates in a run (proposed
  default N = 5) stop the run and notify the owner. The run ends as
  `cancelled` with `end_reason: "consecutive_rejections"` (proposed).

Where the ceilings are configured is open ([§17](#17-open-decisions)); the
proposal is a `limits` field on the evolution policy for per-bot ceilings
and tenant settings for per-tenant ones.

### 11.3 Kill switches

| Switch | Effect | Who |
| --- | --- | --- |
| **Per strategy** (disable everywhere) | The strategy version (or all versions of a strategy) can no longer be bound, submitted, or claimed; its running runs are cancelled | Platform operators; recorded on the registration in the Strategy Registry ([03-strategy.md](03-strategy.md)), enforced here |
| **Per bot** (freeze evolution, keep `active`) | Triggers for the bot stop firing, submissions are refused (`409`, `error.code: "evolution_frozen"`), running runs are cancelled. The bot keeps running its `active` revision | Bot owner, tenant admin |
| **Global** (pause orchestrator) | No claims are served, no triggers fire, no runs are submitted; running runs keep their records and resume (by re-dispatch) after the pause | Platform operators |

A frozen or paused state never moves any genome ref. Pending verdicts of
candidates already submitted still complete; promotion of them follows the
gate ([08-promotion.md](08-promotion.md)).

## 12. Storage

Proposed, in `apps/evolution` (SQLite in the singlebox local profile, a
server database elsewhere):

| Table | Key | Holds |
| --- | --- | --- |
| `evolution_policy` | `(owner_id, bot_id)` | The bindings document and its ETag |
| `evolution_run` | `run_id` | The `Run` record: frozen inputs, status, attempt, usage |
| `evolution_run_key` | `(owner_id, bot_id, idempotency_key)` | `run_id`; makes submission idempotent |
| `evolution_job` | `job_id` | Lease holder, expiry, fencing token, attempt |
| `evolution_operation` | `operation_id`, unique `(run_id, idempotency_key)` | The `Operation` record |
| `evolution_workspace` | `workspace_id`, unique `(run_id, key)` | The `Workspace` record; sandbox files live in the executor |
| `evolution_run_candidate` | `(run_id, candidate_id)` | Order `n` (for `candidate/<run>/<n>`), verdict status cache |
| `evolution_budget_charge` | append-only | Every charge: run, source (model call, operation, explicit), amount |
| `evolution_trigger_firing` | `(binding_id, fire_key)` | Fired / skipped firings and why |

Every write that a response depends on (run submission, candidate
submission, operation start, workspace materialisation) is committed
before the response returns; a failed write returns an error, never a
silent success.

## 13. Service interface

The Python Protocols below are what other parts of the platform (the API
layer in [09-evolution-api.md](09-evolution-api.md), the trigger scheduler,
the Job Protocol adapter, the in-process host) call. They are
transport-agnostic: the HTTP endpoints in §14 are delivery adapters over
them.

```python
class EvolutionPolicyService(Protocol):
    async def get_policy(self, bot: BotRef) -> EvolutionPolicy:
        """Return the bot's policy; an empty bindings list if none was written."""

    async def put_policy(self, bot: BotRef, bindings: list[Binding], *,
                         expected_etag: str | None, actor: str) -> EvolutionPolicy:
        """Replace the bindings after running the binding checks (§3.2) on every
        new or changed binding. expected_etag None means "create; fail if one exists".
        Raises BindingCheckFailed (all violations listed) or PreconditionFailed."""


class RunService(Protocol):
    async def submit(self, bot: BotRef, binding_id: str, *, idempotency_key: str,
                     trigger: TriggerRecord, params: dict | None = None,
                     budget: Budget | None = None) -> str:
        """Create a run of the binding and return its run id, or return the existing
        run id for (owner_id, bot_id, idempotency_key). params are merged over the binding's params;
        budget, when given, must not exceed the binding's budget (None: use the binding's).
        Freezes inputs, reserves the budget, records the run as queued.
        Raises BindingCheckFailed, EvolutionFrozen, CeilingExceeded, BindingBusy,
        IdempotencyKeyReused."""

    async def get(self, bot: BotRef, run_id: str) -> Run: ...

    async def list(self, bot: BotRef, *, status: list[RunStatus] | None = None,
                   binding_id: str | None = None, page: int = 1,
                   page_size: int = 20) -> Page[Run]: ...

    async def cancel(self, bot: BotRef, run_id: str, *, reason: str, actor: str) -> Run:
        """Set the run's cancellation token; cancel its unfinished operations;
        end it as cancelled. Idempotent on an already cancelled run; raises
        RunAlreadyEnded for completed/failed/budget_exhausted runs."""

    async def candidates(self, bot: BotRef, run_id: str) -> list[RunCandidate]:
        """Candidates the run submitted, in order, with verdict status."""


class JobService(Protocol):
    """The dispatch side, used by the Job Protocol adapter and the in-process host."""

    async def claim(self, worker_id: str, strategy_ids: list[str]) -> ClaimedJob | None:
        """Lease one queued job of the given strategies; None if there is none.
        Increments nothing: attempt was set when the run was (re-)queued."""

    async def heartbeat(self, job_id: str, fencing_token: str) -> LeaseState:
        """Renew the lease; returns new expiry and whether the run was cancelled.
        Raises StaleFencingToken."""

    async def complete(self, job_id: str, fencing_token: str, summary: RunSummary) -> None: ...

    async def fail(self, job_id: str, fencing_token: str, *, reason: str,
                   retryable: bool) -> None:
        """Retryable with attempts left: re-queue (attempt + 1). Otherwise end as failed."""

    async def expire_leases(self, now: datetime) -> list[str]:
        """Called by the service's own timer: for every job whose lease has expired,
        re-queue its run (a new job, attempt + 1) or fail the run. Returns the affected run ids."""


class OperationService(Protocol):
    async def start(self, run_id: str, kind: Literal["agent_session", "train_evaluation"], *,
                    idempotency_key: str, request: dict) -> str:
        """Record the operation and hand it to its executor; return the operation id.
        Returns the existing id for (run_id, idempotency_key)."""

    async def get(self, run_id: str, operation_id: str) -> Operation: ...

    async def cancel(self, run_id: str, operation_id: str) -> Operation: ...

    async def cancel_unfinished(self, run_id: str) -> int:
        """Called when a run ends. Returns how many were cancelled."""


class BudgetService(Protocol):
    async def reserve(self, bot: BotRef, tenant: str, budget: Budget) -> None:
        """Raises CeilingExceeded."""

    async def charge(self, run_id: str, usage: BudgetUsage, *,
                     source: Literal["model_call", "operation", "explicit"]) -> BudgetUsage:
        """Add to the run's spend and return what remains. Raises BudgetExhausted
        once any dimension is used up (the run is then ended by the caller)."""

    async def remaining(self, run_id: str) -> BudgetUsage: ...


class KillSwitches(Protocol):
    async def is_strategy_disabled(self, strategy: str, version: str) -> bool: ...
    async def is_bot_frozen(self, bot: BotRef) -> bool: ...
    async def is_paused(self) -> bool: ...
```

Ports this service depends on (defined by the owning docs):

| Port | Used for | Owner |
| --- | --- | --- |
| Genome Registry (create revision from `{base, patch}`, move `candidate/<run>/<n>`, resolve `active`, read content) | Freezing the parent; recording candidates; materialising workspaces | [01-genome.md](01-genome.md) |
| Experience queries | Serving `experience.sessions` / `experience.feedback` | [02-experience.md](02-experience.md) |
| Strategy Registry (registration record, conformance status, agent definition digests, capability providers per engine) | Binding checks; building the context; loading agent definitions | [03-strategy.md](03-strategy.md) |
| Ledger append | Recording runs, submissions, verdicts, costs, models used | [05-experiment-ledger.md](05-experiment-ledger.md) |
| `verify(candidate, profile)`, train evaluation | Verdicts; `evaluate.train` operations | [07-verification.md](07-verification.md) |
| Static floor (`FloorCheck`, via `PromotionService.check_floor`) | Rejecting a candidate at submission, before verification | [08-promotion.md](08-promotion.md) |
| Gate intake | Handing verdicts to the gate and review queue | [08-promotion.md](08-promotion.md) |

## 14. API

### 14.1 Public API

Public endpoints are under the prefix `/openapi/v1`; paths below are
relative to it. They follow the shared conventions (error envelope, ETags
on mutable resources, `Idempotency-Key` header on creating POSTs) in
[09-evolution-api.md](09-evolution-api.md). Callers are pipelines and CI,
the UI backend, and humans through the `avn` CLI. Caller authentication and
authorization are not specified in this design set; bot callers are
postponed (DR-3). Responses below show the `data` payload of the standard
envelope; see [09-evolution-api.md](09-evolution-api.md) for the envelope,
errors, pagination, and idempotency.

### GET /bots/{bot_id}/evolution/policy

Read the bot's evolution policy (its bindings). Called by the UI backend,
`avn evolve policy get`, and pipelines.

Request:

```http
GET /openapi/v1/bots/bot_123/evolution/policy
```

Response `200` (header `ETag: "pol-v12"`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "bindings": [
    {
      "id": "bind_01",
      "strategy": "clawevolve/bot-evolution@2.0.0",
      "trigger": {"schedule": "0 2 * * *"},
      "parent": "active",
      "allowed_genes": ["persona", "skills"],
      "verification_profile": "default@1",
      "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
      "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60}
    }
  ],
  "etag": "\"pol-v12\"",
  "updated_at": "2026-10-06T08:00:00Z",
  "updated_by": "user_owner_5"
}
```

Errors: `404` unknown bot.

### PUT /bots/{bot_id}/evolution/policy

Replace the bot's bindings. Runs the binding checks (§3.2) on every new or
changed binding and rejects the whole document if any fails. Called by the
bot owner or tenant admin through the UI or `avn evolve policy set`.

Request (header `If-Match: "pol-v12"`):

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
      "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60}
    },
    {
      "id": "bind_02",
      "strategy": "platform/consolidate-memory@1.0.0",
      "trigger": {"schedule": "0 4 * * 0"},
      "parent": "active",
      "allowed_genes": ["memory"],
      "verification_profile": "default@1",
      "budget": {"max_usd": 5, "max_wall_clock_s": 1800},
      "params": {}
    }
  ]
}
```

Response `200` (header `ETag: "pol-v13"`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "bindings": [
    {"id": "bind_01", "strategy": "clawevolve/bot-evolution@2.0.0", "trigger": {"schedule": "0 2 * * *"},
     "parent": "active", "allowed_genes": ["persona", "skills"], "verification_profile": "default@1",
     "budget": {"max_usd": 20, "max_wall_clock_s": 7200}, "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60}},
    {"id": "bind_02", "strategy": "platform/consolidate-memory@1.0.0", "trigger": {"schedule": "0 4 * * 0"},
     "parent": "active", "allowed_genes": ["memory"], "verification_profile": "default@1",
     "budget": {"max_usd": 5, "max_wall_clock_s": 1800}, "params": {}}
  ],
  "etag": "\"pol-v13\"",
  "updated_at": "2026-10-08T09:30:00Z",
  "updated_by": "user_owner_5"
}
```

Error example `422` (binding check failed):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "error": {
    "code": "binding_check_failed",
    "message": "binding bind_01 is not valid for bot_123",
    "details": [
      {"binding": "bind_01", "check": "allowed_genes", "detail": "gene 'tools.mcp' is locked by the bot's genome policy"},
      {"binding": "bind_01", "check": "engine", "detail": "agents@1 definitions target engine 'openclaw'; bot engine is 'hermes'"}
    ]
  }
}
```

Other errors: `412` ETag mismatch (someone else changed the policy); `404`
unknown bot or unknown strategy version.

### POST /bots/{bot_id}/evolution/runs

Submit a run of one of the bot's bindings. The body is
`{binding, params?, budget?}`. Idempotent: the
`Idempotency-Key` header is required; a repeat with the same key returns
the same run id and starts nothing. Called by pipelines and CI, the UI
backend, and `avn evolve run start`. Triggers use the same service
operation internally (§4).

Request (header `Idempotency-Key: nightly-bot_123-2026-10-08`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "binding": "bind_01",
  "params": {"max_rounds": 2},                 // optional; merged over the binding's params
  "budget": {"max_usd": 10, "max_wall_clock_s": 3600}   // optional; must not exceed the binding's budget
}
```

Response `202` (also on a repeat with the same key):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "queued"}
```

Errors: `404` unknown binding; `409` with `error.code` one of
`evolution_frozen`, `strategy_disabled`, `binding_busy` (a run of this
binding is still active), `ceiling_exceeded`, `idempotency_key_reused` (same
key, different body); `422` `binding_check_failed` (the bot or strategy
changed since the policy was written), or `budget` above the binding's
budget.

### GET /bots/{bot_id}/evolution/runs

List the bot's runs, newest first. Filters: `status`, `binding`, `since`;
`page` and `page_size`. Called by the UI backend and `avn evolve run status`.

Request:

```http
GET /openapi/v1/bots/bot_123/evolution/runs?status=running,queued&binding=bind_01&page=1&page_size=20
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 1,
  "items": [
    {"id": "run_7f3", "binding_id": "bind_01", "strategy": "clawevolve/bot-evolution",
     "strategy_version": "2.0.0", "status": "running", "attempt": 2,
     "created_at": "2026-10-08T02:00:03Z",
     "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
     "budget_used": {"usd": 6.42, "wall_clock_s": 1830, "rollouts": 96, "tokens": 412300},
     "candidate_count": 1}
  ]
}
```

Errors: `404` unknown bot; `422` unknown filter value.

### GET /bots/{bot_id}/evolution/runs/{run}

Read one run by id: status, frozen inputs, attempt, budget used, summary.
This is the status lookup every caller repeats until the status is
terminal. Called by pipelines, the UI backend, and `avn evolve run status
--wait`.

Request:

```http
GET /openapi/v1/bots/bot_123/evolution/runs/run_7f3
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "run_7f3",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "binding_id": "bind_01",
  "idempotency_key": "bind_01/2026-10-08T02:00:00Z",
  "trigger": {"kind": "schedule", "fire_time": "2026-10-08T02:00:00Z", "event_id": null, "requested_by": null},
  "strategy": "clawevolve/bot-evolution",
  "strategy_version": "2.0.0",
  "parent_ref": "active",
  "parent_revision": "sha256:a90b…",
  "allowed_genes": ["persona", "skills"],
  "verification_profile": "default@1",
  "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60},
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "granted": ["parent", "workspace", "operations", "budget", "log", "artifacts", "cancelled",
              "candidates@1", "models@1", "experience.sessions@1", "agents@1", "evaluate.train@1"],
  "agent_definitions": {"clawevolve-tune": "sha256:5d0e…", "clawevolve-review": "sha256:8b17…"},
  "status": "completed",
  "attempt": 2,
  "max_attempts": 3,
  "budget_used": {"usd": 14.80, "wall_clock_s": 5210, "rollouts": 288, "tokens": 1203400},
  "candidate_count": 2,
  "created_at": "2026-10-08T02:00:03Z",
  "started_at": "2026-10-08T02:00:09Z",
  "ended_at": "2026-10-08T03:26:59Z",
  "end_reason": null,
  "summary": {"rounds": 3, "notes": "two candidates submitted; round 2 accepted", "extra": {"findings": 5}}
}
```

Errors: `404` unknown run, or a run of another bot.

### POST /bots/{bot_id}/evolution/runs/{run}:cancel

Cancel a run. Sets the run's cancellation token, cancels its unfinished
operations, and ends it as `cancelled`. Candidates already submitted are
kept and still verified. Idempotent on an already cancelled run. Called by
the bot owner (UI, `avn evolve run cancel`) and pipelines.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"reason": "persona freeze during product launch"}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"id": "run_7f3", "status": "cancelled", "end_reason": "cancelled_by_owner",
 "ended_at": "2026-10-08T02:40:12Z", "candidate_count": 1}
```

Errors: `404` unknown run; `409` `run_already_ended` for a run that is
`completed`, `failed`, or `budget_exhausted`.

### GET /bots/{bot_id}/evolution/runs/{run}/candidates

List the candidates a run submitted, in submission order, with their verdict
status. The full report of one candidate (diff, verification, gate
decision) is `GET /bots/{bot_id}/evolution/candidates/{candidate}` in
[08-promotion.md](08-promotion.md). Called by the UI backend, pipelines, and
`avn evolve run report`.

Request:

```http
GET /openapi/v1/bots/bot_123/evolution/runs/run_7f3/candidates
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "items": [
    {"candidate_id": "sha256:c41e…", "n": 1, "ref": "candidate/run_7f3/1",
     "revision": "sha256:7c1e…",                    // r42, parent r41; not the candidate id (hash of the patch)
     "base": "sha256:a90b…",
     "submitted_at": "2026-10-08T02:31:55Z",
     "rationale": "Fixes trigger misses on partial refunds",
     "verdict": {"status": "accept"}},
    {"candidate_id": "sha256:e07d…", "n": 2, "ref": "candidate/run_7f3/2",
     "revision": "sha256:9d40…",                    // r43, parent r42
     "base": "sha256:7c1e…",
     "submitted_at": "2026-10-08T03:10:20Z",
     "rationale": "Tightens escalation wording",
     "verdict": {"status": "reject"}}
  ]
}
```

Errors: `404` unknown run.

### 14.2 Internal API (Job Protocol)

Internal endpoints for job-worker strategies, under `/evolution/v1` (not
`/openapi/v1`). They are not part of the public API and are not exposed
through the gateway. After the claim, every call sends the header
`Evolution-Fencing-Token` (proposed name). Common errors: `403`
capability not granted; `409` stale fencing token; `402`
`budget_exhausted` on charged calls (proposed); `404` unknown run, job,
operation, or candidate of this run.

### POST /evolution/v1/jobs:claim

Lease one queued job of the given strategy ids. Called by a worker's main
loop.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"worker_id": "worker-clawevolve-02", "strategy_ids": ["clawevolve/bot-evolution"]}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "job_id": "job_7f3_2",
  "run_id": "run_7f3",
  "attempt": 2,
  "strategy": "clawevolve/bot-evolution@2.0.0",
  "params": {"window_days": 7, "max_rounds": 3, "poll_s": 60},
  "parent": "sha256:a90b…",
  "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
  "budget_used": {"usd": 3.10, "wall_clock_s": 912, "rollouts": 48, "tokens": 201000},
  "granted": ["parent", "workspace", "operations", "budget", "log", "artifacts", "cancelled",
              "candidates@1", "models@1", "experience.sessions@1", "agents@1", "evaluate.train@1"],
  "fencing_token": "ft_7f3_2_b81c",
  "lease_expires_at": "2026-10-08T02:16:10Z"
}
```

Every later call of this job sends `fencing_token` in the
`Evolution-Fencing-Token` header.

Response `204` (no body) when no job is queued for these strategies.
Errors: `409` `orchestrator_paused` while the global kill switch is on
(proposed; a worker treats it like `204` and retries later).

### POST /evolution/v1/jobs/{id}/heartbeat

Renew the lease. An expired lease re-queues the run as a new job (§7). The response
also tells the worker whether the run was cancelled (proposed).

Request (header `Evolution-Fencing-Token: ft_7f3_2_b81c`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"lease_expires_at": "2026-10-08T02:17:10Z", "cancelled": false}
```

Errors: `409` stale fencing token (the lease was lost; the worker must
stop this run).

### GET /evolution/v1/runs/{run}/parent

`ctx.parent`: the run's frozen parent revision, read-only: spec, files by
digest, lineage.

Request:

```http
GET /evolution/v1/runs/run_7f3/parent
Evolution-Fencing-Token: ft_7f3_2_b81c
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "sha256:a90b…",
  "seq": 41,
  "parents": ["sha256:77f2…"],
  "spec": {
    "persona": {"files": [{"path": "persona/SOUL.md", "digest": "sha256:1a2b…"}]},
    "skills": [{"name": "refund-policy", "files": [{"path": "skills/refund-policy/SKILL.md", "digest": "sha256:3c4d…"}]}],
    "memory": {"items": []}
  },
  "policy": {"locked_genes": ["script", "tools.mcp", "policy"], "pins": []}
}
```

The genome shape itself is defined in [01-genome.md](01-genome.md).

### GET /evolution/v1/runs/{run}/content/{digest}

File bytes of the parent or a workspace, by content digest. Returns raw
bytes, not JSON.

Request:

```http
GET /evolution/v1/runs/run_7f3/content/sha256:1a2b…
Evolution-Fencing-Token: ft_7f3_2_b81c
```

Response `200`:

```http
HTTP/1.1 200 OK
Content-Type: text/markdown; charset=utf-8
Digest: sha256:1a2b…

# Soul
You are the support assistant for …
```

Errors: `404` digest not reachable from this run's parent or workspaces.

### GET /evolution/v1/runs/{run}/experience/sessions

`ctx.experience.sessions` (only if `experience.sessions@1` is granted): the
bot's past conversations as normalized, redacted episodes. Query
parameters: `days`, `limit` (default 500), `revision`. The episode format is
defined in [02-experience.md](02-experience.md).

Request:

```http
GET /evolution/v1/runs/run_7f3/experience/sessions?days=7&limit=2
Evolution-Fencing-Token: ft_7f3_2_b81c
```

Response `200`:

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

Errors: `403` `capability_not_granted`.

### GET /evolution/v1/runs/{run}/experience/feedback

`ctx.experience.feedback` (only if `experience.feedback@1` is granted):
ratings, corrections, outcomes, and findings for the bot. Query
parameters: `days`, `limit`. The feedback format is defined in
[02-experience.md](02-experience.md).

Request:

```http
GET /evolution/v1/runs/run_2c8/experience/feedback?days=7&limit=100
Evolution-Fencing-Token: ft_2c8_1_04aa
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "feedback": [
    {"feedback_id": "fb_301", "episode_id": "ep_91", "revision_id": "sha256:a90b…",
     "kind": "correction", "text": "partial refunds are allowed within 30 days",
     "created_at": "2026-10-07T09:20:00Z"},
    {"feedback_id": "fb_302", "episode_id": "ep_95", "revision_id": "sha256:a90b…",
     "kind": "rating", "score": 0.25, "created_at": "2026-10-07T14:02:00Z"}
  ]
}
```

Errors: `403` `capability_not_granted`.

### POST /evolution/v1/runs/{run}/workspaces

`ctx.workspace.materialise(revision, key)`: materialise a revision into a
sandbox workspace. Idempotent per `(run, key)`: a repeat returns the same
workspace, including edits already made in it.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"revision": "sha256:a90b…", "key": "run_7f3/round-1"}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"workspace_id": "ws_7f3_r1", "revision": "sha256:a90b…", "key": "run_7f3/round-1", "created": false}
```

`created` is `true` on the first call for a key and `false` on repeats
(proposed). Errors: `422` revision not reachable from this run (not the
parent and not a candidate of this run); `409` the same key was used with
a different revision.

### GET /evolution/v1/runs/{run}/workspaces/{workspace}/files

List a workspace's files with their content digests (backs `ws.read`
together with `content/{digest}`, and lets a worker see what an agent
changed).

Request:

```http
GET /evolution/v1/runs/run_7f3/workspaces/ws_7f3_r1/files
Evolution-Fencing-Token: ft_7f3_2_b81c
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "files": [
    {"path": "persona/SOUL.md", "digest": "sha256:1a2b…"},
    {"path": "skills/refund-policy/SKILL.md", "digest": "sha256:5e6f…"}
  ]
}
```

Errors: `404` unknown workspace of this run.

### PUT /evolution/v1/runs/{run}/workspaces/{workspace}/files/{path}

`ws.write(path, content)`: write one file of the sandbox copy (raw bytes in
the body). `{path}` is percent-encoded as one path segment. Only the
workspace changes; nothing touches the live bot.

Request:

```http
PUT /evolution/v1/runs/run_7f3/workspaces/ws_7f3_r1/files/skills%2Frefund-policy%2FSKILL.md
Evolution-Fencing-Token: ft_7f3_2_b81c
Content-Type: application/octet-stream

---
name: refund-policy
---
Use for full and partial refunds of paid orders.
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"digest": "sha256:7a8b…"}
```

Errors: `404` unknown workspace; `413` over the Manifest file size limit.

### POST /evolution/v1/runs/{run}/workspaces/{workspace}:patch

`ws.to_patch()`: the itemized Genome Patch of the workspace against the
revision it was materialised from. A quick call; it does not submit
anything (submission is `POST …/candidates`).

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch": {
    "patch_schema": 1,
    "base": "sha256:a90b…",
    "ops": [{"op": "skill.update", "name": "refund-policy",
             "file_ops": [{"kind": "unified_diff", "path": "SKILL.md", "diff": "@@ -3,1 +3,1 @@ …"}]}],
    "rationale": "",
    "evidence": []
  }
}
```

The strategy fills `rationale` and `evidence` before submitting. Errors:
`404` unknown workspace.

### POST /evolution/v1/runs/{run}/log

`ctx.log`: append structured log lines to the run's log.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"lines": [{"at": "2026-10-08T02:23:05Z", "level": "info", "msg": "train", "fields": {"score": 0.82}}]}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"accepted": 1}
```

### PUT /evolution/v1/runs/{run}/artifacts/{name}

`ctx.artifacts`: upload one artifact of the run (raw bytes), for example a
report the strategy wants reviewers to see. Writing the same name again
replaces it.

Request:

```http
PUT /evolution/v1/runs/run_7f3/artifacts/round-1-report.md
Evolution-Fencing-Token: ft_7f3_2_b81c
Content-Type: text/markdown

# Round 1
Train 0.82 vs parent 0.61 …
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"artifact_id": "art_7f3_r1", "digest": "sha256:c3d4…"}
```

Errors: `413` over the artifact size limit (proposed: the Manifest file size
limit).

### POST /evolution/v1/runs/{run}/agents:start

`ctx.agents.start` (only if `agents@1` is granted): start one of the
strategy's registered agent definitions in a workspace, as an operation.
Idempotent per `idempotency_key`.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "definition": "clawevolve-tune",
  "workspace_id": "ws_7f3_r1",
  "prompt": "Findings: partial-refund trigger misses (5 episodes). Edit the refund-policy skill …",
  "idempotency_key": "run_7f3/round-1/tune",
  "timeout_s": 1800
}
```

Response `202`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"operation_id": "op_19a", "status": "queued"}
```

Errors: `403` `capability_not_granted`; `422` `unknown_definition` (not
registered with this strategy version) or unknown workspace; `402`
`budget_exhausted`.

### POST /evolution/v1/runs/{run}/evaluations:train

`ctx.evaluate.start_train` (only if `evaluate.train@1` is granted): evaluate
a workspace on the **train split only**, with scores and critiques, as an
operation. Idempotent per `idempotency_key`.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"workspace_id": "ws_7f3_r1", "idempotency_key": "run_7f3/round-1/train", "seeds": 3}
```

Response `202`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"operation_id": "op_1b2", "status": "queued"}
```

Errors: `403` `capability_not_granted`; `422` unknown workspace; `402`
`budget_exhausted`.

### POST /evolution/v1/runs/{run}/evaluations/cases

`ctx.evaluate.add_train_cases` (only if `evaluate.train@1` is granted): add
replayable cases the strategy derived from experience. The platform assigns
splits; the strategy cannot choose them. The case format is defined in
[07-verification.md](07-verification.md).

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "cases": [
    {"title": "Partial refund request",
     "prompt": "Can I get a refund for half of my order A17?",
     "expected": "Explains that partial refunds are allowed within 30 days and starts one",
     "evidence": ["episode:ep_91"]}
  ]
}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"accepted": [{"case_id": "case_5e1", "split": "train"}]}
```

Some cases may be assigned to a hidden split; those are reported only as a
count (proposed: `{"accepted": […], "withheld": 1}`), never by id. Errors:
`403` `capability_not_granted`; `422` case does not validate.

### GET /evolution/v1/runs/{run}/operations/{id}

`ctx.operations.get`: look up an operation's status by id; once it has
succeeded, its result.

Request:

```http
GET /evolution/v1/runs/run_7f3/operations/op_1b2
Evolution-Fencing-Token: ft_7f3_2_b81c
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "op_1b2",
  "kind": "train_evaluation",
  "status": "succeeded",
  "cost": {"usd": 2.05, "wall_clock_s": 410, "rollouts": 48, "tokens": 0},
  "result": {
    "split": "train",
    "score": 0.82,
    "cases": [
      {"case_id": "case_5e1", "score": 1.0, "critique": "Correctly allowed the partial refund."},
      {"case_id": "case_5e2", "score": 0.5, "critique": "Did not confirm the order id before refunding."}
    ]
  },
  "error": null
}
```

While it runs, the same call returns `"status": "running"` and
`"result": null`.

### POST /evolution/v1/runs/{run}/operations/{id}:cancel

`ctx.operations.cancel`: cancel an operation of this run. Idempotent; a
finished operation is returned unchanged.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"id": "op_19a", "status": "cancelled"}
```

### POST /evolution/v1/runs/{run}/models:complete

`ctx.models.complete` (always granted): one model call, prompt in, text
out, routed through the platform, charged to the budget, with the model
used recorded. `model` is a name from the platform's model list; when
omitted, the platform default is used.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "messages": [
    {"role": "system", "content": "You rewrite skill descriptions to be precise."},
    {"role": "user", "content": "Rewrite: 'Handles refunds.'"}
  ],
  "max_tokens": 512
}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "text": "Use when a user asks for a full or partial refund of an order …",
  "model": "platform-default",
  "usage": {"input_tokens": 38, "output_tokens": 41},
  "cost_usd": 0.0009
}
```

Errors: `402` `budget_exhausted`; `422` unknown model name or
`max_tokens` above the platform limit.

### POST /evolution/v1/runs/{run}/candidates

`ctx.candidates.submit` (always granted): submit a candidate (a Genome
Patch against a base revision, a rationale, evidence ids, optional
self-reported metrics). Returns the candidate id at once; does not wait for
verification. Idempotent: the id is the content hash of the patch.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "patch": {"patch_schema": 1, "base": "sha256:a90b…",
            "ops": [{"op": "file.edit", "target": "skills/refund-policy/SKILL.md",
                     "edits": [{"kind": "replace_section", "heading": "## When to use", "content": "…"}]}]},
  "rationale": "Best on 14/20 train cases; fixes trigger misses on partial refunds",
  "evidence": ["episode:ep_91", "eval:train_77"],
  "self_metrics": {"train_score_pct": 82}
}
```

Response `200` (same body on a repeat):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"candidate_id": "sha256:c41e…", "ref": "candidate/run_7f3/1", "verdict": {"status": "pending"}}
```

Errors: `422` `patch_invalid` (schema), `base_mismatch`, or
`gene_not_allowed` (an op outside the run's `allowed_genes`, a locked gene,
or a pinned item).

### GET /evolution/v1/runs/{run}/candidates/{id}

`ctx.candidates.verdict`: look up a candidate's verdict by id. Validation
aggregates only, never per-case hidden data.

Request:

```http
GET /evolution/v1/runs/run_7f3/candidates/sha256:c41e…
Evolution-Fencing-Token: ft_7f3_2_b81c
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "candidate": "sha256:c41e…",
  "status": "accept",
  "revision": "sha256:7c1e…",
  "aggregates": {"validation": {"cases": 30, "seeds": 3, "mean_delta": 0.061, "ci": [0.028, 0.094]}},
  "reasons": ["validation_improved"]
}
```

While verification is still running, `status` is `pending` and
`aggregates` is `{}`. This is the `StrategyVerdictView` of
[07-verification.md](07-verification.md), which owns the verdict model.

### POST /evolution/v1/runs/{run}/budget:charge

`ctx.budget.charge`: record an explicit charge (proposed semantics in
§11.1); returns what remains. Calls the platform meters itself are charged
automatically and need no call here.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"usage": {"usd": 0.40, "rollouts": 0, "tokens": 0}, "note": "replay of 4 cached rollouts"}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"remaining": {"usd": 13.18, "wall_clock_s": 5370, "rollouts": 304, "tokens": null}}
```

`null` means no limit on that dimension. Errors: `402` `budget_exhausted`;
`422` negative amounts.

### POST /evolution/v1/jobs/{id}/complete

`run(ctx)` returned. Ends the run as `completed` with its `RunSummary`.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"summary": {"rounds": 3, "notes": "two candidates submitted; round 2 accepted", "extra": {"findings": 5}}}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "completed"}
```

Errors: `409` stale fencing token, or the run already ended (for example
cancelled meanwhile; the response then carries the run's final status).

### POST /evolution/v1/jobs/{id}/fail

`run(ctx)` failed. A retryable failure with attempts left re-queues the run
under the same run id; otherwise the run ends as `failed`.

Request:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"reason": "own progress store unreachable", "retryable": true}
```

Response `200`:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{"run_id": "run_7f3", "status": "queued", "attempt": 3}
```

Errors: `409` stale fencing token or run already ended.

## 15. Examples

### 15.1 A pipeline starts a run and follows it

Using the generated client SDK ([09-evolution-api.md](09-evolution-api.md)):

```python
import time
from avernet_evolution import Client

c = Client.from_env()
key = "nightly-bot_123-2026-10-08"                     # deterministic: same key on every retry
run_id = c.runs.start(bot_id="bot_123", binding="bind_01",   # caller owns the bot: no entity_id
                      budget={"max_usd": 10, "max_wall_clock_s": 3600},
                      idempotency_key=key)              # safe to repeat: returns the same run_id

while (run := c.runs.get(bot_id="bot_123", run_id=run_id)).status in ("queued", "running"):
    time.sleep(30)                                      # short lookups by id; no request held open

print(run.status, run.budget_used.usd)
for cand in c.runs.candidates(bot_id="bot_123", run_id=run_id):
    print(cand.candidate_id, cand.verdict.status)       # approval is Promotion's job (08-promotion.md)
```

The same with the CLI:

```bash
avn evolve run start --bot bot_123 --binding bind_01 \
  --idempotency-key nightly-bot_123-2026-10-08 --max-usd 10 --output json
avn evolve run status --bot bot_123 run_7f3 --wait --output json
```

### 15.2 An owner adds a binding

```python
policy = c.policy.get(bot_id="bot_123")
bindings = policy.bindings + [Binding(
    id="bind_02", strategy="platform/consolidate-memory@1.0.0",
    trigger={"schedule": "0 4 * * 0"}, parent="active", allowed_genes=["memory"],
    verification_profile="default@1",
    budget={"max_usd": 5, "max_wall_clock_s": 1800}, params={})]
try:
    c.policy.put(bot_id="bot_123", bindings=bindings, if_match=policy.etag)
except BindingCheckFailed as e:                          # 422: fix the binding, nothing was written
    for d in e.details:
        print(d.binding, d.check, d.detail)
```

### 15.3 The trigger scheduler fires a binding

Inside the service, a schedule firing is an idempotent submission:

```python
async def fire_schedule(binding: Binding, bot: BotRef, slot: datetime) -> None:
    if await kill.is_paused() or await kill.is_bot_frozen(bot):
        await firings.record_skipped(binding.id, slot, reason="paused_or_frozen")
        return
    key = f"{binding.id}/{slot.isoformat().replace('+00:00', 'Z')}"   # bind_01/2026-10-08T02:00:00Z
    try:
        run_id = await runs.submit(bot, binding.id, idempotency_key=key,
                                   trigger=TriggerRecord(kind="schedule", fire_time=slot,
                                                         event_id=None, requested_by=None))
        await firings.record_fired(binding.id, slot, run_id)
    except (BindingBusy, CeilingExceeded, BindingCheckFailed, EvolutionFrozen) as e:
        await firings.record_skipped(binding.id, slot, reason=type(e).__name__)
```

### 15.4 A minimal job worker over the Job Protocol

A job worker in any language only needs HTTP. This sketch uses Python
without the SDK, to show the wire protocol; the strategy SDK's Job-Protocol
`StrategyContext` wraps exactly these calls.

```python
import os, threading, time
import httpx

base = os.environ["EVOLUTION_JOB_ENDPOINT"]             # injected by the worker's bootstrap
http = httpx.Client(base_url=base, timeout=30)

def heartbeat_loop(job_id: str, token: str, stop: threading.Event, cancelled: threading.Event):
    while not stop.wait(20):
        r = http.post(f"/evolution/v1/jobs/{job_id}/heartbeat", json={},
                      headers={"Evolution-Fencing-Token": token})
        if r.status_code == 409:                        # lease lost: someone else holds the run now
            cancelled.set(); return
        if r.json()["cancelled"]:
            cancelled.set()

while True:
    r = http.post("/evolution/v1/jobs:claim",
                  json={"worker_id": "worker-opt-01", "strategy_ids": ["team-x/prompt-opt"]})
    if r.status_code == 204:
        time.sleep(10); continue
    job = r.json()
    h = {"Evolution-Fencing-Token": job["fencing_token"]}
    run = f"/evolution/v1/runs/{job['run_id']}"
    stop, cancelled = threading.Event(), threading.Event()
    threading.Thread(target=heartbeat_loop, args=(job["job_id"], job["fencing_token"], stop, cancelled)).start()
    try:
        ws = http.post(f"{run}/workspaces", headers=h,
                       json={"revision": job["parent"], "key": f"{job['run_id']}/main"}).json()
        op = http.post(f"{run}/evaluations:train", headers=h,
                       json={"workspace_id": ws["workspace_id"],
                             "idempotency_key": f"{job['run_id']}/baseline-train"}).json()
        while (s := http.get(f"{run}/operations/{op['operation_id']}", headers=h).json())["status"] \
                in ("queued", "running"):
            if cancelled.is_set(): break
            time.sleep(15)
        patch = optimize(s["result"], http, run, h)     # its own search; model calls via models:complete
        cand = http.post(f"{run}/candidates", headers=h, json={"patch": patch,
                         "rationale": "best of 12 prompt variants on train", "evidence": []}).json()
        http.post(f"/evolution/v1/jobs/{job['job_id']}/complete", headers=h,
                  json={"summary": {"rounds": 1, "notes": cand["candidate_id"], "extra": {}}})
    except Exception as e:
        http.post(f"/evolution/v1/jobs/{job['job_id']}/fail", headers=h,
                  json={"reason": str(e)[:500], "retryable": True})
    finally:
        stop.set()
```

The worker stores no operation ids: after a crash, the re-dispatched
attempt repeats the same `workspaces` and `evaluations:train` calls with the
same keys and gets the same workspace and operation back.

### 15.5 One run end to end, with a crash

Bot `bot_123`, binding `bind_01` (`clawevolve/bot-evolution@2.0.0`), nightly
schedule. ClawEvolve's own code is shown in
[04-default-strategies.md](04-default-strategies.md).

| Time | Event | Run state |
| --- | --- | --- |
| 02:00:00 | Schedule fires; key `bind_01/2026-10-08T02:00:00Z`; inputs frozen: parent `active` = `r41` (`sha256:a90b…`), `max_rounds: 3`, `max_usd: 20` | `run_7f3` `queued`, attempt 1 |
| 02:00:09 | Worker A claims `job_7f3_1`, token `ft_7f3_1_…`; context grants `experience.sessions@1`, `agents@1`, `evaluate.train@1` plus the always-granted parts | `running` |
| 02:01 | Strategy reads 7 days of episodes, adds train cases, saves its state in its own store | `running` |
| 02:12 | `workspaces` with key `run_7f3/round-1`; `agents:start` with key `run_7f3/round-1/tune` → `op_19a` | `running` |
| 02:15 | Worker A's host reboots; heartbeats stop | `running` |
| 02:16:10 | Lease expires; attempt becomes 2; token `ft_7f3_1_…` is now stale | `queued`, attempt 2 |
| 02:16:30 | Worker B claims the new job `job_7f3_2`; strategy reloads its state by run id; repeats `workspaces` and `agents:start` with the same keys → same `ws_7f3_r1`, same `op_19a`, still running | `running`, attempt 2 |
| 02:23 | `op_19a` succeeds; train evaluation `op_1b2`; strategy submits candidate `sha256:c41e…` → `candidate/run_7f3/1` (`r42`) | `running` |
| 02:24–02:58 | Verification under `default@1`; strategy looks the verdict up by id: `pending`, then `accept`; next round builds on `r42` | `running` |
| 03:26:59 | Round 3 ends; `jobs/job_7f3_2/complete` with the summary | `completed` |

The gate then routes `r42` (risk tier T2: persona + skill) to the review
queue ([08-promotion.md](08-promotion.md)); the run itself never moves
`active`.

## 16. Interactions

| Other part | Direction | What flows |
| --- | --- | --- |
| [01-genome.md](01-genome.md) Genome Registry | Run → Genome | Resolve `active` to the frozen parent; read revisions and content for `ctx.parent`, workspaces, and `content/{digest}`; create candidate revisions from `{base, patch}`; move `candidate/<run>/<n>` refs |
| [01-genome.md](01-genome.md) | Genome → Run | The bot's genome `policy` (locked genes, pins) for binding checks |
| [02-experience.md](02-experience.md) Experience | Run → Experience | Queries behind `experience.sessions` / `experience.feedback`, redacted |
| [03-strategy.md](03-strategy.md) Strategy Registry | Registry → Run | Registration records, conformance status, capability providers per engine, agent definitions by digest, strategy kill switch |
| [03-strategy.md](03-strategy.md) Strategy implementations | Run ↔ Strategy | `run(ctx)` in process, or the Job Protocol; candidates, operations, model calls, budget charges |
| [04-default-strategies.md](04-default-strategies.md) | Default strategies → Run | ClawEvolve and `platform/consolidate-memory` as job workers / in-process strategies; ClawInsight items as event triggers |
| [05-experiment-ledger.md](05-experiment-ledger.md) Ledger | Run → Ledger | Run records with strategy version and agent definition digests, every submission, verdicts, costs, models used |
| [05-experiment-ledger.md](05-experiment-ledger.md) | Ledger → Run | Later: parent selectors beyond `active` |
| [07-verification.md](07-verification.md) Verification | Run → Verification | `verify(candidate, profile)` with the run's frozen profile; train evaluations; train cases added by strategies |
| [07-verification.md](07-verification.md) | Verification → Run | Verdicts (aggregates only to the strategy); train scores and critiques |
| [08-promotion.md](08-promotion.md) Promotion | Run → Promotion | Static floor check (`FloorCheck`) at submission; verified candidates for the gate and review queue |
| [08-promotion.md](08-promotion.md) | Promotion → Run | Nothing a run waits on; promotion of a run's candidates happens after or independently of the run |
| [09-evolution-api.md](09-evolution-api.md) Evolution API and clients | Clients → Run | Policy reads and writes, run submission, status, cancellation, candidate lists via SDK, CLI, and UI backend |
| [10-meta-evolution.md](10-meta-evolution.md) Meta-evolution (later) | Meta → Run | Runs of candidate mechanisms on improvement problems, with equal budgets |

## 17. Open decisions

| ID | Decision | Options / proposal |
| --- | --- | --- |
| D-1 | Module placement of the control plane | A. new `apps/evolution` (**recommended**); B. Backend `core/evolution/`; C. ClawWeb's TS control plane. See [design.md](design.md) |
| ER-1 | Where the auto-promote ceiling lives | Resolved (proposed fields): `auto_promote_ceiling` and `rollout` are binding fields (§2.2, §3.1), read by Promotion ([08-promotion.md](08-promotion.md)) |
| ER-2 | Where per-bot ceilings and the per-bot freeze are configured | Proposal: `limits` (daily/monthly USD) and `frozen` fields on the evolution policy; per-tenant ceilings in tenant settings |
| ER-3 | Iteration limits | Governance lists "iterations" among per-run limits, but the platform cannot see a strategy's rounds. Proposal: count candidate submissions (`max_candidates`) or drop the dimension |
| ER-4 | Job Protocol gap: workspace contents | Resolved: `GET …/workspaces/{workspace}/files`, `PUT …/workspaces/{workspace}/files/{path}`, and `POST …/workspaces/{workspace}:patch` (§9.1, §14.2); `ctx.log` and `ctx.artifacts` are `POST …/log` and `PUT …/artifacts/{name}` |
| ER-5 | Fencing token transport and cancellation over the wire | Proposal: header `Evolution-Fencing-Token`; `cancelled` flag in the heartbeat response |
| ER-6 | Lease timings and `max_attempts` | Proposal: 60 s lease, 20 s heartbeat, 3 attempts; per-strategy override in registration |
| ER-7 | Concurrency per binding and missed firings | Proposal: one active run per binding; missed slots skipped, not backfilled |
| ER-8 | Run submission body | Resolved: `{binding, params?, budget?}`, because allowed genes and the verification profile come from a binding. Still open: whether an operator may run an unbound strategy version (for example for testing) |
| ER-9 | Params validation at configuration time | Resolved: an optional `params_schema` in the registration record (proposed, [03-strategy.md](03-strategy.md)), checked as binding check 8 when the policy is written (§3.2) |
| ER-10 | The strategy's own progress store | Strategies persist their own progress in their own storage (agreed). Open: how a sandboxed job worker reaches that storage — e.g. an egress allowlist entry declared in the registration record for the strategy's own store, or a platform-provided opaque per-run blob that the platform never interprets. Same decision as S-8 in [03-strategy.md](03-strategy.md) |
| ER-11 | Escalation outcome | N consecutive rejections: end as `cancelled` with `end_reason: "consecutive_rejections"` (proposed) or as `failed`; value of N |
| ER-12 | `budget:charge` semantics | What a strategy may charge explicitly, given that platform calls are metered automatically |
