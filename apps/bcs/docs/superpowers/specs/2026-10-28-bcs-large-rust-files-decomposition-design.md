# BCS 超长 Rust 文件按职责拆分设计

> 日期：2026-10-28
> 状态：设计已确认；第 1 单元已随 #3240 合入；第 2 单元计划已生成，已获授权编写第 3 单元实施计划；第 2～5 单元尚未实施。
> 源码基准：`dd316eb35`（`dev`，包含 #3209）。
> 范围：下表五个源文件及完成其拆分所必需的 crate 内私有模块与测试装配。
> 设计交付：本文、第 1 单元 [实施计划](../plans/2026-10-28-bcs-bot-event-contract-decomposition-implementation-plan.md)及第 2 单元 [实施计划](../plans/2026-10-28-bcs-runtime-progression-decomposition-implementation-plan.md)及第 3 单元 [实施计划](../plans/2026-10-28-bcs-cli-entry-decomposition-implementation-plan.md)；本轮只更新规划文档。

## 1. 决策与目标

采用 **保留原入口、公开类型和契约，按职责提取私有子模块** 的方案。
这是一项源码组织重构，不是服务架构、业务策略或数据库模型重设计。

### 1.1 基准清单

物理行数包括注释和空行；仅统计 Git 跟踪的 `.rs` 文件。
下列数字固定描述上述基准，不作为后续提交的实时统计。

| 文件（相对 `apps/bcs/`） | 行数 | 当前主要集中点 |
| --- | ---: | --- |
| `crates/tools/bcs-cli/src/main.rs` | 8,133 | 参数、启动鉴权、3,109 行 `run()`、输出辅助、内联测试 |
| `crates/services/bcs-message-flow/tests/contract_bot_event.rs` | 7,186 | 事件契约、任务与协调场景、多个记录及阻塞 fixture |
| `crates/services/bcs-collaboration-runtime/src/runtime.rs` | 6,783 | Runtime 状态、Service API 实现、执行推进、查询、辅助函数及测试 |
| `crates/services/bcs-collaboration-runtime/tests/runtime_progression.rs` | 5,823 | 运行场景、共享 fake、YAML 工厂、现有恢复测试树装配 |
| `crates/services/bcs-collaboration-store/src/lib.rs` | 5,026 | Memory/SQL 仓储实现、事务、行映射、模板查询、内联测试 |

### 1.2 验收目标

- **G1**：五个原文件及每个新增、修改的 Rust 文件均不超过 **1,000 物理行**。
- **G2**：每个模块具有可描述的单一职责；不按固定行数切成 `part1`、`part2`。
- **G3**：公开 Rust API、CLI 命令与输出、协议、授权、持久化及错误语义不变。
- **G4**：测试不丢失、不变弱、不新增忽略；保留 Cargo 集成测试 target。
- **G5**：不增加 Cargo 编译 warning，不降低架构、覆盖率或文件大小检查。
- **G6**：每个实施单元均能单独编译、测试、审阅和回滚；不依赖未合入的其他单元。

建议模块在约 300～800 行结束；1,000 是硬上限，不是需要填满的配额。
目录表中的职责是设计约束，文件行数必须在实现后实测，不能用估算宣告通过。

### 1.3 非目标

- 不跨 crate 搬迁、不新建 crate、不增删依赖，不修改 Service/Plugin API。
- 不改变配置、环境变量优先级、默认值、CLI 选项、数据库表或已提交迁移。
- 不顺便消除 CLI bin 与 library 中已有的重复实现。
- 不重写状态机、SQL、队列、重试或缓存；不顺便修复已知无关测试失败。
- 不使用 `include!`、生成代码、压缩成超长单行、全局 formatter 或新增 allowlist 规避限制。
- 不清理本次未触及的其他超长文件；必须说明依赖，但不把任务扩大为全仓整治。

## 2. 依据、替代方案与架构边界

### 2.1 依据

以下路径相对仓库根目录：

- `AGENTS.md`、`apps/bcs/AGENTS.md`、`apps/bcs/CLAUDE.md`。
- `CONTEXT-MAP.md`、`docs/agents/domain.md`。
- `docs/arch/arch.rules.md`、`docs/arch/ci.enforce.md`。
- `docs/arch/context-boundary-format.md`、`docs/arch/protocol-contract-tests.md`：
  后两者的具体自动检查面向 Backend，不据此向 BCS 添加 Python 元数据。
- `apps/bcs/crates/tools/bcs-cli/CONTEXT.md`。
- `apps/bcs/crates/services/bcs-message-flow/CONTEXT.md`。
- 现有 `runtime_*.rs`、Collaboration Store 检查点模块及 Runtime 恢复测试模块。

Runtime 和 Collaboration Store 在本基准下没有 crate-local `CONTEXT.md`。
本次不发明领域术语或改变 crate 边界；本文记录内部职责和传播影响，不以缺失文件为实施前提。
`BCS-AUTHORITY-SIZE-2026-10-10` 的例外不适用于本拆分，不申请或扩展 waiver。

### 2.2 已讨论的两种方案

| 方案 | 特点 | 决策 |
| --- | --- | --- |
| A：原入口 + 职责子模块 | 保留类型、状态持有和装配点；提取既有函数体；仅对超长 trait 实现增加必要内部委托 | **采用**，符合已有模式，行为核对最直接 |
| B：多个内部组件组合 | 建立独立执行器、仓储组件、上下文对象，再由原服务组合 | 不采用；会扩大构造、状态所有权和依赖注入调整范围 |

按行数机械分块也不采用：它降低单文件数字，却没有建立职责边界。

### 2.3 不变的调用方向

- CLI 仍是 delivery/tool 边界，只构造请求和解释响应，不引入服务端业务实现。
- Runtime 继续使用现有 Service API、repo 和 port；不因为拆文件新增 transport 依赖。
- Collaboration Store 继续拥有 SQL、缓存/内存布局和 DB plugin 访问。
- 测试 fixture 可以组合真实实现，但不能进入生产路径或变为公共服务接口。

## 3. 通用拆分规则

### 3.1 Rust 模块与可见性

1. 保留公开类型的原定义位置及公开导出路径。
2. 新模块默认私有；跨模块只开放实际需要的项，优先使用 `pub(super)`。
3. 不统一扩大为 `pub(crate)` 或 `pub`，不通过重导出创建新的公共 API。
4. 状态字段仍由原父模块定义，子模块可以访问祖先模块的私有状态。
5. 现有子模块的逻辑父级尽量不变；使用必要的 `#[path]` 保持逻辑模块树，
   不把物理目录变化误当成 Rust 隐私边界等价。
6. 父模块可私有导入提取后的函数，使既有子模块仍能解析原来的共享名称；
   新模块使用明确的导入，不继续扩大无关的通配依赖。

同一类型的 inherent `impl` 可以分散在多个模块；同一 trait 对同一类型的
`impl` **不能**拆成多个部分实现。超长 trait 实现保留一个完整、薄的适配层，
方法委托至职责模块中的 inherent 方法；短小转发保留原状，不为每个方法再套一层。

### 3.2 行为保持与代码核对

- 优先整段移动已有函数，保留签名、属性、注释和函数体。
- 允许的结构性差异仅限模块声明、导入、必要可见性、路径修正和直接内部委托。
- 提取单个超长函数时保持分支顺序、提前返回、借用/所有权、异步调用顺序和错误传播。
- 不因移动增加序列化、clone、spawn、锁、超时、缓存、网络调用或 DB 操作。
- 不批量格式化。导入调整、空行变化仅限拆分必需的位置。
- `tracing` 默认 target 会随模块路径变化：移动相关宏时核对原有效 target，
  保持运维依赖的 target、事件名、字段和 level；不修改过滤配置来适应漂移。
- 栈回溯源码位置、Rust 内部符号及一般测试模块前缀可以变化；不把它们当公开协议。
  下文规定的外部入口和子进程 `--exact` 名称不在允许变化之列。

### 3.3 测试组织

集成测试入口保持原文件和 target 名称，场景成为其子模块，而不是新的独立 binary。
fixture 保持原持有方式和生命周期：不能因提取导致临时目录提前删除、
服务句柄被 drop、记录器不再共享或全局 tracing 被重复初始化。

只拆当前套件实际需要的 fixture；局部 fake 与唯一使用场景相邻，多个场景共用的
fixture 放在该套件自己的 support 中，不扩成新的全仓测试框架。

## 4. CLI：入口、参数、准备流程与命令处理

本节路径相对 `crates/tools/bcs-cli/src/`。
新文件物理存放在 `cli/`，通过父入口的 `#[path = "cli/…"]` 声明为直接子模块，
使模块关系在独立 binary 和被其他模块 `#[path]` 引用时一致。

### 4.1 目标文件与职责

| 文件 | 职责与迁移来源 |
| --- | --- |
| `main.rs` | 保留 `fn main()`、`pub async fn run()`、模块装配、preview 早返回与薄的顶层命令分发 |
| `cli/args.rs` | `Cli`、顶层 `Commands` 及 Clap 属性；不携带执行逻辑 |
| `cli/session_args.rs` | `SessionCommands`、`SessionFileCommands` |
| `cli/collaboration_args.rs` | `CollaborationCommands`，保留既有 Draft 参数类型引用 |
| `cli/resource_args.rs` | ChatRun、GroupContext、Channel、Friend、Visibility、Service 的子命令声明 |
| `cli/environment.rs` | SessionInfo、URL/Token 发现及网络环境探测，沿用原优先级和探测行为 |
| `cli/authentication.rs` | 从 `run()` 提取 OAuth/AgentPass 准备及 client 工厂；保留原分支、错误投影和 return 行为 |
| `cli/output.rs` | StructuredResult、GroupListOutput、discover 输出与认证错误分类 |
| `cli/bot_commands.rs` | Health、Connect、Onboard、List、Get、Discover、UpdateStatus 的分发与请求 |
| `cli/group_commands.rs` | CreateGroup、GetGroup、Fuse、ListGroups、AddMember、GroupStatus、TerminateGroup |
| `cli/chat_commands.rs` | Chat、ChatRun；保留 timeout、detach、response mode 和流式消费 |
| `cli/collaboration_commands.rs` | Draft、Permission、Run、Query、Respond、Validate、Create 的处理 |
| `cli/collaboration_input.rs` | 自定义绑定、participant tags、panel opening、validation 投影与参数校验 |
| `cli/group_context_commands.rs` | GroupContext 分发、query/body、owners、policy、scope 与 timestamp 参数转换 |
| `cli/relation_commands.rs` | Friend、Visibility 处理，保留选定 Actor 和鉴权行为 |
| `cli/channel_commands.rs` | Channel 处理、绑定 payload 与 debug 脱敏 |
| `cli/session_commands.rs` | Session 非文件命令；File 分支委托文件处理模块 |
| `cli/session_file_commands.rs` | 上传、下载、列表、删除、共享、capabilities |
| `cli/service_commands.rs` | Service Invoke/Status/Wait、SID、BAAS meta 合并、等待及摘要渲染 |
| `cli/input.rs` | `parse_json_arg` 等真正共享的输入函数；不收纳其他业务辅助函数 |
| `cli/tests.rs` | 仅测试装配和公用测试辅助；连接下列场景模块 |
| `cli/tests/{arguments,environment,group_context,collaboration,channel,session,service,output}.rs` | 从原内联测试按行为归类，保留所有属性和断言 |

已有 `agentpass.rs`、`collaboration_output.rs`、`collaboration_snapshot.rs` 保留原位置及父级。
CLI library 的 `lib.rs`、`client.rs`、`oauth.rs` 不因消重而扩大修改范围。

第 3 单元规划基准为 `b57b8e2d7`（文档 HEAD `f443d720a`）：main.rs 当前8,049行，
run()含23个顶层命令arm；bin实际发现115项，103项按8个场景迁移，其余12项保留身份。
原 `retired_service_policy_tests` 保持根模块身份，不恢复上游已退休的Service字段。
计划限定29个搬迁源码文件及1个新增入口回归测试文件；完整归属和验证命令见第3单元计划。

### 4.2 执行数据流

保持 `parse -> preview 早返回 -> 日志/调用者验证 -> 配置与鉴权 -> 命令分发` 的顺序。
主分发显式传入已有 URL、cookie、OAuth headers、debug 与输出模式等值。
不引入新全局状态、通用 command registry 或生命周期组件。

命令族可以消费原 `Commands`，在模块内对所属变体匹配；顶层匹配必须覆盖全部变体，
不能通过宽泛兜底将错误命令静默接受。`run()` 中原嵌套 client 工厂提升为普通私有函数，
仍在原调用时刻解析 token/Actor/TRACEPARENT，不改成启动时统一缓存所有命令的凭据。

鉴权提取使用 std `ControlFlow` 区分继续执行与“已输出structured错误并成功退出”，
不能用空headers表示终止。命令族消费唯一选中的原String/Option准备值，不额外clone；
旧 `cli.json` 与 `structured_mode` 是不同语义，拆分时分别传递，不统一默认输出。
Session File复用Session分支已经创建的client，Health/Connect仍保留各自的构造方式。

`debug_request!`、`debug_response!` 保留为入口内部宏，声明在使用它们的子模块之前。
不新增 `#[macro_export]`；不改变 library 已有宏的公开面。
移动的tracing宏通过原入口的私有 `CLI_LOG_TARGET = module_path!()` 保留有效target，
兼容独立binary和内部嵌入宿主；不把target硬编码为独立binary名称。

### 4.3 必须保留的兼容性

- 保留 `pub async fn run() -> Result<()>`，支持内部宿主通过 `#[path]` 引用入口。
- 新模块不得以 `crate::…` 假设入口永远处于 binary 根；使用相对模块路径。
- preview 不触发 URL 解析、网络探测、鉴权、日志目录创建或服务发现。
- Clap 名称、alias、help、默认值、冲突组、debug/release 条件和全局 flag 传播不变。
- 输出结构、退出状态、错误分类、stdout/stderr 分工及 secret 脱敏不变。
- 流式命令不能因提取变成一次性读取或改变等待/取消时机。

验证必须包含外部模块引用的编译 smoke test、debug/release 构建和 mock HTTP/WS 行为。
不在公共测试中依赖公司 SDK；真实内部宿主未验证时明确说明，不能声称覆盖了它。

## 5. Bot Event 契约测试：按行为分组

本节路径相对 `crates/services/bcs-message-flow/tests/`。
保留 `contract_bot_event.rs` 作为唯一原 Cargo target，引用目录 `contract_bot_event/`。

| 场景文件 | 负责的既有行为 |
| --- | --- |
| `publication.rs` | 终态 observer、Workbench 名称、Run fallback、Session 目标及 audience |
| `thinking_stream.rs` | thinking delta 积累、tool 边界重置、非 tool 边界及 Run 隔离 |
| `channel_delivery.rs` | delta/final Channel 输出、sender name、安全失败通知与 fallback |
| `im_reaction.rs` | 排队来源关联、过期、重启防重与每 tick Provider 调用上界 |
| `group_relay.rs` | DM/私有群转发、Provider tags、legacy Session 和 relay limit |
| `task_lifecycle.rs` | TaskDispatch、Run alias、去重、目标身份、ledger 通知和 pending 状态 |
| `coordination_echo.rs` | tool success、cached start、native MCP 映射、解析缓存及失败关闭 |
| `task_routing.rs` | Manager/Worker 角色、Session membership/mode、目标约束、TaskMessage 路由 |
| `task_result.rs` | Worker final 到 Manager 的结果、独立上下文、成功前后持久化与失败重试 |
| `task_stream.rs` | after-last-tool-call、delta/tool 分界、SSE 及参与者 tags |
| `task_completion.rs` | pending/超时 guard、跨 Session 任务、Service Session 完成与群状态 |
| `task_history.rs` | 任务消息落库顺序、错误/终态 segment、去重及目标身份 |
| `history_persistence.rs` | owner/audience、tool start 保留/清除、写失败及非任务 segment 落库 |
| `human_notify.rs` | legacy mention、结构化路由、Sender Route、策略阻断与 Human 通知 |
| `support.rs` | 套件级共享入口；按下列职责装配 fixture |
| `support/{channel,session,messages,task_flow,frames}.rs` | Channel/System 记录器、Session fake、记录/阻塞 repo、任务组合工厂、payload 与断言辅助 |

按场景语义分组而非原文件连续区间；每个函数只有一个拥有者。
例如 TaskResult 的重试写入测试归 `task_result`，通用消息 repo fixture 归 `support/messages`，
不能复制两份记录器或把一个测试拆成互相依赖的多个测试。

`BlockingInterceptor` 被 task dispatch 阻断和 Human 通知策略阻断两个场景共用，
类型与唯一 trait impl 归 `support/task_flow.rs`，不放在任一场景模块内私有。
通过套件级受限可见性和具名重导出供 `task_routing`、`human_notify` 使用，
不复制实现、不扩大为生产 API。

2026-10-28 规划细化：原文件有 108 个测试，包含一个带 `start_paused = true`
的 Tokio 测试；按 14 个场景模块编制一一迁移清单。`task_history.rs` 将任务历史
与通用持久化场景分开，避免重新形成超长测试模块，不增加测试或改变职责范围。

保留既有 `support/session.rs` 与 `crates/test-support/message_flow_contract_support.rs`
的原装配关系。不改变 `BcsMessageFlow` 的生产导出或把套件 fake 移进生产 crate API。

永久兼容用例 `baas_command_output_end_without_name.json` 及对应 identity 测试不改动。
移动 coordination 场景时，保持 cached start、Run、toolCallId、Bot、Session 校验，
不能以补齐 result name 的方式简化 fixture。

## 6. Runtime：保留状态与唯一 Service API 实现

本节路径相对 `crates/services/bcs-collaboration-runtime/src/`。
保留 `pub mod runtime` 和 `runtime::CollaborationRuntime` 原路径。

### 6.1 类型、构造与模块组织

`runtime.rs` 保留 `CollaborationRuntime` 字段、`new`、现有 `with_*` builder、
配置常量与模块装配。保持同一组 Arc、功能开关和可选 port，不建立子组件状态副本。

新模块放 `runtime/`，均作为 `runtime` 的直接子模块；现有 `runtime_*.rs`
通过原 `#[path]` 保持原逻辑父级，避免连带重写它们的 `super::*` 和可见性。

| 新文件 | 职责与关键函数归属 |
| --- | --- |
| `runtime/service.rs` | 唯一完整 `impl CollaborationRuntimeService for CollaborationRuntime`；保留签名、`#[async_trait]` 与短适配逻辑 |
| `runtime/definition.rs` | resolve/load definition、execution compile、Group 定义 get/patch/upgrade/configure 和绑定兼容校验 |
| `runtime/authoring.rs` | YAML 解析、source YAML 投影、identity 拒绝、默认字段清理及规范化 |
| `runtime/bindings.rs` | participant slot/role/bot-id 解析、runtime binding 校验及 session permission 投影 |
| `runtime/access.rs` | Human/Run access、当前 Session permission、pending Human Node 查询与访问检查 |
| `runtime/start.rs` | `start_state_machine_run` 主体、run/node 构建、初次准备与恢复必要事实 |
| `runtime/rerun.rs` | Session one-shot 启动、rerun、物化 rerun 恢复及 node 构建差异 |
| `runtime/human_input.rs` | activate/resume HumanInput、自然语言输入、respond、通知当前性与 assignee 解析 |
| `runtime/node_execution.rs` | dispatch/failure、prompt 构建、upstream 输出、node outcome/Judge 处理 |
| `runtime/progression.rs` | 目标推进、branch skip、join、失败重试、持久化推进恢复 |
| `runtime/completion.rs` | complete/fail/cancel Run、Service Session 收尾、Group/Session runtime 清理 |
| `runtime/terminal_event.rs` | `handle_bot_terminal_event` 主体、stale event 原因与 terminal payload 处理 |
| `runtime/recovery.rs` | timeout 候选、过期扫描与现有 checkpoint recovery/cleanup 的编排 |
| `runtime/views.rs` | run/node/graph view、状态、history/panel 消息投影及范围窗口 |
| `runtime/events.rs` | public event、panel/bot 发布、persist panel、事件 payload/metadata 与时间转换 |
| `runtime/opening.rs` | opening message 获取、默认构造、模板渲染与 Run override |
| `runtime/logging.rs` | 既有 dispatch/delivery/terminal/timeout 生命周期日志辅助，保留 target 与字段 |
| `runtime/tests.rs` | 原 7 个内联测试及局部 fake；不把测试 port 引入运行路径 |

现有 Judge/dispatch/opening/history/publication/terminal-IM 恢复模块继续负责
原来的持久化检查点工作；新模块负责从原 `runtime.rs` 提取的编排，不再实现一套恢复算法。
现有 `runtime_observability.rs` 继续负责 Loop instrumentation；通用生命周期日志
不混入它的指标职责。

### 6.2 超长 trait 实现的处理

原 Service API 实现有 2,375 行，不能原样搬到一个仍然超限的 `service.rs`。
将有完整业务流程的 method body 提取为职责模块中的 inherent 方法，
trait method 保留同签名的直接委托，不新增内部 trait、动态 dispatch 或错误封装。

内部委托命名体现操作，例如 `start_run_impl`；不得与 trait 方法同名后又递归调用自己。
短小、仅返回投影的 trait method 可以直接保留。不能为了降低行数把所有方法压成单行，
也不能通过新增大量总被调用的空转发函数美化 method coverage。

不改变 `async_trait` 入口的 Send/生命周期契约；提取后必须重新验证这些编译约束。

### 6.3 顺序与运行成本不变量

- 保留授权 -> 持久化准备事实 -> 领取/开始发送 -> 外部投递 -> 结果确认的既有顺序。
- Builder 默认禁用/启用状态、Loop 编译限制、Provider timeout 和重试次数不变。
- HumanInput 保留当前性、授权、持久化-before-Judge 以及晚到响应拒绝语义。
- 失败、unknown 和 accepted 不能因委托层统一映射成相同状态。
- 不增加 await 点、DB/repo 调用、扫描批次、锁持有时长或发送次数。
  机械移函数保留原 await；薄委托的 await 只转交原 future，不另行调度/重试。
- 不把 Run、Session、Group 的一次性上下文变成新增长期缓存。

## 7. Runtime Progression 测试：保留恢复树与共享 fixture

本节路径相对 `crates/services/bcs-collaboration-runtime/tests/`。
保留 `runtime_progression.rs` 集成测试 target，继续直接装配既有
`fixed_loop_tests`、`history_identity`、`history_persistence`。

第 2 单元规划基准为 `b57b8e2d7`（包含 #3240）：入口 5,789 行；49 个顶层测试
按下表迁移，217 个既有后代保持完整身份，实际 target 共 266 项。后代物理文件共
26 个，本单元保持只读；新建 11 个场景、1 个 support 装配和 7 个 fixture 文件，
连同原入口共 20 个新增/修改 Rust 文件。详细一一清单与命令见第 2 单元实施计划。

| 新场景（`runtime_progression/` 下） | 职责 |
| --- | --- |
| `session_permission.rs` | 当前 Session permission、legacy Chat、Human access/cancel |
| `one_shot.rs` | transient bindings、Chat 保持打开、initiator 发布、ManagerWorker HumanInput |
| `result_publication.rs` | publication 失败及 rerun，不与初次启动测试挤成超长文件 |
| `human_input_activation.rs` | 有效 Human 存在、加入/恢复、通知与前端/IM 绑定 |
| `human_input_response.rs` | response 校验、pending、Judge、已持久化响应与失败 |
| `timeouts.rs` | Human timeout、缺失资源、无效候选跳过、Provider timeout 与拒绝投递 |
| `completion.rs` | 单 Node 完成、Session 收尾（保留上游移除 callback 后的契约）、日志以及 message-less final 重试 |
| `resource_cleanup.rs` | 删除 Session/Group、孤儿 Run 与 runtime 清理 |
| `definition_binding.rs` | source YAML、默认定义、模板 bindings、无额外 definition upsert |
| `opening.rs` | 自定义 opening、历史复用及写失败前不 dispatch |
| `graph_and_judge.rs` | snapshot graph、fan-out/join、Judge 分支、失败和 shared merge |
| `support.rs` | 测试共享装配，不再同时容纳所有 YAML 和 fake |
| `support/{delivery,judge,history,channel,definitions,groups,logging}.rs` | 保留原记录器/阻塞器/错误注入、YAML 工厂、Group/Session 工厂和唯一 tracing 初始化 |

### 7.1 有意保留的测试身份

既有恢复子树中存在 `current_exe()` + 完整 `--exact` 名称的重启测试。
本次不把 `fixed_loop_tests` 包到新 `scenarios` 或 `support` 层之下，
不重命名其后代模块，保留原完整名称。例如：

```text
fixed_loop_tests::recovery_tests::sqlite_restart_tests::session_recovery_tests::terminal_session_recovery_survives_process_restart
```

新场景可以改变原顶层测试的模块前缀，但叶函数名和场景数保持一致；
验证时使用明确的旧名 -> 新名映射，不能只比较总数。
若实现发现必须改变一个用于子进程的名称，应作为设计变更重新确认，不能静默修改过滤器。

### 7.2 fixture 与源码路径

- `test_runtime!` 宏在使用它的子模块前声明，保留注入 MemoryMessageRepo 的行为。
- tracing buffer/OnceLock 保持套件内同一份，不让每个模块重复安装全局 subscriber。
  旧 observability 后代直接读取 `SharedLogBuffer.0`，需开放到原 target 祖先范围；
  `SharedLogWriter` 是 `MakeWriter::Writer` 关联类型，需匹配的受限可见性，
  但无需在 target 根重导出 Writer 名称。
- 既有子模块通过祖先可见的测试辅助名称继续共享原对象，不复制 fake。
- 搬移 `include_str!("fixtures/fixed_loop.yaml")` 时修正路径，比较最终解析内容不变。
- 最初设计基准下 `runtime_progression/fixed_loop.rs` 恰为 1,000 行；第 2 单元基准
  已因上游变更降为 997 行。仍保持原文件及装配只读，不向它追加 import、重导出或辅助函数，
  把新增共享入口放在原测试 target 父级。

## 8. Collaboration Store：类型留在根、实现按存储与能力拆分

本节路径相对 `crates/services/bcs-collaboration-store/src/`。
Memory 和 SQL 共用的根类型仍放 `lib.rs`，以便既有检查点子模块访问原私有字段。
不把 `StoreInner` 拆成多个锁，也不把一个 store 实例变成多个子仓储实例。

### 8.1 目标文件与职责

| 文件 | 职责 |
| --- | --- |
| `lib.rs` | MemoryCollaborationStore、MySqlCollaborationStore、DbCollaborationTemplateRepo、StoreInner/记录类型、构造器和模块装配；保留公开路径与 `pub mod draft` |
| `memory_definition.rs` | Memory definition/source YAML/run snapshot trait 实现 |
| `memory_binding.rs` | Memory Group binding 与 compare-current 更新 |
| `memory_runs.rs` | 完整唯一 Memory `StateMachineRunRepoPort` 实现（基准约 696 行），保留原锁内流程 |
| `memory_events.rs` | Memory CollaborationEventRepoPort 及 eventful transition 的共享实现 |
| `memory_transition.rs` | `apply_memory_state_machine_transition`、状态校验等纯内存事务辅助 |
| `sql_definition.rs` | SQL definition metadata/blob/source/snapshot 读写与一致性检查 |
| `sql_binding.rs` | SQL Group binding、默认定义绑定及 compare-current 事务 |
| `sql_runs.rs` | 完整唯一 SQL StateMachineRunRepoPort 薄适配与短检查点转发 |
| `sql_run_lifecycle.rs` | create/idle-create/rerun、状态更新、Run 查询及分页 |
| `sql_node_state.rs` | Node 查询、activation、artifact/response、失败、retry、skip、过期候选 |
| `sql_transition.rs` | SQL `commit_eventful_transition` 完整事务及参数绑定 |
| `sql_correlation.rs` | delivery correlation/alias 写入、查找与身份校验 |
| `sql_events.rs` | SQL CollaborationEventRepoPort 实现 |
| `sql_mapping.rs` | row/param/status 映射、列投影、序列化校验与 DB 错误转换 |
| `snapshot_validation.rs` | snapshot envelope、compiler/hash 校验及 definition conflict 元数据辅助 |
| `template.rs` | 模板 repo trait 实现、tag/language 批量查询与模板专属 SQL helper |
| `tests.rs` | 原内联 SQLite/Memory/模板测试与其局部 fixture；基准约 815 行 |

已有 `judge.rs`、`history.rs`、`opening.rs`、`dispatch.rs`、`publication.rs`、
`terminal_im.rs`、`recovery_gap.rs`、`terminal_cleanup.rs`、`branch_skip.rs`
保持原职责、原逻辑父级与共享状态，不为统一目录外观再次搬移。

### 8.2 trait 与事务处理

Memory Run trait 当前已低于 1,000 行，可整块搬移，不强行做方法级委托。
SQL Run trait 当前约 1,501 行，按 Runtime 相同规则保留一个完整薄实现，
长方法委托到 SQL 职责模块；不能把 trait 分成多个重复 impl。
其他已低于上限的完整 trait impl 尽量整体搬移，避免不必要的函数层级。

以下行为全部保持：

- SQL 文本、参数位置、空值/sentinel 约定、CAS 条件、受影响行判断和事务步骤顺序。
- eventful transition 内业务写与事件写的原子性；不分成两个 transaction。
- Memory transition 的 clone-candidate、事件提交和替换真实状态的原失败行为。
- rerun 的 Session activation、唯一后继约束、snapshot/opening override 原子继承。
- 租约 owner/token/deadline、条件释放、terminal cleanup 和恢复 cursor 的 fencing。
- Definition source/hash 冲突和损坏持久化数据的错误返回，不添加默认值掩盖错误。

DB 往返数、批次上界、读写行数、锁/事务范围的预期变化为 **零**。
验证必须追踪实际 DbPlugin 调用，而不是从 repo 方法数量推断；
保留已有 query-count/recorded-statement 断言，并覆盖成功、冲突及持久化失败路径。

## 9. 验证设计

### 9.1 实施前保存基线

本节是未来实施的验收要求，不表示本次 design 编写已经执行测试。

1. 记录 HEAD、merge-base、工具链、feature 和测试环境。
2. 保存默认及 all-feature 测试编译 diagnostics，确认与 #3209 的零 warning 目标一致。
3. 保存两个集成测试 target、CLI bin 单测、Runtime/Store lib 单测及已有后代模块的
   `--list` 清单，包含 ignored、序列化测试属性和实际唯一测试名。
4. 保存 CLI 递归 help/叶命令清单、关键 mock 请求与输出、现有 E2E coverage 工件。
5. 对生产代码待搬迁段建立函数归属和基线覆盖映射，标明真实 E2E 可达性。
6. 重新复现并记录任何基线失败，不把之前执行过的结果当作新分支已通过。

#3209 的历史验证记录出现两个 bcs-bot-store 测试失败：
`persistent_capability_save_preserves_patched_internal_attributes` 和
`memory_control_plane_list_by_task_modes_filters_visibility_status_user_visibility`。
这只是待核对的已知记录，不是允许跳过任意失败或把新失败归为基线的理由。

### 9.2 每个实施单元的检查

- 使用 AST/结构化提取比较搬迁前后函数及测试体，列明所有非字节等价修改。
  允许的非字节等价项必须对应导入、路径、可见性或直接委托，不容许弱化断言。
- AST 保持性检查不能替代 Rust 编译；共享 fixture 拆分后必须实际验证跨模块名称解析
  和隐私边界，不能只凭 preview/verify 成功宣告工具可执行。
- 跨工具调用恢复 Bash 会话时，同时恢复工作目录、执行变量及 `set -euo pipefail`；
  验证经 `tee` 记录的失败仍以非零退出，避免 Cargo/门禁失败被日志管道掩盖。
- 验证测试名单旧 -> 新映射是一一对应；不能以相同数量掩盖丢失与重复。
- 重启测试保持完整名称，并逐个验证子进程实际运行目标测试，不能仅检查退出码 0。
- 对所有新增、修改 `.rs` 逐一计算物理行数，包含不在原五文件中的必要修改。
- `git diff --check`；不新增 allow、不跑 cargo fmt、不扩大格式化 diff。
- CLI help/alias/default、JSON/Text、退出码、preview 离线行为和动态 library OAuth 扩展检查。
- Runtime 权限、顺序、重试、终态、恢复和持久化失败回归。
- Memory/SQLite conformance + SQL statement/真实 MySQL 覆盖；mock SQL 不能代替真实 dialect。

### 9.3 命令与验收层次

以下 Cargo 命令在 `apps/bcs/` 执行：

```bash
# 各实施单元优先跑最接近的目标
cargo test -p bcs-cli
cargo test -p bcs-message-flow --test contract_bot_event
cargo test -p bcs-collaboration-runtime
cargo test -p bcs-collaboration-store

# 生产实现和测试支撑传播的交叉回归
cargo test -p bcs-message-flow -p bcs-collaboration-runtime -p bcs-collaboration-store

# 最终统一编译与运行
cargo build
cargo build --workspace --all-features
cargo test --workspace --no-run
cargo test --workspace --all-features --no-run
cargo test --workspace --no-fail-fast
cargo build -p bcs-cli --release
```

必须记录实际结果、warning 数、ignored 原因和未完成项。
设计阶段不要求跑这些命令；实施时不能只用 `--no-run` 代替业务测试。
架构/文件大小检查使用仓库当时的 canonical 入口；不可另写更宽松脚本替代门禁。

真实 MySQL 验证包括现有 `bcs-collaboration-store` 的
`mysql_store` target 下的
`real_mysql_fixed_loop::real_mysql_fixed_loop_snapshot_and_rerun_contract` ignored 场景：

```bash
cargo test -p bcs-collaboration-store --test mysql_store \
  real_mysql_fixed_loop::real_mysql_fixed_loop_snapshot_and_rerun_contract \
  -- --ignored --exact --nocapture
```

使用 CI 的现有 migration/数据库准备方式，不改迁移来适配重构。
此场景需要明确属于测试的空、可丢弃 MySQL schema；保留它的表所有权检查，
不得指向保留数据的开发或生产数据库。
如果缺少真实 MySQL，记录未运行并依赖可追踪的远端执行结果后再验收 Store 单元。

### 9.4 覆盖率：必须提前处理的风险

代码搬移会被 git diff 视为新增行；“业务没变”不是 changed-line gate 的豁免。
同一个低覆盖大文件拆分后可能使大量历史未覆盖分支进入分母。

- 不改 unit/E2E denominator、exclusion、skip pattern、changed-line-min-lines 或门槛。
- 不把生产代码放到 `tests/` 或伪装成 excluded 文件。
- 不让仅供测试的生产调用、空 wrapper 或重命名策略替代真实用户行为覆盖。
- 基准工作流的 E2E changed-line 配置是严格 `> 50%`，至少 20 个可测变化行时执行；
  根 AGENTS 的要求为 `> 60%`。本设计以更严格的 **`> 60%`** 为验收目标，
  不在本任务修改配置；若后续门槛更严，遵守更严者。
- 当前统一 runner 的 Runtime line/method 最低值为 41%/36%；保持门禁，
  HTTP endpoint 和 CLI leaf coverage 仍需 100%。不以旧工件代替本次实测。
- CLI leaf inventory 来自构建后二进制的递归 `--help`，不是扫描 `main.rs`。
  搬移参数声明后必须确认真实发现清单不变，不改 inventory 算法制造相同数字。

生产单元每批提取后执行真实 E2E 并检查逐文件 changed-line 报告；
从基线识别的未覆盖正常/错误路径，必要时补充通过公共入口驱动的有效验收场景。
无法在本范围内合规通过时停止该单元并报告阻塞，不擅自扩大业务修改或请求默认豁免。

统一验证在仓库根运行 `singlebox/ci/singlebox_coverage.sh`，然后使用
`singlebox/ci/verify_singlebox_coverage_artifacts.py` 校验本次工件。
只跑独立 BCS runner 不能被报告成已通过统一 shared-stack gate。
端口冲突时使用 runner 支持的隔离配置，不能终止其他 worktree 的服务。

## 10. 实施切片、传播范围与回滚

这不是逐步编码计划；以下定义可独立交付的边界和顺序。
每个单元开始前重新测量当前源码，不要求沿用基准行号。

| 顺序 | 独立单元 | 主要验证/传播 |
| --- | --- | --- |
| 1 | Bot Event 契约测试拆分 | 同一 target 下所有事件/任务测试、fixture 状态与测试发现 |
| 2 | Runtime Progression 测试拆分 | 既有 fixed-loop/recovery 后代身份、SQLite 重启、HumanInput 与日志 |
| 3 | CLI 入口拆分 | Clap/请求/输出、内部入口复用、release/debug 与 100% leaf coverage |
| 4 | Collaboration Store 拆分 | Memory/SQLite/真实 MySQL、事务/CAS/失败和运行成本不变 |
| 5 | Runtime 生产实现拆分 | 唯一 trait 实现、全部运行/恢复/查询场景及统一 E2E coverage |

先建立更清晰的测试定位，再动生产实现；Store 与 Runtime 不要求同时改接口。
建议每个单元一个独立 PR，单元内部可按职责分批提交和核对。
只有原入口及该单元所有新增、修改文件同时满足 1,000 行后，才交付该实施 PR；
不能把仍然超限的原入口提交到另一个 PR，并承诺“下一 PR 再拆”。

本设计覆盖一次 cleanup initiative，不把五个单元变为同时落地的大型架构迁移。
实施计划只展开下一项已获授权的单元，其余保持独立，避免一个未通过单元绑住全部工作。

回滚通过 revert 对应单元完成；无需 schema/config migration、数据修复或双版本兼容层。
共享 fixture 的迁移和其调用者必须在同一单元，不能留下无法独立 revert 的跨 PR 半成品。

## 11. 风险与合入条件

| 风险 | 必须采取的措施 |
| --- | --- |
| 模块路径导致 API/内部宿主构建失败 | 保留入口、公开类型与逻辑父级；外部 `#[path]` smoke 编译 |
| trait 拆分成重复 impl 或直接递归 | 唯一 trait impl；业务方法明确命名，编译与逐入口调用验证 |
| fixture 丢失、跨测试共享状态或资源早释放 | 保留所有权与测试内创建；名单与函数体比对、并发套件回归 |
| 子进程过滤器没有执行测试却返回成功 | 保留原完整名称，核对运行计数与 phase 产物 |
| Macro 作用域、include 路径或 release cfg 改变 | 显式模块装配、相同 fixture 内容、debug/release 编译 |
| 日志 target 与监控过滤漂移 | 核对移动前有效 target/level/字段，不修改运维过滤器掩盖 |
| SQL/Memory 事务边界改变 | 保持原完整事务与锁内流程，真实数据库及故障断言 |
| 新 wrapper 改变成本或 coverage 表面数字 | 最小委托，无新调度/序列化，检查 DB 调用与真实 E2E 分母 |
| 名为拆分实为大规模重写 | 逐职责提交，结构化函数体比对，非结构差异单列并阻止顺带修改 |

单元合入必须满足：零新增 warning、源码行数上限、无新测试失败、公开兼容验证、
适用门禁通过，以及未运行项目的明确说明。必需但尚未完成的验证不等于通过。

## 12. 设计自检与审阅边界

- 范围：仅五个文件与其必要内部装配，不引入跨 crate 组件化或消重。
- 一致性：类型和状态留原父级；长 trait 采用唯一实现，不使用部分 trait impl。
- 可执行性：给出每个模块的职责、特殊兼容点、逐单元验证和失败停止条件。
- 门禁：承认源码搬移进入覆盖率分母，采用更严格文档约束，不改阈值或统计范围。
- 权限与进度：第 1 单元已由 #3240 合入；第 2 单元计划已生成，用户已授权编写第 3 单元计划；
  各后续单元实际实施仍需执行指令。
- 初版设计证据：已检查源码、AST item 边界、现有模块布局、重启测试名称和 coverage 入口；
  设计阶段未执行构建、业务测试、数据库或 E2E。
- 第 1 单元规划期证据：当时临时副本通过 AST 保持性及 Rust 名称解析/类型检查，
  Bash 恢复与失败传播检查通过；该记录不替代后来 #3240 的实现与合入验证。
  详见第 1 单元实施计划及对应 PR。

第 2 单元规划已重新检查上游基线、266 项发现及共享 fixture 传播；临时副本验证和
子进程 phase 证据详见第 2 单元计划的“当前验证边界”，不等同于工作区实施或最终门禁通过。

第3单元规划验证包括原bin发现、临时debug草稿编译和help/入口次序比较；
完整release、宿主矩阵、业务/E2E门禁尚未执行，详见第3单元计划的验证边界。
