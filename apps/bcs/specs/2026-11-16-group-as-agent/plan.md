# Group as an Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 public 且显式开启 `supportA2A` 的合格 BCS 群交付可认证、可重试、可取消、可恢复的标准 A2A 调用能力。

**Architecture:** 在现有 Session／MessageFlow／Collaboration Runtime 之上增加 transport-neutral 的 Group A2A application 和生命周期 Core；通过组合 Repo 保证受理及终态原子性。认证使用独立的 A2A 插件 profile，首期只接受 BCS 签发的通用调用凭据。HTTP adapter 负责标准 wire，Store 负责 SQL／索引／组合事务，Bootstrap 负责装配和生命周期。

**Tech Stack:** 仓库固定 Rust 1.94.0、Tokio、async-trait、Serde、Axum；现有 `DbPlugin` 的 SQLite／MySQL、SHA-256、rand 0.8、secrecy 0.8；Python pytest／PyYAML 验证 API 合同。不升级现有框架；JCS 必须通过固定 RFC 测试向量，新增实现依赖需单独审核。

**Spec:** [spec.md](spec.md)；同时阅读 [examples.md](examples.md)、[research.md](research.md)。方案取 spec 的建议凭据实现／参数作为**本计划基线**，不改变 spec 的 proposed 状态；请求编写计划不是执行、发布或上线授权。

## Global Constraints

- “首期只交付 BCS 签发 Token 的 Bearer 认证，不支持无鉴权”。
- “不配置调用方到 Group 的白名单或 Group-scoped token”。
- “`visibility=public` **且** `supportA2A=true`”；历史／创建省略默认 false，PATCH 省略不变、null 非法。
- 支持 ManagerWorker／StateMachine；无 Human 成员（以 actor_kind 判断），无 HumanInput 依赖；Human owner 不等于 Human participant。
- Group=Agent、Session=context；每轮新 Task；busy 不排队、不预留 messageId；先查重后检查新调用资格。
- 入站 token 不进入 Task／日志／Artifact／成员上下文；调用身份不等于下游 Human 执行身份。
- 受理事务包括 Session、Task、预留、占用、容量及启动意图；外部投递只能在提交后。
- “最终 Artifact 与成功终态原子提交”；A2A 托管 Session 不允许 Runtime 独立提前完成。
- “未确认停止不能释放执行权”；隔离仍占执行容量，重启不重置 deadline、不重发未知执行。
- supportA2A 关闭只拒绝新轮次、撤销 Card；历史查询／取消／幂等命中保留 Caller 隔离。
- A2A 1.0.1，线上 `A2A-Version: 1.0`，HTTP+JSON/SSE；不套 V1 envelope。
- 默认等待 60 秒、执行 30 分钟、收尾 60 秒、保留 7 天、SSE 心跳及认证复验 15 秒；其余限额逐项使用 spec §12，不另造默认值。
- 不做匿名、调用方 Group grant、文件上传下载、FIFO、轮内追加、自然语言建群、单 Bot 入站或业务 exactly-once。
- 遵循 adapters → application → core／business port → store → plugin API；Core 不直调 delivery，application 不导入 plugin API／SQL／HTTP。
- 不运行 cargo fmt／全局格式化；新增／修改源文件不得超过 1,000 行，邻近大文件只加 mod／转发或作必要的局部提取。
- 不修改已提交迁移；运行时基于各方言最新编号新增，不把本文记录的编号当永久预留。
- 单元测试通过不等于真实 MySQL、host 装配或部署通过；所有执行记录单独报告。

---

## 1. 执行边界与导航

- 计划编写基线：`171ce2e5408f5061da2711e7305bf6d499a0a4cf`，当前分支 `docs/bcs-group-as-agent-spec`。
- 仅本次文档工作已执行；下列实施 checkbox 均未完成。
- 同目录保存 plan 是仓库 spec/plan 布局要求；不使用根 `docs/superpowers/plans/` 默认路径。
- 功能跨多个可独立评审的子系统，因此拆成五份任务明细；它们属于同一发布合同，不在中途宣告 Group A2A 可上线。
- 每次只领取一个任务或一个明确测试切片。先读任务的 Interfaces，再读依赖任务；契约变更必须更新 [接口清单](plan/interfaces.md) 的所有消费者，不能让实现者自行猜邻居的类型。
- Writing-plans 不要求现在建 worktree。执行前按 using-git-worktrees 检查现有隔离和用户偏好，沿用此分支的 spec，不把普通 dev 覆盖成未审设计。
- 多 Agent 只在用户选择该执行方式后启动；本计划不自动授权派发。

| 明细 | 任务 | 可独立验收的产物 |
| --- | --- | --- |
| [A：基础与认证](plan/01-foundation-auth.md) | T01–T05 | 纯合同、迁移链、凭据 Store、认证 profile、管理员签发／撤销 CLI |
| [B：群开放与受理](plan/02-group-admission.md) | T06–T08 | supportA2A 持续不变量、组合受理 Repo、应用受理与执行意图 |
| [C：执行与恢复](plan/03-execution-recovery.md) | T09–T13 | manager／状态机精确执行、完成事务、整轮取消、重启／隔离恢复 |
| [D：数据与发布](plan/04-results-catalog.md) | T14–T16 | 私有查询分页、逻辑清理、同源 Card 投影和可靠撤销 |
| [E：HTTP 与交付](plan/05-http-integration.md) | T17–T20 | 标准 HTTP/SSE、完整装配、Singlebox／MySQL／回归与上线门槛 |
| [迁移字段与事务布局](plan/schema.md) | T02 迁移输入 | 完整列／索引、方言、锁序与后续任务共享的存储合同 |
| [共享接口与测试基座](plan/interfaces.md) | 各任务的输入输出定义 | 跨任务的签名、状态、fixture 和 fault-point 命名 |

## 2. 依赖顺序与发布门槛

```text
T01 → T02 → T03 → T04 → T05
       └──→ T06 → T07 → T08 ─┬→ T09 ─┐
                           └→ T10 ─┴→ T11 → T12 → T13
                    T07 + T11 ───────→ T14 → T15
             T04 + T06 + T07 ────────→ T16
           T04 + T08 + T11 + T12 + T14 + T16 → T17
                                 T13 + T17 → T18 → T19 → T20
```

T09/T10 可独立实现，但共享 Service API／bootstrap 文件必须串行整合；不能假定写集互不重叠。T14/T16 可以在执行适配尚未完成时用真实 Store＋记录型执行端口验证，仍不能开启生产入口。T15/T16 是 T19 的必要依赖，图中无直连不表示可以省略。

- Gate A（T05）：只允许操作签发／撤销通用 client 凭据；Group A2A 入口未挂载。
- Gate B（T08）：同库组合事务和幂等可验证；执行必须是注入的 recording fixture，无伪装成功的生产默认 port。
- Gate C（T13）：真实 MessageFlow／Runtime 的完成、取消、恢复闭环通过；未知执行仍隔离。
- Gate D（T19）：全部能力明确注入、配置合法、migration／worker 就绪时才允许挂载新入口；默认仍 disabled。
- Gate E（T20）：真实 MySQL、Singlebox、回归、容量验证、目录迁移和 host 集成记录完整后，才讨论部署授权。

## 3. 文件结构与职责

所有路径相对于仓库根；下列新增目录均包含 `Cargo.toml`、`src/lib.rs`、`CONTEXT.md`，与其首个可执行任务同交付，不单独创建空 crate。

| 新增单元 | 职责／主要文件 | 不应放入 |
| --- | --- | --- |
| `crates/services/bcs-group-a2a` | lifecycle Core：`admission.rs`、`completion.rs`、`cancel.rs`、`recovery.rs`、`projection.rs` | HTTP、SQL、具体插件、直接 Bot 网络投递 |
| `crates/application/bcs-app-group-a2a` | 用例：`send.rs`、`queries.rs`、`worker.rs`、`retention.rs`、`publication.rs` | 原子事务伪实现、环境读取、wire DTO |
| `crates/services/bcs-group-a2a-store` | `memory.rs`、`sql/{admission,terminal,events,queries,recovery,retention,publication}.rs`、`schema.rs` | 多个非原子 repo 调用冒充提交 |
| `crates/services/bcs-a2a-token-store` | client／token 摘要持久化：`memory.rs`、`sql.rs` | 签发明文日志、Bot 查找 |
| `crates/plugins/bcs-auth-bcs-token` | A2A 验证插件：`verify.rs` | 群范围授权、Task 操作 |
| `crates/application/bcs-app-a2a-auth` | 认证用例＋管理签发：`authenticate.rs`、`credentials.rs` | HTTP header 类型、plugin API 引用 |
| `crates/adapters/http/bcs-api-http/src/v1/a2a/group` | `mod.rs`、`dto.rs`、`auth.rs`、`errors.rs`、`tasks.rs`、`send.rs`、`sse.rs` | 现有 Agent 管理权限重写 |
| `crates/bootstrap/bcs/src/group_a2a` | `mod.rs`、`auth.rs`、`execution.rs`、`stores.rs`、`lifecycle.rs` | 新的一份执行／认证业务策略 |

表中 `crates/` 均以 `apps/bcs/` 为前缀。Service API 新增 `application/group_a2a`、`application/a2a_auth`、`core/group_invocation`、`port/group_execution`、`port/repo/group_invocation`、`port/repo/a2a_token`；纯数据类型放 `bcs-domain/src/group_a2a/`，认证 Plugin API 放现有 `bcs-auth-api/src/a2a.rs`，不另造全局认证框架。

共享测试矩阵放现有 `bcs-test-support/src/contract/`，只依赖合同／fixture 数据；具体 Store／Runtime 构造在被测 crate 的 `tests/support/`，避免 test-support 与实现 crate 循环依赖。实现新增依赖、workspace member、结构边界及 conformance 注册在所属任务一起更新。

已核实的大文件：Group application `service.rs` 911 行、Group Core `group_core.rs` 857 行、bootstrap `server.rs` 989 行、migration `schema.rs` 899 行。新增行为进相邻专属模块，保留薄调用，不能借此任务全库重构或新增豁免。

## 4. 每个任务的执行规程

- [ ] 在仓库根确认 branch、dirty files 和任务依赖；保留非本任务修改。
- [ ] 读取对应 app README、最近 CONTEXT 和 [development](../../../../docs/agents/development.md)；接口变更同时读架构／CI 边界。
- [ ] 按明细先加一个具体断言／fixture，再跑指定最小命令，记录**预期原因**的 RED（不是网络或工具缺失）。
- [ ] 按每个 Step 实现单一行为；复杂 Step 内给出的子动作逐项完成，各用 2–5 分钟可检查的编辑／测试动作推进。任务整体可能需要多个这样的循环，不承诺一个任务只用数分钟。
- [ ] GREEN 后再加该任务列出的竞争／失败用例，禁止仅测 happy path 或仅测 mock 返回值。
- [ ] 完成所属 crate 回归、conformance、`git diff --check`、行数检查和知识更新，按任务建议主题提交。
- [ ] 提交前只 `git add` 本任务 Files；提交内容不含 token、数据库、日志、临时校验工具。不得关闭 pre-push／coverage gate。
- [ ] 将真实执行结果追加到同目录 `delivery.md`，区分 Memory、SQLite、MySQL、HTTP、host；未执行就写未执行。不要提前勾选本文任务。

所有新增源文件的父模块注册和Cargo依赖在对应任务一起交付；文件清单使用仓库相对的完整路径。所有 `cargo` 示例从 `apps/bcs` 执行；Python／Singlebox 命令明确用仓库根。本稿的 Rust 测试片段是实施目标，imports 从对应 contract prelude 引入；不是声称现在可编译的补丁。[接口清单](plan/interfaces.md) 定义 fixture 及伪时钟行为，不能用固定返回值实现 fixture 来“满足”断言。

## 5. Spec 覆盖矩阵

| Spec／AC | 负责任务 |
| --- | --- |
| §1–2 范围／不变量 | T01、T08、T11、T20 |
| §3 开关／资格／并发／Card | T06、T07、T16、T19；AC-06–12 |
| §4 认证／client／轮换／隔离 | T01、T03–05、T14、T17–18；AC-01–05 |
| §5 wire／Parts／wait／list／扩展 | T01、T08、T14、T17–18；AC-15、26–29 |
| §6 幂等／事务／占用 | T07–08；AC-13–15 |
| §7 输入／manager／状态机 | T09–11；AC-16–19 |
| §8 终态／取消／恢复／运维 | T11–13；AC-20–23 |
| §9 表／事务／Artifact／清理 | T02、T07、T11、T14–15；AC-24、30、32–33 |
| §10 投影／快照高水位／SSE | T11、T14、T18；AC-25–27 |
| §11 错误 | T01、T08、T12、T17–18；AC-02、10、14–15、20–21、26、29 |
| §12 参数／预算／读取成本 | T01、T07、T13–15、T18、T20；AC-22、27–28、32 |
| §13 边界／传播 | T01、T06、T19–20；AC-31、34 |
| §14 迁移／交付／回退 | T02、T16、T19–20；AC-11–12、32、34 |
| §15 全部 34 项验收 | T20 的汇总矩阵；各项还须通过对应任务的真实测试 |
| §16 验证记录 | 本计划 §6；实施后 T20 更新 delivery，而非覆盖原研究记录 |

## 6. 本次计划的验证与限制

本次只进行源路径／类型／测试入口核对和文档校验；没有实现、运行未来测试或修改 Cargo／SQL／服务配置。任务中的 Expected PASS 仅描述未来目标。

本次实际文档校验：

| 项目 | 结果 |
| --- | --- |
| 计划文件／本地链接 | 8 份计划文档，28 个相对链接目标存在 |
| 任务结构 | T01–T20 共20项、136个未勾选实施步骤；各项包含接口、测试代码、RED/GREEN命令及提交步骤 |
| 源路径 | 85次Modify引用定位到既有文件，7次定位到前序任务明确创建文件；154次Create引用无已有文件冲突（重复引用按出现次数计） |
| 测试入口 | 42次显式Rust `--test`目标引用，对应既有或任务声明创建的测试文件 |
| Spec覆盖 | AC-01–34全部映射到任务；接口清单补齐执行输入、停止证据、认证profile和公开Part属性 |
| 文档结构 | 代码围栏闭合，无未完成占位项；不会将Expected PASS标成已经运行 |

以上为临时文档检查脚本与人工接口复核的结果，不是未来代码编译、迁移或测试通过。提交前 `git diff --cached --check` 与 `git diff --check` 通过；变更仅为Markdown计划和导航，不含功能代码。

本计划尚未执行，spec 保持 proposed。下一步由用户选择逐任务同会话执行或子 Agent 驱动执行；在选择之前不建实施 worktree、不修改运行代码、不推送。
