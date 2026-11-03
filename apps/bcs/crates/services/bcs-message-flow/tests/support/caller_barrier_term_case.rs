use super::*;

#[tokio::test]
async fn demotion_during_history_complete_never_redrives_the_old_dispatch() {
    let term = Arc::new(std::sync::atomic::AtomicU64::new(1));
    let (_, sender, backfill) = delegate_barrier_world(
        CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:accepted".into() },
        None, Some(term.clone()),
    ).await;
    tokio::time::timeout(Duration::from_secs(2), async {
        while backfill.calls.lock().unwrap().is_empty() { tokio::task::yield_now().await; }
    }).await.expect("barrier completes and changes the term");
    tokio::time::sleep(Duration::from_millis(50)).await;
    assert_eq!(term.load(std::sync::atomic::Ordering::SeqCst), 2);
    assert_eq!(sender.state.lock().unwrap().len(), 1, "only original HISTORY_NOT_READY send; no old-term resend");
}
