//! Bounded authoritative parent and token identity reads; no runtime tokens escape.
use bcs_service_api::types::agent_registration::{AgentBotQuery, AgentBotRecord};
use bcs_service_api::types::AgentBotRegistration;
use super::{PersistentBotRepo, DbSqlFlavor, DbRow, Value, ServiceError, ServiceResult, db_get_column, db_get_column_opt, resolve_env};

impl PersistentBotRepo {
    fn agent_identity_select(&self) -> String {
        let legacy = match self.flavor {
            DbSqlFlavor::Mysql => "JSON_UNQUOTE(JSON_EXTRACT(b.bot_info, '$.agent_code'))",
            DbSqlFlavor::Sqlite => "json_extract(b.bot_info, '$.agent_code')",
        };
        format!("SELECT b.bot_uuid, b.name, b.bot_info, b.created_by, b.status, \
            COALESCE(b.agent_code, {legacy}) AS matched_agent_code, \
            COALESCE(b.provider_id, binding.provider_id) AS provider_id, \
            COALESCE(b.provider_bot_ref, binding.provider_bot_ref) AS provider_bot_ref, \
            p.config AS provider_config, p.disabled AS provider_disabled, \
            binding.disabled AS binding_disabled, binding.provider_id AS binding_provider_id, binding.provider_bot_ref AS binding_provider_bot_ref \
            FROM bcs_bots b LEFT JOIN bcs_provider_bot_bindings binding \
            ON binding.bot_uuid=b.bot_uuid AND binding.env=b.env \
            LEFT JOIN bcs_providers p ON p.provider_id=COALESCE(b.provider_id,binding.provider_id) AND p.env=b.env \
            WHERE b.env=? AND COALESCE(b.is_deleted,0)=0 AND COALESCE(b.actor_kind,'bot')='bot'")
    }

    pub(super) async fn load_agent_bots(&self, query: &AgentBotQuery) -> ServiceResult<Vec<AgentBotRecord>> {
        if (query.agent_code.is_empty() && query.bot_uuid.is_none()) || !(1..=101).contains(&query.limit) {
            return Err(failure("invalid Agent Bot query"));
        }
        let legacy = match self.flavor {
            DbSqlFlavor::Mysql => "JSON_UNQUOTE(JSON_EXTRACT(b.bot_info, '$.agent_code'))",
            DbSqlFlavor::Sqlite => "json_extract(b.bot_info, '$.agent_code')",
        };
        let mut sql = self.agent_identity_select();
        let mut params = vec![Value::from(resolve_env())];
        if !query.agent_code.is_empty() {
            sql.push_str(&format!(" AND (b.agent_code=? OR (b.agent_code IS NULL AND {legacy}=?))"));
            params.extend([Value::from(query.agent_code.clone()), Value::from(query.agent_code.clone())]);
        }
        if let Some(id) = &query.bot_uuid { sql.push_str(" AND b.bot_uuid=?"); params.push(Value::from(id.clone())); }
        if let Some(id) = &query.provider_id { sql.push_str(" AND COALESCE(b.provider_id,binding.provider_id)=?"); params.push(Value::from(id.clone())); }
        if let Some(cursor) = &query.cursor { sql.push_str(" AND b.bot_uuid>?"); params.push(Value::from(cursor.clone())); }
        if query.implicit_only {
            let mode = match self.flavor {
                DbSqlFlavor::Mysql => "JSON_UNQUOTE(JSON_EXTRACT(p.config, '$.downlink.auth_mode'))",
                DbSqlFlavor::Sqlite => "json_extract(p.config, '$.downlink.auth_mode')",
            };
            sql.push_str(&format!(" AND (COALESCE(b.provider_id,binding.provider_id) IS NULL OR {mode}='agentpass')"));
        }
        sql.push_str(&format!(" ORDER BY b.bot_uuid LIMIT {}", query.limit));
        let rows = self.db_query(&sql, params).await.map_err(|_| failure("Agent Bot read failed"))?;
        rows.iter().map(|row| {
            let bot = record(row)?;
            if (!query.agent_code.is_empty() && bot.agent_code != query.agent_code) || query.bot_uuid.as_ref().is_some_and(|id| id != &bot.bot.bot_id) {
                return Err(failure("Agent Bot identity mismatch"));
            }
            Ok(bot)
        }).collect()
    }

    pub(super) async fn load_token_bot(&self, token: &str, actor: Option<&str>) -> ServiceResult<Option<AgentBotRecord>> {
        let mut sql = self.agent_identity_select();
        // Retain the token index while requiring an exact credential comparison.
        sql.push_str(match self.flavor {
            DbSqlFlavor::Mysql => " AND b.session_token=? AND BINARY b.session_token=BINARY ?",
            DbSqlFlavor::Sqlite => " AND b.session_token=? COLLATE BINARY",
        });
        let mut params = vec![Value::from(resolve_env()), Value::from(token)];
        if self.flavor == DbSqlFlavor::Mysql { params.push(Value::from(token)); }
        if let Some(actor) = actor { sql.push_str(" AND b.bot_uuid=?"); params.push(Value::from(actor)); }
        sql.push_str(" LIMIT 1");
        let rows = self.db_query(&sql, params).await.map_err(|_| failure("Bot credential read failed"))?;
        rows.first().map(record).transpose().and_then(|bot| {
            if actor.is_some_and(|id| bot.as_ref().is_some_and(|bot| bot.bot.bot_id != id)) {
                return Err(failure("Bot credential identity mismatch"));
            }
            Ok(bot)
        })
    }
}

fn record(row: &DbRow) -> ServiceResult<AgentBotRecord> {
    let provider: Option<String> = read_opt(row, "provider_id")?;
    let reference: Option<String> = read_opt(row, "provider_bot_ref")?;
    let binding_provider: Option<String> = read_opt(row, "binding_provider_id")?;
    let binding_ref: Option<String> = read_opt(row, "binding_provider_bot_ref")?;
    if binding_provider.as_ref().is_some_and(|id| Some(id) != provider.as_ref())
        || binding_ref.as_ref().is_some_and(|id| Some(id) != reference.as_ref()) {
        return Err(failure("Bot Provider binding mismatch"));
    }
    let config: Option<String> = read_opt(row, "provider_config")?;
    let mode = config.map(|value| -> ServiceResult<_> {
        let config: serde_json::Value = serde_json::from_str(&value).map_err(|_| failure("invalid Provider configuration"))?;
        serde_json::from_value(config.pointer("/downlink/auth_mode").cloned().unwrap_or_default())
            .map_err(|_| failure("invalid Provider auth mode"))
    }).transpose()?;
    let info: Option<String> = read_opt(row, "bot_info")?;
    let info = info.map(|value| serde_json::from_str::<serde_json::Value>(&value)).transpose().map_err(|_| failure("invalid Bot metadata"))?;
    Ok(AgentBotRecord {
        bot: AgentBotRegistration {
            bot_id: db_get_column(row, "bot_uuid").map_err(|_| failure("invalid Bot id"))?,
            name: read_opt(row, "name")?,
            summary: info.and_then(|info| info.get("summary").and_then(|v| v.as_str()).map(str::to_string)),
            provider_id: read_opt(row, "provider_id")?,
            provider_bot_ref: read_opt(row, "provider_bot_ref")?,
        },
        agent_code: read_opt(row, "matched_agent_code")?.unwrap_or_default(),
        owner_id: read_opt(row, "created_by")?,
        provider_auth_mode: mode,
        provider_disabled: read_bool(row, "provider_disabled")?,
        binding_disabled: read_bool(row, "binding_disabled")?,
        status: read_opt(row, "status")?.unwrap_or_else(|| "online".into()),
        provider_metadata_loaded: true,
    })
}

fn read_opt(row: &DbRow, key: &str) -> ServiceResult<Option<String>> {
    db_get_column_opt(row, key).map_err(|_| failure("invalid Bot identity metadata"))
}
fn read_bool(row: &DbRow, key: &str) -> ServiceResult<bool> {
    db_get_column_opt::<i64>(row, key).map(|value| value.unwrap_or(0) != 0).map_err(|_| failure("invalid identity state"))
}
fn failure(message: &str) -> ServiceError { ServiceError::InternalError(message.into()) }
