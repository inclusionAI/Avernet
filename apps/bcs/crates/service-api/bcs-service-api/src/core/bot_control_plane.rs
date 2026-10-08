//! Core contract for Bot control-plane reads, updates, and Provider hydration.

use async_trait::async_trait;
use bcs_domain::BotAccessRelation;

use crate::types::BotOperationContext;
use crate::types::{
    BotCandidateReadQuery, BotControllableQuery, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRecord, BotSearchCandidateQuery, BotTaskModesQuery, ServiceResult,
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

/// Core-owned hydrated controllable-Bot view: the control-plane record with
/// its Provider hydration plus the caller's highest effective access
/// relation, carried from the same union read (the relation label is never
/// re-queried per Bot).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ControllableBotView {
    pub bot: BotControlPlaneView,
    pub access_relation: BotAccessRelation,
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

    async fn search_candidates(
        &self,
        query: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        let _ = query;
        Err(crate::types::ServiceError::InvalidOperation {
            message: "BotControlPlaneCoreService::search_candidates is not configured".to_string(),
            request_id: None,
        })
    }

    /// Owner ∪ manager union projection for one Human (spec §7.2): each
    /// Bot that carries an approved `owner` or `manager` edge from
    /// `user_id` in the current env, deduplicated per Bot with owner
    /// priority, plus the caller's own Human self row as an explicit
    /// `Owner`-labeled compatibility row. The unified kind/name/status
    /// filters are applied by the owning store; reachability, total and
    /// pagination stay with the application.
    ///
    /// Fail-closed strictness follows the Task 3 read contract: a role
    /// edge whose target Bot is dangling/deleted targets a Human row
    /// (other than the self row), or whose Bot's ownership is
    /// uninitialized/corrupt fails the WHOLE query with the typed error —
    /// corruption never degrades into a shorter successful list.
    async fn list_controllable(
        &self,
        query: BotControllableQuery,
    ) -> ServiceResult<Vec<ControllableBotView>> {
        let _ = query;
        Err(crate::types::ServiceError::InvalidOperation {
            message: "BotControlPlaneCoreService::list_controllable is not configured".to_string(),
            request_id: None,
        })
    }

    async fn list_by_creator(
        &self,
        query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>>;

    /// Read physical bots by the task-mode toggles. The default returns an empty result so
    /// test stubs keep compiling; concrete core services override to delegate to the repo
    /// port and hydrate providers.
    async fn list_by_task_modes(
        &self,
        query: BotTaskModesQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        let _ = query;
        Ok(Vec::new())
    }

    /// Mutable control-plane update. The REQUIRED
    /// [`BotOperationContext`] carries the audited operation identity
    /// (spec §12.5): owning stores commit the actual UPDATE and its
    /// `update/bot/applied` audit row in ONE transaction and roll the
    /// business change back when the audit insertion fails.
    async fn patch(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
        operation: BotOperationContext,
    ) -> ServiceResult<Option<BotControlPlaneView>>;
}
