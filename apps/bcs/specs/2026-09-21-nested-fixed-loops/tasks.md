# BCS 嵌套固定 Loop 实现任务

- 日期：2026-09-21
- Rebase 复核：2026-09-22，代码基线 `f49424bc4`；保留 10 项任务，补入既有消息历史合同。
- 依据：[spec.md](spec.md)，含预览发布兼容与简化后的设计。
- 状态：实施中；准备清单、T01–T08、T10 已完成；2026-10-13 已补真实 MySQL 持久化回归，T09 仍缺锁/连接池/满页恢复容量测量。
- 组织：10 项研发任务，另设准备、交付验收与发布清单；任务按合并后的职责重新编号。
- 交付：在现有 compile-to-DAG、Node Run、snapshot 和恢复机制上支持有界嵌套 Loop。
- 路径约定：下文代码路径相对仓库根目录；新增文件的具体名称可按职责调整。

## 1. 执行与完成规则

每个任务完成实现、最近的回归/合同测试，并将实际命令、结果和限制记录到同目录
`validation.md` 后，才能勾选。测试失败、未运行或只通过编译都不能记为完成。
`validation.md` 随实施建立，本次任务拆解不伪造实现证据。
涉及合同、配置或使用行为的文档随对应研发任务更新；交付清单只核对完整性，不把文档更新
推迟到最后。每项 spec 验收用例仅指定一个主责任任务，后续集成测试引用其证据并验证跨层行为。

实施保持以下边界，具体语义以 spec 为准：

- entry/result 仍为直接普通叶子；不增加 LoopRun、子 Run、运行时 stack 或新调度器。
- 一套递归编译算法；v1/v2 差异限于 identity、格式与投影边界。普通 DAG、单层新计划和历史
  v1 plan 保持原合同，snapshot 恢复与 rerun 不调用当前编译器。
- `loop_path` 表达完整实例；旧 loop 字段等于最近一帧；descriptor 复用 map key 表达层级。
- 新准入开关默认关闭；预览受编译 limits 约束；已准入 Run 不重新检查新增准入开关。
- 默认无 DDL 迁移；不改历史 migration。若发现真实 schema 限制，先补充 spec 和必要性。
- 改动限于本需求所需的 `src/` 文件；复用既有门禁，不修改根目录脚本/配置来绕过检查。
- 既有消息身份、历史 checkpoint 和 Session cutoff 选读均复用；不新增历史版本、存储或开关。
- 遵循当前用户范围约束：已有文件保留布局，只做必要局部修改；新增职责可放新模块，
  不拆分或搬迁既有实现/测试。检查所有改动源码行数；既有超长文件导致门禁失败须如实记录，
  不放宽检查，不运行全局 formatter。application/core/port/store/adapter 保持 BCS 分层。

## 2. 准备清单

准备完成后进入研发任务。复核证据见 spec §17，不将已通过的旧基线记作嵌套实现完成。

- [x] 记录开始实施的 commit 和工作区状态，运行现有 compiler/snapshot/context 基线；
  固定旧 plan JSON、hash、execution ID、prompt 与公共 payload golden，不随新编译器重生成旧 golden。
- [x] 建立两层 BotTask、三层 HumanInput 两套定义 fixture；两层采用 spec §5.4 示例。
  编译边界、同名、并行与故障测试从它们构造变体，不复制成多套业务故事。
- [x] 复用现有历史 `history_loop/history_identity/history_persistence/history_read` 测试及
  consumer fixture，为路径传播、原子接受、补写和 cutoff 选读确定最近回归入口。
- [x] 列出实际需修改的文件和行数，记录局部修改范围与已有门禁风险。
  当前重点是 runtime 的 `runtime.rs`、bootstrap 的 `config.rs/server.rs`、panel 的
  `StateMachineRunView.tsx`；store `lib.rs` 如需修改同样处理，不将拆分列为本需求的前置任务。

完成条件：旧基线通过；fixture 可解析且两层预期为 17 个叶子；改动范围和回归入口明确。
当前拒绝嵌套的旧测试，在 T02 中按新边界更新，不靠删除测试隐藏行为变化。

## 3. 研发任务与依赖

| 任务 | 交付内容 | 前置依赖 |
| --- | --- | --- |
| T01 | 路径与配置合同 | 准备清单 |
| T02 | 递归校验与编译 | T01 |
| T03 | 快照兼容与持久化 | T02 |
| T04 | 配置注入与创建准入 | T03 |
| T05 | 上下文与 HumanInput | T03 |
| T06 | DAG 推进与故障恢复 | T04、T05 |
| T07 | API、事件、消息与 CLI | T06 |
| T08 | panel 与 Frontend | T07 |
| T09 | 容量与数据库成本 | T06 |
| T10 | 真实服务故事与故障变体 | T08 |

表中只列直接前置依赖，表示该任务独立完成验收所需的条件。消费者可依据 T01 的合同先
开发，不要求按表逐个提交。各任务的局部验收不依赖后续任务；真实服务集成与混合版本演练
分别归入 T10 和交付清单。实施顺序不等于发布顺序，任何中间版本部署均遵循第 5 节。

### T01 — 路径与配置合同

依据：spec §4、§7.2、§8、§11.1、§12.1。落点：`apps/bcs/crates/contracts/bcs-domain/`、
`apps/bcs/crates/service-api/bcs-service-api/`、`apps/bcs/crates/service-api/bcs-config-api/`、
bootstrap 配置类型及直接构造这些类型的调用方。

- [x] 增加稳定的 `StateMachineLoopFrame`，扩展 compiled/public execution metadata 和
  `LoopContext.loop_path`，同步必要的导出、构造器及测试 double。
- [x] 固定 v1 省略新增字段、v2 Loop 叶子必有非空路径、顶层普通节点无 execution 的规则；
  校验 frame 范围和最近一帧与旧字段一致，保持旧字段顺序与 hash 所依赖的序列化。
- [x] 提供统一只读路径访问器：v1 单帧、v2 完整路径、普通节点空路径。
  snapshot reader 必须先校验原格式再使用此视图，调用点由 T03 接入；视图不写回 snapshot 或旧 payload。
- [x] 定义正整数 `max_fixed_loop_depth`（默认 4）和 `nested_loop_execution_enabled`
  （默认 false），保留其他预算默认值，校验与总 Loop 开关的非法组合。
  同步配置示例、字段说明和相关 Service API Context；实际注入归 T04。

验收：新旧 DTO round-trip、旧字节 golden、只读访问器及配置解析测试通过；
缺路径、空路径和字段矛盾按版本拒绝；缺省/显式值、零/负值/错误类型/未知键和开关组合有覆盖。
不依赖编译器、服务启动或嵌套运行即可完成本项验收。

### T02 — 递归校验与确定性编译

依据：spec §5–7、§11.1、§13。落点：runtime `validation.rs`、`fixed_loop.rs`、
feature 推导与容纳新增逻辑的编译模块，以及 BCS CLI authoring reference。

- [x] 将 raw YAML 与 typed definition 中的嵌套禁止逻辑改为有界 scope/path 校验；
  递归检查未知键、ID、节点类型、assignee/Judge、outcome 分区、可达性和单入口/结果。
- [x] 保留 entry/result 为普通叶子、局部 target、body 禁止 final_output 等限制；
  错误携带深层 authoring path，不使用生成 ID 代替定位。
- [x] 自动推导 `state_machine.loop.nesting`，用于 validation/capability 和保存的计划；
  显式声明 feature 不改变实际结构的 compiler 版本选择。
- [x] 用 checked arithmetic 预计算深度和递归叶子总量，包含未选中分支；在遍历时限制深度。
  展开/序列化继续核算字节预算，失败不产生 Run、snapshot 或 dispatch。
- [x] 用统一 scope 编译流程递归展开，维护完整祖先 frames、普通入口和逻辑出口；
  每个 Loop 首轮入口、continue、break、exhausted 都解析到正确实例。
- [x] 按实际最大深度选择 compiler v1/v2；实现 spec §7.3 的 v2 ID 编码、确定排序与碰撞检测，
  顶层普通节点沿用原 ID。保持所有单层 v1 golden。
- [x] 生成 node/edge metadata、entry/result 标志和精确 previous-result ID；
  继续使用一个最近层 `loop_route`，保留 ControlOnly/Artifact 的边语义。
- [x] 对最终图复用 DAG 校验，验证唯一 final、metadata 完整和有界序列化。
  同步 authoring 示例与限制，明确新合同替代旧设计中的嵌套禁令。

验收：C01–C10 对应的纯校验/编译测试通过，示例恰为 17 个叶子；同名/跨实例无碰撞，
串行/并行子 Loop 与出口连边正确；深度/节点/字节超限无副作用，旧 v1 golden 不变。
本任务不要求公共 preview 已接通；准入 warning 由 T04 验证，完整 preview 投影由 T07 验证。

### T03 — 快照兼容与持久化

依据：spec §9、§10.4。落点：runtime `snapshot.rs`、`definition.rs`、创建路径、snapshot/history repo 合同，
`apps/bcs/crates/services/bcs-collaboration-store/` 及相关 conformance。

- [x] 显式分派 compiler v1/v2，按原序列化合同验证 envelope/hash；未知版本报错。
- [x] 验证 v2 的 authoring 路径、轮次、叶子内容、完整展开集合、唯一逻辑键、边的 scope/路由/
  projection 和 previous result，拒绝缺失、重复或额外映射。
- [x] 校验后共用 T01 的只读视图；不调用当前编译器、不替换历史 ID，读取不应用当前编译 limits。
  同步 feature reader 支持，保留正常跨轮次 continue 的合法性。
- [x] 通过既有 plan JSON/hash/compiler 字段保存嵌套计划，补 Memory/SQLite/真实 MySQL 的
  round-trip、不可变性、env 隔离和写失败 conformance；无必要不修改生产 store。
- [x] 验证完整 Run/Node materialization 和 snapshot 成功前不得派发；任一步写失败须返回错误，
  保持原创建事务及现有恢复合同，不增加边运行边编译路径。
- [x] rerun 复制源 authoring、plan、bindings 和 generated IDs，在新 run_id 重建初始状态，
  不复制 artifact/轮次进度或历史 checkpoint，不重编，也不按新的编译 limits 拒绝历史计划。
- [x] 扩展既有历史接受 conformance，验证完整 execution 随 artifact/checkpoint 原子保存，
  frozen payload round-trip、env 隔离和幂等性；复用消息身份/物理 ID helper，历史格式仍为 1。

验收：reader、三存储及 rerun 测试各自保留；旧字节/hash 不变，降低 limits 后仍可读/复制。
篡改后重新 hash 也不能通过错误映射校验；写失败不伪成功，创建屏障、session activation、
CAS 和批量写原子边界保持。真实 MySQL 未运行不能以 SQLite 代替。

### T04 — 配置注入与所有创建入口的准入

依据：spec §12–13。
落点：runtime 的 start/一次性执行/Session 创建与 rerun 用例、validation 调用和 bootstrap wiring。

- [x] 汇总所有创建 Run 的入口，共用基于实际 authoring/已保存计划的嵌套判断，
  新建和 rerun 都要求两个执行开关，拒绝发生在创建副作用之前。
- [x] 将 T01 配置贯通 memory/DB 等实际 runtime 构造路径，非法组合在启动时失败；
  core 不读原始环境变量。同步深度 1 的发布配置示例与开关说明。
  保留历史 persistence/cutoff 独立 wiring，开启嵌套不隐式切换历史读写配置。
- [x] 递归解析嵌套叶子的能力与参与者 bindings，保留既有权限、Session、Judge 和绑定检查；
  Loop 本身不分配 Bot、不生成 Node Run。
- [x] 新准入开关不进入已准入 Run 的推进/恢复判断；保留总 Loop 开关的原要求。
  查询历史不受准入影响，rerun 仍受准入控制。
- [x] 验证 validation 的策略层：满足 limits 时执行准入不拦截已编译结果，并生成现有 warning；
  超限仍返回错误。启动使用现有 `InvalidRequest`，adapter 的完整映射在 T07 验证。

验收：逐入口测试开关矩阵、配置 wiring、feature 推导和 warning 行为；关闭新准入后
start/rerun 零副作用，显式 feature、保存的较深 plan 或一次性入口不能绕过检查。
本项验证准入策略不被用于已有 Run；实际运行、恢复与排空由 T06 验收。

### T05 — LoopContext、Prompt 与 HumanInput

依据：spec §8。
落点：runtime `loop_context.rs`、prompt/人工入口投影、
`apps/bcs/crates/services/bcs-channel/src/human_input_notification.rs` 及通知测试。

- [x] BotTask/HumanInput entry 共用上下文构造器；只读取最近所属 Loop 上一轮结果，
  校验 run、祖先路径、轮次相邻、result 身份、Completed/outcome/artifact/时间和选中 continue 边。
- [x] 子 Loop 在每个新父轮次的首轮 previous_result=null；失败时拒绝派发缺上下文任务。
  retry 沿用同一 execution/context，不通过“最近完成节点”回退取值。
- [x] 嵌套 prompt/人工查询/通知传播完整路径；保持 v1 prompt、首轮 null 和普通非 entry 行为。
  发送前保存渲染文本及 request identity，恢复重发复用已保存内容；人工 prompt 的历史接受
  也冻结相同 execution 和受众，不在补写时重新查询上下文。

验收：跨父轮次结果不串用；一次 context 构造最多读取一个 previous-result Node Run，
不逐祖先查 DB；direct-assignee/fixed_group 隐私与通知 golden、旧轮次/attempt 回复拒绝、
通知提交后 crash/重试复用文本和 request identity 的测试通过。

### T06 — DAG 推进、skip/join 与故障恢复

依据：spec §6、§9.3、§12.1。
落点：runtime `progression.rs`、相关 runtime 恢复模块、`tests/runtime_progression/`。

- [x] 用嵌套计划验证现有 reachable/selected-edge 推进，必要修复限定在既有 DAG 算法；
  内层退出只跳过该实例后续轮次，外层退出跳过后续完整子树。
- [x] 验证并行子 Loop/shared join：所有静态 upstream 已终结且有真实选中边才推进，
  不混入 ControlOnly 或未选中边的 artifact，不遗留无望完成的 Pending。
- [x] 在 inner exit、outer continue、outer break 后部分 skip、人工通知提交等边界注入故障；
  重启/换主从 immutable plan 和已提交事实补做，不依赖进程内计数器。
- [x] 覆盖 retry/timeout/Judge、cancel、Session activation、lease fencing 与迟到终态竞争；
  保持每个 attempt 最多一次有效结算，不把节点失败转成隐式 Loop break。
- [x] 复用历史接受/投影屏障，覆盖写消息失败、已写未确认、Judge 失败后 retry：旧 attempt
  路径/正文/受众/时间不变，投影完成前不返回接受成功或派发依赖后继，重复补写不增加 sequence。
  终态 Run 及关闭嵌套准入/历史 persistence 后仍能补写，不重发网络通知、不删除 Pending 事实。
- [x] 引用 T04 的准入策略测试，验证关闭新增准入后已准入 Run 可推进、恢复并排空，历史可查询；
  引用 T05 的上下文测试，验证 entry retry 不改身份，下一 iteration 的 attempt 从 0 开始。

验收：示例得到 15 Completed、2 Skipped；前台和 FO 的最终集合及结果一致；
恢复不重复有效结算、不跨实例推进，重复扫描收敛。

### T07 — API、事件、消息、CLI 与可观测性

依据：spec §10.1–10.2、§10.4、§13、§15.4。
落点：runtime `loop_graph.rs/runtime_message_history.rs/runtime_history_query.rs`、共享历史 projector、
两套 HTTP adapters、service-api 投影、eventing/message/channel、BCS CLI，
`apps/bcs/api-contracts/v1/openapi/`、`apps/bcs/api-contracts/events/v1/` 及各自 conformance。

- [x] 从共享映射传播 start/get/rerun、Node/Run graph、preview、PendingHumanNodeView、
  HumanInputReadyEvent 和各 node 事件的 execution metadata。
- [x] 服务端递归生成 `graph.loops`：map key 为完整逻辑路径，body IDs 仍为直接子节点；
  单层 key/字段保持旧样，不增加重复路径字段，子 Loop 不冒充叶子。
- [x] 将路径写入消息 `metadata.state_machine.execution` 并查询透传；messages-only 不读
  Run/Node/plan 补路径，空页/错误不回退。验证 Session cutoff 前/等于/后及旧 Session 新 Run，
  保留混合会话 owner、受众和窗口补偿，隐藏持久记录不能被快照替代。
- [x] 复用公共历史 projector，panel/prompt/output 不引入 Run 级对话分组；扩展现有 consumer
  fixture 验证 Workbench/CLI 保留各父轮次及 attempt，普通聊天/published result 聚合不变。
  校验通知 checkpoint 和回放不丢上下文；CLI authoring capability 消费同一合同。
- [x] 同步 strict OpenAPI/Event schema、catalog 和新旧 fixtures，保留未知字段拒绝；
  按所属计划版本验证路径必填和最近帧一致，不能整体放宽 schema。
- [x] 复用既有 Loop 指标，按各层普通 entry/result 首次有效 CAS 计数；retry 不重计。
  日志可显示路径，不增加 loop/node/iteration 高基数指标 label；同步观测和 CLI 说明。

验收：同一 execution 在各 API/Event/消息/人工入口中的路径一致；旧 payload 省略规则保持；
两套 HTTP 与 CLI 合同测试、新客户端消费旧 payload 的回归通过，adapter 不新增选路策略。
引用 T02 的 feature/编译格式与 T04 的准入策略证据，完成公共 preview 的严格合同验收：
深度 1 拒绝且无图，允许深度时执行关闭仍返回完整嵌套图及 warning；包括内外同名 Loop。

### T08 — panel 与 Frontend

依据：spec §10.2–10.3、§15.4。
落点：panel `apps/bcs/assets/panel/src/state-machine-graph/` 和 `StateMachineRunView.tsx`，
`apps/frontend/src/services/backend-api/BcnController.ts`、GroupChat 的 loop layout/preview 组件与测试。

- [x] 消费 T07 的 descriptors；两个前端按 map key 建递归容器，按 loop_path 定位叶子；运行轮次选择 key
  包含祖先轮次，切换外层后正确切换内层历史和状态。
- [x] 实现 preview 首轮、runtime 活跃/最后进入轮次、未进入占位、历史展开和路径详情；
  区分 inner 与 outer 出口，缺映射时给明确错误，不按重复局部 ID 合并。
- [x] 补两个前端的单层回归、三层、同名内外 Loop、兄弟 Loop、切换父轮次和人工节点测试。

验收：旧 payload 可用；嵌套预览不因子 Loop 无 Node Run 而报缺叶子；
未来节点不显示为已进入历史，服务端选路不由 UI 重算。运行对应布局/组件测试及 panel 构建验证。

### T09 — 资源与数据库成本验证

依据：spec §11、§15.4。落点：runtime/store 测试 harness 与相关查询计数测试。

- [ ] 测量 17 节点示例、接近 2048 节点、接近 4 MiB、32 个并存 Run、大范围 early break、
  DB 持续失败和重复恢复；分别记录 Repo 调用、SQL、读写行数、事务大小及扫描耗时。
- [ ] 在相同历史 persistence/cutoff 下与同形扁平 DAG 对比：路径解析不新增 DB 查询，
  previous result 不随深度增加读取次数；
  保持既有批量 INSERT、48 行分块、CAS、env 隔离、扫描有界工作及退避。
- [ ] 分别测量历史启用/关闭，计入接受事务、消息幂等写和确认、checkpoint/messages 字节数；
  验证历史扫描每页 100/基线 5 秒、终态批量修复标记与消息查询窗口补偿不因 depth 扩大。
  4 MiB 仅约束 plan，不能作为整个 Run 的消息存储上限。
- [ ] 验证失败 Run 不阻塞后续页面，共享连接池和锁持有时间可接受；
  任何必要的 batch read/skip 优化先记录实测依据和合同，再补 conformance 后实施。

验收：提交成本报告与查询数/批次断言，显式记录测试配置和观测范围；
不把提前 break 宣称为 O(1)，不以小图结果推断满容量，不新增全局缓存或放宽默认预算。

### T10 — 两套真实服务故事及故障变体

依据：spec §15.5。落点：`apps/bcs/scripts/e2e-test/` 的既有 story 注册与 runner、
bootstrap/provider 集成测试及准备清单中的 fixtures。

- [x] 两层 BotTask 故事走真实 API/CLI：第一父轮次 inner early break、outer continue，
  第二父轮次 inner exhausted、outer done；核对 spec 的 17/15/2 轨迹。
- [x] 三层 HumanInput 故事至少一层进入人工第二轮；检查 query、通知、回复身份、
  两个父轮次的内层实例隔离，并覆盖外层 early break 的完整子树跳过。
- [x] 复用两套故事注入 inner exit/outer continue/部分 skip 故障，重启后继续，
  发送旧轮次与旧 attempt 回调，核对没有错误推进或重复有效派发。SQLite live 在 inner exit、
  outer continue 和 outer break 前重启，并在 skip 批量写失败后再次重启；独立 Bot live 与本测试
  分别拒绝旧 delivery callback 和旧 Human 回复。
- [x] 至少一套故事开启历史 persistence 并命中 messages-only，复用故障变体验证接受后的补写、
  关闭嵌套准入后恢复与跨实例路径保留；cutoff/历史开关完整矩阵复用 T07 的定向证据。
  SQLite live 通过 `bcs_messages` 写失败验证已接受 checkpoint/artifact 的幂等补写，并覆盖通知
  checkpoint、关闭准入后恢复和完整 execution path。
- [x] 注册进现有真实服务测试与覆盖入口；联合检查最终状态、artifact、metadata、graph、
  消息历史和适用的人工入口，不能只断言 Run completed。

验收：故事及故障变体在真实服务上通过并保留 artifacts；单元 mock 不能替代 live 验收。
引用 T03 的三存储 conformance、T06 的恢复测试和 T08 的双前端证据；
live 只验证真实跨层链路与故障变体，不对每个 DB/UI 重跑完整结构组合。

## 4. 交付验收清单

研发任务完成后核对 spec §17 的功能条件，并在实际开放前完成发布/回滚演练。
这里汇总证据，不复制各任务的用例实现。

- [ ] T01–T10 均有完成证据；核对已随任务更新的 authoring/schema、相关 `CONTEXT.md`、
  `apps/bcs/docs/observability/fixed-loop.md`、配置和运行图说明与实现一致。
- [ ] 执行受影响 crate、strict schema、BCS architecture、panel/Frontend 与 BCS/BCN 门禁；
  运行统一 singlebox coverage 及 artifact verifier，沿用完整分母和原阈值。
- [ ] 归档提交、命令/退出结果、golden、真实 MySQL、故障、容量和 live artifacts；
  skipped/ignored/无法运行项注明原因及未验证边界，不能计作通过。
- [ ] 在验证环境演练第 5 节的消费者先升级、混合版本深度 1、开放预览/执行顺序；
  再关闭嵌套准入并保留总 Loop 开关，验证任务恢复排空及历史读取。演练先于实际开放。
- [ ] 检查所有变更文件及行数、diff 空白、无关格式化、历史迁移和临时产物；
  将下方覆盖索引逐项对应到实际测试。未达标项仍保持未完成，嵌套准入保持关闭。

完成条件：spec §17 的功能条件、原 DAG/单层回归及发布/回滚演练均有证据。
代码交付与实际部署分别记录；部署清单未执行不能记为已发布。

## 5. 发布清单

依据：spec §12。本清单记录实际部署，演练结果见第 4 节。
兼容消费者可提前发布，开放能力须在交付验收与以下兼容条件都完成之后。

- [ ] 核对实际 API/Event 消费者、CLI、panel、Frontend 和仍在使用的页面/缓存；
  先发布兼容旧 payload 的新消费者，与服务端打包的消费者可随下一步升级。
- [ ] 滚动升级所有服务端 reader/compiler，升级实例显式设置深度 1、嵌套准入 false；
  验证新旧实例均不返回嵌套 preview，全部查询、回调和恢复实例已支持 compiler v2。
- [ ] 消费者全部兼容后，统一调整深度到目标值（默认 4），保持新执行关闭；
  验证 nested preview 可用、start/rerun 被拒绝，特别检查内外同名 Loop。
- [ ] 第 4 节全部通过后，按部署配置开启嵌套准入；记录实际配置、版本、验证结果与演练证据位置。

需要回滚时，执行已演练的流程：只关闭嵌套准入，保留总 Loop 开关以排空已有 Run；
若关闭新预览，再将深度降为 1。历史 v2 图/消息仍须可读，保留兼容 reader/客户端，
不重写快照、不直接降级旧二进制。记录实际回滚结果，无需为了勾选而在生产额外制造一次回滚。

## 6. spec 验收覆盖索引

每个编号只出现一次且只有一个主责任任务。主任务负责用例落地与证据汇总；
引用列表示复用的前置证据或后续集成验证，不增加第二个完成责任人。

| spec 验收项 | 主责任任务 | 证据引用与集成验证 |
| --- | --- | --- |
| C01、C02、C03、C04、C05、C06、C07、C08、C09、C10 | T02 | 引用 T01 的 DTO/配置 golden |
| C11 | T07 | 引用 T02 的 feature/格式和 T04 的 warning 策略；T08 验证展示 |
| R01、R04、R05、R06、R07、R09 | T06 | 引用 T05 的上下文测试；T10 验证真实服务链路 |
| R02、R03、R08 | T05 | T10 验证真实人工输入链路 |
| R10 | T07 | T08 验证 UI 消费；T10 验证历史透传 |
| F01、F02、F03、F04 | T06 | T10 复用故障场景做 live 验证 |
| F05 | T05 | T06/T10 验证恢复链路 |
| F06、F07、F08 | T03 | T06 引用历史 plan 读取/复制合同 |
| F09 | T06 | 引用 T04 的拒绝准入证据；交付清单演练排空 |
| F10 | T04 | 引用 T01 的配置校验与 T03 的未知版本拒绝证据 |

其余验收按职责归属：§15.4 的历史接受存储合同归 T03、投影屏障/补写故障归 T06，
选读/消费合同归 T07、双前端归 T08、成本归 T09；
§15.5 的真实故事归 T10，完整门禁与发布/回滚演练归第 4 节。第 5 节记录实际发布。

## 7. 验证命令入口

以下从仓库根目录执行，使用仓库规定的开发依赖环境。命令是已存在的入口；
实施中新增的测试 target 应加入对应命令或由 crate 全套覆盖。

```bash
# 合同与纯编译/上下文基线；实施后同样运行新增的嵌套测试。
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-domain -p bcs-config-api
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-collaboration-runtime \
  --test fixed_loop_compiler --test fixed_loop_snapshot --test fixed_loop_context

# 快照、运行时与通知；与当前切片有关时执行。
cargo test --manifest-path apps/bcs/Cargo.toml \
  -p bcs-collaboration-store -p bcs-collaboration-runtime -p bcs-channel

# Rebase 新增的历史接受、补写与选读回归；消费测试需先安装 Frontend 开发依赖。
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-collaboration-runtime \
  --test runtime_progression history
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs --test state_machine_history_read
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message -p bcs-message-store
cargo build --manifest-path apps/bcs/Cargo.toml -p bcs-cli
node --test apps/bcs/tests/consumer/state_machine_history.cjs

# 真实 MySQL：先通过既有测试环境提供 BCS_TEST_MYSQL_URL，指向可销毁的空测试库。
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-collaboration-store \
  --test mysql_store real_mysql_fixed_loop_snapshot_and_rerun_contract -- --ignored

# 严格 wire 合同与架构门禁。
python3 -m pytest apps/bcs/tests/openapi apps/bcs/tests/event_contract
bash apps/bcs/scripts/ci/arch-check.sh

# panel 和 Frontend：Frontend CI 仅 lint，布局/组件回归需单独执行 Jest。
npm --prefix apps/bcs/assets/panel run verify
npm --prefix apps/frontend test -- --runInBand src/pages/GroupChat
FRONTEND_CI_PACKAGE_MANAGER=npm bash apps/frontend/scripts/ci_test.sh

# 发布前的完整模块与共享栈门禁。
bash apps/bcs/scripts/ci_test.sh --fast-fail
bash singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

CI 总入口不能替代被忽略的真实 MySQL 测试或未注册的故障变体；确认实际执行列表和结果。
定向诊断可复用已有 focused 入口，最终仍运行默认 all-module singlebox gate。
单纯 lint-only pre-push 不代表这些验收完成。
