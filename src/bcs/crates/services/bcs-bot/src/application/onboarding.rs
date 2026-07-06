//! Bot onboarding use-case implementation.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, AdminBotOnboardCommand, BindingChannels, BotCapabilities, BotOnboardCommand,
    BotOnboardResult, BotOnboardingService, BotRegistryCoreService, OnboardActorIdentity,
    RelationCoreService, ServiceError, ServiceResult,
};
use serde_json::Value;

/// Bot onboarding application service backed by registry and relation services.
pub struct BotOnboarding {
    registry: Arc<dyn BotRegistryCoreService>,
    relation: Arc<dyn RelationCoreService>,
    binding_enabled: bool,
    default_visibility: Option<String>,
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
        }
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
            .await
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
            agent_code: command.agent_code,
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
            return Ok(BotOnboardResult {
                bot_uuid: command.bot_uuid,
                onboarded: false,
                name: None,
                message: Some("Bot 未在协作网络注册，请尝试重启".to_string()),
                binding_results: HashMap::new(),
                unbound: Vec::new(),
                capabilities: None,
                actor_kind: ActorKind::Bot,
            });
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
}

fn non_empty_string(value: Option<String>) -> Option<String> {
    value.filter(|value| !value.trim().is_empty())
}

#[cfg(test)]
mod tests {
    use std::collections::{HashMap, HashSet};
    use std::sync::Arc;

    use async_trait::async_trait;
    use bcs_service_api::{
        ActorKind, ActorStatus, BindingChannel, BindingChannels, BotDynamicStatus,
        BotRegistryCoreService, EnsureHumanResult, EnsureOwnerEdgesResult, OnboardActorIdentity,
        RegisteredBot, RelationCoreService, RelationEdge, ServiceResult, Skill,
    };
    use tempfile::TempDir;
    use tokio::sync::Mutex;

    use super::*;
    use crate::BotCore;

    #[derive(Default)]
    struct RecordingRelationCoreService {
        owner_edges: Mutex<Vec<(String, String, String)>>,
    }

    #[async_trait]
    impl RelationCoreService for RecordingRelationCoreService {
        async fn upsert_edge(&self, _edge: RelationEdge) -> ServiceResult<()> {
            Ok(())
        }

        async fn delete_edge(&self, _from_id: &str, _to_id: &str, _env: &str) -> ServiceResult<()> {
            Ok(())
        }

        async fn get_edge(
            &self,
            _from_id: &str,
            _to_id: &str,
            _env: &str,
        ) -> ServiceResult<Option<RelationEdge>> {
            Ok(None)
        }

        async fn ensure_owner_edges(
            &self,
            human_id: &str,
            bot_id: &str,
            env: &str,
        ) -> ServiceResult<()> {
            self.owner_edges.lock().await.push((
                human_id.to_string(),
                bot_id.to_string(),
                env.to_string(),
            ));
            Ok(())
        }

        async fn ensure_owner_edges_counted(
            &self,
            human_id: &str,
            bot_id: &str,
            env: &str,
        ) -> ServiceResult<EnsureOwnerEdgesResult> {
            self.ensure_owner_edges(human_id, bot_id, env).await?;
            Ok(EnsureOwnerEdgesResult::default())
        }

        async fn add_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
            Ok(())
        }

        async fn remove_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
            Ok(())
        }

        async fn remove_all_friend_edges(&self, _actor_id: &str, _env: &str) -> ServiceResult<()> {
            Ok(())
        }

        async fn add_relation_edge(
            &self,
            _caller: &str,
            _target: &str,
            _env: &str,
        ) -> ServiceResult<()> {
            Ok(())
        }

        async fn list_friends_via_relation(
            &self,
            _actor_id: &str,
            _env: &str,
        ) -> ServiceResult<Vec<String>> {
            Ok(Vec::new())
        }
    }

    struct StaticRegistry {
        bots: Mutex<HashMap<String, RegisteredBot>>,
        binding_index: Mutex<HashMap<(String, String), String>>,
        onboarded: Mutex<HashSet<String>>,
        saved_created_by: Mutex<Vec<(String, String, bool)>>,
    }

    impl StaticRegistry {
        fn new(bots: Vec<RegisteredBot>) -> Self {
            Self::with_onboarded(bots, [])
        }

        fn with_onboarded<const N: usize>(bots: Vec<RegisteredBot>, onboarded: [&str; N]) -> Self {
            let mut bot_map = HashMap::new();
            let mut binding_index = HashMap::new();
            for bot in bots {
                if let Some(bindings) = bot.capabilities.binding_channels.as_ref() {
                    for (channel, binding) in bindings {
                        binding_index.insert(
                            (channel.clone(), binding.binding_key.clone()),
                            bot.bot_uuid.clone(),
                        );
                    }
                }
                bot_map.insert(bot.bot_uuid.clone(), bot);
            }

            Self {
                bots: Mutex::new(bot_map),
                binding_index: Mutex::new(binding_index),
                onboarded: Mutex::new(
                    onboarded
                        .into_iter()
                        .map(|bot_id| bot_id.to_string())
                        .collect(),
                ),
                saved_created_by: Mutex::new(Vec::new()),
            }
        }

        async fn created_by(&self, bot_id: &str) -> Option<String> {
            self.bots
                .lock()
                .await
                .get(bot_id)
                .and_then(|bot| bot.created_by.clone())
        }

        async fn saved_created_by_calls(&self) -> Vec<(String, String, bool)> {
            self.saved_created_by.lock().await.clone()
        }
    }

    #[async_trait]
    impl BotRegistryCoreService for StaticRegistry {
        async fn register(
            &self,
            bot_id: String,
            capabilities: BotCapabilities,
        ) -> ServiceResult<()> {
            self.bots
                .lock()
                .await
                .insert(bot_id.clone(), bot(bot_id, capabilities));
            Ok(())
        }

        async fn update_status(&self, _bot_id: &str, _status: BotDynamicStatus) -> bool {
            false
        }

        async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
            self.bots.lock().await.get(bot_id).cloned()
        }

        async fn get_agent_credentials(
            &self,
            _bot_id: &str,
        ) -> Option<bcs_service_api::AgentCredentials> {
            None
        }

        async fn list_active(&self) -> Vec<RegisteredBot> {
            self.bots.lock().await.values().cloned().collect()
        }

        async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn unregister(&self, _bot_id: &str) -> bool {
            false
        }

        async fn cleanup_expired(&self) {}

        async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
            None
        }

        async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
            if let Some(bot) = self.bots.lock().await.get_mut(bot_id) {
                bot.capabilities = caps.clone();
            }
            self.onboarded.lock().await.insert(bot_id.to_string());
            let mut index = self.binding_index.lock().await;
            index.retain(|_, indexed_bot_id| indexed_bot_id != bot_id);
            if let Some(bindings) = caps.binding_channels.as_ref() {
                for (channel, binding) in bindings {
                    index.insert(
                        (channel.clone(), binding.binding_key.clone()),
                        bot_id.to_string(),
                    );
                }
            }
            Ok(())
        }

        async fn update_visibility(&self, _bot_id: &str, _visibility: &str) -> ServiceResult<()> {
            Ok(())
        }

        async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
            Ok(())
        }

        async fn ensure_human_actor(
            &self,
            staff_no: &str,
            nick_name: &str,
        ) -> ServiceResult<EnsureHumanResult> {
            let human_id = format!("human_{}", staff_no);
            self.bots
                .lock()
                .await
                .entry(human_id.clone())
                .or_insert_with(|| RegisteredBot {
                    bot_uuid: human_id,
                    capabilities: BotCapabilities {
                        name: Some(nick_name.to_string()),
                        visibility: "protected".to_string(),
                        ..Default::default()
                    },
                    dynamic_status: BotDynamicStatus::default(),
                    env: None,
                    created_by: Some(staff_no.to_string()),
                    actor_kind: ActorKind::Human,
                    status: ActorStatus::Online,
                });
            Ok(EnsureHumanResult { created: true })
        }

        async fn has_been_onboarded(&self, bot_id: &str) -> bool {
            self.onboarded.lock().await.contains(bot_id)
        }

        async fn save_created_by(
            &self,
            bot_id: &str,
            created_by: &str,
            overwrite: bool,
        ) -> ServiceResult<()> {
            self.saved_created_by.lock().await.push((
                bot_id.to_string(),
                created_by.to_string(),
                overwrite,
            ));
            if let Some(bot) = self.bots.lock().await.get_mut(bot_id) {
                if overwrite || bot.created_by.is_none() {
                    bot.created_by = Some(created_by.to_string());
                }
            }
            Ok(())
        }

        async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
            Ok(())
        }

        async fn load_token(&self, _bot_id: &str) -> Option<String> {
            None
        }

        async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
            None
        }

        async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
            Err(())
        }

        async fn reconnect_streaming(
            &self,
            _existing_token: String,
        ) -> Result<(String, String), ()> {
            Err(())
        }

        async fn disconnect_streaming(&self, _bot_id: &str) {}

        async fn is_connected(&self, _bot_id: &str) -> bool {
            false
        }

        async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
            Err(())
        }

        async fn list_connected(&self) -> Vec<String> {
            Vec::new()
        }

        async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

        async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
            token
        }

        async fn find_bot_by_binding_channel(
            &self,
            channel: &str,
            binding_key: &str,
        ) -> Option<String> {
            self.binding_index
                .lock()
                .await
                .get(&(channel.to_string(), binding_key.to_string()))
                .cloned()
        }
    }

    fn bot(bot_uuid: impl Into<String>, capabilities: BotCapabilities) -> RegisteredBot {
        RegisteredBot {
            bot_uuid: bot_uuid.into(),
            capabilities,
            dynamic_status: BotDynamicStatus::default(),
            env: None,
            created_by: None,
            actor_kind: ActorKind::Bot,
            status: ActorStatus::Online,
        }
    }

    fn binding(key: &str) -> BindingChannels {
        let mut bindings = BindingChannels::new();
        bindings.insert(
            "antding".to_string(),
            BindingChannel {
                binding_key: key.to_string(),
            },
        );
        bindings
    }

    fn command(bot_uuid: &str) -> BotOnboardCommand {
        BotOnboardCommand {
            bot_uuid: bot_uuid.to_string(),
            name: "Bot".to_string(),
            summary: Some("summary".to_string()),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            binding_channels: None,
            agent_code: None,
            agent_token: None,
            actor_identity: None,
        }
    }

    #[tokio::test]
    async fn partial_update_preserves_existing_fields() {
        let registry = Arc::new(StaticRegistry::new(vec![bot(
            "target",
            BotCapabilities::default(),
        )]));
        let service = BotOnboarding::new(
            registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        let mut first = command("target");
        first.name = "OriginalName".to_string();
        first.summary = Some("Original summary".to_string());
        first.domains = vec!["backend".to_string(), "infra".to_string()];
        first.skills = vec![Skill::new("rust"), Skill::new("python")];
        first.scopes = vec!["internal".to_string()];
        service.onboard_bot(first).await.unwrap();

        let mut second = command("target");
        second.name = "UpdatedName".to_string();
        second.summary = Some("Updated summary".to_string());
        second.domains = Vec::new();
        second.skills = Vec::new();
        second.scopes = Vec::new();
        let result = service.onboard_bot(second).await.unwrap();

        let stored = registry.get("target").await.unwrap();
        assert_eq!(result.name.as_deref(), Some("UpdatedName"));
        assert_eq!(stored.capabilities.name.as_deref(), Some("UpdatedName"));
        assert_eq!(
            stored.capabilities.summary.as_deref(),
            Some("Updated summary")
        );
        assert_eq!(
            stored.capabilities.domains,
            vec!["backend".to_string(), "infra".to_string()]
        );
        assert_eq!(stored.capabilities.skills.len(), 2);
        assert_eq!(stored.capabilities.scopes, vec!["internal".to_string()]);
    }

    #[tokio::test]
    async fn partial_update_empty_name_preserves_existing() {
        let registry = Arc::new(StaticRegistry::with_onboarded(
            vec![bot(
                "target",
                BotCapabilities {
                    name: Some("KeepThisName".to_string()),
                    summary: Some("old summary".to_string()),
                    ..Default::default()
                },
            )],
            ["target"],
        ));
        let service = BotOnboarding::new(
            registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        let mut cmd = command("target");
        cmd.name = String::new();
        cmd.summary = Some("new summary".to_string());
        let result = service.onboard_bot(cmd).await.unwrap();

        let stored = registry.get("target").await.unwrap();
        assert_eq!(result.name.as_deref(), Some("KeepThisName"));
        assert_eq!(stored.capabilities.name.as_deref(), Some("KeepThisName"));
        assert_eq!(stored.capabilities.summary.as_deref(), Some("new summary"));
    }

    #[tokio::test]
    async fn first_onboard_empty_name_returns_error() {
        let registry = Arc::new(StaticRegistry::new(vec![bot(
            "target",
            BotCapabilities::default(),
        )]));
        let service = BotOnboarding::new(
            registry,
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        let mut cmd = command("target");
        cmd.name = String::new();
        let result = service.onboard_bot(cmd).await;

        assert!(matches!(
            result,
            Err(ServiceError::InvalidOperation { message, .. })
                if message == "name is required for first onboard"
        ));
    }

    #[tokio::test]
    async fn first_onboard_without_summary_leaves_summary_empty() {
        let registry = Arc::new(StaticRegistry::new(vec![bot(
            "target",
            BotCapabilities::default(),
        )]));
        let service = BotOnboarding::new(
            registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        let mut cmd = command("target");
        cmd.name = "MyBot".to_string();
        cmd.summary = None;
        service.onboard_bot(cmd).await.unwrap();

        let stored = registry.get("target").await.unwrap();
        assert_eq!(stored.capabilities.name.as_deref(), Some("MyBot"));
        assert_eq!(stored.capabilities.summary, None);
    }

    #[tokio::test]
    async fn binding_conflict_excludes_conflicted_channel() {
        let registry = Arc::new(StaticRegistry::new(vec![
            bot("target", BotCapabilities::default()),
            bot(
                "other",
                BotCapabilities {
                    name: Some("Other Bot".to_string()),
                    binding_channels: Some(binding("same-key")),
                    ..Default::default()
                },
            ),
        ]));
        let service = BotOnboarding::new(
            registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        let mut cmd = command("target");
        cmd.binding_channels = Some(binding("same-key"));
        let result = service.onboard_bot(cmd).await.unwrap();

        assert_eq!(result.binding_results["antding"]["status"], "conflict");
        assert!(result.binding_results["antding"]["message"]
            .as_str()
            .unwrap()
            .contains("Other Bot"));
        assert!(registry
            .get("target")
            .await
            .unwrap()
            .capabilities
            .binding_channels
            .is_none());
    }

    #[tokio::test]
    async fn binding_unbound_reports_removed_channels_and_keeps_successful_bindings() {
        let mut old_bindings = binding("old-key");
        old_bindings.insert(
            "wechat".to_string(),
            BindingChannel {
                binding_key: "wechat-key".to_string(),
            },
        );
        let registry = Arc::new(StaticRegistry::new(vec![bot(
            "target",
            BotCapabilities {
                binding_channels: Some(old_bindings),
                ..Default::default()
            },
        )]));
        let service = BotOnboarding::new(
            registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        let mut cmd = command("target");
        cmd.binding_channels = Some(binding("new-key"));
        let result = service.onboard_bot(cmd).await.unwrap();

        assert_eq!(result.unbound, vec!["wechat: wechat-key".to_string()]);
        let stored = registry.get("target").await.unwrap();
        assert_eq!(
            stored
                .capabilities
                .binding_channels
                .unwrap()
                .get("antding")
                .unwrap()
                .binding_key,
            "new-key"
        );
    }

    #[tokio::test]
    async fn binding_disabled_preserves_existing_bindings() {
        let registry = Arc::new(StaticRegistry::new(vec![bot(
            "target",
            BotCapabilities {
                binding_channels: Some(binding("old-key")),
                ..Default::default()
            },
        )]));
        let service = BotOnboarding::new(
            registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            false,
            None,
        );

        let mut cmd = command("target");
        cmd.binding_channels = Some(binding("new-key"));
        let result = service.onboard_bot(cmd).await.unwrap();

        assert!(result.binding_results.is_empty());
        let stored = registry.get("target").await.unwrap();
        assert_eq!(
            stored
                .capabilities
                .binding_channels
                .unwrap()
                .get("antding")
                .unwrap()
                .binding_key,
            "old-key"
        );
    }

    #[tokio::test]
    async fn binding_channels_absent_preserves_existing_and_reports_no_unbound() {
        let registry = Arc::new(StaticRegistry::with_onboarded(
            vec![bot(
                "target",
                BotCapabilities {
                    binding_channels: Some(binding("old-key")),
                    ..Default::default()
                },
            )],
            ["target"],
        ));
        let service = BotOnboarding::new(
            registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        let mut cmd = command("target");
        cmd.binding_channels = None;
        let result = service.onboard_bot(cmd).await.unwrap();

        assert!(result.unbound.is_empty());
        assert!(result.binding_results.is_empty());
        let stored = registry.get("target").await.unwrap();
        assert_eq!(
            stored
                .capabilities
                .binding_channels
                .unwrap()
                .get("antding")
                .unwrap()
                .binding_key,
            "old-key"
        );
    }

    #[tokio::test]
    async fn visibility_prefers_existing_then_configured_default_then_protected() {
        let existing_registry = Arc::new(StaticRegistry::new(vec![bot(
            "existing",
            BotCapabilities {
                visibility: "public".to_string(),
                ..Default::default()
            },
        )]));
        let existing_service = BotOnboarding::new(
            existing_registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            Some("private".to_string()),
        );
        existing_service
            .onboard_bot(command("existing"))
            .await
            .unwrap();
        assert_eq!(
            existing_registry
                .get("existing")
                .await
                .unwrap()
                .capabilities
                .visibility,
            "public"
        );

        let default_registry = Arc::new(StaticRegistry::new(vec![bot(
            "defaulted",
            BotCapabilities {
                visibility: String::new(),
                ..Default::default()
            },
        )]));
        let default_service = BotOnboarding::new(
            default_registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            Some("private".to_string()),
        );
        default_service
            .onboard_bot(command("defaulted"))
            .await
            .unwrap();
        assert_eq!(
            default_registry
                .get("defaulted")
                .await
                .unwrap()
                .capabilities
                .visibility,
            "private"
        );

        let fallback_registry = Arc::new(StaticRegistry::new(vec![bot(
            "fallback",
            BotCapabilities {
                visibility: String::new(),
                ..Default::default()
            },
        )]));
        let fallback_service = BotOnboarding::new(
            fallback_registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );
        fallback_service
            .onboard_bot(command("fallback"))
            .await
            .unwrap();
        assert_eq!(
            fallback_registry
                .get("fallback")
                .await
                .unwrap()
                .capabilities
                .visibility,
            "protected"
        );
    }

    #[tokio::test]
    async fn admin_onboard_preserves_existing_name_and_summary_when_missing_or_empty() {
        let registry = Arc::new(StaticRegistry::with_onboarded(
            vec![bot(
                "target",
                BotCapabilities {
                    name: Some("Existing Bot".to_string()),
                    summary: Some("Existing summary".to_string()),
                    domains: vec!["existing-domain".to_string()],
                    skills: vec![Skill::new("existing-skill")],
                    scopes: vec!["existing-scope".to_string()],
                    visibility: "public".to_string(),
                    ..Default::default()
                },
            )],
            ["target"],
        ));
        let service = BotOnboarding::new(
            registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        let result = service
            .admin_onboard_bot(AdminBotOnboardCommand {
                bot_uuid: "target".to_string(),
                name: Some(String::new()),
                summary: None,
                domains: vec!["ops".to_string()],
                skills: Vec::new(),
                scopes: Vec::new(),
                binding_channels: None,
                actor_identity: None,
            })
            .await
            .unwrap();

        assert_eq!(result.name.as_deref(), Some("Existing Bot"));
        let stored = registry.get("target").await.unwrap();
        assert_eq!(stored.capabilities.name.as_deref(), Some("Existing Bot"));
        assert_eq!(
            stored.capabilities.summary.as_deref(),
            Some("Existing summary")
        );
        assert_eq!(stored.capabilities.domains, vec!["ops".to_string()]);
        assert_eq!(stored.capabilities.skills.len(), 1);
        assert_eq!(
            stored.capabilities.scopes,
            vec!["existing-scope".to_string()]
        );
    }

    #[tokio::test]
    async fn first_admin_onboard_without_name_returns_error() {
        let registry = Arc::new(StaticRegistry::new(vec![bot(
            "target",
            BotCapabilities::default(),
        )]));
        let service = BotOnboarding::new(
            registry,
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        let result = service
            .admin_onboard_bot(AdminBotOnboardCommand {
                bot_uuid: "target".to_string(),
                name: None,
                summary: None,
                domains: Vec::new(),
                skills: Vec::new(),
                scopes: Vec::new(),
                binding_channels: None,
                actor_identity: None,
            })
            .await;

        assert!(matches!(
            result,
            Err(ServiceError::InvalidOperation { message, .. })
                if message == "name is required for first onboard"
        ));
    }

    #[tokio::test]
    async fn owner_binding_overwrites_only_when_bot_uuid_ends_with_staff_no() {
        let relation = Arc::new(RecordingRelationCoreService::default());
        let registry = Arc::new(StaticRegistry::new(vec![
            bot(
                "bot-alice",
                BotCapabilities {
                    visibility: "protected".to_string(),
                    ..Default::default()
                },
            ),
            bot(
                "bot-bob",
                BotCapabilities {
                    visibility: "protected".to_string(),
                    ..Default::default()
                },
            ),
        ]));
        registry
            .save_created_by("bot-bob", "existing-owner", true)
            .await
            .unwrap();
        let service = BotOnboarding::new(registry.clone(), relation.clone(), true, None);

        let mut alice = command("bot-alice");
        alice.actor_identity = Some(OnboardActorIdentity {
            staff_no: "alice".to_string(),
            nick_name: Some("Alice".to_string()),
        });
        service.onboard_bot(alice).await.unwrap();

        let mut bob = command("bot-bob");
        bob.actor_identity = Some(OnboardActorIdentity {
            staff_no: "alice".to_string(),
            nick_name: Some("Alice".to_string()),
        });
        service.onboard_bot(bob).await.unwrap();

        let calls = registry.saved_created_by_calls().await;
        assert!(calls.contains(&("bot-alice".to_string(), "alice".to_string(), true)));
        assert!(calls.contains(&("bot-bob".to_string(), "alice".to_string(), false)));
        assert_eq!(
            registry.created_by("bot-alice").await.as_deref(),
            Some("alice")
        );
        assert_eq!(
            registry.created_by("bot-bob").await.as_deref(),
            Some("existing-owner")
        );
        assert_eq!(relation.owner_edges.lock().await.len(), 2);
    }

    #[tokio::test]
    async fn works_with_real_registry_for_basic_onboard() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        registry
            .register(
                "real-bot".to_string(),
                BotCapabilities {
                    visibility: "public".to_string(),
                    ..Default::default()
                },
            )
            .await
            .unwrap();
        let service = BotOnboarding::new(
            registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        service.onboard_bot(command("real-bot")).await.unwrap();

        let stored = registry.get("real-bot").await.unwrap();
        assert_eq!(stored.capabilities.name.as_deref(), Some("Bot"));
        assert_eq!(stored.capabilities.visibility, "public");
    }

    #[tokio::test]
    async fn real_registry_partial_update_persists_merged_fields() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        registry
            .register(
                "real-bot".to_string(),
                BotCapabilities {
                    visibility: "public".to_string(),
                    ..Default::default()
                },
            )
            .await
            .unwrap();
        let service = BotOnboarding::new(
            registry.clone(),
            Arc::new(RecordingRelationCoreService::default()),
            true,
            None,
        );

        let mut first = command("real-bot");
        first.name = "DiskBot".to_string();
        first.summary = Some("Disk summary".to_string());
        first.domains = vec!["backend".to_string()];
        first.skills = vec![Skill::new("rust")];
        first.scopes = vec!["internal".to_string()];
        service.onboard_bot(first).await.unwrap();

        let mut second = command("real-bot");
        second.name = "DiskBotV2".to_string();
        second.summary = None;
        second.domains = Vec::new();
        second.skills = Vec::new();
        second.scopes = Vec::new();
        service.onboard_bot(second).await.unwrap();

        let memory = registry.get("real-bot").await.unwrap().capabilities;
        let disk = registry.load_from_storage("real-bot").await.unwrap();

        for caps in [memory, disk] {
            assert_eq!(caps.name.as_deref(), Some("DiskBotV2"));
            assert_eq!(caps.summary.as_deref(), Some("Disk summary"));
            assert_eq!(caps.domains, vec!["backend".to_string()]);
            assert_eq!(caps.skills.len(), 1);
            assert_eq!(caps.scopes, vec!["internal".to_string()]);
            assert_eq!(caps.visibility, "public");
        }
    }
}
