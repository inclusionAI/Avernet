//! Bot decision configuration reads.
use super::*;

/// DB-backed `BotActorConfigRepoPort` implementation (T12).
///
/// Narrow read of `bcs_bots` decision columns for connect/admission. Same
/// plumbing as [`DbPermissionProfileStore`]: `Arc<dyn DbPlugin>` + flavor.
/// Reads across MySQL (TINYINT(1)) and SQLite (INTEGER) via `get_bool`, which
/// coerces integer 0/1 to bool.
pub struct DbBotActorConfigStore {
    pub(super) db: Arc<dyn DbPlugin>,
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

    #[cfg(test)]
    pub(super) async fn execute(&self, operation: &'static str, statement: DbStatement) -> ServiceResult<()> {
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
        "SELECT bot_uuid, env, name, visibility, user_visibility, friend_check_in_strategy, friend_ext, bot_info, status, created_by \
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
