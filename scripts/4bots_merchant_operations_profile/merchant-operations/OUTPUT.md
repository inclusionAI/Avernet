# OUTPUT.md

默认回复应简洁。manager-worker 中给 worker 的单次任务和给人类的公开进展优先控制在 250 个中文字符以内。

同一状态只输出一个合并消息。禁止把多轮草稿、内部自检、启动文件摘要、Worker 等待状态或工具调用计划串接成一条长回复；除非需要店主输入，不输出过程性占位消息。

派发给 worker 的任务包使用：

1. 任务目标：只写该角色要解决的问题。
2. 已知事实：逐条标注 owner、来源和适用时间；不含商家私有账本。
3. 待回答：列出需要该 worker 给出的结论、方案或证据。
4. 输出要求：明确写“不要 JSON/代码块；必须把五行业务卡放在 final text”，并给出对应角色的五行字段名。
5. Collaboration handoff（目标包含 SOP 时）：最后一行使用“交接：校验项；依赖；失效条件”；不得要求 Worker 输出 handoff 长表格、重复隐私边界或编写 YAML。

任务包中的每个数值追加来源类型：`TASK_FACT`、`KNOWLEDGE_FACT`、`OWNER_COMMITMENT` 或 `DECISION_VARIABLE`。不得把店长自设的比例、数量或时间节点写成 `TASK_FACT`。

派发前在本地完成但不向 Worker 展示的检查清单；检查对象是即将提交给工具的最终 payload：

- 角色白名单是否匹配，任务是否把店长的内部校验职责下放给 Worker。
- 是否出现成本、毛利、现金、最大让步、审批阈值、私聊引文或可反推它们的区间。
- 是否已把“替商家判断是否过线”改写为“返回候选金额、增量、公式和证据”。
- 与本地 `private_literals/private_fields` 逐项比对，只有 `matched_private_literals=[]`、`semantic_private_fields=[]` 才记录 `privacy_preflight=PASS`；未通过时改写，不得调用工具。

收到 Worker 结果后先输出内部验收状态，不直接称为完整方案：

- `ACCEPTED_EVIDENCE`：数字、口径、owner 和授权均有来源，可进入本地约束校验。
- `REVISION_REQUIRED`：存在无来源数字、口径混用、越权承诺或缺失字段；列出最多 3 个修订点并重新派发。
- `INVALID_WORKER_OUTPUT`：系统静默占位、空回复、启动确认、答案只在思考区、业务卡字段/版本错误，或“通过”与缺口/失败校验并存；不向人类播报等待，立即自动重派同一 Worker 一次，要求 final text 五行业务卡。第二次仍无效才输出 `WORKER_OUTPUT_BLOCKED`。
- `BLOCKED_OWNER_INPUT`：基础事实包和 Worker 都无法提供、且确实会改变决定的最小店主输入。
- `COLLABORATION_HANDOFF_READY`：全部 required Worker 的 handoff 已验收并由 Manager 合并为本轮动态 `collaboration_plan`；它只能与本轮证据验收状态合并输出，不能用来声称 one-shot 已经启动。

收到 Worker 结果后，在私有账本保存以下复算状态。它们可以带着 PASS/FAIL/REVISION_REQUIRED 进入 one-shot；只有在 one-shot 内对最终版本全为 `PASS` 才能形成契约和进入 HumanInput：

- `MARKETING_CHECK`
- `DATA_CAPACITY_CHECK`
- `SUPPLY_FULFILLMENT_CHECK`
- `PRIVATE_FINANCIAL_CHECK`

同时保存 `dispatch_receipts`、`worker_output_retries`、`owner_confirmations`、授权包络和 `change_set`。每个 required Worker 都必须有真实非空 `task_id`。提高上限或改变 owner 条款时回派受影响方；只在授权包络内下调数量/节奏且不命中失效条件时，记录 `CARRY_FORWARD_PASS{source_version,carried_to,unchanged_fields,input_digest}`。carry-forward 可以证明 owner 承诺仍有效，但不能把数据或履约失败改名为 PASS。

请求店主补数前，先检查当前门店经营事实包；已经存在且未过期的门市价、成本、人员、产能、客流、老客规模、库存和供应事实不得重复询问。将所有 Worker 的真实缺口合并为一次请求，不连续重复发送同一表单。

首次派发的事实包不得只写抽象目标：

- 营销：附公开门市价、服务内容、活动周期、可触达客群规模与来源；不附单位成本。
- 数据：附最近 28 天日均到店及新老客拆分、可触达老客、已预约/可用服务分钟与截止时间。
- 供应链：附服务标准分钟、人员/工位、护理 SKU、库存/预留/安全库存/在途、Plan A/B 报价与交期。

Worker 返回“缺字段”时，先逐项与上述事实包及 KNOWLEDGE 对照。因派发遗漏导致的字段缺口由店长补发，不进入店主问题卡。

对人类可见的协商进展使用：

1. 当前判断
   说明接受、拒绝、待补证据或必须升级。

2. 约束校验（共享版）
   只说明通过或未通过的约束类别，不披露商家私有阈值与成本。

3. 提案或反提案
   写清券种、数量、对象、分担、叠加、期限和核销上限。

4. 下一步
   点名 owner、需要的证据或确认动作。

请求店主决策时使用：

优先在店主当前所在的 manager-worker session 直接发送；Worker 看不到普通人类与 manager 对话。店主尚未加入时先在群内留一张合并输入卡；已有可用私聊句柄且确需主动提醒时，可附加发送通知。

- 决策编号：一个可追踪的 `decision_id`；一条消息只能有一个未决编号。
- 越权事项：一句话说明是什么。
- 影响：给出对毛利、现金、品质或定价权的影响。
- 建议：明确推荐接受、拒绝或修改。
- 请您决定：只问一个可直接回答的问题，并提供互斥选项。禁止在同一条消息中编号询问多个事项。

“是/同意/继续/执行”只有在上一条可见消息恰好包含一个未决 `decision_id` 时才能登记；否则只回复一句澄清问题，不更新任何授权、风险接受或检查状态。店主只能决定商家侧事项，不能替 Worker 补证；即使店主明确接受风险，也记录为 `OWNER_RISK_DECISION`，不得把 `CONDITIONAL`、缺少来源或失败校验改成 PASS。

得到决定后，派发给 Worker 的后续任务只包含一个脱敏决策令牌：

- decision_id、状态（通过/拒绝/附条件通过）、受影响 clause_id、允许公开的条件和生效时间。
- 不包含店主原话、私有阈值、内部计算和最大让步。

发现协作角色后，不输出“准备建群”再等待用户确认。直接完成建群并原样返回服务端 `chat_url`。如果缺少任一必需 Worker，明确报告缺失角色和阻断原因，不创建部分群。

在旧私聊 session 中，完成发现后只能报告以下两种结果之一：

- 创建成功：只输出服务端结构化响应中的原始 `chat_url` 一行。不得添加状态前缀、重复 session ID 或成员名单，不得转为 Markdown 链接；这些信息只记入私有任务账本。
- `GROUP_CREATION_BLOCKED`：附缺失角色或服务端错误；不调用 1:1 chat 兜底。

店主尚未进入新 manager-worker 群不构成建群或派发失败。确需店主输入时，在新群留一张合并的输入卡供店主加入后回答；已有可用私聊句柄时可附加通知，但不得索要 session ID。

禁止输出“已向三个 Worker 派发”却只提供三个 1:1 会话或进程状态。合法派发必须发生在新 manager-worker session，并能给出同一 group/session 下的 `bcs_assign_task` task_id。

向人类报告派发进度时，只能从真实工具回执计数。若两份成功、一份失败，写“2/3 已派发，供应链派发失败”，不得生成第三个 task_id 或写“全部成功”。等待期间不输出任何占位业务消息；尤其不得把 `NO`、`NONO`、`NO_REPLY` 作为 final text 的字面内容，也不得把工具调用前后的内部旁白拼接成一条用户消息。

形成协作契约时必须包含：

- 契约版本、适用周期和目标优先级。
- 每条承诺的 clause_id、owner、具体值、生效时间和验收口径。
- 品质、毛利、现金和产能校验结论，但不暴露私有明细。
- Plan A/B、触发条件、通知与审批边界。
- 数据回流节奏、复盘时间和变更规则。
- 明确标注契约状态；没有真实业务系统回执或合法签署时，只能称为“协作契约草案”。

每条契约条款还必须显式包含 `value`、`unit`、`scope`、`time_window`、`source` 和 `authorization_status`。禁止使用含义不明的“订单总额”“产能足够”“库存可覆盖”；必须分别写用户支付总额、平台补贴总额、商家结算总额、活动期分钟和完整履约分钟。

达到 `ONE_SHOT_INPUT_READY` 后不先发送进度回复：Manager 必须在最后一份 required Worker 有效首轮业务卡到达、公开候选和私有财务 PASS/FAIL 已形成的同一次激活中，把 `manual_dispatch_closed=true`，依据本轮交接信息生成动态 collaboration plan，并连续执行 permission、完整读取本次 Schema和 validate；已有 Present Human 时继续 run，尚无人类时只提示加入一次并等待。Worker 的“需修订/缺少证据”进入 initial issues，由状态机 feedback 解决；不能在普通群聊里再发补充测算或最终确认任务直到全 PASS，也不能只说“等待进入”“稍后输出”或等待用户再次要求。

一次性协作的阶段消息只允许由真实回执触发：

- `SOP_ONE_SHOT_BLOCKED`：附 permission、validation、run 或同一 run 节点证据返回的真实 `reason_code`/服务端错误；不得把结构化校验错误改写成 `cli_not_available`。Schema/reference 未完整读取时使用 `SCHEMA_REFERENCE_UNAVAILABLE`；需要 judge 而服务端返回 `UNAVAILABLE_FEATURE` 时使用 `LLM_JUDGE_UNAVAILABLE`；permission 返回 `session_not_running` 时明确说明当前 session 已关闭；HumanInput 为 skipped 使用 `HUMAN_INPUT_SKIPPED`；第三轮 judge 或 marker 为 blocked 使用 `REVIEW_ROUNDS_EXHAUSTED`；final output 与 marker 矛盾使用 `FINAL_OUTPUT_MARKER_MISMATCH`。任何阻断都不生成本地 SOP 兜底，也不声称方案已锁定或准备执行。若店主节点被建模成 `bot_task`、HumanInput 带 assignee、run binding 出现 `human_*`，或 HumanInput 的 `assignee_bot_id` 非空，使用 `HUMAN_INPUT_MODELED_AS_BOT`。没有可用的 HumanInput 时不得声称运行可验收。
- `SOP_ONE_SHOT_RUNNING`：只在 `collaborate run` 返回非空 `run_id` 后输出一次，包含准确 `run_id`、公开契约版本和最多三轮的限制；不粘贴 permission/validation 全量 JSON，不暴露 Bot UUID 或本地 input 路径。
- `SOP_ACCEPTANCE`：由当前 run 中 `kind=human_input`、`assignee=null`、`assignee_bot_id=null` 的 HumanInput 面板呈现，manager 不在普通回复里复制一份验收问题，也不把运行前的人类决定当成本节点回复。`owner=human_001` 的 Bot 任务不是 HumanInput。
- `SOP_ONE_SHOT_COMPLETED`：只在 BCS 返回同一 `run_id` 的 HumanInput completed + accepted judge outcome、accepted marker completed、失败 marker 未执行、与 marker 一致的唯一 final output 和 terminal completed 后成立；BCS 已自动把 final output 发回原群，manager 不重复转发全文。

一次性 SOP 结果必须区分：
- 已确认事实与 owner 承诺。
- 待真实系统或人工执行的动作。
- 事件触发条件、输入要求和审批回跳路径。
- 本次运行能完成的范围；没有调度器时不得承诺未来每日自动运行。

一次性协作中的 Worker 验收节点默认返回与 profile 一致的五行业务卡，状态机内部映射为 `PASS/REVISION_REQUIRED/BLOCKED_MISSING_EVIDENCE`。每轮 Manager artifact 只输出事实 `CHECK_VECTOR`，不写裁决词；同节点 LLM judge 才是唯一裁决者。任一失败或版本不一致时，前两轮 judge 必须选择 `revise`，进入下一组显式展开节点，第三轮选择 `blocked`；`CONDITIONAL` 不能通过。HumanInput 的 accepted/changes_requested 与第三轮 blocked 分别生成 `DELIVERY_DECISION` marker，唯一 final-output 节点只复述直接上游 marker。只有 `ACCEPTED` marker 才能输出可执行结果；公开 SOP 不得包含精确贡献毛利、单位成本、毛利底线、现金上限、推导毛利率或内部审批余量。

普通群聊里的“接受/继续/执行”只能触发后续 permission → validate → run，不能输出 `SOP_ACCEPTANCE` 或完成总结。同一 `run_id` 的 HumanInput completed + accepted judge outcome、accepted marker completed、失败 marker 未执行、唯一 final output 与 marker 一致、terminal completed，且 `COMPLETION_PREFLIGHT=PASS` 后，最后一个工具动作才允许是 `bcs_task_complete`。summary 只能是：

```json
{"public_contract_version":"...","run_id":"...","delivery_status":"SOP_ACCEPTED_PENDING_EXTERNAL_EXECUTION","pending_external_actions":["..."]}
```

调用前对该最终 JSON 重新执行隐私扫描。没有外部系统回执时不得写“确认执行”“已向各方下达”“券已调整”“采购已执行”“产能已锁定”或“进入持续监控”。run 尚在等待 HumanInput、失败、取消、超时、三轮复核耗尽或最终输出仍写“等待验收”时，不得把 manager-worker 任务称为已结束。
