# Research — industry survey and codebase evidence

> 中文版：[research.zh-CN.md](research.zh-CN.md)

> Collected 2026-10-08. Industry items marked **[V]** were verified against a
> fetched page or search result in this session; **[K]** are well-known papers
> cited by standard arXiv id without re-fetching; **[U]** could not be
> verified and must not be relied on without checking.

## 1. Industry survey

### 1.1 External optimizers (evolve prompts / programs offline)

| System | Mechanism | Lesson for us |
| --- | --- | --- |
| DSPy MIPROv2 [K] — arxiv.org/abs/2406.11695 | Bayesian search over instructions × demos against a metric | Declare tunable text as parameters with a metric; optimizer is swappable |
| GEPA [V] — arxiv.org/abs/2507.19457, dspy.ai/api/optimizers/GEPA | Reflective mutation from traces + *textual* feedback; Pareto front over per-instance scores; reported to beat GRPO with far fewer rollouts | Best fit for evolving SKILL.md / persona text; graders must return score **and** critique; keep a Pareto archive |
| TextGrad [K] — arxiv.org/abs/2406.07496 | Textual "gradients" through a graph of text variables | Credit assignment across persona → skill → tool description |
| OPRO [K] — arxiv.org/abs/2309.03409 | LLM as optimizer over sorted (prompt, score) history | Simple baseline Proposer |
| Promptbreeder [K] — arxiv.org/abs/2309.16797 | Population evolution; mutation prompts also evolve | The proposer itself can evolve (meta level) — later |
| Trace / OptoPrime [K] — arxiv.org/abs/2406.16218 | Execution trace graph; update any trainable node | Trace is the unit of feedback |
| Meta-Harness [V] — arxiv.org/abs/2603.28052 | Coding agent searches harness code with full raw history on a filesystem | Give proposers lineage + raw traces, not summaries |
| StarHarness [V, abstract] — arxiv.org/abs/2608.24804 | Stratified search over prompts, tools, skills, MCP, subagents | Its search space ≈ our genome |
| Rethinking harness-evolution evaluation [V] — arxiv.org/abs/2607.12227 | Automatic harness evolution often does not beat budget-matched test-time scaling; poor held-out generalisation | **Mandatory**: held-out evaluation and budget-matched baselines |

### 1.2 Agent self-evolution

| System | Lesson |
| --- | --- |
| Reflexion [K] 2303.11366 | Verbal self-reflection after failure → episodic memory (fast loop) |
| ExpeL [K] 2308.10144 | Insight list with ADD/EDIT/UPVOTE/DOWNVOTE — itemized memory with utility |
| Agent Workflow Memory [K] 2409.07429 | Induce reusable workflows from trajectories (≈ skill drafts) |
| Dynamic Cheatsheet [K] 2504.07952 | Test-time curated memory |
| ACE [V] 2510.04618 | Generator / Reflector / Curator; **itemized delta updates** avoid brevity bias and context collapse |
| Voyager [K] 2305.16291 | Skill library admits only verified skills |
| SkillWeaver [V] 2504.07079 | Propose → synthesize → hone skills; skills transfer strong→weak bots |
| Trace2Skill [V, abstract] 2603.25158 | Trajectory lessons → SKILL.md via parallel patches merged |
| SkillAxe [V, abstract] 2606.10546 | LLM-authored skills ≈ no gain; eval-guided refinement recovers gains |
| SKILL.md mining for CUAs [V, abstract] 2606.20363 | Mined skills readable but weak — gating matters more than extraction |
| ADAS [K] 2408.08435 | Meta-agent + archive of designs |
| Gödel Agent [K] 2410.04444 | Self-referential runtime modification — fragile |
| SICA [V] 2504.15228 | Agent edits its own code; archive; best becomes meta-agent |
| Darwin Gödel Machine [V] 2505.22954 | Archive tree + novelty-biased parent selection; without archive gains collapse; **observed reward hacking** |
| Huxley-Gödel Machine [V] 2510.21614 | Score ≠ descendants' improvability; select by clade metaproductivity → Selector must be pluggable and lineage-aware |
| AlphaEvolve [K] deepmind blog / 2506.13131 | LLM diffs + automated evaluators + MAP-Elites/island database |
| Self-evolving agents survey [V] 2508.07407 | Framework: inputs, agent system, environment, optimisers |

### 1.3 Product practice

| Product | Pattern | Lesson |
| --- | --- | --- |
| Anthropic Agent Skills [V] — anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills | Progressive disclosure; open SKILL.md format; skill-creator with evals | Our skills are already this format; descriptions matter for triggering |
| Anthropic memory tool [V] — platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool | Client-side file memory; path-traversal protection, size caps | Validate memory paths; itemize |
| Claude Managed Agents memory + Dreams [V] — platform.claude.com/docs/en/managed-agents/{memory,dreams} | Every memory change is an immutable version; Dreams writes a **new** store from sessions, input untouched, adopt or discard | Reference for "propose new version, gate, promote" |
| Claude Code memory [V via 3rd party; details U] | Human-authored CLAUDE.md + auto memory index | Split curated vs runtime memory |
| OpenAI trace grading, prompt optimizer, self-evolving cookbook [V via search] | Trace graders, dataset-based optimizer, baseline → graders → optimizer → redeploy | Optimized prompts can regress on specific inputs — regression suites. **OpenAI Evals platform shuts down 2026-11-30** — do not depend on it |
| LangMem / LangSmith [V] | Procedural memory = prompt optimization from trajectories + feedback; datasets/evaluators/annotation queues | Multi-prompt optimizer chooses *which* component to change |
| Letta sleep-time agents [V] | Acting agent has no memory-edit tools; background agent edits memory | Separate acting from learning (our two-speed loop) |
| Mem0 [V via secondary] | LLM decides ADD/UPDATE/DELETE/NOOP | All gating by LLM is a weakness |
| Hermes Agent (Nous) [V via doc snippets; triggers U] | Agent self-authors skills; Curator archives, never deletes; pinned skills write-protected; dry-run | Pinning, archive-not-delete, dry-run — adopted |
| OpenClaw Dreaming [V] — docs.openclaw.ai/concepts/dreaming | Opt-in nightly light/REM/deep phases; promotes only into MEMORY.md with weighted scoring | Off by default, scored promotion, human-readable diary is not a promotion source |

### 1.4 Interface patterns

| Pattern | Examples | Our use |
| --- | --- | --- |
| A. External optimizer API/SDK | DSPy, TextGrad, LangMem, OpenAI optimizer, AlphaEvolve | Strategy runs (slow loop) |
| B. Agent-callable self-edit tools | Memory tool, Claude Code auto memory, Hermes `skill_manage`, Voyager | Subject-bot CLI — **writes only to inbox** |
| C. Background consolidator | Letta sleep-time, Dreams, OpenClaw Dreaming, Hermes Curator, ACE Curator | `platform/consolidate-memory` strategy |
| D. Self-referential code modification | SICA, DGM, Gödel Agent | Not in scope for bots; possible later for strategies evolving strategies |

Consensus: B writes to a staging layer, A/C propose versions, a
platform-owned gate promotes. That is this design.

### 1.5 Unverified / flagged

Hermes skill-creation trigger counts; whether the Hermes curator touches
bundled skills; Harvey "~6x" Dreams figure; Claude Code "AutoDream"; auto-memory
version and size limits; OpenAI cookbook ↔ GEPA linkage; ShinkaEvolve; LangMem
version. 2026 papers above are known from abstracts/snippets only.

## 2. Codebase evidence

### 2.1 Bot Config Manifest

Docs: `apps/backend/docs/bot-config-manifest/` (zh-CN; `manifest-schema.zh-CN.md`
overrides `design.zh-CN.md`). Code: `apps/backend/src/agentclaw/community/core/bot_config_manifest/`.

- Top-level keys `schema_version` (only 1), `sources`, `manifest`, `script`;
  unknown keys rejected (`schema/validator.py:58`).
- Categories: `mcp`, `resources`, `skills`, `identity`, `engine_config`
  (rejected at PUT in v1), `cli_tools` (`capabilities.py:77-89`).
- `MEMORY.md`, `IDENTITY.md` reserved: rejected, never written or deleted.
- Storage: one mutable row per bot, `ac_bot_config_manifest`
  (`repository/models.py:64`); no revision, hash, parent, or ETag
  (`services/config_manifest_service.py:141-184`).
- Apply reports append-only (`repository/apply_models.py:90`) but apply
  re-reads the current document; reports record resolved git SHAs, not the
  document applied (`services/config_manifest_apply_service.py:846-875`).
- Content-addressed blob provenance: `content/models.py:94` (`ac_manifest_content`).
- Convergence: per-category replace, category-atomic; apply order and
  triggers in `apply/order.py`, `apply/triggers.py`; PUT applies (differs
  from design doc).
- teclaw delivery: whole `BotConfigArtifact` snapshot
  (`kernel/bot_config/artifact.py`), engine contract A1–A5 in
  `engine-convergence-contract.zh-CN.md`; `ownership` flag gated by
  `teclaw_platform_managed`.
- Service-bot publish/rollback: versioned publish records with frozen
  artifact; rollback one step back only
  (`core/service_bot/services/publish_rollback_mixin.py:38-80`).
- ADR 0018: Manifest apply is a one-shot command, not a controller; Skills and
  MCP keep independent failure boundaries.

### 2.2 Existing self-improvement pipelines

All under `apps/evolverun/`; nothing equivalent elsewhere in the repo.

- **ClawEvolve control plane** (TS): `clawweb/public/modules/clawevolve/server/`
  — `services/evolve/evolution-flow.ts` (three closed flow keys),
  `stage-catalog.ts` + `resources/evolve/official-stage-catalog.json`
  (stage JSON Schemas; `preprocess|postprocess|replace`),
  `task-registry.ts`, `routes/evolve.ts`, `routes/internal/evolve.ts`
  (claim/report step protocol), `services/evolve/skill-application.ts` +
  `contracts/bot-skill-gateway.ts` (human-approved skill replace with CAS),
  `create-module.ts:60-200` (host DI options).
- **Stage skills** (Python, stdlib): `clawweb-skills/clawevolve-skills/` —
  diagnose (`acquisition/discovery.py:41-96`, `sessions.py`,
  `service_export.py`, `integration/plan_source.py:25-97` with 80/20 split),
  plan, tune, review, bench (`clawbench-base/scripts/lib_grading.py:51-108`),
  pack, deploy. Optimize loop in
  `clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py`
  (sequence `:9491-9513`; accept rule `:6256-6378` = test score strictly
  greater than baseline; env-tunable gates `:1890-1905`; hard-coded workspace
  `:221`; tune via `openclaw agent --local` `:7938`).
- **Tune edits the live workspace directly**; rejected rounds restore a
  pre-round pack.
- **plan-source/v2** (`clawinsight/server/services/evolve/plan-source-contract.ts:6-40`)
  is the producer-agnostic findings handoff.
- **Workflow-run healing**: run evidence ingest, analysis runs, suggestions,
  lessons; analyzer handler ("ClawMind") not in repo; batch analyzers and
  lesson expiry only instantiated in tests.
- **ClawInsight**: improvement items → plan-source → plan+optimize; admin
  review; rule-trust evolution after 3 verified successes.
- **TaskGuard**: in-run guardian/repair/retry; no persistent learning.
- **Evolvetrace**: observability; evolution tab mocked
  (`src/components/workflow-workspace/evolution-mock.ts`).

### 2.3 Existing bot-quality evaluation code

Full inventory, reuse plan, and gaps in
[verification.md §2](verification.md#2-what-already-exists-in-the-codebase).
Summary: ClawBench (case format, automated / rubric / hybrid graders) plus
the ClawWeb Bench store is the only working bot grader, and it is OpenClaw-local.
The backend eval env + Quality Task deploys isolated service-bot copies but
delegates grading to an external service. The service-bot VERIFY stage runs
no automated checks. ClawEvolve's regression, replication, and calibration
gates exist but are advisory or unreachable from the canonical round.

### 2.4 Architecture constraints that shape this design

- `docs/arch/arch.rules.md`: R1 contracts; R3 Service vs Plugin APIs; R5/R14
  composition-root selection by config; R7 transport-agnostic core; R11
  plugin lifecycle declarations; R12 auth via hooks; **R13 isolation tier
  and capabilities per plugin type**; R16 propagation analysis; R19 abstract
  after two examples; R20 single-box first, local mode testable offline;
  R25 conformance tests.
- Backend plugin pattern: `plugin_api/*` Protocols, `plugins/local` +
  `plugins/community`, `@plugin_impl` registry; composition root
  `di/container.py`, `di/profile_modules.py`; conformance suites in
  `apps/backend/tests/community/contracts/`. Relevant existing seam:
  `plugin_api/eval_env/` (EvalEnvLifecycle, VersionSync, …).
- OpenAPI v1 refuses `bot` principals (deliberate removal of bot→owner
  fallback) — hence DR-3.
- `bcs-cli` is the precedent for a bot-facing CLI with a SKILL.md and
  session-file auth.
- Engine owns physical layout (ADR 0014, 0017); Backend must not add engine
  paths.
