# `avernet_evolution.core.evolution_strategy`

The strategy port of bot evolution — `03-strategy.zh-CN.md` turned into code.

## Context Boundary

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
  - avernet_genome.patch_models   # GenomePatch, ops, text edits
  - avernet_genome.gene_paths  # canonical targets, matching, and Skill member path decoding
  - avernet_genome.text_diff   # unified_diff for to_patch()
  - avernet_genome.canonical      # canonicalize + sha256_digest (candidate id)
internal_dependencies: []
```

## What this is, and what it is not

It **is** the boundary a strategy author codes against: one method, `run(ctx)`, and a context whose parts are the only way to reach the platform. A strategy never promotes, never reads holdout data, and never touches the live bot.

It is part of `apps/evolution`, not a separate control plane. The [run service](../evolution_run/README.md) owns bindings, runs, jobs, leases, Job Protocol, budgets and storage. `build_context` remains an in-process test platform, and `run_conformance` lets an author self-check against §8.4 before registration; neither proves production providers are wired.

## Using the port

```python
from avernet_evolution.core.evolution_strategy import Candidate, RunSummary
from avernet_evolution.core.evolution_strategy.protocols import StrategyContext


class MyStrategy:
    async def run(self, ctx: StrategyContext) -> RunSummary:
        episodes = await ctx.experience.sessions(days=7)  # CapabilityNotGranted if undeclared
        ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/r1")
        await ws.write("persona/SOUL.md", hardened)  # edits the copy only
        patch = await ws.to_patch(rationale="…", evidence=(f"episode:{episodes[0].episode_id}",))
        candidate_id = await ctx.candidates.submit(  # records; never promotes
            Candidate(patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence))
        )
        verdict = await ctx.candidates.verdict(candidate_id)  # pending → accept/reject/inconclusive
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
from avernet_evolution.core.evolution_strategy import (
    Engine,
    Runtime,
    RuntimeKind,
    StrategyRegistration,
)

record = StrategyRegistration(
    id="clawevolve/bot-evolution",
    version="2.0.0",
    runtime=Runtime(RuntimeKind.JOB_WORKER, image="registry.example/clawevolve@sha256:9b1f…"),
    needs={
        "experience.sessions@1": {},
        "agents@1": {
            "definitions": {"clawevolve-tune": {"engine": Engine.OPENCLAW, "path": "agents/clawevolve-tune"}}
        },
        "evaluation.train@1": {},
    },
    params_schema={"type": "object", "properties": {"window_days": {"minimum": 1, "maximum": 90}}},
)
```

`validate_registration` runs the pre-upload checks of §3.2 (unknown capabilities, bad
arguments, missing definition upload, engine/provider mismatch). `check_engine` is the
registration half of a binding check (§3.3); `check_params` validates a binding's
`params` against `params_schema`; `check_allowed_genes` enforces `mutable_genes`/`pins`.
The latter uses Genome's canonical `op_gene_path` for every operation (including
resource operations and `memory.set_mode`). Allowed paths must cover the target;
pins reject overlap in either direction, so removing a parent cannot bypass a
pinned descendant. Registry still enforces the authoritative policy at commit.

For local Skill text edits, the workspace uses canonical paths such as
`skills/quality-check/notes/how%20to.md`. `to_patch()` uses the corresponding
package-relative path `notes/how to.md` in the `skill.update` file diff. The
operation targets the package. Legacy path-only Bindings therefore grant
`skills/quality-check`, not `skills/quality-check/SKILL.md`. Scoped Runs instead
validate every changed member and can retain a member-only policy grant such as
`skills/quality-check/SKILL.md`. Changes are made against
a Skill already present in the parent snapshot; materialising the workspace does
not install or activate it. Include evidence when submitting a Skill patch.

The in-memory registry (`InMemoryStrategyRegistry`) implements the §9 protocol:
`register`/`get`/`list`/`check_engine`/`agent_definition`/`set_enabled`. Registration stores
each agent definition by content digest, derives the engines, and—when a strategy factory
and parent revision are given—runs `run_conformance` before the version may be used.

## Testing a strategy locally

```python
from avernet_evolution.core.evolution_strategy import build_context, run_once
from avernet_evolution.core.evolution_strategy.local import StaticRevision

parent = StaticRevision(
    id="sha256:" + "a" * 64, seq="r1", files={"persona/SOUL.md": "## Core Truths\n- keep it small\n"}
)
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
from avernet_evolution.core.evolution_strategy import run_conformance
from avernet_evolution.core.evolution_strategy.local import StaticRevision

parent = StaticRevision(id="sha256:" + "a" * 64, seq="r1", files={"persona/SOUL.md": "x\n"})
report = run_conformance(lambda: MyStrategy(), registration, parent=parent, mutable_genes=["persona/"])
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
| `BehaviorRefinement` | One bounded model proposal over existing authorized Markdown, with recent session evidence; Persona and Local Skill targets come from frozen scope | Opt-in, new version only |
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
| `06-evolution-run` run service, job leases, budget accounting | implemented in sibling `evolution_run`; see app guide for deployment gaps |

The independent runtime supplies a real `StrategyContext`; `build_context` remains a test fixture, not production readiness proof.

## Opt-in generic refinement

`strategies.BehaviorRefinement` uses `ctx.mutation_scope` and `ctx.parent.paths`.
It reads only authorized existing `.md` files, requests at most 20 recent sessions
and one model completion, and submits at most one patch. No evidence, no changes
or cancellation produces no candidate. Oversized context, malformed JSON,
duplicate or unauthorized paths refuse; scripts, new files and lifecycle changes
are never proposed. Recent sessions are evidence, **not attested executions of
the parent revision**. The prompt preserves Skill identity/front matter and
unrelated constraints; Registry still checks resulting package metadata.

Register a **new** immutable version in the deployment Platform (do not replace
an old registration/image or rewrite its Bindings):

```python
from avernet_evolution.core.evolution_strategy import registration_from_json

record = registration_from_json(
    {
        "id": "reference/behavior-refinement",
        "version": "1.0.0",
        "runtime": {"kind": "job_worker", "image": digest_pinned_worker_image},
        "needs": {"experience.sessions@1": {}, "models@1": {}},
        "mutation_capability": [
            {"gene": "persona", "include": "all", "operations": ["update"]},
            {"gene": "skills", "origin": "local", "include": "all", "operations": ["update"]},
        ],
    }
)
await platform.registry.register(record, {})
```

`digest_pinned_worker_image` is the actual built image `…@sha256:<digest>`,
not a mutable tag. Bake this mapping into that image:

```text
EVOLUTION_WORKER_STRATEGIES=reference/behavior-refinement@1.0.0=avernet_evolution.core.evolution_strategy.strategies:BehaviorRefinement
```

The three per-job environment variables remain unchanged; this mapping is image
configuration, not a new runtime secret or sandbox injection. The deployment must
also supply `Platform.models` (wired to both in-process and isolated Job calls),
session export and independent
[composed verification](../evolution_verify/README.md#gene-method-composition).
Use a new manual Binding with `auto_promote_ceiling: null` and an explicit profile.
Changing only its scope chooses Persona-only, Local Skill-only or both without
changing strategy code. Nothing registers this strategy by default or enables
Apply. Local fixtures and conformance do not establish real behavioral quality.

### Final proposal observability

The observable `BehaviorRefinement` implementation writes a display-only artifact
`behavior-refinement-output-attempt-{attempt}.json` using `ctx.artifacts` before
candidate submission. Deploy it as a **new immutable strategy version** (for example
`1.0.3` after `1.0.2`) with a new pinned Worker image digest. Do not replace old
version images, rewrite existing Bindings or rerun historical Runs automatically.

Schema `1` (`output.py`) contains `attempt`, `outcome`, `reason`, `rationale`,
proposed `edits` (`path`, full replacement `content`, `changed`), final `raw_output`
and `raw_output_truncated`. Raw final text is bounded to 64,000 characters;
structured edits retain the proposal limits (20 files, 24,000 characters each).
It does not separately record prompts, hidden reasoning or input session payloads;
final model text may still echo sensitive input and must be treated accordingly. Recognizable
credentials and contact details are conservatively redacted from display copies;
this is not complete DLP. Candidate content is not rewritten by this redaction.

Outcomes distinguish `not_called` (no authorized Markdown / session evidence),
`no_changes` (`empty_edits` / `identical_content`), `changes_proposed`,
`invalid_proposal`, and `cancelled`. Invalid model responses are saved before a
safe validation failure. Artifact persistence failures propagate and prevent a
successful return. `changes_proposed` is **not** a submission/verification verdict:
workspace validation, cancellation or candidate submission can still fail. Read
the Run summary/status and candidate list for the actual outcome. Model-call or
input failures before a final response may have no output artifact.
