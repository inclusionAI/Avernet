---
name: clawevolve-tune
description: "自进化调优 skill。用于 self-evolution loop 中，根据 objective、当前策略 spec 和 optimization bench 结果，对当前 bot/workspace 做一轮小而可解释的调优，并产出 tune_report、changed_files 和 diff。触发词：自进化调优、evolve tune、self tune、根据优化集调优、bench 后优化、round tune。"
allowed-tools: Read, Bash, Edit, MultiEdit, Write
---

# clawevolve-tune

在自进化的一轮中，根据 `objective + spec-vN + optimization_result` 调优当前 workspace。MVP 阶段保持简单：核心就是先读 objective 的业务硬约束，再读共享 spec 模板里的 `Objective Contract`、`Current Strategy Summary` 和 `Active Optimization Directions`，做小而可解释的优化。objective 负责长期业务规则，spec 负责本轮怎么优化。

## 运行环境的 Skill 路径

由调用方提供当前模式：`openversion` 使用 `workspace/skills/<skill-name>/SKILL.md`，无需中间层或激活软链；未指定或 `internalversion` 保持 `workspace/skills/skills-local/<skill-name>/SKILL.md` 及原激活入口。公共目录、现有公共入口软链及 Release Skill 始终只读。不要改造或搬迁历史目录。

## MVP 路径协议

为避免同 bot 多个 evolve run 并发时串目录，运行期必须绑定明确的 `evolve_run_id` / `run_dir`。优先级：

```text
1. 调用方显式提供 run_dir / round_dir
2. 环境变量 EVOLVE_RUN_DIR / EVOLVE_ROUND_DIR
3. run_manifest.json 中记录的 run_dir / round_id
4. 单机调试时才使用人工指定的 fallback
```

标准目录结构（与 clawevolve.yaml workflow 对齐）：

```text
workspace = /home/admin/.openclaw/workspace
run_dir   = {workspace}/evolve_results/<evolve-run-id>
round_dir = {workspace}/evolve_results/<evolve-run-id>/optimize/output/round-{N:03d}
```

其中 objective 是一次 evolve loop 不变的 run 级输入，放在 `optimize/input/`：

```text
{run_dir}/optimize/input/objective.md
{run_dir}/optimize/input/spec-v0.md
{run_dir}/run_manifest.json
```

Round 是 1-based（round 1 → round-001、round 2 → round-002）。
Spec 文件名按 round 数字版本化：round N 消费 `input/spec-v{N-1}.md`（如 round 1 消费 `spec-v0.md`，round 2 消费 `spec-v1.md`）；`clawevolve-review` 在本轮产出 `spec/spec-vN.md`（如 round 1 产出 `spec-v1.md`）。不要使用未版本化的 spec 别名。

当前轮 spec 和 round 产物放在：

```text
{round_dir}/input/spec-v{N-1}.md
{round_dir}/tune/
{round_dir}/spec/
```

**注意**：runner v4 不再创建 `{round_dir}/bench/` 目录。bench 元数据和 score 直接写入 `round_state.json` 的 `bench.optimization` 字段，原始 benchmark report 在 `clawbench_results/{bench_run_id}/` 目录下。

统一使用 `/home/admin/.openclaw/workspace/clawevolve_results/<task-id>` 绑定当前 run；不要创建未绑定
Task 的全局 `round0`，也不要通过扫描结果根目录中的最新目录来猜当前 run。

## Bundled Resources

- `references/protocol.md`: `clawevolve-tune` 自己的输入/输出和边界协议；需要确认路径契约时读取。
- `../clawevolve-plan/references/evolution-strategy-spec-v0-template.md`: 与 `clawevolve-plan`、`clawevolve-review` 共享的统一 spec 模板；需要确认 `Objective Contract` / `Current Strategy Summary` / `Active Optimization Directions` / 附录结构时读取。

**注意**：`scripts/normalize_clawbench_result.py` 已废弃。runner v4 将 bench summary inline 到 `round_state.json`，不再产出独立的 normalized JSON 文件。

说明：`clawevolve-tune` 不内置 direction pool；它只执行 `spec-v{N-1}.md` 中已经激活的方向。若 spec-v{N-1} 只有 direction ID 而没有 Why/Expected Effect，应先要求补全 spec，而不是跨 skill 读取 `clawevolve-review` 的方向池。

## 输入

优先从已绑定的 `run_dir` / `round_dir` 读取：

```text
{run_dir}/optimize/input/objective.md
{round_dir}/input/spec-v{N-1}.md
{round_dir}/round_state.json    ← bench 元数据 + summary 在 bench.optimization 字段
```

原始 benchmark report 路径记录在 `round_state.json` 的 `bench.optimization.resultPath`，如需逐任务详情（cases、breakdown、transcript）可读取该文件。

如果调用方（runner）通过 prompt 直接内嵌了 bench summary 文本，直接消费即可，不需要再读文件。

若 bench 数据缺失，写明原因并写 blocked `tune_report.md`，不要使用硬编码 fallback 路径。

## 输出

写入已绑定的 `round_dir`：

```text
{round_dir}/tune/tune_report.md
{round_dir}/tune/changed_files.txt
{round_dir}/tune/diff.patch        # 如 git/diff 可用则生成
{round_dir}/tune/change_manifest.json  # 本轮实际 edit 的可证伪实验契约
```

## 工作步骤

1. 定位 `round_dir` 和输入路径（优先使用调用方显式传入的路径）。
2. 读取 objective、`spec-v{N-1}`、`round_state.json` 中的 `bench.optimization`。
3. **强制提取失败证据包**：只要存在 failed/error/partial case，必须读取 `bench.optimization.resultPath` 指向的原始 report；如 report 指向 transcript，优先读取相关 failed/error case 的 transcript。不要只凭 summary 直接改。
4. **先核对 objective 约束**：先确认 objective 里的业务目标、硬约束和成功标准，再看 spec；如果 spec 与 objective 冲突，以 objective 为准。
5. **提取正例保护清单**：只要存在 passed case，必须从 optimization bench 中抽取代表性 passing behavior，形成 Protected Behaviors；后续 patch 不得无说明地破坏这些行为。
6. **执行 Weakness Mining**：把失败按 `signature = judge 终因 + agent 侧关键行为 + 抽象机制` 聚合，区分表面症状相同但机制不同的失败，并标注当前修改面是否 addressable。
7. **生成 Candidate Proposals**：围绕最高优 weakness 至少生成 3 个 operator-diverse 候选（优先覆盖 instruction/workflow/verifier 等不同机制），比较证据、替代解释、修改范围、保护行为风险和可证伪性；恰好选择 1 个 proposal 执行。
8. **先写 Patch Plan Gate，再修改**：为每个被选 proposal 列出 evidence、root cause、target file、exact change、check、expected_fix_cases、at_risk_cases、falsifiable_prediction 和 rollback_condition；只有满足范围、证据、正例保护和反过拟合检查的改动才执行。
9. 修改前先盘点 `workspace/skills/` 已激活的 Skill；修改当前 workspace 中的私有 skill、md 配置、MCP 调用层说明或必要的轻量 glue/config。Skill 修改仅允许位于当前运行模式指定的用户 Skill 可写目录。若同名 Skill 已通过公共/系统入口存在（例如系统已有 `mcporter`），不得在用户 Skill 目录创建同名副本或替换其入口；公共 Skill 及其来源目录、入口软链只允许读取分析，不得修改。不要修改 MCP 本体实现。
10. 对每个执行策略绑定至少一个低成本、可观察检查；能运行则运行，不能运行必须说明原因。
11. 写 `tune_report.md`、`changed_files.txt`、`change_manifest.json`，并尽量生成 `diff.patch`。

## Failure Profile Priority

Runner 会注入 `optimization_failure_profile`。先使用该摘要寻找跨任务重复机制，再回到 raw optimization report 与 transcript 核验：

1. 按“受影响任务数 × 评分权重 × 可修复性”排序，不要默认追逐单个 task 的最低分。
2. 分开诊断 `semantic_accuracy` 与 `delivery_reliability`：前者优先检查判别边界、遗漏的语义对照轴、独立问题被错误合并；后者优先检查中间叙述过长、最终输出截断、Stage/confirm 未完成、重复 validator/repair。
3. 若 automated expert/keyword 项通过而 LLM judge 准确度低，视为 scorer conflict：说明模型提到了词但没有在最终交付中准确形成风险结论。禁止继续优化关键词出现率，应修复“证据 → 精确分歧 → 独立风险项 → 可执行建议”的传递链。
4. Judge notes 中重复出现的遗漏机制优先于泛化 checklist；只有 raw evidence 支持时才能落 patch。
5. 若 `paired_classification_failures > 0`，把两个互斥/互补分类字段视为一个联合契约：同时检查两侧 prompt、联合一致性复核、断点恢复字段别名和最终输出映射。禁止只修一个字段，或把语义失败误路由为工具/等待/格式问题。
6. 若 `optimization_underexposed=true` 且 aggregate generalization gap 明显，optimization 满分不代表无弱点。不得仅因 optimization 无失败就 no-op；可以在不读取 validation 明细的前提下，对运行时代码中的脆弱 literal 规则、双轴不对称、stale alias 和缺失通用 invariant 做一次最小、可证伪的 robustness audit。修改必须由源码证据支持，不能猜 held-out 答案。

## Failure Evidence Extraction

对每个 failed/error/partial case，至少抽取并在 `tune_report.md` 中保留摘要：

| case_id | score | failure_signal | judge_notes/breakdown | tool_path/transcript_signal | suspected_root_cause |
|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... |

提取规则：

- 优先读取 raw benchmark report 中的 `grading.runs[].notes`、`breakdown`、task status、timed_out、frontmatter/name。
- 如存在 transcript，读取失败 case 的 transcript，归纳用户输入、关键 tool/MCP 调用、最终回答和出错点。
- 证据只允许来自 optimization bench；不得读取或使用 validation 结果。
- 证据摘要要短，只保留能支持修改决策的 failure signal，不复制长日志。

## Protected Behaviors Extraction

对 optimization bench 中的 passed case，抽取代表性正例行为，写入 `tune_report.md`：

| case_id | passing_behavior | judge_positive_signal | what_must_not_break |
|---|---|---|---|
| ... | ... | ... | ... |

提取规则：

- 优先选择与本轮失败模式、active direction 或拟修改文件相关的 passed case；无需穷举所有正例。
- `passing_behavior` 写 agent 做对了什么，例如正确选择工具、正确参数、正确降级、正确输出格式、正确引用证据。
- `what_must_not_break` 写 patch 的保护约束，例如不得禁止搜索、不得删除必要校验、不得改变输出 contract。
- 如果 optimization bench 没有 passed case，写明 `no_passing_cases`，不要伪造保护清单。

## Optimization Effect First（低风险决策约束）

本轮首要目标是提升真实能力，不是通过 gate，也不是通过继续堆叠说明文字获得局部分数。修改前必须执行以下约束：

1. **最小反事实修改**：先定位能改变失败行为的最小节点。优先修复工具路由、参数、执行顺序或替换现有规则；最后才考虑追加新 checklist/强制步骤。
2. **One-in-one-out**：如果新增强制步骤、guide read、validator call、repair loop 或大段指令，必须同时删除、合并或降级等价复杂度；无法抵消时必须标记 `complexity_risk`，并说明为什么收益值得增加关键路径。
3. **已知有效机制保留**：从 input spec 的 protected behaviors、accepted baseline 和 evolution history 提取 preservation checklist。不能因为历史候选未 accepted/rejected 就把其中观察到的正向行为视为无效；同时必须保留其已知回归。
4. **推理与交付分开预测**：每个 proposal 分别说明对 `reasoning_quality` 和 `delivery_reliability` 的预期影响。提高风险识别但导致最终四部分输出、确认、stage completion 或一次性交付消失，不属于整体优化。
5. **禁止用追加规则掩盖执行预算问题**：如果失败发生在尾段、截断、跳过阶段或重复输出，默认先测试缩短路径、提前关键产物、替换冗余规则，不得默认再加 checkpoint/verifier。
6. **单机制优先**：一个 selected proposal 只验证一个主要因果机制。多个文件可以服务同一机制，但不得把工具路由、风险框架和最终渲染三类独立修复混进同一候选。
7. **评分盲区检查**：高分不自动代表输出质量提升。必须人工检查 transcript 是否存在重复区块、尾段未完成、确认块重复、内容写入文件但未在对话交付等 scorer 可能遗漏的问题。

proposal 比较时采用以下默认优先级：

```text
修路由/参数/顺序 > 替换现有规则 > 合并步骤 > 追加新规则块 > 新增 verifier/repair loop
```

这些约束用于提高候选质量，不增加新的 runner 硬阻断；证据不足时应如实标记 uncertain，而不是伪造复杂度精确值。

## Weakness Mining and Candidate Proposals

修改前必须先把失败转为可修 weakness，再比较候选 proposal：

| weakness_id | signature | cases | trace_signal | suspected_component | addressable |
|---|---|---|---|---|---|
| W-001 | `timeout + repeated_same_tool + no_retry_budget` | ... | ... | ... | yes/no/uncertain |

| proposal_id | weakness_id | failure_signature | suspected_root_cause | alternative_causes | selected_operator | proposed_change | risk | decision |
|---|---|---|---|---|---|---|---|---|---|
| P-001 | W-001 | ... | ... | [...] | REDUCE_INSTRUCTION_ENTROPY | ... | low/medium/high | selected/rejected/deferred |

规则：

- `signature` 必须描述机制，不只写 timeout/wrong_answer 等表面症状。
- `addressable=no/uncertain` 的 weakness 不应强行 patch，应写入 `Unresolved / Not Touched`。
- 必须至少提出 3 个机制不同的 proposal，恰好选择 1 个实施；不能把同一种 Prompt 改法换三种措辞充数。
- 每个 proposal 必须写 `suspected_root_cause` 和至少一个 `alternative_causes`，并说明如何区分这些解释。
- 每个 proposal 必须明确 `hypothesis_assessment`；Tune 可以拒绝或降级 Review hypothesis，不得把 spec 当成必须照单执行的 patch 方案。
- `selected_operator` 必须来自 runner 注入的 `mutation_operator_library.json`；不得自行发明 operator，family diversity 也由该 library 校验。
- selected proposal 必须能映射到后续 `change_manifest.json` 中至少一个 edit。
- selected proposal 必须声明 `complexity` 与 `impact_radius`；manifest 必须同时声明 expected/protected behavior metrics、baseline、阈值和 rollback condition。
- rejected/deferred proposal 必须保留 `why_not_selected`，供 review 和下一轮 spec 参考。
- 没有重复实验时禁止使用“永久 retire”“模型天花板已确认”“方向已被证明无效”；只能标记 suspected/testing/supported/falsified/frozen_until_round。

## Patch Plan Gate

修改前必须形成计划，并在 `tune_report.md` 中记录：

| Active Direction / Strategy | Evidence | Root Cause | Target File | Exact Change | Expected Fix Cases | At-Risk Cases | Check |
|---|---|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... | ... | ... |

只有同时满足以下条件才允许执行修改：

- 有明确 optimization evidence，而不是泛泛猜测。
- root cause 落到可修改对象：skill description、SKILL.md workflow、参数规则、输出契约、md 配置、MCP 调用层说明/配置或轻量 glue/config。
- Skill 类 target file 必须位于当前运行模式指定的用户 Skill 可写目录。`workspace/clawevolve-skills/**` 是 Release 私有运行代码，禁止读取、修改或列为候选目标。`skills-repo`、`skills-center`、其他共享/系统 Skill 目录，以及 `workspace/skills/` 下指向公共 Skill 的入口均为只读：允许读取用于定位根因和复用既有能力，但不得把它们列为候选修改目标、不得跟随软链修改其真实目标。
- 如果根因只能通过修改公共 Skill 解决，应将 proposal 标记为 `deferred` 或本轮 `blocked`，明确记录公共依赖及建议，不得复制、改写或绕过公共 Skill 来制造可写副本。
- 创建私有 Skill 前必须检查 `workspace/skills/<skill-name>` 是否已经存在。已存在的公共/系统 Skill 名称视为保留名称，不得创建同名用户 Skill 副本，也不得把原公共入口改指向私有副本。
- target file 位于 spec 允许修改范围内；如果 spec 没有明确允许范围，只能改与 active direction 直接对应的最小 skill/md/config 文件。
- exact change 是通用修复，不包含 case-specific answer、固定 query、固定参数值或 benchmark 细节答案。
- expected_fix_cases 来自失败证据或同类 failure signature，at_risk_cases 来自 Protected Behaviors；二者都要可被 review 验证或说明 `unknown`。
- falsifiable_prediction 具体可证伪：说明 patch 预期消除哪个 failure signal，而不是泛泛“提高效果”。
- rollback_condition 明确：如果验证集出现什么回归或预期未兑现，应由 review reject/retire。
- check 可观察；无法运行时说明原因和替代检查。


## Change Manifest

除 `tune_report.md` 外，必须写入 `{round_dir}/tune/change_manifest.json`，用于 review 逐条验证本轮 patch 假设。推荐 schema：

```json
{
  "schema_version": "evolution.change_manifest.v2",
  "spec_hypothesis_assessment": {"decision": "accept|reject|downgrade", "reason": "evidence-based reason", "unresolved_alternatives": []},
  "selected_proposal_id": "P-001",
  "proposals": [
    {
      "proposal_id": "P-001",
      "failure_signature": "long_workflow_early_termination",
      "suspected_root_cause": "critical path is too long before the first useful artifact",
      "alternative_causes": ["tool latency", "output budget exhaustion"],
      "selected_operator": "SHORTEN_CRITICAL_PATH",
      "proposed_change": "merge non-blocking preparation steps",
      "decision": "selected",
      "hypothesis_assessment": "supported enough for a bounded experiment; alternatives remain plausible",
      "complexity": "low",
      "impact_radius": "one runtime skill file and one workflow stage",
      "why_not_selected": "",
      "risk": "low",
      "evidence": ["optimization failure cluster W-001"]
    },
    {
      "proposal_id": "P-002",
      "failure_signature": "long_workflow_early_termination",
      "suspected_root_cause": "missing intermediate completion checkpoints",
      "alternative_causes": ["tool latency"],
      "selected_operator": "ADD_EXECUTION_CHECKPOINT",
      "proposed_change": "emit a machine-checkable artifact after each stage",
      "decision": "deferred",
      "why_not_selected": "larger change surface than P-001",
      "risk": "medium",
      "evidence": ["optimization failure cluster W-001"]
    },
    {
      "proposal_id": "P-003",
      "failure_signature": "long_workflow_early_termination",
      "suspected_root_cause": "final rendering consumes the remaining output budget",
      "alternative_causes": ["tool latency"],
      "selected_operator": "SEPARATE_ANALYSIS_FROM_RENDERING",
      "proposed_change": "produce structured analysis before final rendering",
      "decision": "rejected",
      "why_not_selected": "does not directly address the earliest observed stop point",
      "risk": "medium",
      "evidence": ["optimization failure cluster W-001"]
    }
  ],
  "expected_signals": [
    {"task_id": "task_06", "metric": "score", "baseline": 0.42, "min_delta": 0.10, "role": "required"}
  ],
  "protected_signals": [
    {"behavior_id": "PB-001", "task_id": "task_16", "metric": "score", "baseline": 0.94, "min_value": 0.84, "max_drop": 0.10, "direction": "maintain"}
  ],
  "edits": [
    {
      "edit_id": "chg-001",
      "proposal_id": "P-001",
      "target_file": "path/to/file",
      "change_type": "WORKFLOW_CHANGE",
      "failure_signature": "long_workflow_early_termination",
      "suspected_root_cause": "critical path is too long before the first useful artifact",
      "alternative_causes": ["tool latency", "output budget exhaustion"],
      "selected_operator": "SHORTEN_CRITICAL_PATH",
      "falsifiable_prediction": "the first useful artifact is produced before stage 2",
      "protected_behaviors": ["existing output contract remains complete"],
      "local_check": "verify the shortened stage order and required artifact",
      "rollback_condition": "first artifact timing does not improve or protected output regresses"
    }
  ]
}
```

推荐同时写入以下向后兼容的效果设计字段；runner 不依赖它们执行，但 Review 和后续 Tune 应消费这些证据：

```json
{
  "atomic_experiment": {
    "independent_variable": "本轮唯一改变的行为变量",
    "target_file": "path/to/file",
    "target_anchor": "唯一修改锚点/段落",
    "held_constant": ["必须保持不变的相邻规则与行为"],
    "confounds_checked": ["检查未混入第二变量"]
  },
  "effect_design": {
    "change_strategy": "route|parameter|reorder|replace|merge|append",
    "patch_operation": "append|replace|delete|reorder",
    "minimum_change_rationale": "为什么这是改变目标行为的最小修改",
    "reasoning_quality_expected": "预期如何影响推理/识别",
    "delivery_reliability_expected": "预期如何影响最终交付",
    "scorer_blind_spot_checks": ["duplicate blocks", "last stage completion"]
  },
  "complexity_budget": {
    "instruction_token_delta_estimate": "decrease|neutral|small_increase|large_increase|unknown",
    "mandatory_step_delta": 0,
    "guide_read_delta": 0,
    "validator_call_delta": 0,
    "repair_loop_delta": 0,
    "critical_path_delta": "shorter|neutral|longer|unknown",
    "offsetting_removals_or_merges": [],
    "one_in_one_out_satisfied": true,
    "complexity_risk": "low|medium|high"
  },
  "preserved_mechanisms": [
    {"mechanism": "capability-level behavior", "source": "spec|baseline|history", "preservation_check": "observable check"}
  ]
}
```

要求：

- `edits[]` 只记录实际执行的修改；未执行 proposal 只保留在 `tune_report.md`。
- `edit_id` 稳定唯一，建议 `chg-001`、`chg-002`。
- `expected_fix_cases` 为空时必须解释原因；一般不允许执行没有 expected fix 的 patch。
- `at_risk_cases` 可为空，但必须已检查 Protected Behaviors；未知风险写 `unknown` 到报告中。
- 如果因环境限制无法写 manifest，必须在 `tune_report.md` 写明 `manifest_status: missing` 和原因。

## Change Budget

默认每轮保持窄改：

- 候选搜索可以比较至少 3 个 operator-diverse proposals，但每轮只实施 1 个 selected proposal。
- 实际实验必须恰好 1 个 executed edit、1 个 changed file、1 个 unified-diff hunk；如果同一机制需要跨两个文件联动，拆成两轮，先验证第一个文件的独立效果。
- 一个 diff hunk 内只允许改变一个 independent variable；不得顺手删除防截断、确认、validator、fallback 等其他规则。
- 不做 opportunistic refactor、格式化全仓、重命名、大范围迁移。
- 如果需要大改，把建议写入 `Notes for Spec Evolution`，不要在本轮执行。
- 不要把所有失败都通过扩大 skill description 解决：只有证据显示 skill 未触发或触发不稳定时才改 description；如果 skill 已触发但执行失败，应优先改 workflow、parameter rules、MCP invocation guidance 或 output contract。

## 硬约束

- 不读取或使用 validation result。
- 不修改 `clawevolve-*` / `clawbench-*` 自进化与评测引擎、当前 runner/handler、`clawevolve_results/`、`clawbench_results/` 或历史 round 状态；业务 bot 候选只能修改目标 bot 的运行时能力面。引擎自身优化必须使用独立的 engine-evolution 实验。
- 不修改 objective。
- 不修改 optimization/validation cases。
- 不修改 bench result、metrics 或评测打分逻辑。
- 不覆盖历史 round artifact。
- 不修改 MCP server/tool 本体实现；MCP 相关优化仅限调用选择、命令、参数、顺序、降级、结果消费等调用层说明或配置。
- 不为了个别 case 写硬编码答案。
- 优先小改动，保持修改可解释、可回滚。

## MVP 核心 Prompt

当输入齐全时，可以按这个最小行为执行：

```text
读取 round_dir 中的 objective、spec-v{N-1} 和 optimization_result。先以 objective 的业务硬约束和成功标准为准，再根据 spec-v{N-1} 的 active directions / `优化策略` 优化当前 workspace。先盘点 workspace/skills/ 已激活的 Skill；Skill 修改仅允许位于当前运行模式指定的用户 Skill 可写目录，不得为已经存在的公共/系统 Skill 创建同名私有副本或替换其入口。公共 Skill 及其入口只允许读取分析，不得作为候选修改目标或跟随软链修改。允许修改 md 配置和 MCP 调用层说明/配置；不要修改 MCP 本体、benchmark 数据、scoring、objective 或 validation 数据。保持改动小、可解释、可回滚，并输出 tune_report、changed_files、change_manifest.json 和 diff。
```

## tune_report 模板

```md
# Tune Report

## Summary

...

## Bench Summary

- total:
- passed:
- failed:
- errors:
- score:
- pass_rate:

## Failure Evidence

| case_id | score | failure_signal | judge_notes/breakdown | tool_path/transcript_signal | suspected_root_cause |
|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... |

## Protected Behaviors

| case_id | passing_behavior | judge_positive_signal | what_must_not_break |
|---|---|---|---|
| ... | ... | ... | ... |

## Failure Analysis

| Failure Type | Evidence Cases | Hypothesis | Related Direction | Priority |
|---|---|---|---|---|
| ... | ... | ... | ... | high |

## Weakness Mining

| weakness_id | signature | cases | trace_signal | suspected_component | addressable |
|---|---|---|---|---|---|
| W-001 | ... | ... | ... | ... | yes/no/uncertain |

## Candidate Proposals

| proposal_id | weakness_id | failure_signature | suspected_root_cause | alternative_causes | selected_operator | proposed_change | risk | decision |
|---|---|---|---|---|---|---|---|---|---|
| P-001 | W-001 | ... | ... | [...] | REDUCE_INSTRUCTION_ENTROPY | ... | low/medium/high | selected/rejected/deferred |

## Patch Plan

| Active Direction / Strategy | Evidence | Root Cause | Target File | Exact Change | Expected Fix Cases | At-Risk Cases | Check |
|---|---|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... | ... | ... |

## Effect Design

- change_strategy: route|parameter|reorder|replace|merge|append
- minimum_change_rationale:
- reasoning_quality_expected:
- delivery_reliability_expected:
- scorer_blind_spot_checks:

## Runtime Complexity Budget

| Dimension | Before | After | Delta | Offset / Evidence |
|---|---|---|---|---|
| instruction tokens | ... | ... | decrease/neutral/increase/unknown | ... |
| mandatory steps | ... | ... | ... | ... |
| guide reads | ... | ... | ... | ... |
| validator calls | ... | ... | ... | ... |
| repair-loop upper bound | ... | ... | ... | ... |
| critical path | ... | ... | shorter/neutral/longer/unknown | ... |

- one_in_one_out_satisfied: true/false/unknown
- complexity_risk: low/medium/high

## Preserved Mechanisms

| Mechanism | Source | Why Known-Good / Observed | Preservation Check |
|---|---|---|---|
| ... | spec/baseline/history | ... | ... |

## Direction Execution Result

| Direction ID | Planned | Executed | Changed Files | Confidence | Notes |
|---|---|---|---|---|---|
| ... | yes/no | yes/no | ... | high/medium/low | ... |

## Change Manifest Summary

| edit_id | proposal_id | expected_fix_cases | at_risk_cases | falsifiable_prediction | rollback_condition |
|---|---|---|---|---|---|
| chg-001 | P-001 | ... | ... | ... | ... |

## Changes Made

| File | Change | Reason |
|---|---|---|
| ... | ... | ... |

## Case Impact Hypothesis

| Failure Case | Before Failure | Change Expected to Help | Risk |
|---|---|---|---|
| ... | ... | ... | ... |

## Local Checks

| Check | Command/Method | Result | Notes |
|---|---|---|---|
| ... | ... | passed/failed/not_run | ... |

## Unresolved / Not Touched

| Failure Mode | Reason Not Addressed | Suggested Next Direction |
|---|---|---|
| ... | ... | ... |

## Risks / Watch Points

...

## Notes for Spec Evolution

...
```


## ClawBench 数据来源

runner v4 将 bench summary 直接写入 `round_state.json`，不再产出独立的 `optimization_result.json`。数据获取方式：

1. **优先**：从 prompt 中直接消费（runner 已内嵌 bench summary 文本）。
2. **如需逐任务详情**：读取 `round_state.json` → `bench.optimization.resultPath` 指向的原始 `*_benchmark_report.json`。
3. **如需 transcript**：原始 report 同目录下的 `{run_id}_transcripts/{task_id}.jsonl`。

如果缺少关键输入，不做猜测性修改；写一份 blocked `tune_report.md` 说明缺失项。


## Expected / Protected Signal Direction

Every signal must use the exact field `task_id` and reference one concrete optimization `task_*`; never emit `task_id_or_group`, `overall`, `all_tasks_*`, or aggregate selectors. Expected signals may set `role=required|supporting` (default `required`): every required signal gates effect, while supporting signals are diagnostic and never rescue a failed required signal. Expected signals use `direction=increase|decrease|boolean_flip`; `maintain` is forbidden for expected-fix signals. Numeric `increase|decrease` signals require `expected_min` or a positive `min_delta`. `boolean_flip` signals use `expected_value` (default `1`) and do not require `expected_min` or `min_delta`. Protected signals use `direction=maintain`. Every protected behavior from Spec—resolved or unresolved—must have at least one observable `protected_signals` binding with its `behavior_id` before candidate bench; a missing binding makes the candidate opt signal plan invalid. Put `behavior_id` only on the signal that actually matches that Spec behavior's metric; extra canaries or additional protected metrics must omit `behavior_id`. Protected signals use `gate=hard|budget|supporting`: Spec atomic invariants default to hard, score-breadth and runner canaries use budget, and extra diagnostics without a Spec behavior use supporting. Only hard protected failures block targeted effect; budget failures are decided by full-opt regression budget. The binding must reuse the Spec protected behavior's resolved `metric`, with `min_value` not lower than and `max_drop` not wider than the Spec declares, and a `task_id` in the optimization task set. The protected gate must pass BOTH `candidate >= min_value` and `drop <= max_drop`; violating either fails the gate. `boolean_flip` expected-fix signals require `expected_value` (default `1`) and only the flip-toward-`expected_value` direction counts as a fix (`baseline != expected_value` and `candidate == expected_value`); flipping away from `expected_value` is a rejection. Numeric signal fields (`baseline`, `min_delta`, `expected_min`, `expected_value`, `min_value`, `max_drop`) must be finite numbers; strings, booleans, NaN, Inf, or missing required values fail-closed the candidate gate instead of raising. Before writing `TUNE_COMPLETE`, self-audit the manifest: operator-family diversity meets the Spec threshold; every signal uses one concrete `task_id`; every Spec protected behavior has a same-metric binding; edit target/affected-file coverage equals `changed_files.txt`; and every edit has non-empty `alternative_causes`. Repair the manifest before completion if any check fails.
