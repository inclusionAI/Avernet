//! Actor directory use-case implementation.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use tracing::warn;

use bcs_service_api::{
    ActorCapabilitiesView, ActorDirectoryEntry, ActorDirectoryService, ActorListCommand,
    ActorListResult, ActorSearchCommand, ActorSearchContext, ActorSearchResult,
    ActorStatusUpdateCommand, ActorStatusUpdateResult, BotDeliveryTarget, BotRegistryCoreService,
    DynamicStatusResponse, FriendCoreService, RegisteredBot, RelationCoreService, ServiceError,
    ServiceResult, WorkerProfile, WorkerProfileService, WorkerRecommendCommand,
    WorkerRecommendResult,
};

const DEFAULT_RECOMMEND_MIN_SCORE: f64 = 0.0;

/// Actor directory service backed by registry, friend, relation, and optional
/// worker-profile providers.
pub struct ActorDirectory {
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    relation: Arc<dyn RelationCoreService>,
    worker_profiles: Arc<dyn WorkerProfileService>,
    recommend_min_score: f64,
}

impl ActorDirectory {
    pub fn new(
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
        relation: Arc<dyn RelationCoreService>,
    ) -> Self {
        Self {
            registry,
            friend,
            relation,
            worker_profiles: Arc::new(EmptyWorkerProfileService),
            recommend_min_score: DEFAULT_RECOMMEND_MIN_SCORE,
        }
    }

    pub fn with_worker_profiles(mut self, worker_profiles: Arc<dyn WorkerProfileService>) -> Self {
        self.worker_profiles = worker_profiles;
        self
    }

    pub fn with_recommend_min_score(mut self, min_score: f64) -> Self {
        self.recommend_min_score = min_score;
        self
    }

    async fn friend_set_for(&self, actor_id: &str) -> HashSet<String> {
        self.friend
            .list_friends(actor_id)
            .await
            .into_iter()
            .collect()
    }

    async fn actor_entries_from_registry_rows(
        &self,
        rows: Vec<(RegisteredBot, bool)>,
    ) -> Vec<ActorDirectoryEntry> {
        let ids: Vec<String> = rows.iter().map(|(bot, _)| bot.bot_uuid.clone()).collect();
        let tags_by_id = self.worker_tags_for(&ids).await;

        let mut entries = Vec::with_capacity(rows.len());
        for (bot, is_friend) in rows {
            let tags = tags_by_id.get(&bot.bot_uuid).cloned().unwrap_or_default();
            entries.push(
                self.build_actor_entry(bot, is_friend, tags, None, None)
                    .await,
            );
        }
        entries
    }

    async fn fallback_search(
        &self,
        command: &ActorSearchCommand,
        recommend_response: Option<serde_json::Value>,
    ) -> ActorSearchResult {
        let friend_set = self.friend_set_for(&command.current_bot_uuid).await;
        let (bots, _) = self
            .registry
            .list_bots_by_name_and_cooperatable_with(
                command.query.trim(),
                &command.current_bot_uuid,
                command.cooperatable_only,
                &friend_set,
                0,
                command.limit,
            )
            .await;

        let ids: Vec<String> = bots.iter().map(|(bot, _)| bot.bot_uuid.clone()).collect();
        let tags_by_id = self.worker_tags_for(&ids).await;

        let mut entries = Vec::with_capacity(bots.len());
        for (bot, is_friend) in bots {
            let skills_display = {
                let names: Vec<&str> = bot
                    .capabilities
                    .skills
                    .iter()
                    .map(|skill| skill.name.as_str())
                    .collect();
                format!("bot 能力: {}", names.join(";"))
            };
            let tags = tags_by_id.get(&bot.bot_uuid).cloned().unwrap_or_default();
            entries.push(
                self.build_actor_entry(bot, is_friend, tags, Some(0.0), Some(skills_display))
                    .await,
            );
        }

        ActorSearchResult {
            bots: entries,
            context: ActorSearchContext { recommend_response },
        }
    }

    async fn search_with_worker_profiles(
        &self,
        command: &ActorSearchCommand,
        friend_set: &HashSet<String>,
    ) -> Option<ActorSearchResult> {
        let top_k = command.limit.min(u32::MAX as usize) as u32;
        let recommend = match self
            .worker_profiles
            .recommend_workers(WorkerRecommendCommand {
                query: command.query.trim().to_string(),
                top_k,
                min_score: self.recommend_min_score,
            })
            .await
        {
            Ok(result) => result,
            Err(error) => {
                warn!(
                    error = %error,
                    "actor search: worker profile recommend failed, falling back to registry"
                );
                return None;
            }
        };

        if recommend.recommendations.is_empty() {
            return Some(ActorSearchResult {
                bots: Vec::new(),
                context: ActorSearchContext {
                    recommend_response: Some(recommend.raw_response),
                },
            });
        }

        let mut rows = Vec::with_capacity(recommend.recommendations.len());
        for recommendation in &recommend.recommendations {
            if recommendation.worker_id == command.current_bot_uuid {
                continue;
            }
            let Some(bot) = self.registry.get(&recommendation.worker_id).await else {
                continue;
            };
            let is_friend = friend_set.contains(&bot.bot_uuid);
            if !is_visible_for_actor_search(&bot, command.cooperatable_only, is_friend) {
                continue;
            }
            rows.push((
                bot,
                is_friend,
                recommendation.score,
                recommendation.short_profile.clone(),
            ));
        }

        if rows.is_empty() {
            return Some(ActorSearchResult {
                bots: Vec::new(),
                context: ActorSearchContext {
                    recommend_response: Some(recommend.raw_response),
                },
            });
        }

        let ids: Vec<String> = rows
            .iter()
            .map(|(bot, _, _, _)| bot.bot_uuid.clone())
            .collect();
        let tags_by_id = self.worker_tags_for(&ids).await;

        let mut entries = Vec::with_capacity(rows.len());
        for (bot, is_friend, score, short_profile) in rows {
            let tags = tags_by_id.get(&bot.bot_uuid).cloned().unwrap_or_default();
            entries.push(
                self.build_actor_entry(bot, is_friend, tags, Some(score), short_profile)
                    .await,
            );
        }

        Some(ActorSearchResult {
            bots: entries,
            context: ActorSearchContext {
                recommend_response: Some(recommend.raw_response),
            },
        })
    }

    async fn worker_tags_for(
        &self,
        worker_ids: &[String],
    ) -> HashMap<String, BTreeMap<String, serde_json::Value>> {
        if worker_ids.is_empty() {
            return HashMap::new();
        }

        match self
            .worker_profiles
            .batch_query_worker_profiles(worker_ids)
            .await
        {
            Ok(profiles) => profiles
                .into_iter()
                .map(|profile| (profile.worker_id, profile.tags))
                .collect(),
            Err(error) => {
                warn!(
                    error = %error,
                    "actor directory: worker profile batch query failed, tags will be empty"
                );
                HashMap::new()
            }
        }
    }

    async fn build_actor_entry(
        &self,
        bot: RegisteredBot,
        is_friend: bool,
        tags: BTreeMap<String, serde_json::Value>,
        score: Option<f64>,
        short_profile: Option<String>,
    ) -> ActorDirectoryEntry {
        let is_active = self.registry.is_effectively_online(&bot.bot_uuid).await;
        let status = if is_active { "active" } else { "offline" };
        let is_downlink = matches!(
            self.registry.resolve_delivery_target(&bot.bot_uuid).await,
            Ok(BotDeliveryTarget::HttpProvider { .. })
        );
        let capabilities = actor_capabilities_view(&bot);

        ActorDirectoryEntry {
            bot_uuid: bot.bot_uuid,
            capabilities,
            visibility: bot.capabilities.visibility,
            dynamic_status: DynamicStatusResponse {
                status: status.to_string(),
            },
            is_friend,
            is_downlink,
            tags,
            score,
            short_profile,
        }
    }
}

struct EmptyWorkerProfileService;

#[async_trait]
impl WorkerProfileService for EmptyWorkerProfileService {
    async fn recommend_workers(
        &self,
        _command: WorkerRecommendCommand,
    ) -> ServiceResult<WorkerRecommendResult> {
        Ok(WorkerRecommendResult {
            recommendations: Vec::new(),
            raw_response: serde_json::Value::Null,
        })
    }

    async fn batch_query_worker_profiles(
        &self,
        _worker_ids: &[String],
    ) -> ServiceResult<Vec<WorkerProfile>> {
        Ok(Vec::new())
    }
}

#[async_trait]
impl ActorDirectoryService for ActorDirectory {
    async fn list_actors(&self, command: ActorListCommand) -> ActorListResult {
        let name_filter = command.name.as_deref().unwrap_or("").trim();
        let friend_set = self.friend_set_for(&command.current_bot_uuid).await;
        let (bots, total) = self
            .registry
            .list_bots_by_name_and_cooperatable_with(
                name_filter,
                &command.current_bot_uuid,
                command.cooperatable_only,
                &friend_set,
                command.offset,
                command.limit,
            )
            .await;

        ActorListResult {
            bots: self.actor_entries_from_registry_rows(bots).await,
            total,
        }
    }

    async fn search_actors(&self, command: ActorSearchCommand) -> ActorSearchResult {
        if command.query.trim().is_empty() {
            return ActorSearchResult {
                bots: Vec::new(),
                context: ActorSearchContext {
                    recommend_response: None,
                },
            };
        }

        let friend_set = self.friend_set_for(&command.current_bot_uuid).await;
        if let Some(result) = self
            .search_with_worker_profiles(&command, &friend_set)
            .await
        {
            if !result.bots.is_empty() {
                return result;
            }
            return self
                .fallback_search(&command, result.context.recommend_response)
                .await;
        }

        self.fallback_search(&command, None).await
    }

    async fn update_actor_status_for_caller(
        &self,
        command: ActorStatusUpdateCommand,
    ) -> ServiceResult<ActorStatusUpdateResult> {
        if self.registry.get(&command.actor_id).await.is_none() {
            return Err(ServiceError::BotNotFound(command.actor_id));
        }

        if command.caller_actor_id != command.actor_id {
            let env = bcs_config::resolve_env_str();
            match self
                .relation
                .get_edge(&command.caller_actor_id, &command.actor_id, &env)
                .await?
            {
                Some(edge) if edge.is_creator => {}
                _ => {
                    return Err(ServiceError::Unauthorized(format!(
                        "Caller '{}' is not the actor itself nor a creator of '{}'",
                        command.caller_actor_id, command.actor_id
                    )));
                }
            }
        }

        self.registry
            .update_actor_status(&command.actor_id, command.status)
            .await?;

        Ok(ActorStatusUpdateResult {
            actor_id: command.actor_id,
            status: command.status,
        })
    }
}

fn actor_capabilities_view(bot: &RegisteredBot) -> ActorCapabilitiesView {
    ActorCapabilitiesView {
        name: bot.capabilities.name.clone(),
        summary: bot.capabilities.summary.clone(),
        skills: bot.capabilities.skills.clone(),
        domains: bot.capabilities.domains.clone(),
        scopes: bot.capabilities.scopes.clone(),
    }
}

fn is_visible_for_actor_search(
    bot: &RegisteredBot,
    cooperatable_only: bool,
    is_friend: bool,
) -> bool {
    let visibility = bot.capabilities.visibility.as_str();
    if cooperatable_only {
        visibility == "public" || is_friend
    } else {
        matches!(visibility, "public" | "protected")
    }
}
