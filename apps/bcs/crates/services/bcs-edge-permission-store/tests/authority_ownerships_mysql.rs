//! Candidate owner reads over the full migration chain: inspect actual store
//! statements, not hand-written approximations of their SQL. Run against an
//! isolated empty database using BCS_TEST_MYSQL_URL (never logged).
use std::sync::{Arc, Mutex};

use bcs_db_api::*;
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use serde_json::Value;

#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;

struct RecordingDb {
    inner: Arc<dyn DbPlugin>,
    queries: Mutex<Vec<DbStatement>>,
}

#[async_trait::async_trait]
impl DbPlugin for RecordingDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.lock().unwrap().push(statement.clone());
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }
    async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }
    async fn transaction_with_options(
        &self, steps: Vec<DbTransactionStep>, options: DbTransactionOptions,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction_with_options(steps, options).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

fn plan_table<'a>(value: &'a Value, table: &str) -> Option<&'a Value> {
    if value.get("table_name").and_then(Value::as_str) == Some(table) {
        return Some(value);
    }
    match value {
        Value::Object(fields) => fields.values().find_map(|v| plan_table(v, table)),
        Value::Array(items) => items.iter().find_map(|v| plan_table(v, table)),
        _ => None,
    }
}

async fn assert_indexed(db: &dyn DbPlugin, statement: &DbStatement) {
    assert!(statement.params().len() <= 257, "128 distinct IDs bound twice plus env");
    let (table, identity) = if statement.sql().contains("FROM edge_grants") {
        ("edge_grants", "to_id")
    } else {
        ("bcs_bots", "bot_uuid")
    };
    let rows = db.query(DbStatement::with_params(
        format!("EXPLAIN FORMAT=JSON {}", statement.sql()),
        statement.params().to_vec(),
    )).await.expect("explain generated query");
    let json: String = db_get_column(&rows[0], "EXPLAIN").expect("JSON explain column");
    let plan: Value = serde_json::from_str(&json).expect("JSON explain plan");
    let access = plan_table(&plan, table).expect("queried table in plan");
    // MySQL 8 uses JSON format v1; newer servers can default to v2.
    let (key_parts, range_lookup, examined) = if access.get("index_access_type").is_some() {
        (
            access["key_columns"].as_array(),
            matches!(access["index_access_type"].as_str(), Some("index_range_scan" | "index_lookup")),
            access["estimated_rows"].as_f64(),
        )
    } else {
        (
            access["used_key_parts"].as_array(),
            matches!(access["access_type"].as_str(), Some("range" | "ref" | "const")),
            access["rows_examined_per_scan"].as_f64(),
        )
    };
    assert!(key_parts.is_some_and(|parts| parts.iter().any(|p| p.as_str() == Some(identity))),
        "{table} must use the identity key, not only env: {access}");
    assert!(range_lookup, "bounded identity index lookup: {access}");
    assert!(examined.is_some_and(|rows| rows <= 200.0),
        "100 requested IDs must not scan the 20,000-row environment: {access}");

}

async fn seed(db: &dyn DbPlugin) {
    for start in (0..20_000).step_by(100) {
        let mut bots = Vec::new();
        let mut edges = Vec::new();
        for index in start..start + 100 {
            let bot = format!("bot-{index:05}");
            bots.extend([DbValue::from(bot.clone()), DbValue::from("batch-plan"),
                DbValue::from("candidate"), DbValue::from("owner")]);
            edges.extend([DbValue::from("batch-plan"), DbValue::from("human_owner"), DbValue::from(bot)]);
        }
        let bot_values = vec!["(?, ?, ?, ?, 1)"; 100].join(",");
        db.execute(DbStatement::with_params(format!(
            "INSERT INTO bcs_bots (bot_uuid, env, name, created_by, ownership_version) VALUES {bot_values}"
        ), bots)).await.expect("seed bots");
        let edge_values = vec!["(?, ?, ?, 'owner', 0, 'approved')"; 100].join(",");
        db.execute(DbStatement::with_params(format!(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, status) VALUES {edge_values}"
        ), edges)).await.expect("seed owner edges");
    }
    // ANALYZE returns a row set; use query rather than execute.
    db.query(DbStatement::new("ANALYZE TABLE bcs_bots, edge_grants")).await.unwrap();
}

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL naming an isolated empty database"]
async fn ownership_projection_is_indexed_and_byte_exact() {
    let database = mysql_chain::MysqlConformanceDb::open_empty().await;
    database.apply_full_migration_chain().await;
    seed(database.plugin.as_ref()).await;
    let db = Arc::new(RecordingDb { inner: database.plugin.clone(), queries: Mutex::new(Vec::new()) });
    let store = DbBotAuthorityStore::mysql(db.clone(), "batch-plan".to_string());
    let ids: Vec<String> = (0..100).map(|i| format!("bot-{i:05}")).collect();
    let views = store.ownerships_for(&ids).await.expect("owner page");
    assert!(views.iter().all(|v| v.as_ref().unwrap().owner_user_id == "owner"));
    let queries = std::mem::take(&mut *db.queries.lock().unwrap());
    assert_eq!(queries.len(), 2, "states plus owners");
    for statement in &queries {
        assert_indexed(database.plugin.as_ref(), statement).await;
    }

    // Legacy creator read must also use the identity index.
    database.plugin.execute(DbStatement::new(
        "UPDATE bcs_bots SET ownership_version = 0 WHERE bot_uuid < 'bot-00100'"
    )).await.unwrap();
    let shim = DbBotAuthorityStore::mysql(db.clone(), "batch-plan".to_string())
        .with_legacy_created_by_fallback(true);
    let views = shim.ownerships_for(&ids).await.unwrap();
    assert!(views.iter().all(|v| v.as_ref().unwrap().owner_user_id == "owner"));
    let queries = std::mem::take(&mut *db.queries.lock().unwrap());
    assert_eq!(queries.len(), 3);
    for statement in &queries {
        assert_indexed(database.plugin.as_ref(), statement).await;
    }

    // Exercise both the migrated NO PAD schema and retained case-insensitive /
    // PAD SPACE columns. A broad indexed match must not become an identity match.
    let aliases = vec!["BOT-00100".to_string(), "bot-00100 ".to_string(), "bot-00100".to_string()];
    for legacy_collation in [false, true] {
        if legacy_collation {
            database.plugin.execute(DbStatement::new(
                "ALTER TABLE bcs_bots MODIFY bot_uuid VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL"
            )).await.unwrap();
            database.plugin.execute(DbStatement::new(
                "ALTER TABLE edge_grants MODIFY to_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL"
            )).await.unwrap();
        }
        let views = store.ownerships_for(&aliases).await.unwrap();
        assert!(views[0].is_none() && views[1].is_none(), "aliases are never identities");
        assert_eq!(views[2].as_ref().unwrap().owner_user_id, "owner");
        let repeated = vec![aliases[2].clone(); 257];
        assert_eq!(store.ownerships_for(&repeated).await.unwrap().len(), 257);
    }
    database.close().await;
}
