# 边权限 Connect → `bcs_friendships` 反向同步设计

> 状态：已评审定稿（2026-10-15，方案 A）
> 前置背景：`2026-08-18-friend-edge-permission-reform.md`（好友关系 → 边权限改造，过渡期共存）

## 1. 背景

现状两套好友存储只有**单向**同步：

- 旧路径（`POST /friends/request` + accept → `FriendCore.add_friendship`）写
  `bcs_friendships`，并通过 `with_edge_permission_sync`（bootstrap `server.rs:4683`）
  双写到 `edge_grants`。
- 边权限路径（`/collaboration/friend-connections/*` → `DbConnectService`）只写
  `edge_grants` + `permission_requests`，**不回写** `bcs_friendships`。

而 Bot↔Bot 协作建群闸门尚未切读边权限：

- `bcs-group` `ensure_v1_reachable`（`guards.rs:115`）→ `FriendCoreService::try_are_friends`
  → `DbFriendStore::are_friends`（`bcs_friendships` 表）。
- 结果：用户在边权限添加好友后建群仍报
  `403 Bot 'bot_xxx' is not friends with 'bot_yyy'`。

## 2. 目标

边权限 connect 的 approve/auto-approve/revoke 事件与 `bcs_friendships` 保持一致，
使协作建群闸门在过渡期读到与边权限一致的好友关系。

## 3. 非目标（YAGNI）

- 不加配置开关（正确性修复，回滚 = revert）。
- 不写 Human→Actor 好友行到 `bcs_friendships`（用户在范围确认中明确排除）。
- 不补写 legacy `friend_requests` 表的 accepted 快照行。
- reject / cancel 不做任何写（无行可写）。
- 不切读闸门到边权限（那是改造的终态，另行评审）。

## 4. 方案（已选 A：独立双向写回端口）

否决项：

- **B（DbConnectService 注入 FriendCoreService）**：FriendCore 已持有
  DbConnectService（edge 正向同步），再反向持有形成 Arc 环，服务永不析构；
  且 `add_friendship` 会回调 `sync_add_friendship` 产生冗余环路。
- **C（HTTP 路由层补写）**：业务逻辑泄漏到 delivery 层，两个路由入口重复实现。

### 4.1 端口（`bcs-service-api::port`）

```rust
#[async_trait]
pub trait FriendshipWritebackPort: Send + Sync {
    /// 写入 legacy 好友行（bcs_friendships），Bot↔Bot。
    async fn add_friendship(&self, caller: &str, target: &str) -> ServiceResult<()>;
    /// revoke 后删除 legacy 好友行。
    async fn remove_friendship(&self, caller: &str, target: &str) -> ServiceResult<bool>;
}
```

### 4.2 实现（`bcs-friend` 新类型 `FriendshipWritebackService`）

- 构造：`Arc<dyn FriendRepoPort>` + `Arc<dyn RelationCoreService>`。
- `add_friendship`：`repo.add_friendship(a, b)` + `relation.add_friend_edges(a, b, &env)`，
  语义与 `FriendCore::add_friendship`（`friend_core.rs:107`）一致但**不**回调
  edge 同步（edge 边已由 connect 流程落库，避免冗余与环路）。
- `remove_friendship`：`repo.remove_friendship` + `relation.remove_friend_edges`
  （best-effort 清理关系图，对齐 `friend_core.rs:191`）。
- env：沿用 repo 自身 `resolve_env()`。connect service 的 `self.env` 与
  `resolve_env()` 正常部署下一致；即使不一致，写进"读者 env"也恰恰保证
  建群闸门（同一 repo、同一 env 解析）能读到该行——**对齐读者的 env**。

### 4.3 触发点（`DbConnectService`，均为 Bot↔Bot only）

字段 `friendship_writeback: Option<Arc<dyn FriendshipWritebackPort>>`（None = Noop）。

1. `create_connect` 自动通过分支（≈line 370 后）：`add`。
2. `approve()` 人工通过（Bot↔Bot 双向 decide 完成后，≈line 500 后）：`add`。
   - **idempotent 提前返回分支（line 433 已 Approved）也执行 `add`**：
     INSERT 幂等，天然给上线前已 approve 的存量边权限好友补写 legacy 行。
3. `revoke_friend`（≈line 730 后）：`remove`。

### 4.4 失败策略

**best-effort**：写失败仅 `warn!`，不阻断 approve/revoke。理由：edge_grants 已是
SoR 且先落库、无跨库事务可回滚；与正向同步（`friend_core.rs:128`）的容错模式一致。

### 4.5 bootstrap 接线

`server.rs`：`DbFriendStore` 提前构造（现状已如此，FriendCore 稍后包同一 repo
Arc），构造 `FriendshipWritebackService` 后传入 `DbConnectService::new`；
DB 与 memory 两套 profile 同样接线。单测/Noop 传 `None`。

## 5. 测试计划

- `bcs-edge-permission` 单测（mock writeback 记录调用）：
  - approve Bot↔Bot 触发 add；idempotent 重复 approve 再次触发（幂等补写）；
  - create_connect 自动通过 Bot↔Bot 触发 add；
  - Human→Bot 连接/approve **不**触发；
  - revoke_friend Bot↔Bot 触发 remove；writeback 为 None 时全部为静默 no-op。
- `bcs-friend` 单测：`FriendshipWritebackService` add/remove 落
  `MemoryFriendRepo`，add 幂等。
- 集成回归（`bootstrap/bcs/tests`）：复现原 bug 场景——Bot↔Bot 边权限
  connect + approve → `/collaboration/groups` 建群成功（不再 403）；
  revoke 后建群恢复 403。

## 6. 上线与回滚

- 无开关、无 DDL 变更；`bcs_friendships` 的 INSERT 幂等（归一化 pair + env 主键）。
- 回滚 = revert 代码；已写入的行为 harmless 残留（与 legacy accept 产物同构）。