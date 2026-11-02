//! Noop authority implementations for test
//! containers. These are failing stubs: the
//! authority surface is never silently
//! authorized; test consumers replace them with
//! real composites when authority behavior
//! matters (bot-authority plan Task 5).

use async_trait::async_trait;

use bcs_service_api::application::v1::bot_authority::BotAuthorityService;
use bcs_service_api::application::v1::error::ApplicationError;
use bcs_service_api::application::v1::identity::AuthenticatedCaller;
use bcs_service_api::core::bot_authority::BotAuthorityCoreService;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::types::bot_authority::{
    BotAccessRelation, BotOwnershipView, BotRoleQuery,
    ManagerEdit, ManagerListQuery, ManagerPage, ManagerSetChange,
    ManagerSync, OwnershipChange, OwnershipSync, TransferCommand, TransferKey,
    TransferListQuery, TransferOutcome, TransferPage,
};

/// A failing `BotAuthorityService` stub.
pub struct NoopBotAuthorityService;

#[async_trait]
impl BotAuthorityService for NoopBotAuthorityService {
    async fn role(
        &self,
        _caller: &AuthenticatedCaller,
        _user_id: &str,
        _bot_id: &str,
    ) -> Result<Option<BotAccessRelation>, ApplicationError> {
        Err(ApplicationError::internal(
            "bot authority service is not configured",
        ))
    }

    async fn roles_for(
        &self,
        _caller: &AuthenticatedCaller,
        _queries: &[BotRoleQuery],
    ) -> Result<Vec<Option<BotAccessRelation>>, ApplicationError> {
        Err(ApplicationError::internal(
            "bot authority service is not configured",
        ))
    }

    async fn ownership(
        &self,
        _caller: &AuthenticatedCaller,
        _bot_id: &str,
    ) -> Result<BotOwnershipView, ApplicationError> {
        Err(ApplicationError::internal(
            "bot authority service is not configured",
        ))
    }

    async fn managers(
        &self,
        _caller: &AuthenticatedCaller,
        _query: ManagerListQuery,
    ) -> Result<ManagerPage, ApplicationError> {
        Err(ApplicationError::internal(
            "bot authority service is not configured",
        ))
    }

    async fn transfer(
        &self,
        _caller: &AuthenticatedCaller,
        _key: &TransferKey,
    ) -> Result<
        Option<bcs_service_api::types::bot_authority::TransferView>,
        ApplicationError,
    > {
        Err(ApplicationError::internal(
            "bot authority service is not configured",
        ))
    }

    async fn transfers(
        &self,
        _caller: &AuthenticatedCaller,
        _query: TransferListQuery,
    ) -> Result<TransferPage, ApplicationError> {
        Err(ApplicationError::internal(
            "bot authority service is not configured",
        ))
    }

    async fn edit_manager(
        &self,
        _caller: &AuthenticatedCaller,
        _command: ManagerEdit,
    ) -> Result<ManagerSetChange, ApplicationError> {
        Err(ApplicationError::internal(
            "bot authority service is not configured",
        ))
    }

    async fn sync_managers(
        &self,
        _caller: &AuthenticatedCaller,
        _command: ManagerSync,
    ) -> Result<ManagerSetChange, ApplicationError> {
        Err(ApplicationError::internal(
            "bot authority service is not configured",
        ))
    }

    async fn sync_ownership(
        &self,
        _caller: &AuthenticatedCaller,
        _command: OwnershipSync,
    ) -> Result<OwnershipChange, ApplicationError> {
        Err(ApplicationError::internal(
            "bot authority service is not configured",
        ))
    }

    async fn change_transfer(
        &self,
        _caller: &AuthenticatedCaller,
        _command: TransferCommand,
    ) -> Result<TransferOutcome, ApplicationError> {
        Err(ApplicationError::internal(
            "bot authority service is not configured",
        ))
    }

}

/// A failing `BotAuthorityPort` stub.
pub struct NoopBotAuthorityPort;

#[async_trait]
impl BotAuthorityPort for NoopBotAuthorityPort {
    async fn role(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<Option<BotAccessRelation>> {
        Err(bcs_service_api::ServiceError::InternalError(
            "bot authority hook is not configured".to_string(),
        ))
    }

    async fn roles_for(
        &self,
        _queries: &[BotRoleQuery],
    ) -> bcs_service_api::ServiceResult<Vec<Option<BotAccessRelation>>> {
        Err(bcs_service_api::ServiceError::InternalError(
            "bot authority hook is not configured".to_string(),
        ))
    }

    async fn can_manage(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        Err(bcs_service_api::ServiceError::InternalError(
            "bot authority hook is not configured".to_string(),
        ))
    }

    async fn require_owner(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        Err(bcs_service_api::ServiceError::InternalError(
            "bot authority hook is not configured".to_string(),
        ))
    }

    async fn require_manage(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        Err(bcs_service_api::ServiceError::InternalError(
            "bot authority hook is not configured".to_string(),
        ))
    }
}


// ---------------------------------------------------------------------------
// Real-chain fixture for the adapter route suites (plan
// Task 5): Memory → Core/Hook → `BotAuthorityServiceImpl`
// with one configured trusted-app scope. The adapter test
// keeps only the bcs-service-api view of these objects, so
// the HTTP adapter's own manifest keeps its concrete-crate
// boundary.
// ---------------------------------------------------------------------------

use bcs_app_bot::authority::BotAuthorityServiceImpl;
use bcs_app_bot::authority_policy::{
    AuthorityAppScopeRule, AuthorityServicePolicy,
};
use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::memory_authority::{
    MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus,
};
use bcs_edge_permission::BotAuthorityCoreServiceImpl;
use bcs_service_api::application::v1::InviteCodeService;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{
    TransferStatus, human_actor_id,
};

pub struct InviteGateStub {
    pub allow: bool,
    pub human_checks: std::sync::Mutex<Vec<String>>,
}

#[async_trait]
impl InviteCodeService for InviteGateStub {
    async fn init_invite_codes(
        &self,
        _command: bcs_service_api::application::v1::InitInviteCodes,
    ) -> Result<bcs_service_api::application::v1::InitInviteCodesResult, ApplicationError>
    {
        Err(ApplicationError::internal("unused in authority fixtures"))
    }

    async fn claim_public_invite_code(
        &self,
        _command: bcs_service_api::application::v1::ClaimPublicInviteCode,
    ) -> Result<bcs_service_api::application::v1::ClaimPublicInviteCodeResult, ApplicationError>
    {
        Err(ApplicationError::internal("unused in authority fixtures"))
    }

    async fn bind_invite_code(
        &self,
        _command: bcs_service_api::application::v1::BindInviteCode,
    ) -> Result<bcs_service_api::application::v1::BindInviteCodeResult, ApplicationError>
    {
        Err(ApplicationError::internal("unused in authority fixtures"))
    }

    async fn get_my_invite_code_binding(
        &self,
        _command: bcs_service_api::application::v1::GetMyInviteCodeBinding,
    ) -> Result<bcs_service_api::application::v1::InviteCodeBindingView, ApplicationError>
    {
        Err(ApplicationError::internal("unused in authority fixtures"))
    }

    async fn ensure_invite_code_access(
        &self,
        caller: &AuthenticatedCaller,
    ) -> Result<(), ApplicationError> {
        if let Some(user) = caller.user.as_ref() {
            self.human_checks.lock().expect("gate lock").push(user.id.clone());
        }
        if self.allow {
            Ok(())
        } else {
            Err(ApplicationError::invite_code_required(
                "human callers must bind an invite code",
            ))
        }
    }
}

/// One shared memory authority instance wired real
/// Core → Hook → application service with a test
/// trusted-app scope (app name [`AUTHORITY_FIXTURE_APP_NAME`],
/// tenant `tenant-a`, all four actions; spec §3.1 simplified
/// rule shape). Its caller principals carry this shared name:
pub const AUTHORITY_FIXTURE_APP_NAME: &str = "Authority App";

pub struct AuthorityRoutesFixture {
    pub service: std::sync::Arc<dyn BotAuthorityService>,
    pub invite: std::sync::Arc<InviteGateStub>,
    pub bot_repo: std::sync::Arc<MemoryBotRepo>,
}

pub async fn authority_service_fixture(
    invite_allow: bool,
) -> AuthorityRoutesFixture {
    let repo = std::sync::Arc::new(MemoryBotRepo::new());
    let env = bcs_config::resolve_env_str();
    let core: std::sync::Arc<BotAuthorityCoreServiceImpl> =
        std::sync::Arc::new(BotAuthorityCoreServiceImpl::new(
            repo.clone() as std::sync::Arc<dyn BotAuthorityRepoPort>,
            env.clone(),
        ));
    let invite = std::sync::Arc::new(InviteGateStub {
        allow: invite_allow,
        human_checks: std::sync::Mutex::new(Vec::new()),
    });
    let rule = AuthorityAppScopeRule::from_config_strings(
        AUTHORITY_FIXTURE_APP_NAME,
        "tenant-a",
        &[
            "managers:sync".to_string(),
            "ownership:read".to_string(),
            "ownership:accept".to_string(),
            "ownership:reject".to_string(),
            "ownership:sync".to_string(),
        ],
    )
    .expect("valid fixture scope rule");
    let policy =
        AuthorityServicePolicy::new(&env, vec![rule]).expect("fixture policy");
    let service: std::sync::Arc<dyn BotAuthorityService> =
        std::sync::Arc::new(BotAuthorityServiceImpl::new(
            core.clone() as std::sync::Arc<dyn BotAuthorityPort>,
            core.clone() as std::sync::Arc<dyn BotAuthorityCoreService>,
            invite.clone(),
            true,
            policy,
            env,
        ));
    AuthorityRoutesFixture {
        service,
        invite,
        bot_repo: repo,
    }
}

/// Seed a live bot with an owner and optional managers
/// through the shared production memory authority state.
pub async fn seed_bot(
    repo: &MemoryBotRepo,
    bot_id: &str,
    owner_user: &str,
    manager_users: &[&str],
) {
    use bcs_bot_store::memory_authority::{
        MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus,
    };
    use bcs_service_api::port::repo::BotRepoPort;

    repo.register(
        bot_id.to_string(),
        bcs_domain::BotCapabilities::default(),
    )
    .await
    .expect("register fixture bot");
    // The authority Human registry is the shared bots map's
    // Human actor rows: seed them through the production
    // ensure path BEFORE the authority write-guard span.
    let mut users = vec![owner_user.to_string()];
    users.extend(manager_users.iter().map(|user| user.to_string()));
    for user in &users {
        repo.ensure_human_actor(user, user)
            .await
            .expect("ensure fixture Human actor row");
    }
    let env = bcs_config::resolve_env_str();
    let mut authority = repo.authority_state().write().await;
    authority
        .ownership_versions
        .insert((env.clone(), bot_id.to_string()), 1);
    let mut seed_edge = |user: &str, kind: MemoryAuthorityEdgeKind| {
        authority.edges.insert(
            (
                env.clone(),
                bot_id.to_string(),
                format!("human_{user}"),
                kind,
            ),
            (MemoryAuthorityEdgeStatus::Approved, 0),
        );
    };
    seed_edge(owner_user, MemoryAuthorityEdgeKind::Owner);
    for user in manager_users {
        seed_edge(user, MemoryAuthorityEdgeKind::Manager);
    }
}

/// Pre-provision live Human ACTOR rows (spec §5.2: Backend
/// must run collaborators through the trusted Human
/// provisioning flow BEFORE sync may add them). The
/// authority guards read the shared bots registry, so the
/// fixture drives the same production ensure path.
pub async fn seed_humans(repo: &MemoryBotRepo, users: &[&str]) {
    use bcs_service_api::port::repo::BotRepoPort;

    for user in users {
        repo.ensure_human_actor(user, user)
            .await
            .expect("ensure fixture Human actor row");
    }
}

/// Seed one pending transfer row.
pub async fn seed_transfer_row(
    repo: &MemoryBotRepo,
    bot_id: &str,
    from_owner: &str,
    target: &str,
) {
    use bcs_bot_store::memory_authority::MemoryTransferRow;

    let env = bcs_config::resolve_env_str();
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_millis() as u64;
    let mut authority = repo.authority_state().write().await;
    authority.transfers.insert(
        (
            env,
            bot_id.to_string(),
            human_actor_id(target),
        ),
        MemoryTransferRow {
            bot_id: bot_id.to_string(),
            target_owner_actor_id: human_actor_id(target),
            from_owner_actor_id: human_actor_id(from_owner),
            expected_ownership_version: 1,
            status: TransferStatus::Pending,
            requested_at: now_ms,
            expires_at: now_ms + 7 * 24 * 3600 * 1000,
            updated_at: now_ms,
            decided_at: None,
            decided_by_kind: None,
            decided_by_id: None,
            reason: None,
            result_ownership_version: None,
            bot_name_snapshot: None,
        },
    );
}

// ---------------------------------------------------------------------------
// Consumer-family cutover fixtures (bot-authority plan Task 6.2/6.3):
// a REAL authority chain (MemoryBotRepo → Core → Hook) plus granular
// edge seeding/revocation so consumer suites can drive the A(owner)
// / M(manager) / X(无关) matrix and the revocation scenarios through
// live hook reads — no authorization is mocked away.
// ---------------------------------------------------------------------------

/// Immutable authority-chain snapshot for one fixture.
pub struct AuthorityEdgeFixture {
    pub hook: std::sync::Arc<BotAuthorityCoreServiceImpl>,
    pub repo: std::sync::Arc<MemoryBotRepo>,
}

/// The authority hook over an EXISTING memory store: the
/// consumer fixture's SAME `MemoryBotRepo` must back the
/// hook — the authority `role` read liveness-checks the bot
/// row on that store.
pub fn authority_hook_for(
    repo: std::sync::Arc<MemoryBotRepo>,
) -> std::sync::Arc<BotAuthorityCoreServiceImpl> {
    let env = bcs_config::resolve_env_str();
    std::sync::Arc::new(BotAuthorityCoreServiceImpl::new(
        repo as std::sync::Arc<dyn BotAuthorityRepoPort>,
        env,
    ))
}

/// One MemoryBotRepo-backed authority chain. The SAME repo the
/// consumer fixture registers its Bots on must back this chain —
/// the authority `role` read liveness-checks the bot row here.
pub fn authority_edge_fixture() -> AuthorityEdgeFixture {
    let repo = std::sync::Arc::new(MemoryBotRepo::new());
    let env = bcs_config::resolve_env_str();
    let core: std::sync::Arc<BotAuthorityCoreServiceImpl> =
        std::sync::Arc::new(BotAuthorityCoreServiceImpl::new(
            repo.clone() as std::sync::Arc<dyn BotAuthorityRepoPort>,
            env,
        ));
    AuthorityEdgeFixture {
        hook: core.clone(),
        repo,
    }
}

async fn seed_edge(
    repo: &MemoryBotRepo,
    bot_id: &str,
    user: &str,
    kind: MemoryAuthorityEdgeKind,
) {
    use bcs_service_api::port::repo::BotRepoPort;

    let env = bcs_config::resolve_env_str();
    repo.register(
        bot_id.to_string(),
        bcs_domain::BotCapabilities::default(),
    )
    .await
    .expect("register authority fixture bot");
    // The Human registry is the shared bots map's actor rows
    // — ensure BEFORE the authority write guard is taken.
    repo.ensure_human_actor(user, user)
        .await
        .expect("ensure fixture Human actor row");
    {
        let mut authority = repo.authority_state().write().await;
        authority
            .ownership_versions
            .entry((env.clone(), bot_id.to_string()))
            .or_insert(1);
        // One Bot has exactly ONE current owner
        // (spec §3.2.1): a new owner edge REPLACES
        // the previous owner instead of corrupting
        // the owner state with a duplicate edge.
        if kind == MemoryAuthorityEdgeKind::Owner {
            let owner_keys: Vec<_> = authority
                .edges
                .keys()
                .filter(|key| {
                    key.0 == env
                        && key.1 == bot_id
                        && key.3 == MemoryAuthorityEdgeKind::Owner
                })
                .cloned()
                .collect();
            for key in owner_keys {
                authority.edges.remove(&key);
            }
        }
        authority.edges.insert(
            (env, bot_id.to_string(), format!("human_{user}"), kind),
            (MemoryAuthorityEdgeStatus::Approved, 0),
        );
    }
}

/// Grant the CURRENT owner edge (`human_<user>` on `bot_id`).
///
/// Replaces the historical `created_by` fixture fact: a consumer
/// suite that means "this Human owns this Bot" says it here.
pub async fn grant_owner_edge(repo: &MemoryBotRepo, bot_id: &str, user: &str) {
    seed_edge(repo, bot_id, user, MemoryAuthorityEdgeKind::Owner).await;
}

/// Grant the manager edge (`human_<user>` on `bot_id`).
pub async fn grant_manager_edge(repo: &MemoryBotRepo, bot_id: &str, user: &str) {
    seed_edge(repo, bot_id, user, MemoryAuthorityEdgeKind::Manager).await;
}

/// Synchronous owner-edge grant for sync fixture builders
/// (`Fixture::new().with_bot(...)` chains): seeds through a
/// throwaway current-thread runtime.
pub fn grant_owner_edge_sync(bot_repo: &MemoryBotRepo, bot_id: &str, user: &str) {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("clone authority seed runtime");
    runtime.block_on(async {
        let user = user.to_string();
        let bot_id = bot_id.to_string();
        let bot_id2 = bot_id.clone();
        seed_edge(bot_repo, &bot_id2, &user, MemoryAuthorityEdgeKind::Owner).await;
    });
}

/// Revoke one edge (owner or manager) for the A→B transfer and
/// manager-withdrawal scenarios (spec §3.3).
pub async fn revoke_authority_edge(
    repo: &MemoryBotRepo,
    bot_id: &str,
    user: &str,
    kind: MemoryAuthorityEdgeKind,
) {
    let env = bcs_config::resolve_env_str();
    {
        let mut authority = repo.authority_state().write().await;
        authority.edges.remove(&(
            env,
            bot_id.to_string(),
            format!("human_{user}"),
            kind,
        ));
    }
}

/// A REAL authority chain whose owner-edge seeds are
/// applied lazily: fixtures that build service
/// instances through SYNCHRONOUS builder chains (e.g.
/// `Fixture::new().with_bot(...)`) enqueue the owner
/// edge here, and the first authority read of the
/// service flushes the queue onto the SAME
/// `MemoryBotRepo`. Everything stays a live
/// `can_manage` read — only the fixture seeding is
/// deferred.
/// One deferred fixture seed on the lazy chain.
#[derive(Clone, Copy, PartialEq, Eq)]
enum LazySeedOp {
    Owner,
    Manager,
    /// Remove `human_<user>`'s OWNER edge.
    RevokeOwner,
    /// Remove `human_<user>`'s MANAGER edge.
    RevokeManager,
}

pub struct LazySeedAuthorityHook {
    /// The lazy `MemoryBotRepo` the pending owner
    /// seeds are flushed onto.
    repo: std::sync::Arc<MemoryBotRepo>,
    /// The REAL authority core over the lazy repo
    /// (the same user→actor mapping, role reads and
    /// require semantics as production).
    core: std::sync::Arc<BotAuthorityCoreServiceImpl>,
    pending:
        std::sync::Mutex<
            Vec<(String, String, LazySeedOp)>,
        >,
    registered: std::sync::Mutex<std::collections::HashSet<String>>,
    /// Serializes flushes: one test's seeds apply
    /// ATOMICALLY (register completes before any
    /// edge insert, and no other flush
    /// interleaves), so a concurrent reader never
    /// observes a half-applied seed batch.
    flush_lock: tokio::sync::Mutex<()>,
}

impl LazySeedAuthorityHook {
    /// Enqueue the CURRENT owner edge
    /// (`human_<user>` on `bot_id`); replaces any
    /// previous owner for that Bot (one owner per Bot,
    /// spec §3.2.1).
    pub fn seed_owner(&self, bot_id: &str, user_id: &str) {
        self.pending
            .lock()
            .expect("authority seed lock")
            .push((bot_id.to_string(), user_id.to_string(), LazySeedOp::Owner));
    }

    /// Enqueue the manager edge (`human_<user>`).
    pub fn seed_manager(&self, bot_id: &str, user_id: &str) {
        self.pending
            .lock()
            .expect("authority seed lock")
            .push(
                (
                    bot_id.to_string(),
                    user_id.to_string(),
                    LazySeedOp::Manager,
                ),
            );
    }

    /// Enqueue the owner-edge withdrawal (the
    /// A→B handover scenarios).
    pub fn revoke_owner(&self, bot_id: &str, user_id: &str) {
        self.pending
            .lock()
            .expect("authority seed lock")
            .push(
                (
                    bot_id.to_string(),
                    user_id.to_string(),
                    LazySeedOp::RevokeOwner,
                ),
            );
    }

    /// Enqueue the manager-edge withdrawal.
    pub fn revoke_manager(&self, bot_id: &str, user_id: &str) {
        self.pending
            .lock()
            .expect("authority seed lock")
            .push(
                (
                    bot_id.to_string(),
                    user_id.to_string(),
                    LazySeedOp::RevokeManager,
                ),
            );
    }

    async fn flush(&self) {
        use bcs_service_api::port::repo::BotRepoPort;

        let _guard = self.flush_lock.lock().await;
        let pending: Vec<(String, String, LazySeedOp)> =
            std::mem::take(&mut *self.pending_lock());
        for (bot_id, user_id, op) in pending {
            let is_new =
                !self.register_lock().contains(&bot_id);
            if is_new {
                self.repo
                    .register(
                        bot_id.clone(),
                        bcs_domain::BotCapabilities::default(),
                    )
                    .await
                    .expect("register lazy authority bot");
                self.register_lock().insert(bot_id.clone());
            }
            let env = bcs_config::resolve_env_str();
            // A seeded edge implies its Human: ensure the
            // actor row (the registry the guards read)
            // BEFORE the authority write guard is taken.
            // Revocations never resurrect a Human.
            if op != LazySeedOp::RevokeOwner
                && op != LazySeedOp::RevokeManager
            {
                self.repo
                    .ensure_human_actor(&user_id, &user_id)
                    .await
                    .expect("ensure lazy fixture Human actor row");
            }
            {
                let mut authority = self.repo.authority_state().write().await;
                if op != LazySeedOp::RevokeOwner
                    && op != LazySeedOp::RevokeManager
                {
                    authority
                        .ownership_versions
                        .entry((env.clone(), bot_id.clone()))
                        .or_insert(1);
                }
                let edge_kind = match op {
                    LazySeedOp::Owner => MemoryAuthorityEdgeKind::Owner,
                    LazySeedOp::Manager => {
                        MemoryAuthorityEdgeKind::Manager
                    }
                    LazySeedOp::RevokeOwner => {
                        MemoryAuthorityEdgeKind::Owner
                    }
                    LazySeedOp::RevokeManager => {
                        MemoryAuthorityEdgeKind::Manager
                    }
                };
                if op == LazySeedOp::Owner {
                    // One Bot has exactly ONE owner
                    // (spec §3.2.1): a new owner edge
                    // REPLACES the previous owner.
                    let owner_keys: Vec<_> = authority
                        .edges
                        .keys()
                        .filter(|key| {
                            key.0 == env
                                && key.1 == bot_id
                                && key.3
                                    == MemoryAuthorityEdgeKind::Owner
                        })
                        .cloned()
                        .collect();
                    for key in owner_keys {
                        authority.edges.remove(&key);
                    }
                }
                let key = (
                    env,
                    bot_id,
                    format!("human_{user_id}"),
                    edge_kind,
                );
                match op {
                    LazySeedOp::RevokeOwner
                    | LazySeedOp::RevokeManager => {
                        authority.edges.remove(&key);
                    }
                    LazySeedOp::Owner | LazySeedOp::Manager => {
                        authority.edges.insert(
                            key,
                            (MemoryAuthorityEdgeStatus::Approved, 0),
                        );
                    }
                }
            }
        }
    }

    async fn owned(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        self.flush().await;
        BotAuthorityCoreService::can_manage(&*self.core, user_id, bot_id).await
    }

    /// Probe helper (fixture diagnostics): dump the
    /// current authority keys and versions.
    pub async fn state_dump(&self) -> String {
        self.flush().await;
        let state = self.repo.authority_state().read().await;
        format!(
            "versions={:?} edges={:?}",
            state.ownership_versions,
            state
                .edges
                .iter()
                .map(|(k, (st, _))| (k.clone(), *st))
                .collect::<Vec<_>>()
        )
    }

    fn pending_lock(
        &self,
    ) -> std::sync::MutexGuard<'_, Vec<(String, String, LazySeedOp)>> {
        self.pending
            .lock()
            .expect("authority seed lock")
    }

    fn register_lock(
        &self,
    ) -> std::sync::MutexGuard<'_, std::collections::HashSet<String>> {
        self.registered
            .lock()
            .expect("authority registered lock")
    }
}

#[async_trait]
impl BotAuthorityPort for LazySeedAuthorityHook {
    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<Option<BotAccessRelation>> {
        self.flush().await;
        BotAuthorityCoreService::role(&*self.core, user_id, bot_id).await
    }

    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> bcs_service_api::ServiceResult<Vec<Option<BotAccessRelation>>> {
        self.flush().await;
        BotAuthorityCoreService::roles_for(&*self.core, queries).await
    }

    async fn can_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        self.owned(user_id, bot_id).await
    }

    async fn require_owner(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        self.flush().await;
        BotAuthorityCoreService::require_owner(&*self.core, user_id, bot_id).await
    }

    async fn require_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        self.flush().await;
        BotAuthorityCoreService::require_manage(&*self.core, user_id, bot_id).await
    }
}

/// One lazy-seed authority chain: the hook and the
/// queue container for sync fixture builders.
pub struct LazyAuthorityFixture {
    pub hook: std::sync::Arc<LazySeedAuthorityHook>,
}

impl LazyAuthorityFixture {
    /// The shared store the hook's seeds flush onto: fixtures
    /// whose application/BotCore side must read the SAME
    /// authority facts (retirement is a store-level command
    /// — its in-span operator re-verification only consults
    /// the store the edges actually live in) build their
    /// application registry over this repo.
    pub fn shared_repo(&self) -> std::sync::Arc<MemoryBotRepo> {
        self.hook.repo.clone()
    }
}

pub fn lazy_authority_chain() -> LazyAuthorityFixture {
    lazy_authority_chain_with_repo(std::sync::Arc::new(MemoryBotRepo::new()))
}

/// Same lazy seeding chain but over a CALLER-OWNED shared
/// store: fixtures whose application/BotCore side must read
/// the SAME authority facts the hook seeds (retirement is a
/// store-level command — its in-span operator re-verification
/// can only see edges when hook and store share one repo).
pub fn lazy_authority_chain_with_repo(
    repo: std::sync::Arc<MemoryBotRepo>,
) -> LazyAuthorityFixture {
    let env = bcs_config::resolve_env_str();
    let core: std::sync::Arc<BotAuthorityCoreServiceImpl> =
        std::sync::Arc::new(BotAuthorityCoreServiceImpl::new(
            repo.clone() as std::sync::Arc<dyn BotAuthorityRepoPort>,
            env,
        ),
    );
    LazyAuthorityFixture {
        hook: std::sync::Arc::new(LazySeedAuthorityHook {
            repo,
            core,
            pending: std::sync::Mutex::new(Vec::new()),
            registered: std::sync::Mutex::new(
                std::collections::HashSet::new(),
            ),
            flush_lock: tokio::sync::Mutex::new(()),
        }),
    }
}

/// The failing hook as a `Arc<dyn BotAuthorityPort>`.
pub fn failing_authority_hook() -> std::sync::Arc<dyn BotAuthorityPort> {
    std::sync::Arc::new(FailingAuthorityHook)
}

/// A failing authority hook (Important#3 pin):
/// every read returns a non-Forbidden storage
/// failure, so consumers can be pinned to
/// surface the failure as an internal error —
/// never a deny-as-false masquerade.
pub struct FailingAuthorityHook;

#[async_trait]
impl BotAuthorityPort for FailingAuthorityHook {
    async fn role(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<Option<BotAccessRelation>> {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }

    async fn roles_for(
        &self,
        _queries: &[BotRoleQuery],
    ) -> bcs_service_api::ServiceResult<
        Vec<Option<BotAccessRelation>>,
    > {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }

    async fn can_manage(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }

    async fn require_owner(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }

    async fn require_manage(
        &self,
        _user_id: &str,
        _bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        Err(bcs_service_api::ServiceError::InternalError(
            "authority storage unavailable".to_string(),
        ))
    }
}
