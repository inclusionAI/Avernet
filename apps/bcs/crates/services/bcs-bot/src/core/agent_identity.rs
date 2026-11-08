//! Runtime identity resolution, shared by lookup consumers and parent discovery.
use bcs_service_api::types::agent_registration::{AgentBotQuery, AgentBotRecord};
use bcs_service_api::{ProviderAuthMode, ServiceError, ServiceResult};
use super::bot_core::BotCore;
use super::provider_core::parse_downlink_config;

impl BotCore {
    pub(super) async fn protect_provider_identity(&self, id: &str, mut caps: bcs_service_api::BotCapabilities) -> ServiceResult<bcs_service_api::BotCapabilities> {
        let affiliated = if let Some(metadata) = &self.bot_providers {
            metadata.get_provider_bot(id).await?.is_some()
        } else { false };
        let hard_affiliation = affiliated || self.repo.local_provider_affiliation(id).await?;
        let affiliated = hard_affiliation || if let Some(bindings) = &self.provider_bindings {
            bindings.get_binding_by_bot_uuid(id).await?.is_some()
        } else { false };
        if affiliated {
            let Some(saved) = self.repo.try_get(id).await? else {
                if hard_affiliation { return Err(denied("Provider Bot is unavailable")); }
                // Legacy delivery binding may precede its ordinary Bot row.
                return Ok(caps);
            };
            if caps.agent_code.is_some() && caps.agent_code != saved.capabilities.agent_code {
                return Err(denied("Provider Bot parent identity is immutable"));
            }
            caps.agent_code = saved.capabilities.agent_code;
        }
        Ok(caps)
    }

    pub(super) async fn signed_bot_identity(&self, id: &str) -> ServiceResult<()> {
        let query = AgentBotQuery { agent_code: String::new(), bot_uuid: Some(id.into()),
            provider_id: None, cursor: None, limit: 1, implicit_only: false };
        if let Some(bot) = self.agent_bots(&query).await?.first() {
            return validate(bot, false);
        }
        if self.repo.get_including_deleted(id).await.is_some()
            || if let Some(metadata) = &self.bot_providers { metadata.get_provider_bot(id).await?.is_some() } else { false } {
            return Err(denied("Bot has been deleted"));
        }
        // TC subjects can be authenticated without a local Bot registration.
        Ok(())
    }
    pub(super) async fn agent_bots(&self, query: &AgentBotQuery) -> ServiceResult<Vec<AgentBotRecord>> {
        let mut page_query = query.clone();
        let mut result = Vec::new();
        loop {
            let mut bots = self.repo.query_agent_bots(&page_query).await?;
            let count = bots.len();
            let cursor = bots.last().map(|bot| bot.bot.bot_id.clone());
            for bot in &mut bots { self.enrich_agent_identity(bot).await?; }
            if let Some(provider) = &query.provider_id { bots.retain(|bot| bot.bot.provider_id.as_ref() == Some(provider)); }
            result.extend(bots);
            if result.len() >= query.limit || count < page_query.limit || query.provider_id.is_none() { break; }
            page_query.cursor = cursor;
        }
        result.truncate(query.limit);
        Ok(result)
    }

    async fn enrich_agent_identity(&self, bot: &mut AgentBotRecord) -> ServiceResult<()> {
        if bot.provider_metadata_loaded { return Ok(()); }
        if let Some(metadata) = &self.bot_providers {
            if let Some(record) = metadata.get_provider_bot(&bot.bot.bot_id).await? {
                bot.bot.provider_id = Some(record.provider_id.clone());
                bot.bot.provider_bot_ref = Some(record.provider_bot_ref);
                bot.binding_disabled = record.is_deleted;
                if let Some(bindings) = &self.provider_bindings {
                    if let Some(binding) = bindings.get_binding_by_bot_uuid(&bot.bot.bot_id).await? {
                        if binding.provider_id != record.provider_id || Some(binding.provider_bot_ref.as_str()) != bot.bot.provider_bot_ref.as_deref() { return Err(denied("Bot Provider binding mismatch")); }
                        bot.binding_disabled |= binding.disabled;
                    }
                }
                let providers = self.provider_repo.as_ref().ok_or_else(|| failure("Provider lookup is not configured"))?;
                let provider = providers.get_provider(&record.provider_id).await?.ok_or_else(|| failure("Bot Provider is missing"))?;
                bot.provider_auth_mode = Some(parse_downlink_config(&provider.config)?.auth_mode);
                bot.provider_disabled = provider.disabled;
            }
        }
        // A persisted memory-store affiliation cannot become an unbound Bot
        // when process-local Provider configuration has not been restored.
        if bot.bot.provider_id.is_some() && bot.provider_auth_mode.is_none() {
            return Err(denied("Bot Provider identity is unavailable"));
        }
        bot.provider_metadata_loaded = true;
        Ok(())
    }

    pub(super) async fn agent_bot_identity(&self, agent_code: &str, actor: Option<&str>) -> ServiceResult<Option<AgentBotRecord>> {
        let mut query = AgentBotQuery { agent_code: agent_code.into(), bot_uuid: actor.map(str::to_string), provider_id: None, cursor: None, limit: if actor.is_some() { 1 } else { 2 }, implicit_only: actor.is_none() };
        if actor.is_some() {
            let bot = self.agent_bots(&query).await?.into_iter().next().ok_or_else(|| denied("Actor does not belong to this AgentPass"))?;
            validate(&bot, true)?;
            return Ok(Some(bot));
        }
        let mut chosen = None;
        loop {
            let bots = self.agent_bots(&query).await?;
            for bot in &bots {
                if bot.provider_auth_mode == Some(ProviderAuthMode::AgentPassSubbots) { continue; }
                if bot.provider_auth_mode.is_some_and(|mode| mode != ProviderAuthMode::AgentPass) {
                    return Err(denied("Provider does not permit AgentPass"));
                }
                if chosen.is_some() { return Err(ServiceError::Conflict("AgentPass default Bot is ambiguous".into())); }
                chosen = Some(bot.clone());
            }
            if bots.len() < query.limit { break; }
            query.cursor = bots.last().map(|bot| bot.bot.bot_id.clone());
        }
        if let Some(bot) = chosen { validate(&bot, true)?; return Ok(Some(bot)); }
        query.implicit_only = false; query.cursor = None; query.limit = 1;
        if !self.agent_bots(&query).await?.is_empty() { return Err(denied("ActorRequired: parent AgentPass requires a Bot Actor")); }
        Ok(None)
    }

    pub(super) async fn token_bot_identity(&self, token: &str, actor: Option<&str>) -> ServiceResult<Option<AgentBotRecord>> {
        let mut bot = self.repo.lookup_bot_token(token, actor).await?;
        if let Some(bot) = &mut bot { self.enrich_agent_identity(bot).await?; validate(bot, false)?; }
        else if actor.is_some() { return Err(denied("Actor does not match Bot token")); }
        Ok(bot)
    }
}

fn validate(bot: &AgentBotRecord, agentpass: bool) -> ServiceResult<()> {
    if bot.provider_disabled || bot.binding_disabled { return Err(denied("Bot Provider binding is disabled")); }
    if bot.bot.provider_id.is_some() && bot.provider_auth_mode.is_none() { return Err(denied("Bot Provider identity is unavailable")); }
    if agentpass && bot.provider_auth_mode.is_some_and(|mode| !matches!(mode, ProviderAuthMode::AgentPass | ProviderAuthMode::AgentPassSubbots)) {
        return Err(denied("Provider does not permit AgentPass"));
    }
    Ok(())
}
fn denied(message: &str) -> ServiceError { ServiceError::Unauthorized(message.into()) }
fn failure(message: &str) -> ServiceError { ServiceError::InternalError(message.into()) }
