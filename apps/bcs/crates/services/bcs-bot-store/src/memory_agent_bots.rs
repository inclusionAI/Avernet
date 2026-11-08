use bcs_service_api::types::agent_registration::{AgentBotQuery, AgentBotRecord};
use bcs_service_api::types::AgentBotRegistration;
use bcs_service_api::{ActorKind, RegisteredBot, ServiceError, ServiceResult};
use super::{MemoryBotRepo, BotRepoPort};

impl MemoryBotRepo {
    pub(super) async fn save_affiliation_marker(&self, record: &bcs_service_api::bot_provider::BotProviderRecord) -> ServiceResult<()> {
        use std::io::Write;
        let target = self.bot_info_path(&record.bot_uuid).with_file_name("provider-identity.json");
        let parent = target.parent().ok_or_else(|| ServiceError::InternalError("Invalid Bot metadata path".into()))?;
        tokio::fs::create_dir_all(parent).await?;
        let content = serde_json::to_vec(record).map_err(|_| ServiceError::InternalError("Invalid Bot affiliation".into()))?;
        let mut file = tempfile::NamedTempFile::new_in(parent)?;
        file.write_all(&content)?;
        file.as_file().sync_all()?;
        // No token or request-controlled mode is retained in this marker.
        match file.persist_noclobber(&target) {
            Ok(_) => {},
            Err(error) if error.error.kind() == std::io::ErrorKind::AlreadyExists => {},
            Err(_) => return Err(ServiceError::InternalError("Cannot persist Bot affiliation".into())),
        }
        Ok(())
    }

    async fn with_affiliation(&self, mut bot: AgentBotRecord) -> ServiceResult<AgentBotRecord> {
        let target = self.bot_info_path(&bot.bot.bot_id).with_file_name("provider-identity.json");
        match tokio::fs::read(target).await {
            Ok(content) => {
                let record: bcs_service_api::bot_provider::BotProviderRecord = serde_json::from_slice(&content)
                    .map_err(|_| ServiceError::InternalError("Invalid Bot affiliation marker".into()))?;
                if record.bot_uuid != bot.bot.bot_id { return Err(ServiceError::InternalError("Bot affiliation mismatch".into())); }
                bot.bot.provider_id = Some(record.provider_id);
                bot.bot.provider_bot_ref = Some(record.provider_bot_ref);
                bot.binding_disabled = record.is_deleted;
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
            Err(error) => return Err(error.into()),
        }
        Ok(bot)
    }
    pub(super) async fn load_agent_bots(&self, query: &AgentBotQuery) -> ServiceResult<Vec<AgentBotRecord>> {
        if (query.agent_code.is_empty() && query.bot_uuid.is_none()) || !(1..=101).contains(&query.limit) {
            return Err(ServiceError::InternalError("invalid Agent Bot query".into()));
        }
        let deleted = self.deleted_bot_ids.read().await.clone();
        let mut bots = self.bots.read().await.iter()
            .map(|(id, bot)| (id.clone(), bot.to_registered_bot())).collect::<std::collections::BTreeMap<_, _>>();
        // Disk registrations remain available before a Bot reconnects after restart.
        match tokio::fs::read_dir(&self.bots_base_dir).await {
            Ok(mut entries) => while let Some(entry) = entries.next_entry().await? {
                if !entry.file_type().await?.is_dir() { continue; }
                let id = entry.file_name().to_string_lossy().into_owned();
                if bots.contains_key(&id) || deleted.contains(&id) || id.starts_with("human_") { continue; }
                if let Some(bot) = self.disk_agent_bot(&id).await? { bots.insert(id, bot); }
            },
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
            Err(error) => return Err(error.into()),
        }
        let env = bcs_config::resolve_env_str();
        let candidates = bots.iter().filter(|(id, bot)| {
            !deleted.contains(*id) && bot.actor_kind == ActorKind::Bot
                && bot.env.as_deref().is_none_or(|stored| stored == env)
                && (query.agent_code.is_empty() || bot.capabilities.agent_code.as_deref() == Some(query.agent_code.as_str()))
                && query.bot_uuid.as_ref().is_none_or(|expected| expected == *id)
                && query.cursor.as_ref().is_none_or(|cursor| *id > cursor)
        }).take(query.limit).map(|(_, bot)| from_bot(bot.clone()));
        let mut result = Vec::new();
        for bot in candidates { result.push(self.with_affiliation(bot).await?); }
        Ok(result)
    }

    pub(super) async fn load_token_bot(&self, token: &str, actor: Option<&str>) -> ServiceResult<Option<AgentBotRecord>> {
        let Some(id) = self.find_bot_by_token(token).await else { return Ok(None); };
        if actor.is_some_and(|actor| actor != id) { return Ok(None); }
        let bot = match self.try_get(&id).await? {
            Some(bot) => bot,
            None => match self.disk_agent_bot(&id).await? { Some(bot) => bot, None => return Ok(None) },
        };
        if bot.actor_kind != ActorKind::Bot || bot.env.as_deref().is_some_and(|env| env != bcs_config::resolve_env_str()) { return Ok(None); }
        Ok(Some(self.with_affiliation(from_bot(bot)).await?))
    }

    async fn disk_agent_bot(&self, id: &str) -> ServiceResult<Option<RegisteredBot>> {
        let content = match tokio::fs::read_to_string(self.bot_info_path(id)).await {
            Ok(content) => content,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        let persisted: super::PersistedCapabilities = serde_json::from_str(&content)
            .map_err(|_| ServiceError::InternalError("invalid persisted Bot metadata".into()))?;
        if persisted.bot_id != id { return Err(ServiceError::InternalError("persisted Bot id mismatch".into())); }
        let mut capabilities = bcs_service_api::BotCapabilities::from(&persisted);
        capabilities.agent_token = None;
        Ok(Some(RegisteredBot { bot_uuid:id.into(), capabilities,
            env:Some(bcs_config::resolve_env_str()), created_by:persisted.created_by,
            actor_kind:ActorKind::Bot, status:bcs_service_api::ActorStatus::Online }))
    }
}

fn from_bot(bot: RegisteredBot) -> AgentBotRecord {
    AgentBotRecord {
        bot: AgentBotRegistration { bot_id: bot.bot_uuid, name: bot.capabilities.name, summary: bot.capabilities.summary, provider_id: None, provider_bot_ref: None },
        agent_code: bot.capabilities.agent_code.unwrap_or_default(),
        owner_id: bot.created_by, provider_auth_mode: None,
        provider_disabled: false, binding_disabled: false,
        status: serde_json::to_value(bot.status).ok().and_then(|v| v.as_str().map(str::to_string)).unwrap_or_default(),
        provider_metadata_loaded: false,
    }
}
