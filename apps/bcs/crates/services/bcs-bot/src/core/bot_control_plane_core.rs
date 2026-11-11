use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::{
    ActorKind, BotCandidateReadQuery, BotCandidateReadRecord, BotControlPlaneCandidate,
    BotControlPlaneCoreService, BotSearchCandidateQuery,
    BotControlPlaneOwnedQuery, BotControlPlanePatch, BotControlPlaneProvider,
    BotControlPlaneRecord, BotControlPlaneRepoPort, BotControlPlaneView, BotTaskModesQuery,
    ControllableCandidateQuery, ControlledBotCandidate,
    FriendCheckInStrategy, ProviderBotBindingRepoPort, ProviderRepoPort, ServiceResult,
};

pub struct BotControlPlaneCore {
    control_plane: Arc<dyn BotControlPlaneRepoPort>,
    providers: Arc<dyn ProviderRepoPort>,
    provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
    bot_providers: Option<Arc<dyn BotProviderRepoPort>>,
    catalog_changes: Option<Arc<dyn bcs_service_api::port::CatalogChangePort>>,
}

impl BotControlPlaneCore {
    pub fn with_catalog_changes(mut self, port: Option<Arc<dyn bcs_service_api::port::CatalogChangePort>>) -> Self {
        self.catalog_changes = port;
        self
    }

    pub fn new(
        control_plane: Arc<dyn BotControlPlaneRepoPort>,
        providers: Arc<dyn ProviderRepoPort>,
        provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
    ) -> Self {
        Self {
            control_plane,
            providers,
            provider_bindings,
            bot_providers: None,
            catalog_changes: None,
        }
    }

    pub fn with_bot_provider_repo(mut self, repo: Arc<dyn BotProviderRepoPort>) -> Self {
        self.bot_providers = Some(repo);
        self
    }

    async fn hydrate(
        &self,
        records: Vec<BotControlPlaneRecord>,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        let physical_ids = records
            .iter()
            .filter(|record| record.kind == ActorKind::Bot)
            .map(|record| record.bot_id.clone())
            .collect::<Vec<_>>();
        let bindings = self
            .provider_bindings
            .list_bindings_by_bot_uuids(&physical_ids)
            .await?;
        let mut membership = bindings.iter().map(|binding| (binding.bot_uuid.clone(), binding.provider_id.clone())).collect::<HashMap<_, _>>();
        if let Some(metadata) = &self.bot_providers {
            for id in &physical_ids {
                if let Some(record) = metadata.get_provider_bot(id).await? {
                    membership.insert(id.clone(), record.provider_id);
                }
            }
        }
        let mut provider_ids = membership
            .values().cloned()
            .collect::<HashSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        provider_ids.sort_unstable();
        let providers = self
            .providers
            .list_providers_by_ids(&provider_ids)
            .await?
            .into_iter()
            .map(|provider| (provider.provider_id.clone(), provider))
            .collect::<HashMap<_, _>>();

        Ok(records
            .into_iter()
            .map(|record| {
                let provider = membership
                    .get(&record.bot_id)
                    .and_then(|provider_id| providers.get(provider_id))
                    .map(|provider| BotControlPlaneProvider {
                        provider_id: provider.provider_id.clone(),
                        name: provider.name.clone(),
                    });
                BotControlPlaneView { record, provider }
            })
            .collect())
    }
}

#[async_trait]
impl BotControlPlaneCoreService for BotControlPlaneCore {
    async fn get_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<bcs_service_api::port::repo::BotCallerConfiguration>> {
        self.control_plane.get_caller_configuration(bot_id, env).await
    }

    async fn get_caller_configurations(
        &self,
        bot_ids: &[String],
        env: &str,
    ) -> ServiceResult<Vec<(String, bcs_service_api::port::repo::BotCallerConfiguration)>> {
        self.control_plane.get_caller_configurations(bot_ids, env).await
    }

    async fn sync_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
        snapshot: bcs_service_api::port::repo::CallerIdentitySnapshot,
    ) -> ServiceResult<Option<bcs_service_api::port::repo::CallerConfigurationSync>> {
        self.control_plane.sync_caller_configuration(bot_id, env, snapshot).await
    }

    async fn get_provider_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
        provider_id: &str,
    ) -> ServiceResult<Option<bcs_service_api::port::repo::BotCallerConfiguration>> {
        self.control_plane
            .get_provider_caller_configuration(bot_id, env, provider_id)
            .await
    }

    async fn sync_provider_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
        provider_id: &str,
        snapshot: bcs_service_api::port::repo::CallerIdentitySnapshot,
    ) -> ServiceResult<Option<bcs_service_api::port::repo::CallerConfigurationSync>> {
        self.control_plane
            .sync_provider_caller_configuration(bot_id, env, provider_id, snapshot)
            .await
    }

    async fn get_record(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        self.control_plane.get_control_plane(bot_id, env).await
    }

    async fn get(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        let Some(record) = self.control_plane.get_control_plane(bot_id, env).await? else {
            return Ok(None);
        };
        Ok(self.hydrate(vec![record]).await?.into_iter().next())
    }

    async fn get_by_ids(
        &self,
        bot_ids: &[String],
        env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        let records = self
            .control_plane
            .get_control_plane_by_ids(bot_ids, env)
            .await?;
        self.hydrate(records).await
    }

    async fn list_candidates(
        &self,
        query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        let (records, total) = self
            .control_plane
            .list_control_plane_candidates(query)
            .await?;
        let is_friend = records
            .iter()
            .map(|record| record.is_friend)
            .collect::<Vec<_>>();
        let views = self
            .hydrate(records.into_iter().map(|record| record.bot).collect())
            .await?;
        Ok((
            views
                .into_iter()
                .zip(is_friend)
                .map(|(bot, is_friend)| BotControlPlaneCandidate { bot, is_friend })
                .collect(),
            total,
        ))
    }

    async fn search_candidate_records(
        &self,
        query: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)> {
        self.control_plane.search_control_plane_candidates(query).await
    }

    async fn search_candidates(
        &self,
        query: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        let (records, total) = self
            .control_plane
            .search_control_plane_candidates(query)
            .await?;
        let is_friend = records
            .iter()
            .map(|record| record.is_friend)
            .collect::<Vec<_>>();
        let views = self
            .hydrate(records.into_iter().map(|record| record.bot).collect())
            .await?;
        Ok((
            views
                .into_iter()
                .zip(is_friend)
                .map(|(bot, is_friend)| BotControlPlaneCandidate { bot, is_friend })
                .collect(),
            total,
        ))
    }

    async fn list_by_creator(
        &self,
        query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        let records = self
            .control_plane
            .list_control_plane_by_creator(query)
            .await?;
        self.hydrate(records).await
    }

    /// Mine-union candidates (spec §8.0): a pure
    /// forwarding read — persistent-only filtering,
    /// role dedup and stable order happen in the ONE
    /// store snapshot; this core adds no pagination and
    /// no reachability.
    async fn list_controllable(
        &self,
        query: ControllableCandidateQuery,
    ) -> ServiceResult<Vec<ControlledBotCandidate>> {
        self.control_plane.list_controllable(query).await
    }

    async fn list_by_task_modes(
        &self,
        query: BotTaskModesQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        let records = self
            .control_plane
            .list_control_plane_by_task_modes(query)
            .await?;
        self.hydrate(records).await
    }

    async fn patch(
        &self,
        bot_id: &str,
        env: &str,
        mut patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        // 「全部可见」一致性回填:打开好友审批(OPEN)的这一次写里,目标态是
        // 旧有 protected 且无组织范围(deps 空)时,visibility 一并升 public
        // —— 这正是前端展示的「全部可见」等价态,也让群公开门槛
        // (visibility == "public")不再被审批策略卡死。显式携带的
        // visibility、限定组织(deps 非空)、private/public 一律不动;
        // 反向(OPEN → 非 OPEN)不降级。
        if patch.friend_check_in_strategy == Some(FriendCheckInStrategy::Open)
            && patch.visibility.is_none()
            && let Some(current) = self.control_plane.get_control_plane(bot_id, env).await?
            && current.visibility == "protected"
        {
            let scope_source = patch.friend_ext.as_ref().unwrap_or(&current.friend_ext);
            if agent_view_scope_is_empty(scope_source) {
                patch.visibility = Some("public".to_string());
            }
        }
        let Some(record) = self
            .control_plane
            .patch_control_plane(bot_id, env, patch)
            .await?
        else {
            return Ok(None);
        };
        if let Some(port) = &self.catalog_changes { port.agent_changed(bot_id); }
        Ok(self.hydrate(vec![record]).await?.into_iter().next())
    }
}

/// 目标态的组织范围是否为空:键缺失 / null / 空数组都视为无范围(「全部可见」
/// 等价态);非数组值无法判定为空,保守视为带范围、不触发升级。
fn agent_view_scope_is_empty(friend_ext: &serde_json::Map<String, serde_json::Value>) -> bool {
    match friend_ext.get(AGENT_VIEW_SCOPE_DEPS_KEY) {
        None | Some(serde_json::Value::Null) => true,
        Some(serde_json::Value::Array(deps)) => deps.is_empty(),
        Some(_) => false,
    }
}

const AGENT_VIEW_SCOPE_DEPS_KEY: &str = "view_scope_agent_friend_deps";
