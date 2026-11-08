//! Bot onboarding use-case implementation.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, AdminBotOnboardCommand, BindingChannels, BotCapabilities, BotOnboardCommand,
    BotOnboardResult, BotOnboardingService, BotRegistryCoreService, EnsureBotCommand,
    EnsureBotResult, OnboardActorIdentity, RelationCoreService, ServiceError, ServiceResult,
};
use bcs_service_api::types::bot_profile::{ExistingProfileUpdate, ProfileWrite};
use serde_json::Value;

use bcs_service_api::port::repo::PermissionProfileRepoPort;

/// Bot onboarding application service backed by registry and relation services.
pub struct BotOnboarding {
    registry: Arc<dyn BotRegistryCoreService>,
    relation: Arc<dyn RelationCoreService>,
    binding_enabled: bool,
    default_visibility: Option<String>,
    /// Permission-profile repo for seeding the bot's default profile on onboard
    /// (spec §5.1.1). `None` skips the seed (tests/dev without a profile store).
    profiles: Option<Arc<dyn PermissionProfileRepoPort>>,
}

impl BotOnboarding {
    pub fn new(
        registry: Arc<dyn BotRegistryCoreService>,
        relation: Arc<dyn RelationCoreService>,
        binding_enabled: bool,
        default_visibility: Option<String>,
    ) -> Self {
        Self {
            registry,
            relation,
            binding_enabled,
            default_visibility,
            profiles: None,
        }
    }

    /// Inject the permission-profile repository so onboarding seeds the bot's
    /// default profile (idempotent wildcard-allow; spec §5.1.1, D12 rule 2).
    pub fn with_profiles(mut self, profiles: Arc<dyn PermissionProfileRepoPort>) -> Self {
        self.profiles = Some(profiles);
        self
    }

    async fn process_binding_channels(
        &self,
        bot_uuid: &str,
        requested: Option<&BindingChannels>,
        binding_enabled: bool,
        existing_caps: Option<&BotCapabilities>,
    ) -> (Option<BindingChannels>, HashMap<String, Value>, Vec<String>) {
        let existing_bindings = existing_caps.and_then(|caps| caps.binding_channels.clone());

        if !binding_enabled {
            return (existing_bindings, HashMap::new(), Vec::new());
        }

        let Some(requested_bindings) = requested else {
            return (existing_bindings, HashMap::new(), Vec::new());
        };

        let old_bindings = existing_bindings.clone().unwrap_or_default();

        let mut binding_results = HashMap::new();
        for (channel, binding) in requested_bindings {
            if let Some(existing_bot_uuid) = self
                .registry
                .find_bot_by_binding_channel(channel, &binding.binding_key)
                .await
            {
                if existing_bot_uuid != bot_uuid {
                    let existing_bot_name = self
                        .registry
                        .get(&existing_bot_uuid)
                        .await
                        .and_then(|bot| bot.capabilities.name)
                        .unwrap_or(existing_bot_uuid);
                    binding_results.insert(
                        channel.clone(),
                        serde_json::json!({
                            "status": "conflict",
                            "message": format!("Already bound to {}", existing_bot_name)
                        }),
                    );
                } else {
                    binding_results.insert(
                        channel.clone(),
                        serde_json::json!({
                            "status": "success"
                        }),
                    );
                }
            } else {
                binding_results.insert(
                    channel.clone(),
                    serde_json::json!({
                        "status": "success"
                    }),
                );
            }
        }

        let new_channels: HashSet<&String> = requested_bindings.keys().collect();
        let unbound: Vec<String> = old_bindings
            .keys()
            .filter(|key| !new_channels.contains(*key))
            .map(|key| format!("{}: {}", key, old_bindings[key].binding_key))
            .collect();

        let successful_bindings: BindingChannels = requested_bindings
            .iter()
            .filter(|(channel, _)| {
                binding_results
                    .get(*channel)
                    .and_then(|result| result.get("status"))
                    .and_then(|status| status.as_str())
                    == Some("success")
            })
            .map(|(channel, binding)| (channel.clone(), binding.clone()))
            .collect();

        let final_binding_channels = if successful_bindings.is_empty() {
            None
        } else {
            Some(successful_bindings)
        };

        (final_binding_channels, binding_results, unbound)
    }

    fn effective_visibility(&self, existing_caps: Option<&BotCapabilities>) -> String {
        existing_caps
            .map(|caps| caps.visibility.clone())
            .filter(|visibility| !visibility.is_empty())
            .or_else(|| self.default_visibility.clone())
            .unwrap_or_else(|| "protected".to_string())
    }

    fn merge_name(
        requested: Option<String>,
        existing_caps: Option<&BotCapabilities>,
        first_onboard: bool,
    ) -> ServiceResult<Option<String>> {
        if let Some(name) = non_empty_string(requested) {
            return Ok(Some(name));
        }

        if first_onboard {
            return Err(ServiceError::InvalidOperation {
                message: "name is required for first onboard".to_string(),
                request_id: None,
            });
        }

        Ok(existing_caps.and_then(|caps| caps.name.clone()))
    }

    fn merge_summary(
        requested: Option<String>,
        existing_caps: Option<&BotCapabilities>,
    ) -> Option<String> {
        non_empty_string(requested)
            .or_else(|| existing_caps.and_then(|caps| caps.summary.clone()))
    }

    fn merge_vec<T: Clone>(requested: Vec<T>, existing: Option<&Vec<T>>) -> Vec<T> {
        if requested.is_empty() {
            existing.cloned().unwrap_or_default()
        } else {
            requested
        }
    }

    /// The legacy not-registered response: `onboarded=false` with the
    /// restart hint and no capability/avatar side effects. Shared by the
    /// precheck miss and the missing-after-precheck window of the admin
    /// existing-only profile write (bot-avatar plan Task 5).
    fn not_onboarded_result(bot_uuid: String) -> BotOnboardResult {
        BotOnboardResult {
            bot_uuid,
            onboarded: false,
            name: None,
            message: Some("Bot 未在协作网络注册，请尝试重启".to_string()),
            binding_results: HashMap::new(),
            unbound: Vec::new(),
            capabilities: None,
            actor_kind: ActorKind::Bot,
        }
    }

    async fn save_capabilities(
        &self,
        bot_uuid: &str,
        capabilities: &BotCapabilities,
    ) -> ServiceResult<()> {
        self.registry
            .save_to_storage(bot_uuid, capabilities)
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "Failed to persist bot capabilities: {}",
                    error
                ))
            })
    }

    async fn bind_created_by_and_owner_edges(
        &self,
        bot_uuid: &str,
        identity: Option<&OnboardActorIdentity>,
    ) -> ServiceResult<()> {
        let Some(identity) = identity else {
            return Ok(());
        };
        if identity.staff_no.is_empty() {
            return Ok(());
        }

        let overwrite = bot_uuid.ends_with(&identity.staff_no);
        self.registry
            .save_created_by(bot_uuid, &identity.staff_no, overwrite)
            .await?;

        let nick_name = identity
            .nick_name
            .as_deref()
            .filter(|name| !name.is_empty())
            .unwrap_or(&identity.staff_no);
        let human_id = format!("human_{}", identity.staff_no);
        let env = bcs_config::resolve_env_str();

        self.registry
            .ensure_human_actor(&identity.staff_no, nick_name)
            .await?;
        self.relation
            .ensure_owner_edges(&human_id, bot_uuid, &env)
            .await?;

        // NOTE (B4a): `ensure_default_profile` is NOT in the same DB transaction
        // as `ensure_owner_edges` above — they hit different stores
        // (`bcs-relation-store` vs `bcs-edge-permission-store`). If the profile
        // seed fails, the owner edges are already committed but the default
        // profile is missing, so admission will fall back to `public_default`
        // until repair (ETL reconciliation catches this). Spec §8.3 prefers a
        // same-tx write; this is degraded until a cross-store transaction lands.
        //
        // Seed the bot's default permission profile (wildcard-allow) — idempotent;
        // D12 rule 2: never overwrites an existing default. Spec §5.1.1.
        if let Some(profiles) = &self.profiles {
            profiles.ensure_default_profile(bot_uuid, &env).await?;
        }
        Ok(())
    }

    /// Phase 0 ensure logic (spec §4.2 Step 0b): register the bot if absent,
    /// otherwise update name/summary/visibility; then bind the creator's human
    /// actor + owner edges + seed the default profile (via
    /// [`Self::bind_created_by_and_owner_edges`]).
    ///
    /// Returns `true` when a new `bcs_bots` row was created, `false` when the
    /// bot already existed and was only updated.
    async fn ensure_bot_inner(
        &self,
        bot_uuid: &str,
        name: &str,
        summary: Option<&str>,
        visibility: &str,
        actor_identity: Option<&OnboardActorIdentity>,
    ) -> ServiceResult<bool> {
        let existing_bot = self.registry.get(bot_uuid).await;
        let created = existing_bot.is_none();

        // Resolve the effective visibility: keep existing visibility when the
        // bot already exists and the request leaves visibility blank; otherwise
        // fall back to the configured default, then "protected".
        let effective_visibility = if visibility.is_empty() {
            self.effective_visibility(existing_bot.as_ref().map(|bot| &bot.capabilities))
        } else {
            visibility.to_string()
        };
        // Preserve an existing name/summary when the caller omits them, so a
        // re-ensure does not wipe metadata for a backfill that only wants to
        // bind owner edges.
        let effective_name = non_empty_string(Some(name.to_string()))
            .or_else(|| existing_bot.as_ref().and_then(|bot| bot.capabilities.name.clone()))
            .unwrap_or_else(|| bot_uuid.to_string());
        let effective_summary = non_empty_string(summary.map(|s| s.to_string())).or_else(|| {
            existing_bot.as_ref().and_then(|bot| bot.capabilities.summary.clone())
        });

        let capabilities = BotCapabilities {
            name: Some(effective_name),
            summary: effective_summary,
            visibility: effective_visibility,
            ..Default::default()
        };

        // `register` is an idempotent upsert on both the in-memory and DB
        // stores: it inserts when absent and merges when present.
        self.registry
            .register(bot_uuid.to_string(), capabilities)
            .await?;

        self.bind_created_by_and_owner_edges(bot_uuid, actor_identity)
            .await?;

        Ok(created)
    }
}

#[async_trait]
impl BotOnboardingService for BotOnboarding {
    async fn onboard_bot(&self, command: BotOnboardCommand) -> ServiceResult<BotOnboardResult> {
        let existing_bot = self.registry.get(&command.bot_uuid).await;
        let actor_kind = existing_bot
            .as_ref()
            .map(|bot| bot.actor_kind)
            .unwrap_or(ActorKind::Bot);
        let existing_caps = existing_bot.map(|bot| bot.capabilities);
        let first_onboard = !self.registry.has_been_onboarded(&command.bot_uuid).await;
        let effective_name =
            Self::merge_name(Some(command.name), existing_caps.as_ref(), first_onboard)?;
        let effective_summary = Self::merge_summary(command.summary, existing_caps.as_ref());
        let (binding_channels, binding_results, unbound) = self
            .process_binding_channels(
                &command.bot_uuid,
                command.binding_channels.as_ref(),
                self.binding_enabled,
                existing_caps.as_ref(),
            )
            .await;
        let effective_visibility = self.effective_visibility(existing_caps.as_ref());
        let capabilities = BotCapabilities {
            name: effective_name.clone(),
            summary: effective_summary,
            domains: Self::merge_vec(
                command.domains,
                existing_caps.as_ref().map(|caps| &caps.domains),
            ),
            skills: Self::merge_vec(
                command.skills,
                existing_caps.as_ref().map(|caps| &caps.skills),
            ),
            scopes: Self::merge_vec(
                command.scopes,
                existing_caps.as_ref().map(|caps| &caps.scopes),
            ),
            binding_channels,
            visibility: effective_visibility,
            agent_code: command.agent_code.or_else(|| existing_caps.as_ref().and_then(|caps| caps.agent_code.clone())),
            agent_token: command.agent_token,
            ..Default::default()
        };

        self.save_capabilities(&command.bot_uuid, &capabilities)
            .await?;
        self.bind_created_by_and_owner_edges(&command.bot_uuid, command.actor_identity.as_ref())
            .await?;

        Ok(BotOnboardResult {
            bot_uuid: command.bot_uuid,
            onboarded: true,
            name: effective_name,
            message: None,
            binding_results,
            unbound,
            capabilities: Some(capabilities),
            actor_kind,
        })
    }

    async fn admin_onboard_bot(
        &self,
        command: AdminBotOnboardCommand,
    ) -> ServiceResult<BotOnboardResult> {
        let Some(existing_bot) = self.registry.get(&command.bot_uuid).await else {
            return Ok(Self::not_onboarded_result(command.bot_uuid));
        };

        let actor_kind = existing_bot.actor_kind;
        let existing_caps = existing_bot.capabilities;
        let first_onboard = !self.registry.has_been_onboarded(&command.bot_uuid).await;
        let effective_name = Self::merge_name(command.name, Some(&existing_caps), first_onboard)?;
        let effective_summary = Self::merge_summary(command.summary, Some(&existing_caps));
        let (binding_channels, binding_results, unbound) = self
            .process_binding_channels(
                &command.bot_uuid,
                command.binding_channels.as_ref(),
                true,
                Some(&existing_caps),
            )
            .await;
        let effective_visibility = self.effective_visibility(Some(&existing_caps));
        let capabilities = BotCapabilities {
            name: effective_name.clone(),
            summary: effective_summary,
            domains: Self::merge_vec(command.domains, Some(&existing_caps.domains)),
            skills: Self::merge_vec(command.skills, Some(&existing_caps.skills)),
            scopes: Self::merge_vec(command.scopes, Some(&existing_caps.scopes)),
            binding_channels,
            visibility: effective_visibility,
            ..Default::default()
        };

        // Existing-only profile write (bot-avatar plan Task 5): the merged
        // capabilities and the avatar transition commit in one write that
        // never creates or resurrects a row. A target that slipped away
        // between the precheck and this write keeps the legacy
        // `onboarded=false` outcome and persists nothing.
        let profile_write = self
            .registry
            .update_profile_existing(
                &command.bot_uuid,
                ExistingProfileUpdate {
                    capabilities: capabilities.clone(),
                    avatar: command.avatar,
                },
            )
            .await?;
        if profile_write == ProfileWrite::Missing {
            return Ok(Self::not_onboarded_result(command.bot_uuid));
        }

        self.bind_created_by_and_owner_edges(&command.bot_uuid, command.actor_identity.as_ref())
            .await?;

        Ok(BotOnboardResult {
            bot_uuid: command.bot_uuid,
            onboarded: true,
            name: effective_name,
            message: None,
            binding_results,
            unbound,
            capabilities: Some(capabilities),
            actor_kind,
        })
    }

    async fn ensure_bot(&self, command: EnsureBotCommand) -> ServiceResult<EnsureBotResult> {
        let created = self
            .ensure_bot_inner(
                &command.bot_uuid,
                &command.name,
                command.summary.as_deref(),
                &command.visibility,
                command.actor_identity.as_ref(),
            )
            .await?;
        Ok(EnsureBotResult {
            bot_uuid: command.bot_uuid,
            ensured: true,
            created,
        })
    }
}

fn non_empty_string(value: Option<String>) -> Option<String> {
    value.filter(|value| !value.trim().is_empty())
}

#[cfg(test)]
#[path = "onboarding_tests.rs"]
mod tests;
