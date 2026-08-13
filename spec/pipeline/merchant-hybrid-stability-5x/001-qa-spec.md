# Merchant hybrid 店庆流程五轮稳定性验收

## 目标

在同一代码和配置基线上串行执行五轮相互独立的店庆协作流程，验证
`3 OpenClaw + 1 Claude Code` 拓扑能稳定完成店长私聊、manager-worker
拉群、三位 Worker 协作、one-shot 状态机、店主 HumanInput 接受和最终
店庆方案交付。

## 固定输入

每轮在新建的店长私聊群中发送相同任务：

> 今年要做18周年店庆。下周开始，活动为期一个月。
>
> 原则只有一条：品质不变。第一目标是多来客人，第二目标是提高转化率。
> 老客主推护理套餐，新客用王牌剪发引流。活动贡献毛利率不能低于10%。
>
> 请你协调平台营销、平台数据和平台供应链，协商出一套可执行、可验收的
> 周年庆方案和SOP。

HumanInput 固定回复：`接受当前版本`。

固定授权包络同时声明商家活动优惠承担上限 3000 元、增量采购/预付现金
占用上限 4000 元。该上限覆盖同一固定业务输入下已验证的 3040 元候选采购额，
但仍不授权测试实际执行采购或投放，避免把预期的业务越权审批误判为流程不稳定。

若店长在读取知识包后要求确认授权，固定回复：店主已准备好，授权其在当前
任务给定的目标、品质和毛利门禁以及知识包事实范围内推进协商与方案生成，
但不授权突破底线或实际执行外部投放、采购；无需重复询问准备状态。

## 隔离要求

- 每轮必须创建新的店长私聊群、新的 manager-worker 群、新 session 和新 run。
- 若店长已在可见回复中明确声明 `ONE_SHOT_INPUT_READY` 却以准备确认结束，驱动
  最多发送一次“继续执行 permission → validate → run”；该消息仅恢复已有流程，
  不得作为 run 内 HumanInput 接受证据，并记录触发次数。
- 若驱动已通过 session members API 把 `human_001` 加为 Present observer，但店长的
  Bot-only context 仍提示“尚未检测到 Present Human/请确认店主在群中”，驱动以同一
  Human 身份最多回复一次“我已在当前群，继续 run”；该回执只证明在场，不含
  `接受当前版本`，不得充当 run 内 HumanInput。
- 若店长在首轮业务卡齐备后仍把授权包络内的 `DECISION_VARIABLE` 推荐候选反问
  店主，驱动最多回复一次“按推荐候选推进，继续 one-shot”；该回复仅重申首条任务
  已给出的边界内授权，不新增预算、不覆盖 Worker owner 条款，也不算 HumanInput
  验收。trace 必须记录 `owner_candidate_confirmed`。
- 同一规则覆盖店长未输出 `DECISION_VARIABLE`、但明确以“需要/请/向店主确认关键决策”
  或“待店主确认”重复询问已在固定授权包络内的 Plan A/Plan B（或方案A/方案B）。
  只有消息不含“超出授权/突破上限/额外预算/风险接受”等越界信号时才允许一次重申；
  其余问题不得自动作答。trace 仍记录
  `owner_candidate_confirmed`，且该回执绝不能等同 run 内 HumanInput 接受。
- 五轮串行执行，避免 Claude Code 会话并发干扰稳定性判断。
- one-shot 恢复消息必须等最后一位 Worker 的 assistant 回复之后，店长产生新的 assistant
  回复才可发送；不得在店长仍处理最后一位 Worker 的激活期间抢先写入，否则消息可能只进入
  历史而不触发独立激活。仍只允许每种恢复原因一次。
- OpenClaw 实际收到的状态机 envelope 可能不带 `group_type`/`recipientRole` 字段；当且仅当
  `sender_id=bcs_state_machine`、正文含 `[State Machine Task]` 且当前角色为 manager 时，必须
  视为等价的 Manager 状态机节点，只输出 instruction 要求的 artifact，禁止工具调用。
- 测试事实明确当前账面可用的 `CARE-RP-01` 已通过授权渠道、同 SKU/规格、包装、有效期与
  批次验收；到货与新增采购仍保留执行前复验。不得因已确认的现有库存品质字段再次阻断，
  也不得把尚未复验的在途或新增采购写成已验收。
- run 在 HumanInput 前进入 `blocked_marker` 或 terminal completed 时立即判失败，不继续空等。
- merchant anniversary opt-in CLI run policy 在发 HTTP 前还必须校验恰有三个非 Manager
  Bot binding，且每个 Worker binding 恰有三个人工展开的 `bot_task`（每轮一个）；Manager
  修订节点不能代替缺失的营销、数据或供应 Worker 节点。
- 同一门禁还必须校验每轮 Manager judge 的直接入边覆盖营销、数据和供应三个 Worker，
  以及 run input 中活动 30 天、两类券上限、护理 120 份义务、70/90 采购分支和 Plan A/B
  的固定公开合同字段。链式可达不等于 Judge 可见；字段缺失或改写均须在 HTTP 前安全失败。
- 方案制作与外部执行分离：当采购数量、报价、相对交期、授权渠道、品质标准和 Manager
  授权校验均已闭合时，实际下单、到货回执和到货复验必须进入
  `pending_external_actions`，不构成方案证据缺失。不得声称动作已执行；只有方案输入本身
  未知或不在授权内时才 `REVISION_REQUIRED/BLOCKED_MISSING_EVIDENCE`。
- 测试群和运行记录均保留，便于事后在前端复核。
- 日志和 trace 只记录 ID、状态、节点摘要和耗时；不记录凭据或完整聊天正文。

## 拉群可靠性修复

稳定性测试必须覆盖店长 Bot 自己完成 Worker 发现和 manager-worker 建群，测试脚本
不得代替生产 Bot 预建协作群。已确认 `bcs-cli discover` 在同一 manager 环境中从
终端可成功执行，但经 OpenClaw `exec` 工具启动时会在发出 HTTP 请求前被立即
`SIGKILL`。为避免把运行时 shell 限制误判为 BCS 故障，采用以下最小修复：

- BCN 插件新增 `bcs_discover_bots` 和 `bcs_create_manager_worker_group` 两个原生工具；
- 两个工具仅在 BCS channel 的非 manager-worker session 可见，并复用当前
  `BcsWsClient` 已认证会话；令牌只作为插件内 HTTP Authorization header 使用，
  不进入模型参数、工具结果或日志；
- discovery 只返回 Bot UUID、名称、在线状态与可见性等非敏感字段；
- create-group 自动将当前 Bot 设为 manager，仅接受 Worker UUID、topic 和已脱敏
  context，固定创建 `manager_worker` 群；
- 不新增或修改 BCS Rust 协议，不开放任意 URL/方法的通用 HTTP 工具；
- 店长 profile 恢复为：拉群只使用上述原生工具，`exec` 仍仅允许已有 one-shot
  permission/validate/run 窄例外。

## 状态机首节点与 Claude 超时门禁

首轮冒烟确认：店长在当前激活内调用 `collaborate run` 后，若状态机的唯一零入度
首节点仍分配给同一个店长 Bot，模型虽在 32 秒内生成完整回复，BCS 仍无法取得该
重入请求的完成回执，最终以 180 秒节点超时结束。为避免同一会话在启动激活尚未
结束时自重入，动态图必须满足：

- 唯一零入度入口是第一轮的一名 required Worker，不能是 manager；
- 第一轮由该 Worker 入口继续触发另外两名 Worker 的复核，三份结果再汇入 manager
  的 `CHECK_VECTOR + judge`，后续轮次和 HumanInput 语义保持不变；
- Claude Code 数据 Worker 的每个 `bot_task` 显式设置至少 `600000` 毫秒超时；其余
  Bot 节点仍至少 `180000` 毫秒，HumanInput 仍至少 `600000` 毫秒。

验收脚本从服务端 graph 和 run execution 同时校验上述约束，防止 profile 指令未被
动态 YAML 实际采用。该调整不修改 BCS 协议、调度器或普通聊天语义。

正式首轮复现了 profile 虽声明数据 Worker 必须为 600 秒，但动态图仍把全局默认
180 秒复制到三个 Claude 数据节点并直接启动的问题。为消除 LLM 在 participant 与
节点之间做超时映射的易错分支，本商家店庆 profile 统一要求
`runtime.state_machine.defaults.node_timeout_ms >= 600000`，且任何节点不得显式覆盖为
更小值。其余 Bot 只是获得更宽的失败等待窗口，不改变调度、judge 或业务结果；
BCS 通用 schema、协议和其他 profile 不作修改。验收仍逐节点检查三个 Claude 数据
节点的服务端有效超时至少为 600 秒。

下一次正式首轮又复现了第三轮 Manager judge 把 `approved/blocked` 错写成
`accepted/blocked`：`accepted` 只属于 HumanInput，导致服务端图虽可启动却不满足
三轮 review 契约。profile 的 authoring preflight 必须逐轮核对精确 outcome 集合：
前两轮各为 `{approved,revise}`，第三轮为 `{approved,blocked}`，HumanInput 才是
`{accepted,changes_requested}`；Worker 节点不配置 judge。任一集合不精确相等时禁止
validate/run。验收继续按服务端 graph 出边验证，不按节点名称猜测轮次。

随后模型又在 marker/final 节点显式写回 Schema 示例的 `60000`，证明单靠 prompt
preflight 不能形成稳定硬门禁。仅对 `merchant-operations` manager 启用 CLI 侧的
opt-in run policy：`collaborate run` 在发出 HTTP 请求前解析最终 YAML 与本次 bindings，
校验全图有效超时至少 600 秒、恰有三轮 Manager judge 且 outcomes 精确、Worker 无
judge、HumanInput outcomes 精确、三个 marker 和唯一 final output 完整。失败只返回
节点 ID/规则，不输出 YAML、input、凭据或正文，也不会创建 run。未设置 policy 环境
变量的其他 Bot、profile 及所有服务端协议保持原行为。
五轮驱动若观察到该 policy 的安全错误前缀，应立即把本轮判为失败并保存 trace，
不得继续等待 30 分钟的 run ID 超时，也不得自动放宽 policy 后重试。

“第一轮入口”按服务端 graph 的 assignee 与可达边语义判断，不要求节点 ID 必须含
`round1`。例如 `entry_fanout` 由 required Worker 执行并扇出到三位 required Worker、
再汇入 `manager_round1`，与 `round_1_marketing` 作为入口语义等价；测试不得仅因
命名差异拒绝真实有效图。唯一零入度、入口非 manager、三 Worker 可达和三轮节点
仍是硬门禁。

动态图在调用服务端 validate 前还必须完成可计数的交付分支预检：三轮 manager
汇总均有 LLM judge；HumanInput 有 `accepted/changes_requested` judge；节点 ID 精确
包含 `accepted_marker`、`changes_marker`、`blocked_marker`，三者都汇入唯一 final
output。缺一项时禁止 validate/run。服务端 graph 视图不返回 judge 配置正文，因此
验收侧以 `approved/revise|blocked` 和 `accepted/changes_requested` 出边验证 judge
已经实际进入运行图，不能依赖节点名中出现 `judge`。

## 本地 Provider callback 回包

状态机的 Provider `bot_task` 使用 BCN 2.0 callback transport。BaaS 异步完成
Claude Code 推理后，结果必须经本地 Provider bridge 回传 BCS `/bot/events`，不能
落到默认线上 BCN uplink：

- BaaS 到 bridge 使用仅存在于运行时文件和进程环境中的 loopback token；
- bridge 每次回包时重新读取当前 Provider ID、目标 Bot runtime token 和允许的
  `provider_bot_ref`，再转发到当前本地 BCS；
- bridge 拒绝未知 Bot、错误 token、非终态/畸形事件，并且日志只记录 run ID、
  provider ref 和 HTTP 状态，不记录 token 或消息正文；
- BaaS 普通环境仍使用原有 BCN secret/config；只有 mixed Claude singlebox 通过
  显式环境变量启用本地 callback 地址与 token；
- 不修改 BCS callback 协议、状态机调度和 SSE 普通聊天行为。

Provider 注册模式为 `static_bearer` 时，`/bot/events` 必须使用目标 Provider Bot
注册时返回的 `bot_runtime_token`；Provider admin token 只用于注册/删除等管理操作，
用它回包会被 BCS 以 `auth_mode_mismatch` 拒绝。运行时文件按
`provider_bot_ref → bot_runtime_token` 保存映射并保持 `0600`；bridge 必须精确选择
当前 Bot 的 token，禁止跨 Bot 复用。日志不得输出该映射或 token。

## HumanInput 启动门禁

真实冒烟证明：测试端已通过 session members API 加入 Human observer，但 manager
的 Bot-only context 仍可能看不到 roster 中的人类。若 manager 在 validate 后自行
判断“店主不在群中”并结束，BCS 会收口已无待处理任务的 session，之后任何在场
确认都会得到 `session is not running`，HumanInput 永远无法启动。因此：

- permission 与 validate 通过后，manager 不预判、查询或询问 Present Human，必须
  在同一次激活直接调用 `collaborate run`；
- `run` 的真实返回和随后 HumanInput execution 是人类在场/等待的唯一依据；
- 只有 `run` 明确返回人类缺席或服务端拒绝时，才保留 session 并报告原始阻断；
- 测试端加入 observer 的动作仍保留，但不再依赖普通聊天补发“我已在场”来挽救
  已结束的 session。

## HumanInput 同步 Judge 超时

真实首轮复现：HumanInput `respond` 接口先持久化人工回复，再同步执行最长 600 秒的
LLM Judge 后返回；五轮驱动若沿用普通读请求的 40 秒超时，会在回复已记录、Judge 尚未
完成时主动断开，导致本轮留在 `running` 且无法作为稳定性成功证据。最小验收修复为：

- 普通 BCS 读写请求继续使用 40 秒超时；
- 仅 HumanInput `respond` 使用独立的可配置超时，默认 11 分钟，覆盖 600 秒 Judge
  上限及 60 秒回包余量；
- 超时只改变测试驱动等待窗口，不修改 BCS API、Judge 规则、节点超时或业务结果；
- 驱动超时或服务端失败时，本轮仍判失败且成功计数归零，必须新建 group/session/run
  从第一轮重新完成连续五次。

## One-shot 公开契约保真

新首轮 `sm-886d0add-2553-42d8-a106-a2c95e8c12df` 完成三轮传输、Worker 与 Judge，
但在 HumanInput 前进入 blocked。服务端证据显示 authoring 把固定输入的 30 天活动期
误写成剪发券的 14 天有效期，把 40 套最大核销量当成销量预测并用无来源转化率下调，
遗漏已确认的现货品质证据，还把初始 `CONDITIONAL` 检查状态固化进不可变 run input，
导致后续 artifact 无法使 Judge 得到当前 PASS。最小保真门禁为：

- 活动执行周期与券/套餐有效期始终是三个独立字段，不得互相覆盖；
- owner 已固定的最大核销量是需要按最坏情形校验的契约上限，不是销量预测；不得用
  无来源转化率、行业经验或“保守估计”自行下调；
- 已确认的当前库存品质证据必须进入供应复核；在途/新增采购的到货复验属于
  `pending_external_actions`，不得反向写成当前库存品质标准缺失；
- run input 只保存稳定事实、授权、当前候选、真实 Worker 结论和初始 issues；
  `checks_status` 等会在状态机内变化的派生状态不得固化进 input；
- 每轮 Manager Judge 只依据当前节点 artifact 与真实上游 Worker artifact 判断当前版本，
  不得以初始 issue 或初始检查状态替代本轮结果。

这些约束只修正公开契约的字段保真和 Judge 输入边界，不绕过营销、数据、供应或私有
财务检查；任一当前 artifact 真实不通过时仍必须 revise/blocked。

## 长时验收的主机唤醒与 Worker 失败快停

## 统一模型重试基线

为排除 OpenClaw Worker 与 Claude Code Worker 使用不同模型造成的行为差异，验收可通过
`SINGLEBOX_MODEL_ID_OVERRIDE=Kimi-K2.6` 将 singlebox 生成的 OpenClaw 运行时配置覆盖到
AntChat 的 `Kimi-K2.6`；该覆盖只写入权限为 `0600` 的运行时导入文件，不修改用户的
`~/.openclaw/openclaw.json`。Claude `platform-data` profile 同时显式设置同一 model ID，relay
在每轮调用中传递它并继续从本机 Claude settings 读取认证/provider 环境。启动前必须以
provider 的 models API 确认该 ID 可用；不得记录或提交任何凭据。

## Kimi Worker 任务账本时限

真实首轮中，OpenClaw 营销 Worker 使用 `Kimi-K2.6` 在 7 分 25 秒后返回有效 final event，
而 BCS manager-worker 任务账本的默认 5 分钟窗口会将这类长任务展示为超时。该观察仅作为
后续长流程稳定性评估项；当前实现保留默认 BCS 账本行为，未修改 Rust 超时语义。

## 私聊初始化上下文与店主任务排序

BCS 创建普通 `chat` 私聊 session 时，会先向 driver 发送一条 `chat.send` 初始化
`GROUP CONTEXT`；测试脚本的真实店主任务在该消息之后才进入同一 session。该初始化消息
不包含本轮的业务目标、授权或公开合同。若店长把它当成任务开始执行，就会在真实的 30 天
输入尚未被模型处理前，按泛化历史模板创建错误的 14 天协作群。

最小修复限定在 merchant manager profile，不改变 BCS 的通用 delivery 语义：

- 仅当消息只是自由聊天 driver 的初始化上下文、且没有实际店主业务任务时，店长不得读取
  `KNOWLEDGE.md`、建账、发现 Bot、建群或调用任何工具；只回复精确令牌
  `INITIAL_CONTEXT_READY`；
- 后续真实店主消息到达后，才按既有 `KNOWLEDGE` 门禁进入 `PRIVATE_INTAKE`；
- manager-worker、state-machine 与带真实任务输入的群上下文不适用该等待门禁，仍保留原有
  自动派发语义；
- 五轮脚本必须先观察到 `INITIAL_CONTEXT_READY`，再发送固定 30 天任务；建出的
  manager-worker `context` 必须保留 `30 天`活动执行周期，缺失即在派发前判失败。

这同时验证消息排序与公开合同字段保真，不将“刚好没有报错”作为通过条件。

### Manager-worker 初始化交接（2026-08-12）

店长在私聊中成功创建 manager-worker 群后，旧私聊会严格结束，后续派发只能由新群的
首次 `GROUP CONTEXT` 激活继续。该上下文虽然不重复店主的完整业务原文，但包含
`模式: manager_worker`、manager 身份和完整 worker roster；它不是自由聊天的无任务
初始化。若店长在这里误输出 `INITIAL_CONTEXT_READY`，三个 worker 永远不会获得
`bcs_assign_task`，而服务看起来会处于“全部在线但没有回复”的假成功状态。

- `INITIAL_CONTEXT_READY` 仅适用于创建后的普通自由聊天私聊；
- 新 manager-worker session 的首次上下文必须立即恢复对应私有账本、校验 roster，并向
  三个 required Worker 进行定向派发；不得等待新的店主消息；
- 回归检查至少验证上述两条 profile 约束；端到端验收要求新群在派发后出现三名 Worker
  的可见 final 回复。

连续五轮会跨越数小时。一次正式首轮中，manager 与 marketing 两个独立 OpenClaw
进程同时出现约 947 秒事件循环空档；恢复后 marketing 的 AntChat 调用以 network
connection error 结束，而同一端点随后立即通过鉴权模型列表和 completion 探测。这是
主机空闲休眠造成的长连接失效，不是 BCS 丢任务。测试驱动必须：

- 仅在 macOS 且存在 `caffeinate` 时，用 `caffeinate -i -w <driver_pid>` 抑制当前验收
  进程存活期间的空闲休眠；driver 退出后守护自动退出，不常驻、不修改系统设置；
- 普通 Linux/CI 环境不启用该守护，保持原执行方式；
- Worker 可见回复若是 terminal engine error（例如 agent response timeout/network
  connection error），即使任务账本误标成已回复也立即判本轮失败，不等待 20 分钟；
- 不对失败任务自动补派或吞掉错误；修复环境后仍从第 1 轮重新连续计数。

## 状态机 Manager 节点工具门禁

真实五轮尝试确认：第一轮三个 Worker 节点均完成且 Claude Provider callback 返回
200 后，Manager 汇总节点仍可能把普通 manager-worker 阶段的 `bcs_assign_task`
规则误用于 `group_type=state_machine` 的 `bot_task`。该工具不属于状态机节点当前
暴露的工具集，首次执行会报 `non-exist tool`；重试结果无法作为节点 artifact 回包，
最终导致节点超时。最小约束为：

- Manager 收到状态机 `bot_task` 时只根据节点 instruction 和上游 artifact 输出本节点
  要求的文本 artifact；
- 该激活不调用 `bcs_assign_task`、建群、完成任务、CLI、shell 或任何其他工具；
- 普通 manager-worker 建群和首轮派发语义保持不变，工具禁用只作用于已经运行的
  state-machine 节点；
- profile 静态测试必须锁定这条上下文门禁，真实验收还需确认三个 Manager 汇总节点
  均未因工具调用失败或超时。

## 状态机节点内部 Session 隔离

真实五轮尝试确认：manager-worker 拉群、业务卡收集与 one-shot authoring 已在店长的
普通群 session 中累计大量历史；状态机随后把 Manager 汇总节点继续派到同一个
OpenClaw session 时，模型可能只产生 lifecycle 而没有 assistant 文本，插件最终以
`NO_REPLY` 收口。该问题属于 Engine 内部会话复用，不应通过删除业务上下文或放宽
状态机验收来规避。最小隔离边界为：

- 当且仅当 `session_context.group_type=state_machine` 且 BCS 幂等 run ID 以
  `smnode-` 开头时，BCN 插件为该节点 delivery 派生独立的 OpenClaw 内部 session key；
- 派生键只使用基础 route session key 与 run ID 的哈希，不包含消息正文；不同节点和
  不同 retry attempt 必须得到不同内部 session，避免失败历史污染重试；
- BCS group/session/run ID、回包目标和可见聊天语义保持不变；节点 instruction 已包含
  所需上游 artifact，不依赖普通拉群 session 的 transcript；
- 隔离节点不得覆盖 `group_id -> 普通 session key` 的反向映射，普通 `chat.send`、
  `chat.inject`、`chat.history` 和后续群聊仍使用原 session；
- 诊断日志只记录 `state_machine_session_isolated=true`、run ID 和派生键哈希，不记录
  消息或完整 session key；合约测试必须证明普通消息保持基础 session、两个状态机
  delivery 使用不同 session，且 history 反向映射仍指向基础 session。

## 写工具回执竞态

串行验收还确认：OpenClaw 在一次写工具等待回执期间可能先收到 BCS 状态消息，从而
并发开启后续激活并重放完全相同的工具调用。该竞态同时发生在
`bcs_assign_task` 和 `bcs_create_manager_worker_group`：前者创建幽灵待回复任务，后者
创建重复协作群。最小幂等边界为：

- BCN 插件仅对同一 OpenClaw session、同一 group、同一 target、同一 message 和
  同一 response mode 的并发或 60 秒内重放复用首个成功 task receipt；
- 不同 message、target、session 或 response mode 始终创建新任务，正常修订派发不
  受影响；失败回执不缓存；
- 建群仅对同一 session、相同 Worker UUID 顺序、topic 和 context 的并发或 60 秒内
  重放复用首个成功建群结果；不同参数仍创建新群；只读 discovery 不需要业务幂等；
- 缓存只保存消息指纹和 Promise/receipt，session 删除、插件停止时清理；日志只记录
  group 和 target 的幂等命中，不记录消息或指纹；
- 合约测试必须分别覆盖派发和建群的并发重放、成功后的短时重放和参数变化不去重。

## 单轮 PASS 标准

### Schema 加载的上下文预算

- 每个新 one-shot session 在 permission 通过后，仍须从头到尾读取当前安装的
  `references/custom-collaboration-schema.md`，并确认末尾 `Validation errors` 标题后才能生成 YAML。
- `bcs-coordination/SKILL.md` 和 `references/custom-collaboration.md` 的本场景约束已经固化在
  merchant manager profile 中，不在同一次激活里重复全文读取。三份文档与业务卡叠加会把当前
  模型输入推到上下文上限附近，导致模型在 schema 生成前空回复。
- 最终候选仍须经过 profile 的 `AUTHORING_PREFLIGHT`、服务端 validate 和 merchant 专用
  `bcs-cli collaboration run` 硬门禁；减少重复说明文档读取不得减少任何图结构或隐私校验。

### 五轮固定候选与 Judge 预算

- 五轮使用同一组显式、可复算的公开候选：王牌剪发门市价 80 元、优惠 40 元、
  用户实付 40 元、上限 120 次；护理套餐门市价 360 元、优惠 30 元、用户实付
  330 元、上限 40 套。不得把私有履约成本 32/180 元当成门市价。
- 供应方案保留在途按时/延迟两分支：按时采购 70 份、延迟采购 90 份；Plan A/B
  的报价和相对交期均进入最终方案，实际下单和到货复验仍是外部待执行动作。
- 固定候选只消除模型自行选择业务变量的随机性，不跳过三 Worker、三轮 Judge、
  HumanInput 或 final output。
- 本地 Kimi Judge 使用强制 `tool_call` 结构化输出；真实低敏探针已确认该模型能把
  Judge schema 参数稳定放入 tool arguments，而 `json_schema` 模式曾出现缺字段和
  非 JSON 正文。`max_tokens=8192` 避免推理内容耗尽默认 4096 预算后正文为空。第三轮
  Manager artifact 和三份上游 Worker artifact 叠加后，真实请求可能超过 180 秒；LLM HTTP
  timeout 因此设为 540 秒，低于状态机 600 秒 Judge 预算并为状态机收敛留出 60 秒。凭据仍只从
  `ANTCHAT_API_KEY` 运行时环境读取。

### Kimi-K2.6 首轮派发预算

- merchant manager 在新建 manager-worker 群内完成知识读取和私有账本读取后，首轮
  三个 `bcs_assign_task` 的准备推理可能超过 8192 输出 token。真实会话必须把
  `stopReason=length` 且没有任何派发工具调用视为失败，而不是等待 Worker 回复。
- 当通过 `SINGLEBOX_MODEL_ID_OVERRIDE=Kimi-K2.6` 选择 Kimi 时，singlebox 仅在权限
  `0600` 的运行时模型配置中设置该模型的 `maxTokens=16384`；不修改本机 home 配置、
  其他模型或 BCS 协议。Kimi API 的长上下文强制工具调用探针和真实新群三任务派发
  均须通过后，才开始五轮计数。

- 当前 3 个 OpenClaw Bot 和 1 个 Claude Code Bot 均在线且身份唯一。
- 店长创建的新 manager-worker 群恰好包含店长、营销、数据和供应链四个 Bot。
- 营销、数据和供应链均产生本轮回复。
- one-shot 图包含三类 Worker、最多三轮展开、Judge、HumanInput、三个互斥
  delivery marker 和唯一 final output。
- HumanInput 实际进入 pending，测试用户提交 `接受当前版本` 后完成。
- run 以 `completed` 结束；HumanInput outcome 为 `accepted`，accepted marker
  完成，blocked/changes marker 未执行。
- 官方 final output 直接包含 `DELIVERY_DECISION=ACCEPTED`、版本、run ID、
  Plan A/B、四项检查和待执行外部动作。
- 官方 final output 保存为独立 Markdown。

## 总体 PASS 标准

## Kimi Claude Code bypass 权限回归（2026-08-12）

`merchant_hybrid` 的平台数据分析 Bot 显式使用 `permission_mode=bypassPermissions`。
该模式不应再注册 relay 的 `canUseTool` 人工审批回调：该回调仅为 normal/plan
会话的 HITL suspend/resume 服务，而 BCS Provider 当前没有对应的人工审批端点。若
仍注册，它会把 Kimi 的工具请求错误地等待最多五分钟，表现为聊天长时间无最终回复。

最小修复要求：

- `bypassPermissions` 请求跳过 `canUseTool` 安装，并记录不含提示词、工具入参或凭据的
  诊断事件；SDK 仍按该 permission mode 自行处理工具权限。
- 非 bypass 模式且提供 interaction callback 时维持既有 HITL gate，避免改变原有
  continuation 语义。
- 覆盖三种组合的纯单元测试：bypass+callback 为 false，default+callback 为 true，
  无 callback 为 false。
- 重启 merchant_hybrid 后用 `Kimi-K2.6` 在真实 BCS 群发送无副作用数据问题，确认
  BaaS、Provider bridge 和 Claude relay 返回最终文本，而不是 interaction timeout。

## 本地后台进程会话隔离（2026-08-12）

非交互式本地命令执行器可能在 `singlebox` 返回后清理其原进程组。`nohup` 只忽略
SIGHUP，不能把后台服务移出该进程组，因而会出现“启动日志成功、数分钟后 status 全部
Stopped”的假成功。merchant_hybrid 的 Claude relay 已用 `POSIX::setsid` 隔离，其余
BaaS、Backend、BCS、OpenClaw、Provider bridge 和 Frontend 必须对实际 `exec` 入口
使用相同的隔离方式。

- 不改变服务命令、端口、环境或 stop 的 checkout 所有权校验；只在 `nohup` 与服务命令
  之间插入 `perl -MPOSIX=setsid`。
- `start/restart merchant_hybrid` 在 BCS Rust 源码比三份运行二进制新时必须先执行已有的
  BCS build，不得仅因旧二进制存在就跳过构建。
- 完整启动命令返回后，`status merchant_hybrid` 必须仍显示上述服务运行；随后才允许开始
  五轮验收。
- 静态 shell 回归必须覆盖六个后台入口均含会话隔离包装，防止后续在任一模块回退为同组启动。

## 五轮驱动初始化上下文隔离（2026-08-12）

五轮验收先创建店长私聊，再由下一条 owner `chat.send` 提供完整店庆业务卡。因此私聊
创建 API 的 `context` 只能标注“测试私聊已建立、等待下一条店主消息”，不得出现“执行”、
“协调”、Worker 名单、one-shot 或任何可被店长解读为业务指令的内容。这样才能实际验证
`INITIAL_CONTEXT_READY` 门禁；完整业务要求仍只在后续 `chat.send` 中发送。

验收驱动发现 manager-worker 群时，除了四个精确 Bot 身份外，还必须匹配公开合同的
`activity_period.duration_days=30`。删除失败测试私聊不会同步取消已经入队的 Bot run，
因此不能把一个迟到创建、仍为旧 14 天合同的群误归属给下一轮。忽略这类候选并记录数量，
不改变 BCS 建群或消息协议。

五轮均满足单轮 PASS，且五组 private group、manager-worker group、session 和
run ID 两两不同。任一轮失败时保存失败阶段和服务端错误，定位后只做解决
该失败所需的最小修复，再从五轮重新执行。

## 产物

- `output/merchant-hybrid/stability-5x-<timestamp>/run-01-plan.md` 至
  `run-05-plan.md`
- `output/merchant-hybrid/stability-5x-<timestamp>/run-01-trace.json` 至
  `run-05-trace.json`
- `output/merchant-hybrid/stability-5x-<timestamp>/summary.json`
- `spec/pipeline/merchant-hybrid-stability-5x/005-qa-report.md`
