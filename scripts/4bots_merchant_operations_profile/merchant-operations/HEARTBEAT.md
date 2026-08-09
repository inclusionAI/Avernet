# HEARTBEAT.md

周期性自检：
- 当前任务处于哪个强制阶段，是否已经满足进入下一阶段的条件；不得无故停在口头承诺或本地文档阶段。
- 当前契约版本、适用日期和有效条款是否明确。
- 品质、毛利、现金和产能硬约束是否仍然通过。
- 是否有平台承诺缺少 owner、数值、期限或验收口径。
- 券领取、核销、客流和转化是否使用同一指标口径。
- 是否出现供应延迟、库存缺口、异常客群信号或叠券风险。
- 当前动作属于授权内知会，还是必须由店主审批。
- 建群 context、session input、worker 任务、自定义协作 YAML/input、公开消息和 `bcs_task_complete.summary` 的最终工具参数是否通过 `matched_private_literals=[]`、`semantic_private_fields=[]` 检查。
- 经营活动是否在首次建群时就包含营销、数据和供应链三个 Worker；是否错误创建了只有部分角色的群。
- 涉及供应或品质的方案是否从协商开始就取得供应链 Worker 的证据结论。
- 所有业务数字是否有 owner、来源、口径和时间；是否混入了模型假设或其他会话数值。
- 需要店主补数或决定时，是否优先在当前 manager-worker session 直接询问；是否错误停住并要求用户提供私聊 session ID。
- 建群前是否错误向非 participant 调用了路由/mention；返回群入口时是否逐字保留服务端 `chat_url`。
- 发现多个 `required_workers` 后是否错误调用了 `bcs chat`/`invoke`，或启动了多个独立后台会话；若是，立即停止并回到 `create-group --manager`。
- 当前 Worker task_id 是否全部属于同一个 manager-worker group/session；是否把三个 1:1 session 冒充团队协作。
- required Worker 是否都有真实 `ok=true`、非空 task_id 回执；是否口头补造 task_id、把 `CONDITIONAL_PASS` 改名为 PASS，或在店长改动 Worker-owned 条款后沿用旧 PASS。
- 是否把系统静默占位、答案只在思考区、业务卡不完整、版本不一致或“通过”与缺口并存误当成等待/确认；首次无效是否要求 final text 五行业务卡，第二次无效是否明确阻断。
- 是否只回派受 change_set 影响的 Worker；授权包络内的数量收紧是否正确 carry forward，修订任务是否夹带期望答案而破坏独立复算。
- 产能是否区分基线与增量、总客流与分客群、活动期服务与完整履约；库存采购是否把 MOQ 错当包装倍数；日期与星期是否一致。
- 三份有效首轮业务卡、候选、handoff、隐私预检和私有财务状态齐备后，是否在同一次激活执行 permission、validate 和 run；不得因 Worker 尚有公开 issue 而停在普通群聊。持久群是否确有跨日或复用需求。
- one-shot validation graph 是否为当前运行时支持的 `acyclic`，并用最多三组节点显式展开修订；是否误用了不支持的 `initial_node`、`max_iterations` 或回边。
- 店主节点是否精确为无 assignee 的前端 `human_input`，节点自身是否有正数超时；participants/run bindings 是否只含 Bot。出现 owner Bot slot、`--binding owner=human_001` 或 HumanInput `assignee_bot_id` 非空时必须以 `HUMAN_INPUT_MODELED_AS_BOT` 阻断。
- `delivery_mode=one_shot_collaboration` 时 `session_completion_lock` 是否始终保持 `LOCKED`，直到同一 run 的 HumanInput、唯一 final output、terminal 状态均 completed；是否错误把普通群聊“接受/继续/执行”或自写阶段当作完成证据。
- 调用 `bcs_task_complete` 前是否已逐字段完成 `completion_evidence`，`COMPLETION_PREFLIGHT=PASS`，summary 键集合是否恰好为公开版本、run ID、待外部执行状态和未完成动作；调用后是否仍错误承诺继续使用当前 session。

发现问题时：
- 先停止扩大风险，不擅自突破硬约束。
- 向对应平台 Agent 索要最小必要证据。
- 给出 Plan A/B 和推荐选择。
- 仅在越权时向店主提出一个明确决策问题；优先使用当前 manager-worker session 的 manager↔human 隔离对话，无人类时才回原私聊。
