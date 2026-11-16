use std::sync::Arc;

use bcs_test_support::LazySeedAuthorityHook;
use bcs_message_flow::a2a_chat::{A2aChat, ChatRunStore, DrainOutcome, drain_chat_event, drain_chat_event_with_mode};
use bcs_protocol::BcsFrame;
use bcs_domain::{Organization, OrganizationMember};
use bcs_service_api::{
    A2aChatCommand, A2aChatRunService, A2aChatService, ActorKind, ActorStatus, AgentCredentials,
    AsyncA2aChatCommand, BotActor, BotCapabilities, BotDeliveryCommand,
    BotDeliveryKind, BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget,
    BotRegistryCoreService, CallerContext, ChatResponseMode, ChatRunCancelCommand, ChatRunCleanupPort, ChatRunEventPort, ChatRunQueryCommand,
    AuthorizedOrganizationPair, DirectChatClientKind, DirectChatRunSnapshotPort, FriendCoreService, OrganizationCoreService, RegisteredBot,
    OrganizationCandidateBot, OrganizationCandidateQuery, ProviderTransportPreference,
    ServiceError, ServiceResult,
    interceptor::{
        BlockReason, InterceptorChain, InterceptorDecision, MessageInterceptor, OutboundMessage,
    },
};
use std::collections::{HashMap, HashSet};
use std::time::Duration;
use tokio::sync::{Mutex, Notify, RwLock, mpsc};

use crate::flow_support as support;


/// Real authority chain for the Fixture
/// builders (bot-authority plan Task 6.5):
/// every `created_by: Some(owner)` fixture Bot gets a live
/// owner edge; the legacy `created_by` check it mirrors is
/// gone.
fn staff_authority(pairs: &[(&str, &str)]) -> Arc<LazySeedAuthorityHook> {
    let chain = bcs_test_support::lazy_authority_chain();
    for (bot_id, owner) in pairs {
        chain.hook.seed_owner(bot_id, owner);
    }
    chain.hook
}

const STAFF_PAIRS: &[(&str, &str)] = &[("bot-source", "owner-1")];

fn staff_authority_default() -> Arc<LazySeedAuthorityHook> {
    assert!(STAFF_PAIRS.iter().any(|(id, _)| *id == "bot-source"));
    staff_authority(STAFF_PAIRS)
}

fn chat_command(target_bot_id: &str) -> A2aChatCommand {
    A2aChatCommand {
        caller: CallerContext::Bot(BotActor {
            bot_uuid: "bot-source".to_string(),
        }),
        target_bot_id: target_bot_id.to_string(),
        message: "hello".to_string(),
        from_actor_id: Some("api-user".to_string()),
        run_id: Some("run-1".to_string()),
        async_mode: true,
        session_key: Some("session-1".to_string()),
        timeout_ms: Some(10_000),
        client: Some("contract-test".to_string()),
        authenticated_staff_id: Some("owner-1".to_string()),
        tags: Vec::new(),
        response_mode: ChatResponseMode::Full,
        caller_wait_mode: None,
        organization_code: None,
        provider_bypass_headers: Vec::new(),
    }
}

fn scoped_chat_command(target_bot_id: &str) -> A2aChatCommand {
    A2aChatCommand {
        organization_code: Some("promo-2026".to_string()),
        ..chat_command(target_bot_id)
    }
}

async fn build_organization_service(
    bots: Vec<(&str, &str, Option<&str>)>,
    friendships: Vec<(&str, &str)>,
    members: Vec<&str>,
) -> A2aChat {
    let bot_delivery = Arc::new(RecordingDelivery::new(true));
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    let friend = Arc::new(StaticFriendCoreService::new(friendships));
    let owned: Vec<(&str, &str)> = bots
        .iter()
        .filter_map(|(bot_id, _, created_by)| {
            created_by.map(|owner| (*bot_id, owner))
        })
        .collect();
    for (bot_id, visibility, created_by) in bots {
        registry.insert(bot_id, visibility, created_by).await;
    }
    let organization = Arc::new(StaticOrganizationCoreService::new(members));
    A2aChat::new(bot_delivery, run_store, 30_000, registry, friend)
        .with_organization(organization)
        .with_bot_authority_hook(Some(staff_authority(&owned)))
}

async fn build_service(
    bots: Vec<(&str, &str, Option<&str>)>,
    friendships: Vec<(&str, &str)>,
) -> (A2aChat, Arc<RecordingDelivery>, Arc<MemoryRegistry>) {
    let bot_delivery = Arc::new(RecordingDelivery::new(true));
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    let friend = Arc::new(StaticFriendCoreService::new(friendships));
    let owned: Vec<(&str, &str)> = bots
        .iter()
        .filter_map(|(bot_id, _, created_by)| {
            created_by.map(|owner| (*bot_id, owner))
        })
        .collect();
    for (bot_id, visibility, created_by) in bots {
        registry.insert(bot_id, visibility, created_by).await;
    }
    (
        A2aChat::new(
            bot_delivery.clone(),
            run_store,
            30_000,
            registry.clone(),
            friend,
        )
        .with_bot_authority_hook(Some(staff_authority(&owned))),
        bot_delivery,
        registry,
    )
}

async fn build_run_service(
    events: Vec<String>,
    keep_open_after_send: bool,
) -> (Arc<A2aChat>, Arc<RecordingRunPort>, Arc<ChatRunStore>) {
    let bot_delivery = Arc::new(support::RecordingBotDelivery::default());
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry
        .insert_named("bot-source", "Source Bot", "public", Some("owner-1"))
        .await;
    registry.insert("bot-target", "public", None).await;
    let run_port = Arc::new(RecordingRunPort::new(events, keep_open_after_send));
    let service = Arc::new(
        A2aChat::new_with_run_ports(
            bot_delivery,
            run_store.clone(),
            30_000,
            registry,
            Arc::new(StaticFriendCoreService::default()),
            run_port.clone(),
            run_port.clone(),
        )
        .with_bot_authority_hook(Some(staff_authority_default())),
    );
    (service, run_port, run_store)
}

fn chat_event(state: &str, text: &str) -> String {
    serde_json::json!({
        "type": "event",
        "event": "chat.event",
        "payload": {
            "state": state,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}]
            }
        }
    })
    .to_string()
}

fn chat_delta_text(text: &str) -> String {
    serde_json::json!({
        "type": "event",
        "event": "chat.event",
        "payload": {
            "state": "delta",
            "delta_text": text
        }
    })
    .to_string()
}

fn ping_event() -> String {
    serde_json::json!({
        "type": "event",
        "event": "ping",
        "payload": {}
    })
    .to_string()
}

fn detached_async_command(run_id: &str) -> AsyncA2aChatCommand {
    AsyncA2aChatCommand {
        caller: CallerContext::Bot(BotActor {
            bot_uuid: "bot-source".to_string(),
        }),
        target_bot_id: "bot-target".to_string(),
        message: "hello".to_string(),
        from_actor_id: Some("api-user".to_string()),
        run_channel_from: Some("api-user".to_string()),
        authenticated_staff_id: Some("owner-1".to_string()),
        tags: Vec::new(),
        run_id: run_id.to_string(),
        session_key: format!("session-{run_id}"),
        timeout_ms: 1_000,
        client: Some("http-chat-async".to_string()),
        response_mode: ChatResponseMode::Full,
        caller_wait_mode: Some("detached".to_string()),
        organization_code: None,
        provider_bypass_headers: Vec::new(),
    }
}

fn tool_call_event(state: &str) -> String {
    serde_json::json!({
        "type": "event",
        "event": "chat.event",
        "payload": {
            "state": state,
            "tool_call_id": "tool-1",
            "tool_name": "lookup"
        }
    })
    .to_string()
}

fn agent_tool_event() -> String {
    serde_json::json!({
        "type": "event",
        "event": "agent",
        "payload": {
            "run_id": "run-1",
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "lookup",
                "phase": "result",
                "toolCallId": "tool-1"
            }
        }
    })
    .to_string()
}



































#[derive(Default)]
struct MemoryRegistry {
    bots: RwLock<HashMap<String, RegisteredBot>>,
}

impl MemoryRegistry {
    async fn insert(&self, bot_id: &str, visibility: &str, created_by: Option<&str>) {
        self.insert_named(bot_id, bot_id, visibility, created_by).await;
    }

    async fn insert_named(
        &self,
        bot_id: &str,
        name: &str,
        visibility: &str,
        created_by: Option<&str>,
    ) {
        self.bots.write().await.insert(
            bot_id.to_string(),
            RegisteredBot {
                bot_uuid: bot_id.to_string(),
                capabilities: BotCapabilities {
                    name: Some(name.to_string()),
                    visibility: visibility.to_string(),
                    ..BotCapabilities::default()
                },
                env: None,
                created_by: created_by.map(str::to_string),
                actor_kind: ActorKind::Bot,
                status: ActorStatus::Online,
            },
        );
    }
}

#[async_trait::async_trait]
impl BotRegistryCoreService for MemoryRegistry {
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.bots.write().await.insert(
            bot_id.clone(),
            RegisteredBot {
                bot_uuid: bot_id,
                capabilities,
                env: None,
                created_by: None,
                actor_kind: ActorKind::Bot,
                status: ActorStatus::Online,
            },
        );
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str) -> bool {
        false
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots.read().await.get(bot_id).cloned()
    }

    async fn get_agent_credentials(&self, bot_id: &str) -> Option<AgentCredentials> {
        // Synthetic credentials for tests so the outbound interceptor chain
        // runs through to BlockingInterceptor / SecurityInterceptor. Mirrors
        // the helper in message_flow_contract_support.rs.
        if self.bots.read().await.contains_key(bot_id) {
            Some(AgentCredentials {
                agent_code: Some(format!("test-agent-{bot_id}")),
                agent_token: Some(format!("test-token-{bot_id}")),
            })
        } else {
            None
        }
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.bots.read().await.values().cloned().collect()
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        self.bots
            .read()
            .await
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
        Vec::new()
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
        self.bots.write().await.remove(bot_id).is_some()
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(&self, _bot_id: &str, _caps: &BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().await.get_mut(bot_id) {
            bot.capabilities.visibility = visibility.to_string();
        }
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.bots.read().await.contains_key(bot_id)
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().await.get_mut(bot_id) {
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

    async fn is_connected(&self, bot_id: &str) -> bool {
        self.bots.read().await.contains_key(bot_id)
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Ok(())
    }

    async fn list_connected(&self) -> Vec<String> {
        self.bots.read().await.keys().cloned().collect()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }
}


#[derive(Clone)]
struct StaticOrganizationCoreService {
    members: HashSet<String>,
    disabled: bool,
}

impl StaticOrganizationCoreService {
    fn new(members: Vec<&str>) -> Self {
        Self {
            members: members.into_iter().map(str::to_string).collect(),
            disabled: false,
        }
    }

    fn disabled(mut self) -> Self {
        self.disabled = true;
        self
    }

    fn member(bot_uuid: &str) -> OrganizationMember {
        OrganizationMember {
            env: "test".to_string(),
            organization_code: "promo-2026".to_string(),
            bot_uuid: bot_uuid.to_string(),
            role: None,
            disabled: false,
            created_at: 1,
            updated_at: 1,
        }
    }

    fn organization() -> Organization {
        Organization {
            env: "test".to_string(),
            code: "promo-2026".to_string(),
            name: "Promo 2026".to_string(),
            description: None,
            managing_provider_id: "provider-a".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        }
    }
}

#[async_trait::async_trait]
impl OrganizationCoreService for StaticOrganizationCoreService {
    async fn create(&self, _managing_provider_id: &str, _code: &str, _name: &str, _description: Option<&str>) -> ServiceResult<Organization> {
        Ok(Self::organization())
    }

    async fn get_for_manager(&self, _managing_provider_id: &str, _code: &str) -> ServiceResult<Organization> {
        Ok(Self::organization())
    }

    async fn list_for_manager(&self, _managing_provider_id: &str, _include_disabled: bool) -> ServiceResult<Vec<Organization>> {
        Ok(vec![Self::organization()])
    }

    async fn update_for_manager(&self, _managing_provider_id: &str, _code: &str, _name: Option<&str>, _description: Option<Option<&str>>, _disabled: Option<bool>) -> ServiceResult<Organization> {
        Ok(Self::organization())
    }

    async fn put_member(&self, _managing_provider_id: &str, _organization_code: &str, bot_uuid: &str, _role: Option<&str>) -> ServiceResult<OrganizationMember> {
        Ok(Self::member(bot_uuid))
    }

    async fn delete_member(&self, _managing_provider_id: &str, _organization_code: &str, _bot_uuid: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_member_for_manager(&self, _managing_provider_id: &str, _organization_code: &str, bot_uuid: &str) -> ServiceResult<Option<OrganizationMember>> {
        Ok(self.members.contains(bot_uuid).then(|| Self::member(bot_uuid)))
    }

    async fn list_members_for_manager(&self, _managing_provider_id: &str, _organization_code: &str, _include_disabled: bool, _role: Option<&str>) -> ServiceResult<Vec<OrganizationMember>> {
        Ok(self.members.iter().map(|bot| Self::member(bot)).collect())
    }

    async fn candidate_bots(&self, _managing_provider_id: &str, _query: OrganizationCandidateQuery) -> ServiceResult<Vec<OrganizationCandidateBot>> {
        Ok(Vec::new())
    }

    async fn require_effective_member(&self, _organization_code: &str, bot_uuid: &str) -> ServiceResult<OrganizationMember> {
        if self.disabled {
            return Err(ServiceError::Forbidden("organization_disabled".to_string()));
        }
        if self.members.contains(bot_uuid) {
            Ok(Self::member(bot_uuid))
        } else {
            Err(ServiceError::Forbidden("organization_member_required".to_string()))
        }
    }

    async fn list_effective_members(&self, _organization_code: &str, _role: Option<&str>) -> ServiceResult<Vec<OrganizationMember>> {
        if self.disabled {
            return Err(ServiceError::Forbidden("organization_disabled".to_string()));
        }
        Ok(self.members.iter().map(|bot| Self::member(bot)).collect())
    }

    async fn require_runtime_member(&self, organization_code: &str, bot_uuid: &str) -> ServiceResult<OrganizationMember> {
        self.require_effective_member(organization_code, bot_uuid).await
    }

    async fn list_runtime_members(&self, organization_code: &str, role: Option<&str>) -> ServiceResult<Vec<OrganizationMember>> {
        self.list_effective_members(organization_code, role).await
    }

    async fn authorize_pair(&self, organization_code: &str, sender_bot_uuid: &str, target_bot_uuid: &str) -> ServiceResult<AuthorizedOrganizationPair> {
        let sender = self.require_effective_member(organization_code, sender_bot_uuid).await?;
        let target = self.require_effective_member(organization_code, target_bot_uuid).await?;
        Ok(AuthorizedOrganizationPair {
            organization: Self::organization(),
            sender,
            target,
        })
    }
}

#[derive(Default)]
struct StaticFriendCoreService {
    friendships: HashSet<(String, String)>,
}

impl StaticFriendCoreService {
    fn new(friendships: Vec<(&str, &str)>) -> Self {
        Self {
            friendships: friendships
                .into_iter()
                .map(|(a, b)| normalized_friendship(a, b))
                .collect(),
        }
    }
}

#[async_trait::async_trait]
impl FriendCoreService for StaticFriendCoreService {
    async fn list_friends(&self, bot_id: &str) -> Vec<String> {
        self.friendships
            .iter()
            .filter_map(|(a, b)| {
                if a == bot_id {
                    Some(b.clone())
                } else if b == bot_id {
                    Some(a.clone())
                } else {
                    None
                }
            })
            .collect()
    }

    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> bool {
        self.friendships
            .contains(&normalized_friendship(bot_a, bot_b))
    }

    async fn are_all_friends(&self, bot_id: &str, others: &[String]) -> ServiceResult<()> {
        let missing: Vec<String> = others
            .iter()
            .filter(|other| {
                !self
                    .friendships
                    .contains(&normalized_friendship(bot_id, other))
            })
            .cloned()
            .collect();
        if missing.is_empty() {
            Ok(())
        } else {
            Err(ServiceError::NotFriends(missing))
        }
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }
}

fn normalized_friendship(a: &str, b: &str) -> (String, String) {
    if a <= b {
        (a.to_string(), b.to_string())
    } else {
        (b.to_string(), a.to_string())
    }
}

struct RecordingDelivery {
    connected: bool,
    frames: RwLock<Vec<BcsFrame>>,
    provider_transports: RwLock<Vec<ProviderTransportPreference>>,
}

impl RecordingDelivery {
    fn new(connected: bool) -> Self {
        Self {
            connected,
            frames: RwLock::new(Vec::new()),
            provider_transports: RwLock::new(Vec::new()),
        }
    }

    async fn frames(&self) -> Vec<BcsFrame> {
        self.frames.read().await.clone()
    }

    async fn provider_transports(&self) -> Vec<ProviderTransportPreference> {
        self.provider_transports.read().await.clone()
    }
}

struct RecordingRunPort {
    events: Vec<String>,
    keep_open_after_send: bool,
    event_unregistered: Mutex<Vec<String>>,
    cleanup_unregistered: Mutex<Vec<String>>,
    open_senders: RwLock<HashMap<String, mpsc::Sender<String>>>,
    notify: Notify,
}

impl RecordingRunPort {
    fn new(events: Vec<String>, keep_open_after_send: bool) -> Self {
        Self {
            events,
            keep_open_after_send,
            event_unregistered: Mutex::new(Vec::new()),
            cleanup_unregistered: Mutex::new(Vec::new()),
            open_senders: RwLock::new(HashMap::new()),
            notify: Notify::new(),
        }
    }

    async fn cleanup_unregistered(&self) -> Vec<String> {
        self.cleanup_unregistered.lock().await.clone()
    }

    async fn send_event(&self, run_id: &str, event: String) {
        let sender = self
            .open_senders
            .read()
            .await
            .get(run_id)
            .cloned()
            .unwrap_or_else(|| panic!("{run_id} is not registered"));
        sender.send(event).await.unwrap();
    }

    async fn wait_for_event_unregister(&self, run_id: &str) {
        let timeout = tokio::time::sleep(Duration::from_secs(1));
        tokio::pin!(timeout);
        loop {
            if self
                .event_unregistered
                .lock()
                .await
                .iter()
                .any(|recorded| recorded == run_id)
            {
                return;
            }
            tokio::select! {
                _ = self.notify.notified() => {}
                _ = &mut timeout => panic!("timed out waiting for {run_id} to unregister"),
            }
        }
    }
}

#[async_trait::async_trait]
impl ChatRunEventPort for RecordingRunPort {
    async fn register(
        &self,
        run_id: String,
        _session_key: String,
        sender: mpsc::Sender<String>,
        _source: Option<String>,
        _from: Option<String>,
    ) {
        for event_json in &self.events {
            sender.send(event_json.clone()).await.unwrap();
        }
        if self.keep_open_after_send {
            self.open_senders.write().await.insert(run_id, sender);
        }
    }

    async fn unregister(&self, run_id: &str) {
        self.open_senders.write().await.remove(run_id);
        self.event_unregistered
            .lock()
            .await
            .push(run_id.to_string());
        self.notify.notify_waiters();
    }
}

#[async_trait::async_trait]
impl ChatRunCleanupPort for RecordingRunPort {
    async fn unregister(&self, run_id: &str) {
        self.open_senders.write().await.remove(run_id);
        self.cleanup_unregistered
            .lock()
            .await
            .push(run_id.to_string());
        self.notify.notify_waiters();
    }
}

#[async_trait::async_trait]
impl BotDeliveryPort for RecordingDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        self.connected
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let target_bot_id = cmd.target_bot_id().to_string();
        self.provider_transports
            .write()
            .await
            .push(cmd.provider_transport);
        self.frames.write().await.push(cmd.frame);
        Ok(BotDeliveryResult {
            target_bot_id,
            delivered: self.connected,
            error: None,
        })
    }
}

struct A2aBlockingInterceptor;

#[async_trait::async_trait]
impl MessageInterceptor for A2aBlockingInterceptor {
    async fn on_outbound(&self, _msg: &mut OutboundMessage) -> InterceptorDecision {
        InterceptorDecision::Block(BlockReason {
            interceptor_id: "test-block".to_string(),
            code: "blocked".to_string(),
            message: "a2a chat blocked by test".to_string(),
            user_visible: true,
        })
    }
}


#[path = "contract_a2a_chat/run_start.rs"]
mod run_start;
#[path = "contract_a2a_chat/run_terminal.rs"]
mod run_terminal;
#[path = "contract_a2a_chat/event_parser.rs"]
mod event_parser;
#[path = "contract_a2a_chat/access.rs"]
mod access;
#[path = "contract_a2a_chat/organization.rs"]
mod organization;
