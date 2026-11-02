use std::sync::Arc;
use async_trait::async_trait;
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::core::a2a_bot::A2aBotCoreService;
use bcs_service_api::port::repo::{a2a_bot::{A2aBotRecord, A2aBotRepoPort}, bot_provider::BotProviderRepoPort};

pub struct A2aBotCore {
    cards: Arc<dyn A2aBotRepoPort>,
    bots: Arc<dyn BotProviderRepoPort>,
}

impl A2aBotCore {
    pub fn new(cards: Arc<dyn A2aBotRepoPort>, bots: Arc<dyn BotProviderRepoPort>) -> Self {
        Self { cards, bots }
    }
}

#[async_trait]
impl A2aBotCoreService for A2aBotCore {
    async fn discovery_cards(&self, bot_uuids: &[String], query: Option<&str>, skills: &[String])
        -> ServiceResult<std::collections::BTreeMap<String, Option<serde_json::Value>>> {
        let mut cards = std::collections::BTreeMap::new();
        for chunk in bot_uuids.chunks(256) { cards.extend(self.cards.discovery_cards(chunk, query, skills).await?); }
        Ok(cards)
    }
    async fn cards_for_bots(&self, bot_uuids: &[String]) -> ServiceResult<std::collections::BTreeMap<String, serde_json::Value>> {
        let mut cards = std::collections::BTreeMap::new();
        for chunk in bot_uuids.chunks(256) { cards.extend(self.cards.cards_for_bots(chunk).await?); }
        Ok(cards)
    }
    async fn provider_binding(&self, provider_id: &str, bot_uuid: &str) -> ServiceResult<BotProviderRecord> {
        let binding = self.bots.get_provider_bot(bot_uuid).await?
            .ok_or_else(|| ServiceError::Forbidden("A2A Bot binding unavailable".into()))?;
        if binding.provider_id != provider_id || binding.is_deleted
            || binding.connection_mode != BotConnectionMode::Gateway {
            return Err(ServiceError::Forbidden("A2A Bot binding unavailable".into()));
        }
        Ok(binding)
    }

    async fn get_card(&self, bot_uuid: &str) -> ServiceResult<Option<A2aBotRecord>> {
        self.cards.get(bot_uuid).await
    }

    async fn refreshable_cards(&self, after:Option<&str>, limit:u32)->ServiceResult<Vec<A2aBotRecord>> {
        self.cards.refreshable(after,limit).await
    }

    async fn commit_card(&self, record: A2aBotRecord, expected_revision: u64) -> ServiceResult<A2aBotRecord> {
        let binding = self.provider_binding(&record.provider_id, &record.bot_uuid).await?;
        if binding.provider_bot_ref != record.provider_bot_ref {
            return Err(ServiceError::Conflict("A2A Bot binding changed".into()));
        }
        if !self.cards.compare_and_set(&record, expected_revision).await? {
            return Err(ServiceError::Conflict("A2A Card configuration changed".into()));
        }
        Ok(record)
    }
}
