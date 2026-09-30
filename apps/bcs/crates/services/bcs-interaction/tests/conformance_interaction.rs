use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_interaction::{InteractionManagement, InteractionTerminalObserver, MemoryInteractionStore};
use bcs_service_api::{
    BotTerminalEvent, BotTerminalState, CanResolveInteraction, CanResolveInteractionCommand,
    InteractionFrontendEvent, InteractionFrontendPort, InteractionKind, InteractionProviderAck,
    InteractionProviderCommand, InteractionProviderPort, InteractionService,
    ProviderInteractionRequestedCommand, ServiceResult,
};
use serde_json::json;

struct AllowResolve;

#[async_trait]
impl CanResolveInteraction for AllowResolve {
    async fn can_resolve(&self, _command: CanResolveInteractionCommand) -> ServiceResult<bool> {
        Ok(true)
    }
}

struct AcceptingProvider;

#[async_trait]
impl InteractionProviderPort for AcceptingProvider {
    async fn resolve_interaction(
        &self,
        _command: InteractionProviderCommand,
    ) -> ServiceResult<InteractionProviderAck> {
        Ok(InteractionProviderAck {
            ok: true,
            retryable: None,
            error: None,
        })
    }
}

struct IgnoringFrontend;

#[async_trait]
impl InteractionFrontendPort for IgnoringFrontend {
    async fn publish_interaction(&self, _event: InteractionFrontendEvent) -> ServiceResult<()> {
        Ok(())
    }
}

fn requested() -> ProviderInteractionRequestedCommand {
    ProviderInteractionRequestedCommand {
        bcs_run_id: "contract-run".to_string(),
        provider_run_id: "provider-contract-run".to_string(),
        interaction_id: "contract-interaction".to_string(),
        kind: InteractionKind::Exec,
        bcs_session_id: "contract-session".to_string(),
        group_id: "contract-group".to_string(),
        bot_id: "contract-bot".to_string(),
        run_deadline_ms: u64::MAX,
        provider_target: BotDeliveryTarget::HttpProvider {
            bot_id: "contract-bot".to_string(),
            provider_id: "contract-provider".to_string(),
            provider_bot_ref: "contract-provider-bot".to_string(),
            webhook_url: "https://provider.example/webhook".to_string(),
            bcs_to_provider_token: RedactedToken::new("contract-token"),
            protocol_version: "2.0".to_string(),
        },
        provider_bypass_headers: Vec::new(),
        payload: json!({
            "runId": "provider-contract-run",
            "seq": 1,
            "phase": "requested",
            "interactionId": "contract-interaction",
            "kind": "exec",
            "options": [{"decision": "allow_once", "label": "Allow once"}]
        }),
        received_at_ms: 100,
    }
}

#[tokio::test]
async fn interaction_boundaries_use_central_contract_harnesses() {
    let store = Arc::new(MemoryInteractionStore::new());
    let authorization = Arc::new(AllowResolve);
    let provider = Arc::new(AcceptingProvider);
    let frontend = Arc::new(IgnoringFrontend);
    let service = Arc::new(InteractionManagement::new(
        store.clone(),
        authorization.clone(),
        provider.clone(),
        frontend.clone(),
        120_000,
    ));

    bcs_test_support::contract::port::interaction_store_port_contract_tests(store.as_ref()).await;
    bcs_test_support::contract::port::can_resolve_interaction_port_contract_tests(
        authorization.as_ref(),
    )
    .await;
    bcs_test_support::contract::port::interaction_provider_port_contract_tests(provider.as_ref())
        .await;
    bcs_test_support::contract::port::interaction_frontend_port_contract_tests(frontend.as_ref())
        .await;
    bcs_test_support::contract::application::interaction_service_contract_tests(service.as_ref())
        .await;

    service.on_provider_requested(requested()).await.unwrap();
    let observer = InteractionTerminalObserver::default();
    let service_port: Arc<dyn InteractionService> = service.clone();
    observer.set_service(service_port);
    let observed_service = service.clone();
    bcs_test_support::contract::port::bot_terminal_observer_port_contract_tests(
        &observer,
        BotTerminalEvent {
            run_id: "contract-run".to_string(),
            bot_uuid: "contract-bot".to_string(),
            state: BotTerminalState::Aborted,
            text: String::new(),
        },
        async move {
            observed_service
                .list_pending("contract-session")
                .await
                .is_ok_and(|pending| pending.is_empty())
        },
    )
    .await;
}
