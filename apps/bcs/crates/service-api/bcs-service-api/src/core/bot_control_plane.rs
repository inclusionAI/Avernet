//! Core contract for Bot control-plane reads, updates, and Provider hydration.

use async_trait::async_trait;
use bcs_domain::caller_identity::CallerIdentitySnapshot;
pub use crate::port::repo::bot_control_plane::{BotCallerConfiguration, CallerConfigurationSync};

use crate::types::{
    BotCandidateReadQuery, BotControlPlaneOwnedQuery, BotControlPlanePatch,
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

    async fn patch(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>>;

    /// Authoritative caller configuration read-back (spec §10.7.2). The
    /// default refuses explicitly so an unconfigured composition never
    /// answers caller questions.
    async fn get_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        let _ = (bot_id, env);
        Err(crate::types::ServiceError::InvalidOperation {
            message: "BotControlPlaneCoreService::get_caller_configuration is not configured".to_string(),
            request_id: None,
        })
    }

    /// Batch read of the authoritative caller configurations for one
    /// environment (per-target §10.7.2 semantics). Absent targets are
    /// omitted from the result; insertion order follows each target's
    /// first occurrence in `bot_ids`. The default loops the single read
    /// so unconfigured compositions keep refusing and test stubs keep
    /// compiling; the SQL core delegates to the repo batch, which
    /// answers in ONE round trip and keeps the admission-time member
    /// scan off the N+1 path.
    async fn get_caller_configurations(
        &self,
        bot_ids: &[String],
        env: &str,
    ) -> ServiceResult<Vec<(String, BotCallerConfiguration)>> {
        let mut seen = std::collections::HashSet::new();
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

    /// Synchronize a caller configuration snapshot through the repo port.
    /// The default refuses; callers must not treat the refusal as a disable.
    async fn sync_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
        snapshot: CallerIdentitySnapshot,
    ) -> ServiceResult<Option<CallerConfigurationSync>> {
        let _ = (bot_id, env, snapshot);
        Err(crate::types::ServiceError::InvalidOperation {
            message: "BotControlPlaneCoreService::sync_caller_configuration is not configured".to_string(),
            request_id: None,
        })
    }

    /// Provider-scoped read through the repo port; out-of-scope targets are
    /// `None`. The default refuses so an unconfigured core never answers.
    async fn get_provider_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
        provider_id: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        let _ = (bot_id, env, provider_id);
        Err(crate::types::ServiceError::InvalidOperation {
            message: "BotControlPlaneCoreService::get_provider_caller_configuration is not configured".to_string(),
            request_id: None,
        })
    }

    /// Provider-scoped sync through the repo port. The default refuses.
    async fn sync_provider_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
        provider_id: &str,
        snapshot: CallerIdentitySnapshot,
    ) -> ServiceResult<Option<CallerConfigurationSync>> {
        let _ = (bot_id, env, provider_id, snapshot);
        Err(crate::types::ServiceError::InvalidOperation {
            message: "BotControlPlaneCoreService::sync_provider_caller_configuration is not configured".to_string(),
            request_id: None,
        })
    }
}
