//! Caller-relay capture diagnostics (the starvation WARNs): a relay-capable
//! session whose capture is refused / skipped / never assembled all continue
//! the ordinary course — never failing the Human's speech on housekeeping —
//! but each gap must now leave a WARN naming the exact reason instead of the
//! old silent skip that left "identity never arrived" undiagnosable at
//! task-assign time. The three skips covered here: a saturated ledger of
//! live captures, Human speech into a caller-capable group without a caller
//! ingress, and a caller-capable composition with no relay state at all.

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_config::resolve_env_str as resolve_env;
use bcs_domain::speaking_lease::SessionSpeakingLease;
use bcs_message_flow::caller_admission::CallerAdmissionDeps;
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::caller_relay::CallerRelayState;
use bcs_message_flow::caller_relay_ledger::CallerRelayLedger;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::application::MessageFlowService;
use bcs_service_api::port::repo::session_speaking_lease::{
    AcquireLease, AdmitMessage, LeaseAcquire, LeaseDecision, LeaseRelease, PreemptLease,
    ReleaseLease, SpeakingLeaseRepoPort,
};
use bcs_service_api::{
    BotCallerConfiguration, BotControlPlaneCoreService, CallerContext, CallerIdentityConfig,
    CallerIdentitySnapshot, CallerMinimumIsolation, GroupCoreService, ManagedMessageDeliveryService,
    ServiceResult, WebSendCommand, WebSendLeaseClaim,
};
use bcs_session_store::MemorySpeakingLeaseRepo;
use tracing::Level;

#[path = "support/session.rs"]
mod session_support;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
use session_support::{StaticSessionManagement, test_session};

const SESSION: &str = "group-1:cap";

fn now_clock() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

fn ingress(actor: &str) -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: actor.to_string(), staff_no: "1".to_string() },
        HumanCallerAuthSource::HttpAuth,
        now_clock(),
        resolve_env(),
    )
    .expect("valid ingress")
}

/// Every bot answers caller-enabled: the session is relay-capable, so every
/// capture gate can pass and the diagnostics are reachable.
struct CallerEnabledControlPlane;

#[async_trait]
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
    async fn get_record(&self, _: &str, _: &str) -> ServiceResult<Option<bcs_service_api::BotControlPlaneRecord>> {
        unimplemented!("not needed by this test")
    }
    async fn get(&self, _: &str, _: &str) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn get_by_ids(&self, _: &[String], _: &str) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn list_candidates(&self, _: bcs_service_api::BotCandidateReadQuery)
        -> ServiceResult<(Vec<bcs_service_api::BotControlPlaneCandidate>, u64)> {
        unimplemented!("not needed by this test")
    }
    async fn list_by_creator(
        &self,
        _: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
    async fn patch(
        &self,
        _: &str,
        _: &str,
        _: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
        unimplemented!("not needed by this test")
    }
}

// ---------------------------------------------------------------------------
// Log capture: the test binary's tests share ONE global subscriber (the
// nextest process-per-test model installs it once per test process; under a
// plain `cargo test` threaded run only the first install wins and the sink
// is shared). Each test asserts on its own event message.
// ---------------------------------------------------------------------------
#[derive(Clone)]
struct LogWriter(Arc<std::sync::Mutex<Vec<u8>>>);
impl std::io::Write for LogWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.lock().expect("log sink").extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
}

static SINK_INIT: std::sync::OnceLock<Arc<std::sync::Mutex<Vec<u8>>>> = std::sync::OnceLock::new();

fn log_sink() -> Arc<std::sync::Mutex<Vec<u8>>> {
    SINK_INIT
        .get_or_init(|| {
            let sink = Arc::new(std::sync::Mutex::new(Vec::new()));
            let writer = LogWriter(sink.clone());
            let _ = tracing::subscriber::set_global_default(
                tracing_subscriber::fmt()
                    .with_writer(move || writer.clone())
                    .with_max_level(Level::INFO)
                    .with_ansi(false)
                    .finish(),
            );
            sink
        })
        .clone()
}

fn captured_logs() -> String {
    String::from_utf8(SINK_INIT.get().expect("sink installed").lock().expect("log sink").clone())
        .expect("utf8 logs")
}

/// Recording lease store so the fence read stays observable.
struct RecordingLeaseStore {
    inner: MemorySpeakingLeaseRepo,
    checks: Arc<AtomicUsize>,
}

#[async_trait]
impl SpeakingLeaseRepoPort for RecordingLeaseStore {
    async fn acquire(&self, command: AcquireLease<'_>) -> ServiceResult<LeaseAcquire> {
        self.inner.acquire(command).await
    }
    async fn preempt(&self, command: PreemptLease<'_>) -> ServiceResult<SessionSpeakingLease> {
        self.inner.preempt(command).await
    }
    async fn release(&self, command: ReleaseLease<'_>) -> ServiceResult<LeaseRelease> {
        self.inner.release(command).await
    }
    async fn admit_message(&self, command: AdmitMessage<'_>) -> ServiceResult<LeaseDecision> {
        self.inner.admit_message(command).await
    }
    async fn check_submission(&self, command: AdmitMessage<'_>) -> ServiceResult<LeaseDecision> {
        self.checks.fetch_add(1, Ordering::SeqCst);
        self.inner.check_submission(command).await
    }
    async fn current(&self, session_id: &str) -> ServiceResult<Option<SessionSpeakingLease>> {
        self.inner.current(session_id).await
    }
}

struct Harness {
    flow: BcsMessageFlow,
    store: Arc<RecordingLeaseStore>,
}

/// Assemble a relay-capable (caller-enabled, fenced, non-DM) session. The
/// optional relay decides the after-capture branch: `Some` state assembles
/// the ledger the capture writes to, `None` models the deployment gap.
async fn harness(relay: Option<CallerRelayState>) -> Harness {
    let _ = log_sink();
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let participants = {
        use bcs_service_api::{GroupKind, ParticipantMode, ParticipantRole};
        let mut group = support.group.get("group-1").await.unwrap();
        group.group_kind = GroupKind::Normal;
        let mut human = bcs_service_api::Participant::human("human_1", ParticipantRole::Observer);
        human.mode = Some(ParticipantMode::Present);
        group.participants = vec![
            human,
            bcs_service_api::Participant::bot("bot-driver", ParticipantRole::Driver),
        ];
        let participants = group.participants.clone();
        support.group.upsert(group).await.unwrap();
        participants
    };

    let repo = Arc::new(MemoryMessageRepo::new());
    let managed = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(16));
    let deps = Arc::new(CallerAdmissionDeps {
        handoff: handoff.clone(),
        admission: managed.clone() as Arc<dyn ManagedMessageDeliveryService>,
        control_plane: Arc::new(CallerEnabledControlPlane),
        reserve_ttl_ms: 10_000,
    });
    let checks = Arc::new(AtomicUsize::new(0));
    let store = Arc::new(RecordingLeaseStore {
        inner: MemorySpeakingLeaseRepo::new(),
        checks: checks.clone(),
    });
    let mut flow = BcsMessageFlow::new(
        support.group.clone() as Arc<dyn GroupCoreService>,
        support.routing.clone() as Arc<dyn bcs_service_api::RoutingCoreService>,
        support.registry.clone() as Arc<dyn bcs_service_api::BotRegistryCoreService>,
        support.bot_delivery.clone() as Arc<dyn bcs_service_api::BotDeliveryPort>,
        support.frontend_delivery.clone() as Arc<dyn bcs_service_api::FrontendDeliveryPort>,
    )
    .with_message_repo(repo)
    .with_managed_deliveries(managed.clone())
    .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".to_string(), 100)]))
    .with_bot_run_context(Arc::new(MemoryBotRunContextStore::new()))
    .with_session_management(Arc::new(StaticSessionManagement::new(test_session(
        SESSION, "group-1", participants,
    ))))
    .with_speaking_leases(store.clone() as Arc<dyn SpeakingLeaseRepoPort>);
    flow = match relay {
        Some(state) => flow.with_caller_relay(state),
        None => flow,
    };
    flow.caller_admission = Some(deps);
    Harness { flow, store }
}

async fn acquire(h: &Harness, holder: &str) -> WebSendLeaseClaim {
    match h
        .store
        .acquire(AcquireLease {
            session_id: "group-1",
            requester_actor_id: holder,
            now_ms: now_clock(),
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

fn send_command(
    caller: CallerContext,
    caller_ingress: Option<AuthenticatedHumanCallerIngress>,
    lease: WebSendLeaseClaim,
) -> WebSendCommand {
    WebSendCommand {
        caller_execution: None,
        caller_ingress,
        speaking_lease: Some(lease),
        caller,
        group_id: "group-1".into(),
        session_id: Some(SESSION.into()),
        from_actor_id: "human_1".into(),
        from_name: Some("Human One".into()),
        message: "hello relay diagnostics".into(),
        mentions: Vec::new(),
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    }
}

fn human_caller() -> CallerContext {
    CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() })
}

/// Drain pending async log writes so assertions see the events the send just
/// emitted (the fmt writer is synchronous on emit, but a bounded yield keeps
/// scheduler-dependent buffering honest).
async fn settle_logs() {
    tokio::time::sleep(std::time::Duration::from_millis(20)).await;
}

#[tokio::test]
async fn a_saturated_ledger_warns_and_keeps_the_ordinary_course() {
    // One live capture somewhere else already fills the (capacity-1) ledger:
    // this session's capture is refused — a WARN, never a failed message —
    // and later relays in this session stay closed.
    let ledger = Arc::new(CallerRelayLedger::new(1));
    let other = ingress("human_x");
    assert!(ledger.record(&resolve_env(), "occupied-session", "turn-0", other, now_clock(), 600_000));
    let h = harness(Some(CallerRelayState {
        ledger: ledger.clone(),
        capture_ttl_ms: 600_000,
    }))
    .await;
    let claim = acquire(&h, "human_1").await;
    let outcome = h
        .flow
        .handle_web_send(send_command(human_caller(), Some(ingress("human_1")), claim))
        .await;
    assert!(outcome.is_ok(), "a saturated ledger never fails the Human's speech: {outcome:?}");

    settle_logs().await;
    let logs = captured_logs();
    assert!(
        logs.contains("caller relay capture refused"),
        "the refusal must warn with its reason, got: {logs}"
    );
    // Fail-closed consequence: the session has nothing to redeem.
    assert!(
        ledger.redeem(&resolve_env(), SESSION, now_clock()).is_none(),
        "the refused capture leaves the session's relay closed"
    );
}

#[tokio::test]
async fn caller_capable_group_gets_human_speech_without_ingress_warns() {
    // Every capture gate passed but the message carried no strict caller
    // ingress (the shape the WS dispatcher warns about one hop earlier):
    // the single most expensive silent skip now leaves its breadcrumb.
    let h = harness(Some(CallerRelayState::new(600_000))).await;
    let claim = acquire(&h, "human_1").await;
    // The public caller context keeps the admission targets empty (no
    // Human principal to match), which is exactly how an ingress-less
    // authenticated composition reaches the capture gates.
    let outcome = h
        .flow
        .handle_web_send(send_command(CallerContext::Public, None, claim))
        .await;
    assert!(outcome.is_ok(), "an ingress-less capture skip never fails the speech: {outcome:?}");

    settle_logs().await;
    let logs = captured_logs();
    assert!(
        logs.contains("caller relay capture skipped: a caller-capable group got Human speech without a caller ingress"),
        "the missing-ingress skip must warn with its reason, got: {logs}"
    );
}

#[tokio::test]
async fn no_relay_state_assembled_warns_the_owner_to_caller_gap() {
    // A caller-capable composition whose deployment assembled no relay
    // state (explicit opt-out or an incomplete assembly): the whole
    // owner→caller inheritance would die silently — this now warns.
    let h = harness(None).await;
    let claim = acquire(&h, "human_1").await;
    let outcome = h
        .flow
        .handle_web_send(send_command(human_caller(), Some(ingress("human_1")), claim))
        .await;
    assert!(outcome.is_ok(), "the ordinary course continues without relay state: {outcome:?}");

    settle_logs().await;
    let logs = captured_logs();
    assert!(
        logs.contains("caller relay capture skipped: no relay state assembled while the group carries a caller-enabled member"),
        "the no-relay gap must warn with its reason, got: {logs}"
    );
}
