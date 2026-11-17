use std::sync::Arc;

use bcs_bot::core::BotCore;
use bcs_domain::{DeliveryType, message_delivery::MessageDeliveryStatus as Status};
use bcs_message_flow::{BcsMessageFlow, managed_delivery::ManagedMessageDelivery};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, CallerContext, GroupCoreService, HumanActor,
    ManagedMessageDeliveryService, MessageFlowService, WebSendCommand,
    port::repo::message_delivery::DeliveryLookup,
};

use crate::{flow_support as support, session_support as session};

const SESSION: &str = "group-1:target-failures";
const ERROR: &str = "delivery_target_unavailable";

async fn fixture(deleted: &[&str], legacy: Option<&str>) -> (
    support::FlowTestSupport, Arc<BotCore>, BcsMessageFlow, Arc<ManagedMessageDelivery>,
) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let registry = Arc::new(BotCore::memory());
    for id in ["bot-driver", "bot-observer"] {
        registry.register(id.into(), BotCapabilities::default()).await.unwrap();
    }
    for id in deleted {
        assert!(registry.soft_delete(id).await);
        assert!(registry.get(id).await.is_none());
    }
    let mut group = support.group.get("group-1").await.unwrap();
    // Real Session sender authorization requires a participating Human.
    group.participants.iter_mut().find(|p| p.bot_uuid == "human_1").unwrap().mode =
        Some(bcs_domain::ParticipantMode::Present);
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let limits = ["bot-driver", "bot-observer"].into_iter()
        .filter(|id| Some(*id) != legacy).map(|id| (id.into(), 10)).collect();
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    ).with_message_repo(repo).with_managed_deliveries(service.clone())
        .with_group_delivery_limits(limits)
        .with_session_management(Arc::new(session::StaticSessionManagement::new(
            session::test_session(SESSION, "group-1", group.participants),
        )));
    (support, registry, flow, service)
}

fn command(mentions: &[&str]) -> WebSendCommand {
    WebSendCommand {
        caller_execution: None, caller_ingress: None, speaking_lease: None,
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
        group_id: "group-1".into(), session_id: Some(SESSION.into()), from_actor_id: "human_1".into(),
        from_name: None, message: "hello".into(), mentions: mentions.iter().map(|id| (*id).into()).collect(),
        attachments: None, thinking: None, idempotency_key: Some("target-failure-request".into()),
        source_im_message_id: None, channel_sender_identity: None, sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    }
}

#[tokio::test]
async fn deleted_observer_does_not_block_mentioned_bot_in_either_target_order() {
    for (deleted, healthy) in [("bot-driver", "bot-observer"), ("bot-observer", "bot-driver")] {
        let (support, registry, flow, service) = fixture(&[deleted], None).await;
        let request = command(&[healthy]);
        let outcome = flow.handle_web_send(request.clone()).await
            .expect("a deleted observer must not fail the whole message");
        assert_eq!(outcome.status, "queued");
        let view = outcome.queue_admission.unwrap();
        let failed = view.deliveries.iter().find(|d| d.target_bot_id == deleted).unwrap();
        assert_eq!(failed.kind, DeliveryType::Inject);
        assert_eq!(failed.status, Status::Failed);
        assert_eq!(failed.admission_error, Some(ERROR));
        assert_eq!(view.deliveries.iter().find(|d| d.target_bot_id == healthy).unwrap().status, Status::Queued);
        let rows = service.snapshot(Some(SESSION)).await.unwrap();
        assert_eq!(rows.len(), 2);
        let failed_row = rows.iter().find(|r| r.target_bot_id == deleted).unwrap();
        assert_eq!(failed_row.last_error_code.as_deref(), Some(ERROR));
        assert!(failed_row.terminal_at_ms.is_some());
        assert!(!failed_row.state.may_have_been_sent);
        assert_eq!(failed_row.attempt_no, 0);
        assert!(service.lookup(DeliveryLookup::LanePendingContextCarrier {
            bot: deleted.into(), session: SESSION.into(), now_ms: chrono::Utc::now().timestamp_millis(),
        }).await.unwrap().is_empty());
        assert_eq!(service.lookup(DeliveryLookup::BotPending(healthy.into())).await.unwrap().len(), 1);
        assert!(support.bot_delivery.frames().await.is_empty(), "managed targets never fall back to inline delivery");

        // Restoring the Bot must not turn a replay into a fresh delivery.
        registry.register(deleted.into(), BotCapabilities::default()).await.unwrap();
        let replay = flow.handle_web_send(request).await.unwrap().queue_admission.unwrap();
        assert!(replay.duplicate);
        assert_eq!(replay.message_id, view.message_id);
        let replay_failed = replay.deliveries.iter().find(|d| d.target_bot_id == deleted).unwrap();
        assert_eq!(replay_failed.delivery_id, failed.delivery_id);
        assert_eq!(replay_failed.status, Status::Failed);
        assert_eq!(replay_failed.admission_error, Some(ERROR));
        assert_eq!(service.snapshot(Some(SESSION)).await.unwrap().len(), 2);
    }
}

#[tokio::test]
async fn deleted_send_target_does_not_block_another_send_target() {
    let (_, _, flow, service) = fixture(&["bot-driver"], None).await;
    let result = flow.handle_web_send(command(&["bot-driver", "bot-observer"])).await.unwrap();
    assert_eq!(result.status, "queued");
    let view = result.queue_admission.unwrap();
    assert_eq!(view.deliveries.iter().find(|d| d.target_bot_id == "bot-driver").unwrap().admission_error, Some(ERROR));
    assert_eq!(view.deliveries.iter().find(|d| d.target_bot_id == "bot-observer").unwrap().status, Status::Queued);
    assert!(service.lookup(DeliveryLookup::BotPending("bot-driver".into())).await.unwrap().is_empty());
}

#[tokio::test]
async fn all_deleted_targets_are_reported_failed_without_executable_work() {
    let (support, _, flow, service) = fixture(&["bot-driver", "bot-observer"], None).await;
    let result = flow.handle_web_send(command(&["bot-driver", "bot-observer"])).await.unwrap();
    assert_eq!(result.status, "failed");
    for target in result.queue_admission.unwrap().deliveries {
        assert_eq!(target.status, Status::Failed);
        assert_eq!(target.admission_error, Some(ERROR));
        assert!(service.lookup(DeliveryLookup::BotPending(target.target_bot_id)).await.unwrap().is_empty());
    }
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn deleted_managed_target_does_not_block_legacy_recipient() {
    let (support, _, flow, _) = fixture(&["bot-driver"], Some("bot-observer")).await;
    let result = flow.handle_web_send(command(&["bot-driver", "bot-observer"])).await.unwrap();
    assert_eq!(result.status, "started");
    assert_eq!(result.bot_deliveries.len(), 1);
    assert!(result.bot_deliveries[0].delivered);
    assert_eq!(result.bot_deliveries[0].target_bot_id, "bot-observer");
    let view = result.queue_admission.unwrap();
    assert_eq!(view.deliveries.len(), 1);
    assert_eq!(view.deliveries[0].status, Status::Failed);
    assert_eq!(support.bot_delivery.frames().await.len(), 1);
}
