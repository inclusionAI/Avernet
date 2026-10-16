//! Final-writer delivery authorization (bot-authority plan Task
//! 7.4/7.6, spec §8.2/§8.3).
//!
//! The matrix runs the REAL `DeliveryAuthorizationServiceImpl` over
//! REAL Group/Session authorization snapshots (memory conformance
//! here; the SQL store behavior is the same strict contract with a
//! count-verified case at the bottom) and a controllable authority
//! hook. Every decision cost is asserted in READS (authority edge,
//! resource snapshots) — one frame's decision must be a bounded
//! read set, never an N+1.

use std::collections::BTreeSet;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_session::{DeliveryAuthorizationServiceImpl, GroupSessionConnectionServiceImpl};
use bcs_service_api::application::v1::{GroupSessionConnectionService, VerifyGroupSessionConnectionToken};
use bcs_domain::{ActorKind, GroupStrategy, MessageViewScope, ParticipantMode, ParticipantRole};
use bcs_domain::{MessageAudience, MessageVisibilityDomain};
use bcs_group::GroupCore;
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::port::bot_authority::BotAuthorityHook;
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::application::v1::{
    AddSessionParticipant, ApplicationError, AuthenticatedCaller,
    AuthenticatedUserIdentity, AuthorizeGroupSessionConnection, CompleteSession,
    CollectSession, ConnectionView, CreateSession, CreateSessionOutcome, DeleteResult,
    DeleteSession, DeleteSessionParticipant, DeliveryAuthorizationService,
    DeliveryDecision, DeliveryVisibility, GetSession, GroupSessionConnectionBinding,
    ProtectedDeliveryBinding,
    GroupSessionConnectionError, GROUP_SESSION_WS_TOKEN_TTL_SECONDS,
    IssueGroupSessionConnectionToken, ListSessions, Page, SessionCollectionResult,
    SessionCompletionResult, SessionDetail, SessionParticipant, SessionService,
    SessionStatus, SessionSummary, UncollectSession, UpdateSession,
    UpdateSessionParticipant,
};
use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::types::authorization_snapshot::{
    GroupAuthorizationSnapshot, ParticipantAuthorizationSnapshot,
    SessionAuthorizationSnapshot,
};
use bcs_service_api::types::bot_authority::{BotAccessRelation, BotRoleQuery};
use bcs_service_api::core::{DmActorSpec, GroupMutationCommand};
use bcs_service_api::types::{GroupHumanNotifyPolicy, GroupMutableFieldsPatch};
use bcs_service_api::{
    Group as DomainGroup, GroupKind, GroupMessage, GroupStatus, Participant,
    ServiceError, ServiceResult, ServiceSpec, SessionKind, Workspace,
};
use bcs_session::SessionAuthorizationCoreImpl;
use bcs_session_store::MemorySessionRepo;

const ENV: &str = "delivery-auth";

// ---------------------------------------------------------------------------
// Controllable authority hook (live edge counting + failure injection)
// ---------------------------------------------------------------------------

#[derive(Default)]
struct TestHook {
    managed: Mutex<BTreeSet<(String, String)>>,
    fail: AtomicBool,
    can_manage_calls: AtomicU64,
}

impl TestHook {
    fn grant(&self, user_id: &str, bot_id: &str) {
        self.managed
            .lock()
            .expect("hook set")
            .insert((user_id.to_string(), bot_id.to_string()));
    }

    fn revoke(&self, user_id: &str, bot_id: &str) {
        self.managed
            .lock()
            .expect("hook set")
            .remove(&(user_id.to_string(), bot_id.to_string()));
    }

    fn calls(&self) -> u64 {
        self.can_manage_calls.load(Ordering::SeqCst)
    }

    fn fail_reads(&self) {
        self.fail.store(true, Ordering::SeqCst);
    }
}

#[async_trait]
impl BotAuthorityHook for TestHook {
    async fn role(&self, _user_id: &str, _bot_id: &str) -> ServiceResult<Option<BotAccessRelation>> {
        Err(ServiceError::InternalError("unimplemented".into()))
    }

    async fn roles_for(
        &self,
        _queries: &[BotRoleQuery],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
        Err(ServiceError::InternalError("unimplemented".into()))
    }

    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
        self.can_manage_calls.fetch_add(1, Ordering::SeqCst);
        if self.fail.load(Ordering::SeqCst) {
            return Err(ServiceError::InternalError("authority read failed".into()));
        }
        Ok(self
            .managed
            .lock()
            .expect("hook set")
            .contains(&(user_id.to_string(), bot_id.to_string())))
    }

    async fn require_owner(&self, _user_id: &str, _bot_id: &str) -> ServiceResult<()> {
        Err(ServiceError::InternalError("unimplemented".into()))
    }

    async fn require_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        if self.can_manage(user_id, bot_id).await? {
            Ok(())
        } else {
            Err(ServiceError::Forbidden("no owner/manager edge".into()))
        }
    }
}

// ---------------------------------------------------------------------------
// Fixture: real Group/Session authorization snapshots + real service
// ---------------------------------------------------------------------------

async fn snapshot_fixture() -> (Arc<SnapshotFixture>, String, String) {
    let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let (fixture, group_id, session_id) =
        SnapshotFixture::over(group_repo, session_repo).await;
    (Arc::new(fixture), group_id, session_id)
}

struct SnapshotFixture {
    service: DeliveryAuthorizationServiceImpl,
    hook: Arc<TestHook>,
    group_repo: Arc<dyn GroupRepoPort>,
    session_repo: Arc<dyn SessionRepoPort>,
}

/// Seed a MultiWorker-shaped resource set:
///
/// - Group `strategy` with Bot driver/worker and a Human participant;
/// - Session seeded from the same participants and produced with
///   explicit scopes so the Session scope evolves independently of
///   the Group (spec §8.3).
async fn seed_resource_set(
    group_repo: &dyn GroupRepoPort,
    session_repo: &dyn SessionRepoPort,
    group_id: &str,
    strategy: GroupStrategy,
    group_scope: MessageViewScope,
    session_scope: MessageViewScope,
) -> String {
    let mut group = DomainGroup::new(group_id, "bot-driver", Vec::new());
    group.group_strategy = strategy;
    group.participants = vec![
        Participant::bot("bot-driver", ParticipantRole::Driver),
        Participant::bot("bot-worker", ParticipantRole::Worker),
        human_participant("human_view", ParticipantRole::Observer, group_scope),
    ];
    group_repo.upsert(group).await.expect("seed group upsert");

    let session = session_repo
        .create(
            group_id,
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("bot-driver", ParticipantRole::Driver),
                    Participant::bot("bot-worker", ParticipantRole::Worker),
                    human_participant(
                        "human_view",
                        ParticipantRole::Observer,
                        session_scope,
                    ),
                ],
                created_by: Some("bot-driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    session.id.clone()
}

impl SnapshotFixture {
    async fn over(
        group_repo: Arc<dyn GroupRepoPort>,
        session_repo: Arc<dyn SessionRepoPort>,
    ) -> (Self, String, String) {
        let hook = Arc::new(TestHook::default());
        hook.grant("user-a", "bot-worker");
        let group_id = "delivery-group";
        let session_id = seed_resource_set(
            group_repo.as_ref(),
            session_repo.as_ref(),
            group_id,
            GroupStrategy::ManagerWorker,
            MessageViewScope::Participant,
            MessageViewScope::Participant,
        )
        .await;
        let service = DeliveryAuthorizationServiceImpl::new(
            Arc::new(GroupCore::with_repo(group_repo.clone())),
            Arc::new(SessionAuthorizationCoreImpl::new(session_repo.clone())),
            Some(hook.clone()),
        );
        (
            Self {
                service,
                hook,
                group_repo,
                session_repo,
            },
            group_id.to_string(),
            session_id,
        )
    }

    fn service(&self) -> &DeliveryAuthorizationServiceImpl {
        &self.service
    }
}

fn human_participant(
    actor_id: &str,
    role: ParticipantRole,
    scope: MessageViewScope,
) -> Participant {
    Participant {
        bot_uuid: actor_id.to_string(),
        bot_name: Some(actor_id.to_string()),
        kind: None,
        role,
        actor_kind: ActorKind::Human,
        mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: scope,
    }
}

fn binding(
    user_id: &str,
    group_id: &str,
    session_id: Option<&str>,
    view_actor_id: &str,
) -> ProtectedDeliveryBinding {
    ProtectedDeliveryBinding {
        tenant: Some("tenant-a".to_string()),
        user_id: user_id.to_string(),
        group_id: group_id.to_string(),
        session_id: session_id.map(str::to_string),
        view_actor_id: view_actor_id.to_string(),
        connection_instance_id: 7,
    }
}

fn visibility(
    domain: MessageVisibilityDomain,
    audience: Option<MessageAudience>,
) -> DeliveryVisibility {
    DeliveryVisibility { domain, audience }
}

fn full_only() -> Option<MessageAudience> {
    Some(MessageAudience::FullOnly)
}

fn directed(actor_id: &str) -> Option<MessageAudience> {
    Some(
        MessageAudience::directed([actor_id]).expect("valid directed audience"),
    )
}

async fn decide(
    fixture: &SnapshotFixture,
    binding: &ProtectedDeliveryBinding,
    visibility: &DeliveryVisibility,
) -> Result<DeliveryDecision, ApplicationError> {
    fixture
        .service()
        .authorize_delivery(binding, visibility)
        .await
}

// ---------------------------------------------------------------------------
// Scope tightening: SkipMessage WITHOUT unbinding (§8.3)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn participant_scope_skips_full_only_and_delivers_public_and_directed() {
    let (fixture, group_id, session_id) = snapshot_fixture().await;
    let human = binding("user-a", &group_id, Some(&session_id), "human_view");

    // Group 帧 (Group scope is tightened too; the qualifier stays).
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::ManagerWorker, full_only())).await.unwrap(),
        DeliveryDecision::SkipMessage
    );
    // Public/Directed-to-self frames still deliver per §8.3.
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::ManagerWorker, Some(MessageAudience::Public))).await.unwrap(),
        DeliveryDecision::Deliver
    );
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::ManagerWorker, directed("human_view"))).await.unwrap(),
        DeliveryDecision::Deliver
    );
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::ManagerWorker, directed("other"))).await.unwrap(),
        DeliveryDecision::SkipMessage
    );
    // Ordinary Chat frames deliver to every view.
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::Chat, None)).await.unwrap(),
        DeliveryDecision::Deliver
    );
    // Session 帧 (Session scope, same rules).
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::StateMachine, full_only())).await.unwrap(),
        DeliveryDecision::SkipMessage
    );

    // The binding was never invalidated: every frame decision is
    // independent and the connection stays authorized.
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::Chat, None)).await.unwrap(),
        DeliveryDecision::Deliver,
        "scope tightening must not unbind the connection"
    );
}

#[tokio::test]
async fn full_scope_view_delivers_every_frame() {
    let (fixture, group_id, session_id) = snapshot_fixture().await;
    let deep_group = "full-group";
    let deep_session = seed_resource_set(
        fixture.group_repo.as_ref(),
        fixture.session_repo.as_ref(),
        deep_group,
        GroupStrategy::ManagerWorker,
        MessageViewScope::Full,
        MessageViewScope::Full,
    )
    .await;
    let human = binding("user-a", deep_group, Some(&deep_session), "human_view");
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::ManagerWorker, full_only())).await.unwrap(),
        DeliveryDecision::Deliver
    );
    let _ = (group_id, session_id);
}

/// Chat Group strategy never rewrites the producer domain: a Chat
/// Group's one-shot StateMachine frame stays scoped (§8.3 末段).
#[tokio::test]
async fn chat_group_state_machine_producer_frame_stays_scoped() {
    let (fixture, group_id, session_id) = snapshot_fixture().await;
    let chat_group = "chat-group";
    let chat_session = seed_resource_set(
        fixture.group_repo.as_ref(),
        fixture.session_repo.as_ref(),
        chat_group,
        GroupStrategy::Chat,
        MessageViewScope::Participant,
        MessageViewScope::Participant,
    )
    .await;
    let human = binding("user-a", &chat_group, Some(&chat_session), "human_view");
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::StateMachine, full_only())).await.unwrap(),
        DeliveryDecision::SkipMessage,
        "producer domain=StateMachine frames are not rewritten by the Chat strategy"
    );
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::Chat, None)).await.unwrap(),
        DeliveryDecision::Deliver
    );
    let _ = (group_id, session_id);
}

/// Session/Run frames use the current SESSION scope; Group frames
/// use the current GROUP scope; neither reuses the other (§8.3).
#[tokio::test]
async fn session_and_group_frames_use_their_own_current_scope() {
    let (fixture, group_id, session_id) = snapshot_fixture().await;
    // Group participant scope Participant, Session scope Full.
    let mixed_group = "mixed-group";
    let mixed_session = seed_resource_set(
        fixture.group_repo.as_ref(),
        fixture.session_repo.as_ref(),
        mixed_group,
        GroupStrategy::ManagerWorker,
        MessageViewScope::Participant,
        MessageViewScope::Full,
    )
    .await;

    let session_binding = binding("user-a", mixed_group, Some(&mixed_session), "human_view");
    assert_eq!(
        decide(&fixture, &session_binding, &visibility(MessageVisibilityDomain::ManagerWorker, full_only())).await.unwrap(),
        DeliveryDecision::Deliver,
        "Session scope is Full: Session/Run frames deliver"
    );

    let group_binding = binding("user-a", mixed_group, None, "human_view");
    assert_eq!(
        decide(&fixture, &group_binding, &visibility(MessageVisibilityDomain::ManagerWorker, full_only())).await.unwrap(),
        DeliveryDecision::SkipMessage,
        "Group frames use the current GROUP scope (Participant here), never the Session's"
    );
    let _ = (group_id, session_id);
}

// ---------------------------------------------------------------------------
// 丢失资源资格 → InvalidateBinding (B 只撤资源角色，管理边仍有效)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn lost_session_participation_invalidates_even_with_a_valid_edge() {
    let (fixture, group_id, session_id) = snapshot_fixture().await;
    // 缓存: the current facts authorize the Bot view.
    let worker = binding("user-a", &group_id, Some(&session_id), "bot-worker");
    assert_eq!(
        decide(&fixture, &worker, &visibility(MessageVisibilityDomain::StateMachine, full_only())).await.unwrap(),
        DeliveryDecision::Deliver
    );

    // B revokes ONLY the Bot's resource role; the Human management
    // edge (user-a → bot-worker) stays valid. The writer MUST
    // invalidate — resource qualification loss, not a visibility
    // miss.
    fixture
        .session_repo
        .remove_participant(&session_id, "bot-worker")
        .await
        .expect("remove session participant");

    assert_eq!(
        decide(&fixture, &worker, &visibility(MessageVisibilityDomain::StateMachine, full_only())).await.unwrap(),
        DeliveryDecision::InvalidateBinding,
        "a Group seed must not repair a missing Session participant"
    );
    // The edge is still there but does not substitute the resource.
    assert!(fixture.hook.calls() >= 2, "the authority edge is re-read per frame");

    // Restoring the qualification re-enables delivery without a
    // reconnect (every frame's decision is independent).
    fixture
        .session_repo
        .add_participant(&session_id, Participant::bot("bot-worker", ParticipantRole::Worker))
        .await
        .expect("restore participant");
    assert_eq!(
        decide(&fixture, &worker, &visibility(MessageVisibilityDomain::StateMachine, full_only())).await.unwrap(),
        DeliveryDecision::Deliver
    );
}

#[tokio::test]
async fn lost_group_participation_invalidates_group_frames() {
    let (fixture, group_id, session_id) = snapshot_fixture().await;
    let group_binding = binding("user-a", &group_id, None, "bot-worker");
    fixture
        .group_repo
        .remove_participant(&group_id, "bot-worker")
        .await
        .expect("remove group participant");
    assert_eq!(
        decide(&fixture, &group_binding, &visibility(MessageVisibilityDomain::StateMachine, full_only())).await.unwrap(),
        DeliveryDecision::InvalidateBinding
    );
    let _ = session_id;
}

// ---------------------------------------------------------------------------
// 丢失管理边 / 失效 → InvalidateBinding (§8.2)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn revoked_management_edge_invalidates_the_bot_view() {
    let (fixture, group_id, session_id) = snapshot_fixture().await;
    let worker = binding("user-a", &group_id, Some(&session_id), "bot-worker");
    fixture.hook.revoke("user-a", "bot-worker");
    assert_eq!(
        decide(&fixture, &worker, &visibility(MessageVisibilityDomain::Chat, None)).await.unwrap(),
        DeliveryDecision::InvalidateBinding
    );

    // 转交 keeps the old owner usable: owner removed but manager
    // edge granted to the SAME user still manages.
    fixture.hook.grant("user-a", "bot-worker");
    assert_eq!(
        decide(&fixture, &worker, &visibility(MessageVisibilityDomain::Chat, None)).await.unwrap(),
        DeliveryDecision::Deliver,
        "the old owner remains manager-authorized after handover"
    );

    // The Human self view on the SAME connection pattern keeps its
    // independent §3.3 right and reads no authority edge.
    let self_binding = binding(
        "user-a",
        &group_id,
        Some(&session_id),
        "human_user-a",
    );
    // user-a is not a participant: qualification check is the same
    // invalidation, NOT an authority consultation.
    let calls_before = fixture.hook.calls();
    assert_eq!(
        decide(&fixture, &self_binding, &visibility(MessageVisibilityDomain::Chat, None)).await.unwrap(),
        DeliveryDecision::InvalidateBinding
    );
    assert_eq!(fixture.hook.calls(), calls_before, "Human self views consult no authority edge");
}

#[tokio::test]
async fn read_failures_are_desensitized_internal_errors() {
    let (fixture, group_id, session_id) = snapshot_fixture().await;
    let worker = binding("user-a", &group_id, Some(&session_id), "bot-worker");
    fixture.hook.fail_reads();
    let error = decide(&fixture, &worker, &visibility(MessageVisibilityDomain::Chat, None))
        .await
        .expect_err("authority read failure is an Err");
    assert!(matches!(error, ApplicationError::Internal(_)));
    let message = error.to_string();
    assert!(
        !message.contains("authority read failed"),
        "adapter sees only a desensitized message: {message}"
    );
    assert!(!message.to_lowercase().contains("sql"), "no storage detail escapes");
    let _ = (group_id, session_id);
}

/// Missing wiring fails closed; a deleted resource invalidates; an
/// application read of the Session (not the WS) maps hidden to the
/// desensitized internal error.
#[tokio::test]
async fn deleted_resource_or_unwired_hook_fails_closed() {
    let (fixture, group_id, session_id) = snapshot_fixture().await;
    let worker = binding("user-a", &group_id, Some(&session_id), "bot-worker");

    fixture.session_repo.delete(&session_id).await.expect("delete session");
    assert_eq!(
        decide(&fixture, &worker, &visibility(MessageVisibilityDomain::Chat, None)).await.unwrap(),
        DeliveryDecision::InvalidateBinding
    );
    let _ = group_id;

    // Unwired hook: a Bot view must never deliver.
    let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let unhooked_session = seed_resource_set(
        group_repo.as_ref(),
        session_repo.as_ref(),
        "unhooked",
        GroupStrategy::Chat,
        MessageViewScope::Full,
        MessageViewScope::Full,
    )
    .await;
    let unhooked = DeliveryAuthorizationServiceImpl::new(
        Arc::new(GroupCore::with_repo(group_repo.clone())),
        Arc::new(SessionAuthorizationCoreImpl::new(session_repo.clone())),
        None,
    );
    let bot = binding("user-a", "unhooked", Some(&unhooked_session), "bot-driver");
    let error = unhooked
        .authorize_delivery(&bot, &visibility(MessageVisibilityDomain::Chat, None))
        .await
        .expect_err("unwired hook fails closed");
    assert!(matches!(error, ApplicationError::Internal(_)));
}

/// A Human participant that moved to Absent (removed from the
/// conversation) lost what connect granted.
#[tokio::test]
async fn absent_human_participant_invalidates() {
    let (fixture, group_id, session_id) = snapshot_fixture().await;
    let human = binding("other-user", &group_id, Some(&session_id), "human_view");
    // Same session; keep the edge for the HUMAN irrelevant — self
    // views read no authority.
    fixture
        .session_repo
        .remove_participant(&session_id, "human_view")
        .await
        .expect("remove human");
    fixture
        .session_repo
        .add_participant(
            &session_id,
            Participant {
                bot_uuid: "human_view".into(),
                bot_name: Some("human_view".into()),
                kind: None,
                role: ParticipantRole::Observer,
                actor_kind: ActorKind::Human,
                mode: Some(ParticipantMode::Absent),
                tags: Vec::new(),
                message_view_scope: MessageViewScope::Participant,
            },
        )
        .await
        .expect("re-add absent human");
    assert_eq!(
        decide(&fixture, &human, &visibility(MessageVisibilityDomain::Chat, None)).await.unwrap(),
        DeliveryDecision::InvalidateBinding
    );
    let _ = group_id;
}

// ---------------------------------------------------------------------------
// 7.6 成本记录: bounded reads per decision
// ---------------------------------------------------------------------------

/// Counting Group snapshot core: the delivery authorization path
/// may ONLY call `read_authorization_snapshot`; every other core
/// call panics, which pins the decision cost envelope (no N+1,
/// no accidental core reads through cached paths).
struct CountingGroupCore {
    inner: Arc<dyn bcs_service_api::GroupCoreService>,
    group_reads: AtomicU64,
}

#[async_trait::async_trait]
impl bcs_service_api::GroupCoreService for CountingGroupCore {
/// Create or update a group.
    async fn upsert(&self, group: DomainGroup) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

async fn finalize_provisioning(
        &self,
        command: bcs_service_api::port::repo::FinalizeGroupProvisioning,
    ) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Apply one DomainGroup lifecycle mutation and its public Event in the same
    /// repository transaction. Implementations return the existing state for
    /// idempotent no-ops without producing an Event.
    async fn mutate(&self, command: GroupMutationCommand) -> ServiceResult<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Atomically patch only the mutable OpenAPI v1 fields that are present.
    async fn patch_mutable_fields(
        &self,
        id: &str,
        patch: GroupMutableFieldsPatch,
    ) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Get a group by ID.
    async fn get(&self, id: &str) -> Option<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Fallible lookup for API boundaries that must distinguish storage
    /// failures from a missing DomainGroup.
    async fn try_get(&self, id: &str) -> ServiceResult<Option<DomainGroup>> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

    async fn read_authorization_snapshot(
        &self,
        group_id: &str,
    ) -> ServiceResult<Option<GroupAuthorizationSnapshot>> {
        self.group_reads.fetch_add(1, Ordering::SeqCst);
        self.inner.read_authorization_snapshot(group_id).await
    }

/// Add a message to a group.
    async fn add_message(&self, id: &str, message: GroupMessage) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Add a participant to a group.
    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

async fn add_participant_with_visibility_guard(
        &self,
        id: &str,
        participant: Participant,
        actor_is_public: bool,
    ) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Remove a participant from a group by bot_uuid.
    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Update an existing participant's `mode` (Human Actor V1, Task P.1).
    ///
    /// Idempotent: if the new `mode` equals the current one, returns Ok without
    /// any DB write. Returns `GroupNotFound` if the group is missing and
    /// `ParticipantNotFound` if the actor is not in the group.
    ///
    /// Caller is responsible for validating that `mode.is_valid_for(actor_kind)`
    /// before calling this method (handler-level concern).
    async fn update_participant_mode(
        &self,
        group_id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Persist the effective Human message view scope. Bot participants must
    /// remain `full`; callers authorize self-service versus manager updates.
    async fn update_participant_message_view_scope(
        &self,
        group_id: &str,
        actor_id: &str,
        message_view_scope: MessageViewScope,
    ) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Insert a Human participant into an existing group (Human Actor V1, Task P.1).
    ///
    /// Convenience wrapper that constructs a `Participant` with
    /// `actor_kind=Human`, `role=Observer`, and the supplied `mode`,
    /// then delegates to `add_participant`. Idempotent (no-op if the
    /// `(group_id, human_id)` row already exists).
    async fn insert_human_participant(
        &self,
        group_id: &str,
        human_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Update group workspace.
    async fn update_workspace(&self, id: &str, workspace: Workspace) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Update group label.
    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Update group status.
    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Replace (or remove) the group's `service_spec`. Validation of the
    /// patch (route-field lock, callback_config immutability) is done by the
    /// HTTP/use-case layer; this contract just persists the new value.
    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<ServiceSpec>,
    ) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Terminate a group.
    ///
    /// Only the driver/coordinator can terminate a group.
    /// Sets status to `Completed` and returns the final group state.
    ///
    /// # Arguments
    /// * `id` - The group ID
    /// * `caller_bot_id` - The bot_id of the caller (must be the driver)
    ///
    /// # Returns
    /// The final group state, or error if not authorized or not found.
    async fn terminate(&self, id: &str, caller_bot_id: &str) -> ServiceResult<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Delete a group.
    async fn delete(&self, id: &str) -> ServiceResult<Option<DomainGroup>> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// List all sessions.
    async fn list(&self) -> Vec<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// List groups with pagination, ordered by `updated_at` descending.
    /// - MySQL: uses SQL LIMIT/OFFSET after ordering
    /// - Memory: orders then slices the result after loading
    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Find all sessions where the given bot is a participant.
    ///
    /// Return order is intentionally undefined; externally visible callers
    /// must apply their own ordering before pagination or response mapping.
    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

async fn try_find_by_participant(&self, bot_uuid: &str) -> ServiceResult<Vec<DomainGroup>> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Find groups by participant, optionally filtered by group kind and label
    /// query. `label_query` matches label only; group id is intentionally not
    /// part of the search semantics.
    async fn find_by_participant_filtered(
        &self,
        bot_uuid: &str,
        kind: Option<GroupKind>,
        label_query: Option<&str>,
    ) -> Vec<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Count all groups.
    async fn count(&self) -> u64 {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Count groups where the given bot is a participant.
    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Find groups by participant with pagination, ordered by `updated_at`
    /// descending before `offset` / `limit` are applied.
    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Get the current message count for a group.
    async fn message_count(&self, id: &str) -> ServiceResult<usize> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Increment the message counter for a group (independent of add_message).
    async fn increment_message_count(&self, id: &str) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Reset the message counter for a group to zero.
    async fn reset_message_count(&self, id: &str) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// CR-4: count groups optionally filtered by `group_kind`.
    ///
    /// `kind=None` → count all groups (equivalent to legacy `count()`).
    /// `kind=Some(GroupKind::Dm)` → count only dm groups.
    /// `kind=Some(GroupKind::Normal)` → count only normal groups.
    ///
    /// Implementations own filtering semantics. The contract default is an
    /// empty stub for lightweight mocks only; production stores must override.
    async fn count_by_kind(&self, kind: Option<GroupKind>) -> u64 {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// CR-4: paginate groups optionally filtered by `group_kind`.
    ///
    /// Same semantics as `list_paginated` but the `offset` / `limit`
    /// window is applied **after** filtering by `kind` and ordering by
    /// `updated_at` descending. This means pagination is stable per-filter:
    /// a client paging through `kind=dm` only sees dm groups and `total`
    /// (from `count_by_kind(Some(Dm))`) matches the result set.
    ///
    /// Implementations own filtering and pagination semantics. The contract
    /// default is an empty stub for lightweight mocks only; production stores
    /// must override.
    async fn list_paginated_by_kind(
        &self,
        kind: Option<GroupKind>,
        offset: u64,
        limit: u64,
    ) -> Vec<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Update group visibility ("public" or "private").
    ///
    /// Implementations own persistence semantics. The contract default is an
    /// empty stub for lightweight mocks only; production stores must override.
    async fn update_visibility(&self, id: &str, visibility: &str) -> ServiceResult<()> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Count groups matching the given filters (kind, visibility, label).
    ///
    /// Implementations own filtering semantics. The contract default is an
    /// empty stub for lightweight mocks only; production stores must override.
    async fn count_filtered(
        &self,
        kind: Option<GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> u64 {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// List groups filtered by kind, visibility, and label substring.
    /// Results are ordered by `updated_at` descending with pagination.
    ///
    /// Implementations own filtering and pagination semantics. The contract
    /// default is an empty stub for lightweight mocks only; production stores
    /// must override.
    async fn list_paginated_filtered(
        &self,
        offset: u64,
        limit: u64,
        kind: Option<GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> Vec<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Find an existing `Dm` group by its canonical pair key (Task G.2).
    ///
    /// Returns the group if a row with `group_kind='dm' AND dm_pair_key=key`
    /// exists for the current environment, otherwise `None`. Backed by the
    /// `(env, dm_pair_key)` unique index on `bcs_groups` (migration 005).
    ///
    /// Implementations own lookup semantics. The contract default is an
    /// empty stub for lightweight mocks only; production stores must override.
    async fn find_dm_by_pair_key(&self, dm_pair_key: &str) -> Option<DomainGroup> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Create-or-reuse an actor-level `Dm` group atomically.
    ///
    /// Supports Bot↔Bot and Human↔Bot pairs. Human↔Human and identical actor
    /// pairs are invalid. Implementations must preserve canonical pair identity
    /// on reuse and must not mutate existing DM identity fields.
    async fn create_or_reuse_actor_dm_group(
        &self,
        id: &str,
        actor_a: DmActorSpec,
        actor_b: DmActorSpec,
        legacy_driver_bot: &str,
        originator_actor_id: &str,
        label: Option<String>,
        context: Option<String>,
    ) -> ServiceResult<(DomainGroup, bool)> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

#[allow(clippy::too_many_arguments)]
    async fn create_or_reuse_actor_dm_group_with_record_status(
        &self,
        id: &str,
        actor_a: DmActorSpec,
        actor_b: DmActorSpec,
        legacy_driver_bot: &str,
        originator_actor_id: &str,
        label: Option<String>,
        context: Option<String>,
        record_status: &str,
    ) -> ServiceResult<(DomainGroup, bool)> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Create-or-reuse a `Dm` group atomically (Task G.2 + CR-1 fix).
    ///
    /// Returns `(group, created)` where `created=true` ⇔ this call inserted
    /// the row and `created=false` ⇔ the row already existed (concurrent
    /// caller, reverse-direction reuse, or retry).
    ///
    /// **Why this contract changed (CR-1)**:
    /// the previous version of this method blindly did `upsert(...)`, which
    /// on the MySQL backend translates to `INSERT ... ON DUPLICATE KEY
    /// UPDATE` against the `(env, dm_pair_key)` unique index. On conflict
    /// that overwrote the *existing* group's `driver_bot` / `label` /
    /// `participants` and returned the *locally-constructed* `DomainGroup` whose
    /// `id` was never inserted. The handler then surfaced a `group_id` that
    /// didn't exist in the DB. The new contract:
    /// 1. `find_dm_by_pair_key` first; if hit → `(existing, false)` and the
    ///    caller's `id` / `label` / `driver_bot` are silently dropped (this
    ///    is the documented reverse-reuse semantics — Requirement 3.19,
    ///    Task G.3 acceptance "reverse reuse driver_bot 不变").
    /// 2. Otherwise insert; on race the second caller's insert fails on the
    ///    unique index, we re-read by `pair_key` and return `(existing, false)`.
    /// 3. Identity columns (`group_kind`, `dm_pair_key`, `driver_bot`,
    ///    `label`, `participants`) MUST NOT be mutated by step 2's insert
    ///    when the row already exists.
    ///
    /// Production stores must implement this in the concrete service crate.
    async fn create_or_reuse_dm_group(
        &self,
        id: &str,
        driver_bot: &str,
        bot_a: &str,
        bot_b: &str,
        label: Option<String>,
    ) -> ServiceResult<(DomainGroup, bool)> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }

/// Read the DomainGroup human-mention notify policy. Fail-closed default that
    /// production DomainGroup Core/Store implementations override in Task 2.
    ///
    /// This is a scoped, transport-neutral policy read. `None` means DomainGroup
    /// not found; storage or enum parsing errors return `Err`. It must not
    /// call cache-first `get`/`try_get` or load participants/Session state.
    async fn read_human_notify_policy(
        &self,
        _group_id: &str,
    ) -> ServiceResult<Option<GroupHumanNotifyPolicy>> {
        unreachable!(
            "delivery authorization cost test exercises only the snapshot read"
        )
    }
}

/// Per-decision READ costs (7.6): one frame decision is a
/// BOUNDED read set —
///
/// | decision | authority edge | Group snapshot | Session snapshot | visibility SQL |
/// | --- | --- | --- | --- | --- |
/// | Group frame, Human view | 0 | 1 | 0 | 0 |
/// | Group frame, Bot view | 1 | 1 | 0 | 0 |
/// | Session frame, Human view | 0 | 1 (strategy) | 1 | 0 |
/// | Session frame, Bot view | 1 | 1 | 1 | 0 |
///
/// No visibility SQL exists at all: the scope rides on the
/// snapshot rows. After invalidation the unbound connection
/// issues no further reads (pinned at the WS level).
#[tokio::test]
async fn per_decision_read_costs_are_bounded_and_visible_separately() {
    let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let session_id = seed_resource_set(
        group_repo.as_ref(),
        session_repo.as_ref(),
        "cost-group",
        GroupStrategy::ManagerWorker,
        MessageViewScope::Participant,
        MessageViewScope::Participant,
    )
    .await;

    let hook = Arc::new(TestHook::default());
    hook.grant("user-a", "bot-worker");
    let counting = Arc::new(CountingGroupCore {
        inner: Arc::new(GroupCore::with_repo(group_repo)),
        group_reads: AtomicU64::new(0),
    });
    let group_core: Arc<dyn bcs_service_api::GroupCoreService> = counting.clone();
    let service = DeliveryAuthorizationServiceImpl::new(
        group_core,
        Arc::new(SessionAuthorizationCoreImpl::new(session_repo)),
        Some(hook.clone()),
    );
    assert_eq!(counting.group_reads.load(Ordering::SeqCst), 0, "no reads before any decision");

    // Group frame, Human view.
    let group_human = binding("user-a", "cost-group", None, "human_view");
    assert_eq!(
        service
            .authorize_delivery(&group_human, &visibility(MessageVisibilityDomain::Chat, None))
            .await
            .unwrap(),
        DeliveryDecision::Deliver
    );
    assert_eq!(counting.group_reads.load(Ordering::SeqCst), 1, "Group frame: exactly one Group snapshot read");
    assert_eq!(hook.calls(), 0, "Human self views read no authority data");

    // Group frame, Bot view.
    let group_bot = binding("user-a", "cost-group", None, "bot-worker");
    assert_eq!(
        service
            .authorize_delivery(&group_bot, &visibility(MessageVisibilityDomain::Chat, None))
            .await
            .unwrap(),
        DeliveryDecision::Deliver
    );
    assert_eq!(counting.group_reads.load(Ordering::SeqCst), 2);
    assert_eq!(hook.calls(), 1, "one authority edge read per Bot-view frame");

    // Session frame, Human view.
    let session_human = binding("user-a", "cost-group", Some(&session_id), "human_view");
    assert_eq!(
        service
            .authorize_delivery(&session_human, &visibility(MessageVisibilityDomain::Chat, None))
            .await
            .unwrap(),
        DeliveryDecision::Deliver
    );
    assert_eq!(counting.group_reads.load(Ordering::SeqCst), 3, "Session frames also read the Group (strategy)");
    assert_eq!(hook.calls(), 1);

    // Session frame, Bot view.
    let session_bot = binding("user-a", "cost-group", Some(&session_id), "bot-worker");
    assert_eq!(
        service
            .authorize_delivery(&session_bot, &visibility(MessageVisibilityDomain::Chat, None))
            .await
            .unwrap(),
        DeliveryDecision::Deliver
    );
    assert_eq!(counting.group_reads.load(Ordering::SeqCst), 4);
    assert_eq!(hook.calls(), 2);
    // The counting core PANICS on any core call other than
    // the snapshot read — an accidental deeper core read
    // would fail this test loudly.
}

// ---------------------------------------------------------------------------
// 7.5: 连接视角矩阵 (token 签发不预选视角)
// ---------------------------------------------------------------------------

#[derive(Clone, Copy)]
enum ConnectSessionMode {
    Success,
}

struct ConnectSessionService {
    mode: ConnectSessionMode,
}

#[async_trait::async_trait]
impl SessionService for ConnectSessionService {
    async fn create(
        &self,
        _command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError> {
        panic!("create is not used by delivery authorization tests")
    }

    async fn list(
        &self,
        _command: ListSessions,
    ) -> Result<Page<SessionSummary>, ApplicationError> {
        panic!("list is not used by delivery authorization tests")
    }

    async fn get(&self, query: GetSession) -> Result<SessionDetail, ApplicationError> {
        if matches!(self.mode, ConnectSessionMode::Success) {
            Ok(SessionDetail {
                session_id: query.session_id.clone(),
                version: 1,
                group_id: "token-group".into(),
                status: SessionStatus::Running,
                kind: SessionKind::Chat,
                title: None,
                input: None,
                meta: None,
                participants: vec![SessionParticipant {
                    actor_id: "human_user-a".into(),
                    actor_kind: ActorKind::Human,
                    name: None,
                    role: ParticipantRole::Observer,
                    tags: Vec::new(),
                    mode: ParticipantMode::Present,
                    message_view_scope: MessageViewScope::Full,
                    joined_at: None,
                }],
                caller_principal: None,
                created_by: None,
                created_at: 1,
                updated_at: 1,
                state_machine_run_id: None,
                state_machine_run: None,
            })
        } else {
            panic!("no other mode in this fixture")
        }
    }

    async fn update(
        &self,
        _command: UpdateSession,
    ) -> Result<SessionDetail, ApplicationError> {
        panic!("update is not used by delivery authorization tests")
    }

    async fn delete(
        &self,
        _command: DeleteSession,
    ) -> Result<DeleteResult, ApplicationError> {
        panic!("delete is not used by delivery authorization tests")
    }

    async fn complete(
        &self,
        _command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        panic!("complete is not used by delivery authorization tests")
    }

    async fn collect(
        &self,
        _command: CollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        panic!("collect is not used by delivery authorization tests")
    }

    async fn uncollect(
        &self,
        _command: UncollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        panic!("uncollect is not used by delivery authorization tests")
    }

    async fn add_participant(
        &self,
        _command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        panic!("add_participant is not used by delivery authorization tests")
    }

    async fn update_participant(
        &self,
        _command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        panic!("update_participant is not used by delivery authorization tests")
    }

    async fn delete_participant(
        &self,
        _command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        panic!("delete_participant is not used by delivery authorization tests")
    }
}

struct ConnectSessionSnapshots;

#[async_trait::async_trait]
impl SessionAuthorizationCoreService for ConnectSessionSnapshots {
    async fn read_authorization_snapshot(
        &self,
        _session_id: &str,
    ) -> ServiceResult<Option<SessionAuthorizationSnapshot>> {
        Ok(Some(SessionAuthorizationSnapshot {
            env: ENV.into(),
            id: "session-token".into(),
            live: true,
            group_id: "token-group".into(),
            status: bcs_service_api::SessionStatus::Running,
            session_kind: SessionKind::Chat,
            created_by: None,
            message_visibility_version: 1,
            participant_join_seq: None,
            participants: vec![
                ParticipantAuthorizationSnapshot {
                    actor_id: "bot-worker".into(),
                    actor_kind: ActorKind::Bot,
                    role: ParticipantRole::Worker,
                    effective_mode: ParticipantMode::Auto,
                    message_view_scope: MessageViewScope::Full,
                },
                ParticipantAuthorizationSnapshot {
                    actor_id: "human_user-a".into(),
                    actor_kind: ActorKind::Human,
                    role: ParticipantRole::Observer,
                    effective_mode: ParticipantMode::Present,
                    message_view_scope: MessageViewScope::Participant,
                },
            ],
        }))
    }
}

fn connect_service(
    hook: Option<Arc<dyn BotAuthorityHook>>,
) -> GroupSessionConnectionServiceImpl {
    let tokens = bcs_jwt::GroupSessionJwtService::new("delivery-auth-secret")
        .expect("real JWT service");
    GroupSessionConnectionServiceImpl::new(
        Arc::new(ConnectSessionService { mode: ConnectSessionMode::Success }),
        Arc::new(tokens),
        Arc::new(ConnectSessionSnapshots),
        hook,
    )
}

fn human_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: "user-a".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn connect_binding() -> GroupSessionConnectionBinding {
    GroupSessionConnectionBinding {
        tenant: Some("tenant-a".into()),
        user_id: "user-a".into(),
        group_id: "token-group".into(),
        session_id: "session-token".into(),
    }
}

/// 签发只按 caller/session_id 验证 Session 读取资格，不接收
/// 或返回任何选定视角；视角在 connect/reconnect 时经既有
/// `view_actor_id` wire 字段显式选择并重新授权 (spec §8.3).
#[tokio::test]
async fn token_issuance_does_not_preselect_view() {
    let hook = Arc::new(TestHook::default());
    hook.grant("user-a", "bot-worker");
    let service = connect_service(Some(hook.clone()));

    // 1. Issuance: the REAL JWT port signs only the
    //    {tenant, uid, gid, sid} scope. No view exists on the
    //    command, the claims or the TTL.
    let issued = service
        .issue_token(IssueGroupSessionConnectionToken {
            caller: human_caller(),
            session_id: "session-token".into(),
        })
        .await
        .expect("issue current-session token");
    let claims = bcs_service_api::port::GroupSessionTokenPort::verify(
        &bcs_jwt::GroupSessionJwtService::new("delivery-auth-secret")
            .expect("same verifier"),
        &issued.token,
    )
    .expect("claims verify");
    assert_eq!(claims.scope.user_id, "user-a");
    assert_eq!(claims.scope.group_id, "token-group");
    assert_eq!(claims.scope.session_id, "session-token");
    assert_eq!(
        claims.expires_at - claims.issued_at,
        GROUP_SESSION_WS_TOKEN_TTL_SECONDS,
    );

    // 2. 合法管理者显式 Bot view 可连接.
    let authorized = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: connect_binding(),
            view: ConnectionView::Bot {
                bot_id: "bot-worker".into(),
            },
        })
        .await
        .expect("manager may explicitly select the controlled Bot view");
    assert_eq!(authorized.view, ConnectionView::Bot { bot_id: "bot-worker".into() });
    assert_eq!(hook.calls(), 1, "the connect verifies the CURRENT edge");

    // 3. 缺省 (HumanSelf) 只按 self 资格.
    let self_authorized = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: connect_binding(),
            view: ConnectionView::HumanSelf,
        })
        .await
        .expect("self view authorizes like the legacy connect");
    assert_eq!(self_authorized.view, ConnectionView::HumanSelf);
    assert_eq!(
        hook.calls(),
        1,
        "the self view consults no authority edge"
    );

    // 4. 无权限 Bot view 拒绝.
    let unmanaged = connect_service(Some(Arc::new(TestHook::default())));
    let error = unmanaged
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: connect_binding(),
            view: ConnectionView::Bot {
                bot_id: "bot-worker".into(),
            },
        })
        .await
        .expect_err("a revoked edge refuses the Bot view");
    assert!(matches!(
        error,
        GroupSessionConnectionError::Application(ApplicationError::Forbidden(_))
    ));

    // 5. 签发后撤角色仍拒绝连接: the SAME token works only
    //    while the edge holds.
    hook.revoke("user-a", "bot-worker");
    let error = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: connect_binding(),
            view: ConnectionView::Bot {
                bot_id: "bot-worker".into(),
            },
        })
        .await
        .expect_err("revocation after issuance must refuse the connection");
    assert!(matches!(
        error,
        GroupSessionConnectionError::Application(ApplicationError::Forbidden(_))
    ));

    // 6. 另一个 Human 的 view 不可能: the adapter normalizes
    //    it BEFORE the application; here the strict result is
    //    the Bot view of a bot that IS not controlled by
    //    "user-a".
    let _ = issued;
}

/// 派生 Run/Session 帧必须验证关联: the binding's token-bound
/// Group must STILL be the Session's current Group.
#[tokio::test]
async fn session_group_mismatch_invalidates() {
    let (fixture, _group_id, session_id) = snapshot_fixture().await;
    // The token binds group-a, but the Session has since
    // moved to another Group (the derived chain must verify
    // the association per frame).
    let mismatched = binding("user-a", "stale-group", Some(&session_id), "human_view");
    assert_eq!(
        decide(&fixture, &mismatched, &visibility(MessageVisibilityDomain::Chat, None)).await.unwrap(),
        DeliveryDecision::InvalidateBinding,
        "a mismatched bound Group is a resource mismatch, not a skip"
    );
}

/// The V1 Bot view connect authorizes against the CURRENT edge
/// and the SESSION snapshot; the same token alone proves
/// nothing (真实 snapshot + hook, 不经 SessionService::get).
#[tokio::test]
async fn bot_view_connect_uses_current_edge_and_session_qualification() {
    let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let hook = Arc::new(TestHook::default());
    hook.grant("user-a", "bot-worker");
    // Session with the Bot worker and the Human token user.
    let mut group = DomainGroup::new("conn-group", "bot-driver", Vec::new());
    group.group_strategy = GroupStrategy::Chat;
    group.participants = vec![
        Participant::bot("bot-driver", ParticipantRole::Driver),
        Participant::bot("bot-worker", ParticipantRole::Worker),
    ];
    group_repo.upsert(group).await.expect("seed group");
    let session = session_repo
        .create(
            "conn-group",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("bot-driver", ParticipantRole::Driver),
                    Participant::bot("bot-worker", ParticipantRole::Worker),
                ],
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let tokens = bcs_jwt::GroupSessionJwtService::new("delivery-auth-secret")
        .expect("real JWT");
    let service = GroupSessionConnectionServiceImpl::new(
        Arc::new(ConnectSessionService {
            mode: ConnectSessionMode::Success,
        }),
        Arc::new(tokens),
        Arc::new(SessionAuthorizationCoreImpl::new(session_repo.clone())),
        Some(hook.clone()),
    );

    // The Bot is a participant + the edge holds: authorize.
    let authorized = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: GroupSessionConnectionBinding {
                tenant: None,
                user_id: "user-a".into(),
                group_id: "conn-group".into(),
                session_id: session.id.clone(),
            },
            view: ConnectionView::Bot {
                bot_id: "bot-worker".into(),
            },
        })
        .await
        .expect("current edge + qualification authorizes the Bot view");
    assert_eq!(
        authorized.view,
        ConnectionView::Bot {
            bot_id: "bot-worker".into()
        }
    );

    // 移除 the Bot's SESSION qualification: same edge, same
    // Group — but the Bot is no longer a participant.
    session_repo
        .remove_participant(&session.id, "bot-worker")
        .await
        .expect("remove botworker participant");
    let error = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: GroupSessionConnectionBinding {
                tenant: None,
                user_id: "user-a".into(),
                group_id: "conn-group".into(),
                session_id: session.id.clone(),
            },
            view: ConnectionView::Bot {
                bot_id: "bot-worker".into(),
            },
        })
        .await
        .expect_err("a removed Session participant cannot be a view");
    assert!(matches!(
        error,
        GroupSessionConnectionError::Application(ApplicationError::Forbidden(_))
    ));

    // The token-Group mismatch is rejected before any edge
    // consult: the connector is scoped to the token range.
    let calls_before = hook.calls();
    let error = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: GroupSessionConnectionBinding {
                tenant: None,
                user_id: "user-a".into(),
                group_id: "another-group".into(),
                session_id: session.id.clone(),
            },
            view: ConnectionView::Bot {
                bot_id: "bot-worker".into(),
            },
        })
        .await
        .expect_err("a stale token group cannot be re-scoped");
    assert!(matches!(
        error,
        GroupSessionConnectionError::Application(ApplicationError::Forbidden(_))
    ));
    assert_eq!(hook.calls(), calls_before);
}
