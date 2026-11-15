//! Protected task-result legs toward a caller-enabled Manager (§15.4):
//! the result admission carries the RESULT's own protected material (the
//! canonical result text and the worker as the trusted ordinary sender)
//! and parks the session-captured human identity on the exact handoff
//! scope the root protected dispatch claims — for both terminal paths:
//! the worker's terminal callback (queued_task_terminal::commit) and the
//! runtime's timeout/failure notice (managed_delivery's fallback). A
//! caller-disabled Manager, a missing session capture, or an absent relay
//! each keep the result leg's ordinary shape.

use std::sync::{Arc, atomic::Ordering};
use std::time::Duration;

use bcs_config::resolve_env_str as resolve_env;
use bcs_config_api::message_delivery::{BotDeliveryMode, DeliveryPolicy};
use bcs_domain::message_delivery::PersistedMessageDelivery;
use bcs_domain::{GroupStrategy, ParticipantRole};
use bcs_message_flow::caller_admission::CallerAdmissionDeps;
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::caller_relay::CallerRelayState;
use bcs_message_flow::{
    BcsMessageFlow, MemoryBotRunContextStore, delivery_policy::LiveDeliveryPolicy,
    managed_delivery::ManagedMessageDelivery, queued_group::QueuedGroupPreparation,
};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::caller_handoff::{
    HandoffIdentityReadiness, HandoffScope, HumanCallerDeliveryHandoffService,
};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::MessageRepoPort;
use bcs_service_api::{
    BotCallerConfiguration, BotCandidateReadQuery, BotControlPlaneCandidate,
    BotControlPlaneCoreService, BotControlPlaneRecord, BotControlPlaneView, BotDeliveryPort,
    BotEventCommand,
    CallerContext, CallerIdentityConfig, CallerIdentitySnapshot, CallerMinimumIsolation,
    ChatEventState, GroupCoreService, ManagedDeliveryPreparationService, MessageFlowService,
    ServiceResult, TaskDispatchCommand,
};
use bcs_service_api::{DeliveryTransitionCommand, ManagedMessageDeliveryService};
use bcs_session_store::MemorySpeakingLeaseRepo;
use serde_json::json;

#[path = "support/session.rs"]
mod session;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

const SESSION: &str = "group-1:task";

fn now_ms() -> i64 { chrono::Utc::now().timestamp_millis() }
fn admin() -> CallerContext { CallerContext::Human(HumanActor { actor_id: "human_operator".into(), staff_no: "operator".into() }) }

fn capture_ingress() -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        now_ms(),
        &resolve_env(),
    ).expect("valid ingress")
}

/// Caller-ENABLED for the Manager (bot-driver): the §15.4 gate shape that
/// needs the protected result routing.
struct ManagerCallerEnabledPlane;
/// Unconfigured for every bot: the ordinary deployment shape.
struct UnconfiguredPlane;

#[async_trait::async_trait]
impl BotControlPlaneCoreService for ManagerCallerEnabledPlane {
    async fn get_caller_configuration(&self, _bot_id: &str, _env: &str) -> ServiceResult<Option<BotCallerConfiguration>> {
        Ok(Some(BotCallerConfiguration {
            snapshot: Some(CallerIdentitySnapshot {
                source_revision: 1,
                caller_identity: CallerIdentityConfig { enabled: true, minimum_isolation: CallerMinimumIsolation::CallerContainer },
            }),
        }))
    }
    async fn get_record(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneRecord>> { unimplemented!("not needed by this test") }
    async fn get(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneView>> { unimplemented!("not needed by this test") }
    async fn get_by_ids(&self, _: &[String], _: &str) -> ServiceResult<Vec<BotControlPlaneView>> { unimplemented!("not needed by this test") }
    async fn list_candidates(&self, _: BotCandidateReadQuery) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> { unimplemented!("not needed by this test") }
    async fn list_by_creator(&self, _: bcs_service_api::BotControlPlaneOwnedQuery) -> ServiceResult<Vec<BotControlPlaneView>> { unimplemented!("not needed by this test") }
    async fn patch(&self, _: &str, _: &str, _: bcs_service_api::BotControlPlanePatch) -> ServiceResult<Option<BotControlPlaneView>> { unimplemented!("not needed by this test") }
}

#[async_trait::async_trait]
impl BotControlPlaneCoreService for UnconfiguredPlane {
    async fn get_caller_configuration(&self, _bot_id: &str, _env: &str) -> ServiceResult<Option<BotCallerConfiguration>> {
        Ok(Some(BotCallerConfiguration { snapshot: None }))
    }
    async fn get_record(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneRecord>> { unimplemented!("not needed by this test") }
    async fn get(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneView>> { unimplemented!("not needed by this test") }
    async fn get_by_ids(&self, _: &[String], _: &str) -> ServiceResult<Vec<BotControlPlaneView>> { unimplemented!("not needed by this test") }
    async fn list_candidates(&self, _: BotCandidateReadQuery) -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> { unimplemented!("not needed by this test") }
    async fn list_by_creator(&self, _: bcs_service_api::BotControlPlaneOwnedQuery) -> ServiceResult<Vec<BotControlPlaneView>> { unimplemented!("not needed by this test") }
    async fn patch(&self, _: &str, _: &str, _: bcs_service_api::BotControlPlanePatch) -> ServiceResult<Option<BotControlPlaneView>> { unimplemented!("not needed by this test") }
}

enum Plane { Enabled, Unconfigured }

struct Fixture {
    support: support::FlowTestSupport,
    flow: Arc<BcsMessageFlow>,
    service: Arc<ManagedMessageDelivery>,
    repo: Arc<dyn MessageRepoPort>,
    live: Arc<LiveDeliveryPolicy>,
    handoff: Arc<HumanCallerDeliveryHandoff>,
    ledger: Option<Arc<bcs_message_flow::caller_relay_ledger::CallerRelayLedger>>,
    admission: bool,
}

async fn fixture(relay: bool, plane: Plane) -> Fixture {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    for p in &mut group.participants {
        if p.bot_uuid == "bot-driver" { p.role = ParticipantRole::Manager; }
        if p.bot_uuid == "bot-observer" { p.role = ParticipantRole::Worker; }
    }
    support.group.upsert(group.clone()).await.unwrap();
    let repo = Arc::new(MemoryMessageRepo::new().with_environment(resolve_env()));
    let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
    live.scheduler_available.store(true, Ordering::SeqCst);
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()).with_policy(live.clone()));
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let relay_state = relay.then(|| CallerRelayState::new(600_000));
    let ledger = relay_state.as_ref().map(|state| state.ledger.clone());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = match plane {
        Plane::Enabled => Arc::new(ManagerCallerEnabledPlane),
        Plane::Unconfigured => Arc::new(UnconfiguredPlane),
    };
    let admission = true;
    let mut flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    )
        .with_message_repo(repo.clone()).with_managed_deliveries(service.clone())
        .with_bot_run_context(Arc::new(MemoryBotRunContextStore::default()))
        .with_session_management(Arc::new(session::StaticSessionManagement::new(
            session::test_session(SESSION, "group-1", group.participants.clone()),
        )))
        .with_speaking_leases(Arc::new(MemorySpeakingLeaseRepo::new()));
    flow.delivery_policy = Some(live.clone());
    let mut flow = match relay_state {
        Some(state) => flow.with_caller_relay(state),
        None => flow,
    };
    if admission {
        flow.caller_admission = Some(Arc::new(CallerAdmissionDeps {
            handoff: handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            admission: service.clone(),
            control_plane,
            reserve_ttl_ms: 30_000,
        }));
    }
    let flow = Arc::new(flow);
    flow.retain_terminal_events();
    // The fallback admission site (managed_delivery) reaches the flow through
    // the weak output-cleanup binding only.
    service.bind_output_cleanup(Arc::downgrade(&flow)).unwrap();
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.group = true; policy.flow_enabled.system = true; policy.flow_enabled.task = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    policy.queue_ttl_ms = Some(300_000);
    flow.replace_delivery_policy(admin(), 0, policy).await.unwrap();
    Fixture { support, flow, service, repo, live, handoff, ledger, admission }
}

fn dispatch_command() -> TaskDispatchCommand {
    TaskDispatchCommand { caller_delegation: None, driver_bot_id:"bot-driver".into(), group_id:"group-1".into(), target_bot_id:"bot-observer".into(),
        target_bot_name:None, payload:json!({"message":"TASK_BODY", "bcs_session_id":SESSION, "response_mode":"full",
        "assignment_intent_id":"bcs_intent_assignment"}) }
}

fn final_event(row: &PersistedMessageDelivery) -> BotEventCommand {
    BotEventCommand { bot_id:row.target_bot_id.clone(), run_id:row.run_id.clone().unwrap(), group_id:"group-1".into(),
        bcs_session_id:Some(SESSION.into()), state:ChatEventState::Final, event_type:"chat.event".into(),
        event_payload:json!({"message":{"content":[{"type":"text","text":"WORKER_RESULT"}]}}) }
}

impl Fixture {
    async fn rows(&self) -> Vec<PersistedMessageDelivery> { self.service.snapshot(Some(SESSION)).await.unwrap() }

    async fn context(&self, bot: &str) {
        let group = self.support.group.get("group-1").await.unwrap();
        self.flow.system_queue_port().admit(&group, SESSION, &group.participants,
            bcs_domain::SystemMessageEventKind::SessionContext,
            &[bcs_domain::SystemGroupMessage { recipients: vec![bot.into()], message: format!("REQUIRED_CONTEXT_FOR_{bot}"),
                delivery_type: bcs_domain::DeliveryType::Inject, persist: bcs_domain::PersistMode::PerRecipient }]).await.unwrap().unwrap();
    }

    async fn dispatch(&self) -> (String, PersistedMessageDelivery) {
        let outcome = bcs_test_support::contract::application::queued_task::queued_task_dispatch_contract_tests(
            self.flow.as_ref(), dispatch_command()).await;
        let row = self.rows().await.into_iter().find(|r| r.semantic_projection_json["task"]["task_id"] == outcome.task_id).unwrap();
        (outcome.task_id, row)
    }

    async fn start(&self, row: &PersistedMessageDelivery) -> PersistedMessageDelivery {
        let preparer = QueuedGroupPreparation { flow:Arc::downgrade(&self.flow), deliveries:self.service.clone() };
        let mut prepared = preparer.prepare(row).await.unwrap();
        prepared.transport_context_json["policy_version"] = json!(self.live.snapshot.read().await.version);
        let started = self.service.transition(DeliveryTransitionCommand { caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None,
            delivery_id:row.delivery_id.clone(), expected_state_version:row.state.state_version, event:Event::StartSend,
            now_ms:now_ms(), request_id:None, actor_id:None, reply:None,
            transport_context_json:Some(prepared.transport_context_json), deadline_at_ms:Some(i64::MAX) }).await.unwrap();
        if let bcs_protocol::BcsFrame::Request(frame) = &mut prepared.command.frame { frame.id = started.request_id.clone().unwrap(); }
        preparer.before_send(&started, &prepared.command).await.unwrap();
        self.support.bot_delivery.deliver(prepared.command).await.unwrap();
        started
    }

    fn result_row<'rows>(&self, rows: &'rows [PersistedMessageDelivery], task_id: &str) -> &'rows PersistedMessageDelivery {
        rows.iter().find(|row| row.source_message_id == format!("task-result:{task_id}"))
            .expect("the manager result row exists")
    }

    fn result_scope(&self, task_id: &str) -> HandoffScope {
        HandoffScope {
            env: resolve_env(),
            session_id: SESSION.into(),
            group_id: Some("group-1".into()),
            source_message_id: format!("task-result:{task_id}"),
            target_bot_id: "bot-driver".into(),
        }
    }
}

#[tokio::test]
async fn worker_final_callback_parks_identity_and_marks_the_result_leg() {
    let f = fixture(true, Plane::Enabled).await;
    f.ledger.as_ref().unwrap().record(&resolve_env(), SESSION, "turn-1", capture_ingress(), now_ms(), 600_000);
    f.context("bot-observer").await;
    let (task_id, row) = f.dispatch().await;
    let row = f.start(&row).await;
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    // A replayed terminal callback must not mint a second identity.
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();

    let rows = f.rows().await;
    let result = f.result_row(&rows, &task_id);
    // The RESULT's own material: the canonical result text and the worker as
    // the trusted channel sender, never the dispatch's task text.
    assert_eq!(result.semantic_projection_json["caller_required"], true);
    assert_eq!(result.semantic_projection_json["caller_task"], "WORKER_RESULT");
    assert_eq!(result.semantic_projection_json["caller_channel"]["actor_id"], "bot-observer");
    // The identity is parked on the claim-side scope of this very row.
    assert_eq!(f.handoff.identity_readiness(&f.result_scope(&task_id)).await, HandoffIdentityReadiness::Published);
    // The park consumed exactly one of the turn's sixteen capture redeems.
    for count in 0..15 {
        assert!(f.ledger.as_ref().unwrap().redeem(&resolve_env(), SESSION, now_ms()).is_some(),
            "redeem {count} of the remaining budget must succeed");
    }
    assert!(f.ledger.as_ref().unwrap().redeem(&resolve_env(), SESSION, now_ms()).is_none(),
        "the result park consumed exactly one redeem");
}

#[tokio::test]
async fn unconfigured_manager_keeps_the_result_leg_ordinary() {
    let f = fixture(true, Plane::Unconfigured).await;
    f.ledger.as_ref().unwrap().record(&resolve_env(), SESSION, "turn-1", capture_ingress(), now_ms(), 600_000);
    f.context("bot-observer").await;
    let (task_id, row) = f.dispatch().await;
    let row = f.start(&row).await;
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    let rows = f.rows().await;
    let result = f.result_row(&rows, &task_id);
    assert!(result.semantic_projection_json.get("caller_required").is_none(),
        "an unconfigured Manager keeps the ordinary result delivery");
    assert_eq!(f.handoff.identity_readiness(&f.result_scope(&task_id)).await, HandoffIdentityReadiness::Gone);
    assert_eq!(result.semantic_projection_json["task"]["leg"], "result");
}

#[tokio::test]
async fn absent_relay_keeps_the_result_leg_ordinary() {
    let f = fixture(false, Plane::Enabled).await;
    assert!(f.ledger.is_none(), "the wiring's only disabled shape: no relay state");
    f.context("bot-observer").await;
    let (task_id, row) = f.dispatch().await;
    let row = f.start(&row).await;
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    let rows = f.rows().await;
    let result = f.result_row(&rows, &task_id);
    assert!(result.semantic_projection_json.get("caller_required").is_none(),
        "no relay state keeps the ordinary result delivery");
}

#[tokio::test]
async fn no_live_capture_keeps_the_result_leg_ordinary() {
    let f = fixture(true, Plane::Enabled).await;
    // Caller-ENABLED Manager but the session capture expired / never landed.
    f.context("bot-observer").await;
    let (task_id, row) = f.dispatch().await;
    let row = f.start(&row).await;
    f.flow.handle_bot_event(final_event(&row)).await.unwrap();
    let rows = f.rows().await;
    let result = f.result_row(&rows, &task_id);
    assert!(result.semantic_projection_json.get("caller_required").is_none(),
        "no live capture keeps the ordinary result delivery");
    assert_eq!(f.handoff.identity_readiness(&f.result_scope(&task_id)).await, HandoffIdentityReadiness::Gone);
}

#[tokio::test]
async fn runtime_timeout_notice_parks_the_protected_result_leg() {
    // The runtime's timeout conversion settles the Worker lane inside a managed
    // transition whose fallback notice builds on the SAME protected routing.
    use bcs_message_flow::delivery_runtime::{DeliveryRuntime, DeliveryRuntimeConfig};

    let f = fixture(true, Plane::Enabled).await;
    f.ledger.as_ref().unwrap().record(&resolve_env(), SESSION, "turn-1", capture_ingress(), now_ms(), 600_000);
    f.context("bot-observer").await;
    // The RUNTIME drives this lane from Queued: its own StartSend arms the
    // run deadline the timeout conversion settles.
    let (task_id, row) = f.dispatch().await;
    let runtime = DeliveryRuntime { caller_dispatch: None, policy: Some(f.live.clone()), service: f.service.clone(),
        preparation: Arc::new(QueuedGroupPreparation { flow: Arc::downgrade(&f.flow), deliveries: f.service.clone() }),
        transport: f.support.bot_delivery.clone(), config: DeliveryRuntimeConfig {
            max_safe_retries: 0, pause_dispatch: false, bots: Default::default(), tick: Duration::from_millis(2),
            expiry_tick: Duration::from_millis(20), io_timeout: Duration::from_secs(1), run_timeout: Duration::from_millis(300),
            cancel_timeout: Duration::from_secs(1), startup_recovery_grace: Duration::ZERO,
            max_tasks: 4, max_abort_tasks: 1 } };
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let job = tokio::spawn(runtime.run(shutdown));
    let result = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let rows = f.rows().await;
            if let Some(result_row) = rows.iter().find(|r| r.source_message_id == format!("task-result:{task_id}")) {
                if result_row.semantic_projection_json.get("caller_required").is_some() {
                    return result_row.clone();
                }
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    }).await;
    let _ = stop.send(true);
    let _ = job.await;
    let result = result.expect("the timeout notice must admit a protected result row");
    assert_eq!(result.semantic_projection_json["caller_required"], true);
    assert!(result.semantic_projection_json["caller_task"].as_str().unwrap().contains("任务"),
        "the protected text is the failure body: {}",
        result.semantic_projection_json["caller_task"].as_str().unwrap());
    assert_eq!(result.semantic_projection_json["caller_channel"]["actor_id"], "bot-observer");
    assert_eq!(f.handoff.identity_readiness(&f.result_scope(&task_id)).await, HandoffIdentityReadiness::Published,
        "the fallback admission published the parked identity against the committed row");
}
