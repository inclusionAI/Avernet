# Evolution Direction Pool v2

这是自进化 spec 的**问题归因矩阵 + 优化方向池**合一版本。

- **矩阵层**：`模块 × 问题类型`，负责回答“问题属于哪里”。
- **方向层**：`direction_id`，负责回答“这类问题怎么改”。
- 每条方向都挂到一个明确的 `TC.<模块>.<问题类型>` 上，便于归因、聚类、选方向和回滚。

当前范围限定：

- 可优化：skill、md 配置、MCP 调用层说明/配置。
- 不优化：MCP server/tool 本体实现、bench/case/scoring、objective、历史 artifact。

---

## 0. 使用原则

### 0.1 两层结构

```text
case / transcript / judge note
  → TC.<模块>.<问题类型>
    → direction_id
      → spec-vN.md 中的 active optimization directions
```

### 0.2 归因优先级

当一个失败 case 看起来能归到多个方向时，优先选择最靠近根因的一层：

```text
意图/路由识别失败
  → 执行流程失败
    → 参数/输入失败
      → 工具错误/异常处理失败
        → 结果消费/后处理失败
          → 最终表达质量失败
```

### 0.3 反过拟合约束

- 不写入 validation case 的具体答案、固定 query、固定参数值或固定输出。
- 不弱化 objective 来换取短期分数。
- 不修改 benchmark、scoring、case、历史 artifact。
- 不修改 MCP server/tool 本体实现。
- 优先小改动：每轮只修 1-3 个高置信方向。

---

## 1. 模块 × 问题类型矩阵

> 这是归因索引，不是修改动作列表。`TC` 代码用于对齐 case、direction 和 spec。

| 模块 | CODE | NETWORK | INSTRUCTION | CONFIG | PERMISSION | DATA | PERFORMANCE | STABILITY |
|---|---|---|---|---|---|---|---|---|
| 引擎 Engine | TC.ENGINE.CODE | TC.ENGINE.NETWORK | TC.ENGINE.INSTRUCTION | TC.ENGINE.CONFIG | TC.ENGINE.PERMISSION | TC.ENGINE.DATA | TC.ENGINE.PERFORMANCE | TC.ENGINE.STABILITY |
| Skill | TC.SKILL.CODE | TC.SKILL.NETWORK | TC.SKILL.INSTRUCTION | TC.SKILL.CONFIG | TC.SKILL.PERMISSION | TC.SKILL.DATA | TC.SKILL.PERFORMANCE | TC.SKILL.STABILITY |
| MCP | TC.MCP.CODE | TC.MCP.NETWORK | TC.MCP.INSTRUCTION | TC.MCP.CONFIG | TC.MCP.PERMISSION | TC.MCP.DATA | TC.MCP.PERFORMANCE | TC.MCP.STABILITY |
| 记忆 Memory | TC.MEMORY.CODE | TC.MEMORY.NETWORK | TC.MEMORY.INSTRUCTION | TC.MEMORY.CONFIG | TC.MEMORY.PERMISSION | TC.MEMORY.DATA | TC.MEMORY.PERFORMANCE | TC.MEMORY.STABILITY |
| 模型 Model | TC.MODEL.CODE | TC.MODEL.NETWORK | TC.MODEL.INSTRUCTION | TC.MODEL.CONFIG | TC.MODEL.PERMISSION | TC.MODEL.DATA | TC.MODEL.PERFORMANCE | TC.MODEL.STABILITY |
| Session / Context | TC.SESSION_CONTEXT.CODE | TC.SESSION_CONTEXT.NETWORK | TC.SESSION_CONTEXT.INSTRUCTION | TC.SESSION_CONTEXT.CONFIG | TC.SESSION_CONTEXT.PERMISSION | TC.SESSION_CONTEXT.DATA | TC.SESSION_CONTEXT.PERFORMANCE | TC.SESSION_CONTEXT.STABILITY |
| 工具 Tool | TC.TOOL.CODE | TC.TOOL.NETWORK | TC.TOOL.INSTRUCTION | TC.TOOL.CONFIG | TC.TOOL.PERMISSION | TC.TOOL.DATA | TC.TOOL.PERFORMANCE | TC.TOOL.STABILITY |
| 知识 Knowledge | TC.KNOWLEDGE.CODE | TC.KNOWLEDGE.NETWORK | TC.KNOWLEDGE.INSTRUCTION | TC.KNOWLEDGE.CONFIG | TC.KNOWLEDGE.PERMISSION | TC.KNOWLEDGE.DATA | TC.KNOWLEDGE.PERFORMANCE | TC.KNOWLEDGE.STABILITY |
| Workflow / Planner | TC.WORKFLOW_PLANNER.CODE | TC.WORKFLOW_PLANNER.NETWORK | TC.WORKFLOW_PLANNER.INSTRUCTION | TC.WORKFLOW_PLANNER.CONFIG | TC.WORKFLOW_PLANNER.PERMISSION | TC.WORKFLOW_PLANNER.DATA | TC.WORKFLOW_PLANNER.PERFORMANCE | TC.WORKFLOW_PLANNER.STABILITY |

### 1.1 快速判定口径

- **CODE**：实现、解析、拼装、状态机、适配逻辑错。
- **NETWORK**：握手、连接、DNS、SSE、链路断开。
- **INSTRUCTION**：Prompt、SOP、业务规则、流程顺序、能力描述错。
- **CONFIG**：地址、版本、开关、注册、采样、传输配置错。
- **PERMISSION**：鉴权、ACL、身份、租户、操作权限不足。
- **DATA**：输入、上下文、响应、schema、引用载荷、状态数据错误。
- **STABILITY**：偶发失败、抖动、重连、重复推进、结果漂移。
- **PERFORMANCE**：延迟、吞吐、超时、资源、队列、Token 成本。

---

## 2. 优化方向池

### 2.1 Skill Directions

| Direction ID | TC Code | Failure Mechanism | Evidence Pattern | Primary Change Surface | Expected Effect | Boundary / Anti-overfitting Guard |
|---|---|---|---|---|---|---|
| SKILL-TRIGGER-001 | TC.SKILL.INSTRUCTION | 任务语义已经命中某个专用 skill，但模型没有在早期读取/遵循该 skill，导致先走通用工具或普通回答。 | transcript 中出现“应使用该 skill”的用户意图，但没有读取 SKILL.md，或读取发生在错误路径之后。 | skill description / SKILL.md 入口说明 | 提高该 skill 的早期触发率，减少错误路径启动。 | 只强化通用触发语义和读取时机；不写入具体 case query 或答案。若问题是全局 skill-first 原则缺失，归因 TC.WORKFLOW_PLANNER.INSTRUCTION。 |
| SKILL-TRIGGER-002 | TC.SKILL.INSTRUCTION | 用户表达变体未覆盖，导致同一类任务在不同说法下触发不稳定。 | 某些同义表达触发 skill，另一些同义表达没有触发；failure notes 指向 intent paraphrase miss。 | skill description | 扩大语义覆盖面，提高 paraphrase robustness。 | 只增加意图类别/表达族，不枚举 validation 样本原句。若是已识别但没优先使用，归因 SKILL-TRIGGER-001。 |
| SKILL-BOUNDARY-001 | TC.SKILL.INSTRUCTION | skill 适用/不适用边界不清，导致误触发、漏触发或与相近 skill 冲突。 | transcript 显示任务被路由到错误 skill，或相近 skill 之间选择不稳定。 | skill description / SKILL.md “适用/不适用”段落 | 降低误触发和冲突触发，提高路由稳定性。 | 必须同时写清 include 和 exclude；不能通过扩大边界吞掉其他 skill 的职责。 |
| SKILL-INSTRUCTION-001 | TC.SKILL.INSTRUCTION | 模型已读取 SKILL.md，但没有按内部流程执行，出现漏步骤、乱序或关键条件未判断。 | transcript 显示 SKILL.md 被读取，但执行路径与步骤说明不一致。 | SKILL.md steps / workflow | 提高流程遵循率，减少漏步骤。 | 若根因是 MCP 调用顺序，优先归因 TC.MCP.INSTRUCTION；本方向只修 skill 内流程表达。 |
| SKILL-INSTRUCTION-002 | TC.SKILL.INSTRUCTION | skill 的关键前置步骤没有被显著突出，导致首步/前置检查高频遗漏。 | 多个失败 case 都在第一个检查、初始化或准备步骤出错。 | SKILL.md checklist / quick-start | 降低首步遗漏率，提高执行一致性。 | 只把已有关键步骤 checklist 化；不要引入大范围新流程。 |
| SKILL-PARAM-001 | TC.SKILL.INSTRUCTION | skill 对参数来源、默认值、是否需要追问的规则不清，导致无意义追问、默认值未用或参数取错。 | transcript 中出现已有默认值仍追问、用户已给信息却未抽取、参数来源混乱。 | SKILL.md parameters / defaults | 提高参数填入稳定性，减少不必要追问。 | 若参数问题发生在 MCP tool schema/字段映射层，归因 TC.MCP.DATA。 |
| SKILL-FALLBACK-001 | TC.SKILL.STABILITY | skill 内没有定义工具失败、输入不足、权限缺失等情况下的降级路径，导致卡住或无说明失败。 | 工具失败后直接停止、重复尝试同一路径，或不给用户可理解解释。 | SKILL.md fallback / error handling | 提升失败场景完成率和用户可理解性。 | 若是特定 MCP 的 auth/timeout/retry/fallback，优先归因对应 TC.MCP.*。 |
| SKILL-OUTPUT-001 | TC.SKILL.INSTRUCTION | skill 的输出契约不明确，导致字段缺失、格式漂移、JSON/Markdown 混用或必要章节缺失。 | judge notes 指向格式不稳定、缺字段、输出结构不符合 skill 要求。 | SKILL.md output template | 提高输出格式一致性和可判分性。 | 本方向解决“格式契约”；若是内容质量/可读性，归因 TC.MODEL.INSTRUCTION；若是 MCP 结果没被读懂，归因 TC.MCP.DATA。 |
| SKILL-EVIDENCE-001 | TC.SKILL.INSTRUCTION | skill 没有明确证据、来源、引用或可验证性要求，导致回答缺少依据。 | final answer 缺引用、缺链接、缺来源说明，或 judge notes 指出不可验证。 | SKILL.md evidence / citation rules | 提高可验证性和可信度。 | 只要求保留来源/证据，不编造来源；若 MCP 返回结果本身未被正确消费，归因 TC.MCP.DATA。 |

### 2.2 MD Config Directions

| Direction ID | TC Code | Failure Mechanism | Evidence Pattern | Primary Change Surface | Expected Effect | Boundary / Anti-overfitting Guard |
|---|---|---|---|---|---|---|
| MD-BEHAVIOR-001 | TC.MODEL.INSTRUCTION | 全局 persona / 行为准则与 objective 不一致，导致回答风格、主动性、谨慎程度或任务完成策略偏离目标。 | 多个不同任务都表现出同类行为偏差，而非单一 skill 或工具问题。 | SOUL.md / AGENTS.md | 让整体行为更贴近 objective。 | 不重写 objective；不为了分数弱化安全、真实性或用户意图对齐。 |
| MD-TOOL-PRIORITY-001 | TC.WORKFLOW_PLANNER.INSTRUCTION | 全局工具/skill/MCP 优先级不清，导致模型在多个可选路径中选择了低优路径。 | transcript 显示存在专用 skill/MCP，但模型优先使用通用 shell/web/search/普通回答。 | TOOLS.md / AGENTS.md | 提高工具路径选择稳定性，减少低效或错误路径。 | 若只是不知道某个 skill 何时触发，归因 SKILL-TRIGGER-002；若只是不知道某 MCP tool 选哪个，归因 TC.MCP.INSTRUCTION。 |
| MD-SKILL-FIRST-001 | TC.WORKFLOW_PLANNER.INSTRUCTION | 全局缺少“命中 skill 语义时先读 skill 定义”的原则，导致多个 skill 场景都未先读 SKILL.md。 | 多个不同 skill 的 case 都出现未读 SKILL.md 就行动。 | AGENTS.md / TOOLS.md | 提高所有 skill 的前置读取率。 | 只修全局 skill-first 规则；某个单独 skill 的触发词不足归因 SKILL-TRIGGER-002。 |
| MD-MCP-FIRST-001 | TC.WORKFLOW_PLANNER.INSTRUCTION | 全局缺少“特定任务优先使用对应 MCP”的原则，导致本应走业务系统/MCP 的任务走了泛化工具。 | 多个 MCP 场景中模型优先使用 web_search/shell/普通回答，而不是业务 MCP。 | TOOLS.md | 提高业务 MCP 使用率和数据源正确性。 | 只定义 MCP-first 场景；不强制所有任务都用 MCP。具体 server/tool 选择错误归因 TC.MCP.INSTRUCTION。 |
| MD-ERROR-HANDLING-001 | TC.WORKFLOW_PLANNER.INSTRUCTION | 全局错误处理策略不清，导致工具失败、权限缺失、输入不足时卡住、沉默或解释不可读。 | 多类工具/skill 失败后都出现不说明原因、不提供下一步、不降级。 | SOUL.md / TOOLS.md | 提高失败场景用户体验和可恢复性。 | 若错误只属于某个 skill 或某个 MCP 环节，优先归因 SKILL-FALLBACK-001 或 TC.MCP.*。 |
| MD-OUTPUT-QUALITY-001 | TC.MODEL.INSTRUCTION | 全局输出质量要求不足，导致回答结构差、可读性差、缺摘要、缺结论或不符合 objective 的呈现偏好。 | 多个非同一 skill 的输出都被 judge 指出结构/可读性/完整性问题。 | SOUL.md / output md / AGENTS.md | 提升最终回答的清晰度、完整性和稳定性。 | 不处理特定 skill 格式契约；不处理 MCP 返回结果消费失败。分别归因 SKILL-OUTPUT-001 / TC.MCP.DATA。 |
| MD-NO-ASK-DEFAULT-001 | TC.MODEL.INSTRUCTION | 全局默认值和追问策略不清，导致有默认参数或可推断信息时仍反复追问用户。 | transcript 中反复询问 userId、sceneId、时间范围等已有默认或可推断参数。 | AGENTS.md / TOOLS.md | 减少无效追问，提高任务推进率。 | 只适用于全局默认/追问原则；某 skill 自有参数规则不清归因 SKILL-PARAM-001。 |
| MD-QUERY-CLEAN-001 | TC.MODEL.DATA | 全局意图提取和 query 清洗规则不足，导致把“帮我/搜一下/查一下”等功能词带入检索或 MCP 参数。 | tool 参数中保留用户话术噪声，导致搜索/MCP 结果偏离。 | SOUL.md / TOOLS.md | 提高检索参数质量和结果相关性。 | 不写固定 query；只写通用清洗规则。若是 MCP 字段映射错，归因 TC.MCP.DATA。 |
| MD-COST-AWARE-001 | TC.WORKFLOW_PLANNER.PERFORMANCE | 全局缺少成本意识，导致重复调用、无效调用、过早调用昂贵工具或没有复用已有结果。 | transcript 中出现重复搜索、重复 MCP 调用、已知结果未复用、低价值工具链过长。 | TOOLS.md / AGENTS.md | 降低耗时/token/工具调用成本，同时保持正确性。 | 不以降成本为由跳过必要证据收集；不能牺牲 objective 对齐。 |

### 2.3 MCP Invocation Directions

| Direction ID | TC Code | Failure Mechanism | Evidence Pattern | Primary Change Surface | Expected Effect | Boundary / Anti-overfitting Guard |
|---|---|---|---|---|---|---|
| MCP-CALL-CHECK-001 | TC.MCP.CONFIG | 调用 MCP 前缺少可用性/配置检查，导致调用不存在的 server/tool 或在不可用状态下继续执行。 | transcript 中未执行可用性检查，或检查对象错误。 | skill/md MCP precheck guidance | 提高 MCP 调用前置校验稳定性。 | 只定义检查方法和检查时机；不改变 MCP 实现。若检查后选错工具，归因 TC.MCP.INSTRUCTION。 |
| MCP-CALL-SELECT-001 | TC.MCP.INSTRUCTION | 任务需要 MCP，但 server/tool 选择错误，或有专用 MCP 却没有使用。 | MCP 未调用、server 选错、tool 选错，judge notes 指向 data source/tool mismatch。 | skill/md MCP selection rules | 提高 MCP 路由准确率。 | 若 MCP 根本没被优先考虑且是全局问题，归因 MD-MCP-FIRST-001。若 server 对但 command 名错，归因 MCP-CALL-COMMAND-001。 |
| MCP-CALL-COMMAND-001 | TC.MCP.INSTRUCTION | MCP server 选对，但具体 command/tool name、调用入口或方法名选择错误。 | transcript 中 server 正确，但 tool name/command 不存在或不符合任务。 | skill/md command mapping | 降低 command/tool name 错误率。 | 不解决参数内容问题；参数错误归因 TC.MCP.DATA。 |
| MCP-CALL-PARAMS-001 | TC.MCP.DATA | MCP 参数来源、字段映射、默认值、query 清洗或类型转换错误，导致调用失败或返回无效结果。 | tool call 参数为空、字段名错、类型错、query 带噪声、默认值未填、用户信息未抽取。 | skill/md parameter mapping guidance | 提高 MCP 有效调用率和结果相关性。 | 不写固定 case 参数；只写通用映射/默认/清洗规则。若参数正确但返回后没读懂，归因 TC.MCP.DATA 的结果消费方向。 |
| MCP-CALL-SEQUENCE-001 | TC.WORKFLOW_PLANNER.INSTRUCTION | 多步 MCP 流程顺序错误，前置检查、搜索、详情获取、提交等步骤乱序或漏步骤。 | transcript 显示应先 check/list/search 再 detail/submit，但实际顺序反了或跳步。 | skill/md workflow sequence | 提高多步调用成功率和稳定性。 | 只优化调用顺序；单步参数错误归因 MCP-CALL-PARAMS-001。 |
| MCP-CALL-AUTH-001 | TC.MCP.PERMISSION | MCP 鉴权、授权、配置缺失时没有清晰识别和用户可读说明，或误判为普通失败。 | error 中包含 auth/config/permission/key missing，但最终回答没有说明或给出错误降级。 | skill/md auth error handling | 提高鉴权失败可解释性和可恢复性。 | 不写入真实凭证；不修改 auth 系统。若是 deploy 凭证未还原，记录为环境问题而非 tune 直接修 MCP 本体。 |
| MCP-CALL-TIMEOUT-001 | TC.MCP.PERFORMANCE | MCP 超时或长时间无响应后没有中止、重试上限、降级或用户说明。 | transcript 中工具 timeout 后卡住、无限等待或没有最终解释。 | skill/md timeout policy | 提高超时场景完成率和用户体验。 | 不盲目缩短所有超时；需区分可等待任务和交互任务。 |
| MCP-CALL-RETRY-001 | TC.MCP.STABILITY | MCP 可重试/不可重试错误分类不清，导致盲目重试、完全不重试或重复同样失败调用。 | transcript 中对 transient error 没重试，或对 auth/参数错误反复重试。 | skill/md retry policy | 提高 transient failure 恢复率，减少无效重试。 | 必须定义重试条件和上限；auth/参数错误不应盲重试。 |
| MCP-CALL-FALLBACK-001 | TC.MCP.STABILITY | MCP 不可用或失败后没有替代路径，或降级路径与 objective 不一致。 | MCP 失败后直接失败、沉默、或降级到不可信/不允许的数据源。 | skill/md fallback path | 提高 MCP 故障下的任务完成率。 | 降级必须显式说明可信度/数据源差异；不能绕过 objective 要求。 |
| MCP-CALL-VALIDATE-001 | TC.MCP.DATA | MCP 调用前后缺少关键参数/关键返回字段校验，导致“调用成功但结果无效”未被发现。 | tool 返回 success 但关键字段为空、结果数量为 0、缺 URL/id/title 等，模型仍当作成功。 | skill/md validation rules | 降低 silent failure，提高结果有效性。 | 不要求固定返回内容；只校验通用关键字段和空结果处理。 |
| MCP-CALL-RESULT-001 | TC.MCP.DATA | MCP 返回结果已经可用，但模型没有正确读取、引用、转述或使用关键字段。 | final answer 只给 traceId/raw JSON，遗漏 title/url/summary/status 等关键返回字段。 | skill/md result consumption rules | 提高 MCP 结果利用率和最终答案正确性。 | 如果问题是多结果排序/去重/摘要，归因 MCP-CALL-POSTPROCESS-001；如果是最终写作风格，归因 TC.MODEL.INSTRUCTION。 |
| MCP-CALL-POSTPROCESS-001 | TC.MCP.DATA | MCP 返回多条或冗余结果后，没有摘要、排序、去重、筛选、聚合或保留来源，导致输出噪声大或不可用。 | 返回列表直接 dump、重复项未去重、相关性排序差、没有提炼 top results。 | skill/md postprocess rules | 提高多结果场景可读性、相关性和可验证性。 | 不改变 MCP 返回内容；只规定通用后处理逻辑。若根本没正确读取返回字段，归因 MCP-CALL-RESULT-001。 |

---

## 3. Direction 归因示例

| Failure Observation | Preferred TC Code | Preferred Direction | Reason |
|---|---|---|---|
| 用户说“帮我搜一下 X”，bot 没读 web-search skill，直接普通回答。 | TC.SKILL.INSTRUCTION | SKILL-TRIGGER-001 | 专用 skill 应触发但未早期触发。 |
| “帮我搜一下”能触发，“查查这个看看”不能触发。 | TC.SKILL.INSTRUCTION | SKILL-TRIGGER-002 | 同一意图的表达变体覆盖不足。 |
| 多个 skill 场景都没先读 SKILL.md。 | TC.WORKFLOW_PLANNER.INSTRUCTION | MD-SKILL-FIRST-001 | 全局 skill-first 原则缺失。 |
| 多个业务 MCP 场景都没有优先使用 MCP，而是走 web search / shell / 普通回答。 | TC.WORKFLOW_PLANNER.INSTRUCTION | MD-MCP-FIRST-001 | 全局 MCP-first 原则缺失。 |
| 只有某个任务中 MCP 应该使用但 server/tool 选错，其他 MCP 场景正常。 | TC.MCP.INSTRUCTION | MCP-CALL-SELECT-001 | 局部 MCP server/tool 选择错误。 |
| MCP server 选对了，但 tool name 写错。 | TC.MCP.INSTRUCTION | MCP-CALL-COMMAND-001 | server 正确、command 错误。 |
| 多类搜索/检索任务的 query 都保留“帮我/搜一下”等用户话术噪声。 | TC.MODEL.DATA | MD-QUERY-CLEAN-001 | 全局 query 清洗规则缺失。 |
| MCP tool 调用参数中的 query/字段映射错误，但全局 query 规则在其他场景正常。 | TC.MCP.DATA | MCP-CALL-PARAMS-001 | 局部 MCP 参数构造错误。 |
| MCP 返回了结果，但最终答案只给 traceId。 | TC.MCP.DATA | MCP-CALL-RESULT-001 | 返回结果消费失败。 |
| MCP 返回 20 条结果，最终原样 dump，重复且无排序。 | TC.MCP.DATA | MCP-CALL-POSTPROCESS-001 | 后处理失败。 |
| 鉴权失败后回答“工具不可用”，没有解释缺授权。 | TC.MCP.PERMISSION | MCP-CALL-AUTH-001 | auth/config 错误说明不足。 |
| 工具 timeout 后一直卡住。 | TC.MCP.PERFORMANCE | MCP-CALL-TIMEOUT-001 | 超时策略缺失。 |

## 4. Active Direction 输出要求

当 `clawevolve-review` 从本池选择 active directions 时，`spec-vN+1.md` 中每个 direction 至少写清：

```md
| Direction ID | TC Code | Priority | Why This Round | Expected Effect |
|---|---|---|---|---|
| MCP-CALL-PARAMS-001 | TC.MCP.DATA | high | validation 中 4/9 个失败 case 的 tool call 参数存在 query 未清洗或字段映射错误 | 提高 MCP 有效调用率，减少参数导致的空结果/错结果 |
```

`spec_update_report.md` 中应额外说明：

- evidence：来自哪些 case / judge notes / transcript pattern。
- confidence：高/中/低。
- anti-overfitting check：为什么该方向是通用修复，而不是针对单个 validation case。
- rejected directions：高频但本轮没有激活的方向，以及未激活原因。
