use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
use async_trait::async_trait;
use bcs_bot_store::provider::{DbProviderStore, MemoryProviderStore};
use bcs_db_api::{DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow,
    DbStatement, DbTransactionStep, DbTransactionStepResult, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{ProviderRepoPort, ServiceError};
use bcs_test_support::contract::provider_slug::provider_repo_port_contract_tests;

async fn database() -> Arc<LocalSqliteDbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new("CREATE TABLE bcs_providers (
        provider_id TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL, config TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL, owners TEXT NOT NULL,
        gmt_create TEXT DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (env, provider_id))")).await.unwrap();
    for sql in include_str!("../../../../migrations/sqlite/033_provider_slug.sql")
        .split(';').map(str::trim).filter(|sql| !sql.is_empty()) {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    db
}

#[tokio::test]
async fn conformance_memory_provider_slug() {
    provider_repo_port_contract_tests(&MemoryProviderStore::new()).await;
}

#[tokio::test]
async fn conformance_sqlite_provider_slug() {
    provider_repo_port_contract_tests(&DbProviderStore::sqlite(database().await)).await;
}

#[tokio::test]
async fn sqlite_lookup_is_environment_scoped_and_uses_the_unique_index() {
    let db = database().await;
    let env = bcs_config::resolve_env_str();
    for (id, environment) in [("local", env.as_str()), ("foreign", "other-slug-env")] {
        db.execute(DbStatement::with_params("INSERT INTO bcs_providers
            (provider_id, env, name, config, created_by, owners, slug) VALUES (?, ?, 'P', '{}', 'alice', '[]', 'shared')",
            vec![id.into(), environment.into()])).await.unwrap();
    }
    let store = DbProviderStore::sqlite(db.clone());
    assert_eq!(store.get_provider_by_slug("shared").await.unwrap().unwrap().provider_id, "local");
    let plan = db.query(DbStatement::with_params("EXPLAIN QUERY PLAN SELECT provider_id
        FROM bcs_providers WHERE env = ? AND slug = ? LIMIT 1", vec![env.into(), "shared".into()]))
        .await.unwrap();
    assert!(plan.iter().any(|row| matches!(row.get("detail"), Some(DbValue::String(detail))
        if detail.contains("uk_bcs_providers_env_slug"))));
}

struct ObservedDb {
    db: Arc<LocalSqliteDbPlugin>, queries: AtomicUsize, fail_reads: bool, fail_writes: bool,
}

#[async_trait]
impl DbPlugin for ObservedDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.fetch_add(1, Ordering::Relaxed);
        if self.fail_reads { return Err(DbError::Backend("read unavailable".into())); }
        self.db.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        if self.fail_writes { return Err(DbError::Backend("write unavailable".into())); }
        self.db.execute(statement).await
    }
    async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        self.db.transaction(steps).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> { self.db.health_check().await }
}

#[tokio::test]
async fn slug_lookup_is_one_read_and_errors_do_not_become_absence() {
    let db = database().await;
    for fail_reads in [false, true] {
        let observed = Arc::new(ObservedDb { db: db.clone(), queries: AtomicUsize::new(0),
            fail_reads, fail_writes: false });
        let store = DbProviderStore::sqlite(observed.clone());
        let result = store.get_provider_by_slug("missing").await;
        if fail_reads { assert!(matches!(result, Err(ServiceError::InternalError(_)))); }
        else { assert!(result.unwrap().is_none()); }
        assert_eq!(observed.queries.load(Ordering::Relaxed), 1);
    }
}

#[tokio::test]
async fn failed_slug_write_is_an_error() {
    let observed = Arc::new(ObservedDb { db: database().await, queries: AtomicUsize::new(0),
        fail_reads: false, fail_writes: true });
    let store = DbProviderStore::sqlite(observed);
    assert!(matches!(store.update_provider_metadata("one", None, None, Some("new"), 1).await,
        Err(ServiceError::InternalError(_))));
}

#[tokio::test]
async fn malformed_slug_value_is_an_error_in_provider_reads() {
    let db = database().await;
    db.execute(DbStatement::with_params("INSERT INTO bcs_providers
        (provider_id, env, name, config, created_by, owners, slug)
        VALUES ('one', ?, 'P', '{}', 'alice', '[]', ?)",
        vec![bcs_config::resolve_env_str().into(), DbValue::Bytes(b"first".to_vec())]))
        .await.unwrap();
    let store = DbProviderStore::sqlite(db);
    assert!(matches!(store.get_provider("one").await, Err(ServiceError::InternalError(_))));
    assert!(matches!(store.list_providers_by_ids(&["one".into()]).await,
        Err(ServiceError::InternalError(_))));
    assert!(matches!(store.list_providers().await, Err(ServiceError::InternalError(_))));
}
