//! DB-backed `PermissionRequestRepoPort` implementation (T9), split out of
//! the former over-limit `lib.rs` (plan Task 3 lib split).
//!
//! Same plumbing as [`crate::DbPermissionProfileStore`]: `Arc<dyn DbPlugin>`
//! + flavor. Owns the SQL for `permission_requests`.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbExecuteResult, DbPlugin, DbRow, DbStatement, DbTransactionStep, DbValue};
use bcs_domain::edge_permission::{
    PermissionRequest, RequestKind, RequestStatus,
};
pub use bcs_service_api::port::repo::PermissionRequestRepoPort;
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind,
    BotOperationContext, stable_step_key,
};
use bcs_service_api::{ServiceError, ServiceResult};
use tracing::warn;

use crate::authority::audit::{
    business_action_audit_id, business_action_audit_insert, require_business_operation,
};
use crate::common::{
    optional_string, optional_timestamp_text, optional_u64, parse_json_opt,
    parse_timestamp_epoch_ms, required_string, service_db_error,
};
use crate::EdgeGrantSqlFlavor;

pub struct DbPermissionRequestStore {
    db: Arc<dyn DbPlugin>,
    flavor: EdgeGrantSqlFlavor,
}

impl DbPermissionRequestStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: EdgeGrantSqlFlavor) -> Self {
        Self { db, flavor }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, EdgeGrantSqlFlavor::Mysql)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, EdgeGrantSqlFlavor::Sqlite)
    }

    pub fn flavor(&self) -> EdgeGrantSqlFlavor {
        self.flavor
    }

    async fn execute(&self, operation: &'static str, statement: DbStatement) -> ServiceResult<()> {
        self.db
            .execute(statement)
            .await
            .map(|_| ())
            .map_err(|err| {
                warn!(operation, error = %err, "db_permission_request: execute failed");
                service_db_error(operation, err)
            })
    }

    async fn execute_result(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<DbExecuteResult> {
        self.db.execute(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_permission_request: execute failed");
            service_db_error(operation, err)
        })
    }

    async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db.query(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_permission_request: query failed");
            service_db_error(operation, err)
        })
    }

    /// Idempotent INSERT on auto-increment PK `id`.
    ///
    /// `decided_at` is a DB-managed timestamp: derived from `status` at insert
    /// time (`CURRENT_TIMESTAMP` for a decided status, else NULL) so that
    /// already-approved snapshots carry a decision time without an app-supplied
    /// epoch. The domain `PermissionRequest.decided_at` field is read-only here.
    fn insert_request_sql(&self) -> &'static str {
        match self.flavor {
            EdgeGrantSqlFlavor::Mysql => {
                "INSERT INTO permission_requests \
                 (request_id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                  requested_rules, message, status, decision_reason, created_by, decided_by, \
                  decided_at) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, \
                         CASE WHEN ? IN ('approved','rejected','cancelled') \
                              THEN CURRENT_TIMESTAMP ELSE NULL END)"
            }
            EdgeGrantSqlFlavor::Sqlite => {
                "INSERT INTO permission_requests \
                 (request_id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                  requested_rules, message, status, decision_reason, created_by, decided_by, \
                  decided_at) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, \
                         CASE WHEN ? IN ('approved','rejected','cancelled') \
                              THEN CURRENT_TIMESTAMP ELSE NULL END)"
            }
        }
    }

    const SELECT_REQUEST_SQL: &'static str =
        "SELECT request_id, id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                decided_at \
         FROM permission_requests WHERE request_id = ? AND env = ? LIMIT 1";

    const LIST_INBOX_ALL_SQL: &'static str =
        "SELECT request_id, id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                decided_at \
         FROM permission_requests WHERE to_id = ? AND env = ? ORDER BY gmt_modified DESC";

    const LIST_INBOX_STATUS_SQL: &'static str =
        "SELECT request_id, id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                decided_at \
         FROM permission_requests WHERE to_id = ? AND env = ? AND status = ? \
         ORDER BY gmt_modified DESC";

    const LIST_SENT_ALL_SQL: &'static str =
        "SELECT request_id, id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                decided_at \
         FROM permission_requests WHERE from_id = ? AND env = ? ORDER BY gmt_modified DESC";

    const LIST_SENT_STATUS_SQL: &'static str =
        "SELECT request_id, id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                decided_at \
         FROM permission_requests WHERE from_id = ? AND env = ? AND status = ? \
         ORDER BY gmt_modified DESC";
}

#[async_trait]
impl PermissionRequestRepoPort for DbPermissionRequestStore {
    async fn insert(
        &self,
        request: PermissionRequest,
        operation: &BotOperationContext,
    ) -> ServiceResult<()> {
        require_business_operation(operation)?;
        let audit = request_audit_record(operation, &request.env, &request.request_id, BotActionKind::Invite);
        let PermissionRequest {
            request_id,
            edge_id,
            env,
            from_id,
            to_id,
            request_kind,
            requested_ref_id,
            requested_rules,
            message,
            status,
            decision_reason,
            created_by,
            decided_by,
            decided_at: _,
        } = request;
        // Plan Task 12 (spec §12.5): the request row and its ordinary
        // business audit row commit in ONE DbPlugin transaction — an audit
        // failure rolls the business write back with it.
        self.db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    self.insert_request_sql(),
                    vec![
                        DbValue::from(request_id),
                        match edge_id {
                            Some(id) => DbValue::from(id),
                            None => DbValue::Null,
                        },
                        DbValue::from(env.clone()),
                        DbValue::from(from_id.clone()),
                        DbValue::from(to_id.clone()),
                        DbValue::from(request_kind_str(request_kind)),
                        match requested_ref_id {
                            Some(id) => DbValue::from(id),
                            None => DbValue::Null,
                        },
                        crate::common::json_to_db_value(&requested_rules),
                        DbValue::from(message.clone()),
                        DbValue::from(request_status_str(status)),
                        DbValue::from(decision_reason.clone()),
                        DbValue::from(created_by.clone()),
                        DbValue::from(decided_by.clone()),
                        // The CASE in insert_request_sql keys decided_at off status.
                        DbValue::from(request_status_str(status)),
                    ],
                )),
                DbTransactionStep::Execute(business_action_audit_insert(&audit)),
            ])
            .await
            .map_err(|err| {
                warn!(operation = "insert_request", error = %err, "db_permission_request: transaction failed");
                service_db_error("insert_request", err)
            })?;
        Ok(())
    }

    async fn get(&self, request_id: &str, env: &str) -> Option<PermissionRequest> {
        let rows = self
            .query(
                "get_request",
                DbStatement::with_params(
                    Self::SELECT_REQUEST_SQL,
                    vec![DbValue::from(request_id), DbValue::from(env)],
                ),
            )
            .await;
        match rows {
            Ok(rows) => rows
                .into_iter()
                .next()
                .and_then(|row| match row_to_permission_request(&row) {
                    Ok(r) => Some(r),
                    Err(err) => {
                        warn!(error = %err, "db_permission_request: get row skipped");
                        None
                    }
                }),
            Err(err) => {
                warn!(error = %err, "db_permission_request: get failed");
                None
            }
        }
    }

    async fn list_inbox(
        &self,
        to_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> Vec<PermissionRequest> {
        let rows = match status {
            Some(s) => {
                self.query(
                    "list_inbox_status",
                    DbStatement::with_params(
                        Self::LIST_INBOX_STATUS_SQL,
                        vec![
                            DbValue::from(to_id),
                            DbValue::from(env),
                            DbValue::from(request_status_str(s)),
                        ],
                    ),
                )
                .await
            }
            None => {
                self.query(
                    "list_inbox_all",
                    DbStatement::with_params(
                        Self::LIST_INBOX_ALL_SQL,
                        vec![DbValue::from(to_id), DbValue::from(env)],
                    ),
                )
                .await
            }
        };
        match rows {
            Ok(rows) => rows
                .iter()
                .filter_map(|row| match row_to_permission_request(row) {
                    Ok(r) => Some(r),
                    Err(err) => {
                        warn!(error = %err, "db_permission_request: list_inbox row skipped");
                        None
                    }
                })
                .collect(),
            Err(err) => {
                warn!(error = %err, "db_permission_request: list_inbox failed");
                Vec::new()
            }
        }
    }

    async fn list_sent(
        &self,
        from_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> Vec<PermissionRequest> {
        // Mirror list_inbox's two-branch pattern: a status-filtered SELECT
        // when a status is supplied, else the all-statuses SELECT. Both
        // ordered by gmt_modified DESC.
        let rows = match status {
            Some(s) => {
                self.query(
                    "list_sent_status",
                    DbStatement::with_params(
                        Self::LIST_SENT_STATUS_SQL,
                        vec![
                            DbValue::from(from_id),
                            DbValue::from(env),
                            DbValue::from(request_status_str(s)),
                        ],
                    ),
                )
                .await
            }
            None => {
                self.query(
                    "list_sent_all",
                    DbStatement::with_params(
                        Self::LIST_SENT_ALL_SQL,
                        vec![DbValue::from(from_id), DbValue::from(env)],
                    ),
                )
                .await
            }
        };
        match rows {
            Ok(rows) => rows
                .iter()
                .filter_map(|row| match row_to_permission_request(row) {
                    Ok(r) => Some(r),
                    Err(err) => {
                        warn!(error = %err, "db_permission_request: list_sent row skipped");
                        None
                    }
                })
                .collect(),
            Err(err) => {
                warn!(error = %err, "db_permission_request: list_sent failed");
                Vec::new()
            }
        }
    }

    async fn decide(
        &self,
        request_id: &str,
        env: &str,
        status: RequestStatus,
        decided_by: &str,
        decision_reason: Option<&str>,
        operation: &BotOperationContext,
    ) -> ServiceResult<()> {
        require_business_operation(operation)?;
        let audit = request_audit_record(
            operation,
            env,
            request_id,
            BotActionKind::Update,
        );
        // decided_at is a DB-managed timestamp: set to CURRENT_TIMESTAMP at the
        // moment the request is decided (gmt_modified advances too). The
        // decision row and its business audit commit in ONE transaction.
        self.db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    "UPDATE permission_requests SET status = ?, decided_by = ?, \
                         decision_reason = ?, decided_at = CURRENT_TIMESTAMP, \
                         gmt_modified = CURRENT_TIMESTAMP \
                     WHERE request_id = ? AND env = ?",
                    vec![
                        DbValue::from(request_status_str(status)),
                        DbValue::from(decided_by),
                        match decision_reason {
                            Some(s) => DbValue::from(s),
                            None => DbValue::Null,
                        },
                        DbValue::from(request_id),
                        DbValue::from(env),
                    ],
                )),
                DbTransactionStep::Execute(business_action_audit_insert(&audit)),
            ])
            .await
            .map_err(|err| {
                warn!(operation = "decide_request", error = %err, "db_permission_request: transaction failed");
                service_db_error("decide_request", err)
            })
            .map(|_| ())
    }

    async fn backfill_edge_id(
        &self,
        request_id: &str,
        env: &str,
        edge_id: u64,
        operation: &BotOperationContext,
    ) -> ServiceResult<()> {
        require_business_operation(operation)?;
        let audit = request_audit_record(operation, env, request_id, BotActionKind::Update);
        self.db
            .transaction(vec![
                DbTransactionStep::Execute(DbStatement::with_params(
                    "UPDATE permission_requests SET edge_id = ?, \
                         gmt_modified = CURRENT_TIMESTAMP \
                     WHERE request_id = ? AND env = ?",
                    vec![
                        DbValue::from(edge_id),
                        DbValue::from(request_id),
                        DbValue::from(env),
                    ],
                )),
                DbTransactionStep::Execute(business_action_audit_insert(&audit)),
            ])
            .await
            .map_err(|err| {
                warn!(operation = "backfill_edge_id", error = %err, "db_permission_request: transaction failed");
                service_db_error("backfill_edge_id", err)
            })
            .map(|_| ())
    }
}

fn row_to_permission_request(row: &DbRow) -> ServiceResult<PermissionRequest> {
    // decided_at is a DB-managed timestamp (TEXT/`timestamp NULL`); parse the
    // stored instant back to epoch ms. `None` ⇒ not yet decided.
    let decided_at = optional_timestamp_text(row, "decided_at")?
        .and_then(|s| parse_timestamp_epoch_ms(&s));
    Ok(PermissionRequest {
        request_id: required_string(row, "request_id")?,
        edge_id: optional_u64(row, "edge_id")?,
        env: required_string(row, "env")?,
        from_id: required_string(row, "from_id")?,
        to_id: required_string(row, "to_id")?,
        request_kind: parse_request_kind(&required_string(row, "request_kind")?)?,
        requested_ref_id: optional_u64(row, "requested_ref_id")?,
        requested_rules: parse_json_opt(&optional_string(row, "requested_rules")?)?,
        message: optional_string(row, "message")?,
        status: parse_request_status(&required_string(row, "status")?)?,
        decision_reason: optional_string(row, "decision_reason")?,
        created_by: required_string(row, "created_by")?,
        decided_by: optional_string(row, "decided_by")?,
        decided_at,
    })
}

fn parse_request_kind(value: &str) -> ServiceResult<RequestKind> {
    match value {
        "connect" => Ok(RequestKind::Connect),
        "permission_profile" => Ok(RequestKind::PermissionProfile),
        "rules" => Ok(RequestKind::Rules),
        "revoke" => Ok(RequestKind::Revoke),
        other => Err(ServiceError::InternalError(format!(
            "unknown request_kind: {}",
            other
        ))),
    }
}

fn parse_request_status(value: &str) -> ServiceResult<RequestStatus> {
    match value {
        "pending" => Ok(RequestStatus::Pending),
        "approved" => Ok(RequestStatus::Approved),
        "rejected" => Ok(RequestStatus::Rejected),
        "cancelled" => Ok(RequestStatus::Cancelled),
        other => Err(ServiceError::InternalError(format!(
            "unknown request status: {}",
            other
        ))),
    }
}

fn request_audit_record(
    operation: &BotOperationContext,
    env: &str,
    request_id: &str,
    action: BotActionKind,
) -> BotActionAuditRecord {
    let step_key = stable_step_key(
        action,
        BotActionResourceKind::Friend,
        BotActionAuditPhase::Applied,
    );
    BotActionAuditRecord {
        audit_id: business_action_audit_id(env, &operation.operation_id, &step_key),
        env: env.to_string(),
        operation_id: operation.operation_id.clone(),
        step_key,
        operator: operation.actor.clone(),
        resource_kind: BotActionResourceKind::Friend,
        resource_id: request_id.to_string(),
        action,
        phase: BotActionAuditPhase::Applied,
        reason_code: None,
    }
}

fn request_kind_str(kind: RequestKind) -> &'static str {
    match kind {
        RequestKind::Connect => "connect",
        RequestKind::PermissionProfile => "permission_profile",
        RequestKind::Rules => "rules",
        RequestKind::Revoke => "revoke",
    }
}

fn request_status_str(status: RequestStatus) -> &'static str {
    match status {
        RequestStatus::Pending => "pending",
        RequestStatus::Approved => "approved",
        RequestStatus::Rejected => "rejected",
        RequestStatus::Cancelled => "cancelled",
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use bcs_db_local::LocalSqliteDbPlugin;

    use super::*;

    /// Request store backed by a fresh LocalSqliteDbPlugin with the full
    /// `permission_requests` schema (mirrors 014_edge_permission.sql).
    async fn request_store() -> DbPermissionRequestStore {
        let db = LocalSqliteDbPlugin::new().expect("local sqlite");
        db.execute(DbStatement::new(
            "CREATE TABLE permission_requests (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                request_id VARCHAR(64) NOT NULL, \
                edge_id INTEGER, \
                env VARCHAR(32) NOT NULL, \
                from_id VARCHAR(128) NOT NULL, \
                to_id VARCHAR(128) NOT NULL, \
                request_kind VARCHAR(32) NOT NULL, \
                requested_ref_id INTEGER, \
                requested_rules TEXT, \
                message TEXT, \
                status VARCHAR(16) NOT NULL DEFAULT 'pending', \
                decision_reason TEXT, \
                created_by VARCHAR(128) NOT NULL, \
                decided_by VARCHAR(128), \
                decided_at TEXT, \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        ))
        .await
        .expect("create permission_requests");
        // Plan Task 12: the insert/decide lanes commit one
        // bcs_bot_action_audits row in the SAME transaction (mirrors the
        // migration-032 business audit table).
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_bot_action_audits (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                audit_id VARCHAR(128) NOT NULL, \
                env VARCHAR(32) NOT NULL, \
                operation_id VARCHAR(64) NOT NULL, \
                step_key VARCHAR(128) NOT NULL, \
                operator_kind VARCHAR(16) NOT NULL, \
                operator_id VARCHAR(256) NOT NULL, \
                operator_user_id VARCHAR(256), \
                effective_actor_id VARCHAR(256) NOT NULL, \
                resource_kind VARCHAR(32) NOT NULL, \
                resource_id VARCHAR(256) NOT NULL, \
                action VARCHAR(32) NOT NULL, \
                phase VARCHAR(16) NOT NULL, \
                reason_code VARCHAR(64), \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        ))
        .await
        .expect("create bcs_bot_action_audits");
        DbPermissionRequestStore::sqlite(Arc::new(db))
    }

    fn test_operation(label: &str) -> BotOperationContext {
        BotOperationContext {
            operation_id: format!("test-op-{label}"),
            actor: bcs_service_api::types::BotOperationActor::Human {
                user_id: "85020".to_string(),
                effective_actor_id: "human_85020".to_string(),
            },
        }
    }

    static REQUEST_ID_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

    fn next_test_request_id() -> String {
        format!(
            "test_{}",
            REQUEST_ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        )
    }

    fn sample_request(env: &str) -> PermissionRequest {
        PermissionRequest {
            request_id: next_test_request_id(),
            edge_id: None,
            env: env.to_string(),
            from_id: "human_a".to_string(),
            to_id: "bot_b".to_string(),
            request_kind: RequestKind::Connect,
            requested_ref_id: None,
            requested_rules: None,
            message: Some("hi".to_string()),
            status: RequestStatus::Pending,
            decision_reason: None,
            created_by: "human_a".to_string(),
            decided_by: None,
            decided_at: None,
        }
    }

    #[test]
    fn permission_request_decided_at_accepts_bytes_timestamp() {
        let mut columns = std::collections::BTreeMap::new();
        columns.insert("request_id".to_string(), DbValue::from("req-1"));
        columns.insert("edge_id".to_string(), DbValue::Null);
        columns.insert("env".to_string(), DbValue::from("dev"));
        columns.insert("from_id".to_string(), DbValue::from("human_1"));
        columns.insert("to_id".to_string(), DbValue::from("x:bot"));
        columns.insert("request_kind".to_string(), DbValue::from("connect"));
        columns.insert("requested_ref_id".to_string(), DbValue::Null);
        columns.insert("requested_rules".to_string(), DbValue::Null);
        columns.insert("message".to_string(), DbValue::Null);
        columns.insert("status".to_string(), DbValue::from("approved"));
        columns.insert("decision_reason".to_string(), DbValue::Null);
        columns.insert("created_by".to_string(), DbValue::from("human_1"));
        columns.insert("decided_by".to_string(), DbValue::from("auto"));
        columns.insert(
            "decided_at".to_string(),
            DbValue::from(b"2026-09-02 13:41:55".to_vec()),
        );
        let row = DbRow::new(columns);

        let request = row_to_permission_request(&row).expect("permission request row");
        assert_eq!(request.decided_at, Some(1_788_356_515_000));
    }

    #[tokio::test]
    async fn request_insert_and_get() {
        let store = request_store().await;
        let req = sample_request("dev");
        let request_id = req.request_id.clone();
        store.insert(req, &test_operation("a")).await.expect("insert");
        let got = store.get(&request_id, "dev").await.expect("found");
        assert_eq!(got.request_id, request_id);
        assert_eq!(got.status, RequestStatus::Pending);
        assert!(got.edge_id.is_none(), "pending → no edge_id");
        assert_eq!(got.request_kind, RequestKind::Connect);
        assert!(store.get("missing", "dev").await.is_none(), "missing → None");
    }

    #[tokio::test]
    async fn request_list_inbox_all_and_status_filter() {
        let store = request_store().await;
        let r1 = sample_request("dev");
        let r1_id = r1.request_id.clone();
        store.insert(r1, &test_operation("r1")).await.expect("insert r1");
        let r2 = sample_request("dev");
        let r2_id = r2.request_id.clone();
        store.insert(r2, &test_operation("r2")).await.expect("insert r2");
        // decide r2 → approved
        store
            .decide(
                &r2_id,
                "dev",
                RequestStatus::Approved,
                "85020",
                Some("ok"),
                &test_operation("decide"),
            )
            .await
            .expect("decide");
        let all = store.list_inbox("bot_b", "dev", None).await;
        assert_eq!(all.len(), 2, "both visible without filter");
        let pending = store
            .list_inbox("bot_b", "dev", Some(RequestStatus::Pending))
            .await;
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].request_id, r1_id);
        let approved = store
            .list_inbox("bot_b", "dev", Some(RequestStatus::Approved))
            .await;
        assert_eq!(approved.len(), 1);
        assert_eq!(approved[0].request_id, r2_id);
        assert_eq!(approved[0].decided_by.as_deref(), Some("85020"));
        assert!(
            approved[0].decided_at.is_some(),
            "decided_at set to decision time"
        );
    }

    #[tokio::test]
    async fn request_backfill_edge_id() {
        let store = request_store().await;
        let req = sample_request("dev");
        let request_id = req.request_id.clone();
        store.insert(req, &test_operation("a")).await.expect("insert");
        store
            .backfill_edge_id(&request_id, "dev", 1001, &test_operation("bf"))
            .await
            .expect("backfill");
        let got = store.get(&request_id, "dev").await.expect("found");
        assert_eq!(got.edge_id, Some(1001));
    }
}