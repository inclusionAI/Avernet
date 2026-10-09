use super::*;

pub(crate) async fn apply_outbound_interceptors(
    flow: &BcsMessageFlow,
    group_id: &str,
    message: &GroupMessage,
    target: &RoutingTarget,
) -> Result<GroupMessage, BlockReason> {
    if flow.interceptors.is_empty() {
        return Ok(message.clone());
    }

    // Backward compatibility: if either side has no agent_code, the security
    // gateway has no policy to evaluate. Pre-refactor BCS had no outbound
    // interceptor at all, so missing credentials must NOT silently block —
    // skip the chain and warn instead. Bots that have completed AgentPass
    // registration have credentials; legacy/dev bots that never registered
    // would otherwise be mass-blocked when security_gateway.dry_run=false.
    let caller = match flow.registry.get_agent_credentials(&message.sender).await {
        Some(creds) if creds.agent_code.as_deref().is_some_and(|c| !c.is_empty()) => creds,
        _ => {
            tracing::warn!(
                sender = %message.sender,
                receiver = %target.bot_uuid,
                "skipping outbound interceptor chain: sender has no agent_code (legacy/unregistered bot)"
            );
            return Ok(message.clone());
        }
    };
    let receiver = match flow.registry.get_agent_credentials(&target.bot_uuid).await {
        Some(creds) if creds.agent_code.as_deref().is_some_and(|c| !c.is_empty()) => creds,
        _ => {
            tracing::warn!(
                sender = %message.sender,
                receiver = %target.bot_uuid,
                "skipping outbound interceptor chain: receiver has no agent_code (legacy/unregistered bot)"
            );
            return Ok(message.clone());
        }
    };
    let mut outbound = OutboundMessage {
        group_id: group_id.to_string(),
        message: message.clone(),
        receiver_bot_id: target.bot_uuid.clone(),
        caller,
        receiver,
    };

    let block_context = DeliveryBlockContext {
        target: DeliveryMetricTarget::Bot,
        delivery_kind: delivery_metric_kind(target.delivery_type),
        surface: DeliveryBlockSurface::GroupMessage,
        reason: DeliveryBlockReason::PolicyBlocked,
    };
    match flow
        .interceptors
        .on_outbound_with_context(&mut outbound, block_context)
        .await
    {
        InterceptorDecision::Pass | InterceptorDecision::Modify => Ok(outbound.message),
        InterceptorDecision::Block(reason) => Err(reason),
    }
}

/// Run the outbound interceptor chain once for a human-mention notification
/// before it leaves the process, using the first non-sender delivery target
/// as the policy receiver. Returns the (possibly rewritten) text to notify
/// with, or `None` when the chain blocks the content: what policy forbids for
/// bot deliveries must not reach humans through the notification channel.
///
/// The chain is credential-gated (see [`apply_outbound_interceptors`]):
/// senders without AgentPass credentials skip evaluation, matching what bot
/// deliveries receive under the same posture. With no bot receiver at all
/// there is nothing to evaluate, and the notification proceeds unmodified.
pub(crate) async fn apply_notify_outbound_policy(
    flow: &BcsMessageFlow,
    group_id: &str,
    sender_actor_id: &str,
    message_text: &str,
    targets: &[RoutingTarget],
) -> Option<String> {
    let Some(receiver) = targets
        .iter()
        .find(|target| target.bot_uuid != sender_actor_id)
    else {
        return Some(message_text.to_string());
    };
    let candidate = GroupMessage {
        id: uuid::Uuid::new_v4().to_string(),
        timestamp: now_ms(),
        sender: sender_actor_id.to_string(),
        content: message_text.to_string(),
        message_type: GroupMessageType::Bot,
        bot_name: None,
        role: MessageRole::User,
        run_id: String::new(),
        history_meta: None,
        metadata: None,
        attachments: None,
    };
    match apply_outbound_interceptors(flow, group_id, &candidate, receiver).await {
        Ok(message) => Some(message.content),
        Err(reason) => {
            warn!(
                group_id,
                sender = %sender_actor_id,
                interceptor = %reason.interceptor_id,
                code = %reason.code,
                "human mention notification suppressed by outbound policy"
            );
            None
        }
    }
}

/// Run the outbound interceptor chain for an A2A (1:1 bot-to-bot) chat.
///
/// A2A chats have no group context — `context_tag` is purely a log/trace label
/// (typically the run_id). The synthetic GroupMessage carries only the three
/// fields current interceptors actually inspect: id, sender, content.
///
/// Returns the (possibly modified) message id on Pass/Modify, or the BlockReason.
pub async fn apply_a2a_interceptors(
    flow: &BcsMessageFlow,
    context_tag: &str,
    sender_bot_id: &str,
    target_bot_id: &str,
    message_id: &str,
    message_content: &str,
) -> Result<String, BlockReason> {
    apply_chain_for_bot_pair(
        flow,
        context_tag,
        sender_bot_id,
        target_bot_id,
        message_id,
        message_content,
        DeliveryBlockSurface::DirectChat,
        DeliveryMetricKind::Send,
    )
    .await
}

/// Run the outbound interceptor chain for a master-slave task dispatch.
///
/// Task dispatches are bot-to-bot directives within a group, but the wire
/// frame is built from raw JSON rather than a GroupMessage. This helper
/// adapts the chain accordingly. `context_tag` should be the task_id.
pub async fn apply_task_interceptors(
    flow: &BcsMessageFlow,
    context_tag: &str,
    driver_bot_id: &str,
    target_bot_id: &str,
    task_id: &str,
    message_content: &str,
) -> Result<String, BlockReason> {
    apply_chain_for_bot_pair(
        flow,
        context_tag,
        driver_bot_id,
        target_bot_id,
        task_id,
        message_content,
        DeliveryBlockSurface::Task,
        DeliveryMetricKind::TaskDispatch,
    )
    .await
}

/// Internal helper: run the chain on a synthetic OutboundMessage built from
/// primitive fields. Used by both A2A and task helpers above. Mirrors the
/// missing-credentials skip behavior in `apply_outbound_interceptors` so
/// legacy/unregistered bots are not silently blocked.
async fn apply_chain_for_bot_pair(
    flow: &BcsMessageFlow,
    context_tag: &str,
    sender_bot_id: &str,
    receiver_bot_id: &str,
    message_id: &str,
    message_content: &str,
    surface: DeliveryBlockSurface,
    delivery_kind: DeliveryMetricKind,
) -> Result<String, BlockReason> {
    if flow.interceptors.is_empty() {
        return Ok(message_id.to_string());
    }

    let caller = match flow.registry.get_agent_credentials(sender_bot_id).await {
        Some(creds) if creds.agent_code.as_deref().is_some_and(|c| !c.is_empty()) => creds,
        _ => {
            tracing::warn!(
                sender = %sender_bot_id,
                receiver = %receiver_bot_id,
                context = %context_tag,
                "skipping outbound interceptor chain: sender has no agent_code (legacy/unregistered bot)"
            );
            return Ok(message_id.to_string());
        }
    };
    let receiver = match flow.registry.get_agent_credentials(receiver_bot_id).await {
        Some(creds) if creds.agent_code.as_deref().is_some_and(|c| !c.is_empty()) => creds,
        _ => {
            tracing::warn!(
                sender = %sender_bot_id,
                receiver = %receiver_bot_id,
                context = %context_tag,
                "skipping outbound interceptor chain: receiver has no agent_code (legacy/unregistered bot)"
            );
            return Ok(message_id.to_string());
        }
    };

    // Synthetic GroupMessage: only the three fields SecurityInterceptor reads.
    // group_id slot is reused as a context tag for log correlation.
    let synthetic = GroupMessage {
        id: message_id.to_string(),
        timestamp: 0,
        sender: sender_bot_id.to_string(),
        content: message_content.to_string(),
        message_type: GroupMessageType::default(),
        bot_name: None,
        role: MessageRole::default(),
        run_id: String::new(),
        history_meta: None,
        metadata: None,
        attachments: None,
    };
    let mut outbound = OutboundMessage {
        group_id: context_tag.to_string(),
        message: synthetic,
        receiver_bot_id: receiver_bot_id.to_string(),
        caller,
        receiver,
    };

    let block_context = DeliveryBlockContext {
        target: DeliveryMetricTarget::Bot,
        delivery_kind,
        surface,
        reason: DeliveryBlockReason::PolicyBlocked,
    };
    match flow
        .interceptors
        .on_outbound_with_context(&mut outbound, block_context)
        .await
    {
        InterceptorDecision::Pass | InterceptorDecision::Modify => Ok(outbound.message.id),
        InterceptorDecision::Block(reason) => Err(reason),
    }
}
