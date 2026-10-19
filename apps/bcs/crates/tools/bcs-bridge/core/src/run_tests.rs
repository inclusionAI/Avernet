use super::*;

#[test]
fn delivered_large_request_keeps_only_fixed_size_fingerprint() {
    let req: DownstreamRequest = serde_json::from_value(json!({
        "id": "large-request", "method": "chat.send", "session_id": "session",
        "to_bot": {"provider_id": "provider", "provider_bot_ref": "worker"},
        "message": {"content": [{"type": "text", "text": "x".repeat(8 * 1024 * 1024)}]}
    })).unwrap();
    let fingerprint = body_fingerprint(&req, "session");
    let registry = RunRegistry::new();
    let (handle, _) = registry.begin_with_mode(&req.id, fingerprint.clone(), RetentionMode::PluginPending);
    registry.finish(&req.id);
    handle.release_delivery();
    assert_eq!(handle.fp.len(), 64, "tombstones must not retain request text");
    let (same, is_new) = registry.begin(&req.id, body_fingerprint(&req, "session"));
    assert!(!is_new);
    assert!(same.matches(&fingerprint));
    let mut changed = req;
    changed.message = Some(json!({"content": [{"type": "text", "text": "changed"}]}));
    assert!(!same.matches(&body_fingerprint(&changed, "session")));
}

#[test]
fn delivered_upstream_keeps_tombstone_but_releases_payloads() {
    let registry = RunRegistry::new();
    let (handle, _) = registry.begin_with_mode("delivered", "fp".into(), RetentionMode::PluginPending);
    let mut seq = 0;
    push_frame(&handle, &mut seq, "delivered", &sse::chat_delta("delivered", "payload"), None);
    handle.release_delivery();
    assert!(handle.delivery_pinned.load(Ordering::SeqCst), "active delivery cannot be released");
    assert_eq!(handle.snapshot_after(0).len(), 1);
    registry.finish("delivered");
    handle.release_delivery();
    assert!(handle.snapshot_after(0).is_empty());
    assert_eq!(handle.retention_stats().retained_bytes, 0);
    let (repeated, is_new) = registry.begin("delivered", "fp".into());
    assert!(!is_new, "a duplicate must not launch a new engine");
    assert!(repeated.is_terminal());
    assert!(repeated.matches("fp"));
}

#[tokio::test]
async fn dropping_idle_gateway_subscription_cancels_promptly() {
    let registry = RunRegistry::new();
    let (handle, _) = registry.begin("idle", "fp".into());
    drop(forward_stream(handle.clone()).unwrap());
    tokio::time::timeout(Duration::from_millis(200), handle.abort.cancelled()).await.unwrap();
}

#[test]
fn runtime_retains_events_without_gateway_wire_support() {
    let registry = RunRegistry::new();
    let (handle, _) = registry.begin_with_mode("opaque", "fp".into(), RetentionMode::PluginPending);
    let mut seq = 0;
    let event = StreamEvent::Unknown { event: "opaque".into(), raw: json!({"x": 7}) };
    assert!(matches!(push_frame(&handle, &mut seq, "opaque", &event, None), PushOutcome::Ok));
    assert!(matches!(handle.next_after(0).unwrap().unwrap().event, StreamEvent::Unknown { .. }));
}

#[test]
fn retention_budget_counts_raw_event_payloads() {
    let registry = RunRegistry::new();
    let (handle, _) = registry.begin_with_mode("raw-budget", "fp".into(), RetentionMode::PluginPending);
    let mut seq = 0;
    let mut event = sse::chat_delta("raw-budget", "small wire delta");
    if let StreamEvent::Chat(chat) = &mut event { chat.raw = json!({"trace": "x".repeat(7 * 1024 * 1024)}); }
    for _ in 0..4 {
        assert!(matches!(push_frame(&handle, &mut seq, "raw-budget", &event, None), PushOutcome::Ok));
    }
    assert!(matches!(push_frame(&handle, &mut seq, "raw-budget", &event, None), PushOutcome::Terminate));
    assert!(matches!(&handle.snapshot_after(4)[0].event, StreamEvent::Chat(chat) if chat.error_kind.as_deref() == Some("buffer_overflow")));
}

#[test]
fn upstream_without_receivers_retains_until_delivery_released() {
    let registry = RunRegistry::new();
    let (handle, _) = registry.begin_with_mode("offline", "fp".into(), RetentionMode::PluginPending);
    let mut seq = 0;
    assert!(matches!(push_frame(&handle, &mut seq, "offline", &sse::chat_final("offline", "done".into()), None), PushOutcome::Ok));
    registry.finish("offline");
    registry.inner.lock().unwrap().map.get_mut("offline").unwrap().finished_at = Some(Instant::now() - TERMINAL_GRACE - Duration::from_secs(1));
    assert!(registry.get("offline").is_some());
    assert_eq!(handle.snapshot_after(0).len(), 1);
    handle.release_delivery();
    assert!(registry.get("offline").is_some(), "delivery starts a fresh tombstone grace period");
    let (_, is_new) = registry.begin("offline", "fp".into());
    assert!(!is_new, "late offline delivery must not permit re-execution");
    let expired = Instant::now() - TERMINAL_GRACE - Duration::from_secs(1);
    *handle.delivery_released_at.lock().unwrap() = Some(expired);
    handle.release_delivery();
    assert_eq!(*handle.delivery_released_at.lock().unwrap(), Some(expired), "release is idempotent");
    assert!(registry.get("offline").is_none(), "delivered tombstone expires after its delivery grace");
}

#[test]
fn retention_count_overflow_keeps_terminal_error_and_cancels() {
    let registry = RunRegistry::new();
    let (handle, _) = registry.begin_with_mode("overflow", "fp".into(), RetentionMode::PluginPending);
    let mut seq = 0;
    for _ in 0..MAX_RETAINED_EVENTS {
        assert!(matches!(push_frame(&handle, &mut seq, "overflow", &sse::chat_delta("overflow", "x"), None), PushOutcome::Ok));
    }
    assert!(matches!(push_frame(&handle, &mut seq, "overflow", &sse::chat_delta("overflow", "x"), None), PushOutcome::Terminate));
    assert!(handle.abort.is_cancelled());
    let events = handle.snapshot_after(MAX_RETAINED_EVENTS as u64);
    assert_eq!(events.len(), 1);
    assert!(matches!(&events[0].event, StreamEvent::Chat(chat) if chat.state == ChatState::Error && chat.error_kind.as_deref() == Some("buffer_overflow")));
}

#[tokio::test]
async fn forwarder_repairs_broadcast_lag_without_gaps() {
    use futures::StreamExt;
    let registry = RunRegistry::new();
    let (handle, _) = registry.begin("lag", "fp".into());
    let mut stream = Box::pin(forward_stream(handle.clone()).unwrap());
    let mut seq = 0;
    for _ in 0..600 {
        assert!(matches!(push_frame(&handle, &mut seq, "lag", &sse::chat_delta("lag", "x"), None), PushOutcome::Ok));
    }
    registry.finish("lag");
    for expected in 1..=600 {
        assert_eq!(stream.next().await.unwrap().seq, expected);
    }
    assert!(stream.next().await.is_none());
}

#[test]
fn structured_retention_preserves_metadata() {
    let registry = RunRegistry::new();
    let (handle, _) = registry.begin("typed-run", "fp".into());
    let _receiver = handle.tx.subscribe();
    let mut seq = 0;
    push_frame(&handle, &mut seq, "typed-run", &sse::chat_final("typed-run", "done".into()), None);
    let events = handle.snapshot_after(0);
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].run_id, "typed-run");
    assert_eq!(events[0].seq, 1);
    assert_eq!(events[0].ts, handle.snapshot_after(0)[0].ts);
    assert!(handle.snapshot_after(1).is_empty());
    push_frame(&handle, &mut seq, "typed-run", &sse::chat_final("typed-run", "second".into()), None);
    let first = handle.next_after(0).unwrap().unwrap();
    assert_eq!(first.seq, 1);
    assert_eq!(first.ts, events[0].ts);
    assert_eq!(handle.next_after(1).unwrap().unwrap().seq, 2);
    assert!(handle.next_after(2).unwrap().is_none());
}

#[test]
fn begin_new_then_existing_returns_same_handle() {
    let reg = RunRegistry::new();
    let (h1, is_new) = reg.begin("r-1", "fp-a".into());
    assert!(is_new);
    let (h2, is_new2) = reg.begin("r-1", "fp-a".into());
    assert!(!is_new2);
    assert!(h2.matches("fp-a"), "existing handle carries the same fingerprint");
    assert!(h1.matches("fp-a"));
    assert!(!h1.matches("fp-b"));
    assert!(reg.get("r-1").is_some());
    assert!(reg.get("r-2").is_none());
}

#[test]
fn finish_marks_terminal_and_retains_for_replay() {
    let reg = RunRegistry::new();
    let (h, _) = reg.begin("r-9", "fp".into());
    assert!(!h.is_terminal());
    reg.finish("r-9");
    assert!(h.is_terminal());
    assert!(reg.get("r-9").is_some(), "terminal run retained within grace TTL");
}

#[test]
fn body_fingerprint_stable_for_same_body_distinct_for_different() {
    let mk = |msg: serde_json::Value, sid: &str, ref_: &str| {
        let req = DownstreamRequest {
            id: "x".into(),
            method: "chat.send".into(),
            to_bot: crate::webhook::ToBot { provider_id: "p".into(), provider_bot_ref: ref_.into() },
            session_id: Some(sid.into()),
            message: Some(msg),
            from: None,
            timeout_ms: None,
            params: None,
        };
        body_fingerprint(&req, sid)
    };
    let m = serde_json::json!({"role":"user","content":[{"type":"text","text":"hi"}]});
    let a = mk(m.clone(), "s-1", "b-1");
    let b = mk(m.clone(), "s-1", "b-1");
    assert_eq!(a, b, "same body → same fingerprint");
    let c = mk(m.clone(), "s-1", "b-2");
    assert_ne!(a, c, "different ref → different fingerprint");
    let d = mk(serde_json::json!({"role":"user","content":[{"type":"text","text":"yo"}]}), "s-1", "b-1");
    assert_ne!(a, d, "different message → different fingerprint");
}

#[test]
fn extract_message_text_joins_multiple_content_blocks() {
    let m = serde_json::json!({"content":[
        {"type":"text","text":"line1"},
        {"type":"image","text":"ignored"},  // non-text type but text present: still joined
        {"type":"text","text":"line2"},
        {"type":"text"},                     // no text: skipped
    ]});
    assert_eq!(extract_message_text(Some(&m)), "line1\nignored\nline2");
    assert_eq!(extract_message_text(None), "");
}

#[test]
fn request_abort_sets_flag_overrides_reason_and_cancels_token() {
    // A fresh run handle's flag is false and stop_reason defaults to
    // "user_cancelled"; calling request_abort flips the flag, overrides the
    // stop_reason, and cancels the CancellationToken.
    let reg = RunRegistry::new();
    let (h, _) = reg.begin("r-a", "fp".into());
    assert!(!h.is_abort_requested(), "fresh run is not aborted");
    assert_eq!(h.abort_stop_reason(), "user_cancelled");

    let r2 = h.clone();
    h.request_abort("provider_shutdown");
    assert!(h.is_abort_requested(), "flag set after request_abort");
    assert_eq!(h.abort_stop_reason(), "provider_shutdown", "reason overridden");
    assert!(r2.is_abort_requested(), "shared flag visible to cloned handle");
    assert_eq!(r2.abort_stop_reason(), "provider_shutdown");
    // Cancellation propagates to all clones (CancellationToken is shared).
    assert!(h.abort.is_cancelled(), "token cancelled after request_abort");
}

#[test]
fn find_terminal_run_returns_terminal_match_none_for_active_or_unknown() {
    // Active run (not terminal): find_terminal_run returns None — abort
    // goes through the sessions.active_run path instead.
    let reg = RunRegistry::new();
    let (_h_active, _) = reg.begin("r-active", "fp".into());
    reg.record_session("r-active", "bot-1", "s-1");
    assert_eq!(reg.find_terminal_run("bot-1", "s-1"), None, "active run is not terminal");

    // Mark it terminal: now find_terminal_run resolves to its run_id.
    reg.finish("r-active");
    assert_eq!(reg.find_terminal_run("bot-1", "s-1").as_deref(), Some("r-active"));

    // Unknown session and bot mismatch: no match.
    assert_eq!(reg.find_terminal_run("bot-1", "s-unknown"), None, "unknown session");
    assert_eq!(reg.find_terminal_run("bot-other", "s-1"), None, "bot mismatch");
}

#[test]
fn remove_drops_entry_and_run_session_association_for_rollback() {
    // chat.send's 429 rollback path: the placeholder entry created by
    // `begin` (and its record_session association, if any) must be wiped
    // so the same run_id is not pinned in the registry and a same-id retry
    // is not later surprised by a stale terminal-replay entry. `remove`
    // bypasses the grace-TTL retention `finish` relies on.
    let reg = RunRegistry::new();
    let (_h, is_new) = reg.begin("r-roll", "fp".into());
    assert!(is_new);
    reg.record_session("r-roll", "bot-1", "s-1");
    assert!(reg.get("r-roll").is_some());

    reg.remove("r-roll");

    assert!(reg.get("r-roll").is_none(), "entry removed by rollback");
    assert_eq!(reg.find_terminal_run("bot-1", "s-1"), None,
        "run_session association pruned alongside the entry");
}

#[tokio::test]
async fn abort_all_cancels_every_active_run_and_skips_terminal() {
    // Two active + one terminal: only the two active handles have their
    // abort tokens cancelled after abort_all; the terminal one stays as-is
    // (it was already cancelled when its run loop finalized).
    let reg = RunRegistry::new();
    let (h_a, _) = reg.begin("r-a", "fp".into());
    let (h_b, _) = reg.begin("r-b", "fp".into());
    let (h_t, _) = reg.begin("r-t", "fp".into());
    reg.finish("r-t");
    assert!(!h_a.abort.is_cancelled());
    assert!(!h_b.abort.is_cancelled());

    reg.abort_all("provider_shutdown").await;

    assert!(h_a.abort.is_cancelled(), "active run a cancelled");
    assert!(h_b.abort.is_cancelled(), "active run b cancelled");
    assert!(!h_t.abort.is_cancelled(), "terminal run skipped");
    assert!(h_a.is_abort_requested() && h_b.is_abort_requested(), "flag set on each");
    assert_eq!(h_a.abort_stop_reason(), "provider_shutdown");
}


#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn concurrent_producer_consumer_preserve_final_and_accounting() {
    let registry = Arc::new(RunRegistry::new());
    let (handle, _) = registry.begin_with_mode("concurrent", "fp".into(), RetentionMode::PluginPending);
    let (ready, mut consume) = mpsc::channel(64);
    let producer_handle = handle.clone();
    let producer_registry = registry.clone();
    let producer = tokio::spawn(async move {
        let mut seq = 0;
        for _ in 0..2000 {
            assert!(matches!(push_frame(&producer_handle, &mut seq, "concurrent",
                &sse::agent_thinking("concurrent", Some("increment".into()), None), None), PushOutcome::Ok));
            ready.send(seq).await.unwrap();
        }
        assert!(matches!(push_frame(&producer_handle, &mut seq, "concurrent",
            &sse::chat_final("concurrent", "done".into()), None), PushOutcome::Ok));
        producer_registry.finish("concurrent");
        ready.send(seq).await.unwrap();
    });
    let mut cursor = 0;
    let mut finals = 0;
    while let Some(seq) = consume.recv().await {
        let record = handle.next_after(cursor).unwrap().unwrap();
        assert_eq!(record.seq, seq);
        if matches!(record.event, StreamEvent::Chat(chat) if chat.state == ChatState::Final) { finals += 1; }
        handle.commit_delivery_through(seq).unwrap();
        cursor = seq;
    }
    producer.await.unwrap();
    assert_eq!((cursor, finals), (2001, 1));
    assert!(handle.is_terminal());
    assert_eq!(handle.retention_stats().retained_bytes, 0);
    assert!(handle.retention_stats().peak_retained_events <= 66);
    handle.release_delivery();
    assert!(registry.get("concurrent").unwrap().matches("fp"));
}
