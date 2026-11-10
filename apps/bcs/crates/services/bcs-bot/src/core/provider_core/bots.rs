use super::*;

#[async_trait]
impl ProviderBotCoreService for ProviderCore {
    async fn retire_registered_bot(
        &self,
        params: bcs_service_api::core::RetireProviderBotParams,
    ) -> ServiceResult<ProviderRetirement> {
        use bcs_service_api::core::{
            ProviderRetire, ProviderRetireBranch,
        };
        let RetireProviderBotParams {
            provider_id,
            provider_admin_token,
            provider_bot_ref,
            allow_unbound_owner_suffixed_bot,
        } = params;
        // The credential verification stays in the Core
        // (the same flows the read/update paths run); the
        // token NEVER enters the mutation, audit rows or
        // the return value.
        let provider = self
            .authenticated_provider(&provider_id, &provider_admin_token)
            .await?;
        if provider.provider_id != provider_id {
            return Err(ServiceError::Forbidden(
                "provider_id_mismatch".to_string(),
            ));
        }
        validate_external_id("provider_bot_ref", &provider_bot_ref)?;
        let Some(metadata) = &self.bot_providers else {
            return Err(ServiceError::InvalidOperation {
                message: "provider bot retirement requires the \
                          Bot Provider metadata adapter"
                    .to_string(),
                request_id: None,
            });
        };

        // Resolve the Bot within the existing limits on the
        // three durable identity branches. The flagged
        // LegacyUnbound lookup NEVER grants delete power by
        // itself — the owner-suffix admission and the store's
        // re-verified rules stay intact.
        let (bot_id, expected_branch) = if let Some(record) =
            metadata
                .get_provider_bot_by_ref(
                    &provider.provider_id,
                    &provider_bot_ref,
                )
                .await?
        {
            // The accepted Provider membership routes the
            // retirement; the tombstone KEEPS the metadata,
            // so a cleanup retry re-derives the same branch.
            (record.bot_uuid, ProviderRetireBranch::Metadata)
        } else if let Some(binding) = self
            .bindings
            .get_binding_by_provider_ref(
                &provider.provider_id,
                &provider_bot_ref,
            )
            .await?
        {
            // Legacy gateway before metadata backfill: the
            // binding routes the retirement.
            (
                binding.bot_uuid,
                ProviderRetireBranch::BindingFallback,
            )
        } else if allow_unbound_owner_suffixed_bot
            && bcs_service_api::core::provider::
                is_owner_suffixed_bot_id(&provider_bot_ref)
        {
            // Existing LegacyUnbound admission (owner-suffixed
            // Bot that never gained metadata or a binding). A
            // cleanup retry finds the identity on the retained
            // tombstone — never a resurrected one.
            if self.registry.get(&provider_bot_ref).await.is_none()
                && self
                    .registry
                    .get_including_deleted(&provider_bot_ref)
                    .await
                    .is_none()
            {
                return Err(ServiceError::BotNotFound(
                    provider_bot_ref.to_string(),
                ));
            }
            (
                provider_bot_ref.clone(),
                ProviderRetireBranch::LegacyUnbound,
            )
        } else {
            return Err(ServiceError::BotNotFound(
                provider_bot_ref.to_string(),
            ));
        };

        // The verified Provider identity is the REAL actor
        // (spec §3.1); no Human impersonation, no token.
        let mutation =
            bcs_service_api::types::bot_authority::AuthorityMutation {
                mutation_id: format!(
                    "retire_{}",
                    uuid::Uuid::new_v4().simple()
                ),
                actor:
                    bcs_service_api::types::bot_authority::AuthorityActor::
                        Provider {
                            provider_id: provider.provider_id.clone(),
                        },
            };
        // ONE atomic retirement command — never chained into
        // an ordinary delete afterwards (spec §7.1).
        metadata
            .retire_with_authority(ProviderRetire {
                provider_id: provider.provider_id.clone(),
                bot_id,
                provider_bot_ref,
                mutation,
                expected_branch,
            })
            .await
    }

    async fn update_provider_bot_webhook(
        &self, provider_id: &str, provider_admin_token: &str,
        provider_bot_ref: &str, webhook_url: Option<String>,
    ) -> ServiceResult<UpdateProviderBotCoreResult> {
        let provider = self.authenticated_provider(provider_id, provider_admin_token).await?;
        validate_external_id("provider_bot_ref", provider_bot_ref)?;
        let binding = self.bindings.get_binding_by_provider_ref(provider_id, provider_bot_ref)
            .await?.ok_or_else(|| ServiceError::BotNotFound(provider_bot_ref.to_string()))?;
        if binding.provider_id != provider.provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        let bot = self.registry.try_get(&binding.bot_uuid).await?
            .ok_or_else(|| ServiceError::BotNotFound(binding.bot_uuid.clone()))?;
        if let Some(url) = webhook_url.as_deref() {
            validate_outbound_url(&self.webhook_url_guard, "webhook_url", url)?;
        } else if parse_downlink_config(&provider.config)?.webhook_url.is_none() {
            return Err(ServiceError::InvalidOperation {
                message: "missing_delivery_endpoint: cannot inherit an absent Provider webhook_url".to_string(),
                request_id: None,
            });
        }
        let updated = self.bindings.update_binding_webhook_url(provider_id, &binding.bot_uuid,
            webhook_url.as_deref(), now_ms()).await?
            .ok_or_else(|| ServiceError::BotNotFound(binding.bot_uuid.clone()))?;
        Ok(UpdateProviderBotCoreResult { binding: updated, capabilities: bot.capabilities })
    }

    async fn register_provider_bot_with_bot_uuid(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        params: RegisterProviderBotParams,
    ) -> ServiceResult<(ProviderBotBinding, Option<String>)> {
        self.register_provider_bot_internal(provider_id, provider_admin_token, params, Vec::new())
            .await
    }

    async fn register_provider_bot_with_initial_managers(
        &self, provider_id: &str, provider_admin_token: &str,
        params: RegisterProviderBotParams, initial_managers: Vec<String>,
    ) -> ServiceResult<(ProviderBotBinding, Option<String>)> {
        self.register_provider_bot_internal(provider_id, provider_admin_token, params, initial_managers).await
    }

    async fn list_provider_bots(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        self.authenticated_provider(provider_id, provider_admin_token)
            .await?;
        // Return all bindings registered by this provider in the store, excluding
        // soft-deleted (disabled) ones, regardless of whether each bot is
        // currently active in the registry.
        let bindings = self.bindings.list_bindings_by_provider(provider_id).await?;
        Ok(bindings
            .into_iter()
            .filter(|binding| !binding.disabled)
            .collect())
    }

    async fn get_provider_bot_binding_by_ref(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        self.bindings
            .get_binding_by_provider_ref(provider_id, provider_bot_ref)
            .await
    }

    async fn get_provider_bot_binding_by_bot_uuid(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        self.bindings.get_binding_by_bot_uuid(bot_uuid).await
    }

    async fn authenticate_static_bearer_event(
        &self,
        provider_id: &str,
        bot_runtime_token: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        let bot_uuid = self.registry.resolve_bot_token(bot_runtime_token, None).await?
            .ok_or_else(|| ServiceError::Unauthorized("invalid bot runtime token".to_string()))?.bot.bot_id;
        let binding = self
            .bindings
            .get_binding_by_bot_uuid(&bot_uuid)
            .await?
            .ok_or_else(|| ServiceError::Unauthorized("bot is not provider managed".to_string()))?;
        if binding.provider_id != provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        let provider = self.load_provider_for_binding(&binding).await?;
        let auth_mode = parse_downlink_config(&provider.config)?.auth_mode;
        if !matches!(auth_mode, ProviderAuthMode::StaticBearer | ProviderAuthMode::AgentPassSubbots) {
            return Err(ServiceError::Unauthorized("auth_mode_mismatch".to_string()));
        }
        Ok(RuntimeBotIdentity {
            bot_uuid,
            provider_id: provider_id.to_string(),
        })
    }

    async fn authenticate_agentpass_event(
        &self,
        provider_id: &str,
        agent_code: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        let provider = self.providers.get_provider(provider_id).await?.ok_or_else(|| ServiceError::ProviderNotFound(provider_id.into()))?;
        if provider.disabled { return Err(ServiceError::Unauthorized("provider is disabled".into())); }
        if parse_downlink_config(&provider.config)?.auth_mode == ProviderAuthMode::AgentPassSubbots {
            return Err(ServiceError::Unauthorized("ActorRequired: parent AgentPass requires a Bot Actor".into()));
        }
        let binding = self
            .bindings
            .get_binding_by_provider_ref(provider_id, agent_code)
            .await?
            .ok_or_else(|| ServiceError::Unauthorized("unknown agent_code".to_string()))?;
        let provider = self.load_provider_for_binding(&binding).await?;
        let auth_mode = parse_downlink_config(&provider.config)?.auth_mode;
        if auth_mode != ProviderAuthMode::AgentPass {
            return Err(ServiceError::Unauthorized("auth_mode_mismatch".to_string()));
        }
        Ok(RuntimeBotIdentity {
            bot_uuid: binding.bot_uuid,
            provider_id: provider_id.to_string(),
        })
    }

    async fn authenticate_agentpass_actor_event(&self, provider_id: &str, agent_code: &str, actor: &str) -> ServiceResult<RuntimeBotIdentity> {
        let bot = self.registry.resolve_agent_bot(agent_code, Some(actor)).await?.ok_or_else(|| ServiceError::Unauthorized("AgentPass Bot is not registered".into()))?;
        if bot.bot.provider_id.as_deref() != Some(provider_id) { return Err(ServiceError::Forbidden("provider_id_mismatch".into())); }
        Ok(RuntimeBotIdentity { bot_uuid: bot.bot.bot_id, provider_id: provider_id.into() })
    }

    async fn authenticate_provider_admin_event(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        let credential = self
            .credentials
            .get_credential_by_secret("provider_admin", provider_admin_token)
            .await?
            .ok_or_else(|| {
                ServiceError::Unauthorized("invalid provider admin token".to_string())
            })?;
        if credential.disabled {
            return Err(ServiceError::Unauthorized(
                "provider admin token is disabled".to_string(),
            ));
        }
        if credential.provider_id != provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        let provider = self
            .providers
            .get_provider(&credential.provider_id)
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider '{}' not found", credential.provider_id),
                request_id: None,
            })?;
        if provider.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' is disabled", provider.provider_id),
                request_id: None,
            });
        }
        let auth_mode = parse_downlink_config(&provider.config)?.auth_mode;
        if auth_mode != ProviderAuthMode::ProviderAdmin {
            return Err(ServiceError::Unauthorized("auth_mode_mismatch".to_string()));
        }
        let binding = self
            .bindings
            .get_binding_by_provider_ref(&provider.provider_id, provider_bot_ref)
            .await?
            .ok_or_else(|| {
                ServiceError::BotNotFound(format!("provider bot '{}' not found", provider_bot_ref))
            })?;
        if binding.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider bot '{}' is disabled", binding.bot_uuid),
                request_id: None,
            });
        }
        if self.registry.get(&binding.bot_uuid).await.is_none() {
            return Err(ServiceError::BotNotFound(binding.bot_uuid));
        }
        Ok(RuntimeBotIdentity {
            bot_uuid: binding.bot_uuid,
            provider_id: provider.provider_id,
        })
    }

    async fn get_provider_coordination_config(
        &self,
        provider_id: &str,
    ) -> ServiceResult<ProviderCoordinationConfig> {
        let provider = self
            .providers
            .get_provider(provider_id)
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider '{}' not found", provider_id),
                request_id: None,
            })?;
        if provider.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider '{}' is disabled", provider.provider_id),
                request_id: None,
            });
        }
        parse_coordination_config(&provider.config)
    }

    async fn set_provider_bot_disabled(
        &self,
        provider_id: &str,
        bot_uuid: &str,
        provider_admin_token: &str,
        disabled: bool,
    ) -> ServiceResult<ProviderBotBinding> {
        self.authenticated_provider(provider_id, provider_admin_token)
            .await?;
        let binding = self
            .bindings
            .get_binding_by_bot_uuid(bot_uuid)
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider bot '{}' not found", bot_uuid),
                request_id: None,
            })?;
        if binding.provider_id != provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        let updated = self.bindings
            .update_binding_disabled(bot_uuid, disabled, now_ms())
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("provider bot '{}' not found", bot_uuid),
                request_id: None,
            })?;
        if disabled && self.bot_providers.is_some() {
            // The projection has committed the tombstone; evict runtime caches.
            self.registry.soft_delete(bot_uuid).await;
        }
        Ok(updated)
    }

    async fn update_provider_bot(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        provider_bot_ref: &str,
        name: Option<String>,
        summary: Option<String>,
        domains: Option<Vec<String>>,
        skills: Option<Vec<Skill>>,
        scopes: Option<Vec<String>>,
        visibility: Option<String>,
    ) -> ServiceResult<UpdateProviderBotCoreResult> {
        let provider = self
            .authenticated_provider(provider_id, provider_admin_token)
            .await?;
        validate_external_id("provider_bot_ref", provider_bot_ref)?;
        let binding = match self.bindings.get_binding_by_provider_ref(&provider.provider_id, provider_bot_ref).await? {
            Some(binding) => binding,
            None => {
                let metadata = self.bot_providers.as_ref().ok_or_else(|| ServiceError::BotNotFound(provider_bot_ref.into()))?;
                let record = metadata.get_provider_bot_by_ref(provider_id, provider_bot_ref).await?
                    .filter(|record| record.connection_mode == BotConnectionMode::Plugin && !record.is_deleted)
                    .ok_or_else(|| ServiceError::BotNotFound(provider_bot_ref.into()))?;
                ProviderBotBinding { bot_uuid: record.bot_uuid, provider_id:record.provider_id,
                    provider_bot_ref:record.provider_bot_ref, webhook_url:None, disabled:false,
                    created_at:0, updated_at:0 }
            }
        };
        if binding.provider_id != provider.provider_id {
            return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
        }
        if binding.disabled {
            return Err(ServiceError::InvalidOperation {
                message: format!("provider bot '{}' is disabled", binding.bot_uuid),
                request_id: None,
            });
        }
        let existing = self
            .registry
            .try_get(&binding.bot_uuid)
            .await?
            .ok_or_else(|| ServiceError::BotNotFound(binding.bot_uuid.clone()))?;

        // Stored identity is immutable during ordinary capability updates.
        let agent_code = existing.capabilities.agent_code.clone();

        // PATCH-merge: each `Some` replaces (empty Vec clears), `None` keeps.
        // `agent_token` is left `None` (managed in-memory only, never via caps).
        if let Some(visibility) = visibility.as_deref() {
            let trimmed = visibility.trim();
            if !matches!(trimmed, "public" | "protected" | "private") {
                return Err(ServiceError::InvalidOperation {
                    message: "visibility must be 'public', 'protected', or 'private'"
                        .to_string(),
                    request_id: None,
                });
            }
        }

        let mut merged = existing.capabilities.clone();
        if let Some(name) = name {
            merged.name = Some(name);
        }
        if let Some(summary) = summary {
            merged.summary = Some(summary);
        }
        if let Some(domains) = domains {
            merged.domains = domains;
        }
        if let Some(skills) = skills {
            merged.skills = skills;
        }
        if let Some(scopes) = scopes {
            merged.scopes = scopes;
        }
        if let Some(visibility) = visibility {
            merged.visibility = visibility.trim().to_string();
        }
        merged.agent_code = agent_code;

        // Use `update_capabilities` (wholesale replacement) rather than
        // `register`, whose existing-bot merge skips empty capability arrays
        // — a PATCH that clears domains/skills/scopes must take effect in the
        // live registry and runtime discovery, not only in the response.
        self.registry
            .update_capabilities(&binding.bot_uuid, merged.clone())
            .await?;
        info!(
            provider_id = %provider.provider_id,
            bot_uuid = %binding.bot_uuid,
            provider_bot_ref = %provider_bot_ref,
            "update_provider_bot: capabilities updated in place"
        );
        Ok(UpdateProviderBotCoreResult {
            binding,
            capabilities: merged,
        })
    }
}
