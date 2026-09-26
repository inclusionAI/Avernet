//! Direct coverage of the `ChatRunStore` state-machine engine edge branches:
//! transition guards, terminal immutability, append/replace, detached-delivery
//! acknowledgement, the timeout sweep + terminal/detached retirement, and the
//! wait_update notify/timeout paths. These branches are exercised at the
//! application layer elsewhere but not hit directly, so this targets the
//! changed-line coverage gap.

use std::sync::Arc;
use std::time::Duration;

use bcs_message_flow::a2a_chat::ChatRunStore;
use bcs_service_api::port::repo::{ChatRunCompletionPolicy, ChatRunRecord, ChatRunState};
use bcs_service_api::ChatResponseMode;

fn record(run_id: &str, expires_at_ms: u64) -> ChatRunRecord {
    ChatRunRecord::new(
        run_id.to_string(),
        "bot".to_string(),
        "from".to_string(),
        "sk".to_string(),
        0,
        expires_at_ms,
        Some("http-chat-async".to_string()),
        ChatResponseMode::Full,
        ChatRunCompletionPolicy::WaitForFinal,
    )
}

#[tokio::test]
async fn mark_transitions_and_terminal_guards() {
    let store = ChatRunStore::new();
    store.create(record("r1", u64::MAX)).await.unwrap();
    assert!(store.mark_running("r1").await); // Pending -> Running
    assert!(!store.mark_running("r1").await); // not Pending -> guard false
    assert!(store.mark_completed("r1", Some("done")).await);
    assert!(!store.mark_completed("r1", Some("x")).await); // terminal immutable
    assert!(!store.mark_running("r1").await); // terminal -> guard false

    let store = ChatRunStore::new();
    store.create(record("r2", u64::MAX)).await.unwrap();
    assert!(store.mark_submitted("r2").await);
    assert!(!store.mark_submitted("r2").await); // not Pending -> false
    assert!(store.mark_failed("r2", "boom").await);
    assert!(!store.mark_failed("r2", "x").await); // terminal -> false

    let store = ChatRunStore::new();
    store.create(record("r3", u64::MAX)).await.unwrap();
    assert!(store.mark_cancelled("r3").await);
    assert!(!store.mark_cancelled("r3").await); // terminal -> false
}

#[tokio::test]
async fn append_delta_and_replace_content() {
    let store = ChatRunStore::new();
    store.create(record("r", u64::MAX)).await.unwrap();
    assert!(store.append_delta("r", "hello").await); // flips Pending -> Running
    assert!(store.append_delta("r", " world").await);
    assert_eq!(
        store.get("r").await.unwrap().accumulated_content,
        "hello world"
    );
    assert!(store.replace_content("r", "replaced").await);
    assert_eq!(
        store.get("r").await.unwrap().accumulated_content,
        "replaced"
    );
    store.mark_completed("r", None).await;
    assert!(!store.append_delta("r", "late").await); // terminal -> false
    assert!(!store.replace_content("r", "late").await); // terminal -> false
}

#[tokio::test]
async fn detach_acknowledgement_guarded_by_policy() {
    let store = ChatRunStore::new();
    let mut rec = record("d", u64::MAX);
    rec.completion_policy = ChatRunCompletionPolicy::DetachDeliveryAck;
    store.create(rec).await.unwrap();
    assert!(store.mark_detach_delivery_acknowledged("d").await); // flips to Running + sets ack
    assert!(!store.mark_detach_delivery_acknowledged("d").await); // ack already set -> false

    // WaitForFinal run: detach guard rejects (policy mismatch).
    store.create(record("w", u64::MAX)).await.unwrap();
    assert!(!store.mark_detach_delivery_acknowledged("w").await);
}

#[tokio::test]
async fn cleanup_expired_fails_overdue_drops_terminal_and_retires_detached() {
    let store = ChatRunStore::new();
    // Overdue non-terminal run -> force_fail (Expired). force_fail stamps
    // completed_at ~= real now, so it must NOT also be retirement-eligible.
    store.create(record("overdue", 10)).await.unwrap();
    // Terminal run whose completed_at is far in the past -> dropped.
    let mut done = record("done", u64::MAX);
    done.state = ChatRunState::Completed;
    done.completed_at_ms = Some(0);
    store.create(done).await.unwrap();
    // Acked detached run whose ack is far in the past -> dropped (retired silently).
    let mut detached = record("detached", u64::MAX);
    detached.state = ChatRunState::Running;
    detached.completion_policy = ChatRunCompletionPolicy::DetachDeliveryAck;
    detached.delivery_ack_at_ms = Some(0);
    store.create(detached).await.unwrap();

    // now is large enough that expires_at=10 is overdue and 0+retention is past,
    // but small enough that a just-force-failed run (completed_at ~= real now)
    // is NOT yet past retention.
    let now = 5_000_000;
    let (expired, dropped) = store.cleanup_expired(now, 5_000_000).await;
    let expired_ids: Vec<String> = expired.into_iter().map(|(id, _)| id).collect();
    let dropped_ids: Vec<String> = dropped.into_iter().map(|(id, _)| id).collect();

    assert!(expired_ids.contains(&"overdue".to_string()));
    assert_eq!(
        store.get("overdue").await.unwrap().state,
        ChatRunState::Failed
    );
    assert!(dropped_ids.contains(&"done".to_string()));
    assert!(store.get("done").await.is_none());
    assert!(dropped_ids.contains(&"detached".to_string()));
    assert!(store.get("detached").await.is_none());
}

#[tokio::test]
async fn wait_update_returns_on_terminal_via_notify() {
    let store = Arc::new(ChatRunStore::new());
    store.create(record("w", u64::MAX)).await.unwrap();
    let s = store.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(50)).await;
        s.mark_completed("w", Some("done")).await;
    });
    let rec = store.wait_update("w", 1, Duration::from_secs(2)).await;
    assert_eq!(rec.unwrap().state, ChatRunState::Completed);
}

#[tokio::test]
async fn wait_update_times_out_returning_current() {
    let store = ChatRunStore::new();
    store.create(record("to", u64::MAX)).await.unwrap();
    // No transition; since == current version -> waits, then returns current.
    let rec = store.wait_update("to", 1, Duration::from_millis(50)).await;
    assert_eq!(rec.unwrap().state, ChatRunState::Pending);
}