use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot::{ActorDirectory, Bot, BotCore, BotOnboarding, HumanActor};
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::{
    ActorStatus, AgentCredentials, BotCapabilities, BotConnectParams, BotDynamicStatus,
    BotRegistryCoreService, ConnectionKind, EnsureHumanResult, RegisteredBot, ServiceResult,
};
use bcs_test_support::{
    NoopBotRegistryCoreService, NoopFriendCoreService, NoopRelationCoreService,
};

#[tokio::test]
async fn bot_core_passes_core_contract() {
    let registry = BotCore::new();

    bcs_test_support::contract::core::bot_registry_core_service_contract_tests(&registry).await;
}

#[tokio::test]
async fn bot_core_wrapping_memory_repo_passes_core_contract() {
    let repo = Arc::new(MemoryBotRepo::new());
    let svc = BotCore::with_repo(repo.clone());

    bcs_test_support::contract::core::bot_registry_core_service_contract_tests(&svc).await;
    assert!(repo.get("bcs-contract-missing-bot").await.is_none());
}

#[tokio::test]
async fn bot_core_connect_bot_owns_connection_flow() {
    let repo = Arc::new(NoConnectBotRepo::new());
    let svc = BotCore::with_repo(repo.clone());

    let result = svc
        .connect_bot(
            BotConnectParams {
                bot_id: Some("core-owned-bot".to_string()),
                token: None,
                protocol_version: None,
                client_kind: None,
            },
            ConnectionKind::Http,
        )
        .await
        .expect("connect via core");

    assert!(result.is_new);
    assert_eq!(result.bot_uuid, "core-owned-bot");
    assert_eq!(
        repo.find_bot_by_token(&result.token).await.as_deref(),
        Some("core-owned-bot")
    );
}

#[tokio::test]
async fn bot_core_connect_default_bot_does_not_auto_create_antding_binding() {
    let data_dir = tempfile::tempdir().expect("temp data dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(data_dir.path().to_path_buf()));
    let svc = BotCore::with_repo(repo.clone());

    let result = svc
        .connect_bot(
            BotConnectParams {
                bot_id: Some("default:11111111".to_string()),
                token: None,
                protocol_version: None,
                client_kind: None,
            },
            ConnectionKind::Streaming,
        )
        .await
        .expect("connect default bot");

    assert!(result.is_new);
    let bot = repo
        .get("default:11111111")
        .await
        .expect("default bot should be registered");
    assert!(bot.capabilities.binding_channels.is_none());
    assert_eq!(
        repo.find_bot_by_binding_channel("antding", "11111111").await,
        None
    );
}

#[tokio::test]
async fn bot_use_cases_pass_application_contracts() {
    let svc = Bot::new_with_friend(
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
    );

    bcs_test_support::contract::application::bot_query_service_contract_tests(&svc).await;
    bcs_test_support::contract::application::bot_discovery_service_contract_tests(&svc).await;
    bcs_test_support::contract::application::bot_management_service_contract_tests(&svc).await;
    bcs_test_support::contract::application::bot_runtime_connection_service_contract_tests(&svc)
        .await;
}

struct NoConnectBotRepo {
    inner: MemoryBotRepo,
}

impl NoConnectBotRepo {
    fn new() -> Self {
        Self {
            inner: MemoryBotRepo::new(),
        }
    }
}

#[async_trait]
impl BotRepoPort for NoConnectBotRepo {
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.inner.register(bot_id, capabilities).await
    }

    async fn update_status(&self, bot_id: &str, status: BotDynamicStatus) -> bool {
        self.inner.update_status(bot_id, status).await
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.inner.get(bot_id).await
    }

    async fn get_agent_credentials(&self, bot_id: &str) -> Option<AgentCredentials> {
        self.inner.get_agent_credentials(bot_id).await
    }

    async fn get_by_ids(&self, bot_ids: &[String]) -> Vec<RegisteredBot> {
        self.inner.get_by_ids(bot_ids).await
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.inner.list_active().await
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        self.inner.list_bots_by_creator(created_by).await
    }

    async fn discover(&self, query: &str) -> Vec<RegisteredBot> {
        self.inner.discover(query).await
    }

    async fn find_by_skills(&self, skills: &[&str]) -> Vec<RegisteredBot> {
        self.inner.find_by_skills(skills).await
    }

    async fn find_by_domains(&self, domains: &[&str]) -> Vec<RegisteredBot> {
        self.inner.find_by_domains(domains).await
    }

    async fn find_by_scopes(&self, scopes: &[&str]) -> Vec<RegisteredBot> {
        self.inner.find_by_scopes(scopes).await
    }

    async fn find_by_name(&self, name: &str) -> Vec<RegisteredBot> {
        self.inner.find_by_name(name).await
    }

    async fn list_all_bots(&self) -> Vec<RegisteredBot> {
        self.inner.list_all_bots().await
    }

    async fn list_bots_by_name_and_cooperatable_with(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        friend_uuids: &HashSet<String>,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        self.inner
            .list_bots_by_name_and_cooperatable_with(
                name,
                bot_uuid,
                cooperatable_only,
                friend_uuids,
                offset,
                limit,
            )
            .await
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.inner.unregister(bot_id).await
    }

    async fn cleanup_expired(&self) {
        self.inner.cleanup_expired().await
    }

    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities> {
        self.inner.load_from_storage(bot_id).await
    }

    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
        self.inner.save_to_storage(bot_id, caps).await
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        self.inner.update_visibility(bot_id, visibility).await
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, bot_id: &str, hidden: bool) -> ServiceResult<()> {
        self.inner.set_hidden(bot_id, hidden).await
    }

    async fn update_actor_status(&self, bot_id: &str, status: ActorStatus) -> ServiceResult<()> {
        self.inner.update_actor_status(bot_id, status).await
    }

    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<EnsureHumanResult> {
        self.inner.ensure_human_actor(staff_no, nick_name).await
    }

    async fn list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        self.inner.list_legacy_bots_for_owner(staff_no, env).await
    }

    async fn update_human_name(&self, staff_no: &str, new_name: &str) -> ServiceResult<()> {
        self.inner.update_human_name(staff_no, new_name).await
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.inner.has_been_onboarded(bot_id).await
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        self.inner
            .save_created_by(bot_id, created_by, overwrite)
            .await
    }

    async fn save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        self.inner.save_token(bot_id, token).await
    }

    async fn load_token(&self, bot_id: &str) -> Option<String> {
        self.inner.load_token(bot_id).await
    }

    async fn find_bot_by_token(&self, token: &str) -> Option<String> {
        self.inner.find_bot_by_token(token).await
    }

    async fn find_bot_by_binding_channel(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        self.inner
            .find_bot_by_binding_channel(channel, binding_key)
            .await
    }

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
        self.inner.register_streaming_connection(bot_id).await
    }

    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
        self.inner.reconnect_streaming(existing_token).await
    }

    async fn disconnect_streaming(&self, bot_id: &str) {
        self.inner.disconnect_streaming(bot_id).await
    }

    async fn is_connected(&self, bot_id: &str) -> bool {
        self.inner.is_connected(bot_id).await
    }

    async fn is_effectively_online(&self, bot_id: &str) -> bool {
        self.inner.is_effectively_online(bot_id).await
    }

    async fn send_frame(&self, bot_id: &str, frame: String) -> Result<(), ()> {
        self.inner.send_frame(bot_id, frame).await
    }

    async fn send_request(
        &self,
        bot_id: &str,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        self.inner
            .send_request(bot_id, method, params, timeout_ms)
            .await
    }

    async fn resolve_pending_request(&self, request_id: &str, response: serde_json::Value) {
        self.inner
            .resolve_pending_request(request_id, response)
            .await
    }

    async fn list_connected(&self) -> Vec<String> {
        self.inner.list_connected().await
    }

    async fn store_token_mapping(&self, token: String, bot_id: String) {
        self.inner.store_token_mapping(token, bot_id).await
    }

    async fn get_protocol_version(&self, bot_id: &str) -> u32 {
        self.inner.get_protocol_version(bot_id).await
    }

    async fn set_protocol_version(&self, bot_id: &str, version: u32) {
        self.inner.set_protocol_version(bot_id, version).await
    }

    async fn register_http_connection(&self, bot_id: String, token: String) -> String {
        self.inner.register_http_connection(bot_id, token).await
    }
}

#[tokio::test]
async fn actor_directory_passes_application_contract() {
    let svc = ActorDirectory::new(
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        Arc::new(NoopRelationCoreService),
    );

    bcs_test_support::contract::application::actor_directory_service_contract_tests(&svc).await;
}

#[tokio::test]
async fn bot_onboarding_passes_application_contract() {
    let svc = BotOnboarding::new(
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopRelationCoreService),
        false,
        None,
    );

    bcs_test_support::contract::application::bot_onboarding_service_contract_tests(&svc).await;
}

#[tokio::test]
async fn human_actor_passes_application_contract() {
    let svc = HumanActor::new(
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopRelationCoreService),
    );

    bcs_test_support::contract::application::human_actor_service_contract_tests(&svc).await;
}
