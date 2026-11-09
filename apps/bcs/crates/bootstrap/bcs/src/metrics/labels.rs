//! Stable bounded-cardinality metric labels and error classification.
use super::*;
#[cfg(feature = "prometheus-metrics")]
pub(super) fn metrics_result_from_bool(ok: bool) -> MetricsResult {
    if ok {
        MetricsResult::Success
    } else {
        MetricsResult::Error
    }
}

pub fn http_status_class(status: StatusCode) -> &'static str {
    match status.as_u16() {
        100..=199 => "1xx",
        200..=299 => "2xx",
        300..=399 => "3xx",
        400..=499 => "4xx",
        _ => "5xx",
    }
}

pub fn http_result_label(status: StatusCode) -> &'static str {
    if status.is_server_error() {
        "error"
    } else {
        "success"
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn metrics_result_label(result: MetricsResult) -> &'static str {
    match result {
        MetricsResult::Success => "success",
        MetricsResult::Error => "error",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn ws_peer_label(peer: WsPeer) -> &'static str {
    match peer {
        WsPeer::Bot => "bot",
        WsPeer::Frontend => "frontend",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn ws_close_reason_label(reason: WsCloseReason) -> &'static str {
    match reason {
        WsCloseReason::ClientClose => "client_close",
        WsCloseReason::ServerClose => "server_close",
        WsCloseReason::IdleTimeout => "idle_timeout",
        WsCloseReason::SendError => "send_error",
        WsCloseReason::ProtocolError => "protocol_error",
        WsCloseReason::Unknown => "unknown",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn ws_event_label(event: WsConnectionEvent) -> &'static str {
    match event {
        WsConnectionEvent::Accepted => "accepted",
        WsConnectionEvent::Registered => "registered",
        WsConnectionEvent::RegisterRejected => "register_rejected",
        WsConnectionEvent::DispatchError => "dispatch_error",
        WsConnectionEvent::ProtocolError => "protocol_error",
        WsConnectionEvent::SendError => "send_error",
        WsConnectionEvent::Closed => "closed",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn bot_delivery_metric_kind(kind: &BotDeliveryKind) -> DeliveryMetricKind {
    match kind {
        BotDeliveryKind::Send => DeliveryMetricKind::Send,
        BotDeliveryKind::Inject => DeliveryMetricKind::Inject,
        BotDeliveryKind::Abort => DeliveryMetricKind::Abort,
        BotDeliveryKind::TaskDispatch => DeliveryMetricKind::TaskDispatch,
        BotDeliveryKind::TaskMessage => DeliveryMetricKind::TaskMessage,
        BotDeliveryKind::TaskResult => DeliveryMetricKind::TaskResult,
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn frontend_delivery_metric_kind(kind: &FrontendDeliveryKind) -> DeliveryMetricKind {
    match kind {
        FrontendDeliveryKind::WorkbenchEvent => DeliveryMetricKind::WorkbenchEvent,
        FrontendDeliveryKind::RunEvent => DeliveryMetricKind::RunEvent,
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn delivery_metric_target_label(target: DeliveryMetricTarget) -> &'static str {
    match target {
        DeliveryMetricTarget::Bot => "bot",
        DeliveryMetricTarget::Frontend => "frontend",
        DeliveryMetricTarget::Unknown => "unknown",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn delivery_metric_kind_label(kind: DeliveryMetricKind) -> &'static str {
    match kind {
        DeliveryMetricKind::Send => "send",
        DeliveryMetricKind::Inject => "inject",
        DeliveryMetricKind::Abort => "abort",
        DeliveryMetricKind::TaskDispatch => "task_dispatch",
        DeliveryMetricKind::TaskMessage => "task_message",
        DeliveryMetricKind::TaskResult => "task_result",
        DeliveryMetricKind::WorkbenchEvent => "workbench_event",
        DeliveryMetricKind::RunEvent => "run_event",
        DeliveryMetricKind::Unknown => "unknown",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn delivery_block_reason_code(reason: DeliveryBlockReason) -> DeliveryErrorCode {
    match reason {
        DeliveryBlockReason::PolicyBlocked => DeliveryErrorCode::PolicyBlocked,
        DeliveryBlockReason::Unknown => DeliveryErrorCode::Unknown,
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn delivery_outcome_label(result: DeliveryOutcome) -> &'static str {
    match result {
        DeliveryOutcome::Delivered => "delivered",
        DeliveryOutcome::Failed => "failed",
        DeliveryOutcome::NoReceivers => "no_receivers",
        DeliveryOutcome::Blocked => "blocked",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn delivery_error_code_label(error_code: DeliveryErrorCode) -> &'static str {
    match error_code {
        DeliveryErrorCode::None => "none",
        DeliveryErrorCode::NotFound => "not_found",
        DeliveryErrorCode::NotRegistered => "not_registered",
        DeliveryErrorCode::NotConnected => "not_connected",
        DeliveryErrorCode::InvalidOperation => "invalid_operation",
        DeliveryErrorCode::Unauthorized => "unauthorized",
        DeliveryErrorCode::Forbidden => "forbidden",
        DeliveryErrorCode::MessageLimit => "message_limit",
        DeliveryErrorCode::Internal => "internal",
        DeliveryErrorCode::Io => "io",
        DeliveryErrorCode::Json => "json",
        DeliveryErrorCode::CannotAddSelf => "cannot_add_self",
        DeliveryErrorCode::PendingRequestExists => "pending_request_exists",
        DeliveryErrorCode::CannotAcceptRejected => "cannot_accept_rejected",
        DeliveryErrorCode::CannotRejectAccepted => "cannot_reject_accepted",
        DeliveryErrorCode::NotFriends => "not_friends",
        DeliveryErrorCode::PrivateBot => "private_bot",
        DeliveryErrorCode::PolicyBlocked => "policy_blocked",
        DeliveryErrorCode::Unknown => "unknown",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn service_error_code(error: &ServiceError) -> DeliveryErrorCode {
    match error {
        ServiceError::A2aManagement(_) => DeliveryErrorCode::InvalidOperation,
        ServiceError::DeliveryNotSent { .. } => DeliveryErrorCode::InvalidOperation,
        // A closed client-actionable outcome: reconcile and resubmit.
        ServiceError::RegistrationReconciliationRequired => DeliveryErrorCode::InvalidOperation,
        ServiceError::BotNotFound(_)
        | ServiceError::GroupNotFound(_)
        | ServiceError::ParticipantNotFound(_)
        | ServiceError::FriendRequestNotFound(_) => DeliveryErrorCode::NotFound,
        ServiceError::BotNotRegistered(_) => DeliveryErrorCode::NotRegistered,
        ServiceError::BotNotConnected(_) => DeliveryErrorCode::NotConnected,
        ServiceError::BotMethodUnsupported { .. } => DeliveryErrorCode::InvalidOperation,
        ServiceError::InvalidOperation { .. } => DeliveryErrorCode::InvalidOperation,
        ServiceError::Conflict(_) => DeliveryErrorCode::InvalidOperation,
        ServiceError::Unauthorized(_) => DeliveryErrorCode::Unauthorized,
        ServiceError::Forbidden(_) | ServiceError::BotHidden(_) => DeliveryErrorCode::Forbidden,
        ServiceError::MessageLimitReached(_) => DeliveryErrorCode::MessageLimit,
        ServiceError::InternalError(_) => DeliveryErrorCode::Internal,
        ServiceError::IoError(_) => DeliveryErrorCode::Io,
        ServiceError::JsonError(_) => DeliveryErrorCode::Json,
        ServiceError::CannotAddSelf => DeliveryErrorCode::CannotAddSelf,
        ServiceError::PendingRequestExists { .. } => DeliveryErrorCode::PendingRequestExists,
        ServiceError::CannotAcceptRejected => DeliveryErrorCode::CannotAcceptRejected,
        ServiceError::CannotRejectAccepted => DeliveryErrorCode::CannotRejectAccepted,
        ServiceError::NotFriends(_) => DeliveryErrorCode::NotFriends,
        ServiceError::PrivateBotCannotCollaborate => DeliveryErrorCode::PrivateBot,
        ServiceError::ProviderNotFound(_) => DeliveryErrorCode::NotFound,
        ServiceError::ProviderNotReadyForDownlink { .. } => DeliveryErrorCode::InvalidOperation,
        ServiceError::BotAlreadyBound { .. } => DeliveryErrorCode::InvalidOperation,
        ServiceError::SessionNotFound(_) | ServiceError::SessionInvalidParams(_) => {
            DeliveryErrorCode::Internal
        }
        ServiceError::ExistNonPublicBots { .. } => DeliveryErrorCode::InvalidOperation,
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn group_status_label(status: GroupStatus) -> &'static str {
    match status {
        GroupStatus::Active => "active",
        GroupStatus::Completed => "completed",
        GroupStatus::Error => "error",
        GroupStatus::Closed => "closed",
        GroupStatus::Inactive => "inactive",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn group_kind_label(kind: GroupKind) -> &'static str {
    match kind {
        GroupKind::Normal => "normal",
        GroupKind::Dm => "dm",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn group_strategy_label(strategy: GroupStrategy) -> &'static str {
    match strategy {
        GroupStrategy::Chat => "chat",
        GroupStrategy::ManagerWorker => "manager_worker",
        GroupStrategy::StateMachine => "state_machine",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn actor_kind_label(kind: ActorKind) -> &'static str {
    match kind {
        ActorKind::Bot => "bot",
        ActorKind::Human => "human",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn actor_status_label(status: ActorStatus) -> &'static str {
    match status {
        ActorStatus::Online => "online",
        ActorStatus::Hidden => "hidden",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn visibility_label(visibility: Option<&str>) -> &'static str {
    match visibility {
        Some("public") => "public",
        Some("protected") => "protected",
        Some("private") => "private",
        _ => "other",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn session_status_label(status: SessionStatus) -> &'static str {
    match status {
        SessionStatus::Running => "running",
        SessionStatus::Completed => "completed",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn session_kind_label(kind: SessionKind) -> &'static str {
    match kind {
        SessionKind::Chat => "chat",
        SessionKind::ServiceInvocation => "service_invocation",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn group_event_kind_label(kind: Option<GroupKind>) -> &'static str {
    kind.map(group_kind_label).unwrap_or("unknown")
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn group_session_event_label(event: GroupSessionEvent) -> &'static str {
    match event {
        GroupSessionEvent::Created => "created",
        GroupSessionEvent::Completed => "completed",
        GroupSessionEvent::Closed => "closed",
        GroupSessionEvent::Deleted => "deleted",
        GroupSessionEvent::MemberAdded => "member_added",
        GroupSessionEvent::MemberRemoved => "member_removed",
        GroupSessionEvent::StatusUpdated => "status_updated",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn group_status_event(status: &str) -> GroupSessionEvent {
    if status.eq_ignore_ascii_case("completed") {
        GroupSessionEvent::Completed
    } else if status.eq_ignore_ascii_case("closed") {
        GroupSessionEvent::Closed
    } else {
        GroupSessionEvent::StatusUpdated
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn service_mode_label(mode: Option<&str>) -> &'static str {
    match mode {
        Some("master_slave") => "master_slave",
        Some(_) => "other",
        None => "none",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn message_flow_source_label(source: MessageFlowSource) -> &'static str {
    match source {
        MessageFlowSource::WebWs => "web_ws",
        MessageFlowSource::Http => "http",
        MessageFlowSource::BotWs => "bot_ws",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn message_flow_operation_label(operation: MessageFlowOperation) -> &'static str {
    match operation {
        MessageFlowOperation::WebSend => "web_send",
        MessageFlowOperation::GroupChat => "group_chat",
        MessageFlowOperation::PersistentGroupSend => "persistent_group_send",
        MessageFlowOperation::BotEvent => "bot_event",
        MessageFlowOperation::GroupCallback => "group_callback",
        MessageFlowOperation::ChatAbort => "chat_abort",
        MessageFlowOperation::TaskDispatch => "task_dispatch",
        MessageFlowOperation::TaskMessage => "task_message",
        MessageFlowOperation::TaskComplete => "task_complete",
        MessageFlowOperation::DirectChat => "direct_chat",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn direct_chat_state_label(state: DirectChatRunState) -> &'static str {
    match state {
        DirectChatRunState::Pending => "pending",
        DirectChatRunState::Submitted => "submitted",
        DirectChatRunState::Running => "running",
        DirectChatRunState::Completed => "completed",
        DirectChatRunState::Failed => "failed",
        DirectChatRunState::Cancelled => "cancelled",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn direct_chat_client_kind_label(client_kind: DirectChatClientKind) -> &'static str {
    match client_kind {
        DirectChatClientKind::None => "none",
        DirectChatClientKind::HttpChat => "http_chat",
        DirectChatClientKind::HttpChatAsync => "http_chat_async",
        DirectChatClientKind::BcsCli => "bcs_cli",
        DirectChatClientKind::Unknown => "unknown",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn direct_chat_run_event_label(event: DirectChatRunEvent) -> &'static str {
    match event {
        DirectChatRunEvent::Created => "created",
        DirectChatRunEvent::Submitted => "submitted",
        DirectChatRunEvent::Running => "running",
        DirectChatRunEvent::Completed => "completed",
        DirectChatRunEvent::Failed => "failed",
        DirectChatRunEvent::Cancelled => "cancelled",
        DirectChatRunEvent::Expired => "expired",
        DirectChatRunEvent::Dropped => "dropped",
        DirectChatRunEvent::CapacityRejected => "capacity_rejected",
    }
}

#[cfg(feature = "prometheus-metrics")]
pub(super) fn direct_chat_run_reason_label(reason: DirectChatRunReason) -> &'static str {
    match reason {
        DirectChatRunReason::None => "none",
        DirectChatRunReason::Timeout => "timeout",
        DirectChatRunReason::BotNotConnected => "bot_not_connected",
        DirectChatRunReason::Blocked => "blocked",
        DirectChatRunReason::StoreCapacity => "store_capacity",
        DirectChatRunReason::InternalError => "internal_error",
    }
}
