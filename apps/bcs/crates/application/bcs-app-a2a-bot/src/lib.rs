use std::sync::Arc;
use async_trait::async_trait;
use bcs_service_api::{ProviderCoreService, ServiceError, ServiceResult};
use bcs_service_api::application::a2a_bot::{A2aBotManagementService, ConfigureA2aBotCommand};
use bcs_service_api::core::a2a_bot::A2aBotCoreService;
use bcs_service_api::port::{a2a_card::A2aCardPort, repo::a2a_bot::A2aBotRecord};
use bcs_service_api::types::a2a_bot::A2aCardSource;
mod registration;

#[derive(Default)]
struct RefreshRound {cursor:Option<String>,next_round_ms:Option<i64>}
pub struct A2aBotManagement {
    providers:Arc<dyn ProviderCoreService>,core:Arc<dyn A2aBotCoreService>,fetcher:Arc<dyn A2aCardPort>,
    registration:Option<Arc<dyn bcs_service_api::core::a2a_bot::A2aProviderRegistrationCoreService>>,
    refresh_round:tokio::sync::Mutex<RefreshRound>,
}
impl A2aBotManagement {
    pub fn new(providers:Arc<dyn ProviderCoreService>,core:Arc<dyn A2aBotCoreService>,fetcher:Arc<dyn A2aCardPort>)->Self {
        Self {providers,core,fetcher,registration:None,refresh_round:Default::default()}
    }
    pub fn with_registration(mut self,registration:Arc<dyn bcs_service_api::core::a2a_bot::A2aProviderRegistrationCoreService>)->Self {
        self.registration=Some(registration);self
    }
    async fn authorize(&self,provider_id:&str,token: &str) -> ServiceResult<()> {
        if self.providers.get_provider(provider_id,token).await?.disabled {return Err(ServiceError::Forbidden("provider_disabled".into()));}Ok(())
    }
    async fn load_card(&self,provider_id:&str,source:A2aCardSource)->ServiceResult<(Option<String>,bcs_service_api::port::a2a_card::ValidatedA2aCard)> {
        let credential=self.providers.get_downlink_credential(provider_id).await?;
        if credential.disabled || credential.secret_value.trim().is_empty() || credential.credential_kind!="downlink_bcs_to_provider" {
            return Err(ServiceError::Forbidden("A2A downlink credential unavailable".into()));
        }
        match source {
            A2aCardSource::Url(url)=>{if url.trim().is_empty() || url!=url.trim() {return Err(ServiceError::InvalidOperation {message:"invalid Card URL".into(),request_id:None});}let card=self.fetcher.fetch(&url,true).await?;Ok((Some(url),card))},
            A2aCardSource::Inline(raw)=>Ok((None,self.fetcher.validate(raw,true).await?)),
        }
    }
    async fn commit(&self,provider_id:String,bot_uuid:String,source:A2aCardSource,expected:u64)->ServiceResult<A2aBotRecord> {
        let binding=self.core.provider_binding(&provider_id,&bot_uuid).await?;
        let (agent_card_url,card)=self.load_card(&provider_id,source).await?;
        let revision=expected.checked_add(1).ok_or_else(||ServiceError::Conflict("A2A revision exhausted".into()))?;
        self.core.commit_card(A2aBotRecord {bot_uuid,provider_id,provider_bot_ref:binding.provider_bot_ref,
            agent_card_url,agent_card:card.raw,revision},expected).await
    }
    async fn refresh_card(&self,old:A2aBotRecord)->ServiceResult<A2aBotRecord> {
        let url=old.agent_card_url.ok_or_else(||ServiceError::InvalidOperation {message:"inline Card has no source URL; submit an explicit update".into(),request_id:None})?;
        // Failure never rewrites the last validated snapshot or increments revision.
        self.commit(old.provider_id,old.bot_uuid,A2aCardSource::Url(url),old.revision).await
    }
}
#[async_trait]
impl A2aBotManagementService for A2aBotManagement {
    async fn register_provider_bot(&self,command:bcs_service_api::RegisterProviderBotCommand,source:A2aCardSource,now_ms:i64)
        ->ServiceResult<bcs_service_api::RegisterProviderBotOutcome> {self.register_admin(command,source,now_ms).await}
    async fn get(&self,provider_id:&str,token:&str,bot_uuid:&str)->ServiceResult<Option<A2aBotRecord>> {
        self.authorize(provider_id,token).await?;self.core.provider_binding(provider_id,bot_uuid).await?;self.core.get_card(bot_uuid).await
    }
    async fn configure(&self,command:ConfigureA2aBotCommand)->ServiceResult<A2aBotRecord> {
        self.authorize(&command.provider_id,&command.provider_admin_token).await?;
        self.commit(command.provider_id,command.bot_uuid,command.source,command.expected_revision).await
    }
    async fn refresh(&self,provider_id:&str,token:&str,bot_uuid:&str,_now_ms:i64)->ServiceResult<A2aBotRecord> {
        self.authorize(provider_id,token).await?;self.core.provider_binding(provider_id,bot_uuid).await?;
        let old=self.core.get_card(bot_uuid).await?.ok_or_else(||ServiceError::InvalidOperation {message:"A2A Card not configured".into(),request_id:None})?;
        self.refresh_card(old).await
    }
    async fn refresh_due(&self,now_ms:i64)->ServiceResult<usize> {
        let mut round=self.refresh_round.lock().await;
        let next=*round.next_round_ms.get_or_insert(now_ms.saturating_add(300_000));
        if round.cursor.is_none() && now_ms<next {return Ok(0);}
        let cards=self.core.refreshable_cards(round.cursor.as_deref(),16).await?;
        let next_cursor=if cards.len()==16 {cards.last().map(|c|c.bot_uuid.clone())} else {None};
        let mut count=0;let mut failure=None;
        for card in cards {
            match self.refresh_card(card).await {Ok(_)=>count+=1,Err(error)=>{if failure.is_none() {failure=Some(error);}}}
        }
        round.cursor=next_cursor;
        if round.cursor.is_none() {round.next_round_ms=Some(now_ms.saturating_add(300_000));}
        // Advance past failures so later pages cannot starve; durable write
        // errors still propagate instead of being reported as success.
        if let Some(error)=failure {return Err(error);}Ok(count)
    }
}
