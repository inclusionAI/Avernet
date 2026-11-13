//! Authorized OpenAPI V1 candidate search.
//!
//! Recall and authorization are separate boundaries. The worker-profile
//! provider may return candidates outside the viewer's visibility (that is
//! why the authorized branch asks for `AllVisibilities`); the authoritative
//! control plane re-checks environment, physical-Bot identity, self exclusion,
//! viewer dimension and approved edges before the registry hydrates rows.
//!
//! The semantic branch is the authorized subset of ONE bounded recall
//! (at most [`MAX_RECOMMENDED_CANDIDATES`]) in provider order. It is not a
//! full-directory top-N and never requests a second page. The name fallback
//! runs only when there is no recall, the provider fails, or nothing survives
//! authorization; control-plane and strict registry failures propagate.

use std::collections::{HashMap, HashSet};

use tracing::warn;

use bcs_service_api::core::{
    AuthorizedCandidateSearchQuery, WorkerRecommendCommand, WorkerRecommendation,
    WorkerRecommendationScope,
};
use bcs_service_api::{
    ActorKind, BotCandidateSearchCoreResult, BotCandidateSearchMode, BotCandidateVisibility,
    BotControlPlaneCoreService, BotSearchCandidateQuery, ServiceError, ServiceResult,
};

use super::BotCandidateSearchCore;

/// BCSFuse recommend protocol limit: one recall call and one result page are
/// bounded to 20 candidates. The task does not expand the protocol.
const MAX_RECOMMENDED_CANDIDATES: usize = 20;

impl BotCandidateSearchCore {
    pub(super) async fn search_authorized(
        &self,
        query: AuthorizedCandidateSearchQuery,
    ) -> ServiceResult<BotCandidateSearchCoreResult> {
        let normalized_query = query.query.trim();
        if normalized_query.is_empty() {
            return Ok(BotCandidateSearchCoreResult {
                hits: Vec::new(),
                mode: BotCandidateSearchMode::EmptyQuery,
            });
        }

        let Some(control_plane) = self.control_plane.as_deref() else {
            return Err(ServiceError::InternalError(
                "candidate control plane is not configured".into(),
            ));
        };
        let display_limit = query.limit.min(MAX_RECOMMENDED_CANDIDATES);
        let recommend = self
            .worker_profiles
            .recommend_workers(WorkerRecommendCommand {
                query: normalized_query.to_string(),
                top_k: display_limit as u32,
                min_score: self.recommend_min_score,
                scope: recommendation_scope(&query),
            })
            .await;

        let recommend = match recommend {
            Ok(recommend) => recommend,
            Err(error) => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    error = %error,
                    "authorized candidate search: recommendation failed, falling back to name search"
                );
                return self
                    .authorized_name_fallback(control_plane, &query, normalized_query)
                    .await;
            }
        };

        let recalled_ids = dedupe_recommended_ids(&recommend.recommendations);
        if recalled_ids.is_empty() {
            return self
                .authorized_name_fallback(control_plane, &query, normalized_query)
                .await;
        }

        let mut ranking = HashMap::with_capacity(recommend.recommendations.len());
        for recommendation in recommend.recommendations {
            ranking
                .entry(recommendation.worker_id)
                .or_insert((recommendation.score, recommendation.short_profile));
        }

        // Validate the WHOLE deduplicated recall batch: the query limit covers
        // every recalled id, so the control plane never pre-truncates by
        // creation time before authorization.
        let validation_limit = recalled_ids.len() as u64;
        let (authorized, _) = control_plane
            .search_candidate_records(BotSearchCandidateQuery {
                acting_bot_id: query.acting_actor_id.clone(),
                env: query.env.clone(),
                viewer_kind: Some(query.viewer_kind),
                friend_ids: query.friend_ids.clone(),
                bot_uuids: Some(recalled_ids.clone()),
                name: None,
                q: None,
                visibility_filter: None,
                user_visibility: None,
                status: None,
                friendship: None,
                tc_bot: None,
                name_only: false,
                offset: 0,
                limit: validation_limit,
                visibility: query.visibility,
            })
            .await?;

        let allowed: HashSet<&str> = authorized
            .iter()
            .map(|candidate| candidate.bot.bot_id.as_str())
            .collect();
        let recalled_count = recalled_ids.len();
        let authorized_count = authorized.len();
        let display_ids: Vec<String> = recalled_ids
            .into_iter()
            .filter(|id| allowed.contains(id.as_str()))
            .take(display_limit)
            .collect();

        if display_ids.is_empty() {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                recalled = recalled_count,
                authorized = authorized_count,
                viewer_kind = ?query.viewer_kind,
                mode = "name_fallback",
                "authorized candidate search: no recalled candidate survived authorization, falling back to name search"
            );
            return self
                .authorized_name_fallback(control_plane, &query, normalized_query)
                .await;
        }

        // Strict batched backfill in provider order. `try_get_many` propagates
        // every persistence failure; genuinely absent ids are skipped.
        let bots = self.registry.try_get_many(&display_ids).await?;
        let rows = bots
            .into_iter()
            .filter_map(|bot| {
                let is_friend = query.friend_ids.contains(&bot.bot_uuid);
                // A semantic display id always comes from the provider ranking.
                // A missing ranking entry is a broken invariant, never a
                // licence to fabricate a score; skip it loudly in debug builds.
                match ranking.get(&bot.bot_uuid).cloned() {
                    Some((score, short_profile)) => {
                        Some((bot, is_friend, Some(score), short_profile))
                    }
                    None => {
                        debug_assert!(
                            false,
                            "semantic candidate {} is missing from the provider ranking",
                            bot.bot_uuid
                        );
                        None
                    }
                }
            })
            .collect();

        Ok(BotCandidateSearchCoreResult {
            hits: self.enrich_hits(rows).await,
            mode: BotCandidateSearchMode::Semantic,
        })
    }

    async fn authorized_name_fallback(
        &self,
        control_plane: &dyn BotControlPlaneCoreService,
        query: &AuthorizedCandidateSearchQuery,
        normalized_query: &str,
    ) -> ServiceResult<BotCandidateSearchCoreResult> {
        let limit = query.limit.min(MAX_RECOMMENDED_CANDIDATES) as u64;
        // The name filter and the viewer dimension run in the control plane
        // BEFORE storage pagination, so the fallback is bounded and scoped
        // exactly like the authoritative list.
        let (candidates, _) = control_plane
            .search_candidate_records(BotSearchCandidateQuery {
                acting_bot_id: query.acting_actor_id.clone(),
                env: query.env.clone(),
                viewer_kind: Some(query.viewer_kind),
                friend_ids: query.friend_ids.clone(),
                bot_uuids: None,
                name: Some(normalized_query.to_string()),
                q: None,
                visibility_filter: None,
                user_visibility: None,
                status: None,
                friendship: None,
                tc_bot: None,
                name_only: true,
                offset: 0,
                limit,
                visibility: query.visibility,
            })
            .await?;

        if candidates.is_empty() {
            return Ok(BotCandidateSearchCoreResult {
                hits: Vec::new(),
                mode: BotCandidateSearchMode::NameFallback,
            });
        }

        let ids: Vec<String> = candidates
            .iter()
            .map(|candidate| candidate.bot.bot_id.clone())
            .collect();
        let bots = self.registry.try_get_many(&ids).await?;
        let rows = bots
            .into_iter()
            .map(|bot| {
                let is_friend = query.friend_ids.contains(&bot.bot_uuid);
                (bot, is_friend, None, None)
            })
            .collect();

        Ok(BotCandidateSearchCoreResult {
            hits: self.enrich_hits(rows).await,
            mode: BotCandidateSearchMode::NameFallback,
        })
    }
}

fn recommendation_scope(query: &AuthorizedCandidateSearchQuery) -> WorkerRecommendationScope {
    if query.viewer_kind == ActorKind::Human
        || query.visibility == BotCandidateVisibility::Collaboration
    {
        WorkerRecommendationScope::AllVisibilities
    } else {
        WorkerRecommendationScope::LegacyDefault
    }
}

fn dedupe_recommended_ids(recommendations: &[WorkerRecommendation]) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut ids = Vec::new();
    for recommendation in recommendations {
        if seen.insert(recommendation.worker_id.as_str()) {
            ids.push(recommendation.worker_id.clone());
        }
    }
    ids.truncate(MAX_RECOMMENDED_CANDIDATES);
    ids
}
