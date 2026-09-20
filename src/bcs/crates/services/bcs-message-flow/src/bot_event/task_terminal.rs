//! Retriable post-commit Task effects; persistence failure must not skip cleanup.
use super::*;
use bcs_domain::message_delivery::{PersistedMessageDelivery, MessageDeliveryStatus as Status};

async fn record_completion(flow: &BcsMessageFlow, row: &PersistedMessageDelivery, terminal: &mut BotEventCommand, replay: bool) -> ServiceResult<()> {
    if row.state.status != Status::Completed { return Ok(()); }
    let task = crate::queued_task::intent(row)?.ok_or_else(|| crate::queued_task::error("task intent missing"))?;
    let source = flow.message_repo.as_ref().ok_or_else(|| crate::queued_task::error("task result repository unavailable"))?
        .get_message_by_id(&row.session_id, &crate::queued_task_terminal::result_message_id(&task.task_id)).await
        .map_err(|e| crate::queued_task::error(&format!("committed task result read failed: {e}")))?
        .ok_or_else(|| crate::queued_task::error("committed task result missing"))?;
    let text = source.content.get("task_result_text").and_then(Value::as_str)
        .ok_or_else(|| crate::queued_task::error("committed task result text missing"))?;
    if replay {
        terminal.event_payload = serde_json::json!({"state":"final", "run_id":terminal.run_id,
            "message":{"role":"assistant", "content":source.content.get("text").cloned().unwrap_or(Value::Null)}});
    }
    let mut entry = crate::task_store::new_task_entry(task.task_id, row.group_id.clone(), Some(row.session_id.clone()),
        task.manager, task.worker, Some(task.worker_name), row.created_at_ms as u64, task.response_mode);
    entry.managed = true;
    // EventRecorder deduplicates on producer + producer_key + event_type.
    // Both the result body and timestamp come from committed state on retries.
    crate::task_flow::record_task_completed(flow, &entry, text, source.created_at).await
}

pub(super) async fn finish(flow: &BcsMessageFlow, row: &PersistedMessageDelivery, cmd: &BotEventCommand, replay: bool) -> ServiceResult<BotEventOutcome> {
    let task = crate::queued_task::intent(row)?.ok_or_else(|| crate::queued_task::error("task intent missing"))?;
    if flow.task_store.get(&task.task_id).await.is_some_and(|entry| entry.managed_terminal_effects_done) {
        return Ok(BotEventOutcome { bot_deliveries:Vec::new(), frontend_deliveries:Vec::new(),
            unregistered_run_ids:Vec::new(), mentions:Vec::new(), delivered_count:0, failed_count:0, delivery_results:Vec::new() });
    }
    crate::queued_task::restore(flow, row).await?;
    let mut terminal = cmd.clone();
    terminal.state = match row.state.status {
        Status::Completed => ChatEventState::Final,
        Status::Cancelled => ChatEventState::Aborted,
        _ => ChatEventState::Error,
    };
    if terminal.state == ChatEventState::Error {
        let text = error_display_text(&terminal.event_payload);
        inject_synthesized_message(&mut terminal.event_payload, &text);
        terminal.event_payload["errorMessage"] = Value::String(text);
    }
    let mut error = record_completion(flow, row, &mut terminal, replay).await.err();
    let frontend_deliveries = match publish_incoming_event(flow, &terminal, Some(&task.task_id)).await {
        Ok(deliveries) => deliveries,
        Err(err) => { error.get_or_insert(err); Vec::new() }
    };
    try_channel_outbound(flow, &terminal).await;
    if let Err(err) = flow.frontend_delivery.unregister_run(&terminal.run_id).await { error.get_or_insert(err); }
    if let Err(err) = flow.complete_send_context(&terminal.run_id).await { error.get_or_insert(err); }
    flow.message_tracker.cleanup_run(&terminal.run_id).await;
    flow.message_tracker.cleanup_run(&crate::run_reply::chat_key(&terminal)).await;
    notify_terminal_observer(flow, &terminal).await;
    if let Some(error) = error { return Err(error); }
    flow.task_store.finish_managed_terminal_effects(&task.task_id).await;
    Ok(BotEventOutcome { bot_deliveries:Vec::new(), frontend_deliveries,
        unregistered_run_ids:final_run_ids(&terminal), mentions:Vec::new(), delivered_count:0, failed_count:0, delivery_results:Vec::new() })
}
