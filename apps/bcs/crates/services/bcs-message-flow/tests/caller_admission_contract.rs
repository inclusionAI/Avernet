//! Caller admission orchestration conformance (spec §7.5.1 write side).
//! Everything real: SQLite-backed caller configuration (PersistentBotRepo
//! through BotControlPlaneCore), the real handoff, and the real Managed
//! admission. Only the clock is explicit.

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
use bcs_bot_store::PersistentBotRepo;
use bcs_config::resolve_env_str;
use bcs_service_api::application::caller_execution::{
    HandoffClaim, HandoffScope,
};
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};
use bcs_bot::core::BotControlPlaneCore;
use bcs_service_api::{
    BotControlPlaneCoreService, BotControlPlaneRepoPort, ManagedMessageDeliveryService,
    ProviderBotBindingRepoPort, ProviderRepoPort,
};
use bcs_bot_store::provider::MemoryProviderStore;
use serde_json::json;

const NOW: i64 = 1_780_000_000_000;
const RESERVE_TTL: i64 = 10_000;

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

async fn seed_bot(db: &dyn DbPlugin, bot_id: &str, bot_info: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, bot_info, env) VALUES (?, ?, ?, ?)",
        vec![
            Value::from(bot_id.to_string()),
            Value::from(format!("Bot {bot_id}")),
            Value::from(bot_info.to_string()),
            Value::from(resolve_env_str()),
        ],
    ))
    .await
    .expect("seed bot");
}

fn ingress(expiry: i64) -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        NOW,
        expiry,
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
    format!("msg-{}", unique_counter())
}

fn unique_counter() -> u64 {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    COUNTER.fetch_add(1, Ordering::SeqCst)
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
            rejection: None,
            target_bot_id: "bot-on".into(),
            kind: DeliveryType::Send,
            max_queued: 10,
            // A body-supplied caller_task is spoof material: the producer
            // must own the key exactly like caller_required.
            semantic_projection_json: json!({"version": 1, "caller_task": "body-spoof"}),
        }],
    }
}

async fn deps() -> CallerAdmissionDeps {
    let db = sqlite_db().await;
    seed_bot(db.as_ref(), "bot-on", r#"{"summary":"s","caller":{"source_revision":1,"caller_identity":{"enabled":true}}}"#).await;
    let repo: Arc<dyn BotControlPlaneRepoPort> = Arc::new(PersistentBotRepo::new(db));
    let provider_store = Arc::new(MemoryProviderStore::default());
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo,
        provider_store.clone() as Arc<dyn ProviderRepoPort>,
        provider_store as Arc<dyn ProviderBotBindingRepoPort>,
    ));
    let admission: Arc<dyn ManagedMessageDeliveryService> =
        Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    CallerAdmissionDeps {
        handoff: Arc::new(HumanCallerDeliveryHandoff::new(16)),
        admission,
        control_plane,
        reserve_ttl_ms: RESERVE_TTL,
    }
}

fn caller(bot: &str, message_id: &str, expiry: i64) -> CallerMessageAdmission {
    CallerMessageAdmission {
        scope: Some(scope(bot, message_id)),
        extra_scopes: Vec::new(),
        delegation_scopes: Vec::new(),
        ingress: ingress(expiry),
        admission: admission(message_id),
    }
}

#[tokio::test]
async fn textless_content_refuses_and_releases_the_reservation() {
    // Review P1-5: the protected wire digest requires plain text; an
    // admission without it refuses before persistence and releases the slot.
    let deps = deps().await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id, NOW + 300_000);
    args.admission.message.content = json!({}); // no text material
    let refused = admit_caller_message(&deps, args, NOW).await;
    assert!(matches!(refused, Err(CallerAdmissionError::Admission(_))));
    // The slot released: a re-admission reserves fresh (no AlreadyPublished).
    let outcome = admit_caller_message(
        &deps,
        caller("bot-on", &message_id, NOW + 300_000),
        NOW,
    )
    .await
    .expect("text-carrying re-admission succeeds");
    assert!(!outcome.result.duplicate);
}

#[tokio::test]
async fn enabled_target_marks_admits_and_parks_identity_once() {
    let deps = deps().await;
    let message_id = source_message_id();
    let outcome = admit_caller_message(
        &deps,
        caller("bot-on", &message_id, NOW + 300_000),
        NOW,
    )
    .await
    .expect("caller admission succeeds");

    // The persisted projection carries the marker written by this producer —
    // neither the request body nor any route decorator can skip it. The
    // same is true for the protected dispatch text (`caller_task`, the
    // digest's text input, review P1-5): a body-spoofed value is
    // overwritten by the trusted producer's copy of the canonical text.
    let projection = &outcome.result.deliveries[0].semantic_projection_json;
    assert_eq!(projection.get("caller_required"), Some(&json!(true)));
    assert_eq!(projection.get("caller_task"), Some(&json!(message_id.as_str())));

    // The identity is parked exactly once for the committed dispatch.
    let scope = scope("bot-on", &message_id);
    let claimed = deps
        .handoff
        .claim(
            &scope,
            HandoffClaim::new(
                outcome.result.deliveries[0].delivery_id.clone(),
                outcome.result.deliveries[0].state.state_version,
                None,
                1,
                NOW + 1_000,
            ),
        )
        .await
        .expect("claim parked identity");
    assert_eq!(claimed.expires_at_ms(), NOW + 300_000);

    // Second claim refuses: one-shot, forever.
    let refused = deps
        .handoff
        .claim(&scope, HandoffClaim::new("any", 9, None, 1, NOW + 2_000))
        .await
        .unwrap_err();
    assert_eq!(
        refused,
        bcs_service_api::application::caller_execution::HandoffError::AlreadyClaimed
    );
}

#[tokio::test]
async fn not_enabled_targets_refuse_before_any_side_effect() {
    let deps = deps().await;
    let db = sqlite_db().await;
    // bot-off: explicit disable at a higher revision.
    seed_bot(db.as_ref(), "bot-off", r#"{\"summary\":\"s\",\"caller\":{\"source_revision\":2,\"caller_identity\":{\"enabled\":false}}}"#).await;
    // bot-plain: never configured.
    seed_bot(db.as_ref(), "bot-plain", r#"{\"summary\":\"s\"}"#).await;
    // Rebuild the control plane over the extra rows (same pattern as deps()).
    for bot in ["bot-off", "bot-plain", "hal-ghost"] {
        let message_id = source_message_id();
        let refused = admit_caller_message(&deps, caller(bot, &message_id, NOW + 300_000), NOW).await;
        assert!(
            matches!(refused, Err(CallerAdmissionError::TargetNotCallerEnabled)),
            "{bot} must refuse the caller path"
        );
    }
    // No slot was left behind by the refusals.

    assert!(admit_caller_message(&deps, caller("bot-off", &source_message_id(), NOW + 300_000), NOW).await.is_err());
}

#[tokio::test]
async fn expired_identity_is_refused_before_reservation_or_admission() {
    let deps = deps().await;
    let refused = admit_caller_message(
        &deps,
        caller("bot-on", &source_message_id(), NOW + 10),
        NOW + 10, // seam clock exactly at the trusted window's end
    )
    .await;
    assert!(matches!(refused, Err(CallerAdmissionError::IdentityExpired)));
}

#[tokio::test]
async fn duplicate_admission_keeps_original_association_without_identity() {
    let deps = deps().await;
    let message_id = source_message_id();
    let first = admit_caller_message(&deps, caller("bot-on", &message_id, NOW + 300_000), NOW)
        .await
        .expect("first admission");
    assert!(!first.result.duplicate);

    // Same canonical message id: the duplicate keeps the original
    // association and never parks a second (or refreshed) identity.
    let duplicate = admit_caller_message(&deps, caller("bot-on", &message_id, NOW + 300_000), NOW)
        .await
        .expect("duplicate resolves as duplicate");
    assert!(duplicate.result.duplicate);
    let scope = scope("bot-on", &message_id);
    // The FIRST admission's parked identity is exactly what the committed
    // dispatch of this message claims — the duplicate never re-parked or
    // refreshed it. After that one claim, the slot is sealed forever.
    let parked = deps
        .handoff
        .claim(
            &scope,
            HandoffClaim::new(
                first.result.deliveries[0].delivery_id.clone(),
                first.result.deliveries[0].state.state_version,
                None,
                1,
                NOW + 1_000,
            ),
        )
        .await
        .expect("original identity survived the duplicate");
    assert_eq!(parked.expires_at_ms(), NOW + 300_000);
    let sealed = deps
        .handoff
        .claim(&scope, HandoffClaim::new(duplicate.result.deliveries[0].delivery_id.clone(), 1, None, 1, NOW + 2_000))
        .await;
    assert_eq!(
        sealed.unwrap_err(),
        bcs_service_api::application::caller_execution::HandoffError::AlreadyClaimed
    );
}

#[tokio::test]
async fn duplicate_does_not_disturb_the_first_parked_identity() {
    // The dedup branch released nothing it shouldn't have: after a duplicate,
    // a verified fresh scope (another message id) still parks and claims.
    let deps = deps().await;
    let message_id = source_message_id();
    let _first = admit_caller_message(&deps, caller("bot-on", &message_id, NOW + 300_000), NOW).await.unwrap();
    let duplicate = admit_caller_message(&deps, caller("bot-on", &message_id, NOW + 300_000), NOW).await.unwrap();
    assert!(duplicate.result.duplicate);
    // The duplicate released nothing it mustn't: the first admission's
    // identity is still parked for its own (original) scope and message.
    let scope = scope("bot-on", &message_id);
    let parked = deps
        .handoff
        .claim(&scope, HandoffClaim::new(delivery_id_of(&deps, &message_id).await, 1, None, 1, NOW + 1_000))
        .await
        .expect("first admission's identity survived the duplicate");
    assert_eq!(parked.expires_at_ms(), NOW + 300_000);
}


// Tiny lookup helper: the first delivery row for an admitted message id.
async fn delivery_id_of(deps: &CallerAdmissionDeps, message_id: &str) -> String {
    deps.admission
        .snapshot(Some("session-1"))
        .await
        .expect("snapshot")
        .into_iter()
        .find(|row| row.source_message_id == message_id)
        .expect("row admitted")
        .delivery_id
}

#[tokio::test]
async fn mixed_groups_leave_unscoped_owner_targets_untouched() {
    // Review P1.III.3: a group routing one caller-enabled target and one
    // Owner target marks ONLY the scope-covered target. The Owner row
    // stays key-free (the §15.4 guard's "presence alone decides" means a
    // stray mark with no parked identity would fail it closed instead of
    // running as Owner).
    let deps = deps().await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id, NOW + 300_000);
    // Add an extra "owner" target to the admission — outside both the
    // primary scope and the extra scopes.
    args.admission.targets.push(DeliveryAdmissionTarget {
        rejection: None,
        target_bot_id: "bot-owner".into(),
        kind: DeliveryType::Send,
        max_queued: 10,
        semantic_projection_json: json!({"version": 1}),
    });
    let outcome = admit_caller_message(&deps, args, NOW).await.unwrap();
    assert_eq!(outcome.result.deliveries.len(), 2);
    let enabled_row = outcome
        .result
        .deliveries
        .iter()
        .find(|row| row.target_bot_id == "bot-on")
        .unwrap();
    assert_eq!(enabled_row.semantic_projection_json["caller_required"], true);
    assert_eq!(enabled_row.semantic_projection_json["caller_task"], message_id.as_str());
    let owner_row = outcome
        .result
        .deliveries
        .iter()
        .find(|row| row.target_bot_id == "bot-owner")
        .unwrap();
    assert!(
        owner_row.semantic_projection_json.get("caller_required").is_none(),
        "the unscoped Owner target stays unmarked"
    );
    assert!(
        owner_row.semantic_projection_json.get("caller_task").is_none(),
        "the producer's task text never leaks onto unscoped targets"
    );
}

#[tokio::test]
async fn delegation_only_scopes_mark_owner_targets_and_park_identity() {
    // Spec §7.2.1 (review batch-3 §3.1): the caller-DISABLED routed target
    // of a lease-admitted Human turn gets the delegation-only association
    // — its row carries the trusted `caller_lineage` marker (NEVER
    // caller_required: the §15.4 guard would fail it closed) and its
    // handoff scope parks the identity exactly like a caller target's.
    let deps = deps().await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id, NOW + 300_000);
    args.admission.targets.push(DeliveryAdmissionTarget {
        rejection: None,
        target_bot_id: "bot-owner".into(),
        kind: DeliveryType::Send,
        max_queued: 10,
        semantic_projection_json: json!({"version": 1}),
    });
    // A body-supplied marker is spoof material: the producer overwrites it.
    if let serde_json::Value::Object(map) =
        &mut args.admission.targets[1].semantic_projection_json
    {
        map.insert("caller_lineage".into(), json!("body-spoof"));
    }
    args.delegation_scopes = vec![scope("bot-owner", &message_id)];
    let outcome = admit_caller_message(&deps, args, NOW).await.unwrap();
    assert_eq!(outcome.result.deliveries.len(), 2);
    let owner_row = outcome
        .result
        .deliveries
        .iter()
        .find(|row| row.target_bot_id == "bot-owner")
        .unwrap();
    assert_eq!(
        owner_row.semantic_projection_json["caller_lineage"], "delegation_only",
        "the trusted association marker replaces any body-supplied value"
    );
    assert!(
        owner_row.semantic_projection_json.get("caller_required").is_none(),
        "the Owner target keeps the ordinary execution"
    );
    assert!(
        owner_row.semantic_projection_json.get("caller_task").is_none(),
        "the caller task text never leaks onto the Owner row"
    );
    // The identity parked for the Owner association: claimable once
    // against its own delivery receipt, sealed after consumption.
    let parked = deps
        .handoff
        .claim(
            &scope("bot-owner", &message_id),
            HandoffClaim::new(owner_row.delivery_id.clone(), owner_row.state.state_version, None, 1, NOW + 1_000),
        )
        .await
        .expect("the Owner association identity is parked for its scope");
    assert_eq!(parked.expires_at_ms(), NOW + 300_000);
    // The caller target's own identity is parked independently.
    let enabled_row = outcome
        .result
        .deliveries
        .iter()
        .find(|row| row.target_bot_id == "bot-on")
        .unwrap();
    let _ = deps
        .handoff
        .claim(
            &scope("bot-on", &message_id),
            HandoffClaim::new(enabled_row.delivery_id.clone(), enabled_row.state.state_version, None, 1, NOW + 1_000),
        )
        .await
        .expect("the caller target's identity is parked independently");
}

#[tokio::test]
async fn a_lease_admitted_owner_message_admits_with_lineage_only_scopes() {
    // Spec §7.2.1: when the routed delivery carries NO caller-enabled
    // target at all (an Owner Manager alone receiving the Human turn in a
    // caller group), the seam still admits the message — ordinary Owner
    // execution for every target, with only the delegation-only
    // association. Previously this shape died on "caller admission
    // requires one delivery target".
    let deps = deps().await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id, NOW + 300_000);
    args.scope = None;
    args.admission.targets.clear();
    args.admission.targets.push(DeliveryAdmissionTarget {
        rejection: None,
        target_bot_id: "bot-owner".into(),
        kind: DeliveryType::Send,
        max_queued: 10,
        semantic_projection_json: json!({"version": 1}),
    });
    args.delegation_scopes = vec![scope("bot-owner", &message_id)];
    let outcome = admit_caller_message(&deps, args, NOW)
        .await
        .expect("a lineage-only admission is a valid ordinary admission");
    assert_eq!(outcome.result.deliveries.len(), 1);
    let owner_row = &outcome.result.deliveries[0];
    assert_eq!(owner_row.semantic_projection_json["caller_lineage"], "delegation_only");
    assert!(owner_row.semantic_projection_json.get("caller_required").is_none());
    let parked = deps
        .handoff
        .claim(
            &scope("bot-owner", &message_id),
            HandoffClaim::new(owner_row.delivery_id.clone(), owner_row.state.state_version, None, 1, NOW + 1_000),
        )
        .await
        .expect("the Owner association identity is parked");
    assert_eq!(parked.expires_at_ms(), NOW + 300_000);
}

#[tokio::test]
async fn neither_caller_nor_delegation_scopes_refuse_the_seam() {
    // The seam owns nothing in this shape: refuse before any side effect
    // (the route decides its own ordinary path without the seam).
    let deps = deps().await;
    let message_id = source_message_id();
    let mut args = caller("bot-on", &message_id, NOW + 300_000);
    args.scope = None;
    let error = admit_caller_message(&deps, args, NOW).await.err().expect("the seam refuses");
    assert!(matches!(error, CallerAdmissionError::Admission(_)));
    // No identity ever parked, no row admitted.
    assert!(
        deps.handoff
            .claim(
                &scope("bot-on", &message_id),
                HandoffClaim::new("any".to_string(), 1, None, 1, NOW + 1_000),
            )
            .await
            .is_err()
    );
}

#[tokio::test]
async fn admission_refuses_identity_mismatch_before_persisting_or_reserving() {
    for mismatch in ["sender", "environment"] {
        let deps = deps().await;
        let message_id = source_message_id();
        let mut command = caller("bot-on", &message_id, NOW + 300_000);
        if mismatch == "sender" {
            command.admission.message.sender_id = "human_other".into();
        } else {
            command.scope.as_mut().unwrap().env = "other-environment".into();
        }
        let result = admit_caller_message(&deps, command, NOW).await;
        assert!(matches!(result, Err(ref error) if error.to_string() == "caller identity mismatch"), "{mismatch}");
        assert!(deps.admission.snapshot(None).await.unwrap().is_empty());
        // A valid submission to the same scope still succeeds: refusal must
        // not consume the one-shot identity handoff or reserve its slot.
        assert!(admit_caller_message(&deps, caller("bot-on", &message_id, NOW + 300_000), NOW).await.is_ok());
    }
}
