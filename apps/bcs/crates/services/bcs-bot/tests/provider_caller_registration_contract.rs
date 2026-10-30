//! P1.3 registration-caller contract (spec §"注册失败边界"): the initial
//! caller snapshot lands with the Bot/binding registration; a failed
//! post-commit snapshot sync reports `RegistrationReconciliationRequired`
//! — the registration stays durable, nothing is retried/deleted/replayed,
//! and no credential rides the error. A composition without a caller-config
//! store never silently skips the snapshot.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot::core::{BotCore, ProviderCore};
use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_domain::caller_identity::{
    CallerIdentityConfig, CallerIdentitySnapshot, CallerMinimumIsolation,
};
use bcs_service_api::port::repo::bot_control_plane::{
    BotCandidateReadQuery, BotCallerConfiguration, BotControlPlaneOwnedQuery,
    BotControlPlanePatch, BotControlPlaneRecord, BotControlPlaneRepoPort, BotSearchCandidateQuery,
    BotTaskModesQuery, CallerConfigurationSync,
};
use bcs_service_api::{
    BotRegistryCoreService, ProviderAuthMode, ProviderBotBindingRepoPort,
    ProviderBotConnectionMode, ProviderBotCoreService, ProviderCoreService,
    ProviderCredentialRepoPort, ProviderRepoPort, RegisterProviderBotParams, ServiceError,
    ServiceResult,
};

/// Behavior-selectable caller-config store: records every sync.
struct CallerStore {
    /// Err variant: every sync fails with the wrapped error.
    fail: bool,
    /// Ok(None): the target row reads as missing.
    missing: bool,
    calls: std::sync::Mutex<Vec<(String, String)>>,
}

impl Default for CallerStore {
    fn default() -> Self {
        Self { fail: false, missing: false, calls: Default::default() }
    }
}

#[async_trait]
impl BotControlPlaneRepoPort for CallerStore {
    async fn get_control_plane(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        Ok(None)
    }
    async fn list_control_plane_candidates(
        &self,
        _query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<bcs_service_api::types::BotCandidateReadRecord>, u64)> {
        Ok((Vec::new(), 0))
    }
    async fn search_control_plane_candidates(
        &self,
        _query: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<bcs_service_api::types::BotCandidateReadRecord>, u64)> {
        Ok((Vec::new(), 0))
    }
    async fn list_control_plane_by_creator(
        &self,
        _query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        Ok(Vec::new())
    }
    async fn list_control_plane_by_task_modes(
        &self,
        _query: BotTaskModesQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        Ok(Vec::new())
    }
    async fn patch_control_plane(
        &self,
        _bot_id: &str,
        _env: &str,
        _patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        Ok(None)
    }
    async fn get_caller_configuration(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        Ok(None)
    }
    async fn sync_caller_configuration(
        &self,
        bot_id: &str,
        env: &str,
        incoming: CallerIdentitySnapshot,
    ) -> ServiceResult<Option<CallerConfigurationSync>> {
        self.calls.lock().unwrap().push((bot_id.to_string(), env.to_string()));
        if self.fail {
            return Err(ServiceError::InternalError(
                "caller configuration storage failed".to_string(),
            ));
        }
        if self.missing {
            return Ok(None);
        }
        Ok(Some(CallerConfigurationSync::applied(incoming)))
    }
}

struct Harness {
    core: ProviderCore,
    registry: Arc<BotCore>,
    #[allow(dead_code)]
    temp_dir: tempfile::TempDir,
}

fn harness(store: Option<Arc<CallerStore>>) -> Harness {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let mut core = ProviderCore::new_with_webhook_url_guard(
        provider_repo,
        provider_credentials,
        provider_bindings,
        registry.clone(),
        bcs_route_security::OutboundUrlGuard::strict(),
    );
    if let Some(store) = store {
        core = core.with_caller_config_store(store as Arc<dyn BotControlPlaneRepoPort>);
    }
    Harness { core, registry, temp_dir }
}

fn register_params(initial_caller: Option<CallerIdentitySnapshot>) -> RegisterProviderBotParams {
    RegisterProviderBotParams {
        webhook_url: None,
        bot_name: "Caller Bot".to_string(),
        summary: None,
        owners: vec!["S12345".to_string()],
        provider_bot_ref: "caller-ref-1".to_string(),
        domains: Vec::new(),
        skills: Vec::new(),
        scopes: Vec::new(),
        bot_uuid: None,
        connection_mode: ProviderBotConnectionMode::Gateway,
        // A static-bearer provider needs a downlink webhook; supply one.
        initial_caller,
    }
}

fn snapshot() -> CallerIdentitySnapshot {
    CallerIdentitySnapshot {
        source_revision: 7,
        caller_identity: CallerIdentityConfig {
            enabled: true,
            minimum_isolation: CallerMinimumIsolation::CallerContainer,
        },
    }
}

async fn seed_provider(core: &ProviderCore) -> (String, String) {
    let registered = core
        .register_provider(
            "provider-alpha".to_string(),
            Some("https://provider.example/hook".to_string()),
            ProviderAuthMode::StaticBearer,
            "admin-1".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider");
    (registered.provider.provider_id, registered.provider_admin_token)
}

#[tokio::test]
async fn initial_snapshot_lands_with_the_registration() {
    let store = Arc::new(CallerStore::default());
    let harness = harness(Some(store.clone()));
    let (provider_id, admin_token) = seed_provider(&harness.core).await;

    let (binding, token) = harness
        .core
        .register_provider_bot_with_bot_uuid(
            &provider_id,
            admin_token.as_str(),
            register_params(Some(snapshot())),
        )
        .await
        .expect("registration carries the caller snapshot");
    assert_eq!(
        binding.provider_bot_ref, "caller-ref-1",
        "the binding is committed alongside the snapshot"
    );
    let Some(_runtime_token) = token else {
        // Static-bearer providers return the token; missing it here is not
        // part of this contract, but a panic keeps the fixture honest.
        panic!("static bearer registration must mint the runtime token");
    };
    assert_eq!(store.calls.lock().unwrap().len(), 1, "exactly one snapshot sync");
    let calls = store.calls.lock().unwrap();
    assert_eq!(calls[0].0, binding.bot_uuid, "the snapshot targets the registered Bot");
    assert!(!calls[0].1.is_empty(), "the sync carries an environment scope");
}

#[tokio::test]
async fn snapshot_sync_failure_keeps_the_registration_and_reports_reconciliation() {
    let store = Arc::new(CallerStore { fail: true, missing: false, calls: Default::default() });
    let harness = harness(Some(store.clone()));
    let (provider_id, admin_token) = seed_provider(&harness.core).await;

    let error = harness
        .core
        .register_provider_bot_with_bot_uuid(
            &provider_id,
            admin_token.as_str(),
            register_params(Some(snapshot())),
        )
        .await
        .expect_err("failed post-commit sync reports reconciliation required");
    assert!(
        matches!(error, ServiceError::RegistrationReconciliationRequired),
        "closed reconciliation error, got {error:?}"
    );
    // The registration stays committed and never disappears: the Bot is
    // present and the binding persists (idempotent replay found it).
    let binding = harness
        .core
        .register_provider_bot_with_bot_uuid(
            &provider_id,
            admin_token.as_str(),
            register_params(None),
        )
        .await
        .expect("resubmission without a snapshot replays the existing binding")
        .0;
    assert_eq!(binding.provider_bot_ref, "caller-ref-1");
}

#[tokio::test]
async fn a_missing_snapshot_target_also_requires_reconciliation() {
    let store = Arc::new(CallerStore { fail: false, missing: true, calls: Default::default() });
    let harness = harness(Some(store.clone()));
    let (provider_id, admin_token) = seed_provider(&harness.core).await;
    let error = harness
        .core
        .register_provider_bot_with_bot_uuid(
            &provider_id,
            admin_token.as_str(),
            register_params(Some(snapshot())),
        )
        .await
        .expect_err("a vanished target never fakes success or a skip");
    assert!(matches!(error, ServiceError::RegistrationReconciliationRequired));
}

#[tokio::test]
async fn a_composition_without_a_caller_store_fails_closed_instead_of_skipping() {
    let harness = harness(None);
    let (provider_id, admin_token) = seed_provider(&harness.core).await;
    let error = harness
        .core
        .register_provider_bot_with_bot_uuid(
            &provider_id,
            admin_token.as_str(),
            register_params(Some(snapshot())),
        )
        .await
        .expect_err("memory-shaped composition refuses a caller registration");
    assert!(matches!(error, ServiceError::RegistrationReconciliationRequired));

    // The same composition keeps registering ordinary bots fine.
    harness
        .core
        .register_provider_bot_with_bot_uuid(
            &provider_id,
            admin_token.as_str(),
            register_params(None),
        )
        .await
        .expect("registration without a caller snapshot is unaffected");
}