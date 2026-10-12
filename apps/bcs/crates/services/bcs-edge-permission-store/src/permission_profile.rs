//! DB-backed `PermissionProfileRepoPort` implementation (T8), split out of
//! the former over-limit `lib.rs` (plan Task 3 lib split).
//!
//! Same plumbing as [`crate::DbEdgeGrantStore`]: holds an
//! `Arc<dyn DbPlugin>` + flavor and owns the SQL for `permission_profiles`.
//! `PermissionProfileRepoPort` is implemented on this type, not on
//! `DbEdgeGrantStore`, so callers wire the profile store independently at
//! the composition root.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbExecuteResult, DbPlugin, DbRow, DbStatement, DbValue};
use bcs_domain::edge_permission::{PermissionProfile, ProfileStatus};
pub use bcs_service_api::port::repo::PermissionProfileRepoPort;
use bcs_service_api::{ServiceError, ServiceResult};
use sha2::{Digest, Sha256};
use tracing::warn;

use crate::common::{
    json_to_db_value, optional_string, required_string, required_u64, service_db_error,
};
use crate::EdgeGrantSqlFlavor;

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

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_domain::edge_permission::{PermissionProfile, ProfileStatus};

    use super::*;

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
        let seeded_profile_id = seeded.permission_profile_id;

        // Bump to revision 2 with new rules + new digest. profile_id unchanged.
        let new_rules =
            serde_json::from_str::<serde_json::Value>(
                r#"[{"tool":"read","specifier":"*","effect":"allow"}]"#,
            )
            .unwrap();
        let new_digest = sha256_hex(&new_rules.to_string());
        let updated = PermissionProfile {
            permission_profile_id: seeded_profile_id,
            bot_id: seeded.bot_id.clone(),
            env: seeded.env.clone(),
            name: seeded.name.clone(),
            description: seeded.description.clone(),
            rules_template: new_rules.clone(),
            revision: 2,
            digest: new_digest.clone(),
            is_default: seeded.is_default,
            status: ProfileStatus::Active,
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
}