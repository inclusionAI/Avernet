use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
use async_trait::async_trait;
use bcs_bot_store::provider::{DbProviderStore, MemoryProviderStore};
use bcs_db_api::{DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow,
    DbStatement, DbTransactionStep, DbTransactionStepResult};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{ProviderRecord, ProviderRepoPort, ServiceError};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;

async fn database() -> Arc<LocalSqliteDbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    for sql in ["CREATE TABLE bcs_providers (
        provider_id TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL, config TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL, owners TEXT NOT NULL,
        slug TEXT, gmt_create TEXT DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (env, provider_id))",
        "CREATE TABLE bcs_bots (bot_uuid TEXT, env TEXT, created_by TEXT,
        provider_id TEXT, is_deleted INTEGER DEFAULT 0, PRIMARY KEY (env, bot_uuid))"] {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    db
}

fn provider() -> ProviderRecord {
    ProviderRecord { provider_id: "local".into(), slug: None, name: "Local".into(),
        config: r#"{"collaboration_scope":"same_owner"}"#.into(), created_by: "alice".into(),
        owners: r#"["alice"]"#.into(), disabled: false, created_at: 1, updated_at: 1 }
}

async fn monotonic_contract(store: &dyn ProviderRepoPort) {
    store.insert_provider(provider()).await.unwrap();
    // A stale PATCH may contain config read before another request tightened it.
    let error = store.update_provider_metadata("local", Some("Rejected rename"), Some("{}"), None, 2).await.unwrap_err();
    assert!(matches!(error, ServiceError::Forbidden(_)));
    let current = store.get_provider("local").await.unwrap().unwrap();
    assert_eq!(current.name, "Local", "a rejected policy write must be atomic");
    assert!(current.config.contains("same_owner"));
    store.update_provider_metadata("local", Some("Renamed"), None, None, 3).await.unwrap();
    assert!(store.get_provider("local").await.unwrap().unwrap().config.contains("same_owner"));
}

#[tokio::test]
async fn memory_and_sqlite_reject_stale_relaxation_atomically() {
    monotonic_contract(&MemoryProviderStore::new()).await;
    monotonic_contract(&DbProviderStore::sqlite(database().await)).await;
}

struct ObservedDb {
    db: Arc<LocalSqliteDbPlugin>, queries: AtomicUsize, fail: bool,
}
#[async_trait]
impl DbPlugin for ObservedDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.fetch_add(1, Ordering::Relaxed);
        if self.fail { return Err(DbError::Backend("read unavailable".into())); }
        self.db.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> { self.db.execute(statement).await }
    async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { self.db.transaction(steps).await }
    async fn health_check(&self) -> DbResult<DbHealth> { self.db.health_check().await }
}

#[tokio::test]
async fn projection_is_one_fresh_bounded_environment_scoped_query() {
    let db = database().await;
    let setup = DbProviderStore::sqlite(db.clone());
    setup.insert_provider(provider()).await.unwrap();
    let env = bcs_config::resolve_env_str();
    for (id, environment, provider_id) in [("local-bot", env.as_str(), "local"),
        ("orphan", env.as_str(), "absent"), ("foreign", "other-env", "local")] {
        db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, env, created_by, provider_id) VALUES (?, ?, 'alice', ?)",
            vec![id.into(), environment.into(), provider_id.into()])).await.unwrap();
    }
    let observed = Arc::new(ObservedDb { db: db.clone(), queries: AtomicUsize::new(0), fail: false });
    let store = DbProviderStore::sqlite(observed.clone());
    for size in [2, 256] {
        let mut ids = (0..size).map(|i| format!("missing-{i}")).collect::<Vec<_>>();
        ids[0] = "local-bot".into(); ids[1] = "foreign".into();
        observed.queries.store(0, Ordering::Relaxed);
        let rows = store.collaboration_actors(&ids).await.unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].owner.as_deref(), Some("alice"));
        assert_eq!(observed.queries.load(Ordering::Relaxed), 1);
    }
    observed.queries.store(0, Ordering::Relaxed);
    assert!(store.collaboration_actors(&vec!["local-bot".into(); 257]).await.is_err());
    assert_eq!(observed.queries.load(Ordering::Relaxed), 0);
    let rows = store.collaboration_actors(&["orphan".into()]).await.unwrap();
    assert_eq!(rows[0].provider_id.as_deref(), Some("absent"));
    assert!(rows[0].provider_config.is_none(), "missing affiliation must remain distinguishable");
    db.execute(DbStatement::with_params("UPDATE bcs_providers SET disabled = 1 WHERE env = ?", vec![env.into()])).await.unwrap();
    assert!(store.collaboration_actors(&["local-bot".into()]).await.unwrap()[0].provider_disabled,
        "authorization must observe policy changes without stale caches");
    let failed = DbProviderStore::sqlite(Arc::new(ObservedDb { db, queries: AtomicUsize::new(0), fail: true }));
    assert!(matches!(failed.collaboration_actors(&["local-bot".into()]).await, Err(ServiceError::InternalError(_))));
}
