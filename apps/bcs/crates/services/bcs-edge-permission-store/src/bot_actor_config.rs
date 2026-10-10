//! DB-backed `BotActorConfigRepoPort` implementation (T12), split out of
//! the former over-limit `lib.rs` (plan Task 3 lib split).
//!
//! Narrow read of `bcs_bots` decision columns for connect/admission. Same
//! plumbing as [`crate::DbPermissionProfileStore`]: `Arc<dyn DbPlugin>` +
//! flavor. Reads across MySQL (TINYINT(1)) and SQLite (INTEGER) via
//! `get_bool`, which coerces integer 0/1 to bool.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbRow, DbStatement, DbValue};
use bcs_domain::edge_permission::BotActorConfig;
pub use bcs_service_api::port::repo::BotActorConfigRepoPort;
use bcs_service_api::ServiceResult;
use tracing::warn;

use crate::common::{optional_string, required_string, service_db_error};
use crate::EdgeGrantSqlFlavor;

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

    #[cfg(test)]
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

/// Map a `bcs_bots` row to a [`BotActorConfig`].
///
/// `created_by` is `NULL` for legacy bots. `user_visibility` is read from its
/// own `bcs_bots.user_visibility` column — the same column the internal-
/// attributes PATCH writes — so friend-gating sees the value the operator
/// actually set.
/// `friend_check_in_strategy` is read from its dedicated column so the
/// friend-connect policy does not depend on `bot_info` JSON projection order.
/// `friend_ext` is read from the dedicated `bcs_bots.friend_ext` column, with
/// a legacy `bot_info.friend_ext` fallback for older rows.
fn row_to_bot_actor_config(row: &DbRow) -> ServiceResult<BotActorConfig> {
    let bot_info = optional_string(row, "bot_info")?
        .and_then(|value| serde_json::from_str::<serde_json::Value>(&value).ok())
        .unwrap_or_default();
    let user_visibility = optional_string(row, "user_visibility")?
        .unwrap_or_else(|| "protected".to_string());
    let friend_check_in_strategy = optional_string(row, "friend_check_in_strategy")?
        .unwrap_or_else(|| "APPROVAL".to_string());
    let friend_ext = optional_string(row, "friend_ext")?
        .and_then(|value| serde_json::from_str::<serde_json::Value>(&value).ok())
        .and_then(|value| value.as_object().cloned())
        .or_else(|| {
            bot_info
                .get("friend_ext")
                .and_then(|value| value.as_object().cloned())
        })
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

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use bcs_db_local::LocalSqliteDbPlugin;

    use super::*;

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
                friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL', \
                bot_info TEXT DEFAULT NULL, \
                friend_ext TEXT DEFAULT NULL, \
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
        let friend_ext_json = serde_json::to_string(&friend_ext).expect("friend_ext json");
        let bot_info = serde_json::json!({
            "friend_check_in_strategy": friend_check_in_strategy,
            "friend_ext": friend_ext,
        });
        store
            .execute(
                "seed_bot",
                DbStatement::with_params(
                    "INSERT INTO bcs_bots \
                     (bot_uuid, env, name, visibility, user_visibility, friend_check_in_strategy, bot_info, friend_ext, status, created_by) \
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    vec![
                        DbValue::from(bot_uuid),
                        DbValue::from(env),
                        DbValue::from(bot_uuid),
                        DbValue::from(visibility),
                        DbValue::from(user_visibility),
                        DbValue::from(friend_check_in_strategy),
                        DbValue::from(serde_json::to_string(&bot_info).expect("bot_info json")),
                        DbValue::from(friend_ext_json),
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
    async fn bot_actor_config_reads_friend_strategy_from_column() {
        let store = bot_config_store().await;
        store
            .execute(
                "seed_bot_column_strategy",
                DbStatement::with_params(
                    "INSERT INTO bcs_bots \
                     (bot_uuid, env, name, visibility, user_visibility, friend_check_in_strategy, bot_info, friend_ext, status, created_by) \
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    vec![
                        DbValue::from("column-strategy-bot"),
                        DbValue::from("dev"),
                        DbValue::from("column-strategy-bot"),
                        DbValue::from("protected"),
                        DbValue::from("public"),
                        DbValue::from("OPEN"),
                        DbValue::from(r#"{"friend_ext": {"scope": "bot_info"}}"#),
                        DbValue::from(r#"{"scope": "column"}"#),
                        DbValue::from("online"),
                        DbValue::Null,
                    ],
                ),
            )
            .await
            .expect("seed bot with column strategy");

        let cfg = store
            .get("column-strategy-bot", "dev")
            .await
            .expect("bot exists");
        assert_eq!(cfg.friend_check_in_strategy, "OPEN");
        assert_eq!(cfg.friend_ext["scope"], "column");
    }

    #[test]
    fn bot_actor_config_accepts_utf8_bytes_for_json_text_columns() {
        let mut row = std::collections::BTreeMap::new();
        row.insert("bot_uuid".to_string(), DbValue::String("bytes-json-bot".to_string()));
        row.insert("env".to_string(), DbValue::String("prod".to_string()));
        row.insert("name".to_string(), DbValue::String("bytes-json-bot".to_string()));
        row.insert("visibility".to_string(), DbValue::String("protected".to_string()));
        row.insert("user_visibility".to_string(), DbValue::String("public".to_string()));
        row.insert("friend_check_in_strategy".to_string(), DbValue::String("OPEN".to_string()));
        row.insert(
            "bot_info".to_string(),
            DbValue::Bytes(br#"{"friend_ext":{"scope":"bot_info"}}"#.to_vec()),
        );
        row.insert(
            "friend_ext".to_string(),
            DbValue::Bytes(br#"{"scope":"column"}"#.to_vec()),
        );
        row.insert("status".to_string(), DbValue::String("online".to_string()));
        row.insert("created_by".to_string(), DbValue::String("owner-1".to_string()));

        let cfg = row_to_bot_actor_config(&DbRow::new(row)).expect("bytes JSON columns parse");

        assert_eq!(cfg.bot_id, "bytes-json-bot");
        assert_eq!(cfg.env, "prod");
        assert_eq!(cfg.friend_ext["scope"], "column");
        assert_eq!(cfg.created_by.as_deref(), Some("owner-1"));
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
        // friend_check_in_strategy comes from its dedicated column, and
        // friend_ext now comes from its dedicated column with bot_info fallback.
        let cfg = store.get("bot_b", "prod").await.expect("bot exists in prod");
        assert_eq!(cfg.user_visibility, "private");
        assert_eq!(cfg.friend_check_in_strategy, "DEPT_FREE");
        assert!(cfg.friend_ext.is_empty());
        assert_eq!(cfg.status, "hidden");
        assert!(cfg.created_by.is_none(), "legacy bot has no created_by");
    }
}