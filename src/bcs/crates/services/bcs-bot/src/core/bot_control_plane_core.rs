use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, BotCandidateReadQuery, BotControlPlaneCandidate, BotControlPlaneCoreService,
    BotControlPlaneOwnedQuery, BotControlPlanePatch, BotControlPlaneProvider,
    BotControlPlaneRecord, BotControlPlaneRepoPort, BotControlPlaneView,
    ProviderBotBindingRepoPort, ProviderRepoPort, ServiceResult,
};

pub struct BotControlPlaneCore {
    control_plane: Arc<dyn BotControlPlaneRepoPort>,
    providers: Arc<dyn ProviderRepoPort>,
    provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
}

impl BotControlPlaneCore {
    pub fn new(
        control_plane: Arc<dyn BotControlPlaneRepoPort>,
        providers: Arc<dyn ProviderRepoPort>,
        provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
    ) -> Self {
        Self {
            control_plane,
            providers,
            provider_bindings,
        }
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
        let mut provider_ids = bindings
            .iter()
            .map(|binding| binding.provider_id.clone())
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
        let bindings = bindings
            .into_iter()
            .map(|binding| (binding.bot_uuid.clone(), binding))
            .collect::<HashMap<_, _>>();

        Ok(records
            .into_iter()
            .map(|record| {
                let provider = bindings
                    .get(&record.bot_id)
                    .and_then(|binding| providers.get(&binding.provider_id))
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

    async fn patch(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        let Some(record) = self
            .control_plane
            .patch_control_plane(bot_id, env, patch)
            .await?
        else {
            return Ok(None);
        };
        Ok(self.hydrate(vec![record]).await?.into_iter().next())
    }
}
