use std::{collections::HashSet, sync::Arc};

use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotDetailCommand, BotDiscoveryCommand,
    BotDiscoveryService, BotLeaveCommand, BotListCommand, BotManagementService,
    BotPagedListCommand, BotQueryByIdsCommand, BotQueryService, BotRegistryCoreService,
    BotRuntimeConnectCommand, BotRuntimeConnectionService, BotRuntimeDisconnectCommand,
    BotRuntimeStatusCommand, BotStatusUpdateCommand, BotUseCaseError, BotVisibilityCommand,
    BotVisibilityQueryCommand, ConnectError, FriendCoreService, ProviderAuthMode,
    AuthorizedOrganizationPair, OrganizationCandidateBot, OrganizationCandidateQuery,
    OrganizationCoreService, ProviderBotBindingRepoPort, ProviderBotCoreService,
    ProviderCoreService, ProviderCredentialRepoPort, ProviderRepoPort, RegisterProviderBotParams,
    ServiceError, ServiceResult,
};
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_service_api::types::{Organization, OrganizationMember};
use bcs_bot_store::MemoryBotRepo;
use tempfile::TempDir;

use bcs_bot::{Bot, BotCore, ProviderCore};

struct RegistryFixture {
    registry: Arc<BotCore>,
    _data_dir: TempDir,
}

impl RegistryFixture {
    fn new() -> Self {
        let data_dir = tempfile::tempdir().expect("temp data dir");
        let registry = Arc::new(BotCore::with_base_dir(data_dir.path().to_path_buf()));
        Self {
            registry,
            _data_dir: data_dir,
        }
    }

    fn service(&self) -> Bot {
        let registry: Arc<dyn BotRegistryCoreService> = self.registry.clone();
        Bot::new(registry).with_bot_core(self.registry.clone())
    }

    fn service_with_friends(&self, friends: Vec<(&str, &str)>) -> Bot {
        let registry: Arc<dyn BotRegistryCoreService> = self.registry.clone();
        Bot::new_with_friend(registry, Arc::new(StaticFriendCoreService::new(friends)))
    }
}

struct ProviderRegistryFixture {
    registry: Arc<BotCore>,
    provider: ProviderCore,
    _data_dir: TempDir,
}

impl ProviderRegistryFixture {
    fn new() -> Self {
        let data_dir = tempfile::tempdir().expect("temp data dir");
        let provider_store = Arc::new(MemoryProviderStore::new());
        let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
        let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
        let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(data_dir.path().to_path_buf()));
        let registry = Arc::new(BotCore::with_provider_repos(
            bot_repo,
            provider_repo.clone(),
            provider_credentials.clone(),
            provider_bindings.clone(),
        ));
        let provider = ProviderCore::new(
            provider_repo,
            provider_credentials,
            provider_bindings,
            registry.clone(),
        );
        Self {
            registry,
            provider,
            _data_dir: data_dir,
        }
    }

    fn service(&self) -> Bot {
        let registry: Arc<dyn BotRegistryCoreService> = self.registry.clone();
        Bot::new(registry).with_bot_core(self.registry.clone())
    }

    async fn register_provider_bot(&self, owner: &str) -> String {
        let provider = self
            .provider
            .register_provider(
                "Provider".to_string(),
                "https://provider.example.com/bcs/webhook".to_string(),
                ProviderAuthMode::StaticBearer,
                owner.to_string(),
                None,
                None,
            )
            .await
            .expect("register provider");
        let (binding, _) = self
            .provider
            .register_provider_bot_with_bot_uuid(
                &provider.provider.provider_id,
                &provider.provider_admin_token,
                RegisterProviderBotParams {
                    bot_name: "Provider Bot".to_string(),
                    summary: Some("Provider-managed bot".to_string()),
                    owners: vec![owner.to_string()],
                    provider_bot_ref: "provider-bot-v1".to_string(),
                    ..Default::default()
                },
            )
            .await
            .expect("register provider bot");
        binding.bot_uuid
    }
}

#[derive(Default)]
struct StaticFriendCoreService {
    friends: HashSet<(String, String)>,
}

impl StaticFriendCoreService {
    fn new(friends: Vec<(&str, &str)>) -> Self {
        Self {
            friends: friends
                .into_iter()
                .map(|(a, b)| ordered_pair(a, b))
                .collect(),
        }
    }
}

#[async_trait::async_trait]
impl FriendCoreService for StaticFriendCoreService {
    async fn list_friends(&self, bot_id: &str) -> Vec<String> {
        self.friends
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
        self.friends.contains(&ordered_pair(bot_a, bot_b))
    }

    async fn are_all_friends(&self, bot_id: &str, others: &[String]) -> ServiceResult<()> {
        for other in others {
            if !self.are_friends(bot_id, other).await {
                return Err(ServiceError::NotFriends(vec![other.clone()]));
            }
        }
        Ok(())
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }
}

#[derive(Debug, Default)]
struct StaticOrganizationCoreService {
    members: Vec<OrganizationMember>,
    fail_requester: bool,
}

impl StaticOrganizationCoreService {
    fn with_members(members: Vec<OrganizationMember>) -> Self {
        Self {
            members,
            fail_requester: false,
        }
    }

    fn rejecting_requester() -> Self {
        Self {
            members: Vec::new(),
            fail_requester: true,
        }
    }
}

#[async_trait::async_trait]
impl OrganizationCoreService for StaticOrganizationCoreService {
    async fn create(&self, _: &str, _: &str, _: &str, _: Option<&str>) -> ServiceResult<Organization> {
        Err(ServiceError::InvalidOperation { message: "not implemented".to_string(), request_id: None })
    }

    async fn get_for_manager(&self, _: &str, _: &str) -> ServiceResult<Organization> {
        Err(ServiceError::InvalidOperation { message: "not implemented".to_string(), request_id: None })
    }

    async fn list_for_manager(&self, _: &str, _: bool) -> ServiceResult<Vec<Organization>> {
        Err(ServiceError::InvalidOperation { message: "not implemented".to_string(), request_id: None })
    }

    async fn update_for_manager(&self, _: &str, _: &str, _: Option<&str>, _: Option<Option<&str>>, _: Option<bool>) -> ServiceResult<Organization> {
        Err(ServiceError::InvalidOperation { message: "not implemented".to_string(), request_id: None })
    }

    async fn put_member(&self, _: &str, _: &str, _: &str, _: Option<&str>) -> ServiceResult<OrganizationMember> {
        Err(ServiceError::InvalidOperation { message: "not implemented".to_string(), request_id: None })
    }

    async fn delete_member(&self, _: &str, _: &str, _: &str) -> ServiceResult<()> {
        Err(ServiceError::InvalidOperation { message: "not implemented".to_string(), request_id: None })
    }

    async fn get_member_for_manager(&self, _: &str, _: &str, _: &str) -> ServiceResult<Option<OrganizationMember>> {
        Err(ServiceError::InvalidOperation { message: "not implemented".to_string(), request_id: None })
    }

    async fn list_members_for_manager(&self, _: &str, _: &str, _: bool, _: Option<&str>) -> ServiceResult<Vec<OrganizationMember>> {
        Err(ServiceError::InvalidOperation { message: "not implemented".to_string(), request_id: None })
    }

    async fn candidate_bots(&self, _: &str, _: OrganizationCandidateQuery) -> ServiceResult<Vec<OrganizationCandidateBot>> {
        Err(ServiceError::InvalidOperation { message: "not implemented".to_string(), request_id: None })
    }

    async fn require_effective_member(&self, organization_code: &str, bot_uuid: &str) -> ServiceResult<OrganizationMember> {
        if self.fail_requester {
            return Err(ServiceError::Forbidden("organization_member_required".to_string()));
        }
        self.members
            .iter()
            .find(|member| member.organization_code == organization_code && member.bot_uuid == bot_uuid)
            .cloned()
            .ok_or_else(|| ServiceError::Forbidden("organization_member_required".to_string()))
    }

    async fn list_effective_members(&self, organization_code: &str, role: Option<&str>) -> ServiceResult<Vec<OrganizationMember>> {
        Ok(self.members
            .iter()
            .filter(|member| member.organization_code == organization_code)
            .filter(|member| role.is_none_or(|role| member.role.as_deref() == Some(role)))
            .cloned()
            .collect())
    }

    async fn authorize_pair(&self, _: &str, _: &str, _: &str) -> ServiceResult<AuthorizedOrganizationPair> {
        Err(ServiceError::InvalidOperation { message: "not implemented".to_string(), request_id: None })
    }
}

fn org_member(bot_uuid: &str, role: &str) -> OrganizationMember {
    OrganizationMember {
        env: "test".to_string(),
        organization_code: "promo-2026".to_string(),
        bot_uuid: bot_uuid.to_string(),
        role: Some(role.to_string()),
        disabled: false,
        created_at: 1,
        updated_at: 1,
    }
}

fn ordered_pair(a: &str, b: &str) -> (String, String) {
    if a <= b {
        (a.to_string(), b.to_string())
    } else {
        (b.to_string(), a.to_string())
    }
}

#[tokio::test]
async fn list_bots_filters_paginates_and_maps_dtos() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "a-alpha",
        caps(Some("Alpha"), Some("Named bot"), "public"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "b-default:alice",
        caps(None, Some("Default helper"), "private"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "c-charlie",
        caps(Some("Charlie"), Some("Third bot"), "protected"),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "d-empty",
        caps(None, None, "protected"),
        None,
    )
    .await;
    fixture
        .registry
        .update_actor_status("b-default:alice", ActorStatus::Hidden)
        .await
        .expect("hide default bot");

    let result = service
        .list_bots(BotListCommand {
            caller_actor_id: Some("alice".to_string()),
            offset: 1,
            limit: 1,
            onboarded: Some(true),
        })
        .await
        .expect("list onboarded bots");

    assert_eq!(result.total, 3);
    assert_eq!(result.offset, 1);
    assert_eq!(result.limit, 1);
    assert_eq!(result.bots.len(), 1);
    let entry = &result.bots[0];
    assert_eq!(entry.bot_uuid, "b-default:alice");
    assert_eq!(entry.name, None);
    assert_eq!(entry.summary.as_deref(), Some("Default helper"));
    assert_eq!(entry.status, ActorStatus::Hidden);
    assert_eq!(entry.visibility, "private");
    assert_eq!(entry.owner_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(entry.created_by.as_deref(), Some("alice"));
    assert_eq!(
        entry.capabilities.summary.as_deref(),
        Some("Default helper")
    );

    let unonboarded = service
        .list_bots(BotListCommand {
            caller_actor_id: None,
            offset: 0,
            limit: 10,
            onboarded: Some(false),
        })
        .await
        .expect("list unonboarded bots");

    assert_eq!(unonboarded.total, 2);
    let unonboarded_ids: Vec<&str> = unonboarded
        .bots
        .iter()
        .map(|entry| entry.bot_uuid.as_str())
        .collect();
    assert_eq!(unonboarded_ids, vec!["b-default:alice", "d-empty"]);
}

#[tokio::test]
async fn get_bot_returns_detail_dto_and_not_found_error() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "detail-bot",
        caps(Some("Detail"), Some("Detail summary"), "private"),
        Some("alice"),
    )
    .await;
    fixture
        .registry
        .update_actor_status("detail-bot", ActorStatus::Hidden)
        .await
        .expect("hide detail bot");

    let detail = service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "detail-bot".to_string(),
        })
        .await
        .expect("bot detail");

    assert_eq!(detail.bot_uuid, "detail-bot");
    assert_eq!(detail.capabilities.name.as_deref(), Some("Detail"));
    assert_eq!(
        detail.capabilities.summary.as_deref(),
        Some("Detail summary")
    );
    assert_eq!(detail.status, ActorStatus::Hidden);
    assert_eq!(detail.visibility, "private");
    assert_eq!(detail.owner_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(detail.created_by.as_deref(), Some("alice"));
    assert_eq!(detail.actor_kind, ActorKind::Bot);
    assert!(detail.env.is_some());
    assert_eq!(detail.dynamic_status.status, "offline");

    let missing = service
        .get_bot(BotDetailCommand {
            caller_actor_id: None,
            bot_id: "missing-bot".to_string(),
        })
        .await;

    assert!(matches!(
        missing,
        Err(BotUseCaseError::Service(ServiceError::BotNotFound(id)))
            if id == "missing-bot"
    ));
}

#[tokio::test]
async fn get_bot_rejects_private_bot_owned_by_other_user() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "private-bot",
        caps(Some("Private"), Some("Private summary"), "private"),
        Some("alice"),
    )
    .await;

    let result = service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some("human_bob".to_string()),
            bot_id: "private-bot".to_string(),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Forbidden(message))
            if message == "Not authorized to access bot 'private-bot'"
    ));
}

#[tokio::test]
async fn get_bot_reports_effective_active_status_for_connected_online_bot() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "active-bot",
        caps(Some("Active"), Some("Connected bot"), "public"),
        Some("alice"),
    )
    .await;
    fixture
        .registry
        .register_streaming_connection("active-bot".to_string())
        .await
        .expect("streaming connection");

    let detail = service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "active-bot".to_string(),
        })
        .await
        .expect("bot detail");

    assert_eq!(detail.dynamic_status.status, "active");
}

#[tokio::test]
async fn provider_http_bot_query_views_are_active_without_ws_connection() {
    let fixture = ProviderRegistryFixture::new();
    let service = fixture.service();
    let bot_id = fixture.register_provider_bot("11111111").await;

    assert!(!fixture.registry.is_connected(&bot_id).await);

    let detail = service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some("human_11111111".to_string()),
            bot_id: bot_id.clone(),
        })
        .await
        .expect("provider bot detail");
    assert_eq!(detail.dynamic_status.status, "active");

    let mine = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "11111111".to_string(),
            offset: 0,
            limit: 10,
            active_only: false,
        })
        .await
        .expect("my provider bots");
    let my_bot = mine
        .items
        .iter()
        .find(|entry| entry.bot_uuid == bot_id)
        .expect("provider bot in my bots");
    assert_eq!(my_bot.dynamic_status.status, "active");

    let active_mine = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "11111111".to_string(),
            offset: 0,
            limit: 10,
            active_only: true,
        })
        .await
        .expect("active my provider bots");
    assert_eq!(active_mine.total, 1);
    assert_eq!(active_mine.items[0].bot_uuid, bot_id);

    let queried = service
        .query_bots_by_ids(BotQueryByIdsCommand {
            bot_ids: vec![bot_id.clone()],
        })
        .await
        .expect("provider bot query");
    assert_eq!(queried.bots.len(), 1);
    assert_eq!(queried.bots[0].dynamic_status.status, "active");
}

#[tokio::test]
async fn extended_query_methods_page_creator_and_query_by_ids() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "agent:alice",
        caps(Some("Alice Agent"), Some("Owned"), "public"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "agent:bob",
        caps(Some("Bob Agent"), Some("Owned"), "public"),
        Some("bob"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "draft:alice",
        caps(None, Some("Draft"), "public"),
        Some("alice"),
    )
    .await;
    fixture
        .registry
        .register_streaming_connection("agent:alice".to_string())
        .await
        .expect("connect alice agent");

    let paged = service
        .list_bots_paged(BotPagedListCommand {
            user_id: Some("alice".to_string()),
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();
    assert_eq!(paged.total, 2);
    let alice_agent = paged
        .items
        .iter()
        .find(|entry| entry.bot_uuid == "agent:alice")
        .expect("alice agent in page");
    assert_eq!(alice_agent.dynamic_status.status, "active");

    let mine = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "bob".to_string(),
            offset: 0,
            limit: 10,
            active_only: false,
        })
        .await
        .unwrap();
    assert_eq!(mine.total, 1);
    assert_eq!(mine.items[0].bot_uuid, "agent:bob");

    let queried = service
        .query_bots_by_ids(BotQueryByIdsCommand {
            bot_ids: vec![
                "draft:alice".to_string(),
                "agent:bob".to_string(),
                "missing".to_string(),
            ],
        })
        .await
        .unwrap();
    assert_eq!(queried.bots.len(), 1);
    assert_eq!(queried.bots[0].bot_uuid, "agent:bob");
}

#[tokio::test]
async fn my_bots_active_only_filters_runtime_active_and_ignores_hidden() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "connected-hidden",
        caps(Some("Connected Hidden"), Some("Owned"), "public"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "disconnected",
        caps(Some("Disconnected"), Some("Owned"), "public"),
        Some("alice"),
    )
    .await;
    fixture
        .registry
        .register_streaming_connection("connected-hidden".to_string())
        .await
        .expect("connect hidden bot");
    fixture
        .registry
        .update_actor_status("connected-hidden", ActorStatus::Hidden)
        .await
        .expect("hide connected bot");

    let all = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "alice".to_string(),
            offset: 0,
            limit: 10,
            active_only: false,
        })
        .await
        .expect("all my bots");
    assert_eq!(all.total, 2);
    assert_eq!(all.items[0].bot_uuid, "connected-hidden");
    assert_eq!(all.items[0].dynamic_status.status, "active");
    assert_eq!(all.items[0].status, ActorStatus::Hidden);
    assert_eq!(all.items[1].bot_uuid, "disconnected");
    assert_eq!(all.items[1].dynamic_status.status, "offline");

    let active = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "alice".to_string(),
            offset: 0,
            limit: 10,
            active_only: true,
        })
        .await
        .expect("active my bots");
    assert_eq!(active.total, 1);
    assert_eq!(active.items[0].bot_uuid, "connected-hidden");
    assert_eq!(active.items[0].dynamic_status.status, "active");
}

#[tokio::test]
async fn discover_bots_applies_visibility_and_friend_matrix() {
    let fixture = RegistryFixture::new();
    let service = fixture.service_with_friends(vec![("driver", "protected-friend")]);

    register_bot(
        &fixture.registry,
        "driver",
        caps(Some("Driver"), Some("planner"), "public"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "protected-friend",
        caps(Some("Planner Friend"), Some("planner"), "protected"),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "protected-stranger",
        caps(Some("Planner Stranger"), Some("planner"), "protected"),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "private-planner",
        caps(Some("Private Planner"), Some("planner"), "private"),
        None,
    )
    .await;

    let result = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("planner".to_string()),
            collaborate_bot: Some("driver".to_string()),
            ..Default::default()
        })
        .await
        .unwrap();

    let ids = result
        .bots
        .iter()
        .map(|entry| (entry.bot_uuid.as_str(), entry.is_friend))
        .collect::<Vec<_>>();
    assert!(ids.contains(&("driver", Some(false))));
    assert!(ids.contains(&("protected-friend", Some(true))));
    assert!(!ids.iter().any(|(id, _)| *id == "protected-stranger"));
    assert!(!ids.iter().any(|(id, _)| *id == "private-planner"));

    let protected = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("planner".to_string()),
            visibility: Some("protected".to_string()),
            collaborate_bot: Some("driver".to_string()),
            ..Default::default()
        })
        .await
        .unwrap();
    assert!(
        protected
            .bots
            .iter()
            .any(|entry| entry.bot_uuid == "protected-stranger" && entry.is_friend == Some(false))
    );
}

#[tokio::test]
async fn discover_provider_bots_returns_provider_metadata_and_agent_code() {
    let fixture = ProviderRegistryFixture::new();
    let service = fixture.service();
    let provider = fixture
        .provider
        .register_provider(
            "Provider Directory".to_string(),
            "https://provider.example.com/bcs/webhook".to_string(),
            ProviderAuthMode::AgentPass,
            "alice".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider");
    let (binding, _) = fixture
        .provider
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Provider Searcher".to_string(),
                summary: Some("Finds provider bots".to_string()),
                owners: vec!["alice".to_string()],
                provider_bot_ref: "agent-code-1".to_string(),
                skills: vec![bcs_service_api::Skill::new("search")],
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    let result = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("searcher".to_string()),
            ..Default::default()
        })
        .await
        .expect("discover provider bot");

    assert_eq!(result.count, 1);
    let entry = &result.bots[0];
    assert_eq!(entry.bot_uuid, binding.bot_uuid);
    assert_eq!(entry.agent_code.as_deref(), Some("agent-code-1"));
    let provider_info = entry.provider_info.as_ref().expect("provider info");
    assert_eq!(provider_info.provider_id, provider.provider.provider_id);
    assert_eq!(provider_info.provider_name, "Provider Directory");
}


#[tokio::test]
async fn organization_scoped_discovery_filters_effective_members_and_attaches_metadata() {
    let fixture = RegistryFixture::new();
    let registry: Arc<dyn BotRegistryCoreService> = fixture.registry.clone();
    let organization = Arc::new(StaticOrganizationCoreService::with_members(vec![
        org_member("bot-a", "planner"),
        org_member("bot-b", "traffic_analyst"),
        org_member("bot-c", "traffic_analyst"),
        org_member("bot-d", "traffic_analyst"),
    ]));
    let service = Bot::new_with_friend(
        registry,
        Arc::new(StaticFriendCoreService::new(vec![("bot-a", "bot-d")])),
    )
    .with_bot_core(fixture.registry.clone())
    .with_organization(organization);

    register_bot(&fixture.registry, "bot-a", caps(Some("Requester"), Some("planner"), "public"), None).await;
    register_bot(&fixture.registry, "bot-b", caps(Some("Traffic Public"), Some("traffic"), "protected"), None).await;
    register_bot(&fixture.registry, "bot-c", caps(Some("Traffic Private"), Some("traffic"), "private"), None).await;
    register_bot(&fixture.registry, "bot-d", caps(Some("Traffic Friend"), Some("traffic"), "private"), None).await;
    register_bot(&fixture.registry, "bot-x", caps(Some("Traffic Global"), Some("traffic"), "public"), None).await;

    let result = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("traffic".to_string()),
            requester_bot_id: Some("bot-a".to_string()),
            organization_code: Some("promo-2026".to_string()),
            role: Some("traffic_analyst".to_string()),
            ..Default::default()
        })
        .await
        .expect("scoped discovery");

    let ids = result
        .bots
        .iter()
        .map(|entry| entry.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(ids, vec!["bot-b", "bot-d"]);
    assert!(result.bots.iter().all(|entry| entry.organization_member.as_ref().is_some_and(|member| {
        member.organization_code == "promo-2026" && member.role.as_deref() == Some("traffic_analyst")
    })));
    assert_eq!(result.bots[0].is_friend, Some(false));
    assert_eq!(result.bots[1].is_friend, Some(true));
}

#[tokio::test]
async fn organization_scoped_discovery_rejects_role_without_org_and_nonmember_requester() {
    let fixture = RegistryFixture::new();
    let registry: Arc<dyn BotRegistryCoreService> = fixture.registry.clone();
    let service = Bot::new(registry).with_organization(Arc::new(StaticOrganizationCoreService::rejecting_requester()));

    let role_without_org = service
        .discover_bots(BotDiscoveryCommand {
            role: Some("traffic_analyst".to_string()),
            ..Default::default()
        })
        .await
        .expect_err("role without organization should fail");
    assert!(matches!(role_without_org, BotUseCaseError::Service(ServiceError::InvalidOperation { message, .. }) if message == "role_requires_organization_code"));

    let nonmember = service
        .discover_bots(BotDiscoveryCommand {
            requester_bot_id: Some("bot-a".to_string()),
            organization_code: Some("promo-2026".to_string()),
            ..Default::default()
        })
        .await
        .expect_err("nonmember requester should fail");
    assert!(matches!(nonmember, BotUseCaseError::Service(ServiceError::Forbidden(reason)) if reason == "organization_member_required"));
}

#[tokio::test]
async fn get_visibility_applies_read_policy_and_normalizes_visibility() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "private-bot",
        caps(Some("Private"), Some("Private bot"), "internal"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "protected-bot",
        caps(Some("Protected"), Some("Protected bot"), "protected"),
        Some("alice"),
    )
    .await;

    let owner_view = service
        .get_visibility(BotVisibilityQueryCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "private-bot".to_string(),
        })
        .await
        .expect("owner can read visibility");
    assert_eq!(owner_view.visibility, "private");

    let bot_view = service
        .get_visibility(BotVisibilityQueryCommand {
            caller_actor_id: Some("caller-bot".to_string()),
            bot_id: "protected-bot".to_string(),
        })
        .await
        .expect("bot can read public/protected visibility");
    assert_eq!(bot_view.visibility, "protected");

    let private_for_bot = service
        .get_visibility(BotVisibilityQueryCommand {
            caller_actor_id: Some("caller-bot".to_string()),
            bot_id: "private-bot".to_string(),
        })
        .await;
    assert!(matches!(
        private_for_bot,
        Err(BotUseCaseError::Service(ServiceError::BotNotFound(id)))
            if id == "private-bot"
    ));

    let non_owner = service
        .get_visibility(BotVisibilityQueryCommand {
            caller_actor_id: Some("human_bob".to_string()),
            bot_id: "private-bot".to_string(),
        })
        .await;
    assert!(matches!(non_owner, Err(BotUseCaseError::Forbidden(_))));
}

#[tokio::test]
async fn connect_bot_preserves_provided_bot_id_and_reconnect_token() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    let connected = service
        .connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("provided-bot".to_string()),
            protocol_version: Some(2),
        })
        .await
        .expect("connect new bot");

    assert!(connected.is_new);
    assert_eq!(connected.bot_uuid, "provided-bot");
    assert!(!connected.token.is_empty());

    let reconnected = service
        .connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: None,
            token: Some(connected.token.clone()),
            bot_id: None,
            protocol_version: None,
        })
        .await
        .expect("reconnect by token");

    assert!(!reconnected.is_new);
    assert_eq!(reconnected.bot_uuid, "provided-bot");
    assert_eq!(reconnected.token, connected.token);
}

#[tokio::test]
async fn connect_bot_rejects_unknown_reserved_human_bot_id() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    let result = service
        .connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("human_foo".to_string()),
            protocol_version: Some(2),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::InvalidBotId(message))
            if message.contains("human_")
    ));
}

#[tokio::test]
async fn connect_bot_allows_existing_human_actor_id_to_reach_registry() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();
    fixture
        .registry
        .ensure_human_actor("foo", "Foo")
        .await
        .expect("ensure human");

    let result = service
        .connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("human_foo".to_string()),
            protocol_version: Some(2),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Connect(ConnectError::AlreadyRegistered(id)))
            if id == "human_foo"
    ));
}

#[tokio::test]
async fn connect_bot_preserves_registry_conflict_error_class() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "provided-bot",
        caps(Some("Provided"), Some("Existing bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .connect_bot(bcs_service_api::BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("provided-bot".to_string()),
            protocol_version: Some(2),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Connect(ConnectError::AlreadyRegistered(id)))
            if id == "provided-bot"
    ));
}

#[tokio::test]
async fn bot_runtime_connection_service_manages_streaming_lifecycle() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    let connected = service
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("runtime-bot".to_string()),
            protocol_version: Some(2),
            client_kind: None,
        })
        .await
        .expect("runtime connect");

    assert!(connected.is_new);
    assert_eq!(connected.bot_uuid, "runtime-bot");
    assert!(fixture.registry.is_connected("runtime-bot").await);

    let status = bcs_service_api::BotDynamicStatus {
        status: "busy".to_string(),
        dynamic_summary: Some("serving websocket".to_string()),
        load: Some(0.4),
        updated_at: Some(456),
    };
    let updated = service
        .update_runtime_status(BotRuntimeStatusCommand {
            caller_actor_id: Some("runtime-bot".to_string()),
            bot_id: "runtime-bot".to_string(),
            status: status.clone(),
        })
        .await
        .expect("runtime status");

    assert!(updated.updated);
    assert_eq!(updated.bot_uuid, "runtime-bot");
    assert_eq!(updated.status.status, "busy");
    let stored = fixture
        .registry
        .get("runtime-bot")
        .await
        .expect("stored bot");
    assert_eq!(
        stored.dynamic_status.dynamic_summary,
        status.dynamic_summary
    );

    service
        .disconnect_streaming(BotRuntimeDisconnectCommand {
            bot_id: "runtime-bot".to_string(),
        })
        .await
        .expect("runtime disconnect");

    assert!(!fixture.registry.is_connected("runtime-bot").await);
}

#[tokio::test]
async fn update_status_uses_dynamic_status() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "status-bot",
        caps(Some("Status"), Some("Status bot"), "protected"),
        Some("alice"),
    )
    .await;

    let status = bcs_service_api::BotDynamicStatus {
        status: "busy".to_string(),
        dynamic_summary: Some("Working on a task".to_string()),
        load: Some(0.8),
        updated_at: Some(123),
    };
    let result = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("status-bot".to_string()),
            bot_id: "status-bot".to_string(),
            status: status.clone(),
        })
        .await
        .expect("status update");

    assert!(result.updated);
    assert_eq!(result.bot_uuid, "status-bot");
    assert_eq!(result.status.status, "busy");
    assert_eq!(
        result.status.dynamic_summary.as_deref(),
        Some("Working on a task")
    );

    let stored = fixture
        .registry
        .get("status-bot")
        .await
        .expect("stored bot");
    assert_eq!(stored.dynamic_status.status, status.status);
    assert_eq!(stored.dynamic_status.load, status.load);
}

#[tokio::test]
async fn update_status_preserves_legacy_false_for_missing_self_target() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    let status = bcs_service_api::BotDynamicStatus {
        status: "busy".to_string(),
        dynamic_summary: Some("still booting".to_string()),
        ..Default::default()
    };
    let result = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("missing-bot".to_string()),
            bot_id: "missing-bot".to_string(),
            status: status.clone(),
        })
        .await
        .expect("missing self target should preserve legacy false result");

    assert!(!result.updated);
    assert_eq!(result.bot_uuid, "missing-bot");
    assert_eq!(result.status.status, "busy");
    assert_eq!(
        result.status.dynamic_summary.as_deref(),
        Some("still booting")
    );
}

#[tokio::test]
async fn update_status_rejects_missing_caller() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "status-bot",
        caps(Some("Status"), Some("Status bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: None,
            bot_id: "status-bot".to_string(),
            status: bcs_service_api::BotDynamicStatus {
                status: "busy".to_string(),
                ..Default::default()
            },
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Unauthorized(message))
            if message.contains("caller identity")
    ));
}

#[tokio::test]
async fn update_status_rejects_caller_mismatch() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "status-bot",
        caps(Some("Status"), Some("Status bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("human_bob".to_string()),
            bot_id: "status-bot".to_string(),
            status: bcs_service_api::BotDynamicStatus {
                status: "busy".to_string(),
                ..Default::default()
            },
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("not the owner")
    ));
    let stored = fixture
        .registry
        .get("status-bot")
        .await
        .expect("stored bot");
    assert_eq!(stored.dynamic_status.status, "");
}

#[tokio::test]
async fn leave_bot_allows_owner_soft_delete_for_unmanaged_bot() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "leave-bot",
        caps(Some("Leave"), Some("Leaving bot"), "public"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "other-owner-bot",
        caps(Some("Other"), Some("Other owner bot"), "public"),
        Some("bob"),
    )
    .await;

    let non_owner = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_bob".to_string()),
            human_actor_id: Some("human_bob".to_string()),
            bot_id: "leave-bot".to_string(),
        })
        .await;
    assert!(matches!(
        non_owner,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("not the creator")
    ));
    assert!(fixture.registry.get("leave-bot").await.is_some());

    let left = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_alice".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            bot_id: "leave-bot".to_string(),
        })
        .await
        .expect("owner can delete unmanaged bot");
    assert!(left.left);
    assert_eq!(left.bot_uuid, "leave-bot");
    assert!(fixture.registry.get("leave-bot").await.is_none());
    assert!(fixture.registry.get("other-owner-bot").await.is_some());
}

#[tokio::test]
async fn leave_bot_rejects_bot_token_provider_managed_and_tc_style_deletes() {
    let fixture = ProviderRegistryFixture::new();
    let service = fixture.service();
    let provider_bot_id = fixture.register_provider_bot("alice").await;

    register_bot(
        &fixture.registry,
        "teamclaw-bot:alice",
        caps(Some("Teamclaw"), Some("TC bot"), "public"),
        Some("alice"),
    )
    .await;

    let bot_token_delete = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("teamclaw-bot:alice".to_string()),
            human_actor_id: None,
            bot_id: "teamclaw-bot:alice".to_string(),
        })
        .await;
    assert!(matches!(
        bot_token_delete,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("owner delete")
    ));

    let provider_managed = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_alice".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            bot_id: provider_bot_id.clone(),
        })
        .await;
    assert!(matches!(
        provider_managed,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("provider")
    ));
    assert!(fixture.registry.get(&provider_bot_id).await.is_some());

    let tc_style = service
        .leave_bot(BotLeaveCommand {
            human_actor_id: Some("human_alice".to_string()),
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "teamclaw-bot:alice".to_string(),
        })
        .await;
    assert!(matches!(
        tc_style,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("TC")
    ));
    assert!(fixture.registry.get("teamclaw-bot:alice").await.is_some());
}

#[tokio::test]
async fn set_visibility_rejects_invalid_values() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "visibility-bot",
        caps(Some("Visibility"), Some("Visibility bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("alice".to_string()),
            bot_id: "visibility-bot".to_string(),
            visibility: "friends".to_string(),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::InvalidVisibility(value)) if value == "friends"
    ));
    let stored = fixture
        .registry
        .get("visibility-bot")
        .await
        .expect("stored bot");
    assert_eq!(stored.capabilities.visibility, "protected");
}

#[tokio::test]
async fn set_visibility_updates_registry_for_valid_value() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "visibility-bot",
        caps(Some("Visibility"), Some("Visibility bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "visibility-bot".to_string(),
            visibility: "private".to_string(),
        })
        .await
        .expect("visibility update");

    assert_eq!(result.bot_uuid, "visibility-bot");
    assert_eq!(result.visibility, "private");

    let stored = fixture
        .registry
        .get("visibility-bot")
        .await
        .expect("stored bot");
    assert_eq!(stored.capabilities.visibility, "private");
}

#[tokio::test]
async fn set_visibility_rejects_non_owner_when_owner_is_known() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "visibility-bot",
        caps(Some("Visibility"), Some("Visibility bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_bob".to_string()),
            bot_id: "visibility-bot".to_string(),
            visibility: "public".to_string(),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("not the owner")
    ));
    let stored = fixture
        .registry
        .get("visibility-bot")
        .await
        .expect("stored bot");
    assert_eq!(stored.capabilities.visibility, "protected");
}

#[tokio::test]
async fn set_visibility_rejects_missing_caller() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "visibility-bot",
        caps(Some("Visibility"), Some("Visibility bot"), "protected"),
        Some("alice"),
    )
    .await;

    let result = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: None,
            bot_id: "visibility-bot".to_string(),
            visibility: "public".to_string(),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Unauthorized(message))
            if message.contains("caller identity")
    ));
}

#[tokio::test]
async fn set_visibility_rejects_ownerless_bot_for_non_self_caller() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "ownerless-bot",
        caps(Some("Ownerless"), Some("No owner"), "protected"),
        None,
    )
    .await;

    let result = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "ownerless-bot".to_string(),
            visibility: "public".to_string(),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Forbidden(message))
            if message.contains("not the owner")
    ));
    let stored = fixture
        .registry
        .get("ownerless-bot")
        .await
        .expect("stored bot");
    assert_eq!(stored.capabilities.visibility, "protected");
}

async fn register_bot(
    registry: &BotCore,
    bot_id: &str,
    capabilities: BotCapabilities,
    owner: Option<&str>,
) {
    registry
        .register(bot_id.to_string(), capabilities)
        .await
        .expect("register bot");
    if let Some(owner) = owner {
        registry
            .save_created_by(bot_id, owner, true)
            .await
            .expect("save owner");
    }
}

fn caps(name: Option<&str>, summary: Option<&str>, visibility: &str) -> BotCapabilities {
    BotCapabilities {
        name: name.map(str::to_string),
        summary: summary.map(str::to_string),
        visibility: visibility.to_string(),
        ..Default::default()
    }
}
