use std::sync::Arc;

use bcs_bot::core::{BotCore, ProviderCore};
use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_organization::{OrganizationCore, OrganizationManagement};
use bcs_organization_store::MemoryOrganizationRepo;
use bcs_service_api::{
    BotRegistryCoreService, CreateOrganizationCommand, OrganizationAuth,
    OrganizationCandidatePageQuery, OrganizationCandidateQuery, OrganizationCoreService, OrganizationManagementService,
    OrganizationMemberAuth, OrganizationMemberPageQuery, OrganizationMemberProfilePatch,
    ProviderAuthMode,
    ProviderBotBindingRepoPort, ProviderBotCoreService, ProviderCoreService,
    ProviderCredentialRepoPort, ProviderOrganizationManagementConfig, ProviderRepoPort,
    PutOrganizationMemberCommand, RegisterProviderBotParams, ServiceError, Skill,
    UpdateOrganizationCommand, UpdateOrganizationMemberProfileCommand,
};

struct ProviderFixture {
    provider_id: String,
    admin_token: String,
}

struct TestContext {
    service: OrganizationManagement,
    core: Arc<OrganizationCore>,
    registry: Arc<BotCore>,
    provider_core: Arc<ProviderCore>,
    _temp_dir: tempfile::TempDir,
}

fn provider_auth(provider: &ProviderFixture) -> OrganizationAuth {
    OrganizationAuth {
        provider_id: provider.provider_id.clone(),
        provider_admin_token: provider.admin_token.clone(),
    }
}

fn bad_provider_auth(provider: &ProviderFixture) -> OrganizationAuth {
    OrganizationAuth {
        provider_id: provider.provider_id.clone(),
        provider_admin_token: "not-a-real-admin-token".to_string(),
    }
}

fn member_auth(provider: &ProviderFixture) -> OrganizationMemberAuth {
    OrganizationMemberAuth {
        provider_admin_token: provider.admin_token.clone(),
    }
}

fn candidate_query() -> OrganizationCandidateQuery {
    OrganizationCandidateQuery {
        organization_code: "promo-2026".to_string(),
        ..OrganizationCandidateQuery::default()
    }
}

async fn test_context() -> TestContext {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let providers: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let candidate_reads: Arc<dyn bcs_service_api::OrganizationCandidateReadPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        providers.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let provider_core = Arc::new(ProviderCore::new(
        providers.clone(),
        provider_credentials,
        provider_bindings.clone(),
        registry.clone(),
    ));
    let organization_core = Arc::new(OrganizationCore::new(
        "test".to_string(),
        Arc::new(MemoryOrganizationRepo::new()),
        providers,
        provider_bindings,
        candidate_reads,
        registry.clone(),
    ));
    let service = OrganizationManagement::new(provider_core.clone(), organization_core.clone());
    TestContext {
        service,
        core: organization_core,
        registry,
        provider_core,
        _temp_dir: temp_dir,
    }
}

async fn register_provider(ctx: &TestContext, name: &str) -> ProviderFixture {
    register_provider_with_auth(ctx, name, ProviderAuthMode::StaticBearer).await
}

async fn register_provider_with_auth(
    ctx: &TestContext,
    name: &str,
    auth_mode: ProviderAuthMode,
) -> ProviderFixture {
    let registered = ctx
        .provider_core
        .register_provider(
            name.to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            auth_mode,
            "11111111".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider");
    ProviderFixture {
        provider_id: registered.provider.provider_id,
        admin_token: registered.provider_admin_token,
    }
}

async fn grant_manager(
    ctx: &TestContext,
    resource: &ProviderFixture,
    manager: &ProviderFixture,
) {
    ctx.provider_core
        .update_provider(
            &resource.provider_id,
            &resource.admin_token,
            "11111111",
            None,
            None,
            None,
            None,
            Some(ProviderOrganizationManagementConfig {
                authorized_manager_provider_ids: vec![manager.provider_id.clone()],
            }),
        )
        .await
        .expect("grant organization manager");
}

async fn register_bot(ctx: &TestContext, provider: &ProviderFixture, bot_uuid: &str) {
    ctx.provider_core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                webhook_url: None,
                bot_name: format!("{bot_uuid} name"),
                summary: Some(format!("{bot_uuid} summary")),
                owners: vec!["11111111".to_string()],
                provider_bot_ref: format!("{bot_uuid}-ref"),
                domains: vec!["marketing".to_string()],
                skills: vec![Skill::new("planning")],
                scopes: vec!["campaign".to_string()],
                bot_uuid: Some(bot_uuid.to_string()),
                connection_mode: bcs_service_api::ProviderBotConnectionMode::Gateway,
                initial_caller: None,
            },
        )
        .await
        .expect("register provider bot");
}

async fn create_org(ctx: &TestContext, provider: &ProviderFixture) {
    ctx.service
        .create(CreateOrganizationCommand {
            auth: provider_auth(provider),
            organization_code: "promo-2026".to_string(),
            name: "Promo 2026".to_string(),
            description: Some("campaign org".to_string()),
        })
        .await
        .expect("create organization");
}

#[path = "management/membership.rs"]
mod membership;
#[path = "management/conformance.rs"]
mod conformance;
#[path = "management/member_profile.rs"]
mod member_profile;
#[path = "management/candidates.rs"]
mod candidates;
#[path = "management/runtime_membership.rs"]
mod runtime_membership;
