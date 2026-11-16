# `agentclaw.community.core.evolution_strategy`

The strategy port of bot evolution — `03-strategy.zh-CN.md` turned into code.

```yaml
purpose: "The one plugin port a bot evolution strategy implements, plus the capability catalog, registration checks, conformance kit, and an in-process test platform."
provides:
  - EvolutionStrategy (run(ctx) -> RunSummary), StrategyContext and every capability part
  - Capability catalog (§2.3, §4): candidates@1, models@1, experience.sessions@1,
    experience.feedback@1, agents@1, evaluation.train@1, ledger.read@1
  - RunSummary, Candidate, Verdict, Operation, Budget, Message/Completion
  - StrategyRegistration, Runtime, AgentDefinition, RegisteredStrategy (§2.2)
  - ConformanceState/Status and run_conformance (§8.4)
  - StrategyRegistry protocol implementation in memory (§9)
  - Capability errors: CapabilityNotGranted, BudgetExhausted, RunCancelled,
    UnknownAgentDefinition, StaleAttempt, UnknownCapability, InvalidCapabilityArguments,
    NoProviderForEngine, AgentDefinitionInvalid, VersionExists, StrategyNotFound
  - Reference strategies: IdentityHardening, CorrectionFixer (§11)
  - An in-process test platform (StaticRevision, LocalWorkspace, build_context, run_once)
consumes:
  - agentclaw.community.core.bot_genome.patch_models   # GenomePatch, ops, text edits
  - agentclaw.community.core.bot_genome.diff           # unified_diff for to_patch()
  - agentclaw.community.core.bot_genome.canonical      # canonicalize + sha256_digest (candidate id)
internal_dependencies: []
```

## What this is, and what it is not

It **is** the boundary a strategy author codes against: one method, `run(ctx)`, and a context whose parts are the only way to reach the platform. A strategy never promotes, never reads holdout data, and never touches the live bot.

It **is not** `apps/evolution`. The run service (bindings, runs, jobs, leases, Job Protocol, budgets, storage — `06-evolution-run.zh-CN.md`) does not exist yet. Until it does, `build_context` gives a strategy an in-process platform good enough to develop and test against, and `run_conformance` lets an author self-check against §8.4 before registration.

## Using the port

```python
from agentclaw.community.core.evolution_strategy import Candidate, RunSummary
from agentclaw.community.core.evolution_strategy.protocols import StrategyContext


class MyStrategy:
    async def run(self, ctx: StrategyContext) -> RunSummary:
        episodes = await ctx.experience.sessions(days=7)          # CapabilityNotGranted if undeclared
        ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/r1")
        await ws.write("persona/SOUL.md", hardened)               # edits the copy only
        patch = await ws.to_patch(rationale="…", evidence=(f"episode:{episodes[0].episode_id}",))
        candidate_id = await ctx.candidates.submit(               # records; never promotes
            Candidate(patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence))
        )
        verdict = await ctx.candidates.verdict(candidate_id)      # pending → accept/reject/inconclusive
        return RunSummary(summary="submitted 1 candidate", metrics={"rounds": 1.0})
```

Rules the port enforces:

| Rule | Where |
| --- | --- |
| an undeclared capability refuses on first use | `CapabilityNotGranted` |
| a charge over the run budget stops the strategy | `BudgetExhausted` |
| cancellation is observed, not pushed | `ctx.cancelled.is_set()` |
| re-dispatch is visible, work stays resumable | `ctx.attempt`, idempotent `materialise`/`submit` |
| candidate id is the patch content hash | `candidate_id_of` |
| the strategy proposes; the platform decides | `ctx.candidates.submit` returns an id only |

## Registering a strategy version

`StrategyRegistration` is the author-written record (the `strategy.json` of a strategy project):

```python
from agentclaw.community.core.evolution_strategy import (
    Engine, Runtime, RuntimeKind, StrategyRegistration,
)

record = StrategyRegistration(
    id="clawevolve/bot-evolution",
    version="2.0.0",
    runtime=Runtime(RuntimeKind.JOB_WORKER,
                    image="registry.example/clawevolve@sha256:9b1f…"),
    needs={
        "experience.sessions@1": {},
        "agents@1": {"definitions": {
            "clawevolve-tune": {"engine": Engine.OPENCLAW, "path": "agents/clawevolve-tune"}
        }},
        "evaluation.train@1": {},
    },
    params_schema={"type": "object", "properties": {"window_days": {"minimum": 1, "maximum": 90}}},
)
```

`validate_registration` runs the pre-upload checks of §3.2 (unknown capabilities, bad
arguments, missing definition upload, engine/provider mismatch). `check_engine` is the
registration half of a binding check (§3.3); `check_params` validates a binding's
`params` against `params_schema`; `check_allowed_genes` enforces `mutable_genes`/`pins`.

The in-memory registry (`InMemoryStrategyRegistry`) implements the §9 protocol:
`register`/`get`/`list`/`check_engine`/`agent_definition`/`set_enabled`. Registration stores
each agent definition by content digest, derives the engines, and—when a strategy factory
and parent revision are given—runs `run_conformance` before the version may be used.

## Testing a strategy locally

```python
from agentclaw.community.core.evolution_strategy import build_context, run_once
from agentclaw.community.core.evolution_strategy.local import StaticRevision

parent = StaticRevision(id="sha256:" + "a" * 64, seq="r1",
                        files={"persona/SOUL.md": "## Core Truths\n- keep it small\n"})
ctx = build_context(parent=parent, granted=["experience.sessions@1", "candidates@1"])
summary = run_once(MyStrategy(), ctx)
```

`build_context` hands back a `LocalContext`; its `workspace`, `candidates`, `budget`,
`log`, `artifacts`, `cancelled` and `operations` all work in memory. Supply `models=`,
`experience=`, `agents=` or `evaluation=` to inject a provider; otherwise those raise
`CapabilityNotGranted` (undeclared) or `NotImplementedError` (granted but no local
provider — the conformance kit reports those checks as skipped).

## One-conformance-kit self-check (§8.4)

```python
from agentclaw.community.core.evolution_strategy import run_conformance
from agentclaw.community.core.evolution_strategy.local import StaticRevision

parent = StaticRevision(id="sha256:" + "a" * 64, seq="r1", files={"persona/SOUL.md": "x\n"})
report = run_conformance(lambda: MyStrategy(), registration,
                         parent=parent, mutable_genes=["persona/"])
# report.status → pending / passed / failed
```

Checks: candidate validity against `allowed_genes`, granted-capabilities-only use,
stopping on cancellation and budget exhaustion, idempotent resubmission, and stable
candidates across re-dispatch. A check needing a provider this local platform lacks is
skipped; the overall status is `pending` until every check can run.

## Reference strategies

| Strategy | What it does | Doc |
|---|---|---|
| `IdentityHardening` | Reads recent sessions, appends uncovered core truths to an identity file, submits a candidate | New in this package |
| `CorrectionFixer` | Diagnoses corrected episodes with one model call, adds train cases, runs its own agent to fix files, submits only when the train score improves | §11.1 |

## Current gaps against 03-strategy.zh-CN.md

| Doc | Status |
| --- | --- |
| §2.1 `RunSummary` / `EvolutionStrategy` | done |
| §2.2 `StrategyRegistration`, `Runtime`, `AgentDefinition`, `RegisteredStrategy` | done |
| §2.3 capability catalog + `@1` versioning | done (closed catalog) |
| §2.4 `AgentDefinition` ingestion and engine validation | done |
| §2.5 `StrategyContext` | done (field-for-field) |
| §2.6–2.8 `Candidate` / `Verdict` / `Operation` | done |
| §4.3 error set | done |
| §8.4 conformance kit | done (local; provider contract tests belong to the platform) |
| §9 `StrategyRegistry` protocol + in-memory reference | done |
| §10 public API (`POST /evolution/strategies`, …) | **missing** — HTTP layer of `apps/evolution` |
| `06-evolution-run` run service, job leases, budget accounting | **missing** (`apps/evolution`) |

A strategy written against this package therefore does not need rewriting when
`apps/evolution` lands: it gets a real `StrategyContext` instead of the local one.
