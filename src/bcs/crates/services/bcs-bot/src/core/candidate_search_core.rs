//! Shared candidate-search Core implementation.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use tracing::warn;

use bcs_service_api::core::{
    WorkerProfile, WorkerProfileCoreService, WorkerRecommendCommand, WorkerRecommendResult,
};
use bcs_service_api::{
    ActorKind, BotCandidateSearchCoreResult, BotCandidateSearchCoreService, BotCandidateSearchHit,
    BotCandidateSearchMode, BotCandidateSearchQuery, BotCandidateVisibility,
    BotRegistryCoreService, FriendCoreService, LegacyBotCandidateSearchCoreResult, RegisteredBot,
    ServiceResult,
};

/// Local/no-network worker-profile provider used when BCSFuse is not configured.
pub struct EmptyWorkerProfileCoreService;

#[async_trait]
impl WorkerProfileCoreService for EmptyWorkerProfileCoreService {
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

/// Candidate-search policy shared by legacy and V1 application services.
pub struct BotCandidateSearchCore {
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    worker_profiles: Arc<dyn WorkerProfileCoreService>,
    recommend_min_score: f64,
}

impl BotCandidateSearchCore {
    pub fn new(
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
        worker_profiles: Arc<dyn WorkerProfileCoreService>,
        recommend_min_score: f64,
    ) -> Self {
        Self {
            registry,
            friend,
            worker_profiles,
            recommend_min_score,
        }
    }

    async fn search(&self, query: BotCandidateSearchQuery) -> LegacyBotCandidateSearchCoreResult {
        let normalized_query = query.query.trim();
        if normalized_query.is_empty() {
            return LegacyBotCandidateSearchCoreResult {
                result: BotCandidateSearchCoreResult {
                    hits: Vec::new(),
                    mode: BotCandidateSearchMode::EmptyQuery,
                },
                recommend_response: None,
            };
        }

        let friend_ids = self.friend_set_for(&query.acting_actor_id).await;
        let recommend = self
            .worker_profiles
            .recommend_workers(WorkerRecommendCommand {
                query: normalized_query.to_string(),
                top_k: query.limit.min(u32::MAX as usize) as u32,
                min_score: self.recommend_min_score,
            })
            .await;

        match recommend {
            Ok(recommend) => {
                let raw_response = recommend.raw_response;
                let mut recommended_hits = Vec::with_capacity(recommend.recommendations.len());
                for recommendation in recommend.recommendations {
                    if recommendation.worker_id == query.acting_actor_id {
                        continue;
                    }
                    let Some(bot) = self.registry.get(&recommendation.worker_id).await else {
                        continue;
                    };
                    if bot.actor_kind == ActorKind::Human {
                        continue;
                    }
                    let is_friend = friend_ids.contains(&bot.bot_uuid);
                    if !is_visible(&bot, query.visibility, is_friend) {
                        continue;
                    }
                    recommended_hits.push((
                        bot,
                        is_friend,
                        Some(recommendation.score),
                        recommendation.short_profile,
                    ));
                }

                if recommended_hits.is_empty() {
                    return self
                        .name_fallback(&query, normalized_query, &friend_ids, Some(raw_response))
                        .await;
                }

                LegacyBotCandidateSearchCoreResult {
                    result: BotCandidateSearchCoreResult {
                        hits: self.enrich_hits(recommended_hits).await,
                        mode: BotCandidateSearchMode::Semantic,
                    },
                    recommend_response: Some(raw_response),
                }
            }
            Err(error) => {
                warn!(
                    error = %error,
                    "candidate search: worker recommendation failed, falling back to name search"
                );
                self.name_fallback(&query, normalized_query, &friend_ids, None)
                    .await
            }
        }
    }

    async fn friend_set_for(&self, actor_id: &str) -> HashSet<String> {
        self.friend
            .list_friends(actor_id)
            .await
            .into_iter()
            .collect()
    }

    async fn name_fallback(
        &self,
        query: &BotCandidateSearchQuery,
        normalized_query: &str,
        friend_ids: &HashSet<String>,
        recommend_response: Option<serde_json::Value>,
    ) -> LegacyBotCandidateSearchCoreResult {
        let cooperatable_only = query.visibility == BotCandidateVisibility::Collaboration;
        let (rows, _) = self
            .registry
            .list_bots_by_name_and_cooperatable_with(
                normalized_query,
                &query.acting_actor_id,
                cooperatable_only,
                friend_ids,
                0,
                query.limit,
            )
            .await;
        let rows = rows
            .into_iter()
            .filter(|(bot, _)| bot.actor_kind != ActorKind::Human)
            .map(|(bot, is_friend)| (bot, is_friend, None, None))
            .collect();

        LegacyBotCandidateSearchCoreResult {
            result: BotCandidateSearchCoreResult {
                hits: self.enrich_hits(rows).await,
                mode: BotCandidateSearchMode::NameFallback,
            },
            recommend_response,
        }
    }

    async fn enrich_hits(
        &self,
        rows: Vec<(RegisteredBot, bool, Option<f64>, Option<String>)>,
    ) -> Vec<BotCandidateSearchHit> {
        let worker_ids: Vec<String> = rows
            .iter()
            .map(|(bot, _, _, _)| bot.bot_uuid.clone())
            .collect();
        let tags_by_id = self.worker_tags_for(&worker_ids).await;

        rows.into_iter()
            .map(|(bot, is_friend, score, short_profile)| {
                let tags = tags_by_id.get(&bot.bot_uuid).cloned().unwrap_or_default();
                BotCandidateSearchHit {
                    bot,
                    is_friend,
                    tags,
                    score,
                    short_profile,
                }
            })
            .collect()
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
                    "candidate search: worker profile lookup failed, tags will be empty"
                );
                HashMap::new()
            }
        }
    }
}

#[async_trait]
impl BotCandidateSearchCoreService for BotCandidateSearchCore {
    async fn search_candidates(
        &self,
        query: BotCandidateSearchQuery,
    ) -> BotCandidateSearchCoreResult {
        self.search(query).await.result
    }

    async fn search_candidates_for_legacy(
        &self,
        query: BotCandidateSearchQuery,
    ) -> LegacyBotCandidateSearchCoreResult {
        self.search(query).await
    }
}

fn is_visible(bot: &RegisteredBot, visibility: BotCandidateVisibility, is_friend: bool) -> bool {
    match visibility {
        BotCandidateVisibility::Discovery => {
            matches!(bot.capabilities.visibility.as_str(), "public" | "protected")
        }
        BotCandidateVisibility::Collaboration => {
            bot.capabilities.visibility == "public" || is_friend
        }
    }
}
