use std::sync::Arc;
use async_trait::async_trait;
use bcs_service_api::{BotRegistryCoreService, ProviderCoreService, ServiceError, ServiceResult};
use bcs_service_api::application::a2a_bot::{A2aBotManagementService, ConfigureA2aBotCommand};
use bcs_service_api::core::a2a_bot::A2aBotCoreService;
use bcs_service_api::port::{a2a_card::A2aCardPort, repo::a2a_bot::A2aBotRecord};
use bcs_service_api::types::a2a_bot::A2aCardSource;
pub mod v1;
mod registration;
mod names;
mod management;

#[derive(Default)]
struct RefreshRound {cursor:Option<String>,next_round_ms:Option<i64>}
struct ProviderRegistration {
    core:Arc<dyn bcs_service_api::core::a2a_bot::A2aProviderRegistrationCoreService>,
    registry:Arc<dyn BotRegistryCoreService>,
}
pub struct A2aBotManagement {
    policy:Arc<bcs_service_api::types::catalog_labels::CatalogLabelPolicy>,
    providers:Arc<dyn ProviderCoreService>,core:Arc<dyn A2aBotCoreService>,fetcher:Arc<dyn A2aCardPort>,
    registration:Option<ProviderRegistration>,
    refresh_round:tokio::sync::Mutex<RefreshRound>,
}
impl A2aBotManagement {
    pub fn new(providers:Arc<dyn ProviderCoreService>,core:Arc<dyn A2aBotCoreService>,fetcher:Arc<dyn A2aCardPort>)->Self {
        Self {policy:Arc::default(),providers,core,fetcher,registration:None,refresh_round:Default::default()}
    }
    pub fn with_catalog_label_policy(mut self, policy:Arc<bcs_service_api::types::catalog_labels::CatalogLabelPolicy>)->Self {
        self.policy=policy;self
    }
    pub fn with_registration(mut self,registration:Arc<dyn bcs_service_api::core::a2a_bot::A2aProviderRegistrationCoreService>,
        registry:Arc<dyn BotRegistryCoreService>)->Self {
        self.registration=Some(ProviderRegistration {core:registration,registry});self
    }
    async fn authorize(&self,provider_id:&str,token: &str) -> ServiceResult<()> {
        if self.providers.get_provider(provider_id,token).await?.disabled {return Err(ServiceError::Forbidden("provider_disabled".into()));}Ok(())
    }
    async fn load_card(&self,provider_id:&str,source:A2aCardSource)->ServiceResult<(Option<String>,bcs_service_api::port::a2a_card::ValidatedA2aCard)> {
        // Fetch public declarations first; credential material is never sent to the Card URL.
        let (url,card)=match source {
            A2aCardSource::Url(url)=>{if url.trim().is_empty() || url!=url.trim() {return Err(ServiceError::InvalidOperation {message:"invalid Card URL".into(),request_id:None});}
                let card=self.fetcher.fetch(&url,true).await?;(Some(url),card)},
            A2aCardSource::Inline(raw)=>(None,self.fetcher.validate(raw,true).await?),
        };
        self.providers.a2a_authentication(provider_id,&card.raw).await?;
        bcs_service_api::types::a2a_bot::card_bot_name(&card.raw)
            .map_err(|message|ServiceError::InvalidOperation {message:message.into(),request_id:None})?;
        bcs_service_api::types::a2a_bot::a2a_management::source_agent(&card.raw)?;
        bcs_service_api::types::a2a_card_projection::project_agent_card_capabilities(Some(&card.raw))?;
        Ok((url,card))
    }
    async fn commit(&self,provider_id:String,bot_uuid:String,source:A2aCardSource,expected:u64,skip_unchanged:bool,labels:bcs_service_api::types::catalog_labels::CatalogLabels)->ServiceResult<A2aBotRecord> {
        let binding=self.core.provider_binding(&provider_id,&bot_uuid).await?;
        let (agent_card_url,card)=self.load_card(&provider_id,source).await?;
        if skip_unchanged {
            if let Some(current)=self.core.get_card(&bot_uuid).await? {
                let policy=&bcs_service_api::types::a2a_bot::a2a_management::validate_management_card;
                if policy(&current.agent_card).is_ok() {policy(&card.raw)?;}
                if current.revision==expected && current.provider_id==provider_id
                    && current.provider_bot_ref==binding.provider_bot_ref
                    && current.agent_card_url==agent_card_url && current.agent_card==card.raw {
                    // A no-op refresh still validates affiliation after the network wait.
                    // Concurrent configuration changes must not be acknowledged as this refresh.
                    let latest=self.core.provider_binding(&provider_id,&bot_uuid).await?;
                    if latest.provider_bot_ref!=binding.provider_bot_ref {
                        return Err(ServiceError::Conflict("A2A Bot binding changed".into()));
                    }
                    self.core.synchronize_card_projection(&current).await?;
                    return Ok(current);
                }
            }
        }
        let revision=expected.checked_add(1).ok_or_else(||ServiceError::Conflict("A2A revision exhausted".into()))?;
        // Labels share the same revision fence as the Card: a newer label-only
        // write supersedes this refresh instead of accepting a stale snapshot.
        self.core.commit_card(A2aBotRecord {catalog_labels:labels,bot_uuid,provider_id,provider_bot_ref:binding.provider_bot_ref,
            agent_card_url,agent_card:card.raw,revision},expected).await
    }

    async fn configure_preserving_labels(
        &self, command: ConfigureA2aBotCommand,
    ) -> ServiceResult<A2aBotRecord> {
        let binding=self.core.provider_binding(&command.provider_id,&command.bot_uuid).await?;
        let (agent_card_url,card)=self.load_card(&command.provider_id,command.source).await?;
        // Fetch once outside the retry loop. Omission must merge with the latest
        // committed labels on every attempt, including a label-only CAS race.
        for _ in 0..3 {
            self.authorize(&command.provider_id,&command.provider_admin_token).await?;
            let latest=self.core.provider_binding(&command.provider_id,&command.bot_uuid).await?;
            if latest.provider_bot_ref!=binding.provider_bot_ref {
                return Err(ServiceError::Conflict("A2A Bot binding changed".into()));
            }
            let current=self.core.get_card(&command.bot_uuid).await?;
            if current.as_ref().is_some_and(|record|record.provider_id!=command.provider_id
                || record.provider_bot_ref!=binding.provider_bot_ref) {
                return Err(ServiceError::Conflict("A2A Bot binding changed".into()));
            }
            let expected=current.as_ref().map(|record|record.revision).unwrap_or(0);
            if command.expected_revision.is_some_and(|revision|revision!=expected) {
                return Err(ServiceError::Conflict("A2A revision changed".into()));
            }
            let revision=expected.checked_add(1)
                .ok_or_else(||ServiceError::Conflict("A2A revision exhausted".into()))?;
            let next=A2aBotRecord {
                catalog_labels:current.map(|record|record.catalog_labels).unwrap_or_default(),
                bot_uuid:command.bot_uuid.clone(),provider_id:command.provider_id.clone(),
                provider_bot_ref:binding.provider_bot_ref.clone(),
                agent_card_url:agent_card_url.clone(),agent_card:card.raw.clone(),revision,
            };
            match self.core.commit_card(next,expected).await {
                Err(ServiceError::Conflict(_)) if command.expected_revision.is_none()=>continue,
                result=>return result,
            }
        }
        Err(ServiceError::Conflict("A2A configuration changed concurrently".into()))
    }
    async fn refresh_card(&self,old:A2aBotRecord)->ServiceResult<A2aBotRecord> {
        let url=old.agent_card_url.ok_or_else(||ServiceError::InvalidOperation {message:"inline Card has no source URL; submit an explicit update".into(),request_id:None})?;
        // Failure never rewrites the last validated snapshot or increments revision.
        self.commit(old.provider_id,old.bot_uuid,A2aCardSource::Url(url),old.revision,true,old.catalog_labels).await
    }
}
#[async_trait]
impl A2aBotManagementService for A2aBotManagement {
    async fn update_agent(&self,command:bcs_service_api::application::a2a_bot::UpdateAgentCommand)->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {
        self.public_update(&command.provider_admin_token,&command.bot_id,command.source,command.visibility,command.catalog_labels).await
    }
    async fn register_provider_bot_with_labels(&self,command:bcs_service_api::RegisterProviderBotCommand,source:A2aCardSource,now_ms:i64,
        labels:bcs_service_api::types::catalog_labels::CatalogLabelsInput)->ServiceResult<bcs_service_api::RegisterProviderBotOutcome> {
        self.register_admin_with_labels(command,source,now_ms,labels).await
    }
    async fn register_agent(&self,command:bcs_service_api::application::a2a_bot::RegisterAgentCommand)
        ->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {self.public_register(command).await}
    async fn find_agents_by_provider_ref(&self,token:&str,provider_bot_ref:&str)->ServiceResult<Vec<bcs_service_api::application::a2a_bot::ManagedA2aCard>> {
        self.public_find(token,provider_bot_ref).await
    }
    async fn get_agent_by_id(&self,token:&str,bot_id:&str)->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {
        self.public_get(token,bot_id).await
    }
    async fn update_agent_by_id(&self,token:&str,bot_id:&str,source:A2aCardSource)->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {
        self.public_update(token,bot_id,Some(source),None,None).await
    }
    async fn update_agent_by_id_with_visibility(&self,token:&str,bot_id:&str,source:Option<A2aCardSource>,visibility:Option<bcs_service_api::types::a2a_bot::A2aRegistrationVisibility>)->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {
        self.public_update(token,bot_id,source,visibility,None).await
    }
    async fn refresh_agent_by_id(&self,token:&str,bot_id:&str)->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {
        self.public_refresh(token,bot_id).await
    }

    async fn resolve_provider_ref_target(&self,provider:&str,bot:&str)->ServiceResult<String> {
        if !bcs_service_api::types::a2a_names::valid_provider_name(provider) {
            return Err(ServiceError::InvalidOperation {message:"invalid provider_name".into(),request_id:None});
        }
        let provider=self.providers.get_provider_by_slug(provider).await?
            .filter(|value|value.enabled).ok_or_else(||ServiceError::ProviderNotFound(provider.into()))?;
        bcs_service_api::types::a2a_bot::a2a_management::validate_provider_bot_ref(bot)?;
        let record=self.core.get_by_provider_ref(&provider.provider_id,bot).await?
            .ok_or_else(||ServiceError::BotNotFound(bot.into()))?;
        self.core.provider_binding(&provider.provider_id,&record.bot_uuid).await?;
        // Failed target lookups must not mutate another Provider. The pin still
        // checks the resolved identity and slug to fence a concurrent rename.
        self.providers.pin_a2a_provider_name(&provider.provider_id,&provider.slug).await?;
        Ok(record.bot_uuid)
    }
    async fn register_named(&self,command:bcs_service_api::application::a2a_bot::RegisterNamedA2aBotCommand)
        ->ServiceResult<bcs_service_api::application::a2a_bot::NamedA2aRegistration> {self.register_by_name(command).await}

    async fn register_provider_bot(&self,command:bcs_service_api::RegisterProviderBotCommand,source:A2aCardSource,now_ms:i64)
        ->ServiceResult<bcs_service_api::RegisterProviderBotOutcome> {self.register_admin(command,source,now_ms).await}
    async fn configure_catalog_labels(&self,provider_id:&str,token:&str,bot_id:&str,
        labels:bcs_service_api::types::catalog_labels::CatalogLabelsInput,expected:Option<u64>)->ServiceResult<A2aBotRecord> {
        self.authorize(provider_id,token).await?;
        let labels=self.policy.normalize(labels)?;
        for _ in 0..3 {
            let binding=self.core.provider_binding(provider_id,bot_id).await?;
            let current=self.core.get_card(bot_id).await?.filter(|card|card.provider_id==provider_id && card.provider_bot_ref==binding.provider_bot_ref)
                .ok_or_else(||ServiceError::BotNotFound(bot_id.into()))?;
            if expected.is_some_and(|revision|revision!=current.revision) {return Err(ServiceError::Conflict("A2A revision changed".into()));}
            let revision=current.revision.checked_add(1).ok_or_else(||ServiceError::Conflict("A2A revision exhausted".into()))?;
            match self.core.commit_card(A2aBotRecord {catalog_labels:labels.clone(),revision,..current},revision-1).await {
                Err(ServiceError::Conflict(_)) if expected.is_none()=>continue,
                result=>return result,
            }
        }
        Err(ServiceError::Conflict("A2A configuration changed concurrently".into()))
    }
    async fn get(&self,provider_id:&str,token:&str,bot_uuid:&str)->ServiceResult<Option<A2aBotRecord>> {
        self.authorize(provider_id,token).await?;self.core.provider_binding(provider_id,bot_uuid).await?;self.core.get_card(bot_uuid).await
    }
    async fn configure(&self,command:ConfigureA2aBotCommand)->ServiceResult<A2aBotRecord> {
        self.authorize(&command.provider_id,&command.provider_admin_token).await?;
        let Some(labels)=command.catalog_labels else {
            return self.configure_preserving_labels(command).await;
        };
        let labels=self.policy.normalize(labels)?;
        if let Some(expected)=command.expected_revision {
            return self.commit(command.provider_id,command.bot_uuid,command.source,expected,false,labels).await;
        }
        let binding=self.core.provider_binding(&command.provider_id,&command.bot_uuid).await?;
        let (agent_card_url,card)=self.load_card(&command.provider_id,command.source).await?;
        self.core.replace_card(A2aBotRecord {catalog_labels:labels,bot_uuid:command.bot_uuid,provider_id:command.provider_id,
            provider_bot_ref:binding.provider_bot_ref,agent_card_url,agent_card:card.raw,revision:0}).await
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
