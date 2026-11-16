# Group as an Agent：来源、代码证据与设计差异

日期：2026-11-16。本文是 [spec](spec.md) 的研究与验证记录，不是运行诊断或已上线能力声明。

## 1. 固定输入版本

| 输入 | 版本／读取范围 |
| --- | --- |
| BCS 工作分支基线 | `3b1b3859b32b3acf20888b9ad20ba6d32a047b26`，由拉取后的 origin/dev 创建 `docs/bcs-group-as-agent-spec` |
| 调研 PR #3979 | `387cd5c32a4d7c391a298ce6dfc3164cf3c764ab`；读取 overview.md、spec.md、next-discussion.md、examples.md 的协议样例，并核对 design-review.html 的执行／身份／产物段落 |
| PR 公开讨论 | GitHub issue comments、reviews、inline comments 查询未发现内容；不能把 HTML 本地评论存储推断成已读取的团队意见 |
| 原调研的代码基线 | `9a61798cb7752308aaa01f8e59d78a46809066cb`；不直接视为当前分支事实 |
| A2A 规范 | tag v1.0.1，提交 `3303592588e388e62e0f69f701af531d2f4e3991`；读取官方 docs/specification.md 与 specification/a2a.proto |

网页抓取未返回可用正文，协议依据通过 GitHub API 读取上述固定 tag 的官方仓库文件，不以搜索摘要、第三方 SDK 或对最新版的记忆代替。该记录只说明使用的协议版本，不宣称它是最新版本。

外部引用：

- [原 PR](https://github.com/inclusionAI/Avernet/pull/3979)。
- [固定版调研 spec](https://github.com/inclusionAI/Avernet/blob/387cd5c32a4d7c391a298ce6dfc3164cf3c764ab/apps/bcs/specs/2026-11-05-group-as-agent/spec.md)。
- [固定版讨论稿](https://github.com/inclusionAI/Avernet/blob/387cd5c32a4d7c391a298ce6dfc3164cf3c764ab/apps/bcs/specs/2026-11-05-group-as-agent/next-discussion.md)。
- [官方 proto](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/specification/a2a.proto)。
- [JCS 数字／字符串与确定性排序规范](https://www.rfc-editor.org/rfc/rfc8785)。
- [官方规范源](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/specification.md)。

## 2. Brainstorming 决策轨迹

| 问题 | 收敛结论 |
| --- | --- |
| 调用方是否只限 Agent／Bot | 不限；包括应用系统，不要求 Bot 注册 |
| 认证是否硬编码 | 统一可配置插件链，首期 BCS 签发 token 的 Bearer 方式 |
| 是否按调用方限制 Group | 首期不做，不增加 Group 白名单／Group-scoped grants |
| 是否支持无鉴权 | 曾讨论显式无鉴权；明确其无法提供可信调用方隔离后，用户撤回，**首期不支持** |
| 哪些群开放 | public 前置条件，加独立 supportA2A 开关；开启校验类型、无 Human 成员等 |
| Card 注册条件 | supportA2A 开启且资格通过才对外发布／注册 |
| 执行方案 | 复用既有执行，新增持久化 A2A 调用层；不重建引擎、不只包装 Session |
| 轮次／幂等／完成／取消恢复 | 用户确认每轮新 Task、busy 拒绝、先去重、显式完成、原子产物、停止确认和隔离恢复 |
| 关闭开关 | 用户确认拒绝新轮次、撤销 Card，已受理任务继续执行并允许原调用方查询／取消 |

通用 client 凭据的 opaque 实现、管理 CLI、输出选择字段、物理表布局、资源上限和建议默认值由本稿提出，需书面评审；不能混同为用户逐项已经确认，也不把它们写成现有能力。

## 3. 当前实现证据与必须补齐的差距

| 证据 | 当前事实 | 对本 spec 的影响 |
| --- | --- | --- |
| [Group domain](../../crates/contracts/bcs-domain/src/group.rs) | 有 public/private、GroupStrategy、actor_kind；没有 supportA2A；legacy ParticipantKind 仅 Bot | 新字段、迁移、序列化、所有写入口资格校验；Human 用 actor_kind 判定 |
| [Group application](../../crates/application/v1/bcs-app-group/CONTEXT.md)、[Group API](../../crates/service-api/bcs-service-api/src/application/v1/group.rs) | 管理授权与创建／变更用例已有 | 开关沿用管理权限，不给调用 token 群管理权 |
| [Group Core](../../crates/services/bcs-group/src/core/group_core.rs) | 有 upsert、mutate、成员和 visibility 等多条写路径，已有 Provider scope 约束 | 不能只改一个 HTTP PATCH；新公开资格不绕过旧 Provider 约束 |
| [Card snapshot](../../crates/services/bcs-group-store/src/card_snapshot.rs)、[Card sync](../../crates/services/bcs-fusion/src/core/card_sync.rs) | 资格 normal/public/active/支持类型；没有 supportA2A，也不读成员／流程 | 同步合同需要显式演进，资格在写入时维护；读取成本不扩成全量扫描 |
| [目录合同](../../docs/bcsfuse-catalog.md) | 队列内存持有、有限重试、不做启动全量扫描 | 不能当成可靠撤销证明；A2A 增加持久化意图与显式有界目录核对，不更改 Bot 发布策略 |
| [AuthPluginChain](../../crates/plugin-api/bcs-auth-api/src/chain.rs)、[AuthPrincipal](../../crates/plugin-api/bcs-auth-api/src/types.rs) | 插件与链已有；主体以 Human/Bot 为中心；全链无命中可返回匿名 | 复用扩展基础但补通用 client 主体；A2A 的强制认证不得继承匿名结果 |
| [现有 Bearer 插件](../../crates/plugins/bcs-auth-session/src/lib.rs)、[auth wiring](../../crates/bootstrap/bcs/src/auth_wiring.rs) | non-JWT token 主要通过 BotLookupPort 解析；默认链由构建配置决定 | 不能声称已支持通用应用 token；A2A 显式配置，不继承 debug local |
| [CallerContext](../../crates/service-api/bcs-service-api/src/application/principal.rs) | 已有 IntegrationClient(client_id, scopes) | 可复用主体概念；不是已有通用凭据签发／认证的证据 |
| [Session domain](../../crates/contracts/bcs-domain/src/session.rs)、[Session Repo](../../crates/service-api/bcs-service-api/src/port/repo/session.rs) | ServiceInvocation、caller_principal、activation、精确 CAS 已有；输出仍是 Session 当前值 | 独立 Task／Artifact，原子组合受理及托管入口保护需要新增 |
| [旧服务调用](../../crates/adapters/http/bcs-http/src/routes/services.rs) | 创建／reactivate 后才启动 Run／通知，返回旧 202；认证有独立历史逻辑 | 不能直接包一层获得原子 A2A 受理或沿用旧认证兜底 |
| [task.complete](../../crates/services/bcs-message-flow/src/task_flow.rs)、[support](../../crates/services/bcs-message-flow/src/task_flow/support.rs) | 有 manager／待办检查、summary；存在无精确 activation 的完成和查最新 Session 路径 | A2A 托管路径需独立关联、完成 ack/退出分离，禁止 latest fallback |
| [Runtime start](../../crates/services/bcs-collaboration-runtime/src/runtime/start.rs)、[completion](../../crates/services/bcs-collaboration-runtime/src/runtime/completion.rs) | 有 Run 输入和取消；取消写 Aborted 并完成 Session，不足以证明所有下游停止 | 新轮输入／结果选择／完整停止证据／隔离需补齐，不直接把 Aborted 映射为安全 CANCELED |
| [Runtime rerun](../../crates/services/bcs-collaboration-runtime/src/runtime/rerun.rs)、[recovery](../../crates/services/bcs-collaboration-runtime/src/runtime/recovery.rs) | 失败重跑和恢复已有自己的用途 | 正常 A2A 续聊创建新 Run；不重置原任务预算或开放原生 rerun 旁路 |
| [Event Store](../../crates/services/bcs-event-store/CONTEXT.md)、[事务 API](../../crates/plugin-api/bcs-db-api/src/transaction.rs) | 已有原子事件／组合事务片段模式 | 组合事务有实现基础，但 A2A 的跨记录原子语义仍是新增工作 |
| [V1 HTTP boundary](../../crates/adapters/http/bcs-api-http/CONTEXT.md) | 已拥有 /api/v1/collaboration/a2a 的管理路由，现用 V1 envelope | 新标准数据面归此 adapter，标准响应与管理响应分开；原稿把新路由一律放 bcs-http 不再沿用 |
| [Session policy retirement](../../specs/2026-10-27-retire-service-session-policies/spec.md) | 旧 service_spec、timeout scanner、callback 已退役 | 新 Task deadline 不恢复旧机制 |
| [Session delete](../../docs/session-delete-protocol.md) | Bot-local delete 只操作精确 Session，不是整个 BCS 群删除 | 不把此 RPC 当作整轮停止证明或跨资源清理接口 |

以上都是固定版本的静态源／合同观察，不是线上数据，不包含私有端点、凭据或运行记录。

## 4. 官方协议交叉检查

- v1.0.1 proto 的 SecurityScheme 使用 `httpAuthSecurityScheme` 分支，SecurityRequirement 使用 `schemes` 映射及 StringList；样例不采用旧版扁平 `type/security` 格式。
- SendMessageResponse 与 StreamResponse 都是明确的 payload union；流式样例使用 task/statusUpdate/artifactUpdate，不使用旧的 kind/final 字段。
- returnImmediately 默认 false，必须等待终态或中断态；本实现等待上限到期用明确错误而非成功返回 WORKING。
- ListTasks 的 pageSize 默认 50、最大 100；返回 totalSize；statusTimestampAfter 为大于等于，非严格大于。
- CancelTask 尝试取消并返回更新状态，不要求取消响应等于已经停止；终态取消和终态新 subscribe 均有标准错误。
- Part.data 标准可表示任意 JSON；本稿 object-only 是**输入范围**，不是声称标准只允许对象。
- HTTP+JSON 错误使用 error.code/status/message/details，标准 ErrorInfo reason 为去除 Error 后缀的 UPPER_SNAKE_CASE，domain 为 a2a-protocol.org。
- group 单独 Card URL、busy 错误、去重保留、同步等待上限、公开进度和 supportA2A 是 BCS 的明确选择，不伪称为 A2A 标准强制要求。

## 5. 文档检查与未验证项

文档检查包括：修改范围与空白检查、相对链接／本地锚点、Markdown 代码块、JSON／SSE 样例解析、A2A 关键字段和本稿相互一致性。实际执行结果：

| 检查 | 结果与边界 |
| --- | --- |
| 文档相对链接 | 41 个（含新增导航入口）目标存在；本次无本地锚点链接；未全量验证外部网络地址 |
| JSON / SSE | 13 个 JSON 代码块、4 个 SSE data 帧解析成功；无重复 JSON key |
| 协议对象形状 | 按固定官方 proto 提取字段／required／oneof，52 个嵌套协议对象通过轻量结构核对；不是完整生成客户端互通测试 |
| 配置示例 | 1 个 TOML 代码块解析成功；不是已经实现的配置 loader 测试 |
| 文档自检 | 无未完成占位项、冲突标记、未闭合代码围栏或行尾空白；补齐独立 SSE 认证复验、组合 Session 完成、逻辑删除与物理回收区别 |
| Git 范围 | 仅 3 个新 Markdown 和现有 spec 索引；没有 Rust／schema／迁移／运行配置变更 |

上述轻量检查在本地临时脚本中执行，不向仓库新增一次性校验工具。`git diff --check` 和新增文档的空白检查通过；完整运行时验收仍由 spec 的 AC 矩阵定义。

未运行 Rust workspace、BCS 服务／Singlebox、真实 MySQL、迁移升级、客户端互通、HTTP/SSE 网关或大规模负载测试；本次没有实现代码或迁移可供验证。未来验收矩阵不能记为已通过。扩展发布路径和建议 API／配置／表名不代表现在可访问。

文档提交主题：`docs(bcs): specify group A2A admission and lifecycle`；提交定位以 Git 历史为准，避免将自身尚未生成的 hash 写入文件。没有推送、创建新 PR、合并、host 依赖升级或部署。此处保留的是源版本、设计与文档验证范围，而非任何环境的运行状态。
