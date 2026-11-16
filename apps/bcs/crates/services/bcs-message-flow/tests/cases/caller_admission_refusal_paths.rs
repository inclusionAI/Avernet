//! Refusal-and-release coverage for the caller admission seam (spec §7.5.1
//! fail-closed set). The happy-path contract lives in
//! `caller_admission_contract.rs`; this file drives every closed refusal
//! branch and, critically, every reservation-release loop: a seam that
//! refuses must never leak a parked slot, and the sanitized error text a
//! trusted route surfaces must stay predictable (the production
//! "queue admission failed: delivery attempt or context carrier has
//! changed" 500 came through exactly this wrapper).

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue as Value};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::message_delivery::DeliveryFlowKind;
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_message_flow::caller_admission::{
    admit_caller_message, CallerAdmissionDeps, CallerAdmissionError, CallerMessageAdmission,
};
use bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_config::resolve_env_str;
use bcs_bot::core::BotControlPlaneCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_service_api::application::caller_handoff::{
    HandoffClaim, HandoffScope, HumanCallerDeliveryHandoffService,
};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::port::repo::message_delivery::{
    AdmitMessageDeliveries, DeliveryAdmissionTarget, DeliveryLookup,
};
use bcs_service_api::{
    BotControlPlaneCoreService, BotControlPlaneRepoPort, ManagedMessageDeliveryService,
    ProviderBotBindingRepoPort, ProviderRepoPort,
};
use serde_json::json;

const NOW: i64 = 1_780_000_000_000;
const RESERVE_TTL: i64 = 10_000;

struct Fixture {
    deps: CallerAdmissionDeps,
    handoff: Arc<HumanCallerDeliveryHandoff>,
}

/// Real SQLite-backed caller configuration (one enabled bot) + the real
/// handoff at a caller-chosen capacity, so capacity-exhaustion refusals are
/// arranged without concurrent racing.
async fn fixture_with_capacity(capacity: usize) -> Fixture {
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
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, bot_info, env) VALUES (?, ?, ?, ?)",
        vec![
            Value::from("bot-on".to_string()),
            Value::from("Bot bot-on".to_string()),
            Value::from(r#"{"summary":"s","caller":{"source_revision":1,"caller_identity":{"enabled":true}}}"#.to_string()),
            Value::from(resolve_env_str()),
        ],
    ))
    .await
    .expect("seed bot");
    let repo: Arc<dyn BotControlPlaneRepoPort> = Arc::new(PersistentBotRepo::new(db));
    let provider_store = Arc::new(MemoryProviderStore::default());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo,
        provider_store.clone() as Arc<dyn ProviderRepoPort>,
        provider_store as Arc<dyn ProviderBotBindingRepoPort>,
    ));
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(capacity));
    let admission: Arc<dyn ManagedMessageDeliveryService> =
        Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    Fixture {
        deps: CallerAdmissionDeps {
            handoff: handoff.clone(),
            admission,
            control_plane,
            reserve_ttl_ms: RESERVE_TTL,
        },
        handoff,
    }
}

fn ingress() -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        NOW,
        resolve_env_str(),
    )
    .expect("valid ingress")
}

fn scope(bot: &str, message_id: &str) -> HandoffScope {
    HandoffScope {
        env: resolve_env_str(),
        session_id: "session-1".into(),
        group_id: None,
        source_message_id: message_id.to_string(),
        target_bot_id: bot.into(),
    }
}

fn source_message_id() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    format!("refusal-msg-{}", COUNTER.fetch_add(1, Ordering::SeqCst))
}

fn admission(message_id: &str) -> AdmitMessageDeliveries {
    AdmitMessageDeliveries {
        display_message: None,
        message_id: message_id.into(),
        flow_kind: DeliveryFlowKind::Group,
        now_ms: NOW,
        expire_at_ms: None,
        event: None,
        message: NewMessage {
            visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
            audience: None,
            group_id: "group".into(),
            session_id: "session-1".into(),
            sender_id: "human_alice".into(),
            sender_type: SenderType::Human,
            message_type: "chat".into(),
            content: json!({"text": message_id}),
            client_msg_id: Some(message_id.to_string()),
            owner_bot_id: None,
            created_at: NOW as u64,
            run_id: String::new(),
        },
        targets: vec![DeliveryAdmissionTarget {
            run_id: None,
            rejection: None,
            target_bot_id: "bot-on".into(),
            kind: DeliveryType::Send,
            max_queued: 10,
            semantic_projection_json: json!({"version": 1}),
        }],
    }
}

fn caller(bot: &str, message_id: &str) -> CallerMessageAdmission {
    CallerMessageAdmission {
        scope: Some(scope(bot, message_id)),
        extra_scopes: Vec::new(),
        delegation_scopes: Vec::new(),
        ingress: ingress(),
        sender_name: String::new(),
        admission: admission(message_id),
    }
}

/// A parked slot must be free again after a refusal released it. The
/// re-reserve is released immediately so the probe itself never consumes
/// the fixture's capacity budget.
async fn assert_slot_released(f: &Fixture, bot: &str, message_id: &str) {
    let probe = scope(bot, message_id);
    match f.handoff.reserve(&probe, RESERVE_TTL, NOW).await {
        Ok(()) => {
            f.handoff.release(&probe).await.expect("probe release");
        }
        Err(e) => panic!("slot for {bot} not released: {e:?}"),
    }
}

/// admit_caller_message returns a non-Debug outcome on success; extract the
/// refusal without unwrap_err's Debug bound.
async fn refuse(
    deps: &CallerAdmissionDeps,
    args: CallerMessageAdmission,
    now_ms: i64,
) -> CallerAdmissionError {
    match admit_caller_message(deps, args, now_ms).await {
        Err(error) => error,
        Ok(_) => panic!("expected a refusal, got an admission outcome"),
    }
}

#[tokio::test]
async fn sender_identity_mismatch_refuses_without_reserving() {
    let f = fixture_with_capacity(16).await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id);
    args.admission.message.sender_id = "human_bob".into();
    let refused = refuse(&f.deps, args, NOW).await;
    assert!(matches!(refused, CallerAdmissionError::IdentityMismatch));
    assert_slot_released(&f, "bot-on", &message_id).await;
    // And nothing reached persistence: the message repo never saw the id.
    let looked_up = f
        .deps
        .admission
        .lookup(DeliveryLookup::Ids(vec![message_id]))
        .await
        .expect("lookup must succeed");
    assert!(looked_up.is_empty(), "a refusal must not persist anything");
}

#[tokio::test]
async fn no_scope_of_any_kind_refuses_the_admission() {
    let f = fixture_with_capacity(16).await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id);
    args.scope = None;
    let refused = refuse(&f.deps, args, NOW).await;
    match refused {
        CallerAdmissionError::Admission(text) => {
            assert!(text.contains("one target scope"), "unexpected text: {text}");
        }
        other => panic!("expected the shape gate, got {other:?}"),
    }
}

#[tokio::test]
async fn an_invalid_clock_refuses_via_the_handoff_taxonomy() {
    // now_ms = 0 makes the very first reserve fail with InvalidInput; the
    // closed taxonomy maps every handoff refusal onto HandoffRefused — no
    // raw handoff error ever crosses the seam.
    let f = fixture_with_capacity(16).await;
    let args = caller("bot-on", &source_message_id());
    let refused = refuse(&f.deps, args, 0).await;
    assert!(matches!(refused, CallerAdmissionError::HandoffRefused));
}

#[tokio::test]
async fn extra_scope_capacity_conflict_releases_the_primary_reservation() {
    let f = fixture_with_capacity(1).await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id);
    args.extra_scopes = vec![scope("bot-second", &message_id)];
    let refused = refuse(&f.deps, args, NOW).await;
    assert!(matches!(refused, CallerAdmissionError::HandoffRefused));
    assert_slot_released(&f, "bot-on", &message_id).await;
    // The unreservable extra never parked anything either: its slot is
    // still free for a fresh reservation.
    f.handoff
        .reserve(&scope("bot-second", &message_id), RESERVE_TTL, NOW)
        .await
        .expect("the refused extra scope never held a slot");
}

#[tokio::test]
async fn delegation_capacity_conflict_releases_every_earlier_reservation() {
    let f = fixture_with_capacity(2).await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id);
    args.delegation_scopes = vec![
        scope("bot-deleg-1", &message_id),
        scope("bot-deleg-2", &message_id),
    ];
    let refused = refuse(&f.deps, args, NOW).await;
    assert!(matches!(refused, CallerAdmissionError::HandoffRefused));
    // capacity 2 = primary + deleg-1 got reserved, then deleg-2 refused:
    // the refusal must release everything reserved before it.
    assert_slot_released(&f, "bot-on", &message_id).await;
    assert_slot_released(&f, "bot-deleg-1", &message_id).await;
    assert_slot_released(&f, "bot-deleg-2", &message_id).await;
}

#[tokio::test]
async fn textless_content_with_delegation_releases_all_reservations() {
    let f = fixture_with_capacity(16).await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id);
    args.admission.message.content = json!({});
    args.delegation_scopes = vec![scope("bot-owner", &message_id)];
    let refused = refuse(&f.deps, args, NOW).await;
    match refused {
        CallerAdmissionError::Admission(text) => {
            assert!(text.contains("protected text"), "unexpected text: {text}");
        }
        other => panic!("expected the protected-text refusal, got {other:?}"),
    }
    assert_slot_released(&f, "bot-on", &message_id).await;
    assert_slot_released(&f, "bot-owner", &message_id).await;
}

#[tokio::test]
async fn an_unavailable_admission_releases_every_reservation_and_sanitizes_text() {
    // set_admission_available(false) is the runtime's recovery posture: the
    // managed queue refuses new admissions. The seam must release every
    // reservation and report the managed Conflict text verbatim — this is
    // the exact text the trusted route wraps into "queue admission failed".
    let f = fixture_with_capacity(16).await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id);
    args.delegation_scopes = vec![scope("bot-owner", &message_id)];
    let unavailable = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    unavailable.set_admission_available(false);
    let deps = CallerAdmissionDeps {
        handoff: f.deps.handoff.clone(),
        admission: unavailable.clone(),
        control_plane: f.deps.control_plane.clone(),
        reserve_ttl_ms: RESERVE_TTL,
    };
    let refused = refuse(&deps, args, NOW).await;
    match refused {
        CallerAdmissionError::Admission(text) => {
            assert_eq!(
                text, "delivery attempt or context carrier has changed",
                "the sanitized Managed Conflict text must surface unchanged"
            );
        }
        other => panic!("expected the admission refusal, got {other:?}"),
    }
    assert_slot_released(&f, "bot-on", &message_id).await;
    assert_slot_released(&f, "bot-owner", &message_id).await;
    // The conflict also never persisted the canonical message.
    let rows = unavailable
        .lookup(DeliveryLookup::Ids(vec![message_id.clone()]))
        .await
        .expect("lookup must succeed");
    assert!(rows.is_empty(), "the refused admission must not persist");
}

/// A full multi-target duplicate replay: with every scope's slot already
/// Published by the first admission, the replay marks slot_already_bound on
/// the primary, every extra and every delegation scope, keeps the original
/// association (no re-park), and frees none of the Published slots onto
/// which the first identity is still parked.
#[tokio::test]
async fn a_multi_target_duplicate_replay_keeps_the_first_association() {
    let f = fixture_with_capacity(16).await;
    let message_id = source_message_id();
    let mut first = caller("bot-on", &message_id);
    first.extra_scopes = vec![scope("bot-second", &message_id)];
    first.delegation_scopes = vec![scope("bot-owner", &message_id)];
    for bot in ["bot-second", "bot-owner"] {
        first.admission.targets.push(DeliveryAdmissionTarget {
            run_id: None,
            rejection: None,
            target_bot_id: bot.into(),
            kind: DeliveryType::Send,
            max_queued: 10,
            semantic_projection_json: json!({"version": 1}),
        });
    }
    let first_outcome = admit_caller_message(&f.deps, first, NOW).await.expect("first admission");
    let first_primary = first_outcome
        .result
        .deliveries
        .iter()
        .find(|row| row.target_bot_id == "bot-on")
        .expect("primary delivery row")
        .clone();

    // The replay: identical scopes, same canonical message. Every reserve
    // now answers AlreadyPublished — the whole admission is a duplicate.
    let mut replay = caller("bot-on", &message_id);
    replay.extra_scopes = vec![scope("bot-second", &message_id)];
    replay.delegation_scopes = vec![scope("bot-owner", &message_id)];
    for bot in ["bot-second", "bot-owner"] {
        replay.admission.targets.push(DeliveryAdmissionTarget {
            run_id: None,
            rejection: None,
            target_bot_id: bot.into(),
            kind: DeliveryType::Send,
            max_queued: 10,
            semantic_projection_json: json!({"version": 1}),
        });
    }
    let replay_outcome = admit_caller_message(&f.deps, replay, NOW).await.expect("duplicate replay must not refuse");
    assert!(replay_outcome.result.duplicate, "the replay is a duplicate");

    // Published slots survive the replay: the first identity stays parked
    // on its own receipts and is claimable exactly once.
    let parked = f
        .handoff
        .claim(
            &scope("bot-on", &message_id),
            HandoffClaim::new(first_primary.delivery_id.clone(), first_primary.state.state_version, None, 1, NOW + 1_000),
        )
        .await
        .expect("the first admission's identity must survive the replay");
    assert_eq!(parked.actor().actor_id, "human_alice");
}
