use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery, MessageDeliveryStatus as Status};
use bcs_message_flow::{managed_delivery::ManagedMessageDelivery, queued_payload::read_bounded_queued_payload};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::{ManagedMessageDeliveryService, DeliveryTransitionCommand};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::*;
use std::sync::Arc;

fn admission(id: &str, text: serde_json::Value, kind: DeliveryType) -> AdmitMessageDeliveries {
    AdmitMessageDeliveries {
        display_message: None,
        message_id: id.into(), flow_kind: DeliveryFlowKind::Group, now_ms: 1,
        expire_at_ms: None, event: None,
        targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: "bot".into(), kind, max_queued: 100, semantic_projection_json: serde_json::json!({"version":1}) }],
        message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None, group_id: "g".into(), session_id: "s".into(), sender_id: "human".into(), sender_type: SenderType::Human,
            message_type: "chat".into(), client_msg_id: Some(id.into()), owner_bot_id: None, created_at: 1, run_id: String::new(),
            content: serde_json::json!({"text":text,"attachments":[{"attachment_id":id,"type":"image","file_name":"image.png","url":"https://example.invalid/image"}]}) },
    }
}

fn transition(row: &PersistedMessageDelivery, event: Event) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand { delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
        event, now_ms: 100, request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None }
}

async fn fixture(texts: Vec<serde_json::Value>) -> (Arc<MemoryMessageRepo>, ManagedMessageDelivery, PersistedMessageDelivery) {
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = ManagedMessageDelivery::new(repo.clone());
    for (i, text) in texts.into_iter().enumerate() { service.admit(admission(&format!("context-{i}"), text, DeliveryType::Inject)).await.unwrap(); }
    let carrier = service.admit(admission("send", "CURRENT".into(), DeliveryType::Send)).await.unwrap().deliveries.remove(0);
    (repo, service, carrier)
}

#[tokio::test]
async fn zero_twenty_four_and_twenty_five_contexts_preserve_recent_order_and_attachments() {
    for n in [0, 24, 25] {
        let (repo, service, carrier) = fixture((0..n).map(|i| format!("BODY-{i:02}").into()).collect()).await;
        let page = service.bounded_contexts(&carrier.delivery_id, 25).await.unwrap();
        let payload = read_bounded_queued_payload(repo.as_ref(), &carrier, &page.rows, page.total, 24, 131072, |_,s| Ok(s.into())).await.unwrap();
        assert_eq!(payload.selection.selected.len(), n.min(24));
        assert_eq!(payload.attachments.len(), n.min(24) + 1);
        if n == 25 { assert!(!payload.text.contains("BODY-00")); assert!(payload.text.contains("部分较早历史")); }
        if n > 1 { assert!(payload.text.find("BODY-22").unwrap() < payload.text.find("BODY-23").unwrap()); }
        assert!(payload.text.ends_with("CURRENT"));
        assert!(payload.selection.history_bytes <= 131072);
    }
}

#[tokio::test]
async fn oversized_latest_retains_utf8_tail_and_wrapper_budget() {
    for unit in ["a", "中文", "🦀"] {
        let text = format!("{}TAIL🦀", unit.repeat(140000));
        let (repo, service, carrier) = fixture(vec!["old".into(), text.into()]).await;
        let page = service.bounded_contexts(&carrier.delivery_id, 25).await.unwrap();
        let payload = read_bounded_queued_payload(repo.as_ref(), &carrier, &page.rows, page.total, 24, 131072, |_,s| Ok(s.into())).await.unwrap();
        assert_eq!(payload.selection.selected.len(), 1);
        assert!(payload.selection.selected[0].body_start > 0);
        assert!(payload.text.contains("该条历史前部已省略"));
        assert!(payload.text.contains("TAIL🦀"));
        assert!(!payload.text.contains("\nold\n"));
        let history = payload.text.split("\n当前请求：\n").next().unwrap();
        assert_eq!(history.len() as u64, payload.selection.history_bytes);
        assert!(history.len() <= 131072);
        assert_eq!(payload.attachments.len(), 2);
        let mut retry = carrier.clone();
        retry.context_selection_json = Some(serde_json::to_value(&payload.selection).unwrap());
        let replay = read_bounded_queued_payload(repo.as_ref(), &retry, &page.rows, page.total, 1, 512, |_,s| Ok(s.into())).await.unwrap();
        assert_eq!(replay.text, payload.text);
        assert_eq!(replay.selection, payload.selection);
    }
}

#[tokio::test]
async fn complete_history_that_fits_does_not_reserve_unnecessary_omission_marker() {
    // 512 bytes fits both complete messages, but would not fit the latest one
    // plus a marker for omitting a tiny older message.
    let (repo, service, carrier) = fixture(vec!["".into(), "x".repeat(512 - "历史上下文（以下消息不要求单独回复）：\n".len() - 2 * "\n[from:human; seq:1]\n\n".len()).into()]).await;
    let page = service.bounded_contexts(&carrier.delivery_id, 25).await.unwrap();
    let payload = read_bounded_queued_payload(repo.as_ref(), &carrier, &page.rows, page.total, 24, 512, |_,s| Ok(s.into())).await.unwrap();
    assert_eq!(payload.selection.selected.len(), 2);
    assert_eq!(payload.selection.history_bytes, 512);
    assert!(!payload.text.contains("省略"));
}

#[tokio::test]
async fn overflowing_middle_stops_selection_without_loading_older_invalid_body() {
    let (repo, service, carrier) = fixture(vec!["MUST_NOT_READ".into(), "x".repeat(1000).into(), "newest".into()]).await;
    let page = service.bounded_contexts(&carrier.delivery_id, 25).await.unwrap();
    let payload = read_bounded_queued_payload(repo.as_ref(), &carrier, &page.rows, page.total, 24, 512, |_,s| { assert_ne!(s, "MUST_NOT_READ"); Ok(s.into()) }).await.unwrap();
    assert_eq!(payload.selection.selected.len(), 1);
    assert!(payload.text.contains("newest"));
    assert_eq!(payload.attachments.len(), 2);
}

#[tokio::test]
async fn stable_retry_unknown_and_terminal_discard_are_distinct_and_idempotent() {
    let (repo, service, carrier) = fixture((0..25).map(|i| format!("body{i}").into()).collect()).await;
    let page = service.bounded_contexts(&carrier.delivery_id, 25).await.unwrap();
    let first = read_bounded_queued_payload(repo.as_ref(), &carrier, &page.rows, page.total, 24, 131072, |_,s| Ok(s.into())).await.unwrap();
    let mut cmd = transition(&carrier, Event::StartSend);
    cmd.transport_context_json = Some(serde_json::json!({"context_selection":first.selection}));
    let sent = service.transition(cmd).await.unwrap();
    let mut cmd = transition(&sent, Event::DefinitelyNotSent { retry: true }); cmd.request_id = sent.request_id.clone();
    let retry = service.transition(cmd).await.unwrap();
    assert!(service.transition(transition(&page.rows[0], Event::CancelRequested)).await.is_err(), "a retry must not change history under the same idempotency key");
    let second = read_bounded_queued_payload(repo.as_ref(), &retry, &page.rows, page.total, 1, 512, |_,s| Ok(s.into())).await.unwrap();
    assert_eq!(first.text, second.text, "retry ignores changed limits");
    let mut cmd = transition(&retry, Event::StartSend); cmd.now_ms = retry.available_at_ms;
    cmd.transport_context_json = Some(serde_json::json!({"context_selection":second.selection}));
    let sent = service.transition(cmd).await.unwrap();
    let unknown = service.transition(transition(&sent, Event::TransportUnknown)).await.unwrap();
    assert_eq!(service.bounded_contexts(&carrier.delivery_id, 25).await.unwrap().total, 25);
    assert!(service.transition(transition(&unknown, Event::StartSend)).await.is_err());
    let done = service.transition(transition(&unknown, Event::Completed)).await.unwrap();
    service.transition(transition(&done, Event::Completed)).await.unwrap();
    let rows = service.snapshot(None).await.unwrap();
    assert_eq!(rows.iter().filter(|d| d.state.status == Status::Consumed).count(), 24);
    let discarded: Vec<_> = rows.iter().filter(|d| d.state.status == Status::DiscardedContext).collect();
    assert_eq!(discarded.len(), 1); assert_eq!(discarded[0].last_error_code.as_deref(), Some("context_limit"));
    assert_eq!(discarded[0].bound_to_delivery_id.as_deref(), Some(carrier.delivery_id.as_str()));
    assert_eq!(repo.list_deliveries(None).await.unwrap().len(), 26);
}

#[tokio::test]
async fn recovery_keeps_selection_and_definitely_not_sent_final_failure_releases_it() {
    let (repo, service, carrier) = fixture(vec!["old".into(), "new".into()]).await;
    let page = service.bounded_contexts(&carrier.delivery_id, 2).await.unwrap();
    let payload = read_bounded_queued_payload(repo.as_ref(), &carrier, &page.rows, page.total, 1, 512, |_,s| Ok(s.into())).await.unwrap();
    let mut cmd = transition(&carrier, Event::StartSend); cmd.transport_context_json = Some(serde_json::json!({"context_selection":payload.selection}));
    let sent = service.transition(cmd).await.unwrap();
    let recovered = service.transition(transition(&sent, Event::Recover)).await.unwrap();
    assert_eq!(recovered.state.status, Status::Unknown);
    assert_eq!(recovered.context_selection_json, sent.context_selection_json);
    assert_eq!(service.bounded_contexts(&carrier.delivery_id, 2).await.unwrap().total, 2);
    let mut cmd = transition(&recovered, Event::DefinitelyNotSent { retry: false }); cmd.request_id = recovered.request_id.clone();
    service.transition(cmd).await.unwrap();
    let rows = service.snapshot(None).await.unwrap();
    assert_eq!(rows.iter().filter(|d| d.state.status == Status::PendingContext).count(), 2);
    assert!(rows.iter().all(|d| d.state.status != Status::DiscardedContext));
}

#[tokio::test]
async fn cancel_before_send_rebinds_all_history_and_stale_preparation_cannot_send() {
    let (repo, service, carrier) = fixture((0..25).map(|i| format!("body{i}").into()).collect()).await;
    let page = service.bounded_contexts(&carrier.delivery_id, 25).await.unwrap();
    let prepared = read_bounded_queued_payload(repo.as_ref(), &carrier, &page.rows, page.total, 24, 131072, |_,s| Ok(s.into())).await.unwrap();
    let next = service.admit(admission("next", "NEXT".into(), DeliveryType::Send)).await.unwrap().deliveries.remove(0);
    service.transition(transition(&carrier, Event::CancelRequested)).await.unwrap();
    let mut stale = transition(&carrier, Event::StartSend); stale.transport_context_json = Some(serde_json::json!({"context_selection":prepared.selection}));
    assert!(service.transition(stale).await.is_err());
    let rebound = service.bounded_contexts(&next.delivery_id, 25).await.unwrap();
    assert_eq!(rebound.total, 25);
    assert!(rebound.rows.iter().all(|r| r.state.status == Status::Bound));
    let next = service.lookup(DeliveryLookup::Id(next.delivery_id)).await.unwrap().remove(0);
    let fresh = read_bounded_queued_payload(repo.as_ref(), &next, &rebound.rows, rebound.total, 24, 131072, |_,s| Ok(s.into())).await.unwrap();
    assert_eq!(fresh.selection.selected.len(), 24);
}
