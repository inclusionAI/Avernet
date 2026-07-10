use bcs_domain::{LedgerSummary, SenderType};
use bcs_protocol::{BcsFrame, GroupContext, RequestFrame};
use bcs_service_api::{
    BotDeliveryCommand, BotDeliveryKind, ChatResponseMode, DeliveryType, Group, GroupStatus,
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryResult, FrontendDeliveryTarget,
    GroupStrategy, Participant, ParticipantMode, ParticipantRole, ServiceError, ServiceResult,
    MessageLogContent, MessageLogEventType, MessageLogMode, MessageLogStatus,
    MESSAGE_LOG_SCHEMA_VERSION,
    Session, SessionKind, SessionStatus, SystemMessageEvent, TaskCompleteCommand,
    TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome, TaskMessageCommand,
    TaskMessageOutcome,
};
use serde_json::Value;
use tracing::{info, warn};

use crate::BcsMessageFlow;
use crate::protocol_context::group_type_wire;
use crate::task_store::new_task_entry;
use crate::MSG_LOG_TARGET;

pub async fn handle_task_dispatch(
    flow: &BcsMessageFlow,
    cmd: TaskDispatchCommand,
) -> ServiceResult<TaskDispatchOutcome> {
    let (group_id, manager_session_id) = task_dispatch_scope(&cmd.group_id, &cmd.payload);
    let mut group = flow
        .group
        .get(&group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(group_id.clone()))?;
    if let Some(session_id) = manager_session_id.as_deref() {
        apply_session_participants(flow, &mut group, &group_id, session_id).await?;
    }

    ensure_task_dispatch_allowed(&group)?;
    if !is_task_manager(&group, &cmd.driver_bot_id) {
        return Err(ServiceError::Unauthorized(
            task_manager_error_message(&group, "dispatch tasks").to_string(),
        ));
    }

    // Resolve target bot: match by bot_uuid first, then bot_name.
    // If neither matches, fall back to registry capabilities.name.
    let mut target = group
        .participants
        .iter()
        .find(|participant| {
            participant.bot_uuid == cmd.target_bot_id
                || participant.bot_name.as_deref() == Some(cmd.target_bot_id.as_str())
        })
        .cloned();
    if target.is_none() {
        for p in group.participants.iter().filter(|p| p.is_bot()) {
            if let Some(bot) = flow.registry.get(&p.bot_uuid).await {
                if bot.capabilities.name.as_deref() == Some(cmd.target_bot_id.as_str()) {
                    target = Some(p.clone());
                    break;
                }
            }
        }
    }
    let target = target.ok_or_else(|| ServiceError::BotNotFound(cmd.target_bot_id.clone()))?;
    let target_mode = target
        .mode
        .unwrap_or_else(|| ParticipantMode::default_for(target.actor_kind));
    if target_mode == ParticipantMode::Muted {
        return Err(ServiceError::InvalidOperation {
            message: "target bot is muted".to_string(),
            request_id: None,
        });
    }
    let target_bot_id = target.bot_uuid.clone();

    // Resolve target_bot_name: explicit param > participant.bot_name >
    // registry capabilities.name > bot_uuid fallback.
    let target_bot_name = if let Some(name) = cmd.target_bot_name.clone() {
        name
    } else if let Some(name) = target.bot_name.clone().filter(|n| !n.is_empty()) {
        name
    } else {
        flow.registry
            .get(&target_bot_id)
            .await
            .and_then(|bot| bot.capabilities.name.clone())
            .filter(|n| !n.is_empty())
            .unwrap_or_else(|| target_bot_id.clone())
    };
    let message = task_message(&cmd.payload);
    let manager_session_id = manager_session_id.unwrap_or_else(|| group_id.clone());
    let task_id = uuid::Uuid::new_v4().to_string();
    let now = now_ms();

    // Outbound interceptor chain runs BEFORE the task is registered so a
    // Block decision never leaves a phantom task in the store. Apply chain
    // first, then register only on success.
    let effective_task_id = match crate::group_flow::apply_task_interceptors(
        flow,
        &group_id,
        &cmd.driver_bot_id,
        &target_bot_id,
        &task_id,
        &message,
    )
    .await
    {
        Ok(id) => id,
        Err(reason) => {
            tracing::warn!(
                interceptor = %reason.interceptor_id,
                code = %reason.code,
                task = %task_id,
                "task dispatch blocked by interceptor chain"
            );
            return Err(ServiceError::Forbidden(if reason.user_visible {
                reason.message
            } else {
                "task dispatch blocked by policy".to_string()
            }));
        }
    };

    let response_mode = task_response_mode(&group, &cmd.payload);
    flow.task_store
        .register(new_task_entry(
            effective_task_id.clone(),
            group_id.clone(),
            (manager_session_id != group_id).then(|| manager_session_id.clone()),
            cmd.driver_bot_id.clone(),
            target_bot_id.clone(),
            Some(target_bot_name.clone()),
            now,
            response_mode,
        ))
        .await;

    // Resolve driver_name with registry fallback: prefer
    // participant.bot_name, then registry capabilities.name, then bot_uuid.
    let driver_name = group
        .participants
        .iter()
        .find(|p| p.bot_uuid == cmd.driver_bot_id)
        .and_then(|p| p.bot_name.clone().filter(|n| !n.is_empty()))
        .or_else(|| {
            // async in sync context — we resolve via a separate step
            None // fall through
        });
    // Registry fallback for driver_name must be async — handled inline below.
    let driver_name = match driver_name {
        Some(name) => name,
        None => flow
            .registry
            .get(&cmd.driver_bot_id)
            .await
            .and_then(|bot| bot.capabilities.name.clone())
            .filter(|n| !n.is_empty())
            .unwrap_or_else(|| cmd.driver_bot_id.clone()),
    };

    let frame = build_task_dispatch_frame(
        &group,
        &cmd.driver_bot_id,
        &driver_name,
        &target_bot_id,
        &target_bot_name,
        &message,
        &effective_task_id,
        &manager_session_id,
        now,
    );
    let ledger_session_id = (manager_session_id != group_id).then_some(manager_session_id.as_str());
    log_task_dispatch_created(
        &group_id,
        &manager_session_id,
        &effective_task_id,
        &cmd.driver_bot_id,
        &target_bot_id,
        &message,
    );
    let delivery_target = match flow.registry.resolve_delivery_target(&target_bot_id).await {
        Ok(target) => target,
        Err(error) => {
            let error_text = error.to_string();
            log_manager_worker_deliver_result(
                &group_id,
                Some(&manager_session_id),
                &effective_task_id,
                &target_bot_id,
                Some(&cmd.driver_bot_id),
                DeliveryType::Send,
                false,
                Some(error_text.as_str()),
                Some("resolve_target"),
            );
            flow.task_store.mark_failed(&effective_task_id).await;
            emit_task_ledger_status(flow, &group, &group_id, ledger_session_id, &cmd.driver_bot_id).await;
            return Err(error);
        }
    };
    let delivery_kind = BotDeliveryKind::TaskDispatch;
    let provider_transport = flow
        .provider_transport_preference(&target_bot_id, &delivery_kind, &delivery_target)
        .await;
    let result = match flow
        .bot_delivery
        .deliver(BotDeliveryCommand {
            target: delivery_target,
            run_id: effective_task_id.clone(),
            frame,
            delivery_kind,
            provider_transport,
        })
        .await
    {
        Ok(result) if result.delivered => {
            log_manager_worker_deliver_result(
                &group_id,
                Some(&manager_session_id),
                &effective_task_id,
                &target_bot_id,
                Some(&cmd.driver_bot_id),
                DeliveryType::Send,
                true,
                result.error.as_ref().map(ToString::to_string).as_deref(),
                None,
            );
            flow.record_successful_send_context(
                DeliveryType::Send,
                &result,
                &effective_task_id,
                &target_bot_id,
                &group_id,
                Some(&manager_session_id),
            )
            .await;
            if group.group_strategy == GroupStrategy::ManagerWorker {
                crate::group_flow::try_persist_group_message(
                    flow,
                    &group_id,
                    Some(&manager_session_id),
                    &cmd.driver_bot_id,
                    SenderType::Bot,
                    "chat",
                    Value::String(message.clone()),
                    None,
                    Some(target_bot_id.clone()),
                    &effective_task_id,
                )
                .await;
            }
            result
        }
        Ok(result) => {
            log_manager_worker_deliver_result(
                &group_id,
                Some(&manager_session_id),
                &effective_task_id,
                &target_bot_id,
                Some(&cmd.driver_bot_id),
                DeliveryType::Send,
                false,
                result.error.as_ref().map(ToString::to_string).as_deref(),
                Some("deliver"),
            );
            flow.task_store.mark_failed(&effective_task_id).await;
            emit_task_ledger_status(flow, &group, &group_id, ledger_session_id, &cmd.driver_bot_id).await;
            return Err(ServiceError::InvalidOperation {
                message: "target bot is not connected".to_string(),
                request_id: Some(effective_task_id),
            });
        }
        Err(error) => {
            let error_text = error.to_string();
            log_manager_worker_deliver_result(
                &group_id,
                Some(&manager_session_id),
                &effective_task_id,
                &target_bot_id,
                Some(&cmd.driver_bot_id),
                DeliveryType::Send,
                false,
                Some(error_text.as_str()),
                Some("deliver"),
            );
            flow.task_store.mark_failed(&effective_task_id).await;
            emit_task_ledger_status(flow, &group, &group_id, ledger_session_id, &cmd.driver_bot_id).await;
            return Err(error);
        }
    };

    flow.record_successful_send_context(
        DeliveryType::Send,
        &result,
        &effective_task_id,
        &target_bot_id,
        &group_id,
        Some(&manager_session_id),
    )
    .await;

    emit_task_ledger_status(flow, &group, &group_id, ledger_session_id, &cmd.driver_bot_id).await;

    Ok(TaskDispatchOutcome {
        task_id: effective_task_id,
        status: "dispatched".to_string(),
        bot_deliveries: vec![result],
        frontend_deliveries: Vec::new(),
    })
}

pub(crate) async fn emit_task_ledger_status(
    flow: &BcsMessageFlow,
    group: &Group,
    group_id: &str,
    session_id: Option<&str>,
    driver_bot_id: &str,
) {
    let Some(system_message) = flow.system_message.as_ref() else {
        return;
    };
    let Some(receiver) = group
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == driver_bot_id)
        .cloned()
    else {
        return;
    };
    let summary = flow.task_store.ledger_summary_at(group_id, session_id, now_ms()).await;
    let message = format_ledger_status_line(&summary);
    if message.is_empty() {
        return;
    }
    let event = SystemMessageEvent::GenericNotification {
        group_id: group_id.to_string(),
        message,
        receivers: vec![receiver],
    };
    let notify_session_id = session_id.unwrap_or(group_id);
    let _ = system_message
        .notify(group_id, event, notify_session_id, &group.participants)
        .await;
}

fn format_ledger_status_line(summary: &LedgerSummary) -> String {
    if summary.pending.is_empty()
        && summary.replied.is_empty()
        && summary.failed.is_empty()
        && summary.timed_out.is_empty()
    {
        return String::new();
    }
    format!(
        "[任务状态] 待回复: {} | 已回复: {} | 失败: {} | 超时: {}",
        join_or_dash(&summary.pending),
        join_or_dash(&summary.replied),
        join_or_dash(&summary.failed),
        join_or_dash(&summary.timed_out),
    )
}

fn join_or_dash(items: &[String]) -> String {
    if items.is_empty() {
        "-".to_string()
    } else {
        items.join(", ")
    }
}

pub async fn handle_task_complete(
    flow: &BcsMessageFlow,
    cmd: TaskCompleteCommand,
) -> ServiceResult<TaskCompleteOutcome> {
    let raw_group_id = cmd
        .payload
        .get("group_id")
        .and_then(|value| value.as_str())
        .unwrap_or(&cmd.task_id);
    let status = cmd
        .payload
        .get("status")
        .and_then(|value| value.as_str())
        .unwrap_or("completed");
    let group_status = match status {
        "completed" => GroupStatus::Completed,
        "error" => GroupStatus::Error,
        other => {
            return Err(ServiceError::InvalidOperation {
                message: format!("invalid task complete status: {other}"),
                request_id: Some(cmd.task_id),
            });
        }
    };

    let scope = resolve_task_complete_scope(flow, raw_group_id, &cmd.payload).await?;
    let group = flow
        .group
        .get(&scope.group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(scope.group_id.clone()))?;
    ensure_task_dispatch_allowed(&group)?;
    if !is_task_manager(&group, &cmd.bot_id) {
        return Err(ServiceError::Unauthorized(
            task_manager_error_message(&group, "complete a task group").to_string(),
        ));
    }
    let pending = flow
        .task_store
        .pending_targets_at(&scope.group_id, scope.session_id.as_deref(), now_ms())
        .await;
    if !pending.is_empty() {
        if cmd.via_echo {
            return Ok(TaskCompleteOutcome {
                status: status.to_string(),
                blocked: true,
                pending,
                callback_requested: false,
                completed_session: None,
                frontend_deliveries: Vec::new(),
            });
        }
        return Err(ServiceError::InvalidOperation {
            message: format!(
                "task completion blocked by pending targets: {}",
                pending.join(", ")
            ),
            request_id: Some(cmd.task_id),
        });
    }

    let completed_session = if let Some(session_id) = scope.session_id.as_deref() {
        complete_session_target(flow, &scope.group_id, session_id, status, &cmd.payload).await?
    } else {
        flow.group.update_status(&scope.group_id, group_status).await?;
        complete_service_session_if_needed(flow, &group, &scope.group_id, status, &cmd.payload).await?
    };
    log_task_complete(
        &scope.group_id,
        scope.session_id.as_deref(),
        &cmd.task_id,
        &cmd.bot_id,
        status,
    );

    Ok(TaskCompleteOutcome {
        status: status.to_string(),
        blocked: false,
        pending: Vec::new(),
        callback_requested: completed_session.is_some(),
        completed_session,
        frontend_deliveries: Vec::new(),
    })
}

pub async fn handle_task_message(
    flow: &BcsMessageFlow,
    cmd: TaskMessageCommand,
) -> ServiceResult<TaskMessageOutcome> {
    let (group_id, manager_session_id) = task_dispatch_scope(&cmd.group_id, &cmd.payload);
    let manager_session_id = manager_session_id.ok_or_else(|| ServiceError::InvalidOperation {
        message: "task.message requires bcs_session_id".to_string(),
        request_id: None,
    })?;
    let mut group = flow
        .group
        .get(&group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(group_id.clone()))?;
    apply_session_participants(flow, &mut group, &group_id, &manager_session_id).await?;

    if group.group_strategy != GroupStrategy::ManagerWorker {
        return Err(ServiceError::InvalidOperation {
            message: "task.message requires manager_worker group".to_string(),
            request_id: None,
        });
    }
    if !is_task_worker(&group, &cmd.worker_bot_id) {
        return Err(ServiceError::Unauthorized(
            "only worker bot can send task messages".to_string(),
        ));
    }

    let manager = group
        .participants
        .iter()
        .find(|participant| participant.role == ParticipantRole::Manager)
        .cloned()
        .ok_or_else(|| ServiceError::InvalidOperation {
            message: "manager bot not found".to_string(),
            request_id: None,
        })?;
    let manager_mode = manager
        .mode
        .unwrap_or_else(|| ParticipantMode::default_for(manager.actor_kind));
    if manager_mode == ParticipantMode::Muted {
        return Err(ServiceError::InvalidOperation {
            message: "manager bot is muted".to_string(),
            request_id: None,
        });
    }

    let message = task_message(&cmd.payload);
    if message.trim().is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "task.message requires non-empty message".to_string(),
            request_id: None,
        });
    }
    let worker = group
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == cmd.worker_bot_id)
        .ok_or_else(|| ServiceError::Unauthorized(
            "only worker bot can send task messages".to_string(),
        ))?;
    let worker_name = resolve_participant_name(flow, worker).await;
    let manager_name = resolve_participant_name(flow, &manager).await;
    let run_id = uuid::Uuid::new_v4().to_string();
    let frame = build_task_message_frame(
        &group,
        &manager_session_id,
        &cmd.worker_bot_id,
        &worker_name,
        &manager.bot_uuid,
        &manager_name,
        &message,
        &run_id,
    );
    let delivery_target = match flow.registry.resolve_delivery_target(&manager.bot_uuid).await {
        Ok(target) => target,
        Err(error) => {
            let error_text = error.to_string();
            log_manager_worker_deliver_result(
                &group_id,
                Some(&manager_session_id),
                &run_id,
                &manager.bot_uuid,
                Some(&cmd.worker_bot_id),
                DeliveryType::Send,
                false,
                Some(error_text.as_str()),
                Some("resolve_target"),
            );
            return Err(error);
        }
    };
    let delivery_kind = BotDeliveryKind::TaskMessage;
    let provider_transport = flow
        .provider_transport_preference(&manager.bot_uuid, &delivery_kind, &delivery_target)
        .await;
    let result = flow
        .bot_delivery
        .deliver(BotDeliveryCommand {
            target: delivery_target,
            run_id: run_id.clone(),
            frame,
            delivery_kind,
            provider_transport,
        })
        .await?;

    log_manager_worker_deliver_result(
        &group_id,
        Some(&manager_session_id),
        &run_id,
        &manager.bot_uuid,
        Some(&cmd.worker_bot_id),
        DeliveryType::Send,
        result.delivered,
        result.error.as_ref().map(ToString::to_string).as_deref(),
        if result.delivered { None } else { Some("deliver") },
    );

    if !result.delivered {
        return Err(ServiceError::InvalidOperation {
            message: "manager bot is not connected".to_string(),
            request_id: Some(run_id),
        });
    }
    flow.record_successful_send_context(
        DeliveryType::Send,
        &result,
        &run_id,
        &manager.bot_uuid,
        &group_id,
        Some(&manager_session_id),
    )
    .await;
    crate::group_flow::try_persist_group_message(
        flow,
        &group_id,
        Some(&manager_session_id),
        &cmd.worker_bot_id,
        SenderType::Bot,
        "chat",
        Value::String(message.clone()),
        None,
        None,
        &run_id,
    )
    .await;
    let frontend_deliveries = publish_task_message_to_workbench(
        flow,
        &group,
        &manager_session_id,
        &cmd.worker_bot_id,
        &worker_name,
        &message,
    )
    .await;

    Ok(TaskMessageOutcome {
        status: "sent".to_string(),
        bot_deliveries: vec![result],
        frontend_deliveries,
    })
}

struct TaskCompleteScope {
    group_id: String,
    session_id: Option<String>,
}

async fn resolve_task_complete_scope(
    flow: &BcsMessageFlow,
    raw_group_id: &str,
    payload: &Value,
) -> ServiceResult<TaskCompleteScope> {
    if let Some(session_id) = task_session_id(payload) {
        let group_id = session_group_id(flow, session_id)
            .await?
            .unwrap_or_else(|| raw_group_id.to_string());
        return Ok(TaskCompleteScope {
            group_id,
            session_id: Some(session_id.to_string()),
        });
    }

    if let Some(group_id) = session_group_id(flow, raw_group_id).await? {
        return Ok(TaskCompleteScope {
            group_id,
            session_id: Some(raw_group_id.to_string()),
        });
    }

    Ok(TaskCompleteScope {
        group_id: raw_group_id.to_string(),
        session_id: None,
    })
}

async fn session_group_id(
    flow: &BcsMessageFlow,
    session_id: &str,
) -> ServiceResult<Option<String>> {
    let Some(session_management) = flow.session_management.as_ref() else {
        return Ok(None);
    };
    session_management
        .get(session_id)
        .await
        .map(|session| session.map(|session| session.group_id))
        .map_err(|error| ServiceError::InternalError(error.to_string()))
}

async fn complete_session_target(
    flow: &BcsMessageFlow,
    group_id: &str,
    session_id: &str,
    status: &str,
    payload: &Value,
) -> ServiceResult<Option<Session>> {
    let Some(session_management) = flow.session_management.as_ref() else {
        return Err(ServiceError::InvalidOperation {
            message: "task complete targets a session but session management is unavailable"
                .to_string(),
            request_id: Some(session_id.to_string()),
        });
    };
    if !session_belongs_to_group(session_management.as_ref(), session_id, group_id).await? {
        return Err(ServiceError::SessionNotFound(session_id.to_string()));
    }
    complete_session_with_summary(session_management.as_ref(), session_id, status, payload).await
}

async fn complete_service_session_if_needed(
    flow: &BcsMessageFlow,
    group: &Group,
    group_id: &str,
    status: &str,
    payload: &Value,
) -> ServiceResult<Option<Session>> {
    if group.service_spec.is_none() {
        return Ok(None);
    }
    let Some(session_management) = flow.session_management.as_ref() else {
        return Ok(None);
    };
    let target_session_id = match task_session_id(payload) {
        Some(session_id) if session_belongs_to_group(session_management.as_ref(), session_id, group_id).await? => {
            Some(session_id.to_string())
        }
        _ => latest_running_service_session(session_management.as_ref(), group_id)
            .await?
            .map(|session| session.id),
    };
    let Some(session_id) = target_session_id else {
        return Ok(None);
    };
    complete_session_with_summary(session_management.as_ref(), &session_id, status, payload).await
}

async fn complete_session_with_summary(
    session_management: &dyn bcs_service_api::SessionManagementService,
    session_id: &str,
    status: &str,
    payload: &Value,
) -> ServiceResult<Option<Session>> {
    let summary = payload
        .get("summary")
        .and_then(|value| value.as_str())
        .unwrap_or_default();
    let output = if summary.is_empty() {
        None
    } else {
        Some(Value::String(summary.to_string()))
    };
    let error = (status == "error").then(|| summary.to_string());
    session_management
        .complete_if_running(session_id, output, error)
        .await
        .map_err(|error| ServiceError::InternalError(error.to_string()))
}

async fn session_belongs_to_group(
    session_management: &dyn bcs_service_api::SessionManagementService,
    session_id: &str,
    group_id: &str,
) -> ServiceResult<bool> {
    session_management
        .get(session_id)
        .await
        .map(|session| session.is_some_and(|session| session.group_id == group_id))
        .map_err(|error| ServiceError::InternalError(error.to_string()))
}

async fn latest_running_service_session(
    session_management: &dyn bcs_service_api::SessionManagementService,
    group_id: &str,
) -> ServiceResult<Option<Session>> {
    let mut sessions = session_management
        .list_by_group(group_id, Some(SessionStatus::Running), 0, 100, None, None)
        .await
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    sessions.retain(|session| session.session_kind == SessionKind::ServiceInvocation);
    sessions.sort_by(|a, b| b.updated_at.cmp(&a.updated_at).then_with(|| b.created_at.cmp(&a.created_at)));
    Ok(sessions.into_iter().next())
}

fn ensure_task_dispatch_allowed(group: &Group) -> ServiceResult<()> {
    if group.service_mode.as_deref() == Some("master_slave") {
        return Ok(());
    }
    if group.group_strategy == GroupStrategy::ManagerWorker {
        return Ok(());
    }
    Err(ServiceError::InvalidOperation {
        message: format!(
            "task methods require service_mode=master_slave or manager_worker group, \
             group {} has service_mode={}",
            group.id,
            group.service_mode.as_deref().unwrap_or("none")
        ),
        request_id: None,
    })
}

fn is_task_manager(group: &Group, bot_id: &str) -> bool {
    if group.group_strategy == GroupStrategy::ManagerWorker {
        return group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == bot_id && participant.role == group.group_strategy.lead_role());
    }
    group.driver_bot == bot_id
}

fn is_task_worker(group: &Group, bot_id: &str) -> bool {
    group.group_strategy == GroupStrategy::ManagerWorker
        && group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == bot_id && participant.role == ParticipantRole::Worker)
}

fn task_manager_error_message<'a>(group: &Group, action: &'a str) -> String {
    if group.group_strategy == GroupStrategy::ManagerWorker {
        return format!("only the manager bot can {action}");
    }
    format!("only the driver bot can {action}")
}

async fn resolve_participant_name(flow: &BcsMessageFlow, participant: &Participant) -> String {
    if let Some(name) = participant.bot_name.clone().filter(|name| !name.is_empty()) {
        return name;
    }
    flow.registry
        .get(&participant.bot_uuid)
        .await
        .and_then(|bot| bot.capabilities.name.clone())
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| participant.bot_uuid.clone())
}

fn task_message(payload: &Value) -> String {
    payload
        .get("message")
        .and_then(|value| value.as_str())
        .map(str::to_string)
        .unwrap_or_else(|| payload.to_string())
}

fn task_response_mode(group: &Group, payload: &Value) -> ChatResponseMode {
    payload
        .get("response_mode")
        .or_else(|| payload.get("responseMode"))
        .cloned()
        .and_then(|value| serde_json::from_value(value).ok())
        .unwrap_or_else(|| {
            if group.group_strategy == GroupStrategy::ManagerWorker {
                ChatResponseMode::AfterLastToolCall
            } else {
                ChatResponseMode::Full
            }
        })
}

async fn publish_task_message_to_workbench(
    flow: &BcsMessageFlow,
    group: &Group,
    manager_session_id: &str,
    worker_bot: &str,
    worker_name: &str,
    message: &str,
) -> Vec<FrontendDeliveryResult> {
    let event_json = build_workbench_task_message_event(
        group,
        manager_session_id,
        worker_bot,
        worker_name,
        message,
    );
    let delivery = flow
        .frontend_delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: manager_session_id.to_string(),
            },
            event_json,
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
        })
        .await;

    match delivery {
        Ok(result) => vec![result],
        Err(error) => {
            warn!(
                group_id = %group.id,
                bcs_session_id = %manager_session_id,
                worker = %worker_bot,
                error = %error,
                "failed to publish task message to workbench"
            );
            Vec::new()
        }
    }
}

fn task_dispatch_scope(group_id: &str, payload: &Value) -> (String, Option<String>) {
    let (real_group_id, legacy_session_id) = unwrap_legacy_session_group_id(group_id);
    let session_id = task_session_id(payload)
        .map(str::to_string)
        .or_else(|| legacy_session_id.map(str::to_string));
    (real_group_id.to_string(), session_id)
}

fn unwrap_legacy_session_group_id(group_id: &str) -> (&str, Option<&str>) {
    match group_id.split_once(':') {
        Some((real_group_id, _)) if !real_group_id.is_empty() => (real_group_id, Some(group_id)),
        _ => (group_id, None),
    }
}

fn task_session_id(payload: &Value) -> Option<&str> {
    payload
        .get("bcs_session_id")
        .or_else(|| payload.get("session_id"))
        .and_then(|value| value.as_str())
        .filter(|value| !value.is_empty())
}

async fn apply_session_participants(
    flow: &BcsMessageFlow,
    group: &mut Group,
    group_id: &str,
    session_id: &str,
) -> ServiceResult<()> {
    let Some(session_management) = flow.session_management.as_ref() else {
        return Ok(());
    };
    let session = session_management
        .get(session_id)
        .await
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
    if session.group_id != group_id {
        return Err(ServiceError::InvalidOperation {
            message: format!("session '{}' does not belong to group '{}'", session_id, group_id),
            request_id: None,
        });
    }
    if session.participants.is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: format!("session '{}' has no participants", session_id),
            request_id: None,
        });
    }
    group.participants = session.participants;
    bcs_service_api::backfill_bot_names(flow.registry.as_ref(), group).await;
    Ok(())
}

fn build_task_dispatch_frame(
    group: &Group,
    driver_bot: &str,
    driver_name: &str,
    target_bot: &str,
    target_bot_name: &str,
    message: &str,
    task_id: &str,
    manager_session_id: &str,
    now_ms: u64,
) -> BcsFrame {
    let participant_names: Vec<String> = group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
        .map(|participant| {
            participant
                .bot_name
                .clone()
                .unwrap_or_else(|| participant.bot_uuid.clone())
        })
        .collect();
    let group_context = GroupContext {
        session_id: manager_session_id.to_string(),
        participants: participant_names,
        originator: driver_name.to_string(),
        from: driver_name.to_string(),
        you_are_mentioned: true,
        is_sender: false,
        mentions: vec![target_bot_name.to_string()],
        message: message.to_string(),
        response_directive: None,
        recipient: Some(target_bot.to_string()),
        recipient_name: Some(target_bot_name.to_string()),
        recipient_role: Some("worker".to_string()),
        delivery_type: Some(delivery_slug(DeliveryType::Send).to_string()),
        routing_mode: None,
        group_type: group_type_wire(group.group_strategy)
            .or_else(|| group.service_mode.clone())
            .or(Some("task".to_string())),
        from_bot_id: None,
        from_bot_owner: None,
    };
    let params = serde_json::json!({
        "session_key": manager_session_id,
        "bcs_group_id": manager_session_id,
        "bcs_session_id": manager_session_id,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": format!("[from:{}] {}", driver_name, message)}],
            "timestamp": now_ms / 1000,
        },
        "channel": {
            "source": "api",
            "user_id": driver_bot,
            "actor_id": driver_bot,
            "actor_name": driver_name,
            "thread_id": group.id,
        },
        "session_context": group_context,
        "timeout_ms": null,
        "idempotency_key": null,
    });

    BcsFrame::Request(RequestFrame::new(
        task_id.to_string(),
        "chat.send",
        Some(params),
    ))
}

fn build_task_message_frame(
    group: &Group,
    manager_session_id: &str,
    worker_bot: &str,
    worker_name: &str,
    manager_bot: &str,
    manager_name: &str,
    message: &str,
    run_id: &str,
) -> BcsFrame {
    let participant_names: Vec<String> = group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
        .map(|participant| {
            participant
                .bot_name
                .clone()
                .unwrap_or_else(|| participant.bot_uuid.clone())
        })
        .collect();
    let group_context = GroupContext {
        session_id: manager_session_id.to_string(),
        participants: participant_names,
        originator: manager_name.to_string(),
        from: worker_name.to_string(),
        you_are_mentioned: true,
        is_sender: false,
        mentions: vec![manager_name.to_string()],
        message: message.to_string(),
        response_directive: None,
        recipient: Some(manager_bot.to_string()),
        recipient_name: Some(manager_name.to_string()),
        recipient_role: Some("manager".to_string()),
        delivery_type: Some(delivery_slug(DeliveryType::Send).to_string()),
        routing_mode: None,
        group_type: group_type_wire(group.group_strategy)
            .or_else(|| group.service_mode.clone())
            .or(Some("task".to_string())),
        from_bot_id: Some(worker_bot.to_string()),
        from_bot_owner: None,
    };
    let params = serde_json::json!({
        "session_key": manager_session_id,
        "bcs_group_id": manager_session_id,
        "bcs_session_id": manager_session_id,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": format!("[from:{}] {}", worker_name, message)}],
            "timestamp": now_ms() / 1000,
        },
        "channel": {
            "source": "api",
            "user_id": worker_name,
            "actor_id": worker_bot,
            "actor_name": worker_name,
            "thread_id": group.id,
        },
        "session_context": group_context,
        "timeout_ms": null,
        "idempotency_key": null,
    });

    BcsFrame::Request(RequestFrame::new(
        run_id.to_string(),
        "chat.send",
        Some(params),
    ))
}

fn build_workbench_task_message_event(
    group: &Group,
    manager_session_id: &str,
    worker_bot: &str,
    worker_name: &str,
    message: &str,
) -> String {
    let event = serde_json::json!({
        "run_id": uuid::Uuid::new_v4().to_string(),
        "session_key": manager_session_id,
        "bcs_session_id": manager_session_id,
        "seq": 0,
        "state": "final",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": message}],
            "from": worker_bot,
            "from_name": worker_name,
            "mentions": [],
        },
    });
    let frame = serde_json::json!({
        "type": "event",
        "event": "chat",
        "payload": event,
        "group_id": group.id,
        "bot_uuid": worker_bot,
        "bot_name": worker_name,
    });
    serde_json::to_string(&frame).unwrap_or_default()
}

fn delivery_slug(delivery_type: DeliveryType) -> &'static str {
    match delivery_type {
        DeliveryType::Send => "send",
        DeliveryType::Inject => "inject",
    }
}

fn effective_message_log_session_id<'a>(group_id: &'a str, session_id: Option<&'a str>) -> &'a str {
    session_id.filter(|value| !value.is_empty()).unwrap_or(group_id)
}

fn log_task_dispatch_created(
    group_id: &str,
    session_id: &str,
    task_id: &str,
    manager_bot_id: &str,
    worker_bot_id: &str,
    message: &str,
) {
    let content = MessageLogContent::from_text(message);
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::TaskDispatchCreated.as_str(),
        status = MessageLogStatus::Routed.as_str(),
        mode = MessageLogMode::ManagerWorker.as_str(),
        session_id = %effective_message_log_session_id(group_id, Some(session_id)),
        group_id = %group_id,
        task_id = %task_id,
        run_id = %task_id,
        bot_id = %worker_bot_id,
        manager_bot_id = %manager_bot_id,
        worker_bot_id = %worker_bot_id,
        content = %content.content,
        content_length = content.content_length,
        content_truncated = content.content_truncated,
        content_truncated_bytes = content.content_truncated_bytes,
        "task_dispatch_created"
    );
}

fn log_task_complete(
    group_id: &str,
    session_id: Option<&str>,
    task_id: &str,
    bot_id: &str,
    task_status: &str,
) {
    let status = if task_status == "completed" {
        MessageLogStatus::Completed
    } else {
        MessageLogStatus::Failed
    };
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::TaskComplete.as_str(),
        status = status.as_str(),
        mode = MessageLogMode::ManagerWorker.as_str(),
        session_id = %effective_message_log_session_id(group_id, session_id),
        group_id = %group_id,
        task_id = %task_id,
        run_id = %task_id,
        bot_id = %bot_id,
        task_status = %task_status,
        "task_complete"
    );
}

fn log_manager_worker_deliver_result(
    group_id: &str,
    session_id: Option<&str>,
    run_id: &str,
    bot_id: &str,
    from_bot_id: Option<&str>,
    delivery_type: DeliveryType,
    delivered: bool,
    error: Option<&str>,
    failure_phase: Option<&str>,
) {
    let status = if delivered {
        MessageLogStatus::Delivered
    } else {
        MessageLogStatus::Failed
    };
    if delivered {
        info!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::ManagerWorker.as_str(),
            session_id = %effective_message_log_session_id(group_id, session_id),
            group_id = %group_id,
            run_id = %run_id,
            task_id = %run_id,
            bot_id = %bot_id,
            from_bot_id = %from_bot_id.unwrap_or(""),
            to_bot_id = %bot_id,
            delivery_type = delivery_slug(delivery_type),
            delivered = delivered,
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    } else {
        warn!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = MessageLogEventType::BotDeliverResult.as_str(),
            status = status.as_str(),
            mode = MessageLogMode::ManagerWorker.as_str(),
            session_id = %effective_message_log_session_id(group_id, session_id),
            group_id = %group_id,
            run_id = %run_id,
            task_id = %run_id,
            bot_id = %bot_id,
            from_bot_id = %from_bot_id.unwrap_or(""),
            to_bot_id = %bot_id,
            delivery_type = delivery_slug(delivery_type),
            delivered = delivered,
            error = %error.unwrap_or(""),
            failure_phase = %failure_phase.unwrap_or(""),
            "bot_deliver_result"
        );
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use bcs_service_api::{Group, GroupStrategy, Participant};

    use super::ensure_task_dispatch_allowed;

    fn make_test_group() -> Group {
        Group::new("g1", "driver_bot", vec![
            Participant::bot("driver_bot", bcs_service_api::ParticipantRole::Driver),
            Participant::bot("worker_bot", bcs_service_api::ParticipantRole::Worker),
        ])
    }

    #[test]
    fn master_slave_service_mode_allows_dispatch() {
        let mut group = make_test_group();
        group.service_mode = Some("master_slave".to_string());
        assert!(
            ensure_task_dispatch_allowed(&group).is_ok(),
            "master_slave service_mode should allow task dispatch"
        );
    }

    #[test]
    fn manager_worker_strategy_allows_dispatch() {
        let mut group = make_test_group();
        group.group_strategy = GroupStrategy::ManagerWorker;
        assert!(
            ensure_task_dispatch_allowed(&group).is_ok(),
            "ManagerWorker strategy should allow task dispatch"
        );
    }

    #[test]
    fn chat_strategy_without_service_mode_rejects_dispatch() {
        let group = make_test_group();
        let err = ensure_task_dispatch_allowed(&group).unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("task methods require"),
            "expected rejection message, got: {msg}"
        );
    }
}
