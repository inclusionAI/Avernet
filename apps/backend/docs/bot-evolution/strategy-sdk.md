# Strategy SDK — making evolution pluggable

> Status: DRAFT. How another team plugs a new evolution approach into the
> platform without changing platform code.

## 1. Principles

1. **The platform owns the loop skeleton and retention; strategies own
   variation and contribute to selection.** A strategy can decide *what to
   try* and *what counts as better*; it can never decide *what goes live*
   on its own (see [governance.md](governance.md)).
2. **Small plugin kinds, composed by a manifest.** Instead of one giant
   "Strategy" interface, a strategy is a declared composition of narrow
   plugins. Teams can replace one piece (e.g. a better Evaluator) and reuse
   the rest (e.g. the default Analyzer).
3. **Language-agnostic by default.** Heavy plugins run out of process over a
   wire protocol, so the existing Python skills, the TS control plane, a
   container from another team, or a bot can all implement them.
4. **Contracts with conformance tests.** Every plugin kind has a JSON Schema
   input/output contract and a conformance kit (R25). The kit is shipped in
   the SDK so authors run it locally.
5. **Abstract after two examples (R19).** The kinds below are each backed by
   at least two existing or surveyed implementations (table in §3).

## 2. Plugin kinds

| Kind | Input | Output | Required? |
| --- | --- | --- | --- |
| **ExperienceSource** | engine + bot + window | normalized `Episode[]` | Per engine (platform-level, not per strategy) |
| **Trigger** | signals (schedule, metrics, events, inbox) | `RunRequest?` | Optional (manual always available) |
| **Selector** | archive view | parent revision(s) | Optional (default `latest-active`) |
| **Analyzer** | episodes, feedback, inbox items | `Findings` (`plan-source/v2`-compatible) | Optional |
| **SuiteBuilder** | findings | candidate eval cases (platform assigns splits) | Optional |
| **Proposer** | parent genome (read-only sandbox), findings, train-split failures with critiques, history | `GenomePatch[]` + rationale | **Required** |
| **Evaluator** | genome revision, case set, budget | per-case scores + critiques + traces | Optional (default: platform ClawBench evaluator) |
| **AcceptancePolicy** | parent vs candidate eval results | accept / reject / continue + reason | Optional (default: no-regression + improvement on validation) |
| **Curator** | whole genome + usage stats | `GenomePatch` (dedupe, retire, merge) | Optional; is a Proposer specialisation run on schedule |

What is deliberately **not** a plugin: recording revisions, static checks,
the platform floor of the gate, promotion, rollout, apply. These are
platform code (DR-2).

## 3. Evidence each kind is real (R19)

| Kind | Existing in repo | Surveyed |
| --- | --- | --- |
| ExperienceSource | ClawEvolve `acquisition/discovery.py`+`sessions.py` (OpenClaw JSONL); `service_export` (`session-export/v1`) | LangSmith traces, OpenAI trace grading |
| Analyzer | `clawevolve-diagnose`; workflow-run `SingleRunAnalyzer`/`BatchRunAnalyzer`; ClawInsight improvement adapter | ExpeL insight extraction, Trace2Skill |
| SuiteBuilder | `clawevolve-plan` (ClawBench templates) | SkillWeaver curriculum, Voyager curriculum |
| Proposer | `clawevolve-tune`(+review); TaskGuard repair strategy; suggestion_apply | GEPA reflective mutation, ACE curator, OPRO, Dreams, Hermes `skill_manage` |
| Evaluator | `clawbench-base/lib_grading.py` | DSPy metrics, Promptfoo, LangSmith evaluators |
| AcceptancePolicy | `action_accept` (test > baseline), env-tunable gates, `calibrate_evolution_gates.py` | GEPA Pareto, DGM archive admission |
| Selector | implicit latest | DGM, HGM clade-metaproductivity, MAP-Elites |
| Trigger | manual, failed-run observer, Insight monitoring | Hermes curator idle trigger, OpenClaw dreaming cron |

## 4. Strategy manifest

A strategy is a versioned YAML document registered in C3.

```yaml
strategy_schema: 1
id: clawevolve/bot-evolution
version: 2.0.0
description: Diagnose real sessions, tune persona and local skills, gate on ClawBench.
applies_to:
  engines: [openclaw, claude_code]           # checked against engine capabilities
  genes: [persona, skills]                   # max scope this strategy may patch
requires_capabilities: [session_export.v1, eval_env.sandbox]
flow:
  - step: analyze
    plugin: clawevolve/diagnose@1.4
    params: {window_days: 7, max_cases: 40}
  - step: build_suite
    plugin: clawevolve/plan@1.2
  - loop: {max_iterations: 3, until: policy.stop}
    steps:
      - step: propose
        plugin: clawevolve/tune-review@2.0
      - step: evaluate
        plugin: platform/clawbench@1          # platform-provided
        splits: [train, validation]
      - step: decide
        plugin: clawevolve/acceptance@1        # "validation > parent, paired win-rate ≥ 0.6"
defaults:
  selector: platform/latest-active@1
  budget: {max_usd: 20, max_wall_clock: 2h, max_rollouts: 400}
  models: {proposer: "${MODEL_STRONG}", judge: "${MODEL_JUDGE}"}   # resolved by config, no hardcoded endpoints
```

The flow vocabulary is deliberately small: `step`, `loop`, `parallel`
(population of proposers), `when` (conditional). It generalises ClawEvolve's
frozen `{key, version, stages}` and the `preprocess | postprocess | replace`
stage extension model: an extension is just a strategy that reuses another's
plugins and swaps one step.

## 5. Execution bindings

A plugin implementation declares one binding.

| Binding | When | How |
| --- | --- | --- |
| **In-process (Python)** | Cheap, deterministic plugins: Selector, AcceptancePolicy, Trigger | Python package implementing the `plugin_api` Protocol, registered via entry point; loaded by `apps/evolution` composition root per config (R5/R14) |
| **Job worker** | Heavy or non-Python plugins: Analyzer, Proposer, Evaluator, SuiteBuilder | Out-of-process worker speaks the **Job Protocol**; packaged as container image or as a skill bundle |
| **Runner bot** | The plugin *is* an agent (ClawEvolve tune is an OpenClaw agent) | A bot provisioned with the plugin's skill bundle + `avn` CLI; it claims jobs exactly like a worker, authenticated as a bot principal with `evolution:runner` scope |

### Job Protocol (wire)

Generalises ClawEvolve's `/internal/tasks/:id/steps/:sid/{claim,input,output,report}`:

```text
POST /evolution/v1/jobs:claim            {worker_id, plugin_ids[], capabilities} → job | 204
GET  /evolution/v1/jobs/{id}/input       → typed input (JSON Schema per plugin kind) + artifact URLs
POST /evolution/v1/jobs/{id}/heartbeat   {progress, note}         (lease extension; fencing token)
PUT  /evolution/v1/jobs/{id}/artifacts/{name}                     (content-addressed upload)
POST /evolution/v1/jobs/{id}/complete    typed output             (validated against schema)
POST /evolution/v1/jobs/{id}/fail        {reason, retryable}
```

- Leases with fencing tokens so a stuck worker cannot complete a job that was
  reassigned.
- Inputs are filtered by the orchestrator per kind contract (a Proposer
  input never contains holdout/regression cases).
- Workers get **no credentials to the target bot**; they receive a sandbox
  handle (eval bot or a materialised workspace) scoped to the job.

## 6. SDK packages

| Package | For | Contents |
| --- | --- | --- |
| `avernet-evolution` (Python), `@avernet/evolution` (TS) | Callers (pipelines, CI, UI backends) | Generated client from OpenAPI + ergonomic helpers (`runs.start`, `genomes.diff`, `candidates.review`) |
| `avernet-evolution-plugin` (Python first, TS second) | Strategy authors | Typed models for every contract; base classes per plugin kind; `JobWorker` loop; `GenomeWorkspace` helper that materialises a revision to a temp dir and diffs edits back into a `GenomePatch`; local harness (`avn strategy dev`) with in-memory orchestrator and fake engine; **conformance kit** (pytest plugin) |

The `GenomeWorkspace` helper is the important ergonomic piece: most existing
proposers (ClawEvolve tune, coding-agent proposers à la Meta-Harness/DGM)
want to *edit files in a folder*. The helper lets them do that against a
sandbox copy and converts the result into an itemized patch, so authors do
not hand-write patch ops.

```python
# Illustrative only
from avernet_evolution_plugin import Proposer, ProposeInput, GenomeWorkspace

class MyProposer(Proposer):
    def propose(self, inp: ProposeInput) -> list[GenomePatch]:
        with GenomeWorkspace.materialise(inp.parent) as ws:
            run_my_agent(ws.path, findings=inp.findings, failures=inp.train_failures)
            return [ws.to_patch(rationale="…", evidence=inp.findings.ids())]
```

## 7. Registration and trust

1. Author publishes plugin implementation (image digest or skill bundle
   digest) + manifest to C3 via API/CLI.
2. C3 runs the conformance kit in a sandbox; status `conformant` required to
   be referenced by a non-dev strategy.
3. Each implementation declares **isolation tier, allowed and disallowed
   capabilities** (R13): e.g. "network: model provider only", "no access to
   tenant data beyond job input".
4. Strategies are tenant-scoped by default; `platform/*` and
   `clawevolve/*` are platform-published defaults. Promoting a third-party
   strategy to platform-wide is a reviewed action.
5. A bot owner (or tenant admin) **enables** strategies per bot with limits
   (genes, budget, schedule, auto-promote ceiling). Enabling is a policy
   change, not a genome change.

## 8. Conformance shape

Following `docs/arch/protocol-contract-tests.md`: for each plugin kind,
exercise the **consumer** (orchestrator) with the local implementation,
assert the observable outcome, and assert the plugin was invoked. Plus a
kind-level kit for authors:

- Proposer: output validates; base matches; never touches locked genes;
  deterministic given seed when declared deterministic; respects budget
  signal; works without network when declared local.
- Evaluator: same revision + same cases + seed → scores within tolerance;
  returns critique per failed case; never mutates the revision.
- AcceptancePolicy: pure function of inputs; total (always returns a
  decision).
