# Tasks: Group Context 多值 Owners

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Source: `spec-owner-field.md` + `plan-owner-field.md`
>
> 当前阶段只实现 persisted owners。不引入 creator bot owner/管理员判定或空接口；严禁在 `owners` 中持久化管理员。

---

## Task 1: Domain 与 transport-neutral contract

- [x] **Done:** Domain owners/WRO contract
- **Goal:** 定义多值 owners 和 `WRO`，并锁死 wire contract。
- **Files:** `apps/bcs/crates/contracts/bcs-domain/src/group_context.rs`, `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/group_context.rs`, `apps/bcs/crates/service-api/bcs-service-api/tests/v1_group_context_application_contracts.rs`
- **Done when:**
  - [x] 新增 `GroupContextOwners`，wire JSON 为 `{"userIds":["<owner-uuid>"]}`。
  - [x] `GroupContextEntry` 与 `GroupContextEntryMeta` 都包含 `owners`（`NewGroupContextEntry` 在 Task 2 落地）。
  - [x] `GroupContextPermission` 支持 `R`、`W`、`WR`、`WRO`，且 `WRO` 序列化为 `"WRO"`。
  - [x] `UpdateGroupContext` 接受 `Option<GroupContextOwners>`。
  - [x] `AddGroupContext` 不增加 owners 输入。
  - [x] `GroupContextGetItem` 的 `visible_to` / `collect_from` 为 optional，且非 owner 不序列化。
  - [x] Contract tests 覆盖 JSON shape 和 `WRO`。
- **Depends on:** —

## Task 2: Store 持久化与三 dialect migration

- [x] **Done:** Store owners and migrations
- **Goal:** 在 SQL、memory store 和历史版本行中持久化 owners。
- **Files:** `apps/bcs/crates/services/bcs-group-context-store/src/store.rs`, `apps/bcs/crates/services/bcs-group-context-store/src/memory.rs`, `apps/bcs/crates/services/bcs-group-context-store/tests/common/mod.rs`, `apps/bcs/crates/services/bcs-group-context-store/tests/conformance_group_context_repo.rs`, `apps/bcs/crates/services/bcs-group-context-store/tests/conformance_group_context_repo_mysql.rs`, `apps/bcs/migrations/sqlite/035_group_context_owners.sql`, `apps/bcs/migrations/mysql/034_group_context_owners.sql`, `apps/bcs/migrations/oceanbase/032_group_context_owners.sql`, `apps/bcs/crates/bootstrap/bcs/src/migrations.rs`, `apps/bcs/crates/bootstrap/bcs/src/migrations/tests/`（新增 migration regression）
- **Done when:**
  - [x] `NewGroupContextEntry.owners` 贯穿 memory 和 SQL 的 create/supersede。
  - [x] SQL select/meta select 包含 `owners_json`，row 映射能反序列化。
  - [x] `GroupContextEntryMeta` list 查询能读 owners。
  - [x] memory repo 的 `row_from_new` / `meta_from_entry` 完整映射 owners。
  - [x] 新版本行使用请求指定或服务端计算的 owners；历史版本不被改写。
  - [x] 三个 dialect 均新增 additive migration，`owners_json` 非空并完成回填。
  - [x] 存量 active/history row 按各自 `creator` 回填为 `{"userIds":[row.creator]}`。
  - [x] SQLite registry / runner 追加 version 35 且包含 migration 分支。
  - [x] Migration tests 证明应用前后的 schema、数据回填、账本记录和 checksum 正确。
- **Depends on:** Task 1

## Task 3: Application 写入语义与默认 owners

- [x] **Done:** Application owner write semantics
- **Goal:** add/systemset 默认写入 owners，并隔离 owner 与 W-only 的可修改字段。
- **Files:** `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs`, `apps/bcs/crates/application/v1/bcs-app-group-context/src/permissions.rs`, `apps/bcs/crates/application/v1/bcs-app-group-context/tests/group_context_service.rs`
- **Done when:**
  - [x] `add` 服务端默认写入 `owners.userIds=[actor_id]`。
  - [x] `add` 不读取调用方 owners；请求 body 中的未知 owners 不会生效。
  - [x] `systemset` 首次创建默认 `owners=["system"]`。
  - [x] `systemset` supersede 既有 context 时沿用 old owners。
  - [x] `systemset` 不执行 owner 或 `collect_from` 授权判断。
  - [x] update 授权顺序为先判断当前 active row owner，再对非 owner 判断 `collect_from`。
  - [x] update 传入 owners 时校验非空；未传时沿用 old owners。
  - [x] owner 可修改 `content`、`valid_from`、`valid_to`、`visible_to`、`collect_from`、`owners`。
  - [x] owner 不要求出现在 `collect_from`。
  - [x] 非 owner 必须满足现有 `collect_from` W 权限。
  - [x] 非 owner W 只能修改 `content`、`valid_from`、`valid_to`。
  - [x] 非 owner W 传入 `owners`、`visible_to` 或 `collect_from` 返回 `permission_denied`。
  - [x] `change_reason` 不能绕过上述边界。
  - [x] 授权只基于请求读取的当前 active row，不读取历史版本 owners；`command.owners` 不参与本次授权。
  - [x] 应用测试覆盖：非 owner W 在请求中把自己的 id 写入 owners 被拒绝。
  - [x] 应用测试覆盖：owner 不在 `collect_from` 时，可修改 content 与 `visible_to` / `collect_from` / `owners`。
  - [x] 应用测试覆盖：owner A 将 owners 移交给 B 后，B 是新 active row owner；A 立即失去 owner 权限，只能依据普通策略写。
  - [x] 应用测试覆盖：systemset supersede old owners 不为 `system` 的 context 时仍成功并沿用 old owners。
  - [x] 应用测试覆盖上述分支和 ownership 移交语义。
- **Depends on:** Task 2

## Task 4: List / Get 的 owner 读权限

- [x] **Done:** Owner list/get read semantics
- **Goal:** owner 隐含 R/W，输出 `WRO`，并只对 owner 暴露策略字段。
- **Files:** `apps/bcs/crates/application/v1/bcs-app-group-context/src/permissions.rs`, `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs`, `apps/bcs/crates/application/v1/bcs-app-group-context/tests/group_context_service.rs`
- **Done when:**
  - [x] owner 无需出现在 `visible_to` 或 `collect_from`。
  - [x] owner 判定优先于 `visible_to` / `collect_from`，不在任一策略中的 owner 返回 `WRO`。
  - [x] list 对 owner 返回 `permission="WRO"`。
  - [x] 非 owner 保持现有 `R/W/WR` 输出和过滤行为。
  - [x] get 中 owner 可读取不在 `visible_to` 的 context。
  - [x] get 对 owner 额外返回 `visible_to` 和 `collect_from`。
  - [x] get 对非 owner 不返回 `visible_to` 和 `collect_from`。
  - [x] list/get 不返回 owners 本身。
  - [x] owner 判定只使用 persisted owners，不引入 creator/admin 判定。
  - [x] 测试覆盖 owner、W-only、R-only 和无权限 actor。
- **Depends on:** Task 3

## Task 5: HTTP 与 CLI 暴露

- [x] **Done:** HTTP and CLI owners support
- **Goal:** 通过 HTTP 和 CLI 暴露 update owners，并保持 add 输入关闭。
- **Files:** `apps/bcs/crates/adapters/http/bcs-http/src/routes/group_context.rs`, `apps/bcs/crates/adapters/http/bcs-http/tests/group_context_routes.rs`, `apps/bcs/crates/tools/bcs-cli/src/main.rs`, `apps/bcs/crates/tools/bcs-cli/src/client.rs`, `apps/bcs/crates/tools/bcs-cli/tests/e2e/group_context_test.rs`, `apps/bcs/crates/tools/bcs-cli/tests/`
- **Done when:**
  - [x] `UpdateRequest` 支持 `owners`，update handler 原样传递给 service API。
  - [x] `AddRequest` 不增加 `owners`，add 构建 request 时不会读取该字段。
  - [x] GET 响应对 owner 输出策略字段，对非 owner 省略字段。
  - [x] list 响应可输出 `"WRO"`。
  - [x] CLI update 支持 `--owners '{"userIds":["bot-a","bot-b"]}'`。
  - [x] CLI add 不提供 owners 参数。
  - [x] CLI 通用 `group_context_request` 保持 body JSON 透传。
  - [x] HTTP/CLI tests 覆盖 update、add 不支持、owner-only get 字段和 `WRO`。
- **Depends on:** Task 3, Task 4

## Task 6: 文档、集成与验证

- **Goal:** 收口 API 文档、端到端行为和 spec 验收。
- **Files:** `apps/bcs/docs/group-context-api.md`, `apps/bcs/crates/bootstrap/bcs/tests/integration_group_context.rs`, `apps/bcs/specs/2026-09-09-group-context-api/spec-owner-field.md`, `apps/bcs/specs/2026-09-09-group-context-api/plan-owner-field.md`, `apps/bcs/specs/2026-09-09-group-context-api/tasks-owner-field.md`
- **Done when:**
  - [ ] API 文档说明 owners JSON shape、add 默认、update 字段权限、`WRO` 和 get owner-only 字段。
  - [ ] API 文档明确 owners 更新是一次立即生效的所有权移交，无两阶段确认或旧 owner 保底。
  - [ ] API 文档说明本阶段 owner 授权只使用 persisted owners，不把 creator/admin 判定描述为已支持能力。
  - [ ] Integration walkthrough 覆盖 add 创建 owner、owner 不在 `collect_from` 时的更新、ownership 移交、W-only 修改受限、owner list/get。
  - [ ] 以下目标测试通过：
    - `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-domain --all-targets`
    - `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-service-api --all-targets`
    - `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-group-context-store --all-targets`
    - `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-app-group-context --all-targets`
    - `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-http --test group_context_routes`
    - `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --all-targets`
  - [ ] Migration/integration tests 通过。
  - [ ] `git diff --check` 通过。
  - [ ] Spec acceptance criteria 中当前可实现项全部由测试或文档验证。
  - [ ] creator/admin 扩展只保留为后续需求记录，本轮不包含相关代码或占位接口。
- **Depends on:** Task 4, Task 5

---

## Groups

- **Group A — Contract/model:** Task 1
  - Theme: 先锁定 domain 和 transport-neutral 的 owners/WRO wire contract。
- **Group B — Persistence/migration:** Task 2
  - Theme: 让每个版本行可靠持久化 owners，并完成三 dialect 升级。
- **Group C — Write semantics:** Task 3
  - Theme: 建立默认 owners 和 owner/W 的字段级授权边界。
- **Group D — Read semantics:** Task 4
  - Theme: 让 owner 在 list/get 中获得预期读权限和响应字段。
- **Group E — Transport/CLI:** Task 5
  - Theme: 暴露 update 通道并保持 add 输入关闭。
- **Group F — Integration & verification:** Task 6
  - Theme: 用文档和端到端测试验证本阶段仅基于 persisted owners 的权限语义。
