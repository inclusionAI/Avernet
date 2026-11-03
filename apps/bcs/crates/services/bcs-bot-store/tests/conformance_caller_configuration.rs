//! Caller-configuration storage conformance (P1.1/P1.2 storage batch).
//!
//! Locks the `bcs_bots.bot_info.$.caller` sync/read contract against a real
//! SQLite store: the §10.7.2 revision matrix, target scoping, preservation
//! of other bot_info fields through the binary CAS, and refusal to
//! interpret broken data as "disabled".

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::BTreeMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue as Value,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::{
    BotCallerConfiguration, BotControlPlaneRepoPort, CallerConfigurationSync,
    CallerIdentitySnapshot,
};
use serde_json::json;

fn snapshot(revision: u64, enabled: bool) -> CallerIdentitySnapshot {
    serde_json::from_value(json!({
        "source_revision": revision,
        "caller_identity": { "enabled": enabled }
    }))
    .expect("valid snapshot")
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bot_uuid TEXT NOT NULL,
            name TEXT NOT NULL,
            bot_info TEXT,
            session_token TEXT,
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            env TEXT,
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            status TEXT NOT NULL DEFAULT 'online',
            is_deleted INTEGER NOT NULL DEFAULT 0,
            UNIQUE (bot_uuid, env)
        )",
    ))
    .await
    .expect("create bots table");
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_provider_bot_bindings (
            bot_uuid TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            env TEXT NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0,
            UNIQUE (env, bot_uuid)
        )",
    ))
    .await
    .expect("create bindings table");
    db
}

async fn seed_binding(db: &dyn DbPlugin, bot_id: &str, provider_id: &str, disabled: bool) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_provider_bot_bindings (bot_uuid, provider_id, env, disabled) VALUES (?, ?, 'dev', ?)",
        vec![
            Value::from(bot_id.to_string()),
            Value::from(provider_id.to_string()),
            Value::from(i64::from(disabled)),
        ],
    ))
    .await
    .expect("seed binding");
}

#[tokio::test]
async fn provider_scope_follows_the_canonical_binding() {
    let (repo, db) = fixture().await;
    seed_bot(db.as_ref(), "bot-a", plain_bot_info()).await;
    seed_binding(db.as_ref(), "bot-a", "provider-a", false).await;

    // Correct scope reads and syncs through the binding.
    let config = repo
        .get_provider_caller_configuration("bot-a", "dev", "provider-a")
        .await
        .expect("read")
        .expect("bound bot exists");
    assert_eq!(config, BotCallerConfiguration { snapshot: None });
    let outcome = repo
        .sync_provider_caller_configuration("bot-a", "dev", "provider-a", snapshot(5, true))
        .await
        .expect("sync")
        .expect("bound target");
    assert!(matches!(outcome, CallerConfigurationSync::Applied { .. }));

    // Another provider, no binding at all and disabled bindings are all out
    // of scope — not "unconfigured", and never auto-claimed.
    assert!(repo
        .get_provider_caller_configuration("bot-a", "dev", "provider-b")
        .await
        .expect("read")
        .is_none());
    assert!(repo
        .sync_provider_caller_configuration("bot-a", "dev", "provider-b", snapshot(9, true))
        .await
        .expect("sync")
        .is_none());

    let (repo, db) = fixture().await;
    seed_bot(db.as_ref(), "bot-unbound", plain_bot_info()).await;
    assert!(repo
        .get_provider_caller_configuration("bot-unbound", "dev", "provider-a")
        .await
        .expect("read")
        .is_none());

    let (repo, db) = fixture().await;
    seed_bot(db.as_ref(), "bot-off", plain_bot_info()).await;
    seed_binding(db.as_ref(), "bot-off", "provider-a", true).await;
    assert!(repo
        .sync_provider_caller_configuration("bot-off", "dev", "provider-a", snapshot(5, true))
        .await
        .expect("sync")
        .is_none());
}

async fn fixture() -> (PersistentBotRepo, Arc<dyn DbPlugin>) {
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Sqlite);
    (repo, db)
}

async fn seed_bot(db: &dyn DbPlugin, bot_id: &str, bot_info: serde_json::Value) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, bot_info, env) VALUES (?, ?, ?, 'dev')",
        vec![
            Value::from(bot_id.to_string()),
            Value::from(format!("Bot {bot_id}")),
            if bot_info.is_null() {
                Value::Null
            } else {
                Value::from(bot_info.to_string())
            },
        ],
    ))
    .await
    .expect("seed bot row");
}

fn plain_bot_info() -> serde_json::Value {
    json!({ "summary": "existing summary", "scopes": ["workspace"] })
}

#[tokio::test]
async fn unknown_and_deleted_targets_are_outer_none() {
    let (repo, _db) = fixture().await;
    // Never registered.
    assert!(repo.get_caller_configuration("ghost", "dev").await.expect("read").is_none());
    // Deleted bots are out of scope, not "unconfigured".
    assert!(repo
        .sync_caller_configuration("ghost", "dev", snapshot(1, true))
        .await
        .expect("sync")
        .is_none());
}

#[tokio::test]
async fn present_bot_without_caller_object_is_inner_none_not_disabled() {
    let (repo, db) = fixture().await;
    seed_bot(db.as_ref(), "bot-a", plain_bot_info()).await;
    let config = repo
        .get_caller_configuration("bot-a", "dev")
        .await
        .expect("read")
        .expect("bot exists");
    assert_eq!(config, BotCallerConfiguration { snapshot: None });
}

#[tokio::test]
async fn first_sync_applies_and_preserves_sibling_bot_info_fields() {
    let (repo, db) = fixture().await;
    seed_bot(db.as_ref(), "bot-a", plain_bot_info()).await;

    let outcome = repo
        .sync_caller_configuration("bot-a", "dev", snapshot(7, true))
        .await
        .expect("sync")
        .expect("target");
    assert!(matches!(&outcome, CallerConfigurationSync::Applied { .. }));

    let config = repo
        .get_caller_configuration("bot-a", "dev")
        .await
        .expect("read")
        .expect("bot");
    assert_eq!(config.snapshot, Some(snapshot(7, true)));
    // Sibling fields survive the caller write.
    let rows = db
        .query(DbStatement::new(
            "SELECT bot_info FROM bcs_bots WHERE bot_uuid = 'bot-a' AND env = 'dev'",
        ))
        .await
        .expect("raw read");
    let raw: String = bcs_db_api::db_get_column(&rows[0], "bot_info").expect("bot_info present");
    let value: serde_json::Value = serde_json::from_str(&raw).expect("stored json");
    assert_eq!(value["summary"], "existing summary");
    assert_eq!(value["scopes"][0], "workspace");
    assert_eq!(value["caller"]["source_revision"], 7);
}

#[tokio::test]
async fn revision_matrix_is_authoritative() {
    let (repo, db) = fixture().await;
    seed_bot(db.as_ref(), "bot-a", plain_bot_info()).await;
    repo.sync_caller_configuration("bot-a", "dev", snapshot(10, true))
        .await
        .expect("apply")
        .expect("target");

    // Same revision, same content -> unchanged, no write.
    let outcome = repo
        .sync_caller_configuration("bot-a", "dev", snapshot(10, true))
        .await
        .expect("sync")
        .expect("target");
    assert!(matches!(outcome, CallerConfigurationSync::Unchanged { .. }));

    // Same revision, different content -> conflict, revision only.
    let outcome = repo
        .sync_caller_configuration("bot-a", "dev", snapshot(10, false))
        .await
        .expect("sync")
        .expect("target");
    assert_eq!(outcome, CallerConfigurationSync::Conflict { source_revision: 10 });

    // Lower revision -> stale, authoritative snapshot returned.
    let outcome = repo
        .sync_caller_configuration("bot-a", "dev", snapshot(9, true))
        .await
        .expect("sync")
        .expect("target");
    assert_eq!(
        outcome,
        CallerConfigurationSync::Stale { snapshot: snapshot(10, true) }
    );

    // Higher revision applies, including an explicit disable.
    let outcome = repo
        .sync_caller_configuration("bot-a", "dev", snapshot(11, false))
        .await
        .expect("sync")
        .expect("target");
    assert!(matches!(outcome, CallerConfigurationSync::Applied { .. }));
    let config = repo
        .get_caller_configuration("bot-a", "dev")
        .await
        .expect("read")
        .expect("bot");
    assert_eq!(config.snapshot, Some(snapshot(11, false)));
}

#[tokio::test]
async fn legacy_null_bot_info_row_accepts_first_sync() {
    let (repo, db) = fixture().await;
    seed_bot(db.as_ref(), "legacy", serde_json::Value::Null).await;
    let outcome = repo
        .sync_caller_configuration("legacy", "dev", snapshot(1, true))
        .await
        .expect("sync")
        .expect("target");
    assert!(matches!(outcome, CallerConfigurationSync::Applied { .. }));
    assert_eq!(
        repo.get_caller_configuration("legacy", "dev")
            .await
            .expect("read")
            .expect("bot")
            .snapshot,
        Some(snapshot(1, true))
    );
}

#[tokio::test]
async fn broken_caller_data_is_an_error_never_disabled() {
    let (repo, db) = fixture().await;

    // `caller: null` must not be reported as "never configured".
    seed_bot(db.as_ref(), "null-caller", plain_bot_info()).await;
    db.execute(DbStatement::with_params(
        "UPDATE bcs_bots SET bot_info = ? WHERE bot_uuid = 'null-caller'",
        vec![Value::from(
            json!({ "summary": "x", "caller": null }).to_string(),
        )],
    ))
    .await
    .expect("break caller");
    assert!(repo.get_caller_configuration("null-caller", "dev").await.is_err());

    // A malformed snapshot is an error, and a sync against it fails too —
    // never silently disabled and never overwritten.
    seed_bot(db.as_ref(), "bad-caller", plain_bot_info()).await;
    db.execute(DbStatement::with_params(
        "UPDATE bcs_bots SET bot_info = ? WHERE bot_uuid = 'bad-caller'",
        vec![Value::from(
            json!({ "summary": "x", "caller": { "source_revision": "not-a-number" } }).to_string(),
        )],
    ))
    .await
    .expect("break snapshot");
    assert!(repo.get_caller_configuration("bad-caller", "dev").await.is_err());
    assert!(repo
        .sync_caller_configuration("bad-caller", "dev", snapshot(5, true))
        .await
        .is_err());
}

/// Fake plugin: reads yield a bot row without a caller object, while the
/// conditional update affects zero rows (simulating a lost CAS race).
struct ContendedDb;

#[async_trait]
impl DbPlugin for ContendedDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        Ok(vec![DbRow::new(BTreeMap::from([
            ("bot_info".to_string(), Value::from(plain_bot_info().to_string())),
        ]))])
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        // The CAS comparison fails: zero rows changed, target still exists.
        Ok(DbExecuteResult { affected_rows: 0, last_insert_id: None })
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        Ok(Vec::new())
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

#[tokio::test]
async fn lost_cas_race_re_reads_once_then_conflicts_instead_of_overwriting() {
    let repo = PersistentBotRepo::with_sql_flavor(Arc::new(ContendedDb), DbSqlFlavor::Sqlite);
    // First read sees no caller object -> Apply; the CAS loses the race;
    // the single allowed re-read still wants to write -> Conflict, never an
    // uncritical overwrite.
    let outcome = repo
        .sync_caller_configuration("bot-a", "dev", snapshot(3, true))
        .await
        .expect("sync must resolve, not error")
        .expect("target exists");
    assert_eq!(outcome, CallerConfigurationSync::Conflict { source_revision: 3 });
}
/// §15.96 batch read: ONE round trip for a whole admission scan, with
/// per-target results identical to the single reads — absent targets
/// excluded, arrival order following first occurrence, duplicates answered
/// once.
#[tokio::test]
async fn batch_read_matches_single_reads_and_first_occurrence_order() {
    let (repo, db) = fixture().await;
    seed_bot(db.as_ref(), "configured-on", plain_bot_info()).await;
    seed_bot(db.as_ref(), "configured-off", plain_bot_info()).await;
    seed_bot(db.as_ref(), "never-configured", plain_bot_info()).await;
    seed_bot(db.as_ref(), "deleted-bot", plain_bot_info()).await;
    db.execute(DbStatement::with_params(
        "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = 'deleted-bot'",
        vec![],
    ))
    .await
    .expect("soft-delete");
    repo.sync_caller_configuration("configured-on", "dev", snapshot(5, true))
        .await
        .expect("sync on")
        .expect("target");
    repo.sync_caller_configuration("configured-off", "dev", snapshot(7, false))
        .await
        .expect("sync off")
        .expect("target");

    let ids = [
        "configured-off", // order follows FIRST occurrence, not lexicographic
        "configured-off", // duplicate
        "configured-on",
        "never-configured",
        "ghost",
        "deleted-bot",
        "configured-on", // duplicate of a mid-list entry
    ]
    .iter()
    .map(|id| id.to_string())
    .collect::<Vec<_>>();

    let batched = repo
        .get_caller_configurations(&ids, "dev")
        .await
        .expect("batch read");
    let mut expected: Vec<(String, BotCallerConfiguration)> = Vec::new();
    for id in ["configured-off", "configured-on", "never-configured"] {
        let single = repo
            .get_caller_configuration(id, "dev")
            .await
            .expect("single read")
            .expect("present in batch");
        expected.push((id.to_string(), single));
    }
    assert_eq!(batched, expected, "batch and single reads must agree per target, in first-occurrence order");
}

/// §15.96 write-through invalidation: a successful sync empties the
/// read-through cache, so the NEXT SINGLE READ of the same process sees
/// the fresh snapshot immediately — the cross-instance TTL is the ONLY
/// staleness left.
#[tokio::test]
async fn sync_invalidates_the_read_cache_immediately() {
    let (repo, db) = fixture().await;
    seed_bot(db.as_ref(), "bot-a", plain_bot_info()).await;

    // Populate the cache with the never-configured answer.
    let cold = repo
        .get_caller_configuration("bot-a", "dev")
        .await
        .expect("cold read")
        .expect("bot exists");
    assert_eq!(cold, BotCallerConfiguration { snapshot: None });

    // Enable through the sync port: the read cache entry dies with it.
    repo.sync_caller_configuration("bot-a", "dev", snapshot(4, true))
        .await
        .expect("sync")
        .expect("target");

    let hot = repo
        .get_caller_configuration("bot-a", "dev")
        .await
        .expect("hot read must see the fresh sync, not the cached None");
    assert_eq!(hot.expect("bot exists").snapshot.expect("configured"), snapshot(4, true));

    // And the batch path agrees — its peek answered from the refilled
    // post-sync entry, no new storage read needed.
    let batched = repo
        .get_caller_configurations(&["bot-a".to_string()], "dev")
        .await
        .expect("batch read");
    assert_eq!(
        batched,
        vec![("bot-a".to_string(), BotCallerConfiguration { snapshot: Some(snapshot(4, true)) })]
    );

    // The cache itself still works between syncs: a raw SQL update bypassing
    // the port is absorbed until the TTL passes (that is the documented
    // cross-... well, load-bearing staleness boundary of the design; the
    // port carries freshness, the cache carries locality).
    db.execute(DbStatement::with_params(
        "UPDATE bcs_bots SET bot_info = json_set(bot_info, '$.caller.source_revision', 99) WHERE bot_uuid = 'bot-a'",
        vec![],
    ))
    .await
    .expect("raw update");
    let still_cached = repo
        .get_caller_configuration("bot-a", "dev")
        .await
        .expect("read")
        .expect("bot exists");
    assert_eq!(still_cached.snapshot.expect("configured").source_revision, 4, "within-TTL raw writes stay cached by design");
}
