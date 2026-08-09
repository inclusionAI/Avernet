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
- `create-group --manager` 成功后旧私聊是否仍继续派发、查询新群、生成 YAML 或启动 run；若是立即停止，旧私聊不能遥控新群。
- 发现多个 `required_workers` 后是否错误调用了 `bcs chat`/`invoke`，或启动了多个独立后台会话；若是，立即停止并回到 `create-group --manager`。
- 当前 Worker task_id 是否全部属于同一个 manager-worker group/session；是否把三个 1:1 session 冒充团队协作。
- required Worker 是否都有真实 `ok=true`、非空 task_id 回执；是否口头补造 task_id、把 `CONDITIONAL_PASS` 改名为 PASS，或在店长改动 Worker-owned 条款后沿用旧 PASS。
- 是否把系统静默占位、答案只在思考区、业务卡不完整、版本不一致或“通过”与缺口并存误当成等待/确认；首次无效是否要求 final text 五行业务卡，第二次无效是否明确阻断。
- 三份首轮有效业务卡到齐后，`manual_dispatch_closed` 是否精确为 true；关闭后是否仍在普通群聊回派“补充测算/最终确认”，而没有把 change_set、carry-forward 和复核交给 one-shot。
- 店主的当前回复是否绑定唯一未决 `decision_id`；是否把模糊“是/继续”扩展成多个决定、`risk_accepted=true`，或用店主决定覆盖 Worker-owned 缺证和条件性结论。
- 产能是否区分基线与增量、总客流与分客群、活动期服务与完整履约；库存采购是否把 MOQ 错当包装倍数；日期与星期是否一致。
- 多服务是否共享同一员工/时段；是否先汇总全部增量分钟再检查整体剩余产能，还是错误把技能池与整体池相加。活动可用库存是否已含在途后又重复抵扣；120 义务、50 活动可用是否被错误写成零缺口。
- 三份有效首轮业务卡、候选、handoff、隐私预检和私有财务状态齐备后，是否在同一次激活连续执行 permission、完整读取当前 Schema 和 validate；已有 Present Human 时是否继续 run，无人类时是否只提示加入而没有试跑。不得因 Worker 尚有公开 issue 而停在普通群聊。持久群是否确有跨日或复用需求。
- permission 通过后、写 YAML 前是否已经完整读取 BCS Skill、custom collaboration reference 和 schema 到 `Validation errors`，并记录三份真实路径与读取时间；没有 `schema_read_receipt` 时必须阻断。
- one-shot candidate 是否只用当前 schema 的顶层键、mapping participants、嵌套 nodes、节点内 transitions、`bot_task/human_input` 和唯一 final output；是否混入顶层 nodes、participant 数组、depends_on、condition、prompt、额外 finalizer、占位符或不存在的引用。
- 每次 validate 前及 run 前是否扫描了最终完整 YAML/input；任何修改后是否重新扫描。节点 instruction、judge criteria、metadata 或推导值中是否泄露精确毛利率、成本、底线、现金上限、余额或差额。
- one-shot validation graph 是否为当前运行时支持的 `acyclic`，并用最多三组节点显式展开修订；是否误用了不支持的 `initial_node`、`max_iterations` 或回边。validate 是否保留真实错误与退出码，是否错误用 fallback 追加 `cli_not_available`；judge 不可用时是否正确阻断。
- state machine 默认及每个 bot_task 的有效超时是否至少 180000ms，HumanInput 是否至少 600000ms；是否复制了 Schema 的 60000ms 示例。marker 是否只输出单行且未调用工具。
- Manager 汇总 artifact 是否只含 CHECK_VECTOR，还是与 judge 各自产生了一个互相矛盾的裁决。是否存在 accepted/changes/blocked 三个 marker，且唯一 final output 按 marker 输出而非写死成功。
- 调用 run 前是否有 Present Human；缺席时是否错误试跑、调用 session/bot/help 探测或预填 HumanInput。
- 店主节点是否精确为无 assignee 的前端 `human_input`，节点自身是否有正数超时；participants/run bindings 是否只含 Bot。出现 owner Bot slot、`--binding owner=human_001` 或 HumanInput `assignee_bot_id` 非空时必须以 `HUMAN_INPUT_MODELED_AS_BOT` 阻断。
- `delivery_mode=one_shot_collaboration` 时 `session_completion_lock` 是否始终保持 `LOCKED`，直到同一 run 的 HumanInput completed + accepted judge outcome、accepted marker completed、失败 marker 未执行、唯一 final output 与 terminal 均 completed；是否错误把 skipped HumanInput、普通群聊“接受/继续/执行”、final 文案或自写阶段当作完成证据。
- 调用 `bcs_task_complete` 前是否已逐字段完成 `completion_evidence`，`COMPLETION_PREFLIGHT=PASS`，summary 键集合是否恰好为公开版本、run ID、待外部执行状态和未完成动作；调用后是否仍错误承诺继续使用当前 session。
- 是否在 blocked/failed/timeout/changes_requested 或任一 State Machine node 中调用了 `terminate-group`、CLI `task complete` 或其他 group/session 关闭命令；任一出现都是硬违规。

发现问题时：
- 先停止扩大风险，不擅自突破硬约束。
- 向对应平台 Agent 索要最小必要证据。
- 给出 Plan A/B 和推荐选择。
- 仅在越权时向店主提出一个明确决策问题；优先使用当前 manager-worker session 的 manager↔human 隔离对话，无人类时才回原私聊。
