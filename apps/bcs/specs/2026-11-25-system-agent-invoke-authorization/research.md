# 基线、已核实事实与证据边界

## 1. 本次源码审查

日期：2026-11-25，Asia/Shanghai。本文件的“当前”仅指下列固定源码快照，不代表真实部署。

| 项 | 证据 |
| --- | --- |
| origin | `git@github.com:inclusionAI/Avernet.git` |
| 原 checkout | `/Users/shenlifang/work/codes/datamarket/teamclaw/Avernet` |
| 原 branch / HEAD | `dev` / `36c5f021ca3af75c0e6513cbdc9db89aeb7f42f9` |
| 原 dirty state | 已有 tracked E2E shell 修改与未跟踪报告/目录；全部保留，不以此树作基线 |
| 刷新 | `git fetch origin dev` 成功，将 origin/dev 从 `672804bbcd…` 更新到下列 SHA |
| 固定 dev SHA | `9a9aea2c3b5a0e5a0ed77c85e3790b74dca18bd8` |
| commit time | `2026-11-25T19:37:19+08:00` |
| 独立 worktree | `/Users/shenlifang/work/codes/datamarket/teamclaw/Avernet/.worktree/system-agent-invoke-authorization-spec` |
| task branch / 初始 HEAD | `codex/system-agent-invoke-authorization-spec` / 同一 dev SHA，创建时 clean |

创建前检查 `.worktree` 是真实目录而非 symlink、没有被 Git 跟踪、目标路径无冲突，确认本地 Git exclude 含 `/.worktree/`；随后 `git worktree add -b ... origin/dev`。没有切换/重置/清理主 checkout。后续只在专用 worktree 增加规范与导航。

## 2. 当前事实（不是目标规范）

以下路径相对于本 spec；审查基于上面的固定 commit。符号名比随后的行号更稳定，实施时必须重新核对。

| 源码锚点 | 核实到的行为 | 设计后果 |
| --- | --- | --- |
| [domain edge_permission.rs](../../crates/contracts/bcs-domain/src/edge_permission.rs) `GrantKind/EdgeGrant/RequestKind/AuthzGrantRef` | 已有四种 GrantKind；Owner/Manager 注释明确只作 Human→Bot authority metadata；RequestKind 为 connect/profile/rules/revoke | 不得沿用旧记忆中“只有两种 kind”的结论，新类型需独立 wire/DB mapping |
| [repo edge_grant.rs](../../crates/service-api/bcs-service-api/src/port/repo/edge_grant.rs) | list_active_grants 返回 Vec、is_authorized 返回 bool；friend paginated 是 fallible；注释称 any active edge | 必须与实现一起核实候选类型，bool 不携带 caller/operation/originator |
| [DB edge_grants.rs](../../crates/services/bcs-edge-permission-store/src/edge_grants.rs) `list_active_grants/is_authorized/insert_grant_sql` | SQL 白名单仅 profile/rules；query error 变空，坏行 filter_map 跳过；is_authorized=非空；upsert 更新 status 并复活 revoked 行 | 新 kind 不会自动被读取；新安全路径需 checked read；旧 upsert 不足以证明 revoke/replay 安全 |
| [Memory store](../../crates/services/bcs-edge-permission-store/src/memory.rs) `list_active_grants` | 同样只匹配 profile/rules、approved、from/to/env | 两实现必须同步，不以假 store 的预期代替真实 DB |
| [mapping.rs](../../crates/services/bcs-edge-permission-store/src/mapping.rs) `parse_grant_kind/grant_kind_str` | 4 种精确字符串；未知 DB kind 返回错误 | 新 enum 增加不等于旧读者兼容 |
| [permission request port](../../crates/service-api/bcs-service-api/src/port/repo/permission_request.rs) | insert/get/find/list/decide/backfill 为独立操作；部分返回 Option，list 已 fallible | 不能简单串接就声称批准+edge+audit 原子；需 dedicated transaction contract |
| [admission.rs](../../crates/services/bcs-edge-permission/src/admission.rs) `check_admission/build_authz_context` | 前者调用 bool 后合成 default-profile ref；originator 目前被忽略；后者直接将候选边构成 refs，旧 ref 的语义因 kind 而异 | 单改 enum/store 白名单会扩大权限并泄漏未校验 context；需显式 system operation 分支且隔离旧消费者 |
| [connect lifecycle](../../crates/services/bcs-edge-permission/src/connect/lifecycle.rs) revoke | 通过 PermissionProfile kind + default ref 选好友边 | 保留精确过滤，新增系统边不因 unfriend 被撤销 |
| [BCS HTTP router](../../crates/adapters/http/bcs-http/src/router.rs) | 注册 friend-connections lifecycle 和 GET /bots/{id}/admission，没有本提案的 system-invoke routes | 尚无可直接调用的系统授权公共生命周期 |
| [OpenAPI friend route](../../crates/adapters/http/bcs-api-http/src/v1/openapi/routes/friend_connection.rs) | 公开 friend request/list/accept/reject/cancel/revoke；不是一般 Rules/Profile/System 管理器 | 新资源不能冒充 friend 路由已支持 |
| [MySQL 014](../../migrations/mysql/014_edge_permission.sql) | 原四列 unique、VARCHAR(16)、历史注释不能代表演进后 schema | 必须追 migration chain，不改旧文件 |
| [MySQL 036](../../migrations/mysql/036_bot_authority.sql) | kind 扩到 VARCHAR(32)，unique 加 grant_kind，批准 owner 唯一生成列/authority 索引 | 新 kind 不需要进一步 widening，保持 owner invariant；后续索引应评估复用 |
| [MySQL 044](../../migrations/mysql/044_actor_identity_no_pad.sql) | 对已有部署的身份 collation 做 additive NO PAD 修复 | 新 store/查询不得大小写折叠、trim 或改变身份唯一性 |
| [bot agent registration](../../crates/services/bcs-bot-store/src/persistent_repo/agent_registration.rs) 与 [actor_ops](../../crates/services/bcs-bot-store/src/persistent_repo/actor_ops.rs) | 当前存在 AgentCode 查询与 legacy first-match/兼容读取 | 可以定位入口，但不能直接用它声称授权身份唯一、受信 |
| [repo conformance harness](../../crates/test-support/bcs-test-support/src/contract/repo/edge_grant.rs) | 已有共用 run_edge_grant_repo_contract；friend/default 的生命周期断言 | 未来扩展共用 suite，不另用单实现测试代替 conformance |
| [store CONTEXT](../../crates/services/bcs-edge-permission-store/CONTEXT.md) | 声明 SQL/infra 所有权、authority 事务和 MySQL conformance 实例 | 用现有安全模式分析命令，不把 authority store 私有实现直接引入 HTTP/core |

## 3. AgentCard 身份 Draft 的关系

本轮额外只读核对已有独立 worktree 中的 `docs/specs/2026-11-24-agentcard-identity-status/spec.md`（branch `codex/agentcard-identity-status-spec`）。它是工作中 Draft，不在本次 origin/dev 树中，不建立仓库内指向另一台机器 worktree 的链接，也不复制整份合同。

该 Draft 当前约定外部 `agent_id` → DB `agent_code`；新注册允许省略/null/空串，多 Bot 可共享，非空身份受保护，未来才定义 token 证明。本规范因此不要求把 agent_code 当全局唯一权键或强制回填 Card。身份可信解析和最终落库 Bot ID 是两个不同问题。相关草案仍需独立评审，不能据此声称 token/authz 已实现。

## 4. 明确未验证

- 未执行 Rust 单元/conformance/HTTP/store/E2E；没有 DB/schema 修改，文档检查不能替代这些。
- 未查询运行服务、部署配置、生产 inventory/授权数据、日志、真实 AgentPass 认证链。
- 未证明租户物理隔离、Provider 真实系统资格、OceanBase/MySQL 线上 schema 或 host 接入可用。
- 未创建/更新 PR、运行远端 CI、部署或生产试写。原始开发树的历史报告不作为本规范运行验收证据。
