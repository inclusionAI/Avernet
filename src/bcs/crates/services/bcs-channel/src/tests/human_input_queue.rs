use super::*;

#[tokio::test]
async fn queued_notification_drains_more_than_two_batches_of_stale_requests() -> TestResult {
    assert_queue_continues(HumanInputRequestStatus::Cancelled).await
}

#[tokio::test]
async fn queued_notification_drains_more_than_two_batches_of_delivery_failures() -> TestResult {
    assert_queue_continues(HumanInputRequestStatus::DeliveryFailed).await
}

async fn assert_queue_continues(skipped_status: HumanInputRequestStatus) -> TestResult {
    const SKIPPED: u64 = 65;
    let harness = TestHarness::new(state_machine_group("group_sm")).await?;
    start_state_machine_channel(&harness).await?;
    SessionChannelOutboundPort::publish_human_input_ready(
        &harness.service,
        human_input_ready_event("head", HumanInputNotificationMode::DirectAssignee),
    ).await?;
    let head = harness.human_input_requests.get("head").await?.unwrap();
    for index in 0..=SKIPPED {
        let mut request = head.clone();
        request.request_id = format!("queued-{index}");
        request.run_id = format!("run-queued-{index}");
        request.session_id = format!("session-queued-{index}");
        request.created_at += index + 1;
        request.status = HumanInputRequestStatus::Queued;
        request.active_slot_key = None;
        request.delivery_attempts = 0;
        request.activated_at = None;
        if index < SKIPPED && skipped_status == HumanInputRequestStatus::Cancelled {
            harness.collaboration_runtime.stale_human_runs.lock().await.insert(request.run_id.clone());
        }
        harness.human_input_requests.enqueue(request).await?;
    }
    if skipped_status == HumanInputRequestStatus::DeliveryFailed {
        harness.delivery.failures_remaining.store(SKIPPED, Ordering::SeqCst);
    }
    harness.delivery.events.lock().await.clear();
    harness.human_input_requests.close_for_run_node(
        &head.run_id, &head.node_id, HumanInputRequestStatus::Cancelled,
    ).await?;

    // Re-delivery of a queued event is a normal entry point. No recovery scan
    // or subsequent inbound message is required to reach the valid request.
    let outcome = SessionChannelOutboundPort::publish_human_input_ready(
        &harness.service,
        human_input_ready_event(&format!("queued-{SKIPPED}"), HumanInputNotificationMode::DirectAssignee),
    ).await?;
    assert_eq!(outcome, SessionChannelDeliveryOutcome::Delivered);
    let active = harness.human_input_requests.find_active_by_scope(&head.reply_scope_key).await?.unwrap();
    assert_eq!(active.request_id, format!("queued-{SKIPPED}"));
    assert_eq!(active.delivery_attempts, 1);
    assert_eq!(harness.human_input_requests.count_queued(&head.reply_scope_key).await?, 0);
    for index in 0..SKIPPED {
        let skipped = harness.human_input_requests.get(&format!("queued-{index}")).await?.unwrap();
        assert_eq!(skipped.status, skipped_status);
        assert!(skipped.active_slot_key.is_none());
    }
    let delivered = harness.delivery.events.lock().await;
    assert_eq!(delivered.iter().filter(|event| event.run_id == format!("human-input-queued-{SKIPPED}")).count(), 1);
    assert_eq!(delivered.len(), if skipped_status == HumanInputRequestStatus::DeliveryFailed { SKIPPED as usize + 1 } else { 1 });
    Ok(())
}
