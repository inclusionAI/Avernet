use bcs_bot::core::provider_registration::ProviderRegistrationCore;
use bcs_bot::{BotCore, ProviderCore};
use bcs_bot_store::provider_registration::MemoryProviderRegistrationStore;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_relation::RelationCore;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::core::provider_registration::*;
use bcs_service_api::port::repo::provider_registration::ProviderRegistrationRepoPort;
use bcs_service_api::{
    BotDeliveryTarget, BotRegistryCoreService, ProviderAuthMode, ProviderBotBindingRepoPort,
    ProviderCoreService, ProviderCredential, ProviderCredentialRepoPort, ProviderRepoPort,
    RelationCoreService, ServiceError, ServiceResult,
};
use std::sync::Arc;

struct Fixture {
    core: ProviderRegistrationCore,
    registry: Arc<BotCore>,
    providers: Arc<MemoryProviderStore>,
    journal: Arc<MemoryProviderRegistrationStore>,
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
    let dir = tempfile::tempdir().unwrap();
    let providers = Arc::new(MemoryProviderStore::new());
    let credentials = credentials.unwrap_or_else(|| providers.clone());
    let journal = Arc::new(MemoryProviderRegistrationStore::new());
    let relations = Arc::new(RelationCore::new());
    let registry = Arc::new(BotCore::with_provider_repos(
        Arc::new(MemoryBotRepo::with_base_dir(dir.path().into())),
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
            ProviderAuthMode::StaticBearer,
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
        journal.clone(),
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
        journal,
        relations,
        provider,
        _dir: dir,
    }
}

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
    let credentials = ReadinessCredentials::new(outcome);
    let f = fixture_with_credentials(
        Some("https://shared.example.com/hook"),
        false,
        Some(credentials.clone()),
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
        f.journal
            .get(&f.provider, "stable-ref")
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
async fn gateway_rejects_missing_downlink_credential_before_reservation() {
    assert_gateway_not_ready(CredentialRead::Missing).await;
}

#[tokio::test]
async fn gateway_rejects_disabled_downlink_credential_before_reservation() {
    assert_gateway_not_ready(CredentialRead::Disabled).await;
}

#[tokio::test]
async fn gateway_rejects_empty_downlink_secret_before_reservation() {
    for secret in ["", " \t"] {
        assert_gateway_not_ready(CredentialRead::Empty(secret)).await;
    }
}

#[tokio::test]
async fn gateway_propagates_credential_read_failure_before_reservation() {
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
        f.journal
            .get(&f.provider, "stable-ref")
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
                .register(request(&f, ProviderRegistrationMode::Upstream))
                .await
                .unwrap()
                .record
                .completed
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
async fn upstream_persists_membership_without_delivery_binding_and_retries_stably() {
    let f = fixture(None, false).await;
    let command = request(&f, ProviderRegistrationMode::Upstream);
    let first = f.core.register(command.clone()).await.unwrap();
    let second = f.core.register(command).await.unwrap();
    assert_eq!(first.record.bot_uuid, second.record.bot_uuid);
    assert_eq!(first.record.bot_token, second.record.bot_token);
    assert!(!first.record.bot_token.starts_with("MOCK_"));
    assert!(first.record.completed);
    assert!(
        f.journal
            .get(&f.provider, "stable-ref")
            .await
            .unwrap()
            .unwrap()
            .completed
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
async fn token_authorization_needs_no_webhook_but_gateway_validates_before_reservation() {
    let f = fixture(None, false).await;
    assert!(f.core.authorize(&f.provider, "alice").await.is_ok());
    assert!(
        f.core
            .register(request(&f, ProviderRegistrationMode::Gateway))
            .await
            .is_err()
    );
    assert!(
        f.journal
            .get(&f.provider, "stable-ref")
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
    let command = request(&shared, ProviderRegistrationMode::Upstream);
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
    let f = fixture(Some("https://shared.example.com/hook"), true).await;
    assert!(f.core.authorize(&f.provider, "bob").await.is_ok());
    let mut command = request(&f, ProviderRegistrationMode::Gateway);
    command.owner = "bob".into();
    command.webhook_url = Some("https://untrusted.example.com/hook".into());
    assert!(matches!(
        f.core.register(command).await,
        Err(ServiceError::Forbidden(_))
    ));
    assert!(
        f.journal
            .get(&f.provider, "stable-ref")
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
    let f = fixture(Some("https://shared.example.com/hook"), true).await;
    for (reference, mode) in [
        ("upstream", ProviderRegistrationMode::Upstream),
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
        ProviderRegistrationMode::Upstream,
        ProviderRegistrationMode::Gateway,
    ] {
        let mut command = request(&f, mode);
        command.webhook_url = Some("http://127.0.0.1/hook".into());
        assert!(f.core.register(command).await.is_err());
        assert!(
            f.journal
                .get(&f.provider, "stable-ref")
                .await
                .unwrap()
                .is_none()
        );
    }
}

#[tokio::test]
async fn completed_retry_never_recreates_deleted_bot() {
    let f = fixture(None, false).await;
    let command = request(&f, ProviderRegistrationMode::Upstream);
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

struct FailCompletionOnce {
    inner: Arc<MemoryProviderRegistrationStore>,
    fail: std::sync::atomic::AtomicBool,
}

#[async_trait::async_trait]
impl ProviderRegistrationRepoPort for FailCompletionOnce {
    async fn get(
        &self,
        provider: &str,
        reference: &str,
    ) -> bcs_service_api::ServiceResult<Option<ProviderRegistrationRecord>> {
        self.inner.get(provider, reference).await
    }
    async fn reserve(
        &self,
        record: ProviderRegistrationRecord,
    ) -> bcs_service_api::ServiceResult<ProviderRegistrationRecord> {
        self.inner.reserve(record).await
    }
    async fn complete(
        &self,
        provider: &str,
        reference: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        if self.fail.swap(false, std::sync::atomic::Ordering::SeqCst) {
            return Err(ServiceError::InternalError(
                "injected journal write failure".into(),
            ));
        }
        self.inner.complete(provider, reference).await
    }
}

#[tokio::test]
async fn failed_completion_is_not_success_and_retry_resumes_same_identity() {
    let f = fixture(Some("https://shared.example.com/hook"), false).await;
    let core = ProviderRegistrationCore::new(
        f.providers.clone(),
        f.providers.clone(),
        f.providers.clone(),
        Arc::new(FailCompletionOnce {
            inner: f.journal.clone(),
            fail: true.into(),
        }),
        f.registry.clone(),
        f.relations.clone(),
        "prod".into(),
        vec![],
        OutboundUrlGuard::strict(),
    );
    let command = request(&f, ProviderRegistrationMode::Gateway);
    assert!(core.register(command.clone()).await.is_err());
    let pending = f
        .journal
        .get(&f.provider, "stable-ref")
        .await
        .unwrap()
        .unwrap();
    assert!(!pending.completed);
    assert!(
        f.registry
            .try_get(&pending.bot_uuid)
            .await
            .unwrap()
            .is_some()
    );
    let recovered = core.register(command).await.unwrap();
    assert_eq!(pending.bot_uuid, recovered.record.bot_uuid);
    assert_eq!(pending.bot_token, recovered.record.bot_token);
    assert!(recovered.record.completed);
    assert_eq!(
        f.providers
            .list_bindings_by_provider(&f.provider)
            .await
            .unwrap()
            .len(),
        1
    );
}

#[tokio::test]
async fn changed_mode_or_webhook_and_rotated_token_conflict_without_mutation() {
    let f = fixture(Some("https://shared.example.com/hook"), false).await;
    let command = request(&f, ProviderRegistrationMode::Gateway);
    let first = f.core.register(command.clone()).await.unwrap();
    let mut change = command.clone();
    change.mode = ProviderRegistrationMode::Upstream;
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
async fn pending_retry_never_recreates_a_deleted_bot() {
    let f = fixture(None, false).await;
    let core = ProviderRegistrationCore::new(
        f.providers.clone(),
        f.providers.clone(),
        f.providers.clone(),
        Arc::new(FailCompletionOnce {
            inner: f.journal.clone(),
            fail: true.into(),
        }),
        f.registry.clone(),
        f.relations.clone(),
        "prod".into(),
        vec![],
        OutboundUrlGuard::strict(),
    );
    let command = request(&f, ProviderRegistrationMode::Upstream);
    assert!(core.register(command.clone()).await.is_err());
    let pending = f
        .journal
        .get(&f.provider, "stable-ref")
        .await
        .unwrap()
        .unwrap();
    assert!(!pending.completed);
    assert!(f.registry.soft_delete(&pending.bot_uuid).await);
    assert!(
        core.register(command).await.is_err(),
        "pending retry resurrected a deleted Bot"
    );
    assert!(
        f.registry
            .try_get(&pending.bot_uuid)
            .await
            .unwrap()
            .is_none()
    );
}
