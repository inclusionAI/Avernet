//! Shared internals of the `ownership_transfer` test binary (plan Task 8):
//! the per-driver seeding levers, the deterministic drift/counting plugin
//! wrapper for the SQLite driver, and the shared Memory/SQLite conformance
//! suites implementing the brief's case list (OT01–OT18/OT20–OT21's repo
//! subcases). Kept in a subdirectory module so cargo does not auto-discover
//! it as a second integration-test binary; `ownership_transfer.rs` pulls
//! it in with `#[path]` and declares the individual #[tokio::test]s.

#[path = "../../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_db_api::{
    DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
use bcs_service_api::types::bot_operation::{BotOperationActor, BotOperationContext};
use tempfile::TempDir;

pub mod suite;
pub mod suite_second_half;

pub const ENV: &str = "local";

/// A clearly DB-clock-past deadline (the 'YYYY-MM-DD HH:MM:SS' TEXT shape
/// the schema stores): deterministic, parseable, lapsed forever.
pub const LAPSED_DEADLINE: &str = "2000-01-01 00:00:00";
/// A clearly DB-clock-future deadline.
pub const FUTURE_DEADLINE: &str = "2099-01-01 00:00:00";

// ---------------------------------------------------------------------------
// Harness (per-driver seeding + raw-row levers; production work goes
// through `repo` only)
// ---------------------------------------------------------------------------

/// The raw, STORED (pre-projection) row facts the boundary cases assert —
/// the effective-expiry projection is exactly what these must NOT see.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RawTransferRow {
    pub stored_status: String,
    pub terminal_reason: Option<String>,
}

#[async_trait]
pub trait TransferDriver: Send + Sync {
    /// One legal Bot (live, version 1) + its approved owner edge, atomically
    /// (Task 2 schema shape). Test-only stand-in for the Task 5
    /// initialization contract.
    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str);
    /// Real Human materialization (`human_<user_id>` actor row).
    async fn seed_human(&self, user_id: &str);
    /// Formal-source manager seeding (never a friend edge).
    async fn seed_manager_source(&self, bot_id: &str, user_id: &str, kind: &str, id: &str);
    /// Seed one live bot with UNINITIALIZED ownership (version 0).
    async fn seed_uninitialized_bot(&self, bot_id: &str);
    /// Seed one full-shape PENDING transfer row with an explicit deadline
    /// text and version snapshot (the lapsed/mismatched preconditions the
    /// production create lane cannot produce). Returns the transfer id.
    async fn seed_pending(
        &self,
        bot_id: &str,
        from_user_id: &str,
        to_user_id: &str,
        expected_owner_version: u64,
        expires_at: &str,
    ) -> String;
    /// The Task 5 retirement lane: soft-delete the Bot, revoke every
    /// authority edge, and INVALIDATE its pendings with terminal_reason
    /// `bot_deleted` (the acceptance must never resurrect a retired Bot).
    async fn retire_bot(&self, bot_id: &str);
    /// The STORED (pre-projection) row facts of one transfer.
    async fn raw_row(&self, bot_id: &str, transfer_id: &str) -> Option<RawTransferRow>;
    /// The count of this bot's stored transfer rows (不落单/留存 proofs).
    async fn row_count(&self, bot_id: &str) -> u64;
    /// The stored status of one manager edge (OT02's raw proof).
    async fn manager_edge_status(&self, bot_id: &str, user_id: &str, kind: &str, id: &str)
        -> Option<String>;
}

pub struct Harness {
    pub repo: Arc<dyn BotAuthorityRepoPort>,
    pub driver: Arc<dyn TransferDriver>,
}

impl Harness {
    pub(crate) async fn seed_owned(&self, bot_id: &str, owner: &str) {
        self.driver.seed_owned(bot_id, owner).await;
    }
    pub(crate) async fn seed_human(&self, user: &str) {
        self.driver.seed_human(user).await;
    }
    pub(crate) async fn seed_manager_source(&self, bot: &str, user: &str, kind: &str, id: &str) {
        self.driver.seed_manager_source(bot, user, kind, id).await;
    }
}

// ---------------------------------------------------------------------------
// SQLite driver over the full migration chain
// ---------------------------------------------------------------------------

/// DbPlugin wrapper of the SQLite driver: counts transactions and
/// standalone queries (the statement-budget proofs), and applies a
/// deterministic drift statement exactly when the NEXT WRITE transaction
/// on the authority tables arrives — AFTER the validated read, BEFORE the
/// guarded writes (the in-transaction guard proofs).
pub struct TransferDb {
    pub inner: Arc<dyn DbPlugin>,
    drift: Mutex<Option<String>>,
    armed_failure: AtomicBool,
    transactions: AtomicUsize,
    standalone_queries: AtomicUsize,
}

impl TransferDb {
    fn steps_touch_authority(steps: &[DbTransactionStep]) -> bool {
        let touches = |statement: &DbStatement| {
            statement.sql().contains("bot_ownership_transfers")
                || (statement.sql().contains("edge_grants")
                    && (statement.sql().contains("UPDATE")
                        || statement.sql().contains("INSERT")))
        };
        steps.iter().any(|step| match step {
            DbTransactionStep::Query(_) => false,
            DbTransactionStep::Execute(statement)
            | DbTransactionStep::ExecuteChecked { statement, .. } => touches(statement),
        })
    }

    pub fn arm_drift(&self, sql: &str) {
        *self.drift.lock().unwrap() = Some(sql.to_string());
    }

    pub fn arm_write_failure(&self) {
        self.armed_failure.store(true, Ordering::SeqCst);
    }

    pub fn transaction_count(&self) -> usize {
        self.transactions.load(Ordering::SeqCst)
    }

    pub fn query_count(&self) -> usize {
        self.standalone_queries.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl DbPlugin for TransferDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.standalone_queries.fetch_add(1, Ordering::SeqCst);
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.transactions.fetch_add(1, Ordering::SeqCst);
        if Self::steps_touch_authority(&steps) {
            // The armed drift (if any) fires when the WRITE transaction
            // arrives — after the validated read committed. The mutex guard
            // is scoped to the take so it never lives across an await.
            let drift_sql = { self.drift.lock().unwrap().take() };
            if let Some(drift_sql) = drift_sql {
                self.inner
                    .execute(DbStatement::new(drift_sql))
                    .await
                    .expect("apply the armed drift statement");
            }
            if self.armed_failure.load(Ordering::SeqCst) {
                self.armed_failure.store(false, Ordering::SeqCst);
                return Err(bcs_db_api::DbError::Backend(
                    "test-injected transfer write failure".into(),
                ));
            }
        }
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

struct SqliteDriver {
    db: Arc<TransferDb>,
    env: String,
}

#[async_trait]
impl TransferDriver for SqliteDriver {
    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str) {
        self.db
            .inner
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version) VALUES (?, ?, ?, 1)",
                    vec![
                        DbValue::from(bot_id),
                        DbValue::from(bot_id),
                        DbValue::from(self.env.clone()),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, \
                     rules, status, originator_policy_type, originator_policy_data, \
                     management_source_kind, management_source_id) \
                     VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from', NULL, 'owner', 'owner')",
                    vec![
                        DbValue::from(self.env.clone()),
                        DbValue::from(format!("human_{owner_user_id}")),
                        DbValue::from(bot_id),
                    ],
                )),
            ])
            .await
            .expect("seed_owned");
    }

    async fn seed_human(&self, user_id: &str) {
        let actor_id = format!("human_{user_id}");
        let existing = self
            .db
            .inner
            .query(DbStatement::with_params(
                "SELECT 1 AS one FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
                vec![
                    DbValue::from(actor_id.clone()),
                    DbValue::from(self.env.clone()),
                ],
            ))
            .await
            .expect("seed_human lookup");
        if !existing.is_empty() {
            return;
        }
        self.db
            .inner
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) \
                 VALUES (?, ?, ?, 'human', 'online')",
                vec![
                    DbValue::from(actor_id),
                    DbValue::from(user_id),
                    DbValue::from(self.env.clone()),
                ],
            ))
            .await
            .expect("seed_human");
    }

    async fn seed_manager_source(&self, bot_id: &str, user_id: &str, kind: &str, id: &str) {
        self.db
            .inner
            .execute(DbStatement::with_params(
                "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, status, \
                 originator_policy_type, originator_policy_data, management_source_kind, \
                 management_source_id) \
                 VALUES (?, ?, ?, 'manager', 0, 'approved', 'same_as_from', NULL, ?, ?)",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(format!("human_{user_id}")),
                    DbValue::from(bot_id),
                    DbValue::from(kind),
                    DbValue::from(id),
                ],
            ))
            .await
            .expect("seed_manager_source");
    }

    async fn seed_uninitialized_bot(&self, bot_id: &str) {
        self.db
            .inner
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, name, env) VALUES (?, ?, ?)",
                vec![
                    DbValue::from(bot_id),
                    DbValue::from(bot_id),
                    DbValue::from(self.env.clone()),
                ],
            ))
            .await
            .expect("seed_uninitialized_bot");
    }

    async fn seed_pending(
        &self,
        bot_id: &str,
        from_user_id: &str,
        to_user_id: &str,
        expected_owner_version: u64,
        expires_at: &str,
    ) -> String {
        let transfer_id = uuid::Uuid::new_v4().to_string();
        self.db
            .inner
            .execute(DbStatement::with_params(
                "INSERT INTO bot_ownership_transfers \
                     (transfer_id, env, bot_id, from_user_id, to_user_id, expected_owner_version, \
                      client_request_id, status, expires_at, bot_name_snapshot) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                vec![
                    DbValue::from(transfer_id.clone()),
                    DbValue::from(self.env.clone()),
                    DbValue::from(bot_id),
                    DbValue::from(from_user_id),
                    DbValue::from(to_user_id),
                    DbValue::from(expected_owner_version as i64),
                    DbValue::from(uuid::Uuid::new_v4().to_string()),
                    DbValue::from(expires_at),
                    DbValue::from(bot_id),
                ],
            ))
            .await
            .expect("seed_pending");
        transfer_id
    }

    async fn retire_bot(&self, bot_id: &str) {
        // The Task 5 production retirement statements (the same lane
        // `PersistentBotRepo::retire_bot_lifecycle_impl` commits): the
        // soft delete leads, every authority edge is withdrawn, every
        // pending transfer is invalidated with `bot_deleted`.
        let now = "CURRENT_TIMESTAMP";
        self.db
            .inner
            .transaction(vec![
                DbTransactionStep::Execute(
                    DbStatement::with_params(
                        "UPDATE bcs_bots SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP \
                         WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0 \
                           AND COALESCE(actor_kind, 'bot') <> 'human'",
                        vec![
                            DbValue::from(bot_id),
                            DbValue::from(self.env.clone()),
                        ],
                    )
                    .with_transaction_stop_on_no_rows(),
                ),
                DbTransactionStep::Execute(DbStatement::with_params(
                    format!(
                        "UPDATE edge_grants SET status = 'revoked', gmt_modified = {now} \
                         WHERE env = ? AND to_id = ? AND status = 'approved' \
                           AND grant_kind IN ('owner', 'manager')"
                    ),
                    vec![
                        DbValue::from(self.env.clone()),
                        DbValue::from(bot_id),
                    ],
                )),
                DbTransactionStep::Execute(DbStatement::with_params(
                    format!(
                        "UPDATE bot_ownership_transfers \
                         SET status = 'invalidated', terminal_reason = 'bot_deleted', \
                             decision_actor_kind = 'system', decided_by = 'ownership-deletion', \
                             decided_at = {now}, gmt_modified = {now} \
                         WHERE env = ? AND bot_id = ? AND status = 'pending'"
                    ),
                    vec![
                        DbValue::from(self.env.clone()),
                        DbValue::from(bot_id),
                    ],
                )),
            ])
            .await
            .expect("retire_bot");
    }

    async fn raw_row(&self, _bot_id: &str, transfer_id: &str) -> Option<RawTransferRow> {
        let rows = self
            .db
            .inner
            .query(DbStatement::with_params(
                "SELECT status, terminal_reason FROM bot_ownership_transfers \
                 WHERE env = ? AND transfer_id = ? LIMIT 1",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(transfer_id),
                ],
            ))
            .await
            .expect("raw_row");
        rows.into_iter().next().map(|row| RawTransferRow {
            stored_status: row
                .get_string("status")
                .ok()
                .flatten()
                .unwrap_or_default(),
            terminal_reason: row.get_string("terminal_reason").ok().flatten(),
        })
    }

    async fn row_count(&self, bot_id: &str) -> u64 {
        let rows = self
            .db
            .inner
            .query(DbStatement::with_params(
                "SELECT COUNT(*) AS n FROM bot_ownership_transfers WHERE env = ? AND bot_id = ?",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(bot_id),
                ],
            ))
            .await
            .expect("row_count");
        rows.first()
            .and_then(|row| row.get_i64("n").ok().flatten())
            .map(|value| value.max(0) as u64)
            .unwrap_or(0)
    }

    async fn manager_edge_status(
        &self,
        bot_id: &str,
        user_id: &str,
        kind: &str,
        id: &str,
    ) -> Option<String> {
        let rows = self
            .db
            .inner
            .query(DbStatement::with_params(
                "SELECT status FROM edge_grants \
                 WHERE env = ? AND to_id = ? AND from_id = ? AND grant_kind = 'manager' \
                   AND management_source_kind = ? AND management_source_id = ? LIMIT 1",
                vec![
                    DbValue::from(self.env.clone()),
                    DbValue::from(bot_id),
                    DbValue::from(format!("human_{user_id}")),
                    DbValue::from(kind),
                    DbValue::from(id),
                ],
            ))
            .await
            .expect("manager_edge_status");
        rows.into_iter()
            .next()
            .and_then(|row| row.get_string("status").ok().flatten())
    }
}

pub async fn sqlite_harness() -> (Harness, Arc<TransferDb>) {
    let raw = LocalSqliteDbPlugin::new().expect("open local sqlite");
    migrations::run_sqlite_migrations(&raw)
        .await
        .expect("apply the full sqlite migration chain");
    let db = Arc::new(TransferDb {
        inner: Arc::new(raw),
        drift: Mutex::new(None),
        armed_failure: AtomicBool::new(false),
        transactions: AtomicUsize::new(0),
        standalone_queries: AtomicUsize::new(0),
    });
    let driver = SqliteDriver {
        db: db.clone(),
        env: ENV.to_string(),
    };
    let repo: Arc<dyn BotAuthorityRepoPort> =
        Arc::new(DbBotAuthorityStore::sqlite(db.clone(), ENV.to_string()));
    (
        Harness {
            repo,
            driver: Arc::new(driver),
        },
        db,
    )
}

// ---------------------------------------------------------------------------
// Memory driver over the shared MemoryBotRepo state boundary
// ---------------------------------------------------------------------------

struct MemoryDriver {
    repo: Arc<MemoryBotRepo>,
    _dir: TempDir,
}

#[async_trait]
impl TransferDriver for MemoryDriver {
    async fn seed_owned(&self, bot_id: &str, owner_user_id: &str) {
        self.repo
            .seed_authority_owned(bot_id, owner_user_id)
            .await
            .expect("seed_owned");
    }

    async fn seed_human(&self, user_id: &str) {
        self.repo
            .ensure_human_actor(user_id, user_id)
            .await
            .expect("seed_human");
    }

    async fn seed_manager_source(&self, bot_id: &str, user_id: &str, kind: &str, id: &str) {
        self.repo
            .seed_authority_manager_source(bot_id, user_id, kind, id)
            .await
            .expect("seed_manager_source");
    }

    async fn seed_uninitialized_bot(&self, bot_id: &str) {
        self.repo
            .seed_authority_uninitialized_bot(bot_id)
            .await
            .expect("seed_uninitialized_bot");
    }

    async fn seed_pending(
        &self,
        bot_id: &str,
        from_user_id: &str,
        to_user_id: &str,
        expected_owner_version: u64,
        expires_at: &str,
    ) -> String {
        self.repo
            .seed_authority_pending_transfer_custom(
                bot_id,
                from_user_id,
                to_user_id,
                expected_owner_version,
                expires_at,
            )
            .await
            .expect("seed_pending")
    }

    async fn retire_bot(&self, bot_id: &str) {
        // The production Task 5 memory lane, reached through the
        // `BotRepoPort` contract with a system operator context.
        let retired = self
            .repo
            .retire_bot_lifecycle(
                bot_id,
                BotOperationContext {
                    operation_id: uuid::Uuid::new_v4().to_string(),
                    actor: BotOperationActor::System {
                        system_id: "ownership-transfer-tests".to_string(),
                        effective_actor_id: "ownership-transfer-tests".to_string(),
                    },
                },
            )
            .await
            .expect("retire_bot");
        assert!(retired, "the seeded bot must retire through the lane");
    }

    async fn raw_row(&self, bot_id: &str, transfer_id: &str) -> Option<RawTransferRow> {
        // The memory twin's `authority_transfer_statuses` lever reports the
        // STORED row facts (never the read-side projection).
        let statuses = self
            .repo
            .authority_transfer_statuses(bot_id)
            .await
            .expect("raw_row statuses");
        statuses
            .into_iter()
            .find(|(id, _, _)| id == transfer_id)
            .map(|(_, status, reason)| RawTransferRow {
                stored_status: status,
                terminal_reason: reason,
            })
    }

    async fn row_count(&self, bot_id: &str) -> u64 {
        self.repo
            .authority_transfer_statuses(bot_id)
            .await
            .expect("row_count")
            .len() as u64
    }

    async fn manager_edge_status(
        &self,
        bot_id: &str,
        user_id: &str,
        kind: &str,
        id: &str,
    ) -> Option<String> {
        // The memory twin keeps authority rows crate-private; raw edge
        // status proofs are SQLite-only (the memory store keeps no seed
        // lever for arbitrary edge status reads), so the memory driver
        // reports "unknown" and the shared suites never assert on raw
        // edge facts driver-neutrally.
        let _ = (bot_id, user_id, kind, id);
        None
    }
}

pub async fn memory_harness() -> Harness {
    let dir = TempDir::new().expect("bots base dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(dir.path().to_path_buf()));
    Harness {
        repo: repo.clone(),
        driver: Arc::new(MemoryDriver {
            repo,
            _dir: dir,
        }),
    }
}

// ---------------------------------------------------------------------------
// Fixture helpers shared by the suites
// ---------------------------------------------------------------------------

/// Create-command builder with an explicit idempotency key.
pub fn create_with_key(
    actor: &str,
    bot: &str,
    to: &str,
    expected: u64,
    key: &str,
) -> bcs_service_api::types::CreateOwnershipTransfer {
    bcs_service_api::types::CreateOwnershipTransfer {
        actor_user_id: actor.to_string(),
        bot_id: bot.to_string(),
        to_user_id: to.to_string(),
        expected_owner_version: expected,
        client_request_id: key.to_string(),
    }
}