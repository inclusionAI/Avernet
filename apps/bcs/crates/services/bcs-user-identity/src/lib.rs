//! Database-backed `UserIdentityRepoPort` implementation.
//!
//! Owns the `bcs_user_identities` SQL and depends only on `bcs-db-api`. The
//! composition root chooses the concrete DB plugin.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement, DbValue};
use bcs_service_api::{UserIdentity, UserIdentityRepoPort};
use tracing::warn;

pub mod memory;
pub use memory::{generate_user_id, MemoryUserIdentityRepo};

pub type MysqlUserIdentityRepo = DbUserIdentityStore;
pub type SqliteUserIdentityRepo = DbUserIdentityStore;

pub struct DbUserIdentityStore {
    db: Arc<dyn DbPlugin>,
    flavor: DbSqlFlavor,
}

impl DbUserIdentityStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor) -> Self {
        Self { db, flavor }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, DbSqlFlavor::Mysql)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, DbSqlFlavor::Sqlite)
    }

    /// Reserved for future MySQL/SQLite UPSERT dialect branching.
    pub fn flavor(&self) -> DbSqlFlavor {
        self.flavor
    }

    async fn select_user_id(
        &self,
        auth_source: &str,
        external_user_id: &str,
        env: &str,
    ) -> Result<Option<String>, String> {
        let sql = "SELECT user_id FROM bcs_user_identities \
                   WHERE auth_source = ? AND external_user_id = ? AND env = ?";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(auth_source),
                    DbValue::from(external_user_id),
                    DbValue::from(env),
                ],
            ))
            .await
            .map_err(|e| format!("select user_id: {e}"))?;
        match rows.first() {
            Some(row) => row
                .get_string("user_id")
                .map_err(|e| format!("read user_id: {e}")),
            None => Ok(None),
        }
    }
}

#[async_trait]
impl UserIdentityRepoPort for DbUserIdentityStore {
    async fn ensure_identity(
        &self,
        auth_source: &str,
        external_user_id: &str,
        external_user_name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, String> {
        // Hit -> update external_user_name and avatar, return existing user_id.
        if let Some(existing) = self
            .select_user_id(auth_source, external_user_id, env)
            .await?
        {
            let update = "UPDATE bcs_user_identities SET external_user_name = ?, avatar = ? \
                          WHERE auth_source = ? AND external_user_id = ? AND env = ?";
            self.db
                .execute(DbStatement::with_params(
                    update,
                    vec![
                        DbValue::from(external_user_name),
                        DbValue::from(avatar),
                        DbValue::from(auth_source),
                        DbValue::from(external_user_id),
                        DbValue::from(env),
                    ],
                ))
                .await
                .map_err(|e| format!("update identity: {e}"))?;
            return Ok(existing);
        }

        // Miss -> insert a freshly generated user_id, retry on uk_user_id clash.
        // The internal `user_name` is initialized from the external display name
        // on first creation, then left untouched on subsequent logins (the
        // UPDATE branch above only refreshes external_user_name/avatar). This
        // lets the internal display name diverge from the provider's later.
        let insert = "INSERT INTO bcs_user_identities \
                      (user_id, auth_source, external_user_id, user_name, external_user_name, avatar, env) \
                      VALUES (?, ?, ?, ?, ?, ?, ?)";
        let mut attempts = 0;
        loop {
            let user_id = generate_user_id();
            let result = self
                .db
                .execute(DbStatement::with_params(
                    insert,
                    vec![
                        DbValue::from(user_id.as_str()),
                        DbValue::from(auth_source),
                        DbValue::from(external_user_id),
                        DbValue::from(external_user_name),
                        DbValue::from(external_user_name),
                        DbValue::from(avatar),
                        DbValue::from(env),
                    ],
                ))
                .await;
            match result {
                Ok(_) => return Ok(user_id),
                Err(e) => {
                    attempts += 1;
                    // A concurrent inserter may have created the external row, OR
                    // the random user_id collided. Re-resolve, then retry.
                    if let Some(existing) = self
                        .select_user_id(auth_source, external_user_id, env)
                        .await?
                    {
                        return Ok(existing);
                    }
                    if attempts >= 5 {
                        warn!(error = %e, "ensure_identity insert retry exhausted");
                        return Err(format!("insert identity: {e}"));
                    }
                }
            }
        }
    }

    async fn lookup_user_id(
        &self,
        auth_source: &str,
        external_user_id: &str,
        env: &str,
    ) -> Option<String> {
        self.select_user_id(auth_source, external_user_id, env)
            .await
            .ok()
            .flatten()
    }

    async fn lookup_by_user_id(
        &self,
        user_id: &str,
        auth_source: &str,
    ) -> Option<String> {
        let sql = "SELECT external_user_id FROM bcs_user_identities \
                   WHERE user_id = ? AND auth_source = ? LIMIT 1";
        self.db
            .query(DbStatement::with_params(
                sql,
                vec![DbValue::from(user_id), DbValue::from(auth_source)],
            ))
            .await
            .ok()
            .and_then(|rows| {
                rows.first()
                    .and_then(|r| r.get_string("external_user_id").ok().flatten())
            })
    }

    async fn get_by_token(&self, token: &str) -> Option<UserIdentity> {
        let sql = "SELECT user_id, auth_source, user_name, external_user_name, avatar, env \
                   FROM bcs_user_identities WHERE token = ? LIMIT 1";
        self.db
            .query(DbStatement::with_params(sql, vec![DbValue::from(token)]))
            .await
            .ok()
            .and_then(|rows| rows.first().map(|r| row_to_display_identity(r)))
    }

    async fn get_by_user_id_display(&self, user_id: &str) -> Option<UserIdentity> {
        let sql = "SELECT user_id, auth_source, user_name, external_user_name, avatar, env \
                   FROM bcs_user_identities WHERE user_id = ? LIMIT 1";
        self.db
            .query(DbStatement::with_params(sql, vec![DbValue::from(user_id)]))
            .await
            .ok()
            .and_then(|rows| rows.first().map(|r| row_to_display_identity(r)))
    }

    async fn update_token(
        &self,
        user_id: &str,
        token: &str,
        expire_at: u64,
    ) -> Result<(), String> {
        let sql = match self.flavor {
            DbSqlFlavor::Mysql => {
                "UPDATE bcs_user_identities SET token = ?, token_expire_at = FROM_UNIXTIME(?) WHERE user_id = ?"
            }
            DbSqlFlavor::Sqlite => {
                "UPDATE bcs_user_identities SET token = ?, token_expire_at = datetime(?, 'unixepoch') WHERE user_id = ?"
            }
        };
        self.db
            .execute(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(token),
                    DbValue::from(expire_at as i64),
                    DbValue::from(user_id),
                ],
            ))
            .await
            .map_err(|e| format!("update_token: {e}"))?;
        Ok(())
    }
}

fn row_to_display_identity(row: &bcs_db_api::DbRow) -> UserIdentity {
    UserIdentity {
        user_id: row.get_string("user_id").ok().flatten().unwrap_or_default(),
        auth_source: row.get_string("auth_source").ok().flatten().unwrap_or_default(),
        external_user_id: String::new(),
        user_name: row.get_string("user_name").ok().flatten(),
        external_user_name: row.get_string("external_user_name").ok().flatten(),
        avatar: row.get_string("avatar").ok().flatten(),
        token: None,
        token_expire_at: None,
        env: row.get_string("env").ok().flatten().unwrap_or_default(),
    }
}
