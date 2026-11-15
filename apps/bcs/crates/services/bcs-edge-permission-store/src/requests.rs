//! Permission request SQL persistence.
use super::*;

/// DB-backed `PermissionRequestRepoPort` implementation (T9).
///
/// Same plumbing as [`DbPermissionProfileStore`]: `Arc<dyn DbPlugin>` + flavor.
/// Owns the SQL for `permission_requests`.
pub struct DbPermissionRequestStore {
    pub(super) db: Arc<dyn DbPlugin>,
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
                decided_at, gmt_create, gmt_modified \
         FROM permission_requests WHERE request_id = ? AND env = ? LIMIT 1";

    const LIST_INBOX_ALL_SQL: &'static str =
        "SELECT request_id, id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                decided_at, gmt_create, gmt_modified \
         FROM permission_requests WHERE to_id = ? AND env = ? ORDER BY gmt_modified DESC";

    const LIST_INBOX_STATUS_SQL: &'static str =
        "SELECT request_id, id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                decided_at, gmt_create, gmt_modified \
         FROM permission_requests WHERE to_id = ? AND env = ? AND status = ? \
         ORDER BY gmt_modified DESC";

    const LIST_SENT_ALL_SQL: &'static str =
        "SELECT request_id, id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                decided_at, gmt_create, gmt_modified \
         FROM permission_requests WHERE from_id = ? AND env = ? ORDER BY gmt_modified DESC";

    const LIST_SENT_STATUS_SQL: &'static str =
        "SELECT request_id, id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                decided_at, gmt_create, gmt_modified \
         FROM permission_requests WHERE from_id = ? AND env = ? AND status = ? \
         ORDER BY gmt_modified DESC";

    /// Bounded duplicate-guard read (LIMIT 1): existence of one caller-owned
    /// `(from_id, to_id, env, request_kind, status)` row, newest first.
    const FIND_REQUEST_SQL: &'static str =
        "SELECT request_id, id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                decided_at, gmt_create, gmt_modified \
         FROM permission_requests \
         WHERE from_id = ? AND to_id = ? AND env = ? AND request_kind = ? \
           AND status = ? AND created_by = ? \
         ORDER BY gmt_modified DESC, id DESC LIMIT 1";
}

#[async_trait]
impl PermissionRequestRepoPort for DbPermissionRequestStore {
    async fn insert(&self, request: PermissionRequest) -> ServiceResult<()> {
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
            // DB-managed timestamps: gmt_create/gmt_modified defaults apply on
            // insert, so caller-supplied values are ignored here.
            created_at: _,
            updated_at: _,
        } = request;
        self
            .execute_result(
                "insert_request",
                DbStatement::with_params(
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
                        json_to_db_value(&requested_rules),
                        DbValue::from(message.clone()),
                        DbValue::from(request_status_str(status)),
                        DbValue::from(decision_reason.clone()),
                        DbValue::from(created_by.clone()),
                        DbValue::from(decided_by.clone()),
                        // The CASE in insert_request_sql keys decided_at off status.
                        DbValue::from(request_status_str(status)),
                    ],
                ),
            )
            .await?;
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
    ) -> ServiceResult<Vec<PermissionRequest>> {
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
        }?;
        rows.iter().map(row_to_permission_request).collect()
    }

    async fn list_sent(
        &self,
        from_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> ServiceResult<Vec<PermissionRequest>> {
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
        }?;
        rows.iter().map(row_to_permission_request).collect()
    }

    async fn find_request(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
        request_kind: RequestKind,
        status: RequestStatus,
        created_by: &str,
    ) -> Option<PermissionRequest> {
        let rows = self
            .query(
                "find_request",
                DbStatement::with_params(
                    Self::FIND_REQUEST_SQL,
                    vec![
                        DbValue::from(from_id),
                        DbValue::from(to_id),
                        DbValue::from(env),
                        DbValue::from(request_kind_str(request_kind)),
                        DbValue::from(request_status_str(status)),
                        DbValue::from(created_by),
                    ],
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
                        warn!(error = %err, "db_permission_request: find_request row skipped");
                        None
                    }
                }),
            Err(err) => {
                warn!(error = %err, "db_permission_request: find_request failed");
                None
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
    ) -> ServiceResult<()> {
        // decided_at is a DB-managed timestamp: set to CURRENT_TIMESTAMP at the
        // moment the request is decided (gmt_modified advances too).
        self.execute(
            "decide_request",
            DbStatement::with_params(
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
            ),
        )
        .await
    }

    async fn backfill_edge_id(
        &self,
        request_id: &str,
        env: &str,
        edge_id: u64,
    ) -> ServiceResult<()> {
        self.execute(
            "backfill_edge_id",
            DbStatement::with_params(
                "UPDATE permission_requests SET edge_id = ?, \
                     gmt_modified = CURRENT_TIMESTAMP \
                 WHERE request_id = ? AND env = ?",
                vec![
                    DbValue::from(edge_id),
                    DbValue::from(request_id),
                    DbValue::from(env),
                ],
            ),
        )
        .await
    }
}
