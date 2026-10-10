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
    CreateOrganizationCommand, OrganizationAuth, OrganizationManagementService,
    OrganizationMemberAuth,
    ProviderOrganizationManagementConfig, PutOrganizationMemberCommand, ServiceError, ServiceResult,
};
use bcs_bot_store::provider::{MemoryBotProviderStore, MemoryProviderStore, ProviderBindingProjection};
use bcs_organization::{OrganizationCore, OrganizationManagement};
use bcs_organization_store::MemoryOrganizationRepo;
use bcs_service_api::types::{Organization, OrganizationMember};
use bcs_bot_store::MemoryBotRepo;
use tempfile::TempDir;

use bcs_bot::{Bot, BotCore, ProviderCore};

struct RegistryFixture {
    registry: Arc<BotCore>,
    /// The memory bot authority repo behind `registry`, so tests can seed the
    /// live owner/manager rows the Task-12 mine lane reads (the creation
    /// fact alone no longer decides `/bots/my`).
    repo: Arc<MemoryBotRepo>,
    _data_dir: TempDir,
}

impl RegistryFixture {
    fn new() -> Self {
        let data_dir = tempfile::tempdir().expect("temp data dir");
        let repo = Arc::new(MemoryBotRepo::with_base_dir(data_dir.path().to_path_buf()));
        let registry = Arc::new(BotCore::with_repo(repo.clone()));
        Self {
            registry,
            repo,
            _data_dir: data_dir,
        }
    }

    /// The live-authority hook over the fixture's own memory authority rows —
    /// the same validation-then-role composition the production
    /// `BotAuthorityHookImpl` gets from `BotAuthorityCoreServiceImpl`
    /// (ownership invariant first, then the caller's role pair).
    fn authority_hook(&self) -> Arc<dyn bcs_service_api::application::v1::BotAuthorityHook> {
        Arc::new(RepoAuthorityHook(self.repo.clone()))
    }

    fn control_plane(&self) -> Arc<dyn bcs_service_api::BotControlPlaneCoreService> {
        let provider_store = Arc::new(MemoryProviderStore::new());
        let bot_providers = Arc::new(MemoryBotProviderStore::new(
            self.repo.clone(),
            provider_store.clone(),
        ));
        let provider_bindings = Arc::new(ProviderBindingProjection::new(
            provider_store.clone(),
            bot_providers.clone(),
            bcs_domain::bot_provider::DownlinkDetectionSource::default(),
        ));
        Arc::new(
            bcs_bot::BotControlPlaneCore::new(
                self.repo.clone(),
                provider_store,
                provider_bindings,
            )
            .with_bot_provider_repo(bot_providers),
        )
    }

    fn service(&self) -> Bot {
        let registry: Arc<dyn BotRegistryCoreService> = self.registry.clone();
        Bot::new(registry)
            .with_bot_core(self.registry.clone())
            .with_control_plane(self.control_plane())
            .with_authority(self.authority_hook())
    }

    fn service_with_friends(&self, friends: Vec<(&str, &str)>) -> Bot {
        let registry: Arc<dyn BotRegistryCoreService> = self.registry.clone();
        Bot::new_with_friend(registry, Arc::new(StaticFriendCoreService::new(friends)))
            .with_control_plane(self.control_plane())
            .with_authority(self.authority_hook())
    }
}

/// Live-authority hook over one `MemoryBotRepo`, mirroring the production
/// hook's Core composition (spec §12.4): the Bot's ownership invariant is
/// validated FIRST (fail closed on uninitialized/corrupt authority), then
/// the caller's role pair answers can_manage / require_owner.
struct RepoAuthorityHook(Arc<MemoryBotRepo>);

#[async_trait::async_trait]
impl bcs_service_api::application::v1::BotAuthorityHook for RepoAuthorityHook {
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
        use bcs_service_api::port::repo::BotAuthorityRepoPort;
        self.0.ownership(bot_id).await?;
        Ok(self.0.role(user_id, bot_id).await?.is_some())
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        use bcs_service_api::port::repo::BotAuthorityRepoPort;
        match self.0.role(user_id, bot_id).await? {
            Some(bcs_service_api::types::BotAccessRelation::Owner) => Ok(()),
            _ => Err(ServiceError::Unauthorized(format!(
                "user '{user_id}' is not the owner of bot '{bot_id}'"
            ))),
        }
    }
}

struct ProviderRegistryFixture {
    registry: Arc<BotCore>,
    provider: ProviderCore,
    repo: Arc<MemoryBotRepo>,
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
            bot_repo.clone(),
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
            repo: bot_repo,
            _data_dir: data_dir,
        }
    }

    fn service(&self) -> Bot {
        let registry: Arc<dyn BotRegistryCoreService> = self.registry.clone();
        let provider_store = Arc::new(MemoryProviderStore::new());
        let bot_providers = Arc::new(MemoryBotProviderStore::new(
            self.repo.clone(),
            provider_store.clone(),
        ));
        let provider_bindings = Arc::new(ProviderBindingProjection::new(
            provider_store.clone(),
            bot_providers.clone(),
            bcs_domain::bot_provider::DownlinkDetectionSource::default(),
        ));
        Bot::new(registry)
            .with_bot_core(self.registry.clone())
            .with_control_plane(Arc::new(
                bcs_bot::BotControlPlaneCore::new(
                    self.repo.clone(),
                    provider_store,
                    provider_bindings,
                )
                .with_bot_provider_repo(bot_providers),
            ))
    }

    async fn register_provider_bot(&self, owner: &str) -> String {
        let provider = self
            .provider
            .register_provider(
                "Provider".to_string(),
                Some("https://provider.example.com/bcs/webhook".to_string()),
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

struct OrganizationProviderFixture {
    provider_id: String,
    admin_token: String,
}

async fn register_organization_provider(
    provider: &ProviderCore,
    name: &str,
) -> OrganizationProviderFixture {
    let registered = provider
        .register_provider(
            name.to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            ProviderAuthMode::StaticBearer,
            "11111111".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider");
    OrganizationProviderFixture {
        provider_id: registered.provider.provider_id,
        admin_token: registered.provider_admin_token,
    }
}

async fn register_organization_bot(
    provider: &ProviderCore,
    owner: &OrganizationProviderFixture,
    bot_uuid: &str,
) {
    provider
        .register_provider_bot_with_bot_uuid(
            &owner.provider_id,
            &owner.admin_token,
            RegisterProviderBotParams {
                bot_name: format!("{bot_uuid} name"),
                summary: Some(format!("{bot_uuid} summary")),
                owners: vec!["11111111".to_string()],
                provider_bot_ref: format!("{bot_uuid}-ref"),
                bot_uuid: Some(bot_uuid.to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");
}

fn organization_auth(provider: &OrganizationProviderFixture) -> OrganizationAuth {
    OrganizationAuth {
        provider_id: provider.provider_id.clone(),
        provider_admin_token: provider.admin_token.clone(),
    }
}

fn organization_member_auth(provider: &OrganizationProviderFixture) -> OrganizationMemberAuth {
    OrganizationMemberAuth {
        provider_admin_token: provider.admin_token.clone(),
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

    async fn require_runtime_member(&self, organization_code: &str, bot_uuid: &str) -> ServiceResult<OrganizationMember> {
        self.require_effective_member(organization_code, bot_uuid).await
    }

    async fn list_runtime_members(&self, organization_code: &str, role: Option<&str>) -> ServiceResult<Vec<OrganizationMember>> {
        self.list_effective_members(organization_code, role).await
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
    // Task-12 mine rules: the provider-bot registration owner must hold the
    // CURRENT owner edge for `/bots/my` (the pure ProviderCore test lane does
    // not run the v2 registration's ownership-initialization contract).
    fixture.repo.seed_authority_owned(&bot_id, "11111111").await.unwrap();

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
    // Task-12 mine rules: bob must hold the CURRENT owner edge of his bot.
    fixture
        .repo
        .seed_authority_owned("agent:bob", "bob")
        .await
        .unwrap();
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
    // Task-12 mine rules: alice must hold the CURRENT owner edges.
    fixture
        .repo
        .seed_authority_owned("connected-hidden", "alice")
        .await
        .unwrap();
    fixture
        .repo
        .seed_authority_owned("disconnected", "alice")
        .await
        .unwrap();
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
            requester_bot_id: Some("driver".to_string()),
            ..Default::default()
        })
        .await
        .unwrap();

    let ids = result
        .bots
        .iter()
        .map(|entry| (entry.bot_uuid.as_str(), entry.is_friend))
        .collect::<Vec<_>>();
    assert!(!ids.iter().any(|(id, _)| *id == "driver"));
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
async fn discover_bots_matches_one_skill_exactly_ignoring_case() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    for (bot_id, skill) in [
        ("exact-lower", "code_review"),
        ("exact-mixed", "Code_Review"),
        ("partial", "code_review_extended"),
        ("unrelated", "deployment"),
    ] {
        register_bot(
            &fixture.registry,
            bot_id,
            caps_with_skill(Some(bot_id), Some("review helper"), "public", skill),
            None,
        )
        .await;
    }

    let result = service
        .discover_bots(BotDiscoveryCommand {
            skills: vec!["code_review".to_string()],
            ..Default::default()
        })
        .await
        .expect("discover by exact skill");

    let bot_ids = result
        .bots
        .iter()
        .map(|entry| entry.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(bot_ids, vec!["exact-lower", "exact-mixed"]);
}

#[tokio::test]
async fn discover_bots_requires_all_exact_skills() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    for (bot_id, skills) in [
        ("all-exact", vec!["code_review", "SQL"]),
        ("missing-sql", vec!["code_review"]),
        ("partial-sql", vec!["code_review", "sql_extended"]),
    ] {
        register_bot(
            &fixture.registry,
            bot_id,
            caps_with_skills(
                Some(bot_id),
                Some("review helper"),
                "public",
                skills.as_slice(),
            ),
            None,
        )
        .await;
    }

    let result = service
        .discover_bots(BotDiscoveryCommand {
            skills: vec!["code_review".to_string(), "sql".to_string()],
            ..Default::default()
        })
        .await
        .expect("discover by all exact skills");

    let bot_ids = result
        .bots
        .iter()
        .map(|entry| entry.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(bot_ids, vec!["all-exact"]);
}

#[tokio::test]
async fn discover_bots_combines_q_and_all_skills() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    for (bot_id, summary, skills) in [
        (
            "query-and-skills",
            "deployment helper",
            vec!["code_review", "sql"],
        ),
        ("query-one-skill", "deployment helper", vec!["code_review"]),
        (
            "skills-only",
            "documentation helper",
            vec!["code_review", "sql"],
        ),
    ] {
        register_bot(
            &fixture.registry,
            bot_id,
            caps_with_skills(Some(bot_id), Some(summary), "public", skills.as_slice()),
            None,
        )
        .await;
    }

    let result = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("deployment".to_string()),
            skills: vec!["code_review".to_string(), "sql".to_string()],
            ..Default::default()
        })
        .await
        .expect("discover by query and all exact skills");

    let bot_ids = result
        .bots
        .iter()
        .map(|entry| entry.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(bot_ids, vec!["query-and-skills"]);
}

#[tokio::test]
async fn discover_provider_bots_returns_provider_metadata_and_agent_code() {
    let fixture = ProviderRegistryFixture::new();
    let service = fixture.service();
    let provider = fixture
        .provider
        .register_provider(
            "Provider Directory".to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
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
                skills: vec![
                    bcs_service_api::Skill::new("search"),
                    bcs_service_api::Skill::new("sql"),
                ],
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");
    fixture
        .provider
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Provider Searcher Missing Skill".to_string(),
                summary: Some("Finds provider bots".to_string()),
                owners: vec!["alice".to_string()],
                provider_bot_ref: "agent-code-2".to_string(),
                skills: vec![bcs_service_api::Skill::new("search")],
                ..Default::default()
            },
        )
        .await
        .expect("register provider query-only bot");
    fixture
        .provider
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Provider Searcher Partial Skill".to_string(),
                summary: Some("Finds provider bots".to_string()),
                owners: vec!["alice".to_string()],
                provider_bot_ref: "agent-code-3".to_string(),
                skills: vec![
                    bcs_service_api::Skill::new("search"),
                    bcs_service_api::Skill::new("sql_extended"),
                ],
                ..Default::default()
            },
        )
        .await
        .expect("register provider partial-skill bot");
    fixture
        .provider
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Provider Writer".to_string(),
                summary: Some("Writes documentation".to_string()),
                owners: vec!["alice".to_string()],
                provider_bot_ref: "agent-code-4".to_string(),
                skills: vec![
                    bcs_service_api::Skill::new("search"),
                    bcs_service_api::Skill::new("sql"),
                ],
                ..Default::default()
            },
        )
        .await
        .expect("register provider skills-only bot");

    let result = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("searcher".to_string()),
            skills: vec!["search".to_string(), "sql".to_string()],
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
        org_member("bot-a", "traffic_analyst"),
        org_member("bot-b", "traffic_analyst"),
        org_member("bot-c", "traffic_analyst"),
        org_member("bot-d", "traffic_analyst"),
        org_member("bot-e", "traffic_analyst"),
        org_member("bot-f", "traffic_analyst"),
    ]));
    let service = Bot::new_with_friend(
        registry,
        Arc::new(StaticFriendCoreService::new(vec![("bot-a", "bot-d")])),
    )
    .with_bot_core(fixture.registry.clone())
    .with_organization(organization);

    register_bot(&fixture.registry, "bot-a", caps(Some("Requester"), Some("traffic"), "public"), None).await;
    register_bot(
        &fixture.registry,
        "bot-b",
        caps_with_skills(
            Some("Traffic Public"),
            Some("traffic"),
            "protected",
            &["routing", "sql"],
        ),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "bot-c",
        caps_with_skills(
            Some("Traffic Private"),
            Some("traffic"),
            "private",
            &["routing", "sql"],
        ),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "bot-d",
        caps_with_skills(
            Some("Traffic Friend"),
            Some("traffic"),
            "private",
            &["routing", "sql"],
        ),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "bot-e",
        caps_with_skills(
            Some("Traffic Missing Skill"),
            Some("traffic"),
            "public",
            &["routing"],
        ),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "bot-f",
        caps_with_skills(
            Some("Traffic Partial Skill"),
            Some("traffic"),
            "public",
            &["routing", "sql_extended"],
        ),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "bot-x",
        caps_with_skills(
            Some("Traffic Global"),
            Some("traffic"),
            "public",
            &["routing", "sql"],
        ),
        None,
    )
    .await;

    let result = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("traffic".to_string()),
            skills: vec!["routing".to_string(), "sql".to_string()],
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
async fn organization_scoped_discovery_keeps_members_after_provider_state_changes() {
    let data_dir = tempfile::tempdir().expect("temp data dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let providers: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let candidate_reads: Arc<dyn bcs_service_api::OrganizationCandidateReadPort> = provider_store.clone();
    let registry = Arc::new(BotCore::with_provider_repos(
        Arc::new(MemoryBotRepo::with_base_dir(data_dir.path().to_path_buf())),
        providers.clone(),
        credentials.clone(),
        bindings.clone(),
    ));
    let provider_core = Arc::new(ProviderCore::new(
        providers.clone(),
        credentials,
        bindings.clone(),
        registry.clone(),
    ));
    let organization_core = Arc::new(OrganizationCore::new(
        "test".to_string(),
        Arc::new(MemoryOrganizationRepo::new()),
        providers,
        bindings,
        candidate_reads,
        registry.clone(),
    ));
    let organization_management = OrganizationManagement::new(
        provider_core.clone(),
        organization_core.clone(),
    );
    let service = Bot::new_with_friend(
        registry.clone(),
        Arc::new(StaticFriendCoreService::default()),
    )
    .with_bot_core(registry.clone())
    .with_organization(organization_core);

    let provider_a = register_organization_provider(&provider_core, "Provider A").await;
    let provider_b = register_organization_provider(&provider_core, "Provider B").await;
    let provider_c = register_organization_provider(&provider_core, "Provider C").await;
    for (resource, managers) in [
        (&provider_b, vec![provider_a.provider_id.clone()]),
        (&provider_c, vec![provider_a.provider_id.clone()]),
    ] {
        provider_core
            .update_provider(
                &resource.provider_id,
                &resource.admin_token,
                "11111111",
                None,
                None,
                None,
                None,
                Some(ProviderOrganizationManagementConfig {
                    authorized_manager_provider_ids: managers,
                }),
            )
            .await
            .expect("grant manager");
    }
    register_organization_bot(&provider_core, &provider_a, "bot-a").await;
    register_organization_bot(&provider_core, &provider_b, "bot-b").await;
    register_organization_bot(&provider_core, &provider_c, "bot-c").await;
    organization_management
        .create(CreateOrganizationCommand {
            auth: organization_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            name: "Promo 2026".to_string(),
            description: None,
        })
        .await
        .expect("create organization");
    for (bot_uuid, role) in [
        ("bot-a", "planner"),
        ("bot-b", "traffic_analyst"),
        ("bot-c", "traffic_analyst"),
    ] {
        organization_management
            .put_member(PutOrganizationMemberCommand {
                auth: organization_member_auth(&provider_a),
                organization_code: "promo-2026".to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: Some(role.to_string()),
            })
            .await
            .expect("add organization member");
    }

    provider_core
        .update_provider(
            &provider_c.provider_id,
            &provider_c.admin_token,
            "11111111",
            None,
            None,
            None,
            None,
            Some(ProviderOrganizationManagementConfig {
                authorized_manager_provider_ids: Vec::new(),
            }),
        )
        .await
        .expect("revoke manager grant");
    provider_core
        .set_provider_bot_disabled(
            &provider_b.provider_id,
            "bot-b",
            &provider_b.admin_token,
            true,
        )
        .await
        .expect("disable provider binding");

    let result = service
        .discover_bots(BotDiscoveryCommand {
            requester_bot_id: Some("bot-a".to_string()),
            organization_code: Some("promo-2026".to_string()),
            role: Some("traffic_analyst".to_string()),
            ..Default::default()
        })
        .await
        .expect("discover organization members");

    let ids = result
        .bots
        .iter()
        .map(|bot| bot.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(ids, vec!["bot-b", "bot-c"]);
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
    assert!(serde_json::to_value(stored).unwrap().get("dynamic_status").is_none());

    service
        .disconnect_streaming(BotRuntimeDisconnectCommand {
            bot_id: "runtime-bot".to_string(),
        })
        .await
        .expect("runtime disconnect");

    assert!(!fixture.registry.is_connected("runtime-bot").await);
}

#[tokio::test]
async fn streaming_client_kind_requires_server_profile_and_is_replaced_on_reconnect() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    let untrusted = service
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: Some("profile-bot".to_string()),
            protocol_version: Some(3),
            client_kind: Some("native_mcp".to_string()),
        })
        .await
        .expect("initial connect");
    assert_eq!(untrusted.negotiated_client_kind, None);
    assert_eq!(
        fixture.registry.get_bot_info("profile-bot", "client_kind").await,
        None
    );

    service
        .disconnect_streaming(BotRuntimeDisconnectCommand {
            bot_id: "profile-bot".to_string(),
        })
        .await
        .expect("disconnect untrusted connection");
    let service = fixture.service().with_uplink_config(bcs_config_api::UplinkConfig {
        allowed_profiles: vec![bcs_config_api::UplinkProfile::NativeMcp],
    });

    let trusted = service
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None,
            token: Some(untrusted.token.clone()),
            bot_id: Some("profile-bot".to_string()),
            protocol_version: Some(3),
            client_kind: Some("native_mcp".to_string()),
        })
        .await
        .expect("trusted reconnect");
    assert_eq!(trusted.negotiated_client_kind.as_deref(), Some("native_mcp"));
    assert_eq!(
        fixture.registry.get_bot_info("profile-bot", "client_kind").await.as_deref(),
        Some("native_mcp")
    );

    service
        .disconnect_streaming(BotRuntimeDisconnectCommand {
            bot_id: "profile-bot".to_string(),
        })
        .await
        .expect("disconnect trusted connection");
    assert_eq!(
        fixture.registry.get_bot_info("profile-bot", "client_kind").await,
        None
    );
    let omitted = service
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: None,
            token: Some(trusted.token),
            bot_id: Some("profile-bot".to_string()),
            protocol_version: Some(3),
            client_kind: None,
        })
        .await
        .expect("reconnect without client profile");
    assert_eq!(omitted.negotiated_client_kind, None);
    assert_eq!(
        fixture.registry.get_bot_info("profile-bot", "client_kind").await,
        None
    );
}

#[tokio::test]
async fn update_status_echoes_payload_without_retaining_it() {
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
    assert_eq!(result.status.load, status.load);
    assert_eq!(result.status.updated_at, status.updated_at);

    let stored = fixture
        .registry
        .get("status-bot")
        .await
        .expect("stored bot");
    assert!(serde_json::to_value(stored).unwrap().get("dynamic_status").is_none());
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
    fixture
        .repo
        .seed_authority_owned("status-bot", "alice")
        .await
        .expect("seed live owner edge");

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
        Err(BotUseCaseError::Forbidden(ref message))
            if message.contains("owner/manager role")
    ));
}

#[tokio::test]
async fn leave_bot_allows_owner_soft_delete_for_unmanaged_bot() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    // `created_by` alice + her live approved owner edge (the trusted
    // first-ownership claim): after the §12.4 cutover the delete lane
    // resolves this edge, not the creation fact.
    register_bot(
        &fixture.registry,
        "leave-bot",
        caps(Some("Leave"), Some("Leaving bot"), "public"),
        Some("alice"),
    )
    .await;
    fixture
        .repo
        .seed_authority_owned("leave-bot", "alice")
        .await
        .expect("seed live owner edge");
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
            if message.contains("owner/manager role")
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

/// CI-red regression (e2e story order `story_user_prepares_agent_network` →
/// public-API mine): a Human deletes ONE of two owned agents through the
/// legacy owner-delete lane; the strict mine union for the SURVIVING owned
/// agent must still answer. The deleted agent's approved owner edge may
/// never dangle behind its tombstone (plan Task 5 deletion boundary):
/// `list_controllable` fails the WHOLE read with a typed corruption error
/// on a dangling role edge, which turned the owner's `mine` into a 500 and
/// every fail-closed guard lane into 403s in CI.
#[tokio::test]
async fn leave_bot_retires_authority_edges_so_mine_stays_strict_green() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    // Trusted first-ownership claim per bot (the onboarding store lane):
    // register the live Bot, then claim v0 -> 1 with alice's owner edge.
    for bot_id in ["leave-strict-a", "leave-strict-b"] {
        register_bot(
            &fixture.registry,
            bot_id,
            caps(Some(bot_id), Some("strict mine survivor"), "public"),
            None,
        )
        .await;
        fixture
            .registry
            .initialize_existing_ownership(
                bot_id,
                bcs_service_api::types::OwnershipInitialization {
                    owner_user_id: "alice".to_string(),
                    actor: bcs_service_api::types::AuditActor::Human {
                        user_id: "alice".to_string(),
                    },
                    operation_id: uuid::Uuid::new_v4().to_string(),
                },
            )
            .await
            .expect("trusted first-ownership claim");
        fixture
            .registry
            .save_created_by(bot_id, "alice", true)
            .await
            .expect("bind created_by for the legacy lane");
    }

    let left = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_alice".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            bot_id: "leave-strict-a".to_string(),
        })
        .await
        .expect("owner deletes through the legacy owner-delete lane");
    assert!(left.left);
    assert!(fixture.registry.get("leave-strict-a").await.is_none());

    let mine = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "alice".to_string(),
            offset: 0,
            limit: 50,
            active_only: false,
        })
        .await
        .expect("the strict mine union must still answer after the delete");
    let uuids: Vec<&str> = mine.items.iter().map(|e| e.bot_uuid.as_str()).collect();
    assert!(
        uuids.contains(&"leave-strict-b"),
        "the surviving owned agent stays in mine: {uuids:?}"
    );
    assert!(
        !uuids.contains(&"leave-strict-a"),
        "the deleted agent left mine: {uuids:?}"
    );
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

/// F1 regression (spec §12.2/§12.4): the leave/status/visibility lanes must
/// authorize the CURRENT owner (live authority edge), never the historical
/// `created_by` fact. A Bot created by alice but transferred to bob deletes
/// for bob; the former creator without a current role is denied.
#[tokio::test]
async fn leave_bot_authorizes_current_owner_not_the_former_creator() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    // The historical creation fact says alice; the live owner edge says bob.
    register_bot(
        &fixture.registry,
        "transfer-bot",
        caps(Some("Transfer"), Some("Former-creator fixture bot"), "public"),
        Some("alice"),
    )
    .await;
    fixture
        .repo
        .seed_authority_owned("transfer-bot", "bob")
        .await
        .expect("seed live owner edge");

    let former_creator_delete = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_alice".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            bot_id: "transfer-bot".to_string(),
        })
        .await;
    assert!(
        matches!(
            former_creator_delete,
            Err(BotUseCaseError::Forbidden(ref message)) if message.contains("owner/manager role"),
        ),
        "the former creator must be denied: {former_creator_delete:?}"
    );
    assert!(fixture.registry.get("transfer-bot").await.is_some());

    let owner_delete = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_bob".to_string()),
            human_actor_id: Some("human_bob".to_string()),
            bot_id: "transfer-bot".to_string(),
        })
        .await
        .expect("the CURRENT owner deletes through the live edge");
    assert!(owner_delete.left);
    assert!(fixture.registry.get("transfer-bot").await.is_none());
}

/// F1 regression (spec §1.3/§8.2): a manager is at parity with the owner for
/// the delete lane (still subject to the TC/provider business conditions), so
/// a live direct manager may delete the Bot.
#[tokio::test]
async fn leave_bot_allows_live_manager_to_delete() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "managed-bot",
        caps(Some("Managed"), Some("Manager-managed fixture bot"), "public"),
        None,
    )
    .await;
    fixture
        .repo
        .seed_authority_owned("managed-bot", "bob")
        .await
        .expect("seed live owner edge");
    fixture
        .repo
        .seed_authority_manager_source("managed-bot", "alice", "direct", "manual")
        .await
        .expect("seed live manager edge");

    let left = service
        .leave_bot(BotLeaveCommand {
            caller_actor_id: Some("human_alice".to_string()),
            human_actor_id: Some("human_alice".to_string()),
            bot_id: "managed-bot".to_string(),
        })
        .await
        .expect("a live manager deletes at owner parity");
    assert!(left.left);
    assert!(fixture.registry.get("managed-bot").await.is_none());
}

/// F1 regression: the status and visibility mutation lanes authorize the same
/// live authority facts — the current owner/manager may change them, a
/// former creator without a role may not, and the Bot itself stays allowed.
#[tokio::test]
async fn status_and_visibility_lanes_authorize_live_roles() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "mutate-bot",
        caps(Some("Mutate"), Some("Status/visibility fixture bot"), "protected"),
        Some("alice"),
    )
    .await;
    fixture
        .repo
        .seed_authority_owned("mutate-bot", "bob")
        .await
        .expect("seed live owner edge");

    // The former creator (no live role) may not mutate status or visibility.
    let former_creator_status = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "mutate-bot".to_string(),
            status: bcs_service_api::BotDynamicStatus {
                status: "busy".to_string(),
                ..Default::default()
            },
        })
        .await;
    assert!(
        matches!(
            former_creator_status,
            Err(BotUseCaseError::Forbidden(ref message)) if message.contains("owner/manager role"),
        ),
        "the former creator must not update status: {former_creator_status:?}"
    );
    let former_creator_visibility = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "mutate-bot".to_string(),
            visibility: "private".to_string(),
        })
        .await;
    assert!(
        matches!(
            former_creator_visibility,
            Err(BotUseCaseError::Forbidden(ref message)) if message.contains("owner/manager role"),
        ),
        "the former creator must not set visibility: {former_creator_visibility:?}"
    );
    assert_eq!(
        fixture
            .registry
            .get("mutate-bot")
            .await
            .expect("stored bot")
            .capabilities
            .visibility,
        "protected"
    );

    // The CURRENT owner may mutate both lanes.
    let owner_status = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("human_bob".to_string()),
            bot_id: "mutate-bot".to_string(),
            status: bcs_service_api::BotDynamicStatus {
                status: "busy".to_string(),
                ..Default::default()
            },
        })
        .await
        .expect("the current owner updates status");
    assert!(owner_status.updated);

    let owner_visibility = service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("human_bob".to_string()),
            bot_id: "mutate-bot".to_string(),
            visibility: "private".to_string(),
        })
        .await
        .expect("the current owner sets visibility");
    assert_eq!(owner_visibility.visibility, "private");

    // The Bot itself keeps its self-lane (bot token status/visibility).
    let self_status = service
        .update_status(BotStatusUpdateCommand {
            caller_actor_id: Some("mutate-bot".to_string()),
            bot_id: "mutate-bot".to_string(),
            status: bcs_service_api::BotDynamicStatus {
                status: "idle".to_string(),
                ..Default::default()
            },
        })
        .await
        .expect("bot self lane stays allowed");
    assert!(self_status.updated);
    service
        .set_visibility(BotVisibilityCommand {
            caller_actor_id: Some("mutate-bot".to_string()),
            bot_id: "mutate-bot".to_string(),
            visibility: "public".to_string(),
        })
        .await
        .expect("bot self visibility lane stays allowed");
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
    fixture
        .repo
        .seed_authority_owned("visibility-bot", "alice")
        .await
        .expect("seed live owner edge");

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
    fixture
        .repo
        .seed_authority_owned("visibility-bot", "alice")
        .await
        .expect("seed live owner edge");

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
            if message.contains("owner/manager role")
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

    // A Bot whose ownership is still UNINITIALIZED never falls back to a
    // permissive legacy allowance: the live lane answers with the typed
    // ownership_not_initialized branch (spec §12.4) instead of a denial
    // that could look like a plain permission decision.
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
        Err(BotUseCaseError::Service(ServiceError::Authority(
            bcs_service_api::types::error::AuthorityError::OwnershipNotInitialized { .. }
        )))
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

fn caps_with_skill(
    name: Option<&str>,
    summary: Option<&str>,
    visibility: &str,
    skill: &str,
) -> BotCapabilities {
    BotCapabilities {
        skills: vec![bcs_service_api::Skill::new(skill)],
        ..caps(name, summary, visibility)
    }
}

fn caps_with_skills(
    name: Option<&str>,
    summary: Option<&str>,
    visibility: &str,
    skills: &[&str],
) -> BotCapabilities {
    BotCapabilities {
        skills: skills
            .iter()
            .map(|skill| bcs_service_api::Skill::new(*skill))
            .collect(),
        ..caps(name, summary, visibility)
    }
}

#[tokio::test]
async fn discovery_ignores_heartbeat_summary_and_keeps_static_metadata() {
    let fixture = RegistryFixture::new();
    register_bot(&fixture.registry, "summary-bot",
        caps(Some("Database Helper"), Some("Static SQL expertise"), "public"), None).await;
    fixture.registry.update_status("summary-bot").await;
    let service = fixture.service();
    let ignored = service.discover_bots(BotDiscoveryCommand {
        q: Some("ephemeral-only-marker".into()), ..Default::default()
    }).await.unwrap();
    assert_eq!(ignored.count, 0);
    let matched = service.discover_bots(BotDiscoveryCommand {
        q: Some("Static SQL".into()), ..Default::default()
    }).await.unwrap();
    assert_eq!(matched.count, 1);
    assert_eq!(matched.bots[0].bot_uuid, "summary-bot");
}
