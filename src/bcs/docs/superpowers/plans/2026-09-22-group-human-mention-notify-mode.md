# Group 级 at-human 外部通知策略 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 BCS Group 上增加持久化的 `human_mention_notify_mode`，通过两套 Group Patch 接口更新，并让所有现有及新建 Session 按当前 Group 配置决定是否调用 Human Notify Provider。

**Architecture:** 新配置作为 `bcs-domain::Group` 的独立枚举字段持久化到 `bcs_groups`，沿用现有 `GroupMutableFieldsPatch`、Group Core mutable-mutation change detection 和 V1 Group update 链路。消息流在共享的 `human_notify_hook` 中，通过 Group Core 的专用 current-policy read 在通知 gate 前读取当前 mode/driver，再判断 sender 是否允许外部通知并调用原有 `HumanMentionNotifyPort`；Session 和 `human_notify` Plugin API 不增加字段或逻辑。

**Tech Stack:** Rust workspace, Tokio, async-trait, serde, Axum, SQLite/MySQL migrations, existing Group Repo conformance tests, BCS message-flow contract tests, OpenAPI YAML.

---

## 0. Implementation boundary and working rules

**Read before editing:**

- `AGENTS.md`
- `src/bcs/AGENTS.md`
- `src/bcs/CLAUDE.md`
- `docs/arch/arch.rules.md`
- `docs/arch/ci.enforce.md`
- `docs/arch/context-boundary-format.md`
- `docs/arch/protocol-contract-tests.md`
- `CONTEXT-MAP.md`
- `src/bcs/crates/service-api/bcs-service-api/CONTEXT.md`
- `src/bcs/crates/services/bcs-group/CONTEXT.md`
- `src/bcs/crates/services/bcs-message-flow/CONTEXT.md`
- `src/bcs/docs/superpowers/specs/2026-09-22-group-human-mention-notify-mode-design.md`

Preserve the BCS rule not to run global `cargo fmt` or `cargo fmt --all`. Keep formatting changes limited to touched lines. Do not modify `bcs-human-notify-api`, dummy/work-order providers, or `[[human_notify.providers]]` configuration.

### File map

| Unit | Files | Responsibility in this change |
| --- | --- | --- |
| Domain | `src/bcs/crates/contracts/bcs-domain/src/group.rs` | Define enum, default, sender eligibility, Group field, constructors/tests |
| Service contracts | `src/bcs/crates/service-api/bcs-service-api/src/types/mod.rs`, `src/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs`, `src/bcs/crates/service-api/bcs-service-api/src/core/group.rs`, `src/bcs/crates/service-api/bcs-service-api/src/port/repo/group.rs` | Carry typed Patch/projections and declare the current-policy read contract |
| Group Core | `src/bcs/crates/services/bcs-group/src/core/group_core.rs` | Detect mode-only mutations, preserve no-op/version semantics, and delegate policy reads |
| V1 application | `src/bcs/crates/application/v1/bcs-app-group/src/lib.rs` | Authorize, apply, persist, and project the field |
| HTTP adapters | `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`, `src/bcs/crates/adapters/http/bcs-http/src/routes/groups.rs` | Parse both Patch request shapes and serialize legacy responses |
| API contract | `src/bcs/api-contracts/v1/openapi/groups.yaml`, `src/bcs/api-contracts/v1/domain-models.yaml` | Publish enum, Patch property, and response properties |
| Group persistence | `src/bcs/crates/services/bcs-group-store/src/lib.rs`, `src/bcs/crates/services/bcs-group-store/src/memory.rs` | SQL/Memory mapping, policy reads, patching, eventful mutation, cache invalidation |
| Schema | `src/bcs/migrations/mysql/030_group_human_mention_notify_mode.sql`, `src/bcs/migrations/sqlite/031_group_human_mention_notify_mode.sql`, `src/bcs/crates/bootstrap/bcs/src/migrations.rs` | Additive migrations; historical SQLite baseline remains unchanged |
| Migration verification | `src/bcs/crates/tools/bcs-admin/src/migrate_mysql_chain_tests.rs` | Update fixed version assertions and run real MySQL migration/Store checks |
| Message flow | `src/bcs/crates/services/bcs-message-flow/src/human_notify_hook.rs`, `src/bcs/crates/services/bcs-message-flow/src/group_flow/group_send.rs`, `src/bcs/crates/services/bcs-message-flow/src/group_flow/web_send.rs`, `src/bcs/crates/services/bcs-message-flow/src/bot_event/relay.rs` | Apply one shared current-policy external-notification gate |
| OpenAPI tests | `src/bcs/tests/openapi/test_group_v1_contract.py` | Assert enum, Patch property, and response projection contract |
| Tests | Existing domain, service, HTTP, Store, migration, MySQL, and message-flow contract tests | Pin behavior and compatibility |

No new crate or Plugin API is required. Execute Tasks 1 -> 2 -> 3 -> 4 -> 5 -> 6 in order: Store support precedes the real application/API green tests. Each shell block starts from the repository root unless it contains an explicit `cd src/bcs`; do not concatenate blocks into one persistent changed-directory shell.

### Required preflight: full-diff baseline and source-size preparation

Before the first implementation edit/commit, record `export BASE_SHA=$(git rev-parse HEAD)` in the implementation session and its validation notes. Do not reset this value after the per-task commits; use the correct PR merge-base instead if validating a rebased branch. Install hooks for that worktree with `scripts/install_git_hooks.sh`.

Inventory every planned source edit, including mechanical Group/DTO literal updates, and check line counts **before** feature work. There is no verified automatic four-file allowlist. Prepare behavior-preserving responsibility splits in the owning task before expanding an over-limit file:

| Existing source | Split responsibilities (within the same crate/module) |
| --- | --- |
| `crates/services/bcs-group-store/src/lib.rs` | enum/row mapping; group reads/lists; direct writes; eventful mutations; DM operations; unit fixtures |
| `crates/services/bcs-group-store/src/memory.rs` | builder; mutation helpers; tests; retain a small Repo facade |
| `crates/application/v1/bcs-app-group/src/lib.rs` | authorization; projections; create/provisioning; updates/membership; test module |
| `crates/services/bcs-group/src/application/management.rs` | create/runtime orchestration; projections; management operations; tests |
| `crates/adapters/http/bcs-http/src/routes/groups.rs` | request DTOs; response projection; route groups; translation helpers |
| `crates/application/v1/bcs-app-group/tests/v1_group_service.rs` and HTTP/Store contract tests | shared fixture module and responsibility-specific test submodules, preserving existing Cargo test target names |

Paths in this table are relative to `src/bcs`. Discover additional over-limit literal-sweep files with the Task 1 command; split only files touched by this work, preserving public paths, behavior and existing tests. Update the task file map to the actual extracted paths in the same preparation commit. Re-run the owning crate suites after each split. If a file truly cannot be split, obtain the explicit, owned exception through the real repository CI allowlist with its reason/cleanup plan; absence of such an approved entry is a blocker, not permission to skip it. Final Task 6 size verification covers both old facade files and new modules.

---

## Task 1: Add the domain enum, Group field, and typed Patch field

**Files:**

- Modify: `src/bcs/crates/contracts/bcs-domain/src/group.rs`
- Modify: `src/bcs/crates/contracts/bcs-domain/src/lib.rs` (root re-export)
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/lib.rs` (Service API re-export)
- Modify: every existing explicit `Group` struct literal reported by the sweep command below; at minimum the current source/test paths include:
  - `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/channel.rs`
  - `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`
  - `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs`
  - `src/bcs/crates/adapters/http/bcs-http/src/routes/channel.rs`
  - `src/bcs/crates/adapters/http/bcs-http/src/routes/groups.rs`
  - `src/bcs/crates/adapters/http/bcs-http/src/routes/session_files.rs`
  - `src/bcs/crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs`
  - `src/bcs/crates/adapters/ws/bcs-ws/src/web/frontend_delivery.rs`
  - `src/bcs/crates/application/v1/bcs-app-group/src/lib.rs`
  - `src/bcs/crates/bootstrap/bcs/src/server.rs`
  - `src/bcs/crates/bootstrap/bcs/src/timeout_scanner.rs`
  - `src/bcs/crates/contracts/bcs-domain/src/channel.rs`
  - `src/bcs/crates/contracts/bcs-protocol/src/delivery.rs`
  - `src/bcs/crates/services/bcs-channel-store/src/db.rs`
  - `src/bcs/crates/services/bcs-channel-store/src/memory.rs`
  - `src/bcs/crates/services/bcs-channel/src/bindings.rs`
  - `src/bcs/crates/services/bcs-channel/src/lib.rs`
  - `src/bcs/crates/services/bcs-channel/src/service.rs`
  - `src/bcs/crates/services/bcs-channel/src/session_outbound.rs`
  - `src/bcs/crates/services/bcs-event-store/src/memory.rs`
  - `src/bcs/crates/services/bcs-eventing/src/subscription.rs`
  - `src/bcs/crates/services/bcs-group-store/src/lib.rs`
  - `src/bcs/crates/services/bcs-group-store/src/memory.rs`
  - `src/bcs/crates/services/bcs-message-flow/src/bot_event/persistence.rs`
  - `src/bcs/crates/services/bcs-message-flow/src/bot_event/protocol.rs`
  - `src/bcs/crates/services/bcs-message-flow/src/group_flow/delivery.rs`
  - `src/bcs/crates/services/bcs-routing/src/core/router.rs`
  - relevant `src/bcs/crates/**/tests/*.rs` literals found by the sweep
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/types/mod.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/lib.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/core/group.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/port/repo/group.rs`
- Test: `src/bcs/crates/contracts/bcs-domain/src/group.rs` unit-test module
- Test: `src/bcs/crates/service-api/bcs-service-api/tests/core_group_contract.rs`

- [ ] **Step 1: Write failing domain tests.** Add tests beside the existing `Group` tests for the exact enum contract:

```rust
#[test]
fn human_mention_notify_mode_uses_wire_values_and_all_default() {
    assert_eq!(
        serde_json::to_string(&HumanMentionNotifyMode::DriverBotOnly).unwrap(),
        "\"driver_bot_only\""
    );
    assert_eq!(
        serde_json::from_str::<HumanMentionNotifyMode>("\"all\"").unwrap(),
        HumanMentionNotifyMode::All
    );
    assert_eq!(HumanMentionNotifyMode::default(), HumanMentionNotifyMode::All);
    assert!(serde_json::from_str::<HumanMentionNotifyMode>("\"invalid\"").is_err());
}

#[test]
fn human_mention_notify_mode_allows_only_the_driver_when_requested() {
    assert!(HumanMentionNotifyMode::All.allows_external_notify("other", "driver"));
    assert!(HumanMentionNotifyMode::DriverBotOnly.allows_external_notify("driver", "driver"));
    assert!(!HumanMentionNotifyMode::DriverBotOnly.allows_external_notify("other", "driver"));
    assert!(!HumanMentionNotifyMode::None.allows_external_notify("driver", "driver"));
}

#[test]
fn legacy_group_json_without_notify_mode_defaults_to_all() {
    let mut value = serde_json::to_value(Group::new(
        "group-1",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    ))
    .unwrap();
    value.as_object_mut().unwrap().remove("human_mention_notify_mode");
    let group: Group = serde_json::from_value(value).unwrap();
    assert_eq!(group.human_mention_notify_mode, HumanMentionNotifyMode::All);
}
```

- [ ] **Step 2: Run the focused tests to establish the red state.**

Run:

```bash
cd src/bcs
cargo test -p bcs-domain human_mention_notify_mode --lib
```

Expected: compilation/test failure because the enum, field, and helper do not yet exist.

- [ ] **Step 3: Implement the domain contract.** Add the enum before `GroupStrategy` in `group.rs`:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum HumanMentionNotifyMode {
    DriverBotOnly,
    #[default]
    All,
    None,
}

impl HumanMentionNotifyMode {
    pub fn allows_external_notify(self, sender_actor_id: &str, driver_bot_id: &str) -> bool {
        match self {
            Self::DriverBotOnly => sender_actor_id == driver_bot_id,
            Self::All => true,
            Self::None => false,
        }
    }
}
```

Add this field next to the other Group-level policy fields:

```rust
#[serde(default)]
pub human_mention_notify_mode: HumanMentionNotifyMode,
```

Initialize it to `HumanMentionNotifyMode::default()` in `Group::new` and every explicit `Group` struct literal. Add
`HumanMentionNotifyMode` to the `bcs-domain/src/lib.rs` `pub use group::{...}` list, and re-export
`GroupHumanNotifyPolicy` plus the domain enum from the established `bcs-service-api` root/types exports. Do not make
the runtime Group field optional: missing serialized legacy data is handled by serde default, while all in-memory Groups
have an explicit value.

- [ ] **Step 4: Perform the mechanical Group-literal sweep.** Run:

```bash
rg -l 'Group \{' src/bcs/crates --glob '*.rs' | sort
```

For every result that constructs `bcs_domain::Group`, add:

```rust
human_mention_notify_mode: HumanMentionNotifyMode::default(),
```

Use the actual imported type or the fully qualified `bcs_domain::HumanMentionNotifyMode` in test/support modules. Do not add the field to unrelated structs whose names only contain `Group`.

- [ ] **Step 5: Extend the typed mutable Patch and policy-read contract.** In `service-api/src/types/mod.rs`, add:

```rust
// Add inside GroupMutableFieldsPatch:
pub human_mention_notify_mode: Option<HumanMentionNotifyMode>,
```

Add this separate shared DTO in the same `types` module:

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupHumanNotifyPolicy {
    pub mode: HumanMentionNotifyMode,
    pub driver_bot_id: String,
}
```

`None` means omitted; `Some(mode)` means set the Group value. No `Option<Option<HumanMentionNotifyMode>>` is needed because `null` is rejected at the HTTP boundary and there is no “clear to null” state.

Add this fail-closed compatibility default to both `GroupRepoPort` and `GroupCoreService`:

```rust
async fn read_human_notify_policy(
    &self,
    _group_id: &str,
) -> ServiceResult<Option<GroupHumanNotifyPolicy>> {
    Err(ServiceError::InternalError(
        "current Group human-notify policy read is not implemented".to_string(),
    ))
}
```

Import `GroupHumanNotifyPolicy` and `ServiceError` from the existing `types` module in both trait files. Production
Group Core/Store implementations override it in Task 2. The default exists only so old test doubles cannot silently fall
back to `all`; a caller must treat the error as fail-closed. The method is a scoped,
transport-neutral policy read. `None` means Group not found; storage or enum parsing errors return `Err`. It must not
call cache-first `get`/`try_get` or load participants/Session state. Re-export `HumanMentionNotifyMode` and
`GroupHumanNotifyPolicy` from the established domain/Service API roots, and add a contract test proving the default
returns an error rather than a permissive policy.

- [ ] **Step 6: Run domain and service contract tests.**

Run:

```bash
cd src/bcs
cargo test -p bcs-domain
cargo test -p bcs-service-api --test core_group_contract
cargo test -p bcs-service-api --lib
cargo check -p bcs-service-api --all-targets
```

Expected: PASS for the new enum/default tests; any remaining compile errors identify Group literals that still need the explicit default field.

- [ ] **Step 7: Commit the contract slice.**

```bash
git add src/bcs/crates/contracts/bcs-domain src/bcs/crates/service-api/bcs-service-api
# include every source/test file changed by the Group literal sweep
# Do not mark the slice complete until the default policy-read contract test passes.
git commit -m "feat(bcs): add group human mention notify mode contract"
```

---

## Task 2: Add MySQL/SQLite migrations, Group Store implementations, and current-policy reads

**Files:**

- Create: `src/bcs/migrations/mysql/030_group_human_mention_notify_mode.sql`
- Create: `src/bcs/migrations/sqlite/031_group_human_mention_notify_mode.sql`
- Modify: `src/bcs/crates/bootstrap/bcs/src/migrations.rs`
- Do not modify: `src/bcs/crates/bootstrap/bcs/src/migrations/baseline.rs` (historical bootstrap DDL)
- Modify: `src/bcs/crates/services/bcs-group-store/src/lib.rs`
- Modify: `src/bcs/crates/services/bcs-group-store/src/memory.rs`
- Modify: `src/bcs/crates/services/bcs-group/src/core/group_core.rs` (fresh-read delegation)
- Test: `src/bcs/crates/services/bcs-group-store/tests/conformance_group_repo.rs`
- Modify/Test: `src/bcs/crates/bootstrap/bcs/src/migrations/tests.rs`
- Modify/Test: `src/bcs/crates/bootstrap/bcs/src/migrations/tests/bot_provider_storage.rs`
- Create/Test: `src/bcs/crates/bootstrap/bcs/src/migrations/tests/group_human_mention_notify_mode.rs`
- Modify/Test: `src/bcs/crates/tools/bcs-admin/src/migrate_mysql_chain_tests.rs` (register `#[path = "migrate_group_notify_contract.rs"] mod group_notify_contract;`)
- Create/Test: `src/bcs/crates/services/bcs-group-store/tests/conformance_human_notify_policy.rs`
- Create/Test: `src/bcs/crates/test-support/bcs-test-support/src/contract/group_human_notify.rs`
- Modify: `src/bcs/crates/test-support/bcs-test-support/src/contract/mod.rs`
- Create/Test: `src/bcs/crates/tools/bcs-admin/src/migrate_group_notify_contract.rs`
- Modify: `src/bcs/crates/tools/bcs-admin/Cargo.toml` (test-only `bcs-group-store` and `bcs-test-support` dependencies)
- Modify: `src/bcs/Cargo.lock` if the test-only dependency graph changes
- Modify: `.github/workflows/unit-tests.yml` to label migration and Store phases separately

- [ ] **Step 1: Add the shared Store behavior harness before persistence code.** Define `group_human_notify_contract` in the new test-support module and export that module from `contract/mod.rs`:

```rust
pub async fn group_human_notify_contract(
    writer: &dyn GroupRepoPort,
    fresh_reader: impl Fn() -> Arc<dyn GroupRepoPort>,
) {
    let group = Group::new("notify-contract", "driver", vec![
        Participant::bot("driver", ParticipantRole::Driver),
    ]);
    assert_eq!(group.human_mention_notify_mode, HumanMentionNotifyMode::All);
    for mode in [HumanMentionNotifyMode::All,
        HumanMentionNotifyMode::DriverBotOnly, HumanMentionNotifyMode::None]
    {
        let mut configured = group.clone();
        configured.human_mention_notify_mode = mode;
        writer.upsert(configured).await.expect("persist mode");
        let reader = fresh_reader();
        let loaded = reader.try_get(&group.id).await.expect("cold read").expect("group");
        assert_eq!(loaded.human_mention_notify_mode, mode);
        let policy = reader.read_human_notify_policy(&group.id).await
            .expect("read policy").expect("policy");
        assert_eq!(policy.mode, mode);
        assert_eq!(policy.driver_bot_id, "driver");
    }
}
```

Import the contract/domain types plus `Arc`. Extend this same harness with default/omitted Patch, ordinary Patch,
eventful Patch/version and unrelated-field isolation, and missing Group assertions. Eventful requests use the current
expected version and `event: None`; mode-only Core no-op behavior is tested in Task 3, not inferred from the raw Repo
primitive. Invoke the harness from Memory and migrated SQLite and from the real MySQL helper. For Memory the factory
returns a clone of the same Arc; SQL returns a new Store over the same DB **on each call**, including after every Patch.
A single reused reader would retain its own stale cache and is not a cold-read proof.

Add focused SQL/parser tests for NULL/legacy missing values => `All`, empty/unknown strings => error, all SELECT projections, DM race-safe insert, direct/eventful write failure propagation, and current-policy reads with a deliberately warm stale Group cache. Use a recording/failing DB decorator to assert one scoped policy SELECT (at most one row); Task 4 proves candidate filtering yields zero reads when Port/targets are unavailable. Because production DDL is NOT NULL, test NULL/missing-row-field defaults at the parser boundary without weakening the schema.

Additional failing cases in `conformance_human_notify_policy.rs`:

1. Build a Group with `DriverBotOnly`, upsert it, and assert a cold reader returns `DriverBotOnly`.
2. Patch to `HumanMentionNotifyMode::None`, reload through a separate reader, and assert other fields such as `label`, `visibility`, and `routing_policy` are unchanged.
3. Use `GroupMutableFieldsPatch::default()` plus the new field to verify omitted behavior.
4. Call `read_human_notify_policy` after warming an old Group cache and after a second Store commits a new mode/driver; assert the policy read returns the new mode/driver and does not populate/consult the ordinary Group cache.

Use a separate migration test module to assert a fresh SQLite runner (unchanged baseline followed by versions 1..31) contains the new column and an old schema upgraded through version 30 receives the default `all` column. The test must prove the baseline itself does not already contain the column; otherwise the fresh path can hide a duplicate `ADD COLUMN` defect.

- [ ] **Step 2: Run the Store tests to verify they fail.**

```bash
cd src/bcs
cargo test -p bcs-group-store --test conformance_human_notify_policy
cargo test -p bcs --lib migrations::tests::group_human_mention_notify_mode
```

Expected: the new Store/migration assertions fail because mapping, policy-read, and migration version 31 do not yet exist; do not satisfy the red state by making the domain field optional or mapping errors to `all`.

- [ ] **Step 3: Add the migration SQL.** Re-run `ls migrations/mysql migrations/sqlite` from `src/bcs` before editing. At the reviewed baseline the new numbers are MySQL 030 and SQLite 031; if another migration landed, allocate the next free number and update every filename, version assertion, and command in this plan consistently. The SQL below assumes 030/031 remain free:

`src/bcs/migrations/mysql/030_group_human_mention_notify_mode.sql`:

```sql
ALTER TABLE bcs_groups
    ADD COLUMN human_mention_notify_mode VARCHAR(32) NOT NULL DEFAULT 'all';
```

`src/bcs/migrations/sqlite/031_group_human_mention_notify_mode.sql`:

```sql
ALTER TABLE bcs_groups
    ADD COLUMN human_mention_notify_mode TEXT NOT NULL DEFAULT 'all';
```

Do not alter existing migration files. The MySQL chain must be checked on a real MySQL-compatible database because static SQL parsing is insufficient.

- [ ] **Step 4: Register SQLite migration version 31.** In `bootstrap/bcs/src/migrations.rs`:

1. Append `SqliteMigration { version: 31, name: "group_human_mention_notify_mode" }` to `SQLITE_VERSIONED_MIGRATIONS`.
2. Add the version-31 branch to `apply_sqlite_migration_body` that executes `031_group_human_mention_notify_mode.sql`; leave the outer history/checksum logic unchanged.
3. Preserve the existing checksum/history behavior and make the branch idempotent through the migration version record, not by rewriting historical migrations.

Do not edit `baseline.rs`. Fresh SQLite startup creates the historical baseline table first, then version 31 adds the column. Never add the same column to both baseline and version 31 migration, and do not use `IF NOT EXISTS` to hide duplicate ownership.

- [ ] **Step 5: Add migration regression coverage.** Create `migrations/tests/group_human_mention_notify_mode.rs`, register it from `migrations/tests.rs`, and cover:

```rust
#[tokio::test]
async fn fresh_sqlite_schema_contains_group_human_mention_notify_mode() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_migrations(&db).await?;
    let columns = column_names(&db, "bcs_groups").await?;
    assert!(columns.iter().any(|column| column == "human_mention_notify_mode"));
    Ok(())
}

#[tokio::test]
async fn sqlite_version_30_upgrade_adds_default_all_and_is_repeatable() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|migration| migration.version <= 30) {
        apply_sqlite_migration(&db, migration).await?;
    }
    run_sqlite_bootstrap_indexes(&db).await?;
    assert!(!column_names(&db, "bcs_groups").await?
        .iter().any(|name| name == "human_mention_notify_mode"));
    db.execute(DbStatement::new(
        "INSERT INTO bcs_groups (group_id, status, driver_bot, env) VALUES ('notify-upgrade', 'active', 'driver', 'test')",
    )).await?;
    let history_sql = "SELECT version, name, dialect, checksum, applied_at FROM bcs_schema_migrations ORDER BY version";
    let old_history = db.query(DbStatement::new(history_sql)).await?;
    run_sqlite_migrations(&db).await?;
    let rows = db.query(DbStatement::new(
        "SELECT human_mention_notify_mode FROM bcs_groups WHERE group_id = 'notify-upgrade'",
    )).await?;
    assert_eq!(db_get_column::<String>(&rows[0], "human_mention_notify_mode")?, "all");
    let history = db.query(DbStatement::new(history_sql)).await?;
    assert_eq!(&history[..30], old_history.as_slice());
    assert_eq!(history.len(), 31);
    assert_eq!(current_sqlite_version(&db, true).await?, Some(31));
    let columns = column_names(&db, "bcs_groups").await?;
    assert!(columns.iter().any(|column| column == "human_mention_notify_mode"));
    run_sqlite_migrations(&db).await?;
    assert_eq!(current_sqlite_version(&db, true).await?, Some(31));
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, history);
    Ok(())
}
```

The upgrade test must apply migrations through version 30, run the normal migration runner, assert current version 31, inspect `PRAGMA table_info(bcs_groups)`, query a pre-existing Group row, and assert the new column is `all`; a second runner invocation must not add another history row or fail.

Update `bot_provider_storage.rs` expectations by dialect: MySQL Provider storage remains version 029 and the new Group migration is 030; SQLite Provider storage remains version 030 and the new Group migration is 031. Keep its partial-step tests focused on the existing Provider storage migration and assert the final SQLite runner reaches version 31.

Update `migrate_mysql_chain_tests.rs` in the same change: full-chain assertions move from 29 to 30, the expected version range is `1..=30`, the v20 prefix upgrade reports 10 applied migrations, and `assert_chain_columns` includes `bcs_groups.human_mention_notify_mode`. In the same disposable MySQL test, after migration, retain an `Arc<dyn DbPlugin>` over the migrated plugin, instantiate writer and reader `MySqlGroupStore` values over the same DB, and run the shared three-mode/cold-read/policy-read harness. Then exercise mutable and eventful Patch paths to `none`, invalid persisted values, env isolation, and write-error propagation. Add a pre-existing Group to the v20 upgrade fixture and assert its default `all` plus preservation of all old migration records. This is separate evidence from migration-only success; do not reuse the SQLite-backed ignored placeholder as MySQL evidence.

Register `migrate_group_notify_contract.rs` as `group_notify_contract` inside `migrate_mysql_chain_tests.rs` using
`#[cfg(test)]` and `#[path = "migrate_group_notify_contract.rs"]`; do not grow the already 988-line `migrate.rs`. Its `pub(super) async fn verify(db: Arc<dyn DbPlugin>) -> anyhow::Result<()>`
invokes the shared harness with fresh MySQL Store factories and the SQL-specific assertions above. In
`migrate_mysql_chain_tests.rs`, pass an Arc to `check_full_mysql_chain` and use `.as_ref()` for existing DB helpers;
call `group_notify_contract::verify(db.clone()).await?` after the fresh migration phase and before the table
cleanup/prefix-upgrade phase. Keep empty-database refusal and cleanup on both success/failure. The same existing CI
command therefore gates migration and Store conformance; label/report the two phases separately and do not create a
second unmanaged database lifecycle. Add only the listed test dependencies, not production Store wiring to admin.

- [ ] **Step 6: Add Store enum conversion helpers.** In `bcs-group-store/src/lib.rs`, add canonical conversion helpers next to the existing Group enum converters:

```rust
fn human_mention_notify_mode_to_str(mode: HumanMentionNotifyMode) -> &'static str {
    match mode {
        HumanMentionNotifyMode::DriverBotOnly => "driver_bot_only",
        HumanMentionNotifyMode::All => "all",
        HumanMentionNotifyMode::None => "none",
    }
}

fn parse_human_mention_notify_mode(
    raw: Option<&str>,
) -> ServiceResult<HumanMentionNotifyMode> {
    match raw {
        None => Ok(HumanMentionNotifyMode::All),
        Some("driver_bot_only") => Ok(HumanMentionNotifyMode::DriverBotOnly),
        Some("all") => Ok(HumanMentionNotifyMode::All),
        Some("none") => Ok(HumanMentionNotifyMode::None),
        Some(other) => Err(ServiceError::InternalError(format!(
            "unknown human_mention_notify_mode '{other}'"
        ))),
    }
}
```

The parser maps only SQL `NULL` (`Option<&str>::None`) to `All`, accepts the three exact values, and returns `ServiceError::InternalError` for any other non-NULL value, including the empty string. Do not silently map unknown stored values to `All`.

- [ ] **Step 7: Update every SQL Group projection and constructor.** In `bcs-group-store/src/lib.rs`, update all Group SELECT lists and Group constructors found by:

```bash
rg -n "SELECT group_id|Group \{" src/bcs/crates/services/bcs-group-store/src/lib.rs
```

The affected paths include `load_group_from_mysql`, participant queries, paginated list queries, filtered list queries, `find_by_participant`, and their explicit `Group` initializers. Add the column to each SELECT and parse it into `human_mention_notify_mode`.

- [ ] **Step 8: Update SQL create/upsert/DM insert paths.**

1. In `upsert`, extract `group.human_mention_notify_mode`, add it to the INSERT values, and include it in the conflict update columns.
2. In `insert_dm_group_if_absent`, add the field to the race-safe Group INSERT. Its default remains `all` when the caller uses a default Group.
3. In `commit_eventful_mutation`, when the mutation is `PatchMutableFields`, add an assignment and bound parameter when `patch.human_mention_notify_mode` is `Some(mode)`.
4. In `apply_db_group_mutation_candidate`, update the in-memory terminal Group when the field differs.
5. In direct `patch_mutable_fields`, append `human_mention_notify_mode = ?` and the canonical string when present. Preserve the existing cache invalidation and storage-error propagation.

Do not add a new write method; the existing typed Patch remains the write contract. The dedicated current-policy read
is the only new Repo operation and is required by the design's freshness boundary.

- [ ] **Step 9: Update Memory Store and builders.** In `memory.rs`:

1. Set the field in `GroupBuilder::build` to `HumanMentionNotifyMode::default()`.
2. Apply the field in `patch_mutable_fields`.
3. Apply the field in `apply_memory_group_mutation` so eventful V1 updates match SQL behavior.
4. Update all test Group literals in this crate.

Implement the dedicated current-policy read in both repositories and delegate it in `GroupCore`:

```rust
async fn read_human_notify_policy(
    &self,
    group_id: &str,
) -> ServiceResult<Option<GroupHumanNotifyPolicy>> {
    self.repo.read_human_notify_policy(group_id).await
}
```

Memory acquires its `groups` read lock once and copies mode/driver before releasing it. SQL executes one
standalone query on the primary datasource, never the ordinary Group cache, a read replica or an old transaction:

```sql
SELECT human_mention_notify_mode, driver_bot
FROM bcs_groups
WHERE group_id = ? AND env = ?
```

Decode with the strict parser; no row => `None`, read/parse error => `Err`. Do not load participants or update the
ordinary cache. Test env isolation and a warm stale cache explicitly.

- [ ] **Step 10: Run persistence and SQLite migration tests.**

```bash
cd src/bcs
cargo test -p bcs-group-store --test conformance_human_notify_policy
cargo test -p bcs-group-store --test conformance_group_repo
cargo test -p bcs-group-store
cargo test -p bcs --lib migrations::tests
cargo check -p bcs-group-store -p bcs-group -p bcs-admin -p bcs --all-targets
```

Expected: Memory and SQLite round-trip tests pass, the cold-read assertion proves SQL mapping rather than cache-only behavior, the unchanged baseline plus version-31 migration is append-only and repeatable, and all Group load paths compile with the new field.

- [ ] **Step 11: Run the real MySQL migration and Store verification.** When a MySQL 8.4 test database is available, run the updated full-chain test against a disposable empty database:

```bash
cd src/bcs
: "${BCS_TEST_MYSQL_URL:?set BCS_TEST_MYSQL_URL to a disposable MySQL 8.4 test database}"
cargo test -p bcs-admin full_mysql_migration_chain_applies_and_preserves_history -- --ignored
```

Expected: the complete MySQL chain through version 30 applies to an empty database, the Group column has the expected default, cold-read Store round-trip and mutable/eventful Patch checks pass, and repeated migration/history checks remain unchanged. Static SQL checks or migration-only success are not a substitute for Store behavior. If local MySQL is unavailable, leave both migration and Store evidence explicitly unrun and use the same updated test in `.github/workflows/unit-tests.yml` as CI evidence; do not report either as locally verified.

- [ ] **Step 12: Commit the persistence slice.**

```bash
git add src/bcs/migrations/mysql/030_group_human_mention_notify_mode.sql \
  src/bcs/migrations/sqlite/031_group_human_mention_notify_mode.sql \
  src/bcs/crates/bootstrap/bcs/src/migrations.rs \
  src/bcs/crates/bootstrap/bcs/src/migrations \
  src/bcs/crates/services/bcs-group-store \
  src/bcs/crates/services/bcs-group/src/core/group_core.rs \
  src/bcs/crates/test-support/bcs-test-support/src/contract \
  src/bcs/crates/tools/bcs-admin src/bcs/Cargo.lock .github/workflows/unit-tests.yml

git commit -m "feat(bcs): persist group human mention notify mode"
```

---

## Task 3: Propagate the field through V1 application contracts and both HTTP Patch APIs

**Files:**

- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/group_management.rs`
- Modify: `src/bcs/crates/services/bcs-group/src/core/group_core.rs`
- Modify/Test: `src/bcs/crates/services/bcs-group/tests/group_events.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-group/src/lib.rs`
- Modify: `src/bcs/crates/services/bcs-group/src/application/management.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-http/src/routes/groups.rs`
- Modify: `src/bcs/api-contracts/v1/openapi/groups.yaml`
- Modify: `src/bcs/api-contracts/v1/domain-models.yaml`
- Test: `src/bcs/crates/application/v1/bcs-app-group/tests/v1_group_service.rs`
- Test: `src/bcs/crates/service-api/bcs-service-api/tests/v1_group_application_contracts.rs`
- Test: `src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs`
- Test: `src/bcs/crates/adapters/http/bcs-http/tests/legacy_group_patch_contract.rs`
- Test: `src/bcs/crates/adapters/http/bcs-http/tests/groups_contract.rs`
- Modify/Test: `src/bcs/tests/openapi/test_group_v1_contract.py`
- Modify: `src/bcs/crates/service-api/bcs-service-api/CONTEXT.md`
- Modify: `src/bcs/crates/services/bcs-message-flow/CONTEXT.md`

- [ ] **Step 1: Add failing DTO, OpenAPI, and application tests.** Before implementation, add the mode-only Core/application assertions described in Step 6 and, in the existing V1 DTO test module, add assertions with these inputs:

```rust
let configured: UpdateGroupRequest = serde_json::from_value(serde_json::json!({
    "human_mention_notify_mode": "driver_bot_only"
}))
.expect("valid notify mode");
assert_eq!(
    configured.human_mention_notify_mode,
    Some(HumanMentionNotifyMode::DriverBotOnly)
);

let omitted: UpdateGroupRequest = serde_json::from_value(serde_json::json!({
    "name": "renamed"
}))
.expect("omitted notify mode");
assert_eq!(omitted.human_mention_notify_mode, None);

assert!(serde_json::from_value::<UpdateGroupRequest>(serde_json::json!({
    "human_mention_notify_mode": null
})).is_err());
assert!(serde_json::from_value::<UpdateGroupRequest>(serde_json::json!({
    "human_mention_notify_mode": "invalid"
})).is_err());
```

Add the equivalent legacy `LegacyUpdateGroupRequest` cases in `legacy_group_patch_contract.rs` or its nearest DTO test module.

- [ ] **Step 2: Run the tests to verify they fail.**

```bash
cd src/bcs
cargo test -p bcs-api-http --test group_routes
cargo test -p bcs-http --test legacy_group_patch_contract
cargo test -p bcs-group --test group_events
```

Expected: DTO compilation/rejection tests and the mode-only Core regression fail for the expected missing-field/no-op reasons. Do not mark this red step complete if tests fail for unrelated fixture or environment errors.

- [ ] **Step 3: Extend the V1 service contract.** In `application/v1/group.rs`:

1. Re-export `HumanMentionNotifyMode` with the existing domain type re-exports.
2. Add `pub human_mention_notify_mode: HumanMentionNotifyMode` to:
   - `NormalGroupSummary`;
   - `DirectMessageGroupSummary`;
   - `CollaborationGroupDetail`;
   - `DirectMessageGroupDetail`.
3. Add `pub human_mention_notify_mode: Option<HumanMentionNotifyMode>` to `GroupPatch`.
4. Add `&& self.human_mention_notify_mode.is_none()` to `GroupPatch::is_empty()`.

Keep the field non-optional in response DTOs so all returned Groups expose an effective value.

- [ ] **Step 4: Extend both HTTP request DTOs.** In both `UpdateGroupRequest` and `LegacyUpdateGroupRequest`, add:

```rust
#[serde(default, deserialize_with = "deserialize_present_non_null")]
pub human_mention_notify_mode: Option<HumanMentionNotifyMode>,
```

Use the already-present `deserialize_present_non_null`; do not add a new null parser. In both `From<UpdateGroupRequest> for GroupPatch` and `From<LegacyUpdateGroupRequest> for GroupPatch` implementations, forward the field unchanged.

- [ ] **Step 5: Add application-level Patch and projection behavior.** In `bcs-app-group/src/lib.rs`:

1. In `GroupServiceImpl::update`, after the existing visibility/delivery policy handling, copy the request into both the working Group and `GroupMutableFieldsPatch`:

```rust
if let Some(mode) = patch.human_mention_notify_mode {
    group.human_mention_notify_mode = mode;
    persistence_patch.human_mention_notify_mode = Some(mode);
}
```

2. In `DetailCommon`, carry `human_mention_notify_mode` so normal and Dm projections share the same source.
3. Populate the field in `CollaborationGroupDetail`, `DirectMessageGroupDetail`, `NormalGroupSummary`, and `DirectMessageGroupSummary`.
4. Ensure `project_detail_with_state_machine` and `project_summary` preserve the current Group value and do not read Session state for it.

- [ ] **Step 6: Include the field in Group Core change detection.** In `src/bcs/crates/services/bcs-group/src/core/group_core.rs`, extend `prepare_group_mutation` so a value change is recognized before the repository mutation is prepared:

```rust
if let Some(mode) = patch.human_mention_notify_mode
    && group.human_mention_notify_mode != mode
{
    changed_fields.push("human_mention_notify_mode");
}
```

Keep the existing `changed_fields.is_empty()` behavior: return the current Group without a version increment or Event. Add a `group_events.rs` regression for (a) only this field changing, which must commit and return the new value, and (b) setting the existing value again, which must remain a no-op. Updating only the Store cannot make a single-field Patch work because Group Core currently exits before calling the Store.

- [ ] **Step 7: Extend the legacy Group service projections.** First add a non-optional `HumanMentionNotifyMode` field to `GroupDetailResult` and `GroupListEntry` in `src/bcs/crates/service-api/bcs-service-api/src/application/group_management.rs`. Then populate it from Group in `services/bcs-group/src/application/management.rs` (`group_to_detail_with_context` and `group_to_list_entry`). Update explicit response fixtures instead of defaulting configured projections. Update the listed Service API/message-flow Context files with caller/callee, uncached read and failure semantics.

- [ ] **Step 8: Extend the legacy JSON projections.** In `bcs-http/src/routes/groups.rs`, include the field in:

- `group_detail_to_create_json`;
- `bot_group_list_entry_to_legacy_json`;
- `group_to_detail_json`.

Do not add it to `PATCH /groups/{id}/settings`; that route remains service-spec-only.

- [ ] **Step 9: Update OpenAPI schemas.** In `src/bcs/api-contracts/v1/domain-models.yaml`:

1. Add a reusable schema:

```yaml
HumanMentionNotifyMode:
  type: string
  enum:
    - driver_bot_only
    - all
    - none
```

2. Add a required `human_mention_notify_mode` property referencing that schema to `NormalGroupSummary`, `DirectMessageGroupSummary`, `CollaborationGroupDetail`, and `DirectMessageGroupDetail`.

In `src/bcs/api-contracts/v1/openapi/groups.yaml`, add the same property to `UpdateGroup` with the reusable reference. Keep `additionalProperties: false` and `minProperties: 1`.

Add YAML assertions to `src/bcs/tests/openapi/test_group_v1_contract.py` for the three-value enum, omitted-but-non-null Patch field, `minProperties: 1`, `additionalProperties: false`, and the required field on all four response shapes. Run the actual Python validator and this pytest suite in Task 6 Step 2. `check-protocol-compat.sh` is a Rust wire-test presence check, while `check-public-api.sh` checks Rust public API/versioning; neither classifies OpenAPI required response fields. Complete any Rust API propagation/version requirement without treating those scripts as OpenAPI evidence.

- [ ] **Step 10: Add application and route assertions.** Pin that:

- a Patch containing only the new field is not empty;
- V1 and Legacy adapters forward a `Some(HumanMentionNotifyMode)` value for each enum variant into `GroupPatch`;
- omitted field remains `None`;
- null and invalid values fail at the HTTP boundary;
- response projections include the configured value for normal and Dm Groups; persistence round-trip is asserted after Task 2 Store support;
- old Patch fields still round-trip unchanged.

- [ ] **Step 11: Run the focused HTTP/application tests.**

```bash
cd src/bcs
cargo test -p bcs-app-group --test v1_group_service
cargo test -p bcs-group --test group_events
cargo test -p bcs-api-http --lib human_mention_notify_mode
cargo test -p bcs-service-api --test v1_group_application_contracts
cargo test -p bcs-api-http --test group_routes
cargo test -p bcs-http --test legacy_group_patch_contract
cargo test -p bcs-http --test groups_contract
```

Expected: DTO mapping, `GroupPatch::is_empty`, Core change detection, and real application -> Core -> Store round-trip all pass. Task 2 is already complete, so persistence is not a deferred dependency. Same-value updates preserve version and the absence of a public mutable-Patch Event; write failures propagate.

- [ ] **Step 12: Commit the API/application slice.**

```bash
git add src/bcs/crates/service-api/bcs-service-api \
  src/bcs/crates/services/bcs-group \
  src/bcs/crates/application/v1/bcs-app-group \
  src/bcs/crates/adapters/http/bcs-api-http \
  src/bcs/crates/adapters/http/bcs-http \
  src/bcs/tests/openapi/test_group_v1_contract.py src/bcs/api-contracts/v1 \
  src/bcs/crates/services/bcs-message-flow/CONTEXT.md
# Stage any public-API version/Cargo.lock update explicitly if required by the checker.
git commit -m "feat(bcs): expose group human notify mode in patches"
```

---

## Task 4: Gate external notifications in the shared message-flow helper

**Files:**

- Modify: `src/bcs/crates/services/bcs-message-flow/src/human_notify_hook.rs`
- Modify: `src/bcs/crates/services/bcs-message-flow/src/group_flow/group_send.rs`
- Modify: `src/bcs/crates/services/bcs-message-flow/src/group_flow/web_send.rs`
- Modify: `src/bcs/crates/services/bcs-message-flow/src/bot_event/relay.rs`
- Test: `src/bcs/crates/services/bcs-message-flow/src/human_notify_hook.rs`
- Test: `src/bcs/crates/services/bcs-message-flow/tests/contract_human_notify_context.rs`
- Modify/Test: `src/bcs/crates/services/bcs-message-flow/tests/support/session.rs` only if a no-read assertion needs a counting Session fake

- [ ] **Step 1: Add failing helper matrix tests.** Extend the private tests in `human_notify_hook.rs` with a recording port and assertions for:

```text
all + driver       => one notification
all + other bot    => one notification
all + human        => one notification
driver_bot_only + driver => one notification
driver_bot_only + other bot => zero notifications
driver_bot_only + human => zero notifications
none + any sender  => zero notifications
```

Use the existing overlay fixture so the tests also prove that the mention target remains a Human participant and the gate is only about the sender.

- [ ] **Step 2: Run helper tests to verify they fail.**

```bash
cd src/bcs
cargo test -p bcs-message-flow human_notify_hook
```

Expected: compile failure because the helper does not yet read policy or await the Group Core contract. The tests must later use a policy-aware Memory/recording Core; a default fail-closed Core must not make the `all` cases pass.

- [ ] **Step 3: Make the shared helper async and read the authoritative policy.** Task 2 has implemented `GroupCoreService::read_human_notify_policy`. Make `spawn_human_mention_notify` async with first parameter `groups: &dyn GroupCoreService`, retaining all existing parameters and its `()` return type. Import that trait, and order it as follows: first check Provider availability, the presence of mention ids, and resolve the overlay to a non-empty Human list (all are in-memory); then call the dedicated policy read; then compare mode/driver synchronously without another await; only then create the existing notification task.

```rust
let Some(port) = port.as_ref().filter(|port| port.is_available()) else { return; };
let Some(mention_actor_ids) = mention_actor_ids else { return; };
let Some(humans) = build_mention_trigger(
    mention_actor_ids, overlay, &context.sender_actor_id,
) else { return; };
let policy = match groups.read_human_notify_policy(&context.group_id).await {
    Ok(Some(policy)) => policy,
    Ok(None) | Err(_) => {
        tracing::warn!(group_id = %context.group_id,
            sender_actor_id = %context.sender_actor_id,
            "current human-notify policy unavailable; external notification skipped");
        return;
    }
};
if !policy.mode.allows_external_notify(
    &context.sender_actor_id, &policy.driver_bot_id,
) {
    tracing::debug!(group_id = %context.group_id,
        sender_actor_id = %context.sender_actor_id, mode = ?policy.mode,
        "human mention external notification suppressed by group policy");
    return;
}
// Existing MentionNotification assembly and tokio::spawn follow here.
```

Do not log message text, resolved Humans, or raw DB errors. Keep existing mention resolution, outbound-text handling,
Session title lookup, and Provider error behavior unchanged. This order gives eligible candidates exactly one policy read;
Port unavailable/no mention/no valid Human produces zero new reads and no Session-title lookup.

- [ ] **Step 4: Await the shared helper in all three callers.** In `group_send.rs`, `web_send.rs`, and `bot_event/relay.rs`, add `flow.group.as_ref()` as the first helper argument and append `.await` after message persistence and the existing outbound policy check. Keep the helper return type `()`; a suppressed or failed policy read must resume the existing Bot/Workbench delivery code, so do not return early from the main message function. Do not pass mode/driver from the early routing snapshot or derive them from Session participants/Provider configuration. In `group_flow`, preserve the Group field when `apply_session_participant_scope` replaces only `participants`.

- [ ] **Step 5: Add message-flow regression tests.** Extend `contract_human_notify_context.rs` and nearby message-flow tests to cover:

1. `none` suppresses a WebSend notification while the Group message/message count and normal delivery outcome still occur.
2. `driver_bot_only` allows a `bot-driver` WebSend and suppresses a non-driver sender.
3. A Human sender is allowed under `all` and suppressed under `driver_bot_only`.
4. `bot_event/relay` uses the same policy gate.
5. Persistent group send uses the same policy gate.
6. A `manager_worker` Group with `driver_bot = manager` allows manager and suppresses worker.
7. Dm Group behavior remains no external notification for every mode.
8. Change the Group in the shared Group repo from `all` to `none` between two messages using the same Session id; the second message produces no Provider notification without changing Session participants.
9. Hold a message after its early Group snapshot is loaded but before the notification helper starts. Commit `all -> none` or change `driver_bot`, release the barrier, and assert the helper's dedicated current-policy read observes the committed value; a sequential two-message test alone is insufficient.
10. Change the Group `driver_bot` between two `driver_bot_only` messages and assert that the first driver is eligible before the committed change and the new driver is eligible after it; the Session does not pin the old driver for notification policy.
11. Force the policy read to fail and assert the message remains persisted/routed while no notification task, Session-title read, or Provider call occurs.
12. Record helper-only policy-read counts: Port unavailable/no mention/no valid Human => zero; eligible notification candidate => exactly one scoped read. Do not mistake existing main-message Session reads for new notification-title reads.
13. With barriers, cover a Patch overlapping the policy read: accept only one consistent committed mode/driver snapshot, and do not attempt to retract an already-created notification task.
14. Exercise the existing maximum supported mention count, concurrent notification candidates and repeated DB failures. Reads scale at most one per eligible message, never per Human; no retry or write is introduced. Capture real-MySQL read latency/pool limits, and report load-validation limits explicitly.

Use the existing `RecordingHumanMentionNotify` and assert the Group message/delivery result independently from the recorder count. Use deterministic barriers/completion signals, not sleeps, when asserting no notification. New helper fixtures must implement the policy read rather than rely on the fail-closed trait default. Assert no Session-title read or spawned notification on suppression/missing/error; normal message Session access remains unchanged.

- [ ] **Step 6: Run the message-flow tests.**

```bash
cd src/bcs
cargo test -p bcs-message-flow --test contract_human_notify_context
cargo test -p bcs-message-flow --test contract_message_flow
cargo test -p bcs-message-flow --lib human_notify_hook
```

Expected: all existing notification context tests still pass, and the new mode matrix passes.

- [ ] **Step 7: Commit the message-flow slice.**

```bash
git add src/bcs/crates/services/bcs-message-flow/src \
  src/bcs/crates/services/bcs-message-flow/tests

git commit -m "feat(bcs): gate human mention notifications by group mode"
```

---

## Task 5: Complete cross-layer response, Store, and contract regression coverage

**Files:**

- Modify: `src/bcs/crates/services/bcs-group/src/application/management.rs`
- Modify/Test: `src/bcs/crates/services/bcs-group/tests/management.rs`
- Modify/Test: `src/bcs/crates/services/bcs-group/tests/group_events.rs`
- Modify/Test: `src/bcs/crates/services/bcs-group-store/tests/conformance_group_repo.rs`
- Modify/Test: `src/bcs/crates/application/v1/bcs-app-group/tests/v1_group_service.rs`
- Modify/Test: `src/bcs/crates/adapters/http/bcs-http/tests/groups_contract.rs`
- Modify/Test: `src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs`
- Modify/Test: `src/bcs/tests/openapi/test_group_v1_contract.py`
- Modify: every file identified by the Task 1 `Group {` sweep that still fails compilation after the explicit list is updated

- [ ] **Step 1: Add Group application projection assertions.** Assert that `GroupDetailResult` and `GroupListEntry` expose the exact `HumanMentionNotifyMode` value after a Group is loaded, updated, and listed. Include a Dm Group projection so the stored field is visible even though Dm notification remains disabled.

- [ ] **Step 2: Add eventful mutation assertions.** In `group_events.rs`, construct `GroupMutableFieldsPatch { human_mention_notify_mode: Some(HumanMentionNotifyMode::None), ..Default::default() }`, commit it through the existing eventful Group mutation path, and assert the returned Group, stored Group, version, and event behavior remain consistent with other mutable fields.

- [ ] **Step 3: Add Patch isolation assertions.** Use a Group with a non-default label, context, visibility, and routing policy. Patch only `human_mention_notify_mode`, reload, and assert every unrelated field remains identical. Add the inverse case where a normal field Patch does not reset the notify mode.

- [ ] **Step 4: Add API response assertions.** For both HTTP adapters, assert successful Group detail/list responses contain `human_mention_notify_mode` and old clients’ existing fields are unchanged. For invalid values and null, assert the current 400 error envelope and verify the application recorder was not called.

- [ ] **Step 5: Run all affected crate suites.**

```bash
cd src/bcs
cargo test -p bcs-group
cargo test -p bcs-group-store
cargo test -p bcs-app-group
cargo test -p bcs-api-http --test group_routes
cargo test -p bcs-http --test groups_contract
cargo test -p bcs-http --test legacy_group_patch_contract
```

Expected: no response projection, eventful mutation, or Group Store conformance regression.

- [ ] **Step 6: Commit the regression slice.**

```bash
git add src/bcs/crates/services/bcs-group \
  src/bcs/crates/services/bcs-group-store/tests \
  src/bcs/crates/application/v1/bcs-app-group/tests \
  src/bcs/crates/adapters/http/bcs-http/tests \
  src/bcs/crates/adapters/http/bcs-api-http/tests

git commit -m "test(bcs): cover group human notify mode propagation"
```

---

## Task 6: Validate OpenAPI, migration chain, architecture boundaries, and workspace compatibility

**Files:**

- Verify all files changed by Tasks 1–5
- Modify: `src/bcs/docs/superpowers/specs/2026-09-22-group-human-mention-notify-mode-design.md` only if implementation reveals a confirmed contract difference; otherwise leave the approved spec unchanged
- Create/Modify: no Plugin API files
- Modify: `src/bcs/scripts/ci/check-protocol-compat.sh`
- Modify: `src/bcs/scripts/ci/check-store-boundaries.sh`
- Create/Test: `src/bcs/scripts/ci/tests/checker_scope_regression.sh`
- Modify if required by the public-API gate: `src/bcs/crates/service-api/bcs-service-api/Cargo.toml`, `src/bcs/Cargo.lock`

- [ ] **Step 1: Run focused contract and migration verification.**

```bash
cd src/bcs
cargo test -p bcs-domain
cargo test -p bcs-service-api
cargo test -p bcs-group-store
cargo test -p bcs --lib migrations::tests
cargo test -p bcs-message-flow --test contract_human_notify_context
```

Expected: zero failures and migration history ending at SQLite version 31. MySQL migration and Store acceptance is the separate ignored full-chain command in Task 2 Step 11, locally when `BCS_TEST_MYSQL_URL` is available and otherwise in the existing CI job.

- [ ] **Step 2: Run the actual OpenAPI contract validation and API assertions.** From the repository root, run the checker that parses the edited YAML and the existing Group OpenAPI tests:

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
uv run --with pyyaml --with pytest python -m pytest \
  src/bcs/tests/openapi/test_group_v1_contract.py -q
```

Expected: the new enum, Patch property, required normal/Dm response properties, envelope rules, and existing Group compatibility assertions pass. `check-protocol-compat.sh` is a Rust wire-test presence check, not the OpenAPI validator; run it only as part of the architecture suite and report SKIP/FAIL rather than treating it as schema evidence.

- [ ] **Step 3: Repair checker scope, then run architecture gates.** First write `checker_scope_regression.sh` with temporary miniature BCS trees: missing expected source directories must fail, an injected `DbPlugin` reference must fail, and a valid fixture with all expected protocol test categories must pass. Invoke scripts from both the repository root and BCS root. The current protocol checker uses obsolete `crates/service-api/bcs-protocol/tests`; the Store checker can print PASS after `rg` reports missing paths.

Fix both scripts to resolve BCS_ROOT from their own SCRIPT_DIR and scan it deterministically. Point the protocol checker at `crates/contracts/bcs-protocol/tests`, retaining all required test-category assertions. In the Store checker prevalidate target directories and distinguish `rg` exit 0 (forbidden match), 1 (no match), and >1 (scan error); missing trees/scan errors fail, not PASS/SKIP. Missing optional executables may retain documented SKIP behavior. These are check repairs, not exemptions.

```bash
bash src/bcs/scripts/ci/tests/checker_scope_regression.sh
(
  cd src/bcs
  bash scripts/ci/arch-check.sh
)
```

Expected: the regression script proves actual source is scanned and errors cannot masquerade as PASS. No transport dependency enters core, no concrete Provider enters message-flow, and config boundaries remain enforced. Record each PASS/SKIP/FAIL; unresolved required gates are not completion. The Rust public-API check requires its executable, an actual base ref and propagation/version evidence; install/use the documented toolchain or explicitly record it unrun, never suppress its findings.

- [ ] **Step 4: Run workspace compilation and tests.**

```bash
cd src/bcs
cargo check --workspace --all-targets
cargo test --workspace
```

Expected: the complete workspace compiles and all tests pass. If a test fixture fails because it constructs a Group literal, add only the missing `HumanMentionNotifyMode::default()` field; do not make the domain field optional to avoid updating fixtures.

- [ ] **Step 5: Inspect Plugin and Session boundaries.**

```bash
rg -n "HumanMentionNotify|bcs-human-notify|human_notify.providers|human_mention_notify_mode" \
  src/bcs/crates/plugin-api/bcs-human-notify-api \
  src/bcs/crates/plugins/bcs-human-notify-dummy \
  src/bcs/crates/plugins/bcs-human-notify-work-order \
  src/bcs/crates/services/bcs-session* \
  src/bcs/crates/service-api/bcs-service-api/src/port/human_notify.rs
```

Expected: only the approved existing notification types and no new mode field in Plugin or Session contracts. The new mode references should remain in Group/domain/application/message-flow code and HTTP contracts.

- [ ] **Step 6: Run hygiene checks against the complete implementation diff.** Use the preflight `BASE_SHA`, not the last commit or only the index. From the repository root, run this as one fail-fast shell block:

```bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
: "${BASE_SHA:?restore the recorded implementation base SHA}"
export BASE_SHA
git diff --check "$BASE_SHA..HEAD"
git diff --check
git diff --cached --check
python3 - <<'PY_CHECK'
import os
import subprocess
from pathlib import Path

def paths(*args):
    raw = subprocess.check_output(["git", *args])
    return {os.fsdecode(item) for item in raw.split(b"\0") if item}

base = os.environ["BASE_SHA"]
changed = paths("diff", "--name-only", "-z", "--diff-filter=AMR", f"{base}..HEAD")
changed |= paths("diff", "--name-only", "-z", "--diff-filter=AMR")
changed |= paths("diff", "--cached", "--name-only", "-z", "--diff-filter=AMR")
changed |= paths("ls-files", "--others", "--exclude-standard", "-z")
violations = []
for name in sorted(changed):
    path = Path(name)
    if path.is_file() and path.suffix in {".rs", ".ts", ".tsx", ".js", ".jsx", ".py", ".sh"}:
        lines = len(path.read_bytes().splitlines())
        print(f"{lines:5d} {name}")
        if lines > 1000:
            violations.append(name)
if violations:
    raise SystemExit("Split over-limit sources; no automatic historical-file exemption: "
                     + ", ".join(violations))
PY_CHECK
git status --short
```

This conservative local check has no built-in allowlist. If a formally approved repository exception is necessary,
run the actual CI allowlist-aware gate as separate recorded evidence rather than editing this snippet to silently skip
files. Without that evidence, any over-limit result is a failure. Source splits belong in their owning tasks and must
not be postponed until this final check. Do not run global formatting.

- [ ] **Step 7: Review the final diff against the approved spec.** Confirm all of the following before merge:

- default is `all`;
- the dedicated current-policy Service/Repo contract is implemented by production Group Core/Store and defaults fail closed;
- Group Core recognizes a Patch containing only the new field and preserves existing no-op/version semantics;
- both `PATCH /openapi/v1/collaboration/groups/{id}` and `PATCH /groups/{id}` support the field;
- `PATCH /groups/{id}/settings` remains service-spec-only;
- normal and Dm Group responses expose the field;
- Dm Groups still do not emit external Human Notify messages;
- `driver_bot_only` uses `group.driver_bot`, which is the manager in `manager_worker` Groups;
- notification gates use the dedicated current-policy read rather than the early Group snapshot/cache;
- suppressed or policy-read-failed messages still persist and route normally;
- existing Session state is not rewritten;
- SQLite baseline is unchanged and only the new versioned migration adds the column;
- no `human_notify` Plugin or Provider code changed;
- MySQL migration-chain and MySQL Store behavior are separate verified items;
- SQL, SQLite, Memory, HTTP, OpenAPI, and message-flow tests cover the new behavior.

- [ ] **Step 8: Commit the final verification/documentation state if changes remain.**

```bash
git add -p -- src/bcs .github/workflows/unit-tests.yml
# Inspect staged paths; include only the approved implementation/test/gate/doc edits.
git diff --cached --check
git commit -m "test(bcs): verify group human mention notify mode"
# Repeat Step 6 after this commit with the same BASE_SHA; do not create an empty commit.
```

---

## Plan self-review checklist

- **Spec coverage:** Task 1 covers the Group enum/default and fail-closed policy-read contract. Task 2 covers MySQL/SQLite/Memory persistence, additive migration, unchanged SQLite baseline, invalid stored values, cold-read behavior, cache invalidation, current-policy reads, and real MySQL verification. Task 3 covers Group Core change detection, both Patch APIs, response projections, strict input validation, and OpenAPI. Task 4 covers all three message-flow entry points, current-policy read semantics, sender matrix, Dm behavior, manager-worker behavior, Session-wide effect, fail-closed read errors, query-count bounds, and unchanged message persistence/routing. Tasks 5–6 cover conformance, architecture, OpenAPI, workspace, and complete-diff hygiene verification.
- **Type consistency:** The single domain type is `HumanMentionNotifyMode`; the Group field is non-optional; Patch fields are `Option<HumanMentionNotifyMode>`; response fields are non-optional; `GroupHumanNotifyPolicy` carries only mode/driver; Store conversion accepts `Option<&str>` only for legacy missing/NULL reads.
- **Boundary consistency:** No new `HumanMentionNotifyPort` or Plugin API field is introduced. The decision is made after a dedicated current Group policy read and before Provider invocation; read failures fail closed without changing the main message result.
- **Migration consistency:** MySQL version 030 and SQLite version 031 are new additive migrations; existing migration files, checksums and SQLite baseline remain unchanged. Fresh and upgrade paths both execute the versioned SQLite migration.
- **Verification consistency:** OpenAPI validation uses `validate_openapi_contract.py` plus the Group contract test; protocol-presence checks remain separate. Final hygiene checks `BASE_SHA..HEAD`, not only the index.
- **Completeness review:** The plan contains no open-ended “handle edge cases” step or unspecified file assignment. Task 2's shared Store harness is the single source for Memory/SQLite/MySQL behavior; Task 4's message-flow tests cover the single-policy-read boundary. Every revised implementation slice has an explicit contract, implementation location, focused verification command, and commit boundary. This document records required future checks, not evidence that they have already run.
