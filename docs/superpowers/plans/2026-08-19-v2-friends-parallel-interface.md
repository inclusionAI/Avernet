# 执行方案：并行接口（`/v2/friends/*` 新接口 + `/friends/*` 老接口还原）

> 日期：2026-08-19
> 原则：新接口用边权限实现，老接口保持原逻辑不变，数据迁移后从外部调用切到新接口。

## 改动清单

### 1. 还原老 `routes/friends.rs`（从 T3 之前恢复）
- `git show 7a9b65c98:src/bcs/crates/adapters/http/bcs-http/src/routes/friends.rs` → 覆盖当前 `routes/friends.rs`
- 老 handler 调老 `FriendService`/`bot_use_cases`（读 `bcs_friendships`/`bcs_friend_requests`），逻辑完全不变
- HttpAppState 不需改（T3 只加了 connect/admission，未移除老字段）

### 2. 新 `routes/v2_friends.rs`（T3 handler 移到这里 + 路径改 `/v2/`）
- 当前 `routes/friends.rs`（T3+PR-1 的 handler 调 `ConnectService`）→ 重命名为 `routes/v2_friends.rs`
- 路径前缀从 `/friends/*` → `/v2/friends/*`
- B1 envelope 去掉（新路径不需要兼容老 bcs-cli；恢复扁平 DTO）
- `routes/mod.rs` 加 `pub mod v2_friends;`

### 3. `router.rs`
- 老 `/friends/*` → `routes::friends::*`（恢复的老 handler）
- 新 `/v2/friends/*` → `routes::v2_friends::*`（移来的新 handler）
- `GET /bots/{id}/friends` → 老 handler（`routes::friends::list_friends`）
- `GET /v2/bots/{id}/friends` → 新 handler（`routes::v2_friends::list_friends`）
- `GET /bots/{id}/admission` + `PUT human-addable/friend-approval` + `POST ensure` 保持不变

### 4. 移除 `EdgeAuthzFriendAdapter`
- `bcs-edge-permission/src/lib.rs` 删 `EdgeAuthzFriendAdapter` struct + impl + `use FriendCoreService`
- 老 gates 继续用老 `FriendCoreService`（不变）
- 数据迁移后 gates 直接调 `EdgeGrantRepoPort::is_authorized`（不走 adapter）

### 5. `bcs-protocol/http/friends.rs` 清理（可选）
- B1 的 `envelope()` helper 可保留（老 handler 不用它，v2 handler 不用它——无害）
- 或删除（clean）—— 不阻塞

### 6. 验证
- `cargo check -p bcs-http` + `cargo test -p bcs-http` → 老 handler 恢复 + 新 handler 在 `/v2/`
- `cargo check -p bcs-edge-permission` → adapter 移除后编译
- `cargo build -p bcs` → binary 构建
- `cargo test -p bcs --lib` → 无回归

### 7. 提交
- 一个 commit：`refactor: parallel interfaces — restore old /friends/* + move edge-permission to /v2/friends/* + remove EdgeAuthzFriendAdapter`

## 切换时间线

| 阶段 | 老 `/friends/*` | 新 `/v2/friends/*` | gates |
|---|---|---|---|
| Phase 1 Build | ✅ 老逻辑（读旧表） | ✅ 新逻辑（读 edge_grants） | ✅ 老 FriendCoreService |
| Phase 2 ETL | ✅ 老 | ✅ 新（ETL 后有数据） | ✅ 老 |
| Phase 4 shadow | ✅ 老（用户走老路径） | shadow 比对 | ✅ 老 |
| Phase 5 cutover | 前端切到 `/v2/` | ✅ 正式 | gates 改接 `is_authorized` |
| Phase 5 retire | drop 老 routes + 老 service | ✅ | ✅ 新 |