//! Compatibility delivery view. The read selector never changes write policy.
//! A Provider upstream membership is deliberately NOT projected as a binding.
use super::*;
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord, DownlinkDetectionSource};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;

pub struct ProviderBindingProjection {
    legacy: Arc<dyn ProviderBotBindingRepoPort>,
    bots: Arc<dyn BotProviderRepoPort>,
    source: DownlinkDetectionSource,
}

impl ProviderBindingProjection {
    pub fn new(legacy: Arc<dyn ProviderBotBindingRepoPort>, bots: Arc<dyn BotProviderRepoPort>, source: DownlinkDetectionSource) -> Self {
        Self { legacy, bots, source }
    }

    async fn ensure_metadata(&self, binding: &ProviderBotBinding) -> ServiceResult<()> {
        if self.bots.get_provider_bot(&binding.bot_uuid).await?.is_none() {
            if binding.disabled { return Err(ServiceError::Conflict("disabled legacy binding requires audited migration".into())); }
            self.bots.attach_provider_bot(metadata(binding.clone())).await?;
        }
        Ok(())
    }

    async fn delivery(&self, record: BotProviderRecord) -> ServiceResult<Option<ProviderBotBinding>> {
        if record.connection_mode != BotConnectionMode::Gateway { return Ok(None); }
        // Mode, affiliation and override come from the Bot. Public legacy
        // binding timestamps still come from the binding, not duplicate columns.
        let binding = self.legacy.get_binding_by_bot_uuid(&record.bot_uuid).await?
            .filter(|binding| binding.provider_id == record.provider_id && binding.provider_bot_ref == record.provider_bot_ref)
            .ok_or_else(|| ServiceError::InternalError("gateway Bot has no matching binding".into()))?;
        Ok(Some(ProviderBotBinding {
            bot_uuid: record.bot_uuid, provider_id: record.provider_id, provider_bot_ref: record.provider_bot_ref,
            webhook_url: record.webhook_url, disabled: record.is_deleted,
            created_at: binding.created_at, updated_at: binding.updated_at,
        }))
    }
}

pub(super) fn metadata(binding: ProviderBotBinding) -> BotProviderRecord {
    BotProviderRecord {
        bot_uuid: binding.bot_uuid, provider_id: binding.provider_id, provider_bot_ref: binding.provider_bot_ref,
        connection_mode: BotConnectionMode::Gateway, webhook_url: binding.webhook_url,
        is_deleted: binding.disabled,
    }
}

#[async_trait]
impl ProviderBotBindingRepoPort for ProviderBindingProjection {
    async fn insert_binding(&self, binding: ProviderBotBinding) -> ServiceResult<()> {
        self.bots.attach_provider_bot(metadata(binding)).await
    }

    async fn get_binding_by_bot_uuid(&self, bot_uuid: &str) -> ServiceResult<Option<ProviderBotBinding>> {
        if self.source == DownlinkDetectionSource::Binding {
            return self.legacy.get_binding_by_bot_uuid(bot_uuid).await;
        }
        match self.bots.get_connection_mode(bot_uuid).await? {
            None | Some(BotConnectionMode::Plugin) => Ok(None),
            Some(BotConnectionMode::Gateway) => {
                let record = self.bots.get_provider_bot(bot_uuid).await?
                    .ok_or_else(|| ServiceError::InternalError("gateway Bot has no Provider metadata".into()))?;
                self.delivery(record).await
            }
        }
    }

    async fn get_binding_by_provider_ref(&self, provider_id: &str, provider_bot_ref: &str) -> ServiceResult<Option<ProviderBotBinding>> {
        if self.source == DownlinkDetectionSource::Binding {
            self.legacy.get_binding_by_provider_ref(provider_id, provider_bot_ref).await
        } else {
            match self.bots.get_provider_bot_by_ref(provider_id, provider_bot_ref).await? {
                Some(record) => self.delivery(record).await,
                None => Ok(None),
            }
        }
    }

    async fn list_bindings_by_provider(&self, provider_id: &str) -> ServiceResult<Vec<ProviderBotBinding>> {
        if self.source == DownlinkDetectionSource::Binding {
            self.legacy.list_bindings_by_provider(provider_id).await
        } else {
            let mut bindings = Vec::new();
            for record in self.bots.list_provider_bot_metadata(Some(provider_id)).await? {
                if let Some(binding) = self.delivery(record).await? { bindings.push(binding); }
            }
            Ok(bindings)
        }
    }

    async fn list_discoverable_provider_bot_records(&self, selector: &ProviderBotDiscoverySelector) -> ServiceResult<Vec<ProviderBotDiscoveryRecord>> {
        // Discovery is a separate public list policy; it must not start exposing
        // upstream membership merely because the delivery read source changed.
        self.legacy.list_discoverable_provider_bot_records(selector).await
    }

    async fn update_binding_webhook_url(&self, provider_id: &str, bot_uuid: &str, webhook_url: Option<&str>, updated_at: u64) -> ServiceResult<Option<ProviderBotBinding>> {
        let Some(binding) = self.get_binding_by_bot_uuid(bot_uuid).await? else { return Ok(None); };
        if binding.provider_id != provider_id { return Err(ServiceError::Forbidden("provider_id_mismatch".into())); }
        self.ensure_metadata(&binding).await?;
        let record = self.bots.update_provider_webhook(provider_id, bot_uuid, webhook_url.map(str::to_owned), updated_at).await?;
        self.delivery(record).await
    }

    async fn update_binding_disabled(&self, bot_uuid: &str, disabled: bool, updated_at: u64) -> ServiceResult<Option<ProviderBotBinding>> {
        let Some(binding) = self.get_binding_by_bot_uuid(bot_uuid).await? else { return Ok(None); };
        if !disabled {
            if binding.disabled { return Err(ServiceError::Conflict("deleted Bot cannot be re-enabled through a binding".into())); }
            return Ok(Some(binding));
        }
        if binding.disabled { return Ok(Some(binding)); }
        self.ensure_metadata(&binding).await?;
        self.bots.delete_provider_bot(&binding.provider_id, bot_uuid, updated_at).await?;
        match self.bots.get_provider_bot(bot_uuid).await? {
            Some(record) => self.delivery(record).await,
            None => Ok(None),
        }
    }
}
