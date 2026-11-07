use super::*;

#[derive(Default)]
pub(super) struct FakeRegistry {
    bots: RwLock<HashMap<String, RegisteredBot>>,
}

impl FakeRegistry {
    pub(super) fn insert(&self, bot: RegisteredBot) {
        self.bots.write().unwrap().insert(bot.bot_uuid.clone(), bot);
    }
}

#[async_trait]
impl BotRegistryCoreService for FakeRegistry {
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.bots.write().unwrap().insert(
            bot_id.clone(),
            RegisteredBot {
                bot_uuid: bot_id,
                capabilities,
                env: None,
                created_by: None,
                actor_kind: ActorKind::Bot,
                status: Default::default(),
            },
        );
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str) -> bool {
        true
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots.read().unwrap().get(bot_id).cloned()
    }

    async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
        None
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.bots.read().unwrap().values().cloned().collect()
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        self.bots
            .read()
            .unwrap()
            .values()
            .filter(|bot| bot.created_by.as_deref() == Some(created_by))
            .cloned()
            .collect()
    }

    async fn list_uninitialized_bots_by_creator(
        &self,
        _created_by: &str,
        _limit: usize,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        Ok(Vec::new())
    }

    async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
        self.list_active().await
    }

    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.bots.write().unwrap().remove(bot_id).is_some()
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(&self, _bot_id: &str, _caps: &BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().unwrap().get_mut(bot_id) {
            bot.capabilities.visibility = visibility.to_string();
        }
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.bots.read().unwrap().contains_key(bot_id)
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().unwrap().get_mut(bot_id) {
            if overwrite || bot.created_by.is_none() {
                bot.created_by = Some(created_by.to_string());
            }
        }
        Ok(())
    }

    async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }

    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }

    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }

    async fn reconnect_streaming(&self, _existing_token: String) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, _bot_id: &str) -> bool {
        false
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        Vec::new()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }
}
