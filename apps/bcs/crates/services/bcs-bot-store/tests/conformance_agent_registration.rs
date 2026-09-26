use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{DbPlugin, DbStatement, DbSqlFlavor, DbRow, DbResult, DbExecuteResult, DbHealth, DbTransactionStep, DbTransactionStepResult};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{BotRepoPort, ServiceError};
use bcs_test_support::contract::bot_self::agent_registration_lookup_contract_tests;

#[path = "common/bot_provider.rs"]
mod database;

struct CountReads {
    db: Arc<LocalSqliteDbPlugin>,
    queries: AtomicUsize,
    rows: AtomicUsize,
}

#[async_trait]
impl DbPlugin for CountReads {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.fetch_add(1, Ordering::SeqCst);
        let rows = self.db.query(statement).await?;
        self.rows.fetch_add(rows.len(), Ordering::SeqCst);
        Ok(rows)
    }
    async fn execute(&self, _: DbStatement) -> DbResult<DbExecuteResult> {
        panic!("Agent registration lookup must never write")
    }
    async fn transaction(&self, _: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        panic!("Agent registration lookup must never start a transaction")
    }
    async fn health_check(&self) -> DbResult<DbHealth> { self.db.health_check().await }
}

async fn fixture() -> (Arc<LocalSqliteDbPlugin>, Arc<CountReads>, PersistentBotRepo) {
    let db = database::sqlite().await;
    let counted = Arc::new(CountReads { db: db.clone(), queries: AtomicUsize::new(0), rows: AtomicUsize::new(0) });
    let repo = PersistentBotRepo::with_sql_flavor(counted.clone(), DbSqlFlavor::Sqlite);
    (db, counted, repo)
}

async fn insert(db: &dyn DbPlugin, bot_id: &str, agent_code: &str, env: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid,env,agent_code,name,bot_info,status,provider_id,provider_bot_ref) VALUES (?,?,?,?,?,?,?,?)",
        vec![bot_id.into(), env.into(), agent_code.into(), "Poolab Assistant".into(),
             r#"{"summary":"负责研发任务的 Agent","agent_token":"must-not-escape"}"#.into(),
             "hidden".into(), "provider-a".into(), bot_id.into()],
    )).await.unwrap();
}

#[tokio::test]
async fn persisted_lookup_ignores_heartbeat_and_returns_safe_provider_projection() {
    let (db, count, repo) = fixture().await;
    insert(db.as_ref(), "bot-001", "agent-001", &bcs_config::resolve_env_str()).await;
    agent_registration_lookup_contract_tests(&repo).await;
    let bot = repo.find_agent_registration("agent-001").await.unwrap().unwrap();
    assert_eq!(bot.bot_id, "bot-001");
    assert_eq!(bot.name.as_deref(), Some("Poolab Assistant"));
    assert_eq!(bot.summary.as_deref(), Some("负责研发任务的 Agent"));
    assert_eq!(bot.provider_id.as_deref(), Some("provider-a"));
    assert_eq!(bot.provider_bot_ref.as_deref(), Some("bot-001"));
    assert!(!format!("{bot:?}").contains("must-not-escape"));
    assert_eq!(count.queries.load(Ordering::SeqCst), 3);
    assert_eq!(count.rows.load(Ordering::SeqCst), 2);
    assert!(repo.find_agent_registration("never-registered").await.unwrap().is_none());
}

#[tokio::test]
async fn lookup_excludes_other_environments_tombstones_and_humans() {
    let (db, _, repo) = fixture().await;
    let env = bcs_config::resolve_env_str();
    insert(db.as_ref(), "other-env", "agent-001", &format!("{env}-other")).await;
    insert(db.as_ref(), "deleted", "agent-001", &env).await;
    insert(db.as_ref(), "human", "agent-001", &env).await;
    db.execute(DbStatement::new("UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid='deleted'")).await.unwrap();
    db.execute(DbStatement::new("UPDATE bcs_bots SET actor_kind='human' WHERE bot_uuid='human'")).await.unwrap();
    assert!(repo.find_agent_registration("agent-001").await.unwrap().is_none());
}

#[tokio::test]
async fn legacy_json_identity_is_registered_and_conflicts_with_column_mapping() {
    let (db, count, repo) = fixture().await;
    let env = bcs_config::resolve_env_str();
    insert(db.as_ref(), "legacy", "agent-001", &env).await;
    db.execute(DbStatement::new("UPDATE bcs_bots SET agent_code=NULL,bot_info='{\"agent_code\":\"agent-001\",\"summary\":\"legacy\"}'")).await.unwrap();
    assert_eq!(repo.find_agent_registration("agent-001").await.unwrap().unwrap().bot_id, "legacy");
    assert_eq!(count.queries.load(Ordering::SeqCst), 1);
    insert(db.as_ref(), "current", "agent-001", &env).await;
    assert!(matches!(repo.find_agent_registration("agent-001").await, Err(ServiceError::Conflict(_))));
    // A dedicated column always wins over retained legacy JSON metadata.
    db.execute(DbStatement::new("UPDATE bcs_bots SET agent_code='different-agent' WHERE bot_uuid='legacy'")).await.unwrap();
    assert_eq!(repo.find_agent_registration("agent-001").await.unwrap().unwrap().bot_id, "current");
}

#[tokio::test]
async fn multiple_mappings_conflict_with_one_bounded_query() {
    let (db, count, repo) = fixture().await;
    for index in 0..25 {
        insert(db.as_ref(), &format!("bot-{index}"), "agent-001", &bcs_config::resolve_env_str()).await;
    }
    assert!(matches!(repo.find_agent_registration("agent-001").await, Err(ServiceError::Conflict(_))));
    assert_eq!(count.queries.load(Ordering::SeqCst), 1);
    assert_eq!(count.rows.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn legacy_binding_metadata_and_unaffiliated_bots_have_explicit_projection() {
    let (db, _, repo) = fixture().await;
    let env = bcs_config::resolve_env_str();
    insert(db.as_ref(), "bot-001", "agent-001", &env).await;
    db.execute(DbStatement::new("UPDATE bcs_bots SET provider_id=NULL,provider_bot_ref=NULL")).await.unwrap();
    let bot = repo.find_agent_registration("agent-001").await.unwrap().unwrap();
    assert!(bot.provider_id.is_none() && bot.provider_bot_ref.is_none());
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_provider_bot_bindings (bot_uuid,env,provider_id,provider_bot_ref,disabled) VALUES ('bot-001',?,'legacy-provider','legacy-ref',1)", vec![env.into()],
    )).await.unwrap();
    let bot = repo.find_agent_registration("agent-001").await.unwrap().unwrap();
    assert_eq!(bot.provider_id.as_deref(), Some("legacy-provider"));
    assert_eq!(bot.provider_bot_ref.as_deref(), Some("legacy-ref"));
}

#[tokio::test]
async fn read_and_decode_failures_are_not_absence_and_do_not_expose_row_secrets() {
    let (db, _, repo) = fixture().await;
    insert(db.as_ref(), "bot-001", "agent-001", &bcs_config::resolve_env_str()).await;
    db.execute(DbStatement::new("UPDATE bcs_bots SET bot_info='must-not-escape'")).await.unwrap();
    let error = repo.find_agent_registration("agent-001").await.unwrap_err();
    assert!(matches!(error, ServiceError::InternalError(_)));
    assert!(!error.to_string().contains("must-not-escape"));
    db.execute(DbStatement::new("DROP TABLE bcs_bots")).await.unwrap();
    assert!(matches!(repo.find_agent_registration("agent-001").await, Err(ServiceError::InternalError(_))));
}

#[tokio::test]
async fn corrupt_registration_metadata_fails_closed() {
    let (db, _, repo) = fixture().await;
    insert(db.as_ref(), "bot-001", "agent-001", &bcs_config::resolve_env_str()).await;
    for info in ["[]", "42", r#"{"summary":7}"#] {
        db.execute(DbStatement::with_params("UPDATE bcs_bots SET bot_info=?", vec![info.into()])).await.unwrap();
        assert!(matches!(repo.find_agent_registration("agent-001").await, Err(ServiceError::InternalError(_))));
    }
    db.execute(DbStatement::new("UPDATE bcs_bots SET bot_info='{}',provider_bot_ref=NULL")).await.unwrap();
    assert!(matches!(repo.find_agent_registration("agent-001").await, Err(ServiceError::InternalError(_))));
}

struct CoercedLegacyRow { code: &'static str, info: &'static str }

#[async_trait]
impl DbPlugin for CoercedLegacyRow {
    async fn query(&self, _: DbStatement) -> DbResult<Vec<DbRow>> {
        // MySQL JSON_UNQUOTE can yield text for JSON numbers/null. Model that
        // actual driver projection so the repository must validate its source.
        Ok(vec![DbRow::new(std::collections::BTreeMap::from([
            ("bot_uuid".into(), "legacy".into()),
            ("matched_agent_code".into(), self.code.into()),
            ("agent_code".into(), bcs_db_api::DbValue::Null),
            ("bot_info".into(), self.info.into()),
            ("name".into(), "Legacy".into()),
            ("provider_id".into(), bcs_db_api::DbValue::Null),
            ("provider_bot_ref".into(), bcs_db_api::DbValue::Null),
            ("legacy_provider_id".into(), bcs_db_api::DbValue::Null),
            ("legacy_provider_bot_ref".into(), bcs_db_api::DbValue::Null),
        ]))])
    }
    async fn execute(&self, _: DbStatement) -> DbResult<DbExecuteResult> { panic!("read only") }
    async fn transaction(&self, _: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { panic!("read only") }
    async fn health_check(&self) -> DbResult<DbHealth> { Ok(DbHealth::healthy()) }
}

#[tokio::test]
async fn mysql_coercion_cannot_turn_malformed_legacy_identity_into_registration() {
    for (code, info) in [("123", r#"{"agent_code":123}"#), ("null", r#"{"agent_code":null}"#)] {
        let repo = PersistentBotRepo::new(Arc::new(CoercedLegacyRow { code, info }));
        assert!(matches!(repo.find_agent_registration(code).await, Err(ServiceError::InternalError(_))));
    }
}
