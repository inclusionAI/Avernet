//! Process-local implementation of the Bot Provider metadata contract.
//! Like MemoryProviderStore, identity metadata is scoped to this store instance.
use super::*;
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::{BotRepoPort, bot_provider::BotProviderRepoPort};

pub struct MemoryBotProviderStore {
    bots: Arc<dyn BotRepoPort>,
    bindings: Arc<MemoryProviderStore>,
    records: RwLock<BTreeMap<String, BotProviderRecord>>,
}

impl MemoryBotProviderStore {
    pub fn new(bots: Arc<dyn BotRepoPort>, bindings: Arc<MemoryProviderStore>) -> Self {
        Self { bots, bindings, records: RwLock::new(BTreeMap::new()) }
    }
}

fn conflict() -> ServiceError { ServiceError::Conflict("Bot or Provider/ref is already registered or changed".into()) }

#[async_trait]
impl BotProviderRepoPort for MemoryBotProviderStore {
    async fn get_provider_bot_by_ref(&self, provider_id: &str, provider_bot_ref: &str) -> ServiceResult<Option<BotProviderRecord>> {
        Ok(self.records.read().await.values().find(|r| r.provider_id == provider_id && r.provider_bot_ref == provider_bot_ref).cloned())
    }

    async fn list_provider_bot_metadata(&self, provider_id: Option<&str>) -> ServiceResult<Vec<BotProviderRecord>> {
        Ok(self.records.read().await.values().filter(|r| provider_id.is_none_or(|id| r.provider_id == id)).cloned().collect())
    }

    async fn get_connection_mode(&self, bot_uuid: &str) -> ServiceResult<Option<BotConnectionMode>> {
        if let Some(record) = self.get_provider_bot(bot_uuid).await? { return Ok(Some(record.connection_mode)); }
        Ok(self.bots.try_get(bot_uuid).await?.map(|_| BotConnectionMode::Plugin))
    }

    async fn attach_provider_bot(&self, mut record: BotProviderRecord) -> ServiceResult<()> {
        bot_storage::validate_record(&record)?;
        let mut records = self.records.write().await;
        let mut bindings = self.bindings.bindings_by_bot.write().await;
        let mut refs = self.bindings.binding_ref_index.write().await;
        if self.bots.try_get(&record.bot_uuid).await?.is_none() { return Err(ServiceError::BotNotFound(record.bot_uuid)); }
        if records.values().any(|r| r.provider_id == record.provider_id && r.provider_bot_ref == record.provider_bot_ref && r.bot_uuid != record.bot_uuid) { return Err(conflict()); }
        if let Some(previous) = records.get(&record.bot_uuid) {
            if previous.is_deleted || previous.provider_id != record.provider_id || previous.provider_bot_ref != record.provider_bot_ref
                || (previous.connection_mode == BotConnectionMode::Gateway && (record.connection_mode != BotConnectionMode::Gateway || previous.webhook_url != record.webhook_url)) { return Err(conflict()); }
            record.registered_at = previous.registered_at;
            record.updated_at = record.updated_at.max(previous.updated_at.saturating_add(1));
        }
        let key = (record.provider_id.clone(), record.provider_bot_ref.clone());
        if refs.get(&key).is_some_and(|id| id != &record.bot_uuid) { return Err(conflict()); }
        if let Some(binding) = bindings.get(&record.bot_uuid) {
            if binding.disabled || record.connection_mode != BotConnectionMode::Gateway || binding.provider_id != record.provider_id
                || binding.provider_bot_ref != record.provider_bot_ref || binding.webhook_url != record.webhook_url { return Err(conflict()); }
        } else if record.connection_mode == BotConnectionMode::Gateway {
            bindings.insert(record.bot_uuid.clone(), ProviderBotBinding {
                bot_uuid: record.bot_uuid.clone(), provider_id: record.provider_id.clone(), provider_bot_ref: record.provider_bot_ref.clone(),
                webhook_url: record.webhook_url.clone(), disabled: false, created_at: record.registered_at, updated_at: record.updated_at,
            });
            refs.insert(key, record.bot_uuid.clone());
        }
        records.insert(record.bot_uuid.clone(), record);
        Ok(())
    }

    async fn get_provider_bot(&self, bot_uuid: &str) -> ServiceResult<Option<BotProviderRecord>> {
        Ok(self.records.read().await.get(bot_uuid).cloned())
    }

    async fn create_provider_bot(&self, record: BotProviderRecord, capabilities: BotCapabilities, owner: &str, token: &str) -> ServiceResult<()> {
        bot_storage::validate_record(&record)?;
        if owner.trim().is_empty() || token.is_empty() {
            return Err(ServiceError::InvalidOperation { message: "Bot owner and runtime credential are required".into(), request_id: None });
        }
        let mut records = self.records.write().await;
        let mut bindings = self.bindings.bindings_by_bot.write().await;
        let mut refs = self.bindings.binding_ref_index.write().await;
        let key = (record.provider_id.clone(), record.provider_bot_ref.clone());
        if records.contains_key(&record.bot_uuid) || refs.contains_key(&key)
            || bindings.contains_key(&record.bot_uuid)
            || records.values().any(|existing| existing.provider_id == record.provider_id && existing.provider_bot_ref == record.provider_bot_ref)
        { return Err(conflict()); }
        if !self.bots.create_registration_if_absent(record.bot_uuid.clone(), capabilities, owner, token).await? {
            return Err(conflict());
        }
        if record.connection_mode == BotConnectionMode::Gateway {
            bindings.insert(record.bot_uuid.clone(), ProviderBotBinding {
                bot_uuid: record.bot_uuid.clone(), provider_id: record.provider_id.clone(), provider_bot_ref: record.provider_bot_ref.clone(),
                webhook_url: record.webhook_url.clone(), disabled: false, created_at: record.registered_at, updated_at: record.updated_at,
            });
            refs.insert(key, record.bot_uuid.clone());
        }
        records.insert(record.bot_uuid.clone(), record);
        Ok(())
    }

    async fn update_provider_webhook(&self, provider_id: &str, bot_uuid: &str, webhook_url: Option<String>, updated_at: u64) -> ServiceResult<BotProviderRecord> {
        let mut records = self.records.write().await;
        let record = records.get_mut(bot_uuid).ok_or_else(|| ServiceError::BotNotFound(bot_uuid.into()))?;
        if record.provider_id != provider_id { return Err(ServiceError::Forbidden("provider_id_mismatch".into())); }
        if record.is_deleted || record.connection_mode != BotConnectionMode::Gateway { return Err(conflict()); }
        let mut bindings = self.bindings.bindings_by_bot.write().await;
        let binding = bindings.get_mut(bot_uuid).filter(|b| !b.disabled && b.provider_id == provider_id && b.provider_bot_ref == record.provider_bot_ref).ok_or_else(conflict)?;
        record.webhook_url = webhook_url.clone();
        record.updated_at = updated_at.max(record.updated_at.saturating_add(1));
        binding.webhook_url = webhook_url;
        binding.updated_at = record.updated_at;
        Ok(record.clone())
    }

    async fn delete_provider_bot(&self, provider_id: &str, bot_uuid: &str, updated_at: u64) -> ServiceResult<bool> {
        let mut records = self.records.write().await;
        let record = records.get_mut(bot_uuid).ok_or_else(|| ServiceError::BotNotFound(bot_uuid.into()))?;
        if record.provider_id != provider_id { return Err(ServiceError::Forbidden("provider_id_mismatch".into())); }
        if record.is_deleted { return Ok(false); }
        let mut bindings = self.bindings.bindings_by_bot.write().await;
        let binding = if record.connection_mode == BotConnectionMode::Gateway {
            Some(bindings.get_mut(bot_uuid).filter(|b| !b.disabled && b.provider_id == provider_id && b.provider_bot_ref == record.provider_bot_ref).ok_or_else(conflict)?)
        } else { None };
        if !self.bots.soft_delete(bot_uuid).await { return Err(conflict()); }
        record.is_deleted = true;
        record.updated_at = updated_at.max(record.updated_at.saturating_add(1));
        if let Some(binding) = binding { binding.disabled = true; binding.updated_at = record.updated_at; }
        Ok(true)
    }
}
