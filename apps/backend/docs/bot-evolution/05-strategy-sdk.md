# Strategy SDK — making evolution pluggable

> 中文版：[05-strategy-sdk.zh-CN.md](05-strategy-sdk.zh-CN.md)

> Status: DRAFT. How a team plugs an evolution approach (ClawEvolve today,
> others later) into the platform without changing platform code.

## 1. Principles

1. **One port.** The platform knows a single strategy interface with a
   single method, `run(ctx)`. ClawEvolve, another team's optimizer, and the
   platform's own composed strategy all implement the same port.
2. **One door out.** A strategy reaches the platform only through the
   context it is given. It cannot promote, read held-out tests, touch the
   live bot, or read platform storage. Isolation and budgets are therefore
   enforced in one place, whatever the strategy is. (A **budget** is the
   per-run spending limit set in the bot's binding: model spend in USD,
   wall-clock time, and evaluation rollouts. Every model call and evaluation
   is charged through `ctx.budget`, and the run stops when it runs out.)
   This is a deliberate limitation: a strategy can use only what the
   capability catalog (§4) provides, and cannot bring its own model keys
   or agent runtimes. Its own computation (parsing, search, ranking) is
   unrestricted. A new need is met by adding a catalog entry once a second
   strategy needs it, not by an exception for one strategy.
3. **Strategies propose; the platform decides.** A strategy submits
   candidates. Recording, verification, the gate, and promotion stay
   platform-owned (DR-2).
4. **Facts about code are registered; choices about bots are configured.**
   Some information is true of a strategy version no matter which bot uses
   it. For example, "ClawEvolve 2.0 drives OpenClaw agents and reads
   conversation history". That is recorded once, when the version is
   registered (§3). Other information is a decision about one bot. For
   example, "bot_123 runs ClawEvolve nightly, may change persona and skills,
   and has a $20 budget". That lives in the bot's binding (§5), and the
   owner can change it at any time without touching the strategy.
5. **Few concepts, defined once.** Every term below has one definition; no
   field restates another (for example, engine compatibility is derived from
   `needs`, not declared separately).

![Strategy port and context](images/strategy-port.svg)

## 2. Domain model

| Concept | What it is | Owned by | Changes when |
| --- | --- | --- | --- |
| **Strategy** | Code implementing `run(ctx)`, plus its registration record (§3) | Strategy author | New strategy version |
| **Capability** | A named, versioned part of the context a strategy may use, from a platform-owned catalog (§4) | Platform | Platform contract change |
| **Binding** | One entry in a bot's evolution policy: which strategy, when, what it may change, how it is verified, budget, params (§5) | Bot owner / tenant admin | Any time |
| **Run** | One execution of a binding, with strategy version, params, parent, and budget frozen at start; the run id returned at submission is its only handle (§7) | Platform | — |
| **StrategyContext** | The run's only door to the platform: always-granted parts plus the capabilities the strategy needs (§6) | Platform | — |
| **Candidate → Verdict** | A Genome Patch the strategy submits, and the platform's verification result for it (§6) | Strategy → platform | — |

## 3. Strategy: code plus a registration record

The interface is one method:

```python
class EvolutionStrategy(Protocol):
    async def run(self, ctx: StrategyContext) -> RunSummary: ...
```

**Registering** a strategy version tells the platform that it exists: where
its code runs (`runtime`) and which capabilities it needs (`needs`). Only a
registered version that passes the conformance kit (§11) can be bound to
bots. Registration produces the record below, stored in the Strategy
Registry (C3). It is data, not a method, because the platform needs it
without running the strategy's code (for example, before a job-worker
container exists):

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
{
  "id": "clawevolve/bot-evolution",
  "version": "2.0.0",
  "runtime": {"kind": "job_worker", "image": "registry.example/clawevolve@sha256:…"},
  "needs": {                                       // capabilities from the catalog (§4), with arguments
    "experience.sessions@1": {},
    "agents@1": {"definitions": {                  // agent definitions shipped with the strategy (§4.2)
      "clawevolve-tune":   {"engine": "openclaw", "path": "agents/clawevolve-tune"},
      "clawevolve-review": {"engine": "openclaw", "path": "agents/clawevolve-review"}
    }},
    "evaluate.train@1": {}
  }
}
```

There is no separate `supports_engines`: a binding check derives engine
compatibility from `needs` (§5).

## 4. Capability catalog

The platform owns a small, closed, versioned catalog. Each entry names a
part of `StrategyContext` and is a contract: method signatures, data schema,
semantics, and a conformance test (R25). Strategies can only declare names
from the catalog. Each entry has **providers per engine** (for example, the
engine adapter's session export provides `experience.sessions` for
OpenClaw), which is how a binding check knows what a bot can supply.

| Capability | Context part | What it gives | Notes |
| --- | --- | --- | --- |
| *(always granted)* | `parent`, `workspace`, `submit`, `budget`, `log`, `artifacts`, `cancelled` | Read the parent revision; materialise revisions to a sandbox and diff back to a patch; submit candidates; budget, logs, artifacts, cancellation | Nothing to declare |
| `experience.sessions@1` | `ctx.experience.sessions()` | The bot's past conversations as normalized episodes, filtered | Reads conversation history; shown to owners |
| `experience.feedback@1` | `ctx.experience.feedback()` | Ratings, corrections, outcomes, and subject-bot observations from the inbox | |
| `agents@1` `{definitions}` | `ctx.agents.run(definition, …)` | Run one of the strategy's own agent definitions inside a sandbox workspace | Each definition names its engine; the bot's engine must be among them (§4.2) |
| `evaluate.train@1` | `ctx.evaluate.train(…)`, `ctx.evaluate.add_train_cases(…)` | Platform evaluation on the **train split only**, with scores and critiques; adding train cases | Validation, holdout, regression, and safety stay hidden |

**What `@1` means.** The number after `@` is the version of the
*capability's contract* (its methods and data shapes), not the version of a
strategy. A strategy states which contract version it was written against.
If the platform later changes `experience.sessions` in an incompatible way
(for example, a different episode format), it publishes
`experience.sessions@2` and keeps serving `@1` to strategies registered
against `@1`. Adding an entry or a version is a reviewed platform change.

### 4.1 What capabilities look like

Each capability is a small, typed API on the context. Two examples:

```python
# experience.sessions@1
async def sessions(self, *, days: int, limit: int = 500,
                   revision: str | None = None) -> list[Episode]: ...
```

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// One Episode returned by ctx.experience.sessions(days=7)
{
  "episode_id": "ep_91",
  "revision_id": "sha256:a90b…",            // the genome revision the bot was running
  "started_at": "2026-10-07T09:12:00Z",
  "turns": [
    {"role": "user", "text": "Can I get a refund for half of my order?"},
    {"role": "assistant", "text": "…", "tool_calls": [{"name": "order_lookup", "args": {"id": "A17"}}]},
    {"role": "tool", "name": "order_lookup", "result": "…"}
  ],
  "outcome": {"status": "user_corrected", "feedback": "partial refunds are allowed"},
  "redactions": ["email", "phone"]           // personal data removed before the strategy sees it
}
```

```python
# agents@1 — registered with {"agents@1": {"definitions": {...}}} (§3)
async def run(self, definition: str, *, workspace: Workspace,
              prompt: str, timeout_s: int = 1800) -> AgentResult: ...
```

An **agent** here is a multi-step, tool-using agent session (an LLM that
reads and edits files over many steps), as opposed to a single model call.
For example, ClawEvolve's tune step calls
`ctx.agents.run("clawevolve-tune", workspace=ws, prompt=…)`. The platform
starts that agent inside the sandbox `ws`, not on the live bot, and returns
its transcript and exit status. Files it changed stay in `ws` until the
strategy turns them into a patch. A call naming a definition the strategy
did not register is refused.

### 4.2 Where agent definitions come from

`ctx.agents.run` names an agent, but the platform also needs the agent's
**definition**: its instructions, skills, and tool configuration. Today,
ClawEvolve's tune agent is the `clawevolve-tune` skill (`SKILL.md` plus
references) in `clawevolve-skills`, installed into the OpenClaw runtime
that ClawEvolve itself drives, so naming it is enough. A black-box strategy
from another team has no such shared install: the platform's sandboxed
runner has never seen its agents. The mechanism:

1. **Shipped with the strategy.** Each agent definition is a directory in
   the strategy's source, in the format of its engine (for OpenClaw: the
   agent's skill and configuration layout). The registration record lists
   each one under `agents@1.definitions` with its `engine` and `path` (§3).
2. **Uploaded at registration.** `avn strategy publish` uploads each
   definition directory to the Strategy Registry (C3), which stores it
   content-addressed (digest as in
   [02-genome.md §7.1](02-genome.md#71-content-reuse-the-manifest-content-store))
   and records the digest in the registration record. The platform never
   needs to read the strategy's container image or Python package to find
   them, so both runtimes (§9) work the same way.
3. **Validated at registration.** The `agents@1` provider for each named
   engine validates the definition against that engine's definition
   contract. A definition for an engine with no provider, or one that does
   not validate, fails registration.
4. **Loaded per call.** `ctx.agents.run("clawevolve-tune", …)` makes the
   provider load that definition by digest into the sandbox, next to the
   workspace `ws`. The definition is read-only to the agent; only `ws` is
   writable.
5. **Versioned with the strategy.** Definitions belong to a strategy
   version: changing a tune prompt means registering a new strategy
   version. The Experiment Ledger H records the definition digests with
   each run, so results are attributable to the exact prompts used.

The engines a strategy drives are the `engine` values of its definitions,
so there is no separate engine list.

## 5. Binding: which strategies a bot uses

A bot's evolution policy is a list of bindings. Different bots use different
strategies, and a bot may use several:

```jsonc
// Illustrative. Comments explain the example only; the canonical form is plain JSON (RFC 8785).
// Evolution policy of bot_123 (OpenClaw support bot)
{
  "bindings": [
    {
      "strategy": "clawevolve/bot-evolution@2.0.0",
      "trigger": {"schedule": "0 2 * * *"},           // or {"manual": true}, {"event": "failure_rate_alert"}
      "parent": "active",                             // which revision runs start from
      "allowed_genes": ["persona", "skills"],         // what this strategy may change on THIS bot
      "verification_profile": "default@1",            // owners may pick a stricter one, never a looser one
      "budget": {"max_usd": 20, "max_wall_clock_s": 7200},
      "params": {"window_days": 7, "max_rounds": 3}   // validated by the strategy
    },
    {
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

When a binding is created or changed, the platform checks it against the
bot. Every capability in `needs` must have a provider for the bot's engine;
the bot's engine must be among the `engine` values of the strategy's
`agents@1` definitions; `allowed_genes` must stay within the
bot's `policy` (locked genes stay locked). A mismatch is rejected at
configuration time, not partway through a paid run.

Triggers, parent choice, allowed genes, and verification strictness are
binding fields, owned by the bot owner. They are not strategy code.

## 6. StrategyContext, candidates, and verdicts

```python
class StrategyContext(Protocol):
    run_id: str; params: dict                       # frozen at run start
    parent: GenomeRevisionView                      # read-only: spec, files by digest, lineage
    workspace: WorkspaceFactory                     # materialise(revision) → sandbox dir; ws.to_patch()
    budget: BudgetMeter                             # remaining(); charge(); raises BudgetExhausted
    log: RunLog; artifacts: ArtifactSink; cancelled: CancellationToken
    attempt: int                                    # 1 on first dispatch, +1 on each re-dispatch (§7)
    async def submit(self, c: Candidate) -> str: ...      # returns the candidate id
    async def verdict(self, candidate_id: str) -> Verdict: ...  # status lookup by id

    # present only if declared in `needs`; otherwise access raises CapabilityNotGranted
    experience: ExperienceQuery                     # experience.sessions@1 / experience.feedback@1
    agents: AgentRunner                             # agents@1
    evaluate: TrainEvaluator                        # evaluate.train@1
```

- **Candidate** = a Genome Patch against a base revision, a rationale,
  evidence ids, and optional self-reported metrics (shown to reviewers,
  never used for acceptance).
- `submit` records the candidate and returns its **candidate id** at once;
  it does not wait for verification. It is idempotent: the candidate id is
  the content hash of the patch, so a retried submission (including one
  repeated after a re-dispatch, §7) returns the same id and creates no
  duplicate.
- **Verdict** = the result of looking up a candidate id: status `pending`,
  `accept`, `reject`, or `inconclusive`, with validation **aggregates**
  only, never per-case hidden data. The id is the only handle; there is no
  callback and no blocking call.
- A multi-round strategy such as ClawEvolve that builds the next round on
  the last accepted candidate looks the verdict up by id until it is no
  longer `pending`. The run stays `running` while it does, and that time
  counts against the binding's `max_wall_clock_s`.
- What a strategy submits is its own choice (its heuristics decide what is
  worth submitting). Whether a candidate is accepted is the platform's
  choice: verification under the binding's profile, then the gate and risk
  tier ([08-governance.md §2](08-governance.md#2-the-gate)).

## 7. Run lifecycle

```text
queued → running → completed | failed | cancelled | budget_exhausted
```

- **Start:** the orchestrator freezes the strategy version, params, parent,
  and budget; builds the context with exactly the granted capabilities; and
  reserves the budget.
- **During:** every model call and evaluation is charged to the budget.
- **End:** submissions made before a failure, cancellation, or budget stop
  are kept and still verified. Every submission, accepted or rejected, is
  recorded in the Experiment Ledger H with the strategy version.

### 7.1 Submission and status by id

Starting a run (a trigger firing, or a caller through the Evolution API,
[06-interfaces.md](06-interfaces.md)) returns a **run id**. Submission is
idempotent, and the platform guarantees it: the caller sends an
idempotency key, and a repeated submission with the same key returns the
same run id instead of starting a second run. From then on, the run id is
the only handle: callers look up status, submissions, and verdicts by it.
The same holds one level down: a strategy's `ctx.submit` returns a
candidate id, and verdicts are looked up by that id (§6).

### 7.2 Crashes and restarts

The work is split between the platform and the strategy:

| Concern | Owner | How |
| --- | --- | --- |
| The run record, its frozen inputs, budget spent, and candidates submitted | Platform | Persisted before `submit` or run submission returns |
| Noticing that a run's process died | Platform | Every run is a **leased job**. The worker (or the in-process host) renews the lease; when it expires (process crash, hardware failure, reboot), the job goes back to `queued` and is dispatched again with the same run id and `ctx.attempt + 1`. A fencing token rejects calls from the old holder. After `max_attempts` the run ends as `failed` |
| The strategy's own progress (round number, search state, history) | Strategy | The strategy persists whatever it needs in **its own storage**, keyed by run id, and on re-dispatch reloads it and continues. The platform has no checkpoint API and never reads this state; its shape differs from strategy to strategy |

A re-dispatched run uses the same frozen inputs and the same budget: what
earlier attempts spent stays spent. Because `submit` is idempotent, a
strategy that resubmits a candidate it had already submitted before the
crash gets the same candidate id back.

## 8. Two tiers on the same port

| Tier | What a team writes | When to use |
| --- | --- | --- |
| **Black box** | A whole strategy: `run(ctx)`, registered with the platform (§3) | An existing engine with its own inner loop (ClawEvolve, a GEPA-style optimizer, a coding-agent loop). This is the default way to plug in, and the only tier in the first iteration |
| **Composed** *(later)* | One small step that plugs into `platform/composed` | Reusing most of an existing strategy and swapping one piece |

The composed tier exists for teams that have a better *piece*, not a whole
strategy. `platform/composed` is itself an ordinary strategy shipped by the
platform. Its params list a sequence of small steps, and it calls each step
in turn. For example, suppose a team has a better way to find the root
causes of failures, but no tuning loop of its own. Instead of writing a
complete strategy, it would write one "analyze" step. A binding would then
run `platform/composed` with params such as
`{"steps": ["team-x/root-cause-analyzer@1", "clawevolve/tune@2"]}`, reusing
ClawEvolve's tuning. To the orchestrator, this is just another strategy.

The step types (analyze, propose, and so on) are defined only when a second
team actually needs to swap a piece (R19: abstract after two examples).
Until then, ClawEvolve and other strategies plug in as black boxes.

## 9. Runtimes

| `runtime.kind` | How it runs | How `ctx` reaches it |
| --- | --- | --- |
| `in_process` | Python package loaded by the `apps/evolution` composition root, selected by configuration (R5/R14) | Direct Python objects |
| `job_worker` | Container image (any language) | The Job Protocol below: each `ctx` call maps to one HTTP endpoint |

### Job Protocol

```text
POST /evolution/v1/jobs:claim                       {worker_id, strategy_ids[]} → job {run_id, attempt, params, parent, budget, granted, fencing_token}
POST /evolution/v1/jobs/{id}/heartbeat              lease renewal; an expired lease re-queues the job (§7.2)
GET  /evolution/v1/runs/{run}/parent                ctx.parent
GET  /evolution/v1/runs/{run}/content/{digest}      file bytes of the parent / workspace
GET  /evolution/v1/runs/{run}/experience/sessions   ctx.experience.sessions   (if granted)
GET  /evolution/v1/runs/{run}/experience/feedback   ctx.experience.feedback   (if granted)
POST /evolution/v1/runs/{run}/agents:run            ctx.agents.run            (if granted)
POST /evolution/v1/runs/{run}/evaluations:train     ctx.evaluate.train        (if granted)
POST /evolution/v1/runs/{run}/candidates            ctx.submit → {candidate_id}   (idempotent)
GET  /evolution/v1/runs/{run}/candidates/{id}       ctx.verdict → {status, aggregates}
POST /evolution/v1/runs/{run}/budget:charge         ctx.budget.charge
POST /evolution/v1/jobs/{id}/complete | /fail       RunSummary | {reason, retryable}
```

All payloads are JSON with JSON Schemas. A worker gets no credentials to
the bot; endpoints for capabilities that were not granted return `403`;
calls with a stale fencing token return `409`.

Workers are platform-run containers. Bots acting as workers ("runner bots")
are postponed together with DR-3
([decisions/0003](decisions/0003-bot-principal-for-evolution-surface.md)).

## 10. Examples

**ClawEvolve as a black-box strategy.** Its internals stay: diagnose logic,
tune prompt, mutation operator library, round loop. Only the edges move to
the context.

```python
class ClawEvolveStrategy(EvolutionStrategy):
    async def run(self, ctx):
        state = await self.store.load(ctx.run_id)                        # its own storage, not the platform's
        if state is None:                                                # first attempt
            findings = diagnose(await ctx.experience.sessions(days=ctx.params["window_days"]))
            await ctx.evaluate.add_train_cases(plan_bench(findings))      # platform assigns splits
            state = State(findings=findings, base=ctx.parent.id, next_round=0)
            await self.store.save(ctx.run_id, state)
        while state.next_round < ctx.params["max_rounds"]:
            if state.pending is None:
                ws = await ctx.workspace.materialise(state.base)         # sandbox, not the live bot
                await ctx.agents.run("clawevolve-tune", workspace=ws,
                                     prompt=build_tune_prompt(state.findings, state.history))
                train = await ctx.evaluate.train(ws)                     # replaces its own bench step
                if train.score > state.best_train:                       # its own heuristic
                    state.pending = await ctx.submit(Candidate(patch=ws.to_patch(), rationale=...,
                                                               evidence=state.findings.ids))
                    await self.store.save(ctx.run_id, state)             # survives a crash from here on
            if state.pending is not None:
                verdict = await ctx.verdict(state.pending)               # the platform decides
                if verdict.status == "pending":
                    await asyncio.sleep(ctx.params["poll_s"]); continue
                if verdict.status == "accept":
                    state.base = verdict.revision                        # next round builds on it
                state.pending = None
            state.next_round += 1
            await self.store.save(ctx.run_id, state)
        return RunSummary(rounds=state.next_round)
```

**Another team's optimizer in another language.** A TypeScript, GEPA-style
prompt optimizer registers with `runtime.kind: "job_worker"` and
`needs: {"evaluate.train@1": {}, "experience.feedback@1": {}}`. It claims
jobs and calls the Job Protocol. It never learns how revisions are stored,
how verification works, or how service bots are published. A candidate it
submits:

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

## 11. SDK and conformance

| Package | For | Contents |
| --- | --- | --- |
| `avernet-evolution` (Python), `@avernet/evolution` (TS) | Callers: pipelines, CI, UI backends | Generated client for the Evolution API (bindings, runs, verdicts) |
| `avernet-evolution-strategy` (Python first, TS second) | Strategy authors | `EvolutionStrategy` base, typed models, an in-process and a Job-Protocol `StrategyContext`, `WorkspaceFactory` (materialise / `to_patch`), `AgentRunner` (OpenClaw first), a local harness with a fake platform (`avn strategy dev`), `avn strategy publish` (registers a version and uploads its agent definitions, §4.2), and the conformance kit |

Conformance runs on both sides of the port:

- **Strategy kit** (run by authors, and by C3 before a version can be bound
  outside development): candidates validate against the patch schema and
  the run's `allowed_genes`; the strategy uses only granted capabilities;
  it stops on cancellation and on `BudgetExhausted`; resubmitting the same
  candidate is idempotent; killing the strategy mid-run and dispatching the
  same run id again neither duplicates candidates nor exceeds the budget.
- **Capability providers** (run by the platform and engine adapters): each
  catalog entry has a contract test per engine provider, following
  `docs/arch/protocol-contract-tests.md`.

## 12. Evidence that the port is general enough

| Strategy | Shape | Fits the port by |
| --- | --- | --- |
| ClawEvolve (`apps/evolverun`) | Multi-round tune → bench → review | Black box; `agents`, `experience.sessions`, `evaluate.train`; looks up verdicts by candidate id between rounds |
| `platform/consolidate-memory` ([07-default-strategy.md §5](07-default-strategy.md#5-a-second-non-clawevolve-default-memory-consolidation)) | Scheduled consolidation of observations into memory items | Black box; `experience.feedback` only; one submission per run |
| GEPA / OPRO-style optimizers | Population search with reflective mutation | Black box; `evaluate.train` for fitness; submits the best candidates |
| Coding-agent strategy (Meta-Harness style) | Agent edits files with full history | Black box; `workspace` + `agents` |
