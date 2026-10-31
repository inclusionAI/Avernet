//! Chat-final coordination claim channel contract (provider manager bots
//! have no tool_call_end echo): the terminal `chat.event` final's reply
//! text carries the v2 stored-receipt frame; the scan turns it into the
//! claim trigger — gated on a live owner→caller session capture (G1),
//! failing as a silent no-op for every malformed or unprovenanced shape.

use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_config::resolve_env_str;
use bcs_domain::{DeliveryType, GroupStrategy, ParticipantRole, SenderType};
use bcs_message_flow::caller_relay::CallerRelayState;
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore, managed_delivery::ManagedMessageDelivery};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::caller_identity::{
    AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
};
use bcs_service_api::application::principal::HumanActor;
use bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries;
use bcs_service_api::port::{
    CoordinationClaim, CoordinationContext, CoordinationIntentPort, CoordinationLease,
    CoordinationResult, CoordinationStatus,
};
use bcs_service_api::{
    BotDeliveryKind, BotEventCommand, BotRunContext, BotRunContextPort, ChatEventState,
    CoordinationMode, CoordinationSurface, GroupCoreService, ManagedMessageDeliveryService,
    MessageFlowService, ServiceResult,
};
use serde_json::{Value, json};
use tokio::sync::Mutex;

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

const SESSION: &str = "group-1:chatpulse";
const INTENT_ID: &str = "bcs_intent_00000000000000000000000000000001";
const CHAT_FINAL_FRAME: &str = r#"{"__bcs_coordination__":true,"v":2,"tool":"bcs_assign_task","intent_id":"bcs_intent_00000000000000000000000000000001","status":"stored"}"#;

struct StoredIntent {
    arguments: serde_json::Map<String, Value>,
    receipt: Mutex<Option<CoordinationResult>>,
    claims: std::sync::atomic::AtomicUsize,
}

#[async_trait::async_trait]
impl CoordinationIntentPort for StoredIntent {
    async fn resolve_and_claim(
        &self,
        id: &str,
        tool: &str,
        context: &CoordinationContext,
        _deadline_ms: u64,
    ) -> ServiceResult<CoordinationClaim> {
        assert_eq!(id, INTENT_ID);
        assert_eq!(tool, "bcs_assign_task");
        self.claims.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        assert_eq!(context.tool_call_id, format!("chat-final:{INTENT_ID}"));
        Ok(CoordinationClaim::Acquired(CoordinationLease {
            arguments: self.arguments.clone(),
            claim_token: "claim-1".into(),
        }))
    }

    async fn finish(
        &self,
        _id: &str,
        _context: &CoordinationContext,
        claim_token: &str,
        result: &CoordinationResult,
    ) -> ServiceResult<()> {
        assert_eq!(claim_token, "claim-1");
        *self.receipt.lock().await = Some(result.clone());
        Ok(())
    }
}

struct Harness {
    support: support::FlowTestSupport,
    flow: BcsMessageFlow,
    intents: Arc<StoredIntent>,
    run_id: String,
}

async fn harness() -> Harness {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    for p in &mut group.participants {
        if p.bot_uuid == "bot-driver" { p.role = ParticipantRole::Manager; }
        if p.bot_uuid == "bot-observer" { p.role = ParticipantRole::Worker; }
    }
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group.clone()).await.unwrap();
    support.registry.set_coordination_surface("bot-driver", CoordinationSurface {
        mode: CoordinationMode::McporterMcp,
        worker_send_task_message_enabled: true,
        mcp_server: Some("bcs".into()),
        mcporter_command: Some("mcporter".into()),
        tool_name_mapping: BTreeMap::new(),
    }).await;

    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let contexts = Arc::new(MemoryBotRunContextStore::new());
    let intents = Arc::new(StoredIntent {
        arguments: serde_json::Map::from_iter([
            ("target_bot".to_string(), json!("bot-observer")),
            ("message".to_string(), json!("report current status")),
        ]),
        receipt: Mutex::new(None),
        claims: std::sync::atomic::AtomicUsize::new(0),
    });
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    )
        .with_message_repo(repo)
        .with_managed_deliveries(service.clone())
        .with_bot_run_context(contexts.clone())
        .with_coordination_intents(Some(intents.clone()))
        .with_caller_relay(CallerRelayState::new(600_000));

    // A live managed group chat row for the manager bot: StartSend stamps
    // the wire run identity the terminal event's run will match.
    let admitted = service
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: "msg-chatpulse".into(),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            now_ms: bcs_protocol::now_ms() as i64,
            expire_at_ms: None,
            event: None,
            targets: vec![bcs_service_api::port::repo::message_delivery::DeliveryAdmissionTarget {
                rejection: None,
                target_bot_id: "bot-driver".into(),
                kind: DeliveryType::Send,
                max_queued: 100,
                semantic_projection_json: json!({"version": 1}),
                run_id: None,
            }],
            message: bcs_domain::NewMessage {
                visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
                audience: None,
                group_id: "group-1".into(),
                session_id: SESSION.into(),
                sender_id: "human_1".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                client_msg_id: Some("msg-chatpulse".into()),
                owner_bot_id: None,
                created_at: bcs_protocol::now_ms(),
                run_id: String::new(),
                content: json!({"text": "please check the backlog"}),
            },
        })
        .await
        .expect("group chat row admits");
    let row = admitted.deliveries.into_iter().next().expect("one target row");
    let started = service
        .transition(bcs_service_api::application::message_delivery::DeliveryTransitionCommand {
            caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None,
            delivery_id: row.delivery_id.clone(),
            expected_state_version: row.state.state_version,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend,
            now_ms: bcs_protocol::now_ms() as i64,
            request_id: None, actor_id: None, reply: None,
            transport_context_json: Some(json!({
                "version": 1,
                "owner": {"kind": "web_socket"},
                "connection_id": Value::Null,
                "downstream_session_key": SESSION,
                "downstream_run_id": Value::Null,
                "provider_route_headers": [],
            })),
            deadline_at_ms: Some(i64::MAX),
        })
        .await
        .expect("chat row starts");
    let run_id = started.run_id.clone().expect("StartSend stamps the run");

    contexts.put_context(BotRunContext {
        bot_id: "bot-driver".into(),
        run_id: run_id.clone(),
        group_id: "group-1".into(),
        bcs_session_id: Some(SESSION.into()),
        deadline_ms: bcs_protocol::now_ms() as u64 + 600_000,
        terminal: false,
    }).await;

    Harness { support, flow, intents, run_id }
}


async fn task_dispatch_count(h: &Harness) -> usize {
    h.support
        .bot_delivery
        .kinds()
        .await
        .into_iter()
        .filter(|kind| *kind == BotDeliveryKind::TaskDispatch)
        .count()
}

fn chat_final(h: &Harness, text: &str, state: ChatEventState) -> BotEventCommand {
    BotEventCommand {
        bot_id: "bot-driver".into(),
        run_id: h.run_id.clone(),
        group_id: "group-1".into(),
        event_type: "chat.event".into(),
        event_payload: json!({"message": {"content": [{"type": "text", "text": text}]}}),
        state,
        bcs_session_id: Some(SESSION.into()),
    }
}

#[tokio::test]
async fn chat_final_with_a_live_capture_claims_and_dispatches_the_stored_intent() {
    let h = harness().await;
    h.flow.caller_relay.as_ref().expect("relay wired").ledger.record(
        &resolve_env_str(),
        SESSION,
        "turn-chatpulse",
        AuthenticatedHumanCallerIngress::new(
            HumanActor { actor_id: "human_1".into(), staff_no: "S1".into() },
            HumanCallerAuthSource::HttpAuth,
            bcs_protocol::now_ms() as i64,
            &resolve_env_str(),
        ).expect("valid ingress"),
        bcs_protocol::now_ms() as i64,
        600_000,
    );
    h.flow.handle_bot_event(chat_final(&h, CHAT_FINAL_FRAME, ChatEventState::Final))
        .await
        .expect("chat final settles");
    let claims = h.intents.claims.load(std::sync::atomic::Ordering::SeqCst);
    assert_eq!(claims, 1, "exactly one claim-once redemption of the intent");
    let dispatches = task_dispatch_count(&h).await;
    assert_eq!(dispatches, 1, "the stored intent must reach the worker as exactly one task dispatch");
    let receipt = h.intents.receipt.lock().await;
    let receipt = receipt.as_ref().expect("the claim must finish");
    assert_eq!(receipt.status, CoordinationStatus::Applied);
    assert!(receipt.task_id.is_some());
}

#[tokio::test]
async fn chat_final_without_a_live_capture_keeps_the_stored_intent_a_silent_orphan() {
    let h = harness().await;
    h.flow.handle_bot_event(chat_final(&h, CHAT_FINAL_FRAME, ChatEventState::Final))
        .await
        .expect("settlement proceeds; the frame is just not claimed");
    assert_eq!(task_dispatch_count(&h).await, 0, "G1: no capture, no claim, no dispatch (settlement broadcast aside)");
    let receipt = h.intents.receipt.lock().await;
    assert!(receipt.is_none(), "nothing was ever claimed");
}

#[tokio::test]
async fn chat_final_text_without_the_frame_keeps_the_legacy_silent_orphan_shape() {
    let h = harness().await;
    h.flow.handle_bot_event(chat_final(&h, "普通最终回复,没有协调帧", ChatEventState::Final))
        .await
        .expect("settlement proceeds");
    assert_eq!(task_dispatch_count(&h).await, 0, "a failed claim never dispatched");
    assert!(h.intents.receipt.lock().await.is_none());
}

#[tokio::test]
async fn chat_final_v1_frame_without_intent_id_is_skipped_without_provenance() {
    let h = harness().await;
    h.flow.caller_relay.as_ref().expect("relay wired").ledger.record(
        &resolve_env_str(),
        SESSION,
        "turn-chatpulse",
        AuthenticatedHumanCallerIngress::new(
            HumanActor { actor_id: "human_1".into(), staff_no: "S1".into() },
            HumanCallerAuthSource::HttpAuth,
            bcs_protocol::now_ms() as i64,
            &resolve_env_str(),
        ).expect("valid ingress"),
        bcs_protocol::now_ms() as i64,
        600_000,
    );
    let v1 = r#"{"__bcs_coordination__":true,"v":1,"tool":"bcs_assign_task","arguments":{"target_bot":"bot-observer"},"status":"received"}"#;
    h.flow.handle_bot_event(chat_final(&h, v1, ChatEventState::Final))
        .await
        .expect("settlement proceeds");
    assert_eq!(task_dispatch_count(&h).await, 0, "v1 carrier without provenance never dispatches");
    assert!(h.intents.receipt.lock().await.is_none());
}

#[tokio::test]
async fn chat_final_claim_failure_settles_as_a_silent_no_op() {
    let h = harness().await;
    h.flow.caller_relay.as_ref().expect("relay wired").ledger.record(
        &resolve_env_str(),
        SESSION,
        "turn-chatpulse",
        AuthenticatedHumanCallerIngress::new(
            HumanActor { actor_id: "human_1".into(), staff_no: "S1".into() },
            HumanCallerAuthSource::HttpAuth,
            bcs_protocol::now_ms() as i64,
            &resolve_env_str(),
        ).expect("valid ingress"),
        bcs_protocol::now_ms() as i64,
        600_000,
    );
    // Kill the run context: the shared claim core refuses the mismatched
    // run and the chat-final channel absorbs it as debug noise.
    h.flow.bot_run_context.as_ref().expect("contexts wired")
        .put_context(BotRunContext {
            bot_id: "bot-driver".into(),
            run_id: h.run_id.clone(),
            group_id: "group-1".into(),
            bcs_session_id: Some(SESSION.into()),
            deadline_ms: 1,
            terminal: true,
        }).await;
    h.flow.handle_bot_event(chat_final(&h, CHAT_FINAL_FRAME, ChatEventState::Final))
        .await
        .expect("the failed claim must not fail the terminal settlement");
    assert_eq!(task_dispatch_count(&h).await, 0, "a failed claim never dispatched");
    assert!(h.intents.receipt.lock().await.is_none());
}

#[tokio::test]
async fn non_terminal_chat_events_never_scan_for_the_frame() {
    let h = harness().await;
    h.flow.handle_bot_event(chat_final(&h, CHAT_FINAL_FRAME, ChatEventState::Delta))
        .await
        .expect("delta proceeds");
    assert_eq!(task_dispatch_count(&h).await, 0, "a failed claim never dispatched");
    assert!(h.intents.receipt.lock().await.is_none());
}

