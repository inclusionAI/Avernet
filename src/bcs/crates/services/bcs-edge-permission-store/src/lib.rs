//! Database-backed implementation of the `edge_grants` repository port.
//!
//! Owns the SQL for `edge_grants` + `permission_profiles` (default-profile
//! cache) and depends only on the driver-level `bcs-db-api` contract. The
//! composition root decides which concrete DB plugin backs it. Mirrors the
//! `bcs-relation-store` plumbing (MySQL + SQLite via `DbSqlFlavor`).
//!
//! Implements [`EdgeGrantRepoPort`] (installment 1) for [`DbEdgeGrantStore`]
//! and [`PermissionProfileRepoPort`] (T8) for [`DbPermissionProfileStore`].
//! `PermissionRequestRepoPort` (T9) will be added to this same crate later.

use std::collections::HashSet;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_db_api::{DbError, DbExecuteResult, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::edge_permission::{
    EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType, PermissionProfile, ProfileStatus,
    PermissionRequest, RequestKind, RequestStatus,
};
pub use bcs_service_api::port::repo::EdgeGrantRepoPort;
pub use bcs_service_api::port::repo::PermissionProfileRepoPort;
pub use bcs_service_api::port::repo::PermissionRequestRepoPort;
use bcs_service_api::{ServiceError, ServiceResult};
use sha2::{Digest, Sha256};
use tracing::warn;

pub type EdgeGrantSqlFlavor = DbSqlFlavor;

/// MySQL-backed edge-grant repository.
pub type MysqlEdgeGrantRepo = DbEdgeGrantStore;

/// SQLite-backed edge-grant repository.
pub type SqliteEdgeGrantRepo = DbEdgeGrantStore;

/// DB-backed `EdgeGrantRepoPort` implementation.
///
/// Holds an `Arc<dyn DbPlugin>` + flavor, like `DbRelationStore`. The store
/// owns the SQL; callers inject the concrete plugin (mysql / sqlite local).
pub struct DbEdgeGrantStore {
    db: Arc<dyn DbPlugin>,
    flavor: EdgeGrantSqlFlavor,
}

impl DbEdgeGrantStore {
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
        self.execute_result(operation, statement).await.map(|_| ())
    }

    async fn execute_result(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<DbExecuteResult> {
        self.db.execute(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_edge_grant: execute failed");
            service_db_error(operation, err)
        })
    }

    async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db.query(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_edge_grant: query failed");
            service_db_error(operation, err)
        })
    }

    /// INSERT with idempotent behavior on the unique key
    /// `(from_id, to_id, env, grant_ref_id)`: SQLite `ON CONFLICT DO NOTHING`
    /// vs MySQL `INSERT IGNORE`.
    fn insert_grant_sql(&self) -> &'static str {
        match self.flavor {
            EdgeGrantSqlFlavor::Mysql => {
                "INSERT IGNORE INTO edge_grants \
                 (edge_id, env, from_id, to_id, grant_kind, grant_ref_id, rules, \
                  status, originator_policy_type, originator_policy_data, \
                  created_at, updated_at) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            }
            EdgeGrantSqlFlavor::Sqlite => {
                "INSERT INTO edge_grants \
                 (edge_id, env, from_id, to_id, grant_kind, grant_ref_id, rules, \
                  status, originator_policy_type, originator_policy_data, \
                  created_at, updated_at) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) \
                 ON CONFLICT(from_id, to_id, env, grant_ref_id) DO NOTHING"
            }
        }
    }
}

#[async_trait]
impl EdgeGrantRepoPort for DbEdgeGrantStore {
    async fn list_active_grants(&self, from: &str, to: &str, env: &str) -> Vec<EdgeGrant> {
        let rows = self
            .query(
                "list_active_grants",
                DbStatement::with_params(
                    "SELECT edge_id, env, from_id, to_id, grant_kind, grant_ref_id, \
                            rules, status, originator_policy_type, originator_policy_data \
                     FROM edge_grants \
                     WHERE from_id = ? AND to_id = ? AND env = ? AND status = 'approved'",
                    vec![
                        DbValue::from(from),
                        DbValue::from(to),
                        DbValue::from(env),
                    ],
                ),
            )
            .await;
        match rows {
            Ok(rows) => rows
                .iter()
                .filter_map(|row| match row_to_edge_grant(row) {
                    Ok(grant) => Some(grant),
                    Err(err) => {
                        warn!(error = %err, "db_edge_grant: list_active_grants row skipped");
                        None
                    }
                })
                .collect(),
            Err(err) => {
                warn!(error = %err, "db_edge_grant: list_active_grants failed");
                Vec::new()
            }
        }
    }

    async fn has_friend_edge(&self, x: &str, y: &str, env: &str) -> bool {
        // D12: any-direction default-profile edge. x→y uses y's default (dy);
        // y→x uses x's default (dx). Either direction being a friend edge
        // counts (None default ⇒ that side is human ⇒ that direction cannot
        // be a friend edge).
        let dy = self.get_default_profile_id(y, env).await;
        let dx = self.get_default_profile_id(x, env).await;

        if dy.is_some() && self.has_default_edge(x, y, env, dy.as_deref()).await {
            return true;
        }
        if dx.is_some() && self.has_default_edge(y, x, env, dx.as_deref()).await {
            return true;
        }
        false
    }

    async fn list_friends(&self, actor: &str, env: &str) -> Vec<String> {
        // §4.6 two-branch index scan + (bot_id,env)→default cache +
        // memory compare. Avoids a SQL join.
        let d_actor = self.get_default_profile_id(actor, env).await;

        let mut cache: std::collections::HashMap<String, Option<String>> =
            std::collections::HashMap::new();
        let mut friends: HashSet<String> = HashSet::new();

        // Branch ① actor initiated: actor → to_id. Keep if grant_ref_id ==
        // to_id's default profile id (Some).
        let outbound = self
            .query(
                "list_friends_outbound",
                DbStatement::with_params(
                    "SELECT to_id, grant_ref_id FROM edge_grants \
                     WHERE from_id = ? AND env = ? AND status = 'approved' \
                       AND grant_kind = 'permission_profile'",
                    vec![DbValue::from(actor), DbValue::from(env)],
                ),
            )
            .await;
        if let Ok(rows) = outbound {
            for row in rows {
                let to_id = match required_string(&row, "to_id") {
                    Ok(v) => v,
                    Err(err) => {
                        warn!(error = %err, "list_friends_outbound: missing to_id");
                        continue;
                    }
                };
                let grant_ref_id = match required_string(&row, "grant_ref_id") {
                    Ok(v) => v,
                    Err(err) => {
                        warn!(error = %err, "list_friends_outbound: missing grant_ref_id");
                        continue;
                    }
                };
                let d_y = match cache.get(&to_id) {
                    Some(v) => v.clone(),
                    None => {
                        let v = self.get_default_profile_id(&to_id, env).await;
                        cache.insert(to_id.clone(), v.clone());
                        v
                    }
                };
                // Outbound friend edge actor→to_id is keyed on to_id's
                // default profile id (dy), per §4.6 ①.
                if let Some(d_y) = d_y {
                    if grant_ref_id == d_y {
                        friends.insert(to_id);
                    }
                }
            }
        }

        // Branch ② others initiated: from_id → actor. Only if actor has a
        // default profile (is a bot). Keep if grant_ref_id == actor's default.
        if let Some(d_actor) = d_actor.as_deref() {
            let inbound = self
                .query(
                    "list_friends_inbound",
                    DbStatement::with_params(
                        "SELECT from_id, grant_ref_id FROM edge_grants \
                         WHERE to_id = ? AND env = ? AND status = 'approved' \
                           AND grant_kind = 'permission_profile'",
                        vec![DbValue::from(actor), DbValue::from(env)],
                    ),
                )
                .await;
            if let Ok(rows) = inbound {
                for row in rows {
                    let from_id = match required_string(&row, "from_id") {
                        Ok(v) => v,
                        Err(err) => {
                            warn!(error = %err, "list_friends_inbound: missing from_id");
                            continue;
                        }
                    };
                    let grant_ref_id = match required_string(&row, "grant_ref_id") {
                        Ok(v) => v,
                        Err(err) => {
                            warn!(error = %err, "list_friends_inbound: missing grant_ref_id");
                            continue;
                        }
                    };
                    if grant_ref_id == d_actor {
                        friends.insert(from_id);
                    }
                }
            }
        }

        let mut out: Vec<String> = friends.into_iter().collect();
        out.sort();
        out
    }

    async fn insert_grant(&self, grant: EdgeGrant) -> ServiceResult<()> {
        let now = now_millis();
        let rules_val = json_to_db_value(&grant.rules);
        let policy_data_val = json_to_db_value(&grant.originator_policy_data);
        self.execute(
            "insert_grant",
            DbStatement::with_params(
                self.insert_grant_sql(),
                vec![
                    DbValue::from(grant.edge_id),
                    DbValue::from(grant.env),
                    DbValue::from(grant.from_id),
                    DbValue::from(grant.to_id),
                    DbValue::from(grant_kind_str(grant.grant_kind)),
                    DbValue::from(grant.grant_ref_id),
                    rules_val,
                    DbValue::from(edge_status_str(grant.status)),
                    DbValue::from(originator_policy_type_str(grant.originator_policy_type)),
                    policy_data_val,
                    DbValue::from(now as i64),
                    DbValue::from(now as i64),
                ],
            ),
        )
        .await
    }

    async fn revoke_grant(&self, edge_id: &str, env: &str) -> ServiceResult<()> {
        self.execute(
            "revoke_grant",
            DbStatement::with_params(
                "UPDATE edge_grants SET status = 'revoked', updated_at = ? \
                 WHERE edge_id = ? AND env = ?",
                vec![
                    DbValue::from(now_millis() as i64),
                    DbValue::from(edge_id),
                    DbValue::from(env),
                ],
            ),
        )
        .await
    }

    async fn get_default_profile_id(&self, bot_id: &str, env: &str) -> Option<String> {
        let rows = self
            .query(
                "get_default_profile_id",
                DbStatement::with_params(
                    "SELECT permission_profile_id FROM permission_profiles \
                     WHERE bot_id = ? AND env = ? AND is_default = 1 \
                       AND status = 'active' LIMIT 1",
                    vec![DbValue::from(bot_id), DbValue::from(env)],
                ),
            )
            .await;
        match rows {
            Ok(rows) => rows.into_iter().next().and_then(|row| {
                row.get_string("permission_profile_id")
                    .ok()
                    .flatten()
            }),
            Err(err) => {
                warn!(error = %err, "db_edge_grant: get_default_profile_id failed");
                None
            }
        }
    }
}

impl DbEdgeGrantStore {
    /// Helper for `has_friend_edge`: does an approved default-profile edge
    /// `from → to` exist with `grant_ref_id` equal to `default_ref`?
    async fn has_default_edge(
        &self,
        from: &str,
        to: &str,
        env: &str,
        default_ref: Option<&str>,
    ) -> bool {
        let Some(default_ref) = default_ref else {
            return false;
        };
        let rows = self
            .query(
                "has_default_edge",
                DbStatement::with_params(
                    "SELECT 1 AS hit FROM edge_grants \
                     WHERE from_id = ? AND to_id = ? AND env = ? \
                       AND status = 'approved' \
                       AND grant_kind = 'permission_profile' \
                       AND grant_ref_id = ? LIMIT 1",
                    vec![
                        DbValue::from(from),
                        DbValue::from(to),
                        DbValue::from(env),
                        DbValue::from(default_ref),
                    ],
                ),
            )
            .await;
        match rows {
            Ok(rows) => !rows.is_empty(),
            Err(err) => {
                warn!(error = %err, "db_edge_grant: has_default_edge failed");
                false
            }
        }
    }
}

/// DB-backed `PermissionProfileRepoPort` implementation (T8).
///
/// Same plumbing as [`DbEdgeGrantStore`]: holds an `Arc<dyn DbPlugin>` + flavor.
/// Owns the SQL for `permission_profiles`. `PermissionProfileRepoPort` is
/// implemented on this type, not on `DbEdgeGrantStore`, so callers wire the
/// profile store independently at the composition root (T11).
pub struct DbPermissionProfileStore {
    db: Arc<dyn DbPlugin>,
    flavor: EdgeGrantSqlFlavor,
}

impl DbPermissionProfileStore {
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
                warn!(operation, error = %err, "db_permission_profile: execute failed");
                service_db_error(operation, err)
            })
    }

    async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db.query(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_permission_profile: query failed");
            service_db_error(operation, err)
        })
    }

    /// Idempotent INSERT of the default profile on PK `permission_profile_id`.
    /// SQLite `ON CONFLICT(permission_profile_id) DO NOTHING` vs MySQL
    /// `INSERT IGNORE` — D12 rule 2: never overwrite or bump an existing default.
    fn insert_default_profile_sql(&self) -> &'static str {
        match self.flavor {
            EdgeGrantSqlFlavor::Mysql => {
                "INSERT IGNORE INTO permission_profiles \
                 (permission_profile_id, bot_id, env, name, description, rules_template, \
                  revision, digest, is_default, status, created_by, updated_by, \
                  created_at, updated_at) \
                 VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 1, 'active', 'system', NULL, ?, ?)"
            }
            EdgeGrantSqlFlavor::Sqlite => {
                "INSERT INTO permission_profiles \
                 (permission_profile_id, bot_id, env, name, description, rules_template, \
                  revision, digest, is_default, status, created_by, updated_by, \
                  created_at, updated_at) \
                 VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 1, 'active', 'system', NULL, ?, ?) \
                 ON CONFLICT(permission_profile_id) DO NOTHING"
            }
        }
    }

    /// SELECT all 14 columns of a default, active profile for (bot_id, env).
    const SELECT_DEFAULT_SQL: &'static str =
        "SELECT permission_profile_id, bot_id, env, name, description, rules_template, \
                revision, digest, is_default, status, created_by, updated_by, \
                created_at, updated_at \
         FROM permission_profiles \
         WHERE bot_id = ? AND env = ? AND is_default = 1 AND status = 'active' LIMIT 1";
}

#[async_trait]
impl PermissionProfileRepoPort for DbPermissionProfileStore {
    async fn ensure_default_profile(&self, bot_id: &str, env: &str) -> ServiceResult<()> {
        let profile_id = format!("pp_{}_default", bot_id);
        let digest = sha256_hex(WILDCARD_ALLOW);
        let now = now_millis();
        // INSERT all 14 cols (description/updated_by NULL). Idempotent on PK: a
        // pre-existing default is left untouched — its rules_template, revision,
        // and digest are NOT overwritten (D12 rule 2).
        self.execute(
            "ensure_default_profile",
            DbStatement::with_params(
                self.insert_default_profile_sql(),
                vec![
                    DbValue::from(profile_id),
                    DbValue::from(bot_id),
                    DbValue::from(env),
                    DbValue::from("default"),
                    DbValue::from(WILDCARD_ALLOW),
                    DbValue::from(1_i64), // revision
                    DbValue::from(digest),
                    DbValue::from(now as i64), // created_at
                    DbValue::from(now as i64), // updated_at
                ],
            ),
        )
        .await
    }

    async fn get_active_default(&self, bot_id: &str, env: &str) -> Option<PermissionProfile> {
        let rows = self
            .query(
                "get_active_default",
                DbStatement::with_params(
                    Self::SELECT_DEFAULT_SQL,
                    vec![DbValue::from(bot_id), DbValue::from(env)],
                ),
            )
            .await;
        match rows {
            Ok(rows) => rows.into_iter().next().and_then(|row| {
                match row_to_permission_profile(&row) {
                    Ok(profile) => Some(profile),
                    Err(err) => {
                        warn!(error = %err, "db_permission_profile: get_active_default row skipped");
                        None
                    }
                }
            }),
            Err(err) => {
                warn!(error = %err, "db_permission_profile: get_active_default failed");
                None
            }
        }
    }

    async fn upsert_revision(&self, profile: PermissionProfile) -> ServiceResult<()> {
        // D12 rule 2: profile_id is UNCHANGED. Only rules_template / revision /
        // digest / updated_by / updated_at move; is_default/status/bot_id/env/
        // name/created_by/created_at are left as-is.
        let rules_val = json_to_db_value(&Some(profile.rules_template));
        self.execute(
            "upsert_revision",
            DbStatement::with_params(
                "UPDATE permission_profiles SET rules_template = ?, revision = ?, \
                     digest = ?, updated_by = ?, updated_at = ? \
                 WHERE permission_profile_id = ?",
                vec![
                    rules_val,
                    DbValue::from(profile.revision as i64),
                    DbValue::from(profile.digest),
                    DbValue::from(profile.updated_by),
                    DbValue::from(profile.updated_at as i64),
                    DbValue::from(profile.permission_profile_id),
                ],
            ),
        )
        .await
    }
}

/// DB-backed `PermissionRequestRepoPort` implementation (T9).
///
/// Same plumbing as [`DbPermissionProfileStore`]: `Arc<dyn DbPlugin>` + flavor.
/// Owns the SQL for `permission_requests`.
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

    /// Idempotent INSERT on PK `request_id`: SQLite
    /// `ON CONFLICT(request_id) DO NOTHING` vs MySQL `INSERT IGNORE`.
    fn insert_request_sql(&self) -> &'static str {
        match self.flavor {
            EdgeGrantSqlFlavor::Mysql => {
                "INSERT IGNORE INTO permission_requests \
                 (request_id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                  requested_rules, message, status, decision_reason, created_by, decided_by, \
                  created_at, updated_at, decided_at) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            }
            EdgeGrantSqlFlavor::Sqlite => {
                "INSERT INTO permission_requests \
                 (request_id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                  requested_rules, message, status, decision_reason, created_by, decided_by, \
                  created_at, updated_at, decided_at) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) \
                 ON CONFLICT(request_id) DO NOTHING"
            }
        }
    }

    const SELECT_REQUEST_SQL: &'static str =
        "SELECT request_id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                created_at, updated_at, decided_at \
         FROM permission_requests WHERE request_id = ? AND env = ? LIMIT 1";

    const LIST_INBOX_ALL_SQL: &'static str =
        "SELECT request_id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                created_at, updated_at, decided_at \
         FROM permission_requests WHERE to_id = ? AND env = ? ORDER BY updated_at DESC";

    const LIST_INBOX_STATUS_SQL: &'static str =
        "SELECT request_id, edge_id, env, from_id, to_id, request_kind, requested_ref_id, \
                requested_rules, message, status, decision_reason, created_by, decided_by, \
                created_at, updated_at, decided_at \
         FROM permission_requests WHERE to_id = ? AND env = ? AND status = ? \
         ORDER BY updated_at DESC";
}

#[async_trait]
impl PermissionRequestRepoPort for DbPermissionRequestStore {
    async fn insert(&self, request: PermissionRequest) -> ServiceResult<()> {
        self.execute(
            "insert_request",
            DbStatement::with_params(
                self.insert_request_sql(),
                vec![
                    DbValue::from(request.request_id),
                    DbValue::from(request.edge_id),
                    DbValue::from(request.env),
                    DbValue::from(request.from_id),
                    DbValue::from(request.to_id),
                    DbValue::from(request_kind_str(request.request_kind)),
                    DbValue::from(request.requested_ref_id),
                    json_to_db_value(&request.requested_rules),
                    DbValue::from(request.message),
                    DbValue::from(request_status_str(request.status)),
                    DbValue::from(request.decision_reason),
                    DbValue::from(request.created_by),
                    DbValue::from(request.decided_by),
                    DbValue::from(request.created_at as i64),
                    DbValue::from(request.updated_at as i64),
                    request
                        .decided_at
                        .map(|t| DbValue::from(t as i64))
                        .unwrap_or(DbValue::Null),
                ],
            ),
        )
        .await
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

    async fn decide(
        &self,
        request_id: &str,
        env: &str,
        status: RequestStatus,
        decided_by: &str,
        decision_reason: Option<&str>,
        decided_at: u64,
    ) -> ServiceResult<()> {
        self.execute(
            "decide_request",
            DbStatement::with_params(
                "UPDATE permission_requests SET status = ?, decided_by = ?, \
                     decision_reason = ?, decided_at = ?, updated_at = ? \
                 WHERE request_id = ? AND env = ?",
                vec![
                    DbValue::from(request_status_str(status)),
                    DbValue::from(decided_by),
                    match decision_reason {
                        Some(s) => DbValue::from(s),
                        None => DbValue::Null,
                    },
                    DbValue::from(decided_at as i64),
                    DbValue::from(now_millis() as i64),
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
        edge_id: &str,
    ) -> ServiceResult<()> {
        self.execute(
            "backfill_edge_id",
            DbStatement::with_params(
                "UPDATE permission_requests SET edge_id = ?, updated_at = ? \
                 WHERE request_id = ? AND env = ?",
                vec![
                    DbValue::from(edge_id),
                    DbValue::from(now_millis() as i64),
                    DbValue::from(request_id),
                    DbValue::from(env),
                ],
            ),
        )
        .await
    }
}

fn row_to_permission_request(row: &DbRow) -> ServiceResult<PermissionRequest> {
    let created_at = row
        .get_i64("created_at")
        .map_err(|err| service_db_error("created_at", err))?
        .unwrap_or(0) as u64;
    let updated_at = row
        .get_i64("updated_at")
        .map_err(|err| service_db_error("updated_at", err))?
        .unwrap_or(0) as u64;
    let decided_at = row
        .get_i64("decided_at")
        .map_err(|err| service_db_error("decided_at", err))?
        .map(|v| v as u64);
    Ok(PermissionRequest {
        request_id: required_string(row, "request_id")?,
        edge_id: optional_string(row, "edge_id")?,
        env: required_string(row, "env")?,
        from_id: required_string(row, "from_id")?,
        to_id: required_string(row, "to_id")?,
        request_kind: parse_request_kind(&required_string(row, "request_kind")?)?,
        requested_ref_id: optional_string(row, "requested_ref_id")?,
        requested_rules: parse_json_opt(&optional_string(row, "requested_rules")?)?,
        message: optional_string(row, "message")?,
        status: parse_request_status(&required_string(row, "status")?)?,
        decision_reason: optional_string(row, "decision_reason")?,
        created_by: required_string(row, "created_by")?,
        decided_by: optional_string(row, "decided_by")?,
        created_at,
        updated_at,
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

fn row_to_permission_profile(row: &DbRow) -> ServiceResult<PermissionProfile> {
    let revision = row
        .get_i64("revision")
        .map_err(|err| service_db_error("revision", err))?
        .unwrap_or(0) as u64;
    let is_default = row
        .get_i64("is_default")
        .map_err(|err| service_db_error("is_default", err))?
        .unwrap_or(0)
        != 0;
    let created_at = row
        .get_i64("created_at")
        .map_err(|err| service_db_error("created_at", err))?
        .unwrap_or(0) as u64;
    let updated_at = row
        .get_i64("updated_at")
        .map_err(|err| service_db_error("updated_at", err))?
        .unwrap_or(0) as u64;
    // rules_template is NOT NULL in DDL, so it is always present; parse string→Value.
    let rules_template = match required_string(row, "rules_template")? {
        ref s if s.is_empty() => serde_json::Value::Null,
        ref s => serde_json::from_str::<serde_json::Value>(s).map_err(|err| {
            ServiceError::InternalError(format!("permission_profiles json parse: {}", err))
        })?,
    };
    Ok(PermissionProfile {
        permission_profile_id: required_string(row, "permission_profile_id")?,
        bot_id: required_string(row, "bot_id")?,
        env: required_string(row, "env")?,
        name: required_string(row, "name")?,
        description: optional_string(row, "description")?,
        rules_template,
        revision,
        digest: required_string(row, "digest")?,
        is_default,
        status: parse_profile_status(&required_string(row, "status")?)?,
        created_by: required_string(row, "created_by")?,
        updated_by: optional_string(row, "updated_by")?,
        created_at,
        updated_at,
    })
}

fn row_to_edge_grant(row: &DbRow) -> ServiceResult<EdgeGrant> {
    Ok(EdgeGrant {
        edge_id: required_string(row, "edge_id")?,
        env: required_string(row, "env")?,
        from_id: required_string(row, "from_id")?,
        to_id: required_string(row, "to_id")?,
        grant_kind: parse_grant_kind(&required_string(row, "grant_kind")?)?,
        grant_ref_id: required_string(row, "grant_ref_id")?,
        rules: parse_json_opt(&optional_string(row, "rules")?)?,
        status: parse_edge_status(&required_string(row, "status")?)?,
        originator_policy_type: parse_originator_policy_type(
            &required_string(row, "originator_policy_type")?,
        )?,
        originator_policy_data: parse_json_opt(&optional_string(row, "originator_policy_data")?)?,
    })
}

fn required_string(row: &DbRow, column: &'static str) -> ServiceResult<String> {
    row.get_string(column)
        .map_err(|err| service_db_error(column, err))?
        .ok_or_else(|| {
            ServiceError::InternalError(format!("missing edge_grants column {}", column))
        })
}

fn optional_string(row: &DbRow, column: &'static str) -> ServiceResult<Option<String>> {
    row.get_string(column).map_err(|err| service_db_error(column, err))
}

fn parse_json_opt(value: &Option<String>) -> ServiceResult<Option<serde_json::Value>> {
    match value {
        None => Ok(None),
        Some(s) if s.is_empty() => Ok(None),
        Some(s) => serde_json::from_str::<serde_json::Value>(s).map(Some).map_err(|err| {
            ServiceError::InternalError(format!("edge_grants json parse: {}", err))
        }),
    }
}

fn parse_grant_kind(value: &str) -> ServiceResult<GrantKind> {
    match value {
        "permission_profile" => Ok(GrantKind::PermissionProfile),
        "rules" => Ok(GrantKind::Rules),
        other => Err(ServiceError::InternalError(format!(
            "unknown grant_kind: {}",
            other
        ))),
    }
}

fn parse_edge_status(value: &str) -> ServiceResult<EdgeStatus> {
    match value {
        "approved" => Ok(EdgeStatus::Approved),
        "revoked" => Ok(EdgeStatus::Revoked),
        other => Err(ServiceError::InternalError(format!(
            "unknown edge status: {}",
            other
        ))),
    }
}

fn parse_originator_policy_type(value: &str) -> ServiceResult<OriginatorPolicyType> {
    match value {
        "any" => Ok(OriginatorPolicyType::Any),
        "same_as_from" => Ok(OriginatorPolicyType::SameAsFrom),
        "specific" => Ok(OriginatorPolicyType::Specific),
        "owner" => Ok(OriginatorPolicyType::Owner),
        other => Err(ServiceError::InternalError(format!(
            "unknown originator_policy_type: {}",
            other
        ))),
    }
}

fn grant_kind_str(kind: GrantKind) -> &'static str {
    match kind {
        GrantKind::PermissionProfile => "permission_profile",
        GrantKind::Rules => "rules",
    }
}

fn edge_status_str(status: EdgeStatus) -> &'static str {
    match status {
        EdgeStatus::Approved => "approved",
        EdgeStatus::Revoked => "revoked",
    }
}

fn originator_policy_type_str(policy: OriginatorPolicyType) -> &'static str {
    match policy {
        OriginatorPolicyType::Any => "any",
        OriginatorPolicyType::SameAsFrom => "same_as_from",
        OriginatorPolicyType::Specific => "specific",
        OriginatorPolicyType::Owner => "owner",
    }
}

fn parse_profile_status(value: &str) -> ServiceResult<ProfileStatus> {
    match value {
        "active" => Ok(ProfileStatus::Active),
        "deleted" => Ok(ProfileStatus::Deleted),
        other => Err(ServiceError::InternalError(format!(
            "unknown profile status: {}",
            other
        ))),
    }
}

/// Wildcard-allow rules template (default profile seed, spec §5.1.1).
const WILDCARD_ALLOW: &str = r#"[{"tool":"*","specifier":"*","effect":"allow"}]"#;

/// SHA-256 hex digest of a string (used for `permission_profiles.digest`).
fn sha256_hex(input: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(input.as_bytes());
    hex_encode(&hasher.finalize())
}

/// Lowercase hex encoding without pulling in an extra dependency.
fn hex_encode(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(&format!("{:02x}", byte));
    }
    out
}

fn json_to_db_value(value: &Option<serde_json::Value>) -> DbValue {
    match value {
        None => DbValue::Null,
        Some(v) => match serde_json::to_string(v) {
            Ok(s) => DbValue::from(s),
            Err(err) => {
                warn!(error = %err, "edge_grants: failed to serialize json, storing NULL");
                DbValue::Null
            }
        },
    }
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn service_db_error(operation: &'static str, err: DbError) -> ServiceError {
    ServiceError::InternalError(format!("edge_grants db {}: {}", operation, err))
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_db_local::LocalSqliteDbPlugin;

    async fn sqlite_store() -> DbEdgeGrantStore {
        let db = LocalSqliteDbPlugin::new().expect("local sqlite");
        // edge_grants + permission_profiles schema (mirrors
        // migrations/mysql/006_edge_permission.sql for SQLite).
        db.execute(DbStatement::new(
            "CREATE TABLE edge_grants (\
                edge_id VARCHAR(128) NOT NULL, \
                env VARCHAR(32) NOT NULL, \
                from_id VARCHAR(128) NOT NULL, \
                to_id VARCHAR(128) NOT NULL, \
                grant_kind VARCHAR(32) NOT NULL, \
                grant_ref_id VARCHAR(128) NOT NULL, \
                rules TEXT, \
                status VARCHAR(16) NOT NULL DEFAULT 'approved', \
                originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any', \
                originator_policy_data TEXT, \
                created_at INTEGER NOT NULL, \
                updated_at INTEGER NOT NULL, \
                PRIMARY KEY (edge_id), \
                UNIQUE (from_id, to_id, env, grant_ref_id))",
        ))
        .await
        .expect("create edge_grants");
        db.execute(DbStatement::new(
            "CREATE TABLE permission_profiles (\
                permission_profile_id VARCHAR(128) NOT NULL, \
                bot_id VARCHAR(128) NOT NULL, \
                env VARCHAR(32) NOT NULL, \
                name VARCHAR(128) NOT NULL, \
                rules_template TEXT NOT NULL, \
                is_default INTEGER NOT NULL DEFAULT 0, \
                status VARCHAR(16) NOT NULL DEFAULT 'active', \
                created_by VARCHAR(128) NOT NULL, \
                created_at INTEGER NOT NULL, \
                updated_at INTEGER NOT NULL, \
                PRIMARY KEY (permission_profile_id))",
        ))
        .await
        .expect("create permission_profiles");
        DbEdgeGrantStore::sqlite(Arc::new(db))
    }

    async fn seed_default(store: &DbEdgeGrantStore, bot_id: &str, env: &str, profile_id: &str) {
        store
            .execute(
                "seed_profile",
                DbStatement::with_params(
                    "INSERT INTO permission_profiles \
                     (permission_profile_id, bot_id, env, name, rules_template, \
                      is_default, status, created_by, created_at, updated_at) \
                     VALUES (?, ?, ?, 'default', '{}', 1, 'active', 'test', 0, 0)",
                    vec![
                        DbValue::from(profile_id),
                        DbValue::from(bot_id),
                        DbValue::from(env),
                    ],
                ),
            )
            .await
            .expect("seed profile");
    }

    fn default_grant(from: &str, to: &str, env: &str, ref_id: &str) -> EdgeGrant {
        EdgeGrant {
            edge_id: format!("eg_{}_{}_{}", from, to, ref_id),
            env: env.to_string(),
            from_id: from.to_string(),
            to_id: to.to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: ref_id.to_string(),
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        }
    }

    #[tokio::test]
    async fn get_default_profile_id_roundtrip() {
        let store = sqlite_store().await;
        seed_default(&store, "bot_a", "dev", "pp_a").await;
        assert_eq!(
            store.get_default_profile_id("bot_a", "dev").await,
            Some("pp_a".to_string())
        );
        assert_eq!(store.get_default_profile_id("human_x", "dev").await, None);
    }

    #[tokio::test]
    async fn insert_and_list_active_grants() {
        let store = sqlite_store().await;
        let g = default_grant("a", "b", "dev", "pp_b");
        store.insert_grant(g.clone()).await.expect("insert");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].from_id, "a");
        assert_eq!(listed[0].grant_kind, GrantKind::PermissionProfile);
    }

    #[tokio::test]
    async fn insert_idempotent_on_unique_key() {
        let store = sqlite_store().await;
        let mut g = default_grant("a", "b", "dev", "pp_b");
        store.insert_grant(g.clone()).await.expect("insert 1");
        // Re-insert with same (from,to,env,ref) but different edge_id: DO NOTHING.
        g.edge_id = "different_id".to_string();
        store.insert_grant(g).await.expect("insert 2");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert_eq!(listed.len(), 1);
        // The original edge_id survives (DO NOTHING kept the first row).
        assert_eq!(listed[0].edge_id, "eg_a_b_pp_b");
    }

    #[tokio::test]
    async fn revoke_removes_from_active() {
        let store = sqlite_store().await;
        let g = default_grant("a", "b", "dev", "pp_b");
        store.insert_grant(g.clone()).await.expect("insert");
        store.revoke_grant(&g.edge_id, "dev").await.expect("revoke");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert!(listed.is_empty());
    }

    #[tokio::test]
    async fn has_friend_edge_any_direction() {
        let store = sqlite_store().await;
        seed_default(&store, "bot_b", "dev", "pp_b").await;
        // a (human) → b : friend edge (ref = b's default).
        let g = default_grant("human_a", "bot_b", "dev", "pp_b");
        store.insert_grant(g).await.expect("insert");
        assert!(store.has_friend_edge("human_a", "bot_b", "dev").await);
        assert!(store.has_friend_edge("bot_b", "human_a", "dev").await);
    }

    #[tokio::test]
    async fn list_friends_outbound_human_actor() {
        let store = sqlite_store().await;
        seed_default(&store, "bot_b", "dev", "pp_b").await;
        seed_default(&store, "bot_c", "dev", "pp_c").await;
        store
            .insert_grant(default_grant("human_a", "bot_b", "dev", "pp_b"))
            .await
            .expect("insert b");
        store
            .insert_grant(default_grant("human_a", "bot_c", "dev", "pp_c"))
            .await
            .expect("insert c");
        // non-friend (wrong ref) should not be listed
        store
            .insert_grant(default_grant("human_a", "bot_c", "dev", "pp_b"))
            .await
            .expect("insert wrong ref (idempotent vs pp_c? different ref)");

        let mut friends = store.list_friends("human_a", "dev").await;
        friends.sort();
        assert_eq!(friends, vec!["bot_b".to_string(), "bot_c".to_string()]);
    }

    // ---- DbPermissionProfileStore (T8) ----

    /// Profile store backed by a fresh LocalSqliteDbPlugin with the full
    /// 14-column `permission_profiles` schema (mirrors 006_edge_permission.sql).
    async fn profile_store() -> DbPermissionProfileStore {
        let db = LocalSqliteDbPlugin::new().expect("local sqlite");
        db.execute(DbStatement::new(
            "CREATE TABLE permission_profiles (\
                permission_profile_id VARCHAR(128) NOT NULL, \
                bot_id VARCHAR(128) NOT NULL, \
                env VARCHAR(32) NOT NULL, \
                name VARCHAR(128) NOT NULL DEFAULT 'default', \
                description VARCHAR(512), \
                rules_template TEXT NOT NULL, \
                revision INTEGER NOT NULL DEFAULT 1, \
                digest VARCHAR(128) NOT NULL, \
                is_default INTEGER NOT NULL DEFAULT 0, \
                status VARCHAR(16) NOT NULL DEFAULT 'active', \
                created_by VARCHAR(128) NOT NULL, \
                updated_by VARCHAR(128), \
                created_at INTEGER NOT NULL, \
                updated_at INTEGER NOT NULL, \
                PRIMARY KEY (permission_profile_id))",
        ))
        .await
        .expect("create permission_profiles");
        DbPermissionProfileStore::sqlite(Arc::new(db))
    }

    #[tokio::test]
    async fn ensure_default_profile_idempotent() {
        let store = profile_store().await;
        // First call seeds the default profile.
        store
            .ensure_default_profile("bot_x", "dev")
            .await
            .expect("seed 1");
        // Second call must be a no-op (D12 rule 2): no overwrite, no revision bump.
        store
            .ensure_default_profile("bot_x", "dev")
            .await
            .expect("seed 2");

        let profile = store
            .get_active_default("bot_x", "dev")
            .await
            .expect("default exists");
        // Deterministic profile_id.
        assert_eq!(profile.permission_profile_id, "pp_bot_x_default");
        assert!(profile.is_default);
        assert_eq!(profile.status, ProfileStatus::Active);
        assert_eq!(profile.created_by, "system");
        // revision stays 1 (idempotent, not bumped).
        assert_eq!(profile.revision, 1);
        // rules_template is the wildcard-allow seed.
        assert_eq!(
            profile.rules_template,
            serde_json::from_str::<serde_json::Value>(WILDCARD_ALLOW).unwrap()
        );
        assert_eq!(
            profile.digest,
            sha256_hex(WILDCARD_ALLOW),
            "digest must match sha256(rules_template)"
        );
    }

    #[tokio::test]
    async fn upsert_revision_keeps_profile_id_bumps_revision() {
        let store = profile_store().await;
        store
            .ensure_default_profile("bot_y", "prod")
            .await
            .expect("seed");
        let seeded = store
            .get_active_default("bot_y", "prod")
            .await
            .expect("seeded default");
        assert_eq!(seeded.revision, 1);
        let seeded_profile_id = seeded.permission_profile_id.clone();

        // Bump to revision 2 with new rules + new digest. profile_id unchanged.
        let new_rules =
            serde_json::from_str::<serde_json::Value>(
                r#"[{"tool":"read","specifier":"*","effect":"allow"}]"#,
            )
            .unwrap();
        let new_digest = sha256_hex(&new_rules.to_string());
        let updated = PermissionProfile {
            permission_profile_id: seeded_profile_id.clone(),
            bot_id: seeded.bot_id.clone(),
            env: seeded.env.clone(),
            name: seeded.name.clone(),
            description: seeded.description.clone(),
            rules_template: new_rules.clone(),
            revision: 2,
            digest: new_digest.clone(),
            is_default: seeded.is_default,
            status: seeded.status,
            created_by: seeded.created_by.clone(),
            updated_by: Some("admin".to_string()),
            created_at: seeded.created_at,
            updated_at: now_millis(),
        };
        store
            .upsert_revision(updated)
            .await
            .expect("upsert revision");

        let after = store
            .get_active_default("bot_y", "prod")
            .await
            .expect("updated default");
        // D12 rule 2: profile_id is unchanged.
        assert_eq!(after.permission_profile_id, seeded_profile_id);
        // revision + rules_template + digest bumped.
        assert_eq!(after.revision, 2);
        assert_eq!(after.rules_template, new_rules);
        assert_eq!(after.digest, new_digest);
        assert_eq!(after.updated_by.as_deref(), Some("admin"));
        // is_default/status/bot_id/env/name/created_by/created_at untouched.
        assert!(after.is_default);
        assert_eq!(after.status, ProfileStatus::Active);
        assert_eq!(after.bot_id, "bot_y");
        assert_eq!(after.env, "prod");
        assert_eq!(after.created_by, "system");
        assert_eq!(after.created_at, seeded.created_at);
    }

    // ---- DbPermissionRequestStore (T9) ----

    /// Request store backed by a fresh LocalSqliteDbPlugin with the full
    /// 16-column `permission_requests` schema (mirrors 006_edge_permission.sql).
    async fn request_store() -> DbPermissionRequestStore {
        let db = LocalSqliteDbPlugin::new().expect("local sqlite");
        db.execute(DbStatement::new(
            "CREATE TABLE permission_requests (\
                request_id VARCHAR(128) NOT NULL, \
                edge_id VARCHAR(128), \
                env VARCHAR(32) NOT NULL, \
                from_id VARCHAR(128) NOT NULL, \
                to_id VARCHAR(128) NOT NULL, \
                request_kind VARCHAR(32) NOT NULL, \
                requested_ref_id VARCHAR(128), \
                requested_rules TEXT, \
                message TEXT, \
                status VARCHAR(16) NOT NULL DEFAULT 'pending', \
                decision_reason TEXT, \
                created_by VARCHAR(128) NOT NULL, \
                decided_by VARCHAR(128), \
                created_at INTEGER NOT NULL, \
                updated_at INTEGER NOT NULL, \
                decided_at INTEGER, \
                PRIMARY KEY (request_id))",
        ))
        .await
        .expect("create permission_requests");
        DbPermissionRequestStore::sqlite(Arc::new(db))
    }

    fn sample_request(id: &str, env: &str) -> PermissionRequest {
        PermissionRequest {
            request_id: id.to_string(),
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
            created_at: 1,
            updated_at: 1,
            decided_at: None,
        }
    }

    #[tokio::test]
    async fn request_insert_and_get() {
        let store = request_store().await;
        store
            .insert(sample_request("req_1", "dev"))
            .await
            .expect("insert");
        let got = store.get("req_1", "dev").await.expect("found");
        assert_eq!(got.status, RequestStatus::Pending);
        assert!(got.edge_id.is_none(), "pending → no edge_id");
        assert_eq!(got.request_kind, RequestKind::Connect);
        assert!(store.get("req_x", "dev").await.is_none(), "missing → None");
    }

    #[tokio::test]
    async fn request_list_inbox_all_and_status_filter() {
        let store = request_store().await;
        store
            .insert(sample_request("r1", "dev"))
            .await
            .expect("insert r1");
        store
            .insert(sample_request("r2", "dev"))
            .await
            .expect("insert r2");
        // decide r2 → approved
        store
            .decide("r2", "dev", RequestStatus::Approved, "85020", Some("ok"), 99)
            .await
            .expect("decide");
        let all = store.list_inbox("bot_b", "dev", None).await;
        assert_eq!(all.len(), 2, "both visible without filter");
        let pending = store
            .list_inbox("bot_b", "dev", Some(RequestStatus::Pending))
            .await;
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].request_id, "r1");
        let approved = store
            .list_inbox("bot_b", "dev", Some(RequestStatus::Approved))
            .await;
        assert_eq!(approved.len(), 1);
        assert_eq!(approved[0].request_id, "r2");
        assert_eq!(approved[0].decided_by.as_deref(), Some("85020"));
        assert_eq!(approved[0].decided_at, Some(99));
    }

    #[tokio::test]
    async fn request_backfill_edge_id() {
        let store = request_store().await;
        store
            .insert(sample_request("r1", "dev"))
            .await
            .expect("insert");
        store
            .backfill_edge_id("r1", "dev", "eg_1")
            .await
            .expect("backfill");
        let got = store.get("r1", "dev").await.expect("found");
        assert_eq!(got.edge_id.as_deref(), Some("eg_1"));
    }
}