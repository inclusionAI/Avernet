//! Core contract for Bot control-plane reads, updates, and Provider hydration.

use async_trait::async_trait;

use crate::types::{
    BotCandidateReadQuery, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRecord, ServiceResult,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotControlPlaneProvider {
    pub provider_id: String,
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotControlPlaneView {
    pub record: BotControlPlaneRecord,
    pub provider: Option<BotControlPlaneProvider>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotControlPlaneCandidate {
    pub bot: BotControlPlaneView,
    pub is_friend: bool,
}

#[async_trait]
pub trait BotControlPlaneCoreService: Send + Sync {
    async fn get_record(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>>;

    async fn get(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneView>>;

    async fn get_by_ids(
        &self,
        bot_ids: &[String],
        env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneView>>;

    async fn list_candidates(
        &self,
        query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)>;

    async fn list_by_creator(
        &self,
        query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>>;

    async fn patch(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>>;
}
