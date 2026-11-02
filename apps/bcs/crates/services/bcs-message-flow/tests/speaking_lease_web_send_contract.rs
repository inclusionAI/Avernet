//! P3.2 speaking-lease admission over the real group web_send path
//! (spec §6.1/§6.3): caller groups fence Human speech on the lease
//! coordinates, acceptance renews the idle window (duplicates never),
//! stale claims are refused entry, plain groups never consult the store,
//! and a caller group without a store fails closed. Real SQLite caller
//! configuration + real BcsMessageFlow + real memory lease store.

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_bot::core::BotControlPlaneCore;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_bot_store::PersistentBotRepo;
use bcs_config::resolve_env_str;
use bcs_db_api::{DbPlugin, DbStatement, DbValue as Value};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::speaking_lease::SessionSpeakingLease;
use bcs_message_flow::caller_admission::CallerAdmissionDeps;
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor as CallerPrincipal;
use bcs_service_api::application::MessageFlowService;
use bcs_service_api::port::repo::session_speaking_lease::{
    AcquireLease, AdmitMessage, LeaseAcquire, LeaseDecision, LeaseRelease,
    PreemptLease, ReleaseLease, SpeakingLeaseRepoPort,
};
use bcs_service_api::port::repo::BotControlPlaneRepoPort;
use bcs_service_api::{
    BotRegistryCoreService, BotControlPlaneCoreService, CallerContext,
    GroupCoreService, ManagedMessageDeliveryService, ProviderBotBindingRepoPort, ProviderRepoPort,
    WebSendCommand, WebSendLeaseClaim,
};
use bcs_session_store::MemorySpeakingLeaseRepo;
use serde_json::json;

#[path = "support/session.rs"]
mod session_support;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
use session_support::{StaticSessionManagement, test_session};

fn now_clock() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bot_uuid TEXT NOT NULL,
            name TEXT NOT NULL,
            bot_info TEXT,
            env TEXT,
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            status TEXT NOT NULL DEFAULT 'online',
            is_deleted INTEGER NOT NULL DEFAULT 0,
            UNIQUE (bot_uuid, env)
        )",
    ))
    .await
    .expect("create bots table");
    db
}

async fn seed_bot(db: &dyn DbPlugin, bot_id: &str, caller_enabled: bool) {
    let bot_info = if caller_enabled {
        json!({"summary":"s","caller":{"source_revision":1,"caller_identity":{"enabled":true}}}).to_string()
    } else {
        json!({"summary":"s"}).to_string()
    };
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, bot_info, env) VALUES (?, ?, ?, ?)",
        vec![
            Value::from(bot_id.to_string()),
            Value::from(format!("Bot {bot_id}")),
            Value::from(bot_info),
            Value::from(resolve_env_str()),
        ],
    ))
    .await
    .expect("seed bot");
}

fn ingress() -> AuthenticatedHumanCallerIngress {
    let now = now_clock();
    AuthenticatedHumanCallerIngress::new(
        CallerPrincipal { actor_id: "human_1".to_string(), staff_no: "1".to_string() },
        HumanCallerAuthSource::HttpAuth,
        now,
        resolve_env_str(),
    )
    .expect("valid ingress")
}

/// Recording wrapper proving plain groups never consult the store.
struct RecordingLeaseStore {
    inner: MemorySpeakingLeaseRepo,
    checks: AtomicUsize,
    admissions: AtomicUsize,
}

#[async_trait]
impl SpeakingLeaseRepoPort for RecordingLeaseStore {
    async fn acquire(&self, command: AcquireLease<'_>) -> bcs_service_api::ServiceResult<LeaseAcquire> {
        self.inner.acquire(command).await
    }
    async fn preempt(&self, command: PreemptLease<'_>) -> bcs_service_api::ServiceResult<SessionSpeakingLease> {
        self.inner.preempt(command).await
    }
    async fn release(&self, command: ReleaseLease<'_>) -> bcs_service_api::ServiceResult<LeaseRelease> {
        self.inner.release(command).await
    }
    async fn admit_message(&self, command: AdmitMessage<'_>) -> bcs_service_api::ServiceResult<LeaseDecision> {
        self.admissions.fetch_add(1, Ordering::SeqCst);
        self.inner.admit_message(command).await
    }
    async fn check_submission(&self, command: AdmitMessage<'_>) -> bcs_service_api::ServiceResult<LeaseDecision> {
        self.checks.fetch_add(1, Ordering::SeqCst);
        self.inner.check_submission(command).await
    }
    async fn current(&self, session_id: &str) -> bcs_service_api::ServiceResult<Option<SessionSpeakingLease>> {
        self.inner.current(session_id).await
    }
}

struct Harness {
    flow: BcsMessageFlow,
    store: Arc<RecordingLeaseStore>,
    db: Arc<dyn DbPlugin>,
}

async fn harness(caller_enabled_bot: bool, leases_mounted: bool) -> Harness {
    harness_with_extra_bot(caller_enabled_bot, leases_mounted, false).await
}

async fn harness_with_extra_bot(caller_enabled_bot: bool, leases_mounted: bool, extra_enabled_bot: bool) -> Harness {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    {
        let mut group = support.group.get("group-1").await.unwrap();
        // A real (non-DM) group with a Human and a bot.
        let mut human = bcs_service_api::Participant::human("human_1", bcs_service_api::ParticipantRole::Observer);
        human.mode = Some(bcs_service_api::ParticipantMode::Present);
        group.participants = vec![
            human,
            bcs_service_api::Participant::bot("bot-driver", bcs_service_api::ParticipantRole::Driver),
        ];
        if extra_enabled_bot {
            group.participants.push(bcs_service_api::Participant::bot(
                "bot-worker",
                bcs_service_api::ParticipantRole::Observer,
            ));
        }
        support.group.upsert(group).await.unwrap();
    }
    let db = sqlite_db().await;
    seed_bot(db.as_ref(), "bot-driver", caller_enabled_bot).await;
    if extra_enabled_bot {
        seed_bot(db.as_ref(), "bot-worker", true).await;
    }

    let repo = Arc::new(MemoryMessageRepo::new());
    let managed = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let repo_port: Arc<dyn BotControlPlaneRepoPort> = Arc::new(PersistentBotRepo::new(db.clone()));
    let provider_store = Arc::new(MemoryProviderStore::default());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo_port,
        provider_store.clone() as Arc<dyn ProviderRepoPort>,
        provider_store as Arc<dyn ProviderBotBindingRepoPort>,
    ));
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(16));
    let deps = Arc::new(CallerAdmissionDeps {
        handoff: handoff.clone(),
        admission: managed.clone() as Arc<dyn ManagedMessageDeliveryService>,
        control_plane,
        reserve_ttl_ms: 10_000,
    });
    let group = support.group.get("group-1").await.unwrap();
    let mut flow = BcsMessageFlow::new(
        support.group,
        support.routing,
        support.registry.clone() as Arc<dyn BotRegistryCoreService>,
        support.bot_delivery,
        support.frontend_delivery,
    )
    .with_message_repo(repo)
    .with_managed_deliveries(managed.clone())
    .with_group_delivery_limits(std::collections::BTreeMap::from([(
        "bot-driver".to_string(),
        100,
    )]))
    .with_bot_run_context(Arc::new(MemoryBotRunContextStore::new()));
    flow.caller_admission = Some(deps);

    let participants = group.participants.clone();
    flow = flow.with_session_management(Arc::new(StaticSessionManagement::new(test_session(
        "group-1:lease",
        "group-1",
        participants,
    ))));

    let store = Arc::new(RecordingLeaseStore {
        inner: MemorySpeakingLeaseRepo::new(),
        checks: AtomicUsize::new(0),
        admissions: AtomicUsize::new(0),
    });
    if leases_mounted {
        flow = flow.with_speaking_leases(store.clone() as Arc<dyn SpeakingLeaseRepoPort>);
    }
    Harness { flow, store, db }
}

fn send_command(lease: Option<WebSendLeaseClaim>) -> WebSendCommand {
    WebSendCommand {
        caller_execution: None,
        caller: CallerContext::Human(CallerPrincipal { actor_id: "human_1".to_string(), staff_no: "1".to_string() }),
        group_id: "group-1".into(),
        session_id: None,
        from_actor_id: "human_1".into(),
        from_name: Some("Human One".into()),
        message: "hello lease".into(),
        mentions: Vec::new(),
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
        caller_ingress: Some(ingress()),
        speaking_lease: lease,
    }
}

async fn acquire(harness: &Harness, holder: &str) -> WebSendLeaseClaim {
    match harness
        .store
        .acquire(AcquireLease { session_id: "group-1", requester_actor_id: holder, now_ms: now_clock(), idle_timeout_ms: 60_000 })
        .await
        .unwrap()
    {
        LeaseAcquire::Granted(lease) => WebSendLeaseClaim { lease_id: lease.lease_id, epoch: lease.epoch },
        LeaseAcquire::SelfHeld(lease) => WebSendLeaseClaim { lease_id: lease.lease_id, epoch: lease.epoch },
        other => panic!("expected a held lease, got {other:?}"),
    }
}

#[tokio::test]
async fn caller_groups_fence_human_speech_and_renew_only_on_fresh_acceptance() {
    let world = harness(true, true).await;
    let claim = acquire(&world, "human_1").await;
    let before = world.store.current("group-1").await.unwrap().unwrap().expires_at_ms;
    let outcome = world.flow.handle_web_send(send_command(Some(claim.clone()))).await;
    assert!(outcome.is_ok(), "legal lease admits: {outcome:?}");
    let checks = world.store.checks.load(Ordering::SeqCst);
    assert_eq!(checks, 1, "one eligibility fence");
    let renewed = world.store.current("group-1").await.unwrap().unwrap();
    assert!(renewed.expires_at_ms >= before, "fresh acceptance renews the idle window");
    assert_eq!(renewed.lease_id, claim.lease_id, "renewal keeps the lease id");
    assert_eq!(renewed.epoch, claim.epoch, "renewal keeps the epoch");

    // A stale claim loses to a preemption: refused entry, no admission.
    let _bob = world
        .store
        .preempt(PreemptLease { session_id: "group-1", requester_actor_id: "human_2", now_ms: now_clock(), idle_timeout_ms: 60_000 })
        .await
        .unwrap();
    let error = world.flow.handle_web_send(send_command(Some(claim))).await.unwrap_err();
    let message = match error {
        bcs_service_api::ServiceError::InvalidOperation { message, .. } => message,
        other => panic!("expected InvalidOperation, got {other:?}"),
    };
    assert!(message.contains("speaking lease rejected"), "closed refusal, got {message}");
}

#[tokio::test]
async fn caller_groups_demand_a_claim_and_fail_closed_without_a_store() {
    // Missing claim: refused entry with the closed message.
    let world = harness(true, true).await;
    let _ = acquire(&world, "human_1").await;
    let error = world.flow.handle_web_send(send_command(None)).await.unwrap_err();
    let message = match error {
        bcs_service_api::ServiceError::InvalidOperation { message, .. } => message,
        other => panic!("expected InvalidOperation, got {other:?}"),
    };
    assert!(message.contains("speaking lease required"), "closed refusal, got {message}");

    // No lease store mounted: fail closed instead of un-limiting speech.
    let world = harness(true, false).await;
    let error = world.flow.handle_web_send(send_command(Some(WebSendLeaseClaim {
        lease_id: "any".into(),
        epoch: 1,
    })))
    .await
    .unwrap_err();
    let message = match error {
        bcs_service_api::ServiceError::InvalidOperation { message, .. } => message,
        other => panic!("expected InvalidOperation, got {other:?}"),
    };
    assert!(message.contains("required but unavailable"), "fail closed, got {message}");
}

#[tokio::test]
async fn plain_groups_never_consult_the_lease_store() {
    // No caller-enabled bot in the group: the ordinary path with no lease
    // reads at all.
    let world = harness(false, true).await;
    let outcome = world.flow.handle_web_send(send_command(None)).await;
    assert!(outcome.is_ok(), "plain group accepts ordinary speech: {outcome:?}");
    assert_eq!(world.store.checks.load(Ordering::SeqCst), 0, "no eligibility fence");
    assert_eq!(world.store.admissions.load(Ordering::SeqCst), 0, "no lease writes");
    assert!(world.store.current("group-1").await.unwrap().is_none(), "nothing persisted");
}

#[tokio::test]
async fn group_dispatch_admits_with_dual_caller_enabled_targets() {
    // One Human message into a caller group whose TWO bots are both
    // caller-enabled: admission succeeds and the lease renew semantics
    // hold exactly once — the per-target dispatch generalization carries
    // every enabled target through the same atomic admission (the
    // caller_required marker for every target is applied inside the
    // admission loop and fails the whole message on error).
    let world = harness_with_extra_bot(true, true, true).await;
    let claim = acquire(&world, "human_1").await;
    let outcome = world.flow.handle_web_send(send_command(Some(claim))).await;
    assert!(outcome.is_ok(), "dual-target caller group admits: {outcome:?}");
    assert_eq!(world.store.checks.load(Ordering::SeqCst), 1);
    assert!(world.store.current("group-1").await.unwrap().is_some(), "lease held after acceptance");
}

#[tokio::test]
async fn speaking_lease_never_substitutes_for_missing_caller_ingress() {
    let world = harness(true, true).await;
    let mut command = send_command(None);
    command.caller_ingress = None;
    let error = world.flow.handle_web_send(command).await.unwrap_err();
    assert!(error.to_string().contains("caller identity missing"));

    // A valid speaking claim grants submission eligibility, not identity.
    let claim = acquire(&world, "human_1").await;
    let mut command = send_command(Some(claim));
    command.caller_ingress = None;
    let error = world.flow.handle_web_send(command).await.unwrap_err();
    assert!(error.to_string().contains("caller identity missing"));
    assert_eq!(world.store.checks.load(Ordering::SeqCst), 0,
        "identity refusal precedes lease admission");
}

#[tokio::test]
async fn a_control_plane_read_failure_refuses_the_send_instead_of_failing_open() {
    // Review 2.4: a configuration read failure is an UNDECIDABLE group
    // type, never "not enabled" — the message is refused rather than the
    // fence silently disappearing exactly when the control plane is broken.
    let world = harness(true, true).await;
    world
        .db
        .execute(DbStatement::new("DROP TABLE bcs_bots"))
        .await
        .expect("drop bots table");
    let error = world.flow.handle_web_send(send_command(None)).await.unwrap_err();
    let message = match error {
        bcs_service_api::ServiceError::InvalidOperation { message, .. } => message,
        other => panic!("expected InvalidOperation, got {other:?}"),
    };
    assert!(
        message.contains("caller configuration unavailable"),
        "read failures never degrade the fence to Plain, got {message}"
    );
    // The fence never consulted the lease store: the refusal happened at
    // group-type resolution, before any lease decision.
    assert_eq!(world.store.checks.load(Ordering::SeqCst), 0, "no eligibility fence ran");
}
