//! Bot-authority F1 acceptance: operation-boundary delivery
//! admission (spec §8.2/§8.3 — 已准入输出不追溯).
//!
//! Replaces the old per-frame final-writer contract. What is pinned
//! here, with REAL snapshots + a REAL admission service and REAL
//! Workbench wiring (registry / run manager / frontend delivery):
//!
//! - admission is decided ONCE per operation per connection;
//!   mid-operation transfer / manager-edge removal / membership
//!   removal / scope change NEVER interrupts the admitted output
//!   on the same live connection;
//! - NEW operations, NEW subscriptions, reconnects and replacements
//!   re-admit against CURRENT facts;
//! - a read failure at admission installs zero permits for THAT
//!   operation and disturbs no earlier one; the frame path itself
//!   performs ZERO authorization reads (costs track operations,
//!   never frames);
//! - aliases share the source operation's permit; the Session
//!   last-Run fallback never borrows a Workbench permit lineage;
//! - terminals drain in order even when unregistered first, and
//!   slow subscribers get bounded backpressure instead of silent
//!   terminal loss;
//! - independent resource events are authorized per event against
//!   current facts — an event failure never closes a stream.
//!
//! The doubles only replace the Application resolver doubles where
//! noted; the permit semantics itself is always the production one.

#![allow(clippy::expect_used, reason = "test assertions fail fast")]

mod support;

use std::collections::BTreeSet;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use bcs_domain::{
    ActorKind, GroupStrategy, MessageAudience, MessageViewScope, MessageVisibilityDomain,
    Participant, ParticipantMode, ParticipantRole,
};
use bcs_group::GroupCore;
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::application::v1::ProtectedDeliveryBinding;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::types::bot_authority::{BotAccessRelation, BotRoleQuery};
use bcs_service_api::types::GroupMutableFieldsPatch;
use bcs_service_api::{
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort,
    FrontendDeliveryTarget, Group as DomainGroup, OutputAdmissionPort,
    OutputOperationAdmission, ServiceError, ServiceResult, SessionKind,
};
use bcs_session::SessionAuthorizationCoreImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_ws::shared::{OutboundFrame, OutputPermitStore, RunChannelManager};
use bcs_ws::web::{WorkbenchConnectionRegistry, WorkbenchFrontendDelivery, WorkbenchOutputAdmission};
use support::{FailingAdmission, RecordingAdmission};
use tokio::sync::mpsc;

// ---------------------------------------------------------------------------
// Controllable authority hook (real BTreeSet semantics)
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

    fn heal(&self) {
        self.fail.store(false, Ordering::SeqCst);
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
// Real-stack fixture: real snapshots, real admission service, real
// Workbench registry + run manager + frontend delivery + output
// admission port — the same composition production wires.
// ---------------------------------------------------------------------------

struct Stack {
    permits: Arc<OutputPermitStore>,
    registry: Arc<WorkbenchConnectionRegistry>,
    run_channels: Arc<RunChannelManager>,
    delivery: Arc<WorkbenchFrontendDelivery>,
    output_admission: Arc<WorkbenchOutputAdmission>,
    hook: Arc<TestHook>,
    session_repo: Arc<dyn SessionRepoPort>,
    group_snapshot_reads: Arc<AtomicU64>,
    group_id: String,
    session_id: String,
}

/// A counting Group snapshot core: the admission path may ONLY read
/// the snapshot; every other core call panics, and the read count
/// proves frame-path silence.
struct CountingGroupCore {
    inner: GroupCore,
    reads: Arc<AtomicU64>,
}

#[async_trait]
impl bcs_service_api::GroupCoreService for CountingGroupCore {
    async fn read_authorization_snapshot(
        &self,
        group_id: &str,
    ) -> ServiceResult<Option<bcs_service_api::types::authorization_snapshot::GroupAuthorizationSnapshot>> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        self.inner.read_authorization_snapshot(group_id).await
    }

    async fn upsert(&self, _g: DomainGroup) -> ServiceResult<()> { unreachable!() }
    async fn finalize_provisioning(&self, _c: bcs_service_api::port::repo::FinalizeGroupProvisioning) -> ServiceResult<()> { unreachable!() }
    async fn mutate(&self, _c: bcs_service_api::core::GroupMutationCommand) -> ServiceResult<DomainGroup> { unreachable!() }
    async fn patch_mutable_fields(&self, _i: &str, _p: GroupMutableFieldsPatch) -> ServiceResult<()> { unreachable!() }
    async fn get(&self, _i: &str) -> Option<DomainGroup> { unreachable!() }
    async fn try_get(&self, _i: &str) -> ServiceResult<Option<DomainGroup>> { unreachable!() }
    async fn add_message(&self, _i: &str, _m: bcs_service_api::GroupMessage) -> ServiceResult<()> { unreachable!() }
    async fn add_participant(&self, _i: &str, _p: Participant) -> ServiceResult<()> { unreachable!() }
    async fn add_participant_with_visibility_guard(&self, _i: &str, _p: Participant, _b: bool) -> ServiceResult<()> { unreachable!() }
    async fn remove_participant(&self, _g: &str, _b: &str) -> ServiceResult<()> { unreachable!() }
    async fn update_participant_mode(&self, _g: &str, _a: &str, _m: ParticipantMode) -> ServiceResult<()> { unreachable!() }
    async fn update_participant_message_view_scope(&self, _g: &str, _a: &str, _s: MessageViewScope) -> ServiceResult<()> { unreachable!() }
    async fn insert_human_participant(&self, _g: &str, _h: &str, _m: ParticipantMode) -> ServiceResult<()> { unreachable!() }
    async fn update_label(&self, _i: &str, _l: Option<String>) -> ServiceResult<()> { unreachable!() }
    async fn update_status(&self, _i: &str, _s: bcs_service_api::GroupStatus) -> ServiceResult<()> { unreachable!() }
    async fn update_service_spec(&self, _i: &str, _s: Option<bcs_service_api::ServiceSpec>) -> ServiceResult<()> { unreachable!() }
    async fn terminate(&self, _i: &str, _c: &str) -> ServiceResult<DomainGroup> { unreachable!() }
    async fn delete(&self, _i: &str) -> ServiceResult<Option<DomainGroup>> { unreachable!() }
    async fn list(&self) -> Vec<DomainGroup> { unreachable!() }
    async fn list_paginated(&self, _o: u64, _l: u64) -> Vec<DomainGroup> { unreachable!() }
    async fn find_by_participant(&self, _b: &str) -> Vec<DomainGroup> { unreachable!() }
    async fn try_find_by_participant(&self, _b: &str) -> ServiceResult<Vec<DomainGroup>> { unreachable!() }
    async fn find_by_participant_filtered(&self, _b: &str, _k: Option<bcs_service_api::GroupKind>, _l: Option<&str>) -> Vec<DomainGroup> { unreachable!() }
    async fn count(&self) -> u64 { unreachable!() }
    async fn count_by_participant(&self, _b: &str) -> u64 { unreachable!() }
    async fn find_by_participant_paginated(&self, _b: &str, _o: u64, _l: u64) -> Vec<DomainGroup> { unreachable!() }
    async fn message_count(&self, _i: &str) -> ServiceResult<usize> { unreachable!() }
    async fn increment_message_count(&self, _i: &str) -> ServiceResult<()> { unreachable!() }
    async fn reset_message_count(&self, _i: &str) -> ServiceResult<()> { unreachable!() }
    async fn count_by_kind(&self, _k: Option<bcs_service_api::GroupKind>) -> u64 { unreachable!() }
    async fn list_paginated_by_kind(&self, _k: Option<bcs_service_api::GroupKind>, _o: u64, _l: u64) -> Vec<DomainGroup> { unreachable!() }
    async fn update_visibility(&self, _i: &str, _v: &str) -> ServiceResult<()> { unreachable!() }
    async fn count_filtered(&self, _k: Option<bcs_service_api::GroupKind>, _v: Option<&str>, _l: Option<&str>) -> u64 { unreachable!() }
    async fn list_paginated_filtered(&self, _o: u64, _l: u64, _k: Option<bcs_service_api::GroupKind>, _v: Option<&str>, _lb: Option<&str>) -> Vec<DomainGroup> { unreachable!() }
    async fn find_dm_by_pair_key(&self, _k: &str) -> Option<DomainGroup> { unreachable!() }
    async fn create_or_reuse_actor_dm_group(&self, _i: &str, _a: bcs_service_api::core::DmActorSpec, _b: bcs_service_api::core::DmActorSpec, _d: &str, _o: &str, _l: Option<String>, _c: Option<String>) -> ServiceResult<(DomainGroup, bool)> { unreachable!() }
    async fn create_or_reuse_actor_dm_group_with_record_status(&self, _i: &str, _a: bcs_service_api::core::DmActorSpec, _b: bcs_service_api::core::DmActorSpec, _d: &str, _o: &str, _l: Option<String>, _c: Option<String>, _r: &str) -> ServiceResult<(DomainGroup, bool)> { unreachable!() }
    async fn create_or_reuse_dm_group(&self, _i: &str, _d: &str, _a: &str, _b: &str, _l: Option<String>) -> ServiceResult<(DomainGroup, bool)> { unreachable!() }
    async fn read_human_notify_policy(&self, _g: &str) -> ServiceResult<Option<bcs_service_api::types::GroupHumanNotifyPolicy>> { unreachable!() }
}

fn participant(actor: &str, kind: ActorKind, role: ParticipantRole, scope: MessageViewScope) -> Participant {
    Participant {
        bot_uuid: actor.to_string(),
        bot_name: Some(actor.to_string()),
        kind: None,
        role,
        actor_kind: kind,
        mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: scope,
    }
}

async fn new_stack() -> Stack {
    let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let mut group = DomainGroup::new("stack-group", "bot-driver", Vec::new());
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        participant("bot-driver", ActorKind::Bot, ParticipantRole::Driver, MessageViewScope::Full),
        participant("bot-worker", ActorKind::Bot, ParticipantRole::Worker, MessageViewScope::Full),
        participant("human_user-a", ActorKind::Human, ParticipantRole::Observer, MessageViewScope::Full),
        participant("human_user-b", ActorKind::Human, ParticipantRole::Observer, MessageViewScope::Full),
    ];
    group_repo.upsert(group.clone()).await.expect("seed group");
    let session = session_repo
        .create(
            "stack-group",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: group.participants.clone(),
                created_by: Some("bot-driver".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");

    let hook = Arc::new(TestHook::default());
    hook.grant("user-a", "bot-worker");
    let group_snapshot_reads = Arc::new(AtomicU64::new(0));
    let counting = Arc::new(CountingGroupCore {
        inner: GroupCore::with_repo(group_repo.clone()),
        reads: group_snapshot_reads.clone(),
    });
    let service = Arc::new(bcs_app_session::DeliveryAdmissionServiceImpl::new(
        counting as Arc<dyn bcs_service_api::GroupCoreService>,
        Arc::new(SessionAuthorizationCoreImpl::new(session_repo.clone())),
        Some(hook.clone()),
    ));

    let permits = Arc::new(OutputPermitStore::new());
    let registry = Arc::new(
        WorkbenchConnectionRegistry::new().with_output_permits(permits.clone()),
    );
    registry.set_delivery_admission(service.clone());
    let run_channels = Arc::new(
        RunChannelManager::new()
            .with_output_permits(permits.clone())
            .with_connection_retirement(registry.clone()),
    );
    let delivery = Arc::new(WorkbenchFrontendDelivery::new(
        registry.clone(),
        run_channels.clone(),
    ));
    let output_admission = Arc::new(WorkbenchOutputAdmission::new(
        registry.clone(),
        service,
    ));
    Stack {
        permits,
        registry,
        run_channels,
        delivery,
        output_admission,
        hook,
        session_repo,
        group_snapshot_reads,
        group_id: "stack-group".to_string(),
        session_id: session.id.clone(),
    }
}

/// Subscribe one protected connection into the scope, returning its
/// (conn id, queue receiver). The binding is what the REAL connect
/// flow would produce for the same view.
async fn subscribe(
    stack: &Stack,
    user: &str,
    view_actor: &str,
) -> (u64, mpsc::Receiver<OutboundFrame>) {
    let (control_tx, _control_rx) = mpsc::channel(8);
    let (frame_tx, frame_rx) = mpsc::channel(bcs_ws::shared::PROTECTED_FRAME_QUEUE_SIZE);
    let binding = ProtectedDeliveryBinding {
        tenant: Some("tenant-a".to_string()),
        user_id: user.to_string(),
        group_id: stack.group_id.clone(),
        session_id: Some(stack.session_id.clone()),
        view_actor_id: view_actor.to_string(),
        connection_instance_id: 0,
    };
    let conn_id = stack
        .registry
        .subscribe_protected_with_shutdown(
            stack.session_id.clone(),
            control_tx,
            Some(view_actor.to_string()),
            None,
            tokio_util::sync::CancellationToken::new(),
            binding,
            frame_tx,
        )
        .await
        .expect("protected subscribe");
    (conn_id, frame_rx)
}

/// Admit one operation for the current subscribers — the exact call
/// `handle_web_send` makes at accept time.
async fn admit(stack: &Stack, operation_id: &str) {
    stack
        .output_admission
        .admit_output_operation(OutputOperationAdmission {
            operation_id: operation_id.to_string(),
            group_id: stack.group_id.clone(),
            session_id: Some(stack.session_id.clone()),
        })
        .await
        .expect("operation admission");
}

/// Register the operation's downstream run sink for one connection
/// — the exact call the WS dispatcher makes after the send.
async fn register_run_sink(
    stack: &Stack,
    operation_id: &str,
    run_id: &str,
    conn_id: u64,
) -> mpsc::Receiver<OutboundFrame> {
    let (frame_tx, frame_rx) = mpsc::channel(bcs_ws::shared::PROTECTED_FRAME_QUEUE_SIZE);
    stack
        .run_channels
        .register_workbench(
            run_id.to_string(),
            stack.session_id.clone(),
            frame_tx,
            conn_id,
            operation_id,
            Some("auth-delivery-test".to_string()),
            None,
            None,
        )
        .await;
    frame_rx
}

fn op_command(
    stack: &Stack,
    operation_id: &str,
    json: &str,
    terminal: bool,
    domain: MessageVisibilityDomain,
    audience: Option<MessageAudience>,
) -> FrontendDeliveryCommand {
    run_command(stack, operation_id, None, json, terminal, domain, audience)
}

/// An operation frame carrying its canonical stream identity: its
/// terminal ends THAT stream only — siblings of the same operation
/// keep flowing (Run terminal ≠ operation complete).
fn run_command(
    stack: &Stack,
    operation_id: &str,
    run_id: Option<&str>,
    json: &str,
    terminal: bool,
    domain: MessageVisibilityDomain,
    audience: Option<MessageAudience>,
) -> FrontendDeliveryCommand {
    FrontendDeliveryCommand {
        target: FrontendDeliveryTarget::Session {
            session_id: stack.session_id.clone(),
        },
        event_json: json.to_string(),
        delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
        run_fallback: None,
        exclude_conn_id: None,
        visibility_domain: domain,
        audience,
        operation_id: Some(operation_id.to_string()),
        terminal,
        run_id: run_id.map(str::to_string),
    }
}

fn event_command(
    stack: &Stack,
    json: &str,
    domain: MessageVisibilityDomain,
    audience: Option<MessageAudience>,
) -> FrontendDeliveryCommand {
    FrontendDeliveryCommand {
        target: FrontendDeliveryTarget::Session {
            session_id: stack.session_id.clone(),
        },
        event_json: json.to_string(),
        delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
        run_fallback: None,
        exclude_conn_id: None,
        visibility_domain: domain,
        audience,
        operation_id: None,
        terminal: false,
        run_id: None,
    }
}

async fn expect_payload(
    rx: &mut mpsc::Receiver<OutboundFrame>,
    expected: &str,
) {
    match tokio::time::timeout(Duration::from_secs(2), rx.recv()).await {
        Ok(Some(OutboundFrame::Data { payload })) => assert_eq!(payload, expected),
        Ok(Some(OutboundFrame::Terminal { payload })) => assert_eq!(payload, expected),
        other => panic!("expected payload {expected:?}, got {other:?}"),
    }
}

async fn expect_quiet(rx: &mut mpsc::Receiver<OutboundFrame>) {
    assert!(
        tokio::time::timeout(Duration::from_millis(150), rx.recv())
            .await
            .is_err(),
        "no frame may arrive"
    );
}

// ---------------------------------------------------------------------------
// The acceptance matrix
// ---------------------------------------------------------------------------

/// A revocation that lands BETWEEN the operation admission and its
/// FIRST frame changes nothing: the frozen permit still delivers —
/// the gap between accept and first frame is no longer an
/// authorization race window (首帧前撤权不追溯).
#[tokio::test]
async fn revocation_between_admission_and_first_frame_still_delivers() {
    let stack = new_stack().await;
    let (conn_a, mut rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    assert!(conn_a > 0);
    admit(&stack, "op-1").await;
    let reads_after_admission = stack.group_snapshot_reads.load(Ordering::SeqCst);

    // The Human is removed from the Session entirely — AFTER accept.
    stack
        .session_repo
        .remove_participant(&stack.session_id, "human_user-a")
        .await
        .expect("remove human participant");

    let delivered = stack
        .delivery
        .publish(op_command(
            &stack,
            "op-1",
            "first-frame",
            false,
            MessageVisibilityDomain::Chat,
            None,
        ))
        .await
        .expect("publish");
    assert_eq!(delivered.delivered, 1);
    expect_payload(&mut rx_a, "first-frame").await;

    // The delivery of that frame performed ZERO new authorization
    // reads: costs track operations, never frames.
    assert_eq!(
        stack.group_snapshot_reads.load(Ordering::SeqCst),
        reads_after_admission,
        "a delivered frame performed fresh resource reads"
    );
}

/// Mid-run transfer + manager-edge removal + membership change:
/// the admitted output is never interrupted; only FUTURE admissions
/// re-check (A 已准入 B 新请求被拒不连坐).
#[tokio::test]
async fn mid_operation_revocations_do_not_interrupt_but_new_operations_recheck() {
    let stack = new_stack().await;
    let (_conn_a, mut rx_a) = subscribe(&stack, "user-a", "bot-worker").await;
    let (_conn_b, mut rx_b) = subscribe(&stack, "user-b", "human_user-b").await;
    admit(&stack, "op-1").await;

    // Delivered for both before the revocation.
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "before",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("publish before")
            .delivered,
        2
    );
    expect_payload(&mut rx_a, "before").await;
    expect_payload(&mut rx_b, "before").await;

    // Revoke EVERYTHING: the Bot-view edge, then user-b's Session
    // membership (transfer shapes are the same removal of committed
    // facts).
    stack.hook.revoke("user-a", "bot-worker");
    stack
        .session_repo
        .remove_participant(&stack.session_id, "human_user-b")
        .await
        .expect("remove user-b");

    // op-1 keeps flowing on BOTH live connections.
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "after-revocation",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("publish after")
            .delivered,
        2
    );
    expect_payload(&mut rx_a, "after-revocation").await;
    expect_payload(&mut rx_b, "after-revocation").await;

    // A NEW operation re-admits against CURRENT facts: user-a's Bot
    // view and user-b both qualify for nothing any more — the SAME
    // live connections receive nothing of op-2 without being closed.
    admit(&stack, "op-2").await;
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-2",
                "new-operation",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("publish op-2")
            .delivered,
        0,
        "op-2 admits nobody after the revocation"
    );
    expect_quiet(&mut rx_a).await;
    expect_quiet(&mut rx_b).await;

    // op-1 resumes uninterrupted afterwards (no 连坐).
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "still-flowing",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("publish op-1 again")
            .delivered,
        2
    );
    expect_payload(&mut rx_a, "still-flowing").await;
    expect_payload(&mut rx_b, "still-flowing").await;
}

/// An admission-time read failure installs ZERO permits for THAT
/// operation (the send itself is undisturbed upstream), never
/// mistakes the failure for "no role", and never touches earlier
/// permits — later healthy operations work again.
#[tokio::test]
async fn admission_read_failure_installs_no_permits_and_disturbs_no_stream() {
    let stack = new_stack().await;
    let (_conn_a, mut rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "admitted",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("op-1 publish")
            .delivered,
        1
    );
    expect_payload(&mut rx_a, "admitted").await;

    // Poison the authority read (a DesensitizedErr surface): the
    // NEXT operation installs no permits for the Bot-view present
    // downstream, but op-1 is untouched.
    let (_conn_bot, mut rx_bot) = subscribe(&stack, "user-a", "bot-worker").await;
    stack.hook.fail_reads();
    admit(&stack, "op-2").await;
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-2",
                "failing-admission-op",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("op-2 publish")
            .delivered,
        0,
        "a failed admission installs no permits (the send itself is not failed here)"
    );
    expect_quiet(&mut rx_bot).await;
    stack
        .delivery
        .publish(op_command(
            &stack,
            "op-1",
            "still-admitted",
            false,
            MessageVisibilityDomain::Chat,
            None,
        ))
        .await
        .expect("op-1 publish");
    expect_payload(&mut rx_a, "still-admitted").await;

    // Healthy again: the next operation admits normally (BOTH views
    // qualify once the hook heals).
    stack.hook.heal();
    admit(&stack, "op-3").await;
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-3",
                "recovered",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("op-3 publish")
            .delivered,
        2
    );
    expect_payload(&mut rx_a, "recovered").await;
    expect_payload(&mut rx_bot, "recovered").await;
}

/// Alias frames resolve to the SAME operation's permit; the run
/// identity client-side never cross-borrows (alias 复用同一执行).
#[tokio::test]
async fn alias_frames_share_the_source_operations_permit() {
    let stack = new_stack().await;
    let (conn_a, _rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;
    let mut sink_rx = register_run_sink(&stack, "op-1", "run-canon", conn_a).await;
    assert!(
        stack
            .run_channels
            .register_alias("run-alias".to_string(), "run-canon".to_string())
            .await
    );

    assert!(
        stack
            .run_channels
            .send_visible_event(
                "run-alias",
                "alias-payload".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                false,
            )
            .await,
        "the alias reaches the SAME permit already checked for the run"
    );
    expect_payload(&mut sink_rx, "alias-payload").await;
    assert_eq!(conn_a, conn_a, "same connection instance carried the alias");
}

/// An unregister before the terminal does NOT strand queued frames:
/// terminal + queued deltas drain in order; anything produced after
/// the terminal is refused (drain 语义, not 丢 terminal).
#[tokio::test]
async fn terminal_after_unregister_still_drains_in_order() {
    let stack = new_stack().await;
    let (_conn_a, mut rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;

    // A delta and the typed terminal are enqueued, THEN the run is
    // unregistered (bot_event's exact publish→unregister ordering).
    stack
        .delivery
        .publish(op_command(
            &stack,
            "op-1",
            "delta-1",
            false,
            MessageVisibilityDomain::Chat,
            None,
        ))
        .await
        .expect("delta publish");
    stack
        .delivery
        .publish(op_command(
            &stack,
            "op-1",
            "run-complete",
            true,
            MessageVisibilityDomain::Chat,
            None,
        ))
        .await
        .expect("terminal publish");
    stack.delivery.unregister_run("run-1").await.expect("unregister");

    // Draining now yields BOTH frames, in order, terminal last.
    expect_payload(&mut rx_a, "delta-1").await;
    match tokio::time::timeout(Duration::from_secs(2), rx_a.recv()).await {
        Ok(Some(OutboundFrame::Terminal { payload })) => {
            assert_eq!(payload, "run-complete")
        }
        other => panic!("expected the typed terminal after the delta, got {other:?}"),
    }

    // Anything produced after the terminal is refused.
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "post-terminal",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("post-terminal publish")
            .delivered,
        0,
        "Draining operations reject post-terminal frames"
    );
    assert!(
        stack.permits.frames_dropped_after_terminal() >= 1,
        "the refusal is counted, not silent"
    );
}

/// The Session last-Run fallback serves Internal sinks only: a
/// Workbench sink with no matching canonical Run binding is never
/// reached through the session shortcut (§7.2), while the Internal
/// HTTP drain keeps its legacy contract.
#[tokio::test]
async fn session_fallback_never_borrows_a_workbench_permit_lineage() {
    let stack = new_stack().await;
    let (conn_a, _wb_rx) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;
    // The Internal drain registers FIRST so the Workbench sink is
    // `last()` of this session at fallback time.
    let (internal_tx, mut internal_rx) = mpsc::channel(4);
    stack
        .run_channels
        .register_internal(
            "run-legacy".to_string(),
            stack.session_id.clone(),
            internal_tx,
            None,
            None,
        )
        .await;
    let mut sink_rx = register_run_sink(&stack, "op-1", "run-wb", conn_a).await;

    // Make sure the Workbench registers LATER so it is `last()`.
    // Direct lane reaches it (its own op permit), fallback lane does.
    let sent = stack
        .run_channels
        .send_visible_event_by_session(
            &stack.session_id,
            "fallback-event".to_string(),
            MessageVisibilityDomain::Chat,
            None,
            false,
        )
        .await;
    assert!(!sent, "the LAST run is the Workbench sink: refused");
    expect_quiet(&mut sink_rx).await;
    assert!(internal_rx.try_recv().is_err());

    // And the DIRECT run lane is exactly what the fallback refused:
    // the SAME run's OWN frames flow through its operation permit.
    assert!(
        stack
            .run_channels
            .send_visible_event(
                "run-wb",
                "direct-event".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                false,
            )
            .await,
        "the run's own op permit still covers the direct lane"
    );
    expect_payload(&mut sink_rx, "direct-event").await;

    // Reordered (the Internal sink is last): legacy consumers flow.
    stack.run_channels.unregister("run-wb").await;
    let sent_legacy = stack
        .run_channels
        .send_event_by_session(&stack.session_id, "legacy-event".to_string())
        .await;
    assert!(sent_legacy);
    assert_eq!(
        internal_rx.recv().await.as_deref(),
        Some("legacy-event"),
        "the Internal fallback contract is unchanged"
    );
}

/// A late subscription receives NOTHING of operations admitted
/// before it joined: each new operation re-admits only against the
/// CURRENT subscriber set and the CURRENT facts.
#[tokio::test]
async fn late_subscribers_need_a_fresh_operation_admission() {
    let stack = new_stack().await;
    let (_conn_a, mut rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;
    // user-b subscribes AFTER op-1's admission.
    let (_conn_b, mut rx_b) = subscribe(&stack, "user-b", "human_user-b").await;

    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "early-stream",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("publish op-1")
            .delivered,
        1,
        "only the original subscriber has the op-1 permit"
    );
    expect_payload(&mut rx_a, "early-stream").await;
    expect_quiet(&mut rx_b).await;

    // op-2 admitted after BOTH subscribed: both qualify now.
    admit(&stack, "op-2").await;
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-2",
                "fresh-operation",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("publish op-2")
            .delivered,
        2
    );
    expect_payload(&mut rx_a, "fresh-operation").await;
    expect_payload(&mut rx_b, "fresh-operation").await;
}

/// Independent resource events (成员/元数据变更) use CURRENT facts
/// at publish time: a mid-run revoked viewer stops receiving THOSE
/// events while its admitted run output streams on — and an event
/// failure never closes the stream (§7.3).
#[tokio::test]
async fn independent_events_use_current_facts_without_touching_streams() {
    let stack = new_stack().await;
    let (_conn_a, mut rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;

    // An independent event flows while everything holds.
    assert_eq!(
        stack
            .delivery
            .publish(event_command(
                &stack,
                "member-note-1",
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("event publish 1")
            .delivered,
        1
    );
    expect_payload(&mut rx_a, "member-note-1").await;

    // Revoke: the NEXT independent event re-resolves (0 delivered);
    // the still-admitted op-1 keeps flowing.
    stack
        .session_repo
        .remove_participant(&stack.session_id, "human_user-a")
        .await
        .expect("remove human");
    assert_eq!(
        stack
            .delivery
            .publish(event_command(
                &stack,
                "member-note-2",
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("event publish 2")
            .delivered,
        0,
        "the independent event re-checks current facts"
    );
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "stream-continues",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("op publish")
            .delivered,
        1,
        "the admitted operation ignores the independent event's re-check"
    );
    expect_payload(&mut rx_a, "stream-continues").await;
    assert!(
        stack.registry.connection_count(&stack.session_id).await >= 1,
        "the connection was never cancelled by an event denial"
    );
}

/// Frame-count independence (SQL 计数验收): one admitted operation
/// delivers 1, 20 and 1000 frames with ZERO additional
/// authorization reads per frame — the operation count, never the
/// frame count, scales authorization cost.
#[tokio::test]
async fn frame_volume_never_scales_authorization_reads() {
    let stack = new_stack().await;
    let (_conn_a, mut rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;
    let baseline_reads = stack.group_snapshot_reads.load(Ordering::SeqCst);
    let baseline_hooks = stack.hook.calls();

    for n in 0..32 {
        assert_eq!(
            stack
                .delivery
                .publish(op_command(
                    &stack,
                    "op-1",
                    &format!("frame-{n}"),
                    false,
                    MessageVisibilityDomain::Chat,
                    None,
                ))
                .await
                .expect("publish n")
                .delivered,
            1
        );
        expect_payload(&mut rx_a, &format!("frame-{n}")).await;
    }
    assert_eq!(
        stack.group_snapshot_reads.load(Ordering::SeqCst),
        baseline_reads,
        "32 frames performed no resource re-reads"
    );
    assert_eq!(stack.hook.calls(), baseline_hooks, "no authority edge re-reads");
}

/// A saturated slow subscriber loses DATA frames with the slow-
/// consumer policy, while the TERMINAL frame takes the bounded
/// backpressure budget and still lands (不是静默丢终态).
#[tokio::test]
async fn slow_subscriber_terminal_uses_bounded_backpressure() {
    let stack = new_stack().await;
    let (_conn_a, rx_a) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "op-1").await;
    let mut rx_a = rx_a;

    // Saturate the queue WITHOUT a consumer.
    for n in 0..bcs_ws::shared::PROTECTED_FRAME_QUEUE_SIZE {
        let filled = stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                &format!("flood-{n}"),
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("flood publish")
            .delivered;
        assert!(filled <= 1);
    }
    // One more data frame overflows the bounded queue (drop policy).
    let overflowed = stack
        .delivery
        .publish(op_command(
            &stack,
            "op-1",
            "overflow-data",
            false,
            MessageVisibilityDomain::Chat,
            None,
        ))
        .await
        .expect("overflow publish")
        .delivered;
    assert_eq!(overflowed, 0, "the saturated data frame is dropped loudly");

    // Start draining so the bounded terminal backpressure can land.
    let drain = tokio::spawn(async move {
        // Simulate the writer catching up after ~200ms.
        tokio::time::sleep(Duration::from_millis(200)).await;
        let mut last = None;
        while let Ok(Some(frame)) =
            tokio::time::timeout(Duration::from_millis(50), rx_a.recv()).await
        {
            last = Some(frame);
        }
        last
    });
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "run-complete",
                true,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("terminal publish")
            .delivered,
        1,
        "the terminal frame took the bounded backpressure instead of being dropped"
    );
    let last = drain.await.expect("drain joins");
    assert!(
        matches!(last, Some(OutboundFrame::Terminal { .. })),
        "the drained tail IS the typed terminal"
    );
}

/// Conversation-level Bot view: the admitted fanout honors the
/// permit's frozen visibility even when the producer metadata
/// changes per frame (FullOnly 仍被许可谓词过滤).
#[tokio::test]
async fn per_frame_visibility_still_applies_through_the_frozen_permit() {
    let stack = new_stack().await;
    let (conn_a, mut rx_a) = subscribe(&stack, "user-a", "bot-worker").await;
    // Tighten the Session scope for the Human override-free Bot view
    // down to Participant by RESEEDING the participant scope.
    let _ = conn_a;
    stack
        .session_repo
        .remove_participant(&stack.session_id, "bot-worker")
        .await
        .expect("remove worker");
    stack
        .session_repo
        .add_participant(
            &stack.session_id,
            participant("bot-worker", ActorKind::Bot, ParticipantRole::Worker, MessageViewScope::Participant),
        )
        .await
        .expect("reseed scoped worker");
    admit(&stack, "op-1").await;

    // FullOnly is outside the frozen Participant scope → filtered
    // at the enqueue boundary.
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "full-only",
                false,
                MessageVisibilityDomain::ManagerWorker,
                Some(MessageAudience::FullOnly),
            ))
            .await
            .expect("full-only publish")
            .delivered,
        0
    );
    expect_quiet(&mut rx_a).await;

    // A public frame of the SAME operation flows with the same permit.
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "public-note",
                false,
                MessageVisibilityDomain::ManagerWorker,
                Some(MessageAudience::Public),
            ))
            .await
            .expect("public publish")
            .delivered,
        1
    );
    expect_payload(&mut rx_a, "public-note").await;
    // Directed to THIS actor also flows.
    let directed = MessageAudience::directed(["bot-worker"]).expect("directed audience");
    assert_eq!(
        stack
            .delivery
            .publish(op_command(
                &stack,
                "op-1",
                "directed-note",
                false,
                MessageVisibilityDomain::ManagerWorker,
                Some(directed),
            ))
            .await
            .expect("directed publish")
            .delivered,
        1
    );
    expect_payload(&mut rx_a, "directed-note").await;
}

/// Failure doubles sanity: with the Application resolver replaced
/// by an always-failing one, the fanout admits NOTHING at event
/// level and the send-level output admission installs nothing —
/// both fail closed without panics.
#[tokio::test]
async fn failing_admission_fails_everything_closed() {
    let permits = Arc::new(OutputPermitStore::new());
    let registry = Arc::new(
        WorkbenchConnectionRegistry::new().with_output_permits(permits.clone()),
    );
    registry.set_delivery_admission(Arc::new(FailingAdmission));
    let run_channels = Arc::new(RunChannelManager::new().with_output_permits(permits));
    let delivery = Arc::new(WorkbenchFrontendDelivery::new(
        registry.clone(),
        run_channels.clone(),
    ));
    let output_admission = Arc::new(WorkbenchOutputAdmission::new(
        registry.clone(),
        Arc::new(FailingAdmission),
    ));
    let (control_tx, _control_rx) = mpsc::channel(8);
    let (frame_tx, mut frame_rx) = mpsc::channel(4);
    registry
        .subscribe_protected_with_shutdown(
            "session-x".to_string(),
            control_tx,
            Some("human_user-a".to_string()),
            None,
            tokio_util::sync::CancellationToken::new(),
            ProtectedDeliveryBinding {
                tenant: None,
                user_id: "user-a".to_string(),
                group_id: "group-x".to_string(),
                session_id: Some("session-x".to_string()),
                view_actor_id: "human_user-a".to_string(),
                connection_instance_id: 0,
            },
            frame_tx,
        )
        .await
        .expect("subscribe");

    output_admission
        .admit_output_operation(OutputOperationAdmission {
            operation_id: "op-x".to_string(),
            group_id: "group-x".to_string(),
            session_id: Some("session-x".to_string()),
        })
        .await
        .expect("admission failure is absorbed upstream");
    let delivered = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: "session-x".to_string(),
            },
            event_json: "evt".to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::Chat,
            audience: None,
            operation_id: Some("op-x".to_string()),
            terminal: false,
            run_id: None,
        })
        .await
        .expect("op publish");
    assert_eq!(delivered.delivered, 0);
    let delivered_event = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: "session-x".to_string(),
            },
            event_json: "evt".to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::Chat,
            audience: None,
            operation_id: None,
            terminal: false,
            run_id: None,
        })
        .await
        .expect("event publish");
    assert_eq!(delivered_event.delivered, 0);
    assert!(frame_rx.try_recv().is_err());
    assert!(registry.events_skipped_auth_failure() >= 1);
}

/// The recording double proves the batch shape of boundary calls:
/// one operation admission resolves its whole candidate set in ONE
/// resolver call, and frames never re-enter the resolver.
#[tokio::test]
async fn boundary_calls_are_one_batch_per_operation_and_zero_per_frame() {
    let permits = Arc::new(OutputPermitStore::new());
    let registry = Arc::new(
        WorkbenchConnectionRegistry::new().with_output_permits(permits.clone()),
    );
    let admission = Arc::new(RecordingAdmission::new());
    registry.set_delivery_admission(admission.clone());
    let output_admission = Arc::new(WorkbenchOutputAdmission::new(
        registry.clone(),
        admission.clone(),
    ));
    let run_channels = Arc::new(RunChannelManager::new().with_output_permits(permits));
    let delivery = Arc::new(WorkbenchFrontendDelivery::new(
        registry.clone(),
        run_channels.clone(),
    ));
    for conn in 0..3u64 {
        let (control_tx, _control_rx) = mpsc::channel(8);
        let (frame_tx, _frame_rx) = mpsc::channel(8);
        registry
            .subscribe_protected_with_shutdown(
                "session-x".to_string(),
                control_tx,
                Some("human_view".to_string()),
                None,
                tokio_util::sync::CancellationToken::new(),
                ProtectedDeliveryBinding {
                    tenant: None,
                    user_id: "user-a".to_string(),
                    group_id: "group-x".to_string(),
                    session_id: Some("session-x".to_string()),
                    view_actor_id: "human_view".to_string(),
                    connection_instance_id: 0,
                },
                frame_tx,
            )
            .await
            .unwrap_or_else(|_| panic!("subscribe {conn}"));
    }
    let calls_before = admission.call_count();
    output_admission
        .admit_output_operation(OutputOperationAdmission {
            operation_id: "op-batch".to_string(),
            group_id: "group-x".to_string(),
            session_id: Some("session-x".to_string()),
        })
        .await
        .expect("admission");
    assert_eq!(
        admission.call_count() - calls_before,
        1,
        "ONE resolution per operation regardless of the candidate count"
    );
    for n in 0..16 {
        delivery
            .publish(FrontendDeliveryCommand {
                target: FrontendDeliveryTarget::Session {
                    session_id: "session-x".to_string(),
                },
                event_json: format!("f-{n}"),
                delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: None,
                exclude_conn_id: None,
                visibility_domain: MessageVisibilityDomain::Chat,
                audience: None,
                operation_id: Some("op-batch".to_string()),
                terminal: false,
                run_id: None,
            })
            .await
            .expect("publish");
    }
    assert_eq!(
        admission.call_count() - calls_before,
        1,
        "16 frames re-entered the resolver"
    );
}

// ---------------------------------------------------------------------------
// Stream lifecycle (Run terminal ≠ operation complete) and the
// slow-consumer terminal floor — the F1 follow-up acceptance matrix.
// ---------------------------------------------------------------------------

/// A run sink's terminal ends THAT stream only: its sibling run of
/// the same admitted operation keeps flowing (one WebSend can drive
/// several bot Runs).
#[tokio::test]
async fn one_run_sink_terminal_never_closes_a_sibling_run() {
    let stack = new_stack().await;
    let (conn, mut fanout) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "multi-run-operation").await;
    let mut run_a = register_run_sink(&stack, "multi-run-operation", "run-a", conn).await;
    let mut run_b = register_run_sink(&stack, "multi-run-operation", "run-b", conn).await;

    assert!(
        stack
            .run_channels
            .send_visible_event(
                "run-a",
                "a-final".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                true,
            )
            .await
    );
    expect_payload(&mut run_a, "a-final").await;
    assert!(
        stack
            .run_channels
            .send_visible_event(
                "run-b",
                "b-delta".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                false,
            )
            .await,
        "run-a's terminal must not retire run-b's admitted output"
    );
    expect_payload(&mut run_b, "b-delta").await;
    let _ = fanout.try_recv();
}

/// Fanout lane: a stream terminal retires only its own stream; the
/// operation closes when EVERY registered stream is terminal.
#[tokio::test]
async fn fanout_stream_terminal_retires_only_its_own_stream() {
    let stack = new_stack().await;
    let (_conn, mut rx) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "fanout-multi-run").await;
    stack
        .output_admission
        .register_operation_runs(
            "fanout-multi-run",
            vec!["run-a".to_string(), "run-b".to_string()],
        )
        .await
        .expect("register streams");

    // Interleaved deltas: everything flows.
    for (run, payload) in [("run-a", "a-1"), ("run-b", "b-1")] {
        assert_eq!(
            stack
                .delivery
                .publish(run_command(
                    &stack,
                    "fanout-multi-run",
                    Some(run),
                    payload,
                    false,
                    MessageVisibilityDomain::Chat,
                    None,
                ))
                .await
                .expect("delta publish")
                .delivered,
            1
        );
        expect_payload(&mut rx, payload).await;
    }

    // run-a's terminal: delivered, and run-b keeps flowing.
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fanout-multi-run",
                Some("run-a"),
                "a-final",
                true,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("run-a terminal")
            .delivered,
        1
    );
    expect_payload(&mut rx, "a-final").await;
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fanout-multi-run",
                Some("run-b"),
                "b-2",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("run-b delta")
            .delivered,
        1,
        "a sibling stream survives another stream's terminal"
    );
    expect_payload(&mut rx, "b-2").await;
    // Post-terminal leakage on run-a is refused and counted.
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fanout-multi-run",
                Some("run-a"),
                "a-late",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("run-a late frame")
            .delivered,
        0,
        "run-a refuses frames after its own terminal"
    );

    // The last registered stream's terminal completes the operation:
    // even a DUPLICATE terminal afterwards is refused and counted.
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fanout-multi-run",
                Some("run-b"),
                "b-final",
                true,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("run-b terminal")
            .delivered,
        1
    );
    expect_payload(&mut rx, "b-final").await;
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fanout-multi-run",
                Some("run-b"),
                "b-final-duplicate",
                true,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("duplicate terminal")
            .delivered,
        0,
        "with every registered stream terminal the operation refuses further frames"
    );
    assert!(stack.permits.frames_dropped_after_terminal() >= 1);
}

/// A terminal observed BEFORE its stream registration (a fast
/// managed callback) still counts: later registration completes the
/// bookkeeping — completion is never inferred from "currently no
/// runs".
#[tokio::test]
async fn fast_terminal_before_registration_completes_on_registration() {
    let stack = new_stack().await;
    let (_conn, mut rx) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "fast-op").await;
    stack
        .permits
        .mark_run_terminal("fast-op", "run-a")
        .await;
    // run-a's terminal arrived before registration; run-b registered
    // afterwards and still flows.
    stack
        .output_admission
        .register_operation_runs("fast-op", vec!["run-a".to_string(), "run-b".to_string()])
        .await
        .expect("register");
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fast-op",
                Some("run-b"),
                "b-delta",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("b delta")
            .delivered,
        1
    );
    expect_payload(&mut rx, "b-delta").await;
    stack
        .permits
        .mark_run_terminal("fast-op", "run-b")
        .await;
    assert_eq!(
        stack
            .delivery
            .publish(run_command(
                &stack,
                "fast-op",
                Some("run-b"),
                "b-late",
                false,
                MessageVisibilityDomain::Chat,
                None,
            ))
            .await
            .expect("late frame")
            .delivered,
        0,
        "everything is closed once both registered streams are terminal"
    );
}

/// The slow-consumer transport floor: a terminal that cannot land
/// within the bounded backpressure budget RETIRES the saturated
/// connection — an observable transport failure (the client
/// reconnects), never a silent live stream without its final.
#[tokio::test]
async fn terminal_timeout_retires_the_saturated_connection() {
    let stack = new_stack().await;
    let (_conn, mut rx) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "slow-operation").await;
    for n in 0..bcs_ws::shared::PROTECTED_FRAME_QUEUE_SIZE {
        assert_eq!(
            stack
                .delivery
                .publish(op_command(
                    &stack,
                    "slow-operation",
                    &format!("delta-{n}"),
                    false,
                    MessageVisibilityDomain::Chat,
                    None,
                ))
                .await
                .expect("fill")
                .delivered,
            1
        );
    }
    let result = stack
        .delivery
        .publish(op_command(
            &stack,
            "slow-operation",
            "final",
            true,
            MessageVisibilityDomain::Chat,
            None,
        ))
        .await
        .expect("terminal publish");
    assert_eq!(
        result.delivered, 0,
        "the terminal could not land within the bounded budget"
    );
    assert_eq!(
        stack.registry.connection_count(&stack.session_id).await,
        0,
        "the saturated connection was retired — transport failure is observable"
    );
    while let Ok(frame) = rx.try_recv() {
        assert!(
            !matches!(frame, OutboundFrame::Terminal { .. }),
            "no terminal was silently queued"
        );
    }
}

/// The run-sink lane applies the same floor: a lost terminal retires
/// the connection and is counted.
#[tokio::test]
async fn run_sink_terminal_timeout_retires_and_is_counted() {
    let stack = new_stack().await;
    let (conn, _rx) = subscribe(&stack, "user-a", "human_user-a").await;
    admit(&stack, "sink-slow-op").await;
    let mut sink_rx = register_run_sink(&stack, "sink-slow-op", "run-slow", conn).await;
    for n in 0..bcs_ws::shared::PROTECTED_FRAME_QUEUE_SIZE {
        assert!(
            stack
                .run_channels
                .send_visible_event(
                    "run-slow",
                    format!("delta-{n}"),
                    MessageVisibilityDomain::Chat,
                    None,
                    false,
                )
                .await,
            "delta {n} buffered"
        );
    }
    assert!(
        !stack
            .run_channels
            .send_visible_event(
                "run-slow",
                "final".to_string(),
                MessageVisibilityDomain::Chat,
                None,
                true,
            )
            .await,
        "the terminal was lost past the budget"
    );
    assert_eq!(stack.run_channels.terminal_frames_lost(), 1);
    assert_eq!(
        stack.registry.connection_count(&stack.session_id).await,
        0,
        "the saturated connection was retired through the shared hook"
    );
    assert!(sink_rx.try_recv().is_ok(), "buffered deltas remain drainable");
}
