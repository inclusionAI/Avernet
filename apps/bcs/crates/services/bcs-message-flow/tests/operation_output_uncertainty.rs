//! Unknown commit/transport outcomes must not revoke accepted output.
#![allow(clippy::expect_used, clippy::unwrap_used)]
use std::sync::{Arc, Mutex};
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore};
use bcs_service_api::{BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult,
    BotRunContextPort, ManagedMessageDeliveryService,
    MessageFlowService, ServiceError, ServiceResult};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "support/operation_output.rs"]
mod operation_output;
#[path = "support/failing_delivery.rs"]
mod failing_delivery;
#[path = "support/callback_identity.rs"]
mod callback_identity;
use operation_output::*;

async fn admission_error(commit: bool, reject: bool) {
    let s = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let managed = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()));
    let fault = Arc::new(failing_delivery::FailingDelivery::new(managed.clone()));
    if reject {
        managed.set_admission_available(false);
    } else {
        fault.arm(if commit { "admit_after_commit" } else { "admit" }, 1);
    }
    let admission = Arc::new(RecordingOutputAdmission::default());
    let flow = managed_output_flow(&s, repo, fault.clone(), admission.clone(), 10).await;
    let result = flow.handle_web_send(web_send("group-1", "human_1", Some("group-1:ffffffff"),
        &["bot-driver", "bot-observer"])).await;
    assert!(result.is_err(), "do not turn an unknown outcome into success");
    assert_eq!(fault.remaining(), 0, "the intended failure point was exercised");
    assert!(s.bot_delivery.frames().await.is_empty(), "direct targets never started");
    let registered = admission.registered_runs.lock().unwrap().clone();
    assert_eq!(registered.len(), 1);
    assert_eq!(registered[0].1.len(), 2, "one managed and one direct Send");
    let released: Vec<String> = admission.released_runs.lock().unwrap().iter()
        .flat_map(|(_, runs)| runs.clone()).collect();
    assert_eq!(released.len(), if reject { 2 } else { 1 });
    let rows = managed.snapshot(None).await.unwrap();
    if !commit {
        assert!(rows.is_empty());
        // An empty/failed lookup is not proof of rollback. The unknown Run
        // stays registered, even when this injected failure preceded commit.
        return;
    }
    let row = rows.into_iter().find(|r| r.state.kind == bcs_domain::DeliveryType::Send).unwrap();
    let run = row.run_id.clone().unwrap();
    assert!(!released.contains(&run), "durable managed Run must survive ACK loss");
    managed.transition(bcs_service_api::DeliveryTransitionCommand {
        delivery_id: row.delivery_id, expected_state_version: row.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend,
        now_ms: row.created_at_ms + 1, request_id: None, actor_id: None, reply: None,
        transport_context_json: Some(serde_json::json!({"version":1,"owner":{"kind":"web_socket"},
            "connection_id":"test","downstream_session_key":"session"})),
        deadline_at_ms: Some(i64::MAX),
    }).await.unwrap();
    fault.arm("lookup", 1);
    assert!(fault.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Message(
        row.source_message_id.clone())).await.is_err());
    // Once storage is reachable again, the original Run resumes callback
    // processing with its original operation (no new admission/retry).
    assert_eq!(fault.remaining(), 0, "the read outage was exercised");
    flow.handle_bot_event(delta_event(&run, "bot-driver", Some("group-1:ffffffff"))).await.unwrap();
    let events = s.frontend_delivery.commands().await;
    assert_eq!(events.last().unwrap().operation_id.as_deref(), Some(registered[0].0.as_str()));
}

#[tokio::test]
async fn committed_admission_with_lost_ack_retains_managed_but_releases_unsent_direct() {
    admission_error(true, false).await;
}

#[tokio::test]
async fn unknown_storage_failure_without_readback_keeps_managed_identity() {
    admission_error(false, false).await;
}

#[tokio::test]
async fn definitive_admission_rejection_releases_every_unused_run() {
    admission_error(false, true).await;
}

struct TimeoutTransport {
    timeout_bot: &'static str,
    received: Mutex<Vec<(String, String)>>,
}
#[async_trait::async_trait]
impl BotDeliveryPort for TimeoutTransport {
    async fn is_available(&self, _: &bcs_service_api::BotDeliveryTarget) -> bool { true }
    async fn deliver(&self, command: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let bot = command.target.bot_id().to_string();
        if command.delivery_kind == bcs_service_api::BotDeliveryKind::Send {
            self.received.lock().unwrap().push((command.run_id, bot.clone()));
        }
        if bot == self.timeout_bot {
            Err(ServiceError::InternalError("timeout after possible acceptance".into()))
        } else {
            Ok(BotDeliveryResult { target_bot_id: bot, delivered: true, error: None })
        }
    }
}

async fn transport_error(persistent: bool) {
    let s = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let admission = Arc::new(RecordingOutputAdmission::default());
    let transport = Arc::new(TimeoutTransport {
        timeout_bot: if persistent { "bot-driver" } else { "bot-observer" },
        received: Mutex::new(Vec::new()),
    });
    let context = Arc::new(MemoryBotRunContextStore::new());
    let authority = bcs_test_support::lazy_authority_chain();
    authority.hook.seed_owner("bot-observer", "1");
    let flow = Arc::new(BcsMessageFlow::new(s.group.clone(), s.routing.clone(), s.registry.clone(),
        transport.clone(), s.frontend_delivery.clone())
        .with_bot_run_context(context.clone())
        .with_bot_authority_hook(Some(authority.hook))
        .with_output_admission(Some(admission.clone())));
    if persistent {
        flow.handle_persistent_group_send(persistent_cmd("bot-observer", 10)).await.unwrap();
    } else {
        let mut cmd = web_send("group-1", "human_1", None, &["bot-driver", "bot-observer"]);
        cmd.source_im_message_id = Some("source-message".into());
        let outcome = flow.handle_web_send(cmd).await.unwrap();
        assert_eq!(outcome.active_run_ids.len(), 1, "the healthy sibling still succeeds");
        let received = transport.received.lock().unwrap();
        assert!(received.iter().any(|(run, bot)| bot == "bot-driver" && outcome.active_run_ids.contains(run)));
    }
    let received = transport.received.lock().unwrap().clone();
    assert_eq!(received.len(), if persistent { 1 } else { 2 });
    assert!(received.iter().any(|(_, bot)| bot == transport.timeout_bot));
    let operation = admission.registered_runs.lock().unwrap()[0].0.clone();
    let ingress = bcs_bot::ProviderBotEvents::new(
        Arc::new(callback_identity::CallbackIdentity), context.clone(), flow.clone());
    use bcs_service_api::{ProviderBotEventCommand, ProviderBotEventCredential,
        ProviderBotEventError, ProviderBotEventService, ChatEventState};
    for (run, bot) in received {
        let ctx = context.get_context(&run).await.unwrap();
        assert!(!ctx.terminal, "uncertain Run must pass callback ingress");
        assert!(context.find_active_run(&run).await.unwrap().is_some());
        assert_eq!(ctx.bot_id, bot);
        assert!(ctx.deadline_ms > 0);
        if !persistent {
            assert_eq!(flow.message_tracker.channel_source_message_id(&run).await.as_deref(),
                Some("source-message"), "uncertain callback keeps its IM reply destination");
        }
        let identity = ProviderBotEventCredential::StaticBearer(bot.clone());
        let other = ProviderBotEventCredential::StaticBearer("other-bot".into());
        let command = ProviderBotEventCommand {
            provider_id: "test-provider".into(),
            credential: identity,
            run_id: run.clone(), state: ChatEventState::Delta,
            message_text: "answer after timeout".into(), event: Some("chat".into()),
            payload: Some(serde_json::json!({"state":"delta","message":{"content":[
                {"type":"text","text":"answer after timeout"}]}})),
        };
        let mut wrong_actor = command.clone();
        wrong_actor.credential = other;
        assert!(matches!(ingress.submit_event(wrong_actor).await, Err(ProviderBotEventError::Forbidden(_))));
        ingress.submit_event(command.clone()).await.expect("real callback admission accepts uncertain Run");
        assert_eq!(s.frontend_delivery.commands().await.last().unwrap().operation_id.as_deref(), Some(operation.as_str()));
        let mut expired = ctx.clone();
        expired.deadline_ms = 0;
        context.put_context(expired).await;
        assert!(matches!(ingress.submit_event(command.clone()).await, Err(ProviderBotEventError::RunTerminated(_))));
        context.put_context(ctx).await;
        context.mark_terminal(&run).await;
        assert!(matches!(ingress.submit_event(command).await, Err(ProviderBotEventError::RunTerminated(_))));
    }
    assert!(admission.released_runs.lock().unwrap().is_empty());
}

#[tokio::test]
async fn web_timeout_retains_failed_runs_ingress_context_and_output() { transport_error(false).await; }

#[tokio::test]
async fn persistent_timeout_retains_failed_runs_ingress_context_and_output() { transport_error(true).await; }
