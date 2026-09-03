use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, BotControlPlaneCoreService, BotRegistryCoreService, BotTaskModesQuery,
    ChannelBindingCleanupPort, DeleteProviderBotCommand, DeleteProviderBotOutcome,
    NoopChannelBindingCleanupPort, ProviderBotBinding, ProviderBotCoreService,
    ProviderBotRosterItem, ProviderBotTaskModesFilter, ProviderCoreService,
    ProviderManagementService, ProviderRecord, RegisterProviderBotCommand,
    RegisterProviderBotOutcome, RegisterProviderBotParams, RegisterProviderCommand,
    RegisterProviderOutcome, RelationCoreService, ServiceError, ServiceResult,
    UpdateProviderBotCommand, UpdateProviderBotOutcome, UpdateProviderCommand,
};
use bcs_user_directory_api::UserDirectoryPlugin;

#[derive(Clone)]
pub struct ProviderManagement {
    provider_core: Arc<dyn ProviderCoreService>,
    provider_bot_core: Arc<dyn ProviderBotCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    relation: Arc<dyn RelationCoreService>,
    channel_binding_cleanup: Arc<dyn ChannelBindingCleanupPort>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    control_plane: Option<Arc<dyn BotControlPlaneCoreService>>,
}

impl ProviderManagement {
    pub fn new(
        provider_core: Arc<dyn ProviderCoreService>,
        provider_bot_core: Arc<dyn ProviderBotCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        relation: Arc<dyn RelationCoreService>,
    ) -> Self {
        Self {
            provider_core,
            provider_bot_core,
            registry,
            relation,
            channel_binding_cleanup: Arc::new(NoopChannelBindingCleanupPort),
            user_directory: None,
            control_plane: None,
        }
    }

    pub fn with_channel_binding_cleanup(
        mut self,
        channel_binding_cleanup: Arc<dyn ChannelBindingCleanupPort>,
    ) -> Self {
        self.channel_binding_cleanup = channel_binding_cleanup;
        self
    }

    pub fn with_user_directory(mut self, user_directory: Arc<dyn UserDirectoryPlugin>) -> Self {
        self.user_directory = Some(user_directory);
        self
    }

    /// Inject the bot control-plane core required by the task-mode roster
    /// (`list_provider_bots_by_task_modes`). The composition root wires this for
    /// the production/memory server paths; without it the roster method errors.
    pub fn with_control_plane(
        mut self,
        control_plane: Arc<dyn BotControlPlaneCoreService>,
    ) -> Self {
        self.control_plane = Some(control_plane);
        self
    }

    async fn ensure_owner_binding(&self, bot_uuid: &str, staff_no: &str) -> ServiceResult<()> {
        let nick_name = self.resolve_owner_nick_name(staff_no).await;
        self.registry
            .ensure_human_actor(staff_no, &nick_name)
            .await?;
        let human_id = format!("human_{}", staff_no);
        let env = bcs_config::resolve_env_str();
        self.relation
            .ensure_owner_edges(&human_id, bot_uuid, &env)
            .await
    }

    async fn resolve_owner_nick_name(&self, staff_no: &str) -> String {
        let Some(user_directory) = self.user_directory.as_ref() else {
            return staff_no.to_string();
        };
        match user_directory.lookup_by_staff_no(staff_no).await {
            Ok(Some(profile)) => profile
                .nick_name
                .as_deref()
                .map(str::trim)
                .filter(|nick_name| !nick_name.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| staff_no.to_string()),
            Ok(None) => {
                tracing::warn!(
                    staff_no = %staff_no,
                    "user directory returned no profile; falling back to staff_no for human actor name"
                );
                staff_no.to_string()
            }
            Err(error) => {
                tracing::warn!(
                    staff_no = %staff_no,
                    error = %error,
                    "user directory lookup failed; falling back to staff_no for human actor name"
                );
                staff_no.to_string()
            }
        }
    }

    async fn reject_existing_bot_uuid_for_unbound_ref(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<()> {
        self.provider_core
            .get_provider(provider_id, provider_admin_token)
            .await?;
        let binding = self
            .provider_bot_core
            .get_provider_bot_binding_by_ref(provider_id, provider_bot_ref)
            .await?;
        if binding.is_some() {
            return Ok(());
        }
        if self.registry.get(provider_bot_ref).await.is_some() {
            return Err(ServiceError::Conflict(format!(
                "provider_bot_ref '{}' is already registered as bot_uuid",
                provider_bot_ref
            )));
        }
        Ok(())
    }

    async fn ensure_provider_admin_active(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord> {
        let provider = self
            .provider_core
            .get_provider(provider_id, provider_admin_token)
            .await?;
        if provider.disabled {
            tracing::warn!(
                provider_id = %provider.provider_id,
                "disabled provider admin access rejected"
            );
            return Err(ServiceError::Forbidden("provider_disabled".to_string()));
        }
        Ok(provider)
    }
}

#[async_trait]
impl ProviderManagementService for ProviderManagement {
    async fn register_provider(
        &self,
        command: RegisterProviderCommand,
    ) -> ServiceResult<RegisterProviderOutcome> {
        let admin_callback_url = command.admin_callback_url;
        let registered = self
            .provider_core
            .register_provider(
                command.name,
                command.webhook_url,
                command.auth_mode,
                command.created_by,
                command.protocol_version,
                command.coordination,
            )
            .await?;
        if let Some(admin_callback_url) = admin_callback_url {
            self.provider_core
                .update_provider_admin_callback_url(
                    &registered.provider.provider_id,
                    &registered.provider_admin_token,
                    &registered.provider.created_by,
                    admin_callback_url,
                )
                .await?;
        }
        Ok(RegisterProviderOutcome {
            provider_id: registered.provider.provider_id,
            provider_admin_token: registered.provider_admin_token,
            bcs_to_provider_token: registered.bcs_to_provider_token,
        })
    }

    async fn get_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord> {
        self.provider_core
            .get_provider(provider_id, provider_admin_token)
            .await
    }

    async fn get_active_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord> {
        self.ensure_provider_admin_active(provider_id, provider_admin_token)
            .await
    }

    async fn update_provider(
        &self,
        command: UpdateProviderCommand,
    ) -> ServiceResult<ProviderRecord> {
        let admin_callback_url = command.admin_callback_url;
        let provider = self
            .provider_core
            .update_provider(
                &command.provider_id,
                &command.provider_admin_token,
                &command.authenticated_staff_id,
                command.name,
                command.webhook_url,
                command.protocol_version,
                command.coordination,
                command.organization_management,
            )
            .await?;
        match admin_callback_url {
            Some(admin_callback_url) => {
                self.provider_core
                    .update_provider_admin_callback_url(
                        &provider.provider_id,
                        &command.provider_admin_token,
                        &command.authenticated_staff_id,
                        admin_callback_url,
                    )
                    .await
            }
            None => Ok(provider),
        }
    }

    async fn register_provider_bot(
        &self,
        command: RegisterProviderBotCommand,
    ) -> ServiceResult<RegisterProviderBotOutcome> {
        if command.reject_existing_bot_uuid {
            self.reject_existing_bot_uuid_for_unbound_ref(
                &command.provider_id,
                &command.provider_admin_token,
                &command.provider_bot_ref,
            )
            .await?;
        }
        let owner = command
            .owners
            .first()
            .map(|owner| owner.trim().to_string())
            .unwrap_or_default();
        let duplicate_registration = self
            .provider_bot_core
            .get_provider_bot_binding_by_ref(&command.provider_id, &command.provider_bot_ref)
            .await?
            .is_some();
        let (binding, bot_runtime_token) = self
            .provider_bot_core
            .register_provider_bot_with_bot_uuid(
                &command.provider_id,
                &command.provider_admin_token,
                RegisterProviderBotParams {
                    bot_name: command.name,
                    summary: command.summary,
                    owners: command.owners,
                    provider_bot_ref: command.provider_bot_ref,
                    domains: command.domains,
                    skills: command.skills,
                    scopes: command.scopes,
                    bot_uuid: command.bot_uuid,
                    connection_mode: command.connection_mode,
                },
            )
            .await?;
        if !duplicate_registration {
            self.ensure_owner_binding(&binding.bot_uuid, &owner).await?;
        }
        let created = !duplicate_registration;
        let capabilities = if created {
            match self.registry.get(&binding.bot_uuid).await {
                Some(bot) => Some(bot.capabilities),
                None => {
                    tracing::warn!(
                        bot_uuid = %binding.bot_uuid,
                        provider_id = %binding.provider_id,
                        "register_provider_bot: freshly created bot missing from registry; \
                         capabilities unavailable for visibility sync"
                    );
                    None
                }
            }
        } else {
            None
        };
        Ok(RegisterProviderBotOutcome {
            bot_uuid: binding.bot_uuid,
            provider_id: binding.provider_id,
            provider_bot_ref: binding.provider_bot_ref,
            bot_runtime_token,
            message: duplicate_registration
                .then(|| "provider bot ref already registered; returning existing bot".to_string()),
            created,
            capabilities,
            actor_kind: ActorKind::Bot,
        })
    }

    async fn list_provider_bots(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        self.provider_bot_core
            .list_provider_bots(provider_id, provider_admin_token)
            .await
    }

    async fn list_provider_bots_by_task_modes(
        &self,
        filter: ProviderBotTaskModesFilter,
    ) -> ServiceResult<Vec<ProviderBotRosterItem>> {
        let control_plane = self.control_plane.clone().ok_or_else(|| {
            ServiceError::InternalError("control-plane core not configured".to_string())
        })?;
        // Env-scoped task-mode roster. Provider admission (admin token +
        // `allowed_switch_provider_ids`) is enforced by the route; this use
        // case intentionally does not intersect with provider bot bindings.
        let env = bcs_config::resolve_env_str();
        let views = control_plane
            .list_by_task_modes(BotTaskModesQuery {
                env,
                task_claim_mode: filter.task_claim_mode,
                task_dream_mode: filter.task_dream_mode,
                match_mode: filter.match_mode,
                visibility: filter.visibility,
                status: filter.status,
                user_visibility: filter.user_visibility,
            })
            .await?;
        let items = views
            .into_iter()
            .map(|view| ProviderBotRosterItem {
                bot_id: view.record.bot_id,
                name: view.record.name,
                env: view.record.env,
                task_claim_mode: view.record.task_claim_mode,
                task_dream_mode: view.record.task_dream_mode,
                updated_at: view.record.updated_at,
                visibility: view.record.visibility,
                created_by: view.record.created_by,
                status: view.record.status,
                user_visibility: view.record.user_visibility,
            })
            .collect();
        Ok(items)
    }

    async fn delete_provider_bot(
        &self,
        command: DeleteProviderBotCommand,
    ) -> ServiceResult<DeleteProviderBotOutcome> {
        self.ensure_provider_admin_active(&command.provider_id, &command.provider_admin_token)
            .await?;

        let binding = self
            .provider_bot_core
            .get_provider_bot_binding_by_ref(&command.provider_id, &command.provider_bot_ref)
            .await?;
        let bot_uuid = match binding {
            Some(binding) => {
                if binding.provider_id != command.provider_id {
                    return Err(ServiceError::Forbidden("provider_id_mismatch".to_string()));
                }
                let _ = self
                    .provider_bot_core
                    .set_provider_bot_disabled(
                        &command.provider_id,
                        &binding.bot_uuid,
                        &command.provider_admin_token,
                        true,
                    )
                    .await?;
                binding.bot_uuid
            }
            // Legacy bots on allowed-switch providers reuse provider_bot_ref as bot_uuid
            // and may have no binding row.
            None if command.allow_unbound_owner_suffixed_bot
                && is_owner_suffixed_bot_id(&command.provider_bot_ref) =>
            {
                if self.registry.get(&command.provider_bot_ref).await.is_none() {
                    return Err(ServiceError::BotNotFound(command.provider_bot_ref));
                }
                command.provider_bot_ref.clone()
            }
            None => {
                return Err(ServiceError::BotNotFound(command.provider_bot_ref));
            }
        };

        // Soft-delete the bot first so concurrent channel binding creation can no
        // longer validate this bot as a target, then remove its channel bindings.
        // Cleanup failure is returned as an error; re-deleting is idempotent for
        // binding-backed bots because the provider binding row still resolves bot_uuid.
        let deleted = self.registry.soft_delete(&bot_uuid).await;
        self.channel_binding_cleanup
            .delete_bindings_for_bot(&bot_uuid)
            .await?;
        Ok(DeleteProviderBotOutcome {
            bot_uuid,
            provider_id: command.provider_id,
            provider_bot_ref: command.provider_bot_ref,
            deleted,
        })
    }

    async fn set_provider_disabled(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        disabled: bool,
    ) -> ServiceResult<ProviderRecord> {
        self.provider_core
            .set_provider_disabled(
                provider_id,
                provider_admin_token,
                authenticated_staff_id,
                disabled,
            )
            .await
    }

    async fn update_provider_bot(
        &self,
        command: UpdateProviderBotCommand,
    ) -> ServiceResult<UpdateProviderBotOutcome> {
        let result = self
            .provider_bot_core
            .update_provider_bot(
                &command.provider_id,
                &command.provider_admin_token,
                &command.provider_bot_ref,
                command.name,
                command.summary,
                command.domains,
                command.skills,
                command.scopes,
                command.visibility,
            )
            .await?;
        let capabilities = result.capabilities;
        Ok(UpdateProviderBotOutcome {
            bot_uuid: result.binding.bot_uuid,
            provider_id: result.binding.provider_id,
            provider_bot_ref: result.binding.provider_bot_ref,
            name: capabilities.name,
            summary: capabilities.summary,
            domains: capabilities.domains,
            skills: capabilities.skills,
            scopes: capabilities.scopes,
            visibility: capabilities.visibility,
        })
    }
}

fn is_owner_suffixed_bot_id(bot_uuid: &str) -> bool {
    bot_uuid
        .rsplit_once(':')
        .is_some_and(|(prefix, owner)| !prefix.trim().is_empty() && !owner.trim().is_empty())
}
