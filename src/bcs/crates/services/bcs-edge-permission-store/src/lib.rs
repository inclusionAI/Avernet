//! Database-backed implementation of the `edge_grants` repository port.
//!
//! Owns the SQL for `edge_grants` + `permission_profiles` (default-profile
//! cache) and depends only on the driver-level `bcs-db-api` contract. The
//! composition root decides which concrete DB plugin backs it. Mirrors the
//! `bcs-relation-store` plumbing (MySQL + SQLite via `DbSqlFlavor`).
//!
//! Implements [`EdgeGrantRepoPort`] (installment 1) for [`DbEdgeGrantStore`].
//! `PermissionProfileRepoPort` / `PermissionRequestRepoPort` (T8/T9) will be
//! added to this same crate later.

use std::collections::HashSet;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_db_api::{DbError, DbExecuteResult, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::edge_permission::{
    EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType,
};
pub use bcs_service_api::port::repo::EdgeGrantRepoPort;
use bcs_service_api::{ServiceError, ServiceResult};
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
}