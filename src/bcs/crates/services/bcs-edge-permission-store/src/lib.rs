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

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbError, DbExecuteResult, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::edge_permission::{
    BotActorConfig, EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType, PermissionProfile,
    ProfileStatus, PermissionRequest, RequestKind, RequestStatus,
};
pub use bcs_service_api::port::repo::EdgeGrantRepoPort;
pub use bcs_service_api::port::repo::PermissionProfileRepoPort;
pub use bcs_service_api::port::repo::PermissionRequestRepoPort;
pub use bcs_service_api::port::repo::BotActorConfigRepoPort;
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
                 (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
                  status, originator_policy_type, originator_policy_data) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            }
            EdgeGrantSqlFlavor::Sqlite => {
                "INSERT INTO edge_grants \
                 (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
                  status, originator_policy_type, originator_policy_data) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) \
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
                    "SELECT id, env, from_id, to_id, grant_kind, grant_ref_id, \
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

    async fn is_authorized(&self, from: &str, to: &str, env: &str) -> bool {
        // Any active approved edge from→to admits (friend OR non-friend).
        // `list_active_grants` already filters `status='approved'`, so a
        // non-empty result ⇒ authorized. Keep the repo-level call (not a raw
        // SELECT 1) so this stays a pure projection over the same SoR.
        !self.list_active_grants(from, to, env).await.is_empty()
    }

    async fn has_friend_edge(&self, x: &str, y: &str, env: &str) -> bool {
        // D12: any-direction default-profile edge. x→y uses y's default (dy);
        // y→x uses x's default (dx). Either direction being a friend edge
        // counts (None default ⇒ that side is human ⇒ that direction cannot
        // be a friend edge).
        let dy = self.get_default_profile_id(y, env).await;
        let dx = self.get_default_profile_id(x, env).await;

        if let Some(dy) = dy {
            if self.has_default_edge(x, y, env, Some(dy)).await {
                return true;
            }
        }
        if let Some(dx) = dx {
            if self.has_default_edge(y, x, env, Some(dx)).await {
                return true;
            }
        }
        false
    }

    async fn list_friends(&self, actor: &str, env: &str) -> Vec<String> {
        // §4.6 two-branch index scan + (bot_id,env)→default cache +
        // memory compare. Avoids a SQL join.
        let d_actor = self.get_default_profile_id(actor, env).await;

        let mut cache: HashMap<String, Option<u64>> = HashMap::new();
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
                let grant_ref_id = match required_u64(&row, "grant_ref_id") {
                    Ok(v) => v,
                    Err(err) => {
                        warn!(error = %err, "list_friends_outbound: missing grant_ref_id");
                        continue;
                    }
                };
                let d_y = match cache.get(&to_id) {
                    Some(v) => *v,
                    None => {
                        let v = self.get_default_profile_id(&to_id, env).await;
                        cache.insert(to_id.clone(), v);
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
        if let Some(d_actor) = d_actor {
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
                    let grant_ref_id = match required_u64(&row, "grant_ref_id") {
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

    async fn insert_grant(&self, grant: EdgeGrant) -> ServiceResult<u64> {
        let EdgeGrant {
            edge_id: _,
            env,
            from_id,
            to_id,
            grant_kind,
            grant_ref_id,
            rules,
            status,
            originator_policy_type,
            originator_policy_data,
        } = grant;
        let rules_val = json_to_db_value(&rules);
        let policy_data_val = json_to_db_value(&originator_policy_data);
        let result = self
            .execute_result(
                "insert_grant",
                DbStatement::with_params(
                    self.insert_grant_sql(),
                    vec![
                        DbValue::from(env.clone()),
                        DbValue::from(from_id.clone()),
                        DbValue::from(to_id.clone()),
                        DbValue::from(grant_kind_str(grant_kind)),
                        DbValue::from(grant_ref_id),
                        rules_val,
                        DbValue::from(edge_status_str(status)),
                        DbValue::from(originator_policy_type_str(originator_policy_type)),
                        policy_data_val,
                    ],
                ),
            )
            .await?;
        if let Some(id) = result.last_insert_id {
            if id != 0 {
                return Ok(id);
            }
        }
        self.query(
            "insert_grant_lookup",
            DbStatement::with_params(
                "SELECT id FROM edge_grants WHERE from_id = ? AND to_id = ? AND env = ? AND grant_ref_id = ? LIMIT 1",
                vec![
                    DbValue::from(from_id),
                    DbValue::from(to_id),
                    DbValue::from(env),
                    DbValue::from(grant_ref_id),
                ],
            ),
        )
        .await?
        .into_iter()
        .next()
        .and_then(|row| row.get_i64("id").ok().flatten())
        .and_then(|id| if id < 0 { None } else { Some(id as u64) })
        .ok_or_else(|| ServiceError::InternalError("edge_grants insert did not return an id".to_string()))
    }

    async fn revoke_grant(&self, edge_id: u64, env: &str) -> ServiceResult<()> {
        self.execute(
            "revoke_grant",
            DbStatement::with_params(
                "UPDATE edge_grants SET status = 'revoked', \
                     gmt_modified = CURRENT_TIMESTAMP \
                 WHERE id = ? AND env = ?",
                vec![DbValue::from(edge_id), DbValue::from(env)],
            ),
        )
        .await
    }

    async fn get_default_profile_id(&self, bot_id: &str, env: &str) -> Option<u64> {
        let rows = self
            .query(
                "get_default_profile_id",
                DbStatement::with_params(
                    "SELECT id FROM permission_profiles \
                     WHERE bot_id = ? AND env = ? AND is_default = 1 \
                       AND status = 'active' LIMIT 1",
                    vec![DbValue::from(bot_id), DbValue::from(env)],
                ),
            )
            .await;
        match rows {
            Ok(rows) => rows.into_iter().next().and_then(|row| {
                row.get_i64("id").ok().flatten().and_then(|value| if value < 0 { None } else { Some(value as u64) })
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
        default_ref: Option<u64>,
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

    async fn execute_result(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<DbExecuteResult> {
        self.db.execute(statement).await.map_err(|err| {
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

    /// Idempotent INSERT of the default profile on PK `id`.
    /// SQLite `INSERT OR IGNORE` vs MySQL `INSERT IGNORE` — D12 rule 2:
    /// never overwrite or bump an existing default.
    fn insert_default_profile_sql(&self) -> &'static str {
        match self.flavor {
            EdgeGrantSqlFlavor::Mysql => {
                "INSERT IGNORE INTO permission_profiles \
                 (bot_id, env, name, description, rules_template, \
                  revision, digest, is_default, status, created_by, updated_by) \
                 VALUES (?, ?, ?, NULL, ?, ?, ?, 1, 'active', 'system', NULL)"
            }
            EdgeGrantSqlFlavor::Sqlite => {
                "INSERT OR IGNORE INTO permission_profiles \
                 (bot_id, env, name, description, rules_template, \
                  revision, digest, is_default, status, created_by, updated_by) \
                 VALUES (?, ?, ?, NULL, ?, ?, ?, 1, 'active', 'system', NULL)"
            }
        }
    }

    /// SELECT all columns of a default, active profile for (bot_id, env).
    const SELECT_DEFAULT_SQL: &'static str =
        "SELECT id, bot_id, env, name, description, rules_template, \
                revision, digest, is_default, status, created_by, updated_by \
         FROM permission_profiles \
         WHERE bot_id = ? AND env = ? AND is_default = 1 AND status = 'active' LIMIT 1";
}

#[async_trait]
impl PermissionProfileRepoPort for DbPermissionProfileStore {
    async fn ensure_default_profile(&self, bot_id: &str, env: &str) -> ServiceResult<u64> {
        let digest = sha256_hex(WILDCARD_ALLOW);
        // INSERT all cols (description/updated_by NULL; gmt_* default to now).
        // Idempotent on unique active default: a pre-existing default is left untouched — its
        // rules_template, revision, and digest are NOT overwritten (D12 rule 2).
        let result = self
            .execute_result(
                "ensure_default_profile",
                DbStatement::with_params(
                    self.insert_default_profile_sql(),
                    vec![
                        DbValue::from(bot_id),
                        DbValue::from(env),
                        DbValue::from("default"),
                        DbValue::from(WILDCARD_ALLOW),
                        DbValue::from(1_i64), // revision
                        DbValue::from(digest),
                    ],
                ),
            )
            .await?;
        if let Some(id) = result.last_insert_id {
            if id != 0 {
                return Ok(id);
            }
        }
        self.get_active_default(bot_id, env)
            .await
            .map(|profile| profile.permission_profile_id)
            .ok_or_else(|| ServiceError::InternalError(format!("default profile for bot '{}' missing after ensure", bot_id)))
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
        // digest / updated_by move (gmt_modified auto-advances);
        // is_default/status/bot_id/env/name/created_by are left as-is.
        let rules_val = json_to_db_value(&Some(profile.rules_template));
        self.execute(
            "upsert_revision",
            DbStatement::with_params(
                "UPDATE permission_profiles SET rules_template = ?, revision = ?, \
                     digest = ?, updated_by = ?, gmt_modified = CURRENT_TIMESTAMP \
                 WHERE id = ?",
                vec![
                    rules_val,
                    DbValue::from(profile.revision as i64),
                    DbValue::from(profile.digest),
                    DbValue::from(profile.updated_by),
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

/// DB-backed `BotActorConfigRepoPort` implementation (T12).
///
/// Narrow read of `bcs_bots` decision columns for connect/admission. Same
/// plumbing as [`DbPermissionProfileStore`]: `Arc<dyn DbPlugin>` + flavor.
/// Reads across MySQL (TINYINT(1)) and SQLite (INTEGER) via `get_bool`, which
/// coerces integer 0/1 to bool.
pub struct DbBotActorConfigStore {
    db: Arc<dyn DbPlugin>,
    flavor: EdgeGrantSqlFlavor,
}

impl DbBotActorConfigStore {
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

    async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db.query(statement).await.map_err(|err| {
            warn!(operation, error = %err, "db_bot_actor_config: query failed");
            service_db_error(operation, err)
        })
    }

    async fn execute(&self, operation: &'static str, statement: DbStatement) -> ServiceResult<()> {
        self.db
            .execute(statement)
            .await
            .map(|_| ())
            .map_err(|err| {
                warn!(operation, error = %err, "db_bot_actor_config: execute failed");
                service_db_error(operation, err)
            })
    }

    /// SELECT the decision columns for `(bot_uuid, env)`. Excludes soft-deleted
    /// rows, mirroring the bot store read (`COALESCE(is_deleted, 0) = 0`).
    const SELECT_BOT_CONFIG_SQL: &'static str =
        "SELECT bot_uuid, env, name, visibility, user_visibility, bot_info, status, created_by \
         FROM bcs_bots \
         WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0 LIMIT 1";
}

#[async_trait]
impl BotActorConfigRepoPort for DbBotActorConfigStore {
    async fn get(&self, bot_id: &str, env: &str) -> Option<BotActorConfig> {
        let rows = self
            .query(
                "get_bot_actor_config",
                DbStatement::with_params(
                    Self::SELECT_BOT_CONFIG_SQL,
                    vec![DbValue::from(bot_id), DbValue::from(env)],
                ),
            )
            .await;
        match rows {
            Ok(rows) => rows.into_iter().next().and_then(|row| {
                match row_to_bot_actor_config(&row) {
                    Ok(config) => Some(config),
                    Err(err) => {
                        warn!(error = %err, "db_bot_actor_config: get row skipped");
                        None
                    }
                }
            }),
            Err(err) => {
                warn!(error = %err, "db_bot_actor_config: get failed");
                None
            }
        }
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
    // rules_template is NOT NULL in DDL, so it is always present; parse string→Value.
    let rules_template = match required_string(row, "rules_template")? {
        ref s if s.is_empty() => serde_json::Value::Null,
        ref s => serde_json::from_str::<serde_json::Value>(s).map_err(|err| {
            ServiceError::InternalError(format!("permission_profiles json parse: {}", err))
        })?,
    };
    Ok(PermissionProfile {
        permission_profile_id: required_u64(row, "id")?,
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
    })
}

fn row_to_edge_grant(row: &DbRow) -> ServiceResult<EdgeGrant> {
    Ok(EdgeGrant {
        edge_id: required_u64(row, "id")?,
        env: required_string(row, "env")?,
        from_id: required_string(row, "from_id")?,
        to_id: required_string(row, "to_id")?,
        grant_kind: parse_grant_kind(&required_string(row, "grant_kind")?)?,
        grant_ref_id: required_u64(row, "grant_ref_id")?,
        rules: parse_json_opt(&optional_string(row, "rules")?)?,
        status: parse_edge_status(&required_string(row, "status")?)?,
        originator_policy_type: parse_originator_policy_type(
            &required_string(row, "originator_policy_type")?,
        )?,
        originator_policy_data: parse_json_opt(&optional_string(row, "originator_policy_data")?)?,
    })
}

/// Map a `bcs_bots` row to a [`BotActorConfig`].
///
/// `created_by` is `NULL` for legacy bots. `user_visibility` is read from its
/// own `bcs_bots.user_visibility` column — the same column the internal-
/// attributes PATCH writes — so friend-gating sees the value the operator
/// actually set (rather than a `bot_info` default that no write path populates).
/// `bot_info` still carries `friend_check_in_strategy` / `friend_ext`.
fn row_to_bot_actor_config(row: &DbRow) -> ServiceResult<BotActorConfig> {
    let bot_info = optional_string(row, "bot_info")?
        .and_then(|value| serde_json::from_str::<serde_json::Value>(&value).ok())
        .unwrap_or_default();
    let user_visibility = optional_string(row, "user_visibility")?
        .unwrap_or_else(|| "protected".to_string());
    let friend_check_in_strategy = bot_info
        .get("friend_check_in_strategy")
        .and_then(|value| value.as_str())
        .unwrap_or("APPROVAL")
        .to_string();
    let friend_ext = bot_info
        .get("friend_ext")
        .and_then(|value| value.as_object())
        .cloned()
        .unwrap_or_default();
    Ok(BotActorConfig {
        bot_id: required_string(row, "bot_uuid")?,
        env: required_string(row, "env")?,
        name: required_string(row, "name")?,
        visibility: required_string(row, "visibility")?,
        status: required_string(row, "status")?,
        created_by: optional_string(row, "created_by")?,
        user_visibility,
        friend_check_in_strategy,
        friend_ext,
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

fn optional_timestamp_text(row: &DbRow, column: &'static str) -> ServiceResult<Option<String>> {
    match row.get(column) {
        None | Some(DbValue::Null) => Ok(None),
        Some(DbValue::String(value)) => Ok(Some(value.clone())),
        Some(DbValue::Bytes(value)) => String::from_utf8(value.clone())
            .map(Some)
            .map_err(|err| service_db_error(column, DbError::Conversion(format!("column '{}' is not valid UTF-8: {}", column, err)))),
        Some(other) => Err(service_db_error(column, DbError::Conversion(format!("column '{}' is not a timestamp string: {:?}", column, other)))),
    }
}

fn required_u64(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    let value = row
        .get_i64(column)
        .map_err(|err| service_db_error(column, err))?
        .ok_or_else(|| ServiceError::InternalError(format!("missing edge_permission column {}", column)))?;
    if value < 0 {
        return Err(ServiceError::InternalError(format!("negative edge_permission column {}", column)));
    }
    Ok(value as u64)
}

fn optional_u64(row: &DbRow, column: &'static str) -> ServiceResult<Option<u64>> {
    Ok(row
        .get_i64(column)
        .map_err(|err| service_db_error(column, err))?
        .and_then(|value| if value < 0 { None } else { Some(value as u64) }))
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

/// Parse a DB-managed timestamp back to epoch milliseconds (UTC).
///
/// Accepts `YYYY-MM-DD HH:MM:SS` (SQLite `CURRENT_TIMESTAMP` / MySQL `timestamp`)
/// and the ISO `T` separator variant, with optional fractional seconds. Returns
/// `None` for an unparseable/empty value (e.g. NULL ⇒ already `None` upstream).
fn parse_timestamp_epoch_ms(value: &str) -> Option<u64> {
    let s = value.trim();
    if s.is_empty() {
        return None;
    }
    let s = s.replacen('T', " ", 1);
    let mut parts = s.split(' ');
    let date = parts.next()?;
    let time = parts.next()?;
    let d: Vec<&str> = date.split('-').collect();
    if d.len() != 3 {
        return None;
    }
    let t_main = time.split('.').next()?;
    let t: Vec<&str> = t_main.split(':').collect();
    if t.len() != 3 {
        return None;
    }
    let (y, mo, dy) = (d[0].parse::<i64>().ok()?, d[1].parse::<i64>().ok()?, d[2].parse::<i64>().ok()?);
    let (h, mi, se) = (t[0].parse::<u64>().ok()?, t[1].parse::<u64>().ok()?, t[2].parse::<u64>().ok()?);
    if !(1..=12).contains(&mo) || !(1..=31).contains(&dy) {
        return None;
    }
    // Howard Hinnant's days_from_civil — counts days since 1970-01-01 (UTC).
    let y = if mo <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = (y - era * 400) as u64;
    let doy = ((153 * (if mo > 2 { mo - 3 } else { mo + 9 }) + 2) / 5 + dy - 1) as u64;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = (era * 146097 + doe as i64 - 719468) as i64;
    if days < 0 {
        return None;
    }
    let epoch_ms = (days as u64) * 86_400_000 + h * 3_600_000 + mi * 60_000 + se * 1_000;
    Some(epoch_ms)
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

fn service_db_error(operation: &'static str, err: DbError) -> ServiceError {
    ServiceError::InternalError(format!("edge_grants db {}: {}", operation, err))
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_db_local::LocalSqliteDbPlugin;

    #[test]
    fn parse_timestamp_epoch_ms_converts_db_timestamp() {
        // SQLite CURRENT_TIMESTAMP / MySQL `timestamp` format, UTC.
        // 2026-08-21 00:00:00 UTC == 1787270400_000 ms.
        assert_eq!(parse_timestamp_epoch_ms("2026-08-21 00:00:00"), Some(1_787_270_400_000));
        assert_eq!(parse_timestamp_epoch_ms("2026-08-21T12:34:56"), Some(1_787_315_696_000));
        // Fractional seconds tolerated; ISO 'T' separator accepted.
        assert_eq!(parse_timestamp_epoch_ms("2026-01-01 00:00:00.000"), Some(1_767_225_600_000));
        // Empty / unparseable / pre-epoch → None (NULL upstream becomes None).
        assert_eq!(parse_timestamp_epoch_ms(""), None);
        assert_eq!(parse_timestamp_epoch_ms("not-a-date"), None);
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

    async fn sqlite_store() -> DbEdgeGrantStore {
        let db = LocalSqliteDbPlugin::new().expect("local sqlite");
        // edge_grants + permission_profiles schema (mirrors
        // migrations/mysql/014_edge_permission.sql for SQLite).
        db.execute(DbStatement::new(
            "CREATE TABLE edge_grants (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                env VARCHAR(32) NOT NULL, \
                from_id VARCHAR(128) NOT NULL, \
                to_id VARCHAR(128) NOT NULL, \
                grant_kind VARCHAR(32) NOT NULL, \
                grant_ref_id INTEGER NOT NULL, \
                rules TEXT, \
                status VARCHAR(16) NOT NULL DEFAULT 'approved', \
                originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any', \
                originator_policy_data TEXT, \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                UNIQUE (from_id, to_id, env, grant_ref_id))",
        ))
        .await
        .expect("create edge_grants");
        db.execute(DbStatement::new(
            "CREATE TABLE permission_profiles (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
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
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                UNIQUE (bot_id, env, is_default, status))",
        ))
        .await
        .expect("create permission_profiles");
        DbEdgeGrantStore::sqlite(Arc::new(db))
    }

    async fn seed_default(store: &DbEdgeGrantStore, bot_id: &str, env: &str) -> u64 {
        let profile_store = DbPermissionProfileStore::sqlite(store.db.clone());
        profile_store
            .ensure_default_profile(bot_id, env)
            .await
            .expect("seed profile")
    }

    fn default_grant(from: &str, to: &str, env: &str, ref_id: u64) -> EdgeGrant {
        EdgeGrant {
            edge_id: 0,
            env: env.to_string(),
            from_id: from.to_string(),
            to_id: to.to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: ref_id,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        }
    }

    #[tokio::test]
    async fn get_default_profile_id_roundtrip() {
        let store = sqlite_store().await;
        let profile_id = seed_default(&store, "bot_a", "dev").await;
        assert_eq!(store.get_default_profile_id("bot_a", "dev").await, Some(profile_id));
        assert_eq!(store.get_default_profile_id("human_x", "dev").await, None);
    }

    #[tokio::test]
    async fn insert_and_list_active_grants() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "b", "dev").await;
        let g = default_grant("a", "b", "dev", ref_id);
        let edge_id = store.insert_grant(g.clone()).await.expect("insert");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].edge_id, edge_id);
        assert_eq!(listed[0].from_id, "a");
        assert_eq!(listed[0].grant_kind, GrantKind::PermissionProfile);
    }

    #[tokio::test]
    async fn insert_idempotent_on_unique_key() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "b", "dev").await;
        let mut g = default_grant("a", "b", "dev", ref_id);
        let edge_id = store.insert_grant(g.clone()).await.expect("insert 1");
        // Re-insert with same (from,to,env,ref) but different edge_id: DO NOTHING.
        g.edge_id = 9999;
        let dup_id = store.insert_grant(g).await.expect("insert 2");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert_eq!(listed.len(), 1);
        // The original auto-generated edge_id survives.
        assert_eq!(listed[0].edge_id, edge_id);
        assert_eq!(dup_id, edge_id);
    }

    #[tokio::test]
    async fn revoke_removes_from_active() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "b", "dev").await;
        let g = default_grant("a", "b", "dev", ref_id);
        let edge_id = store.insert_grant(g.clone()).await.expect("insert");
        store.revoke_grant(edge_id, "dev").await.expect("revoke");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert!(listed.is_empty());
    }

    #[tokio::test]
    async fn has_friend_edge_any_direction() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "bot_b", "dev").await;
        // a (human) → b : friend edge (ref = b's default).
        let g = default_grant("human_a", "bot_b", "dev", ref_id);
        store.insert_grant(g).await.expect("insert");
        assert!(store.has_friend_edge("human_a", "bot_b", "dev").await);
        assert!(store.has_friend_edge("bot_b", "human_a", "dev").await);
    }

    #[tokio::test]
    async fn list_friends_outbound_human_actor() {
        let store = sqlite_store().await;
        let ref_b = seed_default(&store, "bot_b", "dev").await;
        let ref_c = seed_default(&store, "bot_c", "dev").await;
        store
            .insert_grant(default_grant("human_a", "bot_b", "dev", ref_b))
            .await
            .expect("insert b");
        store
            .insert_grant(default_grant("human_a", "bot_c", "dev", ref_c))
            .await
            .expect("insert c");
        // non-friend (wrong ref) should not be listed
        store
            .insert_grant(default_grant("human_a", "bot_c", "dev", ref_b))
            .await
            .expect("insert wrong ref (different ref)");

        let mut friends = store.list_friends("human_a", "dev").await;
        friends.sort();
        assert_eq!(friends, vec!["bot_b".to_string(), "bot_c".to_string()]);
    }

    // ---- DbPermissionProfileStore (T8) ----

    /// Profile store backed by a fresh LocalSqliteDbPlugin with the full
    /// `permission_profiles` schema (mirrors 014_edge_permission.sql).
    async fn profile_store() -> DbPermissionProfileStore {
        let db = LocalSqliteDbPlugin::new().expect("local sqlite");
        db.execute(DbStatement::new(
            "CREATE TABLE permission_profiles (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
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
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                UNIQUE (bot_id, env, is_default, status))",
        ))
        .await
        .expect("create permission_profiles");
        DbPermissionProfileStore::sqlite(Arc::new(db))
    }

    #[tokio::test]
    async fn ensure_default_profile_idempotent() {
        let store = profile_store().await;
        // First call seeds the default profile.
        let profile_id = store
            .ensure_default_profile("bot_x", "dev")
            .await
            .expect("seed 1");
        // Second call must be a no-op (D12 rule 2): no overwrite, no revision bump.
        let profile_id_2 = store
            .ensure_default_profile("bot_x", "dev")
            .await
            .expect("seed 2");
        assert_eq!(profile_id, profile_id_2);

        let profile = store
            .get_active_default("bot_x", "dev")
            .await
            .expect("default exists");
        assert_eq!(profile.permission_profile_id, profile_id);
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
        // is_default/status/bot_id/env/name/created_by untouched.
        assert!(after.is_default);
        assert_eq!(after.status, ProfileStatus::Active);
        assert_eq!(after.bot_id, "bot_y");
        assert_eq!(after.env, "prod");
        assert_eq!(after.created_by, "system");
    }

    // ---- DbPermissionRequestStore (T9) ----

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
        DbPermissionRequestStore::sqlite(Arc::new(db))
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

    #[tokio::test]
    async fn request_insert_and_get() {
        let store = request_store().await;
        let req = sample_request("dev");
        let request_id = req.request_id.clone();
        store.insert(req).await.expect("insert");
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
        store.insert(r1).await.expect("insert r1");
        let r2 = sample_request("dev");
        let r2_id = r2.request_id.clone();
        store.insert(r2).await.expect("insert r2");
        // decide r2 → approved
        store
            .decide(&r2_id, "dev", RequestStatus::Approved, "85020", Some("ok"))
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
        store.insert(req).await.expect("insert");
        store
            .backfill_edge_id(&request_id, "dev", 1001)
            .await
            .expect("backfill");
        let got = store.get(&request_id, "dev").await.expect("found");
        assert_eq!(got.edge_id, Some(1001));
    }

    // ---- DbBotActorConfigStore (T12) ----

    /// Bot-config store backed by a fresh LocalSqliteDbPlugin with a minimal
    /// `bcs_bots` schema (the decision cols + the soft-delete flag the read
    /// filters on). Mirrors the bot-store decision fields plus `bot_info`
    /// for the internal friend-gating attributes.
    async fn bot_config_store() -> DbBotActorConfigStore {
        let db = LocalSqliteDbPlugin::new().expect("local sqlite");
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_bots (\
                bot_uuid TEXT NOT NULL, \
                env TEXT NOT NULL, \
                name TEXT NOT NULL DEFAULT '', \
                visibility TEXT NOT NULL DEFAULT 'public', \
                user_visibility TEXT NOT NULL DEFAULT 'protected', \
                bot_info TEXT DEFAULT NULL, \
                status TEXT NOT NULL DEFAULT 'online', \
                created_by TEXT, \
                is_deleted INTEGER NOT NULL DEFAULT 0, \
                PRIMARY KEY (bot_uuid, env))",
        ))
        .await
        .expect("create bcs_bots");
        DbBotActorConfigStore::sqlite(Arc::new(db))
    }

    /// Seed a `bcs_bots` row.
    async fn seed_bot(
        store: &DbBotActorConfigStore,
        bot_uuid: &str,
        env: &str,
        visibility: &str,
        user_visibility: &str,
        friend_check_in_strategy: &str,
        status: &str,
        created_by: Option<&str>,
    ) {
        seed_bot_with_friend_ext(
            store,
            bot_uuid,
            env,
            visibility,
            user_visibility,
            friend_check_in_strategy,
            status,
            created_by,
            serde_json::Map::new(),
        )
        .await;
    }

    async fn seed_bot_with_friend_ext(
        store: &DbBotActorConfigStore,
        bot_uuid: &str,
        env: &str,
        visibility: &str,
        user_visibility: &str,
        friend_check_in_strategy: &str,
        status: &str,
        created_by: Option<&str>,
        friend_ext: serde_json::Map<String, serde_json::Value>,
    ) {
        let bot_info = serde_json::json!({
            "friend_check_in_strategy": friend_check_in_strategy,
            "friend_ext": friend_ext,
        });
        store
            .execute(
                "seed_bot",
                DbStatement::with_params(
                    "INSERT INTO bcs_bots \
                     (bot_uuid, env, name, visibility, user_visibility, bot_info, status, created_by) \
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    vec![
                        DbValue::from(bot_uuid),
                        DbValue::from(env),
                        DbValue::from(bot_uuid),
                        DbValue::from(visibility),
                        DbValue::from(user_visibility),
                        DbValue::from(serde_json::to_string(&bot_info).expect("bot_info json")),
                        DbValue::from(status),
                        match created_by {
                            Some(v) => DbValue::from(v),
                            None => DbValue::Null,
                        },
                    ],
                ),
            )
            .await
            .expect("seed bot");
    }

    #[tokio::test]
    async fn bot_actor_config_get_roundtrip() {
        let store = bot_config_store().await;
        // Public, human-addable, auto-approval bot owned by user 85020.
        seed_bot(
            &store,
            "20260421_x:85020",
            "dev",
            "public",
            "protected",
            "APPROVAL",
            "online",
            Some("85020"),
        )
        .await;
        let cfg = store
            .get("20260421_x:85020", "dev")
            .await
            .expect("bot exists");
        assert_eq!(cfg.bot_id, "20260421_x:85020");
        assert_eq!(cfg.env, "dev");
        assert_eq!(cfg.name, "20260421_x:85020");
        assert_eq!(cfg.visibility, "public");
        assert_eq!(cfg.user_visibility, "protected");
        assert_eq!(cfg.friend_check_in_strategy, "APPROVAL");
        assert!(cfg.friend_ext.is_empty());
        assert_eq!(cfg.status, "online");
        assert_eq!(cfg.created_by.as_deref(), Some("85020"));
    }

    #[tokio::test]
    async fn bot_actor_config_missing_returns_none_and_legacy_owner() {
        let store = bot_config_store().await;
        // Missing bot -> None (non-fallible).
        assert!(store.get("nope", "dev").await.is_none());
        // Different env -> None (PK is bot_uuid + env).
        seed_bot(&store, "bot_b", "prod", "protected", "private", "DEPT_FREE", "hidden", None).await;
        assert!(store.get("bot_b", "dev").await.is_none());
        // Same env row reads back; user_visibility comes from its column,
        // friend_* still come from bot_info.
        let cfg = store.get("bot_b", "prod").await.expect("bot exists in prod");
        assert_eq!(cfg.user_visibility, "private");
        assert_eq!(cfg.friend_check_in_strategy, "DEPT_FREE");
        assert!(cfg.friend_ext.is_empty());
        assert_eq!(cfg.status, "hidden");
        assert!(cfg.created_by.is_none(), "legacy bot has no created_by");
    }
}