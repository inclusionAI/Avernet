//! Narrow persistence contract for V1 Bot control-plane reads and updates.

use std::collections::HashSet;

use async_trait::async_trait;

pub use crate::types::{
    BotCandidateReadQuery, BotCandidateReadRecord, BotCandidateVisibility,
    BotControlPlaneDescriptor, BotControlPlaneDescriptorPatch, BotControlPlaneOwnedQuery,
    BotControlPlanePatch, BotControlPlaneRecord,
};
use crate::ServiceResult;

#[async_trait]
pub trait BotControlPlaneRepoPort: Send + Sync {
    async fn get_control_plane(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>>;

    async fn get_control_plane_by_ids(
        &self,
        bot_ids: &[String],
        env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        let mut seen = HashSet::new();
        let mut records = Vec::new();
        for bot_id in bot_ids {
            if seen.insert(bot_id.as_str()) {
                if let Some(record) = self.get_control_plane(bot_id, env).await? {
                    records.push(record);
                }
            }
        }
        Ok(records)
    }

    async fn list_control_plane_candidates(
        &self,
        query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)>;

    async fn list_control_plane_by_creator(
        &self,
        query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>>;

    async fn patch_control_plane(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneRecord>>;
}
