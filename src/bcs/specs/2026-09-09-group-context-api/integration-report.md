# Group Context Integration Report

- **Date:** 2026-09-21
- **Branch:** `feature/group-context-bcn-integration`
- **Status:** Phase 1 complete (noop implementation + tool registration + E2E smoke passed)

---

## 1. 架构概要

Group Context API 的分工：

```
LLM (群聊中的 bot)
  │
  │  "请调用 bcs_group_context_status"  ← LLM 决定 WHEN 和 WHAT 参数
  ▼
BCN Plugin (TypeScript, 进程内)
  │  注册 4 个 tool → OpenClaw agent 的 tool schema
  │  自动填充 origin（group_id/session_id/run_id/actor_id）
  │  → POST /groupcontext/{status|createByTemplate|updateContent|retrieve}
  ▼
BCS HTTP API (Rust)
  │  actor_id = bot_uuid_from_headers()  ← 从 Bearer token 解析
  │  Application → Core → Repo 四层调用
  │  → NoopGroupContextRepo（Phase 1: 空读 / 写报错）
  ▼
(Phase 2+) BcsGroupContextStore → SQLite / MySQL
```

**关键设计决策**：origin 参数（group_id, session_id, run_id, actor_id）由 BCS 框架注入，LLM 不可操控。LLM 只提供业务参数（domain, key, content, query）。

---

## 2. BCS 侧实现（Rust）

遵循四层架构：Domain → Port → Core → Application。

### 2.1 Domain Types（`bcs-domain/src/group_context.rs`）

- `Origin` — 数据来源证明（tenant_id, group_id, session_id, run_id, actor_id）
- `ContextEntry` — 单条上下文条目（数据面 + 策略面 + 治理面）
- `PolicyTemplate` — 策略模板（含 Flow、VisibleTo、CollectFrom、Consistency、Granularity、FreshnessClass、Permission）
- `ContextView` — 状态查询结果（contexts + templates）
- `TemplateView` — 模板视图
- `RetrievalItem` — 检索结果条目

### 2.2 Repo Port（`bcs-service-api/src/port/repo/group_context.rs`）

`GroupContextRepoPort` trait，10 个方法：
- 读：`status`, `retrieve`, `get_template`, `list_templates`
- 写：`create_by_template`, `update_content`, `supersede`
- 审计：`audit_log`, `get_audit_entry`

### 2.3 Core Trait（`bcs-service-api/src/core/group_context.rs`）

`GroupContextCoreService` trait，4 方法：
- `status(origin, filter)` → `Vec<ContextEntry>`
- `create_by_template(origin, template_id, domain, key, content)` → `ContextEntry`
- `update_content(origin, entry_id, new_content, expected_version)` → `SupersedeResult`
- `retrieve(origin, query, limit)` → `Vec<RetrievalItem>`

### 2.4 Application Trait（`bcs-service-api/src/application/group_context.rs`）

`GroupContextService` trait + 请求/响应 DTO：
- `StatusRequest` — 包含 `into_origin()` 方法（从 tenant_id/group_id/session_id/run_id/actor_id 构建 Origin）
- `StatusResponse` — 上下文列表 + 模板列表
- `CreateByTemplateRequest/Response`
- `UpdateContentRequest/Response`
- `RetrieveRequest/Response`

### 2.5 Noop 实现（`services/bcs-group-context/src/lib.rs`）

```
NoopGroupContextRepo:
  - status / retrieve → 空列表
  - create / update → Err(InvalidOperation, "not supported")
  - templates → 空列表

GroupContextCore:
  - 委托给 NoopGroupContextRepo

GroupContextApplication:
  - 委托给 GroupContextCore
  - 处理 DTO ↔ domain type 转换
```

### 2.6 HTTP Route（`bcs-http/src/routes/group_contexts.rs`）

当前仅暴露 `POST /groupcontext/status`：

```rust
// actor_id 从 Bearer token 解析，不从 body 取
let actor_id = bot_uuid_from_headers(req.headers())?;
let origin = req.into_origin(actor_id);
let result = app.status(origin, req.domain, req.limit).await?;
```

### 2.7 Bootstrap（`bcs/src/http_adapter.rs`）

```rust
let group_context_repo = Arc::new(NoopGroupContextRepo);
let group_context_core = Arc::new(GroupContextCore::new(group_context_repo));
let group_context_application = Arc::new(GroupContextApplication::new(group_context_core));
// 注入 HttpAppState
```

### 2.8 Contract Tests

`bcs-http/tests/group_context_status_contract.rs` — 3 个测试：
| 测试 | 结果 |
|---|---|
| status 返回 200 + 空列表 | ✓ |
| 无 auth header 返回 401 | ✓ |
| 请求 body 带 session_id/run_id 正常传递 | ✓ |

---

## 3. BCN 侧实现（TypeScript）

### 3.1 新建文件：`group-context-handler.ts`（235 行）

4 个 tool schema + 4 个 handler：

| Tool | Purpose | Params (LLM-provided) |
|---|---|---|
| `bcs_group_context_status` | 查看上下文状态 | domain?, limit? |
| `bcs_group_context_create` | 按模板创建 | template_id, domain, key, content |
| `bcs_group_context_update` | 更新内容 | entry_id, new_content, expected_version? |
| `bcs_group_context_retrieve` | 检索上下文 | domain?, query?, limit? |

`bcsApiCall()` 辅助函数自动填充 origin：

```typescript
const groupId = resolveBcsGroupId(sessionKey);    // 从 Map 取
const sessionId = resolveBcsSessionId(sessionKey); // 从 Map 取
const runId = resolveActiveRunId(sessionKey);      // 从 Map 取
// actor_id 通过 Bearer token 由 BCS 解析
```

### 3.2 修改 `inbound-handler.ts`（+10 行）

```typescript
export function resolveBcsGroupId(sessionKey: string): string | undefined;
export function resolveBcsSessionId(sessionKey: string): string | undefined;
```

### 3.3 修改 `core.ts`（+100 行）

4 个 `api.registerTool()` 调用，对所有 BCS session 无条件激活：

```typescript
api.registerTool(
  (ctx) => {
    const { sessionKey, channel } = rememberSessionSandbox(ctx);
    if (channel !== 'bcs' || !sessionKey) return null;
    return { name: 'bcs_group_context_status', ... };
  },
  { name: 'bcs_group_context_status' },
);
```

### 3.4 Origin 来源（不可伪造）

| Origin 字段 | 来源 | 声明 |
|---|---|---|
| `tenant_id` | 固定值 `"default"`（第一期）| 部署级别常量 |
| `group_id` | `sessionKeyToGroupId` Map | BCS `chat.send` 帧下发时写入 |
| `session_id` | `sessionKeyToBcsSessionId` Map | BCS `chat.send` 帧下发时写入 |
| `run_id` | `resolveActiveRunId(sessionKey)` | OpenClaw run tracker |
| `actor_id` | BCS `bot_uuid_from_headers()` | 从 Bearer token 解析 |

---

## 4. 测试方案

### 4.1 已通过的测试

#### A. Contract Tests（Rust）

```
cargo test --package bcs-http -- group_context_status_contract
→ 3 passed
```

#### B. TypeScript 编译

```
cd src/bcs/crates/plugins/openclaw-channel-bcn
npx tsc --noEmit
→ exit 0, zero errors
```

#### C. E2E 直连验证（Python）

```python
# WS bot.connect → HTTP onboard → HTTP create group → POST /groupcontext/status
Status: HTTP 200
Response: {"contexts": [], "templates": []}
No auth: HTTP 401
```

### 4.2 待执行的测试

#### D. BCN Tool Call 端到端（通过 singlebox）

通过 `singlebox.sh` 启动 2 bot 群聊，让 bot 在群中收到消息后通过 LLM 调用 `bcs_group_context_status`：

**步骤：**
1. `./scripts/singlebox.sh start all --profile-dir scripts/gc_2bots_profile`
2. 确认 bot 在线 → `bcs-cli list` 可见 `GC-Driver`、`GC-Member`
3. 建群 → `bcs-cli create-group`
4. 通过前端 `http://localhost:8000` 向群发消息：
   > "请调用 bcs_group_context_status 查看当前群上下文"
5. 验证点：
   - bot 回复中包含 tool call 结果（或调用失败的具体错误）
   - BCS 日志中出现 `/groupcontext/status` 请求
   - 前端群聊中可见回复

**预期（Phase 1）：**
- status 返回 `{ contexts: [], templates: [] }`（noop 实现）
- tool call 链路：LLM → BCN tool handler → BCS HTTP → NoopGroupContextRepo → response

**预期（Phase 2，有 store 实现后）：**
- create → 持久化条目 → status 可见 → retrieve 可见 → update 版本递增

### 4.3 测试文件位置

| 类型 | 路径 |
|---|---|
| Contract tests | `src/bcs/crates/adapters/http/bcs-http/tests/group_context_status_contract.rs` |
| E2E 脚本 | `/tmp/gc_test.py`（临时，连接 / 建群 / 调 status） |
| Bot profile | `scripts/gc_2bots_profile/`（2 bot 定义 + SOUL.md 告知调 tool） |

---

## 5. CLI 方案的问题

### 5.1 CLI 的现有模型

`bcs-cli` 是 bot 本地的一个命令行工具，通过 HTTP 调用 BCS。当前 bot 调用 CLI 的方式是：

```
LLM → tool call → bcs-cli <command> --token <token> --param1 val1
```

例如：`bcs-cli fuse --group <group_id> --token <token>`

**auth 模型**：`--token` 参数由 BCN 注入环境变量或由 LLM 从上下文获取。token 绑定 bot 身份，但**不绑定 session**。

### 5.2 CLI 对 Group Context 不适用

Group Context 的 `origin` 包含 5 个字段：

| 字段 | CLI 能否可信获取 | 问题 |
|---|---|---|
| `tenant_id` | ✓ | 部署常量，LLM 可自己知道 |
| `actor_id` | ✓（间接） | 通过 token → BCS 解析 actor_id |
| `group_id` | ✗ | **LLM 自己传参，可伪造** |
| `session_id` | ✗ | **LLM 自己传参，可伪造** |
| `run_id` | ✗ | **LLM 自己传参，可伪造** |

**与现有 CLI 命令的差异**：

- `bcs-cli fuse` — 传 `--group`，但 fuse 的后果仅限于当前 session 的提示词，不影响持久化数据。伪造 group_id 的后果：看到错误的融合上下文，但不会产生跨 session 的影响。
- `bcs-cli add-member` — 传 `--group`，但操作需要 group 内 bot 的授权，安全边界不在 group_id 本身。
- `bcs-cli group-context status` — 传 `--group`，但 group context 是**持久化存储**，读写的后果会跨 session 持续存在。恶意或错误的 group_id 可以：
  1. **读**：偷看另一个群的私有上下文（如游戏的底牌、内部投票结果）
  2. **写**：向另一个群的上下文注入虚假数据或覆盖已有状态
  3. **审计污染**：在另一个群的 audit log 中插入假记录

### 5.3 为什么不能用 token 绑定 group_id

Token 代表 bot 身份，一个 bot 可能同时存在于多个群。如果把 group_id 编码到 token 里：
- 需要为每个 bot-group pair 生成独立 token（爆炸性增长）
- group 生命周期不匹配 token 生命周期（group 创建/解散，token 不变）
- 当前 BCS 的 token 在 `bot.connect` 时生成，此时 bot 还不知道自己要进哪个群

**结论**：token 绑定 bot，不能绑定 group。

### 5.4 三种引擎的差异

| 引擎 | Group Context 访问方式 | 信任状态 |
|---|---|---|
| **OpenClaw（BCN plugin）** | Tool call → BCN handler → BCS HTTP，origin 由 BCN 填充 | ✅ 可信（Map 由 BCS frame 填充，LLM 无法操控） |
| **Claude Code（bcs-cli）** | CLI 子进程 → BCS HTTP，origin 由 LLM 传参 | ❌ 不可信（group_id/session_id/run_id 来自 LLM） |
| **DSH（bcs-cli）** | CLI 子进程 → BCS HTTP，origin 由 LLM 传参 | ❌ 不可信（同 Claude Code） |

### 5.5 让 CLI 可信的条件

CLI 要可信地调用 group context，需要引擎在 fork 子进程前通过**可信通道**注入 `group_id`/`session_id`/`run_id`：

| 注入方式 | 可否 | 风险 |
|---|---|---|
| **环境变量** | 可能 | 并发风险（多个 session 同时运行 → 环境变量互相覆盖） |
| **CLI 参数** | 可能 | 需要引擎启动子进程时感知当前 session context |
| **BCN tool（进程内）** | ✅ | 无并发风险，Map 隔离，当前实现 |

对于 Claude Code 和 DSH，**短期方案**是接受 CLI 路径不可信，只允许读操作（status/retrieve），拒绝写操作（create/update）除非 origin 通过额外校验。**长期方案**是让引擎实现 session-aware 的子进程上下文注入。

### 5.6 当前状态的开发量分析

| 引擎 | 已实现 | 额外开发量 |
|---|---|---|
| OpenClaw | BCN tool 4 个 | 0（已完成） |
| Claude Code | `bcs-cli group-context status`（尚不存在） | 1 次 CLI 命令开发 + 引擎注入开发 |
| DSH | `bcs-cli group-context status`（同上） | CLI 命令复用，仍需引擎注入 |

---

## 6. 下一步

1. **Store 实现**：从 `NoopGroupContextRepo` 变为 SQLite-backed `BcsGroupContextStore` — `store-plan.md`
2. **HTTP route 补齐**：`/groupcontext/createByTemplate`, `/updateContent`, `/retrieve`
3. **Policy template admin**：模板的 CRUD API 和管理界面
4. **CLI 注入方案**：确定 Claude Code/DSH 的 session context 注入机制
5. **生产环境测试**：真实 bot 在群聊中调用 tool 的全链路验证

---

## 附录：代码位置

| 组件 | 路径 |
|---|---|
| Domain types | `src/bcs/crates/contracts/bcs-domain/src/group_context.rs` |
| Repo port | `src/bcs/crates/service-api/bcs-service-api/src/port/repo/group_context.rs` |
| Core trait | `src/bcs/crates/service-api/bcs-service-api/src/core/group_context.rs` |
| App trait | `src/bcs/crates/service-api/bcs-service-api/src/application/group_context.rs` |
| Noop impl | `src/bcs/crates/services/bcs-group-context/src/lib.rs` |
| HTTP route | `src/bcs/crates/adapters/http/bcs-http/src/routes/group_contexts.rs` |
| HTTP state | `src/bcs/crates/adapters/http/bcs-http/src/state.rs` |
| Bootstrap | `src/bcs/crates/bootstrap/bcs/src/http_adapter.rs` |
| Contract tests | `src/bcs/crates/adapters/http/bcs-http/tests/group_context_status_contract.rs` |
| BCN handler | `src/bcs/crates/plugins/openclaw-channel-bcn/src/group-context-handler.ts` |
| BCN core | `src/bcs/crates/plugins/openclaw-channel-bcn/src/core.ts` |
| BCN inbound | `src/bcs/crates/plugins/openclaw-channel-bcn/src/inbound-handler.ts` |
| Bot profiles | `scripts/gc_2bots_profile/` |
| Spec | `src/bcs/specs/2026-09-09-group-context-api/spec.md` |
| Plan | `src/bcs/specs/2026-09-09-group-context-api/plan.md` |
| Store plan | `src/bcs/specs/2026-09-09-group-context-api/store-plan.md` |
| BCN integration (old) | `src/bcs/specs/2026-09-09-group-context-api/bcn-integration-plan.md` |