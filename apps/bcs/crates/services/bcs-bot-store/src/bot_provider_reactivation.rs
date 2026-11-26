//! SQL Provider tombstone reactivation with fresh credentials and authority.

use super::*;
use bcs_db_api::DbTransactionStep;
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;

impl DbProviderStore {
    pub(crate) async fn restore_provider_bot_composite(
        &self,
        record: BotProviderRecord,
        capabilities: BotCapabilities,
        owner: &str,
        token: &str,
        initialization: &bcs_service_api::types::bot_authority::OwnershipInitialization,
    ) -> ServiceResult<()> {
        bot_storage::validate_record(&record)?;
        if owner.trim().is_empty() || token.is_empty() || owner.trim() != initialization.owner_user_id {
            return Err(ServiceError::InvalidOperation {
                message: "Bot owner and runtime credential are required".into(),
                request_id: None,
            });
        }
        let previous = self
            .get_provider_bot_by_ref(&record.provider_id, &record.provider_bot_ref)
            .await?;
        if let Some(previous) = previous {
            if !previous.is_deleted || previous.bot_uuid != record.bot_uuid {
                return Err(ServiceError::Conflict(
                    "Provider Bot is not a restorable tombstone".into(),
                ));
            }
        } else {
            // Legacy single-owner Gateway registrations have no Provider
            // metadata row. Their disabled binding identifies the tombstone;
            // the checked Bot update below still requires is_deleted = 1.
            self.get_binding_by_provider_ref(&record.provider_id, &record.provider_bot_ref)
                .await?
                .filter(|binding| binding.disabled && binding.bot_uuid == record.bot_uuid)
                .ok_or_else(|| ServiceError::Conflict(
                    "Provider Bot is not a restorable tombstone".into(),
                ))?;
        }
        if let Some(binding) = self.get_binding_by_bot_uuid(&record.bot_uuid).await? {
            if binding.provider_id != record.provider_id
                || binding.provider_bot_ref != record.provider_bot_ref
            {
                return Err(ServiceError::Conflict(
                    "gateway projection differs from Bot membership".into(),
                ));
            }
        }

        let env = resolve_env();
        crate::a2a::ensure_provider_name_available(
            self.db.as_ref(),
            self.flavor,
            &env,
            &record.provider_id,
            capabilities.name.as_deref().unwrap_or(&record.bot_uuid),
            Some(&record.bot_uuid),
        )
        .await?;
        let info = serde_json::to_string(&capabilities)
            .map_err(|_| ServiceError::InternalError("Bot capability serialization failed".into()))?;
        let mode = record.connection_mode.as_str();
        let mut steps = vec![DbTransactionStep::Execute(DbStatement::with_params(
            "DELETE FROM bcs_provider_bot_bindings \
             WHERE bot_uuid = ? AND env = ? AND provider_id = ? AND provider_bot_ref = ?",
            vec![
                record.bot_uuid.as_str().into(),
                env.as_str().into(),
                record.provider_id.as_str().into(),
                record.provider_bot_ref.as_str().into(),
            ],
        ))];
        // Keep the retirement lock order: binding first, then the physical Bot.
        if record.connection_mode == BotConnectionMode::Gateway {
            steps.push(DbTransactionStep::Execute(DbStatement::with_params(
                Self::insert_binding_sql(),
                vec![
                    record.bot_uuid.as_str().into(),
                    record.provider_id.as_str().into(),
                    record.provider_bot_ref.as_str().into(),
                    env.as_str().into(),
                    false.into(),
                    record.webhook_url.clone().into(),
                ],
            )));
        }
        steps.push(DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_params(
                "UPDATE bcs_bots SET name = ?, bot_info = ?, session_token = ?, created_by = ?, \
                 visibility = ?, agent_code = ?, provider_id = ?, provider_bot_ref = ?, \
                 connection_mode = ?, webhook_url = ?, is_deleted = 0, status = 'online', \
                 ownership_version = 1, updated_at = CURRENT_TIMESTAMP \
                 WHERE bot_uuid = ? AND env = ? AND is_deleted = 1",
                vec![
                    capabilities.name.as_deref().unwrap_or(&record.bot_uuid).into(),
                    info.into(),
                    token.into(),
                    owner.into(),
                    capabilities.visibility.as_str().into(),
                    capabilities.agent_code.clone().into(),
                    record.provider_id.as_str().into(),
                    record.provider_bot_ref.as_str().into(),
                    mode.into(),
                    record.webhook_url.clone().into(),
                    record.bot_uuid.as_str().into(),
                    env.as_str().into(),
                ],
            ),
            expected_affected_rows: 1,
        });
        // Retired edges cannot be revived with INSERT IGNORE. Start the new
        // owner/manager snapshot from a clean slate, retaining runtime edges.
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "DELETE FROM edge_grants WHERE env = ? AND to_id = ? \
             AND grant_kind IN ('owner', 'manager') AND grant_ref_id = 0 AND rules IS NULL",
            vec![env.as_str().into(), record.bot_uuid.as_str().into()],
        )));
        steps.push(DbTransactionStep::Execute(DbStatement::with_params(
            "DELETE FROM bot_ownership_transfers WHERE env = ? AND bot_id = ?",
            vec![env.as_str().into(), record.bot_uuid.as_str().into()],
        )));

        let human_session_token = uuid::Uuid::new_v4().to_string();
        let ownership_steps = crate::ownership::provider_ownership_authorization_steps(
            self.flavor,
            &env,
            &record.bot_uuid,
            &record.provider_id,
            initialization,
            &human_session_token,
            steps.len(),
        )?;
        steps.extend(ownership_steps.steps);
        steps.push(ownership_steps.final_guard);

        match self
            .db
            .transaction_with_options(steps, crate::ownership::transaction_options())
            .await
        {
            Ok(_) => {}
            Err(DbError::Timeout) => {
                return Err(ServiceError::InternalError(format!(
                    "{}: the Provider reactivation transaction exceeded the deadline; \
                     per contract it rolled back and the caller must not auto-retry",
                    bcs_service_api::application::v1::bot_authority::ERROR_BOT_TX_OUTCOME_UNCERTAIN
                )));
            }
            Err(error) => return Err(bot_storage::storage_error(error)),
        }
        self.bindings.invalidate(&format!("{env}:{}", record.bot_uuid));
        Ok(())
    }
}
