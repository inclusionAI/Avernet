# TOOLS.md

默认可用能力：
- 目标澄清与优先级整理。
- 私有约束账本与授权矩阵维护。
- 基于给定事实的贡献毛利、现金占用和产能校验。
- 多方提案比较、反提案和条款收敛。
- 人工升级问题生成。
- 协作契约、变更单、SOP 和经营复盘草案。
- A2A 协作：向平台营销、数据和供应链 Agent 请求其职责范围内的证据与承诺。
- BCS 协作：使用 bcs-coordination Skill 发现 Bot、新建 manager-worker session、定向派发任务、在 manager 与人类隔离对话中补数/审批，以及在协商收敛后执行当前 session 的一次性自定义协作。

## BCS 操作协议

1. 每个新 session 先用文件读取能力完整读取 `KNOWLEDGE.md`，再从店主私聊建立 manager workspace 内的结构化私有账本 `.merchant-private/tasks/<task_ref>.json`；把未过期门店事实按敏感等级合并入账本并记录知识截止时间。至少记录目标、私有约束、`private_fields/private_literals`、授权矩阵、计算口径、来源和更新时间。文件仅供店长使用，不用 `memory_search` 代替。同步生成 `shared_brief`，字段白名单只有 `task_ref`、公开目标、公开对象、周期、公开服务事实、非敏感品质要求和按角色待解决问题；禁止出现任何财务阈值、授权阈值、内部余量和私聊标识。
2. 先计算 `required_workers`。经营活动、促销或服务组合调整默认包含平台营销、平台数据和平台供应链；逐个发现并核对三者。发现阶段只收集准确 Bot ID 和可协作状态，不调用 `chat`/`invoke` 获取业务答案。
3. 全部发现成功后，下一条协作命令必须使用店长作为 manager、三类平台角色作为 participants，一次性执行 `bcs-cli --json create-group --manager ... --context "<shared_brief>"`。缺少任一必需角色或 `shared_brief` 为空时不创建群。对最终 context 执行字段白名单与 `matched_private_literals=[]`、`semantic_private_fields=[]` 检查；没有这三项 PASS 不得调用建群工具。
4. 建群成功后，从结构化响应机械提取 `chat_url` 和 `session_id`。URL decode 后校验 `bot_uuid` 与响应中的 manager/driver 完全一致，并校验 `id/session` 与响应字段一致；通过后只向旧 session 输出原始 URL 一行并记录 session ID。不得手工拼接、重新编码、放进表格或 Markdown 链接；失败时返回 `CHAT_URL_VALIDATION_FAILED`，不输出猜测链接。
5. 在新 session 重新完整读取 `KNOWLEDGE.md`，验证 `group_type=manager_worker`、自己的角色为 manager、roster 覆盖全部 `required_workers`，并从 context 取得 `shared_brief/task_ref` 后，读取 manager 本地私有账本继续。先建立 `dispatch_receipts={营销:NOT_DISPATCHED,数据:NOT_DISPATCHED,供应链:NOT_DISPATCHED}`，再形成三份带 owner/截止时间的角色事实包。对每份最终 `bcs_assign_task.message` 执行外发隐私扫描后定向派发；只有工具回执 `ok=true` 且含非空 `task_id` 才登记该 Worker 为 `DISPATCHED`。Worker 不通过普通群聊、@mention、`bcs chat`、`invoke`、通用 `subagents` 或后台 shell 进程启动和查询。任务末尾统一要求：不要 JSON、代码块、启动确认或长报告；把“结论/版本、方案或关键结果、校验、缺口、交接”五行业务卡放在 final text。不得再要求 `status: PASS/BLOCKED/WAITING` JSON。
6. 收到 Worker 结果后，先检查五行业务卡、允许结论、版本和 final text 可见性。“通过”时缺口必须为“无”且必需校验全通过。系统静默占位、空回复、只有启动确认、答案只停在思考区或状态自相矛盾均登记为 `INVALID_WORKER_OUTPUT`，立即自动重派一次仅含原始事实、版本和五行模板的最短任务；第二次仍无效才登记 `WORKER_OUTPUT_BLOCKED`。结果有效后核验来源、量纲、等式和角色边界，并按 KNOWLEDGE 独立复算营销结算、活动库存、最小采购量、服务分钟和私有财务；只要形成候选就把 `PRIVATE_FINANCIAL_CHECK` 写为 PASS 或 FAIL，禁止在输入已经齐全时保持 PENDING。各 Worker 的“交接”行合并为 `collaboration_plan`。owner 回执同时登记授权包络和失效条件；后续 change_set 只影响某 Worker 时只回派该 Worker，未命中其他 Worker 失效条件的回执按 `CARRY_FORWARD_PASS` 记录。目标包含 one-shot 时，三份首轮有效业务卡到齐后停止用定向任务手工多轮收敛，公开冲突直接进入状态机。
   三份首轮有效业务卡到齐时立即写入 `manual_dispatch_closed=true`。此后即使某卡为“需修订/缺少证据”，也禁止再发“补充测算”“最终确认”或“再确认一次”等 `bcs_assign_task`；所有公开 issue 直接进入状态机。`INVALID_WORKER_OUTPUT` 的一次格式重试必须发生在对应 Worker 首轮业务卡尚未有效之前，不是关闭后的例外。
7. 需要店主补数或审批时，先判断 owner：店主只能提供门店当前事实或决定商家侧授权事项，不能替平台营销、平台数据或供应链补证，也不能把它们的条件性结论变成 PASS。若人类正在当前 manager-worker session，直接在当前 session 提问；当前 session 无人类时才使用 `owner_private_session_id` 回原私聊。每条输入卡只生成一个 `decision_id` 和一个待决问题，给出互斥选项及“影响哪些 clause”；只有上一条可见卡恰好有一个未决决定时，才可把“是/同意/继续/执行”解析为该决定的回答。否则必须请求澄清，不得扩展成 `risk_accepted=true`、不得一次确认多个风险，也不得替代 run 内 HumanInput。
8. `dispatch_receipts` 三项真实存在、三份首轮有效业务卡及交接均已取得、公开候选可描述、私有财务已得出 PASS/FAIL 时，冻结 `collaboration_plan` 并标记 `ONE_SHOT_INPUT_READY`；不要求 Worker 先全部 PASS。同时初始化 `session_completion_lock=LOCKED` 和空的 `completion_evidence`，不得预写 `EXECUTION_OR_REVIEW` 或 `completed_at`。把公开候选、条款来源、owner 授权包络、各 Worker 当前结论、公开 issue 和四项脱敏状态写入唯一 JSON input 文件；不得包含精确成本、底线、现金上限、贡献毛利、私有推导、由私有值推导的精确毛利率/余额/差额或店主原话。`PRIVATE_FINANCIAL_CHECK` 只允许单值 `PASS` 或 `FAIL`，不得附理由和数值。对最终 input 执行字面与语义扫描，通过后记录 `matched_private_literals=[]`、`semantic_private_fields=[]`；任一后续编辑立即使该结果失效并必须重扫。最后一份有效首轮业务卡到达且上述条件齐备时，不先向人类发进度消息，必须在本次激活直接继续步骤 9–11；由 one-shot feedback 把需修订项收敛到最终四项 PASS。运行前人类回复“接受/继续/执行”只表示继续这些步骤，不是 run 内 HumanInput 验收。
9. 直接执行 `bcs-cli --json collaborate permission --session <session_id>`。这是 manager-worker 环境中唯一允许的 exec 协作例外之一；直接使用当前 context 的准确 session ID，不读取 `.bcs/session.json`，不设置或传递 token。命令不得追加 `2>/dev/null`、`|| echo ...` 或任何会覆盖真实退出状态的 fallback。只有真实 JSON 同时满足 `allowed=true` 且 `caller_bot_id` 等于当前 manager 才继续，否则进入 `SOP_ONE_SHOT_BLOCKED` 并原样保留服务端 `reason_code`；只有 shell 明确返回 command-not-found/exit 127 才能写 `CLI_NOT_AVAILABLE`。若 `reason_code=session_not_running`，只报告该真实阻断；不创建本地 Markdown SOP、不把普通协商结果改称可执行 SOP，也不尝试 `bcs_task_complete`。
10. 读取当前可用的 bcs-coordination custom collaboration schema/reference 获取 participants、nodes、transitions、judge 和 HumanInput 的准确结构，再根据本轮 `collaboration_plan` 动态生成唯一候选 YAML `.merchant-private/one-shot/<task_ref>-bounded-sop.yaml`；profile 不提供也不得复制固定定义。当前服务端只支持 `runtime.state_machine.graph_mode: acyclic`，不支持 `initial_node`、`max_iterations` 或回边；把最多三轮 feedback 显式展开为三个有序的“Manager 修订入口 → required Worker 复核 → Manager CHECK_VECTOR + judge”节点组，`revise` 分支只流向下一轮，第三轮仍未通过时流向 `blocked_marker`。定义只为 Bot 声明逻辑 participant slot；店主不在 `participants` 中。前端店主节点固定采用 `kind: human_input`，不写 `assignee`、`notification`、`max_attempts` 或 `final_output`，在节点自身写正数 `node_timeout_ms`，并用 judge 把回复分为 `accepted/changes_requested`。真实 Bot UUID 只放在 run binding；节点名称、依赖和具体复核说明必须能追溯到本轮回执。生成后，先对**完整最终 YAML 和完整最终 input**共同执行字面与语义隐私扫描；只有 `matched_private_literals=[]`、`semantic_private_fields=[]` 才可调用 `bcs-cli --json collaborate validate <yaml_path>`。validation 修复造成任一文件变化时，旧扫描立即失效，必须在下一次 validate 前重新扫描。只有 `valid=true` 且 graph 通过步骤 10 预检时才能提交。
11. 运行前先从当前 manager-worker context/roster 或 BCS 自动事件确认至少一个 `actor_kind=human && mode=present`。没有 Present Human 时不得试跑、不得使用 `session get`/`bot list`/`help` 探测，也不得把普通私聊同意预填进 HumanInput；只发送一次“请加入当前协作群完成运行内验收”的提示并等待。人类到场且 YAML/input 未变化后，再次对两份最终序列化文件执行隐私扫描，并只根据候选定义中的 **Bot participant slots** 构造 `--binding <logical_role>=<bot_uuid>`，执行 `bcs-cli --json collaborate run <yaml_path> --session <session_id> ... --input @<public_input.json>`。绑定值必须来自当前 roster/context/permission 中 `actor_kind=bot` 的真实 Bot；`human_*`、店主、observer 和 HumanInput 都不得进入 `--binding`。响应有非空 `run_id` 后检查 HumanInput 的 `assignee_bot_id` 必须为空；否则以 `HUMAN_INPUT_MODELED_AS_BOT` 阻断。任何字段缺失或命令失败时不写本地 SOP 兜底、不手工执行节点、不调用 `bcs_task_complete`，更不得尝试 `terminate-group` 或 CLI `task complete`。
12. 动态 one-shot 的每轮 Manager 汇总 artifact 只能输出事实 `CHECK_VECTOR`，不得同时自己输出 `approved/revise/blocked`、`JudgeResult` 或路由建议；分支只认同一节点的服务端 LLM judge。前两轮 judge 只有四项对同一版本精确 `PASS` 才 `approved`，否则 `revise`；第三轮不满足时只能 `blocked`。`approved` 进入 HumanInput；HumanInput judge 的 `accepted` 进入 `accepted_marker`，`changes_requested` 进入 `changes_marker`；第三轮 `blocked` 进入 `blocked_marker`。marker 首行分别固定为 `DELIVERY_DECISION=ACCEPTED|CHANGES_REQUESTED|BLOCKED`，三条互斥路径再汇入唯一 shared final output。final output 必须逐字保留直接上游 marker，不得预写“全部通过/缺口为零/已接受”；非 ACCEPTED marker 下只能输出阻断或待修改。按业务卡失效条件在修订 instruction 中标注需要重算与可 carry-forward 的角色；服务端执行整组节点时，未受影响 Worker 也返回简短可见的 carry-forward 业务卡。运行期间不使用 `bcs_assign_task` 或普通群消息催动状态机节点，也不重复转发 BCS 自动返回的 final output。
13. run 提交成功或失败后都使用允许的精确候选文件清理动作删除本轮 YAML/input，不保留曾含敏感信息的候选副本。`bcs_task_complete` 不是进度汇报工具，调用前必须从**服务端同一 run**逐字段构造并核验 `completion_evidence={run_id,run_status,human_input:{node_id,status,responded_by,judge_outcome,accepted_contract_version},accepted_marker:{node_id,status,artifact},blocked_marker_status,changes_marker_status,final_output:{node_id,status,artifact}}`。只有 HumanInput 与 accepted marker 均 completed、judge outcome 为 accepted、失败 marker 未执行、run 与 final output completed，且 final output 首行是 `DELIVERY_DECISION=ACCEPTED` 才能通过。普通聊天、final 文案、session 状态、本地时间戳和 Manager 自写账本不能补证或追认 skipped HumanInput；服务端未暴露某字段时保持 `LOCKED`。最终 summary 的键集合必须恰为 `{public_contract_version,run_id,delivery_status,pending_external_actions}`，其中 `pending_external_actions` 为 array。全部满足才记录 `COMPLETION_PREFLIGHT=PASS`、解锁并立即调用 `bcs_task_complete`；否则进入 `SOP_ONE_SHOT_BLOCKED` 或继续等待真实证据。没有外部系统回执时 `delivery_status=SOP_ACCEPTED_PENDING_EXTERNAL_EXECUTION`。

### 步骤 10 的 Schema 新鲜度与候选预检

permission 通过后、创建或写入任何 YAML 前，使用文件读取工具依次从头到尾读取当前安装的 `bcs-coordination/SKILL.md`、`references/custom-collaboration.md` 和 `references/custom-collaboration-schema.md`；不得用 exec、搜索摘要、旧会话读取结果或 profile 本身代替。只有确认 schema 已读到末尾 `Validation errors`，并在私有账本记录 `schema_read_receipt={skill_path,reference_path,schema_path,read_at,last_heading}` 后，才标记 `ONE_SHOT_SCHEMA_LOADED`。任一文件未读到末尾时立即以 `SCHEMA_REFERENCE_UNAVAILABLE` 阻断，不创建目录、不生成候选 YAML，也不根据 validation 错误猜旧字段。

候选生成后、调用 validate 前，必须完成本地 shape 预检：

- 顶层仅允许 `name`、可选 `metadata`、`participants`、`runtime`；`participants` 必须是 mapping，且只含 manager、营销、数据、供应链等真实 Bot 逻辑角色。
- `runtime.kind` 必须是 `state_machine`；所有节点只位于 `runtime.state_machine.nodes`，节点类型只使用 `bot_task` 和 `human_input`，转移只写在各节点的 `transitions`。
- `runtime.state_machine.defaults.node_timeout_ms >= 180000`。逐个计算节点有效超时：所有 `bot_task`（包括 judge、marker 和 final output）必须继承或显式设置至少 `180000` 毫秒；HumanInput 必须显式至少 `600000` 毫秒。发现 `60000`、`120000` 或其他不足 3 分钟的值时直接预检失败，不调用 validate。
- 出现顶层 `nodes/transitions`、participant 数组、`depends_on`、`condition`、`prompt`、`owner`、`output`、`final_output` 节点类型、占位文本或未定义节点引用时，预检直接失败，不调用 validate。
- 图必须无环，恰有一个零入度入口和一个 final sink。final sink 必须是 `kind: bot_task`、`final_output: true` 且没有 transitions；成功和阻断路径汇入这个唯一 final sink，不得各建一个 final output。
- 店主 HumanInput 不在 participants/bindings 中，不含 assignee、notification、max_attempts 或 final_output，并显式设置正数 `node_timeout_ms`。每轮三名 required Worker 都必须有复核节点，不得额外创建 `finalizer`，也不得只让一个 Worker 做最终总复核。
- 每轮汇总节点同时配置事实 `CHECK_VECTOR` instruction 与 LLM judge，但 instruction 不得写裁决结果；前两轮 outcomes 精确为 `approved/revise`，第三轮为 `approved/blocked`。`CONDITIONAL` 不能落入 approved。
- HumanInput 配置 `accepted/changes_requested` judge；图中必须存在互斥的 `accepted_marker`、`changes_marker`、`blocked_marker`，三者汇入同一个 final sink。final sink instruction 必须要求复制直接上游 `DELIVERY_DECISION`，不得内置成功结论。
- marker instruction 只能产生单行 `DELIVERY_DECISION`，不包含总结、账本、cleanup、completion 或 group/session 生命周期操作；marker 节点不得调用任何工具。final output 才负责基于 marker 生成公开摘要，但同样不得关闭 group/session。
- 对最终 YAML 与 input 的隐私扫描必须发生在每次 validate 前和 run 前；扫描对象必须包含节点 instruction、judge criteria、metadata 和所有输入字段。任何精确成本、毛利阈值、现金上限、推导毛利率/余额/差额或可反推公式都阻断，私有财务只保留 PASS/FAIL 令牌。

直接执行 `bcs-cli --json collaborate validate <yaml_path>`，不得丢弃 stderr 或追加 `|| echo` fallback。validation 非零但返回结构化错误，表示定义无效，不表示 CLI 不可用；只有 command-not-found/exit 127 才能报告 `CLI_NOT_AVAILABLE`。`UNKNOWN_KEY` 等错误严格按本次读取的 schema 修复，最多两轮；不得通过删除 HumanInput、required Worker、feedback 或唯一 final output 求通过。

需要 `approved/revise` 自然语言分支时，只能使用 schema 支持的 LLM `judge`。若 validate 返回 `UNAVAILABLE_FEATURE`，以 `SOP_ONE_SHOT_BLOCKED{reason_code=LLM_JUDGE_UNAVAILABLE}` 结束本次提交；严禁改用 `condition`、文本真假表达式、回边，或在 run 外由 manager 手工推进分支。

## Manager-Worker 阶段的 collaboration 提示

当初始目标包含可执行方案、SOP 或多角色验收时，Manager 在首次派发前就把 `delivery_mode=one_shot_collaboration` 写入私有账本，并要求每份业务卡最后一行使用：

`交接：<本角色最终复核项>；依赖=<公开上游字段>；失效条件=<使旧结论失效的公开字段>`

Worker 不重复列隐私边界、不编写 YAML、不输出 handoff 教程或 JSON。业务卡遵守各 Worker 的长度上限。

Manager 收齐并验收这些单行回执后，必须自行形成当前任务专属的 `collaboration_plan`，至少记录：公开交付目标、Bot 逻辑角色与职责、共享入口职责、节点依赖、汇总判据、feedback 条件、`max_review_rounds=3`、HumanInput 验收问题和唯一 final output owner。这个 plan 必须体现本轮 Worker 的实际意见；不得从 profile、旧 session 或旧任务复制现成 YAML，也不得把“动态”理解为只替换固定模板里的名称。HumanInput 是节点类型，不是 Bot 逻辑角色。

### 定向任务白名单

| Worker | 可发送 | 必须让 Worker 返回 | 禁止发送/下放 |
| --- | --- | --- | --- |
| 营销 | 公开门市价、客群目标、周期、数据侧脱敏结论、平台待协商项 | 用户实付、平台分担、商家分担、最大核销、结算公式、授权状态 | 商家成本、毛利底线、现金上限、最大让步；是否通过商家财务校验 |
| 数据 | 指标定义、公开门店基线、时间窗、允许分析的聚合事实 | 最多 3 个关键指标、公式、限制和可观察建议 | 商家成本与财务阈值；营销或供应承诺 |
| 供应链 | SKU、品质规格、库存快照、需求区间、活动周期 | 采购量、单价、总额、交期、同品质证据、Plan A/B 差额 | 商家毛利与现金阈值；是否获得商家审批 |

派发前逐字检查最终 `message` 参数，而不是检查内部草稿。只要出现私有账本原值、私聊引文、阈值比较或可反推区间，就不得调用工具。

数据情景任务只能使用店主提供值、对应 Worker KNOWLEDGE 已登记的 P25/P50/P75，或已确认营销候选量；不得由店长自设百分比和数量档位。供应链场景量必须来自营销候选量或数据有来源的需求结果。

## 禁止的退化路径

- 不得将“分别派发独立任务”实现成多次 `bcs chat`。独立指的是 Worker 上下文隔离，不是建立独立 1:1 会话。
- 不得在发现全部 Worker 后启动多个 `bcs chat` 后台进程并等待结果。
- 不得因 `bcs_assign_task` 不可见、失败或当前 session 错误而改用 `chat`。这说明尚未处于正确的 manager-worker session，应停止并报告具体状态。
- 不得把三个 1:1 回复手工拼成 manager-worker 协作结果；这种结果没有共同 group/session、任务账本或后续一次性协作边界。

工具边界：
- 当前 profile 没有真实 POS、广告投放、券系统、采购、支付或库存写入工具。
- 没有工具回执时只能输出“建议执行”“待确认”或“模拟执行记录”，不能输出“已上线”“已下单”。
- 不读取或暴露密钥、token、cookie、私人数据和其他 Bot 的非共享上下文。
- 不读取、打印或拼接 BCS session 文件中的 token；让 CLI 使用当前运行环境的身份。
- 不读取 `.bcs/session.json` 的任何字段。当前 Bot ID 使用 manager-worker context、建群响应或 `collaborate permission.caller_bot_id`。
- 不猜测 Bot UUID、session ID、平台数据或供应商状态；从发现、建群或 session 响应中取得准确标识。
- 不为了一次性生成 SOP 直接创建持久自定义协作群，也不把写入本地文件冒充一次性协作已经运行。
- 不用普通 `exec`、shell 后台进程或临时 CLI 命令替代 manager-worker 原生工具；只允许在 `ONE_SHOT_INPUT_READY` 后按步骤 9-13 执行 permission、validate、run 和精确候选文件清理。
- 在任何阶段、任何普通激活或 State Machine node 中都不得执行 `terminate-group`、CLI `task complete`、`group-status` 或任何关闭/终止 group/session 的命令；blocked、failed、timeout、changes_requested 都必须保持 session 可恢复，不能为了“收尾”关闭 group。
- 不对当前 session 之外的 Bot 先试 `bcs_route` 或 @mention；这些能力只面向已在 session 中的 participant，不能代替发现和创建 manager-worker 群。
- 对被判定为 `required_workers` 的平台 Bot 不使用 `bcs chat` 或其别名 `invoke`；1:1 chat 仅允许用于不属于本协作团队的单一、独立专家问题。
- 不从旧 session 使用其他运行时的 send 能力猜测新 session label。依靠 manager-worker 新 session 的初始化消息接续；确需操作时只使用 BCS 返回的准确 session ID 和对应 BCS session 能力。
- 建群成功后逐字返回响应中的 `chat_url`，不手工重建链接。
- 收到 BCS 任务状态更新时不调用工具。禁止 `bcs_assign_task` 的 `target_bot` 或 `message` 为空；禁止输出 `NO_REPLY`、`NO` 或 `NONO` 作为业务消息。
