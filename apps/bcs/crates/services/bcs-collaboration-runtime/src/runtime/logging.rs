use super::*;

#[cfg(test)]
#[path = "logging_tests.rs"]
mod tests;
pub(super) fn log_state_machine_node_dispatch(
    group: &Group,
    run: &StateMachineRun,
    node_id: &str,
    attempt: i32,
    assignee_bot_id: &str,
    delivery_request_id: &str,
    prompt: &str,
) {
    let content = MessageLogContent::from_text(prompt);
    info!(
        target: MSG_LOG_TARGET,
        log_class = "business_key",
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::NodeDispatch.as_str(),
        status = MessageLogStatus::Routed.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %group.id,
        state_machine_run_id = %run.run_id,
        run_id = %delivery_request_id,
        node_id = %node_id,
        attempt = attempt,
        bot_id = %assignee_bot_id,
        assignee_bot_id = %assignee_bot_id,
        delivery_request_id = %delivery_request_id,
        content = %content.content,
        content_length = content.content_length,
        content_truncated = content.content_truncated,
        content_truncated_bytes = content.content_truncated_bytes,
        "node_dispatch"
    );
}

pub(super) fn log_state_machine_delivery_result(
    group: &Group,
    run: &StateMachineRun,
    node_id: &str,
    attempt: i32,
    assignee_bot_id: &str,
    delivery_request_id: &str,
    target_bot_id: Option<&str>,
    delivered: bool,
    error: Option<&str>,
    failure_phase: Option<&str>,
) {
    let status = if delivered {
        MessageLogStatus::Delivered
    } else {
        MessageLogStatus::Failed
    };
    let bot_id = target_bot_id.unwrap_or(assignee_bot_id);
    if delivered {
        info!(
            target: MSG_LOG_TARGET,
            log_class = "business_key",
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::StateMachine.as_str(),
            session_id = %run.session_id,
            group_id = %group.id,
            state_machine_run_id = %run.run_id,
            run_id = %delivery_request_id,
            node_id = %node_id,
            attempt = attempt,
            bot_id = %bot_id,
            assignee_bot_id = %assignee_bot_id,
            to_bot_id = %bot_id,
            delivery_request_id = %delivery_request_id,
            delivered = delivered,
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    } else {
        warn!(
            target: MSG_LOG_TARGET,
            log_class = "business_key",
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::StateMachine.as_str(),
            session_id = %run.session_id,
            group_id = %group.id,
            state_machine_run_id = %run.run_id,
            run_id = %delivery_request_id,
            node_id = %node_id,
            attempt = attempt,
            bot_id = %bot_id,
            assignee_bot_id = %assignee_bot_id,
            to_bot_id = %bot_id,
            delivery_request_id = %delivery_request_id,
            delivered = delivered,
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    }
}

pub(super) fn log_state_machine_bot_event(
    run: &StateMachineRun,
    correlation: &StateMachineDeliveryCorrelation,
    cmd: &HandleBotTerminalEventCommand,
    text_len: usize,
) {
    let text = extract_text(&cmd.event_payload).unwrap_or_default();
    let content = MessageLogContent::from_text(&text);
    info!(
        target: MSG_LOG_TARGET,
        log_class = matches!(cmd.state, ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted).then_some("business_key"),
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::BotEvent.as_str(),
        status = MessageLogStatus::Responded.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %run.group_id,
        state_machine_run_id = %run.run_id,
        run_id = %cmd.run_id,
        node_id = %correlation.node_id,
        attempt = correlation.attempt,
        bot_id = %cmd.bot_id,
        assignee_bot_id = %correlation.assignee_bot_id,
        delivery_request_id = %correlation.delivery_request_id,
        bot_delivery_run_id = %cmd.run_id,
        chat_event_type = %cmd.event_type,
        chat_event_state = chat_event_state_slug(&cmd.state),
        text_len = text_len,
        content = %content.content,
        content_length = content.content_length,
        content_truncated = content.content_truncated,
        content_truncated_bytes = content.content_truncated_bytes,
        "bot_event"
    );
}

pub(super) fn log_state_machine_node_result(
    run: &StateMachineRun,
    correlation: &StateMachineDeliveryCorrelation,
    status: MessageLogStatus,
    outcome: Option<&str>,
    error: Option<&str>,
    artifact_len: Option<usize>,
) {
    let level_error = status == MessageLogStatus::Failed;
    if level_error {
        warn!(
            target: MSG_LOG_TARGET,
            log_class = "business_key",
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::NodeResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::StateMachine.as_str(),
            session_id = %run.session_id,
            group_id = %run.group_id,
            state_machine_run_id = %run.run_id,
            run_id = %correlation.delivery_request_id,
            node_id = %correlation.node_id,
            attempt = correlation.attempt,
            bot_id = %correlation.assignee_bot_id,
            assignee_bot_id = %correlation.assignee_bot_id,
            delivery_request_id = %correlation.delivery_request_id,
            bot_delivery_run_id = %correlation.bot_delivery_run_id.as_deref().unwrap_or(""),
            outcome = %outcome.unwrap_or(""),
            artifact_len = artifact_len.unwrap_or(0),
            error = %error.unwrap_or(""),
            "node_result"
        );
    } else {
        info!(
            target: MSG_LOG_TARGET,
            log_class = "business_key",
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::NodeResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::StateMachine.as_str(),
            session_id = %run.session_id,
            group_id = %run.group_id,
            state_machine_run_id = %run.run_id,
            run_id = %correlation.delivery_request_id,
            node_id = %correlation.node_id,
            attempt = correlation.attempt,
            bot_id = %correlation.assignee_bot_id,
            assignee_bot_id = %correlation.assignee_bot_id,
            delivery_request_id = %correlation.delivery_request_id,
            bot_delivery_run_id = %correlation.bot_delivery_run_id.as_deref().unwrap_or(""),
            outcome = %outcome.unwrap_or(""),
            artifact_len = artifact_len.unwrap_or(0),
            error = %error.unwrap_or(""),
            "node_result"
        );
    }
}

pub(super) fn log_state_machine_transition(
    run: &StateMachineRun,
    node_id: &str,
    outcome: &str,
    next_node_ids: &[String],
    status: MessageLogStatus,
) {
    info!(
        target: MSG_LOG_TARGET,
        log_class = "business_key",
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::Transition.as_str(),
        status = status.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %run.group_id,
        state_machine_run_id = %run.run_id,
        run_id = %run.run_id,
        node_id = %node_id,
        outcome = %outcome,
        next_node_ids = %message_log_json(&next_node_ids),
        next_node_count = next_node_ids.len(),
        "transition"
    );
}

pub(super) fn log_state_machine_run_complete(
    run: &StateMachineRun,
    node_count: usize,
    output_len: usize,
    session_complete_result: &str,
) {
    info!(
        target: MSG_LOG_TARGET,
        log_class = "business_key",
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::RunComplete.as_str(),
        status = MessageLogStatus::Completed.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %run.group_id,
        state_machine_run_id = %run.run_id,
        run_id = %run.run_id,
        node_count = node_count,
        output_len = output_len,
        session_complete_result = %session_complete_result,
        "run_complete"
    );
}

pub(super) fn log_state_machine_run_failed(run: &StateMachineRun, error: &str, session_complete_result: &str) {
    warn!(
        target: MSG_LOG_TARGET,
        log_class = "business_key",
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::RunFailed.as_str(),
        status = MessageLogStatus::Failed.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %run.group_id,
        state_machine_run_id = %run.run_id,
        run_id = %run.run_id,
        error = %error,
        session_complete_result = %session_complete_result,
        "run_failed"
    );
}

pub(super) fn log_state_machine_timeout(
    run: &StateMachineRun,
    node: &StateMachineNodeRun,
    timeout_ms: u64,
    deadline_ms: Option<u64>,
    timeout_grace_ms: u64,
) {
    warn!(
        target: MSG_LOG_TARGET,
        log_class = "business_key",
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::Timeout.as_str(),
        status = MessageLogStatus::Timeout.as_str(),
        mode = MessageLogMode::StateMachine.as_str(),
        session_id = %run.session_id,
        group_id = %run.group_id,
        state_machine_run_id = %run.run_id,
        run_id = %node.delivery_request_id.as_deref().unwrap_or(run.run_id.as_str()),
        node_id = %node.node_id,
        attempt = node.attempt,
        bot_id = %node.assignee_bot_id.as_deref().unwrap_or(""),
        assignee_bot_id = %node.assignee_bot_id.as_deref().unwrap_or(""),
        delivery_request_id = %node.delivery_request_id.as_deref().unwrap_or(""),
        bot_delivery_run_id = %node.bot_delivery_run_id.as_deref().unwrap_or(""),
        node_timeout_ms = timeout_ms,
        timeout_deadline_ms = deadline_ms.unwrap_or(0),
        timeout_grace_ms = timeout_grace_ms,
        "timeout"
    );
}

pub(super) fn elapsed_ms(started_at: Instant) -> u64 {
    started_at
        .elapsed()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}
