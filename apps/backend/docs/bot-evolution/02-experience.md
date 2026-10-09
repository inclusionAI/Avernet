# Experience Store

> 中文版：[02-experience.zh-CN.md](02-experience.zh-CN.md)

> Status: DRAFT. Component in the [bot evolution architecture](design.md).
> This doc covers the Experience Store: the normalized, revision-tagged
> record of what bots did and how it went, how it is filled from engines and
> feedback sources, who may read it, and the data-handling rules that apply
> to it.

## 1. Purpose and scope

**Experience** is the evidence that evolution learns from: what a bot did in
its sessions, what happened as a result, and how its candidates scored in
evaluation. The **Experience Store** is the component that keeps a
normalized, engine-neutral, indexed, retention-bounded copy of that evidence
for evolution.

It records three kinds of things:

- **Episode** — one session or task trajectory: messages, tool calls, tool
  results, timings, model, cost, and outcome. Episodes are normalized from
  engine-specific formats by the engine's **session export** (the provider
  behind the `experience.sessions@1` capability).
- **Feedback** — signals about how things went: user ratings, corrections,
  task outcomes, BCS coordination outcomes, and run-evidence events
  (TaskGuard).
- **Eval trace** — every evaluation rollout, with grader scores and textual
  critiques.

Who generates these records: the **engine** during the bot's sessions
(episodes, pulled through session export), **users and products** (feedback,
sent to the feedback endpoint or picked up by ingest adapters), and
**Verification** (one eval trace per evaluation rollout). Every record is
stored with the **genome revision id** of the bot version that produced it,
attached at ingest: an eval trace knows its revision directly, because
Verification evaluates a specific revision; an episode gets it by
attribution from the bot's apply history (the revision that was applied to
the bot when the episode started; for a service bot, the published version
that was serving, mapped to its revision); feedback inherits it from its
episode. Records from before the bot had revisions are stored with
`revision_id: null` (unattributed). The rules and an example timeline are in
§3. The revision id is the one field missing everywhere in the codebase
today, and it is what turns logs into attributable fitness signal ("revision
`r42` fails refunds less often than `r41`") and later into training data
(§8).

**What it owns**

- The normalized record types (`Episode`, `Feedback`, `EvalTrace`) and their
  schemas.
- The ingest pipeline: pull from engine session exports, normalize, redact,
  tag with the revision, index.
- The read paths: the public experience endpoints (§10), and the
  `experience.sessions@1` and `experience.feedback@1` capabilities that
  strategies use.
- Retention, redaction, and PII rules for experience
  (governance topic **data handling**, §7).

**What it does not own**

| Concern | Owner | Doc |
| --- | --- | --- |
| Raw chat history (the source of truth for sessions) | The engine (AGENTS.md assigns chat history to engine-facing services) | This doc only defines the export contract the engine implements (§5) |
| Genome revisions and the revision id itself | Genome Registry | [01-genome.md](01-genome.md) |
| The capability catalog, `StrategyContext`, and how a strategy is granted capabilities | Strategy | [03-strategy.md](03-strategy.md) |
| How ClawEvolve and memory consolidation use experience | Default strategies | [04-default-strategies.md](04-default-strategies.md) |
| Experiment records, preference pairs, and training-data export | Experiment Ledger | [05-experiment-ledger.md](05-experiment-ledger.md) |
| Serving capability calls to job workers (Job Protocol) | Evolution Run | [06-evolution-run.md](06-evolution-run.md) |
| Running evaluations, graders, suites, and splits | Verification | [07-verification.md](07-verification.md) |
| Secret/PII/URL scanning of patches derived from experience | Promotion (gate floor) | [08-promotion.md](08-promotion.md) |
| Shared API conventions, SDKs, and the `avn` CLI | Evolution API | [09-evolution-api.md](09-evolution-api.md) |
| Frozen experience snapshots for improvement problems (level 3, later) | Meta-evolution | [10-meta-evolution.md](10-meta-evolution.md) |

**Where it runs.** The store is part of the evolution control plane, in the
proposed new module `apps/evolution` (recommended option A of open decision
D-1; see [design.md](design.md)). Session export is implemented in the
**engine adapter**, because the engine owns its physical layout (ADR 0014,
0017) and its chat history; Backend must not learn engine session paths.

**Fast-loop observations.** Observations and draft patches recorded by a
subject bot mid-session would also land here as feedback. They depend on
how bots call the platform, which is postponed (DR-3), so they are out of
the first iteration; this doc does not mention bot callers again.

## 2. Domain model

| Type | What it is | Owned by | Lifecycle |
| --- | --- | --- | --- |
| `Episode` | One normalized session or task trajectory of one bot, tagged with the revision it ran | Experience Store (normalized copy); engine (raw source) | Created at ingest; immutable afterwards except redaction; deleted when retention expires |
| `Turn` / `ToolCall` | Parts of an episode: one message, and one tool invocation inside an assistant turn | Experience Store | Part of its episode |
| `Outcome` | How an episode ended (succeeded, failed, corrected by the user, …) | Experience Store, derived at ingest and updated by linked feedback | Part of its episode |
| `Feedback` | One signal about a bot's behaviour: rating, correction, task outcome, coordination outcome, run evidence, or finding | Its producer writes it; the store keeps it | Created by `POST …/feedback` or by an ingest adapter; immutable; deleted when retention expires |
| `EvalTrace` | One evaluation rollout: case, split, revision, transcript reference, grader scores and critiques, cost | Verification writes it; the store keeps it | Created per rollout; immutable; retention as for episodes |
| `SessionExport` | One request to an engine's session-export provider and its status | Engine adapter (provider); Experience Store (caller) | An operation (§5): `queued`, `running`, then `succeeded`, `failed`, or `cancelled` |
| `RetentionPolicy` | Per-tenant retention, redaction, and PII rules | Tenant admin | Changed by admins; applied at ingest and by the retention sweep |

Identifiers: episodes use `ep_…` (example `ep_91`), feedback `fb_…`, eval
traces `et_…`. Other components refer to them as **evidence ids** with a
type prefix: `episode:ep_91`, `feedback:fb_204`, `eval:et_5521`. A candidate's
`evidence` list and the Experiment Ledger use these strings.

### 2.1 Episode

The capability example in the strategy SDK (`experience.sessions@1`) fixes
the core fields `episode_id`, `revision_id`, `started_at`, `turns`,
`outcome`, `redactions`. The remaining fields below (engine, timings, model,
cost, source reference) are what C2 is described as holding in the
architecture; their exact shape is **proposed** here and finalized by work
item RSI-10.

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Role = Literal["user", "assistant", "tool", "system"]
OutcomeStatus = Literal["succeeded", "failed", "user_corrected", "abandoned", "unknown"]
Engine = Literal["openclaw", "claude-code", "hermes", "teclaw"]   # the engines Avernet runs bots on
# Personal-data categories the redactor knows. New values are added by a reviewed
# change to the redactor, together with their rules (defaults: open decision X-4).
PiiCategory = Literal["email", "phone", "address"]
# BotRef {owner_id, bot_id}: a bot's full identity (a bot_id alone is not unique
# across users); defined once in 09-evolution-api.md §2.7.

@dataclass(frozen=True)
class ToolCall:
    name: str                      # tool name as the engine reports it, e.g. "order_lookup"
    args: dict                     # JSON arguments, after redaction

@dataclass(frozen=True)
class Turn:
    role: Role
    text: str                      # message text, after redaction
    at: datetime | None = None     # None only when the engine export has no per-turn time
    tool_calls: list[ToolCall] = field(default_factory=list)  # assistant turns only
    name: str | None = None        # tool turns: which tool produced `result`
    result: str | None = None      # tool turns: tool output, after redaction

@dataclass(frozen=True)
class Cost:
    usd: float                     # model spend in US dollars for this episode or rollout
    input_tokens: int              # tokens sent to the model, summed over all calls
    output_tokens: int             # tokens the model produced, summed over all calls

@dataclass(frozen=True)
class Outcome:
    status: OutcomeStatus
    feedback: str | None = None    # short summary of the deciding feedback, if any
    feedback_ids: list[str] = field(default_factory=list)   # the feedback records that decided `status`

@dataclass(frozen=True)
class SourceRef:
    engine: Engine                 # which engine ran the session
    export_api: str                # contract version that produced it, e.g. "session-export/v2"
    session_id: str                # the engine's own session id
    content_digest: str            # hash of the raw exported session, for re-normalization

@dataclass(frozen=True)
class Episode:
    episode_id: str                # "ep_91"
    bot: BotRef                    # the bot that ran the session (owner + bot id)
    revision_id: str | None        # None = unattributed (ran before revisions existed, §3)
    started_at: datetime
    ended_at: datetime
    turns: list[Turn]
    model: str                     # model name reported by the engine
    cost: Cost
    outcome: Outcome
    redactions: list[PiiCategory]  # personal-data categories removed at ingest, e.g. ["email", "phone"]
    source: SourceRef
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "episode_id": "ep_91",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "revision_id": "sha256:a90b…",                 // the genome revision the bot was running (r41)
  "started_at": "2026-10-07T09:12:00Z",
  "ended_at": "2026-10-07T09:14:31Z",
  "turns": [
    {"role": "user", "at": "2026-10-07T09:12:00Z", "text": "Can I get a refund for half of my order?"},
    {"role": "assistant", "at": "2026-10-07T09:12:04Z", "text": "Let me look up the order.",
     "tool_calls": [{"name": "order_lookup", "args": {"id": "A17"}}]},
    {"role": "tool", "at": "2026-10-07T09:12:05Z", "name": "order_lookup", "result": "{\"id\": \"A17\", \"total\": 84.0}"},
    {"role": "assistant", "at": "2026-10-07T09:12:09Z", "text": "Partial refunds are not possible."},
    {"role": "user", "at": "2026-10-07T09:13:50Z", "text": "Your policy page says partial refunds are allowed."}
  ],
  "model": "platform-default",
  "cost": {"usd": 0.012, "input_tokens": 5210, "output_tokens": 388},
  "outcome": {"status": "user_corrected", "feedback": "partial refunds are allowed", "feedback_ids": ["fb_204"]},
  "redactions": ["email", "phone"],              // personal data removed before any strategy sees it
  "source": {"engine": "openclaw", "export_api": "session-export/v2", "session_id": "s-20261007-0912",
             "content_digest": "sha256:7d1e…"}
}
```

### 2.2 Feedback

Feedback is any signal about how a bot behaved. It may point at an episode
(a thumbs-down on one conversation), at a revision (a canary metric), or
only at the bot (a ClawInsight finding across many sessions).

```python
FeedbackKind = Literal[
    "rating",                 # user thumbs/score on an answer or session
    "correction",             # user states what the right answer was
    "outcome",                # task outcome reported by a product or pipeline
    "coordination_outcome",   # BCS: how a multi-bot exchange ended
    "run_evidence",           # TaskGuard: guard / repair / retry events of a run
    "finding",                # a producer's diagnosis, e.g. ClawInsight plan-source/v2 items
]
FeedbackSource = Literal[
    "user",                   # a person, through a product UI
    "pipeline",               # a deterministic pipeline or product backend
    "bcs",                    # BCS coordination outcomes
    "taskguard",              # TaskGuard run evidence
    "clawinsight",            # ClawInsight findings
]

@dataclass(frozen=True)
class Feedback:
    feedback_id: str                  # "fb_204"
    bot: BotRef                       # the bot the feedback is about (owner + bot id)
    kind: FeedbackKind
    source: FeedbackSource            # who produced it
    created_at: datetime
    revision_id: str | None           # copied from the episode, or given by the producer; None = unattributed
    episode_id: str | None            # None for feedback not tied to one episode
    score: float | None               # ratings / outcomes on [0, 1]; None for text-only kinds
    text: str | None                  # correction text, finding summary; redacted at ingest
    data: dict                        # kind-specific structured payload, schema per kind
    idempotency_key: str              # the producer's key; (owner_id, bot_id, key) is unique (§10)
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "feedback_id": "fb_204",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "kind": "correction",
  "source": "user",
  "created_at": "2026-10-07T09:13:50Z",
  "revision_id": "sha256:a90b…",          // copied from ep_91 at ingest
  "episode_id": "ep_91",
  "score": null,
  "text": "partial refunds are allowed",
  "data": {"turn_index": 4},
  "idempotency_key": "support-ui/ep_91/turn-4/correction"
}
```

A `finding` from ClawInsight carries its `plan-source/v2` item in `data`, so
that the producer-agnostic findings handoff that exists today reaches
strategies through `experience.feedback@1` (see
[04-default-strategies.md](04-default-strategies.md)):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "feedback_id": "fb_311",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "kind": "finding",
  "source": "clawinsight",
  "created_at": "2026-10-08T01:00:00Z",
  "revision_id": "sha256:a90b…",
  "episode_id": null,
  "score": null,
  "text": "Refund requests for partial orders are escalated unnecessarily",
  "data": {"contract": "plan-source/v2", "item_id": "imp_88", "affected_episodes": ["ep_91", "ep_97"]},
  "idempotency_key": "clawinsight/imp_88"
}
```

### 2.3 Eval trace

An eval trace is the record of one evaluation rollout. The Verification
Service produces it; the store keeps it so that scores and critiques are
attributable to a revision and available to the ledger and to training-data
export. Which split a trace came from decides who may ever see it (§6).

```python
Split = Literal["train", "validation", "holdout", "regression", "safety"]

@dataclass(frozen=True)
class GraderResult:
    grader: str               # id of the grader that scored the rollout, e.g. "platform/clawbench"
    score: float              # 0 = complete failure, 1 = perfect, by that grader's rubric
    critique: str             # textual critique; reflective strategies need it

@dataclass(frozen=True)
class EvalTrace:
    trace_id: str             # "et_5521"
    bot: BotRef               # the bot whose revision was evaluated (owner + bot id)
    evaluation_id: str        # the Verification evaluation this rollout belongs to
    revision_id: str          # always known: verification evaluates a specific revision
    suite: str                # suite id, e.g. "bot_123/support"
    case_id: str              # the case that was run
    split: Split
    seed: int                 # which repetition of the case this is (each seed is one rollout)
    transcript: list[Turn]    # the rollout's conversation, same shape as Episode.turns
    grades: list[GraderResult]
    cost: Cost
    started_at: datetime
    ended_at: datetime
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "trace_id": "et_5521",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "evaluation_id": "eval_train_77",
  "revision_id": "sha256:c41e…",
  "suite": "bot_123/support",
  "case_id": "case_partial_refund_03",
  "split": "train",
  "seed": 2,
  "transcript": [
    {"role": "user", "text": "I want half my money back for order A17."},
    {"role": "assistant", "text": "Partial refunds are allowed within 30 days. I can start one for you."}
  ],
  "grades": [{"grader": "platform/clawbench", "score": 0.9,
              "critique": "Correct policy; did not confirm the refund amount."}],
  "cost": {"usd": 0.004, "input_tokens": 1900, "output_tokens": 120},
  "started_at": "2026-10-08T02:31:10Z",
  "ended_at": "2026-10-08T02:31:22Z"
}
```

### 2.4 Session export and retention policy

`SessionExport` is the store's request to an engine provider (§5).
`RetentionPolicy` is the per-tenant data-handling configuration (§7).

```python
@dataclass(frozen=True)
class SessionExport:
    export_id: str                # "sx_402"
    bot: BotRef                   # whose sessions are exported (owner + bot id)
    engine: Engine                # the engine whose provider runs the export
    since: datetime               # window start (inclusive)
    until: datetime               # window end (exclusive)
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    package_digest: str | None    # set once succeeded: digest of the exported session package
    error: str | None             # set once failed

@dataclass(frozen=True)
class RetentionPolicy:
    tenant_id: str
    episode_days: int             # episodes and eval traces older than this many days are deleted
    feedback_days: int            # feedback older than this many days is deleted
    pii_categories: list[PiiCategory]   # categories redacted at ingest, e.g. ["email", "phone", "address"]
    training_export_opt_in: bool  # §7: export for training only with explicit tenant opt-in
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "session_export": {
    "export_id": "sx_402",
    "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
    "engine": "openclaw",
    "since": "2026-10-07T00:00:00Z",
    "until": "2026-10-08T00:00:00Z",
    "status": "succeeded",
    "package_digest": "sha256:e09a…",
    "error": null
  },
  "retention_policy": {
    "tenant_id": "tenant_9",
    "episode_days": 90,
    "feedback_days": 365,
    "pii_categories": ["email", "phone", "address"],
    "training_export_opt_in": false
  }
}
```

## 3. Revision attribution

Attribution is the store's main reason to exist, so the rule is strict: an
episode is tagged with the genome revision that was **applied to the bot
when the episode started**.

- Once the Genome Registry is in place, every apply report records the
  `revision_id` it applied ([01-genome.md](01-genome.md); today apply
  reports record resolved git SHAs, not the document applied —
  `services/config_manifest_apply_service.py:846-875`). The store resolves an
  episode's revision from that apply history and the episode's `started_at`.
  This is **proposed**; the alternative is that the engine stamps the
  revision into each exported session (§5, open decision X-2).
- Episodes recorded before the bot had any revision, or that cannot be
  matched (for example, an apply was in flight), get `revision_id: null` and
  are reported as **unattributed**. They are still useful as diagnosis input,
  but they are excluded from any per-revision comparison.
- **Service bots.** A service bot serves a published version, not the
  draft. Each publish record stores the `revision_id` it published
  ([01-genome.md](01-genome.md), RSI-04), so the store maps the published
  version that was serving when the episode started to its revision.
- Feedback inherits the revision of its episode. Feedback without an episode
  takes the revision the producer supplies, or the bot's `active` revision at
  `created_at`.
- Eval traces always carry a revision, because Verification evaluates a
  specific revision.

When this happens: the revision id is attached **at ingest**, once, and
stored with the record; it is never rewritten later.

Example timeline for `bot_123`:

| Time | Event | Records produced and their `revision_id` |
| --- | --- | --- |
| Sep 20 | Bot runs on Manifest v1; no revisions exist yet | Episode `ep_12` → `null` (unattributed) |
| Sep 30 02:40 | First revision `r41` recorded and applied | (apply report records `r41`) |
| Oct 7 09:12 | Session starts | Episode `ep_91` → `r41` (the revision applied at 09:12) |
| Oct 7 09:13 | User corrects the bot in that session | Feedback `fb_204` → `r41` (copied from `ep_91`) |
| Oct 8 02:31 | Verification evaluates candidate `r42` in a sandbox | Eval trace `et_5521` → `r42` (known directly) |
| Oct 8 11:29 | Session starts a minute before the next apply finishes | Episode `ep_120` → `r41` (what was applied at its start) |
| Oct 8 11:30 | Promotion applies `r42` | (apply report records `r42`) |
| Oct 8 14:00 | Session starts | Episode `ep_131` → `r42` |

After a promotion (`active` moves from `r41` to `r42`), new episodes carry
`r42`. That is what lets a later run, or online verification, compare live
outcomes of `r41` and `r42`.

## 4. Sources and ingestion

### 4.1 Sources

| Source | Record | How it arrives | First iteration? |
| --- | --- | --- | --- |
| Engine sessions (OpenClaw first) | `Episode` | Store pulls via the engine's session-export provider (§5) on a schedule, and on demand before a run that needs `experience.sessions@1` | Yes |
| Product UI / pipelines (ratings, corrections, task outcomes) | `Feedback` (`rating`, `correction`, `outcome`) | `POST /bots/{bot_id}/experience/feedback` | Yes |
| ClawInsight improvement items | `Feedback` (`finding`) | Ingest adapter reading `plan-source/v2` items | Yes (needed by default strategies) |
| TaskGuard run evidence | `Feedback` (`run_evidence`) | Ingest adapter over TaskGuard's run evidence | Yes; TaskGuard itself stays runtime resilience, not evolution |
| BCS coordination outcomes | `Feedback` (`coordination_outcome`) | Ingest adapter, later | Proposed, after the first iteration |
| Verification rollouts | `EvalTrace` | Written by the Verification Service per rollout | Yes |
| Engine runtime memory (episodic memory the bot writes) | Evidence for consolidation | Captured as experience through the memory export contract (`export_memory`, RSI-05) | With RSI-05 |

### 4.2 Ingest pipeline

```text
engine provider ──export──▶ normalize ──▶ redact ──▶ attribute revision ──▶ index ──▶ store
                         (session-export/v2)  (§7)        (§3)            (bot, revision, time, outcome)
```

1. **Export.** The store asks the bot's engine provider for sessions in a
   time window (§5). The export is an operation: start returns an id, status
   is looked up by id.
2. **Normalize.** The provider's package is converted into `Episode`
   records. Normalization is deterministic and versioned: `source.export_api`
   and `source.content_digest` let the store re-normalize the same raw
   session later if the episode schema changes.
3. **Redact.** Secrets are removed and PII is handled per the tenant's
   `RetentionPolicy` before anything is stored (§7). The categories removed
   are listed in `redactions`.
4. **Attribute.** Set `revision_id` (§3).
5. **Index** by bot, revision, time, and outcome status, so the read paths
   (§6, §10) can filter cheaply.

Ingest is idempotent per `(owner_id, bot_id, source.engine,
source.session_id, source.content_digest)`: re-exporting a window does not duplicate episodes.
A session that changed since the last export (new turns) produces a new
digest and replaces the earlier episode with the same id.

Write failures propagate: if storing a normalized episode fails, the ingest
step fails and is retried; it never reports success for records it did not
write.

### 4.3 What exists today

Session acquisition already exists inside ClawEvolve's diagnose stage, under
`apps/evolverun/clawweb-skills/clawevolve-skills/clawevolve-diagnose/clawevolve_diagnose/acquisition/`:

| File | What it does today | In the new model |
| --- | --- | --- |
| `discovery.py` (`:41-96`) | Discovers the local OpenClaw layout: state directory, `agents/*/sessions`, `workspace/sessions`, workspace, skill and doc directories | Moves into the OpenClaw session-export provider in the engine adapter; strategies never see these paths |
| `sessions.py` | Parses OpenClaw JSONL session files and session stores into `SessionRow` (session id, bot id, created time, first question, user / assistant / tool text, original model) | Becomes the provider's normalizer, producing `Episode` instead of `SessionRow` |
| `service_export.py` | Acquires service-bot sessions through `session-export/v1`: `POST /api/integrations/v1/session-exports` with an `Idempotency-Key` header and a target `{userId, botId, stage, engineType: "openclaw"}`, then looks the export up by `exportId` until it is terminal, and downloads a `session-package/v1` archive. Explicit session selectors go through a separate session-analysis path | The pattern (idempotent start, status by id, package download) is kept and generalized as `session-export/v2` (§5) |

The `session-export/v1` server side is ClawWeb's ClawInsight module
(`clawweb/public/modules/clawinsight/server/routes/session-export-integration.ts`):
scopes `single | bot`, stages `all | draft | service`, and `engineType`
fixed to `openclaw`.

The migration (work item RSI-10): move `acquisition/discovery.py`,
`sessions.py`, and `service_export.py` behind the engine session-export
contract, normalize to `Episode`, and tag with the genome revision.
ClawEvolve's diagnose then reads episodes through
`ctx.experience.sessions()` instead of from disk. Done when episodes from an
OpenClaw bot are queryable by revision and ClawEvolve diagnose can read
from the store.

## 5. Engine session-export contract

The engine is the source of truth for raw sessions; the store holds a copy.
The boundary between them is a versioned **Plugin API** owned by the engine
adapter: `session-export/v1` exists today in ClawEvolve; the proposed
`session-export/v2` generalizes it so that every engine can provide
`experience.sessions@1`.

What v2 changes relative to v1 (proposed):

| Aspect | v1 (today) | v2 (proposed) |
| --- | --- | --- |
| Engines | `engineType` fixed to `openclaw` | Any engine with a provider; one provider per engine |
| Window | `since` / `until` on the client side | `since` / `until` in the request; provider returns only sessions that changed in the window |
| Output | `session-package/v1` archive of raw OpenClaw sessions, parsed by the caller | Package of raw sessions **plus** provider-normalized `Episode` drafts (turns, tool calls, timings, model, cost) |
| Revision | Not present | Optional `applied_revision` per session, if the engine knows it (open decision X-2) |
| Lifecycle | Start with idempotency key, poll by export id | Same: an operation (`queued`, `running`, `succeeded`, `failed`, `cancelled`), status by id, no request held open |

```python
from typing import Protocol

class SessionExportProvider(Protocol):
    """Engine-side provider of session export (session-export/v2). One per engine.

    Implemented in the engine adapter. Conformance-tested per engine following
    docs/arch/protocol-contract-tests.md.
    """

    engine: Engine

    async def start_export(self, *, bot: BotRef, since: datetime, until: datetime,
                           idempotency_key: str) -> str:
        """Start exporting the bot's sessions in [since, until). Returns an export id at once.

        Repeating the call with the same idempotency key returns the same export id.
        """

    async def get_export(self, export_id: str) -> SessionExport:
        """Look up an export by id. A short request; never waits for the export."""

    async def read_package(self, export_id: str) -> "SessionPackage":
        """Read a succeeded export's package: raw sessions plus normalized Episode drafts."""
```

The idempotency key the store uses for scheduled exports is
`<owner_id>/<bot_id>/<engine>/<since>/<until>`, so a retried export of the same window
returns the same export instead of exporting twice.

Capability catalog link: a bot can be bound to a strategy that needs
`experience.sessions@1` only if its engine has a session-export provider.
That check happens when the binding is created or changed
([03-strategy.md](03-strategy.md)).

## 6. Who sees what

Experience is sensitive (it is conversation history) and it is untrusted
(it is a prompt-injection and memory-poisoning vector). Both properties
shape the read paths.

| Reader | Path | What it gets |
| --- | --- | --- |
| Strategy with `experience.sessions@1` | `ctx.experience.sessions(…)` | Redacted episodes of the run's bot only |
| Strategy with `experience.feedback@1` | `ctx.experience.feedback(…)` | Redacted feedback of the run's bot only |
| Strategy with `evaluate.train@1` | The train evaluation result (Verification) | Scores and critiques of **train-split** rollouts only |
| Strategy, any | — | Never: eval traces of `validation` (aggregates only, through verdicts), `holdout`, `regression`, `safety`; any other bot's or tenant's experience |
| Owner, tenant admin, pipelines | Public API (§10), UI, `avn experience episodes` and `avn experience feedback` | Their bots' episodes and feedback |
| Verification | Internal | Recent episodes for shadow replay; failed episodes as candidates for new `regression` cases (diagnose → plan pipeline) |
| Experiment Ledger | Internal | Evidence ids and eval traces, for audit and training-data export |

Rules:

- **Capabilities are bot-scoped and redacted.** A capability call returns
  only the run's bot and only post-redaction content. It is not a query
  language over the store.
- **Hidden splits stay hidden.** Eval traces from non-train splits are never
  served through any capability, matching the anti-reward-hacking rules in
  [07-verification.md](07-verification.md).
- **Reading conversation history is shown to owners.** When an owner binds
  a strategy whose registration `needs` `experience.sessions@1`, the UI and
  CLI show that the strategy reads conversation history.
- **Experience is untrusted input.** Strategies must treat episode and
  feedback text as data, never as instructions. Patches derived from it get
  secret/PII/URL scanning at the gate ([08-promotion.md](08-promotion.md)).

### 6.1 The two capabilities

Both are part of the platform's capability catalog
([03-strategy.md](03-strategy.md)); the number after `@` is the version of
the capability's contract. If the episode format changes incompatibly, the
platform publishes `experience.sessions@2` and keeps serving `@1` to
strategies registered against `@1`. Both calls are quick reads, so they are
plain request and response, not operations.

```python
class ExperienceQuery(Protocol):
    """ctx.experience — present only if the strategy declared the capability in `needs`.

    Calling a method of a capability that was not declared raises CapabilityNotGranted.
    """

    # experience.sessions@1
    async def sessions(self, *, days: int, limit: int = 500,
                       revision: str | None = None) -> list[Episode]:
        """Episodes of the run's bot from the last `days` days, newest first.

        revision=None returns episodes of every revision, including unattributed ones;
        a revision id restricts the result to episodes that ran that revision.
        """

    # experience.feedback@1
    async def feedback(self, *, days: int, kinds: list[FeedbackKind] | None = None,
                       limit: int = 500) -> list[Feedback]:
        """Feedback for the run's bot from the last `days` days, newest first.

        kinds=None returns every kind. (Proposed signature; the source docs fix only the
        capability's purpose: ratings, corrections, and outcomes.)
        """
```

For a job-worker strategy, these calls map to the Job Protocol endpoints listed under
[Internal APIs](#internal-apis).

## 7. Data handling

This section holds the governance rules for experience data
(governance topic **data handling**).

- **Retention per tenant.** Episodes, feedback, and eval traces are kept for
  the tenant's configured period (`RetentionPolicy`) and then deleted by a
  retention sweep. The Experience Store is a retention-bounded copy; the
  engine's raw history follows the engine's own rules.
- **Redaction on ingest.** Secrets are removed before a record is stored,
  using the same scanner rules as the repository's pre-push credential hook
  (private keys, provider tokens, bearer/JWT credentials, high-entropy values
  in credential-like fields). Nothing unredacted reaches the store, so
  nothing unredacted can reach a strategy.
- **PII policy is configurable** per tenant: which categories
  (`pii_categories`) are removed or masked at ingest. Each record lists the
  categories removed in `redactions`.
- **Export for training only with explicit tenant opt-in**
  (`training_export_opt_in`). The export itself is a ledger operation
  ([05-experiment-ledger.md](05-experiment-ledger.md)); it refuses tenants
  that have not opted in.
- **No cross-tenant use.** Using one tenant's experience, or what a strategy
  learned from it, for another tenant is forbidden by default. Capabilities
  are bot-scoped, which also rules out cross-bot reads inside a tenant.
- **Cross-bot skill transfer** (P6) does not read another bot's experience.
  It goes through Skill Center governance (ADR 0010: Skill Center
  distributes, write authority stays with the managing source) and is
  re-evaluated per consuming bot.
- **Untrusted input.** Experience is treated as data by strategies; patches
  derived from it are scanned at the gate (§6).
- **References outlive content.** When retention deletes an episode,
  candidates and ledger entries that cite `episode:ep_91` keep the id; the
  content is gone. The ledger keeps its own aggregates, not episode text.

## 8. Bridge to weight training

Weight training is out of scope, but the store keeps the door open at no
extra cost. Every record already holds the tuple that SFT, RL, and DPO
pipelines want:

```text
(input, genome_revision, output, grader scores, critiques, cost)
```

- For an `Episode`: input = the user turns, output = the assistant turns and
  tool calls, scores = linked feedback.
- For an `EvalTrace`: input = the case, output = the transcript, scores and
  critiques = `grades`.

Accepted versus rejected candidate pairs on the same case are preference
data; they, and the redacting export endpoint, belong to the Experiment
Ledger ([05-experiment-ledger.md](05-experiment-ledger.md)). The export is a
P6 item, not a P1 dependency.

## 9. Service interface

The store's interface for other components. Public callers use the API (§10)
through the generated SDK; strategies use the capabilities (§6.1).

```python
from typing import Protocol

@dataclass(frozen=True)
class EpisodeFilter:
    revision: str | None = None          # None = all revisions, including unattributed
    since: datetime | None = None        # None = from the start of retention
    until: datetime | None = None        # None = now
    outcome: OutcomeStatus | None = None # None = any outcome

@dataclass(frozen=True)
class Page[T]:                           # the shared OpenAPI v1 page (09-evolution-api.md)
    total: int                           # items matching the query, all pages
    items: list[T]


class ExperienceStore(Protocol):
    """Experience Store (apps/evolution). Transport-agnostic core interface."""

    # --- ingest -------------------------------------------------------------
    async def ingest_window(self, *, bot: BotRef, since: datetime, until: datetime) -> str:
        """Export and ingest the bot's sessions in [since, until) from its engine provider.

        Returns the export id at once (an operation). Idempotent per window (§5).
        """

    async def record_feedback(self, bot: BotRef, feedback: "FeedbackInput",
                              *, idempotency_key: str) -> Feedback:
        """Store one feedback record after redaction and attribution.

        The same (owner_id, bot_id, idempotency_key) returns the stored record; a different body
        under a reused key raises IdempotencyConflict.
        """

    async def record_eval_trace(self, trace: EvalTrace) -> None:
        """Store one rollout. Called by Verification only. Idempotent per trace_id."""

    # --- read ---------------------------------------------------------------
    async def list_episodes(self, bot: BotRef, flt: EpisodeFilter, *,
                            page: int, page_size: int) -> Page["EpisodeSummary"]:
        """Episode summaries (no turns), newest first."""

    async def get_episode(self, bot: BotRef, episode_id: str) -> Episode:
        """One episode with turns. Raises NotFound if absent or deleted by retention."""

    async def list_feedback(self, bot: BotRef, *, kinds: list[FeedbackKind] | None,
                            episode_id: str | None, since: datetime | None,
                            page: int, page_size: int) -> Page[Feedback]:
        """Feedback records, newest first."""

    async def eval_traces(self, *, evaluation_id: str,
                          splits: list[Split]) -> list[EvalTrace]:
        """Rollouts of one evaluation. Internal: Verification and the Experiment Ledger.

        Never exposed to strategies except train-split results through evaluate.train@1.
        """

    # --- capabilities -------------------------------------------------------
    def query_for_run(self, *, run_id: str, bot: BotRef,
                      granted: set[str]) -> ExperienceQuery:
        """The ctx.experience object for one run: bot-scoped, redacted, only granted parts."""

    # --- data handling ------------------------------------------------------
    async def apply_retention(self, tenant_id: str) -> int:
        """Delete records past the tenant's retention. Returns the number deleted."""
```

`FeedbackInput` is the request body of `POST …/feedback` (§10);
`EpisodeSummary` is an `Episode` without `turns`, plus `turn_count` and
`feedback_count`.

## 10. API

All paths are relative to the public API prefix `/openapi/v1`. Shared
conventions (pagination, error format, idempotency keys, ETags) are defined
in [09-evolution-api.md](09-evolution-api.md). Callers are bot owners and
tenant admins (UI, `avn experience`), and pipelines and product backends
(client SDK). Responses below show the `data` payload of the standard
envelope; see [09-evolution-api.md](09-evolution-api.md) for the envelope,
errors, pagination, and idempotency.

### `GET /bots/{bot_id}/experience/episodes`

Lists episode summaries of a bot, newest first. Called by the UI, the CLI
(`avn experience episodes`), and pipelines that look at how a revision is
doing.

Query parameters: `revision` (revision id or `unattributed`), `since`,
`until`, `outcome`, `page` (1-based), `page_size` (1 to 100, default 20).

Example request:

```text
GET /openapi/v1/bots/bot_123/experience/episodes?revision=sha256:a90b…&outcome=user_corrected&page=1&page_size=2
```

Example response (`200`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 9,
  "items": [
    {
      "episode_id": "ep_97",
      "revision_id": "sha256:a90b…",
      "revision_seq": "r41",
      "started_at": "2026-10-07T15:40:12Z",
      "ended_at": "2026-10-07T15:43:02Z",
      "model": "platform-default",
      "cost": {"usd": 0.009, "input_tokens": 4011, "output_tokens": 301},
      "outcome": {"status": "user_corrected", "feedback": "store credit is an option", "feedback_ids": ["fb_219"]},
      "turn_count": 6,
      "feedback_count": 1,
      "redactions": ["email"]
    },
    {
      "episode_id": "ep_91",
      "revision_id": "sha256:a90b…",
      "revision_seq": "r41",
      "started_at": "2026-10-07T09:12:00Z",
      "ended_at": "2026-10-07T09:14:31Z",
      "model": "platform-default",
      "cost": {"usd": 0.012, "input_tokens": 5210, "output_tokens": 388},
      "outcome": {"status": "user_corrected", "feedback": "partial refunds are allowed", "feedback_ids": ["fb_204"]},
      "turn_count": 5,
      "feedback_count": 1,
      "redactions": ["email", "phone"]
    }
  ]
}
```

Errors: `404` unknown bot; `400` invalid filter (for example `since` after
`until`, unknown `outcome`).

### `GET /bots/{bot_id}/experience/episodes/{episode}`

Returns one episode with its turns. Called by the UI's episode view, the CLI,
and reviewers following a candidate's evidence ids.

Example request:

```text
GET /openapi/v1/bots/bot_123/experience/episodes/ep_91
```

Example response (`200`): the full `Episode` from §2.1.

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "episode_id": "ep_91",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "revision_id": "sha256:a90b…",
  "revision_seq": "r41",
  "started_at": "2026-10-07T09:12:00Z",
  "ended_at": "2026-10-07T09:14:31Z",
  "turns": [
    {"role": "user", "at": "2026-10-07T09:12:00Z", "text": "Can I get a refund for half of my order?"},
    {"role": "assistant", "at": "2026-10-07T09:12:04Z", "text": "Let me look up the order.",
     "tool_calls": [{"name": "order_lookup", "args": {"id": "A17"}}]},
    {"role": "tool", "at": "2026-10-07T09:12:05Z", "name": "order_lookup", "result": "{\"id\": \"A17\", \"total\": 84.0}"},
    {"role": "assistant", "at": "2026-10-07T09:12:09Z", "text": "Partial refunds are not possible."},
    {"role": "user", "at": "2026-10-07T09:13:50Z", "text": "Your policy page says partial refunds are allowed."}
  ],
  "model": "platform-default",
  "cost": {"usd": 0.012, "input_tokens": 5210, "output_tokens": 388},
  "outcome": {"status": "user_corrected", "feedback": "partial refunds are allowed", "feedback_ids": ["fb_204"]},
  "redactions": ["email", "phone"],
  "source": {"engine": "openclaw", "export_api": "session-export/v2", "session_id": "s-20261007-0912",
             "content_digest": "sha256:7d1e…"}
}
```

Errors: `404` unknown bot, or episode unknown or deleted by retention.

### `POST /bots/{bot_id}/experience/feedback`

Records one feedback item. Called by product backends and UIs (ratings,
corrections), pipelines (task outcomes), and platform ingest adapters
(ClawInsight findings, TaskGuard run evidence).

The `Idempotency-Key` header is required. It is a client-chosen string,
identical for every retry of one logical feedback item and different for
different items; the platform stores `(owner_id, bot_id, key) → feedback
id`. Good keys
are derived from what the feedback is about, for example
`support-ui/ep_91/turn-4/correction` or `clawinsight/imp_88`; a send-time
timestamp is not a valid key, because it changes between retries.

Example request:

```text
POST /openapi/v1/bots/bot_123/experience/feedback
Idempotency-Key: support-ui/ep_91/turn-4/correction
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "kind": "correction",
  "source": "user",
  "episode_id": "ep_91",                     // optional; revision is copied from the episode
  "text": "partial refunds are allowed",
  "data": {"turn_index": 4}
}
```

Example response (`201`; `200` with the same body when the key was already
used for the same request):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "feedback_id": "fb_204",
  "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
  "kind": "correction",
  "source": "user",
  "created_at": "2026-10-07T09:13:50Z",
  "revision_id": "sha256:a90b…",
  "episode_id": "ep_91",
  "score": null,
  "text": "partial refunds are allowed",
  "data": {"turn_index": 4},
  "idempotency_key": "support-ui/ep_91/turn-4/correction"
}
```

Errors: `400` body does not match the schema for `kind`; `404` unknown bot
or `episode_id`; `409` the idempotency key was already used with a different
body; `428` missing `Idempotency-Key`.

### `GET /bots/{bot_id}/experience/feedback`

Lists feedback of a bot, newest first. Called by the UI, the CLI
(`avn experience feedback`), and pipelines.

Query parameters: `kind` (repeatable), `episode`, `revision`, `since`,
`page` (1-based), `page_size` (1 to 100, default 20).

Example request:

```text
GET /openapi/v1/bots/bot_123/experience/feedback?kind=correction&kind=rating&since=2026-10-07T00:00:00Z&page=1&page_size=2
```

Example response (`200`):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "total": 2,
  "items": [
    {
      "feedback_id": "fb_219",
      "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
      "kind": "rating",
      "source": "user",
      "created_at": "2026-10-07T15:43:10Z",
      "revision_id": "sha256:a90b…",
      "episode_id": "ep_97",
      "score": 0.0,
      "text": null,
      "data": {"scale": "thumbs"},
      "idempotency_key": "support-ui/ep_97/rating"
    },
    {
      "feedback_id": "fb_204",
      "bot": {"owner_id": "user_owner_5", "bot_id": "bot_123"},
      "kind": "correction",
      "source": "user",
      "created_at": "2026-10-07T09:13:50Z",
      "revision_id": "sha256:a90b…",
      "episode_id": "ep_91",
      "score": null,
      "text": "partial refunds are allowed",
      "data": {"turn_index": 4},
      "idempotency_key": "support-ui/ep_91/turn-4/correction"
    }
  ]
}
```

Errors: `404` unknown bot; `400` invalid filter.

### Internal APIs

These are not part of the public API.

**Job Protocol (capability calls of job-worker strategies).** The Job
Protocol is specified in [06-evolution-run.md](06-evolution-run.md); the two
endpoints that serve this component's capabilities are:

```text
GET  /evolution/v1/runs/{run}/experience/sessions   ctx.experience.sessions   (if granted)
GET  /evolution/v1/runs/{run}/experience/feedback   ctx.experience.feedback   (if granted)
```

Both return `403` if the capability was not granted to the run, and `409`
for a stale fencing token. Example:

```text
GET /evolution/v1/runs/run_7f3/experience/sessions?days=7&limit=200
```

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
        {"role": "assistant", "text": "Let me look up the order.", "tool_calls": [{"name": "order_lookup", "args": {"id": "A17"}}]},
        {"role": "tool", "name": "order_lookup", "result": "{\"id\": \"A17\", \"total\": 84.0}"}
      ],
      "outcome": {"status": "user_corrected", "feedback": "partial refunds are allowed"},
      "redactions": ["email", "phone"]
    }
  ]
}
```

**Engine session export (Plugin API).** `SessionExportProvider` (§5) is a
Python Protocol in the engine adapter. Where an engine serves it over HTTP
(as `session-export/v1` does today from ClawWeb), the wire form keeps the v1
shape: an idempotent `POST` that returns an export id, and a `GET` by export
id for status and the package. Its exact v2 wire form is part of RSI-10.

**Eval trace write (Verification → store).** `record_eval_trace` (§9) is an
in-process call inside `apps/evolution`; it has no public endpoint.

**Not in the first iteration.** `POST /bots/{bot_id}/experience/observations`
(fast-loop notes, §1) is postponed.

## 11. Examples

### 11.1 A product backend records user feedback

```python
# Illustrative only
from avernet_evolution import Client

c = Client.from_env()

def on_user_correction(bot_id: str, episode_id: str, turn_index: int, text: str) -> None:
    # The key names the thing being reported, so every retry sends the same key.
    c.experience.feedback.create(
        bot_id=bot_id,                     # caller owns the bot; otherwise also pass entity_id=<owner>
        kind="correction",
        source="user",
        episode_id=episode_id,
        text=text,
        data={"turn_index": turn_index},
        idempotency_key=f"support-ui/{episode_id}/turn-{turn_index}/correction",
    )
```

### 11.2 An owner compares two revisions

```python
# Illustrative only
from collections import Counter

def outcome_rates(c, bot_id: str, revision: str) -> dict[str, float]:
    counts: Counter[str] = Counter()
    for ep in c.experience.episodes.list(bot_id=bot_id, revision=revision, since="2026-10-01T00:00:00Z"):
        counts[ep.outcome.status] += 1     # the SDK walks all pages
    total = sum(counts.values()) or 1
    return {status: n / total for status, n in counts.items()}

before = outcome_rates(c, "bot_123", "sha256:a90b…")   # r41
after = outcome_rates(c, "bot_123", "sha256:c41e…")    # r42, after promotion
```

The same comparison from the CLI:

```text
avn experience episodes --bot bot_123 --revision r41 --outcome user_corrected --output json
```

### 11.3 A strategy reads experience

ClawEvolve's diagnose step, inside its `run(ctx)` (the full strategy is in
[04-default-strategies.md](04-default-strategies.md)):

```python
# Illustrative only
episodes = await ctx.experience.sessions(days=ctx.params["window_days"], revision=ctx.parent.id)
findings = diagnose(episodes)            # ClawEvolve's own logic; episode text is data, not instructions
await ctx.evaluate.add_train_cases(plan_bench(findings))   # platform assigns splits
```

A memory-consolidation strategy that declared only `experience.feedback@1`:

```python
# Illustrative only
items = await ctx.experience.feedback(days=7, kinds=["correction", "finding"])
lessons = cluster_into_lessons(items)    # its own logic
# ctx.experience.sessions(...) here would raise CapabilityNotGranted
```

### 11.4 An engine provider (OpenClaw)

```python
# Illustrative only — engine adapter, not Backend
class OpenClawSessionExport(SessionExportProvider):
    engine = "openclaw"

    async def start_export(self, *, bot, since, until, idempotency_key):
        return await self.exports.create_or_get(           # same key → same export id
            key=idempotency_key, owner_id=bot.owner_id, bot_id=bot.bot_id, since=since, until=until)

    async def get_export(self, export_id):
        return await self.exports.get(export_id)

    async def read_package(self, export_id):
        raw = await self.exports.package(export_id)          # raw OpenClaw JSONL sessions
        drafts = [normalize_openclaw_session(s) for s in raw.sessions]  # today's sessions.py parser, emitting Episode
        return SessionPackage(raw=raw, episodes=drafts)
```

## 12. Interactions

| Component / service | Direction | What flows |
| --- | --- | --- |
| Engine adapter (session-export provider) | Engine → Experience | Exported sessions and normalized episode drafts (`session-export/v2`) |
| Engine adapter (memory export, RSI-05) | Engine → Experience | Runtime memory items as evidence for consolidation |
| [01-genome.md](01-genome.md) Genome Registry | Genome → Experience | Apply history (which revision was applied when) for revision attribution; `active` revision for unattached feedback |
| [03-strategy.md](03-strategy.md) Strategy | Experience → Strategy | `experience.sessions@1` and `experience.feedback@1` contracts in the capability catalog; binding check needs a session-export provider for the bot's engine |
| [04-default-strategies.md](04-default-strategies.md) Default strategies | Experience → Strategy | Episodes for ClawEvolve diagnose; feedback and findings for memory consolidation |
| [05-experiment-ledger.md](05-experiment-ledger.md) Experiment Ledger | Experience → Ledger | Evidence ids cited by candidates; eval traces; the training tuple for export (opt-in only) |
| [06-evolution-run.md](06-evolution-run.md) Evolution Run | Run → Experience | Builds `ctx.experience` per run with the granted parts; serves the Job Protocol experience endpoints |
| [07-verification.md](07-verification.md) Verification | Both | Verification writes eval traces; reads recent episodes for shadow replay and failed episodes as candidates for `regression` cases |
| [08-promotion.md](08-promotion.md) Promotion | — (indirect) | Promotion moves `active`; later episodes carry the new revision. The gate scans patches derived from experience |
| [09-evolution-api.md](09-evolution-api.md) Evolution API | Clients → Experience | Public experience endpoints, SDK, and `avn experience` commands |
| [10-meta-evolution.md](10-meta-evolution.md) Meta-evolution (later) | Experience → Meta | Frozen experience snapshots for improvement problems |
| ClawInsight, TaskGuard, BCS | Producers → Experience | Findings (`plan-source/v2`), run evidence, coordination outcomes as feedback |
| Product backends and UIs | Producers → Experience | Ratings, corrections, task outcomes |

## 13. Open decisions

- **X-1: Where eval traces live.** The architecture lists eval traces as
  part of the Experience Store. The alternative is to keep them inside
  Verification and give the store only references. Keeping them here gives
  one place for the training tuple and one retention policy; keeping them in
  Verification keeps hidden-split data physically with its only owner.
  Recommendation: store here, with split-based access enforced by the store
  (§6), and revisit if the access rules become hard to audit.
- **X-2: How an episode gets its revision.** Resolve from apply history by
  time (proposed, §3), or have the engine stamp the applied revision into
  each session (`applied_revision` in `session-export/v2`). Engine stamping
  is exact but needs every engine to know about genome revisions; apply
  history works for every engine but is ambiguous while an apply is in
  flight. Recommendation: apply history first, engine stamping where an
  engine supports it.
- **X-3: Pull or push for session export.** v1 is pull (the caller starts an
  export). Engines could instead push sessions as they finish. Pull keeps
  the engine contract small and is what exists; push gives fresher data.
  Recommendation: pull on a schedule plus on demand before a run.
- **X-4: Default retention and PII categories.** The governance rules say
  "per tenant" and "configurable" but no defaults are set. They need an
  owner decision before RSI-10 ships.
- **X-5: BCS coordination outcomes.** The architecture lists them as
  feedback, but no ingest path or schema exists yet. Decide the payload with
  BCS owners when a strategy needs it.
- **X-6: Feedback signature of `experience.feedback@1`.** The sources fix
  the purpose but not the method signature; §6.1 proposes one. It must be
  fixed before the capability's conformance test is written.
- **X-7: Episode granularity.** "One session or task trajectory": for
  long-lived sessions that span many tasks, decide whether an episode is the
  whole session or one task inside it. Today's parser works per session.
