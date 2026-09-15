import type { EvolutionFlowKey } from "./evolution-flow.js";
import type { OfficialStageDefinition, StageExtensionMode, StageKey } from "./stage-catalog.js";
import { stageDevelopmentBackground } from "./stage-development-background.js";

const modeNames: Record<StageExtensionMode, string> = {
  preprocess: "前置处理", postprocess: "后置处理", replace: "完整替换",
};

function json(value: unknown): string {
  return `\`\`\`json\n${JSON.stringify(value, null, 2)}\n\`\`\``;
}

// Concrete business examples, checked against the platform contracts in tests.
// IDs and values are illustrative, never runtime defaults.
function resultExample(stage: StageKey): Record<string, unknown> {
  if (stage === "diagnose") return {
    diagnosis: {
      summary: "发现执行前缺少范围确认的问题",
      issues: [{ code: "scope_missing", title: "执行范围不明确", severity: "medium", caseCount: 1, suggestion: "执行前确认处理范围" }],
    },
    cases: { total: 1, goodCount: 0, badCount: 1, items: [{ caseId: "真实案例标识", type: "bad", summary: "用户未指定范围时直接开始处理" }] },
  };
  if (stage === "plan") return {
    goal: { summary: "缺少处理范围时先确认", metrics: [{ key: "pass_rate", name: "通过率", operator: ">=", target: 0.9, unit: "ratio" }] },
    spec: { version: "v0", content_type: "text", content: "补充处理范围检查；信息不足时向用户询问。" },
    benchCases: {
      trainCount: 1, testCount: 1,
      items: ["train", "test"].map((split) => ({
        sourceCaseId: `${split}来源案例标识`, taskId: `${split}评测案例标识`, split,
        template: { ownerUserId: "真实所有者标识", domainId: `${split}真实评测域标识`, templateName: `${split}已发布模板名称`, version: 1 },
      })),
    },
    benchDomains: { trainBenchDomainId: "train真实评测域标识", testBenchDomainId: "test真实评测域标识", validationIndependent: true, validationMode: "independent" },
  };
  if (stage === "hardening") return {
    summary: "补充输入不足时的处理约束，并修正失效的相对引用",
    changed: true,
    changed_files: ["SKILL.md"],
  };
  return {
    diff: { summary: "补充执行范围检查", files: ["实际修改的文件路径"] },
    metrics: ["train", "test"].map((role) => ({
      key: "pass_rate", name: "通过率", role: `candidate_${role}`, value: 0.9,
      ownerUserId: "真实所有者标识", domainId: `${role}真实评测域标识`, benchRunId: `${role}真实候选评测运行标识`,
    })),
    baseline: Object.fromEntries(["train", "test"].map((role) => [role, {
      role, producerStepId: "真实来源步骤标识", source: "generated", ownerUserId: "真实所有者标识",
      domainId: `${role}真实评测域标识`, benchRunId: `${role}真实基线运行标识`, metrics: { pass_rate: 0.7 },
    }])),
    roundDecision: { stop: true, reason: "根据实际评测结果判断已达到目标" },
  };
}

// Only the new Skill-evolution Plan package describes the four business calls.
// Keep the approved background and every other slot's guide unchanged.
function planBusinessGuide(stage: OfficialStageDefinition): string {
  const question = { tag: "confirm_scope", format: "text", content: "请说明本次允许处理的范围" };
  const formQuestion = { ...question, format: "html", content: '<form><label>处理范围<input name="scope" required></label><button type="submit">确认</button></form>' };
  const formAnswer = { scope: "只补充执行前的范围检查" };
  const discovery = {
    schema_version: "clawevolve.plan.discovery.v1",
    workspace_root: "本次独立工作目录的实际路径",
    analysis_summary: { diagnosed_problem: "根据真实案例概括问题", environment_root_cause: "根据已读文件说明原因", optimization_strategy: "说明有证据支持的改进方向" },
    case_findings: [{ case_id: "输入中的案例标识", failure_mode: "输入中的问题类型", inspected_files: ["目标 Skill 内已读文件的相对路径"], environment_analysis: "说明已读文件与案例的关联", optimization_ideas: ["根据证据提出改进建议"] }],
    target_findings: [{ path: "目标 Skill 内已读文件的相对路径", reason: "为何选择此处", current_gap: "当前缺口", proposed_change: "建议如何改进", related_case_ids: ["输入中的案例标识"], failure_modes: ["输入中的问题类型"] }],
    merged_targets: ["目标 Skill 内已读文件的相对路径"],
  };
  const contract = {
    schema_version: "clawevolve.case-contract.v1",
    case_id: "输入中的案例标识", template_id: "输入中的模板标识", case_type: "bad", split: "train", source_session_id: "输入中的真实会话标识",
    task_contract: {
      user_intent: "案例中的用户意图", required_outcomes: ["应交付的结果"], acceptable_approaches: ["允许的处理方式"],
      required_actions: [], required_evidence: ["应提供的证据"], completion_signals: ["完成标志"],
      acceptable_failure_handling: ["信息缺失时如实说明"], forbidden_behaviors: ["编造输入中没有的事实"],
    },
    grading_strategy: { grading_type: "llm_judge", criteria: [{ id: "completion", name: "完成情况", weight: 100, description: "按案例要求检查实际结果", score_1: "全部满足", score_075: "大部分满足，仅有小缺口", score_05: "部分满足", score_025: "仅有少量有效进展", score_0: "未满足" }] },
    automated_checks: [],
    replayability: { query_available: true, required_context: [], missing_context: [], replayable: true },
    provenance: { source_fields: ["case.query"], confidence: 0.8, unsupported_claims: [] },
  };
  const phases = [
    {
      key: "discovery", name: "问题分析",
      input: { source_path: "本次真实诊断资料文件的实际路径", source_sha256: "该文件的实际校验值", workspace_root: "本次独立工作目录的实际路径", allowed_targets: ["目标 Skill 副本的实际路径"] },
      requirements: "基于真实诊断资料和已读目标文件，按本次完整字段要求返回问题分析与改进位置。",
      notes: "读取 source_path 提供的诊断资料和 allowed_targets 内的目标 Skill。只分析，不修改目标文件。输出案例发现、原因、改进建议和具体目标路径；案例标识沿用真实来源，目标必须是实际检查过且位于允许范围内的窄路径。",
      output: discovery,
    },
    {
      key: "case_contract", name: "案例评测要求",
      input: { case: { case_id: "输入中的案例标识", template_id: "输入中的模板标识", case_type: "bad", split: "train", source_session_id: "输入中的真实会话标识", query: "案例中的真实用户请求" }, goal: "本次改进目标与限制", user_intent: null, discovery_notes: "已完成的问题分析", validation_error: "" },
      requirements: "针对这一个案例，按本次完整字段要求返回一个 contracts 条目。",
      notes: "每次只处理 input.case 指定的一个案例，可能多次调用。保留案例标识、模板标识、案例类型、训练或测试归属及真实会话来源；这些是输入数据，不是要求你发布模板。编写任务要求、评分标准、可重放性和证据来源；无可靠自动检查时保留空数组，不编造检查条件。缺少原始请求或上下文时如实标明，不能宣称可重放。评分权重合计为 100，并提供五档评分说明。",
      output: { contracts: [contract] },
    },
    {
      key: "objective_document", name: "目标文档",
      input: { context: {}, objective_template: "本次目标文档的完整模板", objective: {} },
      requirements: "按提供的目标结构、证据和完整模板编写目标文档，保留模板结构及冻结目标。",
      notes: "context 提供本次目标、诊断证据、已检查文件和评测选择；objective 提供完整目标结构。按 objective_template 编写正文，不改变平台给定的目标、指标或阈值。",
      output: { objective_markdown: "按本次完整模板编写的目标文档正文" },
    },
    {
      key: "spec_document", name: "方案文档",
      input: { context: {}, objective_markdown: "已经生成的完整目标文档", spec_template: "本次方案文档的完整模板", spec: {} },
      requirements: "按提供的方案结构、目标文档、证据和完整模板编写方案文档。",
      notes: "context 提供本次证据和约束；spec 提供完整方案结构。结合 objective_markdown，按 spec_template 说明改什么、为什么改、如何验证。保留允许的修改范围，不把待验证方案写成已通过的结果。",
      output: { spec_markdown: "按本次完整模板编写的方案文档正文" },
    },
  ];
  return [
    `# ${stage.name} · 完整替换 Skill 开发任务`, "",
    "## 背景与本次开发目标", "", stageDevelopmentBackground(stage, "replace", true), "",
    "## 输入与输出", "",
    "运行时，平台会向执行 Agent 提供本次输入文件和结果文件的具体位置。读取输入文件，完成处理后，将结果以 JSON 格式写入结果文件。", "",
    "平台每次提供当前要完成的问题分析、案例评测要求、目标文档或方案文档之一，以及对应的完整数据和要求。同一个规划 Skill 根据 phase 处理本次工作，只返回这一项的业务结果，不一次返回整个规划环节结果。", "",
    "输入包含 phase（本次工作类型）、input（本次数据）、output_requirements（本次完整输出要求）。下面示例说明格式；context、objective、spec 在实际调用时提供对应完整对象，示例不展开其全部内容。路径、案例、模板、结论和数值必须以真实输入为准，不可照抄示例。", "",
    ...phases.flatMap((phase) => [
      `### ${phase.name}（${phase.key}）`, "", phase.notes, "",
      "输入示例：", "", json({ phase: phase.key, input: phase.input, output_requirements: phase.requirements }), "",
      "输出示例：", "", json(phase.output), "",
    ]),
    "只写本次要求的 JSON 业务对象。文档正文放在对应字符串字段中，不另外添加结果外层。信息不足时提出问题；输入或文件不可用时如实说明，不猜替代路径，也不编造成功结果。", "",
    "## 平台提供的能力", "",
    "平台保留 OpenClaw 执行管理、目标路径验证、训练与测试划分、评测模板渲染和发布、已发布资源核验、结果校验及后续流程。规划 Skill 只提供当前业务分析或文档，不负责这些平台操作。", "",
    "平台在各次业务处理后沿用默认规划逻辑，组装目标、方案、评测案例和评测域。最终环节结果由平台生成；中间业务结果按本次 output_requirements 校验，不套用整个环节的最终结果格式。平台完成发布后取得真实评测域和模板引用，规划 Skill 不生成或上传这些发布标识。", "",
    "单段集成测试由平台提供独立测试副本及本次真实诊断资料；这里只分析目标 Skill 的独立副本，不修改原始 Skill。", "",
    "### 请求用户补充信息", "",
    "需要补充信息时，只将问题 JSON 写入结果文件并结束本次处理；不要同时写业务结果。文字问题：", "", json({ question }), "",
    "需要表单时，format 使用 html，content 提供表单 HTML：", "", json({ question: formQuestion }), "",
    "平台收集回答后继续调用当前 Skill。恢复输入保留原 phase、input、output_requirements，并增加 hitl：question 是原问题，answer 是文字字符串或原始表单对象，history 是本次工作累计的问题与回答。文字回答示例：", "",
    json({ hitl: { question, answer: "只补充执行前的范围检查", history: [{ question, answer: "只补充执行前的范围检查" }] } }), "",
    "表单回答时，answer 直接为表单对象，例如：", "", json({ hitl: { question: formQuestion, answer: formAnswer, history: [{ question: formQuestion, answer: formAnswer }] } }), "",
    "读取回答后继续本次工作，完成时返回对应业务结果。不自行等待、轮询或调度下一步。", "",
    "## 开发与交付", "",
    "根据以上说明，将本文件替换为实现当前处理逻辑的 `SKILL.md`。需要时，可以在同目录下增加脚本、参考说明等辅助文件。", "",
    "完成开发后，将 `SKILL.md` 及辅助文件打包为 ZIP，并提示用户将该 ZIP 上传到平台。", "",
  ].join("\n");
}

export function stageDevelopmentGuide(stage: OfficialStageDefinition, mode: StageExtensionMode, flow?: EvolutionFlowKey): string {
  if (flow === "skill_evolution" && stage.stage === "plan" && mode === "replace") return planBusinessGuide(stage);
  const hardeningFlow = flow === "skill_hardening";
  const skillFlow = flow === "skill_evolution" || hardeningFlow;
  const input: Record<string, unknown> = {
    task: { task_id: "本次任务标识", task_type: hardeningFlow ? "hardening" : "full", target_bot_id: "执行 Bot 标识" },
    ...(skillFlow ? { target_skill: {
      asset_id: "Skill 资产标识", skill_id: "原始 Skill 标识", name: "待进化 Skill 名称",
      workspace: "本次独立工作目录的实际路径", path: "待进化 Skill 副本的实际目录路径", baseline_sha256: "原始内容的校验值",
    } } : {}),
    ...(stage.stage === "hardening" ? { goal: "在不改变业务意图的前提下提升 Skill 稳定性" }
      : stage.stage === "diagnose" ? { diagnose_goal: "检查缺少处理范围时的执行表现", session_source: { mode: "local" } }
      : stage.stage === "plan" ? { goal: "缺少处理范围时先确认", diagnose_result: resultExample("diagnose") }
        : { round: 1, plan_result: resultExample("plan") }),
    ...(mode === "postprocess" ? { builtin_result: resultExample(stage.stage) } : {}),
  };
  const patches: Record<StageKey, unknown> = {
    diagnose: { diagnosis: { summary: "补充说明问题的适用范围和证据" } },
    hardening: { summary: "补充说明实际完成的加固和保留的业务边界" },
    plan: { spec: { content: "在原方案基础上补充处理范围检查和验证要求。" } },
    optimize: { roundDecision: { stop: false, reason: "根据实际结果仍需继续优化" } },
  };
  const output = mode === "preprocess" ? { summary: "如实说明本次处理", changed: false, changed_files: [] }
    : mode === "postprocess" ? { result_patch: patches[stage.stage] } : resultExample(stage.stage);
  const inputNotes: Record<StageKey, string> = {
    diagnose: "`diagnose_goal` 是本次诊断重点；`session_source` 指定真实会话来源，mode 为 local 或 service_export。诊断目标不是会话内容，需要按所提供的来源读取会话。",
    hardening: "`target_skill` 是必须处理的独立候选；`goal` 是可选的额外加固目标。不得修改候选目录之外的内容。",
    plan: "`goal` 是改进目标与限制；`diagnose_result` 是上游诊断的最终结果，包含 diagnosis 和 cases。直接按目标进化时不提供诊断结果。",
    optimize: "`round` 是当前轮次；`plan_result` 是上游规划冻结的目标、方案和评测选择。第二轮及以后还会提供 `previous_round_result`，结构与优化结果相同。",
  };
  const resultNotes: Record<StageKey, string> = {
    diagnose: "必须包含 diagnosis 和 cases。diagnosis 的 summary、issues 必填；每个 issue 的 code、title、severity、caseCount、suggestion 必填。cases 的 total、goodCount、badCount、items 必填；每个案例的 caseId、type 必填，type 为 good 或 bad，summary 可选。数量应与真实案例一致。",
    hardening: "必须包含 summary 和 changed。summary 如实说明本次完成的加固；changed 表示是否修改候选 Skill；changed_files 仅列实际修改的候选 Skill 相对路径，未修改时可省略或为空。",
    plan: "必须包含 goal、spec、benchCases、benchDomains。goal 的 summary、metrics 必填，指标包含示例中的全部字段，operator 为 >=。spec 的 version、content_type、content 必填，content_type 为 text。benchCases 的 trainCount、testCount、items 必填；每项的 sourceCaseId、taskId、split、template 必填，split 为 train 或 test；template 的 ownerUserId、domainId、templateName 必填，version 可选。benchDomains 的 trainBenchDomainId、testBenchDomainId 必填，其余字段可选。评测域与模板必须真实存在且可执行，不能使用示例占位标识。",
    optimize: "必须包含 diff、metrics、baseline、roundDecision。diff 的 summary、files 必填；每项指标的 benchRunId、ownerUserId、domainId 必填，其余示例字段可选，role 为 candidate_train 或 candidate_test。baseline 的 train、test 及其示例字段全部必填，source 为 generated 或 reused。roundDecision 的 stop 必填，reason 可选；还可提供 spec（version、content_type、content 必填，content_type 为 text）。所有评测标识和指标必须来自真实运行，停止判断必须有实际依据。",
  };
  const patchNotes: Record<StageKey, string> = {
    diagnose: "diagnosis 用于补充诊断摘要和问题，cases 用于修正案例；数组项沿用输入示例中的字段要求，案例类型为 good 或 bad，数量与案例保持一致。",
    hardening: "只能补充或修正 summary，不得把未发生的修改写入结果。",
    plan: "goal 用于补充目标和指标，spec 用于修正规划文本，benchCases、benchDomains 用于修正评测选择。评测标识必须真实有效；修改数组时提供完整数组项，不能丢失原有案例或填写占位标识。",
    optimize: "diff 用于补充实际修改说明，roundDecision 用于修正继续或停止的判断及原因，spec 用于修订方案。不得修改 metrics 或 baseline，不得把未经评测的结论写成已经达标。",
  };
  return [
    `# ${stage.name} · ${modeNames[mode]} Skill 开发任务`, "",
    "## 背景与本次开发目标", "", stageDevelopmentBackground(stage, mode, skillFlow, hardeningFlow), "",
    "## 输入与输出", "",
    "运行时，平台会向执行 Agent 提供本次输入文件和结果文件的具体位置。读取输入文件，完成处理后，将结果以 JSON 格式写入结果文件。", "",
    "### 输入", "", "以下示例说明数据格式；标识、路径和业务内容以本次实际输入为准。", "", json(input), "",
    `\`task\` 是平台提供的任务信息。${hardeningFlow ? "Skill 加固任务的 task_type 为 hardening" : "完整任务的 task_type 为 full"}，单段集成测试为 stage_test。`, "",
    ...(stage.stage !== "optimize" ? [`${skillFlow ? "目标 Skill" : "Bot"}单独诊断任务的 task_type 为 diagnose，执行诊断及按任务选择启用的规划，不进入优化环节。`, ""] : []),
    inputNotes[stage.stage], "",
    ...(skillFlow ? ["`target_skill` 描述本次待进化 Skill 的独立副本。path 是可读取的 Skill 目录，workspace 是本次工作目录；其余字段标识原始 Skill 和内容基线。修改范围限于任务允许的副本内容。", ""] : []),
    ...(skillFlow && (hardeningFlow || (stage.stage === "diagnose" && mode === "preprocess") || (stage.stage === "plan" && mode === "replace"))
      ? ["本点位的单段集成测试由平台提供独立测试副本；target_skill.kind 为 stage_test_fixture，fixture_id 标明测试素材。此时 asset_id、skill_id 是 fixture: 开头的测试资源标识，不代表正式 Skill 资产，baseline_sha256 是测试素材原始 ZIP 的校验值。读取和输出方式不变，测试不会应用到原始 Skill。", ""] : []),
    ...(mode === "postprocess" ? [`\`builtin_result\` 是平台内置${stage.name}已经生成的结果，结构见上例。`, ""] : []),
    "所需输入或文件不可用时，应如实说明缺少什么，不猜测替代路径，不编造内容或成功结果。", "",
    "### 输出", "", json(output), "",
    mode === "preprocess"
      ? "summary（字符串）和 changed（布尔值）必填；changed_files 是实际修改文件路径的字符串数组，未修改时可省略或为空。不添加其他字段，也不需要返回完整环节结果。"
      : mode === "postprocess"
        ? `只返回 result_patch 中需要补充或修正的字段。允许修改的字段：${stage.postprocessWritablePaths.join("、")}。对象可只提供要变更的子字段；数组需要提供完整替换内容。平台负责合并并校验最终结果。无需修改时返回空对象作为 result_patch。\n\n${patchNotes[stage.stage]}`
        : resultNotes[stage.stage], "",
    "示例中的结论、修改和评测数值仅说明格式；输出必须反映本次真实处理结果。", "",
    "## 平台提供的能力", "",
    "平台负责准备本次输入、提供文件位置、调用当前 Skill、校验结果并继续后续流程。", "",
    "### 请求用户补充信息", "",
    "需要用户补充信息时，将以下问题 JSON 写入结果文件并结束本次处理。", "", json({ question: { tag: "confirm_scope", format: "text", content: "请说明本次允许处理的范围" } }), "",
    "需要表单时，format 使用 html，content 提供表单 HTML：", "", json({ question: { tag: "confirm_scope", format: "html", content: '<form><label>处理范围<input name="scope" required></label><button type="submit">确认</button></form>' } }), "",
    "平台展示问题、收集回答，再继续调用当前 Skill。下一次输入会增加 human_input，tag 与问题对应；文本回答在 content，表单字段在 fields。例如表单回答：", "", json({ human_input: { tag: "confirm_scope", fields: { scope: "只补充执行前的范围检查" } } }), "",
    "读取回答后继续处理，完成时按上一节写入业务结果。不要自行等待、轮询或调度下一步。", "",
    "## 开发与交付", "",
    "根据以上说明，将本文件替换为实现当前处理逻辑的 `SKILL.md`。需要时，可以在同目录下增加脚本、参考说明等辅助文件。", "",
    "完成开发后，将 `SKILL.md` 及辅助文件打包为 ZIP，并提示用户将该 ZIP 上传到平台。", "",
  ].join("\n");
}
