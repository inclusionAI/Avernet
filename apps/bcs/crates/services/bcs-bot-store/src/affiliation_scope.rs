//! Affiliation activates Provider policy just like tightening Provider config.
use super::*;
use bcs_db_api::{DbTransactionParam, DbTransactionStep};
use bcs_service_api::{ProviderCollaborationScope, bot_provider::BotProviderRecord};

pub(super) fn public_group_conflict() -> ServiceError {
    ServiceError::Conflict("Bot belongs to public groups; change them to private before affiliating with a same_owner Provider".into())
}

impl MemoryProviderStore {
    pub(super) async fn check_affiliation_scope(
        &self,
        provider_id: &str,
        bot_uuid: &str,
    ) -> ServiceResult<()> {
        let provider = self
            .get_provider(provider_id)
            .await?
            .ok_or_else(|| ServiceError::InternalError("affiliated Provider is missing".into()))?;
        if ProviderCollaborationScope::from_config(&provider.config)?
            == ProviderCollaborationScope::Standard
        {
            return Ok(());
        }
        let source = self
            .group_scope_source
            .read()
            .map_err(|_| ServiceError::InternalError("group scope source lock failed".into()))?
            .clone();
        let (groups, _) = source.ok_or_else(|| {
            ServiceError::InternalError("public group scope lookup is not configured".into())
        })?;
        if groups
            .has_public_group_with_members(&[bot_uuid.into()])
            .await?
        {
            return Err(public_group_conflict());
        }
        Ok(())
    }
}

impl DbProviderStore {
    pub(super) async fn check_affiliation_scope(
        &self,
        provider_id: &str,
        bot_uuid: &str,
    ) -> ServiceResult<()> {
        // Authorization reads bypass the Provider cache so tightening is visible.
        let env = resolve_env();
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT config FROM bcs_providers WHERE provider_id = ? AND env = ?",
                vec![provider_id.into(), env.as_str().into()],
            ))
            .await
            .map_err(bot_storage::storage_error)?;
        let config = rows
            .first()
            .ok_or_else(|| ServiceError::InternalError("affiliated Provider is missing".into()))?
            .get_string("config")
            .map_err(bot_storage::storage_error)?
            .ok_or_else(|| ServiceError::InternalError("Provider config is missing".into()))?;
        if ProviderCollaborationScope::from_config(&config)? == ProviderCollaborationScope::Standard
        {
            return Ok(());
        }
        let groups = self.db.query(DbStatement::with_params(
            "SELECT 1 FROM bcs_group_participants gp JOIN bcs_groups g ON g.group_id = gp.group_id AND g.env = gp.env WHERE gp.env = ? AND gp.bot_uuid = ? AND g.visibility = 'public' AND g.record_status <> 'deleted' LIMIT 1",
            vec![env.as_str().into(), bot_uuid.into()],
        )).await.map_err(bot_storage::storage_error)?;
        if !groups.is_empty() {
            return Err(public_group_conflict());
        }
        Ok(())
    }

    pub(super) fn affiliation_scope_steps(
        &self,
        record: &BotProviderRecord,
        env: &str,
    ) -> Vec<DbTransactionStep> {
        let (scope, valid, provider_lock, bot_lock) = match self.flavor {
            DbSqlFlavor::Mysql => (
                "COALESCE(JSON_UNQUOTE(JSON_EXTRACT(config, '$.collaboration_scope')), 'standard')",
                "JSON_TYPE(config) = 'OBJECT'",
                " LOCK IN SHARE MODE",
                " FOR UPDATE",
            ),
            DbSqlFlavor::Sqlite => (
                "COALESCE(json_extract(config, '$.collaboration_scope'), 'standard')",
                "json_type(config) = 'object' AND COALESCE(json_type(config, '$.collaboration_scope'), 'text') = 'text'",
                "",
                "",
            ),
        };
        let exact_scope = if self.flavor == DbSqlFlavor::Mysql { format!("BINARY {scope}") } else { scope.into() };
        vec![
            // Provider -> Bot -> Group ordering matches public Group writes.
            DbTransactionStep::Query(DbStatement::with_params(
                format!(
                    "SELECT provider_id, {scope} AS collaboration_scope FROM bcs_providers WHERE provider_id = ? AND env = ? AND {valid} AND {exact_scope} IN ('standard', 'same_owner'){provider_lock}"
                ),
                vec![record.provider_id.as_str().into(), env.into()],
            )),
            DbTransactionStep::Query(DbStatement::with_transaction_params(
                format!(
                    "SELECT bot_uuid FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND is_deleted = 0 AND ? IS NOT NULL{bot_lock}"
                ),
                vec![
                    DbTransactionParam::value(record.bot_uuid.as_str()),
                    DbTransactionParam::value(env),
                    DbTransactionParam::query_result(0, 0, "provider_id"),
                ],
            )),
            // Missing assertion rows prevent the subsequent UPDATE from binding
            // its Provider parameter; no membership or gateway projection writes.
            DbTransactionStep::Query(DbStatement::with_transaction_params(
                "SELECT ? AS provider_id WHERE ? = 'standard' OR NOT EXISTS (SELECT 1 FROM bcs_group_participants gp JOIN bcs_groups g ON g.group_id = gp.group_id AND g.env = gp.env WHERE gp.env = ? AND gp.bot_uuid = ? AND g.visibility = 'public' AND g.record_status <> 'deleted')",
                vec![
                    DbTransactionParam::query_result(0, 0, "provider_id"),
                    DbTransactionParam::query_result(0, 0, "collaboration_scope"),
                    DbTransactionParam::value(env),
                    DbTransactionParam::query_result(1, 0, "bot_uuid"),
                ],
            )),
        ]
    }
}
