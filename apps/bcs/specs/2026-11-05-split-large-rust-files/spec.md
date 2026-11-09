# BCS 三个超大 Rust 文件按职责拆分 Spec

- 日期：2026-11-05。
- 所属模块：BCS；涉及 `bcs-message-flow`、`bcs-cli`、`bcs-group`。
- 状态：spec 文件已由用户审阅确认；三个独立 plan 已编写待审阅，尚未实施拆分。
- 代码阅读基线：`41f728aaa`；行数取自当日工作区。
- 交付组织：**共用本 spec，后续分别编写三个独立 plan；不是一个总 plan 下的三个阶段。**
- 首轮 spec 交付仅新增本文件；后续 plan 现已按第 7 节单独编写，仍未修改 Rust 代码。

## 1. 问题与目标

当前 `apps/bcs` 中有三个 Rust 文件超过 4000 物理行。测试入口混合多个业务主题与
大量测试替身；CLI client 混合多类 HTTP API、请求共用逻辑与单元测试，影响定位、
评审和后续维护。

以下路径均相对 `apps/bcs/`：

| 编号 | 原文件 | 基线物理行数 | 文件内静态测试属性数 |
| --- | --- | ---: | ---: |
| P1 | `crates/services/bcs-message-flow/tests/contract_message_flow.rs` | 4936 | 84 |
| P2 | `crates/tools/bcs-cli/src/client.rs` | 4864 | 46 |
| P3 | `crates/services/bcs-group/tests/management.rs` | 4423 | 84 |

静态测试属性数只统计上述文件中的 `#[test]` / `#[tokio::test]`，不是 Cargo 实际
发现或运行的测试数量。P3 已挂载的 `tests/management/no_session.rs` 另有 3 个测试，
必须继续纳入验证。实际完整清单由每个 plan 在实施前后采集并对照。

### 1.1 目标

1. 按职责组织原文件及其拆出模块，使每个文件具有明确的主要变更原因。
2. 保持现有 crate、调用入口、公共类型、方法签名和所有外部可观察行为。
3. 每个 plan 完成时，原目标文件及该 plan 所有新增、修改的 Rust 文件均不超过
   **1000 物理行**；建议不超过 800 行，为后续修改预留空间。
4. 保留既有测试场景、断言、测试属性及隔离行为，证明拆分没有漏挂载测试。
5. 三个 plan 可独立实施、验收、交付和回滚，不以另一个 plan 的产物为前置条件。

### 1.2 非目标

- 不清理其余超过 1000 行的 BCS 文件，也不扩展到其他模块。
- 不新增业务功能，不顺带修复历史行为，不改变权限、协议、超时、重试或错误语义。
- 不调整 Service API、Plugin API、HTTP/WS DTO、数据库 schema 或配置 schema。
- 不新建公共 crate、通用测试框架、请求执行框架或新的可替换接口。
- 不全局格式化，不借拆分进行命名、日志、字符串或 unrelated imports 的清理。
- 不修改 CI 阈值、覆盖率排除规则或既有测试的启用条件以使重构通过。

## 2. 依据与架构边界

本 spec 服从以下现有规则：

- [仓库 AGENTS.md](../../../../AGENTS.md) 与
  [BCS AGENTS.md](../../AGENTS.md)、[BCS CLAUDE.md](../../CLAUDE.md)。
- [架构宪法](../../../../docs/arch/arch.rules.md) 的契约权威、分层、单一职责和增量变更规则。
- [CI 约束](../../../../docs/arch/ci.enforce.md)。
- [Context Boundary 格式](../../../../docs/arch/context-boundary-format.md) 与
  [Protocol Contract Tests](../../../../docs/arch/protocol-contract-tests.md)：其中明确面向
  Backend 的具体元数据和 Python 测试格式不机械应用到 Rust；保留其边界和契约验证原则。
- [Message Flow context](../../crates/services/bcs-message-flow/CONTEXT.md)、
  [CLI context](../../crates/tools/bcs-cli/CONTEXT.md)、
  [Group context](../../crates/services/bcs-group/CONTEXT.md)。

参考 [Context map](../../../../CONTEXT-MAP.md) 和现有 [ADR 目录](../../../../docs/adr/)，
本次不引入新的系统级决策，不替代既有 ADR；未发现直接规定这三个文件物理拆分方式的
专项 ADR。文档使用当前 `apps/bcs/` 路径，不沿用部分历史文档中的 `src/bcs/`。

拆分不改变运行时依赖方向。CLI 仍是工具侧 HTTP client，不获得服务端业务或存储职责；
两个测试套件仍属于各自 crate 的测试边界，测试 fixture 不进入生产代码。

## 3. 方案选择与共同设计约束

### 3.1 方案比较

| 方案 | 优点 | 问题 | 决策 |
| --- | --- | --- | --- |
| 按职责拆分，保留入口 | 兼容面小，主题清晰，可独立验证 | 需要梳理 helper 依赖与模块可见性 | 采用 |
| 按行数切成数字片段并拼装 | 迁移机械 | 只隐藏体积，职责和依赖仍混杂 | 不采用 |
| 提取公共 crate / 通用框架 | 可能增加跨处复用 | 范围过大，引入本次不需要的抽象与依赖 | 不采用 |

### 3.2 物理结构与代码迁移

- 保留两个 integration test 根入口；入口主要承担模块挂载及少量必要接线。
- 保留 `src/client.rs` 与 `lib.rs` 现有导出路径；client 子模块继续提供
  `impl BcsClient`，不要求调用方改用新对象或新模块路径。
- 使用正常 Rust 模块边界；不得通过 `include!`、`part_1.rs` 等数字分片、删除注释、
  合并多条语句或压缩空白规避行数限制。
- 允许修改模块声明、必要的 `use` / `#[path]`、局部可见性及由自身迁移导致的死 import。
  函数体、断言、请求内容和调用顺序默认原样迁移。
- 新模块默认私有。内部 helper 仅在确有调用需要时开放最小可见性，不将 client 字段
  改成 public，也不为了方便测试新增公共 API。
- 单主题仍接近或超过 1000 行时，继续按真实子职责拆分；不能把问题转移到 helper 文件。
- 不在 production / dev dependencies 中新增依赖，不通过跨 crate 迁移绕过大小约束。

### 3.3 测试辅助代码

- 单主题使用的 fake / builder / helper 跟随主题；确实被多个主题使用的实现才进入
  当前套件内部的 support 模块。
- support 自身也按职责拆分，禁止形成新的超大 `support.rs` 或 `mod.rs`。
- 优先复用既有共享辅助代码，不扩张 `bcs-test-support` 或跨 crate support 的职责。
- 保持每个测试原有的独立 fixture、mock、时钟、端口、临时目录和锁行为，不引入共享
  可变状态、全局单例或对测试执行顺序的依赖。

下述模块名是职责命名建议，不是精确迁移步骤。三个 plan 应落实实际路径和归属；
可以在同一职责内细分命名，但改变兼容性约束或引入跨 plan 依赖必须回到本 spec 审阅。

## 4. P1：Message Flow 契约测试边界

入口保持为 `tests/contract_message_flow.rs`；主题文件放在
`tests/contract_message_flow/`，不得成为 Cargo 自动额外发现的顶层测试入口。

| 建议主题 | 负责内容 |
| --- | --- |
| `session_history` | Session 参与者、Human/Bot 身份、访问授权与 Session history 请求 |
| `group_history` | 历史来源选择、优先级、Bot/Provider 请求、窗口与 fallback |
| `web_send_routing` | 基础发送、路由、拦截、消息投影、DM、部分投递失败 |
| `web_send_sessions` | V2/V3 Group/Session 标识、显式 Session 和上下文投影 |
| `web_send_provider` | Provider target、旧 gray-list 兼容、bypass headers 与原投递路径 |
| `persistent_send` | 持久化发送、Session 解析、消息和事件写入 |
| `bot_event_callback` | Bot final、Group callback、转发、前端发布与持久化失败 |
| `abort` | Plugin/Provider abort、Run/Session 作用域及原始 downstream 标识 |
| `attachments` | 附件回显、临时附件投递及缺省/空列表字段语义 |
| `human_mentions` | 显式/文本 mention、通知限制与持久化文本语义 |

`RecordingMessageRepo`、history request fake、authority seed、active-run registration
等按实际复用关系分配到主题或本套件 support。现有
`../../../test-support/message_flow_contract_support.rs` 与 `support/session.rs`
的挂载路径需要在迁移时核实，但优先保持其内容与职责不变。

### P1 行为保持要求

- 保留消息持久化、Run context 注册、下游投递与前端发布的既有顺序和失败行为。
- 保留 Group/Session/Run 精确作用域、V2/V3 标识转换和 Provider target 选择。
- 保留拦截、授权、通知、附件与 callback 场景；不能通过减少参与者或替换 fake
  的语义让迁移后的测试更容易通过。
- 只修改本测试入口及其套件内部模块；不顺带拆分生产 Message Flow 文件。

## 5. P2：CLI Client 边界

`src/client.rs` 保留 `BcsClient` 状态、构造与配置方法、通用请求头/认证处理和必要的
通用响应 helper。子模块建议放在 `src/client/`，按下列职责扩展同一个 `BcsClient`。

| 建议主题 | 负责内容 |
| --- | --- |
| `bots` | Bot 生命周期、当前 Bot 解析、发现、查询、状态与 group-context 请求 |
| `chat` | 异步提交、状态查询、轮询、取消、Group chat 与 outcome 构建 |
| `groups` | Group 创建、列表、详情、成员与状态管理 |
| `collaboration` | 定义校验、自定义 Group、Session 单次执行、Run 查询与响应 |
| `relations` | Friendship 与 visibility |
| `sessions` | Session 创建、列表、消息、成员、参与模式与邀请 |
| `channels` | Channel binding 与 conversation 查询 |
| `services` | Service invocation 与状态查询 |
| `files` | 文件准备、上传、完成、下载、分享、能力查询、MIME 与 charset |
| `fusion` | Fusion 请求与 focus 参数 |

CLI 自有 option / page / outcome / error 类型可以留在根模块，或迁入私有类型模块后
从根模块保持原路径可用；现有 `lib.rs` 对外导出必须兼容。`ChatRunOutcome` 及
`ChatAsyncError` 不迁入 `bcs-protocol`，它们不是新增的 wire DTO。

单元测试按认证/请求头、Chat 提交、Chat 轮询、文件、资源请求等主题分配。
需要访问主题私有 helper 的测试优先作为该主题的子模块，不为了迁移测试扩大生产
helper 的可见性。不得将现有测试整体搬入另一个超过 1000 行的 `tests.rs`。

现有 `#[path = "draft_client.rs"] mod drafts` 的接线和 `draft_request` 行为保持兼容，
不重构其业务内容；保留原文件名时，必须验证新的模块声明仍解析到原文件。

### P2 行为保持要求

- 公共类型、构造函数、方法名、参数、返回类型和现有消费者的导入保持兼容。
- 请求方法、路径编码、query/body、响应解析及错误分类保持不变，包括 204 响应。
- 认证选择和优先级、Service Key、Actor、OAuth/Cookie/Bearer、Chat version、Trace
  header 的设置及省略规则保持不变，不增加凭据暴露。
- 保留跨 origin 重定向限制，以及文件传输的跨站认证隔离。
- 保留 Submitted/Queued/Running/Terminal/Unknown 状态处理、轮询预算、截止时间、
  durable identity 和 submit 不确定性；不新增隐式取消或重试。
- 保留 MIME、charset、文件内容、分享 `ttl_seconds` 等既有请求语义。
- 不改变 URL / token / 环境配置的发现入口或优先级，不新增环境读取。

### P2 既有行数豁免

[现有 client source-size waiver](../../docs/waivers/agentpass-subbots-client-source-size.md)
包含 `client.rs` 条目，记录的历史行数不等于本 spec 的现状基线，不能据其放宽本次要求。
该记录到期日为 **2026-11-12**。

P2 达到本 spec 的大小要求后，仅删除或标记完成其中 `client.rs` 的活动豁免条目，
同步必要的数量说明；保留其他文件的条目、所有者、期限和约束，不以本次重构延长豁免。

## 6. P3：Group Management 测试边界

入口保持为 `tests/management.rs`；主题文件位于既有 `tests/management/`。

| 建议主题 | 负责内容 |
| --- | --- |
| `creation` | Group 创建、策略、角色、配额、初始 Session、初始化投递与回滚 |
| `queries` | 列表、详情、排序、过滤、分页与通知模式投影 |
| `members` | 添加/移除成员、角色、参与模式、订阅边与成员权限 |
| `dm` | Human-Bot DM 创建、复用、命名及访问限制 |
| `deletion` | 删除授权、幂等性、Channel binding 清理及失败语义 |
| `workbench` | Connect、Chat、Abort、interaction resolve 的精确 Session 授权 |
| `human_authority` | Human 参与与角色约束、originator/driver 管理权限 |
| `visibility` | Public/private 约束、变更、成员限制与查询过滤 |
| `no_session` | 既有无初始 Session 创建测试，继续挂载，不能遗漏 |

`workbench` 或 `creation` 如体积过大，应按 connect / chat / abort / interaction、
普通创建 / 初始化等真实职责继续细分。不能将同主题的后半部分命名为数字续篇。

`Fixture` 与构造 helper、Registry fake、Friend/Relation fake、Session fake、runtime
connection fake、recording ports 按职责组织到本套件的 support 子模块。既有
`no_session.rs` 当前通过 `use super::*` 使用根模块符号，迁移时同步处理其依赖，
不得单独创建一套不同语义的 fixture 来规避模块可见性问题。

### P3 行为保持要求

- 保留 live authority seed、所有权/管理关系、当前 Session 参与者和参与模式判断。
- 保留 Group 创建、初始 Session 创建失败回滚及初始化投递结果的原有断言。
- 保留 Provider downlink、私有好友、Human 角色、DM 和 Public/private 约束。
- 保留“Group 删除已提交后，Channel binding cleanup 失败不回滚 Group 删除”的
  既有测试语义；不能将其泛化为允许忽略其他持久化错误。
- 不修改 Group 生产实现或共享契约，不通过全局 fixture 改变测试隔离性。

## 7. 三个独立 plan 的交付契约

三个 plan 与本 spec 放在同一目录，并明确引用 `spec.md`，不各自复制一份 spec。
以下三个文件为已编写的独立执行计划；共同服从本 spec，不构成串行的三个阶段。

| Plan 文件名 | 独立写入范围 | 独立验收范围 |
| --- | --- | --- |
| [plan-1-message-flow-contract-tests.md](plan-1-message-flow-contract-tests.md) | `contract_message_flow.rs` 与 `tests/contract_message_flow/` 内部模块 | 目标测试入口、完整 `bcs-message-flow` crate |
| [plan-2-cli-client.md](plan-2-cli-client.md) | `src/client.rs`、`src/client/`，必要的原导出接线及 client 行数豁免条目 | 完整 `bcs-cli`、现有消费者编译与 CLI 契约测试 |
| [plan-3-group-management-tests.md](plan-3-group-management-tests.md) | `management.rs` 与 `tests/management/` 内部模块，包括既有 `no_session.rs` 的必要接线 | 目标测试入口、完整 `bcs-group` crate |

### 7.1 独立性与集成

- 三个 plan 没有必须的执行顺序，不依赖其他 plan 新增的 helper 或 fixture。
- 不把跨 crate support 修改设为共同前置任务，不引入单独的第四个基础重构 plan。
- 涉及外部路径消费者或必要检查脚本更新时，先在所属 plan 列明精确范围及原因；
  不允许借此进入另一项拆分的写入范围，或把检查改成更宽松。
- 可以分别交付，也可以最后合并为一次交付；共同集成验收只验证组合结果，
  不替代任一 plan 的独立验证。
- 回滚以单个 plan 的源文件、模块接线及相关记录为单位；无需数据或配置回滚。

### 7.2 每个 plan 必须落实的内容

- 真实代码基线、实际发现的测试清单、helper/方法归属和路径依赖审计。
- 精确的新增/修改路径、职责说明、模块挂载关系、可见性设计和预计行数。
- 保持行为的迁移方式、检查点、实际可执行的验证命令与失败处理。
- 原测试到新测试的映射、兼容性证据、最终文件行数与未执行验证的原因。

## 8. 验证与验收标准

### 8.1 文件与差异检查

- **AC-1**：三个原文件及全部因本次拆分新增/修改的 Rust 文件均不超过 1000 物理行。
  统计包含注释、空行及文件末尾未换行的一行；同时检查所有拆出的 support/test 模块。
- **AC-2**：按第 3 节允许的迁移差异审查代码；没有业务逻辑变更、无关格式化、新依赖、
  API/schema/config 变更或新的行数豁免。
- **AC-3**：审计 `#[path]`、相对 fixture 路径、`include_str!` / `include_bytes!`、
  `file!` / `module_path!`、测试过滤规则和源文件路径型结构检查，避免迁移改变资源解析
  或绕过既有检查。仅更新确实受影响的引用，不重写历史文档中的旧路径记录。

### 8.2 测试不丢失、不重复

- **AC-4**：实施前后使用相同工具链、features 和测试发现条件采集实际测试清单。
  保留原测试函数名；模块前缀变化允许，但必须提供旧完整名称到新完整名称的一一映射。
  不只比较数量，也检查没有遗漏、重复挂载或变更 `ignore` / `cfg` / timeout 等属性。
- **AC-5**：保留原断言、mock 期望、失败注入、测试输入和调用顺序；不能改成更弱的
  “成功即可”断言或新增无业务意义的测试调用以填补覆盖率。
- **AC-6**：三个 plan 的近端验证和所属 crate 全量测试均通过；无法执行的项目明确列为
  未验收，并记录缺失的工具、依赖或运行环境，不将静态阅读视为测试通过。

下列命令从仓库根目录执行，是后续 plan 的最低验证范围，不代表本次文档阶段已运行：

```bash
# P1：实施前后用 -- --list 采集入口测试清单，再执行测试及所属 crate 全量测试。
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow --test contract_message_flow -- --list
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow --test contract_message_flow
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow

# P2：实际测试清单需包含 lib、bin 和 integration tests。
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli -- --list
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli
cargo check --manifest-path apps/bcs/Cargo.toml -p bcs-cli -p bcs-admin --all-targets

# P3：清单必须包括已挂载的 no_session 测试。
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-group --test management -- --list
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-group --test management
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-group
```

### 8.3 集成和既有 CI

- **AC-7**：集成后对受影响 crate 执行 `cargo check --all-targets`，并通过现有 BCS
  架构/依赖检查、CLI 契约测试和 BCS 单元测试 CI；消费者范围不只限于近端测试入口。
- **AC-8**：BCS unit gate 保持 100% 通过率、整体行覆盖率严格大于 87%；超过 50 条
  instrumentable changed lines 时 changed-line coverage 严格大于 85%，1–50 条只报告、
  0 条 N/A。移动源码不自行获得覆盖率豁免，不添加路径排除掩盖变动。
- **AC-9**：交付时通过现有统一 `singlebox/ci/singlebox_coverage.sh` 门禁，再使用
  `singlebox/ci/verify_singlebox_coverage_artifacts.py` 验证
  `singlebox/.dependencies/coverage/singlebox/reports/` 中的报告。
  保留 BCS E2E 全故事通过、runtime line 至少 54%、method 至少 48%、HTTP endpoint
  和 CLI leaf-command 100% 的要求；E2E scope 中至少 20 条 instrumentable changed
  lines 时 changed-line coverage 大于 65%，较小或 scope 外变更仅报告。
- **AC-10**：不以旧独立 E2E 入口替换统一 Singlebox 门禁，不弱化 gate、修改阈值、
  排除迁移后的文件或为本次重构延长 waiver。未运行的集成门禁需明确报告。

### 8.4 运行时访问成本

这次只移动函数与测试，不增加数据库、缓存、网络调用、锁、后台任务或重试。
每个 plan 应在差异审查中确认原调用顺序和次数未变；如实际迁移需要改变这些行为，
停止相应实施并另行设计，而不是将其视为无影响的拆分。

## 9. 风险、兼容性与完成定义

主要风险是遗漏模块挂载、相对路径解析变化、Rust 私有项不可见、测试重复执行，以及
CLI 请求/错误处理在迁移中被顺手修改。通过保持入口、明确归属、测试清单一一映射、
最小可见性和调用方编译检查控制这些风险。

公共契约、持久化和部署兼容性不变，不需要迁移或发布切换。测试完整名称允许因新增
内部模块前缀变化，相关过滤/报告引用需要审计并记录，不能静默失去覆盖。

当三个独立 plan 各自满足 AC-1 至 AC-6，组合结果满足 AC-7 至 AC-10，且交付记录
包含每个 plan 的变更提交、最终文件行数、测试映射和实际验证结果时，才可标记本
spec 已实施完成。当前文档完成、设计确认或仅近端测试通过均不等于实施完成。

## 10. 本次 spec 编写的证据与后续审阅

本次已阅读仓库/BCS 指令、架构文件、三个 crate 的 context、目标文件的声明/方法/
测试主题、共享辅助代码挂载关系、现有 CLI 导出与 client 行数豁免，并复核基线行数
和文件内静态测试属性数。以上是代码阅读与静态统计证据，不是运行时验证。

本次不执行 Rust 编译、Cargo 测试、覆盖率或 Singlebox：尚无实现变更；这些是后续
执行计划的验收工作。本次文档交付检查相对链接、需求一致性及 Git 差异，不声称
现有 Rust 测试已通过。

用户已确认本文，并授权使用 writing-plans 流程编写第 7 节的三个独立 plan。计划编写
阶段额外核实了全部测试/方法的职责归属，执行离线 Cargo metadata 检查了实际 targets
和 CLI 直接消费者，并通过本地 nextest 帮助核对清单命令；没有编译或运行 Rust 测试。
三个 plan 仍待审阅与执行，不存在替代它们的总执行 plan，也未开始 Rust 拆分。
