# RFC：多人谁是卧底的上下文隔离

状态：待维护者评审。源码和基线测试以 `oss-camp2026 @ 86252c1e9` 为准，并对照了 `dev @ f0540f9b6e` 的 Group Context ACL、HumanInput 定义校验和协作命名空间路由。[威胁模型](threat-model.md)记录数据流和风险。[维护者回复](https://github.com/inclusionAI/Avernet/issues/2609#issuecomment-6412333662)约束 HumanInput 和票面展示的范围。现有实现与主线方案需和 [#3193](https://github.com/inclusionAI/Avernet/pull/3193)、[#3515](https://github.com/inclusionAI/Avernet/pull/3515)、[#3748](https://github.com/inclusionAI/Avernet/pull/3748) 对齐，重叠实现由维护者选定后再合入。

## 问题

目前真人词会进入主持人的 `init` / `my-word` 输出、群聊以及真人的 `human_input.instruction`。加入第二位真人后，群聊和未绑定的节点无法按座位隔离。现有副屏使用公开的 `currentViewerActorId` 选择展示动作。多人版需要让玩家在发言前读到自己的词，并由服务端认证和授权决定取词权限。

## 候选方案：每位真人一条 Group Context 记录

1. **主持人发词。** `undercover.py` 从已核对的会话名单取得座位及其真人 `actor_id`，为每位真人在本局会话作用域创建一条独立记录。Group Context 的 `add` 会把已认证的创建者设为 owner。`visible_to.userIds` 明确包含主持人和该座玩家，`collect_from.userIds` 仅包含主持人。空的读取名单表示公开，脚本应拒绝生成这样的发词记录。Bot 继续从分配给自己的协作节点取得词语。
2. **副屏确认身份。** 使用宿主同源且经认证的身份入口。nextgen 的候选入口 `/openapi/v1/collaboration/me` 读取 `AuthenticatedCaller`，返回 `user_id`。已有 BCS 原生宿主可核对 `/me` 返回的 `actor_uuid`。将认证结果按同一部署的 Human actor 规则与本局座位表匹配；身份缺失、格式不符、匹配不唯一或不在本局时，显示无法读取，停止取词。公开座位数据仅作定位来源。
3. **服务端决定能否取词。** 副屏携带当前登录态，按会话作用域和条目引用请求私有词。服务端从该请求的凭证识别调用者，Group Context 检查该调用者是否为 owner 或在 `visible_to` 中。请求提供的会话与条目引用用于定位，访问者身份由服务端解析。即使客户端选错了座位或条目，ACL 仍应拒绝读取其他座位的词。

### 记录与发布边界

- 每条记录使用本局独立、随机且与词值、阵营无关的引用。公开副屏可以带上座位到条目引用的映射，引用本身没有授权效力。引用只在全部发词记录创建成功后发布；冲突或部分写入失败时保持未开局，公开报错只包含可排障的状态。重试使用新一组未公布引用，避免沿用未核实的条目。
- 多人模式下，写入动作在主持人脚本内部完成，主持工具的调用入参、输出及对外错误保持脱敏。群聊、真人任务说明与 UI JSON、公开副屏参数、共享运行输入和会话历史均不得放入私有词。单人模式保留原有交互和 `flow_baseline.json` 快照。
- 私有响应使用 `no-store`，副屏只在内存中保存。会话、玩家身份或本局标识变化时清除显示和缓存。终局或开局废弃后的记录回收方式待定，`valid_to` 是业务字段，现有 `get` 不会据此自动停止返回当前记录。

## 当前接入缺口与评审项

源码显示，`bcs-http` 注册了根路径 `/groupcontext/get`，其调用者解析支持经配置的真人身份提取。nextgen 本地开发把 `/bcnproxy` 改写到 `/api/v1/collaboration`，当前基线的该命名空间没有挂载 Group Context。因此，根路由存在与 nextgen 副屏能否安全取词是两个独立的检查项。

- 优先在 singlebox 核对实际宿主能否通过已支持的同源路由调用根路径，并确认身份入口、座位表和 Group Context 在同一会话中得到同一个真人 actor。
- 如果 nextgen 部署无法安全到达根路由，请维护者选择已有认证网关的受控转发，或在协作命名空间暴露最小的只读入口。两者均要求复用服务端的真人认证与 Group Context Service API，保留作用域和 ACL 语义。副屏遇到缺路由或无权限时显示错误，停止取词；任何 BCS 契约变更在评审后单独提 PR，并配套认证、权限及兼容性测试。

## 多人开局与状态

- 接受 2～4 位已加入会话的真人、至少 1 位玩家 Bot 和 4～8 个玩家席位。主持人单独计数。开局前用 BCS 会话名单校验唯一真人 actor、Bot worker 和主持人，成功发词后冻结名单。后来加入、退出或重新分配席位不得改变已开局的发词归属。
- 将真人 `actor_id` 随座位记录保存，渲染时读取该座位的身份。配置卧底数，默认 1，建议上限取 `floor((总席位数 - 1) / 2)`，确保开局时为少数。保留原版胜负、PK、回合上限和出局流程。该人数规则待评审确认。
- 单人局继续读取现有状态结构并生成现有 YAML 和副屏参数。多人开局使用独立的新状态结构与发词标识。升级时不转换进行中的对局，也不重录单人基线快照。

## 真人发言串行、投票并行

| 阶段 | 状态机图 | 副屏行为 |
| --- | --- | --- |
| 发言 | 按现有座位顺序依赖生成各席任务，PK 只让候选者发言。真人节点的 `assignee` 绑定当前座位的 `runtime_actor`。最后由 `collect` 汇总。 | 当前真人只看到自己的待办；未轮到时展示公开进度；按现有规则展示允许读取的前序发言。 |
| 投票 | 单入口 `vote_start` 发出公开开场后，扇出存活玩家的投票节点。不同真人的任务互不依赖，与 Bot 一同并行等待，最后由 `tally` 汇合。 | 根据本轮参与者身份展示自己的待办。计票前显示公开的「已投／待投」进度，计票后才展示公布的票面。 |

`vote_start` 优先选存活 Bot。没有存活 Bot 时，建议使用仍可调度的出局 Bot，只发公开开场信号，不增加投票资格或计入进度。若会话没有可调度的玩家 Bot，报告可恢复错误，不生成缺少入口的投票图。若维护者希望支持纯真人局，需要另评估中立入口能力。

### 需要评审的 BCS 契约变更

当前 BCS 定义校验禁止前端 HumanInput 带 `assignee`，并要求真人节点之间存在先后依赖。建议允许前端节点绑定明确的 `runtime_actor`。异步等待节点只有在都绑定且绑定不同真人时才可并行。同一真人、未绑定的旧节点和已有通知模式保留其依赖限制与兼容行为。运行启动阶段应核对绑定对象属于本次会话的真人成员。

查询待办和提交回答复用现有接口及服务端认证逻辑，分别检查读取者和提交者是否为绑定真人。浏览器不传额外的访问者身份参数。应验证一位真人只能处理自己的节点、不同真人独立完成各自节点、同一节点的重复提交沿用现有冲突语义。该变更由 BCS 单独提 PR，配套定义与运行时契约测试，包含原有无绑定单人节点的兼容用例。

### 票面公布边界

多人投票副屏参数显式标记本轮 `voteTallyNodeId`，值为 `tally`。参与者视角以当前运行、轮次和尝试号匹配到该节点已完成为公布条件。公布前既不请求也不渲染他人投票详情；普通发言内容和进度照常刷新。重新投票或切换运行时清除旧详情缓存，保持常规投票与 PK 的结算互不串用。

保留默认 `Full` 会话视图。本阶段保持既有的节点详情和会话历史访问契约，具备运行权限的参与者仍可能在服务端详情或 `Full` 历史中收到未公布的票面。因此只验收参与者副屏的票面展示边界。玩家词语的隔离通过独立发词记录和服务端授权验收。上游原话和遗言回执在遮蔽前的可见性仍按威胁模型 C03、C06 验证，必要的跨服务治理另外评审。

## 首个验收用例

在 singlebox 内用两个独立真人身份和两个不同的测试词，先验证最短路径，再接入完整游戏流程。

| 认证身份 | A 座位记录 | B 座位记录 |
| --- | --- | --- |
| 真人 A | 返回 A 的词 | 不返回词 |
| 真人 B | 不返回词 | 返回 B 的词 |
| 主持人 | 返回 A 的词 | 返回 B 的词 |
| 玩家 Bot 或未登录访问者 | 不返回词 | 不返回词 |

测试同时覆盖公开参数被更改、会话切换、写入失败和不同真人依次打开同一副屏。检查两名真人实际收到的响应、群聊、真人节点说明、主持工具事件和历史，按威胁模型编号记录证据。以上是待实施的验收项。

## 交付与验证计划

1. 将 RFC、威胁模型和 AI 使用说明提交到 `oss-camp2026` 的独立文档 PR，请维护者确认真人取词路由、HumanInput 绑定与并行规则，以及无存活 Bot 时的投票入口。
2. 评审通过后，将经批准的 BCS 契约变更单独提交。由 profile 和 panel 消费该契约，分别验证多人状态与编排、私有取词、公布时机，并保留单人流程。
3. 隔离测试按 C01～C11 记录角色和出口。用不同的固定测试词检查真人与 Bot 各自的词、公开数据、上游产物、任务回执、工具事件、文件共享和历史。记录所见范围及平台限制，确保每格可执行断言都有对应测试。保留注入一处假词后断言失败的证据。
4. 跑裁判 `tests/*.py`、副屏 `npm run verify` 和单人 `flow_baseline.json` 回归。当前源码基线已通过 Ubuntu WSL 的 51 项裁判测试（含单人快照）及 Windows 的副屏 `npm run verify`（UMD 251730 / 256000 字节）。公开参数故障注入只验证了既有断言。待实现后用相同命令做回归。
5. 使用符合 Issue 要求的 macOS 或 Linux singlebox 环境，至少跑完三局真实多人对局。第一局为 2 真人 + 3 Bot，第二局为 3 真人 + 3 Bot，第三局建议覆盖 4 位真人及多个卧底。复盘记录实际参与者、关键阶段、隔离检查和失败情况，并录制不超过 2 分钟的双真人副屏演示。

## 源码依据

- [Group Context API 契约](../../../apps/bcs/docs/group-context-api.md)与[读取权限规则](../../../apps/bcs/crates/application/v1/bcs-app-group-context/src/permissions.rs)。
- [Group Context 根路由处理器](../../../apps/bcs/crates/adapters/http/bcs-http/src/routes/group_context.rs)、[当前真人入口](../../../apps/bcs/crates/adapters/http/bcs-api-http/src/v1/human.rs)与[原生 BCS 身份响应](../../../apps/bcs/crates/adapters/http/bcs-http/src/routes/me.rs)。
- [nextgen 本地代理](../../../apps/frontend-nextgen/config/config.local.ts)与[协作命名空间路由](../../../apps/bcs/crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs)。
- [协作定义校验](../../../apps/bcs/crates/services/bcs-collaboration-runtime/src/definition.rs)、[协作运行时](../../../apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs)与[主持流程](../../../singlebox/agents/6bots_undercover_game_profile/referee/skills/undercover-game-referee/scripts/undercover.py)。
