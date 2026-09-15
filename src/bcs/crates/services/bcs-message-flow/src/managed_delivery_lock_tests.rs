use super::*;
use bcs_domain::{NewMessage, SenderType};
use bcs_message_store::MemoryMessageRepo;
use std::time::Duration;

fn admission(id: &str, bot: &str) -> AdmitMessageDeliveries {
    AdmitMessageDeliveries { display_message: None, message_id: id.into(), flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
        now_ms: 1, expire_at_ms: None, event: None,
        message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None, group_id: "group".into(), session_id: "session".into(), sender_id: "human".into(), sender_type: SenderType::Human,
            message_type: "chat".into(), content: serde_json::json!({"text":id}), client_msg_id: Some(id.into()), owner_bot_id: None, created_at: 1, run_id: String::new() },
        targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: bot.into(), kind: DeliveryType::Send, max_queued: 100, semantic_projection_json: serde_json::json!({"version":1}) }] }
}
fn transition(row: &PersistedMessageDelivery, event: Event) -> DeliveryTransitionCommand {
    DeliveryTransitionCommand { delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version, event, now_ms: 2,
        request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None }
}

#[tokio::test]
async fn held_bot_does_not_block_another_bot_admission_or_transition() {
    let service = ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new()));
    let held = service.mutations.acquire(["A".into()]).await;
    let b = tokio::time::timeout(Duration::from_secs(1), service.admit(admission("b", "B"))).await.unwrap().unwrap().deliveries.remove(0);
    tokio::time::timeout(Duration::from_secs(1), service.transition(transition(&b, Event::StartSend))).await.unwrap().unwrap();
    assert!(tokio::time::timeout(Duration::from_millis(20), service.admit(admission("a", "A"))).await.is_err());
    drop(held);
    service.admit(admission("a", "A")).await.unwrap();
}

#[tokio::test]
async fn reply_locks_target_and_rechecks_primary_after_wait() {
    let service = ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new()));
    let a = service.admit(admission("a", "A")).await.unwrap().deliveries.remove(0);
    let a = service.transition(transition(&a, Event::StartSend)).await.unwrap();
    let mut complete = transition(&a, Event::Completed);
    complete.reply = Some(admission("reply", "B"));
    let held = service.mutations.acquire(["B".into()]).await;
    assert!(tokio::time::timeout(Duration::from_millis(20), service.transition(complete.clone())).await.is_err());
    // Cancelling the lock waiter must release any earlier acquired A lock.
    let cancelled = service.transition(transition(&a, Event::CancelRequested)).await.unwrap();
    assert_eq!(cancelled.state.status, Status::Cancelling);
    drop(held);
    assert!(service.transition(complete).await.is_err(), "must not apply stale pre-lock state");
    let mut fresh = transition(&cancelled, Event::Completed);
    fresh.reply = Some(admission("reply", "B"));
    service.transition(fresh).await.unwrap();
    assert_eq!(service.lookup(DeliveryLookup::Message("reply".into())).await.unwrap().len(), 1);
}

#[tokio::test]
async fn opposite_target_orders_and_duplicates_do_not_deadlock() {
    let locks = BotMutationLocks::default();
    let left = async { for _ in 0..30 { let _held = locks.acquire(["A".into(), "B".into(), "A".into()]).await; tokio::task::yield_now().await; } };
    let right = async { for _ in 0..30 { let _held = locks.acquire(["B".into(), "A".into()]).await; tokio::task::yield_now().await; } };
    tokio::time::timeout(Duration::from_secs(2), async { tokio::join!(left, right); }).await.unwrap();
}

#[tokio::test]
async fn lock_directory_reclaims_idle_bots_without_replacing_live_locks() {
    let locks = BotMutationLocks::default();
    let held = locks.acquire(["live".into()]).await;
    for i in 0..1024 { drop(locks.acquire([format!("idle-{i}")]).await); }
    assert!(locks.directory.lock().await.0.len() <= 129);
    assert!(tokio::time::timeout(Duration::from_millis(20), locks.acquire(["live".into()])).await.is_err());
    drop(held);
    locks.acquire(["live".into()]).await;
}
#[tokio::test]
async fn cancelled_persistence_keeps_bot_guard_until_commit_finishes() {
    let lock = Arc::new(tokio::sync::Mutex::new(()));
    let guard = lock.clone().lock_owned().await;
    let (entered_tx, entered_rx) = tokio::sync::oneshot::channel();
    let (release_tx, release_rx) = tokio::sync::oneshot::channel();
    let caller = tokio::spawn(async move {
        persist_with_guards(vec![guard], async move {
            entered_tx.send(()).unwrap();
            release_rx.await.unwrap();
            Ok(())
        }).await
    });
    entered_rx.await.unwrap();
    caller.abort();
    let _ = caller.await;
    assert!(lock.try_lock().is_err());
    release_tx.send(()).unwrap();
    drop(tokio::time::timeout(Duration::from_secs(1), lock.lock()).await.unwrap());
}
