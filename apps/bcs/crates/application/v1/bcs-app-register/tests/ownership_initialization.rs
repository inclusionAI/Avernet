//! Registration ownership-initialization contract (plan Task 6, spec 13.3).
//!
//! Every assertion drives the REAL `RegisterServiceImpl` facade wired to the
//! REAL bcs-bot application services (`Bot`, `BotOnboarding`, `HumanActor`,
//! `ProviderManagement`), the REAL `ProviderRegistrationCore` and the REAL
//! plan-Task-5 store lanes. `MemoryBotRepo` IS the recording lifecycle core
//! (levers count committed initializations and arm write failures); the
//! SQLite `PersistentBotRepo` runs the production SQL transactions. Nothing
//! is faked or bypassed.

use std::sync::Arc;

use bcs_bot::core::provider_registration::ProviderRegistrationCore;
use bcs_bot::{Bot, BotCore, BotOnboarding, HumanActor, ProviderCore, ProviderManagement};
use bcs_bot_store::provider::MemoryBotProviderStore;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore, PersistentBotRepo};
use bcs_relation::RelationCore;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::application::v1::{
    AuthenticatedCaller, AuthenticatedUserIdentity, IssueRegisterToken, RegisterService,
};
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::OwnershipState;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::port::repo::ProviderBotBindingRepoPort;
use bcs_service_api::{
    AdminBotOnboardCommand, BotConnectCommand, BotManagementService, BotOnboardingService,
    BotRegistryCoreService, CurrentHumanActorCommand, HumanActorService, OnboardActorIdentity,
    ProviderAuthMode, ProviderCoreService, ProviderManagementService, RegisterProviderBotCommand,
    SwitchDeliveryToProviderCommand,
};
use bcs_app_register::RegisterServiceImpl;
use tempfile::TempDir;

const SECRET: &[u8] = b"test-secret-key-32-bytes-long!!!";

fn human_caller(id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: None,
        user: Some(AuthenticatedUserIdentity {
            id: id.to_string(),
            username: id.to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn v1_command(token: String, name: &str) -> bcs_service_api::application::v1::RegisterBot {
    bcs_service_api::application::v1::RegisterBot {
        token,
        bot_name: name.to_string(),
        mode: None,
        provider_bot_ref: None,
        webhook_url: None,
    }
}

async fn issue_v1_token(register: &RegisterServiceImpl, staff_no: &str) -> String {
    register
        .issue_register_token(IssueRegisterToken {
            caller: human_caller(staff_no),
            provider_id: None,
        })
        .await
        .expect("issue v1 register token")
        .token
}

// -- Real-facade harness: Memory twin of the Task 5 lifecycle core. --

struct MemoryHarness {
    register: RegisterServiceImpl,
    management: Arc<Bot>,
    human_actor: Arc<HumanActor>,
    provider_management: Arc<ProviderManagement>,
    registry: Arc<BotCore>,
    repo: Arc<MemoryBotRepo>,
    membership: Arc<MemoryBotProviderStore>,
    providers: Arc<MemoryProviderStore>,
    provider_id: String,
    provider_admin_token: String,
    _dir: TempDir,
}

async fn memory_harness(
    auth_mode: ProviderAuthMode,
    endpoint: Option<&str>,
    self_service: bool,
) -> MemoryHarness {
    let dir = TempDir::new().unwrap();
    let providers = Arc::new(MemoryProviderStore::new());
    let repo = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    let membership = Arc::new(MemoryBotProviderStore::new(repo.clone(), providers.clone()));
    let relations = Arc::new(RelationCore::new());
    let registry = Arc::new(BotCore::with_provider_repos(
        repo.clone(),
        providers.clone(),
        providers.clone(),
        providers.clone(),
    ));
    let provider_core = Arc::new(ProviderCore::new(
        providers.clone(),
        providers.clone(),
        providers.clone(),
        registry.clone(),
    ));
    let registered = provider_core
        .register_provider(
            "Test Provider".into(),
            endpoint.map(str::to_string),
            auth_mode,
            "staff-1".into(),
            None,
            None,
        )
        .await
        .expect("register provider");
    let provider_id = registered.provider.provider_id.clone();
    let provider_admin_token = registered.provider_admin_token.clone();
    let core = ProviderRegistrationCore::new(
        providers.clone(),
        providers.clone(),
        providers.clone(),
        membership.clone(),
        registry.clone(),
        relations.clone(),
        bcs_config::resolve_env_str(),
        if self_service {
            vec![provider_id.clone()]
        } else {
            vec![]
        },
        OutboundUrlGuard::strict(),
    );
    let management = Arc::new(
        Bot::new(registry.clone())
            .with_bot_core(registry.clone())
            .with_relation(relations.clone()),
    );
    let onboarding = Arc::new(BotOnboarding::new(
        registry.clone(),
        relations.clone(),
        true,
        None,
    ));
    let human_actor = Arc::new(HumanActor::new(registry.clone(), relations.clone()));
    let provider_management = Arc::new(ProviderManagement::new(
        provider_core.clone(),
        provider_core,
        registry.clone(),
        relations.clone(),
    ));
    let register = RegisterServiceImpl::new(
        management.clone(),
        onboarding.clone(),
        SECRET.to_vec(),
    )
    .with_provider_registration(Arc::new(core));
    MemoryHarness {
        register,
        management,
        human_actor,
        provider_management,
        registry,
        repo,
        membership,
        providers,
        provider_id,
        provider_admin_token,
        _dir: dir,
    }
}

async fn default_harness() -> MemoryHarness {
    memory_harness(ProviderAuthMode::StaticBearer, None, false).await
}

/// Real-facade v1 register: success initializes ownership; runtime reconnect
/// finds the same owner; a no-Human runtime connect keeps the version-0
/// exception; a required authority write failure fails the registration.
#[tokio::test]
async fn v1_register_initializes_authority_and_reconnect_preserves_owner() {
    let harness = default_harness().await;
    let token = issue_v1_token(&harness.register, "85020").await;
    let registration = harness
        .register
        .register_bot(v1_command(token, "初始化机器人"))
        .await
        .expect("v1 registration succeeds");
    // Registration success == initialized ownership (spec 13.3): version 1,
    // the trusted token Human is the unique owner, one committed init audit
    // and one default profile.
    let state = harness
        .repo
        .ownership(&registration.bot_uuid)
        .await
        .expect("registered bot has initialized authority");
    assert_eq!(state.owner_user_id, "85020");
    assert_eq!(state.ownership_version, 1);
    let init_count = harness
        .repo
        .authority_ownership_initialization_count(&registration.bot_uuid)
        .await
        .unwrap();
    assert_eq!(init_count, 1);
    assert!(
        harness
            .repo
            .authority_default_profile_id(&registration.bot_uuid)
            .await
            .unwrap()
            .is_some()
    );
    let owner_before_reconnect = state.owner_user_id;

    // Runtime reconnect with the returned credential: the same live Bot, and
    // the authority slot is untouched by the handshake.
    let reconnect = harness
        .management
        .connect_bot(BotConnectCommand {
            caller_actor_id: None,
            token: Some(registration.bot_token.clone()),
            bot_id: None,
            protocol_version: None,
        })
        .await
        .expect("runtime reconnect");
    assert_eq!(reconnect.bot_uuid, registration.bot_uuid);
    let after = harness
        .repo
        .ownership(&registration.bot_uuid)
        .await
        .expect("authority survives reconnect");
    let owner_after_reconnect = after.owner_user_id;
    assert_eq!(owner_before_reconnect, owner_after_reconnect);
    assert_eq!(after.ownership_version, 1);
    assert_eq!(
        harness
            .repo
            .authority_ownership_initialization_count(&registration.bot_uuid)
            .await
            .unwrap(),
        1
    );

    // A required authority write failure must fail registration: no 2xx
    // "fix later", and the half-connected runtime Bot stays exactly as the
    // runtime handshake made it — uninitialized (version 0), unclaimed.
    harness.repo.arm_authority_write_failure();
    let register_result = harness
        .register
        .register_bot(v1_command(issue_v1_token(&harness.register, "85020").await, "失败机器人"))
        .await;
    assert!(register_result.is_err());
    if let Err(error) = &register_result {
        assert!(
            !error.to_string().contains(&registration.bot_token),
            "register error must not echo credentials"
        );
    }
    let live: Vec<String> = harness
        .registry
        .list_active()
        .await
        .iter()
        .filter(|bot| bot.actor_kind == bcs_service_api::ActorKind::Bot)
        .map(|bot| bot.bot_uuid.clone())
        .filter(|bot_id| bot_id != &registration.bot_uuid)
        .collect();
    assert!(!live.is_empty(), "the failed registration connected a runtime bot");
    for bot_id in live {
        assert!(
            matches!(
                harness.repo.ownership(&bot_id).await,
                Err(bcs_service_api::ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized { .. },
            ))
            ),
            "failed registration must never claim authority for its runtime bot"
        );
        assert!(
            harness
                .registry
                .try_get(&bot_id)
                .await
                .unwrap()
                .unwrap()
                .created_by
                .is_none(),
            "failed registration must not leak its trusted identity onto the runtime row"
        );
    }
}

/// The version-0 exception: a Bot runtime connect with NO Human context
/// registers uninitialized, and repeated connects stay uninitialized.
#[tokio::test]
async fn runtime_connect_without_human_keeps_version_zero_exception() {
    let harness = default_harness().await;
    let first = harness
        .management
        .connect_bot(BotConnectCommand {
            caller_actor_id: None,
            token: None,
            bot_id: None,
            protocol_version: None,
        })
        .await
        .expect("bare runtime connect");
    assert!(
        matches!(
            harness.repo.ownership(&first.bot_uuid).await,
            Err(bcs_service_api::ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized { .. },
            ))
        ),
        "no Human, no register token: the runtime handshake must not claim ownership"
    );
    assert_eq!(
        harness
            .repo
            .authority_ownership_initialization_count(&first.bot_uuid)
            .await
            .unwrap(),
        0
    );
    // Reconnecting with the runtime credential keeps the same uninitialized
    // state (reconnect never writes owner/manager/version/created_by).
    let second = harness
        .management
        .connect_bot(BotConnectCommand {
            caller_actor_id: None,
            token: Some(first.token.clone()),
            bot_id: None,
            protocol_version: None,
        })
        .await
        .expect("runtime reconnect");
    assert_eq!(second.bot_uuid, first.bot_uuid);
    assert!(
        matches!(
            harness.repo.ownership(&first.bot_uuid).await,
            Err(bcs_service_api::ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized { .. },
            ))
        ),
        "reconnect must not initialize or rewrite authority"
    );
}

/// Repeated ensure-human keeps authority exactly as the trusted first
/// registration established it (spec 13.3: ensure-human never re-claims and
/// never rewrites owner/manager/version/created_by).
#[tokio::test]
async fn ensure_human_never_rewrites_initialized_authority() {
    let harness = default_harness().await;
    let registration = harness
        .register
        .register_bot(v1_command(issue_v1_token(&harness.register, "85020").await, "认领机器人"))
        .await
        .expect("register");
    let before = harness
        .repo
        .ownership(&registration.bot_uuid)
        .await
        .unwrap();
    for nick in ["第一次", "第二次"] {
        let result = harness
            .human_actor
            .ensure_current_human_actor(CurrentHumanActorCommand {
                staff_no: Some("85020".into()),
                nick_name: Some(nick.into()),
            })
            .await
            .expect("ensure_current_human_actor");
        assert!(
            result.matched_bots.contains(&registration.bot_uuid),
            "the owned bot is matched for the legacy relation bind"
        );
    }
    let after = harness
        .repo
        .ownership(&registration.bot_uuid)
        .await
        .unwrap();
    assert_eq!(after.owner_user_id, before.owner_user_id);
    assert_eq!(after.ownership_version, before.ownership_version);
    assert_eq!(
        harness
            .registry
            .try_get(&registration.bot_uuid)
            .await
            .unwrap()
            .unwrap()
            .created_by
            .as_deref(),
        Some("85020")
    );
    assert_eq!(
        harness
            .repo
            .authority_ownership_initialization_count(&registration.bot_uuid)
            .await
            .unwrap(),
        1
    );
}

/// Provider switch binds delivery only: never re-writes the initialized
/// owner slot nor resets `created_by` to the ref's owner suffix.
#[tokio::test]
async fn provider_switch_preserves_initialized_authority_and_created_by() {
    let harness = memory_harness(
        ProviderAuthMode::StaticBearer,
        Some("https://shared.example.test/hook"),
        false,
    )
    .await;
    let registration = harness
        .register
        .register_bot(v1_command(issue_v1_token(&harness.register, "85020").await, "切换机器人"))
        .await
        .expect("register");
    let switched = harness
        .management
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: registration.bot_uuid.clone(),
            provider_id: harness.provider_id.clone(),
            provider_bot_ref: "default:77777".into(),
            name: None,
            summary: None,
        })
        .await
        .expect("delivery switch");
    assert!(!switched.idempotent_replay);
    let state = harness
        .repo
        .ownership(&registration.bot_uuid)
        .await
        .unwrap();
    assert_eq!(state.owner_user_id, "85020");
    assert_eq!(state.ownership_version, 1);
    assert_eq!(
        harness
            .registry
            .try_get(&registration.bot_uuid)
            .await
            .unwrap()
            .unwrap()
            .created_by
            .as_deref(),
        Some("85020"),
        "provider switch must not reset created_by to the ref's owner suffix"
    );
    // Re-switch (idempotent replay of the same ref) stays equally inert.
    harness
        .management
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: registration.bot_uuid.clone(),
            provider_id: harness.provider_id.clone(),
            provider_bot_ref: "default:77777".into(),
            name: None,
            summary: None,
        })
        .await
        .expect("idempotent re-switch");
    assert_eq!(
        harness
            .repo
            .ownership(&registration.bot_uuid)
            .await
            .unwrap()
            .owner_user_id,
        "85020"
    );
}

// -- v2 (HMAC Provider-scoped) lane: trusted owner scope, self-service
// -- webhook override, AgentPass agent_code, duplicate ref 409 without
// -- credential replay, and the Provider-admin registration claim.

/// v2 signature path through the real core: issue a scoped token, redeem it
/// anonymously for plugin and gateway (+ self-service webhook override), and
/// pin that success is committed with initialized authority.
#[tokio::test]
async fn v2_scoped_registration_initializes_authority_with_self_service_override() {
    let harness = memory_harness(
        ProviderAuthMode::StaticBearer,
        Some("https://shared.example.test/hook"),
        true,
    )
    .await;
    let view = harness
        .register
        .issue_register_token(IssueRegisterToken {
            caller: human_caller("staff-1"),
            provider_id: Some(harness.provider_id.clone()),
        })
        .await
        .expect("issue v2 register token");
    let scope = view.registration.expect("scoped v2 issuance");
    assert_eq!(scope.token_version, 2);
    assert_eq!(scope.provider_id, harness.provider_id);

    // Plugin: membership without gateway binding, ownership initialized.
    let mut command = v1_command(view.token.clone(), "Provider Plugin Bot");
    command.provider_bot_ref = Some("ref-plugin".into());
    let plugin = harness
        .register
        .register_bot(command)
        .await
        .expect("v2 plugin registration");
    assert!(plugin.registration.as_ref().unwrap().webhook_url.is_none());
    assert!(
        harness
            .providers
            .get_binding_by_bot_uuid(&plugin.bot_uuid)
            .await
            .unwrap()
            .is_none(),
        "plugin registration never creates a gateway binding"
    );
    let plugin_state = harness.repo.ownership(&plugin.bot_uuid).await.unwrap();
    assert_eq!(plugin_state.owner_user_id, "staff-1");
    assert_eq!(plugin_state.ownership_version, 1);

    // Gateway + self-service webhook override: the override is both the
    // stored Bot endpoint and the effective one; authority initialized.
    let mut command = v1_command(view.token.clone(), "Provider Gateway Bot");
    command.provider_bot_ref = Some("ref-gateway".into());
    command.mode = Some(bcs_service_api::application::v1::ProviderRegistrationMode::Gateway);
    command.webhook_url = Some("https://individual.example.test/hook".into());
    let gateway = harness
        .register
        .register_bot(command)
        .await
        .expect("v2 gateway registration with override");
    assert_eq!(
        gateway.registration.as_ref().unwrap().effective_webhook_url,
        Some("https://individual.example.test/hook".to_string())
    );
    let binding = harness
        .providers
        .get_binding_by_bot_uuid(&gateway.bot_uuid)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        binding.webhook_url.as_deref(),
        Some("https://individual.example.test/hook")
    );
    assert_eq!(
        harness.repo.ownership(&gateway.bot_uuid).await.unwrap(),
        OwnershipState {
            owner_user_id: "staff-1".into(),
            ownership_version: 1,
        }
    );
}

/// For AgentPass Providers the real ref is persisted as the Bot's agent_code
/// in both modes, and the same commit initializes ownership.
#[tokio::test]
async fn agentpass_scoped_registration_persists_agent_code_and_authority() {
    for mode in [
        bcs_service_api::application::v1::ProviderRegistrationMode::Plugin,
        bcs_service_api::application::v1::ProviderRegistrationMode::Gateway,
    ] {
        let harness = memory_harness(ProviderAuthMode::AgentPass, Some("https://shared.example.test/hook"), false).await;
        let token = harness
            .register
            .issue_register_token(IssueRegisterToken {
                caller: human_caller("staff-1"),
                provider_id: Some(harness.provider_id.clone()),
            })
            .await
            .unwrap()
            .token;
        let reference = format!("agentpass-ref-{}", if matches!(mode, bcs_service_api::application::v1::ProviderRegistrationMode::Plugin) { "plugin" } else { "gateway" });
        let mut command = v1_command(token, "AgentPass Bot");
        command.provider_bot_ref = Some(reference.clone());
        command.mode = Some(mode);
        let registered = harness
            .register
            .register_bot(command)
            .await
            .expect("agentpass registration");
        assert_eq!(
            harness.registry.find_bot_by_agent_code(&reference).await,
            Some(registered.bot_uuid.clone()),
            "provider_bot_ref is the real agent_code for AgentPass"
        );
        assert_eq!(
            harness.repo.ownership(&registered.bot_uuid).await.unwrap(),
            OwnershipState {
                owner_user_id: "staff-1".into(),
                ownership_version: 1,
            }
        );
    }
}

/// Duplicate Provider/ref is 409 (`registration_conflict`) with no credential
/// replay: the first registration keeps its credential, authority and
/// membership; nothing of the rejected attempt is readable back.
#[tokio::test]
async fn duplicate_scoped_ref_conflicts_without_credential_replay() {
    let harness = default_harness().await;
    let token = harness
        .register
        .issue_register_token(IssueRegisterToken {
            caller: human_caller("staff-1"),
            provider_id: Some(harness.provider_id.clone()),
        })
        .await
        .unwrap()
        .token;
    let mut first = v1_command(token.clone(), "First Bot");
    first.provider_bot_ref = Some("dup-ref".into());
    let first = harness
        .register
        .register_bot(first)
        .await
        .expect("first registration");

    let mut duplicate = v1_command(token.clone(), "Second Bot");
    duplicate.provider_bot_ref = Some("dup-ref".into());
    let error = harness
        .register
        .register_bot(duplicate)
        .await
        .expect_err("duplicate refs conflict");
    assert_eq!(error.code(), "registration_conflict");
    assert!(
        !error.to_string().contains(&first.bot_token),
        "conflict mapping must not echo the Bot runtime credential"
    );
    // The first Bot is untouched and its credential is still exactly the one
    // it was issued; the rejected attempt replayed nothing.
    assert_eq!(
        harness.registry.find_bot_by_token(&first.bot_token).await,
        Some(first.bot_uuid.clone())
    );
    assert_eq!(
        harness.repo.ownership(&first.bot_uuid).await.unwrap(),
        OwnershipState {
            owner_user_id: "staff-1".into(),
            ownership_version: 1,
        }
    );
    let membership_records = harness
        .membership
        .list_provider_bot_metadata(Some(&harness.provider_id))
        .await
        .unwrap();
    assert_eq!(membership_records.len(), 1);
    assert_eq!(membership_records[0].bot_uuid, first.bot_uuid);
    // A valid v2 token still registers distinct refs after a conflict.
    let mut distinct = v1_command(token, "Distinct Bot");
    distinct.provider_bot_ref = Some("distinct-ref".into());
    let distinct = harness
        .register
        .register_bot(distinct)
        .await
        .expect("distinct ref registers");
    assert_ne!(distinct.bot_uuid, first.bot_uuid);
}

/// Provider-admin registration is a trusted first-registration entry: the
/// ref's owner claims exactly once; re-registration never re-claims.
#[tokio::test]
async fn provider_admin_registration_claims_first_ownership_once() {
    let harness = default_harness().await;
    let first = harness
        .provider_management
        .register_provider_bot(RegisterProviderBotCommand {
            webhook_url: None,
            provider_id: harness.provider_id.clone(),
            provider_admin_token: harness.provider_admin_token.clone(),
            name: "Admin Bot".into(),
            summary: None,
            owners: vec!["85020".into()],
            provider_bot_ref: "admin-ref-a".into(),
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            bot_uuid: None,
            reject_existing_bot_uuid: false,
            connection_mode: bcs_service_api::ProviderBotConnectionMode::Plugin,
        })
        .await
        .expect("provider-admin registration");
    assert!(first.created);
    let first_state = harness.repo.ownership(&first.bot_uuid).await.unwrap();
    assert_eq!(first_state.owner_user_id, "85020");
    assert_eq!(first_state.ownership_version, 1);
    assert_eq!(
        harness
            .repo
            .authority_ownership_initialization_count(&first.bot_uuid)
            .await
            .unwrap(),
        1
    );

    // A distinct second ref initializes its Bot; re-running the SAME ref is
    // an idempotent replay that must not re-claim.
    let second = harness
        .provider_management
        .register_provider_bot(RegisterProviderBotCommand {
            webhook_url: None,
            provider_id: harness.provider_id.clone(),
            provider_admin_token: harness.provider_admin_token.clone(),
            name: "Admin Bot Two".into(),
            summary: None,
            owners: vec!["85020".into()],
            provider_bot_ref: "admin-ref-b".into(),
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            bot_uuid: None,
            reject_existing_bot_uuid: false,
            connection_mode: bcs_service_api::ProviderBotConnectionMode::Plugin,
        })
        .await
        .expect("second provider-admin registration");
    assert_eq!(
        harness.repo.ownership(&second.bot_uuid).await.unwrap(),
        OwnershipState {
            owner_user_id: "85020".into(),
            ownership_version: 1,
        }
    );
    // Idempotent replay on the first ref: no extra claim, no authority write.
    harness
        .provider_management
        .register_provider_bot(RegisterProviderBotCommand {
            webhook_url: None,
            provider_id: harness.provider_id.clone(),
            provider_admin_token: harness.provider_admin_token.clone(),
            name: "Admin Bot".into(),
            summary: None,
            owners: vec!["85020".into()],
            provider_bot_ref: "admin-ref-a".into(),
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            bot_uuid: None,
            reject_existing_bot_uuid: false,
            connection_mode: bcs_service_api::ProviderBotConnectionMode::Plugin,
        })
        .await
        .expect("idempotent admin re-registration");
    assert_eq!(
        harness
            .repo
            .authority_ownership_initialization_count(&first.bot_uuid)
            .await
            .unwrap(),
        1,
        "a re-registration must never add an initialization audit row"
    );
    assert_eq!(
        harness.repo.ownership(&first.bot_uuid).await.unwrap(),
        first_state
    );
}

// -- SQLite harness: real PersistentBotRepo SQL transactions, used where a
// -- test must re-shape authority the way a committed transfer would.

const SQLITE_AUTHORITY_MIGRATION: &str =
    include_str!("../../../../../migrations/sqlite/034_bot_authority.sql");

async fn install_sqlite_schema(db: &dyn bcs_db_api::DbPlugin) {
    use bcs_db_api::DbStatement;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            bot_uuid TEXT NOT NULL, env TEXT NOT NULL, name TEXT,
            bot_info TEXT, session_token TEXT, created_by TEXT, visibility TEXT,
            status TEXT NOT NULL DEFAULT 'online', actor_kind TEXT NOT NULL DEFAULT 'bot',
            is_deleted INTEGER NOT NULL DEFAULT 0, agent_code TEXT DEFAULT NULL,
            connection_mode TEXT DEFAULT NULL,
            registered_at TEXT, updated_at TEXT,
            ownership_version INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (bot_uuid, env))",
    ))
    .await
    .unwrap();
    for statement in SQLITE_AUTHORITY_MIGRATION
        .split(';')
        .map(str::trim)
        .filter(|statement| !statement.is_empty())
    {
        db.execute(DbStatement::new(statement)).await.unwrap();
    }
    db.execute(DbStatement::new(
        "CREATE TABLE IF NOT EXISTS permission_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, \
         bot_id TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL DEFAULT 'default', \
         description TEXT, rules_template TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, \
         digest TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0, \
         status TEXT NOT NULL DEFAULT 'active', created_by TEXT NOT NULL, updated_by TEXT, \
         gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
         gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .unwrap();
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_profile_bot_env_default \
         ON permission_profiles(bot_id, env, is_default) WHERE status = 'active'",
    ))
    .await
    .unwrap();
}

struct SqliteHarness {
    register: RegisterServiceImpl,
    registry: Arc<BotCore>,
    db: Arc<dyn bcs_db_api::DbPlugin>,
}

async fn sqlite_harness() -> SqliteHarness {
    use bcs_db_local::LocalSqliteDbPlugin;
    let db: Arc<dyn bcs_db_api::DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    install_sqlite_schema(db.as_ref()).await;
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), bcs_db_api::DbSqlFlavor::Sqlite);
    let registry = Arc::new(BotCore::with_repo(Arc::new(repo)));
    let relations = Arc::new(RelationCore::new());
    let onboarding = Arc::new(BotOnboarding::new(
        registry.clone(),
        relations.clone(),
        true,
        None,
    ));
    let register = RegisterServiceImpl::new(
        Arc::new(Bot::new(registry.clone())) as Arc<dyn BotManagementService>,
        onboarding,
        SECRET.to_vec(),
    );
    SqliteHarness { register, registry, db }
}

async fn scalar(
    db: &dyn bcs_db_api::DbPlugin,
    sql: &str,
    params: Vec<bcs_db_api::DbValue>,
) -> i64 {
    let rows = db
        .query(bcs_db_api::DbStatement::with_params(sql, params))
        .await
        .unwrap();
    rows.first()
        .and_then(|row| row.get_i64("value").ok().flatten())
        .unwrap_or(0)
}

/// After a committed ownership change (the transfer-decided shape: a new
/// owner edge plus ownership_version 2), a repeated onboarding through the
/// legacy admin entry never resets `created_by`, the owner or the version.
#[tokio::test]
async fn reonboard_after_transfer_preserves_created_by_and_version() {
    let harness = sqlite_harness().await;
    let token = issue_v1_token(&harness.register, "85020").await;
    let registration = harness
        .register
        .register_bot(v1_command(token, "转交机器人"))
        .await
        .expect("registration initializes ownership");
    let env = bcs_config::resolve_env_str();
    let bot_id = registration.bot_uuid.clone();

    let created_by_before_transfer = harness
        .registry
        .try_get(&bot_id)
        .await
        .unwrap()
        .unwrap()
        .created_by
        .expect("trusted registration records created_by");
    assert_eq!(created_by_before_transfer, "85020");

    // The store shape of a decided transfer (plan Task 8): a new unique owner
    // edge under the same row lock, plus the bumped ownership version.
    use bcs_db_api::{DbStatement, DbValue};
    harness
        .db
        .execute(DbStatement::with_params(
            "UPDATE bcs_bots SET ownership_version = 2 WHERE env = ? AND bot_uuid = ?",
            vec![DbValue::from(env.as_str()), DbValue::from(bot_id.as_str())],
        ))
        .await
        .unwrap();
    harness
        .db
        .execute(DbStatement::with_params(
            "UPDATE edge_grants SET from_id = ? WHERE env = ? AND to_id = ? \
             AND grant_kind = 'owner' AND status = 'approved'",
            vec![
                DbValue::from("human_99999"),
                DbValue::from(env.as_str()),
                DbValue::from(bot_id.as_str()),
            ],
        ))
        .await
        .unwrap();
    let params = vec![DbValue::from(env.as_str()), DbValue::from(bot_id.as_str())];
    let version_after_transfer = scalar(
        harness.db.as_ref(),
        "SELECT ownership_version AS value FROM bcs_bots WHERE env = ? AND bot_uuid = ?",
        params.clone(),
    )
    .await;
    assert_eq!(version_after_transfer, 2);
    let transferred_owner = scalar(
        harness.db.as_ref(),
        "SELECT COUNT(*) AS value FROM edge_grants WHERE env = ? AND to_id = ? \
         AND grant_kind = 'owner' AND status = 'approved' AND from_id = 'human_99999'",
        params,
    )
    .await;
    assert_eq!(transferred_owner, 1);

    // Re-onboard through the legacy admin entry with the ORIGINAL creator
    // identity (now neither owner nor manager): an authority no-op.
    let onboarding = harness_onboarding(&harness).await;
    let result = onboarding
        .admin_onboard_bot(AdminBotOnboardCommand {
            bot_uuid: bot_id.clone(),
            name: Some("转交机器人".into()),
            summary: None,
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            binding_channels: None,
            actor_identity: Some(OnboardActorIdentity {
                staff_no: "85020".into(),
                nick_name: None,
            }),
        })
        .await
        .expect("re-onboard succeeds as an authority no-op");
    assert!(result.onboarded);

    let params = vec![DbValue::from(env.as_str()), DbValue::from(bot_id.as_str())];
    let version_after_reonboard = scalar(
        harness.db.as_ref(),
        "SELECT ownership_version AS value FROM bcs_bots WHERE env = ? AND bot_uuid = ?",
        params.clone(),
    )
    .await;
    assert_eq!(version_after_reonboard, version_after_transfer);
    let created_by_after_reonboard = harness
        .registry
        .try_get(&bot_id)
        .await
        .unwrap()
        .unwrap()
        .created_by
        .expect("created_by survived the re-onboard");
    assert_eq!(created_by_before_transfer, created_by_after_reonboard);
    let owner_edge = scalar(
        harness.db.as_ref(),
        "SELECT COUNT(*) AS value FROM edge_grants WHERE env = ? AND to_id = ? \
         AND grant_kind = 'owner' AND status = 'approved' AND from_id = 'human_99999'",
        params.clone(),
    )
    .await;
    assert_eq!(owner_edge, 1, "the transferred owner edge is untouched");
    let initialization_rows = scalar(
        harness.db.as_ref(),
        "SELECT COUNT(*) AS value FROM bot_ownership_initializations \
         WHERE env = ? AND bot_id = ?",
        params,
    )
    .await;
    assert_eq!(initialization_rows, 1, "no second initialization audit row");
}

async fn harness_onboarding(harness: &SqliteHarness) -> Arc<BotOnboarding> {
    // The same real onboarding application the facade used at registration.
    let relations = Arc::new(RelationCore::new());
    Arc::new(BotOnboarding::new(
        harness.registry.clone(),
        relations,
        true,
        None,
    ))
}