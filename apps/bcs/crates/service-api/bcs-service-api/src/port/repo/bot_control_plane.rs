//! Narrow persistence contract for V1 Bot control-plane reads and updates.

use std::collections::HashSet;

use async_trait::async_trait;

pub use crate::types::{
    BotCandidateReadQuery, BotCandidateReadRecord, BotCandidateVisibility, BotControllableQuery,
    BotControlPlaneDescriptor, BotControlPlaneDescriptorPatch, BotControlPlaneOwnedQuery,
    BotControlPlanePatch, BotControlPlaneRecord, BotSearchCandidateQuery, BotTaskModesQuery,
    ControllableBotRecord, TaskModeMatch,
};
use crate::types::BotOperationContext;
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

    /// Read physical bots by the task-mode toggles. Internal capability consumed by the
    /// provider roster route; not exposed over OpenAPI.
    async fn list_control_plane_by_task_modes(
        &self,
        query: BotTaskModesQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>>;

    /// Controllable-Bot union read (spec §7.2): every live Bot in
    /// `query.env` carrying an approved `owner` or `manager` role edge
    /// with `from_id = human_<query.user_id>` (exact, per the shared
    /// actor-id encoding), PLUS the caller's own Human self row
    /// (`human_<query.user_id>`) as an explicit `Owner`-labeled
    /// compatibility projection. Other Human rows never enter the result
    /// even if a stray role row targets them.
    ///
    /// Deduplication and labeling: one row per Bot; when both an owner
    /// and a manager edge exist, the row is labeled `owner` (owner
    /// priority, no duplicated items and no role arrays).
    ///
    /// Strictness (Task 3 read contract, fail-closed on the whole
    /// query — corruption never degrades into a shorter success):
    /// - a role edge of this User targeting a missing or soft-deleted
    ///   Bot, or targeting a Human row other than the self row, is
    ///   `CorruptAuthority`;
    /// - a candidate physical Bot with `ownership_version = 0` is
    ///   `OwnershipNotInitialized`;
    /// - a candidate physical Bot without exactly one approved `owner`
    ///   edge (from anyone) is `CorruptAuthority`.
    ///
    /// The unified `kind`/`name`/`status` filters apply to the union
    /// members; results are ordered `created_at DESC, bot_id ASC` and
    /// are NOT paginated (total + reachability + offset/limit stay with
    /// the application). The query must not perform per-Bot sub-queries
    /// (no N+1).
    async fn list_controllable(
        &self,
        query: BotControllableQuery,
    ) -> ServiceResult<Vec<ControllableBotRecord>>;

    /// Mutable control-plane update. The REQUIRED
    /// [`BotOperationContext`] carries the audited operation identity
    /// (spec §12.5): the owning store commits the actual UPDATE and its
    /// `update/bot/applied` audit row in ONE transaction (the in-memory
    /// twin publishes both under one critical section). An audit
    /// insertion failure rolls the business UPDATE back; identical
    /// same-slot audit retries are idempotent, differing content under
    /// the same `(env, operation_id, step_key)` slot is a conflict.
    async fn patch_control_plane(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
        operation: BotOperationContext,
    ) -> ServiceResult<Option<BotControlPlaneRecord>>;
}
