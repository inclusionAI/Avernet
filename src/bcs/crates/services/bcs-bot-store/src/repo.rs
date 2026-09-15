use super::*;

#[async_trait]
impl BotRepoPort for PersistentBotRepo {
    async fn connect_streaming(&self, params: bcs_service_api::BotConnectParams)
        -> Result<bcs_service_api::BotConnectResult, bcs_service_api::ConnectError>
    {
        self.admit_streaming(params).await
    }

    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_register(bot_id, capabilities).await
    }

    async fn update_capabilities(
        &self,
        bot_id: &str,
        capabilities: BotCapabilities,
    ) -> ServiceResult<()> {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_update_capabilities(bot_id, capabilities).await
    }

    async fn register_with_owner_and_token(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<()> {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_register_with_owner_and_token(bot_id, capabilities, created_by, token).await
    }

    async fn update_status(&self, bot_id: &str) -> bool {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_update_status(bot_id).await
    }

    async fn get_by_ids(&self, bot_ids: &[String]) -> Vec<RegisteredBot> {
        self.repo_get_by_ids(bot_ids).await
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.repo_get(bot_id).await
    }

    async fn try_get(&self, bot_id: &str) -> ServiceResult<Option<RegisteredBot>> {
        self.repo_try_get(bot_id).await
    }

    async fn get_including_deleted(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.repo_get_including_deleted(bot_id).await
    }

    async fn get_agent_credentials(
        &self,
        bot_id: &str,
    ) -> Option<bcs_service_api::AgentCredentials> {
        self.repo_get_agent_credentials(bot_id).await
    }

    async fn add_bot_info(&self, bot_id: &str, key: &str, value: String) {
        let _identity = self.identity_locks.lock(bot_id).await;
        self.repo_add_bot_info(bot_id, key, value).await
    }

    async fn get_bot_info(&self, bot_id: &str, key: &str) -> Option<String> {
        self.repo_get_bot_info(bot_id, key).await
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.repo_list_active().await
    }

    async fn list_all_bots(&self) -> Vec<RegisteredBot> {
        self.repo_list_all_bots().await
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        self.repo_list_bots_by_creator(created_by).await
    }

    async fn try_list_bots_by_creator(
        &self,
        created_by: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        self.repo_try_list_bots_by_creator(created_by).await
    }

    async fn discover(&self, query: &str) -> Vec<RegisteredBot> {
        self.repo_discover(query).await
    }

    async fn find_by_skills(&self, skills: &[&str]) -> Vec<RegisteredBot> {
        self.repo_find_by_skills(skills).await
    }

    async fn find_by_domains(&self, domains: &[&str]) -> Vec<RegisteredBot> {
        self.repo_find_by_domains(domains).await
    }

    async fn find_by_scopes(&self, scopes: &[&str]) -> Vec<RegisteredBot> {
        self.repo_find_by_scopes(scopes).await
    }

    async fn list_bots_by_name_and_cooperatable_with(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        _friend_uuids: &std::collections::HashSet<String>,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        self.repo_list_bots_by_name_and_cooperatable_with(name, bot_uuid, cooperatable_only, _friend_uuids, offset, limit).await
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.repo_unregister(bot_id).await
    }

    async fn soft_delete(&self, bot_id: &str) -> bool {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_soft_delete(bot_id).await
    }

    async fn cleanup_expired(&self) {
        self.repo_cleanup_expired().await
    }

    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities> {
        self.repo_load_from_storage(bot_id).await
    }

    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_save_to_storage(bot_id, caps).await
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_update_visibility(bot_id, visibility).await
    }

    async fn set_hidden(&self, bot_id: &str, hidden: bool) -> ServiceResult<()> {
        self.repo_set_hidden(bot_id, hidden).await
    }

    async fn update_actor_status(
        &self,
        bot_id: &str,
        status: bcs_service_api::ActorStatus,
    ) -> ServiceResult<()> {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_update_actor_status(bot_id, status).await
    }

    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<bcs_service_api::EnsureHumanResult> {
        let _identity = self.identity_locks.lock(&format!("human_{staff_no}")).await;
        self.repo_ensure_human_actor(staff_no, nick_name).await
    }

    async fn list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        self.repo_list_legacy_bots_for_owner(staff_no, env).await
    }

    async fn update_human_name(&self, staff_no: &str, new_name: &str) -> ServiceResult<()> {
        let _identity = self.identity_locks.lock(&format!("human_{staff_no}")).await;
        self.repo_update_human_name(staff_no, new_name).await
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.repo_has_been_onboarded(bot_id).await
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_save_created_by(bot_id, created_by, overwrite).await
    }

    async fn save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_save_token(bot_id, token).await
    }

    async fn load_token(&self, bot_id: &str) -> Option<String> {
        self.repo_load_token(bot_id).await
    }

    async fn find_bot_by_token(&self, token: &str) -> Option<String> {
        self.repo_find_bot_by_token(token).await
    }

    async fn find_bot_by_agent_code(&self, agent_code: &str) -> Option<String> {
        self.repo_find_bot_by_agent_code(agent_code).await
    }

    async fn find_bot_by_binding_channel(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        self.repo_find_bot_by_binding_channel(channel, binding_key).await
    }

    async fn connect_or_promote_streaming(
        &self,
        bot_id: String,
    ) -> Result<String, ConnectStreamError> {
        self.admit_streaming(bcs_service_api::BotConnectParams { bot_id: Some(bot_id), ..Default::default() })
            .await.map(|result| result.token).map_err(|error| match error {
                bcs_service_api::ConnectError::AlreadyConnected(id) => ConnectStreamError::AlreadyConnected(id),
                bcs_service_api::ConnectError::AlreadyRegistered(id) => ConnectStreamError::AlreadyRegistered(id),
                other => ConnectStreamError::InternalError(other.to_string()),
            })
    }

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_register_streaming_connection(bot_id).await
    }

    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
        self.repo_reconnect_streaming(existing_token).await
    }

    async fn disconnect_streaming(&self, bot_id: &str) {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_disconnect_streaming(bot_id).await
    }

    async fn is_connected(&self, bot_id: &str) -> bool {
        self.repo_is_connected(bot_id).await
    }

    async fn send_frame(&self, bot_id: &str, _frame: String) -> Result<(), ()> {
        self.repo_send_frame(bot_id, _frame).await
    }

    async fn list_connected(&self) -> Vec<String> {
        self.repo_list_connected().await
    }

    async fn store_token_mapping(&self, token: String, bot_id: String) {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_store_token_mapping(token, bot_id).await
    }

    async fn register_http_connection(&self, bot_id: String, token: String) -> String {
        let _identity = self.identity_locks.lock(&bot_id).await;
        self.repo_register_http_connection(bot_id, token).await
    }

    async fn send_request(
        &self,
        bot_id: &str,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        self.repo_send_request(bot_id, method, params, timeout_ms).await
    }

    async fn resolve_pending_request(&self, request_id: &str, response: serde_json::Value) {
        self.repo_resolve_pending_request(request_id, response).await
    }
}
