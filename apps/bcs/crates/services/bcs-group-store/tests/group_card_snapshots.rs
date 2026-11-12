use std::sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}};
use async_trait::async_trait;
use bcs_db_api::{DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep, DbTransactionStepResult};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group_store::{MemoryGroupRepo, MySqlGroupStore};
use bcs_service_api::port::repo::GroupRepoPort;
use bcs_service_api::{Group, GroupStrategy};
use serde_json::json;

#[tokio::test]
async fn memory_group_card_snapshot_contract() {
    bcs_test_support::contract::group_card::group_card_snapshot_contract(&MemoryGroupRepo::new()).await;
}

#[tokio::test]
async fn sqlite_group_card_snapshot_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let repo = MySqlGroupStore::sqlite(db, "cards".into());
    bcs_test_support::contract::group_card::group_card_snapshot_contract(&repo).await;
}

struct Probe {
    db: Arc<dyn DbPlugin>,
    queries: Mutex<Vec<String>>,
    fail: AtomicBool,
}
#[async_trait]
impl DbPlugin for Probe {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.lock().unwrap().push(statement.sql().into());
        if self.fail.load(Ordering::SeqCst) { return Err(DbError::Backend("snapshot unavailable".into())); }
        self.db.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> { self.db.execute(statement).await }
    async fn health_check(&self) -> DbResult<DbHealth> { self.db.health_check().await }
    async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { self.db.transaction(steps).await }
}

#[tokio::test]
async fn snapshot_is_one_current_env_scoped_read_and_never_hides_errors_or_backfills() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let probe = Arc::new(Probe { db: db.clone(), queries: Mutex::new(vec![]), fail: false.into() });
    let repo = MySqlGroupStore::sqlite(probe.clone(), "cards".into());
    let other = MySqlGroupStore::sqlite(db.clone(), "other".into());
    let mut group = Group::new("snapshot", "manager", vec![]);
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.label = Some("cached name".into());
    repo.upsert(group.clone()).await.unwrap();
    other.upsert(group).await.unwrap();
    repo.try_get("snapshot").await.unwrap(); // Warm the ordinary display cache.
    let mut stored = repo.read_card_snapshot("snapshot").await.unwrap().unwrap().card_template.unwrap();
    stored["description"] = json!("old prefix and name");
    stored["skills"][0]["description"] = json!("old prefix and name");
    stored["opaque"] = json!({"preserve": [1, 2]});
    stored["supportedInterfaces"][0]["url"] = json!("https://example.com/group");
    db.execute(DbStatement::with_params("UPDATE bcs_group_agent_cards SET card_template = ? WHERE env = ? AND group_id = ?",
        vec![stored.to_string().into(), "cards".into(), "snapshot".into()])).await.unwrap();
    db.execute(DbStatement::with_params("UPDATE bcs_groups SET label = ?, context = ?, visibility = ? WHERE env = ? AND group_id = ?",
        vec!["current name".into(), "current goal".into(), "public".into(), "cards".into(), "snapshot".into()])).await.unwrap();
    probe.queries.lock().unwrap().clear();
    let snapshot = repo.read_card_snapshot("snapshot").await.unwrap().unwrap();
    let queries = probe.queries.lock().unwrap().clone();
    assert_eq!(queries.len(), 1);
    assert!(queries[0].contains("LIMIT 1"));
    assert!(!queries[0].contains("bcs_group_participants"));
    assert_eq!(snapshot.visibility, "public");
    let card = snapshot.card_template.unwrap();
    assert_eq!(card["name"], "current name");
    assert_eq!(card["description"], "current goal");
    assert_eq!(card["skills"][0]["description"], "current goal");
    assert_eq!(card["opaque"], stored["opaque"]);
    assert_eq!(card["supportedInterfaces"], stored["supportedInterfaces"]);
    assert_eq!(other.read_card_snapshot("snapshot").await.unwrap().unwrap().card_template.unwrap()["name"], "cached name");
    let rows = db.query(DbStatement::new("SELECT card_template FROM bcs_group_agent_cards WHERE env = 'cards'")).await.unwrap();
    assert_eq!(rows[0].get_string("card_template").unwrap().unwrap(), stored.to_string(), "snapshot reads must not write back");
    probe.fail.store(true, Ordering::SeqCst);
    assert!(repo.read_card_snapshot("snapshot").await.is_err(), "a warm Group cache must not hide read errors");
    probe.fail.store(false, Ordering::SeqCst);
    for invalid in ["{bad json", "[]", r#"{"skills":[]}"#] {
        db.execute(DbStatement::with_params("UPDATE bcs_group_agent_cards SET card_template = ? WHERE env = 'cards'", vec![invalid.into()])).await.unwrap();
        assert!(repo.read_card_snapshot("snapshot").await.is_err());
    }
    db.execute(DbStatement::new("DELETE FROM bcs_group_agent_cards WHERE env = 'cards'")).await.unwrap();
    assert!(repo.read_card_snapshot("snapshot").await.unwrap().unwrap().card_template.is_none());
    assert!(db.query(DbStatement::new("SELECT group_id FROM bcs_group_agent_cards WHERE env = 'cards'")).await.unwrap().is_empty());
}
