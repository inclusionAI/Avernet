//! Narrow persistence contract for V1 Bot control-plane reads and updates.

use std::collections::HashSet;

use async_trait::async_trait;

pub use crate::types::{
    BotCandidateReadQuery, BotCandidateReadRecord, BotCandidateVisibility,
    BotControlPlaneDescriptor, BotControlPlaneDescriptorPatch, BotControlPlaneOwnedQuery,
    BotControlPlanePatch, BotControlPlaneRecord, BotSearchCandidateQuery, BotTaskModesQuery,
    ControllableCandidateQuery, ControlledBotCandidate, TaskModeMatch,
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

    async fn search_control_plane_candidates(
        &self,
        query: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)>;

    async fn list_control_plane_by_creator(
        &self,
        query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>>;

    /// Mine-union candidate read (bot-authority spec §8.0
    /// point 1, plan §3 frozen contract).
    ///
    /// ONE store snapshot returns the persistent-certified
    /// lightweight candidates for `query.user_id`: the
    /// approved `owner|manager` edge union in the bound
    /// env, role-deduped (owner wins over manager), joined
    /// to live (`is_deleted = 0`) Bot rows filtered by
    /// env/kind/name/status, stably sorted by bot id and
    /// INCLUDING the legal Human self entry (its
    /// `access_relation` is the Owner display tag; no
    /// Human→Human owner edge is ever written or read for
    /// it). Storage errors propagate — never a silently
    /// empty success and no full Bot configuration is read
    /// to count.
    async fn list_controllable(
        &self,
        query: ControllableCandidateQuery,
    ) -> ServiceResult<Vec<ControlledBotCandidate>> {
        let _ = query;
        Err(crate::ServiceError::InternalError(
            "the controllable candidate read is not configured".into(),
        ))
    }

    /// Read physical bots by the task-mode toggles. Internal capability consumed by the
    /// provider roster route; not exposed over OpenAPI.
    async fn list_control_plane_by_task_modes(
        &self,
        query: BotTaskModesQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>>;

    async fn patch_control_plane(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneRecord>>;
}
