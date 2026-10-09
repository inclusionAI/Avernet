//! Strict Bot creation, separate from the legacy registration/upsert path.

use bcs_db_api::{DbError, DbStatement, DbTransactionStep};
use bcs_service_api::bot_provider::BotConnectionMode;
use bcs_service_api::types::OwnershipInitialization;

use super::{
    BotCapabilities, BotInfo, DbSqlFlavor, PersistentBotRepo, ServiceError, ServiceResult, Value,
    db_get_column, resolve_env,
};
use super::ownership_initialization::{
    SOURCE_REGISTRATION, ownership_initialization_steps, validate_initialization,
};

impl PersistentBotRepo {
    /// Execute one already-built statement (the shared INSERT builder keeps
    /// both creation paths byte-identical without re-splitting SQL/params).
    async fn db_execute_affected_sql(
        &self,
        statement: DbStatement,
    ) -> bcs_db_api::DbResult<u64> {
        self.db
            .execute(statement)
            .await
            .map(|result| result.affected_rows)
    }

    pub(super) async fn load_registration_token(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Option<String>> {
        // Credentials must be authoritative even if an older token is cached.
        let rows = self.db_query(
            "SELECT session_token FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0",
            vec![Value::from(bot_id), Value::from(resolve_env())],
        ).await.map_err(|_| failure("Bot registration credential read failed"))?;
        match rows.as_slice() {
            [] => Ok(None),
            [row] if row.get("session_token").is_some() => {
                bcs_db_api::db_get_column_opt(row, "session_token")
                    .map_err(|_| failure("invalid Bot registration credential"))
            }
            _ => Err(failure("invalid Bot registration credential projection")),
        }
    }

    pub(super) async fn create_registration_once(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<bool> {
        let statement = registration_insert_statement(&bot_id, &capabilities, created_by, token)?;
        let result = self.db_execute_affected_sql(statement).await;
        match result {
            // Do not hydrate caches from the proposed record. A delayed INSERT
            // acknowledgement may follow a token rotation, rename or deletion.
            // Existing read/token paths obtain the current authoritative row.
            Ok(1) => Ok(true),
            Err(error) if identity_duplicate(&error, self.flavor) => {
                self.confirm_existing_identity(&bot_id).await
            }
            _ => Err(failure("Bot registration insert failed")),
        }
    }

    /// The same strict create-once contract extended with the trusted
    /// first-ownership initialization: the Bot INSERT and every authority
    /// step (CAS, owner edge, Human ensure, default profile, audit) commit
    /// in ONE transaction — or the whole registration rolls back, leaving
    /// no Bot row, no authority row and no replayable credential (plan
    /// Task 5).
    pub(super) async fn create_registration_once_with_initialization(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
        initialization: &OwnershipInitialization,
    ) -> ServiceResult<bool> {
        validate_initialization(initialization)?;
        let env = resolve_env();
        let insert = registration_insert_statement(&bot_id, &capabilities, created_by, token)?;
        let mut steps = vec![DbTransactionStep::ExecuteChecked {
            statement: insert,
            expected_affected_rows: 1,
        }];
        steps.extend(ownership_initialization_steps(
            &self.flavor,
            &env,
            &bot_id,
            initialization,
            SOURCE_REGISTRATION,
            None,
        )?);
        let result = self.db.transaction(steps).await;
        match result {
            Ok(_) => Ok(true),
            Err(error) if identity_duplicate(&error, self.flavor) => {
                self.confirm_existing_identity(&bot_id).await
            }
            Err(_) => Err(failure("Bot registration insert failed")),
        }
    }

    /// Confirm a bot creation identity conflict on the authority of the
    /// stored row, never on the driver message alone.
    async fn confirm_existing_identity(&self, bot_id: &str) -> ServiceResult<bool> {
        let env = resolve_env();
        let rows = self
            .db_query(
                "SELECT bot_uuid, env FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
                vec![Value::from(bot_id), Value::from(env.as_str())],
            )
            .await
            .map_err(|_| failure("Bot registration identity read failed"))?;
        if rows.len() != 1 {
            return Err(failure(
                "Bot registration conflict has no matching identity",
            ));
        }
        let stored_bot: String = db_get_column(&rows[0], "bot_uuid")
            .map_err(|_| failure("invalid Bot registration identity"))?;
        let stored_env: String = db_get_column(&rows[0], "env")
            .map_err(|_| failure("invalid Bot registration environment"))?;
        if stored_bot != bot_id || stored_env != env {
            return Err(failure("Bot registration identity mismatch"));
        }
        Ok(false)
    }
}

/// The strict Bot INSERT shared by both creation paths (identical SQL so
/// create-once semantics cannot drift between them).
fn registration_insert_statement(
    bot_id: &str,
    capabilities: &BotCapabilities,
    created_by: &str,
    token: &str,
) -> ServiceResult<DbStatement> {
    let env = resolve_env();
    let info = BotInfo {
        summary: capabilities.summary.clone(),
        domains: capabilities.domains.clone(),
        skills: capabilities.skills.clone(),
        scopes: capabilities.scopes.clone(),
        binding_channels: capabilities.binding_channels.clone(),
        hidden: false,
        agent_code: capabilities.agent_code.clone(),
        agent_token: capabilities.agent_token.clone(),
    };
    let info = serde_json::to_string(&info)
        .map_err(|_| failure("Bot registration serialization failed"))?;
    let visibility = if capabilities.visibility.is_empty() {
        "private"
    } else {
        capabilities.visibility.as_str()
    };
    Ok(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, bot_info, session_token, created_by, \
         visibility, status, actor_kind, agent_code, is_deleted, env, registered_at, updated_at, connection_mode) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)",
        vec![
            Value::from(bot_id),
            Value::from(capabilities.name.as_deref().unwrap_or(bot_id)),
            Value::from(info),
            Value::from(token),
            Value::from(created_by),
            Value::from(visibility),
            Value::from("online"),
            Value::from("bot"),
            Value::from(capabilities.agent_code.clone()),
            Value::from(0_i64),
            Value::from(env.as_str()),
            Value::from(BotConnectionMode::Plugin.as_str()),
        ],
    ))
}

fn identity_duplicate(error: &DbError, flavor: DbSqlFlavor) -> bool {
    let DbError::Backend(message) = error else {
        return false;
    };
    match flavor {
        DbSqlFlavor::Sqlite => {
            let message = message
                .strip_prefix("execute sqlite statement: ")
                .unwrap_or(message);
            matches!(
                message,
                "UNIQUE constraint failed: bcs_bots.bot_uuid, bcs_bots.env"
                    | "UNIQUE constraint failed: bcs_bots.env, bcs_bots.bot_uuid"
            )
        }
        DbSqlFlavor::Mysql => {
            let message = message
                .strip_prefix("mysql execute failed: ")
                .or_else(|| message.strip_prefix("mysql prepared execute failed: "))
                .unwrap_or(message);
            // mysql_async 0.34.2 opens with a backtick, closes with an apostrophe.
            let message = message.strip_prefix("Server error: `").unwrap_or(message);
            if !message.starts_with("ERROR 23000 (1062): Duplicate entry ") {
                return false;
            }
            let Some((_, key)) = message.rsplit_once(" for key '") else {
                return false;
            };
            matches!(
                key.trim_end_matches('\''),
                "uk_bot_env" | "bcs_bots.uk_bot_env"
            )
        }
    }
}

fn failure(message: &str) -> ServiceError {
    // Driver messages and invalid JSON may contain credentials. Propagate the
    // failure without exposing those values through service errors or logging.
    ServiceError::InternalError(message.into())
}
