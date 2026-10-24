//! Operation-boundary delivery admission (bot-authority spec
//! §8.2/§8.3 — 已准入输出不追溯).
//!
//! The matrix runs the REAL `DeliveryAdmissionServiceImpl` over
//! REAL Group/Session authorization snapshots (memory conformance
//! here) and a controllable authority hook. The contract under
//! test: ONE resolution call reads its resource snapshots ONCE for
//! the WHOLE candidate batch (that's what makes SQL cost track the
//! operation count, never the frame count), every candidate is
//! admitted or absent independently, and failures are either
//! per-candidate absences or one desensitized application error —
//! never a raw storage error, never a fake "deliver".

use std::collections::BTreeSet;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_session::DeliveryAdmissionServiceImpl;
use bcs_domain::{ActorKind, GroupStrategy, MessageAudience, MessageViewScope, ParticipantMode, ParticipantRole};
use bcs_domain::MessageVisibilityDomain;
use bcs_group::GroupCore;
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::application::v1::{
    ApplicationError, DeliveryAdmissionService, DeliveryScope, DeliveryVisibility,
    ProtectedDeliveryBinding,
};
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::types::bot_authority::{BotAccessRelation, BotRoleQuery};
use bcs_service_api::{
    Group as DomainGroup, Participant, ServiceError, ServiceResult, SessionKind,
};
use bcs_session::SessionAuthorizationCoreImpl;
use bcs_session_store::MemorySessionRepo;

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
impl BotAuthorityPort for TestHook {
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
// Fixture: real Group/Session authorization snapshots + real admission
// ---------------------------------------------------------------------------

struct Fixture {
    service: DeliveryAdmissionServiceImpl,
    hook: Arc<TestHook>,
    group_repo: Arc<dyn GroupRepoPort>,
    session_repo: Arc<dyn SessionRepoPort>,
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

/// Seed a ManagerWorker-shaped resource set: Group with Bot
/// driver/worker and a scoped Human, Session produced from the same
/// participants with its own scope.
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
                    human_participant("human_view", ParticipantRole::Observer, session_scope),
                ],
                created_by: Some("bot-driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    session.id.clone()
}

async fn admission_fixture() -> (Fixture, String, String) {
    let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let hook = Arc::new(TestHook::default());
    hook.grant("user-a", "bot-worker");
    let group_id = "delivery-group".to_string();
    let session_id = seed_resource_set(
        group_repo.as_ref(),
        session_repo.as_ref(),
        &group_id,
        GroupStrategy::ManagerWorker,
        MessageViewScope::Participant,
        MessageViewScope::Participant,
    )
    .await;
    let service = DeliveryAdmissionServiceImpl::new(
        Arc::new(GroupCore::with_repo(group_repo.clone())),
        Arc::new(SessionAuthorizationCoreImpl::new(session_repo.clone())),
        Some(hook.clone()),
    );
    (
        Fixture {
            service,
            hook,
            group_repo,
            session_repo,
        },
        group_id,
        session_id,
    )
}

fn binding(
    user_id: &str,
    group_id: &str,
    session_id: Option<&str>,
    view_actor_id: &str,
    conn: u64,
) -> ProtectedDeliveryBinding {
    ProtectedDeliveryBinding {
        tenant: Some("tenant-a".to_string()),
        user_id: user_id.to_string(),
        group_id: group_id.to_string(),
        session_id: session_id.map(str::to_string),
        view_actor_id: view_actor_id.to_string(),
        connection_instance_id: conn,
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
    Some(MessageAudience::directed([actor_id]).expect("valid directed audience"))
}

// ---------------------------------------------------------------------------
// Admission matrix
// ---------------------------------------------------------------------------

/// One batched call admits each qualified candidate with ITS OWN
/// frozen scope, and the permit's pure predicate applies the
/// visibility matrix without any further reads.
#[tokio::test]
async fn one_batch_admits_each_candidate_with_its_frozen_scope() {
    let (fixture, group_id, session_id) = admission_fixture().await;
    let scope = DeliveryScope {
        group_id: group_id.clone(),
        session_id: Some(session_id.clone()),
    };
    let human = binding("user-a", &group_id, Some(&session_id), "human_view", 7);
    let bot_view = binding("user-a", &group_id, Some(&session_id), "bot-worker", 8);

    let admitted = fixture
        .service
        .resolve_recipient_views(&scope, &[human.clone(), bot_view.clone()])
        .await
        .expect("batched admission");
    assert_eq!(admitted.len(), 2);
    let human_permit = admitted
        .iter()
        .find(|permit| permit.connection_instance_id == 7)
        .expect("human admitted");
    assert_eq!(human_permit.scope, MessageViewScope::Participant);
    assert!(
        !human_permit.allows(&visibility(MessageVisibilityDomain::ManagerWorker, full_only())),
        "FullOnly skipped by the pure predicate"
    );
    assert!(human_permit.allows(&visibility(
        MessageVisibilityDomain::ManagerWorker,
        Some(MessageAudience::Public)
    )));
    assert!(human_permit.allows(&visibility(
        MessageVisibilityDomain::ManagerWorker,
        directed("human_view")
    )));
    assert!(!human_permit.allows(&visibility(
        MessageVisibilityDomain::ManagerWorker,
        directed("other")
    )));
    assert!(human_permit.allows(&visibility(MessageVisibilityDomain::Chat, None)));

    let bot_permit = admitted
        .iter()
        .find(|permit| permit.connection_instance_id == 8)
        .expect("bot view admitted");
    assert_eq!(
        bot_permit.scope,
        MessageViewScope::Full,
        "Bot participants default to the Full view in this fixture"
    );
    assert_eq!(fixture.hook.calls(), 1, "one authority edge read per Bot-view candidate");
}

/// A revoked management edge drops ONLY that Bot-view candidate;
/// the other candidates of the same batch are unaffected.
#[tokio::test]
async fn a_revoked_edge_drops_only_that_candidate() {
    let (fixture, group_id, session_id) = admission_fixture().await;
    fixture.hook.revoke("user-a", "bot-worker");
    let scope = DeliveryScope {
        group_id: group_id.clone(),
        session_id: Some(session_id.clone()),
    };
    let admitted = fixture
        .service
        .resolve_recipient_views(
            &scope,
            &[
                binding("user-a", &group_id, Some(&session_id), "bot-worker", 7),
                binding("user-a", &group_id, Some(&session_id), "human_view", 8),
            ],
        )
        .await
        .expect("batch");
    assert_eq!(admitted.len(), 1);
    assert_eq!(admitted[0].connection_instance_id, 8);

    // handover (转交) renews the SAME user's edge: a later admission
    // sees it, earlier absentee stays absent for ITS operation.
    fixture.hook.grant("user-a", "bot-worker");
    let admitted_after = fixture
        .service
        .resolve_recipient_views(
            &scope,
            &[binding("user-a", &group_id, Some(&session_id), "bot-worker", 7)],
        )
        .await
        .expect("admission after handover");
    assert_eq!(admitted_after.len(), 1, "the renewed edge admits the next operation");
}

/// The Session participant list is the ONLY source for a
/// session-scoped admission: a Group seed never repairs a missing
/// Session participant, even with a live management edge.
#[tokio::test]
async fn a_session_scope_is_never_repaired_from_the_group_seed() {
    let (fixture, group_id, session_id) = admission_fixture().await;
    fixture
        .session_repo
        .remove_participant(&session_id, "bot-worker")
        .await
        .expect("remove session participant");
    let admitted = fixture
        .service
        .resolve_recipient_views(
            &DeliveryScope {
                group_id: group_id.clone(),
                session_id: Some(session_id.clone()),
            },
            &[binding("user-a", &group_id, Some(&session_id), "bot-worker", 7)],
        )
        .await
        .expect("resolution");
    assert!(admitted.is_empty(), "the live edge does not substitute the resource");
    assert_eq!(fixture.hook.calls(), 1, "the edge is still consulted (and passes)");
}

/// A session-scoped admission whose recorded Group disagrees with
/// the Session's current Group admits nobody (stale bound resource,
/// not a visibility miss).
#[tokio::test]
async fn a_stale_group_binding_admits_nobody() {
    let (fixture, _group_id, session_id) = admission_fixture().await;
    let admitted = fixture
        .service
        .resolve_recipient_views(
            &DeliveryScope {
                group_id: "stale-group".into(),
                session_id: Some(session_id.clone()),
            },
            &[binding("user-a", "stale-group", Some(&session_id), "human_view", 7)],
        )
        .await
        .expect("resolution");
    assert!(admitted.is_empty());
}

/// Group-scope admissions read the Group only; Session human moves
/// to Absent and loses the admission.
#[tokio::test]
async fn group_scope_and_absent_human_behaviors() {
    let (fixture, group_id, session_id) = admission_fixture().await;
    // Group scope: the Group's own scope applies (ManagerWorker →
    // unclassified legacy frames fail closed).
    let admitted = fixture
        .service
        .resolve_recipient_views(
            &DeliveryScope {
                group_id: group_id.clone(),
                session_id: None,
            },
            &[binding("user-a", &group_id, None, "human_view", 7)],
        )
        .await
        .expect("group resolution");
    let permit = admitted.first().expect("human admitted at group scope");
    assert!(!permit.allow_legacy_unclassified_chat);
    assert_eq!(fixture.hook.calls(), 0, "Human self views consult no authority edge");

    // Absent Human loses the admission entirely.
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
    let admitted = fixture
        .service
        .resolve_recipient_views(
            &DeliveryScope {
                group_id: group_id.clone(),
                session_id: Some(session_id.clone()),
            },
            &[binding("user-a", &group_id, Some(&session_id), "human_view", 7)],
        )
        .await
        .expect("resolution after absent");
    assert!(admitted.is_empty());
}

/// Deleted resources admit nobody; an unwired Bot-view hook or an
/// authority read failure is one desensitized application error for
/// the WHOLE call (never a partial silent downgrade of Bot views).
#[tokio::test]
async fn deleted_resources_and_failures_fail_closed_desensitized() {
    let (fixture, group_id, session_id) = admission_fixture().await;
    // Deleted Session: empty, no error.
    fixture.session_repo.delete(&session_id).await.expect("delete session");
    let admitted = fixture
        .service
        .resolve_recipient_views(
            &DeliveryScope {
                group_id: group_id.clone(),
                session_id: Some(session_id.clone()),
            },
            &[binding("user-a", &group_id, Some(&session_id), "bot-worker", 7)],
        )
        .await
        .expect("deleted session resolution");
    assert!(admitted.is_empty());

    // Authority read failure: Err, desensitized.
    let (fixture2, group_id2, session_id2) = admission_fixture().await;
    fixture2.hook.fail_reads();
    let error = fixture2
        .service
        .resolve_recipient_views(
            &DeliveryScope {
                group_id: group_id2.clone(),
                session_id: Some(session_id2.clone()),
            },
            &[binding("user-a", &group_id2, Some(&session_id2), "bot-worker", 7)],
        )
        .await
        .expect_err("authority read failure is an Err");
    assert!(matches!(error, ApplicationError::Internal(_)));
    let message = error.to_string();
    assert!(
        !message.contains("authority read failed"),
        "adapter sees only a desensitized message: {message}"
    );
    assert!(!message.to_lowercase().contains("sql"), "no storage detail escapes");

    // Unwired hook: a Bot view must never be admitted.
    let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let unwired_session = seed_resource_set(
        group_repo.as_ref(),
        session_repo.as_ref(),
        "unhooked",
        GroupStrategy::Chat,
        MessageViewScope::Full,
        MessageViewScope::Full,
    )
    .await;
    let unwired = DeliveryAdmissionServiceImpl::new(
        Arc::new(GroupCore::with_repo(group_repo.clone())),
        Arc::new(SessionAuthorizationCoreImpl::new(session_repo.clone())),
        None,
    );
    let error = unwired
        .resolve_recipient_views(
            &DeliveryScope {
                group_id: "unhooked".into(),
                session_id: Some(unwired_session.clone()),
            },
            &[binding("user-a", "unhooked", Some(&unwired_session), "bot-driver", 7)],
        )
        .await
        .expect_err("unwired hook fails closed");
    assert!(matches!(error, ApplicationError::Internal(_)));
}

// ---------------------------------------------------------------------------
// Cost contract: ONE resolution is a bounded read set REGARDLESS of
// the candidate count (frames never re-enter this path at all).
// ---------------------------------------------------------------------------

/// Counting Group snapshot core: the admission path may ONLY call
/// `read_authorization_snapshot`; every other core call panics.
struct CountingGroupCore {
    inner: Arc<dyn bcs_service_api::GroupCoreService>,
    group_reads: AtomicU64,
}

#[async_trait::async_trait]
impl bcs_service_api::GroupCoreService for CountingGroupCore {
    async fn read_authorization_snapshot(
        &self,
        group_id: &str,
    ) -> ServiceResult<Option<bcs_service_api::types::authorization_snapshot::GroupAuthorizationSnapshot>> {
        self.group_reads.fetch_add(1, Ordering::SeqCst);
        self.inner.read_authorization_snapshot(group_id).await
    }

    // Everything below is unreachable for admission resolution.
    async fn upsert(&self, _group: DomainGroup) -> ServiceResult<()> { unreachable!() }
    async fn finalize_provisioning(&self, _c: bcs_service_api::port::repo::FinalizeGroupProvisioning) -> ServiceResult<()> { unreachable!() }
    async fn mutate(&self, _c: bcs_service_api::core::GroupMutationCommand) -> ServiceResult<DomainGroup> { unreachable!() }
    async fn patch_mutable_fields(&self, _id: &str, _p: bcs_service_api::types::GroupMutableFieldsPatch) -> ServiceResult<()> { unreachable!() }
    async fn get(&self, _id: &str) -> Option<DomainGroup> { unreachable!() }
    async fn try_get(&self, _id: &str) -> ServiceResult<Option<DomainGroup>> { unreachable!() }
    async fn add_message(&self, _id: &str, _m: bcs_service_api::GroupMessage) -> ServiceResult<()> { unreachable!() }
    async fn add_participant(&self, _id: &str, _p: Participant) -> ServiceResult<()> { unreachable!() }
    async fn add_participant_with_visibility_guard(&self, _id: &str, _p: Participant, _b: bool) -> ServiceResult<()> { unreachable!() }
    async fn remove_participant(&self, _g: &str, _b: &str) -> ServiceResult<()> { unreachable!() }
    async fn update_participant_mode(&self, _g: &str, _a: &str, _m: ParticipantMode) -> ServiceResult<()> { unreachable!() }
    async fn update_participant_message_view_scope(&self, _g: &str, _a: &str, _s: MessageViewScope) -> ServiceResult<()> { unreachable!() }
    async fn insert_human_participant(&self, _g: &str, _h: &str, _m: ParticipantMode) -> ServiceResult<()> { unreachable!() }
    async fn update_label(&self, _id: &str, _l: Option<String>) -> ServiceResult<()> { unreachable!() }
    async fn update_status(&self, _id: &str, _s: bcs_service_api::GroupStatus) -> ServiceResult<()> { unreachable!() }
    async fn update_service_spec(&self, _id: &str, _s: Option<bcs_service_api::ServiceSpec>) -> ServiceResult<()> { unreachable!() }
    async fn terminate(&self, _id: &str, _c: &str) -> ServiceResult<DomainGroup> { unreachable!() }
    async fn delete(&self, _id: &str) -> ServiceResult<Option<DomainGroup>> { unreachable!() }
    async fn list(&self) -> Vec<DomainGroup> { unreachable!() }
    async fn list_paginated(&self, _o: u64, _l: u64) -> Vec<DomainGroup> { unreachable!() }
    async fn find_by_participant(&self, _b: &str) -> Vec<DomainGroup> { unreachable!() }
    async fn try_find_by_participant(&self, _b: &str) -> ServiceResult<Vec<DomainGroup>> { unreachable!() }
    async fn find_by_participant_filtered(&self, _b: &str, _k: Option<bcs_service_api::GroupKind>, _l: Option<&str>) -> Vec<DomainGroup> { unreachable!() }
    async fn count(&self) -> u64 { unreachable!() }
    async fn count_by_participant(&self, _b: &str) -> u64 { unreachable!() }
    async fn find_by_participant_paginated(&self, _b: &str, _o: u64, _l: u64) -> Vec<DomainGroup> { unreachable!() }
    async fn message_count(&self, _id: &str) -> ServiceResult<usize> { unreachable!() }
    async fn increment_message_count(&self, _id: &str) -> ServiceResult<()> { unreachable!() }
    async fn reset_message_count(&self, _id: &str) -> ServiceResult<()> { unreachable!() }
    async fn count_by_kind(&self, _k: Option<bcs_service_api::GroupKind>) -> u64 { unreachable!() }
    async fn list_paginated_by_kind(&self, _k: Option<bcs_service_api::GroupKind>, _o: u64, _l: u64) -> Vec<DomainGroup> { unreachable!() }
    async fn update_visibility(&self, _id: &str, _v: &str) -> ServiceResult<()> { unreachable!() }
    async fn count_filtered(&self, _k: Option<bcs_service_api::GroupKind>, _v: Option<&str>, _l: Option<&str>) -> u64 { unreachable!() }
    async fn list_paginated_filtered(&self, _o: u64, _l: u64, _k: Option<bcs_service_api::GroupKind>, _v: Option<&str>, _lb: Option<&str>) -> Vec<DomainGroup> { unreachable!() }
    async fn find_dm_by_pair_key(&self, _k: &str) -> Option<DomainGroup> { unreachable!() }
    async fn create_or_reuse_actor_dm_group(&self, _id: &str, _a: bcs_service_api::core::DmActorSpec, _b: bcs_service_api::core::DmActorSpec, _d: &str, _o: &str, _l: Option<String>, _c: Option<String>) -> ServiceResult<(DomainGroup, bool)> { unreachable!() }
    async fn create_or_reuse_actor_dm_group_with_record_status(&self, _id: &str, _a: bcs_service_api::core::DmActorSpec, _b: bcs_service_api::core::DmActorSpec, _d: &str, _o: &str, _l: Option<String>, _c: Option<String>, _r: &str) -> ServiceResult<(DomainGroup, bool)> { unreachable!() }
    async fn create_or_reuse_dm_group(&self, _id: &str, _d: &str, _a: &str, _b: &str, _l: Option<String>) -> ServiceResult<(DomainGroup, bool)> { unreachable!() }
    async fn read_human_notify_policy(&self, _g: &str) -> ServiceResult<Option<bcs_service_api::types::GroupHumanNotifyPolicy>> { unreachable!() }
}

#[tokio::test]
async fn one_resolution_reads_once_for_the_whole_batch() {
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
    hook.grant("user-b", "bot-worker");
    let counting = Arc::new(CountingGroupCore {
        inner: Arc::new(GroupCore::with_repo(group_repo)),
        group_reads: AtomicU64::new(0),
    });
    let service = DeliveryAdmissionServiceImpl::new(
        counting.clone(),
        Arc::new(SessionAuthorizationCoreImpl::new(session_repo)),
        Some(hook.clone()),
    );
    assert_eq!(counting.group_reads.load(Ordering::SeqCst), 0);

    // EIGHT candidates in ONE call: a Human, three distinct Bot
    // views, duplicates of both shapes.
    let scope = DeliveryScope {
        group_id: "cost-group".into(),
        session_id: Some(session_id.clone()),
    };
    let candidates = vec![
        binding("user-a", "cost-group", Some(&session_id), "human_view", 1),
        binding("user-a", "cost-group", Some(&session_id), "bot-worker", 2),
        binding("user-a", "cost-group", Some(&session_id), "bot-worker", 3),
        binding("user-a", "cost-group", Some(&session_id), "bot-driver", 4),
        binding("user-b", "cost-group", Some(&session_id), "bot-driver", 5),
        binding("user-a", "cost-group", Some(&session_id), "human_view", 6),
        binding("user-b", "cost-group", Some(&session_id), "bot-worker", 7),
        binding("user-a", "cost-group", Some(&session_id), "human_view", 8),
    ];
    let admitted = service
        .resolve_recipient_views(&scope, &candidates)
        .await
        .expect("batched resolution");

    // The batch NEVER multiplies its snapshot reads: 1 Session + 1
    // Group for any candidate count. Distinct Bot views cost one
    // authority edge read each (and deduplicate per (user, bot)).
    assert_eq!(
        counting.group_reads.load(Ordering::SeqCst),
        1,
        "the whole batch shares ONE Group snapshot read"
    );
    // distinct (user, bot) pairs: (a,worker), (a,driver), (b,driver), (b,worker)
    assert_eq!(hook.calls(), 4, "authority reads dedupe per distinct Bot view");
    // bot-driver has no edge for either user → both dropped; the rest
    // admitted.
    let admitted_ids: BTreeSet<u64> = admitted
        .iter()
        .map(|recipient| recipient.connection_instance_id)
        .collect();
    assert_eq!(admitted_ids, BTreeSet::from([1, 2, 3, 6, 7, 8]));
}
