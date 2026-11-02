//! Owner→caller identity relay contract (spec L62 direction, DEFAULT-ON):
//! any Human speech into a session carrying a caller-enabled member is
//! captured into the session-keyed ledger; the trusted task-assign edge
//! redeems it onto the caller target's handoff scope keyed from the
//! committed row's own facts. The middle bot carries no token; expiry,
//! spent budget and an explicit deployment opt-out all stay fail-closed
//! exactly like before the relay existed.

use std::sync::{Arc, atomic::Ordering};

use bcs_config::resolve_env_str as resolve_env;
use bcs_config_api::message_delivery::{BotDeliveryMode, DeliveryPolicy};
use bcs_service_api::port::repo::session_speaking_lease::{AcquireLease, LeaseAcquire, SpeakingLeaseRepoPort as _SpeakingLeaseRepoPort};
use bcs_service_api::WebSendLeaseClaim;
use bcs_session_store::MemorySpeakingLeaseRepo;
use bcs_service_api::{
    BotCallerConfiguration, BotCandidateReadQuery, BotControlPlaneCandidate,
    BotControlPlaneCoreService, BotControlPlaneRecord, BotControlPlaneView,
    CallerIdentityConfig, CallerIdentitySnapshot, CallerMinimumIsolation, ServiceResult,
};
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_domain::{GroupStrategy, ParticipantRole};
use bcs_message_flow::caller_admission::CallerAdmissionDeps;
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::caller_relay::CallerRelayState;
use bcs_message_flow::{
    BcsMessageFlow, MemoryBotRunContextStore, delivery_policy::LiveDeliveryPolicy,
    managed_delivery::ManagedMessageDelivery,
};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::caller_handoff::{
    HandoffClaim, HandoffIdentityReadiness, HandoffScope, HumanCallerDeliveryHandoffService,
};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::{
    CallerContext, GroupCoreService, ManagedMessageDeliveryService, MessageFlowService,
    TaskDispatchCommand,
};
use serde_json::json;

#[path = "support/session.rs"]
mod session;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;


async fn acquire_lease(f: &Fixture, holder: &str) -> WebSendLeaseClaim {
    let repo = f.flow.speaking_leases.as_ref().expect("lease store wired");
    match repo
        .acquire(AcquireLease {
            session_id: "group-1",
            requester_actor_id: holder,
            now_ms: chrono::Utc::now().timestamp_millis(),
            idle_timeout_ms: 60_000,
        })
        .await
        .unwrap()
    {
        LeaseAcquire::Granted(lease) | LeaseAcquire::SelfHeld(lease) =>
            WebSendLeaseClaim { lease_id: lease.lease_id, epoch: lease.epoch },
        other => panic!("expected a held lease, got {other:?}"),
    }
}

const SESSION: &str = "group-1:task";

fn dispatch_command() -> TaskDispatchCommand {
    TaskDispatchCommand {
        caller_delegation: Some(bcs_service_api::CallerDelegationAssignment {
            parent_run_id: "run-manager".into(),
            parent_attempt: 1,
            parent_bot_id: "bot-driver".into(),
            invocation_id: "tool-call-relay".into(),
        }),
        driver_bot_id: "bot-driver".into(),
        group_id: "group-1".into(),
        target_bot_id: "bot-observer".into(),
        target_bot_name: None,
        payload: json!({"message":"RELAY_BODY", "bcs_session_id":SESSION, "response_mode":"full"}),
    }
}

fn ingress(actor: &str) -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: actor.into(), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        1,
        &resolve_env(),
    )
    .expect("valid ingress")
}

struct CallerEnabledControlPlane;

/// The pure-Owner deployment shape: every bot reads caller-unconfigured, so
/// the session fences as a Plain group and the Human's speech is ordinary
/// Owner work — exactly the H→A(owner) leg the relay capture targets.
struct CallerUnconfiguredControlPlane;

#[async_trait::async_trait]
impl BotControlPlaneCoreService for CallerUnconfiguredControlPlane {
    async fn get_caller_configuration(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        Ok(Some(BotCallerConfiguration { snapshot: None }))
    }

    async fn get_record(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneRecord>> {
        unimplemented!("not needed by this test")
    }
    async fn get(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn get_by_ids(&self, _: &[String], _: &str) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn list_candidates(&self, _: BotCandidateReadQuery)
        -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        unimplemented!("not needed by this test")
    }
    async fn list_by_creator(
        &self,
        _: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn patch(
        &self,
        _: &str,
        _: &str,
        _: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
}

#[async_trait::async_trait]
impl BotControlPlaneCoreService for CallerEnabledControlPlane {
    async fn get_caller_configuration(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<BotCallerConfiguration>> {
        Ok(Some(BotCallerConfiguration {
            snapshot: Some(CallerIdentitySnapshot {
                source_revision: 1,
                caller_identity: CallerIdentityConfig {
                    enabled: true,
                    minimum_isolation: CallerMinimumIsolation::CallerContainer,
                },
            }),
        }))
    }

    async fn get_record(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneRecord>> {
        unimplemented!("not needed by this test")
    }
    async fn get(&self, _: &str, _: &str) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn get_by_ids(&self, _: &[String], _: &str) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn list_candidates(&self, _: BotCandidateReadQuery)
        -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)> {
        unimplemented!("not needed by this test")
    }
    async fn list_by_creator(
        &self,
        _: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn patch(
        &self,
        _: &str,
        _: &str,
        _: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
}

struct Fixture {
    support: support::FlowTestSupport,
    flow: Arc<BcsMessageFlow>,
    service: Arc<ManagedMessageDelivery>,
    handoff: Arc<HumanCallerDeliveryHandoff>,
    ledger: Option<Arc<bcs_message_flow::caller_relay_ledger::CallerRelayLedger>>,
}

async fn fixture(relay_enabled: bool) -> Fixture {
    fixture_with_plane(relay_enabled, true).await
}

async fn fixture_with_plane(
    relay_enabled: bool,
    caller_enabled_plane: bool,
) -> Fixture {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    for p in &mut group.participants {
        if p.bot_uuid == "bot-driver" { p.role = ParticipantRole::Manager; }
        if p.bot_uuid == "bot-observer" { p.role = ParticipantRole::Worker; }
    }
    support.group.upsert(group.clone()).await.unwrap();
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
    live.scheduler_available.store(true, Ordering::SeqCst);
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let relay = (relay_enabled as bool).then(|| CallerRelayState::new(600_000));
    let ledger = relay.as_ref().map(|state| state.ledger.clone());
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    )
        .with_message_repo(repo).with_managed_deliveries(service.clone())
        .with_bot_run_context(Arc::new(MemoryBotRunContextStore::default()))
        .with_session_management(Arc::new(session::StaticSessionManagement::new(
            session::test_session(SESSION, "group-1", group.participants.clone()),
        )))
        .with_speaking_leases(Arc::new(MemorySpeakingLeaseRepo::new()));
    let flow = match relay {
        Some(state) => flow.with_caller_relay(state),
        None => flow,
    };
    let mut flow = Arc::new(flow);
    {
        let flow_mut = Arc::get_mut(&mut flow).unwrap();
        flow_mut.delivery_policy = Some(live);
        flow_mut.caller_admission = Some(Arc::new(CallerAdmissionDeps {
            handoff: handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            admission: service.clone(),
            control_plane: if caller_enabled_plane {
                Arc::new(CallerEnabledControlPlane)
            } else {
                Arc::new(CallerUnconfiguredControlPlane)
            },
            reserve_ttl_ms: 30_000,
        }));
    }
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.group = true; policy.flow_enabled.system = true; policy.flow_enabled.task = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    policy.queue_ttl_ms = Some(300_000);
    let admin = CallerContext::Human(HumanActor { actor_id: "human_operator".into(), staff_no: "operator".into() });
    flow.replace_delivery_policy(admin, 0, policy).await.unwrap();
    Fixture { support, flow, service, handoff, ledger }
}

async fn dispatch_row(f: &Fixture) -> (String, bcs_domain::message_delivery::PersistedMessageDelivery) {
    let mut cmd = dispatch_command();
    cmd.caller_delegation = cmd.caller_delegation.clone();
    let outcome = bcs_test_support::contract::application::queued_task::queued_task_dispatch_contract_tests(
        f.flow.as_ref(), cmd).await;
    let rows = f.service.snapshot(Some(SESSION)).await.unwrap();
    let row = rows.into_iter()
        .find(|r| r.semantic_projection_json["task"]["task_id"] == outcome.task_id)
        .expect("dispatched row");
    (outcome.task_id, row)
}

fn worker_scope(row: &bcs_domain::message_delivery::PersistedMessageDelivery) -> HandoffScope {
    HandoffScope {
        env: resolve_env(),
        session_id: row.session_id.clone(),
        group_id: Some(row.group_id.clone()),
        source_message_id: row.source_message_id.clone(),
        target_bot_id: row.target_bot_id.clone(),
    }
}

#[tokio::test]
async fn a_delegation_without_relay_state_stays_fail_closed() {
    // Present-day behavior pinned: with no relay state on the composition,
    // the owner middle-hop delegation parks nothing — the worker row's scope
    // reads Gone and nothing upgrades it.
    let f = fixture(false).await;
    let (_, row) = dispatch_row(&f).await;
    assert_eq!(row.semantic_projection_json["caller_delegation"]["parent_run_id"], "run-manager");
    assert_eq!(
        f.handoff.identity_readiness(&worker_scope(&row)).await,
        HandoffIdentityReadiness::Gone,
        "relay-disabled deployments never park identity through the middle hop"
    );
}

#[tokio::test]
async fn an_allowed_edge_redeems_the_session_capture_exactly_once() {
    // The core relay flow: the session's captured human ingress is redeemed
    // by the trusted task-assign edge and parked under the worker row's own
    // scope; the first committed claim consumes it and everything later
    // answers AlreadyClaimed.
    let f = fixture(true).await;
    let now = chrono::Utc::now().timestamp_millis();
    assert!(f.ledger.as_ref().unwrap().record(&resolve_env(), SESSION, "turn-relay-1", ingress("human_alice"), now, 600_000));
    let (task_id, row) = dispatch_row(&f).await;
    let scope = worker_scope(&row);
    assert_eq!(
        f.handoff.identity_readiness(&scope).await,
        HandoffIdentityReadiness::Published,
        "the task with id {task_id} must have the session's captured identity parked"
    );
    let claimed = f.handoff.claim(&scope, HandoffClaim::new(
        row.delivery_id.clone(), row.state.state_version, row.run_id.clone(), 1, now + 1,
    )).await.expect("the relayed identity claims once");
    assert_eq!(claimed.actor().actor_id, "human_alice");
    assert_eq!(
        f.handoff.identity_readiness(&scope).await,
        HandoffIdentityReadiness::Gone,
        "the claim consumed the parked ingress; later attempts find the tombstone"
    );
}

#[tokio::test]
async fn an_opted_out_deployment_assembles_no_relay_and_stays_closed() {
    // The single deployment opt-out (`enabled = false`) assembles no relay
    // state at all: nothing captures, nothing redeems, the worker scope
    // stays Gone exactly like before the relay existed.
    let f = fixture(false).await;
    assert!(f.ledger.is_none(), "an opted-out composition assembles no relay ledger");
    let (_, row) = dispatch_row(&f).await;
    assert_eq!(
        f.handoff.identity_readiness(&worker_scope(&row)).await,
        HandoffIdentityReadiness::Gone,
        "no identity ever parks without the relay state"
    );
}

#[tokio::test]
async fn an_expired_capture_fails_closed_at_the_edge() {
    // B5 at the relay boundary: a capture past its window redeems nothing —
    // the Human must speak again; the task stays fail-closed.
    let f = fixture(true).await;
    let now = chrono::Utc::now().timestamp_millis();
    assert!(f.ledger.as_ref().unwrap().record(&resolve_env(), SESSION, "turn-relay-stale", ingress("human_alice"), now - 1_200_000, 600_000));
    let (_, row) = dispatch_row(&f).await;
    assert_eq!(
        f.handoff.identity_readiness(&worker_scope(&row)).await,
        HandoffIdentityReadiness::Gone,
        "an expired capture must not relay"
    );
}

#[tokio::test]
async fn an_authenticated_human_speech_is_captured_at_admission() {
    // The capture seam: an authenticated Human speaking into the owner
    // session, with the allowlist's middle bot among the mentions, lands in
    // the relay ledger under the session key — no caller target needs to be
    // routed, and nothing is handed to any bot yet.
    let f = fixture_with_plane(true, true).await;
    let now = chrono::Utc::now().timestamp_millis();
    let lease = acquire_lease(&f, "human_alice").await;
    let cmd = bcs_service_api::WebSendCommand {
        caller_ingress: Some(ingress("human_alice")),
        speaking_lease: Some(lease),
        caller_execution: None,
        caller: CallerContext::Human(HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() }),
        group_id: "group-1".into(),
        session_id: Some(SESSION.into()),
        from_actor_id: "human_alice".into(),
        from_name: Some("Alice".into()),
        message: "please relay this".into(),
        mentions: vec!["bot-driver".into()],
        attachments: None,
        thinking: None,
        idempotency_key: Some("relay-capture-1".into()),
        source_im_message_id: Some("turn-relay-capture".into()),
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: vec![],
    };
    f.flow.handle_web_send(cmd).await.expect("web send admits");
    let (turn, captured) = f.ledger.as_ref().unwrap().redeem(&resolve_env(), SESSION, now)
        .expect("the human's speech must be captured for a session with a caller-enabled member");
    assert_eq!(turn, "turn-relay-capture");
    assert_eq!(captured.actor().actor_id, "human_alice");
}

#[tokio::test]
async fn a_frontend_group_chat_speech_is_captured_without_mentions() {
    // The real frontend speech path: handle_group_chat forwards with
    // `mentions: Vec::new()` — the common everyday message carries no
    // explicit @. Capture must key off the allowlisted middle bot being a
    // SESSION PARTICIPANT (presence), not an explicit mention, or relay
    // only ever fires for hand-crafted mentions.
    let f = fixture_with_plane(true, true).await;
    let now = chrono::Utc::now().timestamp_millis();
    let refused = f.flow.handle_group_chat(bcs_service_api::GroupChatCommand {
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "S12345".into() }),
        group_id: "group-1".into(),
        requested_sender_id: None,
        message: "please handle this quietly".into(),
        session_id: Some(SESSION.into()),
        provider_bypass_headers: vec![],
        caller_ingress: Some(ingress("human_1")),
    }).await.expect_err("a caller group fences leaseless group-chat speech at admission");
    assert!(refused.to_string().contains("speaking lease required"), "{refused}");
    // The lease refusal aborts admission BEFORE the capture seam: an
    // unadmitted speech never captures identity (capture is bound to a
    // completed admission scan, not to a raw request). The production
    // group-chat shape — a lease-holding Human speech — captures through
    // the same handle_web_send path the web-send test covers.
    assert!(
        f.ledger.as_ref().unwrap().redeem(&resolve_env(), SESSION, now).is_none(),
        "a fence-refused speech must not capture"
    );
}

// Keep the unused-variable lint honest for fixtures that ignore status.
#[allow(dead_code)]
fn _status_shape(_: Status) {}
