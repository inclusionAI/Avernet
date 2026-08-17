//! Shared contracts for candidate search and worker-profile enrichment.

use std::collections::BTreeMap;

use async_trait::async_trait;

use super::{RegisteredBot, ServiceResult};
use crate::types::BotCandidateVisibility;

/// Normalized input for candidate search.
///
/// `query` is already trimmed by the calling application. `visibility`
/// selects discovery or collaboration visibility policy for the acting Actor.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotCandidateSearchQuery {
    pub query: String,
    pub acting_actor_id: String,
    pub visibility: BotCandidateVisibility,
    pub limit: usize,
}

/// Identifies which search path produced a candidate result.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BotCandidateSearchMode {
    EmptyQuery,
    Semantic,
    NameFallback,
}

/// One normalized candidate hit returned by the shared Core.
#[derive(Debug, Clone)]
pub struct BotCandidateSearchHit {
    pub bot: RegisteredBot,
    pub is_friend: bool,
    pub tags: BTreeMap<String, serde_json::Value>,
    pub score: Option<f64>,
    pub short_profile: Option<String>,
}

/// Normalized candidate-search result shared by application services.
#[derive(Debug, Clone)]
pub struct BotCandidateSearchCoreResult {
    pub hits: Vec<BotCandidateSearchHit>,
    pub mode: BotCandidateSearchMode,
}

/// Candidate-search result used only by the legacy actor-search projection.
///
/// The normalized result is kept separate from the opaque provider response
/// so OpenAPI V1 consumers can depend only on [`BotCandidateSearchCoreResult`].
/// `recommend_response` must never enter an OpenAPI V1 response.
#[derive(Debug, Clone)]
pub struct LegacyBotCandidateSearchCoreResult {
    pub result: BotCandidateSearchCoreResult,
    /// Opaque provider response retained only for the legacy actor-search
    /// compatibility projection. This internal data must never enter an
    /// OpenAPI V1 response.
    pub recommend_response: Option<serde_json::Value>,
}

/// Shared candidate-search business capability.
#[async_trait]
pub trait BotCandidateSearchCoreService: Send + Sync {
    /// Search candidates without exposing provider-specific response data.
    async fn search_candidates(
        &self,
        query: BotCandidateSearchQuery,
    ) -> BotCandidateSearchCoreResult;

    /// Search candidates for the legacy actor-directory compatibility view.
    ///
    /// Only legacy application code may use this entry point. OpenAPI V1 must
    /// call [`Self::search_candidates`] so provider response data stays behind
    /// the legacy boundary.
    async fn search_candidates_for_legacy(
        &self,
        query: BotCandidateSearchQuery,
    ) -> LegacyBotCandidateSearchCoreResult;
}

/// Worker-profile request for semantic candidate recommendation.
#[derive(Debug, Clone)]
pub struct WorkerRecommendCommand {
    pub query: String,
    pub top_k: u32,
    pub min_score: f64,
}

/// One worker recommendation from a worker-profile provider such as BCSFuse.
#[derive(Debug, Clone)]
pub struct WorkerRecommendation {
    pub worker_id: String,
    pub score: f64,
    pub short_profile: Option<String>,
}

/// Result returned by worker semantic recommendation.
#[derive(Debug, Clone)]
pub struct WorkerRecommendResult {
    pub recommendations: Vec<WorkerRecommendation>,
    /// Opaque provider payload for internal Core processing and legacy
    /// compatibility only. Application DTOs must not expose this value.
    pub raw_response: serde_json::Value,
}

/// Worker profile metadata used to enrich candidate hits.
#[derive(Debug, Clone, Default)]
pub struct WorkerProfile {
    pub worker_id: String,
    pub tags: BTreeMap<String, serde_json::Value>,
}

/// External worker-profile capability used by candidate search.
#[async_trait]
pub trait WorkerProfileCoreService: Send + Sync {
    async fn recommend_workers(
        &self,
        command: WorkerRecommendCommand,
    ) -> ServiceResult<WorkerRecommendResult>;

    async fn batch_query_worker_profiles(
        &self,
        worker_ids: &[String],
    ) -> ServiceResult<Vec<WorkerProfile>>;
}
