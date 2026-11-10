//! Authorized candidate search for the OpenAPI V1 viewer perspective.
//!
//! The application authorizes the selected perspective, reads the acting
//! actor's approved Connect/edge friends exactly once per request and hands the
//! complete set to the Core's authorized entry. The Core owns recall,
//! visibility validation and friend markers; this facade only maps its bounded
//! result onto the V1 DTO. An empty query never reads friends and never reaches
//! the Core, and a friend-read or Core failure fails the whole request instead
//! of degrading to a name search.

use std::collections::HashMap;

use bcs_service_api::application::v1::{
    ApplicationError, Bot, BotCandidatePurpose, BotCandidateSearchItem, BotCandidateSearchMode,
    BotCandidateSearchResult, SearchBotCandidates,
};
use bcs_service_api::{
    AuthorizedCandidateSearchQuery, BotCandidateSearchMode as CoreCandidateSearchMode,
    BotCandidateVisibility,
};

use crate::{BotServiceImpl, map_service_error};

/// Fixed OpenAPI V1 display cap for candidate search. The Core independently
/// clamps its own recall/display batch to `MAX_RECOMMENDED_CANDIDATES` (also
/// 20), so this constant is the façade's v1 display cap, not the recall bound.
const CANDIDATE_SEARCH_LIMIT: usize = 20;

impl BotServiceImpl {
    pub(crate) async fn search_candidates_impl(
        &self,
        command: SearchBotCandidates,
    ) -> Result<BotCandidateSearchResult, ApplicationError> {
        let staff_no = Self::human_staff_no(&command.caller)?;
        Self::validate_bot_id(&command.bot_id)?;
        let acting = self
            .authorize_candidate_perspective(&staff_no, &command.bot_id)
            .await?;

        let normalized_query = command
            .query
            .as_deref()
            .unwrap_or_default()
            .trim()
            .to_string();
        if normalized_query.is_empty() {
            return Ok(BotCandidateSearchResult {
                items: Vec::new(),
                search_mode: BotCandidateSearchMode::EmptyQuery,
            });
        }

        // One strict bounded read; the semantic and name-fallback branches
        // share this exact set.
        let friend_ids = self.approved_bot_friend_ids(&command.bot_id).await?;
        let search = self
            .candidate_search
            .search_authorized_candidates(AuthorizedCandidateSearchQuery {
                query: normalized_query,
                acting_actor_id: command.bot_id,
                env: acting.env.clone(),
                viewer_kind: acting.kind,
                visibility: match command.purpose {
                    BotCandidatePurpose::Discovery => BotCandidateVisibility::Discovery,
                    BotCandidatePurpose::Collaboration => BotCandidateVisibility::Collaboration,
                },
                friend_ids,
                limit: CANDIDATE_SEARCH_LIMIT,
            })
            .await
            .map_err(map_service_error)?;

        let search_mode = search.mode;
        let searched_ids = search
            .hits
            .iter()
            .map(|hit| hit.bot.bot_uuid.clone())
            .collect::<Vec<_>>();
        let projected = self
            .project_records(
                self.control_plane
                    .get_by_ids(&searched_ids, &acting.env)
                    .await
                    .map_err(map_service_error)?,
            )
            .await?;
        let mut projected_by_id = projected
            .into_iter()
            .map(|bot| (bot.bot_id().to_string(), bot))
            .collect::<HashMap<_, _>>();
        let mut items = Vec::with_capacity(search.hits.len());
        for hit in search.hits {
            let Some(bot) = projected_by_id.remove(&hit.bot.bot_uuid) else {
                continue;
            };
            let Bot::Physical(bot) = bot else {
                return Err(ApplicationError::internal(
                    "Actor search returned a Human record",
                ));
            };
            let (score, short_profile) = if search_mode == CoreCandidateSearchMode::Semantic {
                (hit.score, hit.short_profile)
            } else {
                (None, None)
            };
            items.push(BotCandidateSearchItem {
                bot,
                is_friend: hit.is_friend,
                tags: hit.tags,
                score,
                short_profile,
            });
        }
        Ok(BotCandidateSearchResult {
            items,
            search_mode: match search_mode {
                CoreCandidateSearchMode::EmptyQuery => BotCandidateSearchMode::EmptyQuery,
                CoreCandidateSearchMode::Semantic => BotCandidateSearchMode::Semantic,
                CoreCandidateSearchMode::NameFallback => BotCandidateSearchMode::NameFallback,
            },
        })
    }
}
