# Avernet BCS 五个超限 Rust 文件按职责拆分设计

- **日期**：2026-11-12
- **状态**：draft / 文档修订，尚未实施；进入 DEV 前需审阅本文与配套 plan。
- **仓库与范围**：`inclusionAI/Avernet` 的 `apps/bcs`，不是内部 overlay。
- **源码基线**：`9a61798cb7752308aaa01f8e59d78a46809066cb`（PR #3926 的原始父提交）。
  当前 PR 原始文档提交未修改这些源码；实施时基于实际工作分支重新采集清单，不把旧行号当剪切指令。
- **计划**：[Implementation plan](plan.md)。

## 1. 范围纠正与目标选择

原稿把公共 BCS 工单误写成内部 workspace 拆分。按用户确认，本版完全替换该方案：
不初始化外层宿主子模块，不依赖内部插件、内部制品源或内部结构检查脚本。
旧方案的源码行数、测试数量、工具链探测与自检结论不构成本文的证据。

本工单固定拆分五个文件，不承诺清理整个 BCS：优先处理 server source-size waiver 中
仍超限的四个 Rust 文件，再处理与 Bot frame 套件对应的 Workbench frame 兼容测试。
这是本版的明确选取规则，不是“公共仓库仅剩五个超限文件”的声明。

| ID | 原文件（相对 `apps/bcs/`） | 物理行数 | 内容 / 静态测试属性数 |
| --- | --- | ---: | --- |
| F1 | `crates/adapters/http/bcs-http/src/routes/sessions.rs` | 2279 | Session HTTP handlers；尾部 tests 为空模块 |
| F2 | `crates/adapters/ws/bcs-ws/tests/frame_compat.rs` | 3424 | Bot WS 兼容测试；58 个直接测试，另挂载 `task_rpc_scope` |
| F3 | `crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs` | 2840 | Workbench WS 兼容测试；40 个直接测试，另挂载共享 `support` |
| F4 | `crates/services/bcs-bot/tests/bot_use_cases.rs` | 2299 | Bot application 用例；37 个直接测试 |
| F5 | `crates/services/bcs-organization/tests/management.rs` | 1294 | Organization management；26 个直接测试 |

以上测试数只是源码静态统计，不代替 Cargo/nextest 的实际发现清单，尤其不能漏掉被挂载的子模块。
不重做 [2026-11-05 的拆分方案](../2026-11-05-split-large-rust-files/spec.md) 所覆盖的
Message Flow 契约、CLI client 和 **Group** management；本版 F5 是 **Organization** management。

## 2. 权威、兼容性与非目标

先读 [BCS README](../../README.md)、[BCS AGENTS](../../AGENTS.md)、[CLAUDE](../../CLAUDE.md)，以及
[开发规则](../../../../docs/agents/development.md)、[架构宪章](../../../../docs/arch/arch.rules.md)、
[CI 约束](../../../../docs/arch/ci.enforce.md)、[Context Boundary 格式](../../../../docs/arch/context-boundary-format.md)、
[契约测试](../../../../docs/arch/protocol-contract-tests.md)、[知识布局](../../../../docs/agents/knowledge-standard.md)
和 [push 验证](../../../../docs/agents/validation.md)。模块事实以源码和下列 context 为准：

- [HTTP adapter](../../crates/adapters/http/bcs-http/CONTEXT.md)：只移动已有 handler，不把业务策略迁入 adapter。
- [WS adapter](../../crates/adapters/ws/bcs-ws/CONTEXT.md)：保留身份、Run/Session、版本及 admission 兼容语义。
- [Bot](../../crates/services/bcs-bot/CONTEXT.md) 与 [Organization](../../crates/services/bcs-organization/CONTEXT.md)：只迁移测试，不改生产实现。

没有 Service API / Plugin API、wire schema、数据库或配置变更；不新增 crate 或依赖，不改
Cargo manifests/lockfile、bootstrap wiring、路由挂载、公共类型/函数签名或 CI 阈值。
不创建 ADR：同 crate 内部文件布局调整不引入新的系统级决策。当前文档仅属 change spec；
实施后在本记录追加验证与提交，公共接口保持不变时不改写领域契约。

硬上限是每个原目标及本次新增/修改的 `.rs` 文件 **≤1000 物理行**，建议 ≤800。
注释、空行、测试以及末尾无换行的非空行均计入。其他既有超限文件只报告，不扩大本次范围，
也不得通过豁免、路径排除或弱化检查把本次新超限文件判绿。

只允许原函数/测试整体移动、必要模块声明、import/path 修正、精确 re-export，以及下述日志 target 保持补丁；不全局格式化，
不删测试、不改断言、ignore/cfg/timeout、mock、并发或错误语义。移动不得增加数据库/缓存/
网络调用、循环扫描、锁或重试；不优化已有查询，已有调用顺序和次数保持不变。

## 3. F1：保留 `sessions.rs` 作为兼容入口

保留 `src/routes/sessions.rs`，新文件置于 `src/routes/sessions/`。在文件模块中使用
`mod creation;` 会正确加载 `sessions/creation.rs`，不把同目录普通 `mod` 当成 sibling 文件加载。
不改成目录 `mod.rs`，避免一次额外重命名。

| 文件（相对 `bcs-http/src/routes/`） | 责任、迁移锚点 | 预计行数 |
| --- | --- | ---: |
| `sessions.rs` | 保留共享 imports、序列化 helpers、三个 `human_*` 授权 helper、`session_error_to_response`、`session_application_error_response`、`resolve_session_caller`；声明子模块并精确导出 | 约 300 |
| `sessions/creation.rs` | `CreateSessionRequest`、`legacy_creator_role`、`legacy_session_kind`、`session_launch_error_to_legacy`、`create_session_for_group`（160–332） | 约 190 |
| `sessions/queries.rs` | `ListSessionsQuery`、`default_limit`、`list_sessions_for_group`、`get_session_by_id`（333–608） | 约 290 |
| `sessions/lifecycle.rs` | `PatchSessionRequest`、`patch_session`、`complete_session`（609–769）及 `delete_session`（2047–2156） | 约 290 |
| `sessions/participants.rs` | Add/Update participant DTO；add/remove/update mode handlers（770–1407） | 约 660 |
| `sessions/chat.rs` | `SessionChatRequest`、`session_chat`（1459–1684） | 约 240 |
| `sessions/history.rs` | `GetSessionMessagesQuery`、get/merge/history-view/authorize helpers（1685–1975）及 `session_history_error_to_response`（2012–2046） | 约 340 |
| `sessions/collections.rs` | `CollectSessionRequest`、`resolve_collector_bot`、collect/uncollect handlers（2157–2273） | 约 140 |

每个锚点包含其关联 doc comments、derive/serde 属性，不允许按行号切掉属性。表中的估算含接线余量，
实施以实际计数为准。尾部既有空 tests 模块可原样保留，不虚构迁移了 Session 单元测试。

### 路径与可见性

- `sessions.rs` 用显式 `pub use child::{原 public 项};` 保持全部原 handler/DTO 路径，
  包括 `bcs_http::routes::sessions::CreateSessionRequest`。不新增 public 子模块或 glob 公共导出。
- 三个 `human_*` 维持原 `pub(crate)`，`session_error_to_response` 维持 `pub`；父模块其他
  私有 helpers 保持私有，后代可直接访问。不能为了移文件把所有字段改成 `pub(crate)`。
- 子模块用 `use super::*;` 或精确 imports；只有跨兄弟复用的
  `collections::resolve_collector_bot` 需要 `pub(super)`，父模块私有导入供 queries 使用。
- `delete_session` 的 `super::groups::DeleteSessionQuery` 移动后必须改为
  `crate::routes::groups::DeleteSessionQuery`，指向同一类型；这是允许的路径修正，不是契约改变。
- `router.rs`、`routes/channel.rs`、`routes/services.rs`、`routes/session_files.rs` 的调用路径不变。
  保留 session-only 参与权限、Human→Bot live authority 错误传播、history 的 view/cutoff、
  collect 代理授权、完成 CAS 和投递/通知顺序；不改其既有业务含义。

## 4. F2–F5：只迁测试，保留套件根的共享设施

四个 integration target 文件原位保留。测试按 plan 的逐函数映射移入各自同名子目录，
每个主题直接挂在 integration crate 根，不引入额外 `mod tests` 层。
根保留 shared imports、fixtures、recording doubles、constructors 和已有外部模块声明。
主题用 `use super::*;` 访问父模块私有设施，**不需要放宽 fixture 字段可见性**。

| ID | 目录（原入口旁） | 主题模块 | 根 / 最大主题预算 |
| --- | --- | --- | --- |
| F2 | `frame_compat/` | connection、diagnostics、v3_events、interactions、task_dispatch、coordination_echo、chat_events、tracing、run_aliases | 根约 880；interactions 约 670 |
| F3 | `web_frame_compat/` | diagnostics、subscriptions、chat_send、abort、interactions、bound_connection、bound_messages、ingress_identity | 根约 680；chat_send 约 480 |
| F4 | `bot_use_cases/` | queries、discovery、organization_discovery、connection、status、authority_writes | 根约 500；queries 约 480 |
| F5 | `management/` | membership、conformance、member_profile、candidates、runtime_membership | 根约 230；各主题约 320 以内 |

F2 根的非测试内容静态估计约 846 行，加接线后约 880 行：这是建议 800 行目标的明确例外，
仍低于硬上限 1000。保留 fixture 在父模块可避免无必要的跨兄弟可见性扩张；不是新行数豁免。

挂载使用显式路径，例如在 `frame_compat.rs`：

```rust
#[path = "frame_compat/connection.rs"]
mod connection;
```

模块名在不同 integration target 中独立。子目录文件不成为新的顶层 test target。
F2 根保留 `#[path = "support/task_rpc_scope.rs"] mod task_rpc_scope;`，以及 `new_state`、
`TestState`、`recv_response`、全部 Recording 类型和 impl、coordination builders、
`connect_v3_interaction_bot`、`capture_otel_spans`、`assert_gen_ai_output_message`。
该已有模块的 `use super::*` 继续指向同一根，内容不变。

F3 根保留 `mod support;`，不复制/改动 `tests/support/mod.rs`；保留所有 doubles 和
`new_state*`、`recv_response`、`session_bound_auth`、`connect_session_bound`、`system_now_ms`、
`upgrade_ingress`。F4/F5 根保留所有非测试 helpers/fixtures 和 trait impl。
如果重新采样发现根超过预算，须先更新职责表，不能随意将辅助设施搬入另一个超限 support。

保持 F2 工具结果授权及去重、V2/V3 scope、terminal/sequence、tracing 语义；
保持 F3 connection binding、admission、Human input、interaction replay、abort 和 ingress identity；
保持 F4 manager/owner、provider、visibility/discovery 与 reconnect；
保持 F5跨 Provider grant、effective/runtime membership、分页及 conformance 调用。
不修改无 name/toolName 的永久 BaaS fixture，不引入按测试名称排序才能通过的共享全局状态。

## 5. 实施切片与风险控制

按 F5 → F4 → F2 → F3 → F1 实施，每个目标完成后独立编译、测试和 review；不强制凑七个提交。
中间拆分允许原入口尚未达标，但不得宣称该目标已完成。生产模块和测试主题都完成后，
再做该目标的文件尺寸验收；本次全部新增/修改 Rust 文件最终都受 1000 行限制。

函数体移动不等于自动证明行为不变。每个阶段核对完整 diff、公开导出、测试属性、路径敏感引用
（`#[path]`、`include_*`、`file!`、`module_path!`、tracing target）及测试筛选配置。
完整测试名可以增加主题前缀，但必须提供 before→after 一一映射，禁止只比较总数。
F1 有六处隐式 target 的 tracing 宏：participants 两处、chat 两处、lifecycle 两处。
它们迁移后必须显式保留 `target: "bcs_http::routes::sessions"`；只增加此 metadata，
级别、字段、消息及求值顺序不变，审阅中单独列出这六处非纯文本移动。源码位置可随移动改变。
不得将默认 target 变化误称为零日志影响；若还有需行为改动的依赖，先停止该项并修订方案。

回滚以单个目标的入口、主题文件、接线与对应完成记录为单位，无数据/配置迁移。

## 6. 验收与门禁

1. plan 的尺寸检查覆盖五个入口与全部本次新增/修改 `.rs`，按文件计数，支持无尾换行，
   任一超限/缺失返回非零；不统计 `wc` 的 total，不扫描 target/cache，不要求全仓历史超限清零。
2. `suite_sessions`、`frame_compat`、`web_frame_compat`、`bot_use_cases`、`management`
   及所属四个 crate 全量测试通过；F1 没有自带单测，不能以 `--lib` 替代 HTTP 契约套件。
3. 采集所有测试（包括已挂载子模块及 ignored 元数据）的 before/after 清单、精确映射；
   保持同工具链/features，无丢失、重复挂载或测试属性变更。
4. 运行现有 `apps/bcs/scripts/ci/arch-check.sh`、`apps/bcs/scripts/ci_test.sh --fast-fail --coverage`
   及统一 Singlebox 和 artifact verifier，命令见 plan。保留 pass-rate、overall/changed coverage
   的现有门槛；没有“纯移动所以豁免覆盖率”的特例。
5. 正常安装并运行 repository hooks，禁止绕过 pre-push 或关闭必需门禁。
   本机工具缺失应补齐公开依赖，或记录未验收并由有能力的 CI 环境补齐同一提交的证据；
   缺失、失败和 skip 不算 pass，所有必需门禁有通过证据之前不得声明完成/合并。
6. 完成 F1/F2/F4/F5 后，在 [server waiver](../../docs/waivers/agentpass-subbots-source-size.md)
   将这四项移出活动清单并记录完成提交/实际行数；保留历史基线和其他条目，不延长 expiry，
   不改无关 client waiver。不把四项清理描述成整份 waiver 已关闭。
7. 按仓库 PR 模板记录 Problem/Solution/Validation，以及无契约变化、消费者范围、
   最终行数、测试映射、实际门禁结果和未运行原因。同步本 spec 的交付状态。

## 7. 文档阶段证据与限制

本次已静态核对五个源码的声明、职责、相对路径、消费者及测试属性，读取所属 context、
原拆分 spec 和 waiver；使用 `cargo metadata --no-deps --offline --locked` 验证五个 test target
及所属 crate 存在。行数脚本的合规、超限、无尾换行、带空格文件名、缺失路径和变更文件发现用例见验证记录。
这些不是 Rust 测试通过证据。本次不修改 Rust、不运行产品测试/coverage/Singlebox；
后续实施必须按 plan 重新建立绿色基线并执行验收，不沿用原稿的内部环境探测结果。
