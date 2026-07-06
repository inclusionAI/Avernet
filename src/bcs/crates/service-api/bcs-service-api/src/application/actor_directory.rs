//! Actor directory use-case contracts shared by delivery adapters and services.

use std::collections::BTreeMap;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::core::{ActorStatus, DynamicStatusResponse, ServiceResult, Skill};

/// Request for listing actors visible to a caller.
#[derive(Debug, Clone, Default)]
pub struct ActorListCommand {
    pub name: Option<String>,
    pub current_bot_uuid: String,
    pub cooperatable_only: bool,
    pub offset: usize,
    pub limit: usize,
}

/// Request for searching actors visible to a caller.
#[derive(Debug, Clone, Default)]
pub struct ActorSearchCommand {
    pub query: String,
    pub current_bot_uuid: String,
    pub cooperatable_only: bool,
    pub limit: usize,
}

/// Request for updating an actor lifecycle status.
#[derive(Debug, Clone)]
pub struct ActorStatusUpdateCommand {
    pub caller_actor_id: String,
    pub actor_id: String,
    pub status: ActorStatus,
}

/// Response for `/actors/list`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActorListResult {
    pub bots: Vec<ActorDirectoryEntry>,
    pub total: usize,
}

/// Response for `/actors/search`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActorSearchResult {
    pub bots: Vec<ActorDirectoryEntry>,
    pub context: ActorSearchContext,
}

/// Search context returned alongside actor results.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActorSearchContext {
    /// Raw bcsfuse recommend response when a worker-profile service is wired.
    /// `None` means recommend is unavailable or the search fell back to registry.
    pub recommend_response: Option<serde_json::Value>,
}

/// Response payload for a successful actor status update.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActorStatusUpdateResult {
    pub actor_id: String,
    pub status: ActorStatus,
}

/// Actor card shape returned by actor directory endpoints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActorDirectoryEntry {
    pub bot_uuid: String,
    pub capabilities: ActorCapabilitiesView,
    pub visibility: String,
    pub dynamic_status: DynamicStatusResponse,
    pub is_friend: bool,
    #[serde(default)]
    pub is_downlink: bool,
    pub tags: BTreeMap<String, serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub score: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub short_profile: Option<String>,
}

/// Capability subset exposed by actor directory endpoints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActorCapabilitiesView {
    pub name: Option<String>,
    pub summary: Option<String>,
    pub skills: Vec<Skill>,
    pub domains: Vec<String>,
    pub scopes: Vec<String>,
}

/// Worker-profile request for semantic actor recommendation.
#[derive(Debug, Clone)]
pub struct WorkerRecommendCommand {
    pub query: String,
    pub top_k: u32,
    pub min_score: f64,
}

/// One worker recommendation from a worker-profile backend such as bcsfuse.
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
    pub raw_response: serde_json::Value,
}

/// Worker profile metadata used to enrich actor directory cards.
#[derive(Debug, Clone, Default)]
pub struct WorkerProfile {
    pub worker_id: String,
    pub tags: BTreeMap<String, serde_json::Value>,
}

/// Optional external worker-profile capability used by actor directory search.
#[async_trait]
pub trait WorkerProfileService: Send + Sync {
    async fn recommend_workers(
        &self,
        command: WorkerRecommendCommand,
    ) -> ServiceResult<WorkerRecommendResult>;

    async fn batch_query_worker_profiles(
        &self,
        worker_ids: &[String],
    ) -> ServiceResult<Vec<WorkerProfile>>;
}

/// Actor directory application service.
#[async_trait]
pub trait ActorDirectoryService: Send + Sync {
    async fn list_actors(&self, command: ActorListCommand) -> ActorListResult;

    async fn search_actors(&self, command: ActorSearchCommand) -> ActorSearchResult;

    async fn update_actor_status_for_caller(
        &self,
        command: ActorStatusUpdateCommand,
    ) -> ServiceResult<ActorStatusUpdateResult>;
}
