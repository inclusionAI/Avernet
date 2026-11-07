//! Narrow persistence contract for V1 Bot control-plane reads and updates.

use std::collections::HashSet;

use async_trait::async_trait;
// Re-exported for repo implementors, which consume these types only through
// this port and do not depend on the contracts crate directly.
pub use bcs_domain::caller_identity::{
    CallerConfigRevisionConflict, CallerConfigRevisionDecision, CallerIdentitySnapshot,
};

pub use crate::types::{
    BotCandidateReadQuery, BotCandidateReadRecord, BotCandidateVisibility,
    BotControlPlaneDescriptor, BotControlPlaneDescriptorPatch, BotControlPlaneOwnedQuery,
    BotControlPlanePatch, BotControlPlaneRecord, BotSearchCandidateQuery, BotTaskModesQuery,
    ControllableCandidateQuery, ControlledBotCandidate, TaskModeMatch,
};
use crate::ServiceResult;

/// Authoritative per-Bot caller configuration read-back (spec §10.7.2).
/// The outer `Option` of the repo methods expresses target absence; a present
/// record with `snapshot: None` means "existing Bot, never configured" and
/// is never fabricated into a disabled snapshot or a zero revision.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotCallerConfiguration {
    pub snapshot: Option<CallerIdentitySnapshot>,
}

/// Closed linearization result of a configuration sync (spec §10.7.2
/// `sync_status` semantics). `Applied` carries the freshly written snapshot;
/// `Unchanged`/`Stale` carry the authoritative stored snapshot; `Conflict`
/// carries only the non-sensitive revision. This is a storage
/// linearization, not Provider readiness or execution authorization.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CallerConfigurationSync {
    Applied { snapshot: CallerIdentitySnapshot },
    Unchanged { snapshot: CallerIdentitySnapshot },
    Stale { snapshot: CallerIdentitySnapshot },
    Conflict { source_revision: u64 },
}

impl CallerConfigurationSync {
    pub fn applied(snapshot: CallerIdentitySnapshot) -> Self {
        Self::Applied { snapshot }
    }

    pub fn unchanged(snapshot: CallerIdentitySnapshot) -> Self {
        Self::Unchanged { snapshot }
    }

    pub fn stale(snapshot: CallerIdentitySnapshot) -> Self {
        Self::Stale { snapshot }
    }
}

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

    /// Read the authoritative caller configuration of one non-deleted Bot in
    /// the environment. The default explicitly refuses: repositories that do
    /// not implement caller configuration must not fabricate "unconfigured"
    /// or disabled answers.
    async fn get_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        let _ = (bot_id, env);
        Err(crate::types::ServiceError::InvalidOperation {
            message: "BotControlPlaneRepoPort::get_caller_configuration is not supported by this repository".to_string(),
            request_id: None,
        })
    }

    /// Batch read of the authoritative caller configurations of several
    /// non-deleted Bots in one environment (same semantics as the single
    /// read per target). Absent targets are omitted from the result;
    /// insertion order follows each target's first occurrence in `bot_ids`.
    /// The default loops the single read verbatim so unsupported
    /// repositories keep refusing and supported ones keep working;
    /// SQL repositories override with ONE round trip to keep the
    /// admission-time member scan off the N+1 path.
    async fn get_caller_configurations(
        &self,
        bot_ids: &[String],
        env: &str,
    ) -> ServiceResult<Vec<(String, BotCallerConfiguration)>> {
        let mut seen = HashSet::new();
        let mut results = Vec::new();
        for bot_id in bot_ids {
            if !seen.insert(bot_id.as_str()) {
                continue;
            }
            if let Some(config) = self.get_caller_configuration(bot_id, env).await? {
                results.push((bot_id.clone(), config));
            }
        }
        Ok(results)
    }

    /// Atomically synchronize a caller configuration snapshot from an
    /// authorized source, applying the §10.7.2 revision matrix in one
    /// storage transaction. Write failures propagate; they are never
    /// reported as `Applied` or interpreted as a disable.
    async fn sync_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
        snapshot: CallerIdentitySnapshot,
    ) -> ServiceResult<Option<CallerConfigurationSync>> {
        let _ = (bot_id, env, snapshot);
        Err(crate::types::ServiceError::InvalidOperation {
            message: "BotControlPlaneRepoPort::sync_caller_configuration is not supported by this repository".to_string(),
            request_id: None,
        })
    }

    /// Provider-scoped read-back: the target must carry a canonical, enabled
    /// binding to exactly this Provider. Bots owned by other providers, with
    /// disabled bindings, or without any binding are out of scope (`None`),
    /// never "unconfigured" and never auto-claimed from legacy ownership.
    async fn get_provider_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
        provider_id: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        let _ = (bot_id, env, provider_id);
        Err(crate::types::ServiceError::InvalidOperation {
            message: "BotControlPlaneRepoPort::get_provider_caller_configuration is not supported by this repository".to_string(),
            request_id: None,
        })
    }

    /// Provider-scoped sync: every decision and the CAS write itself include
    /// the canonical ownership constraint, so a concurrent ownership change
    /// can never be overwritten by a stale provider authority.
    async fn sync_provider_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
        provider_id: &str,
        snapshot: CallerIdentitySnapshot,
    ) -> ServiceResult<Option<CallerConfigurationSync>> {
        let _ = (bot_id, env, provider_id, snapshot);
        Err(crate::types::ServiceError::InvalidOperation {
            message: "BotControlPlaneRepoPort::sync_provider_caller_configuration is not supported by this repository".to_string(),
            request_id: None,
        })
    }
}
