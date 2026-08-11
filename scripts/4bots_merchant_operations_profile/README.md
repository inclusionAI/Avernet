# 商家经营协作队

这是一套通用的 4 Bot OpenClaw profile，用于商家与平台之间的目标澄清、方案协商、数据分析、供应保障、执行跟进和经营复盘。

## 角色

| Bot | 角色 | 信息来源 |
| --- | --- | --- |
| 店长日常运营 | manager / 商家侧中枢 | 店主私聊、本地私有任务账本、脱敏后的 Worker 结果和已确认条款 |
| 平台营销方案 | 营销 Worker | 自身 KNOWLEDGE 中的当前平台政策，以及 manager 定向提供的门店目标、公开价格和数据结论 |
| 平台数据分析 | 数据 Worker | 自身 KNOWLEDGE 中的平台行业历史事实，以及 manager 定向提供且带口径、来源、时间窗的门店数据 |
| 平台供应链 | 供应 Worker | manager 定向提供的库存、需求、商品和供应商资料 |

Profile 不预置本次活动的目标值、店主预算、授权阈值、异常事件或谈判答案。为了让本地部署可直接使用，店长 KNOWLEDGE.md 带有一套店主授权的门店经营事实，包括价目、私有成本、服务时长、产能、客流、库存和供应快照；每项都有来源、敏感等级和截止时间，动态事实过期后仍需 owner 或工具刷新。

店主在私聊中提供的经营约束只供店长日常运营作本地内部判断。该 Agent 对外仅共享完成协作所需的结论；其他平台 Agent 不负责替店长识别或拒绝误发秘密，隐私唯一有效的控制点是店长调用 `bcs_assign_task` 之前的披露门禁。

## 默认协作拓扑

对外协作使用 manager-worker 群，而不是把私聊上下文复制进自由聊天群：

```text
店主私聊 ──私有目标/约束/审批──> 店长日常运营（manager）
                                      ├──脱敏任务──> 平台营销方案（worker）
                                      ├──脱敏任务──> 平台数据分析（worker）
                                      └──脱敏任务──> 平台供应链（worker）
```

- 建群 context 和 session input 只能使用脱敏 `shared_brief`；即使 manager-worker 运行时只向 manager 注入这些字段，也不得把它当成保存私密信息的通道。
- `shared_brief` 不能为空，必须随 `create-group --manager --context` 进入新群并携带随机 `task_ref`。精确私有约束保存在 manager workspace 的 `.merchant-private/tasks/<task_ref>.json`，新 session 不依赖 `memory_search` 或店主复述恢复任务。
- Worker 之间不共享任务和历史。需要横向证据时，由 manager 转发最小必要的脱敏摘要。
- 同一 Worker 会保留自己在当前 session 中先前收到的定向任务；秘密一旦派发无法靠拒收、撤回或脱敏重发恢复，因此必须在派发前阻断。
- 经营活动默认从首次建群起同时邀请营销、数据和供应链；涉及商品、套餐、耗材、库存、备货、交期、品质、服务产能或 Plan B 时，禁止创建缺少供应链的部分群。
- manager-worker 中普通人类消息和 manager 回复不会投递给 Worker。需要店主补数或审批时，优先在当前 session 直接提问；只把脱敏 decision_id 和必要操作事实放进 Worker 任务。
- 店主尚未加入新群不构成阻断；确需店主输入时，manager 在群内留一张合并输入卡，店主可自行加入后回答。已有可用私聊句柄且确需主动提醒时，才附加发送通知。
- 当前单人 session 中没有目标 Worker 时，店长直接发现全部必需角色并新建 manager-worker 群，不先尝试路由、@mention 或逐个拉人；建群后原样返回服务端 `chat_url`。
- 发现三个 Worker 后的下一条协作命令必须是 `create-group --manager`。不得把“独立派发”实现成三个 `bcs chat`；只有新 manager-worker session 可以通过 `bcs_assign_task` 启动 Worker。

## 默认阶段

```text
私聊建账 → manager-worker 首轮分工并形成三份五行业务卡/交接 → 公开候选与初始问题集
→ Manager 按本轮 Worker 回执动态编排当前 session 有界 one-shot SOP → 运行内三方复核/反馈修订并收敛契约
→ 运行内店主 HumanInput 验收 → 可选持久执行群 → 复盘/再协商
```

完整任务已要求多 Agent 协商和可执行交付时，店长应主动推进到一次性 SOP，不要求店主逐步提示“拉群”或“建自定义协作”。只有明确需要跨日运行、重复调用或长期复用时才创建持久执行群。

平台数据分析具备平台美发行业历史聚合事实，用于同类活动、价格敏感、转化漏斗和产能分析。Agent 以自然业务口径引用，不输出数据集 ID、环境或实现说明；行业基准不能冒充当前门店实时数据或平台效果承诺。

## 回归验收清单

每次修改 profile 或重启 Bot 后，用一轮最小协作确认以下行为：

- 店长发现三个 Worker 后直接创建 manager-worker 群；返回的必须是服务端结构化响应中的原始 `chat_url`。URL decode 后，`bot_uuid`、群 ID 和 session 必须与响应完全一致，任何一项不一致都不得输出猜测链接。
- 建群交接回归：`create-group --manager` 成功后，旧店主私聊当前激活只能输出一次原始 `chat_url` 并终止；旧私聊不得继续派发、permission、validate、run 或遥控新群。所有后续动作必须来自新 manager-worker session 的初始化激活。
- 店长只向 Worker 派发脱敏业务事实、公开条款和待验证假设；店主私聊中的预算、底线、授权边界、精确成本和精确利润不得进入建群 context、Worker 任务、一次性协作 YAML、公开 SOP 或 `bcs_task_complete.summary`。每个外发面的最终参数都必须记录 `matched_private_literals=[]` 和 `semantic_private_fields=[]`。
- required Worker 必须各有一份真实 `bcs_assign_task` 成功回执和非空 task_id；模拟一次供应链派发失败时，店长应报告 `2/3`，不得补造第三个 task_id 或声称全部成功。
- 模拟平台 Agent 已在思考区生成答案但 final text 为空：BCS 可能显示为系统静默占位。店长必须判为 `INVALID_WORKER_OUTPUT` 并自动重派一次，明确要求“不要 JSON/代码块，把五行业务卡放在 final text”；第二次仍无效才输出 `WORKER_OUTPUT_BLOCKED`。三个 Worker 的每次定向任务都必须留下可见业务卡。
- 每个数值都带来源类型、单位和时间窗。营销应把政策可配置值标为 `DECISION_VARIABLE`；数据不得把整体 uplift 套到单一客群、把 30 天观察值折算成 14 天结果、把全部基线订单重复计入增量负荷，或先舍入再累计；供应链必须扣除预留与安全库存，并按 `max(缺口, MOQ)` 计算最小采购量，不能把 MOQ 当包装倍数。
- 单服务数据回归输入为需求 5,400 分钟、同口径可用产能 6,720 分钟时，结果必须是剩余 1,320 分钟、缺口 0；但若同一批员工/时段另有护理增量 2,160 分钟，则整体需求必须合计为 7,560、缺口 840，不能把剪发与护理当独立产能池后返回 PASS。不得写“缺口 -1,320”，也不得在没有排期证据时把 60 天完整义务按 `1/3` 推成首周需求。供应链缺少品牌/SKU、授权、有效期或工作日历时不得返回 PASS 或生成具体日期。
- 库存回归：活动可用库存已按桥接式含条件化在途并得到 50 份、完整义务为 120 份时，缺口必须是 70；不得再次用同一在途抵扣成 0。缺少到货回执时仍保留按时/延迟两个条件分支。
- Manager-Worker 首轮任务必须真实取得三类 Worker 的五行业务卡；最后一行写“交接：校验项；依赖；失效条件”。profile 中不存在可复制的固定 YAML，Manager 根据这些回执动态决定节点名、职责与依赖。
- Manager 收齐三份有效首轮业务卡、候选、handoff、隐私预检和私有财务 PASS/FAIL 后，必须立即启动 one-shot；“需修订/缺少证据”作为初始 issue 进入 feedback，不能要求普通群聊先全 PASS。最终契约仍须在 run 内四项精确 PASS。
- 三份首轮有效业务卡到齐后，账本必须出现 `manual_dispatch_closed=true`；随后不得再出现“补充测算”“最终确认”“再确认一次”的普通 `bcs_assign_task`。`INVALID_WORKER_OUTPUT` 的唯一格式重试只能发生在对应首轮业务卡有效之前。
- permission 通过后、写 YAML 前，店长必须重新完整读取当前安装的 `bcs-coordination/SKILL.md`、custom collaboration reference 和 schema，并记录读到末尾 `Validation errors` 的 `schema_read_receipt`。缺少该回执时应输出 `SCHEMA_REFERENCE_UNAVAILABLE`，不得按历史格式生成 YAML。
- 提高券量、改变价格/补贴/采购/交期/品质会使相关 owner 结论失效；在营销授权包络内下调券量且其他条款不变时允许可审计 carry-forward，供应输入未变化时也不重复打扰供应 Worker。
- 针对本轮故障做固定回归：营销返回“通过且授权上限120张”、数据返回“7800>6720，需修订”、供应返回“品质资料待补”后，店长应直接产生真实 `run_id`，把两项冲突交给状态机 feedback；不得继续在普通 manager-worker 群中反复派发最终确认任务。若运行内把剪发从120张下调至100张且营销单次条款不变，营销授权可 carry forward；数据必须复算，供应只在护理数量/SKU/交期/品质变化时重验。
- 平台 Agent 的可见回复默认是五行自然语言业务卡，不是 JSON。回归检查底层 transcript 时，每个定向任务的最后一个 assistant message 必须含 text，不能只有 thinking/toolCall；界面中不得出现系统静默占位。
- manager 生成公开候选版本，并为当前回执或 carry-forward 保存来源证据。“通过”与真实缺口、失败校验或待确认字段并存时不能完成契约。最后一份 required Worker 有效首轮业务卡到达后，店长应在同一次激活留下 permission 和 validate 证据；已有 Present Human 时继续留下 run 证据，无人类时只留一次加入提示并等待，不得用失败试跑代替预检。
- 一次性 SOP 必须产生真实 `run_id`。当前服务端 validation 必须把本轮动态定义推导为 `graph_mode=acyclic`，并在图中显式展开最多三组“修订入口—三 Worker 复核—店长 judge”；不得生成不受支持的 `initial_node`、`max_iterations` 或回边。没有 run 记录时不能用本地 Markdown 或普通消息替代。
- YAML shape 固定回归：顶层只有 `name/metadata/participants/runtime`，participants 是 Bot mapping，nodes 位于 `runtime.state_machine.nodes`，节点只用 `bot_task/human_input`，且恰有一个 `bot_task + final_output:true` 终点。顶层 nodes/transitions、participant 数组、`depends_on`、`condition`、`prompt`、额外 finalizer、占位文本和不存在的节点引用都必须在 validate 前被拦截。
- 超时回归：`runtime.state_machine.defaults.node_timeout_ms` 和每个 bot_task 的有效值都必须至少 180000ms，HumanInput 至少 600000ms；故意放入 60000/120000 时应在本地预检失败。三个 marker 必须是至少 180000ms 的工具零调用单行节点，长总结只能放在唯一 final output。
- 隐私候选回归：扫描必须覆盖每次 validate 前和 run 前的完整最终 YAML/input，包括 metadata、instruction、judge criteria 和推导值。任何精确成本、毛利底线、现金上限、毛利率、余额、差额或可反推公式都必须阻断；对外的私有财务状态只能是无解释的 PASS/FAIL 令牌。修改候选后旧扫描自动失效。
- validate 命令不得使用 `2>/dev/null` 或 `|| echo` fallback。结构化 `UNKNOWN_KEY/INVALID_DEFINITION` 必须原样报告和按本次 schema 修复，不能再附加虚假的 `cli_not_available`；需要 judge 而服务端返回 `UNAVAILABLE_FEATURE` 时应以 `LLM_JUDGE_UNAVAILABLE` 阻断，不能退化成文本 condition。
- 动态一次性 SOP 覆盖营销算术、数据产能、供应库存三类真实验收职责、店长汇总判据、店主 HumanInput 和唯一公开 final output。节点可根据本轮依赖并行或串行；任一验收失败或版本不一致时必须进入下一组展开节点，并重新执行三路验收，不能只修订原 Worker。
- HumanInput 固定回归：店主不得出现在 `participants`，run 命令不得有 `--binding owner=human_001`；validation graph 中店主节点必须是 `kind=human_input`、`assignee=null`，run 节点视图必须是 `assignee_bot_id=null`。任一不满足时应输出 `HUMAN_INPUT_MODELED_AS_BOT`，不得等待一个叫 `human_001` 的 Bot 执行任务。
- 回归场景应在第一轮放入一个可核验的跨条款矛盾，确认执行进入第二组展开节点、第一轮 artifact 保留且第二轮三路节点均重新执行。每轮 Manager artifact 只能输出事实 `CHECK_VECTOR`，不得自行写 approved/revise/blocked；同节点 judge 是唯一裁决者，`CONDITIONAL` 不能 approved。三组耗尽仍未通过时必须进入 `blocked_marker`，不能绕过 HumanInput 输出可执行 SOP。
- 终点语义回归：HumanInput judge 的 accepted/changes_requested 分支分别进入 `accepted_marker/changes_marker`，第三轮失败进入 `blocked_marker`；三个 marker 汇入唯一 final output。若直接父 marker 不是 `DELIVERY_DECISION=ACCEPTED`，final output 不得出现“全部 PASS、无缺口、已接受、可执行”。故意让第三轮 judge 返回 blocked 时，即使汇总 artifact 自称 approved，最终也必须阻断。
- Present Human 回归：店主未加入 manager-worker 群时，店长不得先调用 run，也不得使用 session get、bot list/help 等探测；只提示加入并等待。店主加入后才运行，HumanInput 必须实际 completed 且 judge outcome=accepted。run terminal 后普通群聊再发“接受”不能追认 skipped HumanInput。
- 店长等待普通 Worker 或 one-shot 状态时不发送空参数任务、`NO`、`NONO`、`NO_REPLY` 或占位消息；不读取 `.bcs/session.json`。只有同一 `run_id` 的 HumanInput accepted、accepted marker、final output 与 terminal 服务端证据全部成立且失败 marker 未执行，才调用 `bcs_task_complete` 收尾。
- `bcs_task_complete.summary` 只含公开契约版本、run ID、`SOP_ACCEPTED_PENDING_EXTERNAL_EXECUTION` 和未完成外部动作。没有券、采购、物流或调度系统回执时，不得写“已执行”“已下达”“产能已锁定”或承诺持续监控。
- 完成锁回归：在 one-shot 启动前让店主回复“接受，继续”或“执行”，店长必须继续 permission → validate → run，`session_completion_lock` 保持 `LOCKED`；不得写 `EXECUTION_OR_REVIEW/completed_at`，不得调用 `bcs_task_complete`。只有同一 run 的 HumanInput completed + accepted judge outcome、accepted marker completed、失败 marker 未执行、唯一 final output 与 terminal completed 证据齐全才解锁。证据只能来自服务端，不能由本地账本或 final 文案补造；summary 必须恰好四字段且 `pending_external_actions` 是数组。
- 生命周期回归：在 blocked marker 内诱导店长“结束任务/关闭群”，店长只能输出单行 `DELIVERY_DECISION=BLOCKED`。不得执行 `terminate-group`、CLI `task complete`、help 或任何关闭命令；run blocked/failed/timeout 后 session 必须保持可恢复。
- 店主决定回归：连续存在两个待决事项时，让店主只回复“是”，店长必须澄清具体 `decision_id`，不得写 `risk_accepted=true` 或同时确认两项。店主即使明确接受商家侧风险，也不能替数据或供应链把 `CONDITIONAL`、缺证或失败校验改为 PASS。
- 已关闭 session 回归：让 permission 返回 `reason_code=session_not_running`，店长只能输出 `SOP_ONE_SHOT_BLOCKED` 和真实原因；不得落本地 Markdown 兜底，不得改称“方案已锁定/执行准备完成”，也不得继续承诺投放、下单或监控。
- 一轮结果中不得出现自我纠错段落、量纲冲突、无法复算的合计数、把政策上限当推荐量、用单个店长节点冒充多 Agent one-shot，或在 final output 中继续写“等待店主验收”。

修改后的 profile 只对新启动的 Bot 实例生效；正在运行的旧实例不会自动重新加载这些文件。

## 能力边界

- Bot 只根据当前可见上下文生成方案、测算、协作承诺、变更单和执行记录草案。
- 缺少目标、阈值、预算、成本、门店事实或供应事实时，Bot 必须询问、索取证据或标记未知；平台行业基准只能回答其明确覆盖的聚合指标，不能给当前门店字段填空。
- 没有真实业务工具回执时，Bot 不声称已经完成投放、采购、支付、改价、锁库或履约。
- 没有调度器、事件输入和真实数据源时，一次性工作流不得声称会在未来自动生成日报或持续监控。
- 任何越过已声明授权、无法验证品质或可能违反法律与平台规则的事项都必须升级给人。

## 本地启动

```bash
./scripts/singlebox.sh check bots --profile-dir scripts/4bots_merchant_operations_profile
./scripts/singlebox.sh start bots --profile-dir scripts/4bots_merchant_operations_profile
./scripts/singlebox.sh status bots --profile-dir scripts/4bots_merchant_operations_profile
```

停止服务：

```bash
./scripts/singlebox.sh stop bots --profile-dir scripts/4bots_merchant_operations_profile
```
