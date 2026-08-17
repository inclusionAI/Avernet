use std::collections::VecDeque;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_interaction::{InteractionManagement, MemoryInteractionStore};
use bcs_protocol::{BcsFrame, RequestFrame, ResponseFrame};
use bcs_service_api::{
    CanResolveInteraction, CanResolveInteractionCommand, FrontendDeliveryPort, InteractionKind,
    InteractionProviderAck, InteractionProviderCommand, InteractionProviderPort,
    InteractionService, ProviderInteractionRequestedCommand, ProviderInteractionResolvedCommand,
    ServiceResult,
};
use bcs_test_support::{
    NoopCollaborationRuntimeService, NoopMessageFlowService, NoopWorkbenchSessionService,
};
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::{
    WebClientConnectionState, WebDispatchState, WorkbenchConnectionAuth,
    WorkbenchConnectionRegistry, WorkbenchFrontendDelivery, WorkbenchInteractionDelivery,
    dispatch_client_frame,
};
use serde_json::{Value, json};
use tokio::sync::{Mutex, mpsc};

struct AllowResolve;

#[async_trait]
impl CanResolveInteraction for AllowResolve {
    async fn can_resolve(&self, _command: CanResolveInteractionCommand) -> ServiceResult<bool> {
        Ok(true)
    }
}

struct ScriptedProvider {
    responses: Mutex<VecDeque<InteractionProviderAck>>,
    calls: Mutex<Vec<InteractionProviderCommand>>,
}

#[async_trait]
impl InteractionProviderPort for ScriptedProvider {
    async fn resolve_interaction(
        &self,
        command: InteractionProviderCommand,
    ) -> ServiceResult<InteractionProviderAck> {
        self.calls.lock().await.push(command);
        Ok(self
            .responses
            .lock()
            .await
            .pop_front()
            .expect("scripted ACK"))
    }
}

fn requested(interaction_id: &str) -> ProviderInteractionRequestedCommand {
    ProviderInteractionRequestedCommand {
        bcs_run_id: "bcs-run-e2e".to_string(),
        provider_run_id: "provider-run-e2e".to_string(),
        interaction_id: interaction_id.to_string(),
        kind: InteractionKind::Exec,
        bcs_session_id: "session-e2e".to_string(),
        group_id: "group-e2e".to_string(),
        bot_id: "bot-e2e".to_string(),
        run_deadline_ms: u64::MAX,
        provider_target: BotDeliveryTarget::HttpProvider {
            bot_id: "bot-e2e".to_string(),
            provider_id: "provider-e2e".to_string(),
            provider_bot_ref: "provider-bot-e2e".to_string(),
            webhook_url: "https://provider.example/webhook".to_string(),
            bcs_to_provider_token: RedactedToken::new("secret"),
            protocol_version: "2.0".to_string(),
        },
        provider_bypass_headers: Vec::new(),
        payload: json!({
            "runId": "provider-run-e2e",
            "seq": 1,
            "phase": "requested",
            "interactionId": interaction_id,
            "kind": "exec",
            "command": format!("deploy {interaction_id}"),
            "options": [
                {"decision": "allow_once", "label": "Allow once"},
                {"decision": "deny", "label": "Deny"}
            ]
        }),
        received_at_ms: bcs_protocol::now_ms(),
    }
}

async fn receive_json(rx: &mut mpsc::Receiver<String>) -> Value {
    serde_json::from_str(&rx.recv().await.expect("WS frame")).expect("JSON WS frame")
}

#[tokio::test]
async fn requested_events_resolve_in_reverse_order_retry_and_continue_on_same_run() {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let run_channels = Arc::new(RunChannelManager::new());
    let raw_frontend: Arc<dyn FrontendDeliveryPort> = Arc::new(WorkbenchFrontendDelivery::new(
        connections.clone(),
        run_channels.clone(),
    ));
    let provider = Arc::new(ScriptedProvider {
        responses: Mutex::new(VecDeque::from([
            InteractionProviderAck {
                ok: false,
                retryable: Some(true),
                error: Some("temporary".to_string()),
            },
            InteractionProviderAck {
                ok: true,
                retryable: None,
                error: None,
            },
            InteractionProviderAck {
                ok: true,
                retryable: None,
                error: None,
            },
        ])),
        calls: Mutex::new(Vec::new()),
    });
    let interactions: Arc<dyn InteractionService> = Arc::new(InteractionManagement::new(
        Arc::new(MemoryInteractionStore::new()),
        Arc::new(AllowResolve),
        provider.clone(),
        Arc::new(WorkbenchInteractionDelivery::new(raw_frontend)),
        120_000,
    ));
    let state = Arc::new(WebDispatchState {
        message_flow: Arc::new(NoopMessageFlowService),
        collaboration_runtime: Arc::new(NoopCollaborationRuntimeService),
        workbench_sessions: Arc::new(NoopWorkbenchSessionService),
        interactions: interactions.clone(),
        group_session_connections: None,
        frontend_connections: connections.clone(),
        run_channels,
    });
    let (tx, mut rx) = mpsc::channel(16);
    connections
        .subscribe(
            "session-e2e".to_string(),
            tx.clone(),
            Some("human-e2e".to_string()),
        )
        .await;

    interactions
        .on_provider_requested(requested("first"))
        .await
        .unwrap();
    interactions
        .on_provider_requested(requested("second"))
        .await
        .unwrap();
    let first_event = receive_json(&mut rx).await;
    let second_event = receive_json(&mut rx).await;
    assert_eq!(first_event["payload"]["interactionId"], "first");
    assert_eq!(second_event["payload"]["interactionId"], "second");

    let auth = WorkbenchConnectionAuth::UserBound {
        actor_id: Some("human-e2e".to_string()),
    };
    let mut connection_state = WebClientConnectionState::default();
    for (request_id, interaction_id, idempotency_key, expect_ok) in [
        ("resolve-second-1", "second", "idem-second", false),
        ("resolve-second-2", "second", "idem-second", true),
        ("resolve-first", "first", "idem-first", true),
    ] {
        let frame = BcsFrame::Request(RequestFrame::new(
            request_id,
            "interaction.resolve",
            Some(json!({
                "bcsRunId": "bcs-run-e2e",
                "interactionId": interaction_id,
                "idempotencyKey": idempotency_key,
                "decision": "allow_once"
            })),
        ));
        dispatch_client_frame(
            &state,
            &serde_json::to_string(&frame).unwrap(),
            &tx,
            &mut connection_state,
            &auth,
        )
        .await
        .unwrap();
        let response: ResponseFrame =
            serde_json::from_value(receive_json(&mut rx).await).expect("response frame");
        assert_eq!(response.ok, expect_ok);
        if !expect_ok {
            assert!(response.error.expect("retryable error").retryable);
        }
    }

    assert!(
        interactions
            .list_pending("session-e2e")
            .await
            .unwrap()
            .is_empty()
    );
    interactions
        .on_provider_resolved(ProviderInteractionResolvedCommand {
            bcs_run_id: "bcs-run-e2e".to_string(),
            provider_run_id: "provider-run-e2e".to_string(),
            interaction_id: "second".to_string(),
            kind: InteractionKind::Exec,
            payload: json!({
                "runId": "provider-run-e2e",
                "seq": 4,
                "phase": "resolved",
                "interactionId": "second",
                "kind": "exec",
                "decision": "allow_once"
            }),
            received_at_ms: bcs_protocol::now_ms(),
        })
        .await
        .unwrap();
    let resolved_event = receive_json(&mut rx).await;
    assert_eq!(resolved_event["payload"]["phase"], "resolved");
    assert_eq!(resolved_event["bcsRunId"], "bcs-run-e2e");

    let calls = provider.calls.lock().await;
    assert_eq!(calls.len(), 3);
    assert_eq!(calls[0].interaction_id, "second");
    assert_eq!(calls[1].interaction_id, "second");
    assert_eq!(calls[2].interaction_id, "first");
}
