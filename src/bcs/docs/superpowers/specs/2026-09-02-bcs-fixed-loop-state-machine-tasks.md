# BCS 固定 Loop 开发任务追踪

- 建立日期：2026-09-14
- 状态：开发中；S1（FL-01～FL-06）已完成；S2（FL-07～FL-09）已完成 snapshot 增量和三类 Store 验收；本地 MySQL 完整迁移链已验证，部署历史核对与发布升级仍由 FL-28～FL-29 跟踪；S3 正常执行路径（FL-10～FL-14）已完成受控验证；FL-15 通用恢复 11/11 完成，FL-16 受控故障验证 8/10 完成，真实产品多实例/强杀与完整 Singlebox/FO 仍未完成；S4 已完成 FL-17/18 API、Event 与消息 metadata、FL-19 IM 通知投影与请求重试、FL-20 面板、FL-21 Frontend preview、FL-22 CLI 和 FL-23 观测，S4 全部完成；生产默认关闭；2026-09-17 按用户要求增加本地实验执行开关，效果测试不替代 S5 发布验收。
- 主 spec：[固定 Loop 与上一轮结果传递设计](2026-09-02-bcs-fixed-loop-state-machine-design.md)，以其 2026-09-14 修订为基线。
- 实施顺序：沿用主 spec §20 的五个切片；本文把切片拆成可执行、可验收的任务。
- 关联合同：[State Machine 持久化、FO 与 rerun](2026-08-19-bcs-collaboration-state-persistence-fo-rerun-design.md)，事务边界以 §6.3 / D24 为准。
- 所有权：BCS；消费者包括 BCS panel、Frontend Definition preview、CLI 和 IM 通知。
- 本文沿用现有 spec 所在目录，不迁移或复制设计正文。代码路径均相对仓库根目录。
- 最新进度（2026-09-17）：**24/30** 顶层任务完成；FL-24 全量合同回归和 FL-25 三条 HTTP live story 已验收。剩余 FL-16（8/10 子项）、FL-26～FL-30；正常路径通过不替代真实 FO、性能与发布验收。

## 1. 追踪规则与固定边界

1. 开始任务时，在 §9 执行记录登记任务 ID、负责人和状态；未完成任务保持 `- [ ]`。
2. 任务的实现、合同传播和本项验证全部完成后，才改为 `- [x]`，并记录 commit/PR、实际命令及结果。
3. 状态使用 `未开始 / 进行中 / 阻塞 / 已完成`。阻塞必须写明缺少的依赖或环境；未运行和被跳过的测试不能记为通过。
4. 依赖指完成验收所需的前置任务；合同稳定后可先用 fixtures 开发消费者。任务 ID 保持稳定，拆分任务追加 ID，不重排历史编号。
5. 变更行为先同步主 spec，再更新任务及对应测试。本文负责进度，不另行定义与 spec 不同的实现语义。
6. 普通 acyclic 能力已存在不代表 Loop 任务已完成；必须有 v2 场景的验证证据。FO 相关缺口单独追踪，不能由正常路径通过代替。

开发期间必须保留以下边界：

- v2 authoring 使用 `hierarchical`，执行计划为展开后的 `acyclic`；Loop 控制节点不创建 Node Run。
- `continue_outcomes` 必填非空，`break_outcomes` 必填但可为 `[]`。无 Judge 时只能 `[complete] + []`。
- break 为空时执行至最大轮数，再执行非空 exhausted targets；耗尽是路由，不是 Run 的终态。后续节点执行或等待人工时 Run 仍为 `Running`。
- 每轮使用独立 execution node ID；iteration 从 1 开始，attempt 从 0 开始。不得重置旧 Node Run 复用下一轮。
- 上一轮结果只取指定 result Node Run 的持久化事实；Bot prompt、人工查询和人工通知复用同一 LoopContext 构造器。
- Run snapshot 是执行和 rerun 的权威；不得用当前 Definition/compiler 补编历史计划。
- 保留现有 Run/Node 创建事务、rerun 防重事务、单节点 CAS 和已有 eventful transition 局部事务。completion、skip、readiness/dispatch 分步提交，写入错误必须传播。
- 不新增覆盖所有展开节点的大事务、逐 edge checkpoint、可漂移的 iteration 状态行、Loop 专用 scheduler 或恢复 worker。
- 首次外部发送前保存恢复所需载荷；不能把缺 checkpoint 直接视为未发送。完整 FO 尚未通过时，能力默认关闭且不得宣称 FO-safe。
- 不扩展到嵌套 Loop、任意 goto、动态节点/参与者、通用 mutable State、局部 rerun 或 Loop 专用公共事件。

## 2. 切片与完成条件

| 切片 | 任务 | 交付物 | 切片出口 |
| --- | --- | --- | --- |
| S1 Contract 与编译器 | FL-01～FL-06 | v2 合同、严格校验、确定性 plan、preview | 可验证和预览；生产入口仍拒绝创建 v2 Run |
| S2 Snapshot 与 Store | FL-07～FL-09 | 增量迁移、完整 snapshot、历史读取与 rerun | 三类 Store 验证通过；不回退重编 |
| S3 Runtime 与恢复 | FL-10～FL-16 | Loop 推进、共享上下文、事件隔离、恢复 | 正常路径与故障恢复分别有证据；不提前开启能力 |
| S4 API 与消费者 | FL-17～FL-23 | API/Event/消息、IM、UI、CLI、观测 | 各消费者使用统一 metadata，v1 兼容 |
| S5 集成与发布 | FL-24～FL-30 | 回归、live stories、FO、性能和发布证据 | 主 spec §22 完成验收，或明确保持未启用状态 |

主路径为 S1 → S2 → S3；S4 可在 S1 合同稳定后分项推进。S5 的正常 E2E 可以早于 FO 完成运行，
但正常执行验证不解除 FL-26 / FL-28 / FL-30 的发布限制。

## 3. S1：Contract 与纯编译器

- [x] **FL-01 — 固化实现基线与 v1 回归入口**
  - 依赖：无。对应 spec §2、§18、§19。
  - 阅读根/BCS `AGENTS.md`、`src/bcs/CLAUDE.md`、相关 crate `CONTEXT.md` 和架构约束；确认 Definition、runtime、Store、通知及两套 HTTP adapter 的实际调用路径。
  - 运行最邻近的 v1 Definition/runtime 测试，登记基线；核对已有 timeout scanner、request-retry recovery 和尚缺的通用 progression recovery，作为 FL-15 输入。
  - 验收：记录可复现的基线、故障恢复缺口和受影响消费者；不把“新增 Rust 枚举”当作部署已经支持 Loop。

- [x] **FL-02 — 定义 v2 Loop、Execution Plan 与共享 metadata 合同**
  - 依赖：FL-01。对应 spec §7、§9.1～9.2、§10.1、§14。
  - 位置：`src/bcs/crates/contracts/bcs-domain/src/collaboration.rs`；`src/bcs/crates/service-api/bcs-service-api/src/application/collaboration_runtime.rs` 及相关合同模块。
  - 增加 Loop definition、compiler version、compiled node/edge metadata、`Artifact/ControlOnly`、`StateMachineNodeExecutionMetadata`、`StateMachineLoopRoute`、`LoopContext/PreviousLoopResult`；明确 wire names 与公共/内部合同归属。
  - 验收：serde/合同 fixtures 覆盖 v1 字段省略、首轮 `previous_result: null`、完整 v2 字段和非法字段组合；Domain `api_version` 仍为 `bcs.collaboration/v1`。

- [x] **FL-03 — 实现 authoring 与 body 严格校验**
  - 依赖：FL-02。对应 spec §7.2、§7.4、§8.1～8.3。
  - 位置：`src/bcs/crates/services/bcs-collaboration-runtime/src/validation.rs`、`src/definition.rs`（同 crate）。
  - 严格拒绝未知键、普通 node 携带 loop、Loop 携带 executable 字段、空/遗漏 continue、缺失 break、outcome 重复/重叠/遗漏、exhausted 冲突及空 break/exhausted targets；保留稳定 diagnostic code/path。
  - 验证固定非空 body、唯一 entry/result、可达性、唯一 terminal、无环、同 body 引用、无 body final_output；拒绝嵌套/交叉/重叠和外部跳入 body。继续执行现有 HumanInput、assignee、Judge 和 timeout 校验。
  - 验收：`definition_validation.rs` 覆盖 spec §21.1 的正反例，特别是 `max_iterations: 1 + 空 continue` 必须失败、无 Judge `[complete] + []` 合法、有 Judge 全 continue 合法、每个外层 target 存在且列表非空。

- [x] **FL-04 — 接入资源上限、能力推导和默认关闭的创建入口**
  - 依赖：FL-02、FL-03。对应 spec §7.1、§8.4、§17、§18。
  - 在配置合同/加载与 composition root 接入 `max_fixed_loop_iterations`、`max_fixed_loop_body_nodes`、`max_compiled_state_machine_nodes`、`max_compiled_state_machine_bytes`；记录配置默认值及选取依据，不固化为 wire 常量。
  - 校验编译前规模和编译后 bytes，使用有溢出保护的计数，超限在落库/创建 Run 前返回稳定错误；递归推导 body 的既有能力和五项 Loop features。
  - 区分可进行纯校验/preview 与可执行 v2 Run；所有首次启动/one-shot/rerun 创建入口受部署能力约束，S1 不开启执行。读取历史 v2 plan 不以重新编译代替能力检查。
  - 验收：零值、边界、溢出、node/bytes 超限、缺 Judge 能力、v1 使用 Loop、unsupported version/feature 均有明确结果；配置从 bootstrap 注入，不在 core 读环境变量。

- [x] **FL-05 — 实现确定性的 compile-to-DAG**
  - 依赖：FL-02～FL-04。对应 spec §8.5、§9。
  - 在 runtime crate 内实现纯编译逻辑；固定 ID namespace/算法和规范序列化/hash，登记 `bcs.fixed-loop.compiler/v1` golden fixtures。outer IDs 不变，body IDs 使用 spec 的完整 tuple，校验保留前缀冲突和长度。
  - 展开每轮 body，改写入口边、跨轮 continue、每轮 break、最后一轮 exhausted；固化 previous result 映射、edge projection role 和三种 loop_route，真实 outcome 不改写。
  - 复用现有 DAG entry/reachability/final/participant 校验；Loop 本身不进入可执行节点集合。
  - 验收：相同输入输出 byte-stable；覆盖 N=1、多 outcome、fan-out/join、多个非嵌套 Loop、不同 Loop 同名 body node 和 ID 唯一性；多个末轮 continue 正确映射同一个 exhausted logical_outcome。

- [x] **FL-06 — 发布 authoring 校验/preview 合同与示例**
  - 依赖：FL-03～FL-05。对应 spec §7.3、§7.5、§14.2、§16.1。
  - 同步 `src/bcs/api-contracts/v1/openapi/collaboration-definitions.yaml`、V1 validation application/adapter DTO 和 `src/bcs/crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md`。
  - preview 返回 flattened nodes、execution metadata、edge.loop_route 和可选 execution_graph_mode；authoring graph_mode 保留 hierarchical，preview ID 不承诺为未来 Run ID。
  - 验收：完整 Judge 示例与固定次数无 Judge 示例可通过新 validator；unknown keys/空 targets/资源上限错误定位到 authoring path；preview fixtures 包含真实 continue 与逻辑 exhausted。仍不可创建 v2 Run。

## 4. S2：Snapshot 与 Store

- [x] **FL-07 — 增加 MySQL/SQLite snapshot 迁移**
  - 依赖：FL-02。对应 spec §12.2、§18.2。
  - 位置：`src/bcs/migrations/mysql/`、`src/bcs/crates/bootstrap/bcs/src/migrations.rs`；按实施时的迁移序列分配新编号，不修改已发布迁移。
  - 为 definition snapshots 增加 nullable `execution_plan_json`、`execution_plan_content_hash`、`execution_plan_compiler_version`；MySQL/SQLite 类型遵循 spec。
  - 验收：fresh DB、历史库升级、重复执行通过；历史 v1 rows 保持 NULL，不主动重写历史 snapshot；不新增通用 State/iteration 表。
  - 当前进度：MySQL 028 / SQLite 029 已合并为 fixed_loop_runtime，统一包含 snapshot plan、failure_action 和两类恢复索引；已通过本项验收。SQLite fresh/legacy/重复启动/中途恢复通过；真实 MySQL 8.4.11 用原始 snapshot 建表语句和实际 027 executor 验证空表/历史 v1 行升级、字段类型与 NULL、DDL 失败不记成功、重复 apply 无 pending DDL、checksum 不匹配拒绝。本项验证范围为 snapshot 增量；016 已按后续要求合并并归档原文件；001/008 索引已修正并新增 028 保留旧记录升级；MySQL 语法已修正，真实 MySQL 8.4 的完整 001～028 链、重复 apply 和带旧 checksum 的 020→028 升级已通过；旧版拆分 016 的部署核对和实际生产升级继续由 FL-28～FL-29 跟踪。

- [x] **FL-08 — 扩展 snapshot repo 与三类 Store 实现**
  - 依赖：FL-05、FL-07。对应 spec §12.2、§21.4、FO spec §6.3。
  - 位置：`src/bcs/crates/service-api/bcs-service-api/src/port/repo/collaboration.rs`、`src/bcs/crates/services/bcs-collaboration-store/src/lib.rs` 及其测试。
  - 一次 snapshot 写入保存 Authoring Definition、完整 plan、compiler/hash 和 resolved bindings；同步 Memory、MySQL、SQLite mapping、repo doubles 和 conformance tests。
  - 验收：三种 Store round-trip 保留 node/edge metadata、ControlOnly、loop_route 和关联 ID；注入写失败可见；不扩大现有 Run/Node 创建事务，也不拆散已有 rerun 防重事务。
  - 当前进度：repo、Memory/MySQL/SQLite mapping 和 doubles 已同步；Memory/SQLite round-trip、不可变保存通过。真实 MySQL 8.4.11 通过生产 DB plugin 的 Text/Prepared 两种协议验证完整 node/edge/ControlOnly/loop_route/ID、bindings 与 hash/compiler round-trip、不可变保存、环境隔离和数据库约束导致的写失败传播。相关测试已纳入现有 MySQL CI。

- [x] **FL-09 — 强制历史 plan 读取与 rerun 原样继承**
  - 依赖：FL-08。对应 spec §12.3、§13.3、§18。
  - v1 保留原读取路径；v2 校验 plan 完整性、hash 和 compiler version；缺失/损坏/不支持版本时停止推进并返回明确错误，禁止 fallback 到当前 compiler。
  - rerun 复制 source plan、generated IDs、compiler/hash 和已固化 bindings；创建新的 Run/Node Runs，从第一轮和 attempt 0 开始，不复用 source artifacts/status。
  - 验收：更改或删除当前 Definition、替换当前 compiler 后，旧 Run/rerun 仍使用原 plan；并发 rerun 保持现有唯一性、lineage 和 Session activation 事务语义。
  - 当前进度：历史 plan loader 与 Store rerun 原样复制已实现；hash/compiler、完整映射、previous result、edge route 损坏均拒绝，测试证明保留与当前生成规则不同的 ID。Memory/SQLite 并发 rerun 只创建一个子 Run。受控 v2 application 已验证当前 Definition 不可用、资源上限降低时仍继承原 plan/bindings/IDs，新 Run 从第一轮 attempt 0 开始；并发 rerun 仅一次首节点派发。真实 MySQL 现已补齐原 snapshot 复制、Chat/Service 并发 rerun 唯一性、全部新 Node attempt 0、Service activation 只增加一次，以及 snapshot 写失败时 Run/Node/Session 事务完整回滚；FL-07～FL-08 的增量/Store 前置验收已完成。实际生产升级和完整 FO 仍由 S5 验收。

## 5. S3：Runtime 执行与恢复

- [x] **FL-10 — 使用 plan 启动 Run 并落实派发前屏障**
  - 依赖：FL-04、FL-09。对应 spec §11.1、§12.2、FO spec §6.3。
  - 位置：`src/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs` 及 application start/one-shot/rerun 路径。
  - 创建前完成 compile/validation，仅为 executable nodes 物化 Pending Node Runs；按现有顺序创建 Run/Nodes、保存完整 snapshot、启动和保存 opening，之后才派发 initial frontier。
  - 验收：snapshot/opening 写失败时零节点派发，错误向上传播；分步启动中断保留已提交事实，不能猜测缺失原始载荷；Loop 不产生 delivery/Node Run。仅在测试或明确受控配置下执行 v2。

- [x] **FL-11 — 完成 continue/break/exhausted 推进与 selected-edge 投影**
  - 依赖：FL-10。对应 spec §9.3～9.4、§11.2～11.4。
  - completion CAS 一次固化 outcome/artifact/completed_at；后续 skip、barrier、dispatch 分步执行。ControlOnly 仍参与选路、predecessors 和 skip，只排除通用 artifact 投影。
  - 未选 break 分支仍可由未来 iteration 到达时不得过早 skip；break 后未来轮次全部 Skipped；Loop 外 successor 等待全部静态 upstream Completed/Skipped，且只读取实际选中边的 artifact。
  - 验收：空 break 执行满 N 轮并继续 exhausted targets；耗尽不把 Run 直接设为 Completed/Failed；末轮真实 outcome 不变；唯一外层 final_output 与全体 Completed/Skipped 规则保持成立。v1 artifact 投影不变。

- [x] **FL-12 — 实现共享 LoopContext 构造器与 Bot prompt**
  - 依赖：FL-09、FL-11。对应 spec §10.1～10.3。
  - 只给 entry 注入 context；首轮 previous_result=None，后续精确读取 plan.previous_result_node_id 指定的已完成 Node Run，校验必需 outcome/artifact/completed_at。
  - 严格按 spec 的 header、字段顺序、空值与换行构造独立 prompt block；不重复加入 Upstream Outputs，不累计所有历史，不信任 Bot 回传的 Loop metadata。
  - 验收：首轮/第二轮/第三轮 golden tests、entry retry context 不变、缺失或损坏结果失败；验证非 entry 与普通节点行为不变，且并行完成顺序不改变上一轮来源。

- [x] **FL-13 — 接入 HumanInput 查询与内部通知 context**
  - 依赖：FL-12。对应 spec §10.4、§11.6、§14.5。
  - 位置：runtime `pending_human_node_view`；`src/bcs/crates/service-api/bcs-service-api/src/port/session_channel_outbound.rs`。
  - `PendingHumanNodeView` 与 `HumanInputReadyEvent` 增加可选 loop_context，调用同一构造器；v2 entry 必须完整返回，首轮 null 与字段省略严格区分。
  - HumanInput upstream_artifacts 复用 ControlOnly/selected-edge 过滤；每轮 response ref 绑定 execution ID，迟到回复、旧 attempt、错误授权不能完成当前轮。
  - 验收：两轮人工 entry fixtures、query/event/Bot context 同源；v1/非 entry 省略字段；缺少 plan/previous result 返回错误而不是发出缺上下文请求。

- [x] **FL-14 — 验证 retry、Judge、timeout、cancel 与事件隔离**
  - 依赖：FL-11～FL-13。对应 spec §11.5～11.6、§13.1～13.2、§13.4。
  - 检查 execution ID + attempt + delivery correlation 在 Bot/Human/Judge 路径中完整传递；继续使用现有 CAS 和稳定 delivery request ID。
  - 验收：重复 terminal event 不二次推进或覆盖结果；旧轮/旧 attempt 不污染当前节点；retry 只增加同节点 attempt、下一轮归零；Judge invalid outcome 不推进；尝试耗尽 Run Failed；cancel 后不派发未来节点并保留已完成审计。

- [x] **FL-15 — 补齐或接入通用 State Machine progression recovery**
  - 依赖：FL-10、FL-11、FL-14；使用 FL-01 的缺口清单。对应 spec §13.3、FO spec §6.3、§10。
  - 这是明确的可靠性前置任务：当前 timeout scanner/部分 request-retry recovery 不能替代它。若已有其他开发完成通用恢复，登记其 PR 和复用证据；否则在通用 State Machine 路径补齐，并在 composition root 接线。
  - 对 active Run 有界扫描、批量读取 snapshot/节点，由不可变完成结果重算 skip/readiness/dispatch/finalization；动作前重查 Run active 和依赖，使用目标节点 CAS。不为纯 progression 新增父节点 lease。
  - 跳过已 Skipped 的中间节点时仍遍历后代；completion CAS 未命中不意味着永远放弃补做。外部发送按既有稳定 key/载荷/deadline/歧义处理边界恢复。
  - 验收：普通 DAG 的分步提交故障恢复先通过；恢复不重跑已完成 Bot/Judge，不吞写失败，不新增 Loop 专用 worker 或逐 edge 持久化进度。
  - 当前进度拆分如下；已勾选表示该子项的实现和对应受控验收完成，父项须待所有剩余子项完成后勾选。
  - [x] **FL-15.1 — 通用扫描与 frontier 重算**：Pending/Running 有界游标、批量 Node 读取、不可变 snapshot 校验、全图 skip/readiness、目标 CAS、Service Run 收尾；复用默认关闭的实验 scanner。
  - [x] **FL-15.2 — 终态 Service Session 补写**：独立 Session 游标和 activation CAS；Completed/Failed/Aborted 正常路径与恢复共用条件更新，错误向上传递。
  - [x] **FL-15.3 — Failed attempt 决策恢复**：Failed CAS 保存 retry/fail_run 和原 error；重试只增加同一 iteration 的 attempt，不推断缺失/未知历史 action。
  - [x] **FL-15.4 — Judge 接管**：持久化不可变输入与 phase、短租约、owner/token/attempt/deadline fencing；terminal、Judge audit 与可选公共 Event 局部原子提交，已提交结果不重判。
  - [x] **FL-15.5 — Opening 与初始 frontier**：保存原文和固定消息主键，历史屏障完成后才派发 initial nodes；并发历史幂等，无额外 opening 外部 lease。
  - [x] **FL-15.6 — Bot dispatch checkpoint**：保存原请求/目标引用/deadline；未发送可接管，发送结果未知不重投；ACK/expiry 竞争按原失败决策收敛。
  - [x] **FL-15.7 — Chat 最终结果 publication checkpoint**：冻结原文本/目标/时间/deadline，正常路径与 scanner 共用 Pending claim、发送标记和 ACK fencing。Delivered 只补 Run 收尾，Failed 沿用原失败；Delivering 不重发，到原 deadline 后明确 Failed（可能已送达）。固定消息主键仅保证历史幂等，不宣称整个 message-flow 路由幂等。Memory/SQLite/MySQL 合同、四窗口独立进程测试通过；复用现表，无新增 migration。FL-18 的 Node 输出消息持久化不替代本项。
  - [x] **FL-15.8 — terminal IM notification checkpoint**：Session 完成前保存原收件人/文本/activation/期限，逐收件人进度 CAS、短 lease 和独立有界 checkpoint page 接入既有 scanner。仅明确未发送的预检失败可重试；Sending/结果未知不重发，部分成功不掩盖其他目标失败，IM 不改变终态 Run。Memory/SQLite/MySQL Text+Prepared、六窗口跨进程恢复及 scanner 专项通过；schema 合入未提交的 MySQL 028 / SQLite 029，无新增版本。
  - [x] **FL-15.9 — 缺失原始事实的终态收敛**：创建者按原时间保留不可续期的准备宽限期，到期可撤销准备权限，不以缺记录判断进程死亡。缺 snapshot/opening 通过 Run CAS + 类型化失败事实局部提交，原 Session activation 独立恢复；缺 dispatch 使用原节点 deadline/重试策略，无 deadline 时按原启动时间加 90 秒 FailRun。已补齐事实、已有 Provider run ID、结果和取消阻止误收敛；不重造载荷、不重发未知 attempt。Memory/SQLite/MySQL Text+Prepared、七窗口独立进程与前台写失败/旧 activation 专项通过，无新增 schema 或 migration。
  - [x] **FL-15.10 — 终态遗留 checkpoint 清理**：独立 Run 游标每页最多 32 个，逐 Run 有界 supersede 未完成 dispatch/Chat checkpoint、清除 Judge phase/lease；每次写重查终态，保留原结果/payload/token 和已确认送达事实。不发送消息、不删历史。覆盖半途写失败、并发、SQLite 两进程恢复、真实 MySQL Text/Prepared 和 scanner 游标；无迁移变更。
  - [x] **FL-15.11 — 等待人工通知的主动恢复**：通用 scanner 恢复已有 HumanInputRequest，核对原节点、Session activation 与 deadline，清理失效槽位并提升 Queued；只有缺请求才从原 snapshot 构造相同 ready event。新增 notification_pending 字符串状态并在外部 IO 前 CAS 为 Notifying，只有赢家能发送；既有 Notifying（包括旧版本 attempts=0）保守视作结果未知，不重发。Active/DeliveryFailed/终态不重新创建请求，原文本/目标/deadline 不变。六窗口 SQLite 跨进程恢复、双实例竞争、真实 MySQL Text/Prepared 和取消/迟到确认测试通过。无新 schema/migration；FL-15 已完成 **11/11** 子项，完整产品 FO 仍由 FL-16.9/16.10 与 S5 验收。


- [ ] **FL-16 — 覆盖 Loop 的故障窗口与 snapshot 恢复**
  - 依赖：FL-12～FL-15。对应 spec §13、§21.3～21.4。
  - 使用同一恢复路径测试 completion 后未 dispatch、future skip 中途、部分后代已 Skipped、exhausted 已选未 dispatch、finalization 中断；同时覆盖 DB 写失败和进程重启。
  - 验收：收敛至正确 frontier/最终状态；上一轮 immutable context 不变；重复恢复不产生第二次有效 completion；cancel 后无新派发；plan hash 错误停止推进；测试证明 recovery 不调用当前 compiler。
  - 当前进度拆分如下；完整 FO 不由单个 Store 合同或一次 Memory 恢复代替。
  - [x] **FL-16.1 — Memory progression 故障注入**：普通 DAG/Loop、并行 join、部分 skip、dispatch/Run 收尾写失败、immutable context、并发恢复、取消和损坏 snapshot。
  - [x] **FL-16.2 — SQLite 独立进程恢复**：continue/break/exhausted/finalization 分别跨 prepare/recover 两进程；移除当前 Definition、降低编译上限，证明使用原 plan/ID/结果。FL-18 同时验证持久化输出 metadata 在重启后保持一致。
  - [x] **FL-16.3 — Service Session 与失败决策窗口**：Completed/Failed/Aborted Session 写失败、activation 竞争、分页/错误传播；Failed CAS 后 retry/fail_run 中断、并发 retry 和迟到事件。
  - [x] **FL-16.4 — Judge 故障窗口**：Bot/Human 持久化输入、并发 claim、旧 owner/取消拒写、事务失败释放；SQLite 独立进程接管过期租约。
  - [x] **FL-16.5 — Opening 故障窗口**：Pending 启动前/历史写入后中断、Bot/Human initial node、原 Group 变化、并发恢复、历史冲突、取消与写失败；SQLite 两窗口重启。
  - [x] **FL-16.6 — Dispatch 故障窗口**：发送前/发送标记后/ACK 后写失败、原拒绝、原载荷/deadline、关闭 timeout、取消和迟到事件；SQLite pending/unknown 两窗口。
  - [x] **FL-16.7 — 三类 Store 合同**：Memory/SQLite/MySQL Text 与 Prepared 已验收 snapshot、Judge、opening 固定 ID、dispatch 不可变性、ACK/expiry 竞争与局部事务回滚；具体历史证据见执行记录。
  - [x] **FL-16.8 — 剩余恢复阶段故障注入**：Chat publication 已完成保存/claim/发送标记/ACK/Run 写失败、竞争/取消，以及 SQLite 四窗口重启和真实 MySQL 合同；terminal IM 已完成保存/claim/标记/ACK 写失败、部分发送、并发/旧 activation、SQLite 六窗口重启、真实 MySQL 和独立 scanner 游标测试；缺失原始事实已完成准备宽限、迟到创建者、失败事务回滚、Session 写失败/旧 activation、已受理旧节点、七窗口重启和三类 Store 合同。已补齐终态清理的部分写入续做/跨进程恢复，以及人工通知六窗口跨进程、发送竞争、原 deadline、取消/迟到确认与队列提升。FL-16 当前完成 **8/10** 子项；这些受控恢复测试不替代真实产品进程强杀和 Singlebox。
  - [ ] **FL-16.9 — 真实多实例与进程强杀 FO**：跨实例租约竞争、真实外部调用的发送结果未知、leader 切换、进程强杀后不重复有效 completion。
  - [ ] **FL-16.10 — 完整 Singlebox/FO 门禁**：运行 S5 对应完整场景和覆盖率门禁，登记真实部署边界；生产 v2 和实验 scanner 仍默认关闭。

## 6. S4：API、Event 与消费者

- [x] **FL-17 — 统一 Run/Node/Graph/PendingHuman API 投影**
  - 依赖：FL-02、FL-06、FL-09、FL-13。对应 spec §14、§18.1。
  - 更新 `src/bcs/api-contracts/v1/openapi/state-machine-runs.yaml`、`collaboration-definitions.yaml`（同目录）、application views 与 legacy/V1 HTTP DTO；覆盖 start/get/rerun responses。
  - Run.node_execution_metadata 覆盖全部 Loop body execution nodes；Node/Graph 使用相同 execution 对象；Graph/preview result 出口携带 loop_route，普通边省略。Run 从 snapshot 直接投影，不解析 ID。
  - OpenAPI loop_context 一旦存在则四个字段全部必需，previous_result 仅首轮可 null；客户端只提交现有 respond 输入，不能修改可信 context。
  - 验收：两套 adapter 合同测试及 OpenAPI fixtures 一致；保留 authoring graph_mode，新增 execution_graph_mode；真实 outcome 与 logical_outcome 同时可查；v1 optional-field wire 行为兼容。
  - 当前进度：已完成。Run start/get/rerun 返回全部 Loop body 节点映射，Node/Graph/preview 复用同一 execution 对象，Graph 保留 authoring mode 并返回保存的 execution mode/compiler 与完整 loop_route。PendingHuman 首轮/后续轮 context 与节点映射一致；两套 HTTP（含 Session 创建）及 OpenAPI 使用共享 fixture 验证。历史查询使用原 snapshot/opaque ID，不受执行开关或当前编译上限影响，损坏的 v2 plan 返回错误，v1 省略新增字段。Runtime/Service API 390 项、HTTP 99 项、直接消费者 179 项、OpenAPI 49 项通过；workspace all-targets、面板类型检查及三项架构 gate 通过。

- [x] **FL-18 — 贯通 Event 与持久化消息历史 metadata**
  - 依赖：FL-09、FL-14、FL-17。对应 spec §14.4、§15。
  - 更新 `src/bcs/api-contracts/events/v1/catalog.yaml`、`event-envelope.schema.json`（同目录）及 event fixtures；既有 started/completed/retry_scheduled event 添加可选 execution。
  - 写入 message 时从 snapshot 获取 execution 并持久化到 metadata.state_machine，历史查询原样返回；v2 缺匹配映射不能写入只有 generated ID 的消息。
  - 验收：同一节点的 Run/Node/Graph/Event/Message metadata 完全一致，attempt 与 iteration 独立；v1 省略 execution；不新增未登记的 Loop 公共事件。
  - 当前进度：已完成。三个公共 Node Event 从原 plan 添加 execution，完成事件与 Node CAS 仍复用现有局部事务；Eventing full/metadata_only 保留相同对象。v2 Completed Node 输出用已有固定消息主键持久化 metadata.state_machine.execution，写失败阻止后继推进并由通用恢复补写，历史批量读取后原样返回。保留 v1 历史路径、人工定向/普通 Bot FullOnly 可见性；无新表/迁移/Loop 专用公共事件。

- [x] **FL-19 — 实现 IM LoopContext 展示与通知恢复**
  - 依赖：FL-13。对应 spec §10.4、§14.5；既有 HumanInput IM 设计。
  - 位置：`src/bcs/crates/services/bcs-channel/src/lib.rs`、`tests/conformance_session_channel_outbound.rs`（同 crate）。
  - direct_assignee 单独显示完整 LoopContext；fixed_group 沿用共享/脱敏规则，不能自动发布私密上一轮 artifact，隐藏时指引至人工界面。可见性过滤不把 previous_result 改成首轮 null。
  - 渲染文本保存到已有 HumanInputRequest.notification_text；排队/重试/恢复复用文本，不读取当前 Definition 重渲染、不重复创建人工请求。
  - 验收：首轮/后续轮 golden、fixed_group 私密结果负例、重启/排队/重试 conformance；数据来源与 FL-12 一致，无独立 Loop 通知表/事务。
  - 当前进度：已完成本项通知消费路径。独立渲染并保存首轮/后续轮文本，direct_assignee 展示完整 previous result，fixed_group 只显示轮次与 Workbench 指引，结构化 context 不被改写；通用 upstream 不重复前轮输出。原事件重试先读已有 request，保存的文本/目的地/deadline 不受传入新文案影响，Active/terminal 不重发，Queued 沿用已有提升，Notifying 使用原 interaction stream key；DeliveryFailed 沿用既有 Workbench 处理策略。首次写入和状态写失败返回错误，队列不吞持久化失败。共用 API fixture 的四份 golden、服务重建/磁盘重载、事件重试、过期、隐私及 v1 回归见本轮执行记录；主动 scanner/跨实例发送歧义属于 FL-15.11，不在本项声明完成。

- [x] **FL-20 — 更新运行面板与人工入口界面**
  - 依赖：FL-17、FL-18。对应 spec §10.4、§16.2。
  - 位置：`src/bcs/assets/panel/src/StateMachineRunView.tsx`、`src/state-machine-graph/` 及共享类型/测试。
  - 默认以 Loop 外框展示当前执行的一份 body，任务节点不带次数标记；历史选择和展开图只包含实际进入的执行，未执行的未来节点不计入历史。完整分支保留在循环视图，连线只显示 `continue` 或逻辑 outcome。
  - Node detail 区分 execution ID、logical ID、iteration、attempt；人工 entry 独立展示 LoopContext，回复沿用当前 execution ID/response ref，历史选择不能误投当前人工任务。
  - 验收：break/exhausted/HumanInput、历史固定选择、刷新跟随、取消/重试和高上限短历史的交互 fixtures 通过；使用 metadata 而非解析 ID；普通 v1 不出现空 Loop 区块。
  - 当前进度：已完成，最新行为见 2026-09-17“精简 Loop 标记、仅展示实际执行、拆分出口处理”记录。Panel typecheck/build/Loop integration/UMD 通过，包含上限 100 实际进入 0/5/10 次；浏览器与本地在线资源已验证。下方历史执行记录保留当时的展示行为，不作为当前 UI 要求。

- [x] **FL-21 — 更新 Frontend Definition preview 消费者**
  - 依赖：FL-06、FL-17。对应 spec §14.2、§16.1。
  - 位置：`src/frontend/src/pages/GroupChat/` 的 validation、graph layout 与相关组件/DTO；修改前读取 Frontend 本地说明。
  - 使用 execution_graph_mode 校验服务端投影、保留 authoring mode；有 Loop descriptor 时只显示一份逻辑 body，保留分支/汇合与角色选点，不展开尚未发生的执行记录。节点无次数标记，`max_iterations` 只在悬浮或折叠信息中显示。
  - 原样保留服务端资源限制错误与 authoring path diagnostics；预览 ID 只用于本次响应内选点。`VALIDATION_ONLY_FEATURE` 存在时保留预览并禁用创建，实验开关开启后可创建。
  - 验收：现有 graph/layout/validation tests 加入 v2 fixtures，v1 预览回归通过；类型、UI 与 API 合同一致。
  - 当前进度：已完成；GroupChat 最新 **59 passed**、独立预览构建及浏览器检查通过。模板 approved/exhausted 已分别连接润色/重写后汇总。全量类型检查曾有既有依赖/基线错误，不能把局部通过视为整个 Frontend 门禁通过；历史证据及后续展示修订见执行记录。

- [x] **FL-22 — 同步 CLI authoring、查询与回复展示**
  - 依赖：FL-06、FL-17。对应 spec §14.3、§20 Slice 4。
  - 位置：`src/bcs/crates/tools/bcs-cli/` 及其 authoring reference/fixtures；检查 validate/create/run/query/respond 实际消费路径。
  - CLI 使用服务返回的 execution ID；展示 logical node/iteration/attempt 和可用路由/context 信息，透传版本/能力诊断；不要求用户手工拼接 `ln-...`。
  - 验收：普通 acyclic 输出兼容；Loop validate/query/respond fixtures 通过，人工回复不能误投其他轮；保持既有 leaf-command coverage。
  - 当前进度：已完成。增加 collaborate/collaboration query（Run、--node、--graph、--pending）与 respond；回复前读取授权 pending 列表并核对精确 execution ID，POST 仅含现有 content。validate 文本展示 Loop preview 和版本/能力 warnings，JSON 原样透传；文档与 v2 fixtures 同步。CLI 196 项通过（2 项既有忽略），新增命令纳入既有 Singlebox CLI story；完整 Singlebox 门禁仍由 S5 跟踪。

- [x] **FL-23 — 补齐日志与低基数指标**
  - 依赖：FL-05、FL-14、FL-15。对应 spec §16.3。
  - 结构化日志带 loop_id、loop_iteration、loop_max_iterations、definition_node_id、execution_node_id、attempt、selected_outcome；指标覆盖 iterations started/completed、break、exhausted、compile rejected。
  - 明确一次有效转换的计数点，重复 terminal/recovery 不重复统计已完成轮次；复用当前观测合同与装配点。
  - 验收：日志/指标测试覆盖正常、retry、stale、recovery；指标不用 run/node/Bot ID 作 label，outcome/reason label 受既有低基数规则约束。
  - 当前进度：已完成本项观测路径。Service API 定义同步、无 IO 的 typed hook，bootstrap 三处装配按现有 metrics feature/config 注入；entry attempt 0 的 Running CAS 计开始，result Completed CAS 计完成与 break/exhausted，均先提交状态后计数。typed v2 compiler 拒绝按 invalid_definition/resource_limit 分类，公开 diagnostics 不变；break outcome 使用固定集合，其他值归为 other。日志保留原始 outcome 和完整 Loop/attempt 身份。Runtime/Service API/Test Support 424 项、指标集成 12 项通过，最后扩充 continue 恢复后 7 项专项复验通过；workspace all-targets、无 metrics feature 构建和三项架构检查通过。没有新增持久化/事务；崩溃窗口允许少计，恢复不补账。此项覆盖已实现的 Node/Judge/frontier 恢复观测，FL-15/16 的剩余完整 FO 仍单独跟踪。

## 7. S5：集成验证与发布门禁

- [x] **FL-24 — 运行合同传播与 v1 全量回归**
  - 依赖：FL-02～FL-14、FL-17～FL-22。对应 spec §21.1～21.5、§22.1～22.5。
  - 执行 Domain、Service API/port、Memory/MySQL/SQLite、legacy/V1 adapter、OpenAPI、Event、通知、UI 和 CLI 对应 suites；核对 doubles/Noop 实现及下游 DTO 未遗漏。
  - 验收：每个变化的合同都有 happy/error path，跨投影 metadata 相同、v1 省略字段保持兼容；真实 MySQL 验证与 SQLite 模拟分开记录，条件跳过的用例不算通过。
  - 早期全量合同检查曾有 5 项陈旧断言失败，均在 HEAD 复现（公开操作数/安全边界、导出计数、Group context、Internal inventory）；已按已提交合同更新精确清单，未放宽生产合同。
  - 已完成：完整 Rust workspace fast-fail 单次门禁 **5041 passed、0 failed、55 skipped**；默认跳过的真实 MySQL 合同另在独立 MySQL 8.4 数据库显式执行 **6 passed、0 ignored**，包含完整迁移链与 Text/Prepared Store。OpenAPI/Event **131 passed**，Panel 完整 verify、Frontend CI 及全量 Jest **72 passed / 14 suites**；三项架构检查通过。其余 skipped 不计为通过，完整 Singlebox/FO 仍由 FL-26/29 验收，详见本次执行记录。

- [x] **FL-25 — 新增三条正常执行 live story**
  - 依赖：FL-14、FL-17～FL-22。对应 spec §21.6。
  - Story A：最多三轮、第二轮 break；第二轮入口读取第一轮结果，第三轮全 Skipped，最终输出成功。
  - Story B：空 break 执行满 N 次，exhausted → HumanInput → final；验证等待时 Run Running、末次实际 outcome 与 loop_route 不混淆。
  - Story C：HumanInput entry 两轮；首轮 null，第二轮 query 与 direct_assignee 通知携带第一轮 outcome/output，使用本轮 response ref 回复。
  - 验收：通过真实产品入口和可复现本地 Bot/通知测试设施执行；三条均检查 Node/Event metadata、消息历史、Graph 和最终结果，不能用直接调用内部 runtime 代替 live story。
  - 已完成：`provider_downlink/fixed_loop_live.rs` 三条 HTTP live story **3 passed、0 ignored**，通过现有 ChannelProvider 注册点链接本地 HTTP 通知接收器。共享断言逐节点核对 Run/Node/Graph/Event/消息 metadata、一次 output、最终 Completed，以及未来 Skipped 节点没有输出或 started/completed Event。执行入口和范围见同目录 `README.md`；完整 Singlebox 覆盖率仍由 FL-29 验收。

- [ ] **FL-26 — 验证真实重启与多实例 FO 收敛**
  - 依赖：FL-16.1～FL-16.8、FL-18、FL-19、FL-23、FL-25。对应 spec §13、§21.3、§22.11。本项通过后回填 FL-16.9；FL-16.10 在 FL-29 后验收，避免父项与 S5 互为前置。
  - 在持久化后、后续推进前实际终止/重启运行实例，覆盖 completion、部分 skip、exhausted、人工通知和 finalization 窗口；模拟两个恢复者竞争及重复/迟到事件。
  - 验收：按原 snapshot 收敛，未完成动作被补做、已完成结果不覆盖、无第二次有效推进，cancel 被遵守；外部发送不确定窗口按原 FO 合同处理，不能宣称 provider 不具备的 exactly-once。
  - 未完成时：在执行记录标明阻塞范围，保持能力默认关闭，不将 FL-25 通过解释为 FO-safe。

- [ ] **FL-27 — 验证资源与分步提交性能边界**
  - 依赖：FL-04、FL-11、FL-15、FL-23。对应 spec §8.4、§13.3、§17、FO spec §6.3。
  - 用固定 fixtures 比较 v1 基线与 v2 不同 N/body 大小，记录 compile 时间/plan bytes、启动和单次推进耗时、DB 写次数/事务范围、恢复扫描批次及峰值内存。
  - 验收：超限在创建前拒绝；不引入覆盖全部节点的事务/逐 edge 进度写入，恢复读取有界且批量；prompt 不自动累积历史。记录瓶颈与配置默认值依据，不编造通过阈值或为过门禁削弱正确性。

- [ ] **FL-28 — 验证升级、能力准入与回滚路径**
  - 依赖：FL-07～FL-09、FL-24、FL-26、FL-27。对应 spec §18、§22.11。
  - 验证所有 Store 迁移、compiler 支持和旧 snapshot 读取；在旧实例仍可接管调度的范围禁止新建 v2 Run。覆盖所有启动路径，不只 HTTP validate。
  - 演练关闭新建能力后处置现有 active v2 Runs；退回不支持 plan 的旧版本前先 drain 或确定性终止，新增 nullable DB 字段保留。
  - 验收：登记部署顺序、实例版本约束、准入策略与回滚证据；未具备完整恢复时仅可交付关闭能力的代码，不能勾选“已启用发布”。

- [ ] **FL-29 — 运行仓库门禁并保存 canonical artifacts**
  - 依赖：FL-24～FL-28。对应 spec §20 Slice 5、§22 及根 AGENTS CI 规则。
  - 按 §8 执行 BCS fast-fail workspace gate、OpenAPI/Event、panel 和受影响 Frontend 验证；有边界变更时运行对应架构检查。
  - 将 Loop live stories 接入现有 singlebox 入口，运行默认全模块覆盖门禁和 artifact verifier；保留报告于现有 canonical reports 目录。
  - 当前阻塞：MySQL 016 已合并为单文件，两份原始 SQL 按字节归档；旧版 016 记录需要按 reconciliation 文档核对完整 schema 后单独处理，runner 不自动改写。001/008 索引和 8 份 ADD COLUMN IF NOT EXISTS 语法问题均已修正；真实 MySQL 8.4 的完整 001～028 apply、重复执行、020→028 升级和已知旧 checksum/时间戳保留已通过，原语法阻塞解除。仍未完成真实生产/OceanBase 升级、拆分 016 部署核对及完整 singlebox/FO 发布验收；这些门禁不能由一次性 MySQL 合同替代。
  - 验收：BCS line ≥40%、method ≥36%、HTTP endpoint 和 CLI leaf-command 均 100%，并满足其他模块原有阈值；不降低门禁、不排除生产路径凑覆盖；记录未运行项与原因。

- [ ] **FL-30 — 完成 spec 验收与交付记录**
  - 依赖：FL-01～FL-29。对应 spec §22。
  - 对照 §8 覆盖矩阵逐项补齐证据；同步主 spec 状态、示例、配置/运维说明、compiler version 与迁移说明，关联实际 commit/PR。
  - 记录发布状态 `未启用 / 已启用`、FO 状态和仍未验证的部署边界。S1～S4 代码完成但 FO/门禁缺失时，保持本任务未完成，明确“能力默认关闭”。
  - 验收：能够从本文定位每项实现和真实验证；不把任务文档生成、测试计划或尚未运行的命令算作开发完成。

## 8. 验证入口与覆盖矩阵

下列命令供开发时使用，均从仓库根目录运行。它们是验证入口，不代表本任务文档建立时已经执行。
按改动先运行最近的测试，片段集成后再运行全量门禁；新的 compiler/recovery suites 必须纳入现有入口。

### 8.1 局部验证

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime --test definition_validation
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime --test runtime_progression
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-domain -p bcs-service-api -p bcs-collaboration-store
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-channel --test conformance_session_channel_outbound
python3 -m pytest src/bcs/tests/openapi src/bcs/tests/event_contract
python3 src/bcs/scripts/validate_openapi_contract.py --root src/bcs/api-contracts/v1
npm --prefix src/bcs/assets/panel run verify
npm --prefix src/frontend test -- --runInBand
```

Store/迁移任务还需实际 MySQL 与 SQLite 的 conformance/upgrade fixtures；测试进程返回 0 但跳过数据库用例
不能替代真实数据库验证。前端局部开发可按测试路径过滤，最终执行受影响 suite 和 CI gate。

### 8.2 集成门禁

```bash
bash src/bcs/scripts/ci_test.sh --fast-fail
bash src/frontend/scripts/ci_test.sh
bash scripts/ci/singlebox_coverage.sh
python3 scripts/ci/verify_singlebox_coverage_artifacts.py --reports-dir scripts/.dependencies/coverage/singlebox/reports
git diff --check
```

只诊断 BCS 时可运行 `bash scripts/ci/singlebox_coverage.sh --module bcs`；发布验收仍需默认全模块门禁。
架构检查使用实际变更边界对应的现有 suite；不运行 BCS 全局 formatter。缺少环境或依赖时，记录具体缺项，
保持相应任务未验收，不以 lint-only pre-push 或跳过 heavy tests 替代以上结果。

| 主 spec 条款 | 对应任务 | 必需证据 |
| --- | --- | --- |
| §7～8，§21.1 | FL-02～FL-06 | schema/strict-key/outcome/targets/limits 正反例 |
| §9，§21.1 | FL-05、FL-11 | compiler golden、DAG 再校验、edge role/route 与 v1 回归 |
| §10～11，§21.2 | FL-10～FL-14、FL-19 | Bot/Human context、三类选路、retry/cancel 与 IM fixtures |
| §12，§21.4 | FL-07～FL-09 | Memory/MySQL/SQLite round-trip、迁移、损坏拒绝、rerun |
| §13，§21.3 | FL-15～FL-16、FL-26 | 分步提交故障注入、真实重启、多实例和迟到事件 |
| §14～15，§21.5 | FL-06、FL-17～FL-19、FL-24 | API/Event/Message/通知同源 metadata 与兼容 fixtures |
| §16 | FL-20～FL-23 | 运行图、preview、人工回复、CLI、日志和指标证据 |
| §17～18 | FL-04、FL-27～FL-28 | 资源/性能记录、能力准入、升级与回滚演练 |
| §21.6 | FL-25～FL-26 | 三条正常 live story 及 FO live evidence |
| §22 | FL-24～FL-30 | 完整回归、canonical coverage artifacts 与发布状态 |

## 9. 执行记录

当前迁移以 MySQL `028_fixed_loop_runtime.sql` / SQLite
`029_fixed_loop_runtime.sql` 为准；rebase 后保留 dev 的 Provider webhook MySQL 027 / SQLite 028，Loop 顺延一个编号，SQL 内容不变。活动链分别为 MySQL 001～028、SQLite 001～029。自动重建索引及旧草稿补齐方案已撤回，索引超限按迁移 README 人工处理。下列历史记录中的旧编号、旧草稿升级方案及报告路径保留当时含义，不作为当前部署指令。


每次开始、阻塞或完成任务时追加一行；同一任务最后一条记录表示当前状态。测试证据填写实际命令、日期、
通过/失败/跳过数量及报告位置，必要时链接 PR。不要把敏感数据、运行时数据库或日志正文提交进本文。

| 日期 | 任务 | 负责人 | 状态 | commit/PR | 验证结果或阻塞原因 | 下一步 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-14 | 文档初始化 | — | 清单已建立，开发未开始 | — | 基于修订后的 spec 拆解；未执行开发验收 | 从 FL-01 开始 |
| 2026-09-14 | FL-01 | Codex | 已完成 | 工作区，尚未提交 | `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime --test definition_validation --test runtime_progression`：34 + 48 通过；执行入口共用 v1 validator，只有 timeout/部分 request-retry 恢复 | S1 纯编译与 preview 单独接入；FL-15 补齐通用 progression recovery |
| 2026-09-14 | FL-02～FL-06 | Codex | 进行中 | 工作区，尚未提交 | 基线已通过；v2 执行入口保持关闭 | 合同、编译器、配置与 preview 测试 |
| 2026-09-14 | FL-02～FL-06 | Codex | 已完成 | 工作区，尚未提交 | Domain/config/runtime/service-api 共 419 项通过（含 compiler 19、runtime progression 49）；bootstrap config 127 项、V1 route 8 项、legacy validation route 1 项通过；定向 OpenAPI/State Machine/Event 22 项通过；workspace all-targets check 通过 | FL-07～FL-09：snapshot 迁移、三类 Store 与历史 plan/rerun |
| 2026-09-14 | FL-29 基线记录 | Codex | 未开始；已记录全量检查缺口 | HEAD 与工作区对比 | OpenAPI/Event 全量 85 通过、5 失败；同样 5 项在 HEAD 快照复现，涉及既有接口清单/安全边界/group context；架构检查报告既有依赖、导入、命名和 conformance 登记问题，R25 全量扫描中止，后续子门禁未完成。配置测试首次被 localhost 沙箱限制，允许本机端口后 127 项全部通过 | 发布门禁阶段处理全量基线；不降低断言或宣称门禁全绿 |
| 2026-09-14 | FL-07～FL-09 | Codex | 进行中 | 工作区，尚未提交 | 已核对当前 snapshot 和 rerun 事务；新增 MySQL 027 / SQLite 028，不修改历史迁移 | 迁移、Store round-trip 与历史 plan 校验 |
| 2026-09-14 | FL-07～FL-09 | Codex | 进行中；本地实现已验证 | 工作区，尚未提交 | Domain/config/runtime/service-api/store 合计 498 项通过、0 失败/忽略，workspace all-targets check 通过；SQLite 迁移、Memory/SQLite 并发 rerun 与不可变 snapshot、MySQL 映射/写失败、历史 plan loader 均覆盖。报告：`/tmp/bcs-fixed-loop-all-final-tests.log`、`/tmp/bcs-fixed-loop-s2-workspace-check.log` | 真实 MySQL 升级与 round-trip、FL-10 的 v2 启动/rerun 集成；三项保持未勾选 |
| 2026-09-14 | FL-07 / FL-29 MySQL 基线 | Codex | 进行中；完整验收待补 | HEAD 已包含两份 016 文件 | `bcs-admin db migrate --dialect mysql --check-files` 报 016 重号；定向 `--only 1 --only 27` 报 baseline `idx_human_input_scope_status` 为 3200 bytes，超过 utf8mb4 3072 限制。新增 027 的 `--emit-sql --only 27` 通过；未连接真实 MySQL 执行 DDL | 不修改已发布迁移或弱化校验；发布前处理既有迁移链问题 |
| 2026-09-14 | FL-09～FL-14、FL-17 Human context | Codex | 进行中 | 工作区，尚未提交 | 已接入受控 runtime builder、plan 启动/读取/rerun、共享 Bot/Human context 和 selected-edge 投影；核心 7 场景已通过，补充异常/并行/one-shot 和合同回归中 | 完成定向与 v1 回归；FL-15～FL-16 恢复仍未开始，生产门禁保持关闭 |
| 2026-09-14 | FL-10～FL-14 | Codex | 已完成受控实现与本项验证 | 工作区，尚未提交 | Domain/config/runtime/service-api/store 全量 514 项通过；补充配置启动、连续 Loop 和并发 application rerun 后，最新 runtime progression 65 项通过（含 16 项 Loop 场景）；共享 context 单测 3 项包含在前述全量中；V1 Run HTTP 13 项、channel 96 项、OpenAPI/Event 定向 38 项通过，全部 0 失败/忽略 | 接续 FL-15～FL-16 通用恢复；S2 真实 MySQL 与 S4/S5 验收仍未解除 |
| 2026-09-14 | FL-15～FL-16 | Codex | 进行中；Completed-node 恢复核心已验证 | 工作区，尚未提交 | Runtime/Store/Service API 共 431 项通过（含 13 项定向恢复测试）；SQLite 在独立进程间验证 continue/break/exhausted/Run finalization 四个窗口。Scanner 3 项、配置 gate 128 项和 workspace all-targets check 通过，均无失败或忽略 | 补齐 dispatch/opening/chat-result checkpoint、Judge/Failed attempt/terminal Session 恢复及完整 FO、真实 MySQL 验收；两项保持未勾选 |
| 2026-09-14 | FL-15～FL-16：终态 Service Session | Codex | 进行中；本恢复阶段已受控验证 | 工作区，尚未提交 | Runtime/Collaboration Store/Session/Session Store/Service API 共 535 项通过；补充用例后定向 Session 恢复 6 项通过（含独立进程验证 Completed/Failed/Aborted 三种窗口），scanner 4 项、Session wrapper 2 项通过；workspace all-targets check、port purity、forbidden symbols、Store boundaries 通过；MySQL 029 仅完成 SQL 生成 | 剩余 dispatch/opening/chat-result/terminal-IM checkpoint、Judge takeover、Failed attempt 决策恢复及真实 MySQL/完整 FO 验收；实验 scanner 与生产 v2 仍关闭 |
| 2026-09-14 | FL-15～FL-16：Failed attempt 决策 | Codex | 进行中；本恢复阶段已受控验证 | 工作区，尚未提交 | Runtime/Store/Service API 共 449 项通过，新增 6 项 runtime 和 4 项 Store 合同/迁移测试；SQLite 独立进程测试扩展 retry 与 fail_run 两种窗口；workspace all-targets、port purity、forbidden symbols、Store boundaries 通过 | 剩余 dispatch/opening/chat-result/terminal-IM checkpoint、Judge takeover，以及真实 MySQL/完整 FO；实验 scanner 与生产 v2 仍关闭 |
| 2026-09-14 | FL-07～FL-09：真实 MySQL 收尾 | Codex | 已完成本项增量/Store 验收 | 工作区，尚未提交 | 新增两项真实 MySQL 集成入口通过；Store 覆盖 Text/Prepared、并发 rerun、activation 和写失败回滚；相关模块 469 项通过、3 项 MySQL 测试默认 ignored 后另行显式执行；workspace all-targets、CI workflow 2 项测试通过 | 全量历史链问题保留为 FL-28/29 发布阻塞；001 和两份 016 不变，生产 v2 关闭 |
| 2026-09-15 | FL-15～FL-16：Judge 接管 | Codex | 进行中 | 工作区，尚未提交 | 核对 FO §9.2/§10.2：补齐持久化输入、Node 短租约和结果 fencing，普通路径与恢复共用；schema 合入未提交的 MySQL 027 / SQLite 028 | 完成 Store 合同、Bot/Human 恢复与故障验证，保持实验开关关闭 |
| 2026-09-15 | FL-15～FL-16：Judge 接管 | Codex | 进行中；本恢复阶段已验证 | 工作区，尚未提交 | Runtime/Store/Service API 462 项通过、0 失败，1 项真实 MySQL 默认忽略后显式通过；新增 7 项 Runtime 和 4 项 Store 测试，SQLite 独立进程扩展 Judge 输入/过期租约恢复。bootstrap 迁移 30 项、admin 26 项通过；真实 MySQL 8.4 全链、合并迁移和 Text/Prepared 存储合同通过；workspace all-targets、port purity、forbidden symbols、Store boundaries 通过 | 继续 dispatch/opening/chat-result/terminal-IM checkpoint 和完整跨实例 FO；FL-15/16 保持未勾选，生产 v2/scanner 仍关闭 |
| 2026-09-15 | FL-15～FL-16：Opening 与初始 frontier | Codex | 进行中；本恢复阶段已验证 | 工作区，尚未提交 | Runtime/Collaboration Store/Message Store/Service API 544 项通过、1 项真实 MySQL 默认忽略后显式通过；SQLite 迁移 30 项、admin 26 项、HTTP 53 项通过；真实 MySQL 8.4 全链/合并迁移/Text 与 Prepared 存储合同、workspace all-targets 和三项架构 gate 通过 | 缺失启动事实的终态收敛、dispatch/chat-result/terminal-IM checkpoint 及完整 FO 仍待完成；两项保持未勾选 |
| 2026-09-15 | FL-15～FL-16：Bot dispatch checkpoint | Codex | 进行中；本恢复阶段已验证 | 工作区，尚未提交 | Runtime/Store/Service API 480 项通过、1 项 MySQL 默认忽略后显式通过；SQLite 迁移 30 项、admin 26 项、HTTP 53 项通过；MySQL 8.4 全链、合并迁移及 Text/Prepared Store 合同通过，workspace all-targets 和三项架构 gate 通过 | 两项保持未勾选；继续 chat-result/terminal-IM、缺失事实收敛、终态遗留 checkpoint 清理和完整 FO |
| 2026-09-15 | FL-17：统一 API 投影 | Codex | 进行中 | 工作区，尚未提交 | 核对 Run/Node/Graph 缺失 execution metadata，PendingHuman 与 preview 已有部分合同；本轮补齐并完成专项验收 | 完成两套 HTTP、OpenAPI、历史 snapshot 查询与 v1 回归后勾选 FL-17 |
| 2026-09-15 | FL-17：统一 API 投影 | Codex | 已完成 | 工作区，尚未提交 | Rust 668 项（Runtime/Service API 390、HTTP 99、直接消费者 179）与 OpenAPI 49 项通过，0 失败/忽略；67 个公开操作合同校验、workspace all-targets、面板类型检查、三项架构 gate 通过；历史 opaque ID、开关关闭、当前 Definition 不可用及 v1 兼容均覆盖 | 下一项 FL-18 Event/消息 metadata；FL-15/16 的剩余 FO 和其他 S4/S5 门禁仍单独跟踪 |
| 2026-09-15 | FL-18、FL-20、FL-22；FL-15/16 子项 | Codex | 已完成本轮范围 | 工作区，尚未提交 | Runtime/Eventing/Service API 425 项、HTTP 99 项、CLI 196 项通过；Event/OpenAPI 专项与面板完整 verify 通过，实际浏览器验收 Loop 图和人工输入弹窗；FL-15 拆为 6/10，FL-16 拆为 7/10 已完成子项 | FL-15.7～15.10、FL-16.8～16.10 仍待完成；S4 剩余 FL-19/21/23，生产 v2/scanner 关闭 |
| 2026-09-15 | FL-19：IM LoopContext 与通知文本恢复 | Codex | 已完成本项消费路径 | 工作区，尚未提交 | Channel/Channel Store/Runtime/Service API 520 项通过；共享 API fixture 的私聊/共享群首轮与后续轮 golden、磁盘重载/服务重建、排队/重试、v1 和写失败传播均通过；末次增加过期用例后复验记录见下文 | S4 剩余 FL-21/23；核对出通用 scanner 未主动接管 WaitingHuman 通知，新增未完成 FL-15.11，FL-15 为 6/11；生产 v2/scanner 保持关闭 |
| 2026-09-15 | FL-21：Frontend Definition preview | Codex | 已完成本项消费路径 | 工作区，尚未提交 | GroupChat 53 项、OpenAPI preview 11 项、Frontend CI 和真实组件构建通过；浏览器验证三轮/六条边、实际 outcome、选点、v1 和窄屏；TypeScript 5 + 已有 ReactDOM 类型对比 HEAD 无新增错误 | S4 剩余 FL-23；FL-15 为 6/11、FL-16 为 7/10，完整 FO 与发布门禁仍待完成；生产 v2/scanner 保持关闭 |
| 2026-09-15 | FL-23：Loop 日志与低基数指标 | Codex | 已完成本项观测路径 | 工作区，尚未提交 | Runtime/Service API/Test Support 424 项通过（1 项既有文档测试 ignored）；指标集成 12 项与最后 7 项专项复验通过；workspace all-targets、无 metrics feature 构建及三项架构 gate 通过 | S4 全部勾选；继续 FL-15.7～15.11、FL-16.8～16.10 和 S5；生产 v2/scanner 仍默认关闭 |
| 2026-09-16 | FL-15.7：Chat publication checkpoint | Codex | 已完成本恢复切片 | 工作区，尚未提交 | Runtime/Store/Service API/Test Support 527 项通过，1 项真实 MySQL 默认 ignored 后显式通过、1 项既有 doctest ignored；publisher 单测、MySQL 8.4 Text/Prepared、workspace all-targets 和三项架构 gate 通过；SQLite 四窗口跨进程恢复通过 | FL-15 为 7/11；FL-16.8 的 Chat 故障范围完成，父项仍为 7/10；后续 FL-15.8 terminal IM；生产 v2/scanner 默认关闭 |
| 2026-09-16 | FL-15.8：terminal IM notification checkpoint | Codex | 已完成本恢复切片 | 工作区，尚未提交 | Runtime/Store/Channel/Service API/Test Support 637 项、scanner 5 项、SQLite 迁移 23 项通过；MySQL 8.4 Text/Prepared、027 专项与完整迁移链、workspace all-targets、三项架构 gate 通过；SQLite 六窗口跨进程恢复通过 | FL-15 为 8/11；FL-16.8 的 IM 故障范围完成，父项仍为 7/10；后续 FL-15.9 缺失原始事实收敛；生产 v2/scanner 默认关闭 |
| 2026-09-16 | FL-15.9：缺失原始事实收敛 | Codex | 已完成本恢复切片 | 工作区，尚未提交 | Runtime/Store/Service API/Test Support 主回归 538 项通过；最终 Runtime 5 项和共享 Store 2 项专项、MySQL 8.4 Text/Prepared、workspace all-targets 与三项架构 gate 通过；七窗口独立进程恢复通过 | FL-15 为 9/11；FL-16.8 已覆盖缺失事实，父项仍为 7/10；下一项 FL-15.10 终态 checkpoint 清理；无 schema 变更，生产 v2/scanner 默认关闭 |
| 2026-09-16 | FL-07 / FL-28：已部署 SQLite 028 启动修复 | Codex | 本地升级已完成 | 工作区，尚未提交 | 新增 032 保留旧 execution-plan / 合并草稿的 028 历史；迁移 34 项、admin/Store 131 项通过；bcs/admin 重建通过；真实本地库先备份、副本升级再应用，53 张原表 / 484 行原值和全部旧迁移记录一致，integrity_check=ok，pending=0 | FL-15 仍为 9/11；本项不代替生产/OceanBase 升级或完整 FO 验收；后续列变更不得回填已执行的草稿 |
| 2026-09-17 | FL-15.10/15.11、FL-16.8；FL-24/29 全量核对 | Codex | 恢复子项完成，发布门禁未通过 | 工作区，尚未提交 | 相关模块 691 passed；真实 MySQL Text/Prepared、人工通知六窗口 SQLite 跨进程与双实例恢复通过；全工作区测试 5032 passed、1 failed、55 skipped；OpenAPI/Event 125 passed、5 failed，五个失败均在 HEAD 复现 | FL-15 为 11/11，FL-16 为 8/10；继续真实产品多实例/强杀、Singlebox 与门禁清单修复；v2/scanner 默认关闭，无迁移变更 |
| 2026-09-17 | FL-24 全量合同回归、FL-25 HTTP live stories | Codex | 已完成 | 工作区，尚未提交 | workspace 单次门禁 5041 passed、55 skipped；真实 MySQL 六项另行显式通过；OpenAPI/Event 131 passed；Frontend Jest 72 passed；Panel verify、Frontend CI 和三项架构检查通过；三条新 live story 专项及全量均通过 | 顶层任务 24/30；剩余真实 FO、性能、升级回滚和 Singlebox 发布门禁，详见本次执行记录 |


S1 验证命令和实施说明：

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime -p bcs-domain -p bcs-config-api -p bcs-service-api -q
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
cargo test --manifest-path src/bcs/Cargo.toml -p bcs --lib config --quiet
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test collaboration_definition_routes
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-http --test groups_contract post_collaboration_definition_validate_delegates_to_runtime_service
uv run --offline --no-project --with pytest --with pyyaml --with jsonschema python -m pytest src/bcs/tests/openapi/test_fixed_loop_preview_contract.py src/bcs/tests/openapi/test_state_machine_run_v1_contract.py src/bcs/tests/event_contract -q
```

- `src/bcs/crates/services/bcs-collaboration-runtime/src/fixed_loop.rs` 是纯编译入口；普通执行仍通过 v1 validator，configure/start 的拒绝行为有 application-level 测试。
- preview 返回 authoring Definition，而不是可绕过执行门禁的展开 Definition，并带 `VALIDATION_ONLY_FEATURE` warning。
- `collaboration.fixed_loop_limits` 初始配置为 32 轮、64 body nodes、2048 expanded nodes、4 MiB plan；这是限制分配规模的初始保护值，不代表 FL-27 性能验收已完成。编译前限制节点数量，展开过程中和最终序列化均限制 bytes。
- 本阶段未运行真实 Store migration、FO、多实例和 singlebox live stories；没有启用 Loop 执行，也没有修改已有事务边界。

S2 验证命令和实施说明：

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime -p bcs-collaboration-store -p bcs-domain -p bcs-config-api -p bcs-service-api -q
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime --test fixed_loop_snapshot -q
cargo run --manifest-path src/bcs/Cargo.toml -p bcs-admin -- db migrate --dialect mysql --check-files
src/bcs/target/debug/bcs-admin db migrate --dialect mysql --check-files --only 1 --only 27
src/bcs/target/debug/bcs-admin db migrate --dialect mysql --emit-sql --only 27
```

- `src/bcs/crates/services/bcs-collaboration-runtime/src/snapshot.rs` 只读取存量 plan，验证 hash、reader 支持版本、完整 node/edge 映射及路由；不接受当前 compiler、当前 Definition Repo 或当前资源上限作为输入。
- Memory 的 snapshot 与 bindings 使用同一次持锁写入；SQL 使用一次 INSERT。重复保存不会替换已固化内容；rerun 的 SELECT/INSERT 原样复制三项 plan 字段，保留既有 Run/Session 防重事务。
- SQLite 的真实内存数据库 fresh/legacy/重复执行/部分迁移恢复已验证；MySQL 使用 DbPlugin double 验证字段映射和错误传播，不等于真实 MySQL DDL 或并发验收。
- Application 加载 v2 snapshot 会先检查持久化完整性，再按当前执行门禁拒绝；首次启动和 rerun 仍未启用。FL-09 的节点重建/首轮启动验证与 FL-10 衔接，不将 Store 复制通过视为整个 v2 rerun 已完成。
- 未执行 FO、真实进程重启、多实例或 singlebox live stories；这些继续在 FL-15～FL-16 / FL-25～FL-30 跟踪。

S3 正常执行路径验证命令和实施说明：

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime -p bcs-collaboration-store -p bcs-domain -p bcs-config-api -p bcs-service-api -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime --test runtime_progression -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test collaboration_run_routes -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-channel -q
uv run --offline --no-project --with pytest --with pyyaml --with jsonschema python -m pytest src/bcs/tests/openapi/test_fixed_loop_context_contract.py src/bcs/tests/openapi/test_fixed_loop_preview_contract.py src/bcs/tests/openapi/test_state_machine_run_v1_contract.py src/bcs/tests/event_contract -q
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
```

- `with_experimental_fixed_loop_execution()` 仅作为明确的受控 assembly/test 入口；生产 bootstrap 没有调用或暴露配置。默认 start/configure/one-shot/rerun 的 v2 门禁继续生效。
- 启动保存 authoring Definition 与完整 plan；运行和 rerun 保留 snapshot 的执行元数据。Loop 不创建 Node Run；未扩大事务边界，继续使用现有单节点 CAS、独立 skip/barrier/dispatch。
- Bot prompt、Human pending query/内部 ready event 共用 `loop_context.rs`；上一轮结果缺 outcome/artifact/completed_at、轮次/身份不符或未选中 continue 时返回错误。构造失败发生在节点 Running CAS/通知之前。
- 回归覆盖空 break、提前 break、真实末轮 outcome、selected-edge 投影、并行 join、连续 Loop、人工跨轮回复/授权、retry/timeout/cancel/非法 Judge、snapshot/opening 写失败、损坏 snapshot、one-shot/配置启动和并发 rerun。上一轮 context 来自指定 result，未重复加入普通 upstream。
- `PendingHumanNodeView` 和内部 `HumanInputReadyEvent` 已有可选 `loop_context`，OpenAPI 与 V1 HTTP fixtures 已同步；这不代表 FL-17 的 Run/Node/Graph 元数据或 FL-19 的 IM 展示已完成。
- 主 spec §12.1 的 delivery request ID 示例修正为现有 `smnode-{run_id}-{execution_node_id}-{attempt}`，代码去重键格式不变。
- 报告：`/tmp/bcs-fixed-loop-s3-core-tests.log`、`/tmp/bcs-fixed-loop-s3-progression-final.log`、`/tmp/bcs-fixed-loop-s3-http-tests.log`、`/tmp/bcs-fixed-loop-s3-channel-tests.log`、`/tmp/bcs-fixed-loop-s3-contract-tests.log`；workspace all-targets 编译通过，报告为 `/tmp/bcs-fixed-loop-s3-workspace-check.log`（保留未修改模块的既有 warning）。
- 本次 FL-10～FL-14 勾选仅表示受控正常执行和本项异常验证完成，不解除 S2 数据库前置验收及 FL-15～FL-16 / S4 / S5 的发布限制。尚未验证通用 progression recovery、进程重启、真实 MySQL 或 singlebox E2E；能力不对生产开启。

S3 Completed-node progression 恢复验证命令和实施说明：

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime -p bcs-collaboration-store -p bcs-service-api -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs state_machine_progression_scanner --lib -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime --test runtime_progression sqlite_process_restart_recovers_all_committed_progression_windows -q
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
(cd src/bcs && bash scripts/ci/check-config-validation.sh)
(cd src/bcs && bash scripts/ci/check-port-purity.sh)
(cd src/bcs && bash scripts/ci/check-forbidden-symbols.sh)
(cd src/bcs && bash scripts/ci/check-store-boundaries.sh)
(cd src/bcs && bash scripts/ci/check-deps.sh)
(cd src/bcs && bash scripts/ci/check-public-api.sh)
```

- 恢复以 immutable snapshot 和批量 Node Run 为输入，重算全图的 skip/readiness；已 Skipped 中间节点不会阻止修复后代，其他分支仍可到达的 join 保留。继续使用目标节点 CAS 和独立提交，不增加逐 edge checkpoint 或覆盖展开节点的大事务。
- Repo 增加仅扫描 Running Run 的 keyset 分页；MySQL 028 / SQLite 029 为 `(env, status, record_status, run_id)` 增加索引。Memory/SQLite 共享合同、MySQL SQL 映射/写失败、SQLite 28→29 升级及幂等迁移通过；SQLite query plan 验证使用索引且不产生临时排序。MySQL 028 的 SQL 生成已通过，真实 MySQL DDL 尚未执行。
- 通用 scanner 每秒处理最多 32 个 Run，受现有 Leader Election 控制；失去 leader 身份会取消进行中的 page，关闭时 abort worker。单个 Run 的失败进入 page 结果且不阻断后续游标。`collaboration.experimental_progression_recovery` 默认 `false`；配置开启该实验扫描也不会打开生产 v2 执行门禁。
- 13 项定向恢复测试覆盖普通 DAG、Loop、并行 join、部分 skip、写失败、Human context、并发扫描、取消前后检查、已提交 retry 和损坏 snapshot。SQLite 四种窗口分别使用 prepare/recover 两个独立进程；恢复时删除当前 Definition、收紧当前编译上限，证明使用原 snapshot，Completed 结果不变且不重跑 Judge。这里验证的是写失败后重启，尚不等于真实服务强杀、多实例外部调用或完整 FO。
- 未完成启动、Failed attempt 尚未提交 retry、Chat publication 缺 checkpoint 会明确报错；Running attempt 不重发。Run 已 Completed 后的 Session 状态写失败现在向调用方传播，但尚不在只扫描 Running Run 的恢复范围内。上述缺口继续作为 FL-15～FL-16 未完成项。
- 核心 431 项、scanner 3 项、配置 gate 128 项通过；配置 gate 首次被本地端口沙箱权限阻断，授权后重跑通过。Port purity、forbidden symbols、Store boundaries 通过。依赖 gate 仍被未改动的基线依赖阻断（`bcs-protocol → bcs-domain`、`bcs-service-api → bcs-config-api/bcs-storage-api`）；`cargo-machete` 和 `cargo-public-api` 未安装，对应检查未执行。未将旧的架构失败算作通过。
- 报告：`/tmp/bcs-fixed-loop-recovery-core-final.log`、`/tmp/bcs-fixed-loop-recovery-scanner-tests.log`、`/tmp/bcs-fixed-loop-recovery-restart-tests.log`、`/tmp/bcs-fixed-loop-recovery-workspace-check.log`；架构和配置 gate 报告为 `/tmp/bcs-loop-recovery-check-*.sh.log`。完整 singlebox live E2E、真实 MySQL 和发布验收仍待执行。

S3 终态 Service Session 恢复验证命令和实施说明：

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime -p bcs-collaboration-store -p bcs-session -p bcs-session-store -p bcs-service-api -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime --test runtime_progression session_recovery_tests -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-session --test runtime_cleanup_wrapper -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs --lib state_machine_progression_scanner -q
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
cargo run --manifest-path src/bcs/Cargo.toml -p bcs-admin -- db migrate --dialect mysql --emit-sql --only 29
(cd src/bcs && bash scripts/ci/check-port-purity.sh)
(cd src/bcs && bash scripts/ci/check-forbidden-symbols.sh)
(cd src/bcs && bash scripts/ci/check-store-boundaries.sh)
```

- `recover_state_machine_sessions` 独立扫描仍 Running 的 ServiceInvocation Session，每页最多使用调用方指定的 limit，按 Session id keyset 分页；再查询该 Session 最新 Run。仅 terminal Run、同 group/session、同已保存 activation 且 snapshot 可读时补做完成；不扫描历史 terminal Run 全表，不读取当前 Definition 或重新执行 Bot/Judge。
- 普通完成、失败、取消和恢复使用同一 Session activation CAS；Run 终态更新 CAS 未命中时当前请求停止收尾。Session 写失败向调用方返回错误，后续实验扫描可补做，不覆盖 Run output/error/status/completed_at。已结束、Chat、较新 activation、未关联 Run 或缺 activation 的历史 Run 不由该恢复补做；正常 State Machine 路径也不会把缺失的 activation 替换成当前值，升级前需处理这类历史活动 Run。
- Session Service 复用现有 `session.completed` event transaction；非 eventful SQL 路径使用单行条件 UPDATE，无 Run/Session 大事务。CAS 返回旧 activation 自己的完成 snapshot，避免更新后的 SELECT 读到重激活的新 Session。callback 使用该 snapshot 和现有 activation-aware dispatcher；只有 Session CAS 胜者尝试原有同步 terminal IM，Aborted 沿用现有无 terminal IM 的行为。Session 已完成后的 IM 丢失/重试仍需要独立 checkpoint，本阶段不宣称 IM FO 已完成。
- 同一实验 worker 并行处理 Run page 与 Session page，各自拥有 cursor；每 tick 各最多 32 个候选。一类 page 查询失败只保留该类 cursor，另一类正常前进；单条失败不会阻断后续页面。Leader 丢失或关闭会取消两类在途 page。MySQL 029 / SQLite 030 只增加 Running Session 查询索引；SQLite fresh、29→30、重复启动和 query plan 均验证通过。
- 全量相关模块回归 535 项通过，补充测试后定向 Session 恢复 6 项通过，scanner 4 项和 wrapper 2 项通过，均无失败/忽略。Memory/SQLite 共享合同覆盖分页、CAS、并发完成和旧 activation；额外 SQL 注入覆盖 CAS 前后重激活、写失败、查询和解码失败。Session eventful/非 eventful 与 wrapper 合同均通过。跨进程测试覆盖写失败后的实际进程重启，尚未执行服务强杀或多实例外部调用验收。
- 报告：`/tmp/bcs-loop-session-recovery-core.log`、`/tmp/bcs-loop-session-final-focused.log`、`/tmp/bcs-loop-session-contract-tests.log`、`/tmp/bcs-loop-session-wrapper-tests.log`、`/tmp/bcs-loop-session-scanner-tests.log`、`/tmp/bcs-loop-session-recovery-workspace.log`；三个架构 gate 报告为 `/tmp/bcs-loop-session-check-*.log`。MySQL 029 生成 SQL 为 `/tmp/bcs-loop-session-recovery-mysql-029.sql`；真实 MySQL DDL 仍未执行，沿用前述基线迁移阻塞记录。完整 singlebox E2E/FO 和 S4/S5 仍未完成，FL-15～FL-16 不勾选。

S3 Failed attempt 决策恢复验证命令和实施说明：

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime -p bcs-collaboration-store -p bcs-service-api -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime --test runtime_progression failure_recovery_tests -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime --test runtime_progression sqlite_process_restart_recovers_all_committed_progression_windows -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-store --test mysql_store failure_ -q
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
src/bcs/target/debug/bcs-admin db migrate --dialect mysql --emit-sql --only 30
```

- 新增 Repo 内部 failure_action 和 MySQL 030 / SQLite 031 nullable 列；失败 CAS 同时保存 action、error、completed_at，retry CAS 清除旧 action。只增加单节点 UPDATE 字段，不引入跨 Node/Run/Session 事务；普通成功路径不增加本项查询或写入。失败路径保存决策后读取同一记录进行推进，恢复复用该逻辑。
- Bot terminal error/aborted、空可见输出、明确的 Judge/timeout 失败按原 max_attempts 保存 Retry 或 FailRun；投递被拒绝/调用失败保留原直接 FailRun 策略，即使尚有次数也不重试。失败 CAS 未命中停止当前派发失败请求的收尾；旧 attempt、取消后的 Run 和已完成节点不能被重新处理。已进入 Failed 的 Judge 错误恢复按保存的 Node retry 决策执行；仍处于 Judging 的接管属于未完成的独立阶段。
- Runtime 新增 6 项测试覆盖同轮重试与上一轮 immutable context、并发恢复只派发一次、迟到旧事件、原 error 的 Run/Session 收尾、投递失败、失败写入错误、旧记录无 action、取消，以及 Judge/空结果失败。SQLite restart wrapper 使用 prepare/recover 两个独立进程，现覆盖 continue/break/exhausted/finalize/retry/failed 六种窗口；恢复删除当前 Definition 并收紧当前编译上限。
- Memory/SQLite 共享合同覆盖 failure CAS、旧 attempt、max attempts、普通和 eventful retry、FailRun 禁止 retry、Run 取消和环境隔离；SQLite 额外覆盖未知 action、30→31 升级和迁移标记写入前中断后的重复执行。SQL eventful CAS 未命中沿用现有 Conflict 错误，扫描记录该项失败并可在下一轮重试，不伪报事务成功。
- 相关模块全量 449 项通过，0 失败/忽略；workspace all-targets 和三个架构 gate 通过。报告为 `/tmp/bcs-loop-failure-core-final.log`、`/tmp/bcs-loop-failure-workspace.log`、`/tmp/bcs-loop-failure-{port-purity,forbidden-symbols,store-boundaries}.log`。独立重启报告 `/tmp/bcs-loop-failure-restart.log`；MySQL 030 生成 SQL 为 `/tmp/bcs-loop-failure-mysql-030.sql`，真实 MySQL DDL 仍未验证，基线迁移文件检查仍受既有索引限制阻断。
- 新 Runtime 启动前必须应用新列，即使仅运行 v1 且 scanner 关闭；旧记录 action 为 NULL，不做猜测性回填。降级可保留列，但不支持旧新 writer 并发；需先排空活动 Run。完整 singlebox E2E、真实 MySQL、外部调用 checkpoint 和完整 FO 尚未完成，FL-15～FL-16 保持未勾选。

S2 真实 MySQL 补充验收（FL-07～FL-09 收尾）：

```bash
# BCS_TEST_MYSQL_URL 指向隔离、可丢弃的 MySQL 8.4 测试库。
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-admin fixed_loop_migration_applies_to_real_mysql -- --ignored
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-store --test mysql_store real_mysql_fixed_loop_snapshot_and_rerun_contract -- --ignored
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-store -p bcs-collaboration-runtime -p bcs-service-api -p bcs-admin -q
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
python3 scripts/ci/tests/test_unit_test_workflow_diff.py
```

- 本次使用本机隔离的 MySQL 8.4.11 容器，不连接现有部署；通过 `bcs-db-mysql` 执行真实 SQL。两项 live 入口分别覆盖 migration 和 Store，其中 Store 在 Text/Prepared 两种协议下重复验证。它们已加入 `.github/workflows/unit-tests.yml` 的现有 MySQL 服务测试步骤，常规 Cargo suite 中的 ignored 不作为通过证据。
- Migration 使用未经改写的 001 snapshot CREATE 语句与 027 SQL，覆盖空表、历史行、JSON/CHAR(64)/VARCHAR(128) nullable 类型、失败 DDL 无成功记录、重复 apply 计划无 DDL 和 checksum 拒绝。Store 使用明确记录的 pre-Loop 物理 schema fixture，再执行原始 027；测试实际 snapshot round-trip、不可变保存和环境隔离。
- 真实数据库并发验证 Chat 与 Service rerun 仅一个子 Run；Service activation 只从 4 增至 5，新 Node 全部 attempt 0。通过临时 CHECK 约束注入 snapshot 写失败，证明错误传播且 Run/Node/Session activation 同事务回滚，原 snapshot 和 Session output 不变。无需 SUPER 权限，不修改 MySQL 安全配置。
- 按更新后的 CI 顺序复跑 MySQL DB plugin、Eventing migration、Loop migration、Loop Store、Event Store 共 5 项 live 合同全部通过，0 失败/忽略，确认共享测试数据库无相互干扰。报告为 `/tmp/bcs-loop-mysql-shared-ci.log`。
- 本项收尾将 snapshot 增量验收与完整历史迁移链验收分别登记：前者对应 FL-07～FL-09，后者仍阻塞 FL-28～FL-29。此前把整个历史链缺陷也作为 S2 阻塞的范围过宽；本次补足真实增量/Store 证据后勾选 S2，没有修改或关闭全链检查。001 与两份 016 均保留原文件、名称和 checksum；未调整部署中的迁移记录。全链和定向 baseline 检查仍按原规则失败，报告为 `/tmp/bcs-loop-mysql-history-check.log`、`/tmp/bcs-loop-mysql-baseline-check.log`。
- 相关模块回归 469 项通过、0 失败；常规运行的 3 项 MySQL ignored 另行显式执行，不算入 469。workspace all-targets 通过，workflow 检查 2 项通过。报告：`/tmp/bcs-loop-real-mysql-migration.log`、`/tmp/bcs-loop-real-mysql-store-final.log`、`/tmp/bcs-loop-mysql-core-final.log`、`/tmp/bcs-loop-mysql-workspace-final.log`、`/tmp/bcs-loop-mysql-ci-tests.log`。
- 未验证：完整历史 MySQL 链的新建库/生产升级、真实服务强杀、多实例外部调用和完整 singlebox FO。这些限制继续阻止发布，不由 S2 的 focused 数据库合同替代；实验 scanner 和生产 v2 保持关闭。


MySQL 016 合并与 001 修复建议（后续增补）：

- 用户要求合并两份 016。活动目录只保留 `016_session_callback_lease_and_chat_runs.sql`，包含 callback lease 的三列/恢复索引与 Direct Chat runs 表；将 callback 的 `ADD COLUMN IF NOT EXISTS` 改为 MySQL 8 支持的 `ADD COLUMN`，由版本 runner 避免重复执行。
- 两份旧文件按原字节移至 `migrations/legacy/mysql/`，checksum 与 HEAD 原文件一致。已部署记录不作自动别名或改写；匹配的旧记录收到明确核对提示，checksum 漂移仍拒绝。部署步骤见 [016 历史核对文档](../../../migrations/reconciliation/016-session-callback-and-chat-runs.md)。要求数据库历史不可变的环境继续保留旧行，不能直接通过新版整链核验。
- 隔离 MySQL 验证合并 016 成功、旧 Session 行/NULL 保留、callback 恢复索引顺序、Chat 行写入、重复计划无 pending、两类旧记录拒绝且原值不变；入口已接入现有 MySQL CI。`bcs-admin` 常规测试 22 项通过，3 项真实 MySQL 测试默认 ignored，本次显式执行新增 016 live 测试通过。证据：`/tmp/bcs-mysql-016-admin.log`、`/tmp/bcs-mysql-016-live.log`。
- 001 最小建议是仅把非唯一索引中的 `reply_scope_key` 改为 700 字符前缀，保留 VARCHAR(768) 和完整 `active_slot_key` 唯一键；008 同源定义也需纳入后续方案。一次性 MySQL 实验复现原 DDL 的 1071 超长错误，修改后的完整 HumanInput CREATE 成功；两个 700 字符 Unicode 前缀相同的 scope 可按完整值区分，完整 slot 重复仍报 1062。证据：`/tmp/bcs-mysql-001-prefix-probe.log`；001/008 本身未改动，既有库使用增量修复，新库需单独确定修正版 baseline 与历史 checksum 策略。
- Bootstrap 对应迁移测试、workspace all-targets check、workflow 2 项测试与 `git diff --check` 通过；`--only 16 --emit-sql` 只输出合并迁移。全量 `--check-files` 已无 016 重号，继续明确报 001 索引超长；未弱化该检查。证据：`/tmp/bcs-mysql-016-bootstrap.log`、`/tmp/bcs-mysql-016-workspace.log`、`/tmp/bcs-mysql-016-ci.log`、`/tmp/bcs-mysql-016-files-check.log`。
- 016 合并不解除 FL-28/29 全链发布门禁；未对已有部署执行 DDL 或修改迁移记录，生产 v2 和 scanner 保持关闭。


MySQL HumanInput 索引修复（2026-09-15）：

- 已修正活动 001/008 中 `idx_human_input_scope_status` 的 `reply_scope_key(700)` 前缀；字段仍为 VARCHAR(768)，完整 `active_slot_key` 唯一约束不变。两份原始 SQL 按字节归档到 `migrations/legacy/mysql/`，不删除历史证据。
- 001 使用修正版 baseline body checksum，008 使用修正版完整文件 checksum；runner 只兼容文档列出的旧/新 checksum 对，版本/名称/dialect 必须匹配，未来改动和其他 checksum 不自动放行。旧库保留 001/008 原记录和 applied_at，由新增 MySQL 031 独立记录索引修复。见 [索引升级文档](../../../migrations/reconciliation/human-input-index-size.md)。
- 真实 MySQL 8.4 验证完整修正版 001 建库、独立 008 建表、fresh/已有索引升级、031 DDL 成功但未记版本后的重试、DDL 失败不记成功、前 700 个 Unicode 字符相同的 scope 仍按完整值过滤、完整 slot 唯一性、字段元数据和旧迁移记录/时间戳不变。历史全长索引使用 utf8mb3 fixture 表达，因为 MySQL 无法建出原 utf8mb4 超长索引；本项未宣称 OceanBase 部署验证。
- `bcs-admin` 24 项单测通过，4 项 MySQL ignored 已另行按 CI 顺序显式执行（Eventing → HumanInput index → 合并 016 → Loop migration）均通过，验证共享数据库清理和迁移记录隔离。workspace all-targets check、workflow 2 项测试及 `git diff --check` 通过。证据：`/tmp/bcs-mysql-031-admin.log`、`/tmp/bcs-mysql-031-shared-*.log`、`/tmp/bcs-mysql-031-workspace.log`、`/tmp/bcs-mysql-031-ci.log`。
- 全量 `--check-files` 现在通过（001～031 共 31 个版本），没有弱化索引长度检查。空库通过生产 `bcs-admin --apply --yes` 执行完整链时，001 成功，002 第一条 ADD COLUMN IF NOT EXISTS 在 MySQL 8.4 报 1064；后续链未执行。该失败单独登记为 FL-28/29 的实际发布阻塞，不能用静态检查通过替代。证据：`/tmp/bcs-mysql-031-files.log`、`/tmp/bcs-mysql-031-full-chain.log`。
- 本轮仅操作一次性 MySQL 测试库，未变更已有部署。未运行完整 singlebox/生产升级/多实例 FO，因完整迁移链和 Loop FO 尚未收尾；生产 v2 与 scanner 保持关闭。

MySQL 001 重复列清理（2026-09-15）：

- 按用户明确要求检查 Git 历史并清理活动 001：移除后续提交补入的五个 visibility 列，以及最初已存在但与 011 重复的 `tags_json`；这些列分别由 020 和 011 添加。扫描 001 的 32 张表与后续 ADD COLUMN，清理后无重复列。011/020 和历史 001/008 归档按字节保持原样，已有数据库不执行 DROP COLUMN。
- 更新活动 001 的 body checksum、既有精确旧/新 checksum 对和部署说明；旧迁移记录及时间戳继续保留。BCS AGENTS.md 新增规则：已提交的编号迁移不得新增列或回填后续 schema，新增列必须使用新的唯一后续编号，001 不维护为最新 schema 快照。
- 本轮 `cargo test -p bcs-admin` 24 项通过、4 项默认 ignored；`cargo test -p bcs --lib mysql_` 9 项通过；在一次性 MySQL 8.4 上显式执行 `human_input_index_migrations_apply_to_real_mysql` 通过，覆盖完整新 001 建表和历史记录保留。证据：`/tmp/bcs-001-cleanup-admin.log`、`/tmp/bcs-001-cleanup-bootstrap.log`、`/tmp/bcs-001-cleanup-live.log`。
- 本轮未重跑完整历史链和 singlebox。002 等历史 SQL 的 MySQL 语法问题仍由 FL-28/29 跟踪；本次不引入 SQL 自动翻译，也不扩大到修改其他历史增量。生产 v2 和 scanner 保持关闭。

MySQL 迁移语法兼容与方言编号说明（2026-09-15）：

- 按用户要求修正 MySQL 002/007/011/013/015/017/018/020：只将 `ADD COLUMN IF NOT EXISTS` 改为 `ADD COLUMN`，列定义和索引保持原样；原文件按字节归档，新增精确的原始/当前 checksum 对，保留已部署记录及时间戳。001 本轮不再修改，不引入执行时 SQL 翻译。部署说明见 [MySQL 语法兼容文档](../../../migrations/reconciliation/mysql-syntax-compatibility.md)。
- 新增完整链真实数据库合同，直接调用生产配置加载/计划/执行入口。在空库执行 001～031、重复执行无 DDL；从已记录 020 的历史 checksum fixture 升级到 031，验证旧记录/时间戳、参与者 tags 和 v1 snapshot 保留，新增 plan 为 NULL；未知 checksum 仍拒绝且不写记录。该 fixture 使用修正后的等价 DDL，不宣称已验证真实 OceanBase 历史部署。
- `cargo test -p bcs-admin` 26 项通过、5 项默认 ignored；`cargo test -p bcs --lib mysql_` 9 项通过。5 项真实 MySQL 测试已另行显式执行：完整链后，在同一库按 CI 顺序执行 Eventing、HumanInput index、合并 016、Loop migration，全部通过。workflow 2 项测试通过。证据：`/tmp/bcs-mysql-syntax-admin.log`、`/tmp/bcs-mysql-syntax-bootstrap.log`、`/tmp/bcs-mysql-syntax-chain.log`、`/tmp/bcs-mysql-syntax-shared-*.log`、`/tmp/bcs-mysql-syntax-ci.log`。
- 完整链合同已接入 MySQL CI，先于其他数据库合同执行，拒绝非空数据库并清理本测试创建的表。此前 002 等语法错误阻塞已解除；旧版拆分 016 的核对、实际生产/OceanBase 升级和完整 singlebox/FO 仍待验收，FL-28/29 保持未完成，生产 v2 和 scanner 保持关闭。
- README 已纠正 SQLite “仅到 019”和“两端版本号一致”的过时描述，列出历史功能映射、001～031 注册表及 SQL/Rust 的实现位置。保持历史编号，新增编号按方言顺延；AGENTS.md 明确冻结规则覆盖 Rust 内的 SQLite 历史迁移与 bootstrap DDL，MySQL 必须验证完整实际执行链。

Loop 开发草稿迁移合并（2026-09-15）：

- 按用户确认的方案，将尚未提交的 MySQL 027～030 合并为 `027_fixed_loop_runtime.sql`，SQLite 028～031 合并为 `028_fixed_loop_runtime.sql` / 028 注册版本。每个版本统一包含三列 snapshot plan、`failure_action` 和 Run/Session 恢复索引。删除原拆分草稿文件和注册项，MySQL 031 HumanInput 索引修复按字节保持不变；活动 MySQL 链为 001～027 加 031，共 28 个版本，SQLite 为 001～028。
- SQLite 使用单一 SQL 文件并保留逐列检查；验证 027 升级、六条 DDL 每个完成边界的重启、全部 DDL 完成但尚未记录版本的重试、失败不记版本、历史行/NULL/旧时间戳保留。旧草稿 028 的名称/checksum 不自动映射为新版本，现有数据库未被改写。
- 合并后的 MySQL 027 验证四张表的完整变更、两类索引的列顺序、早期/最后一条 DDL 失败时都不记录成功版本。完整链验证 28 个实际版本、重复 apply 无 DDL、带旧 checksum 的 020→031 升级；原历史 checksum 兼容和独立 031 升级仍通过。Store 对整个合并 SQL 逐条执行，Text/Prepared 两种协议的 snapshot/rerun/回滚合同通过。
- `cargo test -p bcs --lib migrations::` 30 项通过；`cargo test -p bcs-admin` 26 项通过、5 项默认 ignored；`cargo test -p bcs-collaboration-store` 85 项通过、1 项默认 ignored。6 项真实 MySQL 合同已显式执行并全部通过（完整链、Eventing、HumanInput index、合并 016、Loop migration、Loop Store）。MySQL/SQLite 静态迁移检查均通过。Store 回归发现并修正了一处仍使用草稿版本 31 的 SQLite 测试夹具，复跑通过。
- README、bootstrap CONTEXT、注册表、测试及当前任务状态已同步；历史执行记录保留旧编号对应的当时证据。AGENTS.md 补充首次提交前按共同发布范围合并迁移、独立修复单列的规则。本轮不改变 Loop 功能门禁和 FL-15～FL-16 / S4 / S5 未完成状态；未运行生产/OceanBase 升级、完整 singlebox 或真实多实例 FO。
- workspace all-targets check 与 `git diff --check` 通过。验证报告：`/tmp/bcs-loop-consolidated-sqlite.log`、`/tmp/bcs-loop-consolidated-admin.log`、`/tmp/bcs-loop-consolidated-store-unit.log`、`/tmp/bcs-loop-consolidated-chain.log`、`/tmp/bcs-loop-consolidated-{eventing,index,016,loop,store}.log`、`/tmp/bcs-loop-consolidated-{mysql,sqlite}-files.log`、`/tmp/bcs-loop-consolidated-workspace.log`。

HumanInput 索引草稿编号前移（2026-09-15）：

- 按用户要求将尚未提交的 `031_human_input_scope_index.sql` 前移为 `028_human_input_scope_index.sql`；SQL 字节及 checksum 不变。MySQL 027 仍为独立 Loop 迁移，MySQL 活动链现在为连续的 001～028；SQLite 编号未变。
- 同步独立索引测试的选择/记录/清理编号、完整链的连续版本断言、部署文档名称和 `--only 28` 命令、README 与当前任务状态。历史执行记录保留当时的草稿编号，链接指向现行文档；不为未发布的 031 增加自动迁移记录别名，也不改写已有数据库记录。
- `cargo test -p bcs-admin` 26 项通过、5 项默认 ignored；真实 MySQL 完整链和独立索引两项已显式执行并通过，验证 001～028 新建/重复执行、020→028 升级、001/008 原记录与时间戳保留。证据：`/tmp/bcs-index-renumber-admin.log`、`/tmp/bcs-index-renumber-chain.log`、`/tmp/bcs-index-renumber-index.log`。本轮仅操作一次性 MySQL 测试库。

发布记录：

| 项目 | 当前值 |
| --- | --- |
| 固定 Loop 能力 | 未启用；本文不改变运行配置 |
| FO 发布验收 | 未完成 |
| 实现 commit/PR | 待补 |
| canonical coverage 报告 | 待生成 |
| 尚未验证的部署边界 | 完整历史 MySQL 迁移链/生产库升级、生产 SQLite 升级、真实服务强杀/多实例、mixed-version 准入、回滚 |


Judge 输入与租约恢复（2026-09-15）：

- 新增 Node `runtime_phase=judging` 与独立 owner/token/until lease；Bot artifact、Human response/responder
  首次保存后不允许改写。正常请求和恢复都需要 claim；租约过期接管只继续原 attempt 的 Judge。
- Judge result、Node terminal、failure_action、既有 Judge audit 与可选公共 completion Event 同事务提交；
  旧 owner、旧 attempt、到期 lease、取消后的 Run 不能提交。成功清理 lease，本地失败条件释放。
  已提交的 Judge 不重跑；远端返回但本地事务未提交时可能同 attempt 重判，不承诺外部 exactly-once。
- 新 Runtime 测试覆盖 Bot/Human 恢复、并发请求与扫描、重复不同输入、旧 owner 接管后返回、取消、结果写失败、
  原 Judge 失败决策的 retry 收尾。SQLite 用两个独立进程验证缺失当前 Definition、缩小当前 compiler 上限时，
  仍从旧 snapshot/输入及过期 lease 恢复；这不等同于真实进程 kill 或全链路跨实例 FO。
- 新 Store 合同验证 Memory、SQLite 及 MySQL Text/Prepared 的不可变输入、并发 claim、递增 token、
  过期/取消/旧 attempt fencing、显式 release、legacy artifact-only 拒绝接管与 retry。
  SQLite/Memory 另覆盖 audit/public Event 写失败不提交 Node 或释放原 lease。
- schema 继续合入未提交的 MySQL 027 / SQLite 028。MySQL 仍为四条 DDL，SQLite 十条 DDL；SQLite
  验证所有部分执行窗口再跑迁移。历史 001～026（MySQL）/001～027（SQLite）和编号保持不变。
- 验证报告：`/tmp/bcs-judge-tests.log`（462 通过、1 默认忽略）、`/tmp/bcs-judge-migrations.log`（30 通过）、
  `/tmp/bcs-judge-admin.log`（26 通过、5 默认忽略）；本阶段三个真实 MySQL 入口分别显式通过：
  `/tmp/bcs-judge-mysql-chain.log`、`/tmp/bcs-judge-mysql-migration.log`、`/tmp/bcs-judge-mysql-store.log`。
  `/tmp/bcs-judge-workspace.log` 与 `/tmp/bcs-judge-{port-purity,forbidden-symbols,store-boundaries}.log` 通过。
  其余两项独立历史修复 MySQL ignored 测试本轮未重复执行，沿用上一轮证据。临时 MySQL 容器已停止并自动删除。
- 未完成：dispatch/opening/chat-result/terminal-IM checkpoint、真实跨实例外部调用与完整 FO/Singlebox 门禁。
  本阶段结果不解除 v2 和实验 scanner 的默认关闭状态。


Opening checkpoint 与初始节点恢复（2026-09-15）：

- 原 opening 在 Run 启动前以 `StateMachineOpeningPayload` 保存，包含 Run/Group/Session 身份、渲染后的原文、component、固定 client key 和原创建时间；相同载荷可重复提交，冲突载荷拒绝。恢复读取 snapshot 和原 opening，不使用当前 Definition、资源上限或 Group 配置重新生成它们。
- Memory/SQL 实现 checkpoint 保存、读取和 `pending → delivered` 历史屏障。历史使用 `message_id = client_msg_id = {run_id}:000-panel`；新增 MessageRepo 固定逻辑主键写入接口，SQL 依赖已有主键唯一性，重复插入的序号分配随本地事务回滚。旧历史的 client key 若已匹配则保留原随机 ID，并核对内容/身份/可见性；冲突不能当作成功。没有新增 Message 列、opening 外部租约或跨 Run/Message 大事务。
- Scanner 有界合并 Pending/Running Run 页面，snapshot/Session/节点图验证后恢复 Run start，再保存并核对 opening 历史，标记成功后只派发尚未启动的初始节点。Bot/Human 共用此屏障和原 Node CAS，Running attempt 不重发；开场历史完成不代表实时前端已 ACK。终态 Run 不重新启动，缺失 snapshot/opening 的历史 Run 只报告诊断，待定义创建者存活判定后再补终态收敛。
- Schema 合入尚未提交的 MySQL 027（5 条 DDL）/SQLite 028（11 条 DDL），不增加版本，不改 001。SQLite 覆盖各部分提交点的恢复；MySQL 验证完整历史链、合并增量、checkpoint 字段/主键和最后建表失败不登记成功。新二进制即使只处理 v1、关闭 scanner，也须先应用这些 schema。
- 验证范围：原 opening 配置变更后仍使用保存原文；Pending→Running 前/历史写入后中断；Bot/Human 初始节点；并发恢复；取消；历史冲突；checkpoint 保存/完成标记和初始派发写失败；两状态合并游标；SQLite 独立进程 opening 两个窗口；Memory/SQLite/MySQL Text/Prepared 固定 ID 并发和旧消息兼容。
- 本轮验证：Runtime/Collaboration Store/Message Store/Service API 共 544 项通过、0 失败；1 项真实 MySQL 默认忽略后在临时 MySQL 8.4 显式通过（含 Text/Prepared）。SQLite bootstrap 迁移 30 项、admin 26 项（5 项默认忽略）、HTTP Run/Group 合同 13+40 项通过；MySQL 全历史链、合并迁移和真实 Store 各 1 项显式通过。补充旧消息兼容和 Pending/Running 游标后，相关 Store 2 项及 Runtime 1 项定向复验通过。workspace all-targets、port purity、forbidden symbols、Store boundaries 和 `git diff --check` 通过。
- 报告：`/tmp/bcs-opening-tests.log`、`/tmp/bcs-opening-identity.log`、`/tmp/bcs-opening-cursor.log`、`/tmp/bcs-opening-migrations.log`、`/tmp/bcs-opening-admin.log`、`/tmp/bcs-opening-http.log`、`/tmp/bcs-opening-mysql-chain.log`、`/tmp/bcs-opening-mysql-migration.log`、`/tmp/bcs-opening-mysql-store.log`、`/tmp/bcs-opening-workspace.log` 及 `/tmp/bcs-opening-{port-purity,forbidden-symbols,store-boundaries}.log`。
- FL-15/16 保持未勾选。剩余 dispatch 原始载荷/checkpoint 与发送结果未知处理、chat-result、terminal-IM、缺失启动事实的终态收敛，以及完整跨实例/真实进程杀死/Singlebox FO。生产 v2 与实验 scanner 均仍默认关闭。


Bot dispatch checkpoint 与发送结果未知恢复（2026-09-15）：

- 普通 Bot 路径在 Node Running CAS 后保存不可变请求、Run/Node/attempt/Session/Bot 身份、目标引用和原 deadline；保存失败不发送。恢复原样使用请求/ID，不调用当前 compiler 或重新渲染 Group。Provider 只保存引用，不保存 URL、凭据或转发 headers；发送时解析同一引用的当前可信连接信息。
- 正常派发与恢复共用 owner/token 短租约和发送前 `pending → delivering` CAS。仅 pending 可在原 deadline 内接管首次发送；已有发送标记即使未真正开始 IO，也不重投。ACK 后落库失败继续等待原 correlation 的结果或原 deadline；没有统一 Provider 幂等承诺，不添加 status/replay/cancel 协议。
- ACK 与原 deadline 到期竞争时，以 checkpoint/Node 局部事务校验 active Run、attempt、无 artifact 和 checkpoint 状态，至多一个动作生效。SQL 两条路径均按 checkpoint 后 Node 持锁；Node phase 写失败或到期 checkpoint 写失败时完整回滚。明确拒绝/调用失败保存原 error 后沿用直接 FailRun；结果未知到期才按既有 max attempts 决定下一 attempt，不改变 iteration。
- Node timeout 为 None 时，以创建时的 Provider timeout 固化歧义 deadline，后续配置变化不延长它；ACK 成功后仍保持 Node timeout 禁用。Running 节点缺少 payload 只报告诊断，不能推断未发送。正常完成、失败、取消和 active Run 扫描会 supersede 失效 attempt 的未完成 checkpoint。
- Schema 继续合入未提交的 MySQL 027（5 条 DDL）/SQLite 028（12 条 DDL），不新增版本或修改 001。本表增加 Node/attempt/deadline/lease/error 与 Run 查询索引；新二进制即使仅处理 v1、scanner 关闭，也须先应用新 schema。已应用旧草稿的临时开发库须重建，不能改已部署的版本记录来掩盖差异。
- 新增 7 项 Runtime 和 4 项 Store 测试；SQLite 独立进程新增 pending 与 unknown 两个派发窗口，包含当前 Group/Definition 变化。覆盖发送前失败、发送标记后中断、ACK 后写失败、原拒绝恢复、lease/attempt fencing、取消、迟到结果、原 deadline/关闭 timeout、并发 ACK/expiry 和局部事务回滚。原 1ms timeout 测试改为等待实际保存的 deadline，避免持久化屏障使时序假设失效。
- 最终核心回归：480 passed、0 failed、1 ignored；该 Store MySQL 项已显式执行通过。SQLite migrations：30 passed；admin：26 passed、5 ignored，其中本轮显式执行全链和 Fixed Loop 两项，其余历史专项未重跑。HTTP 两套相关合同共 53 passed。临时 MySQL 8.4 上完整 001～028 链/历史升级、合并增量、Text/Prepared Store 合同全部通过。workspace all-targets、port purity、forbidden symbols、Store boundaries 和 diff whitespace 检查通过。

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime -p bcs-collaboration-store -p bcs-service-api
cargo test --manifest-path src/bcs/Cargo.toml -p bcs --lib migrations::tests
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-admin
# BCS_TEST_MYSQL_URL 指向本次创建的空临时 MySQL 数据库；三项顺序执行
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-admin full_mysql_migration_chain_applies_and_preserves_history -- --ignored
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-admin fixed_loop_migration_applies_to_real_mysql -- --ignored
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-store --test mysql_store real_mysql_fixed_loop_snapshot_and_rerun_contract -- --ignored
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
```

- 报告：`/tmp/bcs-dispatch-final-tests.log`、`/tmp/bcs-dispatch-expiry-tests.log`、`/tmp/bcs-dispatch-migrations.log`、`/tmp/bcs-dispatch-admin.log`、`/tmp/bcs-dispatch-http.log`、`/tmp/bcs-dispatch-mysql-{chain,migration,store}.log`、`/tmp/bcs-dispatch-workspace-final.log`、`/tmp/bcs-dispatch-{port-purity,forbidden-symbols,store-boundaries}-final.log`。
- FL-15/16 保持未勾选。仍需 chat-result、terminal-IM checkpoint，缺失 startup/dispatch 原始事实的终态收敛，终态 Run 遗留 checkpoint 清理，以及真实多实例/进程杀死/Singlebox FO 门禁。生产 v2 和实验 scanner 仍默认关闭。


FL-17 统一 API 投影（2026-09-15，已完成）：

- Run 的 start/get/rerun 和 Session 创建响应均返回完整 `node_execution_metadata`；包含尚未执行和已跳过的 Loop body 节点。Node detail、Graph 与 preview 复用同一 execution 对象，不按 ID 形状反推逻辑节点或轮次。并行 body 的每个节点、每一轮均覆盖。
- Graph 返回原 authoring `graph_mode: hierarchical`，同时返回保存的 `execution_graph_mode: acyclic` 和 compiler version。result 出口从 plan 投影 continue/break/exhausted 的完整 `loop_route`；保留真实 outcome 与 logical_outcome，普通边省略该字段。
- Run/Node/Graph/PendingHuman 只读查询使用不可变 snapshot。关闭 v2 执行、收紧当前资源上限或删除当前 Definition 不影响历史读取；测试用不同于当前编译规则的 opaque ID 验证这一点。存在的 v2 snapshot 若损坏或缺少必要 plan，继续报错；无 snapshot 的旧 Run/Node 保留裸记录查询行为，不猜测版本。新建、rerun 和推进门禁不变。
- 两套 HTTP adapter 与 OpenAPI 共用 `tests/fixtures/fixed_loop_api.json`；覆盖 Group start、Session start、get、rerun、Node、Graph、PendingHuman 首轮/后续轮。OpenAPI 复用已有 metadata/route schema，验证 context 四字段必需、首轮 null 和后续轮原始结果；v1 省略新增字段。面板仅同步 DTO 类型，展示交互仍由 FL-20 跟踪。
- 完整回归发现三个旧 Bot timeout scanner 测试假定 1ms 内完成派发；其 fixture 改为 1000ms 并等待实际保存的 deadline，保留原超时/缺失 Group/缺失 Session 的断言，不修改生产超时语义。
- 验证结果：Runtime/Service API **390 passed**（含 Runtime progression 144 项和新增投影 5 项），两套 HTTP **99 passed**，直接应用/Session 消费者 **179 passed**；以上均无失败或忽略。OpenAPI 四个专项 **49 passed**，公开 OpenAPI **67 operations validated**。workspace all-targets、面板 typecheck、port purity、forbidden symbols、Store boundaries 和 `git diff --check` 通过。

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime -p bcs-service-api
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test collaboration_definition_routes --test collaboration_run_routes --test session_routes -p bcs-http --test groups_contract
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-app-session --test v1_session_service -p bcs-app-group --test v1_group_service -p bcs-session --test session_launch
src/backend/.venv/bin/python -m pytest src/bcs/tests/openapi/test_fixed_loop_run_projection_contract.py src/bcs/tests/openapi/test_fixed_loop_context_contract.py src/bcs/tests/openapi/test_fixed_loop_preview_contract.py src/bcs/tests/openapi/test_state_machine_run_v1_contract.py -q
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
npm --prefix src/bcs/assets/panel run typecheck
```

- 报告：`/tmp/bcs-fl17-core-final.log`、`/tmp/bcs-fl17-http-final.log`、`/tmp/bcs-fl17-consumers.log`、`/tmp/bcs-fl17-openapi.log`、`/tmp/bcs-fl17-openapi-validation.log`、`/tmp/bcs-fl17-workspace-final.log`、`/tmp/bcs-fl17-panel-types.log` 及 `/tmp/bcs-fl17-{port-purity,forbidden-symbols,store-boundaries}.log`。
- FL-17 已勾选；下一项是 FL-18 Event/持久化消息 metadata。本项未增加迁移，未运行完整 Singlebox/真实多实例 FO 或面板视觉验收；这些仍由 FL-15/16、FL-20 和 S5 对应任务跟踪。生产 v2 与实验 scanner 仍默认关闭。

### 2026-09-15：FL-18、FL-20、FL-22 完成及 FL-15/16 拆分

- Event started/completed/retry_scheduled 从已保存的 Execution Plan 投影同一 execution 对象；保持原公共事件类型、producer key 和 Node/Event 局部事务。新增 fixtures 验证 full/metadata_only 投影一致、attempt 与 iteration 独立、v1 省略 execution。
- v2 Completed Node 输出复用 MessageRepo 固定主键写入，保存 text 与 metadata；写失败返回错误并停止该节点的后继推进，通用恢复幂等补写。历史使用批量读取，返回保存的 metadata；Human 输出保持定向可见，Bot 输出保持 FullOnly。新增 SQLite 独立进程断言验证重启后消息和原 plan 一致。本轮未新增或修改迁移。
- 修复历史只读查询的鉴权入口仍依赖执行开关的问题；关闭 v2 执行、删除当前 Definition 后仍可授权查询已保存的 Run。实际执行门禁保持不变。
- 面板显示 logical node、轮次、attempt 和 execution ID，区分 continue/break/exhausted。Graph Node 添加可选实际 outcome，用它判断已选边，避免多个出口共享目标时误高亮。Human entry 单独显示前一轮结果并使用当前 pending execution ID 回复。新增 UMD 交互测试和可复用视觉 fixture，实际浏览器检查了退出连线及人工弹窗。
- CLI 新增 `collaborate query --run <id> [--node <execution-id> | --graph | --pending]` 和 `collaborate respond --run <id> --node <execution-id> --content <text>`。回复前核对服务端授权 pending 列表，POST 只含 content；validate 显示资源/能力诊断路径、warnings 和 Loop preview。JSON 保留服务端 metadata，文本显示 logical/iteration/attempt/context。现有 Singlebox CLI story 已加入新 leaf command 调用和误回复拒绝断言。
- FL-15 保留父项未完成，拆成 **6/10** 已完成子项；剩余 Chat 最终结果 publication、terminal IM、缺失原始事实收敛、终态 checkpoint 清理。FL-16 拆成 **7/10** 已完成子项；剩余对应故障注入、真实多实例/进程强杀和完整 Singlebox/FO。Node 输出持久化不替代 Chat 最终结果 publication。

本轮实际验证命令：

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime -p bcs-eventing -p bcs-service-api
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test collaboration_definition_routes --test collaboration_run_routes --test session_routes
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-http --test groups_contract
NO_PROXY=localhost,127.0.0.1,::1 cargo test --manifest-path src/bcs/Cargo.toml -p bcs-cli
NO_PROXY=localhost,127.0.0.1,::1 cargo test --manifest-path src/bcs/Cargo.toml -p bcs-cli --test e2e custom_collaboration_test
src/backend/.venv/bin/python -m pytest src/bcs/tests/event_contract src/bcs/tests/openapi/test_fixed_loop_run_projection_contract.py src/bcs/tests/openapi/test_fixed_loop_context_contract.py src/bcs/tests/openapi/test_fixed_loop_preview_contract.py src/bcs/tests/openapi/test_state_machine_run_v1_contract.py -q
src/backend/.venv/bin/python src/bcs/scripts/validate_openapi_contract.py --root src/bcs/api-contracts/v1
npm --prefix src/bcs/assets/panel run verify
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
bash -n src/bcs/scripts/e2e-test/cli-stories.sh
git diff --check
```

另从 `src/bcs/` 执行 `scripts/ci/check-port-purity.sh`、`check-forbidden-symbols.sh`、`check-store-boundaries.sh`，三项均通过。

- Runtime/Eventing/Service API **425 passed**，HTTP **99 passed**，CLI **196 passed、2 项既有 ignored**；validate warnings 最后修改后 CLI 相关 **9 项复验通过**。Event/OpenAPI 专项 **61 passed**，公开 OpenAPI **67 operations validated**。面板完整 verify、workspace all-targets 和三项架构检查通过。
- 报告：`/tmp/bcs-loop-final-core.log`、`/tmp/bcs-fl18-http.log`、`/tmp/bcs-loop-cli.log`、`/tmp/bcs-loop-cli-final-focused.log`、`/tmp/bcs-loop-final-contract.log`、`/tmp/bcs-loop-panel-verify.log`、`/tmp/bcs-loop-consumers-workspace.log`。CLI 的 localhost mock 测试在允许本地端口并设置 NO_PROXY 后通过。
- 本轮未执行完整 Singlebox、真实多实例/强杀 FO 或生产数据库升级；新增 CLI story 已接入，但不将其计为实测 100% leaf coverage。相关发布验收仍由 S5 跟踪。S4 仍剩 FL-19 IM、FL-21 独立 Frontend preview、FL-23 观测；生产 v2 执行与实验 scanner 继续默认关闭。

### 2026-09-15：FL-19 IM 通知投影与已有请求恢复

- 新增 Channel 内部通知渲染器。direct_assignee 显示 Loop/轮次、前轮 logical/execution ID、outcome、output 和 completed_at；首轮明确没有上一轮结果。fixed_group 沿用共享群隐私边界，只显示 Loop/轮次和 Workbench 指引，不发送前轮私密内容或结果身份；不改写结构化 previous_result。
- 渲染一次后写入已有 notification_text。相同 event_id 重试会校验 Run/Session/Node/assignee/binding 身份，然后复用原文本、目的地和 deadline。已 Active、Responded、Expired、Cancelled 不重发；Queued 复用槽位提升，Notifying 使用原 interaction stream key；provider 重试耗尽仍进入 DeliveryFailed，保持 Workbench 可回复，不创建第二个 request。
- 将“已持久化的投递失败”与“持久化本身失败”分开处理。只有前者可跳过当前项继续队列；状态写失败和 CAS 冲突必须返回错误，避免队列表面成功、实际上停留在未确认状态。
- 通过现有 SessionChannelOutboundPort conformance 入口测试：四份完整文本 golden 共用 fixed_loop_api.json；磁盘重载和服务重建后恢复发送前的 Notifying、释放槽位后激活 Queued，验证文本不受新的 instruction/context 影响；确认事件重试、错误轮次、隐私过滤、provider 失败、状态写失败、过期不延长和 v1 原文兼容。
- 本次受控恢复由原 ready event 重试进入，没有新增 scanner、数据库列或 Loop 专用通知状态。发现通用 scanner 尚未主动处理 WaitingHuman notification，已明确追加 FL-15.11；跨实例竞争、发送结果未知及真实强杀仍由该项与 FL-16/S5 验收。

实际验证：

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-channel -p bcs-channel-store -p bcs-collaboration-runtime -p bcs-service-api
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-channel --test conformance_session_channel_outbound
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
git diff --check
```

主回归 520 项通过、无失败或忽略；最后增加过期用例后的 conformance 8 项复验通过，workspace all-targets 与 `git diff --check` 通过。报告：`/tmp/bcs-fl19-regression.log`、`/tmp/bcs-fl19-final-conformance.log`、`/tmp/bcs-fl19-workspace.log`。从 `src/bcs/` 执行的 port purity、forbidden symbols、Store boundaries 三项检查通过。

未运行真实 IM Provider、MySQL/OceanBase 部署、完整 Singlebox 或真实多实例 FO；本轮没有改变 SQL/schema，通知文本的存储仍使用现有字段。生产 v2 和实验 scanner 均保持默认关闭。


### 2026-09-15：FL-21 Frontend Definition preview

- 补齐 Frontend DTO 的 execution_graph_mode、node.execution 和 edge.loop_route，布局优先采用服务端 execution mode；保留 authoring graph_mode，不解析或重建 opaque ID。每轮显示 logical name 与 iteration/max，长 break 连线绕开中间节点，同目标的 exhausted/break 分别显示标签，提示保留真实 outcome。
- validation warnings 不再丢弃，错误显示 code、authoring path 和 hint。服务端返回 VALIDATION_ONLY_FEATURE 时仍可查看图和绑定信息，创建处理入口及按钮均阻止提交；普通 advisory warning 不阻止 v1。展示定义的最多轮次和展开节点数；API 未返回单独的服务器配置上限，因此资源拒绝继续显示服务端原始诊断，不硬编码上限。
- 拆出原有 ReactFlow Node 组件，沿用原绑定、选点和键盘交互。共享 `tests/fixtures/fixed_loop_preview.json` 同时经过 OpenAPI schema、Frontend layout/validation 和展示测试。新增 [可复用视觉 fixture](../../../../frontend/test/fixed-loop-preview/README.md)，直接挂载生产预览组件；实际浏览器验证三轮、六条边、第二轮选点、普通 v1 键盘选点及 390px 宽度无横向溢出/出口裁切。测试页没有真实创建请求。

实际验证（从仓库根目录）：

```bash
npm --prefix src/frontend test -- --runInBand src/pages/GroupChat
src/backend/.venv/bin/python -m pytest src/bcs/tests/openapi/test_fixed_loop_preview_contract.py -q
bash src/frontend/scripts/ci_test.sh
git diff --check
```

从 `src/frontend/` 执行 `./node_modules/.bin/vite build --config test/fixed-loop-preview/vite.config.mjs`，构建通过。GroupChat **53 passed / 9 suites**，OpenAPI preview **11 passed**，Frontend CI 通过。报告：`/tmp/bcs-fl21-groupchat-final.log`、`/tmp/bcs-fl21-openapi.log`、`/tmp/bcs-fl21-ci-final.log`、`/tmp/bcs-fl21-preview-build.log`。

类型检查的限制单独记录：本地 TypeScript 4.9.5 无法解析已安装 `@types/d3-dispatch` 的声明（9 项解析错误），项目也缺少 ReactDOM 类型声明。未改写依赖锁文件或跳过检查；使用仓库已有的 TypeScript 5.9.3 和 Panel 已安装的 ReactDOM 18 类型，通过临时 compiler host 对比同一依赖环境下的 HEAD 与当前源文件，均为 **155 项既有诊断，新增 0 项**。这项补查证明本轮没有新增类型错误，不代表全量类型检查通过。报告：`/tmp/bcs-fl21-type-comparison-with-react-dom.log`，对比工具和补充类型映射只用于本地验证。

本轮没有 Rust 实现、数据库或公共 API schema 变更，未重跑 Rust workspace、完整 Singlebox、真实 Provider 或多实例 FO。FL-21 已勾选；S4 剩余 FL-23，FL-15/16 和 S5 的未完成项保持原状态，生产 v2 与实验 scanner 仍默认关闭。


### 2026-09-15：FL-23 Loop 日志与低基数指标

- 新增 Service API 的 StateMachineLoopInstrumentationHook 和闭合 metric/outcome/reason 类型；默认不注入，bootstrap 的三个 CollaborationRuntime 装配点复用已有 metrics feature/config。Prometheus 实现仅更新进程计数器，Test Support 提供 noop 和共用合同输入；不存在新的数据库字段、事务、外部请求或 feature flag。
- Bot/Human entry 在 attempt 0 的 Running CAS 成功后计一次 started；result 节点在 Completed CAS 成功后计一次 completed，并按原 plan 路由计 break/exhausted。中间 body 节点、普通 v1/v2 节点、fan-out、retry、stale/cancelled terminal 和已完成结果恢复不新增轮次。Judge 写失败不计成功，恢复首次提交才计；continue 的后续 entry 恢复首次启动正常计数。
- 编译器内部增加 typed rejection 分类，保留原公开 code/path/message 和纯编译入口。注入观测的 v2 validation/execution compile 调用失败时计 invalid_definition 或 resource_limit，不从错误文案提取分类，不把 YAML shape/执行门禁/snapshot 读取计入 compiler 拒绝。四类资源限制均有测试。
- outcome label 限定 complete/done/approved/rejected/other；100 个不同自定义值仅增加同一个 other 序列。指标无 Run/Node/Loop/Bot/iteration/attempt 或载荷标签。结构化日志提供逻辑/执行身份、轮次、attempt、transition 和真实 selected_outcome，retry/ignored 也可关联，且不复制 artifact。
- 两类恢复测试验证 continue/break/exhausted：已提交结果后派发写失败，重建 runtime、移除当前 Definition 后重复恢复不重计；Judge 结果写失败后恢复只统计实际提交的结果。它们验证本项计数边界，不替代 FL-15/16 的真实多实例、强杀和剩余 publication 恢复验收。状态提交到进程计数之间的崩溃可导致少计；明确不为 metrics 增加持久化去重或恢复补账。

实际验证（仓库根目录）：

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime -p bcs-service-api -p bcs-test-support
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-runtime --test runtime_progression loop_observ
NO_PROXY=localhost,127.0.0.1,::1 cargo test --manifest-path src/bcs/Cargo.toml -p bcs --test metrics_fixed_loop --test metrics_wrappers --test metrics_cardinality --test metrics_endpoint --test metrics_http --test metrics_snapshot
cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets
cargo check --manifest-path src/bcs/Cargo.toml -p bcs --no-default-features --lib
git diff --check
```

主回归 **424 passed，0 failed，1 项既有 doctest ignored**；指标集成 **12 passed**；最后增加 continue 恢复场景后，Loop 专项 **7 passed**。真实本机 validation 请求验证 /metrics 中 resource_limit 拒绝计数恰好增加一次，五种指标渲染和标签约束通过。workspace all-targets 和不带 metrics feature 的库构建通过；从 `src/bcs/` 执行 `bash scripts/ci/check-port-purity.sh`、`check-forbidden-symbols.sh`、`check-store-boundaries.sh` 三项通过。

报告：`/tmp/bcs-fl23-core.log`、`/tmp/bcs-fl23-focused-final.log`、`/tmp/bcs-fl23-metrics-final.log`、`/tmp/bcs-fl23-workspace.log`、`/tmp/bcs-fl23-no-metrics-feature.log` 和三份架构 gate 日志。指标服务测试需要本机临时监听端口，获得执行环境授权后通过；先行测试捕获缺失 IterationStarted 的失败证据保留在 `/tmp/bcs-fl23-red.log`。

FL-23 已勾选，S4 全部完成。下一项回到 FL-15.7 Chat 最终结果 publication checkpoint；FL-15 仍为 6/11、FL-16 仍为 7/10，S5 的全量合同传播、Singlebox、真实跨实例/强杀与部署门禁未完成。本轮未重跑真实 MySQL/OceanBase 升级、真实 Provider 或完整 Singlebox；生产 v2 与实验 scanner 继续默认关闭。


### 2026-09-16 — FL-15.7 Chat 最终结果 publication checkpoint

- 正常路径与 scanner 共用 `ensure_chat_result`：所有 Node terminal 后，将原 Run/Group/Session、发起 Bot、最终结果、消息时间和 90 秒原截止时间独立保存到既有 delivery checkpoint 表。使用 `smrun:{run_id}:chat-result`，不新增迁移或 Run phase，不引入跨 Store/全图事务。
- 只有 Pending 可 claim；30 秒以内 owner/token lease、发送前 Pending → Delivering 持久化和 active Run fencing，阻止并发发布、旧 owner ACK、取消后推进。正常返回条件释放 lease，进程退出才等待到期。
- publisher 保存固定 `state-machine-result:{run_id}` 消息主键并核对历史原文/身份/时间/可见性，再沿用既有同步 message-flow。稳定 client ID 只证明历史幂等：旧路由仍会产生新的 Bot delivery ID，因此不盲目重发 Delivering。结果未知时 Run 暂留 Running，原截止时间后明确 Failed，错误说明消息可能已送达。要透明重投须另补消息层幂等 acceptance 合同。
- Delivered 后 Run 写失败只恢复收尾，不再调用 publisher；Failed 后 Run 写失败使用原 error 收敛。保存/claim/发送标记/ACK 的存储错误向调用方传播，不能被吞成成功或 publisher 拒绝。原版本在保存前绝不 IO，保存前失败可由原 snapshot/terminal Node 重建；仍要求 pre-FO drain，禁止混合旧实例主动恢复。
- 验证：`cargo test -p bcs-collaboration-runtime -p bcs-collaboration-store -p bcs-service-api -p bcs-test-support` 共 **527 通过、0 失败**；默认忽略的真实 MySQL 用隔离 MySQL 8.4 显式运行通过，另一项为既有 Test Support doctest。共享 repo 合同覆盖 Memory/SQLite/MySQL Text+Prepared 的载荷不可变、目标一致、双 claim、过期 owner、ACK/expiry 竞争、取消及环境隔离。SQLite 独立进程分别恢复 Pending、Delivered、unknown Delivering、Failed，移除当前 Definition，原输出/目标不变且 publisher 总调用一次。
- publisher 历史冲突/写后路由失败单测通过；workspace all-targets、port purity、forbidden symbols、Store boundaries 和 `git diff --check` 通过。复用已验证的 MySQL 027 / SQLite 028 表，本轮未改任何 migration。
- FL-15.7 已勾选，FL-15 为 **7/11**，剩余 terminal IM、缺原始事实收敛、终态 checkpoint 清理、等待人工通知主动恢复。FL-16.8 部分完成，FL-16 父项仍为 **7/10**；真实多实例强杀、完整 Singlebox/FO 与 S5 发布门禁尚未完成，生产 v2/scanner 继续默认关闭。


### 2026-09-16 — FL-15.8 终态 IM checkpoint

- Completed/Failed Service Run 在 Session 完成前独立保存原 Run/Session activation、去重收件人、渲染原文、完成时间与 90 秒原截止时间；保存失败返回错误，Session 留在 Running 供现有恢复页补做。Aborted 保持原合同，不创建终态 IM。通知失败不改变已经 terminal 的 Run 结果。
- 复用 `smrun:{run_id}:im-terminal`，以逐收件人的 Pending/Sending/Delivered/Failed 和 cleanup marker 记录部分进度。仅绑定/Provider 等只读预检失败允许在原期限内重试；Sending 后的调用错误、超时或 ACK 丢失都按可能已送达处理，恢复绝不重发。发送使用既有稳定 run key 和保存原文，不读取当前 Definition 重新渲染。
- 30 秒 owner/token lease 和进度 CAS 校验同一 Completed Service activation；新 activation 拒绝旧发送/ACK，旧 Pending intent 转 Superseded。先前已送达的目标不随其他目标重试；没有收件人只完成原 cleanup。存储写失败继续向上传递，不冒充外部投递失败。
- 既有实验 scanner 增加独立 IM Run 游标；每页最多 32 个候选，覆盖已 Completed Session。三类游标分别推进和重试，follower 不扫描，demotion/shutdown 取消进行中的页。原 HumanInput 队列推进沿用既有 request guard，其主动恢复和 Notifying 歧义仍归 FL-15.11。
- 两份 migration 均经 `git ls-files` 确认为尚未提交：只在 MySQL 027 / SQLite 028 合并草稿中增加 checkpoint `progress_json` 和 `(env, operation_kind, status, aggregate_id)` 索引，不新增编号、不修改本轮范围外的历史迁移。SQLite bundle 为 13 条；更新迁移说明与部分提交恢复测试。已运行过较早草稿的临时开发库须重建，不改历史校验记录。
- 验证：受影响的 Runtime/Store/Channel/Service API/Test Support **637 passed、0 failed**；其中默认 ignored 的真实 MySQL 合同另行显式运行通过，另 1 项为既有 doctest。scanner **5 passed**、SQLite migration **23 passed**。SQLite 独立 prepare/recover 进程覆盖 intent 保存前、Pending、Sending/未知、Delivered、Failed Run 和原期限耗尽六窗口；移除当前 Definition，原结果/文本/目标保留，不重跑 Bot。用例修正节点级 max_attempts 后验证 Failed Run 路径确实进入终态。
- 隔离 MySQL 8.4 验证 Memory/SQLite 同形合同在 Text/Prepared 下的不可变载荷、双 claim、旧 owner、JSON 进度 CAS 和新 activation fencing；027 专项检查新增列/索引、重复应用、DDL 中途失败不记成功，完整历史迁移链及校验记录保留测试通过。临时容器已删除。workspace all-targets、port purity、forbidden symbols、Store boundaries 和 `git diff --check` 通过；无全局格式化。
- 日志：`/tmp/bcs-fl158-core-verified.log`、`/tmp/bcs-fl158-restart-final.log`、`/tmp/bcs-fl158-mysql-store.log`、`/tmp/bcs-fl158-mysql-migration.log`、`/tmp/bcs-fl158-mysql-chain.log`、`/tmp/bcs-fl158-scanner.log`、`/tmp/bcs-fl158-sqlite-migrations.log`、`/tmp/bcs-fl158-workspace.log` 及三份架构检查日志。
- FL-15 为 **8/11**，剩余 FL-15.9 缺失原始事实收敛、FL-15.10 终态 checkpoint 清理、FL-15.11 等待人工通知主动恢复。FL-16.8 的 IM 范围完成，FL-16 父项仍为 **7/10**；本轮未运行真实多实例/强杀、真实 IM Provider、OceanBase 或完整 Singlebox，不能据此宣称完整 FO。生产 v2/scanner 继续默认关闭。


### 2026-09-16 — FL-15.9 缺失原始事实的终态收敛

- 缺 snapshot/opening 的活动 Run 从原 created_at 起保留 90 秒准备宽限。缺记录不证明进程已死；到期只允许 repository 在重查事实仍缺失、Run 仍 active 时撤销未完成的准备请求。前台明确写失败可立即使用同一失败路径。迟到创建者不能恢复 Failed Run，也不能为失效 Node/attempt 保存新派发。
- Run Failed 与类型化 `startup_failure` 事实在一个局部事务中落库，只影响失败路径；正常创建不多写 heartbeat/lease。事实冻结 Run/Session/activation、缺失类型、原创建时间、失败时间和 error，存入现有 checkpoint 表，不修改任何 migration。失败事实写失败回滚 Run CAS；Session 完成另行提交并由现有 Running Session 页恢复。
- 缺 snapshot 的 Session 补写严格匹配类型化失败事实，使用原 activation；不依赖 error 字符串识别，不读取当前 Definition 补编译、不补造终态 IM。已有 snapshot 的缺 opening 失败保持原 Session/IM 路径，callback 沿用原 dispatcher。缺原 activation 时仍拒绝猜测当前 activation。
- Running Bot 缺 dispatch checkpoint 且无 artifact/Provider run ID：有原 Node deadline 时等待它并保存原 max_attempts 对应的 Retry/FailRun；无 deadline 时使用原 started_at 加 90 秒并 FailRun，历史 started_at 也缺少才使用 Run 原 created_at。条件更新重新检查 attempt、Run/Node 状态及事实缺失；不重发原 attempt、不从当前 Provider 配置制造 deadline。已保存 Provider run ID 是明确受理证据，保留原事件/timeout 等待语义，尤其不误杀关闭 timeout 的旧节点。
- Memory/SQLite/MySQL Text+Prepared 共享合同覆盖宽限边界、已补齐事实、并发一次提交、旧 attempt、取消、已完成结果、无开始时间旧行、受理证据和前台立即失败。SQLite trigger 与 MySQL CHECK 注入失败记录写失败，验证 Run CAS 同时回滚。前台准备写失败和新 activation 的专项保证 Session 不滞留或被旧 Run 错误完成。
- 独立 prepare/recover 进程覆盖缺 snapshot、缺 opening、缺 dispatch/no-timeout、原 timeout retry、Run 失败后 Session 写失败、v2 原 snapshot、已有 Provider run ID 七个场景；prepare 删除当前 Definition，恢复不依赖它、不重新派发未知旧 attempt。额外暂停真实创建 Future，验证宽限内不误收敛、失效后继续创建不能发送。
- 验证：主回归 `cargo test -p bcs-collaboration-runtime -p bcs-collaboration-store -p bcs-service-api -p bcs-test-support` **538 passed、0 failed**；默认 ignored 的真实 MySQL 合同另行在隔离 MySQL 8.4 上显式通过，另 1 项为既有 doctest。最后补充受理证据/旧 activation 后，Runtime 专项 **5 passed**、Memory/SQLite 共享合同 **2 passed**，真实 MySQL Text+Prepared 再次通过。先行失败均为测试夹具复用 Session ID 或继承 Provider ID，已修正并在新空库复验，未放宽断言。
- `cargo check --workspace --all-targets`、port purity、forbidden symbols、Store boundaries、`git diff --check` 通过。日志：`/tmp/bcs-fl159-core.log`、`/tmp/bcs-fl159-runtime-final2.log`、`/tmp/bcs-fl159-store-verified.log`、`/tmp/bcs-fl159-mysql-complete.log`、`/tmp/bcs-fl159-workspace-verified.log` 和三份架构检查日志。本轮没有 schema 改动，未重复跑完整迁移链；未运行 OceanBase、真实多实例/强杀、真实 Provider 或完整 Singlebox。
- FL-15 为 **9/11**，仅剩 FL-15.10 终态 checkpoint 清理与 FL-15.11 等待人工通知主动恢复。FL-16.8 部分继续完成，FL-16 父项仍为 **7/10**；生产 v2/scanner 默认关闭，pre-FO drain 与禁止混合旧写入方的边界保持不变。

SQLite 028 已部署草稿兼容修复（2026-09-16）：

- 用户启动报错对应本地已执行的 `028_fixed_loop_execution_plan`，其 checksum 为 `7b918552e72f1a1c89049ed10ca6bd2f24952f81bed339c5005955d9758d7a58`。此前合并为 `fixed_loop_runtime` 改变 version/name checksum；仅因未提交就假定可重建数据库不成立。本次修正该兼容缺口，028 SQL 文件和历史记录均不改写。
- 新增 SQLite 032（029～031 曾用于拆分草稿，不复用）：只接受旧 028 的准确名称/dialect/checksum，且三列 execution plan 必须已经是 nullable TEXT；先复用冻结 028 的逐列 guarded DDL，再补早期 checkpoint 表缺失的列。所有步骤成功后记录 032，失败可重试；未知校验值和不符 schema 仍拒绝。当前完整库 / 新库不重复 ADD COLUMN。
- 新增旧记录/快照保留、错误名称/dialect/checksum/字段拒绝、八个 checkpoint 补列边界重试、DDL 失败不记成功等回归；旧版本校验和 fresh/027→当前升级继续通过。迁移 34 项、admin/Store 131 项通过；6 项真实 MySQL 测试按默认 ignored，本轮仅改 SQLite，没有重跑真实 MySQL。报告：`/tmp/bcs-sqlite-028-upgrade-tests.log`、`/tmp/bcs-sqlite-032-admin-store.log`；bcs/admin 构建记录：`/tmp/bcs-sqlite-032-build.log`。
- 本地 SQLite 使用 backup API 创建一致性备份，在副本通过 check/apply/check 和逐表原值比对后，应用到原库；原有 53 张表、484 行原值（包括所有旧迁移 metadata）一致，`PRAGMA integrity_check=ok`，版本 32、pending 0。未启动服务或触发工作流；运行时开关未修改。
- 更新升级指南、README、bootstrap CONTEXT 和 AGENTS：保留数据库中已执行的迁移即使尚未 Git 提交也冻结；后续结构变化必须用新版本。此修复不推进 FL-15/16 计数，不替代发布门禁。当时的草稿升级方案现已撤回，当前迁移流程见 [迁移 README](../../../migrations/README.md#sqlite)。

内置 Loop 自定义协作模板（2026-09-16）：

- 新增 `write-review-loop` 中英文模板及 registry 项，展示名称“三轮写作评审（Loop）”，提供必选 `writer` / `reviewer` 角色和“循环”筛选标签。写作→评审固定三轮，随后进入唯一 `final_output` 汇总节点；不依赖自动 Judge，`break_outcomes: []`，末轮通过 `exhausted` 路由继续汇总。
- 评审输出要求保留完整稿件与修改意见，保证上一轮结果能作为下一轮输入。模板与 README 明确当前仅可校验/预览，v2 创建与执行门禁仍关闭；本次没有启用运行时功能、修改 schema 或提升 FL-15/16 完成计数。
- 模板服务 10 项、admin 26 项、定义校验 34 项、Loop 编译 20 项通过，共 90 项；admin 的 5 项真实 MySQL 迁移测试按默认 ignored，本次没有 migration 变更。新增验证覆盖中英文列表/详情、标签筛选、seed 内容保留，以及不依赖 Judge 的七节点展开、跨轮结果关联和第三轮到最终汇总路由。报告：`/tmp/bcs-loop-template-catalog-tests.log`、`/tmp/bcs-loop-template-validation-tests.log`。
- `bcs` / `bcs-admin` 二进制重建通过；seed dry-run 成功读取 8 个模板、16 份本地化内容。local file mode 重启 BCS 后刷新模板列表即可看到新模板；DB catalog 按既有 seed 流程部署。构建报告：`/tmp/bcs-loop-template-build.log`。

### 2026-09-17：FL-15.10/15.11 与 FL-16.8

- 终态清理接入既有 scanner 的第四个独立游标；follower 不扫，demotion/shutdown 取消页，错误仅影响对应游标。
  单次最多扫描 32 个 Run，分别退休至多 32 条 checkpoint 和清除至多 32 个节点内部 phase/lease；每条更新重查 Run 终态。
  已确认派发/结果、payload、token 和业务审计保持原值，opening/startup-failure/terminal-IM 不在本清理范围。
  历史终态 Run 包括已清理的记录会随游标遍历，不宣称是归档或空间回收；大 Run 在后续轮转继续清理。
- 人工通知已有 request 从保存文本/目标/期限恢复。notification_pending 明确未发送，Notifying 为发送中或未知；
  无外部幂等保证时未知不重发，原 deadline 不延长。缺 request 才从原 snapshot/Node 构造同一 logical interaction，
  新写入方必须先落 request 与发送标记再调用 Provider。请求级 CAS 阻止多个发送者，ACK 前复核当前节点/Session/deadline。
  Run/Node 预检不是跨 Store 事务，最后预检后与外部 IO 仍可能竞争，已开始的发送不能撤回；不宣称 exactly-once。
- 排队恢复会关闭已失效或原期限已过的 request 并释放 scope；每次推进至多 32 项。文件 Store 原子替换成功后才
  发布内存变更，持久化错误不会在重试时伪装成 Active/Cancelled 成功；文件模式仍为单进程，SQL 用于多实例。
- Runtime/Collaboration Store/Channel/Channel Store/Service API/Test Support 最终回归 **691 passed，0 failed**；
  2 项数据库用例默认 ignored 后在一次性 MySQL 8.4 Text/Prepared 下分别显式通过，另有 1 项既有 doctest ignored。
  SQLite 跨进程覆盖人工请求未落库、待发送、发送未知、ACK 写失败、已送达、已过期六窗口；恢复进程包含两个竞争实例。
  终态清理覆盖写入中断/续做、并发、重启和业务事实保留；scanner 专项 **6 passed**。
- workspace all-targets、port purity、forbidden symbols、Store boundaries 和 diff whitespace 检查通过。
  本次没有新增或改写 migration；API/Event/页面 payload 不变，仅扩充内部恢复合同与 request status。
- 证据：`/tmp/bcs-fl1511-final-regression.log`、`/tmp/bcs-fl1510-mysql.log`、`/tmp/bcs-fl1511-mysql.log`、
  `/tmp/bcs-fl1511-restart.log`、`/tmp/bcs-fl1510-scanner.log`、`/tmp/bcs-fl1511-workspace.log` 及三份架构日志。
- FL-15 为 **11/11**；FL-16 为 **8/10**，剩真实产品多实例/强杀与完整 Singlebox/FO。新旧 Channel 写入方不能混跑；
  生产 v2 与实验 scanner 仍默认关闭。本次受控测试使用本地 Provider 替身，未验证真实 IM Provider/OceanBase 或生产升级。

- 全量门禁补充：OpenAPI/Event **125 passed、5 failed**；使用 HEAD 的纯文件快照单独运行五个失败用例，
  **5/5 复现**，证明是当前提交已有的合同清单/字段约束漂移。没有修改测试期望来掩盖失败。
  `/tmp/bcs-fl24-contracts-local.log` 与 `/tmp/bcs-fl24-head-contracts.log` 保留对比证据。
- 全工作区门禁已发现 CLI 的 `test_chat_async_returns_transport_error_on_non_listening_port`：固定
  `127.0.0.1:1` 返回 HTTP 500，而用例期待 Transport 错误；该处尚未改动，不把网络原因当作已证实结论。
  因此 FL-24/FL-29 仍未通过。恢复代码验收与发布门禁分开记录。
- 单独修正既有 Judge timeout fixture 的 1ms 启动竞态：给节点/判断 1000ms，测试 Judge 延迟 1500ms，
  仍断言原 timeout 错误和 Run Failed。避免在高并发负载下尚未派发 Bot 就耗尽期限，未改变运行时代码或降低断言。

- 全工作区单次门禁最终为 **5032 passed、1 failed、55 skipped**，唯一失败为上文 CLI 网络用例；报告
  `/tmp/bcs-fl1511-fast-fail.log`。这是本轮后段接线/队首查询补充前启动的全量快照；后段改动由最终模块回归和
  单独接线/超时 fixture 测试复验，不把较早全量结果当作最终版本的完整通过证明。

- 后段最终复验：`/tmp/bcs-fl1511-final-workspace.log` 的 workspace all-targets 通过；
  `/tmp/bcs-fl1511-wiring-final.log` 的 deferred Channel 装配测试通过（未装配为空、已装配转发 covered node IDs、错误透传）；
  `/tmp/bcs-fl1511-timeout-fixture-final.log` 的 Judge timeout fixture 通过。
  新增队首查询在 `/tmp/bcs-fl1511-mysql-final.log` 的真实 MySQL Text/Prepared 复验通过；临时 MySQL 容器已删除。
  终态 Run 占用的人工通知 scope 可由其他等待 Run 主动清理；不依赖终态 Run 再进入 active scanner。


### 2026-09-17：按用户要求开放 Loop 效果测试（已完成）

- 增加 `collaboration.experimental_fixed_loop_execution`（默认 false），local 配置显式 true；三处 Runtime 装配、validation 与全部执行入口使用同一能力。开启后不再返回阻止页面创建的 `VALIDATION_ONLY_FEATURE`，同时开启通用恢复扫描。
- 中英文“三轮写作评审（Loop）”模板说明、配置示例与 API 文档同步更新；不新增 migration，不改变历史数据库记录。
- 本次是用户明确要求的实验开放，不提前勾选 FL-16.9/16.10 或 FL-24～30 的生产验收。
- HTTP 集成验证默认关闭时保留 warning、拒绝创建；开启时用真实模板创建群，经两个本地模拟 Provider Bot 完成三轮与汇总共 7 个节点，确认下一轮包含上一轮评审产物，7 个 delivery ID 不重复，Run 最终 Completed，6 个 body 节点有执行 metadata。OpenAPI 挂载测试验证页面所用校验接口的相同开关行为。2 项均通过；报告 `/tmp/bcs-loop-enable-http.log`、`/tmp/bcs-loop-enable-openapi-http.log`。
- 修复 random-port 测试服务器提前释放 progression task 的生命周期问题，使恢复扫描随服务器持续运行并在服务器退出时取消。配置专项 2 项、独立 recovery 开关及 scanner 专项 7 项通过；Runtime/模板回归 255 项通过，0 失败/忽略；preview OpenAPI schema 11 项通过；singlebox 配置脚本、workspace all-targets check、三个架构检查和 `git diff --check` 通过。报告 `/tmp/bcs-loop-enable-config.log`、`/tmp/bcs-loop-enable-progression.log`、`/tmp/bcs-loop-enable-runtime-final.log`、`/tmp/bcs-loop-enable-openapi.log`、`/tmp/bcs-loop-enable-singlebox-config.log`、`/tmp/bcs-loop-enable-check.log`。首次 Runtime 回归的既有 timeout 用例出现一次计时失败，未修改实现或断言，单项复验及完整相关模块复验均通过；初次 HTTP 测试的空 warnings 被 serde 省略，断言已按合同接受字段省略。
- 已构建 `bcs` 并重启当前工作区本机 21000 服务，复用保留的 SQLite 数据库和已有迁移记录。启动脚本在当前 shell 的 C.UTF-8 locale 下失败，使用 `LC_ALL=C LC_CTYPE=C LANG=C` 后启动成功。在线健康检查显示 2026-09-17 构建，模板 validation 为 valid=true、warnings=[]、7 个节点，catalog 已加载新说明；运行配置和 local 模板均开启，后续 singlebox 重启不会恢复关闭。报告 `/tmp/bcs-loop-enable-build.log`、`/tmp/bcs-loop-enable-restart.log`、`/tmp/bcs-loop-enable-live.log`。
- 本轮没有运行完整 workspace test / Singlebox / 多实例强杀 / 真实模型效果测试。用户现在可在页面选择“三轮写作评审（Loop）”、绑定实际 writer/reviewer 后试运行；真实模型输出质量由此次效果测试确认。


### 2026-09-17：逻辑 Loop 预览与副屏（已完成）

方案已获用户确认；在原 FL-20/FL-21 上追加 UX 验收，不重写历史验收记录。
- [x] FL-20.1/21.1：共享 loops 展示合同、snapshot 投影、OpenAPI 与兼容 fixtures。
- [x] FL-21.2：默认逻辑循环预览、容器/回边/出口、展开图切换及角色选点。
- [x] FL-20.2：副屏循环视图、当前轮跟随/历史轮次选择、状态/详情/人工精确定位。
- [x] FL-20.3/21.3：单轮/提前退出/并行/多 Loop/v1 回归，实际浏览器宽窄屏验收与本地资源部署。

执行范围：展示合同和消费者；不增加 migration，不修改执行 DAG、调度或外部通知语义。

- Service API/OpenAPI 新增可选 `loops` map，v1 省略；Run 从保存的 authoring snapshot 投影，历史 opaque execution ID、原 nodes/edges 不变。两个 HTTP adapter 均断言 descriptors 透传。
- 预览默认容器内只画一份 body；保留分支/汇合、多 Loop、角色选点，提供展开图。副屏按 execution metadata 选择真实轮次，历史选择在刷新时固定；详情和人工回复始终使用对应 execution ID。单轮不画回边，空 break 不画提前退出出口；耗尽、重试、取消有对应提示。
- 验证：Rust compiler/runtime/service 合同 **204 passed**，相关 HTTP **70 passed**，补充 descriptors 断言后 **3 passed**；OpenAPI preview/run **32 passed**；Frontend GroupChat **58 passed**。Panel typecheck/build/UMD、integration（含共享面板、Loop 历史选择/当前轮、首轮与后续人工回复）通过。报告 `/tmp/bcs-loop-view-rust.log`、`/tmp/bcs-loop-view-http.log`、`/tmp/bcs-loop-view-http-final.log`、`/tmp/bcs-loop-view-openapi.log`、`/tmp/bcs-loop-view-frontend-all.log`、`/tmp/bcs-loop-view-panel-tests.log`。
- Frontend CI 与独立生产预览构建通过；完整 TS5 检查仍有工作区已有的 API、消息类型、旧 graph test 和 React DOM 声明错误，本次生产展示组件无类型报错。Workspace all-targets check 与 `git diff --check` 通过。报告 `/tmp/bcs-loop-view-frontend-ci.log`、`/tmp/bcs-loop-view-preview-build.log`、`/tmp/bcs-loop-view-frontend-types.log`、`/tmp/bcs-loop-view-check.log`。
- 架构专项：port purity 通过；依赖检查仍报告 protocol→domain、service-api→config/storage 的已有依赖（对应 Cargo.toml 未修改）；import rules 仍命中本轮范围外的现有 application/core/port 路径，未放宽规则或改动这些导入。报告 `/tmp/bcs-loop-view-arch-deps.log`、`/tmp/bcs-loop-view-arch-imports.log`、`/tmp/bcs-loop-view-arch-ports.log`。
- 实际浏览器验证 1280px/390px：循环体、回边和出口完整可见；预览选点/展开切换、普通 v1，以及副屏历史轮次和提前退出均正常。使用 `fixed_loop_logical_view.json` 复现两节点写作评审 Loop；不创建群或提交真实人工回复。
- 已构建并重启本地 21000 BCS，保留原 SQLite 数据库。在线模板校验返回一份 Loop 描述、2 个逻辑 body 节点与 7 个展开节点；已有 1 个 Loop Run 的 graph 也返回 snapshot descriptors。在线 UMD 与本次构建逐字节一致。报告 `/tmp/bcs-loop-view-build.log`、`/tmp/bcs-loop-view-restart.log`、`/tmp/bcs-loop-view-live.log`。
- 本轮未运行完整 workspace test、Singlebox 全栈或真实模型效果测试；此 UX 验收不改变 FL-24～30 等生产门禁状态。

### 2026-09-17：Loop 视觉层级与主线对齐（已完成）

- [x] 根据截图反馈，将回边移出可见容器；循环体与后续节点等宽、沿同一中轴排列，收紧纵向间距。左右为回边保留相同布局空间，保持并行 Loop 之间不重叠。
- [x] 预览和副屏同步使用细灰边、近透明背景和单行小标题；移除重复的大号 Loop 标题/状态，工具栏改为中性色，回边标签分两行显示。
- [x] 现有布局/集成用例补充主线对齐断言；GroupChat **58 passed**，Panel typecheck/build/integration/UMD、Frontend CI 和预览构建通过。浏览器验证 1280px/390px 的两节点循环及单节点提前退出，无回边裁切。报告 `/tmp/bcs-loop-balance-frontend-tests.log`、`/tmp/bcs-loop-balance-panel-tests.log`、`/tmp/bcs-loop-balance-frontend-ci.log`。
- [x] 本地 BCS 重启加载新面板；在线 UMD 与构建产物逐字节一致，已有 Loop Run 图接口正常。报告 `/tmp/bcs-loop-balance-restart.log`、`/tmp/bcs-loop-balance-live.log`。本次仅调整展示，不修改 API、状态机或数据库。

### 2026-09-17：上限作为兜底，主编评审决定退出（已完成）

- [x] 预览/副屏主要显示“第 N 轮”，移除轮次分母和常驻的“最多 N 轮”；兜底上限可从悬浮说明或折叠的循环限制查看。回边仅显示“继续循环”，正常退出与上限兜底分别标识。
- [x] 中英文 `write-review-loop` 改名为“写作评审循环（Loop）”/“Writing Review Loop”。主编保留完整稿件并给出通过/返修结论，Judge 选择 `approved` 退出或 `revise` 继续；上限保持 3，仅在持续返修时兜底。最终节点不得把兜底退出宣称为评审通过。
- [x] 同步模板 Judge 标签、说明、展示 fixture 和 seed/编译测试；逻辑折叠后的同目标出口重新分配标签位置，避免通过与兜底文案重叠。
- [x] 验证：模板服务 10 项、compiler 20 项、seed loader 1 项、GroupChat 58 项通过；Panel typecheck/build/Loop integration/UMD 和预览构建通过。HTTP 集成覆盖禁用时拒绝、首轮通过、第二轮通过并跳过剩余轮次、连续返修到上限兜底；OpenAPI 校验挂载用例通过。日志：`/tmp/bcs-loop-exit-catalog.log`、`/tmp/bcs-loop-exit-frontend.log`、`/tmp/bcs-loop-exit-provider.log`、`/tmp/bcs-loop-exit-openapi.log`。
- [x] 浏览器验证默认隐藏具体上限、主编/Judge 节点、独立退出线路及副屏当前轮次。已构建并重启本地 21000 BCS，保留原 SQLite；在线 UMD 与构建一致，新模板校验 valid=true、warnings=[]、break_outcomes=[approved]。当前 singlebox 已配置 Judge；仓库默认 `llm.type=none` 的其他部署仍需自行配置。
- 既有群/Run 使用保存的定义和 snapshot，不改写历史流程。完整 workspace、Singlebox 全栈和真实模型评审质量未在本次复验；不改变其他生产门禁状态。

### 2026-09-17：精简 Loop 标记、仅展示实际执行、拆分出口处理（已完成）

- [x] 连线仅显示 `continue` 或逻辑 outcome（例如 `approved`、`exhausted`），移除附加的退出/上限说明。节点只显示任务名，外框标识 Loop；运行历史用 `#N` 区分执行，`max_iterations` 仅保留在悬浮或折叠信息中。
- [x] 定义预览只显示一份逻辑循环体，不再提供未来执行计划展开。副屏选择器和展开图只包含实际进入的执行；尚未执行时显示一份结构占位，不提供历史选择。批量 skipped 的未来节点即使有 completed_at 也不会进入历史，已开始后取消的执行仍保留。
- [x] 展开图按 Loop/iteration 外框分组，过滤尚未进入的循环外分支，防止未来节点被隐藏后产生悬空根节点。循环视图保留完整分支；历史固定选择、刷新跟随、节点详情和人工回复仍使用真实 execution ID。
- [x] 中英文模板新增独立必选角色 `polisher`，`approved → polish → finalize`、`exhausted → rewrite → finalize`。重写输出保留未通过与未解决问题的说明，汇总只消费实际执行的分支。
- [x] 验证：GroupChat **59 passed**；模板服务 **10 passed**、compiler **20 passed**、seed loader **1 passed**；Panel typecheck/build/Loop integration/UMD、前端预览构建通过。回归包含上限 100 实际进入 0/5/10 次、提前结束、取消零时间戳、重试、历史选点、单次循环和 v1。
- [x] HTTP 集成覆盖关闭时拒绝、首次 approved、第二次 approved、持续 revise 至 exhausted，验证独立角色调度、每步上游产物、互斥分支状态及最终 Completed；OpenAPI 挂载校验通过。日志：`/tmp/bcs-loop-branches-provider.log`、`/tmp/bcs-loop-branches-openapi.log`。
- [x] 浏览器确认预览的两分支汇总、纯 outcome 标签、无节点次数标记，以及副屏仅展示实际历史。已重启本地 21000 BCS，健康检查 200，在线 UMD 与构建一致；中英文模板与源码一致，均校验通过，9 个执行节点、approved 指向 polish、exhausted 指向 rewrite。已有群的定义快照不变，新建群可选更新模板。重启日志：`/tmp/bcs-loop-branches-restart.log`。

本次未运行完整 workspace/Singlebox 或真实模型质量测试；不改变 FL-24～30 等生产验收状态。


### 2026-09-17：未完成任务核对

- 当前主清单 **22/30** 完成，未完成 **8** 个顶层任务：FL-16、FL-24～FL-30。FL-16 已完成 8/10 子项；FL-16.9 由 FL-26 验收，FL-16.10 由 FL-29 验收，不重复计算工作量。
- FL-24：本次使用现有 Backend Python 环境重跑 OpenAPI/Event，**126 passed、5 failed**。失败项与此前 HEAD 对照记录相同；日志 `/tmp/bcs-loop-task-audit-contracts.log`。Root `.venv` 不含 pytest，未安装依赖或改动测试断言。
- FL-25：已有模拟 Provider/Judge 的真实 HTTP 模板执行测试，覆盖首次/第二次 approved 和 exhausted，但还未补齐三条产品 live story 的 Node/Event、消息历史、Graph、人工上下文及 direct_assignee 通知联合断言，也未完整接入 Singlebox。
- FL-26 / FL-16.9：现有 SQLite 恢复测试以独立 prepare/recover 测试进程和服务替身运行；不能替代真实 BCS 多实例、leader 切换、发送结果未知和进程强杀的产品验收。
- FL-27：资源边界校验已有测试，但尚无登记的 v1/v2 编译、启动、推进、数据库写入/事务、恢复批次及峰值内存对比报告；配置初值不能作为性能验收结论。
- FL-28：本地 MySQL/SQLite 迁移与 snapshot 合同已验证；仍需核对历史拆分 016 部署、目标数据库升级，以及混合版本准入、active v2 Run 处置和回滚演练。
- FL-29 / FL-16.10：canonical reports 目录当前为空，未形成完整 Singlebox 覆盖率及 verifier 证据。最近已登记的 workspace 门禁有 1 项 CLI 非监听端口用例失败；该用例仍硬编码 `127.0.0.1:1`，本次未重跑全工作区，保留未验收状态。
- FL-30：待上述证据闭合后更新 spec 验收、部署说明和 commit/PR；当前仍为本地实验执行已开放，生产默认关闭。
- 已同步 FL-20/21 正文到最新 Loop 展示行为，保留历史执行记录。未勾选任何尚缺完整证据的任务；本次未修改运行时代码、启动全量 Singlebox 或操作生产数据库。

建议接续顺序：先处理 FL-24 的合同差异并补 FL-25 三条 live story；随后完成 FL-27 性能测量和 FL-26 真实故障恢复，再执行 FL-28 升级/回滚、FL-29 完整门禁，最后关闭 FL-30。

### 2026-09-17：FL-24 合同回归与 FL-25 live story（已完成）

- 负责人：Codex。FL-24/25 均已完成，代码仍未提交；主清单更新为 **24/30**。
- 按已提交的邀请码 spec、OpenAPI 以及挂载路由，更新陈旧的公开操作/标签、安全边界、Internal 操作和 Group PATCH 字段清单。保留精确集合断言；匿名例外限定在 claim 路径。OpenAPI/Event **131 passed**，日志 `/tmp/bcs-fl24-contracts-fixed.log`。
- CLI 连接失败用例改用绑定但不监听的临时端口，并仅在该测试禁用环境代理，避免固定端口 1 或代理响应影响错误分类。专项 **1 passed**，日志 `/tmp/bcs-fl24-cli-transport.log`；随后完整 fast-fail workspace 中也通过。
- FL-25 继续复用真实 BCS HTTP 启动/Provider 回调设施，补齐 Event webhook、消息历史、Graph 和 HumanInput 联合断言。通知设施通过现有 ChannelProvider 插件注册接口仅链接到测试程序，不向生产二进制添加测试接口。
- FL-25 已完成：`cargo test --manifest-path src/bcs/Cargo.toml -p bcs --test provider_downlink_integration fixed_loop_live:: -- --nocapture` **3 passed、0 failed、0 ignored**，日志 `/tmp/bcs-fl25-live.log`。Story A 两节点 body 最多 3 次、第二次 approved、未来 2 节点 Skipped；Story B 两次执行后在人工节点等待、Run Running、实际 complete 与 exhausted 路由分离；Story C query/直接通知共享上一 result outcome/output，旧 response ref 的 execution 回复 409，当前回复后继续并完成。
- 三条均通过真实 HTTP 建群、绑定渠道、订阅 Event、启动/查询/回复和 Provider callback；通过 HTTP 接收实际 Event/IM 投递。测试使用隔离内存 Store/临时 Bot 目录和明确的本地身份，先 `start_initial_run: false` 再绑定渠道/订阅后启动，避免自动启动早于前置设置。首轮夹具问题（启动顺序、用户归属、随机端口策略、Provider Session 字段、Event JSON content）按既有合同修正，没有放宽生产权限或修改运行时。
- Panel 完整 `verify`、Frontend CI、全量 Jest **72 passed / 14 suites** 通过；port purity、forbidden symbols、Store boundaries 和 `git diff --check` 通过。日志 `/tmp/bcs-fl24-panel.log`、`/tmp/bcs-fl24-frontend.log`、`/tmp/bcs-fl24-frontend-tests.log`、`/tmp/bcs-fl24-{port-purity,forbidden-symbols,store-boundaries}.log`。当前工作仍未提交，无新增 migration。
- 首次 workspace 单次门禁为 **5037 passed、1 failed、55 skipped**：CLI 非监听端口用例已通过；唯一失败是既有 `matching_bot_terminal_dispatches_callback_once` 在 2 秒 accept 窗口超时。该项验证最终回调和去重而非 2 秒 SLA，客户端会在异步任务内同步初始化平台 TLS。测试 accept 等待改为有界 15 秒；后续读取超时、身份/内容断言和第二次回调拒绝断言保持原样，生产代码不变。修正后专项及完整单次门禁均通过，首次失败记录保留。
- 回调专项 **1 passed**（`/tmp/bcs-fl24-admin-callback.log`）；修正后 `bash src/bcs/scripts/ci_test.sh --fast-fail` **5041 passed、0 failed、55 skipped，exit 0**，含新增三条 live story。最终日志 `/tmp/bcs-fl24-workspace-final.log`，首次失败日志 `/tmp/bcs-fl24-workspace.log`。默认 skipped 未计为通过。
- 在本次独立创建的 MySQL 8.4 容器中，为每项测试分配独立空数据库，显式运行默认 ignored 的六项合同，**6 passed、0 failed、0 ignored**：001～028 完整链/重复 apply/历史 checksum 与数据保留、Eventing、人工输入索引、合并 016、Loop 增量、Loop Store。最后一项同时覆盖 Text/Prepared 的 snapshot、rerun、publication、恢复 checkpoint 与消息幂等。没有连接现有数据库；测试后已删除本次容器及其卷。日志 `/tmp/bcs-fl24-mysql-{chain,eventing,human-index,merged-016,loop-migration,loop-store}.log`。
- FL-24/25 验收完成；FL-16.9/16.10、FL-26～FL-30 保持未勾选。本轮未执行真实产品多实例/进程强杀 FO、性能测量、生产/OceanBase 升级或完整 Singlebox 覆盖门禁，未改变生产准入或本地实验配置。下一步先补 FL-27 性能报告与 FL-26 真实恢复证据，再推进 FL-28～FL-30。

本轮主要验证入口（真实 MySQL 命令要求 `BCS_TEST_MYSQL_URL` 指向测试专属空数据库）：

```bash
bash src/bcs/scripts/ci_test.sh --fast-fail
src/backend/.venv/bin/python -m pytest src/bcs/tests/openapi src/bcs/tests/event_contract -q
cargo test --manifest-path src/bcs/Cargo.toml -p bcs --test provider_downlink_integration fixed_loop_live:: -- --nocapture
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-admin full_mysql_migration_chain_applies_and_preserves_history -- --ignored
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-admin eventing_mysql_migration_applies_to_real_mysql -- --ignored
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-admin human_input_index_migrations_apply_to_real_mysql -- --ignored
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-admin merged_016_migration_applies_to_real_mysql -- --ignored
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-admin fixed_loop_migration_applies_to_real_mysql -- --ignored
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-collaboration-store --test mysql_store real_mysql_fixed_loop_snapshot_and_rerun_contract -- --ignored
npm --prefix src/bcs/assets/panel run verify
bash src/frontend/scripts/ci_test.sh
npm --prefix src/frontend test -- --runInBand
```

### 2026-09-17：Skill Loop schema 与多个 Loop 模板（已完成）

- [x] 核对 `bcs-coordination` 已有 Loop schema，修正仅支持 v1/仅预览的旧说明；在 Skill 入口增加 Loop 指引，补充多个同级 Loop、独立 previous_result、外层 target 与 outcome/transition 的区别。嵌套 Loop 仍不支持，执行仍受服务端能力开关控制。
- [x] 新增并登记中英文 `research-writing-loops` 模板：资料核验 Loop 通过后进入写作 Loop，两个阶段分别由主编和 Judge 决定 approved/revise；资料 exhausted 输出缺口报告，写作 exhausted 走 rewrite，写作 approved 走独立角色 polish，最终汇总保留实际评审状态。
- [x] 编译回归验证两个 body 可重复使用 review ID、各自继续路径和独立出口；运行时回归覆盖两个 Loop 均中途通过、资料 exhausted 跳过整个写作 Loop、写作 exhausted 三条路径，验证上下文、分支跳过和最终 Completed。
- [x] 定向验证 **34 passed**：模板目录 11、compiler 21、运行时场景测试 1（含三条路径）、seed loader 1。日志 `/tmp/bcs-multi-loop-{catalog,compiler,runtime,seed}.log`。Skill 校验、中英文结构一致性、修改源码行数限制和 `git diff --check` 通过；模板服务原内联测试移至 `src/tests.rs`，生产实现不变。
- 当前改动未提交、未重启本地 BCS；local file catalog 缓存需重启后加载新模板，DB catalog 仍按 seed 流程更新。本次不新增 migration，不改变主清单 **24/30** 或生产验收状态。
- 上一轮 rebase 后的全 workspace 回归在新增模板前主动中断，避免旧编译测试读取修改中的目录；该次 **688 passed、11 项被 SIGINT 中断、4552 项未执行、55 skipped**，不记为完整通过。日志 `/tmp/bcs-loop-rebase-workspace.log`。本次仅完成上述定向验证，未重跑全 workspace、Singlebox 或真实模型质量验收。

### 2026-09-17：连线展示名称、模板与副屏（已完成）

- [x] 按用户确认方案增加可选 `transitions.<outcome>.display_name` 和 `loop.continue_display_name`；exhausted 使用外层对应 transition 的 `display_name`。不填写时保持原标签；填写时必须为非空白字符串。名称仅用于展示，真实 outcome、target、execution ID 和路由语义保持不变。
- [x] Domain、编译计划、Preview/Run Graph、两个 HTTP adapter 和 OpenAPI 同步透传名称；已有未命名计划的 hash golden 保持通过。Run 使用保存的 snapshot，rerun 继承来源 snapshot，不读取后续定义版本的新名称。
- [x] 前端预览和副屏同步显示普通连线、continue、break、exhausted 的名称，悬浮保留真实 outcome。浏览器验证 1280px/390px；修复副屏窄屏展开图裁切，只展示实际进入的执行，历史选择和执行状态不变。
- [x] 完善中英文单 Loop `write-review-loop` 和双 Loop `research-writing-loops` 模板，为提交评审、继续修订、评审通过、重写及汇总设置对应名称；同步 schema、模板说明、spec 和面板文档。
- [x] Rust runtime **254 passed**，Domain/Service API/模板目录 **250 passed**，v1 HTTP **14 passed**、兼容 HTTP **41 passed**、seed loader **1 passed**；OpenAPI/Event **150 passed**，Frontend Jest **74 passed / 15 suites**。Panel 完整 verify、Frontend CI、预览生产构建、Skill 校验、port purity、Store boundaries、forbidden symbols 和 `git diff --check` 通过。日志 `/tmp/bcs-edge-label-{runtime-all,contracts,http-v1,http-legacy,seeds,openapi,frontend-all,panel-verify,frontend-ci,preview-build,skill,port-purity,store-boundaries,forbidden-symbols}.log`。
- [x] 构建并重启本地 21000 BCS，保留原 SQLite。在线中英文两套模板与源码一致，四份定义均 valid=true、warnings=[]；单 Loop 11 条、双 Loop 21 条展开边带名称，exhausted 边仍保留实际 `revise` outcome。在线副屏 UMD 与已验证构建逐字节一致，刷新后可新建协作测试。没有创建测试群或启动真实 Run。日志 `/tmp/bcs-edge-label-{build,restart,live}.log`。
- 源码行数已检查：新增文件均小于 1,000 行；五个原有超长文件仅作必要字段传递/展示调整，仍超过限制（Panel Run View 4,275、runtime 6,689、definition validation tests 1,257、runtime progression tests 5,802、BcnController 1,913）。报告 `/tmp/bcs-edge-label-source-lines.json`。本次未扩大到无关运行时和页面拆分；仓库未找到可用的行数 CI allowlist，不将此项记为通过。后续需分别按图展示、运行时职责、测试场景和 API DTO 拆分，并落实行数门禁。
- 本次未运行完整 workspace、Singlebox 覆盖门禁或真实模型质量测试；主清单维持 **24/30**，FL-16.9/16.10、FL-26～FL-30 保持未勾选。改动未提交，无新增 migration；历史协作定义/Run 保持原快照。

### 2026-09-17：副屏角色优先显示名称（已完成）

- [x] Run graph 节点增加可选 `assignee_display_name`，从 Run 保存的 `participants.<binding>.display_name` 投影；缺少或空白时省略，RuntimeActor 不套用参与者名。副屏右下角色标签和节点详情优先显示该名称，否则回退 binding ID；长名称省略，悬浮保留完整名称和 ID。
- [x] 覆盖普通 v1、Loop、历史 Run/rerun 的快照名称、未命名/空白回退与 HumanInput；运行时 **183 passed**，v1/兼容 HTTP **14 + 41 passed**，相关 OpenAPI **48 passed**，Panel 完整 verify 通过。浏览器 1280px/390px 检查中文和长角色名显示，未扩大角色绑定或执行语义。日志 `/tmp/bcs-role-label-{runtime,http-v1,http-legacy,openapi,panel}.log`。
- [x] 同步 Service API/OpenAPI、spec、Panel README；port purity、Store boundaries、forbidden symbols 和 `git diff --check` 通过。源码行数已检查，新增/扩展专项测试均低于 1,000 行；前条登记的既有超长文件拆分仍未完成，本次 runtime/Panel Run View 分别为 6,695/4,276 行。
- 本次未运行完整 workspace/Singlebox，无新增 migration；主清单和生产验收状态不变，改动未提交。
- [x] 已构建并重启本地 21000 BCS，保留数据库。在线核对最近 3 个已有 Run 的 32 个节点，角色名与保存定义一致，包括“资料研究员”“主编”“写作者”“润色编辑”；副屏 UMD 与验证构建逐字节一致。已有 Run 无需重建，刷新即可加载。日志 `/tmp/bcs-role-label-{build,restart,live}.log`。

### 2026-09-20：模板名称修订与 rebase 验证

- [x] 中文写作评审模板名称移除多余的“（Loop）”，名称为“写作评审循环”；模板目录专项 **11 passed**，在线目录和详情名称已核对。修订并入已有功能提交。
- [x] 两个功能提交已 rebase 到 `origin/dev` 的 `b660c2bb0`。消息存储冲突保留 dev 的 `chat_error` 持久化去重及本分支调用方指定消息 ID 的幂等发布；OpenAPI 保留 dev 的 72 个公开操作与新增接口。不修改迁移历史。
- [x] rebase 后 OpenAPI/Event **161 passed**、Frontend Jest **74 passed / 15 suites**、Frontend CI 和 Panel 完整 `verify` 通过；port purity、Store boundaries、forbidden symbols 和差异空白检查通过。日志 `/tmp/bcs-loop-rebase-20260920-{contracts,frontend-tests,frontend-ci,panel,ports,stores,forbidden}.log`。
- [ ] rebase 后消息存储、消息流、协作存储、协作运行时及模板目录的 Rust 定向回归未完成。`cargo nextest run --manifest-path src/bcs/Cargo.toml --profile ci --retries 0 -p bcs-message-store -p bcs-message-flow -p bcs-collaboration-store -p bcs-collaboration-runtime -p bcs-collaboration-template` 编译成功，但多个测试二进制停在 `--list` 枚举超过 5 分钟；单独执行枚举也未返回，尚未开始运行用例，已主动中断（exit 130）。需在合并前重跑，不记为通过。日志 `/tmp/bcs-loop-rebase-20260920-rust.log`。
- 本次未重跑完整 workspace、真实 MySQL、浏览器或 Singlebox 验收。已有源码行数债务仍未关闭，消息存储 `memory.rs` 为 1,023 行；保留原有职责拆分待办。主清单维持 **24/30**，FL-16.9/16.10、FL-26～FL-30 不改变验收状态。PR 描述已区分当前回归、此前验收和剩余发布工作；未推送分支。

### 2026-09-20：迁移整理与 PR #2339 覆盖回归

- [x] 按用户明确要求移除 MySQL `028_human_input_scope_index` 自动删除/重建索引；新库使用 001/008 已有的 prefix 700。README 补充索引超限的检查和人工处理步骤，已有索引及迁移记录不自动改写。用户链接的 SQLite 028 实际是 Loop 运行时结构，保留其 SQL 原文。
- [x] SQLite 的补全迁移从 032 调整到 029，SQL 原文不变；保留已部署 028/032 的记录和数据，旧 032 数据库执行 029 的列/索引存在性检查后只增加新记录。占用 029 的不兼容草案仍拒绝执行，处理方式见迁移指南。
- [x] SQLite migration **37 passed**、bcs-admin migration **21 passed / 5 ignored**；临时 MySQL 8.4 的完整 001–027 链、历史升级及 HumanInput 索引专项 **2 passed**。日志 `/tmp/bcs-migration-cleanup-sqlite.log`、`/tmp/bcs-migration-cleanup-admin.log`、`/tmp/bcs-migration-mysql-{chain,index}.log`。
- [x] 定位原 CI 的两项 E2E 失败：CLI pending 查询的 Human 未加入会话，以及 CLI 默认 20 条与 API 默认 10 条的群列表误比。测试补齐 Human 在场条件，并显式对齐分页；不放宽权限或覆盖阈值。
- [x] 新增真实 HTTP/CLI Loop story：两次 HumanInput、judge revise/approved、旧 execution 回复拒绝、提前退出后未执行节点 Skipped、exhausted 进入 rewrite，以及一次性协作最终发布。联合检查 Run、Graph、节点详情和历史 metadata。
- [x] 新 story 发现并修复普通 Chat 历史将持久化 Loop output 当作字符串读取的问题；现在保留正文、执行 metadata、消息 ID 及 Human/Bot 角色。bcs-message **36 passed**，日志 `/tmp/bcs-pr2339-test/message-tests.log`。按源码 1,000 行限制拆分消息投影、迁移实现和相关测试，既有 SQL 内容不变。
- [x] 独立 SQLite / 5 Bot 测试栈跑完完整 BCS E2E：**666 passed / 0 failed**，行覆盖 **43.06% ≥ 40%**、方法覆盖 **37.63% ≥ 36%**、CLI **54/54**。日志 `/tmp/bcs-pr2339-test/coverage.log`。覆盖 runner 的 `--skip-start` 原先忽略 `BCS_LOG`，端点门禁误读默认路径而使该次汇总命令退出 1；已修正路径解析，并用同一轮实际服务日志重新计算端点门禁，**156/156**，日志 `/tmp/bcs-pr2339-test/endpoint-gate.log`。没有降低阈值、缩小统计分母或绕过检查。
- 本轮未执行 Backend/BaaS 参与的完整 Singlebox、生产故障恢复或性能验收；FL-16.9/16.10、FL-26～FL-30 保持未勾选。改动未提交、未推送；远端 PR 检查需提交后重跑。


### 2026-09-20：PR #2339 评审处理

- [x] 按确认范围保留合并后的 MySQL 016；撤回旧库自动补齐 migration，最新 MySQL 链仍为 001–027。AGENTS 和迁移说明明确本次编号整理/016 合并的授权例外；旧 016 记录继续明确报错，不自动改写历史。
- [x] 修复 HumanInput 队列处理 32 个无效请求后提前返回的问题：每批让出执行时间后继续推进，直到有效占位或队列为空；持久化错误仍向调用者返回，不依赖恢复扫描器。
- [x] 新增连续 65 个已失效 Run 请求、连续 65 次发送失败且失败状态已持久化后的有效通知回归；验证旧请求关闭/失败状态、后续请求 Active、通知只发一次。Channel **98 单元测试 + 14 契约测试通过**，迁移单元测试 **21 passed / 5 ignored**。日志 `/tmp/bcs-pr2339-channel-tests.log`、`/tmp/bcs-pr2339-review-admin-tests.log`。
- [x] 按职责拆分 Channel 超长文件，原测试无遗漏；本次工作区新增/修改源码均不超过 1,000 行，`git diff --check` 通过。
- [x] 临时 MySQL 8.4：最新 001–027 完整迁移链、重复执行与历史记录检查通过；合并 016 建表及两种旧 016 身份拒绝且原记录不变的测试通过。日志 `/tmp/bcs-pr2339-review-mysql-chain.log`、`/tmp/bcs-pr2339-review-mysql-016.log`。验证容器和临时数据卷已清理。
- 本次未提交、未推送、未发布或 resolve GitHub 评论；未重跑完整 workspace、Singlebox 或产品 E2E，主清单验收状态不变。


### 2026-09-20：SQLite Loop 迁移收敛到 028

- [x] 按本次 PR 尚未合并、只保证最新迁移链的确认范围，删除 `029_fixed_loop_legacy_upgrade.sql`、注册项、执行分支及旧草稿专用升级说明；不新增替代 migration。
- [x] 028 的 checkpoint 建表语句已经包含 029 涉及的全部 8 个字段，SQL 无需追加或改写。SQLite 目标版本与迁移总数统一为 28；移除依赖 029 的旧 028 身份放行和草稿升级测试，保留正常名称/dialect/checksum 校验。
- [x] 更新 README、bootstrap CONTEXT 和本文当前部署口径；历史执行记录中的 029/032 方案保留当时含义，已注明撤回。
- [x] SQLite migration **31 passed**、bcs-admin 迁移测试 **21 passed / 5 ignored**（需 MySQL 的专项用例未在本次 SQLite 改动中重复执行）：新建库、027→028、13 个 DDL 步骤的中断重试、成功后记录版本、幂等执行与冲突记录检查通过。命令 `cargo test --manifest-path src/bcs/Cargo.toml -p bcs -p bcs-admin --lib --bin bcs-admin migrat`，日志 `/tmp/bcs-pr2339-sqlite-028-tests.log`。
- 本次不改动运行中的数据库，不提交或推送；未重跑完整 workspace 或产品 E2E，主清单验收状态不变。
