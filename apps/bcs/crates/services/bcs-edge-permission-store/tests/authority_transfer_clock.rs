//! transfer list clock consistency pin (spec §6.4 同一有效状态
//! 口径): count, page and the projected item statuses share ONE
//! captured DB time — with distinct clocks a pending row expiring
//! mid-read counts as pending yet pages as expired.

use std::sync::{Arc, Mutex};

use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement,
    DbTransactionOptions, DbTransactionStep, DbTransactionStepResult,
    DbValue,
};
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{
    TransferDirection, TransferListQuery, TransferStatus,
};

const TRANSFERS_DDL: &str = "CREATE TABLE IF NOT EXISTS bot_ownership_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    target_owner_actor_id TEXT NOT NULL,
    from_owner_actor_id TEXT NOT NULL,
    expected_ownership_version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    decided_by_kind TEXT,
    decided_by_id TEXT,
    reason TEXT,
    result_ownership_version INTEGER,
    bot_name_snapshot TEXT,
    gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (env, bot_id, target_owner_actor_id)
)";

struct RecordingDb {
    inner: Arc<dyn DbPlugin>,
    statements: Mutex<Vec<(String, Vec<DbValue>)>>,
}

impl RecordingDb {
    fn record(&self, statement: &DbStatement) {
        let sql = statement.sql().to_string();
        if sql.contains("bot_ownership_transfers") {
            let params = statement.params().to_vec();
            self.statements
                .lock()
                .expect("record lock")
                .push((sql, params));
        }
    }
}

#[async_trait::async_trait]
impl DbPlugin for RecordingDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.record(&statement);
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn transaction_with_options(
        &self,
        steps: Vec<DbTransactionStep>,
        options: DbTransactionOptions,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction_with_options(steps, options).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

/// The count and the page statements must bind the SAME literal
/// `now` — one DB time captured per list call, shared with the
/// projected statuses (distinct clocks would leave the lens
/// embedded in each query's own SQL text instead).
#[tokio::test]
async fn transfer_list_binds_one_shared_now_for_count_and_page() {
    let inner = Arc::new(
        bcs_db_local::LocalSqliteDbPlugin::new().expect("sqlite"),
    );
    inner
        .execute(DbStatement::new(TRANSFERS_DDL))
        .await
        .expect("transfers schema");
    inner
        .execute(DbStatement::new(
            "INSERT INTO bot_ownership_transfers \
             (env, bot_id, target_owner_actor_id, from_owner_actor_id, \
              expected_ownership_version, status, expires_at) \
             VALUES ('local', 'bot_clock', 'human_t1', 'human_own', 1, \
                     'pending', datetime('now', '+1 day'))",
        ))
        .await
        .expect("seed pending transfer");
    let db = Arc::new(RecordingDb {
        inner,
        statements: Mutex::new(Vec::new()),
    });
    let repo = DbBotAuthorityStore::sqlite(db.clone(), "local".to_string());

    let page = repo
        .transfers(TransferListQuery {
            user_id: "human_t1".to_string(),
            direction: TransferDirection::Received,
            statuses: vec![TransferStatus::Pending],
            offset: 0,
            limit: 10,
        })
        .await
        .expect("transfer list");
    assert_eq!(page.total, 1);
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].status, TransferStatus::Pending);

    let statements = db.statements.lock().expect("record lock");
    let statement_params = |marker: &str| {
        statements
            .iter()
            .find(|(sql, _)| sql.contains(marker))
            .unwrap_or_else(|| panic!("statement containing {marker} recorded"))
            .1
            .clone()
    };
    let count_params = statement_params("COUNT(*) AS total");
    let page_params = statement_params("LIMIT ? OFFSET ?");

    // Layout: [env, user, now, statuses...] (+ page appends
    // limit/offset). The `now` position carries the captured DB
    // time as a parameter — distinct clocks would bind nothing
    // shared at that spot.
    let looks_like_db_time = |value: &DbValue| match value {
        DbValue::String(text) => text.starts_with('2') && text.contains(':'),
        _ => false,
    };
    let count_now = count_params
        .iter()
        .find(|value| looks_like_db_time(value))
        .expect("count binds the captured now as a parameter");
    let page_now = page_params
        .iter()
        .find(|value| looks_like_db_time(value))
        .expect("page binds the captured now as a parameter");
    assert_eq!(
        count_now, page_now,
        "count and page share ONE captured DB time"
    );
}
