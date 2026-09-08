use std::collections::{HashSet, VecDeque};
use std::sync::{Arc, Mutex};

use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_bot_store::provider::DbProviderStore;
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue,
};
use bcs_service_api::{
    BotCapabilities, BotMetricsSnapshotPort, BotRepoPort, ProviderRecord, ProviderRepoPort,
    ServiceError,
};
use bcs_test_support::capture_request_logs;
use serde_json::Value;

const REQUEST_ID: &str = "bot-store-diagnostic-request";
const DB_FAILURE: &str = "injected storage unavailable";

// Fail or corrupt a specific DB boundary operation while exercising the real
// repository's SQL mapping, error conversion, and fallback behavior.
struct DbStep {
    method: &'static str,
    sql_fragment: &'static str,
    required_params: Vec<DbValue>,
    rows: DbResult<Vec<DbRow>>,
}

impl DbStep {
    fn query(sql: &'static str, params: &[&str], rows: DbResult<Vec<DbRow>>) -> Self {
        Self {
            method: "query",
            sql_fragment: sql,
            required_params: params.iter().map(|value| DbValue::from(*value)).collect(),
            rows,
        }
    }

    fn failed_write(sql: &'static str, params: &[&str]) -> Self {
        Self {
            method: "execute",
            sql_fragment: sql,
            required_params: params.iter().map(|value| DbValue::from(*value)).collect(),
            rows: Err(DbError::Backend(DB_FAILURE.into())),
        }
    }
}

struct ScriptedDb(Mutex<VecDeque<DbStep>>);

impl ScriptedDb {
    fn new(steps: Vec<DbStep>) -> Arc<Self> {
        Arc::new(Self(Mutex::new(steps.into())))
    }

    fn take(&self, method: &str, statement: &DbStatement) -> DbResult<Vec<DbRow>> {
        let step = self.0.lock().expect("database script lock").pop_front()
            .expect("unexpected repository database call");
        assert_eq!(method, step.method);
        assert!(statement.sql().contains(step.sql_fragment), "unexpected SQL: {}", statement.sql());
        for param in step.required_params {
            assert!(statement.params().contains(&param), "repository lost expected parameter {param:?}");
        }
        step.rows
    }

    fn assert_complete(&self) {
        assert!(self.0.lock().expect("database script lock").is_empty(), "repository skipped an expected DB operation");
    }
}

#[async_trait::async_trait]
impl DbPlugin for ScriptedDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.take("query", &statement)
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.take("execute", &statement)?;
        panic!("these scenarios only script failing writes");
    }

    async fn transaction(&self, _steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        panic!("unexpected transaction");
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        panic!("unexpected health check");
    }
}

fn repository(db: Arc<ScriptedDb>) -> PersistentBotRepo {
    PersistentBotRepo::with_plugins(Arc::new(InMemoryCachePlugin::new()), db)
}

fn warning<'a>(events: &'a [Value], message: &str) -> &'a Value {
    let event = events.iter().find(|event| event["fields"]["message"] == message)
        .unwrap_or_else(|| panic!("missing warning {message}: {events:?}"));
    assert_eq!(event["level"], "WARN");
    assert_eq!(event["fields"]["request_id"], REQUEST_ID);
    event
}

fn storage_error(error: ServiceError) {
    match error {
        ServiceError::InternalError(message) => assert!(message.contains(DB_FAILURE), "{message}"),
        other => panic!("expected storage failure, got {other:?}"),
    }
}

#[tokio::test]
async fn registration_failures_report_repository_and_persistence_context() {
    let db = ScriptedDb::new(vec![
        DbStep::query("SELECT 1 FROM bcs_bots", &["new-bot"], Ok(vec![])),
        DbStep::failed_write("INSERT INTO bcs_bots", &["new-bot"]),
        DbStep::query("SELECT 1 FROM bcs_bots", &["owned-bot"], Ok(vec![])),
        DbStep::failed_write("INSERT INTO bcs_bots", &["owned-bot", "owner-17", "registration-token"]),
    ]);
    let repo = repository(db.clone());
    let (_, events) = capture_request_logs(REQUEST_ID, async {
        storage_error(repo.register("new-bot".into(), BotCapabilities::default()).await.expect_err("failed registration"));
        storage_error(repo.register_with_owner_and_token(
            "owned-bot".into(), BotCapabilities::default(), "owner-17", "registration-token",
        ).await.expect_err("failed owner registration"));
    }).await;
    let saved = warning(&events, "save_to_db: failed");
    assert_eq!(saved["fields"]["bot_uuid"], "new-bot");
    assert!(saved["fields"]["error"].as_str().expect("database error").contains(DB_FAILURE));
    assert_eq!(warning(&events, "Failed to save bot to database during register")["fields"]["bot_id"], "new-bot");
    assert_eq!(warning(&events, "Failed to save bot to database during register_with_owner_and_token")["fields"]["bot_id"], "owned-bot");
    db.assert_complete();
}

#[tokio::test]
async fn token_owner_and_visibility_write_failures_return_errors_with_bot_context() {
    let db = ScriptedDb::new(vec![
        DbStep::failed_write("SET session_token = ?", &["stored-bot", "replacement-token"]),
        DbStep::failed_write("SET created_by = ?", &["stored-bot", "new-owner"]),
        DbStep::failed_write("SET visibility = ?", &["stored-bot", "public"]),
    ]);
    let repo = repository(db.clone());
    let (_, events) = capture_request_logs(REQUEST_ID, async {
        storage_error(repo.save_token("stored-bot", "replacement-token").await.expect_err("token persistence must fail"));
        storage_error(repo.save_created_by("stored-bot", "new-owner", true).await.expect_err("owner persistence must fail"));
        storage_error(repo.update_visibility("stored-bot", "public").await.expect_err("visibility persistence must fail"));
    }).await;
    for message in ["save_token_to_db: failed", "update_created_by_in_db: failed", "update_visibility: failed to update database"] {
        let event = warning(&events, message);
        assert_eq!(event["fields"]["bot_uuid"], "stored-bot");
        assert!(event["fields"]["error"].as_str().expect("database error").contains(DB_FAILURE));
    }
    db.assert_complete();
}

#[tokio::test]
async fn identity_lookup_failures_and_invalid_rows_fail_closed_with_request_context() {
    // A missing required projection is a DB contract violation, distinct from a
    // query failure or a valid query returning no matching identity.
    let db = ScriptedDb::new(vec![
        DbStep::query("WHERE session_token = ?", &["query-token"], Err(DbError::Backend(DB_FAILURE.into()))),
        DbStep::query("WHERE session_token = ?", &["invalid-row-token"], Ok(vec![DbRow::empty()])),
        DbStep::query("WHERE agent_code = ?", &["query-agent"], Err(DbError::Backend(DB_FAILURE.into()))),
        DbStep::query("WHERE agent_code = ?", &["invalid-row-agent"], Ok(vec![DbRow::empty()])),
        DbStep::query("WHERE agent_code = ?", &["absent-agent"], Ok(vec![])),
    ]);
    let repo = repository(db.clone());
    let (_, events) = capture_request_logs(REQUEST_ID, async {
        assert_eq!(repo.find_bot_by_token("query-token").await, None);
        assert_eq!(repo.find_bot_by_token("invalid-row-token").await, None);
        assert_eq!(repo.find_bot_by_agent_code("query-agent").await, None);
        assert_eq!(repo.find_bot_by_agent_code("invalid-row-agent").await, None);
        assert_eq!(repo.find_bot_by_agent_code("absent-agent").await, None);
    }).await;
    for message in ["find_bot_by_token_in_db: database query failed", "find_bot_by_agent_code_in_db: query failed"] {
        assert!(warning(&events, message)["fields"]["error"].as_str().expect("database error").contains(DB_FAILURE));
    }
    for message in ["find_bot_by_token_in_db: failed to get bot_uuid", "find_bot_by_agent_code_in_db: failed to get bot_uuid"] {
        assert!(warning(&events, message)["fields"]["error"].as_str().expect("conversion error").contains("bot_uuid"));
    }
    assert_eq!(warning(&events, "find_bot_by_agent_code_in_db: no bot found")["fields"]["agent_code"], "absent-agent");
    db.assert_complete();
}

#[tokio::test]
async fn failed_discovery_count_keeps_the_page_and_distinguishes_page_failure() {
    let page_row = DbRow::new([
        ("bot_uuid".into(), DbValue::from("visible-bot")),
        ("name".into(), DbValue::from("Visible Bot")),
        ("is_friend".into(), DbValue::from(1_i64)),
    ].into_iter().collect());
    let db = ScriptedDb::new(vec![
        DbStep::query("SELECT b.bot_uuid", &["requesting-bot"], Err(DbError::Backend(DB_FAILURE.into()))),
        DbStep::query("SELECT b.bot_uuid", &["requesting-bot"], Ok(vec![page_row])),
        DbStep::query("SELECT count(*) AS total", &["requesting-bot"], Err(DbError::Backend(DB_FAILURE.into()))),
    ]);
    let repo = repository(db.clone());
    let (_, events) = capture_request_logs(REQUEST_ID, async {
        let (page, total) = repo.list_bots_by_name_and_cooperatable_with(
            "Visible", "requesting-bot", true, &HashSet::new(), 0, 10,
        ).await;
        assert!(page.is_empty());
        assert_eq!(total, 0);
        let (page, total) = repo.list_bots_by_name_and_cooperatable_with(
            "Visible", "requesting-bot", true, &HashSet::new(), 0, 10,
        ).await;
        assert_eq!(total, 0);
        assert_eq!(page.len(), 1);
        assert_eq!(page[0].0.bot_uuid, "visible-bot");
        assert_eq!(page[0].0.capabilities.name.as_deref(), Some("Visible Bot"));
        assert!(page[0].1);
    }).await;
    for message in ["list_bots_by_name_and_cooperatable_with_impl (page): failed", "list_bots_by_name_and_cooperatable_with_impl (count): failed"] {
        assert!(warning(&events, message)["fields"]["error"].as_str().expect("database error").contains(DB_FAILURE));
    }
    db.assert_complete();
}

#[tokio::test]
async fn metrics_failure_returns_an_error_instead_of_an_empty_snapshot() {
    let db = ScriptedDb::new(vec![DbStep::query(
        "GROUP BY actor_kind, status, visibility", &[], Err(DbError::Backend(DB_FAILURE.into())),
    )]);
    let repo = repository(db.clone());
    let (result, events) = capture_request_logs(REQUEST_ID, repo.bot_counts()).await;
    storage_error(result.expect_err("metrics query must not look like an empty population"));
    assert!(warning(&events, "bot metrics snapshot query failed")["fields"]["error"].as_str().expect("database error").contains(DB_FAILURE));
    db.assert_complete();
}

#[tokio::test]
async fn provider_read_and_write_failures_report_the_operation_and_keep_the_error() {
    let db = ScriptedDb::new(vec![
        DbStep::failed_write("INSERT INTO bcs_providers", &["provider-17", "owner-17"]),
        DbStep::query("FROM bcs_providers WHERE provider_id = ?", &["provider-17"], Err(DbError::Backend(DB_FAILURE.into()))),
    ]);
    let store = DbProviderStore::sqlite(db.clone());
    let (_, events) = capture_request_logs(REQUEST_ID, async {
        storage_error(store.insert_provider(ProviderRecord {
            provider_id: "provider-17".into(),
            name: "Diagnostic Provider".into(),
            config: "{}".into(),
            created_by: "owner-17".into(),
            owners: "[]".into(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        }).await.expect_err("provider write failure"));
        storage_error(store.get_provider("provider-17").await.expect_err("provider read failure"));
    }).await;
    assert_eq!(warning(&events, "db_provider: execute failed")["fields"]["operation"], "insert_provider");
    assert_eq!(warning(&events, "db_provider: query failed")["fields"]["operation"], "get_provider");
    db.assert_complete();
}

#[tokio::test]
async fn malformed_capabilities_are_reported_without_rewriting_the_file() {
    let directory = tempfile::tempdir().expect("temporary bot directory");
    let bot_directory = directory.path().join("corrupted-bot");
    std::fs::create_dir(&bot_directory).expect("create bot directory");
    let path = bot_directory.join("bot.json");
    let malformed = "{not-json}";
    std::fs::write(&path, malformed).expect("write corrupted capabilities");
    let repo = MemoryBotRepo::with_base_dir(directory.path().to_path_buf());
    let (result, events) = capture_request_logs(REQUEST_ID, repo.load_from_storage("corrupted-bot")).await;
    assert!(result.is_none(), "invalid capabilities must not be loaded");
    let event = warning(&events, "Failed to parse capabilities file");
    assert_eq!(event["fields"]["bot_id"], "corrupted-bot");
    assert!(event["fields"]["error"].as_str().expect("parse error").contains("line 1"));
    assert_eq!(std::fs::read_to_string(path).expect("read original file"), malformed);
}
