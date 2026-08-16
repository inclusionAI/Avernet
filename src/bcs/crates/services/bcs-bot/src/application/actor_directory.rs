//! Actor directory use-case implementation.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use tracing::warn;

use bcs_service_api::core::WorkerProfileCoreService;
use bcs_service_api::{
    ActorCapabilitiesView, ActorDirectoryEntry, ActorDirectoryService, ActorListCommand,
    ActorListResult, ActorSearchCommand, ActorSearchContext, ActorSearchResult,
    ActorStatusUpdateCommand, ActorStatusUpdateResult, BotCandidateSearchCoreService,
    BotCandidateSearchMode, BotCandidateSearchQuery, BotCandidateVisibility, BotDeliveryTarget,
    BotRegistryCoreService, DynamicStatusResponse, FriendCoreService, RegisteredBot,
    RelationCoreService, ServiceError, ServiceResult,
};

/// Actor directory service backed by registry, friend, relation, worker-profile,
/// and candidate-search Core services selected by the composition root.
pub struct ActorDirectory {
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    relation: Arc<dyn RelationCoreService>,
    worker_profiles: Arc<dyn WorkerProfileCoreService>,
    candidate_search: Arc<dyn BotCandidateSearchCoreService>,
}

impl ActorDirectory {
    pub fn new(
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
        relation: Arc<dyn RelationCoreService>,
        worker_profiles: Arc<dyn WorkerProfileCoreService>,
        candidate_search: Arc<dyn BotCandidateSearchCoreService>,
    ) -> Self {
        Self {
            registry,
            friend,
            relation,
            worker_profiles,
            candidate_search,
        }
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
        let result = self
            .candidate_search
            .search_candidates_for_legacy(BotCandidateSearchQuery {
                query: command.query.trim().to_string(),
                acting_actor_id: command.current_bot_uuid,
                visibility: if command.cooperatable_only {
                    BotCandidateVisibility::Collaboration
                } else {
                    BotCandidateVisibility::Discovery
                },
                limit: command.limit,
            })
            .await;
        let mode = result.result.mode;
        let mut entries = Vec::with_capacity(result.result.hits.len());
        for hit in result.result.hits {
            let (score, short_profile) = match mode {
                BotCandidateSearchMode::NameFallback => {
                    let skill_names = hit
                        .bot
                        .capabilities
                        .skills
                        .iter()
                        .map(|skill| skill.name.as_str())
                        .collect::<Vec<_>>();
                    (
                        Some(0.0),
                        Some(format!("bot 能力: {}", skill_names.join(";"))),
                    )
                }
                BotCandidateSearchMode::Semantic | BotCandidateSearchMode::EmptyQuery => {
                    (hit.score, hit.short_profile)
                }
            };
            entries.push(
                self.build_actor_entry(hit.bot, hit.is_friend, hit.tags, score, short_profile)
                    .await,
            );
        }

        ActorSearchResult {
            bots: entries,
            context: ActorSearchContext {
                recommend_response: result.recommend_response,
            },
        }
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
