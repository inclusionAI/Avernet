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
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_organization::{OrganizationCore, OrganizationManagement};
use bcs_organization_store::MemoryOrganizationRepo;
use bcs_service_api::types::{Organization, OrganizationMember};
use bcs_bot_store::MemoryBotRepo;
use tempfile::TempDir;

use bcs_bot::{Bot, BotCore, ProviderCore};

struct RegistryFixture {
    registry: Arc<BotCore>,
    authority: Arc<bcs_test_support::LazySeedAuthorityHook>,
    _data_dir: TempDir,
}

impl RegistryFixture {
    fn new() -> Self {
        let data_dir = tempfile::tempdir().expect("temp data dir");
        // ONE shared memory store: the authority hook sees the
        // edges it seeds AND the registry's store-level
        // retirement re-verifies the same operator edge in its
        // atomic span (store 事务内复验 cannot consult a
        // second store).
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(
            data_dir.path().to_path_buf(),
        ));
        let chain =
            bcs_test_support::lazy_authority_chain_with_repo(bot_repo.clone());
        let registry = Arc::new(BotCore::with_repo(bot_repo));
        Self {
            registry,
            authority: chain.hook,
            _data_dir: data_dir,
        }
    }

    fn service(&self) -> Bot {
        let registry: Arc<dyn BotRegistryCoreService> = self.registry.clone();
        // 删除/配置/状态 consult the authority hook
        // (row 28 family); the lazy chain is seeded
        // per test via `seed_owner`.
        Bot::new(registry)
            .with_bot_core(self.registry.clone())
            .with_bot_authority(self.authority.clone())
    }

    fn service_with_friends(&self, friends: Vec<(&str, &str)>) -> Bot {
        let registry: Arc<dyn BotRegistryCoreService> = self.registry.clone();
        Bot::new_with_friend(registry, Arc::new(StaticFriendCoreService::new(friends)))
            .with_bot_authority(self.authority.clone())
    }

    /// Seed the CURRENT owner edge for one Bot
    /// (the row-28-family fixture fact).
    fn seed_owner(&self, bot_id: &str, user: &str) {
        self.authority.seed_owner(bot_id, user);
    }

    /// Seed the CURRENT manager edge for one
    /// Bot (the row-28-family fixture fact).
    fn seed_manager(&self, bot_id: &str, user: &str) {
        self.authority.seed_manager(bot_id, user);
    }
}

struct ProviderRegistryFixture {
    registry: Arc<BotCore>,
    provider: ProviderCore,
    authority: Arc<bcs_test_support::LazySeedAuthorityHook>,
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
            authority: bcs_test_support::lazy_authority_chain().hook,
            _data_dir: data_dir,
        }
    }

    fn service(&self) -> Bot {
        let registry: Arc<dyn BotRegistryCoreService> = self.registry.clone();
        Bot::new(registry)
            .with_bot_core(self.registry.clone())
            .with_bot_authority(self.authority.clone())
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

#[path = "bot_use_cases/queries.rs"]
mod queries;
#[path = "bot_use_cases/discovery.rs"]
mod discovery;
#[path = "bot_use_cases/organization_discovery.rs"]
mod organization_discovery;
#[path = "bot_use_cases/connection.rs"]
mod connection;
#[path = "bot_use_cases/status.rs"]
mod status;
#[path = "bot_use_cases/authority_writes.rs"]
mod authority_writes;
