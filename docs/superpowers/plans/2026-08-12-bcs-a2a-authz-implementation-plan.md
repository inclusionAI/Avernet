# BCS A2A Authz Implementation Plan（2026-08-12）

> Base branch: `origin/dev`
> Feature branch: `docs/bcs-authz-final-2026-08-07-devbase`
> Spec: `docs/superpowers/specs/2026-08-12-bcs-a2a-authz-final-spec.md`

## 0. 实施原则

1. **必须基于 dev 改**：当前分支已基于 `origin/dev`，后续改动不切到 main。
2. **先 TDD 再实现**：每个核心模型变更先补 contract/unit test，再改代码。
3. **A2A 不泄露内部边信息**：禁止 `edge_id`、完整 rules、完整 PermissionProfile 进入 A2A。
4. **EdgeGrant 只保存当前授权事实**：历史从 `PermissionRequest.edge_id` 反查。
5. **不兼容旧鉴权代码**：项目尚未落地生产鉴权，不做历史迁移兼容，只实现最新模型。
6. **不全局格式化**：遵守 `src/bcs/AGENTS.md`，不运行 `cargo fmt --all`。

---

## 1. 当前 foundation 需要纠偏的模型

### 1.1 AuthzGrantRef

当前旧实现：

```rust
pub struct AuthzGrantRef {
    pub kind: GrantKind,
    pub ref_id: String,
    pub revision: i64,
    pub digest: String,
    pub source: GrantSource,
}
```

目标：

```rust
pub struct AuthzGrantRef {
    pub kind: GrantKind,
    pub ref_id: String,
    pub revision: Option<i64>,
    pub digest: Option<String>,
    pub source: GrantSource,
}
```

规则：

| kind | revision | digest |
| --- | --- | --- |
| `permission_profile` | latest active profile revision | latest active profile digest |
| `rules` | `None` | `None` |

### 1.2 EdgeGrant

删除旧字段：

- `request_id`
- `rules_revision`
- `rules_digest`
- `requested_by`
- `approved_by`
- `revoked_by`
- `reason`
- `expires_at`
- `created_at`
- `updated_at`
- `approved_at`
- `revoked_at`

保留/新增目标字段：

```rust
pub struct EdgeGrant {
    pub edge_id: String,
    pub from_id: String,
    pub to_id: String,
    pub env: String,
    pub grant_kind: GrantKind,
    pub grant_ref_id: String,
    pub rules: Option<Vec<Rule>>,
    pub status: GrantStatus, // approved | revoked
    pub originator_policy_type: OriginatorPolicyType,
    pub originator_policy_data: Option<Value>,
}
```

### 1.3 PermissionRequest

新增/确认：

```rust
pub edge_id: Option<String>
```

语义：

- 建边申请创建时 `edge_id = None`。
- 建边审批通过后，创建 EdgeGrant，再回填该 request 的 `edge_id`。
- 更新/撤销申请创建时，直接带目标 `edge_id`。
- connect 加好友在数据库层落两条 request：A→B default、B→A default。

### 1.4 RulesGrantMaterial

删除：

- `revision`
- `digest`
- `expires_at`

目标：

```rust
pub struct RulesGrantMaterial {
    pub rules_grant_ref: String,
    pub from_id: String,
    pub to_id: String,
    pub env: String,
    pub rules: Vec<Rule>,
}
```

### 1.5 AuthzContext

删除：

- `issued_at`
- `expires_at`

AuthzContext 只表达授权事实，不控制消息 TTL。

---

## 2. TDD 顺序

### Phase 1：Domain / Protocol 合约测试

文件重点：

- `src/bcs/crates/contracts/bcs-domain/src/authorization.rs`
- `src/bcs/crates/contracts/bcs-protocol/src/a2a.rs`
- `src/bcs/crates/contracts/bcs-protocol/tests/a2a_authz_context_contract.rs`

先写/改测试：

1. `AuthzGrantRef(kind=rules)` 序列化时 `revision/digest` 为 `null` 或字段省略。
2. `AuthzGrantRef(kind=permission_profile)` 必须允许携带 revision/digest。
3. `AuthzContext` JSON 不包含 `issued_at/expires_at`。
4. A2A 扩展字段递归扫描，禁止：
   - `edge_id`
   - `edge_version`
   - `rules_template`
   - 完整 `rules`
   - 完整 `permission_profile`
   - `permission_profiles`
   - `rules_grants`
   - `issued_at`
   - `expires_at`

实现：

- 修改 domain struct。
- 修改 protocol tests。
- 调整所有构造点。

验收命令：

```bash
cd src/bcs
cargo test -q -p bcs-domain -p bcs-protocol
```

---

### Phase 2：Repo Port 与 Memory Store 模型纠偏

文件重点：

- `src/bcs/crates/service-api/bcs-service-api/src/port/repo/authorization.rs`
- `src/bcs/crates/services/bcs-authorization-store/src/memory.rs`
- `src/bcs/crates/services/bcs-authorization-store/tests/conformance_authorization_store.rs`

先写/改测试：

1. EdgeGrant 不再需要 request_id / rules_revision / rules_digest。
2. `PermissionRequest.edge_id` 可为空插入。
3. 建边审批后可以回填 `PermissionRequest.edge_id`。
4. 可以按 `edge_id` 查询 request 历史，按 `created_at` 排序。
5. request 不存在时更新状态/回填 edge_id 必须报错，不能静默成功。
6. EdgeGrant 条件约束：
   - `grant_kind=permission_profile` 时 `rules == None`
   - `grant_kind=rules` 时 `rules != None`

实现：

- PermissionRequestRepoPort 增加：
  - `backfill_permission_request_edge_id(request_id, edge_id)`
  - `list_permission_requests_by_edge_id(edge_id)`
  - decision update 带 `decided_by / decision_reason / decided_at / updated_at`
- EdgeGrantRepoPort 增加：
  - revoke/update 当前授权事实需要的方法
  - find rules grant by `grant_ref_id`
- Memory store 实现约束校验。

验收命令：

```bash
cd src/bcs
cargo test -q -p bcs-service-api -p bcs-authorization-store
```

---

### Phase 3：AuthzContextBuilder 修正

文件重点：

- `src/bcs/crates/service-api/bcs-service-api/src/core/authorization.rs`
- `src/bcs/crates/services/bcs-authorization/src/core/authorization_core.rs`
- `src/bcs/crates/services/bcs-authorization/tests/authz_context_builder.rs`

先写/改测试：

1. profile EdgeGrant：builder 查询 latest active PermissionProfile，生成带 revision/digest 的 `AuthzGrantRef`。
2. rules EdgeGrant：builder 生成 `AuthzGrantRef(kind=rules, revision=None, digest=None)`。
3. builder 不读取 EdgeGrant expires_at。
4. builder 输出 AuthzContext 不含 issued_at/expires_at。
5. originator_policy：
   - `any` 允许
   - `same_as_from` 匹配才允许
   - `specific` 命中列表才允许
   - 不匹配则过滤 grant；最终无 grant 则 deny。
6. public/collaboration runtime default：补充目标 Bot latest active default profile。

实现：

- 删除 `BuildA2aAuthzContextRequest.issued_at/ttl_ms`。
- builder 内部如果需要日志时间，使用当前时间 provider 或显式 `created_at` 入参，不写入 AuthzContext。
- grant ref 生成逻辑按最新 spec 改。

验收命令：

```bash
cd src/bcs
cargo test -q -p bcs-authorization
```

---

### Phase 4：Permission Request 审批应用服务

文件重点：

- `src/bcs/crates/service-api/bcs-service-api/src/application/authorization.rs`
- `src/bcs/crates/services/bcs-authorization/src/...`

先写测试：

1. approve connect：
   - 创建/处理两条 request。
   - 创建 A→B default EdgeGrant。
   - 创建 B→A default EdgeGrant。
   - 两条 request 均回填 edge_id。
2. approve permission_profile create：
   - request.edge_id 从 None 回填为新 edge_id。
   - EdgeGrant.grant_ref_id = requested_ref_id。
3. approve rules create：
   - 生成 opaque grant_ref_id。
   - EdgeGrant.rules 非空。
   - AuthzGrantRef 后续不需要 revision/digest。
4. approve profile/rules update：
   - 新 request 绑定已有 edge_id。
   - 审批通过后更新同一 EdgeGrant 当前授权事实。
   - 历史通过同 edge_id 多 request 查询。
5. approve revoke：
   - 新 request 绑定已有 edge_id。
   - 审批通过后 EdgeGrant.status = revoked。
6. reject：
   - 不创建 edge。
   - 不更新 edge。
   - request 标记 rejected。

实现：

- 实现 `AuthorizationService`，不要只停留在 trait。
- 权限校验 MVP 可先做 caller/to_id ownership 基础判断，复杂 owner policy 后续接入。
- 所有写操作失败必须回滚或 fail closed；不能部分成功静默吞错。

验收命令：

```bash
cd src/bcs
cargo test -q -p bcs-authorization -p bcs-service-api -p bcs-authorization-store
```

---

### Phase 5：Resolve API 核心逻辑

文件重点：

- `AuthorizationService::resolve_permission_profile`
- `AuthorizationService::resolve_rules_grant`

先写测试：

1. resolve profile：
   - caller 必须是目标 Bot B。
   - profile 必须属于 B。
   - revision/digest 必须匹配。
   - profile 必须 active。
2. resolve rules：
   - caller 必须是目标 Bot B。
   - 根据 `rules_grant_ref` 找 EdgeGrant。
   - EdgeGrant 必须 active。
   - EdgeGrant.to_id 必须是 B。
   - 返回 RulesGrantMaterial 不包含 edge_id/revision/digest。
3. resolve 失败必须 deny/error，不能返回空成功。

实现：

- 补齐应用服务逻辑。
- HTTP route 可在下一 phase 接。

验收命令：

```bash
cd src/bcs
cargo test -q -p bcs-authorization
```

---

### Phase 6：HTTP Routes / Bootstrap Wiring

文件重点：

- `src/bcs/crates/adapters/http/bcs-http/...`
- `src/bcs/crates/bootstrap/bcs/src/server.rs`
- `src/bcs/crates/bootstrap/bcs/src/migrations.rs`

先写/改测试：

1. `/authz/permission-requests` 创建申请。
2. `/authz/permission-requests/inbox` 查询 inbox。
3. approve/reject request。
4. `/authz/edge-grants` 查询授权边。
5. revoke edge。
6. resolve permission profile。
7. resolve rules grant。
8. production bootstrap 不允许静默缺失 authz builder。

实现：

- HTTP adapter 只调 application service。
- composition root wiring AuthorizationService / AuthzContextBuilder。
- 若 DB repo 暂未实现，必须明确 memory/dev wiring 与 production TODO，不假装 production 完整。

验收命令：

```bash
cd src/bcs
cargo test -q -p bcs
```

---

### Phase 7：Message Flow 集成

文件重点：

- `src/bcs/crates/services/bcs-message-flow/src/a2a_chat/mod.rs`
- `src/bcs/crates/services/bcs-message-flow/src/group_flow.rs`
- `src/bcs/crates/services/bcs-message-flow/src/bot_event.rs`
- `src/bcs/crates/services/bcs-message-flow/tests/contract_a2a_chat.rs`

先写/改测试：

1. direct A2A 注入 unified `grants[]`。
2. public bot 场景补 default profile，source=`public_default`。
3. collaboration 场景补 default profile，source=`collaboration_default`。
4. no grants/default 缺失时 fail closed，不投递。
5. `chat.send` 与 `chat.inject` 都遵守 AuthzContext 注入规则。
6. 输出 frame 不包含禁用字段。

实现：

- direct/group/bot_event 三条路径统一走 AuthzContextBuilder。
- 不在 message-flow 里拼权限，只拿 builder 结果注入。

验收命令：

```bash
cd src/bcs
cargo test -q -p bcs-message-flow
```

---

### Phase 8：DB Migration

文件重点：

- `src/bcs/migrations/mysql/*`
- `src/bcs/crates/bootstrap/bcs/src/migrations.rs`

任务：

1. 修复当前重复 `003` migration 编号。
2. 按最新 spec 建表：
   - `bcs_capabilities`
   - `bcs_permission_profiles`
   - `bcs_permission_requests`，含 nullable `edge_id`
   - `bcs_edge_grants`，不含 request_id / rules_revision / rules_digest / 审批冗余字段
   - `bcs_authz_decision_logs`
3. 添加 CHECK 或应用层等价约束：
   - profile grant rules 必须 null
   - rules grant rules 必须非空
4. 如果当前 PR 不实现 DB repo，则 migration 仍要与 domain model 一致，不能保留旧字段。

验收命令：

```bash
cd src/bcs
cargo test -q -p bcs
```

---

### Phase 9：文档收敛与提交范围清理

任务：

1. 保留唯一最新 spec：
   - `docs/superpowers/specs/2026-08-12-bcs-a2a-authz-final-spec.md`
2. 保留两份 plan：
   - 旧 foundation plan：`docs/superpowers/plans/2026-08-07-bcs-a2a-authz-implementation-plan.md`
   - 当前修正版 plan：`docs/superpowers/plans/2026-08-12-bcs-a2a-authz-implementation-plan.md`
3. 从本 PR diff 中移除 7 月历程文档变更，不再推这些历程文档。
4. 删除临时 agent 输出文件，不提交：
   - `ant_cc_*.json`

最终检查：

```bash
git diff --name-only origin/dev...HEAD
```

期望只包含：

- 最新 spec
- 两份 plan
- BCS authz 代码/测试/migration

---

## 3. 最小验收命令集合

每个 phase 可单独跑，最终至少跑：

```bash
cd src/bcs
cargo test -q -p bcs-domain -p bcs-protocol -p bcs-service-api -p bcs-authorization-store -p bcs-authorization -p bcs-message-flow
```

如果改到 bootstrap/http adapter，再加：

```bash
cd src/bcs
cargo test -q -p bcs
```

---

## 4. 实施顺序建议

推荐顺序：

1. Domain/Protocol 类型改完，先让合约测试红转绿。
2. Repo/Memory store 改完，保证数据模型一致。
3. AuthzContextBuilder 改完，保证 A2A grants 生成正确。
4. 审批服务实现，保证 request 与 edge 关系正确。
5. Resolve 服务实现，保证 Bot 本地缓存 miss 可拉取材料。
6. Message-flow/HTTP/bootstrap wiring。
7. Migration 与提交范围清理。

不要先大面积改 message-flow；否则底层模型还没稳，会反复返工。
