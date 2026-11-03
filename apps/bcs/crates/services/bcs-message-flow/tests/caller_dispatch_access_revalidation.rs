//! Protected-branch dispatch-time access parity (review P1): the queue
//! dwell between admission and the scheduler's pick is a live revocation
//! window — members get removed, sessions complete, outbound policies
//! change. The ordinary path revalidates all of it in
//! `prepare_queued_group_bounded`; the protected caller branch must refuse
//! with the SAME checks inside `resolve_caller_dispatch`, BEFORE any
//! identity issuance or network send.

use std::sync::Arc;

use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_domain::{GroupStrategy, SessionStatus};
use bcs_message_flow::queued_group::QueuedGroupPreparation;
use bcs_message_flow::{
    BcsMessageFlow, MemoryBotRunContextStore, managed_delivery::ManagedMessageDelivery,
};
use bcs_service_api::ManagedMessageDeliveryService;
use bcs_service_api::interceptor::{BlockReason, InterceptorDecision, MessageInterceptor, OutboundMessage};
use bcs_service_api::{
    CallerContext, GroupCoreService, HumanActor, ManagedDeliveryPreparationService, MessageFlowService,
    WebSendCommand,
};
use serde_json::{Value, json};

#[path = "support/session.rs"]
mod session;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

fn command(id: &str) -> WebSendCommand {
    WebSendCommand {
        caller_ingress: None,
        speaking_lease: None,
        caller_execution: None,
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
        group_id: "group-1".into(), session_id: Some("group-1:access".into()), from_actor_id: "human_1".into(),
        from_name: Some("Human One".into()), message: "protected task body".into(),
        mentions: vec!["bot-driver".into()], attachments: None,
        thinking: None, idempotency_key: Some(id.into()), source_im_message_id: Some(id.into()),
        channel_sender_identity: None, sender_conn_id: None, provider_bypass_headers: vec![],
    }
}

async fn access_fixture() ->
    (support::FlowTestSupport, Arc<bcs_message_store::MemoryMessageRepo>, Arc<BcsMessageFlow>, Arc<ManagedMessageDelivery>)
{
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.set_delivery_target("bot-driver", support::FakeRegistryService::provider_target("bot-driver")).await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.participants.retain(|p| p.bot_uuid != "bot-observer");
    group.group_strategy = GroupStrategy::Chat;
    support.group.upsert(group.clone()).await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(), support.bot_delivery.clone(), support.frontend_delivery.clone())
        .with_execution_cache(Arc::new(bcs_cache_local::InMemoryCachePlugin::new()))
        .with_message_repo(repo.clone()).with_managed_deliveries(service.clone())
        .with_bot_run_context(Arc::new(MemoryBotRunContextStore::new()))
        .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 10)]))
        .with_session_management(Arc::new(session::StaticSessionManagement::new(session::test_session("group-1:access", "group-1", group.participants.clone()))));
    (support, repo, Arc::new(flow), service)
}

/// The queued row exactly as the trusted caller admission parks it after an
/// ordinary web send commits: caller_required + caller_task + the channel's
/// trusted ordinary sender facts.
async fn protected_row(flow: &BcsMessageFlow, service: &ManagedMessageDelivery, id: &str) -> PersistedMessageDelivery {
    flow.handle_web_send(command(id)).await.unwrap();
    let mut rows = service.snapshot(None).await.unwrap();
    assert_eq!(rows.len(), 1);
    let mut row = rows.remove(0);
    assert_eq!(row.flow_kind, DeliveryFlowKind::Group);
    if let Value::Object(map) = &mut row.semantic_projection_json {
        map.insert("caller_required".into(), json!(true));
        map.insert("caller_task".into(), json!("protected task body"));
        map.insert("caller_channel".into(), json!({
            "actor_id": "human_1", "actor_name": "Human One",
        }));
    } else {
        panic!("admitted row carries no projection map");
    }
    row
}

#[tokio::test]
async fn a_protected_row_resolves_while_access_stays_intact() {
    // Control: an untouched access surface resolves the same way it did
    // before the parity checks existed.
    let (_support, _repo, flow, service) = access_fixture().await;
    let row = protected_row(flow.as_ref(), service.as_ref(), "im-intact").await;
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&flow), deliveries: service.clone() };
    let context = preparer.resolve_caller_dispatch(&row).await;
    assert!(context.is_some(), "an intact access surface must resolve");
}

#[tokio::test]
async fn a_member_removed_while_queued_refuses_before_issuance() {
    // The queued row's sender is removed from the SESSION roster while the
    // row waits (the dispatch-time scope rewrites the group view from the
    // session's participants): the ordinary branch refuses with "queue
    // participant no longer has session access" — the protected branch
    // must refuse identically, before any identity is claimed or any wire
    // touched.
    let (support, _repo, mut flow, service) = access_fixture().await;
    let row = protected_row(flow.as_ref(), service.as_ref(), "im-removed").await;
    let mut revoked = session::test_session("group-1:access", "group-1", support.group.get("group-1").await.unwrap().participants);
    revoked.participants.retain(|p| p.bot_uuid != "human_1");
    Arc::get_mut(&mut flow).unwrap()
        .session_management = Some(Arc::new(session::StaticSessionManagement::new(revoked)));
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&flow), deliveries: service.clone() };
    assert!(
        preparer.resolve_caller_dispatch(&row).await.is_none(),
        "a sender revoked while queued must not resolve"
    );
}

#[tokio::test]
async fn a_session_completed_while_queued_refuses_before_issuance() {
    // The session completes while the row waits: same assertion — the
    // ordinary branch's "queue session is no longer running" gate must
    // hold on the protected branch too.
    let (support, _repo, mut flow, service) = access_fixture().await;
    let row = protected_row(flow.as_ref(), service.as_ref(), "im-closed").await;
    let mut stopped = session::test_session("group-1:access", "group-1", support.group.get("group-1").await.unwrap().participants);
    stopped.status = SessionStatus::Completed;
    let flow_mut = Arc::get_mut(&mut flow).unwrap();
    flow_mut.session_management = Some(Arc::new(session::StaticSessionManagement::new(stopped)));
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&flow), deliveries: service.clone() };
    assert!(
        preparer.resolve_caller_dispatch(&row).await.is_none(),
        "a completed session must not resolve for a queued caller row"
    );
}

struct OutboundBlocker;

#[async_trait::async_trait]
impl MessageInterceptor for OutboundBlocker {
    async fn on_outbound(&self, _msg: &mut OutboundMessage) -> InterceptorDecision {
        InterceptorDecision::Block(BlockReason {
            interceptor_id: "test-outbound-block".to_string(),
            code: "blocked".to_string(),
            message: "blocked by test".to_string(),
            user_visible: true,
        })
    }
}

#[tokio::test]
async fn an_outbound_policy_deployed_while_queued_refuses_before_issuance() {
    // A runtime-deployed outbound policy (security block) applies to
    // ordinary queued rows at prepare time; the protected branch must run
    // the same interceptor chain over the caller task text and refuse —
    // the queued row must not ride a freshly signed identity through a
    // policy the ordinary path would reject. The row admits under the
    // plain composition, then a second composition over the SAME store
    // carries the blocker — exactly the "policy deployed while the row
    // sits queued" shape (dispatch-time evaluation, admission untouched).
    let (support, repo, flow, service) = access_fixture().await;
    let row = protected_row(flow.as_ref(), service.as_ref(), "im-blocked").await;
    let blocked = Arc::new(
        BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(), support.bot_delivery.clone(), support.frontend_delivery.clone())
            .with_execution_cache(Arc::new(bcs_cache_local::InMemoryCachePlugin::new()))
            .with_message_repo(repo).with_managed_deliveries(service.clone())
            .with_bot_run_context(Arc::new(MemoryBotRunContextStore::new()))
            .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 10)]))
            .with_session_management(Arc::new(session::StaticSessionManagement::new(session::test_session("group-1:access", "group-1", support.group.get("group-1").await.unwrap().participants))))
            .with_interceptor(OutboundBlocker),
    );
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&blocked), deliveries: service.clone() };
    assert!(
        preparer.resolve_caller_dispatch(&row).await.is_none(),
        "a queued row under a blocking outbound policy must not resolve"
    );
}
