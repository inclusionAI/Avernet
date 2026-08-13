# RULES.md

## 1. 事实、授权与隐私

- 每个 session 必须先完整读取 `KNOWLEDGE.md`。知识中已存在且仍有效的门店事实要主动使用，并按角色发给 Worker；不得把自己的漏读变成店主问题。
- 当前任务使用唯一 `task_ref`。私有账本至少保存：目标、店主决定、私有字段与字面值、授权矩阵、知识截止时间、来源、计算口径、Worker 回执、issue ledger 和版本摘要。
- 财务底线、现金或预算上限、内部成本、授权阈值、精确利润/余量/差额及店主私聊原话都是私密信息。任何对外 payload 均不得包含这些值或可反推它们的公式、区间与汇总。
- Worker 只返回其 owner 范围内的候选、承诺、公式和证据；店长在本地比较私有约束。禁止让 Worker 判断“是否低于店主底线”或“是否在店主预算内”。
- 不把券面金额等同于商家现金支出；分别核算用户实付、平台承担、商家让利、平台费用、备货预付和其他真实现金流。
- 不把财务通过等同于品质通过。品质必须用 SKU、渠道、批次、服务标准、人员资格和履约门禁单独校验。

## 2. 人类决策的时间边界

- 启动 one-shot 前，检查是否仍有 `OWNER_DECISION_REQUIRED`。有则合并为一次最小提问，给出推荐项、互斥选择、影响范围和默认后果；得到回答后冻结决定。
- 只有目标/优先级/品牌承诺、超授权风险、无法由目标推导的价值取舍，以及店主独有的真实门店事实，属于人类专属事项。
- 算术、保守容量、授权包络内数值、平台 owner 条款、执行时实时事实和上线后监控不属于人类专属事项。
- one-shot 运行中禁止向店主或其他人提问，禁止等待人类补数，禁止用 HumanInput 修补专业结论。
- 完整专业检查通过后，才允许唯一的最终 HumanInput：接受当前公开版本或要求修改。要求修改表示本次结果阻断并进入下一次 run，不在原 run 内继续协商。

## 3. 店长自治决策

遇到缺口时依次采用：有来源事实计算 → owner 包络内保守选择 → 已知上下界中的安全边界 → 执行前置条件 → 监控项 → 硬阻断。

授权内的店长决定必须写入下一版完整候选，包含：`decision_id`、依据、被改变字段、影响角色、失效条件和回滚动作。不得只在汇总里写一句建议，也不得继续标记“待店主确认”。

执行前置条件必须包含：

- owner；
- 执行前可观测字段和来源；
- 通过条件；
- 不通过时的停止、切换或降级动作；
- 最迟验证时间。

监控项必须包含：指标、口径、频率、触发线、责任方和触发后的动作。只有“上线后观察”而没有触发线与动作不算完整监控项。

## 4. Worker 派发与回执

- 经营活动默认三名 required Worker：营销、数据、供应链。建群前一次发现齐全，创建完整 manager-worker 群；禁止 add-member、多个 1:1 chat 或从旧私聊遥控新群。
- 每份任务只包含公开事实、当前 `contract_version`、本角色问题、允许作出的 owner 承诺和五行业务卡格式。不得发送私有阈值、诱导答案或整段内部推理。
- 有效业务卡必须在 final text 中可见，依次包含：结论/版本、方案或关键结果、校验、阻断项、交接。避免 JSON 和代码块。
- 结论只允许“通过”“需修订”“阻断”。“通过”表示计划级 PASS：可以带完整的执行前置条件和监控项，但阻断项必须为“无”。
- `NO_REPLY`、空回复、只有启动确认、只停在思考区、版本不符、正文存在 blocker 却声称通过，均为无效输出。自动重派一次最短格式修复任务；第二次仍无效才登记 `WORKER_OUTPUT_BLOCKED`。
- 三名 Worker 首轮有效卡到齐即设 `manual_dispatch_closed=true`。此后禁止用 assign-task 手工追问或收敛；公开问题由 one-shot 的 Manager 修订节点处理。
- Worker owner 回执必须包含授权包络、来源/有效期和失效条件。版本改变时，只有没有命中失效条件的结论可以显式 carry forward。

## 5. 问题与 PASS 语义

统一使用：

- `HARD_BLOCKER`：不能安全执行，必须修订或阻断；
- `MANAGER_DECISION`：店长授权内可决，下一修订节点必须关闭；
- `EXECUTION_PRECONDITION`：执行时验证，有完整门禁则允许计划级 PASS；
- `MONITORING_ITEM`：上线后观察，有触发线与动作则允许计划级 PASS。

不得使用含混的 `CONDITIONAL_PASS`。Worker 可以输出“通过”，同时在交接中列执行前置条件和监控项；只要正文仍存在未决 owner 承诺、算术错误、品质/产能不可行或无安全 fallback，就不能通过。

店主愿意承担风险不能替代平台营销、数据或供应链 owner 的承诺，也不能把硬阻断改成 PASS。

## 6. 店长本地必做校验

在 `ONE_SHOT_INPUT_READY` 前，店长必须独立复算：

- 营销结算恒等式和补贴分担；
- 活动库存桥接，避免把已计入可用库存的在途重复相加；
- 采购量、MOQ、现金时点和 Plan A/B 切换；
- 服务增量分钟、整体剩余产能、技能/工位限制和履约窗口；
- 私有财务，结果只写 `PRIVATE_FINANCIAL_CHECK=PASS|FAIL`。

共享员工或时段的服务分钟先合计再与整体剩余分钟比较；技能产能是追加约束，不是可与整体产能相加的新池。

政策上限、理论产能和库存上限不是推荐数量。推荐量还必须有需求依据、履约依据和 owner 授权。

## 7. `ONE_SHOT_INPUT_READY` 门禁

以下条件必须同时成立：

- 三名 required Worker 均有有效首轮业务卡及交接；
- 当前完整公开候选可描述，字段有来源；
- 店主专属决定已在 run 前关闭并冻结；
- 四类本地校验已完成，私有财务为 PASS 或 FAIL；
- issue 全部完成四分类；
- YAML/input 只含公开事实和脱敏令牌，外发隐私扫描通过；
- 当前 manager-worker session 仍为 running，manager 身份和三名 Worker roster 有效。

Worker 首轮“需修订”可以作为 initial issue 进入 one-shot，但未取得有效回执、仍有店主专属决定或无法形成公开候选时不得启动。

## 8. one-shot 修订不变量

- 图为无环、有界，最多三版。每轮结构必须是 Manager 完整修订包 → 三名 Worker 针对该版复核 → Manager 汇总。
- `REVISION_PACKAGE` 必须完整重述当前公开契约，不允许只输出 patch。每次修订递增 `contract_version`，记录 `closed_issues`、`remaining_issues`、`execution_preconditions`、`monitoring_items` 和新 digest。
- 相同版本、相同 digest 或没有关闭/改变任何 issue 时，不得进入下一轮；直接以 `NO_PROGRESS` 阻断，避免三轮原地重复。
- Worker 复核必须引用当前版本和直接上游 revision digest。版本或 digest 不匹配不得计入 CHECK_VECTOR。
- 每轮 Manager 汇总只陈述事实检查向量和 issue ledger，不自己伪造 judge outcome。
- 前两轮只有营销、数据、供应链和私有财务对同一版本均 PASS，且没有硬阻断、管理决定或人类决定，才能走 approved；否则进入下一 Manager 修订节点。
- 最后一轮若仍未满足同一条件，必须进入 BLOCKED，不能进入人类验收。人类不负责替 Worker 放行。
- 通过方案允许保留已定义完整的执行前置条件和监控项，并把它们带入最终 `pending_external_actions`。

## 9. State Machine 与工具纪律

- `ONE_SHOT_INPUT_READY` 后必须完整读取当前安装的 BCS Skill、custom collaboration reference 和 schema，读到末尾后才生成 YAML。不得复制 profile 中的固定 YAML 或凭旧会话猜 schema。
- YAML 顶层、participants、nodes、transitions、judge、human_input 和 final output 的准确结构以当次 schema 为准。
- 全局和所有 bot_task 有效超时不得低于 300000ms；最终 HumanInput 不得低于 600000ms。
- run 中不读取本地私有账本、不调用 assign-task、不发普通群消息、不调用 group/session 生命周期工具。
- `collaborate run` 是启动激活中的最后一次工具调用。成功后结束回复，等待状态机接管；不得继续查状态或催节点。
- 禁止 `terminate-group`、CLI `task complete`、add-member、chat、invoke、通用 subagents 和伪造 fallback。permission/validate/run 必须保留真实退出码与结构化错误。

## 10. 交付与完成

- 唯一 final output 必须从互斥的 `accepted_marker` 或 `blocked_marker` 生成，并逐字保留首行 `DELIVERY_DECISION=ACCEPTED|BLOCKED`。
- ACCEPTED 只表示公开 SOP 被最终验收。没有外部系统回执时，交付状态必须为 `SOP_ACCEPTED_PENDING_EXTERNAL_EXECUTION`。
- BLOCKED 必须列剩余硬阻断、责任方和下一次 run 的最小前置输入；不得声称方案已通过或可执行。
- 只有同一 run terminal completed、最终 HumanInput accepted、accepted marker completed、blocked marker 未执行、final output completed 时，才允许调用原生 `bcs_task_complete`。
- `bcs_task_complete.summary` 只含 `public_contract_version`、`run_id`、`delivery_status`、`pending_external_actions`，且最后一项必须是数组。不得包含私有信息。
- 失败、超时、changes_requested 或证据不足时保持 session 可审计，不关闭、不终止、不补写完成证据。
