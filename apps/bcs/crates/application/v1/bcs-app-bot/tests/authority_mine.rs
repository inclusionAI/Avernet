//! Mine dynamic filtering (bot-authority plan Task
//! 4.5, spec §8.0): the REAL `BotServiceImpl::list_mine`
//! over the REAL `MemoryBotRepo` candidate store, the
//! REAL control-plane Core and the REAL
//! `BotCore::try_runtime_activity_for` — the only
//! test double is a counting/delegating seam that
//! counts the OBSERVATION batches (spec §8.0: 断言实际
//! 查询批次数) and injects the page-drift the
//! algorithm must survive.
//!
//! Pinned here:
//! - A-offline/B-reachable with limit 1 returns B/total 1
//!   (filter BEFORE pagination).
//! - Mixed WS + HTTP-Provider sources keep the existing
//!   `status=Online && (WS active || HTTP Provider
//!   routable)` semantics.
//! - The Human self entry: excluded whenever a
//!   reachability filter is specified; visible
//!   otherwise (Owner display tag, no Human→Human
//!   owner edge exists).
//! - >500 candidates: ONE WS observation + bounded
//!   ≤128-id provider batches (COUNTED), never N
//!   per-bot resolutions.
//! - Page-backfill drift retried ONCE; a page that
//!   still does not reproduce fails with the pinned
//!   public code `bot_list_changed`; storage errors
//!   PROPAGATE (no fake unreachable).
#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;

use bcs_app_bot::{BotServiceConfig, BotServiceImpl};
use bcs_bot::{BotControlPlaneCore, BotCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_service_api::application::v1::{
    ApplicationError, Bot, BotKind, BotReachability, BotService, ListMyBots,
};
use bcs_service_api::application::v1::AuthenticatedCaller;
use bcs_service_api::core::bot_control_plane::BotControlPlaneCoreService;
use bcs_service_api::{
    ActorStatus, BotCapabilities, BotControlPlaneCandidate,
    BotControlPlanePatch, BotControlPlaneRecord, BotControlPlaneView,
    BotRepoPort, ControllableCandidateQuery,
    ControlledBotCandidate, EnsureHumanResult, ProviderBotBinding,
    ProviderBotBindingRepoPort,
    ProviderBotDiscoveryRecord, ProviderBotDiscoverySelector, ProviderCredential,
    ProviderCredentialRepoPort, ProviderRecord, ProviderRepoPort,
    RegisteredBot, ServiceError, ServiceResult,
};
use bcs_test_support::NoopConnectService;

fn resolve_env() -> String {
    bcs_config::resolve_env_str()
}

fn mutation_id(tag: &str) -> bcs_service_api::types::bot_authority::AuthorityMutation {
    bcs_service_api::types::bot_authority::AuthorityMutation {
        mutation_id: format!("mine-fixtures-{tag}"),
        actor: bcs_service_api::types::bot_authority::AuthorityActor::Human {
            user_id: "mine-owner".to_string(),
        },
    }
}

// ---------------------------------------------------------------------------
// Counting observation seam: the WS observation count
// lives on the repo (one `list_connected`), the
// provider batch count on the bindings port (one
// `list_bindings_by_bot_uuids` per ≤128-id chunk).
// The control-plane Core gets the UNCOUNTED store;
// only the runtime observation goes through the
// wrappers, so page-backfill hydration cannot
// contaminate the counts.
// ---------------------------------------------------------------------------

struct CountingRepo {
    inner: Arc<MemoryBotRepo>,
    ws_observations: AtomicUsize,
    migration_sweep_calls: AtomicUsize,
    governance_reads: AtomicUsize,
}

impl CountingRepo {
    fn new(inner: Arc<MemoryBotRepo>) -> Arc<Self> {
        Arc::new(Self {
            inner,
            ws_observations: AtomicUsize::new(0),
            migration_sweep_calls: AtomicUsize::new(0),
            governance_reads: AtomicUsize::new(0),
        })
    }

    fn observations(&self) -> usize {
        self.ws_observations.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl BotRepoPort for CountingRepo {
    async fn register(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
    ) -> ServiceResult<()> {
        self.inner.register(bot_id, capabilities).await
    }

    async fn update_status(&self, bot_id: &str) -> bool {
        self.inner.update_status(bot_id).await
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.inner.get(bot_id).await
    }

    async fn get_agent_credentials(&self, bot_id: &str) -> Option<bcs_service_api::AgentCredentials> {
        self.inner.get_agent_credentials(bot_id).await
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.inner.list_active().await
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        self.inner.list_bots_by_creator(created_by).await
    }

    async fn list_uninitialized_bots_by_creator(
        &self,
        created_by: &str,
        limit: usize,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        self.migration_sweep_calls.fetch_add(1, Ordering::SeqCst);
        self.inner
            .list_uninitialized_bots_by_creator(created_by, limit)
            .await
    }

    async fn ownership_governance_state(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Option<bcs_service_api::types::bot_authority::BotOwnershipGovernance>> {
        self.governance_reads.fetch_add(1, Ordering::SeqCst);
        self.inner.ownership_governance_state(bot_id).await
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

    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities> {
        self.inner.load_from_storage(bot_id).await
    }

    async fn save_to_storage(
        &self,
        bot_id: &str,
        caps: &BotCapabilities,
    ) -> ServiceResult<()> {
        self.inner.save_to_storage(bot_id, caps).await
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        self.inner.update_visibility(bot_id, visibility).await
    }

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

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        self.inner.save_created_by(bot_id, created_by, overwrite).await
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

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
        self.inner.register_streaming_connection(bot_id).await
    }

    async fn reconnect_streaming(
        &self,
        existing_token: String,
    ) -> Result<(String, String), ()> {
        self.inner.reconnect_streaming(existing_token).await
    }

    async fn disconnect_streaming(&self, bot_id: &str) {
        self.inner.disconnect_streaming(bot_id).await
    }

    async fn is_connected(&self, bot_id: &str) -> bool {
        self.inner.is_connected(bot_id).await
    }

    /// THE observed seam: ONE call per runtime
    /// observation (spec §8.0 point 2), regardless of
    /// the candidate count.
    async fn list_connected(&self) -> Vec<String> {
        self.ws_observations.fetch_add(1, Ordering::SeqCst);
        self.inner.list_connected().await
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
        self.inner.send_request(bot_id, method, params, timeout_ms).await
    }

    async fn resolve_pending_request(
        &self,
        request_id: &str,
        response: serde_json::Value,
    ) {
        self.inner.resolve_pending_request(request_id, response).await
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.inner.has_been_onboarded(bot_id).await
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.inner.unregister(bot_id).await
    }

    async fn cleanup_expired(&self) {
        self.inner.cleanup_expired().await
    }

    async fn store_token_mapping(&self, token: String, bot_id: String) {
        self.inner.store_token_mapping(token, bot_id).await
    }

    async fn register_http_connection(&self, bot_id: String, token: String) -> String {
        self.inner.register_http_connection(bot_id, token).await
    }
}

struct CountingBindings {
    inner: Arc<MemoryProviderStore>,
    batch_calls: AtomicUsize,
    fail_reads: AtomicBool,
}

impl CountingBindings {
    fn new(inner: Arc<MemoryProviderStore>) -> Arc<Self> {
        Arc::new(Self {
            inner,
            batch_calls: AtomicUsize::new(0),
            fail_reads: AtomicBool::new(false),
        })
    }

    fn calls(&self) -> usize {
        self.batch_calls.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl ProviderBotBindingRepoPort for CountingBindings {
    async fn update_binding_webhook_url(
        &self,
        provider_id: &str,
        bot_uuid: &str,
        webhook_url: Option<&str>,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        self.inner
            .update_binding_webhook_url(provider_id, bot_uuid, webhook_url, updated_at)
            .await
    }

    async fn insert_binding(&self, binding: ProviderBotBinding) -> ServiceResult<()> {
        self.inner.insert_binding(binding).await
    }

    async fn get_binding_by_bot_uuid(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        self.inner.get_binding_by_bot_uuid(bot_uuid).await
    }

    async fn list_bindings_by_bot_uuids(
        &self,
        bot_uuids: &[String],
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        if self.fail_reads.swap(false, Ordering::SeqCst) {
            return Err(ServiceError::InternalError(
                "injected provider binding read failure".into(),
            ));
        }
        self.batch_calls.fetch_add(1, Ordering::SeqCst);
        self.inner.list_bindings_by_bot_uuids(bot_uuids).await
    }

    async fn get_binding_by_provider_ref(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        self.inner
            .get_binding_by_provider_ref(provider_id, provider_bot_ref)
            .await
    }

    async fn list_bindings_by_provider(
        &self,
        provider_id: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        self.inner.list_bindings_by_provider(provider_id).await
    }

    async fn list_discoverable_provider_bot_records(
        &self,
        selector: &ProviderBotDiscoverySelector,
    ) -> ServiceResult<Vec<ProviderBotDiscoveryRecord>> {
        self.inner
            .list_discoverable_provider_bot_records(selector)
            .await
    }

    async fn update_binding_disabled(
        &self,
        bot_uuid: &str,
        disabled: bool,
        updated_at: u64,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        self.inner
            .update_binding_disabled(bot_uuid, disabled, updated_at)
            .await
    }
}



// ---------------------------------------------------------------------------
// Page-drift seam over the control plane: drops
// page targets from `get_by_ids` (deletion drift)
// and/or revokes an owner edge before the SECOND
// candidate sampling (role drift). Everything else
// delegates to the REAL control-plane Core.
// ---------------------------------------------------------------------------

struct DriftControlPlane {
    inner: Arc<BotControlPlaneCore>,
    authority: Arc<MemoryBotRepo>,
    /// The first N `get_by_ids` calls drop
    /// `dropped_ids` from the result (like a delete
    /// racing the page backfill).
    drop_budget: Mutex<i64>,
    dropped_ids: Vec<String>,
    /// Before the Nth `list_controllable` call,
    /// revoke/restore the owner edge of the given bot
    /// (role drift, §8.0 point 4: every attempt makes
    /// TWO candidate reads — the sampling and the
    /// page's store-side role re-derivation).
    revoke_owner_before_call: Mutex<Option<(i64, String)>>,
    restore_owner_before_call: Mutex<Option<(i64, String)>>,
    candidates_read: AtomicUsize,
}

#[async_trait]
impl BotControlPlaneCoreService for DriftControlPlane {
    async fn get_record(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        self.inner.get_record(bot_id, env).await
    }

    async fn get(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        self.inner.get(bot_id, env).await
    }

    async fn get_by_ids(
        &self,
        bot_ids: &[String],
        env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        let dropping = {
            let mut budget = self.drop_budget.lock().expect("drop budget");
            let dropping = *budget > 0;
            if dropping {
                *budget -= 1;
            }
            dropping
        };
        let views = self.inner.get_by_ids(bot_ids, env).await?;
        if dropping {
            Ok(views
                .into_iter()
                .filter(|view| !self.dropped_ids.contains(&view.record.bot_id))
                .collect())
        } else {
            Ok(views)
        }
    }

    async fn list_candidates(
        &self,
        query: bcs_service_api::BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        self.inner.list_candidates(query).await
    }

    async fn list_by_creator(
        &self,
        query: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        self.inner.list_by_creator(query).await
    }

    async fn list_by_task_modes(
        &self,
        query: bcs_service_api::BotTaskModesQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        self.inner.list_by_task_modes(query).await
    }

    async fn patch(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        self.inner.patch(bot_id, env, patch).await
    }

    async fn list_controllable(
        &self,
        query: ControllableCandidateQuery,
    ) -> ServiceResult<Vec<ControlledBotCandidate>> {
        let read = self.candidates_read.fetch_add(1, Ordering::SeqCst) + 1;
        let revoke = self
            .revoke_owner_before_call
            .lock()
            .expect("revoke plan")
            .clone();
        let restore = self
            .restore_owner_before_call
            .lock()
            .expect("restore plan")
            .clone();
        if revoke.as_ref().is_some_and(|(at, _)| *at == read as i64)
            || restore
                .as_ref()
                .is_some_and(|(at, _)| *at == read as i64)
        {
            use bcs_bot_store::memory_authority::{
                MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus,
            };
            let env = resolve_env();
            let (bot_id, status) = if revoke
                .as_ref()
                .is_some_and(|(at, _)| *at == read as i64)
            {
                let (_, bot_id) = revoke.expect("revoke plan");
                (bot_id, MemoryAuthorityEdgeStatus::Revoked)
            } else {
                let (_, bot_id) = restore.expect("restore plan");
                (bot_id, MemoryAuthorityEdgeStatus::Approved)
            };
            self.authority
                .authority_state()
                .write()
                .await
                .edges
                .insert(
                    (
                        env,
                        bot_id,
                        format!("human_{}", query.user_id),
                        MemoryAuthorityEdgeKind::Owner,
                    ),
                    (status, 0),
                );
        }
        self.inner.list_controllable(query).await
    }
}

// ---------------------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------------------

struct Fixture {
    service: BotServiceImpl,
    repo: Arc<MemoryBotRepo>,
    counting_repo: Arc<CountingRepo>,
    bindings_probe: Arc<CountingBindings>,
    providers: Arc<MemoryProviderStore>,
    _temp: tempfile::TempDir,
}

fn human_caller(staff_no: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
        user: Some(
            bcs_service_api::application::v1::AuthenticatedUserIdentity {
                id: staff_no.to_string(),
                username: staff_no.to_string(),
                display_name: Some("Mine Owner".to_string()),
                full_name: None,
            },
        ),
        bot: None,
        app: None,
        access_key: None,
    }
}

impl Fixture {
    /// `drop: Some((n, ids))` arms the page-drift
    /// seam over the first n backfills;
    /// `revoke: Some((n, bot))` arms the role-drift
    /// seam ahead of the nth candidate sampling.
    async fn new_with_drift(
        drop: Option<(i64, Vec<String>)>,
        revoke: Option<(i64, String)>,
    ) -> Self {
        Self::new_with_role_flip(drop, revoke, None).await
    }

    /// `restore`: before the nth `list_controllable`
    /// call, re-approve the revoked edge (the drift
    /// heals INSIDE the request's retry window).
    async fn new_with_role_flip(
        drop: Option<(i64, Vec<String>)>,
        revoke: Option<(i64, String)>,
        restore: Option<(i64, String)>,
    ) -> Self {
        let temp = tempfile::tempdir().expect("temp dir");
        let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
        let providers = Arc::new(MemoryProviderStore::new());
        let plain_bindings: Arc<dyn ProviderBotBindingRepoPort> = providers.clone();
        let plain_provider_repo: Arc<dyn ProviderRepoPort> = providers.clone();
        let plain_credentials: Arc<dyn ProviderCredentialRepoPort> = providers.clone();

        let core_plane = Arc::new(BotControlPlaneCore::new(
            repo.clone(),
            plain_provider_repo.clone(),
            // The control plane's page hydration gets the
            // UNCOUNTED bindings store: the counts belong
            // to the runtime observation only.
            plain_bindings,
        ));
        let control_plane: Arc<dyn BotControlPlaneCoreService> =
            if drop.is_some() || revoke.is_some() || restore.is_some() {
                let (drop_budget, dropped_ids) = match drop {
                    Some((budget, dropped_ids)) => (budget, dropped_ids),
                    None => (0, Vec::new()),
                };
                Arc::new(DriftControlPlane {
                    inner: core_plane,
                    authority: repo.clone(),
                    drop_budget: Mutex::new(drop_budget),
                    dropped_ids,
                    revoke_owner_before_call: Mutex::new(revoke),
                    restore_owner_before_call: Mutex::new(restore),
                    candidates_read: AtomicUsize::new(0),
                })
            } else {
                core_plane
            };

        let counting_repo = CountingRepo::new(repo.clone());
        let bindings_probe = CountingBindings::new(providers.clone());
        let counted_bindings: Arc<dyn ProviderBotBindingRepoPort> =
            bindings_probe.clone();
        let registry = Arc::new(BotCore::with_provider_repos(
            counting_repo.clone() as Arc<dyn BotRepoPort>,
            plain_provider_repo,
            plain_credentials,
            counted_bindings,
        ));

        let env = resolve_env();
        let service = BotServiceImpl::new(
            control_plane,
            registry,
            Arc::new(bcs_test_support::NoopFriendCoreService),
            Arc::new(NoopConnectService),
            Arc::new(NoopCandidateSearch),
            BotServiceConfig { env: env.clone() },
        );
        Self {
            service,
            repo,
            counting_repo,
            bindings_probe,
            providers,
            _temp: temp,
        }
    }

    /// A runtime-registered, ownership-INITIALIZED bot
    /// (version 1, approved owner edge) — the
    /// authoritative candidate source of the new mine.
    async fn add_controlled_bot(
        &self,
        bot_id: &str,
        owner: &str,
        status: ActorStatus,
    ) {
        // A live Human ACTOR row on the shared registry (the
        // authority guards' Human existence source).
        self.repo
            .ensure_human_actor(owner, owner)
            .await
            .expect("ensure fixture Human actor row");
        self.repo
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    summary: Some(format!("summary-{bot_id}")),
                    domains: vec!["planning".to_string()],
                    scopes: vec!["workspace".to_string()],
                    ..Default::default()
                },
            )
            .await
            .expect("register fixture bot");
        self.repo
            .update_actor_status(bot_id, status)
            .await
            .expect("fixture status");
        self.repo
            .initialize_ownership(bot_id, owner, mutation_id(bot_id))
            .await
            .expect("initialize ownership");
    }

    async fn connect_ws(&self, bot_id: &str) {
        self.repo
            .register_streaming_connection(bot_id.to_string())
            .await
            .expect("connect fixture bot");
    }

    /// Seed an HTTP-Provider-routable bot: enabled
    /// provider downlink + enabled downlink credential +
    /// binding with a delivery endpoint (exactly the
    /// `resolve_delivery_target` predicates).
    async fn seed_provider_routable(
        &self,
        bot_id: &str,
        provider_id: &str,
        webhook: Option<&str>,
        credential_disabled: bool,
    ) {
        self.providers
            .insert_provider(ProviderRecord {
                provider_id: provider_id.to_string(),
                slug: None,
                name: provider_id.to_string(),
                config: serde_json::json!({
                    "downlink": {
                        "enabled": true,
                        "auth_mode": "static_bearer",
                        "webhook_url": webhook,
                        "protocol_version": "1.0"
                    }
                })
                .to_string(),
                created_by: "provider-admin".to_string(),
                owners: "[]".to_string(),
                disabled: false,
                created_at: 1,
                updated_at: 1,
            })
            .await
            .expect("insert provider");
        self.providers
            .insert_credential(ProviderCredential {
                provider_id: provider_id.to_string(),
                credential_kind: "downlink_bcs_to_provider".to_string(),
                secret_value: "downlink-secret".to_string(),
                disabled: credential_disabled,
                created_at: 1,
                updated_at: 1,
            })
            .await
            .expect("insert credential");
        self.providers
            .insert_binding(ProviderBotBinding {
                bot_uuid: bot_id.to_string(),
                provider_id: provider_id.to_string(),
                provider_bot_ref: format!("ref-{bot_id}"),
                webhook_url: webhook.map(str::to_string),
                disabled: false,
                created_at: 1,
                updated_at: 1,
            })
            .await
            .expect("insert binding");
    }

    /// No Human→Human owner edge may exist after any
    /// mine call (spec §8.0 point 3).
    async fn assert_no_human_owner_edge(&self, user_id: &str) {
        let human_actor = format!("human_{user_id}");
        let edge_to_human = self
            .repo
            .authority_state()
            .read()
            .await
            .edges
            .keys()
            .any(|key| key.1 == human_actor);
        assert!(
            !edge_to_human,
            "mine never creates a Human→Human owner edge"
        );
    }
}

struct NoopCandidateSearch;

#[async_trait]
impl bcs_service_api::BotCandidateSearchCoreService for NoopCandidateSearch {
    async fn search_candidates(
        &self,
        _query: bcs_service_api::BotCandidateSearchQuery,
    ) -> bcs_service_api::BotCandidateSearchCoreResult {
        bcs_service_api::BotCandidateSearchCoreResult {
            mode: bcs_service_api::BotCandidateSearchMode::EmptyQuery,
            hits: Vec::new(),
        }
    }

    async fn search_candidates_for_legacy(
        &self,
        _query: bcs_service_api::BotCandidateSearchQuery,
    ) -> bcs_service_api::LegacyBotCandidateSearchCoreResult {
        panic!("the V1 mine suite never searches legacy actor directories")
    }
}

async fn list_mine_at(
    service: &BotServiceImpl,
    reachability: Option<BotReachability>,
    kind: Option<BotKind>,
    offset: u64,
    limit: u64,
) -> Result<bcs_service_api::application::v1::Page<Bot>, ApplicationError> {
    service
        .list_mine(ListMyBots {
            caller: human_caller("mine-owner"),
            kind,
            name: None,
            status: None,
            reachability,
            offset,
            limit,
        })
        .await
}

async fn list_mine(
    service: &BotServiceImpl,
    reachability: Option<BotReachability>,
) -> Result<bcs_service_api::application::v1::Page<Bot>, ApplicationError> {
    list_mine_at(service, reachability, None, 0, 20).await
}

fn physicals(page: &bcs_service_api::application::v1::Page<Bot>) -> Vec<String> {
    page.items
        .iter()
        .filter_map(|bot| match bot {
            Bot::Physical(physical) => Some(physical.bot_id.clone()),
            Bot::Human(_) => None,
        })
        .collect()
}

fn humans(page: &bcs_service_api::application::v1::Page<Bot>) -> Vec<String> {
    page.items
        .iter()
        .filter_map(|bot| match bot {
            Bot::Human(human) => Some(human.bot_id.clone()),
            Bot::Physical(_) => None,
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

/// Spec §8.0: reachable/unreachable applies to ALL
/// candidates BEFORE total and pagination — A offline,
/// B reachable with limit 1 returns B with total 1.
#[tokio::test]
async fn mine_reachable_filter_precedes_pagination() {
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-a-offline", "mine-owner", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot("bot-b-reachable", "mine-owner", ActorStatus::Online)
        .await;
    fixture.connect_ws("bot-b-reachable").await;

    let page = list_mine(&fixture.service, Some(BotReachability::Reachable))
        .await
        .expect("reachable page");
    assert_eq!(page.total, 1, "the filter runs BEFORE pagination");
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].bot_id(), "bot-b-reachable");

    let offline = list_mine_at(
        &fixture.service,
        Some(BotReachability::Unreachable),
        None,
        0,
        20,
    )
    .await
    .expect("unreachable page");
    assert_eq!(offline.total, 1);
    assert_eq!(offline.items[0].bot_id(), "bot-a-offline");

    // No filter: total is the full controlled union
    // plus the Human self entry (kind None).
    let all = list_mine(&fixture.service, None).await.expect("all");
    assert_eq!(all.total, 3);
    let bots_only = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 20)
        .await
        .expect("bots only");
    assert_eq!(bots_only.total, 2);
}

/// Mixed WS + HTTP Provider sources keep the existing
/// `status=Online && (WS active || HTTP Provider
/// routable)` semantics; missing/broken provider
/// ingredients degrade to UNREACHABLE, not to errors.
#[tokio::test]
async fn mine_mixed_ws_and_provider_sources() {
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-ws", "mine-owner", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot("bot-provider-ok", "mine-owner", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot("bot-no-webhook", "mine-owner", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot(
            "bot-no-credential",
            "mine-owner",
            ActorStatus::Online,
        )
        .await;
    fixture.connect_ws("bot-ws").await;
    fixture
        .seed_provider_routable("bot-provider-ok", "provider-ok", Some("https://gateway.example/bcs"), false)
        .await;
    fixture
        .seed_provider_routable("bot-no-webhook", "provider-2", None, false)
        .await;
    fixture
        .seed_provider_routable(
            "bot-no-credential",
            "provider-3",
            Some("https://gateway.example/bcs"),
            true,
        )
        .await;

    let page = list_mine(&fixture.service, Some(BotReachability::Reachable))
        .await
        .expect("reachable page");
    let mut items = physicals(&page);
    items.sort();
    assert_eq!(
        items,
        vec!["bot-provider-ok".to_string(), "bot-ws".to_string()],
        "WS active and routable provider bots are reachable; \
         provider bots without an endpoint or credential are not"
    );
}

/// Human self entry semantics (spec §8.0 point 3):
/// visible without a reachability filter (the Owner
/// display tag comes from the candidate relation; no
/// Human→Human owner edge exists), excluded when a
/// reachability filter is specified (existing
/// behavior).
#[tokio::test]
async fn mine_self_entry_semantics() {
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-controlled", "mine-owner", ActorStatus::Online)
        .await;

    let all = list_mine(&fixture.service, None).await.expect("self visible");
    assert_eq!(humans(&all), vec!["human_mine-owner".to_string()]);
    assert_eq!(physicals(&all), vec!["bot-controlled".to_string()]);
    assert_eq!(all.total, 2);
    fixture.assert_no_human_owner_edge("mine-owner").await;

    let reachable = list_mine(&fixture.service, Some(BotReachability::Reachable))
        .await
        .expect("self excluded");
    assert!(
        humans(&reachable).is_empty(),
        "a specified reachability excludes the Human self entry"
    );
    assert_eq!(reachable.total, 0);
}

/// Spec §8 mine row: 逐项
/// `access_relation=owner|manager`; the Human self
/// entry keeps `self`. The labels come from the
/// SAME sampling as the page and follow the seeded
/// edges — including through an owner transfer.
#[tokio::test]
async fn mine_items_carry_their_sampled_access_relation() {
    use bcs_service_api::application::v1::Page;
    use bcs_test_support::{grant_manager_edge, grant_owner_edge};

    let relation_of = |page: &Page<Bot>, id: &str| -> String {
        page.items
            .iter()
            .find_map(|bot| {
                let relation = match bot {
                    Bot::Physical(physical) => {
                        physical.access_relation.clone()
                    }
                    Bot::Human(human) => {
                        human.access_relation.clone()
                    }
                };
                (bot.bot_id() == id).then_some(relation).flatten()
            })
            .expect("mine item relation")
    };

    // One owned bot plus one managed bot (the
    // caller's relation differs per seed).
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-owned", "mine-owner", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot("bot-managed", "someone-else", ActorStatus::Online)
        .await;
    grant_manager_edge(&fixture.repo, "bot-managed", "mine-owner").await;

    let page = list_mine(&fixture.service, None)
        .await
        .expect("mine page");
    assert_eq!(relation_of(&page, "bot-owned"), "owner");
    assert_eq!(relation_of(&page, "bot-managed"), "manager");
    assert_eq!(relation_of(&page, "human_mine-owner"), "self");

    // Post-transfer: the owner edge moves to bob,
    // and bob samples the SAME bot as `owner`.
    let transfer_fixture = Fixture::new_with_drift(None, None).await;
    transfer_fixture
        .add_controlled_bot("bot-transfer", "alice", ActorStatus::Online)
        .await;
    grant_owner_edge(&transfer_fixture.repo, "bot-transfer", "bob").await;
    let bob_page = transfer_fixture
        .service
        .list_mine(ListMyBots {
            caller: human_caller("bob"),
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("bob mine page");
    assert_eq!(
        relation_of(&bob_page, "bot-transfer"),
        "owner",
        "the new owner samples its own bot as owner"
    );
}


/// >500 candidates: the runtime observation is ONE WS
/// read plus bounded ≤128-id provider batches — COUNTED,
/// not just results (spec §8.0 有界块数).
#[tokio::test]
async fn mine_over_500_candidates_observes_once_with_bounded_batches() {
    let fixture = Fixture::new_with_drift(None, None).await;
    // One WS-connected bot among 501 candidates → the
    // provider resolution covers 500 ids → 4 batches
    // (128/128/128/80) with AUTHORITY_QUERY_CHUNK = 128.
    for index in 0..501 {
        fixture
            .add_controlled_bot(
                &format!("bot-volume-{index:03}"),
                "mine-owner",
                ActorStatus::Online,
            )
            .await;
        if index < 37 {
            fixture
                .connect_ws(&format!("bot-volume-{index:03}"))
                .await;
        }
    }
    assert_eq!(fixture.counting_repo.observations(), 0);
    assert_eq!(fixture.bindings_probe.calls(), 0);

    let page = list_mine_at(
        &fixture.service,
        None,
        Some(BotKind::Bot),
        0,
        20,
    )
    .await
    .expect("volume page");
    assert_eq!(page.total, 501);

    // ONE WS observation…
    assert_eq!(
        fixture.counting_repo.observations(),
        1,
        "exactly ONE WS connection-set observation"
    );
    // …and ceil(500/128) provider bindings batches (no
    // per-bot resolution, the batches are real).
    assert_eq!(
        fixture.bindings_probe.calls(),
        4,
        "500 non-WS ids in bounded batches of ≤128"
    );

    // A SECOND request re-observes (the sampling is not
    // cached across mine calls) — still one observation
    // PER request.
    let _ = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 20)
        .await
        .expect("again");
    assert_eq!(fixture.counting_repo.observations(), 2);
}

/// Storage/read failures PROPAGATE — a failing
/// provider resolution is never a
/// fake-unreachable success (spec §8.0
/// 存储错误返回错误).
#[tokio::test]
async fn mine_storage_error_propagates_not_unreachable() {
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-propagate", "mine-owner", ActorStatus::Online)
        .await;
    fixture.bindings_probe.fail_reads.store(true, Ordering::SeqCst);

    let error = list_mine(&fixture.service, None)
        .await
        .expect_err("storage error propagates");
    assert!(
        error.to_string().contains("injected provider binding read failure"),
        "the real read failure surfaces: {error}"
    );
}

/// Empty page beyond the total: no backfill, no drift,
/// items stay empty with the REAL total (never a fake
/// exact total).
#[tokio::test]
async fn mine_offset_beyond_total_returns_empty_items_with_real_total() {
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-single", "mine-owner", ActorStatus::Online)
        .await;
    let page = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 5, 20)
        .await
        .expect("empty page");
    assert!(page.items.is_empty());
    assert_eq!(page.total, 1);
}

/// Deletion drift discovered at the page backfill:
/// ONE whole retry reproduces the SAME page
/// composition — the recovered page is returned.
#[tokio::test]
async fn mine_page_drift_retries_once_and_returns_the_fresh_page() {
    let fixture = Fixture::new_with_drift(
        Some((1, vec!["bot-other".to_string()])),
        None,
    )
    .await;
    for bot_id in ["bot-page", "bot-other"] {
        fixture
            .add_controlled_bot(bot_id, "mine-owner", ActorStatus::Online)
            .await;
    }
    // Page (sorted ids): [bot-other]; its backfill
    // loses `bot-other` once -> drift; the ONE retry
    // re-samples, the page reproduces exactly and is
    // returned.
    let page = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 1)
        .await
        .expect("recovered page");
    assert_eq!(page.total, 2);
    assert_eq!(page.items[0].bot_id(), "bot-other");
}

/// Persistent drift: the retry STILL does not reproduce
/// the page — the response is the pinned 409
/// `bot_list_changed`, never a shifted page pretending
/// to be exact.
#[tokio::test]
async fn mine_persistent_drift_reports_bot_list_changed() {
    let fixture = Fixture::new_with_drift(
        Some((i64::MAX, vec!["bot-paged".to_string()])),
        None,
    )
    .await;
    for bot_id in ["bot-paged", "bot-spare"] {
        fixture
            .add_controlled_bot(bot_id, "mine-owner", ActorStatus::Online)
            .await;
    }

    let error = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 1)
        .await
        .expect_err("persistent drift is a 409");
    assert_eq!(
        error.code(),
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_LIST_CHANGED
    );
}

/// PURE role drift in the sampling→backfill window
/// (no deletion): the page's role re-derivation finds
/// `bot-revoked` revoked → drift → ONE whole retry;
/// the retry's page cannot reproduce the first
/// composition → `bot_list_changed`.
#[tokio::test]
async fn mine_role_drift_between_samplings_reports_bot_list_changed() {
    let fixture = Fixture::new_with_role_flip(
        None,
        Some((2, "bot-revoked".to_string())),
        None,
    )
    .await;
    for bot_id in ["bot-stable", "bot-revoked", "bot-replacement"] {
        fixture
            .add_controlled_bot(bot_id, "mine-owner", ActorStatus::Online)
            .await;
    }

    // Sorted ids: `bot-replacement` < `bot-revoked`
    // < `bot-stable`. The FIRST sampling's page
    // (offset 0, limit 2) is
    // [bot-replacement, bot-revoked]; its page-backfill
    // ROLE re-derivation (the second candidate read)
    // finds `bot-revoked` revoked — deletion-free
    // role drift → ONE whole retry. The retry's page
    // [bot-replacement, bot-stable] cannot reproduce
    // the first composition — `bot_list_changed`, never
    // the stale page.
    let error = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 2)
        .await
        .expect_err("role drift is a 409");
    assert_eq!(
        error.code(),
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_LIST_CHANGED
    );
}

/// Transient role drift: the page's role
/// re-derivation finds the edge revoked (drift), but
/// the edge is RESTORED before the retry's sampling —
/// the retry reproduces the SAME page composition and
/// serves it (delete-free recovery).
#[tokio::test]
async fn mine_transient_role_drift_stabilizes_and_serves_the_page() {
    let fixture = Fixture::new_with_role_flip(
        None,
        Some((2, "bot-revoked".to_string())),
        Some((3, "bot-revoked".to_string())),
    )
    .await;
    for bot_id in ["bot-stable", "bot-revoked", "bot-replacement"] {
        fixture
            .add_controlled_bot(bot_id, "mine-owner", ActorStatus::Online)
            .await;
    }

    // Attempt 1: page [bot-replacement, bot-revoked];
    // the role re-derivation sees `bot-revoked`
    // revoked → drift. Attempt 2: the edge is back,
    // the page reproduces exactly and is served.
    let page = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 2)
        .await
        .expect("recovered role drift");
    assert_eq!(page.total, 3);
    let items = physicals(&page);
    assert_eq!(
        items,
        vec![
            "bot-replacement".to_string(),
            "bot-revoked".to_string()
        ]
    );
}

/// Review finding (mine steady-state N+1): a
/// fully-migrated owner must not pay a per-Bot governance
/// rescan on every `/bots/mine` — the candidate list is
/// store-side prefiltered (`live physical Bot`, byte-exact
/// creator, `ownership_version = 0`, classify conflict
/// prefilter), so steady state costs ONE bounded candidate
/// lookup and ZERO governance reads.
#[tokio::test]
async fn steady_state_mine_does_not_rescan_initialized_bots() {
    let fixture = Fixture::new_with_drift(None, None).await;
    // 120 fully-initialized owned bots — past the per-request
    // attempt cap so a stale whole-list rescan cannot hide in
    // the counters.
    for i in 0..120 {
        fixture
            .add_controlled_bot(
                &format!("steady-{i:03}"),
                "mine-owner",
                ActorStatus::Online,
            )
            .await;
    }
    // Warm-up listing (settles ensure/token paths and drains
    // any one-time migration work before the measured call).
    list_mine_at(&fixture.service, None, None, 0, 1)
        .await
        .expect("warm-up listing");
    let sweeps_before = fixture
        .counting_repo
        .migration_sweep_calls
        .load(Ordering::SeqCst);
    let reads_before = fixture
        .counting_repo
        .governance_reads
        .load(Ordering::SeqCst);

    let page = list_mine_at(&fixture.service, None, None, 0, 1)
        .await
        .expect("steady-state page");
    assert_eq!(page.total, 121, "120 bots + Human self");
    assert_eq!(
        fixture
            .counting_repo
            .migration_sweep_calls
            .load(Ordering::SeqCst)
            - sweeps_before,
        1,
        "one bounded candidate lookup per listing"
    );
    assert_eq!(
        fixture.counting_repo.governance_reads.load(Ordering::SeqCst) - reads_before,
        0,
        "no per-Bot governance rescan once everything is migrated"
    );
}

