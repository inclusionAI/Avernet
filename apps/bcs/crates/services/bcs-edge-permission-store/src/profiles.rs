//! Permission profile SQL persistence.
use super::*;

/// DB-backed `PermissionProfileRepoPort` implementation (T8).
///
/// Same plumbing as [`DbEdgeGrantStore`]: holds an `Arc<dyn DbPlugin>` + flavor.
/// Owns the SQL for `permission_profiles`. `PermissionProfileRepoPort` is
/// implemented on this type, not on `DbEdgeGrantStore`, so callers wire the
/// profile store independently at the composition root (T11).
pub struct DbPermissionProfileStore {
    pub(super) db: Arc<dyn DbPlugin>,
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
