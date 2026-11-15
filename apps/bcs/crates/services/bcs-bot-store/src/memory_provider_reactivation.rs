//! Provider tombstone reactivation with a fresh runtime and authority snapshot.

use bcs_service_api::types::bot_authority::OwnershipInitialization;
use bcs_service_api::{BotCapabilities, ServiceError, ServiceResult};

use bcs_config::resolve_env_str as resolve_env;

use super::authority::{MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus};
use super::registration_ownership::{mem_append_audit, stage_initial_managers};
use super::{MemoryBotRepo, PersistedCapabilities, RegisteredBotInner};
use crate::ownership as ownership_composition;

impl MemoryBotRepo {
    pub(crate) async fn restore_provider_registration(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
        initialization: &OwnershipInitialization,
        provider_id: &str,
    ) -> ServiceResult<()> {
        crate::ownership_managers::validate_provider_initialization(initialization)?;
        if initialization.mutation.actor.actor_id() != format!("provider_{provider_id}") {
            return Err(ServiceError::Conflict(
                "Provider registration actor does not match the verified Provider".into(),
            ));
        }
        let owner = initialization.owner_user_id.clone();
        let owner_actor = ownership_composition::owner_actor_id(&owner);
        if created_by.trim() != owner || bot_id.trim().is_empty() || token.trim().is_empty() {
            return Err(ownership_composition::initialization_invalid_public(
                "reactivation requires the verified owner, Bot ID and runtime credential",
            ));
        }
        if bot_id == owner_actor {
            return Err(ServiceError::Conflict(
                "physical Bot ID must differ from its owner Human ID".into(),
            ));
        }

        let env = resolve_env();
        let _write_guard = self.record_write_lock.lock().await;
        let mut authority_guard = self.authority.write().await;
        let mut profiles_guard = self.profiles.write().await;
        let mut bots = self.bots.write().await;
        let mut tokens = self.token_to_bot.write().await;
        let mut deleted = self.deleted_bot_ids.write().await;
        let mut bindings = self.binding_channel_index.write().await;
        let mut control_plane_audit = self.control_plane_audit.write().await;

        if !deleted.contains(&bot_id) || bots.contains_key(&bot_id) {
            return Err(ServiceError::Conflict(
                "Provider Bot is not a restorable tombstone".into(),
            ));
        }
        if tokens.get(token).is_some_and(|existing| existing != &bot_id) {
            return Err(ServiceError::Conflict("Bot registration token already exists".into()));
        }
        if deleted.contains(&owner_actor)
            || bots.get(&owner_actor).is_some_and(|row| {
                row.actor_kind != bcs_service_api::ActorKind::Human
                    || row.env.as_deref() != Some(env.as_str())
            })
        {
            return Err(ServiceError::Conflict(
                "registration owner must be a live Human in this environment".into(),
            ));
        }

        let mut staged_authority = authority_guard.clone();
        let mut staged_profiles = profiles_guard.clone();
        staged_authority.edges.retain(|key, _| {
            !(key.0 == env
                && key.1 == bot_id
                && matches!(key.3, MemoryAuthorityEdgeKind::Owner | MemoryAuthorityEdgeKind::Manager))
        });
        staged_authority
            .transfers
            .retain(|key, _| !(key.0 == env && key.1 == bot_id));
        staged_authority
            .ownership_versions
            .insert((env.clone(), bot_id.clone()), 1);
        staged_authority
            .provider_managed
            .insert((env.clone(), bot_id.clone()));
        staged_authority.edges.insert(
            (env.clone(), bot_id.clone(), owner_actor.clone(), MemoryAuthorityEdgeKind::Owner),
            (MemoryAuthorityEdgeStatus::Approved, 0),
        );
        super::permission_profile::stage_default_profile(&mut staged_profiles, &bot_id, &env)?;
        let new_humans = stage_initial_managers(
            &bots,
            &deleted,
            &mut staged_authority,
            &env,
            &bot_id,
            &initialization.initial_manager_user_ids,
        )?;
        let after_state = crate::ownership_managers::audit_state(
            &owner_actor,
            &initialization.initial_manager_user_ids,
        );
        mem_append_audit(
            &mut staged_authority,
            &env,
            &bot_id,
            "initialize",
            &initialization.mutation.mutation_id,
            initialization.mutation.actor.kind().as_str(),
            &initialization.mutation.actor.actor_id(),
            &owner_actor,
            None,
            &after_state,
        )?;

        let now = super::unix_millis();
        let stored = super::profile::StoredBotProfile {
            record: PersistedCapabilities {
                bot_id: bot_id.clone(),
                name: capabilities.name.clone(),
                summary: capabilities.summary.clone(),
                domains: capabilities.domains.clone(),
                skills: capabilities.skills.clone(),
                scopes: capabilities.scopes.clone(),
                binding_channels: capabilities.binding_channels.clone(),
                token: Some(token.to_string()),
                registered_at: now,
                hidden: false,
                created_by: Some(owner.clone()),
                visibility: if capabilities.visibility.is_empty() {
                    None
                } else {
                    Some(capabilities.visibility.clone())
                },
                agent_code: capabilities.agent_code.clone(),
                agent_token: capabilities.agent_token.clone(),
                user_visibility: Default::default(),
                friend_ext: Default::default(),
                friend_check_in_strategy: Default::default(),
            },
            avatar_url: None,
        };
        // Reactivation deliberately replaces the old tombstone file. In-memory
        // state is published only after this durable overwrite succeeds.
        self.write_stored_profile(&bot_id, &stored).await?;

        bindings.retain(|_, value| value != &bot_id);
        if let Some(channels) = &capabilities.binding_channels {
            for (channel, binding) in channels {
                bindings.insert(
                    (channel.clone(), binding.binding_key.clone()),
                    bot_id.clone(),
                );
            }
        }
        tokens.retain(|_, value| value != &bot_id);
        tokens.insert(token.to_string(), bot_id.clone());
        deleted.remove(&bot_id);
        control_plane_audit.insert(bot_id.clone(), (now, now));
        bots.insert(
            bot_id.clone(),
            RegisteredBotInner {
                bot_id: bot_id.clone(),
                last_heartbeat: std::time::Instant::now(),
                capabilities,
                ws_connection: None,
                session_token: Some(token.to_string()),
                env: Some(env.clone()),
                status: bcs_service_api::ActorStatus::Online,
                actor_kind: bcs_service_api::ActorKind::Bot,
                created_by: Some(owner.clone()),
                protocol_version: 1,
                user_visibility: Default::default(),
                friend_ext: Default::default(),
                friend_check_in_strategy: Default::default(),
            },
        );
        if !bots.contains_key(&owner_actor) {
            bots.insert(
                owner_actor.clone(),
                RegisteredBotInner {
                    bot_id: owner_actor,
                    last_heartbeat: std::time::Instant::now(),
                    capabilities: BotCapabilities {
                        name: Some(owner),
                        visibility: "protected".into(),
                        ..Default::default()
                    },
                    ws_connection: None,
                    session_token: Some(uuid::Uuid::new_v4().to_string()),
                    env: Some(env.clone()),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Human,
                    created_by: Some(created_by.to_string()),
                    protocol_version: 1,
                    user_visibility: Default::default(),
                    friend_ext: Default::default(),
                    friend_check_in_strategy: Default::default(),
                },
            );
        }
        for (id, human) in new_humans {
            control_plane_audit.insert(id.clone(), (now, now));
            bots.insert(id, human);
        }
        *authority_guard = staged_authority;
        *profiles_guard = staged_profiles;
        Ok(())
    }
}
