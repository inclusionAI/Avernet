//! SQL Bot membership writes and gateway compatibility projection share a transaction.
use super::*;
use bcs_db_api::{DbTransactionParam, DbTransactionStep};
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;

pub(super) fn validate_record(record: &BotProviderRecord) -> ServiceResult<()> {
    if record.bot_uuid.trim().is_empty() || record.provider_id.trim().is_empty()
        || record.provider_bot_ref.trim().is_empty() || record.is_deleted
        || (record.connection_mode == BotConnectionMode::Upstream && record.webhook_url.is_some())
    {
        return Err(ServiceError::InvalidOperation { message: "invalid Bot Provider metadata".into(), request_id: None });
    }
    Ok(())
}

pub(super) fn storage_error(error: DbError) -> ServiceError {
    if error.is_duplicate_key() || matches!(error, DbError::ConditionFailed { .. }) {
        ServiceError::Conflict("Bot or Provider/ref is already registered or changed".into())
    } else {
        // Driver SQL can include the newly generated runtime credential.
        ServiceError::InternalError("Bot Provider persistence failed".into())
    }
}

fn decode(row: &DbRow) -> ServiceResult<BotProviderRecord> {
    let read = || -> Result<BotProviderRecord, DbError> {
        let mode: String = bcs_db_api::db_get_column(row, "connection_mode")?;
        let mode = match mode.as_str() {
            "upstream" => BotConnectionMode::Upstream,
            "gateway" => BotConnectionMode::Gateway,
            _ => return Err(DbError::Conversion("invalid Bot connection mode".into())),
        };
        Ok(BotProviderRecord {
            bot_uuid: bcs_db_api::db_get_column(row, "bot_uuid")?,
            provider_id: bcs_db_api::db_get_column(row, "provider_id")?,
            provider_bot_ref: bcs_db_api::db_get_column(row, "provider_bot_ref")?,
            connection_mode: mode, webhook_url: row.get_string("webhook_url")?,
            is_deleted: bcs_db_api::db_get_column(row, "is_deleted")?,
            registered_at: bcs_db_api::db_get_column(row, "provider_registered_at")?,
            updated_at: bcs_db_api::db_get_column(row, "provider_updated_at")?,
        })
    };
    read().map_err(|_| ServiceError::InternalError("invalid or unmigrated Bot Provider metadata".into()))
}

impl DbProviderStore {
    fn lock_gateway_binding(&self, record: &BotProviderRecord, env: &str) -> DbTransactionStep {
        let lock = if self.flavor == DbSqlFlavor::Mysql { " FOR UPDATE" } else { "" };
        DbTransactionStep::Query(DbStatement::with_params(format!(
            "SELECT bot_uuid FROM bcs_provider_bot_bindings WHERE bot_uuid = ? AND env = ? \
             AND provider_id = ? AND provider_bot_ref = ? AND disabled = 0{lock}"),
            vec![record.bot_uuid.as_str().into(), env.into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into()]))
    }

    async fn require_provider_bot(&self, provider_id: &str, bot_uuid: &str) -> ServiceResult<BotProviderRecord> {
        let record = self.get_provider_bot(bot_uuid).await?
            .ok_or_else(|| ServiceError::BotNotFound(bot_uuid.into()))?;
        if record.provider_id != provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".into()));
        }
        Ok(record)
    }
}

#[async_trait]
impl BotProviderRepoPort for DbProviderStore {
    async fn get_provider_bot_by_ref(&self, provider_id: &str, provider_bot_ref: &str) -> ServiceResult<Option<BotProviderRecord>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT * FROM bcs_bots WHERE env = ? AND provider_id = ? AND provider_bot_ref = ?",
            vec![resolve_env().into(), provider_id.into(), provider_bot_ref.into()])).await.map_err(storage_error)?;
        rows.first().map(decode).transpose()
    }

    async fn list_provider_bot_metadata(&self, provider_id: Option<&str>) -> ServiceResult<Vec<BotProviderRecord>> {
        let mut sql = "SELECT * FROM bcs_bots WHERE env = ? AND provider_id IS NOT NULL".to_string();
        let mut params = vec![resolve_env().into()];
        if let Some(id) = provider_id { sql.push_str(" AND provider_id = ?"); params.push(id.into()); }
        sql.push_str(" ORDER BY bot_uuid");
        self.db.query(DbStatement::with_params(sql, params)).await.map_err(storage_error)?
            .iter().map(decode).collect()
    }

    async fn get_connection_mode(&self, bot_uuid: &str) -> ServiceResult<Option<BotConnectionMode>> {
        let rows = self.db.query(DbStatement::with_params("SELECT connection_mode FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![bot_uuid.into(), resolve_env().into()])).await.map_err(storage_error)?;
        match rows.first().map(|row| row.get_string("connection_mode")).transpose().map_err(storage_error)?.flatten().as_deref() {
            None if rows.is_empty() => Ok(None),
            None => Err(ServiceError::InternalError("Bot connection mode has not been migrated".into())),
            Some("upstream") => Ok(Some(BotConnectionMode::Upstream)),
            Some("gateway") => Ok(Some(BotConnectionMode::Gateway)),
            Some(_) => Err(ServiceError::InternalError("invalid Bot connection mode".into())),
        }
    }

    async fn attach_provider_bot(&self, record: BotProviderRecord) -> ServiceResult<()> {
        validate_record(&record)?;
        let mut next = record.clone();
        let previous = self.get_provider_bot(&record.bot_uuid).await?;
        let revision = previous.as_ref().map(|record| record.updated_at);
        if let Some(previous) = previous {
            if previous.is_deleted || previous.provider_id != record.provider_id || previous.provider_bot_ref != record.provider_bot_ref
                || (previous.connection_mode == BotConnectionMode::Gateway && (record.connection_mode != BotConnectionMode::Gateway || previous.webhook_url != record.webhook_url))
            { return Err(ServiceError::Conflict("Bot Provider membership cannot be replaced".into())); }
            next.registered_at = previous.registered_at;
            next.updated_at = next.updated_at.max(previous.updated_at.saturating_add(1));
        }
        let binding = self.get_binding_by_bot_uuid(&record.bot_uuid).await?;
        if let Some(binding) = &binding {
            if record.connection_mode != BotConnectionMode::Gateway || binding.disabled
                || binding.provider_id != record.provider_id || binding.provider_bot_ref != record.provider_bot_ref
                || binding.webhook_url != record.webhook_url
            { return Err(ServiceError::Conflict("gateway projection differs from Bot membership".into())); }
        }
        let env = resolve_env();
        let mode = match next.connection_mode { BotConnectionMode::Upstream => "upstream", BotConnectionMode::Gateway => "gateway" };
        let mut steps = vec![DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE bcs_bots SET provider_id = ?, provider_bot_ref = ?, connection_mode = ?, webhook_url = ?, provider_registered_at = ?, provider_updated_at = ?, updated_at = CURRENT_TIMESTAMP \
                 WHERE bot_uuid = ? AND env = ? AND is_deleted = 0 \
                 AND (provider_id IS NULL OR (provider_id = ? AND provider_bot_ref = ? AND (connection_mode = 'upstream' OR connection_mode = ?))) \
                 AND ((? IS NULL AND provider_updated_at IS NULL) OR provider_updated_at = ?) \
                 AND NOT EXISTS (SELECT 1 FROM bcs_provider_bot_bindings WHERE env = ? AND provider_id = ? AND provider_bot_ref = ? AND bot_uuid <> ?)",
                vec![next.provider_id.as_str().into(), next.provider_bot_ref.as_str().into(), mode.into(), next.webhook_url.clone().into(), next.registered_at.into(), next.updated_at.into(),
                    next.bot_uuid.as_str().into(), env.as_str().into(), next.provider_id.as_str().into(), next.provider_bot_ref.as_str().into(), mode.into(),
                    revision.map(DbValue::from).unwrap_or(DbValue::Null), revision.map(DbValue::from).unwrap_or(DbValue::Null),
                    env.as_str().into(), next.provider_id.as_str().into(), next.provider_bot_ref.as_str().into(), next.bot_uuid.as_str().into()]), expected_affected_rows: 1,
        }];
        if next.connection_mode == BotConnectionMode::Gateway && binding.is_none() {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(Self::insert_binding_sql(),
                vec![next.bot_uuid.as_str().into(), next.provider_id.as_str().into(), next.provider_bot_ref.as_str().into(), env.as_str().into(), false.into(), next.webhook_url.into()])));
        }
        self.db.transaction(steps).await.map_err(storage_error)?;
        self.bindings.invalidate(&format!("{env}:{}", record.bot_uuid));
        Ok(())
    }

    async fn get_provider_bot(&self, bot_uuid: &str) -> ServiceResult<Option<BotProviderRecord>> {
        let rows = self.db.query(DbStatement::with_params(
            "SELECT bot_uuid, provider_id, provider_bot_ref, connection_mode, webhook_url, \
             is_deleted, provider_registered_at, provider_updated_at FROM bcs_bots \
             WHERE bot_uuid = ? AND env = ? AND provider_id IS NOT NULL",
            vec![bot_uuid.into(), resolve_env().into()])).await.map_err(storage_error)?;
        rows.first().map(decode).transpose()
    }

    async fn create_provider_bot(&self, record: BotProviderRecord, capabilities: BotCapabilities, owner: &str, token: &str) -> ServiceResult<()> {
        validate_record(&record)?;
        if owner.trim().is_empty() || token.is_empty() {
            return Err(ServiceError::InvalidOperation { message: "Bot owner and runtime credential are required".into(), request_id: None });
        }
        let env = resolve_env();
        let info = serde_json::to_string(&capabilities)
            .map_err(|_| ServiceError::InternalError("Bot capability serialization failed".into()))?;
        let mode = match record.connection_mode { BotConnectionMode::Upstream => "upstream", BotConnectionMode::Gateway => "gateway" };
        let mut steps = vec![DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, env, name, bot_info, session_token, created_by, \
                 visibility, status, actor_kind, is_deleted, registered_at, updated_at, agent_code, \
                 provider_id, provider_bot_ref, connection_mode, webhook_url, provider_registered_at, provider_updated_at) \
                 SELECT ?, ?, ?, ?, ?, ?, ?, 'online', 'bot', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ? \
                 WHERE NOT EXISTS (SELECT 1 FROM bcs_provider_bot_bindings WHERE env = ? AND provider_id = ? AND provider_bot_ref = ?)",
                vec![record.bot_uuid.as_str().into(), env.as_str().into(), capabilities.name.as_deref().unwrap_or(&record.bot_uuid).into(),
                    info.into(), token.into(), owner.into(), capabilities.visibility.as_str().into(),
                    capabilities.agent_code.clone().into(),
                    record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(), mode.into(), record.webhook_url.clone().into(),
                    record.registered_at.into(), record.updated_at.into(), env.as_str().into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into()]),
            expected_affected_rows: 1,
        }];
        if record.connection_mode == BotConnectionMode::Gateway {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                Self::insert_binding_sql(), vec![record.bot_uuid.as_str().into(), record.provider_id.as_str().into(), record.provider_bot_ref.as_str().into(),
                    env.as_str().into(), false.into(), record.webhook_url.clone().into()])));
        }
        self.db.transaction(steps).await.map_err(storage_error)?;
        self.bindings.invalidate(&format!("{env}:{}", record.bot_uuid));
        Ok(())
    }

    async fn update_provider_webhook(&self, provider_id: &str, bot_uuid: &str, webhook_url: Option<String>, updated_at: u64) -> ServiceResult<BotProviderRecord> {
        let mut record = self.require_provider_bot(provider_id, bot_uuid).await?;
        if record.is_deleted || record.connection_mode != BotConnectionMode::Gateway {
            return Err(ServiceError::Conflict("webhook updates require an active gateway Bot".into()));
        }
        let env = resolve_env();
        let next = updated_at.max(record.updated_at.saturating_add(1));
        let steps = vec![
            self.lock_gateway_binding(&record, &env),
            DbTransactionStep::ExecuteChecked { statement: DbStatement::with_params(
                "UPDATE bcs_bots SET webhook_url = ?, provider_updated_at = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ? \
                 AND provider_id = ? AND connection_mode = 'gateway' AND is_deleted = 0 AND provider_updated_at = ?",
                vec![webhook_url.clone().into(), next.into(), bot_uuid.into(), env.as_str().into(), provider_id.into(), record.updated_at.into()]), expected_affected_rows: 1 },
            DbTransactionStep::Execute(DbStatement::with_transaction_params(
                format!("UPDATE bcs_provider_bot_bindings SET webhook_url = ?, {} WHERE bot_uuid = ? AND env = ?", self.now_modified_clause()),
                vec![DbTransactionParam::value(webhook_url.clone()), DbTransactionParam::query_result(0, 0, "bot_uuid"), DbTransactionParam::value(env.as_str())])),
        ];
        self.db.transaction(steps).await.map_err(storage_error)?;
        self.bindings.invalidate(&format!("{env}:{bot_uuid}"));
        record.webhook_url = webhook_url;
        record.updated_at = next;
        Ok(record)
    }

    async fn delete_provider_bot(&self, provider_id: &str, bot_uuid: &str, updated_at: u64) -> ServiceResult<bool> {
        let record = self.require_provider_bot(provider_id, bot_uuid).await?;
        if record.is_deleted { return Ok(false); }
        let env = resolve_env();
        let mut steps = Vec::new();
        if record.connection_mode == BotConnectionMode::Gateway {
            steps.push(self.lock_gateway_binding(&record, &env));
        }
        steps.push(DbTransactionStep::ExecuteChecked { statement: DbStatement::with_params(
            "UPDATE bcs_bots SET is_deleted = 1, provider_updated_at = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ? AND provider_id = ? AND is_deleted = 0 AND provider_updated_at = ?",
            vec![updated_at.max(record.updated_at.saturating_add(1)).into(), bot_uuid.into(), env.as_str().into(), provider_id.into(), record.updated_at.into()]), expected_affected_rows: 1 });
        if record.connection_mode == BotConnectionMode::Gateway {
            steps.push(DbTransactionStep::Execute(DbStatement::with_transaction_params(
                format!("UPDATE bcs_provider_bot_bindings SET disabled = 1, {} WHERE bot_uuid = ? AND env = ?", self.now_modified_clause()),
                vec![DbTransactionParam::query_result(0, 0, "bot_uuid"), DbTransactionParam::value(env.as_str())])));
        }
        self.db.transaction(steps).await.map_err(storage_error)?;
        self.bindings.invalidate(&format!("{env}:{bot_uuid}"));
        Ok(true)
    }
}
