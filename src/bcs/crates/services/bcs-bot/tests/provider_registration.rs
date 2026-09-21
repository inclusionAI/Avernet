use bcs_bot::core::provider_registration::ProviderRegistrationCore;
use bcs_bot::{BotCore, ProviderCore};
use bcs_bot_store::provider::MemoryBotProviderStore;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_relation::RelationCore;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::core::provider_registration::*;
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::{
    BotDeliveryTarget, BotRegistryCoreService, ProviderAuthMode, ProviderBotBindingRepoPort,
    ProviderBotCoreService, ProviderCoreService, ProviderCredential, ProviderCredentialRepoPort, ProviderRepoPort,
    RelationCoreService, ServiceError, ServiceResult,
};
use std::sync::Arc;

struct Fixture {
    core: ProviderRegistrationCore,
    registry: Arc<BotCore>,
    providers: Arc<MemoryProviderStore>,
    membership: Arc<MemoryBotProviderStore>,
    relations: Arc<RelationCore>,
    provider: String,
    _dir: tempfile::TempDir,
}

async fn fixture(endpoint: Option<&str>, self_service: bool) -> Fixture {
    fixture_with_credentials(endpoint, self_service, None).await
}

async fn fixture_with_credentials(
    endpoint: Option<&str>,
    self_service: bool,
    credentials: Option<Arc<dyn ProviderCredentialRepoPort>>,
) -> Fixture {
    fixture_with_auth(endpoint, self_service, credentials, ProviderAuthMode::StaticBearer).await
}

async fn fixture_with_auth(
    endpoint: Option<&str>,
    self_service: bool,
    credentials: Option<Arc<dyn ProviderCredentialRepoPort>>,
    auth_mode: ProviderAuthMode,
) -> Fixture {
    let dir = tempfile::tempdir().unwrap();
    let providers = Arc::new(MemoryProviderStore::new());
    let credentials = credentials.unwrap_or_else(|| providers.clone());
    let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    let membership = Arc::new(MemoryBotProviderStore::new(bots.clone(), providers.clone()));
    let relations = Arc::new(RelationCore::new());
    let registry = Arc::new(BotCore::with_provider_repos(
        bots,
        providers.clone(),
        credentials.clone(),
        providers.clone(),
    ));
    let admin = ProviderCore::new(
        providers.clone(),
        providers.clone(),
        providers.clone(),
        registry.clone(),
    );
    let provider = admin
        .register_provider(
            "Poolab".into(),
            endpoint.map(str::to_owned),
            auth_mode,
            "alice".into(),
            None,
            None,
        )
        .await
        .unwrap()
        .provider
        .provider_id;
    let core = ProviderRegistrationCore::new(
        providers.clone(),
        credentials,
        providers.clone(),
        membership.clone(),
        registry.clone(),
        relations.clone(),
        "prod".into(),
        if self_service {
            vec![provider.clone()]
        } else {
            vec![]
        },
        OutboundUrlGuard::strict(),
    );
    Fixture {
        core,
        registry,
        providers,
        membership,
        relations,
        provider,
        _dir: dir,
    }
}

#[derive(Clone, Copy)]
enum CredentialRead {
    Missing,
    Disabled,
    Empty(&'static str),
    Error,
}

struct ReadinessCredentials {
    outcome: CredentialRead,
    reads: std::sync::atomic::AtomicUsize,
}

impl ReadinessCredentials {
    fn new(outcome: CredentialRead) -> Arc<Self> {
        Arc::new(Self {
            outcome,
            reads: 0.into(),
        })
    }
}

#[async_trait::async_trait]
impl ProviderCredentialRepoPort for ReadinessCredentials {
    async fn get_credential_by_kind(
        &self,
        provider: &str,
        kind: &str,
    ) -> ServiceResult<Option<ProviderCredential>> {
        assert_eq!(kind, "downlink_bcs_to_provider");
        self.reads.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        match self.outcome {
            CredentialRead::Missing => Ok(None),
            CredentialRead::Error => Err(ServiceError::InternalError(
                "injected credential read failure".into(),
            )),
            _ => Ok(Some(ProviderCredential {
                provider_id: provider.into(),
                credential_kind: kind.into(),
                secret_value: match self.outcome {
                    CredentialRead::Empty(secret) => secret.into(),
                    _ => "test-downlink-secret".into(),
                },
                disabled: matches!(self.outcome, CredentialRead::Disabled),
                created_at: 1,
                updated_at: 1,
            })),
        }
    }

    async fn insert_credential(&self, _: ProviderCredential) -> ServiceResult<()> {
        panic!("registration must not write Provider credentials")
    }
    async fn get_credential_by_secret(
        &self,
        _: &str,
        _: &str,
    ) -> ServiceResult<Option<ProviderCredential>> {
        panic!("registration reads only the downlink credential by kind")
    }
    async fn list_credentials_by_provider(
        &self,
        _: &str,
    ) -> ServiceResult<Vec<ProviderCredential>> {
        panic!("registration must not enumerate Provider credentials")
    }
    async fn update_credential_disabled(
        &self,
        _: &str,
        _: &str,
        _: bool,
        _: u64,
    ) -> ServiceResult<Option<ProviderCredential>> {
        panic!("registration must not update Provider credentials")
    }
}

async fn assert_gateway_not_ready(outcome: CredentialRead) {
    for auth_mode in [ProviderAuthMode::StaticBearer, ProviderAuthMode::ProviderAdmin, ProviderAuthMode::AgentPass] {
        assert_gateway_not_ready_for_auth(outcome, auth_mode).await;
    }
}

async fn assert_gateway_not_ready_for_auth(outcome: CredentialRead, auth_mode: ProviderAuthMode) {
    let credentials = ReadinessCredentials::new(outcome);
    let f = fixture_with_auth(
        Some("https://shared.example.com/hook"),
        false,
        Some(credentials.clone()),
        auth_mode,
    )
    .await;
    let result = f
        .core
        .register(request(&f, ProviderRegistrationMode::Gateway))
        .await;
    assert!(
        matches!(
            result,
            Err(ServiceError::ProviderNotReadyForDownlink { .. })
        ),
        "gateway accepted an unavailable downlink credential"
    );
    assert_eq!(
        credentials.reads.load(std::sync::atomic::Ordering::SeqCst),
        1
    );
    assert!(
        f.membership
            .get_provider_bot_by_ref(&f.provider, "stable-ref")
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        f.providers
            .list_bindings_by_provider(&f.provider)
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn gateway_rejects_missing_downlink_credential_before_writes() {
    assert_gateway_not_ready(CredentialRead::Missing).await;
}

#[tokio::test]
async fn gateway_rejects_disabled_downlink_credential_before_writes() {
    assert_gateway_not_ready(CredentialRead::Disabled).await;
}

#[tokio::test]
async fn gateway_rejects_empty_downlink_secret_before_writes() {
    for secret in ["", " \t"] {
        assert_gateway_not_ready(CredentialRead::Empty(secret)).await;
    }
}

#[tokio::test]
async fn gateway_propagates_credential_read_failure_before_writes() {
    let credentials = ReadinessCredentials::new(CredentialRead::Error);
    let f = fixture_with_credentials(
        Some("https://shared.example.com/hook"),
        false,
        Some(credentials.clone()),
    )
    .await;
    assert!(
        matches!(f.core.register(request(&f, ProviderRegistrationMode::Gateway)).await,
        Err(ServiceError::InternalError(message)) if message == "injected credential read failure")
    );
    assert_eq!(
        credentials.reads.load(std::sync::atomic::Ordering::SeqCst),
        1
    );
    assert!(
        f.membership
            .get_provider_bot_by_ref(&f.provider, "stable-ref")
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        f.providers
            .list_bindings_by_provider(&f.provider)
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn issuance_and_upstream_do_not_read_downlink_credentials() {
    for outcome in [
        CredentialRead::Missing,
        CredentialRead::Disabled,
        CredentialRead::Empty(""),
        CredentialRead::Error,
    ] {
        let credentials = ReadinessCredentials::new(outcome);
        let f = fixture_with_credentials(None, false, Some(credentials.clone())).await;
        assert!(f.core.authorize(&f.provider, "alice").await.is_ok());
        assert!(
            f.core
                .register(request(&f, ProviderRegistrationMode::Plugin))
                .await
                .unwrap()
                .record
                .bot_uuid.len() > 0
        );
        assert_eq!(
            credentials.reads.load(std::sync::atomic::Ordering::SeqCst),
            0
        );
    }
}

fn request(f: &Fixture, mode: ProviderRegistrationMode) -> RegisterProviderBot {
    RegisterProviderBot {
        provider_id: f.provider.clone(),
        provider_bot_ref: "stable-ref".into(),
        owner: "alice".into(),
        mode,
        bot_name: "Test bot".into(),
        webhook_url: None,
    }
}

#[tokio::test]
async fn agentpass_upstream_preserves_agent_code_without_downlink_credentials() {
    let credentials = ReadinessCredentials::new(CredentialRead::Error);
    let f = fixture_with_auth(None, true, Some(credentials.clone()), ProviderAuthMode::AgentPass).await;
    assert!(f.core.authorize(&f.provider, "bob").await.is_ok());
    let mut command = request(&f, ProviderRegistrationMode::Plugin);
    command.owner = "bob".into();
    let registered = f.core.register(command).await.unwrap();
    assert_eq!(f.registry.find_bot_by_agent_code("stable-ref").await, Some(registered.record.bot_uuid.clone()));
    assert!(f.providers.get_binding_by_bot_uuid(&registered.record.bot_uuid).await.unwrap().is_none());
    assert!(matches!(f.registry.resolve_delivery_target(&registered.record.bot_uuid).await.unwrap(), BotDeliveryTarget::WebSocket { .. }));
    assert_eq!(credentials.reads.load(std::sync::atomic::Ordering::SeqCst), 0);
}

#[tokio::test]
async fn agentpass_gateway_supports_existing_delivery_and_callback_authentication() {
    for endpoint in [None, Some("https://shared.example.com/hook")] {
        let f = fixture_with_auth(endpoint, false, None, ProviderAuthMode::AgentPass).await;
        let mut command = request(&f, ProviderRegistrationMode::Gateway);
        if endpoint.is_none() {
            command.webhook_url = Some("https://individual.example.com/hook".into());
        }
        let registered = f.core.register(command).await.unwrap();
        assert_eq!(f.registry.find_bot_by_agent_code("stable-ref").await, Some(registered.record.bot_uuid.clone()));
        assert!(matches!(f.registry.resolve_delivery_target(&registered.record.bot_uuid).await.unwrap(),
            BotDeliveryTarget::HttpProvider { provider_bot_ref, webhook_url, .. }
                if provider_bot_ref == "stable-ref" && Some(webhook_url.as_str()) == registered.effective_webhook_url.as_deref()));
        let admin = ProviderCore::new(f.providers.clone(), f.providers.clone(), f.providers.clone(), f.registry.clone());
        let authenticated = admin.authenticate_agentpass_event(&f.provider, "stable-ref").await.unwrap();
        assert_eq!(authenticated.bot_uuid, registered.record.bot_uuid);
    }
}

#[tokio::test]
async fn upstream_persists_membership_without_delivery_binding_and_rejects_duplicate_ref() {
    let f = fixture(None, false).await;
    let command = request(&f, ProviderRegistrationMode::Plugin);
    let first = f.core.register(command.clone()).await.unwrap();
    assert!(matches!(f.core.register(command).await, Err(ServiceError::Conflict(_))));
    assert!(!first.record.bot_token.starts_with("MOCK_"));
    assert!(
        f.membership
            .get_provider_bot_by_ref(&f.provider, "stable-ref")
            .await
            .unwrap()
            .is_some()
    );
    assert!(
        f.providers
            .get_binding_by_bot_uuid(&first.record.bot_uuid)
            .await
            .unwrap()
            .is_none()
    );
    assert!(matches!(
        f.registry
            .resolve_delivery_target(&first.record.bot_uuid)
            .await
            .unwrap(),
        BotDeliveryTarget::WebSocket { .. }
    ));
    assert_eq!(
        f.registry
            .try_get(&first.record.bot_uuid)
            .await
            .unwrap()
            .unwrap()
            .created_by
            .as_deref(),
        Some("alice")
    );
    assert_eq!(
        f.registry
            .find_bot_by_token(&first.record.bot_token)
            .await
            .as_deref(),
        Some(first.record.bot_uuid.as_str())
    );
    assert!(
        f.relations
            .get_edge("human_alice", &first.record.bot_uuid, "prod")
            .await
            .unwrap()
            .unwrap()
            .is_creator
    );
    assert!(
        f.relations
            .get_edge(&first.record.bot_uuid, "human_alice", "prod")
            .await
            .unwrap()
            .is_some()
    );
}

#[tokio::test]
async fn gateway_inherits_or_overrides_endpoint_without_copying_default() {
    let f = fixture(Some("https://shared.example.com/hook"), false).await;
    let first = f
        .core
        .register(request(&f, ProviderRegistrationMode::Gateway))
        .await
        .unwrap();
    assert_eq!(
        first.effective_webhook_url.as_deref(),
        Some("https://shared.example.com/hook")
    );
    let binding = f
        .providers
        .get_binding_by_bot_uuid(&first.record.bot_uuid)
        .await
        .unwrap()
        .unwrap();
    assert!(binding.webhook_url.is_none());
    let mut override_request = request(&f, ProviderRegistrationMode::Gateway);
    override_request.provider_bot_ref = "independent".into();
    override_request.webhook_url = Some("https://independent.example.com/hook".into());
    let overridden = f.core.register(override_request).await.unwrap();
    assert_eq!(
        overridden.effective_webhook_url.as_deref(),
        Some("https://independent.example.com/hook")
    );
}

#[tokio::test]
async fn token_authorization_needs_no_webhook_but_gateway_validates_before_writes() {
    let f = fixture(None, false).await;
    assert!(f.core.authorize(&f.provider, "alice").await.is_ok());
    assert!(
        f.core
            .register(request(&f, ProviderRegistrationMode::Gateway))
            .await
            .is_err()
    );
    assert!(
        f.membership
            .get_provider_bot_by_ref(&f.provider, "stable-ref")
            .await
            .unwrap()
            .is_none()
    );
    let mut command = request(&f, ProviderRegistrationMode::Gateway);
    command.webhook_url = Some("https://bot.example.com/hook".into());
    assert!(f.core.register(command).await.is_ok());
}

#[tokio::test]
async fn authorization_and_conflicts_are_enforced_on_every_request() {
    let f = fixture(None, false).await;
    assert!(matches!(
        f.core.authorize(&f.provider, "bob").await,
        Err(ServiceError::Forbidden(_))
    ));
    let shared = fixture(None, true).await;
    assert!(shared.core.authorize(&shared.provider, "bob").await.is_ok());
    let command = request(&shared, ProviderRegistrationMode::Plugin);
    shared.core.register(command.clone()).await.unwrap();
    let mut hijack = command.clone();
    hijack.owner = "bob".into();
    assert!(matches!(
        shared.core.register(hijack).await,
        Err(ServiceError::Conflict(_))
    ));
    let mut rename = command.clone();
    rename.bot_name = "New name".into();
    assert!(matches!(
        shared.core.register(rename).await,
        Err(ServiceError::Conflict(_))
    ));
    shared
        .providers
        .update_provider_disabled(&shared.provider, true, 1)
        .await
        .unwrap();
    assert!(matches!(
        shared.core.register(command).await,
        Err(ServiceError::Forbidden(_))
    ));
}

#[tokio::test]
async fn self_service_cannot_redirect_shared_provider_credentials() {
    for auth_mode in [ProviderAuthMode::StaticBearer, ProviderAuthMode::ProviderAdmin, ProviderAuthMode::AgentPass] {
        assert_self_service_cannot_redirect(auth_mode).await;
    }
}

async fn assert_self_service_cannot_redirect(auth_mode: ProviderAuthMode) {
    let f = fixture_with_auth(Some("https://shared.example.com/hook"), true, None, auth_mode).await;
    assert!(f.core.authorize(&f.provider, "bob").await.is_ok());
    let mut command = request(&f, ProviderRegistrationMode::Gateway);
    command.owner = "bob".into();
    command.webhook_url = Some("https://untrusted.example.com/hook".into());
    assert!(matches!(
        f.core.register(command).await,
        Err(ServiceError::Forbidden(_))
    ));
    assert!(
        f.membership
            .get_provider_bot_by_ref(&f.provider, "stable-ref")
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        f.providers
            .list_bindings_by_provider(&f.provider)
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn self_service_can_register_upstream_or_use_provider_default_callback() {
    for auth_mode in [ProviderAuthMode::StaticBearer, ProviderAuthMode::ProviderAdmin, ProviderAuthMode::AgentPass] {
        assert_self_service_can_register(auth_mode).await;
    }
}

async fn assert_self_service_can_register(auth_mode: ProviderAuthMode) {
    let f = fixture_with_auth(Some("https://shared.example.com/hook"), true, None, auth_mode).await;
    for (reference, mode) in [
        ("upstream", ProviderRegistrationMode::Plugin),
        ("gateway", ProviderRegistrationMode::Gateway),
    ] {
        let mut command = request(&f, mode);
        command.owner = "bob".into();
        command.provider_bot_ref = reference.into();
        let result = f.core.register(command).await.unwrap();
        assert_eq!(result.record.owner, "bob");
        assert!(result.record.webhook_url.is_none());
        assert_eq!(
            result.effective_webhook_url.as_deref(),
            if mode == ProviderRegistrationMode::Gateway {
                Some("https://shared.example.com/hook")
            } else {
                None
            }
        );
    }
}

#[tokio::test]
async fn validation_rejects_upstream_webhook_and_unsafe_gateway_before_writes() {
    let f = fixture(None, false).await;
    for mode in [
        ProviderRegistrationMode::Plugin,
        ProviderRegistrationMode::Gateway,
    ] {
        let mut command = request(&f, mode);
        command.webhook_url = Some("http://127.0.0.1/hook".into());
        assert!(f.core.register(command).await.is_err());
        assert!(
            f.membership
                .get_provider_bot_by_ref(&f.provider, "stable-ref")
                .await
                .unwrap()
                .is_none()
        );
    }
}

#[tokio::test]
async fn completed_retry_never_recreates_deleted_bot() {
    let f = fixture(None, false).await;
    let command = request(&f, ProviderRegistrationMode::Plugin);
    let first = f.core.register(command.clone()).await.unwrap();
    f.registry.soft_delete(&first.record.bot_uuid).await;
    assert!(f.core.register(command).await.is_err());
    assert!(
        f.registry
            .try_get(&first.record.bot_uuid)
            .await
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
async fn changed_mode_or_webhook_and_rotated_token_conflict_without_mutation() {
    let f = fixture(Some("https://shared.example.com/hook"), false).await;
    let command = request(&f, ProviderRegistrationMode::Gateway);
    let first = f.core.register(command.clone()).await.unwrap();
    let mut change = command.clone();
    change.mode = ProviderRegistrationMode::Plugin;
    assert!(matches!(
        f.core.register(change).await,
        Err(ServiceError::Conflict(_))
    ));
    let mut change = command.clone();
    change.webhook_url = Some("https://different.example.com/hook".into());
    assert!(matches!(
        f.core.register(change).await,
        Err(ServiceError::Conflict(_))
    ));
    f.registry
        .save_token(&first.record.bot_uuid, "rotated-test-runtime-token")
        .await
        .unwrap();
    assert!(matches!(
        f.core.register(command).await,
        Err(ServiceError::Conflict(_))
    ));
    assert_eq!(
        f.registry
            .load_token(&first.record.bot_uuid)
            .await
            .as_deref(),
        Some("rotated-test-runtime-token")
    );
}


#[tokio::test]
async fn concurrent_duplicates_conflict_but_distinct_refs_can_register() {
    let f = fixture(None, false).await;
    let command = request(&f, ProviderRegistrationMode::Plugin);
    let (a, b) = tokio::join!(f.core.register(command.clone()), f.core.register(command.clone()));
    assert_eq!(usize::from(a.is_ok()) + usize::from(b.is_ok()), 1);
    assert!(matches!(a.err().or_else(|| b.err()), Some(ServiceError::Conflict(_))));
    let mut another = command;
    another.provider_bot_ref = "another-ref".into();
    f.core.register(another).await.unwrap();
    assert_eq!(f.membership.list_provider_bot_metadata(Some(&f.provider)).await.unwrap().len(), 2);
}
