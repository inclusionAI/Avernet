use super::*;

#[async_trait]
impl ChannelBindingCleanupPort for BcsChannelService {
    async fn delete_bindings_for_group(&self, group_id: &str) -> ServiceResult<u64> {
        let _guard = self.binding_admin_lock.lock().await;
        let bindings = self
            .bindings
            .list_by_target(
                &BindingTarget::Group {
                    group_id: group_id.to_string(),
                },
                None,
            )
            .await?;
        let mut deleted = 0;
        for binding in bindings {
            self.delete_binding_locked(&binding).await?;
            deleted += 1;
        }
        Ok(deleted)
    }

    async fn delete_bindings_for_bot(&self, bot_id: &str) -> ServiceResult<u64> {
        let _guard = self.binding_admin_lock.lock().await;
        let bindings = self
            .bindings
            .list_by_target(
                &BindingTarget::Bot {
                    bot_id: bot_id.to_string(),
                },
                None,
            )
            .await?;
        let mut deleted = 0;
        for binding in bindings {
            self.delete_binding_locked(&binding).await?;
            deleted += 1;
        }
        Ok(deleted)
    }
}

impl BcsChannelService {
    pub(super) async fn delete_binding_locked(&self, binding: &ChannelBinding) -> ServiceResult<()> {
        self.bindings.set_status(&binding.id, false).await?;
        let mappings = self.conversations.list_by_binding(&binding.id).await?;
        let session_ids = mappings
            .iter()
            .map(|mapping| mapping.bcs_session_id.clone())
            .collect::<HashSet<_>>();

        for session_id in &session_ids {
            let has_other_binding = self
                .conversations
                .list_by_bcs_session(session_id)
                .await?
                .iter()
                .any(|mapping| mapping.binding_id != binding.id);
            if has_other_binding {
                continue;
            }
            self.collaboration_runtime
                .cancel_session_runs(session_id, "channel_binding_deleted")
                .await
                .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            if self.sessions.get(session_id).await.is_some() {
                self.sessions
                    .complete_if_running(
                        session_id,
                        None,
                        Some("channel_binding_deleted".to_string()),
                    )
                    .await?;
            }
        }

        let deleted_mappings = self.conversations.delete_by_binding(&binding.id).await?;
        self.bindings.delete(&binding.id).await?;
        info!(
            binding_id = %binding.id,
            session_count = session_ids.len(),
            deleted_mappings,
            "channel binding: deleted with session cleanup"
        );
        Ok(())
    }

    pub(super) fn redact_bindings(
        &self,
        bindings: Vec<ChannelBinding>,
    ) -> Result<Vec<ChannelBinding>, ChannelUseCaseError> {
        bindings
            .into_iter()
            .map(|mut binding| {
                let provider = self.provider_for(&binding.channel_type)?;
                binding.config = provider.redact_config(&binding.config);
                Ok(binding)
            })
            .collect()
    }

    pub(super) fn provider_for(
        &self,
        channel_type: &str,
    ) -> Result<Arc<dyn ChannelProvider>, ChannelUseCaseError> {
        self.providers.get(channel_type).ok_or_else(|| {
            ChannelUseCaseError::InvalidParams(format!(
                "channel provider '{channel_type}' is not available"
            ))
        })
    }
}
