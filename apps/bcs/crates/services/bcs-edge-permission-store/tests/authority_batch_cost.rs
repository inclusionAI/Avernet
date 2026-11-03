//! Count actual SQL statements and returned rows, not repository method calls.
use std::sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}};
use bcs_db_api::*;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{BotAccessRelation, BotRoleQuery, AUTHORITY_QUERY_CHUNK};
use bcs_test_support::bot_authority_sqlite::{execute, new_fixture_with_db};
use bcs_test_support::contract::bot_authority_contract::BotAuthorityContractFixture;

#[derive(Default)]
struct Reads { count: usize, edge_queries: usize, edge_rows: usize, max_edge_params: usize }
struct CountingDb {
    inner: Arc<dyn DbPlugin>,
    reads: Mutex<Reads>,
    fail_edges: AtomicBool,
}
#[async_trait::async_trait]
impl DbPlugin for CountingDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let edge = statement.sql().starts_with("SELECT from_id, to_id, grant_kind FROM edge_grants");
        if edge && self.fail_edges.load(Ordering::SeqCst) {
            // Use the real plugin error type without fabricating a successful deny.
            return self.inner.query(DbStatement::new("SELECT * FROM missing_authority_table")).await;
        }
        let params = statement.params().len();
        let rows = self.inner.query(statement).await?;
        let mut reads = self.reads.lock().unwrap();
        reads.count += 1;
        if edge {
            reads.edge_queries += 1;
            reads.edge_rows += rows.len();
            reads.max_edge_params = reads.max_edge_params.max(params);
        }
        Ok(rows)
    }
    async fn execute(&self, s: DbStatement) -> DbResult<DbExecuteResult> { self.inner.execute(s).await }
    async fn transaction(&self, s: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { self.inner.transaction(s).await }
    async fn transaction_with_options(&self, s: Vec<DbTransactionStep>, o: DbTransactionOptions) -> DbResult<Vec<DbTransactionStepResult>> { self.inner.transaction_with_options(s,o).await }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}

#[tokio::test]
async fn same_bot_many_viewers_uses_bounded_pair_queries() {
    for count in [1, 64, 65, 100, 257] {
        let db = Arc::new(CountingDb { inner: Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap()),
            reads: Mutex::new(Reads::default()), fail_edges: AtomicBool::new(false) });
        let f = new_fixture_with_db("batch", db.clone()).await;
        f.seed_bot("batch", "bot", 1).await;
        f.grant_edge("batch", "bot", "human_owner", BotAccessRelation::Owner).await;
        let mut queries = Vec::new();
        for i in 0..count {
            let user = format!("human_viewer-{i}");
            f.grant_edge("batch", "bot", &user, BotAccessRelation::Manager).await;
            queries.push(BotRoleQuery { user_id: user, bot_id: "bot".into() });
        }
        // Duplicates retain input ordering but cannot multiply SQL or rows.
        queries.extend([queries[0].clone(), queries[count - 1].clone()]);
        *db.reads.lock().unwrap() = Reads::default();
        let roles = f.store.roles_for(&queries).await.unwrap();
        assert_eq!(roles, vec![Some(BotAccessRelation::Manager); queries.len()]);
        {
            let reads = db.reads.lock().unwrap();
            let edges = count.div_ceil(AUTHORITY_QUERY_CHUNK / 2);
            assert_eq!(reads.edge_queries, edges);
            assert_eq!(reads.count, 2 + edges + count.div_ceil(AUTHORITY_QUERY_CHUNK));
            assert_eq!(reads.edge_rows, count);
            assert!(reads.max_edge_params <= AUTHORITY_QUERY_CHUNK + 1, "identities plus env");
        }
        db.fail_edges.store(true, Ordering::SeqCst);
        assert!(f.store.roles_for(&queries).await.is_err(), "a failed chunk must fail the whole batch");
    }
}

#[tokio::test]
async fn sparse_pairs_do_not_read_unrequested_cross_product_edges() {
    let db = Arc::new(CountingDb { inner: Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap()),
        reads: Mutex::new(Reads::default()), fail_edges: AtomicBool::new(false) });
    let f = new_fixture_with_db("sparse", db.clone()).await;
    let mut queries = Vec::new();
    for i in 0..100 {
        let bot = format!("bot-{i}");
        f.seed_bot("sparse", &bot, 1).await;
        f.grant_edge("sparse", &bot, "human_owner", BotAccessRelation::Owner).await;
        f.grant_edge("sparse", &bot, &format!("human_viewer-{}", (i+1)%100), BotAccessRelation::Manager).await;
        queries.push(BotRoleQuery { user_id: format!("human_viewer-{i}"), bot_id: bot });
    }
    *db.reads.lock().unwrap() = Reads::default();
    assert_eq!(f.store.roles_for(&queries).await.unwrap(), vec![None; 100]);
    let reads = db.reads.lock().unwrap();
    assert_eq!(reads.count, 5);
    assert_eq!(reads.edge_queries, 2);
    assert_eq!(reads.edge_rows, 0, "no reading other users' pairs even when they share queried Bots");
}

#[tokio::test]
async fn batch_preserves_legacy_fallback_liveness_case_and_env_semantics() {
    let db = Arc::new(CountingDb { inner: Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap()),
        reads: Mutex::new(Reads::default()), fail_edges: AtomicBool::new(false) });
    let f = new_fixture_with_db("scope", db).await;
    for (bot, version) in [("bot", 1), ("Bot", 1), ("legacy", 0)] { f.seed_bot("scope", bot, version).await; }
    f.grant_edge("scope", "bot", "human_owner", BotAccessRelation::Owner).await;
    f.grant_edge("scope", "Bot", "human_other", BotAccessRelation::Owner).await;
    f.grant_edge("scope", "bot", "human_deleted", BotAccessRelation::Manager).await;
    execute(f.db.as_ref(), "UPDATE bcs_bots SET is_deleted = 1 WHERE bot_uuid = ? AND env = ?",
        vec![DbValue::from("human_deleted"), DbValue::from("scope")]).await;
    execute(f.db.as_ref(), "UPDATE bcs_bots SET created_by = 'creator' WHERE bot_uuid = 'legacy' AND env = 'scope'", vec![]).await;
    let queries: Vec<_> = [("human_owner","bot"), ("human_owner","Bot"), ("human_Owner","bot"),
        ("human_deleted","bot"), ("human_creator","legacy"), ("human_owner","missing")]
        .into_iter().map(|(u,b)| BotRoleQuery { user_id:u.into(), bot_id:b.into() }).collect();
    let batch = f.store.roles_for(&queries).await.unwrap();
    for (query, role) in queries.iter().zip(batch) {
        assert_eq!(role, f.store.role(&query.user_id,&query.bot_id).await.unwrap());
    }
    let other = bcs_edge_permission_store::DbBotAuthorityStore::new(f.db.clone(), DbSqlFlavor::Sqlite, "other".into());
    assert_eq!(other.roles_for(&queries).await.unwrap(), vec![None; queries.len()]);
}
