//! Authoritative Agent registration reads, independent of runtime caches.

use bcs_service_api::types::AgentBotRegistration;
use serde::Deserialize;

use super::{DbSqlFlavor, PersistentBotRepo, ServiceError, ServiceResult, Value, db_get_column, db_get_column_opt, resolve_env};

#[derive(Deserialize)]
struct PublicBotInfo {
    summary: Option<String>,
    agent_code: Option<String>,
}

impl PersistentBotRepo {
    pub(super) async fn load_agent_registration(
        &self, agent_code: &str,
    ) -> ServiceResult<Option<AgentBotRegistration>> {
        if agent_code.trim().is_empty() {
            return Err(failure("Agent code must not be empty"));
        }
        // One read with a bounded result. The legacy branch may examine rows
        // whose dedicated identity column has not yet been backfilled. Both
        // branches participate in ambiguity detection. Legacy binding
        // metadata is a fallback for Bots not yet migrated to affiliation fields.
        // Disabled bindings/Bot status do not remove persisted registration.
        let legacy_code = match self.flavor {
            DbSqlFlavor::Mysql => "JSON_UNQUOTE(JSON_EXTRACT(b.bot_info, '$.agent_code'))",
            DbSqlFlavor::Sqlite => "json_extract(b.bot_info, '$.agent_code')",
        };
        let sql = format!(
            "SELECT b.bot_uuid, COALESCE(b.agent_code, {legacy_code}) AS matched_agent_code, \
                    b.agent_code, b.name, b.bot_info, b.provider_id, b.provider_bot_ref, \
                    p.provider_id AS legacy_provider_id, p.provider_bot_ref AS legacy_provider_bot_ref \
             FROM bcs_bots b LEFT JOIN bcs_provider_bot_bindings p \
               ON p.bot_uuid = b.bot_uuid AND p.env = b.env \
             WHERE (b.agent_code = ? OR (b.agent_code IS NULL AND {legacy_code} = ?)) \
               AND b.env = ? AND COALESCE(b.is_deleted, 0) = 0 \
               AND COALESCE(b.actor_kind, 'bot') = 'bot' LIMIT 2",
        );
        let rows = self.db_query(
            &sql, vec![Value::from(agent_code), Value::from(agent_code), Value::from(resolve_env())],
        ).await.map_err(|_| failure("Agent registration read failed"))?;
        let row = match rows.as_slice() {
            [] => return Ok(None),
            [row] => row,
            _ => return Err(ServiceError::Conflict("Agent registration is ambiguous".into())),
        };
        let stored_code: String = db_get_column(row, "matched_agent_code")
            .map_err(|_| failure("Invalid Agent registration identity"))?;
        if stored_code != agent_code {
            // Do not expose another identity through a case-insensitive DB
            // collation. The verifier's opaque identity must match exactly.
            return Err(failure("Agent registration identity mismatch"));
        }
        let bot_id: String = db_get_column(row, "bot_uuid")
            .map_err(|_| failure("Invalid Agent Bot id"))?;
        if bot_id.trim().is_empty() {
            return Err(failure("Invalid Agent Bot id"));
        }
        let name = db_get_column_opt(row, "name")
            .map_err(|_| failure("Invalid Agent Bot name"))?;
        let info: Option<String> = db_get_column_opt(row, "bot_info")
            .map_err(|_| failure("Invalid Agent Bot info"))?;
        let info = info.map(|value| serde_json::from_str::<PublicBotInfo>(&value))
            .transpose().map_err(|_| failure("Invalid Agent Bot info"))?;
        let column_code: Option<String> = db_get_column_opt(row, "agent_code")
            .map_err(|_| failure("Invalid Agent registration identity"))?;
        if column_code.is_none()
            && info.as_ref().and_then(|info| info.agent_code.as_deref()) != Some(agent_code)
        {
            // MySQL JSON_UNQUOTE may stringify numbers or JSON null. Require
            // the same original string identity accepted by the legacy loader.
            return Err(failure("Invalid legacy Agent registration identity"));
        }
        let summary = info.and_then(|info| info.summary);
        let mut provider_id: Option<String> = db_get_column_opt(row, "provider_id")
            .map_err(|_| failure("Invalid Agent Provider id"))?;
        let mut provider_bot_ref: Option<String> = db_get_column_opt(row, "provider_bot_ref")
            .map_err(|_| failure("Invalid Agent Provider reference"))?;
        if provider_id.is_none() && provider_bot_ref.is_none() {
            provider_id = db_get_column_opt(row, "legacy_provider_id")
                .map_err(|_| failure("Invalid legacy Agent Provider id"))?;
            provider_bot_ref = db_get_column_opt(row, "legacy_provider_bot_ref")
                .map_err(|_| failure("Invalid legacy Agent Provider reference"))?;
        }
        if provider_id.is_some() != provider_bot_ref.is_some()
            || provider_id.as_ref().is_some_and(|v| v.trim().is_empty())
            || provider_bot_ref.as_ref().is_some_and(|v| v.trim().is_empty())
        {
            return Err(failure("Incomplete Agent Provider affiliation"));
        }
        Ok(Some(AgentBotRegistration { bot_id, name, summary, provider_id, provider_bot_ref }))
    }
}

fn failure(message: &str) -> ServiceError {
    // Never forward driver/JSON errors that could include credentials.
    ServiceError::InternalError(message.into())
}
