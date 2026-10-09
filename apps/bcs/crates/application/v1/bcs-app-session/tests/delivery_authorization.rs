//! Outbound continuous-authorization conformance (plan Task 15, spec §14):
//! the shared `delivery_authorization_service_contract_tests` suite runs
//! twice — once over the REAL Memory authority stack (MemoryBotRepo behind
//! the production `BotAuthorityCoreServiceImpl`, real manager-revoke lane,
//! real session-membership removal lane) and once over the application-level
//! map authority double — both with real Memory Group/Session stores under
//! the shared counting wrappers, so the real query-count assertions bind.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;

use bcs_app_session::DeliveryAuthorizationServiceImpl;
use bcs_bot_store::MemoryBotRepo;
use bcs_domain::{
    AuditActor, BotAccessRelation, ManagerMutation, ManagerMutationResult, MessageViewScope,
    OwnershipState, ParticipantRole, TransferAction,
};
use bcs_edge_permission::BotAuthorityCoreServiceImpl;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::application::v1::DeliveryResourceKind;
use bcs_service_api::core::{BotAuthorityCoreService, GroupCoreService};
use bcs_service_api::port::repo::bot_authority::human_actor_id;
use bcs_service_api::port::repo::{
    BotAuthorityRepoPort, BotRepoPort, NewSessionParams, SessionRepoPort,
};
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, CreateOwnershipTransfer, CreateTransferResult,
    ListOwnershipTransfers, OwnershipTransfer, OwnershipTransferPage,
};
use bcs_service_api::types::team_manager_sync::{TeamManagerSync, TeamSyncReceipt};
use bcs_service_api::types::{BotManagerList, ServiceError, ServiceResult};
use bcs_service_api::types::Participant;
use bcs_service_api::Group;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::contract::application::delivery_authorization::{
    delivery_authorization_service_contract_tests, CountingAuthorityCore, CountingGroupCore,
    CountingSessionRepo, DeliveryAuthorizationDriver, DeliveryAuthorizationHarness,
};

const ENV: &str = "delivery-contract";
const GROUP_ID: &str = "group-delcon-0001";
const SESSION_ID: &str = "group-delcon-0001:9f3ad2c8";
const MANAGER_USER: &str = "delau_manager";
const VIEW_BOT_X: &str = "bot_delcon_view_x";
const VIEW_BOT_Y: &str = "bot_delcon_view_y";
const PARTICIPANT_USER: &str = "delau_participant";
const OTHER_ACTOR: &str = "human_delau_other";
const OWNER_USER: &str = "delau_owner";

fn role_participants() -> Vec<Participant> {
    let mut manager_self =
        Participant::human(human_actor_id(MANAGER_USER), ParticipantRole::Consultant);
    manager_self.message_view_scope = MessageViewScope::Full;
    let mut participant_self =
        Participant::human(human_actor_id(PARTICIPANT_USER), ParticipantRole::Consultant);
    participant_self.message_view_scope = MessageViewScope::Participant;
    vec![
        Participant::bot(VIEW_BOT_X, ParticipantRole::Driver),
        Participant::bot(VIEW_BOT_Y, ParticipantRole::Consultant),
        manager_self,
        participant_self,
    ]
}

/// A driver's resource side: fresh Memory Group/Session stores seeded with
/// the fixture participants, wrapped in the shared counting wrappers the
/// service reads through. Seeding goes through the RAW stores, never through
/// the wrappers, so counters measure only the service's evidence reads.
struct ResourceStack {
    raw_session_repo: Arc<MemorySessionRepo>,
    counting_authority: Arc<CountingAuthorityCore>,
    counting_group: Arc<CountingGroupCore>,
    counting_session: Arc<CountingSessionRepo>,
}

impl ResourceStack {
    async fn seed(authority_core: Arc<dyn BotAuthorityCoreService>) -> Self {
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let group_core = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let mut group = Group::new(
            GROUP_ID.to_string(),
            VIEW_BOT_X.to_string(),
            role_participants(),
        );
        group.visibility = "public".into();
        GroupCoreService::upsert(&*group_core, group)
            .await
            .expect("seed fixture group");

        let raw_session_repo = Arc::new(MemorySessionRepo::new());
        raw_session_repo
            .create(
                GROUP_ID,
                NewSessionParams {
                    id: Some(SESSION_ID.to_string()),
                    participants: role_participants(),
                    ..Default::default()
                },
            )
            .await
            .expect("seed fixture session");

        let counting_group = Arc::new(CountingGroupCore::new(group_core.clone()));
        let counting_session = Arc::new(CountingSessionRepo::new(raw_session_repo.clone()));
        Self {
            raw_session_repo,
            counting_authority: Arc::new(CountingAuthorityCore::new(authority_core)),
            counting_group,
            counting_session,
        }
    }
}

// ── driver 1: the REAL Memory authority stack ───────────────────────────

struct RealMemoryDriver {
    resources: ResourceStack,
    service: Arc<DeliveryAuthorizationServiceImpl>,
    bot_repo: Arc<MemoryBotRepo>,
}

impl RealMemoryDriver {
    async fn build() -> Self {
        let bot_repo = Arc::new(MemoryBotRepo::new());
        let authority_core: Arc<dyn BotAuthorityCoreService> =
            Arc::new(BotAuthorityCoreServiceImpl::new(bot_repo.clone()));

        // Real registration lanes: humans first, then the initialized owner
        // slots and the revocable direct manager sources.
        bot_repo
            .ensure_human_actor(MANAGER_USER, "Delivery Manager")
            .await
            .expect("register manager user");
        bot_repo
            .ensure_human_actor(PARTICIPANT_USER, "Delivery Participant")
            .await
            .expect("register participant user");
        bot_repo
            .ensure_human_actor(OWNER_USER, "Delivery Bot Owner")
            .await
            .expect("register owner user");
        bot_repo
            .seed_authority_owned(VIEW_BOT_X, OWNER_USER)
            .await
            .expect("initialize view bot X");
        bot_repo
            .seed_authority_owned(VIEW_BOT_Y, OWNER_USER)
            .await
            .expect("initialize view bot Y");
        bot_repo
            .seed_authority_manager_source(VIEW_BOT_X, MANAGER_USER, "direct", "manual")
            .await
            .expect("seed manager authority over X");
        bot_repo
            .seed_authority_manager_source(VIEW_BOT_Y, MANAGER_USER, "direct", "manual")
            .await
            .expect("seed manager authority over Y");

        let resources = ResourceStack::seed(authority_core).await;
        let service = Arc::new(DeliveryAuthorizationServiceImpl::new(
            resources.counting_authority.clone(),
            resources.counting_group.clone(),
            resources.counting_session.clone(),
        ));
        Self {
            resources,
            service,
            bot_repo,
        }
    }

    fn harness(self) -> DeliveryAuthorizationHarness {
        let driver = Arc::new(self);
        DeliveryAuthorizationHarness {
            service: driver.service.clone(),
            driver,
        }
    }
}

#[async_trait]
impl DeliveryAuthorizationDriver for RealMemoryDriver {
    fn env(&self) -> &'static str {
        ENV
    }
    fn group_id(&self) -> &'static str {
        GROUP_ID
    }
    fn session_id(&self) -> &'static str {
        SESSION_ID
    }
    fn manager_user(&self) -> &'static str {
        MANAGER_USER
    }
    fn view_bot_x(&self) -> &'static str {
        VIEW_BOT_X
    }
    fn view_bot_y(&self) -> &'static str {
        VIEW_BOT_Y
    }
    fn participant_user(&self) -> &'static str {
        PARTICIPANT_USER
    }
    fn other_actor(&self) -> &'static str {
        OTHER_ACTOR
    }

    fn authority_batch_calls(&self) -> usize {
        self.resources.counting_authority.roles_for_calls()
    }
    fn group_load_calls(&self) -> usize {
        self.resources.counting_group.try_get_calls()
    }
    fn session_load_calls(&self) -> usize {
        self.resources.counting_session.try_get_calls()
    }

    fn arm_resource_read_failure(&self, kind: DeliveryResourceKind) {
        match kind {
            DeliveryResourceKind::Group => self
                .resources
                .counting_group
                .arm_resource_read_failure(),
            DeliveryResourceKind::Session => self
                .resources
                .counting_session
                .arm_resource_read_failure(),
        }
    }

    fn arm_authority_batch_failure(&self) {
        self.resources
            .counting_authority
            .arm_authority_batch_failure();
    }

    async fn revoke_view_x_authority(&self) {
        // The REAL manager-revoke lane: the owner revokes the manager's
        // non-team sources on exactly bot X.
        self.bot_repo
            .mutate_manager(
                AuditActor::Human {
                    user_id: OWNER_USER.to_string(),
                },
                VIEW_BOT_X,
                ManagerMutation::RevokeNonTeam {
                    user_id: MANAGER_USER.to_string(),
                },
            )
            .await
            .expect("real revoke of the manager user over bot X");
    }

    async fn remove_participant_user(&self) {
        // The REAL session-membership removal lane.
        self.resources
            .raw_session_repo
            .remove_participant(SESSION_ID, &human_actor_id(PARTICIPANT_USER))
            .await
            .expect("real session removal of the participant user");
    }
}

// ── driver 2: the application-level map authority double ────────────────

struct MapAuthorityDriver {
    resources: ResourceStack,
    service: Arc<DeliveryAuthorizationServiceImpl>,
    map_authority: Arc<MapAuthorityCore>,
}

impl MapAuthorityDriver {
    async fn build() -> Self {
        let map_authority = Arc::new(MapAuthorityCore::new());
        map_authority.seed_manager_authority(MANAGER_USER, VIEW_BOT_X);
        map_authority.seed_manager_authority(MANAGER_USER, VIEW_BOT_Y);
        let authority_core: Arc<dyn BotAuthorityCoreService> = map_authority.clone();
        let resources = ResourceStack::seed(authority_core).await;
        let service = Arc::new(DeliveryAuthorizationServiceImpl::new(
            resources.counting_authority.clone(),
            resources.counting_group.clone(),
            resources.counting_session.clone(),
        ));
        Self {
            resources,
            service,
            map_authority,
        }
    }

    fn harness(self) -> DeliveryAuthorizationHarness {
        let driver = Arc::new(self);
        DeliveryAuthorizationHarness {
            service: driver.service.clone(),
            driver,
        }
    }
}

#[async_trait]
impl DeliveryAuthorizationDriver for MapAuthorityDriver {
    fn env(&self) -> &'static str {
        ENV
    }
    fn group_id(&self) -> &'static str {
        GROUP_ID
    }
    fn session_id(&self) -> &'static str {
        SESSION_ID
    }
    fn manager_user(&self) -> &'static str {
        MANAGER_USER
    }
    fn view_bot_x(&self) -> &'static str {
        VIEW_BOT_X
    }
    fn view_bot_y(&self) -> &'static str {
        VIEW_BOT_Y
    }
    fn participant_user(&self) -> &'static str {
        PARTICIPANT_USER
    }
    fn other_actor(&self) -> &'static str {
        OTHER_ACTOR
    }

    fn authority_batch_calls(&self) -> usize {
        self.resources.counting_authority.roles_for_calls()
    }
    fn group_load_calls(&self) -> usize {
        self.resources.counting_group.try_get_calls()
    }
    fn session_load_calls(&self) -> usize {
        self.resources.counting_session.try_get_calls()
    }

    fn arm_resource_read_failure(&self, kind: DeliveryResourceKind) {
        match kind {
            DeliveryResourceKind::Group => self
                .resources
                .counting_group
                .arm_resource_read_failure(),
            DeliveryResourceKind::Session => self
                .resources
                .counting_session
                .arm_resource_read_failure(),
        }
    }

    fn arm_authority_batch_failure(&self) {
        self.resources
            .counting_authority
            .arm_authority_batch_failure();
    }

    async fn revoke_view_x_authority(&self) {
        self.map_authority
            .revoke_manager_authority(MANAGER_USER, VIEW_BOT_X);
    }

    async fn remove_participant_user(&self) {
        self.resources
            .raw_session_repo
            .remove_participant(SESSION_ID, &human_actor_id(PARTICIPANT_USER))
            .await
            .expect("real session removal of the participant user");
    }
}

// ── conformance mounts ──────────────────────────────────────────────────

#[tokio::test]
async fn real_memory_authority_stack_meets_delivery_authorization_contract() {
    let harness = RealMemoryDriver::build().await.harness();
    delivery_authorization_service_contract_tests(&harness).await;
}

#[tokio::test]
async fn map_authority_double_meets_delivery_authorization_contract() {
    let harness = MapAuthorityDriver::build().await.harness();
    delivery_authorization_service_contract_tests(&harness).await;
}

pub struct MapAuthorityCore {
    manager_edges: Mutex<Vec<(String, String)>>,
}

impl MapAuthorityCore {
    pub fn new() -> Self {
        Self {
            manager_edges: Mutex::new(Vec::new()),
        }
    }

    pub fn seed_manager_authority(&self, user_id: &str, bot_id: &str) {
        let mut edges = self.manager_edges.lock().unwrap();
        if !edges.contains(&(user_id.to_string(), bot_id.to_string())) {
            edges.push((user_id.to_string(), bot_id.to_string()));
        }
    }

    pub fn revoke_manager_authority(&self, user_id: &str, bot_id: &str) {
        self.manager_edges
            .lock()
            .unwrap()
            .retain(|(user, bot)| !(user == user_id && bot == bot_id));
    }
}

#[async_trait]
impl BotAuthorityCoreService for MapAuthorityCore {
    async fn ownership(&self, _bot_id: &str) -> ServiceResult<OwnershipState> {
        Ok(OwnershipState {
            owner_user_id: "map-owner".to_string(),
            ownership_version: 1,
        })
    }

    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>> {
        let holds = self
            .manager_edges
            .lock()
            .unwrap()
            .contains(&(user_id.to_string(), bot_id.to_string()));
        Ok(holds.then_some(BotAccessRelation::Manager))
    }

    async fn roles_for(
        &self,
        pairs: &[(String, String)],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        let edges = self.manager_edges.lock().unwrap();
        Ok(pairs
            .iter()
            .map(|(user_id, bot_id)| {
                edges
                    .contains(&(user_id.clone(), bot_id.clone()))
                    .then_some(BotAccessRelation::Manager)
            })
            .collect())
    }

    async fn mutate_manager(
        &self,
        _actor: AuditActor,
        _bot_id: &str,
        _mutation: ManagerMutation,
    ) -> ServiceResult<ManagerMutationResult> {
        Err(ServiceError::InvalidOperation {
            message: "MapAuthorityCore is a read-only authority double".to_string(),
            request_id: None,
        })
    }

    async fn list_managers(
        &self,
        _bot_id: &str,
        _offset: u64,
        _limit: u64,
    ) -> ServiceResult<BotManagerList> {
        Err(ServiceError::InvalidOperation {
            message: "MapAuthorityCore is a read-only authority double".to_string(),
            request_id: None,
        })
    }

    async fn sync_team(&self, _command: TeamManagerSync) -> ServiceResult<TeamSyncReceipt> {
        Err(ServiceError::InvalidOperation {
            message: "MapAuthorityCore is a read-only authority double".to_string(),
            request_id: None,
        })
    }

    async fn create_transfer(
        &self,
        _command: CreateOwnershipTransfer,
    ) -> ServiceResult<CreateTransferResult> {
        Err(ServiceError::InvalidOperation {
            message: "MapAuthorityCore is a read-only authority double".to_string(),
            request_id: None,
        })
    }

    async fn decide_transfer(
        &self,
        _actor_user_id: &str,
        _transfer_id: &str,
        _action: TransferAction,
    ) -> ServiceResult<CommittedTransferOutcome> {
        Err(ServiceError::InvalidOperation {
            message: "MapAuthorityCore is a read-only authority double".to_string(),
            request_id: None,
        })
    }

    async fn get_transfer(
        &self,
        _viewer_user_id: &str,
        _transfer_id: &str,
    ) -> ServiceResult<OwnershipTransfer> {
        Err(ServiceError::InvalidOperation {
            message: "MapAuthorityCore is a read-only authority double".to_string(),
            request_id: None,
        })
    }

    async fn list_transfers(
        &self,
        _query: ListOwnershipTransfers,
    ) -> ServiceResult<OwnershipTransferPage> {
        Err(ServiceError::InvalidOperation {
            message: "MapAuthorityCore is a read-only authority double".to_string(),
            request_id: None,
        })
    }
}
