# BCS / A2A 鉴权实现 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 2026-08-07 的 BCS / A2A 鉴权最终设计落到 Rust 后端、协议、存储、前端与测试中，使 A2A 运行时只消费统一 `grants[]` 引用，Bot 本地缓存可按 revision/digest 安全鉴权。

**Architecture:** 采用 BCS 现有分层：domain / protocol / service-api / services / store / adapters / bootstrap。授权事实由 `EdgeGrant` 统一承载，A2A 侧只传统一 `AuthzContext` 引用集合；Bot 本地只缓存 `PermissionProfile` 与 `RulesGrant` 的可执行内容，不缓存 EdgeGrant 作为主要鉴权材料。所有授权决策入口统一收敛到核心授权上下文构建服务，再由 A2A、group、bot event、resolve API 与前端申请流程复用。

**Tech Stack:** Rust workspace、MySQL migrations、SQLite/bootstrap migrations、HTTP/WS adapters、TypeScript frontend、Mermaid docs、repo conformance tests、protocol contract tests。

> **Bootstrap wiring note:** 在真实 DB-backed authz repo、default profile seed、申请/审批 API 尚未完整接入前，不要把空的 `MemoryAuthorizationStore` 接到生产 `A2aChat`。当前 foundation 的正确边界是：message-flow 提供 `with_authz_context_builder(...)` seam；测试中接入 fake builder 验证注入与 fail-closed；生产 enforcement 等 Task 5 / persistence wiring 完成后再打开。

---

## 文件结构与职责

### 新增 / 修改的核心文件

| 文件 | 职责 |
|---|---|
| `src/bcs/crates/contracts/bcs-domain/src/authorization.rs` | 授权领域类型：Capability、Rule、PermissionProfile、EdgeGrant、AuthzContext、Decision。 |
| `src/bcs/crates/contracts/bcs-domain/src/lib.rs` | 导出 authorization 模块。 |
| `src/bcs/crates/contracts/bcs-protocol/src/a2a.rs` | A2A wire DTO：统一 `grants[]` AuthzContext 扩展。 |
| `src/bcs/crates/contracts/bcs-protocol/tests/a2a_authz_context_contract.rs` | A2A wire contract tests。 |
| `src/bcs/crates/service-api/bcs-service-api/src/core/authorization.rs` | `AuthzContextBuilderCoreService` 等核心接口。 |
| `src/bcs/crates/service-api/bcs-service-api/src/application/authorization.rs` | PermissionProfile / Request / Resolve 应用接口。 |
| `src/bcs/crates/service-api/bcs-service-api/src/port/repo/authorization.rs` | Authorization repo traits。 |
| `src/bcs/crates/services/bcs-authorization/src/lib.rs` | 授权核心实现入口。 |
| `src/bcs/crates/services/bcs-authorization/src/core/authorization_core.rs` | AuthzContext 构建与决策逻辑。 |
| `src/bcs/crates/services/bcs-authorization-store/src/lib.rs` | authorization store 的 repo 实现入口。 |
| `src/bcs/crates/services/bcs-authorization-store/src/memory.rs` | 内存 repo 实现。 |
| `src/bcs/crates/services/bcs-authorization-store/tests/conformance_authorization_repo.rs` | repo conformance tests。 |
| `src/bcs/migrations/mysql/00xx_*.sql` | 新授权表 MySQL migration。 |
| `src/bcs/crates/bootstrap/bcs/src/migrations.rs` | SQLite/bootstrap migration 对齐。 |
| `src/bcs/crates/services/bcs-message-flow/src/a2a_chat/mod.rs` | A2A direct chat 注入 AuthzContext。 |
| `src/bcs/crates/services/bcs-message-flow/src/group_flow.rs` | collaboration context 授权注入。 |
| `src/bcs/crates/services/bcs-message-flow/src/bot_event.rs` | bot event / structured routing 授权注入。 |
| `src/bcs/crates/adapters/http/bcs-http/src/routes/*.rs` | 权限申请、审批、resolve、grant 查询 HTTP 入口。 |
| `src/bcs/crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs` | WS / bot runtime 相关授权上下文传递。 |
| `src/bcs/crates/bootstrap/bcs/src/server.rs` | wiring 新 service / repo / adapter。 |
| `src/frontend/src/pages/GroupChat/components/FriendModal.tsx` | connect + 可选权限申请 UI。 |
| `src/frontend/src/pages/GroupChat/components/BotInfoCard.tsx` | AgentCard + capability / profile 展示。 |
| `src/frontend/src/pages/GroupChat/components/CreateGroupModal.tsx` | 协作群组默认权限提示。 |
| `src/frontend/src/stores/friendStore.ts` | 请求与审批状态。 |
| `src/frontend/src/stores/botNetworkStore.ts` | Bot discovery / public / visibility 状态。 |
| `src/frontend/src/services/backend-api/*.ts` | 新 authz API client。 |

---

## Task 1: 定义授权领域与 A2A 协议合同

**Files:**
- Create: `src/bcs/crates/contracts/bcs-domain/src/authorization.rs`
- Modify: `src/bcs/crates/contracts/bcs-domain/src/lib.rs`
- Modify: `src/bcs/crates/contracts/bcs-protocol/src/a2a.rs`
- Create: `src/bcs/crates/contracts/bcs-protocol/tests/a2a_authz_context_contract.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/core/authorization.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/authorization.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/port/repo/authorization.rs`

- [ ] **Step 1: 写失败测试**

  在 `src/bcs/crates/contracts/bcs-protocol/tests/a2a_authz_context_contract.rs` 先写一个 JSON round-trip / schema-like 测试，断言 wire 里只有统一 `grants[]`，每个元素都带 `kind/ref_id/revision/digest/source`，并明确不允许 `permission_profiles[]` / `rules_grants[]`。

- [ ] **Step 2: 跑测试确认失败**

  Run: `cd src/bcs && cargo test -p bcs-protocol a2a_authz_context_contract -- --nocapture`

  Expected: fail，因为类型与 wire 结构还没定义。

- [ ] **Step 3: 写最小实现**

  在 domain 与 protocol 中先落最小类型：

  ```rust
  pub enum GrantKind {
      PermissionProfile,
      Rules,
  }

  pub struct AuthzGrantRef {
      pub kind: GrantKind,
      pub ref_id: String,
      pub revision: i64,
      pub digest: String,
      pub source: AuthzGrantSource,
  }

  pub struct AuthzContext {
      pub task_id: Option<String>,
      pub run_id: Option<String>,
      pub from_id: String,
      pub to_id: String,
      pub env: String,
      pub originator: Option<String>,
      pub context: AuthzRuntimeContext,
      pub grants: Vec<AuthzGrantRef>,
      pub issued_at: i64,
      pub expires_at: i64,
      pub signature: Option<String>,
  }
  ```

  同时在 `src/bcs/crates/service-api/bcs-service-api/src/core/authorization.rs` 定义：

  ```rust
  pub trait AuthzContextBuilderCoreService: Send + Sync {
      async fn build_a2a_authz_context(
          &self,
          request: BuildA2aAuthzContextRequest,
      ) -> ServiceResult<AuthzContext>;
  }
  ```

  在 `src/bcs/crates/service-api/bcs-service-api/src/port/repo/authorization.rs` 定义仓储接口：

  ```rust
  pub trait CapabilityCatalogRepoPort: Send + Sync { /* ... */ }
  pub trait PermissionProfileRepoPort: Send + Sync { /* ... */ }
  pub trait EdgeGrantRepoPort: Send + Sync { /* ... */ }
  pub trait PermissionRequestRepoPort: Send + Sync { /* ... */ }
  pub trait AuthzDecisionLogRepoPort: Send + Sync { /* ... */ }
  ```

- [ ] **Step 4: 跑测试确认通过**

  Run: `cd src/bcs && cargo test -p bcs-protocol a2a_authz_context_contract -p bcs-service-api -- --nocapture`

  Expected: 通过，wire 与 core/repo 接口稳定。

- [ ] **Step 5: 提交**

  ```bash
  git add src/bcs/crates/contracts/bcs-domain/src/authorization.rs \
          src/bcs/crates/contracts/bcs-domain/src/lib.rs \
          src/bcs/crates/contracts/bcs-protocol/src/a2a.rs \
          src/bcs/crates/contracts/bcs-protocol/tests/a2a_authz_context_contract.rs \
          src/bcs/crates/service-api/bcs-service-api/src/core/authorization.rs \
          src/bcs/crates/service-api/bcs-service-api/src/application/authorization.rs \
          src/bcs/crates/service-api/bcs-service-api/src/port/repo/authorization.rs
  git commit -m "feat(bcs): add authz domain and a2a grant contract"
  ```

---

## Task 2: 落地授权表与存储实现

**Files:**
- Modify: `src/bcs/migrations/mysql/001_init_schema.sql` 或新增 `00xx_authorization.sql`
- Modify: `src/bcs/crates/bootstrap/bcs/src/migrations.rs`
- Create: `src/bcs/crates/services/bcs-authorization-store/src/lib.rs`
- Create: `src/bcs/crates/services/bcs-authorization-store/src/memory.rs`
- Create: `src/bcs/crates/services/bcs-authorization-store/tests/conformance_authorization_repo.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`

- [ ] **Step 1: 写失败测试**

  在 `src/bcs/crates/services/bcs-authorization-store/tests/conformance_authorization_repo.rs` 写 conformance tests，覆盖：
  - `bcs_capability_catalog` upsert / list
  - `bcs_permission_profiles` revision/digest 递增
  - `bcs_edge_grants` connect / profile / rules 三类写入
  - `bcs_permission_requests` approve/reject 流程
  - `bcs_authz_decision_logs` 写入

- [ ] **Step 2: 跑测试确认失败**

  Run: `cd src/bcs && cargo test -p bcs-authorization-store conformance_authorization_repo -- --nocapture`

  Expected: fail，因为 store 与 migration 还没接好。

- [ ] **Step 3: 写最小实现**

  增加四张主表和一张审计表的 migration，并在 store 中实现 memory repo：
  - `bcs_capability_catalog`
  - `bcs_permission_profiles`
  - `bcs_edge_grants`
  - `bcs_permission_requests`
  - `bcs_authz_decision_logs`

  `bcs_edge_grants` 统一用 `grant_kind` 区分 `permission_profile` / `rules`，`permission_profile` 时 `rules = null`。

- [ ] **Step 4: 跑测试确认通过**

  Run: `cd src/bcs && cargo test -p bcs-authorization-store conformance_authorization_repo -- --nocapture`

  Expected: 通过，store 行为一致。

- [ ] **Step 5: 提交**

  ```bash
  git add src/bcs/migrations/mysql/001_init_schema.sql \
          src/bcs/migrations/mysql/00xx_authorization.sql \
          src/bcs/crates/bootstrap/bcs/src/migrations.rs \
          src/bcs/crates/services/bcs-authorization-store/src/lib.rs \
          src/bcs/crates/services/bcs-authorization-store/src/memory.rs \
          src/bcs/crates/services/bcs-authorization-store/tests/conformance_authorization_repo.rs \
          src/bcs/crates/bootstrap/bcs/src/server.rs
  git commit -m "feat(bcs): persist authz grants and requests"
  ```

---

## Task 3: 实现 AuthzContext 构建核心

**Files:**
- Create: `src/bcs/crates/services/bcs-authorization/src/lib.rs`
- Create: `src/bcs/crates/services/bcs-authorization/src/core/authorization_core.rs`
- Create: `src/bcs/crates/services/bcs-authorization/tests/authz_context_builder.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`

- [ ] **Step 1: 写失败测试**

  在 `src/bcs/crates/services/bcs-authorization/tests/authz_context_builder.rs` 写测试，分别覆盖：
  - connect approved 的 A→B / B→A default refs
  - direct profile grant
  - direct rules grant
  - public chat 补 target.default
  - collaboration 补 target.default
  - cache freshness proof 使用 revision/digest
  - repo 失败时 deny

- [ ] **Step 2: 跑测试确认失败**

  Run: `cd src/bcs && cargo test -p bcs-authorization authz_context_builder -- --nocapture`

  Expected: fail，因为 core service 还没实现。

- [ ] **Step 3: 写最小实现**

  实现 `AuthzContextBuilderCoreService`：
  1. 查询 `EdgeGrantRepoPort` 的 approved active grants。
  2. 根据 context 决定是否补 target.default。
  3. 加载 active `PermissionProfile` 的 current revision/digest。
  4. 组装统一 `grants[]`。
  5. 记录 `AuthzDecisionLog`。
  6. 任一步失败都返回 error，不做隐式 allow。

  代码里应保证：
  - `grant_kind=permission_profile` 只注入 `kind=permission_profile` 的 ref。
  - `grant_kind=rules` 只注入 `kind=rules` 的 ref。
  - public / collaboration 默认只补 `permission_profile`，且 source 分别为 `public_default` / `collaboration_default`。

- [ ] **Step 4: 跑测试确认通过**

  Run: `cd src/bcs && cargo test -p bcs-authorization authz_context_builder -- --nocapture`

  Expected: 通过。

- [ ] **Step 5: 提交**

  ```bash
  git add src/bcs/crates/services/bcs-authorization/src/lib.rs \
          src/bcs/crates/services/bcs-authorization/src/core/authorization_core.rs \
          src/bcs/crates/services/bcs-authorization/tests/authz_context_builder.rs \
          src/bcs/crates/bootstrap/bcs/src/server.rs
  git commit -m "feat(bcs): build runtime authz context"
  ```

---

## Task 4: 接入 A2A / group / bot event 授权链路

**Files:**
- Modify: `src/bcs/crates/services/bcs-message-flow/src/a2a_chat/mod.rs`
- Modify: `src/bcs/crates/services/bcs-message-flow/src/group_flow.rs`
- Modify: `src/bcs/crates/services/bcs-message-flow/src/bot_event.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-http/src/routes/bot_chat.rs`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs`
- Create: `src/bcs/crates/services/bcs-message-flow/tests/a2a_authz_integration.rs`

- [ ] **Step 1: 写失败测试**

  在 `src/bcs/crates/services/bcs-message-flow/tests/a2a_authz_integration.rs` 写 integration tests，覆盖：
  - direct chat 传入统一 `grants[]`
  - public chat 补 default grant ref
  - collaboration 补 default grant ref
  - 缺少授予时拒绝投递
  - 过期 AuthzContext 不继续投递

- [ ] **Step 2: 跑测试确认失败**

  Run: `cd src/bcs && cargo test -p bcs-message-flow a2a_authz_integration -- --nocapture`

  Expected: fail，因为 message-flow 还没接 core authz service。

- [ ] **Step 3: 写最小实现**

  在 `a2a_chat` / `group_flow` / `bot_event` 里统一调用 `AuthzContextBuilderCoreService`，不要在 adapter 内写策略：
  - HTTP / WS adapter 只提取 caller / bot / run / group 上下文。
  - message-flow 调用核心 authz service。
  - 传给 Bot 的只是一份 AuthzContext，里面只有统一 `grants[]`。

- [ ] **Step 4: 跑测试确认通过**

  Run: `cd src/bcs && cargo test -p bcs-message-flow a2a_authz_integration -- --nocapture`

  Expected: 通过。

- [ ] **Step 5: 提交**

  ```bash
  git add src/bcs/crates/services/bcs-message-flow/src/a2a_chat/mod.rs \
          src/bcs/crates/services/bcs-message-flow/src/group_flow.rs \
          src/bcs/crates/services/bcs-message-flow/src/bot_event.rs \
          src/bcs/crates/adapters/http/bcs-http/src/routes/bot_chat.rs \
          src/bcs/crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs \
          src/bcs/crates/services/bcs-message-flow/tests/a2a_authz_integration.rs
  git commit -m "feat(bcs): inject runtime authz into message flow"
  ```

---

## Task 5: 实现申请 / 审批 / resolve API

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-http/src/routes/*.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/authorization.rs`
- Modify: `src/bcs/crates/services/bcs-authorization/src/lib.rs`
- Create: `src/bcs/crates/services/bcs-authorization/tests/permission_request_flow.rs`
- Create: `src/bcs/crates/services/bcs-authorization/tests/resolve_contract.rs`

- [ ] **Step 1: 写失败测试**

  写两组测试：
  1. `permission_request_flow.rs`：connect / permission_profile / rules 的申请、审批、生成 EdgeGrant。
  2. `resolve_contract.rs`：Bot runtime 根据 `kind + ref_id + revision + digest` 拉取 profile/rules 内容。

- [ ] **Step 2: 跑测试确认失败**

  Run: `cd src/bcs && cargo test -p bcs-authorization permission_request_flow resolve_contract -- --nocapture`

  Expected: fail，因为 API 还没实现。

- [ ] **Step 3: 写最小实现**

  实现以下用例：
  - `POST /authz/permission-requests`
  - `POST /authz/permission-requests/:id/approve`
  - `POST /authz/permission-requests/:id/reject`
  - `GET /authz/edge-grants`
  - `POST /authz/resolve/permission-profile`
  - `POST /authz/resolve/rules-grant`

  Resolve API 必须只面向目标 Bot runtime principal 或 BCS 认可的调用方，不允许任意查询别人权限内容。

- [ ] **Step 4: 跑测试确认通过**

  Run: `cd src/bcs && cargo test -p bcs-authorization permission_request_flow resolve_contract -- --nocapture`

  Expected: 通过。

- [ ] **Step 5: 提交**

  ```bash
  git add src/bcs/crates/adapters/http/bcs-http/src/routes/*.rs \
          src/bcs/crates/service-api/bcs-service-api/src/application/authorization.rs \
          src/bcs/crates/services/bcs-authorization/src/lib.rs \
          src/bcs/crates/services/bcs-authorization/tests/permission_request_flow.rs \
          src/bcs/crates/services/bcs-authorization/tests/resolve_contract.rs
  git commit -m "feat(bcs): add authz request and resolve apis"
  ```

---

## Task 6: 接入前端产品面

**Files:**
- Modify: `src/frontend/src/pages/GroupChat/components/FriendModal.tsx`
- Modify: `src/frontend/src/pages/GroupChat/components/BotInfoCard.tsx`
- Modify: `src/frontend/src/pages/GroupChat/components/CreateGroupModal.tsx`
- Modify: `src/frontend/src/stores/friendStore.ts`
- Modify: `src/frontend/src/stores/botNetworkStore.ts`
- Modify: `src/frontend/src/services/backend-api/BcnController.ts`
- Modify: `src/frontend/src/services/backend-api/ActorController.ts`
- Modify: `src/frontend/src/services/backend-api/BotController.ts`

- [ ] **Step 1: 写失败测试或组件断言**

  为好友弹窗、Bot 信息卡和群组创建弹窗补充最小前端测试或组件断言，覆盖：
  - connect 表单
  - optional permission profile picker
  - optional rules picker
  - grant / request 状态展示

- [ ] **Step 2: 跑测试确认失败**

  Run: `cd src/frontend && pnpm test -- --runInBand`

  Expected: fail，因为 UI 还没有连到新 API。

- [ ] **Step 3: 写最小实现**

  前端至少支持：
  - Bot discovery / AgentCard 展示
  - connect 申请
  - 第二步可选申请 PermissionProfile / rules
  - request inbox / grant viewer 的基础状态
  - group 创建时提示协作默认权限是 runtime 补充，不是永久边

- [ ] **Step 4: 跑测试确认通过**

  Run: `cd src/frontend && pnpm test -- --runInBand`

  Expected: 通过。

- [ ] **Step 5: 提交**

  ```bash
  git add src/frontend/src/pages/GroupChat/components/FriendModal.tsx \
          src/frontend/src/pages/GroupChat/components/BotInfoCard.tsx \
          src/frontend/src/pages/GroupChat/components/CreateGroupModal.tsx \
          src/frontend/src/stores/friendStore.ts \
          src/frontend/src/stores/botNetworkStore.ts \
          src/frontend/src/services/backend-api/BcnController.ts \
          src/frontend/src/services/backend-api/ActorController.ts \
          src/frontend/src/services/backend-api/BotController.ts
  git commit -m "feat(frontend): expose authz request and grant flows"
  ```

---

## Task 7: 补齐 E2E、协议与迁移验证

**Files:**
- Modify: `src/bcs/crates/contracts/bcs-protocol/tests/a2a_authz_context_contract.rs`
- Modify: `src/bcs/crates/services/bcs-authorization-store/tests/conformance_authorization_repo.rs`
- Modify: `src/bcs/crates/services/bcs-message-flow/tests/a2a_authz_integration.rs`
- Modify: `src/bcs/crates/services/bcs-authorization/tests/*.rs`
- Modify: `src/bcs/migrations/mysql/00xx_*.sql`
- Modify: `src/bcs/crates/bootstrap/bcs/src/migrations.rs`

- [ ] **Step 1: 写失败的回归测试清单**

  补齐以下场景：
  - `grants[]` wire contract round-trip
  - public bot 不落 pairwise default edge
  - collaboration 不做 N² 边
  - profile update 后新消息走新 revision/digest
  - cache miss / digest mismatch / resolve failure deny
  - migration 与 store 行为一致

- [ ] **Step 2: 跑测试确认失败**

  Run: `cd src/bcs && cargo test --workspace -- --nocapture`

  Expected: 早期阶段会失败，直到前面任务全部接完。

- [ ] **Step 3: 写最小收口修复**

  修复所有 contract / repo / integration / migration 差异，保证最终状态下：
  - A2A 只传统一 `grants[]`
  - repo 写失败不会吞掉
  - 所有新增表在 MySQL / SQLite bootstrap 一致
  - BCS 运行时和 Bot 本地都 fail closed

- [ ] **Step 4: 跑全量验证**

  Run: `cd src/bcs && cargo test --workspace -- --nocapture`

  Expected: 全部通过，且新授权路径没有回归。

- [ ] **Step 5: 提交**

  ```bash
  git add src/bcs/crates/contracts/bcs-protocol/tests/a2a_authz_context_contract.rs \
          src/bcs/crates/services/bcs-authorization-store/tests/conformance_authorization_repo.rs \
          src/bcs/crates/services/bcs-message-flow/tests/a2a_authz_integration.rs \
          src/bcs/crates/services/bcs-authorization/tests/*.rs \
          src/bcs/migrations/mysql/00xx_*.sql \
          src/bcs/crates/bootstrap/bcs/src/migrations.rs
  git commit -m "test(bcs): harden authz contract and migrations"
  ```

---

## Coverage Check

这份 plan 覆盖了 spec 中的全部核心要求：

| Spec 章节 | 对应 Task |
|---|---|
| 2 / 3 领域模型 | Task 1 |
| 4 / 5 授权事实与数据表 | Task 2 |
| 6 A2A AuthzContext 协议 | Task 1 / 7 |
| 7 BCS runtime 构建 | Task 3 |
| 8 Bot 本地 cache / resolve | Task 3 / 5 / 7 |
| 9 Tool 鉴权语义 | Task 1 / 3 / 7 |
| 10 申请 / 审批流程 | Task 5 / 6 |
| 11 public bot | Task 3 / 4 / 7 |
| 12 collaboration group | Task 3 / 4 / 7 |
| 13 PermissionProfile 更新语义 | Task 3 / 5 / 7 |
| 14 BCS 代码落点 | 全部任务 |
| 15 API 设计 | Task 5 |
| 16 Frontend / Product | Task 6 |
| 17 Agent Card 对齐 | Task 5 / 6 |
| 18 测试矩阵 | Task 1 / 2 / 3 / 4 / 5 / 6 / 7 |
| 19 架构约束 | 全部任务 |
| 20 实现拆分建议 | 本 plan 本身 |
| 21 核心不变量 | Task 1 / 3 / 4 / 5 / 7 |
| 22 Future Work | 留白，不进入 MVP |

---

## Execution choice

Plan complete and saved to `docs/superpowers/plans/2026-08-07-bcs-a2a-authz-implementation-plan.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
