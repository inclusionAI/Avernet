#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore};
use bcs_protocol::stream::TASK_INTENT_ELIGIBLE_KEY;
use bcs_service_api::{
    BotDeliveryKind, BotEventCommand, ChatEventState, CoordinationMode, CoordinationSurface,
    GroupCoreService, MessageFlowService,
    BotRunContext, BotRunContextPort, ServiceResult,
};
use bcs_service_api::port::{
    CoordinationClaim, CoordinationContext, CoordinationIntentPort, CoordinationLease,
    CoordinationResult, CoordinationStatus,
};
use serde_json::{Value, json};
use tokio::sync::Mutex;

const BAAS_END_FIXTURE: &str = include_str!(
    "../../../test-support/fixtures/baas_command_output_end_without_name.json"
);

struct StoredIntent {
    arguments: serde_json::Map<String, Value>,
    receipt: Mutex<Option<CoordinationResult>>,
}

#[async_trait::async_trait]
impl CoordinationIntentPort for StoredIntent {
    async fn resolve_and_claim(&self, id: &str, tool: &str, context: &CoordinationContext,
        _deadline_ms: u64) -> ServiceResult<CoordinationClaim> {
        assert_eq!(id, "bcs_intent_00000000000000000000000000000001");
        assert_eq!(tool, "bcs_assign_task");
        assert_eq!(context.bot_id, "bot-driver");
        assert_eq!(context.run_id, "manager-run");
        assert_eq!(context.group_id, "group-1");
        assert_eq!(context.session_id.as_deref(), Some("group-1:abcdef12"));
        assert_eq!(context.tool_call_id, "assign-call");
        Ok(CoordinationClaim::Acquired(CoordinationLease {
            arguments: self.arguments.clone(), claim_token: "claim-1".into(),
        }))
    }

    async fn finish(&self, _id: &str, _context: &CoordinationContext,
        claim_token: &str, result: &CoordinationResult) -> ServiceResult<()> {
        assert_eq!(claim_token, "claim-1");
        *self.receipt.lock().await = Some(result.clone());
        Ok(())
    }
}

#[tokio::test]
async fn baas_command_output_end_without_name_dispatches_stored_intent() {
    // Permanent compatibility case: do not add result.name to this fixture.
    // BaaS end results must dispatch using the matching start's identity.
    let sample: Value = serde_json::from_str(BAAS_END_FIXTURE).unwrap();
    let data = sample["provider_result"].clone();
    assert!(data.get("name").is_none());
    assert!(data.get("toolName").is_none());
    let intents = Arc::new(StoredIntent {
        arguments: sample["intent_arguments"].as_object().unwrap().clone(),
        receipt: Mutex::new(None),
    });
    let contexts = Arc::new(MemoryBotRunContextStore::new());
    contexts.put_context(BotRunContext {
        bot_id: "bot-driver".into(), run_id: "manager-run".into(), group_id: "group-1".into(),
        bcs_session_id: Some("group-1:abcdef12".into()),
        deadline_ms: bcs_protocol::now_ms() + 60_000, terminal: false,
    }).await;
    let (support, flow) = fixture(CoordinationMode::McporterMcp).await;
    let flow = flow.with_bot_run_context(contexts).with_coordination_intents(Some(intents.clone()));
    start(&flow, sample["engine_frame"]["data"]["toolName"].as_str().unwrap()).await;
    flow.handle_bot_event(event("bot-driver", "manager-run", "group-1:abcdef12", data))
        .await.unwrap();
    assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
    let frames = support.bot_delivery.frames().await;
    let bcs_protocol::BcsFrame::Request(frame) = &frames[0] else {
        panic!("expected worker task dispatch");
    };
    assert_eq!(frame.params.as_ref().unwrap()["message"]["content"][0]["text"],
        "[from:Driver] report current status");
    let receipt = intents.receipt.lock().await;
    let receipt = receipt.as_ref().expect("stored intent must be applied, not just received");
    assert_eq!(receipt.status, CoordinationStatus::Applied);
    assert!(receipt.task_id.is_some());
}

async fn fixture(mode: CoordinationMode) -> (support::FlowTestSupport, BcsMessageFlow) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.service_mode = Some("master_slave".to_string());
    support.group.upsert(group).await.unwrap();
    support.registry.set_coordination_surface("bot-driver", CoordinationSurface {
        mode,
        worker_send_task_message_enabled: true,
        mcp_server: Some("bcs".into()),
        mcporter_command: (mode == CoordinationMode::McporterMcp).then(|| "mcporter".into()),
        tool_name_mapping: if mode == CoordinationMode::NativeMcp {
            BTreeMap::from([("provider_assign_task".into(), "bcs_assign_task".into())])
        } else {
            BTreeMap::new()
        },
    }).await;
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    );
    (support, flow)
}

fn event(bot: &str, run: &str, session: &str, data: Value) -> BotEventCommand {
    BotEventCommand {
        bot_id: bot.into(), run_id: run.into(), group_id: "group-1".into(),
        event_type: "agent".into(), state: ChatEventState::Delta,
        bcs_session_id: Some(session.into()),
        event_payload: json!({TASK_INTENT_ELIGIBLE_KEY: true, "stream": "tool", "data": data}),
    }
}

async fn start(flow: &BcsMessageFlow, name: &str) {
    flow.handle_bot_event(event("bot-driver", "manager-run", "group-1:abcdef12", json!({
        "phase": "start", "toolCallId": "assign-call", "name": name, "args": {},
    }))).await.unwrap();
}

fn result(name: Option<&str>) -> Value {
    let echo = json!({
        "__bcs_coordination__": true, "v": 1, "tool": "bcs_assign_task",
        "arguments": {"target_bot": "bot-observer", "message": "report current status"},
        "status": "received",
    }).to_string();
    let mut data = json!({
        "phase": "result", "toolCallId": "assign-call", "isError": false,
        "result": {"content": [{"type": "text", "text": echo}]},
    });
    if let Some(name) = name { data["name"] = json!(name); }
    data
}

#[tokio::test]
async fn coordination_uses_start_name_regardless_of_result_name() {
    for (mode, start_name) in [
        (CoordinationMode::McporterMcp, "Bash"),
        (CoordinationMode::NativeMcp, "provider_assign_task"),
    ] {
        for result_name in [None, Some(""), Some("unmapped_tool")] {
            let (support, flow) = fixture(mode).await;
            start(&flow, start_name).await;
            flow.handle_bot_event(event("bot-driver", "manager-run", "group-1:abcdef12",
                result(result_name))).await.unwrap();
            assert_eq!(support.bot_delivery.kinds().await, vec![BotDeliveryKind::TaskDispatch]);
            let deliveries = support.bot_delivery.frames().await;
            let bcs_protocol::BcsFrame::Request(frame) = &deliveries[0] else {
                panic!("expected task dispatch request");
            };
            assert_eq!(frame.params.as_ref().unwrap()["message"]["content"][0]["text"],
                "[from:Driver] report current status");
        }
    }
}

#[tokio::test]
async fn result_name_cannot_authorize_an_unsupported_start_tool() {
    for (mode, start_name, result_name) in [
        (CoordinationMode::McporterMcp, "Skill", "Bash"),
        (CoordinationMode::NativeMcp, "unmapped_tool", "provider_assign_task"),
        (CoordinationMode::McporterMcp, "", "Bash"),
    ] {
        let (support, flow) = fixture(mode).await;
        start(&flow, start_name).await;
        flow.handle_bot_event(event("bot-driver", "manager-run", "group-1:abcdef12",
            result(Some(result_name)))).await.unwrap();
        assert!(support.bot_delivery.kinds().await.is_empty());
    }
}

#[tokio::test]
async fn coordination_still_requires_matching_start_identity() {
    for (bot, run, session, call_id) in [
        ("bot-driver", "manager-run", "group-1:abcdef12", "unknown-call"),
        ("bot-driver", "other-run", "group-1:abcdef12", "assign-call"),
        ("bot-driver", "manager-run", "group-1:other", "assign-call"),
        ("bot-observer", "manager-run", "group-1:abcdef12", "assign-call"),
    ] {
        let (support, flow) = fixture(CoordinationMode::McporterMcp).await;
        start(&flow, "Bash").await;
        let mut data = result(None);
        data["toolCallId"] = json!(call_id);
        flow.handle_bot_event(event(bot, run, session, data)).await.unwrap();
        assert!(support.bot_delivery.kinds().await.is_empty());
    }
}

#[tokio::test]
async fn coordination_without_start_does_not_dispatch() {
    let (support, flow) = fixture(CoordinationMode::McporterMcp).await;
    flow.handle_bot_event(event("bot-driver", "manager-run", "group-1:abcdef12",
        result(Some("Bash")))).await.unwrap();
    assert!(support.bot_delivery.kinds().await.is_empty());
}
