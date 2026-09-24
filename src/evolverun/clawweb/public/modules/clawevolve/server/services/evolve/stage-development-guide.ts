import type { EvolutionFlowKey } from "./evolution-flow.js";
import type { OfficialStageDefinition, StageExtensionMode, StageKey } from "./stage-catalog.js";
import { stageDevelopmentBackground } from "./stage-development-background.js";

const businessBoundary = "本开发包说明接入背景、输入输出 Contract 和平台能力。输入中的任务目标、上游结果和资源描述是当前环节的上下文，不另行指定自定义 Skill 的业务步骤。具体处理逻辑、默认策略以及是否和何时交互由业务定义；满足本接入点的输出要求和资源边界即可。示例只演示协议，不是必须实现的业务流程。";
const feedbackRoundGuide = "反馈轮次只以输入 loop.round 为准，未提供 loop 时为第 1 轮。表单或文本确认后的恢复仍属于同一轮，不按调用次数或交互次数递增轮次。";

const modeNames: Record<StageExtensionMode, string> = {
  preprocess: "前置处理", postprocess: "后置处理", replace: "完整替换",
};

function json(value: unknown): string {
  return `\`\`\`json\n${JSON.stringify(value, null, 2)}\n\`\`\``;
}

function textInteractionGuide(nativeBusiness: boolean): string[] {
  const question = { format: "text", tag: "execution_confirmation", content: "本次拟执行的事项与范围：……。请明确回复是否批准继续，或说明调整意见。" };
  const answer = { tag: question.tag, content: "批准按上述范围继续" };
  return [
    "### 文本回复与执行确认", "",
    "需要自由文本回复，或业务流程要求展示当前事项并等待明确批准时，使用 format: text。content 写明本次需要用户回应的内容，tag 是由业务命名的交互标签，仅使用字母、数字、点、下划线或短横线（1–128 字符）。写入结果后结束本次调用，等待平台携带回复恢复。", "",
    json({ hitl: true, question }), "",
    "较长的文本回复请求或执行确认可保存为 UTF-8 Markdown 文件，用 question.contentFile 替代 question.content；平台读取原文后按同一文本交互 Contract 校验和展示。文件路径以结果 JSON 所在目录为基准，允许其子目录，不得越出该目录；文件必须存在且不超过 1 MiB，读取后的内容仍须满足文本交互长度限制。contentFile 与 content 不能同时提供。此能力与总结 summaryFile、表单材料 contentFile 使用相同的文件读取规则，无需业务编写 JSON 转义脚本。", "",
    json({ hitl: true, question: { format: "text", tag: question.tag, contentFile: "confirmation.md" } }), "",
    nativeBusiness
      ? "恢复时从 hitl.answer.content 读取用户原文，hitl.question 是对应请求，hitl.history 保存本次业务调用的累计交互："
      : "恢复时从 human_input.content 读取用户原文，human_input.tag 对应请求标签，human_input.history 保存当前 Step 的累计交互：", "",
    json(nativeBusiness
      ? { hitl: { question, answer, history: [{ question, answer }] } }
      : { human_input: { ...answer, history: [{ question, answer }] } }), "",
    "交互形式、内容和时机由业务流程决定。填写表单与批准执行是不同的业务含义；按对应请求解释用户实际回复，不将未回复或其他问题的回答视为批准。表单与文本交互可以在同一个 Stage 中按业务需要多次发生；收到回复后仍需交互时，再返回 hitl: true。业务完成后的反馈 Loop 不代替执行前确认。", "",
  ];
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
    diff: { summary: "补充执行范围检查", files: [{ path: "实际修改的文件路径", change: "modified" }] },
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

function nativeAnalysisGuide(stage: OfficialStageDefinition, skillFlow: boolean): string {
  const diagnose = stage.stage === "diagnose";
  const diagnosis = {
    case_type: "bad", symptom_class: "tool_use", root_cause_class: "instruction_gap",
    common_problem_key: "file_access", evolution_failure_mode: "tool_failure",
    query: "输入会话中的真实用户请求", root_cause_summary: "根据真实会话证据说明原因",
    evidence: [{ kind: "assistant", text: "原始会话中的相关证据" }], confidence: 0.8,
  };
  const tuneFiles = Object.fromEntries(["tune_report.md", "changed_files.txt", "diff.patch", "change_manifest.json"]
    .map((name) => [name, `本次 input.output_files 中 ${name} 的实际绝对路径`]));
  const reviewFiles = { "review_decision.json": "本次 input.output_files 中 review_decision.json 的实际绝对路径" };
  const question = { format: "form", title: "确认处理范围", contents: [
    { id: "material", title: "待确认材料", format: "markdown", contentFile: "material.md" },
  ], questions: [{ id: "scope", type: "long_text", title: "允许处理的范围", required: true }] };
  const answer = { answers: { scope: { value: "只处理本次输入指定的范围" } } };
  return [
    `# ${stage.name} · 完整替换 Skill 开发任务`, "",
    "## 背景与本次开发目标", "",
    stageDevelopmentBackground(stage, "replace", skillFlow), "",
    "## 输入与输出", "", businessBoundary, "",
    "平台提供输入文件和结果文件的具体位置。读取输入，执行自己的业务逻辑，将本次业务结果写入指定结果文件；不调用平台初始化、提交、轮询或调度脚本。", "",
    "输入包含 phase（当前业务调用）、input（真实业务数据）、output_requirements（本次完整输出要求）。目标为独立 Skill 副本时，还提供 target_skill。示例只说明格式；路径、证据和结果均以真实输入为准。", "",
    ...(diagnose ? [
      "### 会话分析（session_analysis）", "",
      "平台沿用原有会话采集流程，每次交付一个真实会话及诊断要求。读取 input.session：包含 session_id、path、bot_id、created_at、原始问题及可用的会话内容；path 指向原始会话文件。input.requirements 是当前诊断目标与要求。根据它们提取有证据的成功或失败案例；会话没有符合要求的案例时，返回空 diagnoses 数组。", "",
      json({ phase: "session_analysis", input: {
        session: { session_id: "真实会话标识", path: "原始会话文件路径", bot_id: "真实 Bot 标识", created_at: "会话时间", first_question: "原始问题", user_text: "可用的用户原文", assistant_text: "可用的回答原文", tool_text: "可用的工具记录", raw_text: "可用的会话原文" },
        requirements: { raw_message: "用户诊断要求", intent_text: "诊断重点" },
      } }), "",
      "输出为 diagnoses 数组。每项必填 case_type、symptom_class、root_cause_class、common_problem_key、evolution_failure_mode、query、root_cause_summary；case_type 为 good 或 bad。其他可选字段及类型见 output_requirements；不要返回或替换 session 对象。", "",
      json({ diagnoses: [diagnosis] }), "",
      "平台继续执行原有配额、筛选、证据整理、诊断报告和 Plan 输入生成。当前 Skill 不输出整个 Stage 的 diagnosis/cases 汇总，也不发布评测资源。", "",
    ] : [
      "### 调优（tune）", "",
      "input 提供 workspace、round、skill_write_root、objective、input_spec、spec_source、optimization_bench_result、failure_profile、scene_playbook、mutation_operator_library、evolution_history 和 output_files。这些材料由原有流程准备；根据真实目标与证据执行改进，修改范围遵守输入中的工作目录和 Skill 写入边界。", "",
      "写入 input.output_files 指定的四份原生产物：tune_report.md 说明实际改动；changed_files.txt 每行一个工作目录相对路径；diff.patch 是实际修改的 unified diff；change_manifest.json 使用 evolution.change_manifest.v2，记录实际选择的改动、原因、风险及本地检查。未发生的改动不得写成已完成。", "",
      "产物全部完成后，在结果文件中返回这些实际文件引用：", "", json({ artifacts: tuneFiles }), "",
      "### 结果分析（review）", "",
      "input 提供 workspace、round、objective、input_spec、spec_source、acceptance、optimization_bench_result、validation_aggregate、tune_report、diff、evolution_history 和 output_files。仅使用提供的 Test 汇总，不读取逐条测试案例、评分答案或 rubric。解释本轮结果并提出后续策略，不改变 Bench 已作出的验收决定。", "",
      "只将以下业务对象写入 input.output_files 指定的 review_decision.json；acceptance_decision 沿用 input.acceptance.bench_decision：", "",
      json({ schema_version: "evolution.review_decision.v2", round_id: 1, acceptance_decision: "输入中的实际 Bench 决定",
        summary: "根据实际结果解释变化", confidence: "medium", hypotheses: [{ hypothesis_id: "HYP-001",
          failure_signature: "有证据的问题类型", status: "suspected", claim: "待验证的解释",
          alternative_causes: ["其他可能原因"], disambiguation_signal: "可观察的区分证据" }], direction_decisions: [] }), "",
      "hypotheses.status 可用 suspected、testing、supported、falsified；direction_decisions 的每项 decision 可用 keep、strengthen、split、freeze、reject、defer。不要写具体补丁或逐条测试答案。文件完成后，在结果文件中返回引用：", "", json({ artifacts: reviewFiles }), "",
      "平台保留原有准备、基线、Bench、验收、回滚、Spec 渲染、打包和上报。当前 Skill 只完成这两个业务调用，不生成最终 Stage 的 metrics、baseline 或评测运行标识。", "",
    ]),
    "## 用户交互", "",
    "需要用户确认或补充材料时，将以下结构写入结果文件并结束本次调用。原始 Markdown 可以放在结果文件同目录，以 contentFile 引用；也可以直接使用 content。", "", json({ hitl: true, question }), "",
    "平台将 contents 每项单独分页、questions 每页最多展示 5 题；业务不限制一次提问数量，不自行分页。选择题支持 single_choice、multiple_choice，文字题支持 short_text、long_text；选择题附带补充意见框，选择其他时必须填写。", "",
    "回答后平台恢复同一业务调用，保留 phase、input、output_requirements，并增加 hitl。question 是原问题，answer.answers 按问题 id 提供本次回答，history 保存本次业务调用的完整交互：", "",
    json({ hitl: { question, answer, history: [{ question, answer }] } }), "",
    ...textInteractionGuide(true),
    ...(diagnose ? [
      "完成时可返回普通业务对象；需要 Stage 完成后征求下一轮反馈时，返回 { hitl: false, result: 本次业务对象, loop: ... }：", "",
      json({ loop: { action: "request_feedback", prompt: "请确认本轮结果或提供下一轮意见", accepts: { text: true, files: [".txt", ".jsonl", ".xlsx"] } } }), "",
      "平台先完成当前 Stage 原有后续处理，再展示本轮结果并收集反馈。用户继续后创建新 Step，完整执行当前 Stage；输入增加 loop.round、loop.previous_result 和 loop.user_feedback。previous_result 是上一轮完整 Stage 结果；user_feedback.files 的每项提供 name、content_type、size、sha256、path，文件已由平台下载校验。业务不自行创建 Loop。", "",
      feedbackRoundGuide, "",
    ] : ["完成当前业务调用后，返回对应的业务对象，由平台继续原有优化流程。", ""]),
    "## 开发与交付", "",
    "根据以上实际输入输出开发 SKILL.md，保留自身业务逻辑；需要时可附业务辅助文件。将 SKILL.md 及辅助文件打包为 ZIP，上传到平台。无需添加平台 adapt 脚本。", "",
  ].join("\n");
}

// Describe the native Plan business seams. Document rendering, publication and
// final Stage output remain Handler responsibilities.
function planBusinessGuide(stage: OfficialStageDefinition, skillFlow: boolean): string {
  const formQuestion = {
    format: "form", title: "确认本次处理范围", description: "请确认后再继续生成方案。",
    contents: [{ id: "diagnosis", title: "诊断材料", format: "markdown", content: "## 已确认的问题\n\n执行前缺少范围确认。" }],
    questions: [{ id: "scope", type: "long_text", title: "允许处理的范围", required: true,
      placeholder: "例如：只补充执行前的范围检查", validation: { minLength: 1, maxLength: 4000 } }],
  };
  const formAnswer = { answers: { scope: { value: "只补充执行前的范围检查" } } };
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
      input: { source_path: "本次真实诊断资料文件的实际路径", source_sha256: "该文件的实际校验值", workspace_root: "本次独立工作目录的实际路径", allowed_targets: ["目标 Skill 副本的实际路径"], validation_error: "" },
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
      key: "direct_goal", name: "直接目标分析",
      input: { goal: "根据用户目标改进目标 Skill", workspace_root: "本次平台提供的工作目录", allowed_targets: ["允许处理的目标路径"], validation_error: "" },
      requirements: "根据用户目标、已检查的工作目录和本次完整字段要求，返回目标分析、前瞻案例及改进位置。",
      notes: "没有上游诊断资料、直接按目标规划时调用。保留用户目标与约束；前瞻案例不冒充历史会话；只分析允许范围，不修改目标文件。",
      output: {
  "goal_analysis": {
    "raw_goal": "根据用户目标改进目标 Skill",
    "task_scope": "用户希望新增或增强的任务能力范围",
    "desired_outcome": "用户明确期望达到的结果",
    "required_capabilities": [
      "完成目标所需能力"
    ],
    "quality_requirements": [
      "可验证的质量要求"
    ],
    "constraints": [
      "用户明确提供的约束"
    ],
    "requested_deliverables": [
      "skill 或其他明确交付物"
    ]
  },
  "prospective_cases": [
    {
      "case_id": "goal-case-001",
      "case_type": "prospective",
      "query": "可独立回放的完整用户任务",
      "scenario": "该场景为什么能验证用户目标",
      "expected_behavior": "成功行为",
      "forbidden_behavior": [
        "不可接受行为"
      ],
      "success_criteria": [
        "可由 LLM judge 判断的成功标准"
      ],
      "scoring_hints": [
        "个性化评分关注点"
      ],
      "failure_mode": "missing_skill_capability"
    }
  ],
  "discovery": {
    "analysis_summary": {
      "diagnosed_problem": "本次没有 Diagnose；概括用户想补齐的能力",
      "environment_root_cause": "当前 workspace 中的能力缺口",
      "optimization_strategy": "后续 patch loop 的窄范围策略"
    },
    "case_findings": [
      {
        "case_id": "goal-case-001",
        "case_type": "prospective",
        "failure_mode": "missing_skill_capability",
        "symptom": "若不优化，预期无法满足的行为",
        "evidence": [
          "来自 --goal、已检查的创建范围和只读参考文件"
        ],
        "inspected_files": [
          "skills/skills-local/example/SKILL.md"
        ],
        "environment_analysis": "基于已检查文件说明能力缺口",
        "optimization_ideas": [
          "可执行优化方向"
        ],
        "root_cause_hypothesis": "goal 与 workspace 缺口的连接",
        "confidence": "high|medium|low"
      }
    ],
    "target_findings": [
      {
        "path": "skills/skills-local",
        "target_type": "creation_scope",
        "reason": "已检查且足够窄的现有目录，可作为后续创建新 Skill 的安全范围",
        "current_gap": "该范围内尚无满足用户目标的 Skill",
        "proposed_change": "后续 patch loop 在该目录下创建 planned_deliverables",
        "related_case_ids": [
          "goal-case-001"
        ],
        "failure_modes": [
          "missing_skill_capability"
        ],
        "confidence": "high|medium|low"
      }
    ],
    "merged_targets": [
      "skills/skills-local"
    ],
    "reference_files": [
      "skills/skills-local/example/SKILL.md"
    ],
    "planned_deliverables": [
      {
        "path": "skills/skills-local/new-skill/SKILL.md",
        "operation": "create",
        "creation_scope": "skills/skills-local",
        "deliverable_type": "skill",
        "reason": "用户明确要求创建的新 Skill 主文件"
      }
    ],
    "forbidden_boundary_check": {
      "passed": true,
      "checked": [
        "no judge/scorer changes",
        "no generated artifacts",
        "no secrets or production credentials",
        "targets are inside workspace_root and narrow"
      ],
      "notes": "边界检查说明"
    },
    "warnings": []
  }
},
    },
  ];
  return [
    `# ${stage.name} · 完整替换 Skill 开发任务`, "",
    "## 背景与本次开发目标", "", stageDevelopmentBackground(stage, "replace", skillFlow), "",
    "## 输入与输出", "", businessBoundary, "",
    "运行时，平台会向执行 Agent 提供本次输入文件和结果文件的具体位置。读取输入文件，完成处理后，将结果以 JSON 格式写入结果文件。", "",
    "平台每次提供当前要完成的业务分析，以及对应的完整数据和要求。同一个规划 Skill 根据 phase 处理本次工作，只返回这一项的业务结果，不一次返回整个规划环节结果。", "",
    "输入包含 phase（本次工作类型）、input（本次数据）、output_requirements（本次完整输出要求）。下面示例说明格式；实际字段与约束以本次输入中的完整要求为准。路径、案例、模板、结论和数值必须以真实输入为准，不可照抄示例。", "",
    ...phases.flatMap((phase) => [
      `### ${phase.name}（${phase.key}）`, "", phase.notes, "",
      "输入示例：", "", json({ phase: phase.key, input: phase.input, output_requirements: phase.requirements }), "",
      "输出示例：", "", json(phase.output), "",
    ]),
    "只写本次要求的 JSON 业务对象，不另外添加结果外层。输入或文件不可用时如实说明，不猜替代路径，也不编造成功结果。", "",
    "## 平台提供的能力", "",
    "平台保留 OpenClaw 执行管理、目标路径验证、训练与测试划分、目标与方案文档渲染、评测模板渲染和发布、已发布资源核验、结果校验及后续流程。规划 Skill 只提供当前业务分析，不负责这些平台操作。", "",
    "平台在各次业务处理后沿用默认规划逻辑，组装目标、方案、评测案例和评测域。最终环节结果由平台生成；中间业务结果按本次 output_requirements 校验，不套用整个环节的最终结果格式。平台完成发布后取得真实评测域和模板引用，规划 Skill 不生成或上传这些发布标识。", "",
    ...(skillFlow ? ["单段集成测试由平台提供独立测试副本及本次真实诊断资料；这里只分析目标 Skill 的独立副本，不修改原始 Skill。", ""] : []),
    "### 请求用户补充信息", "",
    "需要结构化答案时，只将交互写入结果文件并结束本次处理；不要同时写业务结果。此时 format 为 form；contents 是原样展示的只读 Markdown 材料，questions 是问题列表。问题 type 可用 single_choice、multiple_choice、short_text、long_text；选择题提供 options，文字题不提供 options。", "", json({ hitl: true, question: formQuestion }), "",
    "平台负责将 contents 每项单独分页、将 questions 每页最多展示 5 题，并校验和保存回答。恢复输入保留原 phase、input、output_requirements，并增加 hitl：question 是原交互，answer.answers 按问题 id 提供平台已校验的回答，history 是本次工作累计的问题与回答。", "",
    json({ hitl: { question: formQuestion, answer: formAnswer, history: [{ question: formQuestion, answer: formAnswer }] } }), "",
    ...textInteractionGuide(true),
    "读取回答后继续本次工作，完成时返回对应业务结果。不自行等待、轮询或调度下一步。", "",
    "原始 Markdown 材料可写到结果文件同目录的 UTF-8 文件，用 contents 项的 contentFile 替代 content；平台读取原文并分页展示。不要同时提供这两个字段。", "",
    "### 本轮完成后请求反馈", "",
    "如需用户确认整轮规划结果，将本次结果写为 { hitl: false, result: 本次业务对象, loop: ... }。平台先完成本轮原有处理并展示最终规划结果，再收集反馈；业务 Skill 不调用平台接口。", "",
    json({ loop: { action: "request_feedback", prompt: "请确认本轮规划或提供下一轮意见", accepts: { text: true, files: [".txt", ".jsonl", ".xlsx"] } } }), "",
    "下一轮继续收到原始输入，并增加 loop.round、loop.previous_result（上一轮完整规划结果）、loop.user_feedback（本轮用户意见及附件）。附件含 name、content_type、size、sha256、path；path 是平台下载并校验后的本地文件路径，供业务按需读取。需要在本轮过程中补充信息时，仍使用上面的 HITL。", "",
    feedbackRoundGuide, "",
    "## 开发与交付", "",
    "根据以上说明，将本文件替换为实现当前处理逻辑的 `SKILL.md`。需要时，可以在同目录下增加脚本、参考说明等辅助文件。", "",
    "完成开发后，将 `SKILL.md` 及辅助文件打包为 ZIP，并提示用户将该 ZIP 上传到平台。", "",
  ].join("\n");
}

export function stageDevelopmentGuide(stage: OfficialStageDefinition, mode: StageExtensionMode, flow?: EvolutionFlowKey): string {
  if (stage.stage === "plan" && mode === "replace") return planBusinessGuide(stage, flow === "skill_evolution");
  if (mode === "replace" && (stage.stage === "diagnose" || stage.stage === "optimize")) return nativeAnalysisGuide(stage, flow === "skill_evolution");
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
    diagnose: "`diagnose_goal` 是本次诊断重点；`session_source` 指定真实会话来源，mode 为 local 或 service_export。诊断目标不是会话内容；来源字段描述可用会话资源，是否使用及如何使用由当前 Skill 的业务逻辑决定。",
    hardening: "`target_skill` 描述平台提供的独立候选资源；`goal` 是可选的额外加固目标。不得修改候选目录之外的内容。",
    plan: "`goal` 是改进目标与限制；`diagnose_result` 是上游诊断的最终结果，包含 diagnosis 和 cases。直接按目标进化时不提供诊断结果。",
    optimize: "`round` 是当前轮次；`plan_result` 是上游规划冻结的目标、方案和评测选择。第二轮及以后还会提供 `previous_round_result`，结构与优化结果相同。",
  };
  const resultNotes: Record<StageKey, string> = {
    diagnose: "必须包含 diagnosis 和 cases。diagnosis 的 summary、issues 必填；每个 issue 的 code、title、severity、caseCount、suggestion 必填。cases 的 total、goodCount、badCount、items 必填；每个案例的 caseId、type 必填，type 为 good 或 bad，summary 可选。数量应与真实案例一致。",
    hardening: "必须包含 summary 和 changed。summary 如实说明本次完成的加固；changed 表示是否修改候选 Skill。changed_files 中每个路径都相对于输入 target_skill.path 指定的 Skill 根目录，使用 / 分隔，只列实际发生变化的文件。例如修改 <target_skill.path>/SKILL.md，返回 SKILL.md；修改 <target_skill.path>/references/rules.md，返回 references/rules.md。不要填写绝对路径、.. 或工作区中的 skills/skills-local/技能名/ 前缀。没有修改时返回 changed: false、changed_files: []。",
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
    "## 输入与输出", "", businessBoundary, "",
    "运行时，平台会向执行 Agent 提供本次输入文件和结果文件的具体位置。读取输入文件，完成处理后，将结果以 JSON 格式写入结果文件。", "",
    "### 输入", "", "以下示例说明数据格式；标识、路径和业务内容以本次实际输入为准。", "", json(input), "",
    `\`task\` 是平台提供的任务信息。${hardeningFlow ? "Skill 加固任务的 task_type 为 hardening" : "完整任务的 task_type 为 full"}，单段集成测试为 stage_test。`, "",
    ...(!hardeningFlow && stage.stage !== "optimize" ? [`${skillFlow ? "目标 Skill" : "Bot"}单独诊断任务的 task_type 为 diagnose，执行诊断及按任务选择启用的规划，不进入优化环节。`, ""] : []),
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
    "需要展示材料或收集结构化答案时，将交互写入结果文件并结束本次处理。此时 format 为 form；contents 是只读 Markdown 材料列表，questions 是问题列表。contents 和 questions 至少提供一项；问题 type 可用 single_choice、multiple_choice、short_text、long_text。选择题提供 options，文字题不提供 options。", "", json({ hitl: true, question: {
      format: "form", title: "确认本次处理范围", description: "请确认后再继续处理。",
      contents: [{ id: "current-state", title: "目标现状", format: "markdown", content: "## 当前情况\n\n保留已经确认的业务语义。" }],
      questions: [
        { id: "scope", type: "single_choice", title: "处理范围", required: true, options: [
          { value: "minimal", label: "最小必要范围", description: "只处理当前问题", recommended: true },
          { value: "related", label: "相关范围", description: "同时处理直接相关问题" },
        ] },
        { id: "notes", type: "long_text", title: "补充说明", required: false, placeholder: "选填", validation: { maxLength: 4000 } },
      ],
    } }), "",
    "平台负责生成交互标识、将 contents 每项单独分页、将 questions 每页最多展示 5 题、校验选项与必填项，并保存回答；结构化表单无需 tag，也无需返回 HTML。每道选择题由平台附带一个补充意见框，选择“其他”时该输入框必填。", "",
    "较长的只读 Markdown 可保存为 UTF-8 文件，在 question.contents 的对应项用 contentFile 替代 content。路径以结果 JSON 所在目录为基准，允许其子目录，不得越出该目录；文件必须存在且不超过 1 MiB。contentFile 与 content 不能同时提供。平台读取原文并分页展示，无需业务编写分页或 JSON 转义脚本。", "",
    json({ hitl: true, question: { format: "form", title: "业务材料", contents: [{ id: "material", title: "材料说明", format: "markdown", contentFile: "material.md" }], questions: [] } }), "",
    "summaryFile 仅可替代业务结果顶层原本允许的 summary 字段，文件规则同 contentFile；平台读取后还原 summary，再按正常输出 Contract 校验。带 hitl: false 的结果外层时，引用放在 result.summaryFile。不能放到 result_patch 或其他嵌套对象，也不能给不接受 summary 的输出增加该字段。", "",
    ...((mode === "preprocess" || (stage.stage === "hardening" && mode === "replace"))
      ? [json({ summaryFile: "summary.md", changed: false, changed_files: [] }), ""] : []),
    "平台收集回答后继续调用当前 Skill。下一次输入会增加 human_input.answers，并按问题 id 提供本次平台已校验的 value；选择题还会提供 comment。human_input.history 按发生顺序保留当前 Step 已完成的全部交互，每项包含原 question 和对应 answer。例如：", "", json({ human_input: {
      answers: { scope: { value: "minimal", comment: "保留已确认的业务语义" }, notes: { value: "重点检查异常分支" } },
      history: [{
        question: { format: "form", title: "确认本次处理范围", contents: [], questions: [{ id: "scope", type: "single_choice", title: "处理范围", required: true, options: [{ value: "minimal", label: "最小必要范围" }] }] },
        answer: { answers: { scope: { value: "minimal", comment: "保留已确认的业务语义" } } },
      }],
    } }), "",
    "读取回答后继续处理，完成时按上一节写入业务结果。不要自行等待、轮询或调度下一步。", "",
    ...textInteractionGuide(false),
    "### 基于用户反馈继续处理（可选）", "",
    "当前 Skill 完成一次有效处理后，如果需要用户根据本轮结果提出意见并再次处理，可以在完整业务结果外增加 loop。未返回 loop 时，本次结果按正常完成处理。", "",
    json({ hitl: false, result: output, loop: {
      action: "request_feedback", prompt: "请确认当前结果；如需继续调整，请补充意见或上传资料。",
      accepts: { text: true, files: [".jsonl", ".xlsx", ".txt"] },
    } }), "",
    "result 必须先满足本 Stage 的正常输出要求；loop 只声明是否向用户收集下一轮反馈。text 表示是否接收文字，files 列出允许上传的文件扩展名，至少启用一种反馈形式。写入后结束本次处理，不自行等待或启动下一轮。", "",
    "用户选择继续后，平台会创建新的 Step，再次调用同一个 Stage 实现。正常输入字段保持不变，并增加 loop：", "",
    json({ loop: {
      round: 2,
      previous_result: output,
      user_feedback: {
        text: "请进一步收紧异常输入的处理规则",
        files: [{ name: "补充案例.jsonl", content_type: "application/x-ndjson", size: 1024, sha256: "文件内容的 SHA-256", path: "平台下载后的本地文件路径" }],
      },
    } }), "",
    "round 是当前反馈轮次；previous_result 是上一轮已经校验通过的完整业务结果；user_feedback 只包含本次用户提交的文字和文件。文件由平台下载并校验，path 指向可按业务需要读取的本地文件。没有文字或文件时，对应字段为空或省略。", "",
    feedbackRoundGuide, "",
    "平台只会重新执行当前 Stage 实现，不允许 Skill 编排其他 Stage 或创建任务。如果下一轮仍需反馈，可再次返回 result 和 loop；需要跨轮保留的业务信息，应由当前 Skill 放在本轮 result 中，供下一轮通过 previous_result 读取。", "",
    "## 开发与交付", "",
    "根据以上说明，将本文件替换为实现当前处理逻辑的 `SKILL.md`。需要时，可以在同目录下增加脚本、参考说明等辅助文件。", "",
    "完成开发后，将 `SKILL.md` 及辅助文件打包为 ZIP，并提示用户将该 ZIP 上传到平台。", "",
  ].join("\n");
}
