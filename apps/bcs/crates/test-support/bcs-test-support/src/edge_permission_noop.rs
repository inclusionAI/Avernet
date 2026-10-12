//! No-op implementations of the edge-permission repo/service traits — for DI in
//! tests/dev and as compile-check that the trait surface is object-safe.
use async_trait::async_trait;
use bcs_domain::edge_permission::{
    AdmissionReason, AdmissionResult, AuthzContext, EdgeGrant, FriendListEntry,
    PermissionProfile, PermissionRequest, RequestStatus,
};
use bcs_service_api::application::{
    AdmissionService, ConnectResult, ConnectService, ConnectStatus, RequestDirection, RequestsPage,
};
use bcs_service_api::core::error::ServiceResult;
use bcs_service_api::port::repo::{
    EdgeGrantRepoPort, PermissionProfileRepoPort, PermissionRequestRepoPort,
};
use serde_json::json;
use bcs_service_api::application::connect::{FriendEntriesPage, FriendListQuery};
use bcs_service_api::port::repo::edge_grant::FriendIdsPage;

pub struct NoopEdgeGrantRepo;
#[async_trait]
impl EdgeGrantRepoPort for NoopEdgeGrantRepo {
    async fn list_active_grants(&self, _: &str, _: &str, _: &str) -> Vec<EdgeGrant> { vec![] }
    async fn is_authorized(&self, _: &str, _: &str, _: &str) -> bool { false }
    async fn has_friend_edge(&self, _: &str, _: &str, _: &str) -> bool { false }
    async fn list_friends(&self, _: &str, _: &str) -> Vec<String> { vec![] }
    async fn list_friends_paginated(&self, _: &str, _: &str, _: FriendListQuery) -> ServiceResult<FriendIdsPage> {
        Ok(FriendIdsPage { items: vec![], total: 0 })
    }
    async fn insert_grant(&self, _: EdgeGrant, _: &bcs_service_api::types::BotOperationContext) -> ServiceResult<u64> { Ok(1) }
    async fn revoke_grant(&self, _: u64, _: &str, _: &bcs_service_api::types::BotOperationContext) -> ServiceResult<()> { Ok(()) }
    async fn get_default_profile_id(&self, _: &str, _: &str) -> Option<u64> { None }
}

pub struct NoopPermissionProfileRepo;
#[async_trait]
impl PermissionProfileRepoPort for NoopPermissionProfileRepo {
    async fn ensure_default_profile(&self, _: &str, _: &str) -> ServiceResult<u64> { Ok(1) }
    async fn get_active_default(&self, _: &str, _: &str) -> Option<PermissionProfile> { None }
    async fn upsert_revision(&self, _: PermissionProfile) -> ServiceResult<()> { Ok(()) }
}

pub struct NoopPermissionRequestRepo;
#[async_trait]
impl PermissionRequestRepoPort for NoopPermissionRequestRepo {
    async fn insert(&self, _: PermissionRequest, _: &bcs_service_api::types::BotOperationContext) -> ServiceResult<()> { Ok(()) }
    async fn get(&self, _: &str, _: &str) -> Option<PermissionRequest> { None }
    async fn list_inbox(&self, _: &str, _: &str, _: Option<RequestStatus>) -> Vec<PermissionRequest> { vec![] }
    async fn list_sent(&self, _: &str, _: &str, _: Option<RequestStatus>) -> Vec<PermissionRequest> { vec![] }
    async fn decide(&self, _: &str, _: &str, _: RequestStatus, _: &str, _: Option<&str>, _: &bcs_service_api::types::BotOperationContext) -> ServiceResult<()> { Ok(()) }
    async fn backfill_edge_id(&self, _: &str, _: &str, _: u64, _: &bcs_service_api::types::BotOperationContext) -> ServiceResult<()> { Ok(()) }
}

pub struct NoopConnectService;
#[async_trait]
impl ConnectService for NoopConnectService {
    async fn create_connect(&self, _: &str, _: &str, _: Option<String>, _: Option<bcs_service_api::RequestAuthHeaders>, _: bcs_service_api::types::BotOperationContext) -> ServiceResult<ConnectResult> {
        Ok(ConnectResult { request_ids: vec![], edge_ids: vec![], status: ConnectStatus::Pending, auto_accepted: false })
    }
    async fn approve(&self, _: &str, _: &str, _: Option<bcs_service_api::RequestAuthHeaders>, _: bcs_service_api::types::BotOperationContext) -> ServiceResult<Vec<u64>> { Ok(vec![]) }
    async fn reject(&self, _: &str, _: &str, _: Option<String>, _: bcs_service_api::types::BotOperationContext) -> ServiceResult<()> { Ok(()) }
    async fn cancel(&self, _: &str, _: &str, _: bcs_service_api::types::BotOperationContext) -> ServiceResult<()> { Ok(()) }
    async fn get_request(&self, _: &str) -> ServiceResult<PermissionRequest> {
        Err(ServiceError::FriendRequestNotFound("noop".to_string()))
    }
    async fn revoke_friend(&self, _: &str, _: &str, _: Option<bcs_service_api::RequestAuthHeaders>, _: bcs_service_api::types::BotOperationContext) -> ServiceResult<Vec<u64>> { Ok(vec![]) }
    async fn list_friends(&self, _: &str) -> ServiceResult<Vec<FriendListEntry>> { Ok(vec![]) }
    async fn list_friends_paginated(&self, _: &str, _: FriendListQuery) -> ServiceResult<FriendEntriesPage> {
        Ok(FriendEntriesPage { items: vec![], total: 0 })
    }
    async fn list_requests(
        &self,
        _actor: &str,
        _direction: RequestDirection,
        _status: Option<RequestStatus>,
        page: u32,
        page_size: u32,
    ) -> ServiceResult<RequestsPage> {
        Ok(RequestsPage { items: vec![], total: 0, page, page_size })
    }
}

pub struct NoopAdmissionService;
#[async_trait]
impl AdmissionService for NoopAdmissionService {
    async fn check_admission(&self, _: &str, _: &str, _: &str, _: &str) -> ServiceResult<AdmissionResult> {
        Ok(AdmissionResult { allowed: false, grants: vec![], reason_code: AdmissionReason::NoEdge, public_default: false })
    }
    async fn build_authz_context(&self, from: &str, to: &str, originator: &str, task_id: &str, run_id: &str, env: &str) -> ServiceResult<AuthzContext> {
        Ok(AuthzContext {
            task_id: task_id.into(), run_id: run_id.into(), from_id: from.into(), to_id: to.into(),
            env: env.into(), originator: originator.into(), context: json!({}), grants: vec![], signature: None,
        })
    }
}

// ============================================================================
// Plan Task 18: authority-lane Noops and recording doubles.
//
// Fail-closed contract (brief): an UNWIRED authority lane never grants —
// the Noop manager lanes answer Forbidden on every use case, the Noop
// transfer lane answers fail-closed errors, and the Noop team sync denies
// the credential lane (never an anonymous pass-through). Noop trait impls
// evolve explicitly with trait changes; default empty allowance methods are
// not an acceptable substitute.
// ============================================================================

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

use bcs_domain::{BotAccessRelation, OwnershipState, TransferAction};
use bcs_service_api::application::v1::{
    ApplicationError, BotAuthorityHook, BotManagerGrantResult, BotManagerPage, BotManagerService,
    BotManagerRevokeResult, BotOwnership, BotOwnershipTransferCreation,
    BotOwnershipTransferPage, BotOwnershipTransferReceipt, CreateBotOwnershipTransfer,
    DecideBotOwnershipTransfer, GetBotOwnership, GetBotOwnershipTransfer, GrantBotManager,
    ListBotManagers, ListBotOwnershipTransfers, OwnershipTransferService, RevokeBotManager,
    TeamManagerMemberRepair, TeamManagerSyncCommand, TeamManagerSyncService,
};
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, CreateOwnershipTransfer, CreateTransferResult, ListOwnershipTransfers,
    OwnershipTransfer, OwnershipTransferPage,
};
use bcs_service_api::types::team_manager_sync::TeamSyncReceipt;
use bcs_service_api::types::{
    AuditActor, BotManagerList, ManagerMutation, ManagerMutationResult,
};
use bcs_service_api::{ServiceError, ServiceResult as CoreServiceResult};

const NOT_WIRED_MANAGER: &str = "bot manager lane is not wired (Noop fail-closed double)";
const NOT_WIRED_TRANSFER: &str = "ownership transfer lane is not wired (Noop fail-closed double)";
const NOT_WIRED_TEAM: &str = "team manager sync lane is not wired (Noop fail-closed double)";

fn manager_denied() -> ApplicationError {
    ApplicationError::forbidden(NOT_WIRED_MANAGER.to_string())
}

fn transfer_denied() -> ApplicationError {
    ApplicationError::forbidden(NOT_WIRED_TRANSFER.to_string())
}

fn team_denied() -> ApplicationError {
    ApplicationError::invalid_manager_sync_source(NOT_WIRED_TEAM.to_string())
}

/// Noop [`BotManagerService`]: every use case answers Forbidden. An
/// assembly that forgot to wire the real facade must never observe an
/// empty list or a silent success.
pub struct NoopBotManagerService;

#[async_trait]
impl BotManagerService for NoopBotManagerService {
    async fn list_managers(&self, _command: ListBotManagers) -> Result<BotManagerPage, ApplicationError> {
        Err(manager_denied())
    }

    async fn grant_manager(
        &self,
        _command: GrantBotManager,
    ) -> Result<BotManagerGrantResult, ApplicationError> {
        Err(manager_denied())
    }

    async fn revoke_manager(
        &self,
        _command: RevokeBotManager,
    ) -> Result<BotManagerRevokeResult, ApplicationError> {
        Err(manager_denied())
    }
}

/// Noop [`OwnershipTransferService`]: fail-closed Forbidden on every read
/// and decision — no receipts, no ownership facts, no empty pages leak
/// from an unwired assembly.
pub struct NoopBotOwnershipTransferService;

#[async_trait]
impl OwnershipTransferService for NoopBotOwnershipTransferService {
    async fn get_ownership(&self, _query: GetBotOwnership) -> Result<BotOwnership, ApplicationError> {
        Err(transfer_denied())
    }

    async fn create_transfer(
        &self,
        _command: CreateBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferCreation, ApplicationError> {
        Err(transfer_denied())
    }

    async fn list_transfers(
        &self,
        _query: ListBotOwnershipTransfers,
    ) -> Result<BotOwnershipTransferPage, ApplicationError> {
        Err(transfer_denied())
    }

    async fn get_transfer(
        &self,
        _query: GetBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        Err(transfer_denied())
    }

    async fn accept_transfer(
        &self,
        _command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        Err(transfer_denied())
    }

    async fn reject_transfer(
        &self,
        _command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        Err(transfer_denied())
    }

    async fn cancel_transfer(
        &self,
        _command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        Err(transfer_denied())
    }
}

/// Noop [`TeamManagerSyncService`]: the credential never verifies and every
/// command is denied with the fixed `invalid_manager_sync_source` code —
/// the unmounted lane never admits an anonymous or out-of-scope sync.
pub struct NoopTeamManagerSyncService;

#[async_trait]
impl TeamManagerSyncService for NoopTeamManagerSyncService {
    async fn verify_service_credential(
        &self,
        _credential: &str,
    ) -> Result<
        bcs_service_api::types::team_manager_sync::VerifiedTeamManagerService,
        ApplicationError,
    > {
        Err(team_denied())
    }

    async fn sync(&self, _command: TeamManagerSyncCommand) -> Result<TeamSyncReceipt, ApplicationError> {
        Err(team_denied())
    }

    async fn repair_add_team_member(
        &self,
        _command: TeamManagerMemberRepair,
    ) -> Result<TeamSyncReceipt, ApplicationError> {
        Err(team_denied())
    }

    async fn repair_remove_team_member(
        &self,
        _command: TeamManagerMemberRepair,
    ) -> Result<TeamSyncReceipt, ApplicationError> {
        Err(team_denied())
    }
}

// ----------------------------------------------------------------------------
// Recording doubles (driver-side observation of the authority lane)
// ----------------------------------------------------------------------------

/// Recording [`BotAuthorityHook`]: driver-configurable grant/deny answers
/// with call observation — the shared conformance harnesses assert the
/// hook is CONSULTED (and consulted FIRST: with a denying hook the core
/// reads below must stay at zero).
#[derive(Default)]
pub struct RecordingBotAuthorityHook {
    grants: Mutex<bool>,
    calls: AtomicUsize,
    last_question: Mutex<(String, String)>,
}

impl RecordingBotAuthorityHook {
    /// A hook that answers `can_manage = true` / `require_owner = Ok`.
    pub fn allowing() -> Self {
        Self {
            grants: Mutex::new(true),
            calls: AtomicUsize::new(0),
            last_question: Mutex::new((String::new(), String::new())),
        }
    }

    /// A hook that answers `can_manage = false` / `require_owner = Forbidden`.
    pub fn denying() -> Self {
        Self {
            grants: Mutex::new(false),
            calls: AtomicUsize::new(0),
            last_question: Mutex::new((String::new(), String::new())),
        }
    }

    /// How many hook questions were asked through this double.
    pub fn calls(&self) -> usize {
        self.calls.load(Ordering::SeqCst)
    }

    /// The most recent `(user_id, bot_id)` question.
    pub fn last_question(&self) -> (String, String) {
        self.last_question.lock().expect("recording hook lock").clone()
    }

    fn record(&self, user_id: &str, bot_id: &str) -> bool {
        self.calls.fetch_add(1, Ordering::SeqCst);
        *self.last_question.lock().expect("recording hook lock") =
            (user_id.to_string(), bot_id.to_string());
        *self.grants.lock().expect("recording hook lock")
    }
}

#[async_trait]
impl BotAuthorityHook for RecordingBotAuthorityHook {
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
        Ok(self.record(user_id, bot_id))
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        if self.record(user_id, bot_id) {
            Ok(())
        } else {
            Err(ServiceError::Authority(
                bcs_service_api::types::error::AuthorityError::Forbidden(format!(
                    "user '{user_id}' is not the owner of bot '{bot_id}' (recording hook denial)"
                )),
            ))
        }
    }
}

/// Counting [`BotAuthorityCoreService`]: observes every method call and
/// fails closed after counting. With an inner core the call delegates
/// (the counter still increments); without one a counted call is an
/// `InternalError` — an unwired core must never fabricate authority facts.
pub struct CountingBotAuthorityCore {
    inner: Option<Arc<dyn BotAuthorityCoreService>>,
    reads: AtomicUsize,
    writes: AtomicUsize,
}

impl CountingBotAuthorityCore {
    /// A counting core with NO inner core: every call is counted and fails.
    pub fn fail_closed() -> Self {
        Self {
            inner: None,
            reads: AtomicUsize::new(0),
            writes: AtomicUsize::new(0),
        }
    }

    /// A counting wrapper delegating to a real core.
    pub fn over(inner: Arc<dyn BotAuthorityCoreService>) -> Self {
        Self {
            inner: Some(inner),
            reads: AtomicUsize::new(0),
            writes: AtomicUsize::new(0),
        }
    }

    /// Read-side calls (ownership/role/roles_for/list_managers/
    /// get_transfer/list_transfers) observed so far.
    pub fn reads(&self) -> usize {
        self.reads.load(Ordering::SeqCst)
    }

    /// Mutation-side calls (mutate_manager/sync_team/create_transfer/
    /// decide_transfer) observed so far.
    pub fn writes(&self) -> usize {
        self.writes.load(Ordering::SeqCst)
    }

    fn fail(&self, method: &str) -> ServiceError {
        ServiceError::InternalError(format!(
            "the counting authority core double was consulted on '{method}' without an inner core"
        ))
    }
}

#[async_trait]
impl BotAuthorityCoreService for CountingBotAuthorityCore {
    async fn ownership(&self, bot_id: &str) -> CoreServiceResult<OwnershipState> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        match &self.inner {
            Some(inner) => inner.ownership(bot_id).await,
            None => Err(self.fail("ownership")),
        }
    }

    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> CoreServiceResult<Option<BotAccessRelation>> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        match &self.inner {
            Some(inner) => inner.role(user_id, bot_id).await,
            None => Err(self.fail("role")),
        }
    }

    async fn roles_for(
        &self,
        pairs: &[(String, String)],
    ) -> CoreServiceResult<Vec<Option<BotAccessRelation>>> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        match &self.inner {
            Some(inner) => inner.roles_for(pairs).await,
            None => Err(self.fail("roles_for")),
        }
    }

    async fn mutate_manager(
        &self,
        actor: AuditActor,
        bot_id: &str,
        mutation: ManagerMutation,
    ) -> CoreServiceResult<ManagerMutationResult> {
        self.writes.fetch_add(1, Ordering::SeqCst);
        match &self.inner {
            Some(inner) => inner.mutate_manager(actor, bot_id, mutation).await,
            None => Err(self.fail("mutate_manager")),
        }
    }

    async fn list_managers(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> CoreServiceResult<BotManagerList> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        match &self.inner {
            Some(inner) => inner.list_managers(bot_id, offset, limit).await,
            None => Err(self.fail("list_managers")),
        }
    }

    async fn sync_team(
        &self,
        command: bcs_service_api::types::team_manager_sync::TeamManagerSync,
    ) -> CoreServiceResult<TeamSyncReceipt> {
        self.writes.fetch_add(1, Ordering::SeqCst);
        match &self.inner {
            Some(inner) => inner.sync_team(command).await,
            None => Err(self.fail("sync_team")),
        }
    }

    async fn create_transfer(
        &self,
        command: CreateOwnershipTransfer,
    ) -> CoreServiceResult<CreateTransferResult> {
        self.writes.fetch_add(1, Ordering::SeqCst);
        match &self.inner {
            Some(inner) => inner.create_transfer(command).await,
            None => Err(self.fail("create_transfer")),
        }
    }

    async fn decide_transfer(
        &self,
        actor_user_id: &str,
        transfer_id: &str,
        action: TransferAction,
    ) -> CoreServiceResult<CommittedTransferOutcome> {
        self.writes.fetch_add(1, Ordering::SeqCst);
        match &self.inner {
            Some(inner) => inner.decide_transfer(actor_user_id, transfer_id, action).await,
            None => Err(self.fail("decide_transfer")),
        }
    }

    async fn get_transfer(
        &self,
        viewer_user_id: &str,
        transfer_id: &str,
    ) -> CoreServiceResult<OwnershipTransfer> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        match &self.inner {
            Some(inner) => inner.get_transfer(viewer_user_id, transfer_id).await,
            None => Err(self.fail("get_transfer")),
        }
    }

    async fn list_transfers(
        &self,
        query: ListOwnershipTransfers,
    ) -> CoreServiceResult<OwnershipTransferPage> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        match &self.inner {
            Some(inner) => inner.list_transfers(query).await,
            None => Err(self.fail("list_transfers")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn traits_are_object_safe_and_noop_compiles() {
        let _repo: Box<dyn EdgeGrantRepoPort> = Box::new(NoopEdgeGrantRepo);
        let _prof: Box<dyn PermissionProfileRepoPort> = Box::new(NoopPermissionProfileRepo);
        let _req: Box<dyn PermissionRequestRepoPort> = Box::new(NoopPermissionRequestRepo);
        let _connect: Box<dyn ConnectService> = Box::new(NoopConnectService);
        let _admission: Box<dyn AdmissionService> = Box::new(NoopAdmissionService);
    }

    #[tokio::test]
    async fn noop_admission_check_returns_no_edge() {
        let svc = NoopAdmissionService;
        let r = svc.check_admission("human_1", "bot_1", "human_1", "prod").await.unwrap();
        assert!(!r.allowed);
        assert_eq!(r.reason_code, AdmissionReason::NoEdge);
    }

    #[tokio::test]
    async fn noop_build_authz_context_threads_params() {
        let svc = NoopAdmissionService;
        let ctx = svc
            .build_authz_context("h_1", "b_1", "h_1", "t_9", "r_7", "prod")
            .await
            .unwrap();
        assert_eq!(ctx.from_id, "h_1");
        assert_eq!(ctx.to_id, "b_1");
        assert_eq!(ctx.task_id, "t_9");
        assert_eq!(ctx.run_id, "r_7");
        assert_eq!(ctx.env, "prod");
        assert_eq!(ctx.originator, "h_1");
        assert!(ctx.grants.is_empty());
        assert!(ctx.signature.is_none());
    }

    #[tokio::test]
    async fn noop_list_requests_returns_empty_page_echoing_pagination() {
        let svc = NoopConnectService;
        let page = svc
            .list_requests("human_1", RequestDirection::Received, None, 2, 10)
            .await
            .unwrap();
        assert!(page.items.is_empty());
        assert_eq!(page.total, 0);
        assert_eq!(page.page, 2);
        assert_eq!(page.page_size, 10);
    }

    // ------------------------------------------------------------------
    // Plan Task 18: the authority-lane Noops fail CLOSED (never allow),
    // and the recording doubles observe the hook consultations exactly.
    // ------------------------------------------------------------------

    fn human() -> bcs_service_api::application::v1::AuthenticatedCaller {
        bcs_service_api::application::v1::AuthenticatedCaller {
            tenant: None,
            user: Some(bcs_service_api::application::v1::AuthenticatedUserIdentity {
                id: "staff-1".into(),
                username: "staff-1".into(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    #[tokio::test]
    async fn noop_bot_manager_lane_is_fully_denied() {
        use bcs_service_api::application::v1::{
            GrantBotManager, ListBotManagers, RevokeBotManager,
        };
        let svc = NoopBotManagerService;
        assert!(svc
            .list_managers(ListBotManagers {
                caller: human(),
                bot_id: "bot-1".into(),
                offset: 0,
                limit: 20,
            })
            .await
            .is_err());
        assert!(svc
            .grant_manager(GrantBotManager {
                caller: human(),
                bot_id: "bot-1".into(),
                user_id: "staff-2".into(),
            })
            .await
            .is_err());
        assert!(svc
            .revoke_manager(RevokeBotManager {
                caller: human(),
                bot_id: "bot-1".into(),
                user_id: "staff-2".into(),
            })
            .await
            .is_err());
    }

    #[tokio::test]
    async fn noop_ownership_transfer_lane_is_fully_denied() {
        use bcs_service_api::application::v1::{GetBotOwnership, ListBotOwnershipTransfers};
        let svc = NoopBotOwnershipTransferService;
        assert!(svc
            .get_ownership(GetBotOwnership {
                caller: human(),
                bot_id: "bot-1".into(),
            })
            .await
            .is_err());
        // A denied lane must ALSO deny the party-scoped read below.
        let listed = svc
            .list_transfers(ListBotOwnershipTransfers {
                caller: human(),
                direction: bcs_service_api::application::v1::TransferListDirection::Sent,
                status: None,
                offset: 0,
                limit: 20,
            })
            .await;
        assert!(listed.is_err(), "an unwired transfer lane never yields an empty page as if none existed");
    }

    #[tokio::test]
    async fn noop_team_sync_lane_has_no_anonymous_pass_through() {
        let svc = NoopTeamManagerSyncService;
        // Credential verification denies everything — no credential, real
        // or forged, verifies anonymously.
        let denied = svc
            .verify_service_credential("forged-credential-value")
            .await
            .expect_err("the Noop team lane denies every credential");
        assert!(matches!(
            denied,
            ApplicationError::ForbiddenCode { .. }
        ));
        assert!(svc.verify_service_credential(" ").await.is_err());
    }

    #[tokio::test]
    async fn recording_hook_records_questions_and_answers() {
        let allowing = RecordingBotAuthorityHook::allowing();
        assert!(allowing.can_manage("staff-1", "bot-1").await.unwrap());
        assert_eq!(allowing.calls(), 1);
        assert_eq!(allowing.last_question(), ("staff-1".into(), "bot-1".into()));

        let denying = RecordingBotAuthorityHook::denying();
        assert!(!denying.can_manage("staff-1", "bot-1").await.unwrap());
        assert!(denying.require_owner("staff-1", "bot-1").await.is_err());
        assert_eq!(denying.calls(), 2);
    }

    #[tokio::test]
    async fn counting_authority_core_counts_and_fails_closed() {
        let core = CountingBotAuthorityCore::fail_closed();
        assert!(core.role("staff-1", "bot-1").await.is_err());
        assert!(core.list_managers("bot-1", 0, 10).await.is_err());
        assert_eq!(core.reads(), 2);
        assert_eq!(core.writes(), 0);
        assert!(
            core.mutate_manager(
                AuditActor::Human {
                    user_id: "staff-1".into(),
                },
                "bot-1",
                ManagerMutation::RevokeNonTeam {
                    user_id: "staff-2".into(),
                },
            )
            .await
            .is_err()
        );
        assert_eq!(core.writes(), 1);
    }
}
